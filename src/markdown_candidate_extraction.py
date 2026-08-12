"""Concurrent, resumable Markdown candidate extraction from repository snapshots."""

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import markdown_candidate_store
import markdown_filename_audit
import repository

CandidateProgressCallback = Callable[[int, int, markdown_filename_audit.RepositoryMarkdownFilenameAudit], None]


@dataclass(frozen=True, slots=True)
class CandidateExtractionStats:
    """Observable counts for one resumable candidate-extraction run."""

    requested: int
    skipped: int
    evaluated: int
    elapsed_seconds: float


def run_candidate_extraction(
    candidates: Sequence[repository.RepositoryCandidate],
    *,
    auditor: markdown_filename_audit.MarkdownFilenameAuditor,
    store: markdown_candidate_store.MarkdownCandidateStore,
    workers: int,
    limit: int | None = None,
    on_progress: CandidateProgressCallback | None = None,
) -> CandidateExtractionStats:
    """Extract pending candidates and checkpoint every completed repository."""
    if workers < 1:
        msg = "workers must be at least 1"
        raise ValueError(msg)
    requested = tuple(candidates[:limit]) if limit is not None else tuple(candidates)
    completed = store.completed_repositories()
    pending = tuple(
        candidate for candidate in requested if (candidate.repository, candidate.revision) not in completed
    )
    started_at = time.monotonic()
    evaluated = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = tuple(executor.submit(auditor.audit, candidate) for candidate in pending)
            for evaluated, future in enumerate(as_completed(futures), start=1):
                result = future.result()
                store.append(result)
                if on_progress is not None:
                    on_progress(evaluated, len(pending), result)
    finally:
        store.write_reports()
    return CandidateExtractionStats(
        requested=len(requested),
        skipped=len(requested) - len(pending),
        evaluated=evaluated,
        elapsed_seconds=time.monotonic() - started_at,
    )
