"""Audit Markdown keyword candidates against agent-discovered evidence."""

import csv
import re
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import quote

import github_client
import repository

KEYWORDS = (
    "style",
    "guide",
    "guideline",
    "standard",
    "convention",
    "rule",
)
_KEYWORD_PATTERNS = {keyword: re.compile(rf"\b{re.escape(keyword)}s?\b", flags=re.IGNORECASE) for keyword in KEYWORDS}
_EVIDENCE_COLUMNS = frozenset({"name", "lastCommitSHA", "guideline_path"})
_KEYWORD_FILE_FIELDS = (
    "name",
    "lastCommitSHA",
    "markdown_path",
    "markdown_url",
    "matched_keywords",
    "agent_evidence",
)
_EVIDENCE_COVERAGE_FIELDS = (
    "name",
    "lastCommitSHA",
    "guideline_path",
    "guideline_url",
    "is_markdown",
    "keyword_match",
    "matched_keywords",
)
_REPOSITORY_SUMMARY_FIELDS = (
    "name",
    "lastCommitSHA",
    "status",
    "error",
    "markdown_keyword_file_count",
    "agent_evidence_file_count",
    "agent_evidence_markdown_file_count",
    "agent_evidence_keyword_match_count",
    "agent_evidence_not_evaluated_count",
)


class MarkdownAuditStatus(StrEnum):
    """Outcome of scanning one repository revision."""

    COMPLETED = "completed"
    RETRIEVAL_ERROR = "retrieval_error"
    SCAN_ERROR = "scan_error"


class MarkdownRepositoryClient(Protocol):
    """Retrieve Markdown documents from one revision-pinned GitHub tree."""

    def get_complete_tree(self, repository: str, revision: str) -> github_client.RepositoryTree:
        """Return every blob entry reachable from a repository revision."""
        ...

    def get_text_file(self, repository: str, revision: str, path: str) -> str:
        """Return one text file at a repository revision."""
        ...


@dataclass(frozen=True, slots=True)
class MarkdownKeywordFile:
    """A Markdown file containing one or more configured keywords."""

    path: str
    matched_keywords: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AgentEvidenceCoverage:
    """Whether an agent evidence file is selected by the keyword scan."""

    path: str
    is_markdown: bool
    keyword_match: bool | None
    matched_keywords: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepositoryMarkdownAudit:
    """Keyword files and agent-evidence coverage for one repository."""

    candidate: repository.RepositoryCandidate
    status: MarkdownAuditStatus
    keyword_files: tuple[MarkdownKeywordFile, ...] = ()
    agent_evidence: tuple[AgentEvidenceCoverage, ...] = ()
    error: str = ""


@dataclass(frozen=True, slots=True)
class MarkdownAuditStats:
    """Aggregate counts and elapsed time for one audit run."""

    requested: int
    completed: int
    errors: int
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class MarkdownAuditReport:
    """Deterministically ordered repository audit results."""

    results: tuple[RepositoryMarkdownAudit, ...]
    stats: MarkdownAuditStats


class MarkdownAuditor:
    """Scan revision-pinned repositories without invoking a model."""

    __slots__ = ("_agent_evidence", "_client")

    def __init__(
        self,
        *,
        client: MarkdownRepositoryClient,
        agent_evidence: Mapping[tuple[str, str], tuple[str, ...]],
    ) -> None:
        self._client = client
        self._agent_evidence = agent_evidence

    def audit(self, candidate: repository.RepositoryCandidate) -> RepositoryMarkdownAudit:
        """Return keyword matches and evidence coverage for one revision."""
        identity = (candidate.repository, candidate.revision)
        try:
            keyword_files = scan_github_markdown_files(self._client, *identity)
        except github_client.GitHubRetrievalError as error:
            return RepositoryMarkdownAudit(
                candidate=candidate,
                status=MarkdownAuditStatus.RETRIEVAL_ERROR,
                agent_evidence=_unevaluated_agent_evidence(self._agent_evidence.get(identity, ())),
                error=_error_reason(error),
            )
        except Exception as error:
            return RepositoryMarkdownAudit(
                candidate=candidate,
                status=MarkdownAuditStatus.SCAN_ERROR,
                agent_evidence=_unevaluated_agent_evidence(self._agent_evidence.get(identity, ())),
                error=_error_reason(error),
            )
        return RepositoryMarkdownAudit(
            candidate=candidate,
            status=MarkdownAuditStatus.COMPLETED,
            keyword_files=keyword_files,
            agent_evidence=compare_agent_evidence(
                keyword_files,
                evidence_paths=self._agent_evidence.get(identity, ()),
            ),
        )


