"""Tests for the two-stage guideline organization workflow."""

import csv
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from pytest_mock import MockerFixture

import guideline
import guideline_organization
import main
import openai_responses_client


def test_preparing_extraction_resolves_every_file_for_the_selected_repositories(tmp_path: Path) -> None:
    source_root = tmp_path / "manual-review"
    first_content = "# Rules\n\nFooNode implementations must live in src/nodes/.\n"
    second_content = "# Tests\n\nTests for FooNode must live in tests/nodes/.\n"
    _write_source(source_root / "alpha--one" / "CONTRIBUTING.md", first_content)
    _write_source(source_root / "beta--two" / "docs__TESTING.md", second_content)
    guideline_files_path = tmp_path / "guideline_files.csv"
    _write_csv(
        guideline_files_path,
        (
            _guideline_row(
                repository="beta/two",
                revision="b" * 40,
                file="beta--two/docs__TESTING.md",
            ),
            _guideline_row(
                repository="gamma/three",
                revision="c" * 40,
                file="gamma--three/AGENTS.md",
            ),
            _guideline_row(
                repository="alpha/one",
                revision="a" * 40,
                file="alpha--one/CONTRIBUTING.md",
            ),
        ),
    )
    repositories_path = tmp_path / "verified_repositories.csv"
    _write_csv(repositories_path, ({"repository": "beta/two"}, {"repository": "alpha/one"}))
    output_dir = tmp_path / "organization"

    report = guideline_organization.prepare_extraction(
        guideline_files_path=guideline_files_path,
        repository_list_path=repositories_path,
        source_root=source_root,
        output_dir=output_dir,
        expected_repositories=2,
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
    )

    assert report.repositories == 2
    assert report.files == 2
    manifest = _read_csv(output_dir / "source_manifest.csv")
    assert [row["repository"] for row in manifest] == ["alpha/one", "beta/two"]
    assert manifest[0]["sha256"] == hashlib.sha256(first_content.encode()).hexdigest()
    requests = _read_jsonl(output_dir / "extraction" / "batch_input.jsonl")
    assert [request["custom_id"] for request in requests] == ["source-0001", "source-0002"]
    first_input = _request_input(requests[0])
    assert first_input["repository"] == "alpha/one"
    assert first_input["content"] == ("L1\t# Rules\nL2\t\nL3\tFooNode implementations must live in src/nodes/.\nL4\t")
    configuration = json.loads((output_dir / "run_configuration.json").read_text(encoding="utf-8"))
    assert configuration["repository_count"] == 2
    assert configuration["file_count"] == 2
    assert configuration["max_output_tokens"] == 32_000


def test_preparing_extraction_rejects_a_selected_repository_without_an_accepted_file(tmp_path: Path) -> None:
    guideline_files_path = tmp_path / "guideline_files.csv"
    _write_csv(
        guideline_files_path,
        (
            _guideline_row(
                repository="alpha/one",
                revision="a" * 40,
                file="alpha--one/CONTRIBUTING.md",
            ),
        ),
    )
    repositories_path = tmp_path / "verified_repositories.csv"
    _write_csv(repositories_path, ({"repository": "missing/repository"},))

    with pytest.raises(ValueError, match="no accepted guideline files"):
        guideline_organization.prepare_extraction(
            guideline_files_path=guideline_files_path,
            repository_list_path=repositories_path,
            source_root=tmp_path / "manual-review",
            output_dir=tmp_path / "organization",
            expected_repositories=1,
            model="gpt-5.6-luna",
            reasoning_effort="max",
            max_output_tokens=32_000,
        )

    assert not (tmp_path / "organization").exists()


