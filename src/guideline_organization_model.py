"""Immutable values for two-stage project-rule organization."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

JUDGMENT_FLAGS = (
    "in_scope",
    "persistent",
    "concrete",
    "atomic",
    "diff_closed",
    "objective",
    "grounded",
)


@dataclass(frozen=True, slots=True)
class OrganizationPreparation:
    """Counts and location produced for one extraction experiment."""

    repositories: int
    files: int
    output_dir: Path


@dataclass(frozen=True, slots=True)
class SourceDocument:
    """One accepted guideline file selected for rule extraction."""

    source_id: str
    repository: str
    revision: str
    file: str
    github_url: str
    local_path: Path
    content: str

    @property
    def sha256(self) -> str:
        """Return the source-content fingerprint."""
        return hashlib.sha256(self.content.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ExtractedCandidate:
    """One source-backed, standalone project-rule candidate."""

    candidate_id: str
    source: SourceDocument
    evidence_start_line: int
    evidence_end_line: int
    context_start_line: int
    context_end_line: int
    evidence_quote: str
    context_quote: str
    constraint: str


@dataclass(frozen=True, slots=True)
class JudgmentPreparation:
    """Counts produced while preparing independent selection judgments."""

    sources: int
    candidates: int
    output_dir: Path


@dataclass(frozen=True, slots=True)
class OrganizationFinalization:
    """Counts and location produced by deterministic selection finalization."""

    sources: int
    candidates: int
    accepted: int
    rejected: int
    output_dir: Path
