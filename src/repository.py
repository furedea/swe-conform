"""Repository candidate input model and CSV loader."""

import csv
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REVISION_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
_REQUIRED_COLUMNS = frozenset({"name", "lastCommitSHA", "lastCommit", "defaultBranch", "license"})
SNAPSHOT_START = datetime(2026, 1, 1, tzinfo=UTC)
SELECTION_LANGUAGES = ("Java", "JavaScript", "Python", "TypeScript")
SELECTION_MINIMUMS: Mapping[str, int] = MappingProxyType(
    {
        "stargazers": 1000,
        "totalIssues": 200,
        "totalPullRequests": 200,
        "forks": 200,
        "contributors": 10,
    },
)


class SelectionCriteriaError(ValueError):
    """One or more repository candidates violate the collection criteria."""

    __slots__ = ("violations",)

    def __init__(self, violations: Sequence[str]) -> None:
        self.violations = tuple(violations)
        details = "\n".join(f"- {violation}" for violation in self.violations)
        super().__init__(
            f"Repository candidate selection validation failed ({len(self.violations)} violation(s)):\n{details}",
        )


@dataclass(frozen=True, slots=True)
class RepositoryCandidate:
    """A repository revision selected by the upstream quantitative filters."""

    repository: str
    revision: str
    license_name: str
    source_file: str
    input_index: int
    fields: Mapping[str, str]


def validate_selection_criteria(candidates: Sequence[RepositoryCandidate]) -> None:
    """Reject candidates that violate the quantitative collection criteria."""
    violations: list[str] = []
    language_counts: dict[str, int] = dict.fromkeys(SELECTION_LANGUAGES, 0)
    repository_counts: dict[str, int] = {}
    for candidate in candidates:
        repository_key = candidate.repository.casefold()
        repository_counts[repository_key] = repository_counts.get(repository_key, 0) + 1
        language = candidate.fields.get("mainLanguage", "").strip()
        if language in language_counts:
            language_counts[language] += 1
        else:
            violations.append(
                f"{candidate.repository}: mainLanguage must be one of "
                f"{', '.join(SELECTION_LANGUAGES)}; got {language!r}",
            )
        for field, minimum in SELECTION_MINIMUMS.items():
            value = _selection_integer(candidate, field, violations)
            if value is not None and value < minimum:
                violations.append(
                    f"{candidate.repository}: {field} must be at least {minimum}; got {value}",
                )
        is_fork = candidate.fields.get("isFork", "").strip().casefold()
        if is_fork != "false":
            violations.append(
                f"{candidate.repository}: isFork must be false; got {candidate.fields.get('isFork', '')!r}",
            )
    missing_languages = [language for language, count in language_counts.items() if count == 0]
    if missing_languages:
        violations.append(f"missing language strata: {', '.join(missing_languages)}")
    violations.extend(
        f"duplicate repository: {name}" for name, count in sorted(repository_counts.items()) if count > 1
    )
    if violations:
        raise SelectionCriteriaError(violations)


def selection_criteria_report() -> dict[str, object]:
    """Return the machine-readable collection criteria."""
    return {
        "stargazers_minimum": SELECTION_MINIMUMS["stargazers"],
        "total_issues_minimum": SELECTION_MINIMUMS["totalIssues"],
        "total_pull_requests_minimum": SELECTION_MINIMUMS["totalPullRequests"],
        "forks_minimum": SELECTION_MINIMUMS["forks"],
        "contributors_minimum": SELECTION_MINIMUMS["contributors"],
        "is_fork": False,
        "languages": list(SELECTION_LANGUAGES),
    }


def load_repository_candidates(
    input_dir: Path,
    *,
    enforce_snapshot_window: bool = True,
) -> tuple[RepositoryCandidate, ...]:
    """Load and validate repository candidates from every CSV in a directory."""
    candidates: list[RepositoryCandidate] = []
    for input_path in sorted(input_dir.glob("*.csv")):
        candidates.extend(
            _load_csv(
                input_path,
                start_index=len(candidates),
                enforce_snapshot_window=enforce_snapshot_window,
            ),
        )
    if not candidates:
        msg = f"No repository candidates found in {input_dir}"
        raise ValueError(msg)
    return tuple(candidates)


def _load_csv(
    input_path: Path,
    *,
    start_index: int,
    enforce_snapshot_window: bool,
) -> list[RepositoryCandidate]:
    with input_path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        _validate_columns(input_path, reader.fieldnames)
        return [
            _candidate_from_row(
                row,
                source_file=input_path.name,
                input_index=start_index + row_index,
                enforce_snapshot_window=enforce_snapshot_window,
            )
            for row_index, row in enumerate(reader)
        ]


def _validate_columns(input_path: Path, fieldnames: Sequence[str] | None) -> None:
    missing_columns = _REQUIRED_COLUMNS.difference(fieldnames or ())
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        msg = f"{input_path} is missing required columns: {missing}"
        raise ValueError(msg)


def _selection_integer(
    candidate: RepositoryCandidate,
    field: str,
    violations: list[str],
) -> int | None:
    raw_value = candidate.fields.get(field, "").strip()
    try:
        return int(raw_value)
    except ValueError:
        violations.append(f"{candidate.repository}: {field} must be an integer; got {raw_value!r}")
        return None


def _candidate_from_row(
    row: Mapping[str, str | None],
    *,
    source_file: str,
    input_index: int,
    enforce_snapshot_window: bool,
) -> RepositoryCandidate:
    fields = {key: value or "" for key, value in row.items()}
    repository = fields["name"].strip()
    revision = fields["lastCommitSHA"].strip()
    if not _REPOSITORY_PATTERN.fullmatch(repository):
        msg = f"Repository name must be owner/repository: {repository!r}"
        raise ValueError(msg)
    if not _REVISION_PATTERN.fullmatch(revision):
        msg = f"Repository revision must be a hexadecimal commit SHA: {revision!r}"
        raise ValueError(msg)
    _validate_snapshot_committed_at(
        fields["lastCommit"],
        repository=repository,
        enforce_snapshot_window=enforce_snapshot_window,
    )
    return RepositoryCandidate(
        repository=repository,
        revision=revision,
        license_name=fields["license"].strip(),
        source_file=source_file,
        input_index=input_index,
        fields=MappingProxyType(fields),
    )


def _validate_snapshot_committed_at(
    raw_value: str,
    *,
    repository: str,
    enforce_snapshot_window: bool,
) -> None:
    try:
        committed_at = datetime.fromisoformat(raw_value)
    except ValueError as error:
        msg = f"Repository lastCommit must be an ISO 8601 timestamp: {repository}: {raw_value!r}"
        raise ValueError(msg) from error
    if committed_at.tzinfo is None:
        committed_at = committed_at.replace(tzinfo=UTC)
    committed_at = committed_at.astimezone(UTC)
    if not enforce_snapshot_window:
        return
    if committed_at < SNAPSHOT_START:
        msg = f"Repository commit is before the 2026-01-01 UTC snapshot start: {repository}: {raw_value}"
        raise ValueError(msg)
