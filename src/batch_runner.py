"""Concurrent, resumable execution of the repository filter."""

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import pipeline
import repository
import result_store

ProgressCallback = Callable[[int, int, pipeline.RepositoryResult], None]


@dataclass(frozen=True, slots=True)
class RunStats:
    """Observable execution counts and elapsed wall-clock time."""

    requested: int
    skipped: int
    evaluated: int
    elapsed_seconds: float


class BatchRunner:
    """Evaluate repository candidates concurrently and checkpoint each result."""

    __slots__ = ("_repository_filter", "_workers")

    def __init__(self, *, repository_filter: pipeline.RepositoryFilter, workers: int) -> None:
        if workers < 1:
            msg = "workers must be at least 1"
            raise ValueError(msg)
        self._repository_filter = repository_filter
        self._workers = workers

    def run(
        self,
        candidates: Sequence[repository.RepositoryCandidate],
        store: result_store.ResultStore,
        *,
        limit: int | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> RunStats:
        """Evaluate pending candidates and always materialize completed reports."""
        requested = tuple(candidates[:limit]) if limit is not None else tuple(candidates)
        completed = store.completed_repositories()
        pending = tuple(
            candidate for candidate in requested if (candidate.repository, candidate.revision) not in completed
        )
        started_at = time.monotonic()
        evaluated = 0
        try:
            with ThreadPoolExecutor(max_workers=self._workers) as executor:
                futures = tuple(executor.submit(self._repository_filter.evaluate, candidate) for candidate in pending)
                for evaluated, future in enumerate(as_completed(futures), start=1):
                    result = future.result()
                    store.append(result)
                    if on_progress is not None:
                        on_progress(evaluated, len(pending), result)
        finally:
            store.write_reports()
        return RunStats(
            requested=len(requested),
            skipped=len(requested) - len(pending),
            evaluated=evaluated,
            elapsed_seconds=time.monotonic() - started_at,
        )