def test_preparing_judgment_validates_ranges_and_materializes_source_backed_candidates(tmp_path: Path) -> None:
    output_dir = _prepared_extraction(tmp_path)
    extraction_value = {
        "candidates": [
            {
                "evidence_start_line": 3,
                "evidence_end_line": 3,
                "context_start_line": 1,
                "context_end_line": 3,
                "constraint": "FooNode implementations must live in src/nodes/.",
            },
            {
                "evidence_start_line": 3,
                "evidence_end_line": 3,
                "context_start_line": 3,
                "context_end_line": 3,
                "constraint": "FooNode implementations must not live outside src/nodes/.",
            },
        ],
    }
    _write_jsonl(
        output_dir / "extraction" / "responses_output.jsonl",
        (_response_result("source-0001", extraction_value),),
    )

    report = guideline_organization.prepare_judgment(output_dir=output_dir)

    assert report.sources == 1
    assert report.candidates == 2
    candidates = _read_jsonl(output_dir / "extraction" / "candidates.jsonl")
    assert [candidate["candidate_id"] for candidate in candidates] == [
        "source-0001-rule-001",
        "source-0001-rule-002",
    ]
    assert candidates[0]["evidence_quote"] == "FooNode implementations must live in src/nodes/."
    assert candidates[0]["context_quote"] == "# Rules\n\nFooNode implementations must live in src/nodes/."
    requests = _read_jsonl(output_dir / "judgment" / "batch_input.jsonl")
    judgment_input = _request_input(requests[0])
    judgment_candidates = cast(list[Mapping[str, object]], judgment_input["candidates"])
    assert [candidate["candidate_id"] for candidate in judgment_candidates] == [
        "source-0001-rule-001",
        "source-0001-rule-002",
    ]
    assert "extraction_reason" not in judgment_input


def test_preparing_judgment_rejects_source_content_changed_after_extraction(tmp_path: Path) -> None:
    output_dir = _prepared_extraction(tmp_path)
    manifest = _read_csv(output_dir / "source_manifest.csv")
    Path(manifest[0]["local_path"]).write_text("# Changed after extraction\n", encoding="utf-8")
    _write_jsonl(
        output_dir / "extraction" / "responses_output.jsonl",
        (_response_result("source-0001", {"candidates": []}),),
    )

    with pytest.raises(ValueError, match="source content changed"):
        guideline_organization.prepare_judgment(output_dir=output_dir)

    assert not (output_dir / "judgment").exists()


def test_running_extraction_persists_resumable_raw_results_and_usage(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    output_dir = _prepared_extraction(tmp_path)
    value = {"candidates": []}
    document = _response_body(_response_result("source-0001", value))
    client = mocker.Mock()
    client.complete_json.return_value = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
        document=document,
    )

    report = guideline_organization.run_stage(
        output_dir=output_dir,
        stage="extraction",
        client=client,
        provider="bedrock",
        region="us-east-1",
        workers=2,
    )

    assert report["requested"] == 1
    assert report["completed"] == 1
    assert report["errors"] == 0
    outputs = _read_jsonl(output_dir / "extraction" / "responses_output.jsonl")
    assert outputs[0]["custom_id"] == "source-0001"
    assert (output_dir / "extraction" / "responses_checkpoint.jsonl").is_file()


def test_running_extraction_records_invalid_source_ranges_as_retryable_model_errors(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    output_dir = _prepared_extraction(tmp_path)
    value = {
        "candidates": [
            {
                "evidence_start_line": 999,
                "evidence_end_line": 999,
                "context_start_line": 999,
                "context_end_line": 999,
                "constraint": "A fabricated out-of-range constraint.",
            },
        ],
    }
    document = _response_body(_response_result("source-0001", value))
    client = mocker.Mock()
    client.complete_json.return_value = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
        document=document,
    )

    report = guideline_organization.run_stage(
        output_dir=output_dir,
        stage="extraction",
        client=client,
        provider="bedrock",
        region="us-east-1",
        workers=2,
    )

    assert report["completed"] == 0
    assert report["errors"] == 1
    checkpoint = _read_jsonl(output_dir / "extraction" / "responses_checkpoint.jsonl")
    assert checkpoint[0]["response"] is None
    assert "invalid or non-enclosing line ranges" in _error_message(checkpoint[0])


