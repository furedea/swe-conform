"""Project-guideline repository selection pipeline."""

from dataclasses import dataclass

import guideline
import repository


@dataclass(frozen=True, slots=True)
class RepositoryResult:
    """Project-guideline classification result for one repository."""

    candidate: repository.RepositoryCandidate
    guideline: guideline.GuidelineResult

    @property
    def is_selected(self) -> bool:
        """Return whether the repository passed guideline classification."""
        return self.guideline.status is guideline.GuidelineStatus.PASS


class RepositoryFilter:
    """Apply the project-guideline classifier."""

    __slots__ = ("_guideline_checker",)

    def __init__(
        self,
        *,
        guideline_checker: guideline.GuidelineChecker,
    ) -> None:
        self._guideline_checker = guideline_checker

    def evaluate(self, candidate: repository.RepositoryCandidate) -> RepositoryResult:
        """Evaluate one repository for an explicit project guideline."""
        guideline_result = self._guideline_checker.check(candidate)
        return RepositoryResult(
            candidate=candidate,
            guideline=guideline_result,
        )
