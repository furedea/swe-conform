"""Audit Markdown filenames for project-guideline candidate terms."""

import csv
import re
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import quote

import github_client
import repository

_FILENAME_TERM_PATTERNS = (
    ("readme", re.compile(r"readme", flags=re.IGNORECASE)),
    ("contributing", re.compile(r"contributing", flags=re.IGNORECASE)),
    ("agents", re.compile(r"agents", flags=re.IGNORECASE)),
    ("claude", re.compile(r"claude", flags=re.IGNORECASE)),
    ("copilot-instructions", re.compile(r"copilot-instructions", flags=re.IGNORECASE)),
    ("style", re.compile(r"styles?", flags=re.IGNORECASE)),
    ("guide", re.compile(r"guides?(?!line)", flags=re.IGNORECASE)),
    ("guideline", re.compile(r"guidelines?", flags=re.IGNORECASE)),
    ("standard", re.compile(r"standards?", flags=re.IGNORECASE)),
    ("convention", re.compile(r"conventions?", flags=re.IGNORECASE)),
    ("rule", re.compile(r"rules?", flags=re.IGNORECASE)),
)
_FILENAME_FILE_FIELDS = (
    "name",
    "lastCommitSHA",
    "markdown_path",
    "markdown_url",
    "matched_filename_terms",
    "agent_evidence",
)
_EVIDENCE_COVERAGE_FIELDS = (
    "name",
    "lastCommitSHA",
    "guideline_path",
    "guideline_url",
    "is_markdown",
    "filename_match",
    "matched_filename_terms",
)
_REPOSITORY_SUMMARY_FIELDS = (
    "name",
    "lastCommitSHA",
    "status",
    "error",
    "markdown_filename_file_count",
    "agent_evidence_file_count",
    "agent_evidence_markdown_file_count",
    "agent_evidence_filename_match_count",
    "agent_evidence_not_evaluated_count",
)


class MarkdownFilenameAuditStatus(StrEnum):
    """Outcome of scanning filenames for one repository revision."""

    COMPLETED = "completed"
    RETRIEVAL_ERROR = "retrieval_error"
    SCAN_ERROR = "scan_error"


class MarkdownTreeClient(Protocol):
    """Retrieve a complete revision-pinned GitHub tree."""

    def get_complete_tree(self, repository: str, revision: str) -> github_client.RepositoryTree:
        """Return every blob entry reachable from a repository revision."""
        ...


@dataclass(frozen=True, slots=True)
class MarkdownFilenameFile:
    """A Markdown file whose filename contains candidate terms."""

    path: str
    matched_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentEvidenceFilenameCoverage:
    """Whether an agent evidence path matches the filename strategy."""

    path: str
    is_markdown: bool
    filename_match: bool | None
    matched_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepositoryMarkdownFilenameAudit:
    """Filename candidates and agent-evidence coverage for one repository."""

    candidate: repository.RepositoryCandidate
    status: MarkdownFilenameAuditStatus
    filename_files: tuple[MarkdownFilenameFile, ...] = ()
    agent_evidence: tuple[AgentEvidenceFilenameCoverage, ...] = ()
    error: str = ""


@dataclass(frozen=True, slots=True)
class MarkdownFilenameAuditStats:
    """Aggregate counts and elapsed time for one filename audit run."""

    requested: int
    completed: int
    errors: int
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class MarkdownFilenameAuditReport:
    """Deterministically ordered filename audit results."""

    results: tuple[RepositoryMarkdownFilenameAudit, ...]
    stats: MarkdownFilenameAuditStats


