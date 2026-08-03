"""Evaluate guideline-classifier predictions against fixed gold labels."""

import argparse
import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, cast

_STATUSES = ("pass", "review", "not_found")
_DEFAULT_LANGUAGES = ("Java", "JavaScript", "Python", "TypeScript")
_PRIOR_CLASSIFICATION_FIELDS = frozenset(
    {
        "repository_url",
        "license_spdx_id",
        "license_status",
        "license_reason",
        "guideline_status",
        "guideline_reason",
        "guideline_path",
        "guideline_url",
        "guideline_evidence",
        "candidate_count",
        "candidate_documents_json",
        "tree_truncated",
    },
)


class ClassMetrics(TypedDict):
    """Metrics for one classification status."""

    precision: float
    recall: float
    f1: float
    support: int


class ClassificationError(TypedDict):
    """One prediction that differs from its gold label."""

    repository: str
    revision: str
    expected: str
    predicted: str
    reason: str
    evidence_path: str
    evidence_quote: str


class EvaluationReport(TypedDict):
    """Exact metrics and errors for one calibration split."""

    split: str
    samples: int
    accuracy: float
    macro_f1: float
    per_class: dict[str, ClassMetrics]
    confusion_matrix: dict[str, dict[str, int]]
    errors: list[ClassificationError]


@dataclass(frozen=True, slots=True)
class _MatchedClassification:
    repository: str
    revision: str
    expected: str
    predicted: str
    reason: str
    evidence_path: str
    evidence_quote: str


def evaluate_files(
    gold_path: Path,
    predictions_path: Path,
    *,
    split: str | None = None,
) -> EvaluationReport:
    """Evaluate one prediction CSV against gold labels."""
    return evaluate_rows(
        _read_csv(gold_path),
        _read_csv(predictions_path),
        split=split,
    )


def select_hard_candidates(
    rows: Sequence[Mapping[str, str]],
    *,
    excluded_repositories: set[str],
    languages: Sequence[str],
    pass_per_language: int = 1,
    dense_review_per_language: int = 2,
    weak_review_per_language: int = 2,
) -> list[dict[str, str]]:
    """Select deterministic subtle, dense, and weak boundary cases by language."""
    selected: list[dict[str, str]] = []
    selected_owners: set[str] = set()
    selected_project_names: set[str] = set()
    for language in languages:
        strata = (
            ("subtle_confirmed", "pass", pass_per_language, False),
            ("dense_unconfirmed", "review", dense_review_per_language, True),
            ("weak_unconfirmed", "review", weak_review_per_language, False),
        )
        for stratum, status, quota, descending in strata:
            ranked = sorted(
                (
                    row
                    for row in rows
                    if row["mainLanguage"] == language
                    and row["guideline_status"] == status
                    and row["name"] not in excluded_repositories
                    and row.get("tree_truncated", "false").lower() != "true"
                ),
                key=lambda row: (
                    -_difficulty_score(row) if descending else _difficulty_score(row),
                    row["name"].casefold(),
                ),
            )
            chosen: list[Mapping[str, str]] = []
            for row in ranked:
                if len(chosen) >= quota:
                    break
                owner = _owner(row["name"])
                project_name = _project_name(row["name"])
                if owner in selected_owners or project_name in selected_project_names:
                    continue
                chosen.append(row)
                selected_owners.add(owner)
                selected_project_names.add(project_name)
            if len(chosen) < quota:
                msg = f"Not enough diverse hard candidates for {language}/{stratum}: {len(chosen)} < {quota}"
                raise ValueError(msg)
            for row in chosen:
                annotated = dict(row)
                annotated["sampling_stratum"] = stratum
                annotated["difficulty_score"] = str(_difficulty_score(row))
                annotated["difficulty_reason"] = _difficulty_reason(stratum)
                selected.append(annotated)
    return selected


