"""Tests for exact-content Markdown deduplication."""

import csv
import json
from pathlib import Path

import markdown_deduplication


def test_exact_deduplication_keeps_one_representative_per_content_hash(tmp_path: Path) -> None:
    input_csv = tmp_path / "markdown_filename_files.csv"
    output_dir = tmp_path / "exact-dedup"
    _write_rows(
        input_csv,
        (
            _candidate_row("CLAUDE.md", content_sha256="a" * 64, blob_sha="1" * 40),
            _candidate_row("AGENTS.md", content_sha256="a" * 64, blob_sha="1" * 40),
            _candidate_row("CONTRIBUTING.md", content_sha256="b" * 64, blob_sha="2" * 40),
        ),
    )

    report = markdown_deduplication.write_exact_deduplication(
        input_csv=input_csv,
        output_dir=output_dir,
    )

    with (output_dir / "deduplicated_files.csv").open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert [row["markdown_path"] for row in rows] == ["AGENTS.md", "CONTRIBUTING.md"]
    assert report.input_files == 3
    assert report.unique_contents == 2


def test_exact_deduplication_records_every_original_occurrence(tmp_path: Path) -> None:
    input_csv = tmp_path / "markdown_filename_files.csv"
    output_dir = tmp_path / "exact-dedup"
    content_sha256 = "a" * 64
    _write_rows(
        input_csv,
        (
            _candidate_row("CLAUDE.md", content_sha256=content_sha256, blob_sha="1" * 40),
            _candidate_row("AGENTS.md", content_sha256=content_sha256, blob_sha="1" * 40),
        ),
    )

    markdown_deduplication.write_exact_deduplication(input_csv=input_csv, output_dir=output_dir)

    with (output_dir / "duplicate_occurrences.csv").open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert [(row["markdown_path"], row["is_canonical"]) for row in rows] == [
        ("AGENTS.md", "true"),
        ("CLAUDE.md", "false"),
    ]
    assert {row["exact_group_id"] for row in rows} == {f"sha256:{content_sha256}"}


def test_exact_deduplication_uses_git_blob_identity_for_existing_csv(tmp_path: Path) -> None:
    input_csv = tmp_path / "classified_files.csv"
    output_dir = tmp_path / "exact-dedup"
    blob_sha = "1" * 40
    rows = [
        _candidate_row("CLAUDE.md", content_sha256="a" * 64, blob_sha=blob_sha),
        _candidate_row("AGENTS.md", content_sha256="a" * 64, blob_sha=blob_sha),
    ]
    for row in rows:
        del row["content_sha256"]
    _write_rows(input_csv, tuple(rows))

    markdown_deduplication.write_exact_deduplication(input_csv=input_csv, output_dir=output_dir)

    with (output_dir / "duplicate_occurrences.csv").open(encoding="utf-8", newline="") as input_file:
        occurrences = list(csv.DictReader(input_file))
    assert {row["exact_group_id"] for row in occurrences} == {f"git_blob:{blob_sha}"}


def test_exact_deduplication_reports_conflicting_existing_decisions(tmp_path: Path) -> None:
    input_csv = tmp_path / "classified_files.csv"
    output_dir = tmp_path / "exact-dedup"
    rows = [
        _candidate_row("CLAUDE.md", content_sha256="a" * 64, blob_sha="1" * 40),
        _candidate_row("AGENTS.md", content_sha256="a" * 64, blob_sha="1" * 40),
    ]
    rows[0]["status"] = "pass"
    rows[1]["status"] = "not_found"
    _write_rows(input_csv, tuple(rows))

    report = markdown_deduplication.write_exact_deduplication(
        input_csv=input_csv,
        output_dir=output_dir,
    )

    with (output_dir / "decision_conflicts.csv").open(encoding="utf-8", newline="") as input_file:
        conflicts = list(csv.DictReader(input_file))
    assert [row["status"] for row in conflicts] == ["not_found", "pass"]
    assert report.decision_conflict_groups == 1


def test_exact_deduplication_writes_reproducible_counts(tmp_path: Path) -> None:
    input_csv = tmp_path / "markdown_filename_files.csv"
    output_dir = tmp_path / "exact-dedup"
    _write_rows(
        input_csv,
        (
            _candidate_row("CLAUDE.md", content_sha256="a" * 64, blob_sha="1" * 40),
            _candidate_row("AGENTS.md", content_sha256="a" * 64, blob_sha="1" * 40),
            _candidate_row("CONTRIBUTING.md", content_sha256="b" * 64, blob_sha="2" * 40),
        ),
    )

    markdown_deduplication.write_exact_deduplication(input_csv=input_csv, output_dir=output_dir)

    assert json.loads((output_dir / "summary.json").read_text(encoding="utf-8")) == {
        "decision_conflict_groups": 0,
        "duplicate_groups": 1,
        "input_files": 3,
        "redundant_files": 1,
        "unique_contents": 2,
    }


def test_exact_deduplication_outputs_do_not_depend_on_input_row_order(tmp_path: Path) -> None:
    rows = (
        _candidate_row("Z-RULES.md", content_sha256="b" * 64, blob_sha="2" * 40),
        _candidate_row("CLAUDE.md", content_sha256="a" * 64, blob_sha="1" * 40),
        _candidate_row("AGENTS.md", content_sha256="a" * 64, blob_sha="1" * 40),
    )
    first_csv = tmp_path / "first.csv"
    second_csv = tmp_path / "second.csv"
    _write_rows(first_csv, rows)
    _write_rows(second_csv, tuple(reversed(rows)))

    markdown_deduplication.write_exact_deduplication(
        input_csv=first_csv,
        output_dir=tmp_path / "first",
    )
    markdown_deduplication.write_exact_deduplication(
        input_csv=second_csv,
        output_dir=tmp_path / "second",
    )

    for filename in ("deduplicated_files.csv", "duplicate_occurrences.csv"):
        assert (tmp_path / "first" / filename).read_bytes() == (tmp_path / "second" / filename).read_bytes()


def _candidate_row(path: str, *, content_sha256: str, blob_sha: str) -> dict[str, str]:
    return {
        "name": "example/project",
        "lastCommitSHA": "f" * 40,
        "markdown_path": path,
        "blob_sha": blob_sha,
        "content_sha256": content_sha256,
        "size_bytes": "42",
        "markdown_url": f"https://github.com/example/project/blob/{'f' * 40}/{path}",
        "matched_filename_terms": "agents|claude",
        "matched_content_terms": "rule",
    }


def _write_rows(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
