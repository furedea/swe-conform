"""Tests for direct classification of Markdown blobs from local Git caches."""

import csv
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

import guideline
import markdown_cache_classification
import markdown_cache_results
import openai_responses_client
import repository_cache


def test_cache_classification_judges_one_blob_without_materializing_batch_input(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    output_dir = tmp_path / "classification"
    _write_candidate_csv(candidate_csv)
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {"a" * 40: "Use ProjectNode in src/nodes/.\n"}
    responses_client = mocker.Mock()
    value = {
        "label": "YES",
        "reason": "The file requires ProjectNode in src/nodes/.",
        "quote": "Use ProjectNode in src/nodes/.",
        "confidence": 9,
    }
    responses_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
        document=_response_document(value),
    )

    report = markdown_cache_classification.run_cache_classification(
        candidate_csv=candidate_csv,
        output_dir=output_dir,
        repository_client=repository_client,
        responses_client=responses_client,
        provider="bedrock",
        region="us-east-1",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
        workers=2,
        blob_batch_size=64,
    )

    repository_client.get_text_blobs.assert_called_once_with("example/project", ("a" * 40,))
    input_document = json.loads(responses_client.complete_json.call_args.kwargs["input_text"])
    assert input_document["content"] == "Use ProjectNode in src/nodes/.\n"
    with (output_dir / "classified_files.csv").open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows[0]["status"] == "pass"
    assert rows[0]["blob_sha"] == "a" * 40
    assert report["attempted"] == 1
    assert report["completed"] == 1
    assert not (output_dir / "batch_input.jsonl").exists()


def test_cache_classification_keeps_raw_provider_responses_only_in_the_checkpoint(tmp_path: Path) -> None:
    output_dir = tmp_path / "classification"
    store = markdown_cache_results.CacheClassificationStore(output_dir, configuration={"schema_version": 1})
    store.initialize()
    record = _classification_record(provider_result='{"raw":"response"}')

    store.append(record)
    store.write_reports()

    checkpoint_record = json.loads(
        (output_dir / "cache_classification_checkpoint.jsonl").read_text(encoding="utf-8"),
    )
    with (output_dir / "classified_files.csv").open(encoding="utf-8", newline="") as input_file:
        report_row = next(csv.DictReader(input_file))
    assert checkpoint_record["provider_result"] == '{"raw":"response"}'
    assert "provider_result" not in report_row


def test_cache_classification_reports_pass_empty_and_failed_repositories(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    repository_summary_csv = tmp_path / "repository_filename_summary.csv"
    output_dir = tmp_path / "classification"
    _write_candidate_csv(candidate_csv)
    _write_repository_summary(repository_summary_csv)
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {"a" * 40: "Use ProjectNode in src/nodes/.\n"}
    responses_client = mocker.Mock()
    value = {
        "label": "YES",
        "reason": "The file requires ProjectNode in src/nodes/.",
        "quote": "Use ProjectNode in src/nodes/.",
        "confidence": 9,
    }
    responses_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
        document=_response_document(value),
    )

    report = markdown_cache_classification.run_cache_classification(
        candidate_csv=candidate_csv,
        repository_summary_csv=repository_summary_csv,
        output_dir=output_dir,
        repository_client=repository_client,
        responses_client=responses_client,
        provider="bedrock",
        region="us-east-1",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
        workers=2,
        blob_batch_size=64,
    )

    with (output_dir / "repository_classification_summary.csv").open(
        encoding="utf-8",
        newline="",
    ) as input_file:
        rows = list(csv.DictReader(input_file))
    assert {row["name"]: row["status"] for row in rows} == {
        "example/project": "pass",
        "example/empty": "no_candidates",
        "example/missing": "retrieval_error",
    }
    with (output_dir / "selected_repositories.csv").open(encoding="utf-8", newline="") as input_file:
        selected = list(csv.DictReader(input_file))
    assert [row["name"] for row in selected] == ["example/project"]
    assert report["input_repositories"] == 3
    assert report["complete_repositories"] == 2
    assert report["repository_errors"] == 1


