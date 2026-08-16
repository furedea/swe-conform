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
        reader = csv.DictReader(input_file)
        rows = list(reader)
    assert reader.fieldnames is not None
    assert reader.fieldnames[5:8] == ["human_decision", "duplicate_of", "codex_decision"]
    assert rows == [
        {
            "repository": "example/project",
            "file": "example--project/docs__review_rules.md",
            "github_url": "https://example.test/docs/review_rules.md",
            "review_origin": "mechanical_filter",
            "llm_decision": "",
            "human_decision": "",
            "duplicate_of": "",
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
        reader = csv.DictReader(input_file)
        rows = list(reader)
    assert reader.fieldnames is not None
    assert reader.fieldnames[4:7] == ["human_decision", "duplicate_of", "note"]
    assert rows == [
        {
            "repository": "example/project",
            "file": "example--project/docs__review_rules.md",
            "github_url": "https://example.test/candidate-0001.md",
            "llm_decision": "review",
            "human_decision": "",
            "duplicate_of": "",
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
    assert all(row["review_origin"] == "added_round_1" for row in checklist)


def test_cached_review_export_preserves_existing_annotations(
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
    existing[0]["duplicate_of"] = "example--project/docs__canonical.md"
    existing[0]["codex_decision"] = "pass"
    existing[0]["codex_reason"] = "Evidence remains valid."
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
    assert preserved[0]["duplicate_of"] == "example--project/docs__canonical.md"
    assert preserved[0]["codex_decision"] == "pass"
    assert preserved[0]["codex_reason"] == "Evidence remains valid."
    assert preserved[0]["note"] == "checked"


def test_cached_review_export_writes_a_separate_next_round_checklist(
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
    output_dir.mkdir()
    completed_path = output_dir / "checklist_done.csv"
    completed_row = {
        "repository": "example/project",
        "file": "example--project/docs__rules.md",
        "github_url": "https://example.test/candidate-0001.md",
        "review_origin": "added_round_1",
        "llm_decision": "pass",
        "human_decision": "pass",
        "duplicate_of": "",
        "codex_decision": "",
        "codex_reason": "",
        "note": "reviewed",
    }
    with completed_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(completed_row))
        writer.writeheader()
        writer.writerow(completed_row)
    completed_contents = completed_path.read_bytes()
    next_path = output_dir / "checklist_round_2.csv"

    markdown_review.export_cached_review_files(
        classified_rows=(row,),
        repository_client=repository_client,
        output_dir=output_dir,
        existing_checklist_path=completed_path,
        checklist_path=next_path,
    )

    assert completed_path.read_bytes() == completed_contents
    with next_path.open(encoding="utf-8", newline="") as input_file:
        next_rows = list(csv.DictReader(input_file))
    assert next_rows[0]["human_decision"] == "pass"
    assert next_rows[0]["note"] == "reviewed"


def test_cached_review_export_assigns_the_second_round_only_to_new_files(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    existing_row = {
        **_classified_row("candidate-existing", "pass", "", markdown_path="docs/existing.md"),
        "lastCommitSHA": "a" * 40,
        "blob_sha": "b" * 40,
    }
    new_row = {
        **_classified_row("candidate-new", "pass", "", markdown_path="docs/new.md"),
        "lastCommitSHA": "a" * 40,
        "blob_sha": "c" * 40,
    }
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {
        "b" * 40: "EXISTING content",
        "c" * 40: "NEW content",
    }
    output_dir = tmp_path / "manual-review"
    output_dir.mkdir()
    completed_path = output_dir / "checklist_done.csv"
    completed_row = {
        "repository": "example/project",
        "file": "example--project/docs__existing.md",
        "github_url": "https://example.test/candidate-existing.md",
        "review_origin": "added_round_1",
        "llm_decision": "pass",
        "human_decision": "pass",
        "duplicate_of": "",
        "codex_decision": "",
        "codex_reason": "",
        "note": "",
    }
    with completed_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(completed_row))
        writer.writeheader()
        writer.writerow(completed_row)

    markdown_review.export_cached_review_files(
        classified_rows=(existing_row, new_row),
        repository_client=repository_client,
        output_dir=output_dir,
        existing_checklist_path=completed_path,
        checklist_path=output_dir / "checklist_round_2.csv",
    )

    with (output_dir / "checklist_round_2.csv").open(encoding="utf-8", newline="") as input_file:
        origins = [row["review_origin"] for row in csv.DictReader(input_file)]
    assert origins == ["added_round_1", "added_round_2"]


def test_cached_review_export_assigns_the_round_after_the_latest_existing_origin(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    new_row = {
        **_classified_row("candidate-new", "pass", "", markdown_path="docs/new.md"),
        "lastCommitSHA": "a" * 40,
        "blob_sha": "c" * 40,
    }
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {"c" * 40: "NEW content"}
    output_dir = tmp_path / "manual-review"
    output_dir.mkdir()
    completed_path = output_dir / "checklist_round_2_done.csv"
    completed_rows = [
        {
            "repository": "example/project",
            "file": f"example--project/docs__round_{round_number}.md",
            "github_url": f"https://example.test/candidate-round-{round_number}.md",
            "review_origin": review_origin,
            "llm_decision": "pass",
            "human_decision": "pass",
            "duplicate_of": "",
            "codex_decision": "",
            "codex_reason": "",
            "note": "",
        }
        for round_number, review_origin in ((1, "added_round_1"), (2, "added_round_2"))
    ]
    with completed_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(completed_rows[0]))
        writer.writeheader()
        writer.writerows(completed_rows)

    markdown_review.export_cached_review_files(
        classified_rows=(new_row,),
        repository_client=repository_client,
        output_dir=output_dir,
        existing_checklist_path=completed_path,
        checklist_path=output_dir / "checklist_round_3.csv",
    )

    with (output_dir / "checklist_round_3.csv").open(encoding="utf-8", newline="") as input_file:
        origins = [row["review_origin"] for row in csv.DictReader(input_file)]
    assert origins == ["added_round_1", "added_round_2", "added_round_3"]


def test_cached_review_export_upgrades_a_checklist_without_duplicate_reference(
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
    output_dir.mkdir()
    checklist_path = output_dir / "checklist.csv"
    legacy_row = {
        "repository": "example/project",
        "file": "example--project/docs__rules.md",
        "github_url": "https://example.test/candidate-0001.md",
        "review_origin": "added_round_1",
        "llm_decision": "pass",
        "human_decision": "pass",
        "codex_decision": "",
        "codex_reason": "",
        "note": "",
    }
    with checklist_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(legacy_row))
        writer.writeheader()
        writer.writerow(legacy_row)

    markdown_review.export_cached_review_files(
        classified_rows=(row,),
        repository_client=repository_client,
        output_dir=output_dir,
    )

    with checklist_path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        upgraded = list(reader)
    assert reader.fieldnames is not None
    assert reader.fieldnames[5:8] == ["human_decision", "duplicate_of", "codex_decision"]
    assert upgraded[0]["human_decision"] == "pass"
    assert upgraded[0]["duplicate_of"] == ""


def test_cached_review_export_appends_new_files_after_existing_rows(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    rows = tuple(
        {
            **_classified_row(custom_id, "pass", "", markdown_path=path),
            "lastCommitSHA": "a" * 40,
            "blob_sha": blob_sha,
        }
        for custom_id, path, blob_sha in (
            ("candidate-first", "docs/z-first.md", "1" * 40),
            ("candidate-middle", "docs/a-middle.md", "2" * 40),
            ("candidate-last", "docs/m-last.md", "3" * 40),
        )
    )
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.side_effect = (
        {"1" * 40: "FIRST", "3" * 40: "LAST"},
        {"1" * 40: "FIRST", "2" * 40: "MIDDLE", "3" * 40: "LAST"},
    )
    output_dir = tmp_path / "manual-review"
    markdown_review.export_cached_review_files(
        classified_rows=(rows[0], rows[2]),
        repository_client=repository_client,
        output_dir=output_dir,
    )

    markdown_review.export_cached_review_files(
        classified_rows=rows,
        repository_client=repository_client,
        output_dir=output_dir,
    )

    with (output_dir / "checklist.csv").open(encoding="utf-8", newline="") as input_file:
        urls = [row["github_url"] for row in csv.DictReader(input_file)]
    assert urls == [
        "https://example.test/candidate-first.md",
        "https://example.test/candidate-last.md",
        "https://example.test/candidate-middle.md",
    ]


def test_cached_review_export_removes_unreviewed_files_displaced_from_selection(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    def row(repository: str, custom_id: str, blob_sha: str) -> dict[str, str]:
        return {
            **_classified_row(custom_id, "pass", "", repository=repository),
            "lastCommitSHA": "a" * 40,
            "blob_sha": blob_sha,
        }

    first_selection = (
        row("example/two", "candidate-two", "2" * 40),
        row("example/three", "candidate-three", "3" * 40),
    )
    final_selection = (
        row("example/one", "candidate-one", "1" * 40),
        first_selection[0],
    )
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.side_effect = lambda _repository, blob_shas: {
        blob_sha: blob_sha for blob_sha in blob_shas
    }
    output_dir = tmp_path / "manual-review"
    markdown_review.export_cached_review_files(
        classified_rows=first_selection,
        repository_client=repository_client,
        output_dir=output_dir,
    )

    markdown_review.export_cached_review_files(
        classified_rows=final_selection,
        repository_client=repository_client,
        output_dir=output_dir,
    )

    with (output_dir / "checklist.csv").open(encoding="utf-8", newline="") as input_file:
        repositories = [row["repository"] for row in csv.DictReader(input_file)]
    assert set(repositories) == {"example/one", "example/two"}


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("human_decision", "pass"),
        ("duplicate_of", "example--project/docs__canonical.md"),
        ("codex_decision", "pass"),
        ("codex_reason", "Evidence remains valid."),
        ("note", "review started"),
    ),
)
def test_cached_review_export_keeps_every_file_for_an_annotated_displaced_repository(
    mocker: MockerFixture,
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    rows = tuple(
        {
            **_classified_row(custom_id, "pass", "", repository="example/project"),
            "lastCommitSHA": "a" * 40,
            "blob_sha": blob_sha,
        }
        for custom_id, blob_sha in (
            ("candidate-one", "1" * 40),
            ("candidate-two", "2" * 40),
        )
    )
    repository_client = mocker.Mock()
    repository_client.get_text_blobs.return_value = {
        "1" * 40: "ONE",
        "2" * 40: "TWO",
    }
    output_dir = tmp_path / "manual-review"
    markdown_review.export_cached_review_files(
        classified_rows=rows,
        repository_client=repository_client,
        output_dir=output_dir,
    )
    checklist_path = output_dir / "checklist.csv"
    with checklist_path.open(encoding="utf-8", newline="") as input_file:
        existing = list(csv.DictReader(input_file))
    existing[0][field] = value
    with checklist_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(existing[0]))
        writer.writeheader()
        writer.writerows(existing)

    markdown_review.export_cached_review_files(
        classified_rows=(),
        repository_client=repository_client,
        output_dir=output_dir,
    )

    with checklist_path.open(encoding="utf-8", newline="") as input_file:
        preserved = list(csv.DictReader(input_file))
    assert len(preserved) == 2


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
