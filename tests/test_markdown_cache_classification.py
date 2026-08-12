"""Tests for direct classification of Markdown blobs from local Git caches."""

import csv
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

import guideline
import markdown_cache_classification
import openai_responses_client


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
    assert {row["name"]: row["status"] for row in rows} == {
        "example/project": "pass",
        "example/empty": "no_candidates",
        "example/missing": "retrieval_error",
    }
    with (output_dir / "selected_repositories.csv").open(encoding="utf-8", newline="") as input_file:
        selected = list(csv.DictReader(input_file))
    assert [row["name"] for row in selected] == ["example/project"]


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


def _write_candidate_csv(path: Path, *, count: int = 1) -> None:
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
                    "size_bytes": 35,
                    "markdown_url": f"https://github.com/example/project/blob/revision/{filename}",
                    "matched_filename_terms": "contributing" if index == 0 else "rule",
                    "matched_content_terms": "guideline",
                    "agent_evidence": "False",
                },
            )


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