def test_cache_classification_selects_a_repository_with_only_a_review_file(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    repository_summary_csv = tmp_path / "repository_filename_summary.csv"
    output_dir = tmp_path / "classification"
    _write_candidate_csv(candidate_csv)
    _write_repository_summary(repository_summary_csv)
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {"a" * 40: "Use ProjectNode in src/nodes/.\n"}
    responses_client = mocker.Mock()
    value = {
        "label": "YES",
        "reason": "The file requires ProjectNode in src/nodes/.",
        "quote": "",
        "confidence": 9,
    }
    responses_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
        document=_response_document(value),
    )

    markdown_cache_classification.run_cache_classification(
        candidate_csv=candidate_csv,
        repository_summary_csv=repository_summary_csv,
        output_dir=output_dir,
        repository_client=repository_client,
        responses_client=responses_client,
        provider="bedrock",
        region="us-east-1",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
        workers=2,
        blob_batch_size=64,
    )

    with (output_dir / "repository_classification_summary.csv").open(
        encoding="utf-8",
        newline="",
    ) as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows[0]["status"] == "pass"
    assert rows[0]["review_count"] == "1"
    with (output_dir / "selected_repositories.csv").open(encoding="utf-8", newline="") as input_file:
        selected = list(csv.DictReader(input_file))
    assert [row["name"] for row in selected] == ["example/project"]


def test_cache_classification_inspects_repositories_without_candidate_files(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    repository_summary_csv = tmp_path / "repository_filename_summary.csv"
    output_dir = tmp_path / "classification"
    _write_candidate_csv(candidate_csv)
    _write_repository_summary(repository_summary_csv)
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {"a" * 40: "No project rule.\n"}
    inspector = mocker.Mock()
    inspector.inspect_snapshot.side_effect = lambda name, _revision: repository_cache.SnapshotInspection(
        repository_cache.SnapshotState.COMPLETE
        if name == "example/project"
        else repository_cache.SnapshotState.SNAPSHOT_INCOMPLETE,
    )
    responses_client = mocker.Mock()
    value = {"label": "NO", "reason": "No project rule exists.", "quote": "", "confidence": 9}
    responses_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
        document=_response_document(value),
    )

    report = markdown_cache_classification.run_cache_classification(
        candidate_csv=candidate_csv,
        repository_summary_csv=repository_summary_csv,
        output_dir=output_dir,
        repository_client=repository_client,
        snapshot_inspector=inspector,
        skip_incomplete_repositories=True,
        responses_client=responses_client,
        provider="bedrock",
        region="us-east-1",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
        workers=2,
        blob_batch_size=64,
    )

    assert inspector.inspect_snapshot.call_count == 2
    assert report["input_repositories"] == 3
    assert report["complete_repositories"] == 1
    assert report["incomplete_repositories"] == 1
    assert report["repository_errors"] == 1
    assert report["processed_repositories"] == 1
    with (output_dir / "skipped_repositories.csv").open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert [row["repository"] for row in rows] == ["example/empty"]


def test_cache_classification_reuses_successes_and_retries_only_model_errors(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    output_dir = tmp_path / "classification"
    _write_candidate_csv(candidate_csv, count=2)
    first_repository_client = mocker.Mock()
    first_repository_client.get_text_blobs.return_value = {
        "a" * 40: "Use ProjectNode in src/nodes/.\n",
        "b" * 40: "No project rule.\n",
    }
    value = {
        "label": "NO",
        "reason": "No project rule exists.",
        "quote": "",
        "confidence": 9,
    }
    response = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
        document=_response_document(value),
    )
    first_responses_client = mocker.Mock()
    first_responses_client.complete_json.side_effect = (response, TimeoutError("request timed out"))

    first_report = markdown_cache_classification.run_cache_classification(
        candidate_csv=candidate_csv,
        output_dir=output_dir,
        repository_client=first_repository_client,
        responses_client=first_responses_client,
        provider="bedrock",
        region="us-east-1",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
        workers=1,
        blob_batch_size=64,
    )

    assert first_report["completed"] == 1
    assert first_report["errors"] == 1
    second_repository_client = mocker.Mock()
    second_repository_client.get_text_blobs.return_value = {"b" * 40: "No project rule.\n"}
    second_responses_client = mocker.Mock()
    second_responses_client.complete_json.return_value = response

    second_report = markdown_cache_classification.run_cache_classification(
        candidate_csv=candidate_csv,
        output_dir=output_dir,
        repository_client=second_repository_client,
        responses_client=second_responses_client,
        provider="bedrock",
        region="us-east-1",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
        workers=1,
        blob_batch_size=64,
    )

    second_repository_client.get_text_blobs.assert_called_once_with("example/project", ("b" * 40,))
    second_responses_client.complete_json.assert_called_once()
    assert second_report["attempted"] == 1
    assert second_report["resumed"] == 1
    assert second_report["completed"] == 2
    assert second_report["errors"] == 0


