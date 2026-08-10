"""Tests for repository-wide guideline classification."""

import json
from contextlib import nullcontext
from pathlib import Path

import pytest_mock

import guideline
import guideline_classifier
import openai_responses_client
import repository
import repository_workspace


def _candidate() -> repository.RepositoryCandidate:
    return repository.RepositoryCandidate(
        repository="example/project",
        revision="0123456789abcdef",
        license_name="MIT License",
        source_file="python.csv",
        input_index=0,
        fields={"name": "example/project"},
    )


def test_checker_explores_the_repository_with_the_candidate_guideline_contract(
    mocker: pytest_mock.MockerFixture,
    tmp_path: Path,
) -> None:
    repository_path = tmp_path / "repository"
    document_path = repository_path / "docs" / "api_conventions.md"
    document_path.parent.mkdir(parents=True)
    document_path.write_text(
        "API objects must separate desired and observed state into spec and status.\n",
        encoding="utf-8",
    )
    test_document_path = repository_path / "docs" / "testing.md"
    test_document_path.write_text(
        "Integration tests must use the shared cluster fixture.\n",
        encoding="utf-8",
    )
    workspace = mocker.Mock(spec=repository_workspace.GitRepositoryWorkspace)
    workspace.checkout.return_value = nullcontext(tmp_path)
    model_client = mocker.Mock(spec=guideline_classifier.StructuredModelClient)
    model_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value={
            "status": "pass",
            "evidence": [
                {
                    "path": "docs/api_conventions.md",
                    "quote": "API objects must separate desired and observed state into spec and status.",
                },
                {
                    "path": "docs/testing.md",
                    "quote": "Integration tests must use the shared cluster fixture.",
                },
            ],
        },
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
    )
    mocker.patch("guideline_classifier.time.monotonic", side_effect=[10.0, 12.5, 20.0])
    checker = guideline_classifier.ModelGuidelineChecker(
        workspace=workspace,
        model_client=model_client,
        model="gpt-5.6-luna",
        reasoning_effort="max",
    )

    result = checker.check(_candidate())

    workspace.checkout.assert_called_once_with("example/project", "0123456789abcdef")
    call = model_client.complete_json.call_args
    assert call.kwargs["working_directory"] == tmp_path
    assert call.kwargs["input_text"] == "Inspect the repository snapshot in repository/."
    instructions = call.kwargs["instructions"]
    prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "repository_guideline_exploration.md"
    assert instructions == prompt_path.read_text(encoding="utf-8")
    schema = call.kwargs["schema"]
    schema_path = Path(__file__).resolve().parents[1] / "prompts" / "repository_guideline_exploration_schema.json"
    assert schema == json.loads(schema_path.read_text(encoding="utf-8"))
    assert result.status is guideline.GuidelineStatus.PASS
    assert [item.path for item in result.evidence] == ["docs/api_conventions.md", "docs/testing.md"]
    assert result.evidence[0].content == document_path.read_bytes()
    assert result.checkout_seconds == 2.5
    assert result.model_seconds == 7.5


def test_checker_downgrades_unverifiable_pass_to_review(
    mocker: pytest_mock.MockerFixture,
    tmp_path: Path,
) -> None:
    (tmp_path / "repository").mkdir()
    workspace = mocker.Mock(spec=repository_workspace.GitRepositoryWorkspace)
    workspace.checkout.return_value = nullcontext(tmp_path)
    model_client = mocker.Mock(spec=guideline_classifier.StructuredModelClient)
    model_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value={
            "status": "pass",
            "evidence": [
                {
                    "path": "CONTRIBUTING.md",
                    "quote": "Functions must use snake_case.",
                },
            ],
        },
        usage=guideline.TokenUsage(),
    )
    checker = guideline_classifier.ModelGuidelineChecker(
        workspace=workspace,
        model_client=model_client,
        model="gpt-5.6-luna",
    )

    result = checker.check(_candidate())

    assert result.status is guideline.GuidelineStatus.REVIEW
    assert "verified" in result.reason


