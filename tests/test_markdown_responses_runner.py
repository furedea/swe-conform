"""Tests for concurrent per-file Markdown classification."""

import csv
import json
from collections.abc import Mapping
from pathlib import Path

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
        workers=2,
    )
    second_client = mocker.Mock()

    report = markdown_responses_runner.run_prepared_classification(
        output_dir=tmp_path,
        client=second_client,
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
        workers=1,
    )

    assert first_report["completed"] == 1
    assert first_report["errors"] == 1
    second_client = mocker.Mock()
    second_client.complete_json.return_value = response

    second_report = markdown_responses_runner.run_prepared_classification(
        output_dir=tmp_path,
        client=second_client,
        workers=1,
    )

    second_client.complete_json.assert_called_once()
    assert second_report["attempted"] == 1
    assert second_report["resumed"] == 1
    assert second_report["completed"] == 2
    assert second_report["errors"] == 0


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