class MarkdownFilenameAuditor:
    """Scan revision-pinned Markdown filenames without reading file contents."""

    __slots__ = ("_agent_evidence", "_client")

    def __init__(
        self,
        *,
        client: MarkdownTreeClient,
        agent_evidence: Mapping[tuple[str, str], tuple[str, ...]],
    ) -> None:
        self._client = client
        self._agent_evidence = agent_evidence

    def audit(self, candidate: repository.RepositoryCandidate) -> RepositoryMarkdownFilenameAudit:
        """Return filename candidates and evidence coverage for one revision."""
        identity = (candidate.repository, candidate.revision)
        evidence_paths = self._agent_evidence.get(identity, ())
        try:
            filename_files = scan_github_markdown_filenames(self._client, *identity)
        except github_client.GitHubRetrievalError as error:
            return RepositoryMarkdownFilenameAudit(
                candidate=candidate,
                status=MarkdownFilenameAuditStatus.RETRIEVAL_ERROR,
                agent_evidence=_unevaluated_agent_evidence(evidence_paths),
                error=_error_reason(error),
            )
        except Exception as error:
            return RepositoryMarkdownFilenameAudit(
                candidate=candidate,
                status=MarkdownFilenameAuditStatus.SCAN_ERROR,
                agent_evidence=_unevaluated_agent_evidence(evidence_paths),
                error=_error_reason(error),
            )
        return RepositoryMarkdownFilenameAudit(
            candidate=candidate,
            status=MarkdownFilenameAuditStatus.COMPLETED,
            filename_files=filename_files,
            agent_evidence=compare_agent_evidence(filename_files, evidence_paths=evidence_paths),
        )


class MarkdownFilenameAuditRunner:
    """Audit independent repository snapshots with a fixed thread pool."""

    __slots__ = ("_auditor", "_workers")

    def __init__(self, *, auditor: MarkdownFilenameAuditor, workers: int) -> None:
        if workers < 1:
            msg = "workers must be at least 1"
            raise ValueError(msg)
        self._auditor = auditor
        self._workers = workers

    def run(
        self,
        candidates: Sequence[repository.RepositoryCandidate],
        *,
        limit: int | None = None,
    ) -> MarkdownFilenameAuditReport:
        """Audit requested repositories concurrently."""
        requested = tuple(candidates[:limit]) if limit is not None else tuple(candidates)
        started_at = time.monotonic()
        results: list[RepositoryMarkdownFilenameAudit] = []
        with ThreadPoolExecutor(max_workers=self._workers) as executor:
            futures = tuple(executor.submit(self._auditor.audit, candidate) for candidate in requested)
            results.extend(future.result() for future in as_completed(futures))
        ordered = tuple(sorted(results, key=lambda result: result.candidate.input_index))
        completed = sum(result.status is MarkdownFilenameAuditStatus.COMPLETED for result in ordered)
        return MarkdownFilenameAuditReport(
            results=ordered,
            stats=MarkdownFilenameAuditStats(
                requested=len(requested),
                completed=completed,
                errors=len(ordered) - completed,
                elapsed_seconds=time.monotonic() - started_at,
            ),
        )


def matched_filename_terms(filename: str) -> tuple[str, ...]:
    """Return normalized candidate terms present in a filename."""
    return tuple(term for term, pattern in _FILENAME_TERM_PATTERNS if pattern.search(filename) is not None)


def scan_github_markdown_filenames(
    client: MarkdownTreeClient,
    repository: str,
    revision: str,
) -> tuple[MarkdownFilenameFile, ...]:
    """Return Markdown files whose basenames contain candidate terms."""
    tree = client.get_complete_tree(repository, revision)
    matches: list[MarkdownFilenameFile] = []
    for entry in sorted(tree.entries, key=lambda candidate: candidate.path):
        path = PurePosixPath(entry.path)
        if entry.mode == "120000" or path.suffix.casefold() != ".md":
            continue
        matched_terms = matched_filename_terms(path.name)
        if matched_terms:
            matches.append(MarkdownFilenameFile(path=entry.path, matched_terms=matched_terms))
    return tuple(matches)


def compare_agent_evidence(
    matches: tuple[MarkdownFilenameFile, ...],
    *,
    evidence_paths: tuple[str, ...],
) -> tuple[AgentEvidenceFilenameCoverage, ...]:
    """Report whether each agent evidence path matches the filename strategy."""
    matched_files = {match.path: match for match in matches}
    coverage: list[AgentEvidenceFilenameCoverage] = []
    for path in sorted(set(evidence_paths)):
        match = matched_files.get(path)
        coverage.append(
            AgentEvidenceFilenameCoverage(
                path=path,
                is_markdown=PurePosixPath(path).suffix.casefold() == ".md",
                filename_match=match is not None,
                matched_terms=match.matched_terms if match is not None else (),
            ),
        )
    return tuple(coverage)


