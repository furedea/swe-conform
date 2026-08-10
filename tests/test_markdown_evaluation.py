"""Tests for comparing Markdown classifications with human decisions."""

import csv
import json
from pathlib import Path

import pytest

import markdown_evaluation


def test_evaluation_reports_binary_confusion_matrix(tmp_path: Path) -> None:
    classified_files_path = tmp_path / "classified_files.csv"
    _write_csv(
        classified_files_path,
        (
            _classified_row("one", "pass"),
            _classified_row("two", "pass"),
            _classified_row("three", "not_found"),
            _classified_row("four", "not_found"),
        ),
    )
    checklist_path = tmp_path / "checklist.csv"
    _write_csv(
        checklist_path,
        (
            _checklist_row("one", "pass"),
            _checklist_row("two", "not_found"),
            _checklist_row("three", "pass"),
            _checklist_row("four", "not_found"),
        ),
    )

    report = markdown_evaluation.evaluate_classifications(
        classified_files_path=classified_files_path,
        checklist_path=checklist_path,
        output_dir=tmp_path / "evaluation",
    )

    assert report.true_positives == 1
    assert report.false_positives == 1
    assert report.false_negatives == 1
    assert report.true_negatives == 1
    assert report.resolved_accuracy == 0.5


def test_evaluation_reports_unresolved_model_decisions(tmp_path: Path) -> None:
    classified_files_path = tmp_path / "classified_files.csv"
    _write_csv(
        classified_files_path,
        (
            _classified_row("reviewed", "review"),
            _classified_row("failed", "model_error"),
        ),
    )
    checklist_path = tmp_path / "checklist.csv"
    _write_csv(
        checklist_path,
        (
            _checklist_row("reviewed", "pass"),
            _checklist_row("failed", "not_found"),
            _checklist_row("missing", "pass"),
        ),
    )

    report = markdown_evaluation.evaluate_classifications(
        classified_files_path=classified_files_path,
        checklist_path=checklist_path,
        output_dir=tmp_path / "evaluation",
    )

    assert report.review_decisions == 1
    assert report.model_errors == 1
    assert report.missing_predictions == 1


def test_evaluation_writes_false_positive_details(tmp_path: Path) -> None:
    classified_files_path = tmp_path / "classified_files.csv"
    _write_csv(classified_files_path, (_classified_row("false-positive", "pass"),))
    checklist_path = tmp_path / "checklist.csv"
    _write_csv(checklist_path, (_checklist_row("false-positive", "not_found"),))
    output_dir = tmp_path / "evaluation"

    markdown_evaluation.evaluate_classifications(
        classified_files_path=classified_files_path,
        checklist_path=checklist_path,
        output_dir=output_dir,
    )

    with (output_dir / "false_positives.csv").open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows == [
        {
            "repository": "example/project",
            "markdown_path": "docs/false-positive.md",
            "github_url": "https://example.test/false-positive.md",
            "human_decision": "not_found",
            "llm_decision": "pass",
            "outcome": "false_positive",
            "confidence": "9",
            "model_reason": "Reason for false-positive",
            "quote": "Quote for false-positive",
            "human_note": "",
        },
    ]


def test_evaluation_writes_summary_metrics(tmp_path: Path) -> None:
    classified_files_path = tmp_path / "classified_files.csv"
    _write_csv(
        classified_files_path,
        (
            _classified_row("one", "pass"),
            _classified_row("two", "pass"),
            _classified_row("three", "not_found"),
            _classified_row("four", "not_found"),
        ),
    )
    checklist_path = tmp_path / "checklist.csv"
    _write_csv(
        checklist_path,
        (
            _checklist_row("one", "pass"),
            _checklist_row("two", "not_found"),
            _checklist_row("three", "pass"),
            _checklist_row("four", "not_found"),
        ),
    )
    output_dir = tmp_path / "evaluation"

    markdown_evaluation.evaluate_classifications(
        classified_files_path=classified_files_path,
        checklist_path=checklist_path,
        output_dir=output_dir,
    )

    summary = json.loads((output_dir / "evaluation_summary.json").read_text(encoding="utf-8"))
    assert summary["scope"]["human_labeled_files"] == 4
    assert summary["confusion_matrix"] == {
        "false_negatives": 1,
        "false_positives": 1,
        "true_negatives": 1,
        "true_positives": 1,
    }
    assert summary["metrics"] == {
        "f1": 0.5,
        "false_negative_rate": 0.5,
        "false_positive_rate": 0.5,
        "precision": 0.5,
        "recall": 0.5,
        "resolution_rate": 1.0,
        "resolved_accuracy": 0.5,
        "specificity": 0.5,
        "strict_accuracy": 0.5,
    }
    markdown_summary = (output_dir / "evaluation_summary.md").read_text(encoding="utf-8")
    assert "Metrics cover only the manually reviewed checklist subset." in markdown_summary
    assert "## Confusion matrix" in markdown_summary
    assert "Review, model_error, and missing predictions count as incorrect." in markdown_summary


