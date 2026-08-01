"""Tests for concurrent and resumable batch execution."""

from pathlib import Path

import pytest_mock

import batch_runner
import guideline
import license_filter
import pipeline
import repository
import result_store


def _candidate(index: int) -> repository.RepositoryCandidate:
    revision = f"{index + 1:040x}"
    name = f"example/project-{index}"
    return repository.RepositoryCandidate(
        repository=name,
        revision=revision,
        license_name="MIT License",
        source_file="python.csv",
        input_index=index,
        fields={"name": name, "lastCommitSHA": revision, "license": "MIT License"},
    )


def _result(candidate: repository.RepositoryCandidate) -> pipeline.RepositoryResult:
    return pipeline.RepositoryResult(
        candidate=candidate,
        guideline=guideline.GuidelineResult(
            status=guideline.GuidelineStatus.NOT_FOUND,
            reason="No candidate document found",
        ),
        license=license_filter.LicenseResult(status=license_filter.LicenseStatus.NOT_EVALUATED),
    )


def test_batch_runner_skips_checkpointed_repository(mocker: pytest_mock.MockerFixture, tmp_path: Path) -> None:
    candidates = (_candidate(0), _candidate(1))
    store = result_store.ResultStore(tmp_path / "output", configuration={"model": "gpt-5.6-luna"})
    store.initialize()
    store.append(_result(candidates[0]))
    repository_filter = mocker.Mock(spec=pipeline.RepositoryFilter)
    repository_filter.evaluate.side_effect = _result
    runner = batch_runner.BatchRunner(repository_filter=repository_filter, workers=1)

    stats = runner.run(candidates, store)

    repository_filter.evaluate.assert_called_once_with(candidates[1])
    assert stats.requested == 2
    assert stats.skipped == 1
    assert stats.evaluated == 1
