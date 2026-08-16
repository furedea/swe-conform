"""Apply completed human decisions to guideline-file candidates."""

import csv
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_ACCEPTED_FILES_FILENAME = "accepted_guideline_files.csv"
_REPOSITORY_OUTCOMES_FILENAME = "repository_review_outcomes.csv"
_REPOSITORY_OUTCOME_FIELDS = (
    "repository",
    "status",
    "accepted_file_count",
    "duplicate_file_count",
    "not_found_file_count",
)
_REQUIRED_FIELDS = frozenset({"repository", "file", "github_url", "human_decision"})
_DUPLICATE_FIELD = "duplicate_of"
_HUMAN_DECISIONS = frozenset({"pass", "not_found"})


@dataclass(frozen=True, slots=True)
class GuidelineReviewReport:
    """Counts produced by one completed guideline-review checklist."""

    input_files: int
    accepted_files: int
    duplicate_files: int
    not_found_files: int
    reviewed_repositories: int
    accepted_repositories: int
    rejected_repositories: int
    output_dir: Path


@dataclass(frozen=True, slots=True)
class GuidelineReviewState:
    """Repository outcomes derived from completed file-level human decisions."""

    confirmed_repositories: set[str]
    rejected_repositories: set[str]


def apply_guideline_checklist(*, checklist_path: Path, output_dir: Path) -> GuidelineReviewReport:
    """Validate a completed checklist before materializing accepted files."""
    fieldnames, rows = load_completed_guideline_rows(checklist_path)
    accepted = tuple(row for row in rows if _is_accepted(row))
    outcomes = _repository_outcomes(rows)
    accepted_repositories = sum(row["status"] == "accepted" for row in outcomes)
    report = GuidelineReviewReport(
        input_files=len(rows),
        accepted_files=len(accepted),
        duplicate_files=sum(bool(row["duplicate_of"].strip()) for row in rows),
        not_found_files=sum(row["human_decision"].strip() == "not_found" for row in rows),
        reviewed_repositories=len(outcomes),
        accepted_repositories=accepted_repositories,
        rejected_repositories=len(outcomes) - accepted_repositories,
        output_dir=output_dir,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / _ACCEPTED_FILES_FILENAME, fieldnames=fieldnames, rows=accepted)
    _write_csv(
        output_dir / _REPOSITORY_OUTCOMES_FILENAME,
        fieldnames=_REPOSITORY_OUTCOME_FIELDS,
        rows=outcomes,
    )
    _write_summary(output_dir / "summary.json", report)
    return report


def load_completed_review_state(checklist_path: Path | None) -> GuidelineReviewState:
    """Return accepted and rejected repositories from a completed checklist."""
    if checklist_path is None:
        return GuidelineReviewState(set(), set())
    _, rows = load_completed_guideline_rows(checklist_path)
    repositories = {row["repository"].strip() for row in rows if row["repository"].strip()}
    confirmed = {row["repository"].strip() for row in rows if row["repository"].strip() if _is_accepted(row)}
    return GuidelineReviewState(
        confirmed_repositories=confirmed,
        rejected_repositories=repositories - confirmed,
    )


def load_completed_guideline_rows(
    checklist_path: Path,
    *,
    require_duplicate_column: bool = True,
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    """Read and validate completed file-level human decisions."""
    with checklist_path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = tuple(reader.fieldnames or ())
        missing = set(_REQUIRED_FIELDS.difference(fieldnames))
        if require_duplicate_column and _DUPLICATE_FIELD not in fieldnames:
            missing.add(_DUPLICATE_FIELD)
        if missing:
            raise ValueError(f"checklist is missing required columns: {', '.join(sorted(missing))}")
        rows = tuple({**row, _DUPLICATE_FIELD: row.get(_DUPLICATE_FIELD, "")} for row in reader)
    for line_number, row in enumerate(rows, start=2):
        decision = row["human_decision"].strip()
        if not decision:
            raise ValueError(f"human_decision is blank at line {line_number}: {row['file']}")
        if decision not in _HUMAN_DECISIONS:
            raise ValueError(f"invalid human_decision at line {line_number}: {decision}")
    rows_by_file: dict[str, dict[str, str]] = {}
    for line_number, row in enumerate(rows, start=2):
        file = row["file"].strip()
        if file in rows_by_file:
            raise ValueError(f"file must be unique at line {line_number}: {file}")
        rows_by_file[file] = row
    for line_number, row in enumerate(rows, start=2):
        duplicate_of = row["duplicate_of"].strip()
        if duplicate_of and duplicate_of not in rows_by_file:
            raise ValueError(f"duplicate_of target is absent at line {line_number}: {duplicate_of}")
        if duplicate_of and row["human_decision"].strip() != "pass":
            raise ValueError(f"duplicate row must have human_decision=pass at line {line_number}: {row['file']}")
        if duplicate_of:
            target = rows_by_file[duplicate_of]
            if target["human_decision"].strip() != "pass" or target["duplicate_of"].strip():
                raise ValueError(
                    f"duplicate_of target must be a non-duplicate pass at line {line_number}: {duplicate_of}",
                )
    return fieldnames, rows


def _repository_outcomes(rows: Sequence[Mapping[str, str]]) -> tuple[dict[str, object], ...]:
    grouped: defaultdict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["repository"].strip()].append(row)
    outcomes = []
    for repository in sorted(grouped, key=str.casefold):
        repository_rows = grouped[repository]
        accepted_count = sum(_is_accepted(row) for row in repository_rows)
        duplicate_count = sum(bool(row["duplicate_of"].strip()) for row in repository_rows)
        not_found_count = sum(row["human_decision"].strip() == "not_found" for row in repository_rows)
        outcomes.append(
            {
                "repository": repository,
                "status": "accepted" if accepted_count else "rejected",
                "accepted_file_count": accepted_count,
                "duplicate_file_count": duplicate_count,
                "not_found_file_count": not_found_count,
            },
        )
    return tuple(outcomes)


def _is_accepted(row: Mapping[str, str]) -> bool:
    return row["human_decision"].strip() == "pass" and not row["duplicate_of"].strip()


def _write_csv(
    path: Path,
    *,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(path)


def _write_summary(path: Path, report: GuidelineReviewReport) -> None:
    document = {
        "accepted_files": report.accepted_files,
        "accepted_repositories": report.accepted_repositories,
        "duplicate_files": report.duplicate_files,
        "input_files": report.input_files,
        "not_found_files": report.not_found_files,
        "rejected_repositories": report.rejected_repositories,
        "reviewed_repositories": report.reviewed_repositories,
    }
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(f"{json.dumps(document, indent=2, sort_keys=True)}\n", encoding="utf-8")
    temporary_path.replace(path)
