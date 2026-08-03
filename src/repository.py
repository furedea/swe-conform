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
SNAPSHOT_CUTOFF = datetime(2026, 8, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class RepositoryCandidate:
    """A repository revision selected by the upstream quantitative filters."""

    repository: str
    revision: str
    license_name: str
    source_file: str
    input_index: int
    fields: Mapping[str, str]


def load_repository_candidates(input_dir: Path) -> tuple[RepositoryCandidate, ...]:
    """Load and validate repository candidates from every CSV in a directory."""
    candidates: list[RepositoryCandidate] = []
    for input_path in sorted(input_dir.glob("*.csv")):
        candidates.extend(_load_csv(input_path, start_index=len(candidates)))
    if not candidates:
        msg = f"No repository candidates found in {input_dir}"
        raise ValueError(msg)
    return tuple(candidates)


def _load_csv(input_path: Path, *, start_index: int) -> list[RepositoryCandidate]:
    with input_path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        _validate_columns(input_path, reader.fieldnames)
        return [
            _candidate_from_row(
                row,
                source_file=input_path.name,
                input_index=start_index + row_index,
            )
            for row_index, row in enumerate(reader)
        ]


def _validate_columns(input_path: Path, fieldnames: Sequence[str] | None) -> None:
    missing_columns = _REQUIRED_COLUMNS.difference(fieldnames or ())
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        msg = f"{input_path} is missing required columns: {missing}"
        raise ValueError(msg)


def _candidate_from_row(
    row: Mapping[str, str | None],
    *,
    source_file: str,
    input_index: int,
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
    _validate_snapshot_committed_at(fields["lastCommit"], repository=repository)
    return RepositoryCandidate(
        repository=repository,
        revision=revision,
        license_name=fields["license"].strip(),
        source_file=source_file,
        input_index=input_index,
        fields=MappingProxyType(fields),
    )


def _validate_snapshot_committed_at(raw_value: str, *, repository: str) -> None:
    try:
        committed_at = datetime.fromisoformat(raw_value)
    except ValueError as error:
        msg = f"Repository lastCommit must be an ISO 8601 timestamp: {repository}: {raw_value!r}"
        raise ValueError(msg) from error
    if committed_at.tzinfo is None:
        committed_at = committed_at.replace(tzinfo=UTC)
    committed_at = committed_at.astimezone(UTC)
    if committed_at < SNAPSHOT_START:
        msg = f"Repository commit is before the 2026-01-01 UTC snapshot start: {repository}: {raw_value}"
        raise ValueError(msg)
    if committed_at < SNAPSHOT_CUTOFF:
        return
    msg = f"Repository commit is outside the 2026-07-31 UTC snapshot cutoff: {repository}: {raw_value}"
    raise ValueError(msg)
