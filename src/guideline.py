"""Project coding-guideline classification contract."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import repository


class GuidelineStatus(StrEnum):
    """Outcome of project coding-guideline classification."""

    PASS = "pass"
    REVIEW = "review"
    NOT_FOUND = "not_found"
    RETRIEVAL_ERROR = "retrieval_error"
    MODEL_ERROR = "model_error"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token usage reported by one model request."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class GuidelineEvidence:
    """One verified guideline file and the quote that identified it."""

    path: str
    quote: str
    content: bytes


@dataclass(frozen=True, slots=True)
class GuidelineEvidenceIssue:
    """One model evidence item that could not be verified."""

    index: int
    reason: str


@dataclass(frozen=True, slots=True)
class GuidelineResult:
    """Evidence-backed classification of a repository guideline."""

    status: GuidelineStatus
    reason: str
    evidence: tuple[GuidelineEvidence, ...] = ()
    evidence_issues: tuple[GuidelineEvidenceIssue, ...] = ()
    model_response_json: str = ""
    candidate_count: int = 0
    tree_truncated: bool = False
    model_called: bool = False
    checkout_seconds: float = 0.0
    model_seconds: float = 0.0
    usage: TokenUsage = TokenUsage()


class GuidelineChecker(Protocol):
    """Classify whether a repository revision has a coding guideline."""

    def check(self, candidate: repository.RepositoryCandidate) -> GuidelineResult:
        """Return the coding-guideline classification for one candidate."""
        ...
