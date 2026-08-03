"""Concurrent acquisition of revision-pinned repository caches."""

import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Protocol

import repository
import repository_cache

_MAX_ERROR_CHARACTERS = 1000


class SnapshotCache(Protocol):
    """Ensure that a repository snapshot is available locally."""

    def ensure_snapshot(self, repository: str, revision: str) -> repository_cache.CacheDisposition:
        """Return how the snapshot became available."""
        ...


@dataclass(frozen=True, slots=True)
class CacheFetchResult:
    """Observable result of one cache acquisition attempt."""

    candidate: repository.RepositoryCandidate
    disposition: repository_cache.CacheDisposition | None
    elapsed_seconds: float
    error: str = ""


@dataclass(frozen=True, slots=True)
class CacheRunStats:
    """Aggregate cache acquisition counts and elapsed time."""

    requested: int
    fetched: int
    cached: int
    errors: int
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class CacheBatchReport:
    """Deterministic per-repository results and aggregate statistics."""

    results: tuple[CacheFetchResult, ...]
    stats: CacheRunStats


ProgressCallback = Callable[[int, int, CacheFetchResult], None]


class CacheBatchRunner:
    """Fetch independent repository snapshots with a fixed thread pool."""

    __slots__ = ("_cache", "_workers")

    def __init__(self, *, cache: SnapshotCache, workers: int) -> None:
        if workers < 1:
            msg = "workers must be at least 1"
            raise ValueError(msg)
        self._cache = cache
        self._workers = workers

    def run(
        self,
        candidates: Sequence[repository.RepositoryCandidate],
        *,
        limit: int | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> CacheBatchReport:
        """Fetch requested snapshots while isolating per-repository failures."""
        requested = tuple(candidates[:limit]) if limit is not None else tuple(candidates)
        started_at = time.monotonic()
        results: list[CacheFetchResult] = []
        with ThreadPoolExecutor(max_workers=self._workers) as executor:
            futures = tuple(executor.submit(self._fetch, candidate) for candidate in requested)
            for completed, future in enumerate(as_completed(futures), start=1):
                result = future.result()
                results.append(result)
                if on_progress is not None:
                    on_progress(completed, len(requested), result)
        ordered = tuple(sorted(results, key=lambda result: result.candidate.input_index))
        return CacheBatchReport(
            results=ordered,
            stats=CacheRunStats(
                requested=len(requested),
                fetched=sum(result.disposition is repository_cache.CacheDisposition.FETCHED for result in ordered),
                cached=sum(result.disposition is repository_cache.CacheDisposition.CACHED for result in ordered),
                errors=sum(bool(result.error) for result in ordered),
                elapsed_seconds=time.monotonic() - started_at,
            ),
        )

    def _fetch(self, candidate: repository.RepositoryCandidate) -> CacheFetchResult:
        started_at = time.monotonic()
        try:
            disposition = self._cache.ensure_snapshot(candidate.repository, candidate.revision)
            return CacheFetchResult(
                candidate=candidate,
                disposition=disposition,
                elapsed_seconds=time.monotonic() - started_at,
            )
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"[:_MAX_ERROR_CHARACTERS]
            return CacheFetchResult(
                candidate=candidate,
                disposition=None,
                elapsed_seconds=time.monotonic() - started_at,
                error=reason,
            )
