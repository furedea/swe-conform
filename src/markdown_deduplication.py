"""Deduplicate Markdown CSV rows by exact content identity."""

import csv
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_DEDUPLICATED_FILENAME = "deduplicated_files.csv"
_OCCURRENCES_FILENAME = "duplicate_occurrences.csv"
_CONFLICTS_FILENAME = "decision_conflicts.csv"
_SUMMARY_FILENAME = "summary.json"
_IDENTITY_FIELDS = frozenset({"name", "lastCommitSHA", "markdown_path"})
_HASH_FIELDS = frozenset({"content_sha256", "blob_sha"})
_OCCURRENCE_FIELDS = ("exact_group_id", "is_canonical")


@dataclass(frozen=True, slots=True)
class ExactDeduplicationReport:
    """Counts produced by exact-content deduplication."""

    input_files: int
    unique_contents: int
    duplicate_groups: int
    redundant_files: int
    decision_conflict_groups: int


@dataclass(frozen=True, slots=True)
class ExactDeduplicationOutputFiles:
    """Names of the materialized exact-deduplication reports."""

    deduplicated: str = _DEDUPLICATED_FILENAME
    occurrences: str = _OCCURRENCES_FILENAME
    conflicts: str = _CONFLICTS_FILENAME
    summary: str = _SUMMARY_FILENAME


@dataclass(frozen=True, slots=True)
class ExactContentIdentity:
    """Content identity supplied by a cryptographic digest."""

    algorithm: str
    value: str

    @property
    def group_id(self) -> str:
        return f"{self.algorithm}:{self.value}"


def write_exact_deduplication(
    *,
    input_csv: Path,
    output_dir: Path,
    output_files: ExactDeduplicationOutputFiles | None = None,
) -> ExactDeduplicationReport:
    """Write one deterministic representative for each exact content hash."""
    files = output_files or ExactDeduplicationOutputFiles()
    fieldnames, rows = _read_rows(input_csv)
    _require_fields(fieldnames, _IDENTITY_FIELDS, path=input_csv)
    if _HASH_FIELDS.isdisjoint(fieldnames):
        raise ValueError(f"{input_csv} requires content_sha256 or blob_sha")
    grouped: defaultdict[ExactContentIdentity, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[_content_identity(row, path=input_csv)].append(row)
    representatives = tuple(min(group, key=_representative_order) for _, group in _ordered_groups(grouped))
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / files.deduplicated, fieldnames=fieldnames, rows=representatives)
    _write_csv(
        output_dir / files.occurrences,
        fieldnames=(*fieldnames, *_OCCURRENCE_FIELDS),
        rows=_occurrence_rows(grouped),
    )
    conflicting = _conflicting_groups(grouped)
    _write_csv(
        output_dir / files.conflicts,
        fieldnames=(*fieldnames, *_OCCURRENCE_FIELDS),
        rows=_occurrence_rows(conflicting),
    )
    report = ExactDeduplicationReport(
        input_files=len(rows),
        unique_contents=len(representatives),
        duplicate_groups=sum(len(group) > 1 for group in grouped.values()),
        redundant_files=len(rows) - len(representatives),
        decision_conflict_groups=len(conflicting),
    )
    _write_summary(output_dir / files.summary, report)
    return report


def _representative_order(row: Mapping[str, str]) -> tuple[str, str, str, str]:
    path = row["markdown_path"]
    return row["name"].casefold(), row["lastCommitSHA"], path.casefold(), path


def _content_identity(row: Mapping[str, str], *, path: Path) -> ExactContentIdentity:
    if content_sha256 := row.get("content_sha256", ""):
        return ExactContentIdentity("sha256", content_sha256)
    if blob_sha := row.get("blob_sha", ""):
        return ExactContentIdentity("git_blob", blob_sha)
    raise ValueError(f"{path} has a row without content_sha256 or blob_sha: {row['markdown_path']}")


def _occurrence_rows(
    grouped: Mapping[ExactContentIdentity, Sequence[dict[str, str]]],
) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for identity, group in _ordered_groups(grouped):
        canonical = min(group, key=_representative_order)
        rows.extend(
            (
                {
                    **row,
                    "exact_group_id": identity.group_id,
                    "is_canonical": str(row is canonical).lower(),
                }
                for row in sorted(group, key=_representative_order)
            ),
        )
    return tuple(rows)


def _ordered_groups(
    grouped: Mapping[ExactContentIdentity, Sequence[dict[str, str]]],
) -> tuple[tuple[ExactContentIdentity, Sequence[dict[str, str]]], ...]:
    return tuple(
        sorted(
            grouped.items(),
            key=lambda item: _representative_order(min(item[1], key=_representative_order)),
        ),
    )


def _conflicting_groups(
    grouped: Mapping[ExactContentIdentity, Sequence[dict[str, str]]],
) -> dict[ExactContentIdentity, Sequence[dict[str, str]]]:
    return {
        identity: group
        for identity, group in grouped.items()
        if len({row.get("status", "") for row in group if row.get("status", "")}) > 1
    }


def _read_rows(path: Path) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    with path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = tuple(reader.fieldnames or ())
        return fieldnames, tuple(dict(row) for row in reader)


def _require_fields(fieldnames: Sequence[str], required: frozenset[str], *, path: Path) -> None:
    missing = required.difference(fieldnames)
    if missing:
        raise ValueError(f"{path} is missing required columns: {', '.join(sorted(missing))}")


def _write_csv(path: Path, *, fieldnames: Sequence[str], rows: Sequence[Mapping[str, object]]) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(path)


def _write_summary(path: Path, report: ExactDeduplicationReport) -> None:
    summary = {
        "decision_conflict_groups": report.decision_conflict_groups,
        "duplicate_groups": report.duplicate_groups,
        "input_files": report.input_files,
        "redundant_files": report.redundant_files,
        "unique_contents": report.unique_contents,
    }
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(f"{json.dumps(summary, indent=2, sort_keys=True)}\n", encoding="utf-8")
    temporary_path.replace(path)
