"""Tests for concurrent repository cache acquisition."""

import cache_runner
import repository
import repository_cache


class FakeCache:
    """Return deterministic cache outcomes without Git."""

    __slots__ = ()

    def ensure_snapshot(self, repository: str, revision: str) -> repository_cache.CacheDisposition:
        _ = revision
        if repository == "example/error":
            raise repository_cache.RepositoryCacheError("unavailable")
        if repository == "example/cached":
            return repository_cache.CacheDisposition.CACHED
        return repository_cache.CacheDisposition.FETCHED


def test_cache_runner_continues_after_one_repository_fails() -> None:
    candidates = (
        _candidate("example/fetched", 0),
        _candidate("example/error", 1),
        _candidate("example/cached", 2),
    )
    runner = cache_runner.CacheBatchRunner(cache=FakeCache(), workers=2)

    report = runner.run(candidates)

    assert report.stats.requested == 3
    assert report.stats.fetched == 1
    assert report.stats.cached == 1
    assert report.stats.errors == 1
    assert {result.candidate.repository for result in report.results if result.error} == {"example/error"}


def _candidate(name: str, index: int) -> repository.RepositoryCandidate:
    return repository.RepositoryCandidate(
        repository=name,
        revision=f"{index + 1:040x}",
        license_name="MIT",
        source_file="test.csv",
        input_index=index,
        fields={},
    )
