"""Ordered repository selection pipeline."""

from dataclasses import dataclass

import guideline
import license_filter
import repository


@dataclass(frozen=True, slots=True)
class RepositoryResult:
    """Combined guideline-first filtering result for one repository."""

    candidate: repository.RepositoryCandidate
    guideline: guideline.GuidelineResult
    license: license_filter.LicenseResult

    @property
    def is_selected(self) -> bool:
        """Return whether the repository passed both ordered filters."""
        return (
            self.guideline.status is guideline.GuidelineStatus.PASS
            and self.license.status is license_filter.LicenseStatus.PASS
        )


class RepositoryFilter:
    """Apply project-guideline and OSS-license filters in that order."""

    __slots__ = ("_guideline_checker", "_license_checker")

    def __init__(
        self,
        *,
        guideline_checker: guideline.GuidelineChecker,
        license_checker: license_filter.LicenseChecker,
    ) -> None:
        self._guideline_checker = guideline_checker
        self._license_checker = license_checker

    def evaluate(self, candidate: repository.RepositoryCandidate) -> RepositoryResult:
        """Evaluate one repository and short-circuit after guideline rejection."""
        guideline_result = self._guideline_checker.check(candidate)
        license_result = self._check_license(candidate, guideline_result)
        return RepositoryResult(
            candidate=candidate,
            guideline=guideline_result,
            license=license_result,
        )

    def _check_license(
        self,
        candidate: repository.RepositoryCandidate,
        guideline_result: guideline.GuidelineResult,
    ) -> license_filter.LicenseResult:
        if guideline_result.status is guideline.GuidelineStatus.PASS:
            return self._license_checker.check(candidate.license_name)
        return license_filter.LicenseResult(
            status=license_filter.LicenseStatus.NOT_EVALUATED,
            reason="Guideline filter did not pass",
        )
