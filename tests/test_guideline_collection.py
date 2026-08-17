"""Tests for repository collection driven by per-file classifications."""

import csv
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
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


def test_load_baseline_repositories_deduplicates_repository_names_case_insensitively(tmp_path: Path) -> None:
    checklist = tmp_path / "checklist.csv"
    _write_checklist(
        checklist,
        [
            {"repository": "Example/One", "human_decision": "pass"},
            {"repository": "example/one", "human_decision": "pass"},
        ],
    )

    repositories = guideline_collection.load_baseline_repositories((checklist,))

    assert len(repositories) == 1
    assert {name.casefold() for name in repositories} == {"example/one"}


def test_load_baseline_repositories_ignores_duplicate_human_passes(tmp_path: Path) -> None:
    checklist = tmp_path / "checklist.csv"
    with checklist.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=("repository", "human_decision", "duplicate_of"),
        )
        writer.writeheader()
        writer.writerows(
            [
                {"repository": "example/canonical", "human_decision": "pass", "duplicate_of": ""},
                {
                    "repository": "example/duplicate-only",
                    "human_decision": "pass",
                    "duplicate_of": "canonical.md",
                },
            ],
        )

    repositories = guideline_collection.load_baseline_repositories((checklist,))

    assert repositories == {"example/canonical"}


def test_baseline_repository_counts_are_derived_from_candidate_languages() -> None:
    java = _scheduled_repository("Java", 1).candidate
    python = _scheduled_repository("Python", 2).candidate

    counts = guideline_collection.baseline_repository_counts_by_language(
        {java.repository, python.repository},
        population=(java, python),
    )

    assert counts == {
        "Java": 1,
        "JavaScript": 0,
        "Python": 1,
        "TypeScript": 0,
    }


def test_baseline_repository_counts_reject_a_repository_absent_from_the_population() -> None:
    with pytest.raises(ValueError, match="absent from the candidate population"):
        guideline_collection.baseline_repository_counts_by_language(
            {"example/missing"},
            population=(),
        )


def test_baseline_repository_counts_reject_conflicting_candidate_languages() -> None:
    java = _scheduled_repository("Java", 1).candidate
    conflicting = repository.RepositoryCandidate(
        repository=java.repository.upper(),
        revision=java.revision,
        license_name=java.license_name,
        source_file=java.source_file,
        input_index=java.input_index,
        fields={**java.fields, "mainLanguage": "Python"},
    )

    with pytest.raises(ValueError, match="candidate repository has conflicting languages"):
        guideline_collection.baseline_repository_counts_by_language(
            {java.repository},
            population=(java, conflicting),
        )


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


def test_repository_eligibility_filters_reviewed_acceptances_without_hiding_rejections() -> None:
    eligible_baseline = _scheduled_repository("Java", 1).candidate
    ineligible_baseline = _scheduled_repository("Python", 2).candidate
    eligible_confirmed = _scheduled_repository("JavaScript", 3).candidate
    ineligible_confirmed = _scheduled_repository("TypeScript", 4).candidate
    ineligible_rejected = _scheduled_repository("Java", 5).candidate

    filtered = guideline_collection.filter_review_state_by_repository_eligibility(
        baseline_repositories={eligible_baseline.repository, ineligible_baseline.repository},
        manual_review=guideline_collection.ManualReviewState(
            {eligible_confirmed.repository, ineligible_confirmed.repository},
            {ineligible_rejected.repository},
        ),
        eligible_repositories={eligible_baseline.repository, eligible_confirmed.repository},
        population=(
            eligible_baseline,
            ineligible_baseline,
            eligible_confirmed,
            ineligible_confirmed,
            ineligible_rejected,
        ),
    )

    assert filtered.baseline_repositories == {eligible_baseline.repository}
    assert filtered.manual_review == guideline_collection.ManualReviewState(
        {eligible_confirmed.repository},
        set(),
    )
    assert filtered.ineligible_accepted_repositories == {
        ineligible_baseline.repository,
        ineligible_confirmed.repository,
    }


def test_repository_eligibility_rejects_reviewed_repository_without_candidate_metadata() -> None:
    with pytest.raises(ValueError, match="reviewed repository is absent from the candidate population"):
        guideline_collection.filter_review_state_by_repository_eligibility(
            baseline_repositories={"example/missing"},
            manual_review=guideline_collection.ManualReviewState(set(), set()),
            eligible_repositories=set(),
            population=(),
        )


def test_eligible_schedule_must_cover_each_language_with_a_remaining_quota() -> None:
    scheduled = _scheduled_repository("Java", 1)

    with pytest.raises(ValueError, match=r"eligible repository schedule has no candidates.*TypeScript"):
        guideline_collection.validate_schedule_covers_language_deficits(
            (scheduled,),
            baseline_repository_counts={
                "Java": 1,
                "JavaScript": 1,
                "Python": 1,
                "TypeScript": 0,
            },
            target_total_repositories=4,
        )


