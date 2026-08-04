"""Tests for repository candidate loading."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

import repository


def test_load_repository_candidates_preserves_source_order(tmp_path: Path) -> None:
    input_path = tmp_path / "python.csv"
    input_path.write_text(
        "name,lastCommitSHA,lastCommit,defaultBranch,license,mainLanguage\n"
        "example/project,0123456789abcdef,2026-07-31T23:59:59,main,MIT License,Python\n",
        encoding="utf-8",
    )

    candidates = repository.load_repository_candidates(tmp_path)

    assert len(candidates) == 1
    assert candidates[0].repository == "example/project"
    assert candidates[0].revision == "0123456789abcdef"
    assert candidates[0].license_name == "MIT License"
    assert candidates[0].source_file == "python.csv"
    assert candidates[0].input_index == 0
    assert candidates[0].fields["mainLanguage"] == "Python"


def test_load_repository_candidates_rejects_invalid_repository_name(tmp_path: Path) -> None:
    input_path = tmp_path / "python.csv"
    input_path.write_text(
        "name,lastCommitSHA,lastCommit,defaultBranch,license\n"
        "invalid,0123456789abcdef,2026-07-31T00:00:00,main,MIT License\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="owner/repository"):
        repository.load_repository_candidates(tmp_path)


def test_load_repository_candidates_rejects_a_snapshot_after_the_cutoff(tmp_path: Path) -> None:
    input_path = tmp_path / "python.csv"
    input_path.write_text(
        "name,lastCommitSHA,lastCommit,defaultBranch,license\n"
        "example/project,0123456789abcdef,2026-08-01T00:00:00,main,MIT License\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="snapshot cutoff"):
        repository.load_repository_candidates(tmp_path)


def test_load_repository_candidates_rejects_a_snapshot_before_the_start(tmp_path: Path) -> None:
    input_path = tmp_path / "python.csv"
    input_path.write_text(
        "name,lastCommitSHA,lastCommit,defaultBranch,license\n"
        "example/project,0123456789abcdef,2025-12-31T23:59:59,main,MIT License\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="snapshot start"):
        repository.load_repository_candidates(tmp_path)


def test_load_repository_candidates_can_replay_a_revision_outside_the_snapshot_window(tmp_path: Path) -> None:
    input_path = tmp_path / "python.csv"
    input_path.write_text(
        "name,lastCommitSHA,lastCommit,defaultBranch,license\n"
        "example/project,0123456789abcdef,2025-12-31T23:59:59,main,MIT License\n",
        encoding="utf-8",
    )

    candidates = repository.load_repository_candidates(tmp_path, enforce_snapshot_window=False)

    assert candidates[0].revision == "0123456789abcdef"


def test_default_repository_candidates_use_the_2026_collection_interval() -> None:
    candidates = repository.load_repository_candidates(Path("docs/data/repository-candidates-new"))

    committed_at = tuple(
        datetime.fromisoformat(candidate.fields["lastCommit"]).replace(tzinfo=UTC) for candidate in candidates
    )

    assert min(committed_at) >= repository.SNAPSHOT_START
    assert max(committed_at) < repository.SNAPSHOT_CUTOFF