def test_evaluation_writes_repository_breakdown(tmp_path: Path) -> None:
    classified_files_path = tmp_path / "classified_files.csv"
    _write_csv(
        classified_files_path,
        (
            _classified_row("alpha-false-positive", "pass", repository="alpha/project"),
            _classified_row("alpha-true-positive", "pass", repository="alpha/project"),
            _classified_row("beta-true-negative", "not_found", repository="beta/project"),
        ),
    )
    checklist_path = tmp_path / "checklist.csv"
    _write_csv(
        checklist_path,
        (
            _checklist_row("alpha-false-positive", "not_found", repository="alpha/project"),
            _checklist_row("alpha-true-positive", "pass", repository="alpha/project"),
            _checklist_row("beta-true-negative", "not_found", repository="beta/project"),
        ),
    )
    output_dir = tmp_path / "evaluation"

    markdown_evaluation.evaluate_classifications(
        classified_files_path=classified_files_path,
        checklist_path=checklist_path,
        output_dir=output_dir,
    )

    with (output_dir / "repository_metrics.csv").open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert [(row["repository"], row["false_positives"], row["resolved_accuracy"]) for row in rows] == [
        ("alpha/project", "1", "0.5"),
        ("beta/project", "0", "1.0"),
    ]


def test_evaluation_rejects_unknown_human_decision(tmp_path: Path) -> None:
    classified_files_path = tmp_path / "classified_files.csv"
    _write_csv(classified_files_path, (_classified_row("one", "pass"),))
    checklist_path = tmp_path / "checklist.csv"
    _write_csv(checklist_path, (_checklist_row("one", "maybe"),))

    with pytest.raises(ValueError, match="human_decision must be pass, not_found, or empty"):
        markdown_evaluation.evaluate_classifications(
            classified_files_path=classified_files_path,
            checklist_path=checklist_path,
            output_dir=tmp_path / "evaluation",
        )


def test_evaluation_rejects_duplicate_prediction_url(tmp_path: Path) -> None:
    duplicate = _classified_row("one", "pass")
    classified_files_path = tmp_path / "classified_files.csv"
    _write_csv(classified_files_path, (duplicate, duplicate))
    checklist_path = tmp_path / "checklist.csv"
    _write_csv(checklist_path, (_checklist_row("one", "pass"),))

    with pytest.raises(ValueError, match="duplicate URL"):
        markdown_evaluation.evaluate_classifications(
            classified_files_path=classified_files_path,
            checklist_path=checklist_path,
            output_dir=tmp_path / "evaluation",
        )


def test_evaluation_reads_large_provider_result(tmp_path: Path) -> None:
    classified_row = _classified_row("large", "pass")
    classified_row["provider_result"] = "x" * 2_048
    classified_files_path = tmp_path / "classified_files.csv"
    _write_csv(classified_files_path, (classified_row,))
    checklist_path = tmp_path / "checklist.csv"
    _write_csv(checklist_path, (_checklist_row("large", "pass"),))
    previous_limit = csv.field_size_limit()
    csv.field_size_limit(1_024)

    try:
        report = markdown_evaluation.evaluate_classifications(
            classified_files_path=classified_files_path,
            checklist_path=checklist_path,
            output_dir=tmp_path / "evaluation",
        )
    finally:
        csv.field_size_limit(previous_limit)

    assert report.true_positives == 1


def _classified_row(name: str, status: str, *, repository: str = "example/project") -> dict[str, str]:
    return {
        "name": repository,
        "markdown_path": f"docs/{name}.md",
        "markdown_url": f"https://example.test/{name}.md",
        "status": status,
        "model_reason": f"Reason for {name}",
        "quote": f"Quote for {name}",
        "confidence": "9",
    }


def _checklist_row(
    name: str,
    human_decision: str,
    *,
    repository: str = "example/project",
) -> dict[str, str]:
    return {
        "repository": repository,
        "file": f"example--project/docs__{name}.md",
        "github_url": f"https://example.test/{name}.md",
        "human_decision": human_decision,
    }


def _write_csv(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
