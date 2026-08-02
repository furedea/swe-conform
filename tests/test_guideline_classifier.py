"""Tests for evidence-backed one-shot guideline classification."""

import pytest_mock

import guideline
import guideline_classifier
import guideline_evidence
import openai_responses_client
import repository


def _candidate() -> repository.RepositoryCandidate:
    return repository.RepositoryCandidate(
        repository="example/project",
        revision="0123456789abcdef",
        license_name="MIT License",
        source_file="python.csv",
        input_index=0,
        fields={"name": "example/project"},
    )


def test_checker_skips_model_when_complete_tree_has_no_candidate_documents(
    mocker: pytest_mock.MockerFixture,
) -> None:
    collector = mocker.Mock(spec=guideline_evidence.GuidelineEvidenceCollector)
    collector.collect.return_value = guideline_evidence.RepositoryEvidence(
        documents=(),
        tree_truncated=False,
    )
    model_client = mocker.Mock(spec=openai_responses_client.OpenAIResponsesClient)
    checker = guideline_classifier.ModelGuidelineChecker(
        collector=collector,
        model_client=model_client,
        model="gpt-5.6-luna",
    )

    result = checker.check(_candidate())

    assert result.status is guideline.GuidelineStatus.NOT_FOUND
    assert not result.model_called
    model_client.complete_json.assert_not_called()


def test_checker_requires_manual_review_when_empty_tree_is_truncated(
    mocker: pytest_mock.MockerFixture,
) -> None:
    collector = mocker.Mock(spec=guideline_evidence.GuidelineEvidenceCollector)
    collector.collect.return_value = guideline_evidence.RepositoryEvidence(
        documents=(),
        tree_truncated=True,
    )
    model_client = mocker.Mock(spec=openai_responses_client.OpenAIResponsesClient)
    checker = guideline_classifier.ModelGuidelineChecker(
        collector=collector,
        model_client=model_client,
        model="gpt-5.6-luna",
    )

    result = checker.check(_candidate())

    assert result.status is guideline.GuidelineStatus.REVIEW
    model_client.complete_json.assert_not_called()


def test_checker_uses_project_specific_guideline_contract(
    mocker: pytest_mock.MockerFixture,
) -> None:
    collector = mocker.Mock(spec=guideline_evidence.GuidelineEvidenceCollector)
    collector.collect.return_value = guideline_evidence.RepositoryEvidence(
        documents=(
            guideline_evidence.GuidelineDocument(
                path="CONTRIBUTING.md",
                content="Parser nodes must be created through NodeFactory so source spans remain attached.",
            ),
        ),
        tree_truncated=False,
    )
    model_client = mocker.Mock(spec=openai_responses_client.OpenAIResponsesClient)
    model_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value={
            "status": "pass",
            "reason": "The rule constrains construction of this project's parser nodes.",
            "evidence_path": "CONTRIBUTING.md",
            "evidence_quote": "Parser nodes must be created through NodeFactory",
        },
        usage=guideline.TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
    )
    checker = guideline_classifier.ModelGuidelineChecker(
        collector=collector,
        model_client=model_client,
        model="gpt-5.6-luna",
    )

    result = checker.check(_candidate())

    assert result.status is guideline.GuidelineStatus.PASS
    assert result.evidence_path == "CONTRIBUTING.md"
    assert result.model_called
    assert model_client.complete_json.call_args.kwargs["model"] == "gpt-5.6-luna"
    instructions = model_client.complete_json.call_args.kwargs["instructions"]
    assert "human review" in instructions
    assert "project-specific implementation guideline" in instructions
    assert "counterfactual test" in instructions
    assert "named or unnamed general coding standards" in instructions
    assert "configuration files" in instructions
    assert "describes how a public API behaves" in instructions
    assert "consumers use it" in instructions
    assert "project-specific developer, design, or coding guide" in instructions
    assert "generic contributing, setup, or build guide" in instructions
    assert "Never remove, replace, or join across line breaks" in instructions
    assert "declarative adoption statement" not in instructions


def test_checker_downgrades_unverifiable_pass_to_review(mocker: pytest_mock.MockerFixture) -> None:
    collector = mocker.Mock(spec=guideline_evidence.GuidelineEvidenceCollector)
    collector.collect.return_value = guideline_evidence.RepositoryEvidence(
        documents=(guideline_evidence.GuidelineDocument(path="CONTRIBUTING.md", content="Run tests."),),
        tree_truncated=False,
    )
    model_client = mocker.Mock(spec=openai_responses_client.OpenAIResponsesClient)
    model_client.complete_json.return_value = openai_responses_client.JsonResponse(
        value={
            "status": "pass",
            "reason": "A naming rule exists.",
            "evidence_path": "CONTRIBUTING.md",
            "evidence_quote": "Functions must use snake_case.",
        },
        usage=guideline.TokenUsage(),
    )
    checker = guideline_classifier.ModelGuidelineChecker(
        collector=collector,
        model_client=model_client,
        model="gpt-5.6-luna",
    )

    result = checker.check(_candidate())

    assert result.status is guideline.GuidelineStatus.REVIEW
    assert "verified" in result.reason