def test_checker_reviews_the_result_when_any_evidence_item_is_unverifiable(
    mocker: pytest_mock.MockerFixture,
    tmp_path: Path,
) -> None:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    document_path = repository_path / "CONTRIBUTING.md"
    document_path.write_text("Functions must use snake_case.\n", encoding="utf-8")
    workspace = mocker.Mock(spec=repository_workspace.GitRepositoryWorkspace)
    workspace.checkout.return_value = nullcontext(tmp_path)
    model_client = mocker.Mock(spec=guideline_classifier.StructuredModelClient)
    model_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value={
            "status": "pass",
            "evidence": [
                {
                    "path": "CONTRIBUTING.md",
                    "quote": "Functions must use snake_case.",
                },
                {
                    "path": "missing.md",
                    "quote": "Tests must use the shared fixture.",
                },
            ],
        },
        usage=guideline.TokenUsage(),
    )
    checker = guideline_classifier.ModelGuidelineChecker(
        workspace=workspace,
        model_client=model_client,
        model="gpt-5.6-luna",
    )

    result = checker.check(_candidate())

    assert result.status is guideline.GuidelineStatus.REVIEW
    assert [evidence.path for evidence in result.evidence] == ["CONTRIBUTING.md"]
    assert [(issue.index, issue.reason) for issue in result.evidence_issues] == [
        (2, "path is not a file"),
    ]


def test_checker_retains_the_structured_model_response_for_audit(
    mocker: pytest_mock.MockerFixture,
    tmp_path: Path,
) -> None:
    (tmp_path / "repository").mkdir()
    workspace = mocker.Mock(spec=repository_workspace.GitRepositoryWorkspace)
    workspace.checkout.return_value = nullcontext(tmp_path)
    model_client = mocker.Mock(spec=guideline_classifier.StructuredModelClient)
    model_response = {"status": "not_found", "evidence": []}
    model_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value=model_response,
        usage=guideline.TokenUsage(),
    )
    checker = guideline_classifier.ModelGuidelineChecker(
        workspace=workspace,
        model_client=model_client,
        model="gpt-5.6-luna",
    )

    result = checker.check(_candidate())

    assert json.loads(result.model_response_json) == model_response


def test_checker_normalizes_verified_evidence_paths(
    mocker: pytest_mock.MockerFixture,
    tmp_path: Path,
) -> None:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    document_path = repository_path / "CONTRIBUTING.md"
    document_path.write_text("Changes must preserve API compatibility.\n", encoding="utf-8")
    workspace = mocker.Mock(spec=repository_workspace.GitRepositoryWorkspace)
    workspace.checkout.return_value = nullcontext(tmp_path)
    model_client = mocker.Mock(spec=guideline_classifier.StructuredModelClient)
    model_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value={
            "status": "pass",
            "evidence": [
                {
                    "path": "docs/../CONTRIBUTING.md",
                    "quote": "Changes must preserve API compatibility.",
                },
            ],
        },
        usage=guideline.TokenUsage(),
    )
    checker = guideline_classifier.ModelGuidelineChecker(
        workspace=workspace,
        model_client=model_client,
        model="gpt-5.6-luna",
    )

    result = checker.check(_candidate())

    assert result.status is guideline.GuidelineStatus.PASS
    assert [evidence.path for evidence in result.evidence] == ["CONTRIBUTING.md"]


def test_checker_reviews_not_found_with_any_evidence(
    mocker: pytest_mock.MockerFixture,
    tmp_path: Path,
) -> None:
    (tmp_path / "repository").mkdir()
    workspace = mocker.Mock(spec=repository_workspace.GitRepositoryWorkspace)
    workspace.checkout.return_value = nullcontext(tmp_path)
    model_client = mocker.Mock(spec=guideline_classifier.StructuredModelClient)
    model_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value={
            "status": "not_found",
            "evidence": [{"path": "missing.md", "quote": "A rule that does not exist."}],
        },
        usage=guideline.TokenUsage(),
    )
    checker = guideline_classifier.ModelGuidelineChecker(
        workspace=workspace,
        model_client=model_client,
        model="gpt-5.6-luna",
    )

    result = checker.check(_candidate())

    assert result.status is guideline.GuidelineStatus.REVIEW
    assert "not_found" in result.reason
