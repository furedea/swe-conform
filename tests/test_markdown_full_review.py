import csv
import json
from pathlib import Path

from pytest_mock import MockerFixture

import markdown_full_review


def test_build_full_checklist_preserves_existing_labels_and_marks_added_codex_labels(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate_csv = tmp_path / "candidates.csv"
    _write_csv(
        candidate_csv,
        (
            {
                "name": "example/project",
                "lastCommitSHA": "a" * 40,
                "markdown_path": "AGENTS.md",
                "markdown_url": "https://example.test/AGENTS.md",
                "matched_filename_terms": "agents",
                "matched_content_terms": "rule",
                "agent_evidence": "",
            },
            {
                "name": "example/project",
                "lastCommitSHA": "a" * 40,
                "markdown_path": "docs/STYLE.md",
                "markdown_url": "https://example.test/docs/STYLE.md",
                "matched_filename_terms": "style",
                "matched_content_terms": "style",
                "agent_evidence": "",
            },
        ),
    )
    classified_csv = tmp_path / "classified.csv"
    _write_csv(
        classified_csv,
        (
            {
                "custom_id": "candidate-0001",
                "name": "example/project",
                "markdown_path": "AGENTS.md",
                "markdown_url": "https://example.test/AGENTS.md",
                "status": "not_found",
                "raw_response": "x" * 140_000,
            },
            {
                "custom_id": "candidate-0002",
                "name": "example/project",
                "markdown_path": "docs/STYLE.md",
                "markdown_url": "https://example.test/docs/STYLE.md",
                "status": "pass",
                "raw_response": "",
            },
        ),
    )
    batch_input = tmp_path / "batch_input.jsonl"
    batch_input.write_text(
        "\n".join(
            (
                _batch_request("candidate-0001", "AGENTS.md", "# Rules\n"),
                _batch_request(
                    "candidate-0002",
                    "docs/STYLE.md",
                    "# Style\n\nRendererNode must be registered in index.ts.\n",
                ),
            ),
        )
        + "\n",
        encoding="utf-8",
    )
    existing_checklist = tmp_path / "checklist.csv"
    _write_csv(
        existing_checklist,
        (
            {
                "repository": "example/project",
                "file": "example--project/AGENTS.md",
                "github_url": "https://example.test/AGENTS.md",
                "llm_decision": "pass",
                "human_decision": "pass",
                "codex_decision": "pass",
                "codex_reason": "Existing reason",
                "note": "Existing note",
            },
        ),
    )
    checkpoint = tmp_path / "checkpoint.jsonl"
    checkpoint.write_text(
        json.dumps(
            {
                "custom_id": "candidate-0002",
                "decision": "pass",
                "quote": "RendererNode must be registered in index.ts.",
                "reason": "RendererNodeの登録先を定めている\uff0e",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    output_csv = tmp_path / "checklist_full.csv"
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text("Review every document.", encoding="utf-8")
    client = mocker.Mock()

    report = markdown_full_review.build_full_checklist(
        candidate_csv=candidate_csv,
        classified_files_path=classified_csv,
        batch_input_path=batch_input,
        existing_checklist_path=existing_checklist,
        checkpoint_path=checkpoint,
        output_path=output_csv,
        prompt_path=prompt_path,
        client=client,
        model="gpt-5.6-sol",
        reasoning_effort="max",
        workers=1,
    )

    rows = list(csv.DictReader(output_csv.open(encoding="utf-8", newline="")))
    assert report.rows == 2
    assert rows[0]["review_origin"] == "existing_166"
    assert rows[0]["llm_decision"] == "not_found"
    assert rows[0]["human_decision"] == "pass"
    assert rows[0]["codex_reason"] == "Existing reason"
    assert rows[1]["review_origin"] == "codex_added_561"
    assert rows[1]["llm_decision"] == "pass"
    assert rows[1]["human_decision"] == rows[1]["codex_decision"] == "pass"
    assert rows[1]["codex_reason"].startswith("Evidence (L3):")
    client.complete_json.assert_not_called()


def _write_csv(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _batch_request(custom_id: str, path: str, content: str) -> str:
    input_document = json.dumps(
        {
            "repository": "example/project",
            "revision": "a" * 40,
            "path": path,
            "content": content,
        },
    )
    return json.dumps(
        {
            "custom_id": custom_id,
            "body": {"input": input_document},
        },
    )
