"""Tests for ordered repository filtering."""

import pytest_mock

import guideline
import license_filter
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


def test_pipeline_skips_license_when_guideline_does_not_pass(mocker: pytest_mock.MockerFixture) -> None:
    guideline_checker = mocker.Mock(spec=guideline.GuidelineChecker)
    guideline_checker.check.return_value = guideline.GuidelineResult(
        status=guideline.GuidelineStatus.REVIEW,
        reason="The document is ambiguous.",
    )
    license_checker = mocker.Mock(spec=license_filter.LicenseChecker)
    repository_filter = pipeline.RepositoryFilter(
        guideline_checker=guideline_checker,
        license_checker=license_checker,
    )

    result = repository_filter.evaluate(_candidate())

    assert result.guideline.status is guideline.GuidelineStatus.REVIEW
    assert result.license.status is license_filter.LicenseStatus.NOT_EVALUATED
    license_checker.check.assert_not_called()


def test_pipeline_checks_license_after_guideline_passes(mocker: pytest_mock.MockerFixture) -> None:
    candidate = _candidate()
    guideline_checker = mocker.Mock(spec=guideline.GuidelineChecker)
    guideline_checker.check.return_value = guideline.GuidelineResult(
        status=guideline.GuidelineStatus.PASS,
        reason="A concrete source-code naming rule is present.",
        evidence_path="CONTRIBUTING.md",
        evidence_quote="Functions must use snake_case.",
    )
    license_checker = mocker.Mock(spec=license_filter.LicenseChecker)
    license_checker.check.return_value = license_filter.LicenseResult(
        status=license_filter.LicenseStatus.PASS,
        spdx_id="MIT",
        reason="SPDX OSI Approved",
    )
    repository_filter = pipeline.RepositoryFilter(
        guideline_checker=guideline_checker,
        license_checker=license_checker,
    )

    result = repository_filter.evaluate(candidate)

    assert result.is_selected
    license_checker.check.assert_called_once_with("MIT License")