def test_cache_classification_stops_retrying_a_file_after_three_model_errors(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    output_dir = tmp_path / "classification"
    _write_candidate_csv(candidate_csv)
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {"a" * 40: "No project rule.\n"}

    attempted = []
    for _ in range(4):
        responses_client = mocker.Mock()
        responses_client.complete_json.side_effect = TimeoutError("request timed out")
        report = markdown_cache_classification.run_cache_classification(
            candidate_csv=candidate_csv,
            output_dir=output_dir,
            repository_client=repository_client,
            responses_client=responses_client,
            provider="bedrock",
            region="us-east-1",
            model="gpt-5.6-luna",
            reasoning_effort="max",
            max_output_tokens=32_000,
            workers=1,
            blob_batch_size=64,
        )
        attempted.append(report["attempted"])

    assert attempted == [1, 1, 1, 0]
    with (output_dir / "classified_files.csv").open(encoding="utf-8", newline="") as input_file:
        row = next(csv.DictReader(input_file))
    assert row["status"] == "model_error"
    assert row["model_attempt_count"] == "3"
    assert row["retry_exhausted"] == "True"


def test_cache_classification_records_missing_blobs_without_calling_the_model(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    output_dir = tmp_path / "classification"
    _write_candidate_csv(candidate_csv)
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.side_effect = RuntimeError("cached revision is absent")
    responses_client = mocker.Mock()

    report = markdown_cache_classification.run_cache_classification(
        candidate_csv=candidate_csv,
        output_dir=output_dir,
        repository_client=repository_client,
        responses_client=responses_client,
        provider="bedrock",
        region="us-east-1",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
        workers=2,
        blob_batch_size=64,
    )

    responses_client.complete_json.assert_not_called()
    with (output_dir / "classified_files.csv").open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows[0]["status"] == "retrieval_error"
    assert report["attempted"] == 0
    assert report["errors"] == 1


def test_cache_classification_stops_retrying_a_file_after_two_retrieval_errors(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    output_dir = tmp_path / "classification"
    _write_candidate_csv(candidate_csv)
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.side_effect = RuntimeError("temporary local read failure")
    responses_client = mocker.Mock()

    attempted = []
    for _ in range(3):
        report = markdown_cache_classification.run_cache_classification(
            candidate_csv=candidate_csv,
            output_dir=output_dir,
            repository_client=repository_client,
            responses_client=responses_client,
            provider="bedrock",
            region="us-east-1",
            model="gpt-5.6-luna",
            reasoning_effort="max",
            max_output_tokens=32_000,
            workers=1,
            blob_batch_size=64,
        )
        attempted.append(report["attempted"])

    assert attempted == [0, 0, 0]
    assert repository_client.get_text_blobs.call_count == 2
    with (output_dir / "classified_files.csv").open(encoding="utf-8", newline="") as input_file:
        row = next(csv.DictReader(input_file))
    assert row["retrieval_attempt_count"] == "2"
    assert row["retry_exhausted"] == "True"


def test_cache_classification_skips_incomplete_snapshots_before_blob_reads(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    output_dir = tmp_path / "classification"
    _write_candidate_csv(candidate_csv)
    repository_client = mocker.Mock()
    responses_client = mocker.Mock()
    inspector = mocker.Mock()
    inspector.inspect_snapshot.return_value = repository_cache.SnapshotInspection(
        repository_cache.SnapshotState.SNAPSHOT_INCOMPLETE,
        detail="missing blob",
    )

    report = markdown_cache_classification.run_cache_classification(
        candidate_csv=candidate_csv,
        output_dir=output_dir,
        repository_client=repository_client,
        snapshot_inspector=inspector,
        skip_incomplete_repositories=True,
        responses_client=responses_client,
        provider="bedrock",
        region="us-east-1",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
        workers=2,
        blob_batch_size=64,
    )

    repository_client.get_text_blobs.assert_not_called()
    responses_client.complete_json.assert_not_called()
    with (output_dir / "classified_files.csv").open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows[0]["status"] == "snapshot_incomplete"
    assert rows[0]["reason"] == "snapshot_incomplete: missing blob"
    assert report["incomplete_repositories"] == 1
    assert report["processed_repositories"] == 0
    assert report["completed"] == 0
    assert report["errors"] == 0
    assert report["skipped_files"] == 1
    assert report["resumed"] == 0


def test_cache_classification_skips_explicitly_excluded_repositories(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    output_dir = tmp_path / "classification"
    _write_candidate_csv(candidate_csv)
    repository_client = mocker.Mock()
    responses_client = mocker.Mock()
    inspector = mocker.Mock()

    report = markdown_cache_classification.run_cache_classification(
        candidate_csv=candidate_csv,
        output_dir=output_dir,
        repository_client=repository_client,
        snapshot_inspector=inspector,
        skip_incomplete_repositories=True,
        excluded_repositories=("example/project",),
        responses_client=responses_client,
        provider="bedrock",
        region="us-east-1",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
        workers=2,
        blob_batch_size=64,
    )

    inspector.inspect_snapshot.assert_not_called()
    repository_client.get_text_blobs.assert_not_called()
    responses_client.complete_json.assert_not_called()
    assert report["explicitly_excluded_repositories"] == 1
    assert report["processed_repositories"] == 0
    assert report["completed"] == 0
    assert report["errors"] == 0
    assert report["skipped_files"] == 1
    assert report["resumed"] == 0
    with (output_dir / "skipped_repositories.csv").open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows == [
        {
            "repository": "example/project",
            "snapshot_sha": "1" * 40,
            "status": "explicitly_excluded",
            "reason": "explicitly_excluded",
        },
    ]


def test_cache_classification_replaces_a_previous_success_when_snapshot_becomes_incomplete(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    output_dir = tmp_path / "classification"
    _write_candidate_csv(candidate_csv)
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {"a" * 40: "No project rule.\n"}
    inspector = mocker.Mock()
    inspector.inspect_snapshot.return_value = repository_cache.SnapshotInspection(
        repository_cache.SnapshotState.COMPLETE,
    )
    responses_client = mocker.Mock()
    value = {"label": "NO", "reason": "No project rule exists.", "quote": "", "confidence": 9}
    responses_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
        document=_response_document(value),
    )

    def run() -> dict[str, object]:
        return markdown_cache_classification.run_cache_classification(
            candidate_csv=candidate_csv,
            output_dir=output_dir,
            repository_client=repository_client,
            snapshot_inspector=inspector,
            skip_incomplete_repositories=True,
            responses_client=responses_client,
            provider="bedrock",
            region="us-east-1",
            model="gpt-5.6-luna",
            reasoning_effort="max",
            max_output_tokens=32_000,
            workers=1,
            blob_batch_size=64,
        )

    first_report = run()

    assert first_report["completed"] == 1
    inspector.inspect_snapshot.return_value = repository_cache.SnapshotInspection(
        repository_cache.SnapshotState.SNAPSHOT_INCOMPLETE,
    )
    responses_client.reset_mock()
    repository_client.reset_mock()

    second_report = run()

    responses_client.complete_json.assert_not_called()
    repository_client.get_text_blobs.assert_not_called()
    with (output_dir / "classified_files.csv").open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows[0]["status"] == "snapshot_incomplete"
    assert second_report["completed"] == 0
    assert second_report["errors"] == 0
    assert second_report["skipped_files"] == 1
    assert second_report["resumed"] == 0


def test_cache_classification_records_oversized_markdown_without_model_execution(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    output_dir = tmp_path / "classification"
    _write_candidate_csv(candidate_csv, size_bytes=11)
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {"a" * 40: "x" * 11}
    responses_client = mocker.Mock()

    report = markdown_cache_classification.run_cache_classification(
        candidate_csv=candidate_csv,
        output_dir=output_dir,
        repository_client=repository_client,
        responses_client=responses_client,
        provider="bedrock",
        region="us-east-1",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
        max_input_bytes=10,
        workers=2,
        blob_batch_size=64,
    )

    responses_client.complete_json.assert_not_called()
    with (output_dir / "classified_files.csv").open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows[0]["status"] == "input_too_large"
    assert rows[0]["reason"] == "input_too_large: size_bytes=11 max_input_bytes=10"
    assert report["input_too_large_files"] == 1


def test_oversized_markdown_keeps_the_repository_unresolved(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    repository_summary_csv = tmp_path / "repository_filename_summary.csv"
    output_dir = tmp_path / "classification"
    _write_candidate_csv(candidate_csv, size_bytes=11)
    _write_repository_summary(repository_summary_csv)
    repository_client = mocker.Mock()
    responses_client = mocker.Mock()

    markdown_cache_classification.run_cache_classification(
        candidate_csv=candidate_csv,
        repository_summary_csv=repository_summary_csv,
        output_dir=output_dir,
        repository_client=repository_client,
        responses_client=responses_client,
        provider="bedrock",
        region="us-east-1",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
        max_input_bytes=10,
        workers=2,
        blob_batch_size=64,
    )

    with (output_dir / "repository_classification_summary.csv").open(
        encoding="utf-8",
        newline="",
    ) as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows[0]["status"] == "unresolved"
    assert rows[0]["input_too_large_count"] == "1"


def test_cache_classification_stops_after_one_authentication_failure(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    output_dir = tmp_path / "classification"
    _write_candidate_csv(candidate_csv, count=3)
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {
        "a" * 40: "First.\n",
        "b" * 40: "Second.\n",
        "c" * 40: "Third.\n",
    }
    responses_client = mocker.Mock()
    responses_client.complete_json.side_effect = openai_responses_client.ResponsesRequestError(
        "status=401 body=invalid bearer token",
        status_code=401,
    )

    with pytest.raises(RuntimeError, match="preflight failed"):
        markdown_cache_classification.run_cache_classification(
            candidate_csv=candidate_csv,
            output_dir=output_dir,
            repository_client=repository_client,
            responses_client=responses_client,
            provider="bedrock",
            region="us-east-1",
            model="gpt-5.6-luna",
            reasoning_effort="max",
            max_output_tokens=32_000,
            workers=16,
            blob_batch_size=64,
        )

    responses_client.complete_json.assert_called_once()
    checkpoint = (output_dir / "cache_classification_checkpoint.jsonl").read_text(encoding="utf-8")
    assert "status=401" in checkpoint
    with (output_dir / "classified_files.csv").open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows[0]["status"] == "model_error"


def test_cache_classification_reads_repository_blobs_in_bounded_batches(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    output_dir = tmp_path / "classification"
    _write_candidate_csv(candidate_csv, count=3)
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.side_effect = lambda _repository, blob_shas: dict.fromkeys(
        blob_shas, "No project rule.\n"
    )
    value = {
        "label": "NO",
        "reason": "No project rule exists.",
        "quote": "",
        "confidence": 9,
    }
    responses_client = mocker.Mock()
    responses_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
        document=_response_document(value),
    )

    report = markdown_cache_classification.run_cache_classification(
        candidate_csv=candidate_csv,
        output_dir=output_dir,
        repository_client=repository_client,
        responses_client=responses_client,
        provider="bedrock",
        region="us-east-1",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
        workers=2,
        blob_batch_size=2,
    )

    assert repository_client.get_text_blobs.call_args_list == [
        mocker.call("example/project", ("a" * 40, "b" * 40)),
        mocker.call("example/project", ("c" * 40,)),
    ]
    assert report["completed"] == 3


def test_cached_candidates_collapse_identical_duplicate_rows(tmp_path: Path) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    _write_candidate_csv(candidate_csv)
    with candidate_csv.open(encoding="utf-8", newline="") as input_file:
        row = next(csv.DictReader(input_file))
    with candidate_csv.open("a", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(row))
        writer.writerow(row)

    candidates = markdown_cache_classification.load_cached_candidates(candidate_csv)

    assert len(candidates) == 1


def test_cached_candidates_reject_one_path_with_conflicting_blob_identities(tmp_path: Path) -> None:
    candidate_csv = tmp_path / "markdown_filename_files.csv"
    _write_candidate_csv(candidate_csv)
    with candidate_csv.open(encoding="utf-8", newline="") as input_file:
        row = next(csv.DictReader(input_file))
    conflicting = {**row, "blob_sha": "b" * 40}
    with candidate_csv.open("a", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(row))
        writer.writerow(conflicting)

    with pytest.raises(ValueError, match="conflicting cached Markdown candidate"):
        markdown_cache_classification.load_cached_candidates(candidate_csv)


def _write_candidate_csv(path: Path, *, count: int = 1, size_bytes: int = 35) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=(
                "name",
                "lastCommitSHA",
                "markdown_path",
                "blob_sha",
                "size_bytes",
                "markdown_url",
                "matched_filename_terms",
                "matched_content_terms",
                "agent_evidence",
            ),
        )
        writer.writeheader()
        for index in range(count):
            filename = "CONTRIBUTING.md" if index == 0 else f"RULES-{index}.md"
            blob_sha = chr(ord("a") + index) * 40
            writer.writerow(
                {
                    "name": "example/project",
                    "lastCommitSHA": "1" * 40,
                    "markdown_path": filename,
                    "blob_sha": blob_sha,
                    "size_bytes": size_bytes,
                    "markdown_url": f"https://github.com/example/project/blob/revision/{filename}",
                    "matched_filename_terms": "contributing" if index == 0 else "rule",
                    "matched_content_terms": "guideline",
                    "agent_evidence": "False",
                },
            )


def _classification_record(*, provider_result: str) -> dict[str, object]:
    return {
        "custom_id": "candidate-1",
        "input_index": 0,
        "name": "example/project",
        "lastCommitSHA": "1" * 40,
        "markdown_path": "CONTRIBUTING.md",
        "blob_sha": "a" * 40,
        "size_bytes": 35,
        "markdown_url": "https://github.com/example/project/blob/revision/CONTRIBUTING.md",
        "matched_filename_terms": "contributing",
        "matched_content_terms": "guideline",
        "status": "pass",
        "model_label": "YES",
        "model_reason": "A project rule exists.",
        "quote": "Use ProjectNode in src/nodes/.",
        "confidence": 9,
        "reason": "verified_quote",
        "input_tokens": 100,
        "uncached_input_tokens": 100,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 20,
        "total_tokens": 120,
        "cost_usd": 0.1,
        "elapsed_seconds": 1.0,
        "provider_result": provider_result,
    }


def _write_repository_summary(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=(
                "name",
                "lastCommitSHA",
                "status",
                "error",
                "markdown_filename_file_count",
                "markdown_filename_and_content_file_count",
            ),
        )
        writer.writeheader()
        writer.writerows(
            (
                {
                    "name": "example/project",
                    "lastCommitSHA": "1" * 40,
                    "status": "completed",
                    "error": "",
                    "markdown_filename_file_count": 1,
                    "markdown_filename_and_content_file_count": 1,
                },
                {
                    "name": "example/empty",
                    "lastCommitSHA": "2" * 40,
                    "status": "completed",
                    "error": "",
                    "markdown_filename_file_count": 0,
                    "markdown_filename_and_content_file_count": 0,
                },
                {
                    "name": "example/missing",
                    "lastCommitSHA": "3" * 40,
                    "status": "retrieval_error",
                    "error": "missing revision",
                    "markdown_filename_file_count": 0,
                    "markdown_filename_and_content_file_count": 0,
                },
            ),
        )


def _response_document(value: Mapping[str, object]) -> dict[str, object]:
    return {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(value)}],
            },
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
        },
    }
