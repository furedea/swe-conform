"""Tests for materializing Markdown files selected by the model."""

import csv
import json
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

import markdown_review


def test_candidate_review_export_writes_a_blinded_checklist_for_each_materialized_file(tmp_path: Path) -> None:
    candidate_csv = tmp_path / "candidates.csv"
    _write_candidate_files(candidate_csv, (_candidate_row("docs/review_rules.md"),))
    batch_input_path = tmp_path / "batch_input.jsonl"
    _write_requests(
        batch_input_path,
        (_request("candidate-0001", "REVIEW content", markdown_path="docs/review_rules.md"),),
    )
    output_dir = tmp_path / "manual-review"

    report = markdown_review.export_candidate_files(
        candidate_csv=candidate_csv,
        batch_input_path=batch_input_path,
        output_dir=output_dir,
    )

    review_path = output_dir / "example--project" / "docs__review_rules.md"
    assert report.files == 1
    assert review_path.read_text(encoding="utf-8") == "REVIEW content"
    with (output_dir / "checklist.csv").open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows == [
        {
            "repository": "example/project",
            "file": "example--project/docs__review_rules.md",
            "github_url": "https://example.test/docs/review_rules.md",
            "review_origin": "mechanical_filter",
            "llm_decision": "",
            "human_decision": "",
            "codex_decision": "",
            "codex_reason": "",
            "note": "",
        },
    ]


def test_candidate_review_export_rejects_a_missing_prepared_input(tmp_path: Path) -> None:
    candidate_csv = tmp_path / "candidates.csv"
    _write_candidate_files(candidate_csv, (_candidate_row("docs/review_rules.md"),))
    batch_input_path = tmp_path / "batch_input.jsonl"
    _write_requests(batch_input_path, (_request("candidate-0001", "OTHER content"),))
    output_dir = tmp_path / "manual-review"

    with pytest.raises(ValueError, match="Candidate and prepared-input identities must be equal"):
        markdown_review.export_candidate_files(
            candidate_csv=candidate_csv,
            batch_input_path=batch_input_path,
            output_dir=output_dir,
        )

    assert not output_dir.exists()


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


def test_manual_review_export_numbers_case_insensitive_filename_collisions(tmp_path: Path) -> None:
    first_path = "docs/README.md"
    second_path = "docs/readme.md"
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
    assert (repository_dir / "docs__README.md").read_text(encoding="utf-8") == "FIRST content"
    assert (repository_dir / "docs__readme__2.md").read_text(encoding="utf-8") == "SECOND content"


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


def test_cached_review_export_writes_pass_and_review_files_from_bare_git(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    rows = (
        {
            **_classified_row("candidate-0001", "pass", "", markdown_path="docs/rules.md"),
            "lastCommitSHA": "a" * 40,
            "blob_sha": "b" * 40,
        },
        {
            **_classified_row("candidate-0002", "review", "", markdown_path="test/README.md"),
            "lastCommitSHA": "a" * 40,
            "blob_sha": "c" * 40,
        },
        {
            **_classified_row("candidate-0003", "not_found", "", markdown_path="README.md"),
            "lastCommitSHA": "a" * 40,
            "blob_sha": "d" * 40,
        },
    )
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {
        "b" * 40: "PASS content",
        "c" * 40: "REVIEW content",
    }
    output_dir = tmp_path / "manual-review"

    report = markdown_review.export_cached_review_files(
        classified_rows=rows,
        repository_client=repository_client,
        output_dir=output_dir,
    )

    repository_client.get_text_blobs.assert_called_once_with("example/project", ("b" * 40, "c" * 40))
    assert report.files == 2
    assert (output_dir / "example--project" / "docs__rules.md").read_text(encoding="utf-8") == "PASS content"
    with (output_dir / "checklist.csv").open(encoding="utf-8", newline="") as input_file:
        checklist = list(csv.DictReader(input_file))
    assert [row["llm_decision"] for row in checklist] == ["pass", "review"]
    assert all(row["review_origin"] == "added" for row in checklist)


def test_cached_review_export_preserves_existing_human_decisions(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    row = {
        **_classified_row("candidate-0001", "pass", "", markdown_path="docs/rules.md"),
        "lastCommitSHA": "a" * 40,
        "blob_sha": "b" * 40,
    }
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {"b" * 40: "PASS content"}
    output_dir = tmp_path / "manual-review"
    markdown_review.export_cached_review_files(
        classified_rows=(row,),
        repository_client=repository_client,
        output_dir=output_dir,
    )
    checklist_path = output_dir / "checklist.csv"
    with checklist_path.open(encoding="utf-8", newline="") as input_file:
        existing = list(csv.DictReader(input_file))
    existing[0]["human_decision"] = "pass"
    existing[0]["note"] = "checked"
    with checklist_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(existing[0]))
        writer.writeheader()
        writer.writerows(existing)

    markdown_review.export_cached_review_files(
        classified_rows=(row,),
        repository_client=repository_client,
        output_dir=output_dir,
    )

    with checklist_path.open(encoding="utf-8", newline="") as input_file:
        preserved = list(csv.DictReader(input_file))
    assert preserved[0]["human_decision"] == "pass"
    assert preserved[0]["note"] == "checked"


def test_cached_review_export_does_not_reuse_a_numbered_filename_on_resume(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    paths = ("docs/test/README.md", "docs__test/README.md", "docs/test__README.md")
    rows = tuple(
        {
            **_classified_row(f"candidate-{index}", "pass", "", markdown_path=path),
            "lastCommitSHA": "a" * 40,
            "blob_sha": str(index) * 40,
        }
        for index, path in enumerate(paths, start=1)
    )
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.side_effect = (
        {"1" * 40: "FIRST", "2" * 40: "SECOND"},
        {"1" * 40: "FIRST", "2" * 40: "SECOND", "3" * 40: "THIRD"},
    )
    output_dir = tmp_path / "manual-review"
    markdown_review.export_cached_review_files(
        classified_rows=rows[:2],
        repository_client=repository_client,
        output_dir=output_dir,
    )

    markdown_review.export_cached_review_files(
        classified_rows=rows,
        repository_client=repository_client,
        output_dir=output_dir,
    )

    repository_dir = output_dir / "example--project"
    assert (repository_dir / "docs__test__README__2.md").read_text(encoding="utf-8") == "SECOND"
    assert (repository_dir / "docs__test__README__3.md").read_text(encoding="utf-8") == "THIRD"


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


def _candidate_row(
    markdown_path: str,
    *,
    repository: str = "example/project",
    revision: str = "a" * 40,
) -> dict[str, str]:
    return {
        "name": repository,
        "lastCommitSHA": revision,
        "markdown_path": markdown_path,
        "markdown_url": f"https://example.test/{markdown_path}",
        "matched_filename_terms": "rules",
        "matched_content_terms": "rule",
        "agent_evidence": "False",
    }


def _write_candidate_files(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_requests(path: Path, requests: tuple[dict[str, object], ...]) -> None:
    path.write_text(
        "".join(f"{json.dumps(request)}\n" for request in requests),
        encoding="utf-8",
    )


def _request(
    custom_id: str,
    content: str,
    *,
    markdown_path: str | None = None,
    repository: str = "example/project",
    revision: str = "a" * 40,
) -> dict[str, object]:
    return {
        "custom_id": custom_id,
        "body": {
            "input": json.dumps(
                {
                    "repository": repository,
                    "revision": revision,
                    "path": markdown_path or f"docs/{custom_id}.md",
                    "content": content,
                },
            ),
        },
    }
