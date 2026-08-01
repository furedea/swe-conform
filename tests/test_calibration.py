"""Tests for prompt-calibration metrics."""

import csv
from pathlib import Path

import pytest

import calibration


def test_evaluate_reports_split_metrics_and_confusion_matrix() -> None:
    gold_rows = [
        _gold("a/project", "a1", "tuning", "pass"),
        _gold("b/project", "b1", "tuning", "review"),
        _gold("c/project", "c1", "tuning", "not_found"),
        _gold("d/project", "d1", "holdout", "pass"),
    ]
    prediction_rows = [
        _prediction("a/project", "a1", "pass"),
        _prediction("b/project", "b1", "not_found"),
        _prediction("c/project", "c1", "not_found"),
        _prediction("d/project", "d1", "review"),
    ]

    report = calibration.evaluate_rows(gold_rows, prediction_rows, split="tuning")

    assert report["samples"] == 3
    assert report["accuracy"] == pytest.approx(2 / 3)
    assert report["macro_f1"] == pytest.approx(5 / 9)
    assert report["per_class"]["pass"] == {
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
        "support": 1,
    }
    assert report["confusion_matrix"]["review"]["not_found"] == 1
    assert report["errors"] == [
        {
            "repository": "b/project",
            "revision": "b1",
            "expected": "review",
            "predicted": "not_found",
            "reason": "",
            "evidence_path": "",
            "evidence_quote": "",
        },
    ]


def test_evaluate_rejects_a_missing_prediction() -> None:
    gold_rows = [_gold("a/project", "a1", "tuning", "pass")]

    with pytest.raises(ValueError, match="Missing prediction"):
        calibration.evaluate_rows(gold_rows, [], split="tuning")


def test_write_split_input_preserves_only_requested_rows(tmp_path: Path) -> None:
    input_path = tmp_path / "candidates.csv"
    input_path.write_text(
        "calibration_split,name,lastCommitSHA\ntuning,a/project,a1\nholdout,b/project,b1\n",
        encoding="utf-8",
    )

    output_path = calibration.write_split_input(
        input_path,
        tmp_path / "input-tuning",
        split="tuning",
    )

    with output_path.open(encoding="utf-8", newline="") as output_file:
        rows = list(csv.DictReader(output_file))
    assert rows == [
        {
            "calibration_split": "tuning",
            "name": "a/project",
            "lastCommitSHA": "a1",
        },
    ]


def _gold(repository: str, revision: str, split: str, status: str) -> dict[str, str]:
    return {
        "repository": repository,
        "revision": revision,
        "split": split,
        "status": status,
    }


def _prediction(repository: str, revision: str, status: str) -> dict[str, str]:
    return {
        "name": repository,
        "lastCommitSHA": revision,
        "guideline_status": status,
        "guideline_reason": "",
        "guideline_path": "",
        "guideline_evidence": "",
    }
