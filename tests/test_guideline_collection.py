"""Tests for repository collection driven by per-file classifications."""

import csv
import json
from collections.abc import Mapping
from pathlib import Path

from pytest_mock import MockerFixture

import guideline
import guideline_collection
import guideline_collection_reports
import markdown_filename_audit
import openai_responses_client
import repository
import repository_cache
import repository_sampling


def test_load_baseline_repositories_counts_each_repository_with_a_human_pass_once(tmp_path: Path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    _write_checklist(
        first,
        [
            {"repository": "example/one", "human_decision": "pass"},
            {"repository": "example/one", "human_decision": "pass"},
            {"repository": "example/two", "human_decision": "not_found"},
        ],
    )
    _write_checklist(
        second,
        [
            {"repository": "example/three", "human_decision": "pass"},
            {"repository": "example/four", "human_decision": ""},
        ],
    )

    repositories = guideline_collection.load_baseline_repositories((first, second))

    assert repositories == {"example/one", "example/three"}


def test_baseline_repositories_must_be_part_of_the_prior_sample_exclusions() -> None:
    try:
        guideline_collection.validate_baseline_exclusions(
            {"example/one", "example/missing"},
            excluded_repositories={"example/one"},
        )
    except ValueError as error:
        assert "example/missing" in str(error)
    else:
        raise AssertionError("missing baseline exclusion was accepted")


def test_manual_review_state_confirms_or_rejects_repositories_by_file_decisions(tmp_path: Path) -> None:
    checklist = tmp_path / "checklist.csv"
    with checklist.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=("repository", "human_decision"))
        writer.writeheader()
        writer.writerows(
            [
                {"repository": "example/pass", "human_decision": "not_found"},
                {"repository": "example/pass", "human_decision": "pass"},
                {"repository": "example/rejected", "human_decision": "not_found"},
                {"repository": "example/pending", "human_decision": ""},
            ],
        )

    state = guideline_collection.load_manual_review_state(checklist)

    assert state.confirmed_repositories == {"example/pass"}
    assert state.rejected_repositories == {"example/rejected"}


def test_manual_review_repositories_must_have_a_persisted_positive_screening(tmp_path: Path) -> None:
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    store.append(guideline_collection.RepositoryScreening(_scheduled_repository("Java", 1), status="pass"))
    state = guideline_collection.ManualReviewState(
        confirmed_repositories={"owner-java/project-1"},
        rejected_repositories={"unknown/project"},
    )

    try:
        guideline_collection.validate_manual_review_state(state, store=store)
    except ValueError as error:
        assert "unknown/project" in str(error)
    else:
        raise AssertionError("manual review for an unscreened repository was accepted")


def test_collection_store_keeps_attempts_and_selects_each_positive_repository_once(tmp_path: Path) -> None:
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    first = _scheduled_repository("Java", 1)
    second = _scheduled_repository("Python", 2)

    store.append(guideline_collection.RepositoryScreening(first, status="unresolved"))
    store.append(guideline_collection.RepositoryScreening(first, status="pass"))
    store.append(guideline_collection.RepositoryScreening(second, status="pass"))

    resumed = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    resumed.initialize()
    assert [result.status for result in resumed.results()] == ["pass", "pass"]
    assert [result.attempt_count for result in resumed.results()] == [2, 1]
    assert [result.scheduled.candidate.repository for result in resumed.selected(2)] == [
        "owner-java/project-1",
        "owner-python/project-2",
    ]
    assert len((tmp_path / "repository_attempts.jsonl").read_text(encoding="utf-8").splitlines()) == 3