def test_eligible_schedule_may_omit_a_language_whose_baseline_quota_is_full() -> None:
    guideline_collection.validate_schedule_covers_language_deficits(
        (),
        baseline_repository_counts=dict.fromkeys(repository_sampling.DEFAULT_LANGUAGES, 1),
        target_total_repositories=4,
    )


def test_manual_review_state_confirms_or_rejects_repositories_by_file_decisions(tmp_path: Path) -> None:
    checklist = tmp_path / "checklist.csv"
    with checklist.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=("repository", "file", "github_url", "human_decision", "duplicate_of"),
        )
        writer.writeheader()
        writer.writerows(
            [
                _manual_review_row("example/pass", "rejected.md", "not_found"),
                _manual_review_row("example/pass", "canonical.md", "pass"),
                _manual_review_row("example/rejected", "guide.md", "not_found"),
                _manual_review_row(
                    "example/duplicate-only",
                    "translation.md",
                    "pass",
                    duplicate_of="canonical.md",
                ),
            ],
        )

    state = guideline_collection.load_manual_review_state(checklist)

    assert state.confirmed_repositories == {"example/pass"}
    assert state.rejected_repositories == {"example/duplicate-only", "example/rejected"}


def test_manual_review_state_deduplicates_repository_names_case_insensitively(tmp_path: Path) -> None:
    checklist = tmp_path / "checklist.csv"
    with checklist.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=("repository", "file", "github_url", "human_decision", "duplicate_of"),
        )
        writer.writeheader()
        writer.writerows(
            [
                _manual_review_row("Example/Project", "first.md", "pass"),
                _manual_review_row("example/project", "second.md", "pass"),
            ],
        )

    state = guideline_collection.load_manual_review_state(checklist)

    assert state.confirmed_repositories == {"example/project"}
    assert state.rejected_repositories == set()


def test_manual_review_state_accepts_repository_with_a_prior_positive_screening(tmp_path: Path) -> None:
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    prior = _scheduled_repository("Java", 1)
    state = guideline_collection.ManualReviewState(
        confirmed_repositories={prior.candidate.repository},
        rejected_repositories=set(),
    )

    guideline_collection.validate_manual_review_state(
        state,
        store=store,
        prior_screenings=(guideline_collection.RepositoryScreening(prior, status="pass"),),
    )


def test_manual_review_repositories_must_not_contradict_persisted_screenings(tmp_path: Path) -> None:
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    store.append(guideline_collection.RepositoryScreening(_scheduled_repository("Java", 1), status="pass"))
    rejected = _scheduled_repository("Python", 2)
    store.append(guideline_collection.RepositoryScreening(rejected, status="not_found"))
    state = guideline_collection.ManualReviewState(
        confirmed_repositories={"owner-java/project-1"},
        rejected_repositories={rejected.candidate.repository},
    )

    try:
        guideline_collection.validate_manual_review_state(state, store=store)
    except ValueError as error:
        assert rejected.candidate.repository in str(error)
    else:
        raise AssertionError("manual review contradicting a persisted screening was accepted")


def test_prior_positive_screenings_must_have_a_completed_human_review(tmp_path: Path) -> None:
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    prior = _scheduled_repository("Java", 1)

    with pytest.raises(ValueError, match="prior positive screening has no human review"):
        guideline_collection.validate_manual_review_state(
            guideline_collection.ManualReviewState(set(), set()),
            store=store,
            prior_screenings=(guideline_collection.RepositoryScreening(prior, status="pass"),),
        )


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


def test_prior_collection_loads_each_latest_screening_outcome(tmp_path: Path) -> None:
    prior_dir = tmp_path / "prior"
    store = guideline_collection.RepositoryCollectionStore(prior_dir, configuration={"sample_seed": 41})
    store.initialize()
    first = _scheduled_repository("Java", 1)
    second = _scheduled_repository("Python", 2)
    store.append(guideline_collection.RepositoryScreening(first, status="not_found"))
    store.append(guideline_collection.RepositoryScreening(first, status="pass"))
    store.append(guideline_collection.RepositoryScreening(second, status="no_candidates"))

    loaded = guideline_collection.load_prior_collection(prior_dir)

    assert [(result.scheduled.sample_order, result.status, result.attempt_count) for result in loaded.screenings] == [
        (1, "pass", 2),
        (2, "no_candidates", 1),
    ]


