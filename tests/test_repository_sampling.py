"""Tests for reproducible language-stratified repository sampling."""

import csv
import json
from pathlib import Path

import pytest

import repository
import repository_sampling


def test_stratified_sample_balances_fifty_repositories_across_four_languages() -> None:
    candidates = tuple(
        _candidate(language, index) for language in repository_sampling.DEFAULT_LANGUAGES for index in range(20)
    )

    sampled = repository_sampling.stratified_sample(
        candidates,
        sample_size=50,
        sample_seed=20260807,
        excluded_repositories=set(),
    )

    language_counts = {
        language: sum(item.language == language for item in sampled)
        for language in repository_sampling.DEFAULT_LANGUAGES
    }
    assert sorted(language_counts.values()) == [12, 12, 13, 13]
    assert len({item.candidate.repository for item in sampled}) == 50
    assert [item.sample_order for item in sampled] == list(range(1, 51))


def test_stratified_sample_is_reproducible_and_excludes_prior_repositories() -> None:
    candidates = tuple(
        _candidate(language, index) for language in repository_sampling.DEFAULT_LANGUAGES for index in range(20)
    )
    excluded = {
        candidate.repository.casefold()
        for candidate in candidates
        if candidate.fields["mainLanguage"] == "Python" and candidate.input_index % 2 == 0
    }

    first = repository_sampling.stratified_sample(
        candidates,
        sample_size=20,
        sample_seed=41,
        excluded_repositories=excluded,
    )
    second = repository_sampling.stratified_sample(
        tuple(reversed(candidates)),
        sample_size=20,
        sample_seed=41,
        excluded_repositories=excluded,
    )

    assert first == second
    assert not excluded.intersection(item.candidate.repository.casefold() for item in first)


def test_stratified_sample_rejects_duplicate_repository_sampling_units() -> None:
    candidate = _candidate("Java", 1)

    with pytest.raises(ValueError, match="duplicate repository sampling unit"):
        repository_sampling.stratified_sample(
            (candidate, candidate),
            sample_size=1,
            sample_seed=1,
            languages=("Java",),
            excluded_repositories=set(),
        )


def test_write_sample_preserves_candidate_rows_and_records_sampling_design(tmp_path: Path) -> None:
    input_dir = tmp_path / "population"
    input_dir.mkdir()
    fieldnames = (
        "name",
        "lastCommitSHA",
        "lastCommit",
        "defaultBranch",
        "license",
        "mainLanguage",
    )
    rows = [
        {
            "name": f"owner-{language.casefold()}/project-{index}",
            "lastCommitSHA": f"{index + language_index * 10 + 1:040x}",
            "lastCommit": "2026-07-01T00:00:00+00:00",
            "defaultBranch": "main",
            "license": "MIT License",
            "mainLanguage": language,
        }
        for language_index, language in enumerate(repository_sampling.DEFAULT_LANGUAGES)
        for index in range(5)
    ]
    _write_csv(input_dir / "population.csv", fieldnames, rows)
    excluded_csv = tmp_path / "excluded.csv"
    _write_csv(excluded_csv, ("name",), [{"name": rows[0]["name"]}])
    output_dir = tmp_path / "sample"

    report = repository_sampling.write_stratified_sample(
        input_dir=input_dir,
        output_dir=output_dir,
        sample_size=8,
        sample_seed=7,
        exclude_csvs=(excluded_csv,),
    )

    with (output_dir / "input" / "candidates.csv").open(encoding="utf-8", newline="") as input_file:
        sampled_rows = list(csv.DictReader(input_file))
    with (output_dir / "sampling_manifest.csv").open(encoding="utf-8", newline="") as input_file:
        manifest_rows = list(csv.DictReader(input_file))
    configuration = json.loads((output_dir / "sampling_configuration.json").read_text(encoding="utf-8"))
    assert len(sampled_rows) == 8
    assert list(sampled_rows[0]) == list(fieldnames)
    assert rows[0]["name"] not in {row["name"] for row in sampled_rows}
    assert len(manifest_rows) == 8
    assert configuration["sample_size"] == 8
    assert configuration["sample_seed"] == 7
    assert configuration["excluded_repository_count"] == 1
    assert report.sampled == 8


def _candidate(language: str, index: int) -> repository.RepositoryCandidate:
    language_index = repository_sampling.DEFAULT_LANGUAGES.index(language)
    repository_name = f"owner-{language.casefold()}/project-{index}"
    return repository.RepositoryCandidate(
        repository=repository_name,
        revision=f"{index + language_index * 100 + 1:040x}",
        license_name="MIT License",
        source_file=f"{language.casefold()}.csv",
        input_index=language_index * 100 + index,
        fields={
            "name": repository_name,
            "lastCommitSHA": f"{index + language_index * 100 + 1:040x}",
            "lastCommit": "2026-07-01T00:00:00+00:00",
            "defaultBranch": "main",
            "license": "MIT License",
            "mainLanguage": language,
        },
    )


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
