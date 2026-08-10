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


@dataclass(frozen=True, slots=True)
class MarkdownReviewReport:
    """Describe a completed manual-review export."""

    files: int
    output_dir: Path


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


def _local_filename(row: Mapping[str, str]) -> str:
    repository = re.sub(r"[^A-Za-z0-9._-]+", "_", row["name"].replace("/", "--"))
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", row["markdown_path"].replace("/", "__"))
    return str(PurePosixPath(repository) / filename)


def _numbered_filename(filename: str, filename_counts: dict[str, int]) -> str:
    count = filename_counts.get(filename, 0) + 1
    filename_counts[filename] = count
    if count == 1:
        return filename
    path = PurePosixPath(filename)
    numbered_name = f"{path.stem}__{count}{path.suffix}"
    return str(path.with_name(numbered_name))


def _write_checklist(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=_CHECKLIST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
