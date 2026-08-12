"""Materialize model-selected Markdown inputs for manual review."""

import csv
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

_CHECKLIST_FIELDS = (
    "repository",
    "file",
    "github_url",
    "llm_decision",
    "human_decision",
    "note",
)
_BLIND_CHECKLIST_FIELDS = (
    "repository",
    "file",
    "github_url",
    "review_origin",
    "llm_decision",
    "human_decision",
    "codex_decision",
    "codex_reason",
    "note",
)
_MECHANICAL_FILTER_ORIGIN = "mechanical_filter"


@dataclass(frozen=True, slots=True)
class MarkdownReviewReport:
    """Describe a completed manual-review export."""

    files: int
    output_dir: Path


def export_candidate_files(
    *,
    candidate_csv: Path,
    batch_input_path: Path,
    output_dir: Path,
) -> MarkdownReviewReport:
    """Write mechanically selected Markdown candidates for blind review."""
    candidates = _candidate_rows(candidate_csv)
    inputs = _batch_inputs_by_identity(batch_input_path)
    candidate_identities = {_candidate_identity(row) for row in candidates}
    if candidate_identities != set(inputs):
        msg = "Candidate and prepared-input identities must be equal."
        raise ValueError(msg)
    output_dir.mkdir(parents=True, exist_ok=False)
    checklist_rows = []
    filename_counts: dict[str, int] = {}
    for row in candidates:
        input_document = inputs[_candidate_identity(row)]
        local_file = _numbered_filename(_local_filename(row), filename_counts)
        local_path = output_dir / local_file
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(str(input_document["content"]), encoding="utf-8")
        checklist_rows.append(
            {
                "repository": row["name"],
                "file": local_file,
                "github_url": row["markdown_url"],
                "review_origin": _MECHANICAL_FILTER_ORIGIN,
                "llm_decision": "",
                "human_decision": "",
                "codex_decision": "",
                "codex_reason": "",
                "note": "",
            },
        )
    _write_checklist(
        output_dir / "checklist.csv",
        checklist_rows,
        fieldnames=_BLIND_CHECKLIST_FIELDS,
    )
    return MarkdownReviewReport(files=len(checklist_rows), output_dir=output_dir)


def export_pass_files(
    *,
    classified_files_path: Path,
    batch_input_path: Path,
    output_dir: Path,
) -> MarkdownReviewReport:
    """Write model-selected Markdown inputs as individual review files."""
    review_rows = _review_rows(classified_files_path)
    inputs = _batch_inputs(batch_input_path)
    output_dir.mkdir(parents=True, exist_ok=False)
    checklist_rows = []
    filename_counts: dict[str, int] = {}
    for row in review_rows:
        custom_id = row["custom_id"]
        input_document = inputs[custom_id]
        local_file = _numbered_filename(_local_filename(row), filename_counts)
        local_path = output_dir / local_file
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(str(input_document["content"]), encoding="utf-8")
        checklist_rows.append(
            {
                "repository": row["name"],
                "file": local_file,
                "github_url": row["markdown_url"],
                "llm_decision": row["status"],
                "human_decision": "",
                "note": "",
            },
        )
    _write_checklist(output_dir / "checklist.csv", checklist_rows)
    return MarkdownReviewReport(files=len(checklist_rows), output_dir=output_dir)


def _review_rows(path: Path) -> tuple[dict[str, str], ...]:
    with path.open(encoding="utf-8", newline="") as input_file:
        rows = tuple(dict(row) for row in csv.DictReader(input_file) if row.get("status") in {"pass", "review"})
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row["name"].casefold(),
                row["markdown_path"].casefold(),
                row["custom_id"],
            ),
        ),
    )


def _candidate_rows(path: Path) -> tuple[dict[str, str], ...]:
    with path.open(encoding="utf-8", newline="") as input_file:
        rows = tuple(dict(row) for row in csv.DictReader(input_file))
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row["name"].casefold(),
                row["markdown_path"].casefold(),
                row["lastCommitSHA"].casefold(),
            ),
        ),
    )


def _batch_inputs(path: Path) -> dict[str, Mapping[str, object]]:
    inputs: dict[str, Mapping[str, object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        request = cast(Mapping[str, object], json.loads(line))
        body = cast(Mapping[str, object], request.get("body") or {})
        input_document = cast(Mapping[str, object], json.loads(str(body.get("input", ""))))
        inputs[str(request.get("custom_id", ""))] = input_document
    return inputs


def _batch_inputs_by_identity(path: Path) -> dict[tuple[str, str, str], Mapping[str, object]]:
    return {
        (
            str(document["repository"]),
            str(document["revision"]),
            str(document["path"]),
        ): document
        for document in _batch_inputs(path).values()
    }


def _candidate_identity(row: Mapping[str, str]) -> tuple[str, str, str]:
    return row["name"], row["lastCommitSHA"], row["markdown_path"]


def _local_filename(row: Mapping[str, str]) -> str:
    repository = re.sub(r"[^A-Za-z0-9._-]+", "_", row["name"].replace("/", "--"))
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", row["markdown_path"].replace("/", "__"))
    return str(PurePosixPath(repository) / filename)


def _numbered_filename(filename: str, filename_counts: dict[str, int]) -> str:
    identity = filename.casefold()
    count = filename_counts.get(identity, 0) + 1
    filename_counts[identity] = count
    if count == 1:
        return filename
    path = PurePosixPath(filename)
    numbered_name = f"{path.stem}__{count}{path.suffix}"
    return str(path.with_name(numbered_name))


def _write_checklist(
    path: Path,
    rows: list[dict[str, str]],
    *,
    fieldnames: tuple[str, ...] = _CHECKLIST_FIELDS,
) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
