"""Tests for materializing Markdown files selected by the model."""

import csv
import json
from pathlib import Path

import markdown_review


def test_manual_review_export_contains_pass_and_review_files(tmp_path: Path) -> None:
    classified_files_path = tmp_path / "classified_files.csv"
    _write_classified_files(
        classified_files_path,
        (
            _classified_row("candidate-0001", "pass", "PASS content"),
            _classified_row("candidate-0002", "review", "REVIEW content"),
            _classified_row("candidate-0003", "not_found", "NOT FOUND content"),
        ),
    )
    batch_input_path = tmp_path / "batch_input.jsonl"
    _write_requests(
        batch_input_path,
        (
            _request("candidate-0001", "PASS content"),
            _request("candidate-0002", "REVIEW content"),
            _request("candidate-0003", "NOT FOUND content"),
        ),
    )
    output_dir = tmp_path / "manual-review"

    report = markdown_review.export_pass_files(
        classified_files_path=classified_files_path,
        batch_input_path=batch_input_path,
        output_dir=output_dir,
    )

    exported_contents = sorted(path.read_text(encoding="utf-8") for path in output_dir.rglob("*.md"))
    assert report.files == 2
    assert exported_contents == ["PASS content", "REVIEW content"]


def test_manual_review_export_groups_files_by_repository(tmp_path: Path) -> None:
    classified_files_path = tmp_path / "classified_files.csv"
    _write_classified_files(
        classified_files_path,
        (_classified_row("candidate-0001", "pass", "PASS content", markdown_path="candidate-0001.md"),),
    )
    batch_input_path = tmp_path / "batch_input.jsonl"
    _write_requests(
        batch_input_path,
        (_request("candidate-0001", "PASS content", markdown_path="candidate-0001.md"),),
    )
    output_dir = tmp_path / "manual-review"

    markdown_review.export_pass_files(
        classified_files_path=classified_files_path,
        batch_input_path=batch_input_path,
        output_dir=output_dir,
    )

    assert (output_dir / "example--project" / "candidate-0001.md").read_text(encoding="utf-8") == "PASS content"


def test_manual_review_export_replaces_source_directories_with_double_underscores(tmp_path: Path) -> None:
    markdown_path = "docs/user_guide/coding_style.md"
    classified_files_path = tmp_path / "classified_files.csv"
    _write_classified_files(
        classified_files_path,
        (_classified_row("candidate-0001", "pass", "PASS content", markdown_path=markdown_path),),
    )
    batch_input_path = tmp_path / "batch_input.jsonl"
    _write_requests(batch_input_path, (_request("candidate-0001", "PASS content", markdown_path=markdown_path),))
    output_dir = tmp_path / "manual-review"

    markdown_review.export_pass_files(
        classified_files_path=classified_files_path,
        batch_input_path=batch_input_path,
        output_dir=output_dir,
    )

    exported_path = output_dir / "example--project" / "docs__user_guide__coding_style.md"
    assert exported_path.read_text(encoding="utf-8") == "PASS content"


def test_manual_review_export_numbers_colliding_flattened_filenames(tmp_path: Path) -> None:
    first_path = "docs/test/README.md"
    second_path = "docs__test/README.md"
    classified_files_path = tmp_path / "classified_files.csv"
    _write_classified_files(
        classified_files_path,
        (
            _classified_row("candidate-0001", "pass", "FIRST content", markdown_path=first_path),
            _classified_row("candidate-0002", "pass", "SECOND content", markdown_path=second_path),
        ),
    )
    batch_input_path = tmp_path / "batch_input.jsonl"
    _write_requests(
        batch_input_path,
        (
            _request("candidate-0001", "FIRST content", markdown_path=first_path),
            _request("candidate-0002", "SECOND content", markdown_path=second_path),
        ),
    )
    output_dir = tmp_path / "manual-review"

    markdown_review.export_pass_files(
        classified_files_path=classified_files_path,
        batch_input_path=batch_input_path,
        output_dir=output_dir,
    )

    repository_dir = output_dir / "example--project"
    assert (repository_dir / "docs__test__README.md").read_text(encoding="utf-8") == "FIRST content"
    assert (repository_dir / "docs__test__README__2.md").read_text(encoding="utf-8") == "SECOND content"


def test_manual_review_export_writes_the_human_decision_checklist(tmp_path: Path) -> None:
    markdown_path = "docs/review_rules.md"
    classified_files_path = tmp_path / "classified_files.csv"
    _write_classified_files(
        classified_files_path,
        (_classified_row("candidate-0001", "review", "REVIEW content", markdown_path=markdown_path),),
    )
    batch_input_path = tmp_path / "batch_input.jsonl"
    _write_requests(batch_input_path, (_request("candidate-0001", "REVIEW content", markdown_path=markdown_path),))
    output_dir = tmp_path / "manual-review"

    markdown_review.export_pass_files(
        classified_files_path=classified_files_path,
        batch_input_path=batch_input_path,
        output_dir=output_dir,
    )

    with (output_dir / "checklist.csv").open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows == [
        {
            "repository": "example/project",
            "file": "example--project/docs__review_rules.md",
            "github_url": "https://example.test/candidate-0001.md",
            "llm_decision": "review",
            "human_decision": "",
            "note": "",
        },
    ]


def test_manual_review_checklist_groups_rows_by_repository(tmp_path: Path) -> None:
    classified_files_path = tmp_path / "classified_files.csv"
    _write_classified_files(
        classified_files_path,
        (
            _classified_row("candidate-0001", "pass", "Z content", repository="zeta/project"),
            _classified_row("candidate-0002", "pass", "A content", repository="alpha/project"),
        ),
    )
    batch_input_path = tmp_path / "batch_input.jsonl"
    _write_requests(
        batch_input_path,
        (
            _request("candidate-0001", "Z content"),
            _request("candidate-0002", "A content"),
        ),
    )
    output_dir = tmp_path / "manual-review"

    markdown_review.export_pass_files(
        classified_files_path=classified_files_path,
        batch_input_path=batch_input_path,
        output_dir=output_dir,
    )

    with (output_dir / "checklist.csv").open(encoding="utf-8", newline="") as input_file:
        repositories = [row["repository"] for row in csv.DictReader(input_file)]
    assert repositories == ["alpha/project", "zeta/project"]


def _classified_row(
    custom_id: str,
    status: str,
    content: str,
    *,
    markdown_path: str | None = None,
    repository: str = "example/project",
) -> dict[str, str]:
    resolved_path = markdown_path or f"docs/{custom_id}.md"
    return {
        "custom_id": custom_id,
        "name": repository,
        "markdown_path": resolved_path,
        "markdown_url": f"https://example.test/{custom_id}.md",
        "model_reason": content,
        "quote": content,
        "confidence": "10",
        "status": status,
    }


def _write_classified_files(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_requests(path: Path, requests: tuple[dict[str, object], ...]) -> None:
    path.write_text(
        "".join(f"{json.dumps(request)}\n" for request in requests),
        encoding="utf-8",
    )


def _request(custom_id: str, content: str, *, markdown_path: str | None = None) -> dict[str, object]:
    return {
        "custom_id": custom_id,
        "body": {
            "input": json.dumps(
                {
                    "repository": "example/project",
                    "path": markdown_path or f"docs/{custom_id}.md",
                    "content": content,
                },
            ),
        },
    }
