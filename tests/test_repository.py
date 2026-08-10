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


def test_load_repository_candidates_accepts_a_last_commit_after_july(tmp_path: Path) -> None:
    input_path = tmp_path / "python.csv"
    input_path.write_text(
        "name,lastCommitSHA,lastCommit,defaultBranch,license\n"
        "example/project,0123456789abcdef,2026-08-01T00:00:00,main,MIT License\n",
        encoding="utf-8",
    )

    candidates = repository.load_repository_candidates(tmp_path)

    assert candidates[0].fields["lastCommit"] == "2026-08-01T00:00:00"


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


def test_default_repository_candidates_have_a_last_commit_since_2026() -> None:
    candidates = repository.load_repository_candidates(Path("docs/repository-candidates"))

    committed_at = tuple(
        datetime.fromisoformat(candidate.fields["lastCommit"]).replace(tzinfo=UTC) for candidate in candidates
    )

    assert min(committed_at) >= repository.SNAPSHOT_START


def test_selection_criteria_accept_exact_thresholds_for_all_languages() -> None:
    candidates = tuple(
        _selection_candidate(language, index) for index, language in enumerate(repository.SELECTION_LANGUAGES)
    )

    repository.validate_selection_criteria(candidates)


@pytest.mark.parametrize(
    ("field", "minimum"),
    [
        ("stargazers", 1000),
        ("totalIssues", 200),
        ("totalPullRequests", 200),
        ("forks", 200),
        ("contributors", 10),
    ],
)
def test_selection_criteria_reject_values_below_each_minimum(field: str, minimum: int) -> None:
    candidates = [
        _selection_candidate(language, index) for index, language in enumerate(repository.SELECTION_LANGUAGES)
    ]
    invalid = candidates[0]
    candidates[0] = _with_field(invalid, field, str(minimum - 1))

    with pytest.raises(repository.SelectionCriteriaError, match=f"{field} must be at least {minimum}"):
        repository.validate_selection_criteria(candidates)


def test_selection_criteria_reject_a_fork() -> None:
    candidates = [
        _selection_candidate(language, index) for index, language in enumerate(repository.SELECTION_LANGUAGES)
    ]
    candidates[0] = _with_field(candidates[0], "isFork", "true")

    with pytest.raises(repository.SelectionCriteriaError, match="isFork must be false"):
        repository.validate_selection_criteria(candidates)


def test_selection_criteria_reject_a_non_integer_metric() -> None:
    candidates = [
        _selection_candidate(language, index) for index, language in enumerate(repository.SELECTION_LANGUAGES)
    ]
    candidates[0] = _with_field(candidates[0], "contributors", "unknown")

    with pytest.raises(repository.SelectionCriteriaError, match="contributors must be an integer"):
        repository.validate_selection_criteria(candidates)


def test_selection_criteria_require_all_four_languages() -> None:
    candidates = tuple(
        _selection_candidate(language, index) for index, language in enumerate(repository.SELECTION_LANGUAGES[:-1])
    )

    with pytest.raises(repository.SelectionCriteriaError, match="missing language strata: TypeScript"):
        repository.validate_selection_criteria(candidates)


def test_selection_criteria_reject_duplicate_repositories() -> None:
    candidates = tuple(
        _selection_candidate(language, index) for index, language in enumerate(repository.SELECTION_LANGUAGES)
    )

    with pytest.raises(repository.SelectionCriteriaError, match="duplicate repository: owner-java/project"):
        repository.validate_selection_criteria((*candidates, candidates[0]))


def _selection_candidate(language: str, index: int) -> repository.RepositoryCandidate:
    name = f"owner-{language.casefold()}/project"
    return repository.RepositoryCandidate(
        repository=name,
        revision=f"{index + 1:040x}",
        license_name="MIT License",
        source_file=f"{language.casefold()}.csv",
        input_index=index,
        fields={
            "name": name,
            "lastCommitSHA": f"{index + 1:040x}",
            "lastCommit": "2026-07-31T23:59:59+00:00",
            "defaultBranch": "main",
            "license": "MIT License",
            "mainLanguage": language,
            "stargazers": "1000",
            "totalIssues": "200",
            "totalPullRequests": "200",
            "forks": "200",
            "contributors": "10",
            "isFork": "false",
        },
    )


def _with_field(
    candidate: repository.RepositoryCandidate,
    field: str,
    value: str,
) -> repository.RepositoryCandidate:
    fields = {**candidate.fields, field: value}
    return repository.RepositoryCandidate(
        repository=candidate.repository,
        revision=candidate.revision,
        license_name=candidate.license_name,
        source_file=candidate.source_file,
        input_index=candidate.input_index,
        fields=fields,
    )