def test_prior_screening_must_match_the_current_seeded_schedule() -> None:
    prior = _scheduled_repository("Java", 1)
    changed_candidate = repository.RepositoryCandidate(
        repository=prior.candidate.repository,
        revision="c" * 40,
        license_name=prior.candidate.license_name,
        source_file=prior.candidate.source_file,
        input_index=prior.candidate.input_index,
        fields=prior.candidate.fields,
    )
    current = repository_sampling.ScheduledRepository(
        candidate=changed_candidate,
        sample_order=prior.sample_order,
        round_number=prior.round_number,
        language=prior.language,
        language_population=prior.language_population,
    )

    with pytest.raises(ValueError, match="prior screening does not match the current schedule"):
        guideline_collection.validate_prior_screenings(
            (guideline_collection.RepositoryScreening(prior, status="pass"),),
            schedule=(current,),
        )


def test_prior_schedule_manifest_must_match_the_complete_current_schedule(tmp_path: Path) -> None:
    first = _scheduled_repository("Java", 1)
    prior_second = _scheduled_repository("Java", 5)
    current_second = repository_sampling.ScheduledRepository(
        candidate=repository.RepositoryCandidate(
            repository=prior_second.candidate.repository,
            revision="c" * 40,
            license_name=prior_second.candidate.license_name,
            source_file=prior_second.candidate.source_file,
            input_index=prior_second.candidate.input_index,
            fields=prior_second.candidate.fields,
        ),
        sample_order=prior_second.sample_order,
        round_number=prior_second.round_number,
        language=prior_second.language,
        language_population=prior_second.language_population,
    )
    manifest = tmp_path / "sampling_manifest.csv"
    repository_sampling.write_stratified_schedule(manifest, (first, prior_second))

    with pytest.raises(ValueError, match="prior sampling manifest does not match the current schedule"):
        guideline_collection.validate_prior_schedule_manifest(
            manifest,
            schedule=(first, current_second),
        )


def test_prior_collection_must_use_the_same_classification_contract(tmp_path: Path) -> None:
    prior = guideline_collection.PriorCollection(
        tmp_path,
        {
            "sample_seed": 41,
            "input_fingerprints": {"java.csv": "input-hash"},
            "exclude_csv_fingerprints": {"old/path.csv": "exclude-hash"},
            "classification_contract_sha256": "prior-contract",
            "filter": {"filename_terms": []},
            "provider": "bedrock",
            "region": "us-east-1",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "max",
            "max_output_tokens": 32_000,
            "max_input_bytes": 200_000,
            "enforce_snapshot_window": True,
        },
        (),
    )
    current = {
        **prior.configuration,
        "exclude_csv_fingerprints": {"new/path.csv": "exclude-hash"},
        "classification_contract_sha256": "current-contract",
    }

    with pytest.raises(ValueError, match="prior collection classification_contract_sha256 does not match"):
        guideline_collection.validate_prior_collection_compatibility(prior, current_configuration=current)


def test_collection_store_recovers_retryable_file_errors_from_existing_checkpoints(tmp_path: Path) -> None:
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    scheduled = _scheduled_repository("Java", 1)
    store.append(
        guideline_collection.RepositoryScreening(
            scheduled,
            status="pass",
            candidate_file_count=1,
            retryable=False,
        ),
    )
    classification_dir = tmp_path / "repositories" / "00001" / "classification"
    classification_dir.mkdir(parents=True)
    error_record = _file_row(scheduled, "docs/retry.md", "a" * 40, "model_error")
    error_record["retry_exhausted"] = False
    (classification_dir / "cache_classification_checkpoint.jsonl").write_text(
        f"{json.dumps(error_record)}\n",
        encoding="utf-8",
    )

    retryable = store.retryable(max_attempts=1)

    assert [result.scheduled.sample_order for result in retryable] == [1]


def test_collection_store_does_not_retry_exhausted_file_errors_as_repository_errors(tmp_path: Path) -> None:
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    scheduled = _scheduled_repository("Java", 1)
    store.append(
        guideline_collection.RepositoryScreening(
            scheduled,
            status="unresolved",
            candidate_file_count=1,
            model_error_count=1,
            retryable=True,
        ),
    )
    classification_dir = tmp_path / "repositories" / "00001" / "classification"
    classification_dir.mkdir(parents=True)
    error_record = _file_row(scheduled, "docs/retry.md", "a" * 40, "model_error")
    error_record["retry_exhausted"] = True
    (classification_dir / "cache_classification_checkpoint.jsonl").write_text(
        f"{json.dumps(error_record)}\n",
        encoding="utf-8",
    )

    retryable = store.retryable(max_attempts=3)

    assert retryable == ()