def test_finalizing_uses_the_conjunction_of_every_project_rule_and_selection_condition(tmp_path: Path) -> None:
    output_dir = _prepared_extraction(tmp_path)
    extraction_value = {
        "candidates": [
            {
                "evidence_start_line": 3,
                "evidence_end_line": 3,
                "context_start_line": 1,
                "context_end_line": 3,
                "constraint": "FooNode implementations must live in src/nodes/.",
            },
            {
                "evidence_start_line": 3,
                "evidence_end_line": 3,
                "context_start_line": 3,
                "context_end_line": 3,
                "constraint": "FooNode implementations should use an appropriate design.",
            },
        ],
    }
    _write_jsonl(
        output_dir / "extraction" / "responses_output.jsonl",
        (_response_result("source-0001", extraction_value),),
    )
    guideline_organization.prepare_judgment(output_dir=output_dir)
    judgment_value = {
        "judgments": [
            _judgment("source-0001-rule-001"),
            _judgment("source-0001-rule-002", objective=False),
        ],
    }
    _write_jsonl(
        output_dir / "judgment" / "responses_output.jsonl",
        (_response_result("source-0001", judgment_value),),
    )

    report = guideline_organization.finalize_organization(output_dir=output_dir)

    assert report.candidates == 2
    assert report.accepted == 1
    assert report.rejected == 1
    accepted = _read_jsonl(output_dir / "final" / "accepted_rules.jsonl")
    assert [row["candidate_id"] for row in accepted] == ["source-0001-rule-001"]
    rejected = _read_csv(output_dir / "final" / "rejected_candidates.csv")
    assert rejected[0]["candidate_id"] == "source-0001-rule-002"
    assert rejected[0]["objective"] == "False"
    checklist = _read_csv(output_dir / "manual-review" / "candidate_checklist.csv")
    assert [row["llm_decision"] for row in checklist] == ["pass", "not_found"]
    assert {row["human_decision"] for row in checklist} == {""}
    file_checklist = _read_csv(output_dir / "manual-review" / "file_checklist.csv")
    assert file_checklist[0]["extracted_candidates"] == "2"
    assert file_checklist[0]["accepted_candidates"] == "1"


