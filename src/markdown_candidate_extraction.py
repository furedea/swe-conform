"""Concurrent, resumable Markdown candidate extraction from repository snapshots."""

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Protocol

import markdown_candidate_store
import markdown_filename_audit
import repository
import repository_cache

CandidateProgressCallback = Callable[[int, int, markdown_filename_audit.RepositoryMarkdownFilenameAudit], None]


@dataclass(frozen=True, slots=True)
class CandidateExtractionStats:
    """Observable counts for one resumable candidate-extraction run."""

    requested: int
    skipped: int
    evaluated: int
    elapsed_seconds: float
    complete_repositories: int = 0
    incomplete_repositories: int = 0
    explicitly_excluded_repositories: int = 0
    processed_repositories: int = 0


class SnapshotInspector(Protocol):
    """Inspect one revision-pinned snapshot without network access."""

    def inspect_snapshot(self, repository: str, revision: str) -> repository_cache.SnapshotInspection:
        """Return local snapshot availability and completeness."""
        ...


def run_candidate_extraction(
    candidates: Sequence[repository.RepositoryCandidate],
    *,
    auditor: markdown_filename_audit.MarkdownFilenameAuditor,
    store: markdown_candidate_store.MarkdownCandidateStore,
    workers: int,
    limit: int | None = None,
    on_progress: CandidateProgressCallback | None = None,
    snapshot_inspector: SnapshotInspector | None = None,
    skip_incomplete_repositories: bool = False,
    excluded_repositories: Sequence[str] = (),
) -> CandidateExtractionStats:
    """Extract pending candidates and checkpoint every completed repository."""
    if workers < 1:
        msg = "workers must be at least 1"
        raise ValueError(msg)
    selected = tuple(candidates[:limit]) if limit is not None else tuple(candidates)
    requested = _unique_candidates(selected)
    completed = store.completed_repositories()
    pending = tuple(
        candidate for candidate in requested if (candidate.repository, candidate.revision) not in completed
    )
    normalized_exclusions = {name.casefold() for name in excluded_repositories}
    started_at = time.monotonic()
    evaluated = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = tuple(
                executor.submit(
                    _audit_candidate,
                    candidate,
                    auditor=auditor,
                    snapshot_inspector=snapshot_inspector,
                    skip_incomplete_repositories=skip_incomplete_repositories,
                    excluded_repositories=normalized_exclusions,
                )
                for candidate in pending
            )
            for evaluated, future in enumerate(as_completed(futures), start=1):
                result = future.result()
                store.append(result)
                if on_progress is not None:
                    on_progress(evaluated, len(pending), result)
    finally:
        store.write_reports()
    requested_identities = {(candidate.repository, candidate.revision) for candidate in requested}
    results = tuple(
        result
        for result in store.report().results
        if (result.candidate.repository, result.candidate.revision) in requested_identities
    )
    incomplete_status = markdown_filename_audit.MarkdownFilenameAuditStatus.SNAPSHOT_INCOMPLETE
    excluded_status = markdown_filename_audit.MarkdownFilenameAuditStatus.EXPLICITLY_EXCLUDED
    incomplete_repositories = sum(result.status is incomplete_status for result in results)
    explicitly_excluded_repositories = sum(result.status is excluded_status for result in results)
    processed_repositories = len(results) - incomplete_repositories - explicitly_excluded_repositories
    return CandidateExtractionStats(
        requested=len(requested),
        skipped=len(requested) - len(pending),
        evaluated=evaluated,
        elapsed_seconds=time.monotonic() - started_at,
        complete_repositories=processed_repositories,
        incomplete_repositories=incomplete_repositories,
        explicitly_excluded_repositories=explicitly_excluded_repositories,
        processed_repositories=processed_repositories,
    )


def _audit_candidate(
    candidate: repository.RepositoryCandidate,
    *,
    auditor: markdown_filename_audit.MarkdownFilenameAuditor,
    snapshot_inspector: SnapshotInspector | None,
    skip_incomplete_repositories: bool,
    excluded_repositories: set[str],
) -> markdown_filename_audit.RepositoryMarkdownFilenameAudit:
    if candidate.repository.casefold() in excluded_repositories:
        return _skipped_result(
            candidate,
            status=markdown_filename_audit.MarkdownFilenameAuditStatus.EXPLICITLY_EXCLUDED,
            reason="explicitly_excluded",
        )
    if skip_incomplete_repositories:
        if snapshot_inspector is None:
            raise ValueError("snapshot_inspector is required when incomplete repositories are skipped")
        try:
            inspection = snapshot_inspector.inspect_snapshot(candidate.repository, candidate.revision)
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"[:1000]
            return _skipped_result(
                candidate,
                status=markdown_filename_audit.MarkdownFilenameAuditStatus.SNAPSHOT_INCOMPLETE,
                reason=reason,
            )
        if not inspection.complete:
            detail = f": {inspection.detail}" if inspection.detail else ""
            return _skipped_result(
                candidate,
                status=markdown_filename_audit.MarkdownFilenameAuditStatus.SNAPSHOT_INCOMPLETE,
                reason=f"{inspection.state.value}{detail}",
            )
    return auditor.audit(candidate)


def _skipped_result(
    candidate: repository.RepositoryCandidate,
    *,
    status: markdown_filename_audit.MarkdownFilenameAuditStatus,
    reason: str,
) -> markdown_filename_audit.RepositoryMarkdownFilenameAudit:
    return markdown_filename_audit.RepositoryMarkdownFilenameAudit(
        candidate=candidate,
        status=status,
        error=reason,
    )


def _unique_candidates(
    candidates: Sequence[repository.RepositoryCandidate],
) -> tuple[repository.RepositoryCandidate, ...]:
    identities: set[tuple[str, str]] = set()
    unique: list[repository.RepositoryCandidate] = []
    for candidate in candidates:
        identity = candidate.repository, candidate.revision
        if identity in identities:
            continue
        identities.add(identity)
        unique.append(candidate)
    return tuple(unique)