class MarkdownAuditRunner:
    """Audit independent repository snapshots with a fixed thread pool."""

    __slots__ = ("_auditor", "_workers")

    def __init__(self, *, auditor: MarkdownAuditor, workers: int) -> None:
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
    ) -> MarkdownAuditReport:
        """Audit requested repositories concurrently."""
        requested = tuple(candidates[:limit]) if limit is not None else tuple(candidates)
        started_at = time.monotonic()
        results: list[RepositoryMarkdownAudit] = []
        with ThreadPoolExecutor(max_workers=self._workers) as executor:
            futures = tuple(executor.submit(self._auditor.audit, candidate) for candidate in requested)
            results.extend(future.result() for future in as_completed(futures))
        ordered = tuple(sorted(results, key=lambda result: result.candidate.input_index))
        completed = sum(result.status is MarkdownAuditStatus.COMPLETED for result in ordered)
        return MarkdownAuditReport(
            results=ordered,
            stats=MarkdownAuditStats(
                requested=len(requested),
                completed=completed,
                errors=len(ordered) - completed,
                elapsed_seconds=time.monotonic() - started_at,
            ),
        )


def scan_markdown_files(repository_path: Path) -> tuple[MarkdownKeywordFile, ...]:
    """Return Markdown files containing complete singular or plural keywords."""
    matches: list[MarkdownKeywordFile] = []
    for path in sorted(repository_path.rglob("*")):
        if path.suffix.lower() != ".md" or path.is_symlink() or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        matched_keywords = _matched_keywords(content)
        if matched_keywords:
            matches.append(
                MarkdownKeywordFile(
                    path=path.relative_to(repository_path).as_posix(),
                    matched_keywords=matched_keywords,
                ),
            )
    return tuple(matches)


def scan_github_markdown_files(
    client: MarkdownRepositoryClient,
    repository: str,
    revision: str,
) -> tuple[MarkdownKeywordFile, ...]:
    """Return matching Markdown files fetched from a complete GitHub tree."""
    tree = client.get_complete_tree(repository, revision)
    matches: list[MarkdownKeywordFile] = []
    entries = sorted(tree.entries, key=lambda entry: entry.path)
    for entry in entries:
        if entry.mode == "120000" or PurePosixPath(entry.path).suffix.casefold() != ".md":
            continue
        content = client.get_text_file(repository, revision, entry.path)
        matched_keywords = _matched_keywords(content)
        if matched_keywords:
            matches.append(MarkdownKeywordFile(path=entry.path, matched_keywords=matched_keywords))
    return tuple(matches)


def _matched_keywords(content: str) -> tuple[str, ...]:
    return tuple(keyword for keyword, pattern in _KEYWORD_PATTERNS.items() if pattern.search(content) is not None)


def compare_agent_evidence(
    matches: tuple[MarkdownKeywordFile, ...],
    *,
    evidence_paths: tuple[str, ...],
) -> tuple[AgentEvidenceCoverage, ...]:
    """Report whether each agent evidence path belongs to the keyword candidates."""
    matched_files = {match.path: match for match in matches}
    coverage: list[AgentEvidenceCoverage] = []
    for path in sorted(set(evidence_paths)):
        match = matched_files.get(path)
        coverage.append(
            AgentEvidenceCoverage(
                path=path,
                is_markdown=PurePosixPath(path).suffix.lower() == ".md",
                keyword_match=match is not None,
                matched_keywords=match.matched_keywords if match is not None else (),
            ),
        )
    return tuple(coverage)