def test_an_experiment_with_no_extracted_candidates_skips_judgment_and_still_finalizes(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    output_dir = _prepared_extraction(tmp_path)
    _write_jsonl(
        output_dir / "extraction" / "responses_output.jsonl",
        (_response_result("source-0001", {"candidates": []}),),
    )
    guideline_organization.prepare_judgment(output_dir=output_dir)
    client = mocker.Mock()

    run_report = guideline_organization.run_stage(
        output_dir=output_dir,
        stage="judgment",
        client=client,
        provider="bedrock",
        region="us-east-1",
        workers=2,
    )
    final_report = guideline_organization.finalize_organization(output_dir=output_dir)

    client.complete_json.assert_not_called()
    assert run_report["requested"] == 0
    assert final_report.candidates == 0
    assert final_report.accepted == 0
    file_checklist = _read_csv(output_dir / "manual-review" / "file_checklist.csv")
    assert file_checklist[0]["extracted_candidates"] == "0"


def test_running_judgment_records_missing_candidate_ids_as_retryable_model_errors(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    output_dir = _prepared_extraction(tmp_path)
    extraction_value = {
        "candidates": [
            {
                "evidence_start_line": 3,
                "evidence_end_line": 3,
                "context_start_line": 3,
                "context_end_line": 3,
                "constraint": "FooNode implementations must live in src/nodes/.",
            },
        ],
    }
    _write_jsonl(
        output_dir / "extraction" / "responses_output.jsonl",
        (_response_result("source-0001", extraction_value),),
    )
    guideline_organization.prepare_judgment(output_dir=output_dir)
    value = {"judgments": [_judgment("wrong-candidate-id")]}
    document = _response_body(_response_result("source-0001", value))
    client = mocker.Mock()
    client.complete_json.return_value = openai_responses_client.JsonResponse(
        value=value,
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
        document=document,
    )

    report = guideline_organization.run_stage(
        output_dir=output_dir,
        stage="judgment",
        client=client,
        provider="bedrock",
        region="us-east-1",
        workers=2,
    )

    assert report["completed"] == 0
    assert report["errors"] == 1
    checkpoint = _read_jsonl(output_dir / "judgment" / "responses_checkpoint.jsonl")
    assert checkpoint[0]["response"] is None
    assert "candidate IDs do not match" in _error_message(checkpoint[0])


def test_cli_prepares_a_verified_organization_experiment(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    prepare = mocker.patch(
        "main.guideline_organization.prepare_extraction",
        autospec=True,
        return_value=guideline_organization.OrganizationPreparation(
            repositories=12,
            files=43,
            output_dir=tmp_path / "organization",
        ),
    )

    main.main(
        [
            "organize-guidelines",
            "prepare",
            "--guideline-files-csv",
            "collection/final/guideline_files.csv",
            "--repository-list",
            "verified_repositories.csv",
            "--source-root",
            "collection/manual-review",
            "--output-dir",
            str(tmp_path / "organization"),
            "--expected-repositories",
            "12",
        ],
    )

    prepare.assert_called_once_with(
        guideline_files_path=Path("collection/final/guideline_files.csv"),
        repository_list_path=Path("verified_repositories.csv"),
        source_root=Path("collection/manual-review"),
        output_dir=tmp_path / "organization",
        expected_repositories=12,
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
    )
    output = json.loads(capsys.readouterr().out)
    assert output["repositories"] == 12
    assert output["files"] == 43


def _guideline_row(*, repository: str, revision: str, file: str) -> dict[str, str]:
    return {
        "repository": repository,
        "revision": revision,
        "file": file,
        "github_url": f"https://github.com/{repository}/blob/{revision}/{file.split('/', 1)[1]}",
    }


def _prepared_extraction(tmp_path: Path) -> Path:
    source_root = tmp_path / "manual-review"
    _write_source(
        source_root / "alpha--one" / "CONTRIBUTING.md",
        "# Rules\n\nFooNode implementations must live in src/nodes/.\n",
    )
    guideline_files_path = tmp_path / "guideline_files.csv"
    _write_csv(
        guideline_files_path,
        (
            _guideline_row(
                repository="alpha/one",
                revision="a" * 40,
                file="alpha--one/CONTRIBUTING.md",
            ),
        ),
    )
    repositories_path = tmp_path / "verified_repositories.csv"
    _write_csv(repositories_path, ({"repository": "alpha/one"},))
    output_dir = tmp_path / "organization"
    guideline_organization.prepare_extraction(
        guideline_files_path=guideline_files_path,
        repository_list_path=repositories_path,
        source_root=source_root,
        output_dir=output_dir,
        expected_repositories=1,
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=32_000,
    )
    return output_dir


def _response_result(custom_id: str, value: Mapping[str, object]) -> dict[str, object]:
    return {
        "custom_id": custom_id,
        "response": {
            "status_code": 200,
            "body": {
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
            },
        },
        "error": None,
    }


def _judgment(candidate_id: str, *, objective: bool = True) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "in_scope": True,
        "persistent": True,
        "concrete": True,
        "atomic": True,
        "diff_closed": True,
        "objective": objective,
        "grounded": True,
        "reason": "All criteria pass." if objective else "The requirement is subjective.",
    }


def _write_source(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_csv(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as input_file:
        return list(csv.DictReader(input_file))


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_jsonl(path: Path, documents: tuple[dict[str, object], ...]) -> None:
    value = "".join(f"{json.dumps(document)}\n" for document in documents)
    path.write_text(value, encoding="utf-8")


def _request_input(request: Mapping[str, object]) -> Mapping[str, object]:
    body = cast(Mapping[str, object], request["body"])
    return cast(Mapping[str, object], json.loads(str(body["input"])))


def _response_body(result: Mapping[str, object]) -> Mapping[str, object]:
    response = cast(Mapping[str, object], result["response"])
    return cast(Mapping[str, object], response["body"])


def _error_message(result: Mapping[str, object]) -> str:
    error = cast(Mapping[str, object], result["error"])
    return str(error["message"])
