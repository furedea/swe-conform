"""Tests for concurrent per-file Markdown classification."""

import csv
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

import guideline
import markdown_responses_runner
import openai_responses_client


def test_runner_classifies_each_prepared_file_and_sums_provider_cost(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    _write_prepared_files(tmp_path)
    client = mocker.Mock()

    def complete_json(**arguments: object) -> openai_responses_client.JsonResponse:
        input_document = json.loads(str(arguments["input_text"]))
        is_rule = "snake_case" in input_document["content"]
        value = {
            "label": "YES" if is_rule else "NO",
            "reason": "The document contains a rule." if is_rule else "The document contains no rule.",
            "quote": "Use snake_case." if is_rule else "",
            "confidence": 9,
        }
        cost_usd = 0.000012 if is_rule else 0.000008
        document = _response_document(value, cost_usd=cost_usd)
        return openai_responses_client.JsonResponse(
            value=value,
            usage=guideline.TokenUsage(input_tokens=100, output_tokens=10, total_tokens=110),
            cost_usd=cost_usd,
            document=document,
        )

    client.complete_json.side_effect = complete_json

    report = markdown_responses_runner.run_prepared_classification(
        output_dir=tmp_path,
        client=client,
        provider="openrouter",
        region=None,
        workers=2,
    )

    rows = list(csv.DictReader((tmp_path / "classified_files.csv").open(encoding="utf-8", newline="")))
    assert [row["status"] for row in rows] == ["pass", "not_found"]
    assert client.complete_json.call_count == 2
    assert report["requested"] == 2
    assert report["completed"] == 2
    assert report["errors"] == 0
    assert report["provider_reported_cost_usd"] == 0.00002
    assert float(str(report["elapsed_seconds"])) >= 0
    assert (tmp_path / "responses_checkpoint.jsonl").exists()


def test_runner_records_the_complete_execution_configuration(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    _write_prepared_files(tmp_path)
    client = mocker.Mock()
    value = {
        "label": "NO",
        "reason": "The document contains no rule.",
        "quote": "",
        "confidence": 9,
    }
    client.complete_json.return_value = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=10, total_tokens=110),
        document=_response_document(value, cost_usd=0.000008),
    )

    report = markdown_responses_runner.run_prepared_classification(
        output_dir=tmp_path,
        client=client,
        provider="bedrock",
        region="us-east-1",
        workers=2,
    )

    assert report["provider"] == "bedrock"
    assert report["region"] == "us-east-1"
    assert report["requested_model"] == "gpt-5.6-luna"
    assert report["provider_model"] == "openai.gpt-5.6-luna"
    assert report["reasoning_effort"] == "max"
    assert report["max_output_tokens"] == 500
    assert report["workers"] == 2
    execution = json.loads((tmp_path / "responses_execution.json").read_text(encoding="utf-8"))
    assert execution["requested_model"] == "gpt-5.6-luna"
    assert execution["provider_model"] == "openai.gpt-5.6-luna"
    assert execution["reasoning_effort"] == "max"
    assert execution["max_output_tokens"] == 500
    assert execution["workers"] == 2


def test_runner_calculates_bedrock_luna_cost_from_usage(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    _write_prepared_files(tmp_path)
    client = mocker.Mock()
    value = {
        "label": "NO",
        "reason": "The document contains no rule.",
        "quote": "",
        "confidence": 9,
    }
    document = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(value)}],
            },
        ],
        "usage": {
            "input_tokens": 4_000,
            "input_tokens_details": {
                "cached_tokens": 1_920,
                "cache_write_tokens": 1_024,
            },
            "output_tokens": 300,
            "total_tokens": 4_300,
        },
    }
    client.complete_json.return_value = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(
            input_tokens=4_000,
            output_tokens=300,
            total_tokens=4_300,
            cached_input_tokens=1_920,
            cache_write_input_tokens=1_024,
        ),
        document=document,
    )

    report = markdown_responses_runner.run_prepared_classification(
        output_dir=tmp_path,
        client=client,
        provider="bedrock",
        region="us-east-1",
        workers=2,
    )

    rows = list(csv.DictReader((tmp_path / "classified_files.csv").open(encoding="utf-8", newline="")))
    assert {row["cost_usd"] for row in rows} == {"0.00095216"}
    assert report["calculated_pilot_cost_usd"] == 0.001904
    assert report["provider_reported_cost_usd"] is None
    assert report["cost_source"] == "bedrock_published_pricing"
    assert report["average_completed_cost_usd"] == 0.00095216
    assert report["short_context_requests"] == 2
    assert report["long_context_requests"] == 0