def write_hard_input(
    classified_path: Path,
    previous_gold_path: Path,
    output_dir: Path,
    *,
    languages: Sequence[str] = _DEFAULT_LANGUAGES,
    pass_per_language: int = 1,
    dense_review_per_language: int = 2,
    weak_review_per_language: int = 2,
) -> Path:
    """Select hard cases and write an input CSV without prior classification labels."""
    excluded = {row["repository"] for row in _read_csv(previous_gold_path)}
    selected = select_hard_candidates(
        _read_csv(classified_path),
        excluded_repositories=excluded,
        languages=languages,
        pass_per_language=pass_per_language,
        dense_review_per_language=dense_review_per_language,
        weak_review_per_language=weak_review_per_language,
    )
    rows = [
        {name: value for name, value in row.items() if name not in _PRIOR_CLASSIFICATION_FIELDS} for row in selected
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "candidates.csv"
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(output_path)
    return output_path


def write_split_input(input_path: Path, output_dir: Path, *, split: str) -> Path:
    """Write repository candidates for one calibration split."""
    with input_path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = reader.fieldnames
        rows = [row for row in reader if row.get("calibration_split") == split]
    if fieldnames is None:
        raise ValueError("Calibration input is missing a CSV header")
    if not rows:
        msg = f"Calibration input contains no rows for split: {split}"
        raise ValueError(msg)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / input_path.name
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(output_path)
    return output_path


def evaluate_rows(
    gold_rows: Sequence[Mapping[str, str]],
    prediction_rows: Sequence[Mapping[str, str]],
    *,
    split: str | None = None,
) -> EvaluationReport:
    """Return exact classification metrics for one optional data split."""
    predictions = _prediction_index(prediction_rows)
    classifications = _matched_classifications(gold_rows, predictions, split=split)
    confusion = _confusion_matrix(classifications)
    per_class = {status: _class_metrics(status, confusion) for status in _STATUSES}
    correct = sum(item.expected == item.predicted for item in classifications)
    return {
        "split": split or "all",
        "samples": len(classifications),
        "accuracy": _divide(correct, len(classifications)),
        "macro_f1": sum(float(metrics["f1"]) for metrics in per_class.values()) / len(_STATUSES),
        "per_class": per_class,
        "confusion_matrix": confusion,
        "errors": [
            {
                "repository": item.repository,
                "revision": item.revision,
                "expected": item.expected,
                "predicted": item.predicted,
                "reason": item.reason,
                "evidence_path": item.evidence_path,
                "evidence_quote": item.evidence_quote,
            }
            for item in classifications
            if item.expected != item.predicted
        ],
    }


def main(argv: Sequence[str] | None = None) -> None:
    """Split calibration inputs or print prediction metrics."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("gold_labels", type=Path)
    evaluate_parser.add_argument("predictions", type=Path)
    evaluate_parser.add_argument("--split", choices=("tuning", "holdout"))
    hard_parser = subparsers.add_parser("select-hard-input")
    hard_parser.add_argument("classified", type=Path)
    hard_parser.add_argument("previous_gold", type=Path)
    hard_parser.add_argument("output_dir", type=Path)
    split_parser = subparsers.add_parser("split-input")
    split_parser.add_argument("input", type=Path)
    split_parser.add_argument("output_dir", type=Path)
    split_parser.add_argument("--split", choices=("tuning", "holdout"), required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "select-hard-input":
        output_path = write_hard_input(arguments.classified, arguments.previous_gold, arguments.output_dir)
        print(output_path)
        return
    if arguments.command == "split-input":
        output_path = write_split_input(arguments.input, arguments.output_dir, split=arguments.split)
        print(output_path)
        return
    report = evaluate_files(
        arguments.gold_labels,
        arguments.predictions,
        split=arguments.split,
    )
    print(json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as input_file:
        return list(csv.DictReader(input_file))


def _difficulty_score(row: Mapping[str, str]) -> int:
    raw_documents = json.loads(row.get("candidate_documents_json", "[]") or "[]")
    documents = cast(list[Mapping[str, object]], raw_documents)
    strong_matches = max((_integer(document.get("strong_matches")) for document in documents), default=0)
    normative_matches = max((_integer(document.get("normative_matches")) for document in documents), default=0)
    code_matches = max((_integer(document.get("code_matches")) for document in documents), default=0)
    score = strong_matches * 10 + normative_matches * 4 + min(code_matches, 25) + _integer(row.get("candidate_count"))
    if row["guideline_status"] == "pass":
        score += _pass_path_ambiguity(row.get("guideline_path", ""))
    return score


def _difficulty_reason(stratum: str) -> str:
    reasons = {
        "subtle_confirmed": "lowest-density prior confirmed rule",
        "dense_unconfirmed": "highest-density prior unconfirmed candidate",
        "weak_unconfirmed": "lowest-density prior unconfirmed candidate",
    }
    return reasons[stratum]


def _integer(value: object) -> int:
    return int(str(value)) if value not in (None, "") else 0


def _pass_path_ambiguity(path: str) -> int:
    lowered = path.casefold()
    if "readme" in lowered:
        return 20
    obvious_tokens = ("contribut", "style", "guideline", "develop", "hacking")
    return 0 if any(token in lowered for token in obvious_tokens) else 8


def _owner(repository: str) -> str:
    return repository.partition("/")[0].casefold()


def _project_name(repository: str) -> str:
    return repository.partition("/")[2].casefold()


def _prediction_index(
    rows: Sequence[Mapping[str, str]],
) -> dict[tuple[str, str], Mapping[str, str]]:
    predictions: dict[tuple[str, str], Mapping[str, str]] = {}
    for row in rows:
        identity = (row["name"], row["lastCommitSHA"])
        if identity in predictions:
            msg = f"Duplicate prediction: {identity[0]}@{identity[1]}"
            raise ValueError(msg)
        status = row["guideline_status"]
        _validate_status(status)
        predictions[identity] = row
    return predictions


def _matched_classifications(
    rows: Sequence[Mapping[str, str]],
    predictions: Mapping[tuple[str, str], Mapping[str, str]],
    *,
    split: str | None,
) -> list[_MatchedClassification]:
    classifications: list[_MatchedClassification] = []
    for row in rows:
        if split is not None and row["split"] != split:
            continue
        identity = (row["repository"], row["revision"])
        if identity not in predictions:
            msg = f"Missing prediction: {identity[0]}@{identity[1]}"
            raise ValueError(msg)
        truth = row["status"]
        _validate_status(truth)
        prediction = predictions[identity]
        classifications.append(
            _MatchedClassification(
                repository=identity[0],
                revision=identity[1],
                expected=truth,
                predicted=prediction["guideline_status"],
                reason=prediction.get("guideline_reason", ""),
                evidence_path=prediction.get("guideline_path", ""),
                evidence_quote=prediction.get("guideline_evidence", ""),
            ),
        )
    if not classifications:
        msg = f"No gold labels matched split: {split or 'all'}"
        raise ValueError(msg)
    return classifications


def _validate_status(status: str) -> None:
    if status not in _STATUSES:
        msg = f"Unsupported classification status: {status}"
        raise ValueError(msg)


def _confusion_matrix(
    classifications: Sequence[_MatchedClassification],
) -> dict[str, dict[str, int]]:
    confusion = {truth: dict.fromkeys(_STATUSES, 0) for truth in _STATUSES}
    for item in classifications:
        confusion[item.expected][item.predicted] += 1
    return confusion


def _class_metrics(
    status: str,
    confusion: Mapping[str, Mapping[str, int]],
) -> ClassMetrics:
    true_positive = confusion[status][status]
    predicted = sum(confusion[truth][status] for truth in _STATUSES)
    support = sum(confusion[status].values())
    precision = _divide(true_positive, predicted)
    recall = _divide(true_positive, support)
    return {
        "precision": precision,
        "recall": recall,
        "f1": _divide(2 * precision * recall, precision + recall),
        "support": support,
    }


def _divide(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0


if __name__ == "__main__":
    main()
