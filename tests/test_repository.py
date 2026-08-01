"""Tests for repository candidate loading."""

from pathlib import Path

import pytest

import repository


def test_load_repository_candidates_preserves_source_order(tmp_path: Path) -> None:
    input_path = tmp_path / "python.csv"
    input_path.write_text(
        "name,lastCommitSHA,license,mainLanguage\nexample/project,0123456789abcdef,MIT License,Python\n",
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
        "name,lastCommitSHA,license\ninvalid,0123456789abcdef,MIT License\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="owner/repository"):
        repository.load_repository_candidates(tmp_path)