def _unevaluated_agent_evidence(evidence_paths: tuple[str, ...]) -> tuple[AgentEvidenceCoverage, ...]:
    return tuple(
        AgentEvidenceCoverage(
            path=path,
            is_markdown=PurePosixPath(path).suffix.lower() == ".md",
            keyword_match=None,
            matched_keywords=(),
        )
        for path in sorted(set(evidence_paths))
    )


def load_agent_evidence(paths: Sequence[Path]) -> dict[tuple[str, str], tuple[str, ...]]:
    """Load and merge agent evidence paths from guideline file reports."""
    evidence: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for path in paths:
        with path.open(encoding="utf-8", newline="") as input_file:
            reader = csv.DictReader(input_file)
            missing_columns = _EVIDENCE_COLUMNS.difference(reader.fieldnames or ())
            if missing_columns:
                missing = ", ".join(sorted(missing_columns))
                msg = f"{path} is missing required columns: {missing}"
                raise ValueError(msg)
            for row in reader:
                identity = (row["name"].strip(), row["lastCommitSHA"].strip())
                evidence[identity].add(row["guideline_path"].strip())
    return {identity: tuple(sorted(evidence_paths)) for identity, evidence_paths in evidence.items()}


def write_reports(report: MarkdownAuditReport, output_dir: Path) -> None:
    """Write per-file coverage and per-repository summary CSVs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        output_dir / "markdown_term_files.csv",
        _keyword_file_rows(report.results),
        fieldnames=_KEYWORD_FILE_FIELDS,
    )
    _write_csv(
        output_dir / "agent_evidence_coverage.csv",
        _evidence_coverage_rows(report.results),
        fieldnames=_EVIDENCE_COVERAGE_FIELDS,
    )
    _write_csv(
        output_dir / "repository_summary.csv",
        _repository_summary_rows(report.results),
        fieldnames=_REPOSITORY_SUMMARY_FIELDS,
    )


def _keyword_file_rows(results: Sequence[RepositoryMarkdownAudit]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        candidate = result.candidate
        evidence_paths = {coverage.path for coverage in result.agent_evidence}
        rows.extend(_keyword_file_row(candidate, match, evidence_paths) for match in result.keyword_files)
    return rows


def _keyword_file_row(
    candidate: repository.RepositoryCandidate,
    match: MarkdownKeywordFile,
    evidence_paths: set[str],
) -> dict[str, object]:
    return {
        "name": candidate.repository,
        "lastCommitSHA": candidate.revision,
        "markdown_path": match.path,
        "markdown_url": _github_file_url(candidate, match.path),
        "matched_keywords": "|".join(match.matched_keywords),
        "agent_evidence": match.path in evidence_paths,
    }


def _evidence_coverage_rows(results: Sequence[RepositoryMarkdownAudit]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        candidate = result.candidate
        rows.extend(_evidence_coverage_row(candidate, coverage) for coverage in result.agent_evidence)
    return rows


def _evidence_coverage_row(
    candidate: repository.RepositoryCandidate,
    coverage: AgentEvidenceCoverage,
) -> dict[str, object]:
    return {
        "name": candidate.repository,
        "lastCommitSHA": candidate.revision,
        "guideline_path": coverage.path,
        "guideline_url": _github_file_url(candidate, coverage.path),
        "is_markdown": coverage.is_markdown,
        "keyword_match": coverage.keyword_match,
        "matched_keywords": "|".join(coverage.matched_keywords),
    }


def _repository_summary_rows(results: Sequence[RepositoryMarkdownAudit]) -> list[dict[str, object]]:
    return [
        {
            "name": result.candidate.repository,
            "lastCommitSHA": result.candidate.revision,
            "status": result.status.value,
            "error": result.error,
            "markdown_keyword_file_count": len(result.keyword_files),
            "agent_evidence_file_count": len(result.agent_evidence),
            "agent_evidence_markdown_file_count": sum(coverage.is_markdown for coverage in result.agent_evidence),
            "agent_evidence_keyword_match_count": sum(
                coverage.keyword_match is True for coverage in result.agent_evidence
            ),
            "agent_evidence_not_evaluated_count": sum(
                coverage.keyword_match is None for coverage in result.agent_evidence
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


def _error_reason(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:1000]
