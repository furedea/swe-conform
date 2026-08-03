"""Tests for project-guideline repository filtering."""

import pytest_mock

import guideline
import pipeline
import repository


def _candidate(*, license_name: str = "MIT License") -> repository.RepositoryCandidate:
    return repository.RepositoryCandidate(
        repository="example/project",
        revision="0123456789abcdef",
        license_name=license_name,
        source_file="python.csv",
        input_index=0,
        fields={"name": "example/project"},
    )


def test_pipeline_preserves_a_guideline_non_pass(mocker: pytest_mock.MockerFixture) -> None:
    guideline_checker = mocker.Mock(spec=guideline.GuidelineChecker)
    guideline_checker.check.return_value = guideline.GuidelineResult(
        status=guideline.GuidelineStatus.REVIEW,
        reason="The document is ambiguous.",
    )
    repository_filter = pipeline.RepositoryFilter(guideline_checker=guideline_checker)

    result = repository_filter.evaluate(_candidate())

    assert result.guideline.status is guideline.GuidelineStatus.REVIEW
    assert not result.is_selected


def test_pipeline_selects_every_guideline_pass_without_license_filtering(mocker: pytest_mock.MockerFixture) -> None:
    candidate = _candidate()
    guideline_checker = mocker.Mock(spec=guideline.GuidelineChecker)
    guideline_checker.check.return_value = guideline.GuidelineResult(
        status=guideline.GuidelineStatus.PASS,
        reason="A concrete source-code naming rule is present.",
        evidence=(
            guideline.GuidelineEvidence(
                path="CONTRIBUTING.md",
                quote="Functions must use snake_case.",
                content=b"Functions must use snake_case.\n",
            ),
        ),
    )
    repository_filter = pipeline.RepositoryFilter(guideline_checker=guideline_checker)

    result = repository_filter.evaluate(candidate)

    assert result.is_selected