def test_runner_reuses_successful_checkpoint_results(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    _write_prepared_files(tmp_path)
    first_client = mocker.Mock()
    value = {
        "label": "NO",
        "reason": "The document contains no rule.",
        "quote": "",
        "confidence": 9,
    }
    document = _response_document(value, cost_usd=0.000008)
    first_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=10, total_tokens=110),
        cost_usd=0.000008,
        document=document,
    )
    markdown_responses_runner.run_prepared_classification(
        output_dir=tmp_path,
        client=first_client,
        provider="openrouter",
        region=None,
        workers=2,
    )
    second_client = mocker.Mock()

    report = markdown_responses_runner.run_prepared_classification(
        output_dir=tmp_path,
        client=second_client,
        provider="openrouter",
        region=None,
        workers=2,
    )

    second_client.complete_json.assert_not_called()
    assert report["attempted"] == 0
    assert report["resumed"] == 2
    assert report["provider_reported_cost_usd"] == 0.000016


def test_runner_retries_only_failed_checkpoint_results(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    _write_prepared_files(tmp_path)
    value = {
        "label": "NO",
        "reason": "The document contains no rule.",
        "quote": "",
        "confidence": 9,
    }
    response = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=10, total_tokens=110),
        cost_usd=0.000008,
        document=_response_document(value, cost_usd=0.000008),
    )
    first_client = mocker.Mock()
    first_client.complete_json.side_effect = (response, TimeoutError("request timed out"))

    first_report = markdown_responses_runner.run_prepared_classification(
        output_dir=tmp_path,
        client=first_client,
        provider="openrouter",
        region=None,
        workers=1,
    )

    assert first_report["completed"] == 1
    assert first_report["errors"] == 1
    second_client = mocker.Mock()
    second_client.complete_json.return_value = response

    second_report = markdown_responses_runner.run_prepared_classification(
        output_dir=tmp_path,
        client=second_client,
        provider="openrouter",
        region=None,
        workers=1,
    )

    second_client.complete_json.assert_called_once()
    assert second_report["attempted"] == 1
    assert second_report["resumed"] == 1
    assert second_report["completed"] == 2
    assert second_report["errors"] == 0


def test_runner_rejects_a_checkpoint_from_another_provider(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    _write_prepared_files(tmp_path)
    value = {
        "label": "NO",
        "reason": "The document contains no rule.",
        "quote": "",
        "confidence": 9,
    }
    response = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=10, total_tokens=110),
        cost_usd=0.000008,
        document=_response_document(value, cost_usd=0.000008),
    )
    first_client = mocker.Mock()
    first_client.complete_json.return_value = response
    markdown_responses_runner.run_prepared_classification(
        output_dir=tmp_path,
        client=first_client,
        provider="openrouter",
        region=None,
        workers=2,
    )
    second_client = mocker.Mock()

    with pytest.raises(ValueError, match="execution configuration does not match"):
        markdown_responses_runner.run_prepared_classification(
            output_dir=tmp_path,
            client=second_client,
            provider="bedrock",
            region="us-east-1",
            workers=2,
        )

    second_client.complete_json.assert_not_called()


