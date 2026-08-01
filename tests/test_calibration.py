"""Tests for prompt-calibration metrics."""

import csv
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

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


def test_select_hard_candidates_excludes_previous_repositories() -> None:
    rows = [
        _classified("excluded/project", "Python", "pass", strong=20),
        _classified("pass-owner/pass-project", "Python", "pass", strong=10),
        _classified("lower/pass", "Python", "pass", strong=5),
        _classified("review-owner/review-project", "Python", "review", strong=8),
        _classified("lower/review", "Python", "review", strong=4),
    ]

    selected = calibration.select_hard_candidates(
        rows,
        excluded_repositories={"excluded/project"},
        languages=("Python",),
        pass_per_language=1,
        dense_review_per_language=1,
        weak_review_per_language=0,
    )

    assert [(row["name"], row["sampling_stratum"]) for row in selected] == [
        ("lower/pass", "subtle_confirmed"),
        ("review-owner/review-project", "dense_unconfirmed"),
    ]
    assert all(int(row["difficulty_score"]) > 0 for row in selected)


def test_select_hard_candidates_covers_subtle_dense_and_weak_boundaries() -> None:
    rows = [
        _classified("pass-low/subtle", "Python", "pass", strong=1),
        _classified("pass-high/obvious", "Python", "pass", strong=10),
        _classified("dense-one/dense-project-one", "Python", "review", strong=10),
        _classified("dense-two/dense-project-two", "Python", "review", strong=8),
        _classified("weak-one/weak-project-one", "Python", "review", strong=1),
        _classified("weak-two/weak-project-two", "Python", "review", strong=2),
    ]

    selected = calibration.select_hard_candidates(
        rows,
        excluded_repositories=set(),
        languages=("Python",),
        pass_per_language=1,
        dense_review_per_language=2,
        weak_review_per_language=2,
    )

    assert [(row["name"], row["sampling_stratum"]) for row in selected] == [
        ("pass-low/subtle", "subtle_confirmed"),
        ("dense-one/dense-project-one", "dense_unconfirmed"),
        ("dense-two/dense-project-two", "dense_unconfirmed"),
        ("weak-one/weak-project-one", "weak_unconfirmed"),
        ("weak-two/weak-project-two", "weak_unconfirmed"),
    ]


def test_select_hard_candidates_uses_each_repository_owner_once() -> None:
    rows = [
        _classified("same-owner/pass-project", "Python", "pass", strong=10),
        _classified("same-owner/review-project", "Python", "review", strong=20),
        _classified("other-owner/review-project", "Python", "review", strong=5),
    ]

    selected = calibration.select_hard_candidates(
        rows,
        excluded_repositories=set(),
        languages=("Python",),
        pass_per_language=1,
        dense_review_per_language=1,
        weak_review_per_language=0,
    )

    assert [row["name"] for row in selected] == [
        "same-owner/pass-project",
        "other-owner/review-project",
    ]


def test_select_hard_candidates_uses_each_project_name_once() -> None:
    rows = [
        _classified("pass-owner/shared-project", "Python", "pass", strong=10),
        _classified("review-owner/shared-project", "Python", "review", strong=20),
        _classified("other-owner/other-project", "Python", "review", strong=5),
    ]

    selected = calibration.select_hard_candidates(
        rows,
        excluded_repositories=set(),
        languages=("Python",),
        pass_per_language=1,
        dense_review_per_language=1,
        weak_review_per_language=0,
    )

    assert [row["name"] for row in selected] == [
        "pass-owner/shared-project",
        "other-owner/other-project",
    ]


def test_select_hard_candidates_removes_duplicates_within_one_stratum() -> None:
    rows = [
        _classified("same-owner/first-project", "Python", "review", strong=20),
        _classified("SAME-OWNER/second-project", "Python", "review", strong=15),
        _classified("other-owner/third-project", "Python", "review", strong=5),
    ]

    selected = calibration.select_hard_candidates(
        rows,
        excluded_repositories=set(),
        languages=("Python",),
        pass_per_language=0,
        dense_review_per_language=2,
        weak_review_per_language=0,
    )

    assert [row["name"] for row in selected] == [
        "same-owner/first-project",
        "other-owner/third-project",
    ]


def test_write_hard_input_excludes_prior_classification_fields(tmp_path: Path) -> None:
    classified_path = tmp_path / "all_classified.csv"
    classified_rows = [
        _classified("pass-owner/pass-project", "Python", "pass", strong=10),
        _classified("review-owner/review-project", "Python", "review", strong=8),
    ]
    with classified_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=list(classified_rows[0]))
        writer.writeheader()
        writer.writerows(classified_rows)
    previous_gold_path = tmp_path / "gold_labels.csv"
    previous_gold_path.write_text("repository\nexcluded/project\n", encoding="utf-8")

    output_path = calibration.write_hard_input(
        classified_path,
        previous_gold_path,
        tmp_path / "hard-input",
        languages=("Python",),
        pass_per_language=1,
        dense_review_per_language=1,
        weak_review_per_language=0,
    )

    with output_path.open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert [row["name"] for row in rows] == ["pass-owner/pass-project", "review-owner/review-project"]
    assert "guideline_status" not in rows[0]
    assert "candidate_documents_json" not in rows[0]
    assert rows[0]["sampling_stratum"] == "subtle_confirmed"


def test_main_routes_select_hard_input_command(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "hard-input" / "candidates.csv"
    write_hard_input = mocker.patch(
        "calibration.write_hard_input",
        autospec=True,
        return_value=output_path,
    )

    calibration.main(
        [
            "select-hard-input",
            "classified.csv",
            "gold_labels.csv",
            "hard-input",
        ],
    )

    write_hard_input.assert_called_once_with(
        Path("classified.csv"),
        Path("gold_labels.csv"),
        Path("hard-input"),
    )
    assert capsys.readouterr().out.strip() == str(output_path)


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


def _classified(
    repository: str,
    language: str,
    status: str,
    *,
    strong: int,
) -> dict[str, str]:
    return {
        "name": repository,
        "mainLanguage": language,
        "guideline_status": status,
        "candidate_count": "2",
        "candidate_documents_json": (
            f'[{{"path":"CONTRIBUTING.md","strong_matches":{strong},"normative_matches":2,"code_matches":3}}]'
        ),
        "guideline_path": "CONTRIBUTING.md",
        "tree_truncated": "False",
    }