def write_reports(report: MarkdownFilenameAuditReport, output_dir: Path) -> None:
    """Write independent filename candidate and evidence coverage CSVs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        output_dir / "markdown_filename_files.csv",
        _filename_file_rows(report.results),
        fieldnames=_FILENAME_FILE_FIELDS,
    )
    _write_csv(
        output_dir / "agent_evidence_filename_coverage.csv",
        _evidence_coverage_rows(report.results),
        fieldnames=_EVIDENCE_COVERAGE_FIELDS,
    )
    _write_csv(
        output_dir / "repository_filename_summary.csv",
        _repository_summary_rows(report.results),
        fieldnames=_REPOSITORY_SUMMARY_FIELDS,
    )


def _filename_file_rows(
    results: Sequence[RepositoryMarkdownFilenameAudit],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        evidence_paths = {coverage.path for coverage in result.agent_evidence}
        rows.extend(_filename_file_row(result.candidate, match, evidence_paths) for match in result.filename_files)
    return rows


def _filename_file_row(
    candidate: repository.RepositoryCandidate,
    match: MarkdownFilenameFile,
    evidence_paths: set[str],
) -> dict[str, object]:
    return {
        "name": candidate.repository,
        "lastCommitSHA": candidate.revision,
        "markdown_path": match.path,
        "markdown_url": _github_file_url(candidate, match.path),
        "matched_filename_terms": "|".join(match.matched_terms),
        "agent_evidence": match.path in evidence_paths,
    }


def _evidence_coverage_rows(
    results: Sequence[RepositoryMarkdownFilenameAudit],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        rows.extend(_evidence_coverage_row(result.candidate, coverage) for coverage in result.agent_evidence)
    return rows


def _evidence_coverage_row(
    candidate: repository.RepositoryCandidate,
    coverage: AgentEvidenceFilenameCoverage,
) -> dict[str, object]:
    return {
        "name": candidate.repository,
        "lastCommitSHA": candidate.revision,
        "guideline_path": coverage.path,
        "guideline_url": _github_file_url(candidate, coverage.path),
        "is_markdown": coverage.is_markdown,
        "filename_match": coverage.filename_match,
        "matched_filename_terms": "|".join(coverage.matched_terms),
    }


def _repository_summary_rows(
    results: Sequence[RepositoryMarkdownFilenameAudit],
) -> list[dict[str, object]]:
    return [
        {
            "name": result.candidate.repository,
            "lastCommitSHA": result.candidate.revision,
            "status": result.status.value,
            "error": result.error,
            "markdown_filename_file_count": len(result.filename_files),
            "agent_evidence_file_count": len(result.agent_evidence),
            "agent_evidence_markdown_file_count": sum(coverage.is_markdown for coverage in result.agent_evidence),
            "agent_evidence_filename_match_count": sum(
                coverage.filename_match is True for coverage in result.agent_evidence
            ),
            "agent_evidence_not_evaluated_count": sum(
                coverage.filename_match is None for coverage in result.agent_evidence
            ),
        }
        for result in results
    ]


def _github_file_url(candidate: repository.RepositoryCandidate, path: str) -> str:
    encoded_path = quote(path, safe="/")
    return f"https://github.com/{candidate.repository}/blob/{candidate.revision}/{encoded_path}"


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, object]],
    *,
    fieldnames: Sequence[str],
) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(path)


def _unevaluated_agent_evidence(
    evidence_paths: tuple[str, ...],
) -> tuple[AgentEvidenceFilenameCoverage, ...]:
    return tuple(
        AgentEvidenceFilenameCoverage(
            path=path,
            is_markdown=PurePosixPath(path).suffix.casefold() == ".md",
            filename_match=None,
            matched_terms=(),
        )
        for path in sorted(set(evidence_paths))
    )


def _error_reason(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:1000]
