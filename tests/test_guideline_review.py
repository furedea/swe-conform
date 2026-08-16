"""Tests for applying completed guideline-review checklists."""

import csv
import json
from pathlib import Path

import pytest

import guideline_review


def test_incomplete_checklist_is_rejected_before_outputs_are_written(tmp_path: Path) -> None:
    checklist_path = tmp_path / "checklist.csv"
    _write_checklist(
        checklist_path,
        (
            _review_row("example/accepted", "accepted.md", human_decision="pass"),
            _review_row("example/incomplete", "incomplete.md", human_decision=""),
        ),
    )
    output_dir = tmp_path / "applied"

    with pytest.raises(ValueError, match=r"human_decision is blank.*line 3"):
        guideline_review.apply_guideline_checklist(
            checklist_path=checklist_path,
            output_dir=output_dir,
        )

    assert not output_dir.exists()


def test_unknown_human_decision_is_rejected_before_outputs_are_written(tmp_path: Path) -> None:
    checklist_path = tmp_path / "checklist.csv"
    _write_checklist(
        checklist_path,
        (_review_row("example/project", "rules.md", human_decision="review"),),
    )
    output_dir = tmp_path / "applied"

    with pytest.raises(ValueError, match=r"invalid human_decision.*line 2"):
        guideline_review.apply_guideline_checklist(
            checklist_path=checklist_path,
            output_dir=output_dir,
        )

    assert not output_dir.exists()


def test_duplicate_reference_must_name_a_file_in_the_same_checklist(tmp_path: Path) -> None:
    checklist_path = tmp_path / "checklist.csv"
    _write_checklist(
        checklist_path,
        (
            _review_row(
                "example/project",
                "translation.md",
                human_decision="pass",
                duplicate_of="missing.md",
            ),
        ),
    )

    with pytest.raises(ValueError, match=r"duplicate_of target is absent.*line 2.*missing\.md"):
        guideline_review.apply_guideline_checklist(
            checklist_path=checklist_path,
            output_dir=tmp_path / "applied",
        )


def test_checklist_file_identifiers_must_be_unique(tmp_path: Path) -> None:
    checklist_path = tmp_path / "checklist.csv"
    _write_checklist(
        checklist_path,
        (
            _review_row("example/one", "same.md", human_decision="pass"),
            _review_row("example/two", "same.md", human_decision="pass"),
        ),
    )

    with pytest.raises(ValueError, match=r"file must be unique.*line 3.*same\.md"):
        guideline_review.apply_guideline_checklist(
            checklist_path=checklist_path,
            output_dir=tmp_path / "applied",
        )


def test_only_human_pass_rows_may_be_marked_as_duplicates(tmp_path: Path) -> None:
    checklist_path = tmp_path / "checklist.csv"
    _write_checklist(
        checklist_path,
        (
            _review_row("example/project", "canonical.md", human_decision="pass"),
            _review_row(
                "example/project",
                "rejected.md",
                human_decision="not_found",
                duplicate_of="canonical.md",
            ),
        ),
    )

    with pytest.raises(ValueError, match=r"duplicate row must have human_decision=pass.*line 3"):
        guideline_review.apply_guideline_checklist(
            checklist_path=checklist_path,
            output_dir=tmp_path / "applied",
        )


def test_duplicate_reference_must_target_a_non_duplicate_human_pass(tmp_path: Path) -> None:
    checklist_path = tmp_path / "checklist.csv"
    _write_checklist(
        checklist_path,
        (
            _review_row("example/project", "rejected.md", human_decision="not_found"),
            _review_row(
                "example/project",
                "translation.md",
                human_decision="pass",
                duplicate_of="rejected.md",
            ),
        ),
    )

    with pytest.raises(ValueError, match=r"duplicate_of target must be a non-duplicate pass.*line 3"):
        guideline_review.apply_guideline_checklist(
            checklist_path=checklist_path,
            output_dir=tmp_path / "applied",
        )


def test_only_non_duplicate_human_passes_are_accepted(tmp_path: Path) -> None:
    checklist_path = tmp_path / "checklist.csv"
    _write_checklist(
        checklist_path,
        (
            _review_row("example/accepted", "canonical.md", human_decision="pass"),
            _review_row(
                "example/duplicate",
                "translation.md",
                human_decision="pass",
                duplicate_of="canonical.md",
            ),
            _review_row("example/rejected", "guide.md", human_decision="not_found"),
        ),
    )
    output_dir = tmp_path / "applied"

    guideline_review.apply_guideline_checklist(
        checklist_path=checklist_path,
        output_dir=output_dir,
    )

    with (output_dir / "accepted_guideline_files.csv").open(encoding="utf-8", newline="") as input_file:
        accepted = list(csv.DictReader(input_file))
    assert [row["file"] for row in accepted] == ["canonical.md"]


def test_repository_without_a_non_duplicate_pass_is_rejected(tmp_path: Path) -> None:
    checklist_path = tmp_path / "checklist.csv"
    _write_checklist(
        checklist_path,
        (
            _review_row("example/accepted", "canonical.md", human_decision="pass"),
            _review_row(
                "example/duplicate-only",
                "translation.md",
                human_decision="pass",
                duplicate_of="canonical.md",
            ),
            _review_row("example/not-found", "guide.md", human_decision="not_found"),
        ),
    )
    output_dir = tmp_path / "applied"

    guideline_review.apply_guideline_checklist(
        checklist_path=checklist_path,
        output_dir=output_dir,
    )

    with (output_dir / "repository_review_outcomes.csv").open(encoding="utf-8", newline="") as input_file:
        outcomes = list(csv.DictReader(input_file))
    assert [(row["repository"], row["status"]) for row in outcomes] == [
        ("example/accepted", "accepted"),
        ("example/duplicate-only", "rejected"),
        ("example/not-found", "rejected"),
    ]
    assert outcomes[1]["accepted_file_count"] == "0"
    assert outcomes[1]["duplicate_file_count"] == "1"


def test_applied_review_writes_reproducible_counts(tmp_path: Path) -> None:
    checklist_path = tmp_path / "checklist.csv"
    _write_checklist(
        checklist_path,
        (
            _review_row("example/accepted", "canonical.md", human_decision="pass"),
            _review_row(
                "example/duplicate-only",
                "translation.md",
                human_decision="pass",
                duplicate_of="canonical.md",
            ),
            _review_row("example/not-found", "guide.md", human_decision="not_found"),
        ),
    )
    output_dir = tmp_path / "applied"

    guideline_review.apply_guideline_checklist(
        checklist_path=checklist_path,
        output_dir=output_dir,
    )

    assert json.loads((output_dir / "summary.json").read_text(encoding="utf-8")) == {
        "accepted_files": 1,
        "accepted_repositories": 1,
        "duplicate_files": 1,
        "input_files": 3,
        "not_found_files": 1,
        "rejected_repositories": 2,
        "reviewed_repositories": 3,
    }


def _review_row(
    repository: str,
    file: str,
    *,
    human_decision: str,
    duplicate_of: str = "",
) -> dict[str, str]:
    return {
        "repository": repository,
        "file": file,
        "github_url": f"https://example.test/{file}",
        "human_decision": human_decision,
        "duplicate_of": duplicate_of,
    }


def _write_checklist(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
