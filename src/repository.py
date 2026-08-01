"""Repository candidate input model and CSV loader."""

import csv
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_REVISION_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")
_REQUIRED_COLUMNS = frozenset({"name", "lastCommitSHA", "license"})


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
    return RepositoryCandidate(
        repository=repository,
        revision=revision,
        license_name=fields["license"].strip(),
        source_file=source_file,
        input_index=input_index,
        fields=MappingProxyType(fields),
    )