def test_collection_stops_after_the_complete_round_that_reaches_the_new_target(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    languages = repository_sampling.DEFAULT_LANGUAGES
    schedule = tuple(_scheduled_repository(languages[(order - 1) % len(languages)], order) for order in range(1, 9))
    statuses = {
        1: "not_found",
        2: "pass",
        3: "unresolved",
        4: "not_found",
        5: "pass",
        6: "pass",
        7: "not_found",
        8: "not_found",
    }
    processor = mocker.Mock()
    processor.process.side_effect = lambda item: guideline_collection.RepositoryScreening(
        item,
        status=statuses[item.sample_order],
    )
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()

    report = guideline_collection.collect_repositories(
        schedule,
        baseline_repository_count=1,
        target_total_repositories=3,
        store=store,
        processor=processor,
        workers=4,
    )

    assert processor.process.call_count == 8
    assert [result.scheduled.sample_order for result in store.selected(2)] == [2, 5]
    assert report.new_repository_target == 2
    assert report.selected_new_repositories == 2
    assert report.processed_repositories == 8
    assert report.target_reached is True


def test_collection_stops_before_a_stratified_round_would_exceed_the_screening_limit(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    languages = repository_sampling.DEFAULT_LANGUAGES
    schedule = tuple(_scheduled_repository(languages[(order - 1) % len(languages)], order) for order in range(1, 9))
    processor = mocker.Mock()
    processor.process.side_effect = lambda item: guideline_collection.RepositoryScreening(
        item,
        status="not_found",
    )
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()

    report = guideline_collection.collect_repositories(
        schedule,
        baseline_repository_count=34,
        target_total_repositories=120,
        store=store,
        processor=processor,
        workers=4,
        max_screened_repositories=6,
    )

    assert processor.process.call_count == 4
    assert report.processed_repositories == 4
    assert report.target_reached is False
    assert report.screening_limit_reached is True


def test_collection_retries_unresolved_repositories_before_freezing_selection(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    languages = repository_sampling.DEFAULT_LANGUAGES
    schedule = tuple(_scheduled_repository(languages[(order - 1) % len(languages)], order) for order in range(1, 5))
    attempts: dict[int, int] = {}

    def process(item: repository_sampling.ScheduledRepository) -> guideline_collection.RepositoryScreening:
        attempts[item.sample_order] = attempts.get(item.sample_order, 0) + 1
        if item.sample_order == 1 and attempts[item.sample_order] == 1:
            return guideline_collection.RepositoryScreening(item, status="unresolved", retryable=True)
        status = "pass" if item.sample_order in {1, 2} else "not_found"
        return guideline_collection.RepositoryScreening(item, status=status)

    processor = mocker.Mock()
    processor.process.side_effect = process
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()

    guideline_collection.collect_repositories(
        schedule,
        baseline_repository_count=0,
        target_total_repositories=1,
        store=store,
        processor=processor,
        workers=4,
    )

    assert attempts == {1: 2, 2: 1, 3: 1, 4: 1}
    assert [result.scheduled.sample_order for result in store.selected(1)] == [1]


def test_collection_replaces_human_rejected_repositories_from_the_same_schedule(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    languages = repository_sampling.DEFAULT_LANGUAGES
    schedule = tuple(_scheduled_repository(languages[(order - 1) % len(languages)], order) for order in range(1, 5))
    processor = mocker.Mock()
    processor.process.side_effect = lambda item: guideline_collection.RepositoryScreening(item, status="pass")
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()

    report = guideline_collection.collect_repositories(
        schedule,
        baseline_repository_count=1,
        target_total_repositories=3,
        confirmed_repositories={"owner-java/project-1"},
        rejected_repositories={"owner-javascript/project-2"},
        store=store,
        processor=processor,
        workers=4,
    )

    pending = store.selected(
        1,
        excluded_repositories={"owner-java/project-1", "owner-javascript/project-2"},
    )
    assert [result.scheduled.sample_order for result in pending] == [3]
    assert report.confirmed_new_repositories == 1
    assert report.pending_new_repositories == 1
    assert report.selected_new_repositories == 2


def test_collection_reports_keep_all_file_decisions_and_review_positive_files(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    scheduled = _scheduled_repository("Java", 1)
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    store.append(
        guideline_collection.RepositoryScreening(
            scheduled,
            status="pass",
            candidate_file_count=3,
            pass_count=1,
            review_count=1,
            not_found_count=1,
        ),
    )
    classification_dir = tmp_path / "repositories" / "00001" / "classification"
    classification_dir.mkdir(parents=True)
    rows = [
        _file_row(scheduled, "docs/rules.md", "a" * 40, "pass"),
        _file_row(scheduled, "docs/review.md", "b" * 40, "review"),
        _file_row(scheduled, "README.md", "c" * 40, "not_found"),
    ]
    _write_rows(classification_dir / "classified_files.csv", rows)
    (classification_dir / "cache_classification_checkpoint.jsonl").write_text(
        "".join(f"{json.dumps(row)}\n" for row in rows),
        encoding="utf-8",
    )
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {
        "a" * 40: "PASS",
        "b" * 40: "REVIEW",
    }
    source_metrics = mocker.Mock(
        return_value={
            "github_requests": 4,
            "github_rate_limit_wait_seconds": 7.0,
            "source_content_downloads": 3,
            "source_content_cache_hits": 2,
        },
    )

    guideline_collection_reports.write_collection_reports(
        output_dir=tmp_path,
        population=(scheduled.candidate,),
        store=store,
        baseline_repositories={"baseline/project"},
        target_total_repositories=2,
        repository_client=repository_client,
        max_screened_repositories=200,
        screening_limit_reached=True,
        source_metrics=source_metrics,
    )

    with (tmp_path / "classified_files.csv").open(encoding="utf-8", newline="") as input_file:
        classified = list(csv.DictReader(input_file))
    with (tmp_path / "manual-review" / "checklist.csv").open(encoding="utf-8", newline="") as input_file:
        checklist = list(csv.DictReader(input_file))
    assert [row["status"] for row in classified] == ["review", "pass", "not_found"]
    assert [row["llm_decision"] for row in checklist] == ["review", "pass"]
    assert len((tmp_path / "file_attempts.jsonl").read_text(encoding="utf-8").splitlines()) == 3
    summary = json.loads((tmp_path / "collection_summary.json").read_text(encoding="utf-8"))
    assert summary["max_screened_repositories"] == 200
    assert summary["screening_limit_reached"] is True
    assert summary["github_requests"] == 4
    assert summary["source_content_cache_hits"] == 2
    source_metrics.assert_called_once_with()


def test_cached_repository_processor_persists_each_file_decision(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    scheduled = _scheduled_repository("Java", 1)
    auditor = mocker.Mock()
    auditor.audit.return_value = markdown_filename_audit.RepositoryMarkdownFilenameAudit(
        candidate=scheduled.candidate,
        status=markdown_filename_audit.MarkdownFilenameAuditStatus.COMPLETED,
        filename_files=(
            markdown_filename_audit.MarkdownFilenameFile(
                path="docs/rules.md",
                matched_terms=("rules",),
                matched_content_terms=("rule",),
                blob_sha="a" * 40,
                size_bytes=20,
            ),
        ),
    )
    inspector = mocker.Mock()
    inspector.inspect_snapshot.return_value = repository_cache.SnapshotInspection(
        repository_cache.SnapshotState.COMPLETE,
    )
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {"a" * 40: "Use ProjectNode in src/nodes/.\n"}
    value = {
        "label": "YES",
        "reason": "ProjectNode must be placed in src/nodes/.",
        "quote": "Use ProjectNode in src/nodes/.",
        "confidence": 9,
    }
    responses_client = mocker.Mock()
    responses_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
        document=_response_document(value),
    )
    processor = guideline_collection.RepositoryFileProcessor(
        output_dir=tmp_path,
        auditor=auditor,
        repository_client=repository_client,
        snapshot_inspector=inspector,
        skip_incomplete_repositories=True,
        responses_client=responses_client,
        provider="bedrock",
        region="us-east-1",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
        file_workers=1,
        blob_batch_size=64,
        max_input_bytes=200_000,
        max_model_attempts=3,
        max_retrieval_attempts=2,
        candidate_configuration={"schema_version": 1},
    )

    result = processor.process(scheduled)

    assert result.status == "pass"
    assert result.pass_count == 1
    classified_path = tmp_path / "repositories" / "00001" / "classification" / "classified_files.csv"
    with classified_path.open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows[0]["markdown_path"] == "docs/rules.md"
    assert rows[0]["status"] == "pass"


def _write_checklist(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=("repository", "human_decision"))
        writer.writeheader()
        writer.writerows(rows)


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _file_row(
    scheduled: repository_sampling.ScheduledRepository,
    path: str,
    blob_sha: str,
    status: str,
) -> dict[str, object]:
    return {
        "custom_id": f"candidate-{blob_sha[:8]}",
        "input_index": scheduled.sample_order,
        "name": scheduled.candidate.repository,
        "lastCommitSHA": scheduled.candidate.revision,
        "markdown_path": path,
        "blob_sha": blob_sha,
        "size_bytes": 10,
        "markdown_url": f"https://github.com/{scheduled.candidate.repository}/blob/{scheduled.candidate.revision}/{path}",
        "matched_filename_terms": "rules",
        "matched_content_terms": "rule",
        "status": status,
        "model_label": "YES" if status != "not_found" else "NO",
        "model_reason": "reason",
        "quote": "quote" if status != "not_found" else "",
        "confidence": 9,
        "reason": "verified_quote" if status != "not_found" else "model_not_found",
        "attempt_count": 1,
        "model_attempt_count": 1,
        "retrieval_attempt_count": 0,
        "retry_exhausted": False,
        "input_tokens": 10,
        "uncached_input_tokens": 10,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 5,
        "total_tokens": 15,
        "cost_usd": 0.1,
        "elapsed_seconds": 1.0,
        "provider_result": "{}",
    }


def _response_document(value: Mapping[str, object]) -> dict[str, object]:
    return {
        "output": [{"content": [{"type": "output_text", "text": json.dumps(value)}]}],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "input_tokens_details": {},
        },
    }


def _scheduled_repository(language: str, order: int) -> repository_sampling.ScheduledRepository:
    name = f"owner-{language.casefold()}/project-{order}"
    revision = f"{order:040x}"
    candidate = repository.RepositoryCandidate(
        repository=name,
        revision=revision,
        license_name="MIT License",
        source_file="input.csv",
        input_index=order,
        fields={
            "name": name,
            "lastCommitSHA": revision,
            "lastCommit": "2026-07-01T00:00:00+00:00",
            "defaultBranch": "main",
            "license": "MIT License",
            "mainLanguage": language,
        },
    )
    return repository_sampling.ScheduledRepository(
        candidate=candidate,
        sample_order=order,
        round_number=(order - 1) // 4 + 1,
        language=language,
        language_population=10,
    )