def test_collection_store_retries_snapshot_failures_with_candidates_within_repository_budget(tmp_path: Path) -> None:
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    scheduled = _scheduled_repository("Java", 1)
    store.append(
        guideline_collection.RepositoryScreening(
            scheduled,
            status="unresolved",
            candidate_file_count=1,
            retryable=True,
        ),
    )
    classification_dir = tmp_path / "repositories" / "00001" / "classification"
    classification_dir.mkdir(parents=True)
    snapshot_record = _file_row(scheduled, "docs/rules.md", "a" * 40, "snapshot_incomplete")
    (classification_dir / "cache_classification_checkpoint.jsonl").write_text(
        f"{json.dumps(snapshot_record)}\n",
        encoding="utf-8",
    )

    retryable = store.retryable(max_attempts=2)

    assert [result.scheduled.sample_order for result in retryable] == [1]


def test_collection_processes_only_languages_below_their_repository_quota(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    languages = repository_sampling.DEFAULT_LANGUAGES
    schedule = tuple(_scheduled_repository(languages[(order - 1) % len(languages)], order) for order in range(1, 9))
    statuses = {
        2: "not_found",
        3: "pass",
        4: "pass",
        6: "pass",
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
        baseline_repository_counts={
            "Java": 1,
            "JavaScript": 0,
            "Python": 0,
            "TypeScript": 0,
        },
        target_total_repositories=4,
        store=store,
        processor=processor,
        workers=4,
    )

    assert sorted(call.args[0].sample_order for call in processor.process.call_args_list) == [2, 3, 4, 6]
    assert report.target_reached is True


def test_collection_report_includes_repository_counts_for_each_language(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    languages = repository_sampling.DEFAULT_LANGUAGES
    schedule = tuple(_scheduled_repository(language, order) for order, language in enumerate(languages, start=1))
    processor = mocker.Mock()
    processor.process.side_effect = lambda item: guideline_collection.RepositoryScreening(item, status="pass")
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()

    report = guideline_collection.collect_repositories(
        schedule,
        baseline_repository_counts=dict.fromkeys(languages, 0),
        target_total_repositories=4,
        store=store,
        processor=processor,
        workers=4,
    )

    assert report.target_repositories_by_language == dict.fromkeys(languages, 1)
    assert report.baseline_repositories_by_language == dict.fromkeys(languages, 0)
    assert report.confirmed_new_repositories_by_language == dict.fromkeys(languages, 0)
    assert report.pending_new_repositories_by_language == dict.fromkeys(languages, 1)
    assert report.selected_repositories_by_language == dict.fromkeys(languages, 1)
    assert report.remaining_repositories_by_language == dict.fromkeys(languages, 0)


def test_collection_rejects_a_target_that_cannot_be_divided_equally_by_language(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()

    with pytest.raises(ValueError, match="divide evenly"):
        guideline_collection.collect_repositories(
            (),
            baseline_repository_counts=dict.fromkeys(repository_sampling.DEFAULT_LANGUAGES, 0),
            target_total_repositories=5,
            store=store,
            processor=mocker.Mock(),
            workers=1,
        )


def test_collection_rejects_a_baseline_that_exceeds_a_language_target(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()

    with pytest.raises(ValueError, match="baseline repository count exceeds"):
        guideline_collection.collect_repositories(
            (),
            baseline_repository_counts={
                "Java": 2,
                "JavaScript": 0,
                "Python": 0,
                "TypeScript": 0,
            },
            target_total_repositories=4,
            store=store,
            processor=mocker.Mock(),
            workers=1,
        )


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
        baseline_repository_counts={
            "Java": 6,
            "JavaScript": 6,
            "Python": 10,
            "TypeScript": 12,
        },
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
        baseline_repository_counts=dict.fromkeys(languages, 0),
        target_total_repositories=4,
        store=store,
        processor=processor,
        workers=4,
    )

    assert attempts == {1: 2, 2: 1, 3: 1, 4: 1}
    assert [result.scheduled.sample_order for result in store.selected(1)] == [1]


def test_collection_retries_file_errors_before_finishing_a_human_complete_collection(
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
            candidate_file_count=1,
            retryable=False,
        ),
    )
    checkpoint_path = tmp_path / "repositories" / "00001" / "classification" / "cache_classification_checkpoint.jsonl"
    checkpoint_path.parent.mkdir(parents=True)
    error_record = _file_row(scheduled, "docs/retry.md", "a" * 40, "model_error")
    error_record["retry_exhausted"] = False
    checkpoint_path.write_text(f"{json.dumps(error_record)}\n", encoding="utf-8")

    def resolve(item: repository_sampling.ScheduledRepository) -> guideline_collection.RepositoryScreening:
        resolved_record = _file_row(item, "docs/retry.md", "a" * 40, "not_found")
        checkpoint_path.write_text(
            f"{json.dumps(error_record)}\n{json.dumps(resolved_record)}\n",
            encoding="utf-8",
        )
        return guideline_collection.RepositoryScreening(
            item,
            status="pass",
            candidate_file_count=1,
        )

    processor = mocker.Mock()
    processor.process.side_effect = resolve

    guideline_collection.collect_repositories(
        (scheduled,),
        baseline_repository_counts={
            "Java": 0,
            "JavaScript": 1,
            "Python": 1,
            "TypeScript": 1,
        },
        target_total_repositories=4,
        confirmed_repositories={scheduled.candidate.repository},
        store=store,
        processor=processor,
        workers=1,
    )

    processor.process.assert_called_once_with(scheduled)


def test_collection_retry_replaces_a_later_pending_repository_in_the_same_language(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    schedule = tuple(
        _scheduled_repository(repository_sampling.DEFAULT_LANGUAGES[(order - 1) % 4], order) for order in range(1, 9)
    )
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    store.append(guideline_collection.RepositoryScreening(schedule[0], status="unresolved", retryable=True))
    store.append(guideline_collection.RepositoryScreening(schedule[4], status="pass"))
    processor = mocker.Mock()
    processor.process.return_value = guideline_collection.RepositoryScreening(schedule[0], status="pass")

    report = guideline_collection.collect_repositories(
        schedule,
        baseline_repository_counts={
            "Java": 0,
            "JavaScript": 1,
            "Python": 1,
            "TypeScript": 1,
        },
        target_total_repositories=4,
        store=store,
        processor=processor,
        workers=1,
    )

    processor.process.assert_called_once_with(schedule[0])
    assert report.pending_new_repositories_by_language["Java"] == 1
    assert [
        result.scheduled.sample_order
        for result in store.selected_by_language(
            {
                "Java": 1,
                "JavaScript": 0,
                "Python": 0,
                "TypeScript": 0,
            },
        )
    ] == [1]


def test_repository_screening_uses_the_checkpoint_to_detect_retryable_file_errors(tmp_path: Path) -> None:
    scheduled = _scheduled_repository("Java", 1)
    classification_dir = tmp_path / "classification"
    classification_dir.mkdir()
    record = _file_row(scheduled, "docs/rules.md", "a" * 40, "model_error")
    record["retry_exhausted"] = False
    (classification_dir / "cache_classification_checkpoint.jsonl").write_text(
        f"{json.dumps(record)}\n",
        encoding="utf-8",
    )
    repository_row = {
        "status": "unresolved",
        "candidate_file_count": "1",
        "pass_count": "0",
        "review_count": "0",
        "not_found_count": "0",
        "model_error_count": "1",
        "retrieval_error_count": "0",
        "input_too_large_count": "0",
        "error": "",
    }

    result = guideline_collection._repository_screening(
        scheduled,
        repository_row,
        classification_dir=classification_dir,
    )

    assert result.retryable is True


def test_positive_repository_screening_retries_unresolved_file_errors(tmp_path: Path) -> None:
    scheduled = _scheduled_repository("Java", 1)
    classification_dir = tmp_path / "classification"
    classification_dir.mkdir()
    pass_record = _file_row(scheduled, "docs/rules.md", "a" * 40, "pass")
    error_record = _file_row(scheduled, "docs/retry.md", "b" * 40, "model_error")
    error_record["retry_exhausted"] = False
    (classification_dir / "cache_classification_checkpoint.jsonl").write_text(
        f"{json.dumps(pass_record)}\n{json.dumps(error_record)}\n",
        encoding="utf-8",
    )
    repository_row = {
        "status": "pass",
        "candidate_file_count": "2",
        "pass_count": "1",
        "review_count": "0",
        "not_found_count": "0",
        "model_error_count": "1",
        "retrieval_error_count": "0",
        "input_too_large_count": "0",
        "error": "",
    }

    result = guideline_collection._repository_screening(
        scheduled,
        repository_row,
        classification_dir=classification_dir,
    )

    assert result.retryable is True


def test_collection_replaces_human_rejected_repositories_from_the_same_schedule(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    languages = repository_sampling.DEFAULT_LANGUAGES
    schedule = tuple(_scheduled_repository(languages[(order - 1) % len(languages)], order) for order in range(1, 9))
    processor = mocker.Mock()
    processor.process.side_effect = lambda item: guideline_collection.RepositoryScreening(item, status="pass")
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    for item in schedule[:4]:
        store.append(guideline_collection.RepositoryScreening(item, status="pass"))

    report = guideline_collection.collect_repositories(
        schedule,
        baseline_repository_counts=dict.fromkeys(languages, 0),
        target_total_repositories=4,
        confirmed_repositories={
            "owner-java/project-1",
            "owner-python/project-3",
            "owner-typescript/project-4",
        },
        rejected_repositories={"owner-javascript/project-2"},
        store=store,
        processor=processor,
        workers=4,
    )

    processor.process.assert_called_once_with(schedule[5])
    assert report.confirmed_new_repositories == 3
    assert report.pending_new_repositories == 1
    assert report.selected_new_repositories == 4


def test_collection_does_not_screen_human_reviewed_repositories_without_checkpoints(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    confirmed = _scheduled_repository("Java", 1)
    rejected = _scheduled_repository("Java", 5)
    prior_negative = _scheduled_repository("Java", 9)
    unreviewed = _scheduled_repository("Java", 13)
    processor = mocker.Mock()
    processor.process.return_value = guideline_collection.RepositoryScreening(unreviewed, status="pass")
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()

    report = guideline_collection.collect_repositories(
        (confirmed, rejected, prior_negative, unreviewed),
        baseline_repository_counts={
            "Java": 0,
            "JavaScript": 2,
            "Python": 2,
            "TypeScript": 2,
        },
        target_total_repositories=8,
        confirmed_repositories={confirmed.candidate.repository},
        rejected_repositories={rejected.candidate.repository},
        prior_screenings=(
            guideline_collection.RepositoryScreening(confirmed, status="pass"),
            guideline_collection.RepositoryScreening(rejected, status="pass"),
            guideline_collection.RepositoryScreening(prior_negative, status="not_found"),
        ),
        store=store,
        processor=processor,
        workers=1,
        max_screened_repositories=1,
    )

    processor.process.assert_called_once_with(unreviewed)
    assert report.carried_screened_repositories == 3
    assert report.processed_repositories == 1
    assert report.target_reached is True


def test_collection_processes_a_prior_unresolved_repository_in_the_current_budget(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    unresolved = _scheduled_repository("Java", 1)
    processor = mocker.Mock()
    processor.process.return_value = guideline_collection.RepositoryScreening(unresolved, status="pass")
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()

    report = guideline_collection.collect_repositories(
        (unresolved,),
        baseline_repository_counts={
            "Java": 0,
            "JavaScript": 1,
            "Python": 1,
            "TypeScript": 1,
        },
        target_total_repositories=4,
        prior_screenings=(guideline_collection.RepositoryScreening(unresolved, status="unresolved"),),
        store=store,
        processor=processor,
        workers=1,
        max_screened_repositories=1,
    )

    processor.process.assert_called_once_with(unresolved)
    assert report.carried_screened_repositories == 0
    assert report.processed_repositories == 1
    assert report.target_reached is True


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
        schedule=(scheduled,),
        store=store,
        baseline_repositories={"baseline/javascript", "baseline/python", "baseline/typescript"},
        baseline_repository_counts={
            "Java": 0,
            "JavaScript": 1,
            "Python": 1,
            "TypeScript": 1,
        },
        target_total_repositories=4,
        repository_client=repository_client,
        max_screened_repositories=200,
        screening_limit_reached=True,
        license_ineligible_reviewed_repositories=2,
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
    assert summary["license_ineligible_reviewed_repositories"] == 2
    assert summary["github_requests"] == 4
    assert summary["source_content_cache_hits"] == 2
    source_metrics.assert_called_once_with()


def test_collection_file_reports_follow_sampling_and_candidate_order(
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
            pass_count=3,
        ),
    )
    classification_dir = tmp_path / "repositories" / "00001" / "classification"
    classification_dir.mkdir(parents=True)
    rows = [
        _file_row(scheduled, "docs/z-first.md", "a" * 40, "pass"),
        _file_row(scheduled, "docs/a-middle.md", "b" * 40, "pass"),
        _file_row(scheduled, "docs/m-last.md", "c" * 40, "pass"),
    ]
    for input_index, row in enumerate(rows):
        row["input_index"] = input_index
    (classification_dir / "cache_classification_checkpoint.jsonl").write_text(
        "".join(f"{json.dumps(row)}\n" for row in reversed(rows)),
        encoding="utf-8",
    )
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {
        "a" * 40: "FIRST",
        "b" * 40: "MIDDLE",
        "c" * 40: "LAST",
    }

    guideline_collection_reports.write_collection_reports(
        output_dir=tmp_path,
        population=(scheduled.candidate,),
        schedule=(scheduled,),
        store=store,
        baseline_repositories={"baseline/javascript", "baseline/python", "baseline/typescript"},
        baseline_repository_counts={
            "Java": 0,
            "JavaScript": 1,
            "Python": 1,
            "TypeScript": 1,
        },
        target_total_repositories=4,
        repository_client=repository_client,
    )

    with (tmp_path / "classified_files.csv").open(encoding="utf-8", newline="") as input_file:
        paths = [row["markdown_path"] for row in csv.DictReader(input_file)]
    assert paths == ["docs/z-first.md", "docs/a-middle.md", "docs/m-last.md"]


def test_selected_repository_report_keeps_confirmed_and_pending_rows_in_sampling_order(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    first = _scheduled_repository("Java", 1)
    second = _scheduled_repository("Python", 2)
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    store.append(guideline_collection.RepositoryScreening(first, status="pass"))
    store.append(guideline_collection.RepositoryScreening(second, status="pass"))

    guideline_collection_reports.write_collection_reports(
        output_dir=tmp_path,
        population=(first.candidate, second.candidate),
        schedule=(first, second),
        store=store,
        baseline_repositories={"baseline/javascript", "baseline/typescript"},
        baseline_repository_counts={
            "Java": 0,
            "JavaScript": 1,
            "Python": 0,
            "TypeScript": 1,
        },
        target_total_repositories=4,
        repository_client=mocker.Mock(),
        confirmed_repositories={second.candidate.repository},
    )

    with (tmp_path / "selected_repositories.csv").open(encoding="utf-8", newline="") as input_file:
        repositories = [row["repository"] for row in csv.DictReader(input_file) if row["origin"].startswith("new_")]
    assert repositories == [first.candidate.repository, second.candidate.repository]


def test_collection_reports_include_confirmed_repository_without_local_checkpoint(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    confirmed = _scheduled_repository("Java", 1)
    pending = _scheduled_repository("Python", 2)
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    store.append(guideline_collection.RepositoryScreening(pending, status="pass"))

    guideline_collection_reports.write_collection_reports(
        output_dir=tmp_path,
        population=(confirmed.candidate, pending.candidate),
        schedule=(confirmed, pending),
        store=store,
        baseline_repositories={"baseline/javascript", "baseline/typescript"},
        baseline_repository_counts={
            "Java": 0,
            "JavaScript": 1,
            "Python": 0,
            "TypeScript": 1,
        },
        target_total_repositories=4,
        repository_client=mocker.Mock(),
        confirmed_repositories={confirmed.candidate.repository},
        prior_screenings=(guideline_collection.RepositoryScreening(confirmed, status="pass"),),
    )

    with (tmp_path / "selected_repositories.csv").open(encoding="utf-8", newline="") as input_file:
        selected = [row for row in csv.DictReader(input_file) if row["origin"].startswith("new_")]
    summary = json.loads((tmp_path / "collection_summary.json").read_text(encoding="utf-8"))
    assert [row["repository"] for row in selected] == [
        confirmed.candidate.repository,
        pending.candidate.repository,
    ]
    assert [row["origin"] for row in selected] == ["new_confirmed", "new_pending"]
    assert summary["confirmed_new_repositories_by_language"]["Java"] == 1
    assert summary["pending_new_repositories_by_language"]["Python"] == 1
    assert summary["carried_screened_repositories"] == 1


def test_collection_reports_select_the_language_quota_and_report_its_counts(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    languages = repository_sampling.DEFAULT_LANGUAGES
    scheduled = tuple(
        _scheduled_repository(language, order) for order, language in enumerate((*languages, "Java"), start=1)
    )
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    for item in scheduled:
        store.append(guideline_collection.RepositoryScreening(item, status="pass"))

    guideline_collection_reports.write_collection_reports(
        output_dir=tmp_path,
        population=tuple(item.candidate for item in scheduled),
        schedule=scheduled,
        store=store,
        baseline_repositories=set(),
        baseline_repository_counts=dict.fromkeys(languages, 0),
        target_total_repositories=4,
        repository_client=mocker.Mock(),
    )

    with (tmp_path / "selected_repositories.csv").open(encoding="utf-8", newline="") as input_file:
        selected = list(csv.DictReader(input_file))
    summary = json.loads((tmp_path / "collection_summary.json").read_text(encoding="utf-8"))
    assert [row["repository"] for row in selected] == [item.candidate.repository for item in scheduled[:4]]
    assert [row["license_name"] for row in selected] == [item.candidate.license_name for item in scheduled[:4]]
    assert summary["confirmed_new_repositories_by_language"] == dict.fromkeys(languages, 0)
    assert summary["pending_new_repositories_by_language"] == dict.fromkeys(languages, 1)
    assert summary["selected_repositories_by_language"] == dict.fromkeys(languages, 1)


def test_collection_reports_reject_confirmed_repositories_above_a_language_quota(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    scheduled = _scheduled_repository("Java", 1)
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    store.append(guideline_collection.RepositoryScreening(scheduled, status="pass"))

    with pytest.raises(ValueError, match="confirmed repositories exceed a language target"):
        guideline_collection_reports.write_collection_reports(
            output_dir=tmp_path,
            population=(scheduled.candidate,),
            schedule=(scheduled,),
            store=store,
            baseline_repositories={"baseline/java"},
            baseline_repository_counts={
                "Java": 1,
                "JavaScript": 0,
                "Python": 0,
                "TypeScript": 0,
            },
            target_total_repositories=4,
            repository_client=mocker.Mock(),
            confirmed_repositories={scheduled.candidate.repository},
        )


def test_collection_reports_rebuild_compact_file_views_from_repository_checkpoints(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    scheduled = _scheduled_repository("Java", 1)
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    store.append(guideline_collection.RepositoryScreening(scheduled, status="pass", pass_count=1))
    classification_dir = tmp_path / "repositories" / "00001" / "classification"
    classification_dir.mkdir(parents=True)
    row = _file_row(scheduled, "docs/rules.md", "a" * 40, "pass")
    row["provider_result"] = "x" * 2_048
    _write_rows(classification_dir / "classified_files.csv", [row])
    (classification_dir / "cache_classification_checkpoint.jsonl").write_text(
        f"{json.dumps(row)}\n",
        encoding="utf-8",
    )
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {"a" * 40: "PASS"}

    guideline_collection_reports.write_collection_reports(
        output_dir=tmp_path,
        population=(scheduled.candidate,),
        schedule=(scheduled,),
        store=store,
        baseline_repositories={"baseline/javascript", "baseline/python", "baseline/typescript"},
        baseline_repository_counts={
            "Java": 0,
            "JavaScript": 1,
            "Python": 1,
            "TypeScript": 1,
        },
        target_total_repositories=4,
        repository_client=repository_client,
    )

    with (tmp_path / "classified_files.csv").open(encoding="utf-8", newline="") as input_file:
        classified = list(csv.DictReader(input_file))
    attempts = [
        json.loads(line) for line in (tmp_path / "file_attempts.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    with (classification_dir / "classified_files.csv").open(encoding="utf-8", newline="") as input_file:
        repository_report = next(csv.DictReader(input_file))
    with (tmp_path / "manual-review" / "checklist.csv").open(encoding="utf-8", newline="") as input_file:
        checklist = list(csv.DictReader(input_file))
    assert classified[0]["markdown_path"] == "docs/rules.md"
    assert "provider_result" not in classified[0]
    assert "provider_result" not in attempts[0]
    assert "provider_result" not in repository_report
    assert checklist[0]["llm_decision"] == "pass"


def test_collection_reports_reject_candidate_repositories_without_a_checkpoint(
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
            candidate_file_count=1,
            pass_count=1,
        ),
    )

    with pytest.raises(FileNotFoundError, match="classification checkpoint is absent or empty"):
        guideline_collection_reports.write_collection_reports(
            output_dir=tmp_path,
            population=(scheduled.candidate,),
            schedule=(scheduled,),
            store=store,
            baseline_repositories={"baseline/javascript", "baseline/python", "baseline/typescript"},
            baseline_repository_counts={
                "Java": 0,
                "JavaScript": 1,
                "Python": 1,
                "TypeScript": 1,
            },
            target_total_repositories=4,
            repository_client=mocker.Mock(),
        )


def test_collection_reports_use_the_completed_checklist_as_the_next_round_source(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    store = guideline_collection.RepositoryCollectionStore(tmp_path, configuration={"sample_seed": 41})
    store.initialize()
    completed_checklist = tmp_path / "manual-review" / "checklist_done.csv"
    next_checklist = tmp_path / "manual-review" / "checklist_round_2.csv"
    export = mocker.patch(
        "guideline_collection_reports.markdown_review.export_cached_review_files",
        autospec=True,
    )
    repository_client = mocker.Mock()

    guideline_collection_reports.write_collection_reports(
        output_dir=tmp_path,
        population=(),
        schedule=(),
        store=store,
        baseline_repositories={
            "baseline/java",
            "baseline/javascript",
            "baseline/python",
            "baseline/typescript",
        },
        baseline_repository_counts=dict.fromkeys(repository_sampling.DEFAULT_LANGUAGES, 1),
        target_total_repositories=4,
        repository_client=repository_client,
        review_checklist_path=completed_checklist,
        review_output_checklist_path=next_checklist,
    )

    export.assert_called_once_with(
        classified_rows=(),
        repository_client=repository_client,
        output_dir=tmp_path / "manual-review",
        existing_checklist_path=completed_checklist,
        checklist_path=next_checklist,
    )


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
                content_sha256="b" * 64,
            ),
            markdown_filename_audit.MarkdownFilenameFile(
                path="docs/z-rules.md",
                matched_terms=("rules",),
                matched_content_terms=("rule",),
                blob_sha="a" * 40,
                size_bytes=20,
                content_sha256="b" * 64,
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
    assert responses_client.complete_json.call_count == 1


def _write_checklist(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=("repository", "human_decision"))
        writer.writeheader()
        writer.writerows(rows)


def _manual_review_row(
    repository_name: str,
    file: str,
    human_decision: str,
    *,
    duplicate_of: str = "",
) -> dict[str, str]:
    return {
        "repository": repository_name,
        "file": file,
        "github_url": f"https://example.test/{file}",
        "human_decision": human_decision,
        "duplicate_of": duplicate_of,
    }


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