def test_runner_rejects_a_checkpoint_for_modified_prepared_input(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    _write_prepared_files(tmp_path)
    value = {
        "label": "NO",
        "reason": "The document contains no rule.",
        "quote": "",
        "confidence": 9,
    }
    response = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=10, total_tokens=110),
        cost_usd=0.000008,
        document=_response_document(value, cost_usd=0.000008),
    )
    first_client = mocker.Mock()
    first_client.complete_json.return_value = response
    markdown_responses_runner.run_prepared_classification(
        output_dir=tmp_path,
        client=first_client,
        provider="openrouter",
        region=None,
        workers=2,
    )
    input_path = tmp_path / "batch_input.jsonl"
    input_path.write_text(f"{input_path.read_text(encoding='utf-8')}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="execution configuration does not match"):
        markdown_responses_runner.run_prepared_classification(
            output_dir=tmp_path,
            client=mocker.Mock(),
            provider="openrouter",
            region=None,
            workers=2,
        )


@pytest.mark.parametrize(
    ("status_code", "message"),
    (
        (400, "status=400 body=invalid response schema"),
        (401, "status=401 body=invalid bearer token"),
        (403, "status=403 body=access denied"),
    ),
)
def test_runner_stops_after_one_shared_preflight_failure(
    mocker: MockerFixture,
    tmp_path: Path,
    status_code: int,
    message: str,
) -> None:
    _write_prepared_files(tmp_path)
    client = mocker.Mock()
    client.complete_json.side_effect = openai_responses_client.ResponsesRequestError(
        message,
        status_code=status_code,
    )

    with pytest.raises(RuntimeError, match="preflight failed"):
        markdown_responses_runner.run_prepared_classification(
            output_dir=tmp_path,
            client=client,
            provider="bedrock",
            region="us-east-1",
            workers=16,
        )

    client.complete_json.assert_called_once()
    checkpoint = (tmp_path / "responses_checkpoint.jsonl").read_text(encoding="utf-8")
    assert f"status={status_code}" in checkpoint


def _write_prepared_files(output_dir: Path) -> None:
    with (output_dir / "sample_manifest.csv").open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(
            (
                "custom_id",
                "stratum",
                "stratum_population",
                "name",
                "lastCommitSHA",
                "markdown_path",
                "markdown_url",
                "matched_filename_terms",
                "size_bytes",
            ),
        )
        writer.writerow(
            (
                "candidate-0001",
                "1",
                "10",
                "example/project",
                "0123456789abcdef",
                "CONTRIBUTING.md",
                "https://example.test/CONTRIBUTING.md",
                "contributing",
                "100",
            ),
        )
        writer.writerow(
            (
                "candidate-0002",
                "2",
                "10",
                "example/project",
                "0123456789abcdef",
                "README.md",
                "https://example.test/README.md",
                "readme",
                "100",
            ),
        )
    requests = (
        _request("candidate-0001", "# Rules\n\nUse snake_case.\n"),
        _request("candidate-0002", "# Usage\n\nInstall the application.\n"),
    )
    (output_dir / "batch_input.jsonl").write_text(
        "".join(f"{json.dumps(request)}\n" for request in requests),
        encoding="utf-8",
    )


def _request(custom_id: str, content: str) -> dict[str, object]:
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/responses",
        "body": {
            "model": "gpt-5.6-luna",
            "instructions": "Classify this Markdown file.",
            "input": json.dumps({"content": content}),
            "reasoning": {"effort": "max"},
            "max_output_tokens": 500,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "code_test_rule_label",
                    "strict": True,
                    "schema": {"type": "object"},
                },
            },
        },
    }


def _response_document(value: Mapping[str, object], *, cost_usd: float) -> dict[str, object]:
    return {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(value)}],
            },
        ],
        "usage": {
            "input_tokens": 100,
            "output_tokens": 10,
            "total_tokens": 110,
            "cost": cost_usd,
        },
    }
