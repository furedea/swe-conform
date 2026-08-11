"""Compare per-file Markdown classifications with human decisions."""

import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_EVALUATION_FIELDS = (
    "repository",
    "markdown_path",
    "github_url",
    "human_decision",
    "llm_decision",
    "outcome",
    "confidence",
    "model_reason",
    "quote",
    "human_note",
)
_REPOSITORY_FIELDS = (
    "repository",
    "human_labeled_files",
    "resolved_predictions",
    "true_positives",
    "false_positives",
    "false_negatives",
    "true_negatives",
    "review_decisions",
    "model_errors",
    "missing_predictions",
    "resolved_accuracy",
    "strict_accuracy",
)


@dataclass(frozen=True, slots=True)
class ClassificationEvaluation:
    """Hold binary classification counts for a manually reviewed subset."""

    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    review_decisions: int
    model_errors: int
    missing_predictions: int
    checklist_rows: int
    human_labeled_files: int
    input_repositories: int | None
    human_labeled_repositories: int
    human_pass_repositories: int
    llm_pass_repositories: int
    output_dir: Path

    @property
    def resolved_predictions(self) -> int:
        """Return the number of binary model decisions."""
        return self.true_positives + self.false_positives + self.false_negatives + self.true_negatives

    @property
    def resolved_accuracy(self) -> float | None:
        """Return accuracy among pass and not-found model decisions."""
        if self.resolved_predictions == 0:
            return None
        return (self.true_positives + self.true_negatives) / self.resolved_predictions

    @property
    def strict_accuracy(self) -> float | None:
        """Return agreement with unresolved and missing predictions counted as incorrect."""
        return _ratio(self.true_positives + self.true_negatives, self.human_labeled_files)

    @property
    def resolution_rate(self) -> float | None:
        """Return the share of human-labeled files receiving a binary decision."""
        return _ratio(self.resolved_predictions, self.human_labeled_files)


def evaluate_classifications(
    *,
    classified_files_path: Path,
    checklist_path: Path,
    repository_csv_path: Path | None = None,
    output_dir: Path,
) -> ClassificationEvaluation:
    """Evaluate model decisions against non-empty human checklist decisions."""
    model_rows = _rows_by_url(classified_files_path, url_field="markdown_url")
    checklist_rows = _csv_rows(checklist_path)
    _validate_human_decisions(checklist_rows)
    human_labeled_files = sum(row.get("human_decision", "").strip() in {"pass", "not_found"} for row in checklist_rows)
    counts = {
        "true_positives": 0,
        "false_positives": 0,
        "false_negatives": 0,
        "true_negatives": 0,
        "review_decisions": 0,
        "model_errors": 0,
        "missing_predictions": 0,
    }
    evaluated_rows: list[dict[str, str]] = []
    for human_row in checklist_rows:
        human_decision = human_row.get("human_decision", "").strip()
        model_row = model_rows.get(human_row.get("github_url", "").strip())
        if human_decision not in {"pass", "not_found"}:
            continue
        if model_row is None:
            counts["missing_predictions"] += 1
            evaluated_rows.append(_evaluation_row(human_row, None, outcome="missing_prediction"))
            continue
        model_decision = model_row.get("status", "").strip()
        outcome = _binary_outcome(model_decision=model_decision, human_decision=human_decision)
        if outcome is not None:
            counts[outcome] += 1
            evaluated_rows.append(_evaluation_row(human_row, model_row, outcome=outcome.removesuffix("s")))
        elif model_decision == "review":
            counts["review_decisions"] += 1
            evaluated_rows.append(_evaluation_row(human_row, model_row, outcome="review"))
        elif model_decision == "model_error":
            counts["model_errors"] += 1
            evaluated_rows.append(_evaluation_row(human_row, model_row, outcome="model_error"))
    output_dir.mkdir(parents=True, exist_ok=True)
    ordered_rows = sorted(
        evaluated_rows,
        key=lambda row: (row["repository"].casefold(), row["markdown_path"].casefold(), row["github_url"]),
    )
    _write_evaluation_csv(output_dir / "evaluation_files.csv", ordered_rows)
    _write_evaluation_csv(
        output_dir / "false_positives.csv",
        [row for row in ordered_rows if row["outcome"] == "false_positive"],
    )
    _write_evaluation_csv(
        output_dir / "false_negatives.csv",
        [row for row in ordered_rows if row["outcome"] == "false_negative"],
    )
    repository_rows = _repository_metrics(ordered_rows)
    _write_csv(output_dir / "repository_metrics.csv", _REPOSITORY_FIELDS, repository_rows)
    repository_scope = _repository_scope(ordered_rows)
    report = ClassificationEvaluation(
        checklist_rows=len(checklist_rows),
        human_labeled_files=human_labeled_files,
        input_repositories=_input_repository_count(repository_csv_path),
        **repository_scope,
        output_dir=output_dir,
        **counts,
    )
    summary = _summary_document(
        report,
        classified_files_path=classified_files_path,
        checklist_path=checklist_path,
        repository_csv_path=repository_csv_path,
        evaluated_rows=ordered_rows,
    )
    _write_json(output_dir / "evaluation_summary.json", summary)
    (output_dir / "evaluation_summary.md").write_text(
        _markdown_summary(report, evaluated_rows=ordered_rows),
        encoding="utf-8",
    )
    return report


def _csv_rows(path: Path) -> tuple[dict[str, str], ...]:
    previous_limit = csv.field_size_limit()
    csv.field_size_limit(sys.maxsize)
    try:
        with path.open(encoding="utf-8", newline="") as input_file:
            return tuple(dict(row) for row in csv.DictReader(input_file))
    finally:
        csv.field_size_limit(previous_limit)


def _rows_by_url(path: Path, *, url_field: str) -> dict[str, dict[str, str]]:
    rows_by_url: dict[str, dict[str, str]] = {}
    for row in _csv_rows(path):
        url = row[url_field].strip()
        if url in rows_by_url:
            msg = f"duplicate URL in {path}: {url}"
            raise ValueError(msg)
        rows_by_url[url] = row
    return rows_by_url


def _input_repository_count(path: Path | None) -> int | None:
    if path is None:
        return None
    return len({row.get("name", "").strip() for row in _csv_rows(path) if row.get("name", "").strip()})


def _validate_human_decisions(rows: tuple[dict[str, str], ...]) -> None:
    for row in rows:
        decision = row.get("human_decision", "").strip()
        if decision not in {"", "pass", "not_found"}:
            msg = "human_decision must be pass, not_found, or empty"
            raise ValueError(msg)


def _binary_outcome(*, model_decision: str, human_decision: str) -> str | None:
    outcomes = {
        ("pass", "pass"): "true_positives",
        ("pass", "not_found"): "false_positives",
        ("not_found", "pass"): "false_negatives",
        ("not_found", "not_found"): "true_negatives",
    }
    return outcomes.get((model_decision, human_decision))


def _evaluation_row(
    human_row: dict[str, str],
    model_row: dict[str, str] | None,
    *,
    outcome: str,
) -> dict[str, str]:
    model = model_row or {}
    return {
        "repository": human_row.get("repository", "").strip(),
        "markdown_path": model.get("markdown_path", "").strip(),
        "github_url": human_row.get("github_url", "").strip(),
        "human_decision": human_row.get("human_decision", "").strip(),
        "llm_decision": model.get("status", "").strip(),
        "outcome": outcome,
        "confidence": model.get("confidence", "").strip(),
        "model_reason": model.get("model_reason", "").strip(),
        "quote": model.get("quote", "").strip(),
        "human_note": human_row.get("note", "").strip(),
    }


def _write_evaluation_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=_EVALUATION_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _summary_document(
    report: ClassificationEvaluation,
    *,
    classified_files_path: Path,
    checklist_path: Path,
    repository_csv_path: Path | None,
    evaluated_rows: list[dict[str, str]],
) -> dict[str, object]:
    return {
        "inputs": {
            "classified_files": str(classified_files_path),
            "checklist": str(checklist_path),
            "repositories": str(repository_csv_path) if repository_csv_path is not None else None,
        },
        "scope": {
            "checklist_files": report.checklist_rows,
            "human_labeled_files": report.human_labeled_files,
            "input_repositories": report.input_repositories,
            "human_labeled_repositories": report.human_labeled_repositories,
            "human_pass_repositories": report.human_pass_repositories,
            "llm_pass_repositories": report.llm_pass_repositories,
            "human_unlabeled_files": report.checklist_rows - report.human_labeled_files,
            "matched_predictions": report.human_labeled_files - report.missing_predictions,
            "resolved_predictions": report.resolved_predictions,
            "review_decisions": report.review_decisions,
            "model_errors": report.model_errors,
            "missing_predictions": report.missing_predictions,
        },
        "confusion_matrix": {
            "true_positives": report.true_positives,
            "false_positives": report.false_positives,
            "false_negatives": report.false_negatives,
            "true_negatives": report.true_negatives,
        },
        "decision_matrix": _decision_matrix(evaluated_rows),
        "metrics": _metrics(report),
        "outputs": {
            "evaluated_files": str(report.output_dir / "evaluation_files.csv"),
            "false_positives": str(report.output_dir / "false_positives.csv"),
            "false_negatives": str(report.output_dir / "false_negatives.csv"),
            "repository_metrics": str(report.output_dir / "repository_metrics.csv"),
        },
    }


def _metrics(report: ClassificationEvaluation) -> dict[str, float | None]:
    precision = _ratio(report.true_positives, report.true_positives + report.false_positives)
    recall = _ratio(report.true_positives, report.true_positives + report.false_negatives)
    return {
        "resolved_accuracy": _rounded(report.resolved_accuracy),
        "strict_accuracy": _rounded(report.strict_accuracy),
        "resolution_rate": _rounded(report.resolution_rate),
        "precision": _rounded(precision),
        "recall": _rounded(recall),
        "specificity": _rounded(_ratio(report.true_negatives, report.true_negatives + report.false_positives)),
        "f1": _rounded(_f1(precision, recall)),
        "false_positive_rate": _rounded(
            _ratio(report.false_positives, report.false_positives + report.true_negatives),
        ),
        "false_negative_rate": _rounded(
            _ratio(report.false_negatives, report.false_negatives + report.true_positives),
        ),
    }


def _decision_matrix(rows: list[dict[str, str]]) -> dict[str, dict[str, int]]:
    decisions = ("pass", "not_found", "review", "model_error", "missing_prediction")
    matrix = {human: dict.fromkeys(decisions, 0) for human in ("pass", "not_found")}
    for row in rows:
        llm_decision = row["llm_decision"] or "missing_prediction"
        if llm_decision in decisions:
            matrix[row["human_decision"]][llm_decision] += 1
    return matrix


def _markdown_summary(
    report: ClassificationEvaluation,
    *,
    evaluated_rows: list[dict[str, str]],
) -> str:
    metrics = _metrics(report)
    matrix = _decision_matrix(evaluated_rows)
    lines = [
        "# Markdown classification evaluation",
        "",
        "> Metrics cover only the manually reviewed checklist subset.",
        "",
        "## Scope",
        "",
        "| Item | Count |",
        "| --- | ---: |",
        f"| Checklist files | {report.checklist_rows} |",
        f"| Human-labeled files | {report.human_labeled_files} |",
        f"| Input repositories | {_optional_count(report.input_repositories)} |",
        f"| Human-labeled repositories | {report.human_labeled_repositories} |",
        f"| Human-pass repositories | {report.human_pass_repositories} |",
        f"| LLM-pass repositories | {report.llm_pass_repositories} |",
        f"| Resolved model decisions | {report.resolved_predictions} |",
        f"| Review decisions | {report.review_decisions} |",
        f"| Model errors | {report.model_errors} |",
        f"| Missing predictions | {report.missing_predictions} |",
        "",
        "## Confusion matrix",
        "",
        "| Human \\ LLM | pass | not_found | review | model_error | missing |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        _matrix_row("pass", matrix["pass"]),
        _matrix_row("not_found", matrix["not_found"]),
        "",
        "## Metrics",
        "",
        "Resolved accuracy uses only pass and not_found predictions. "
        "Strict accuracy uses every human-labeled file. "
        "Review, model_error, and missing predictions count as incorrect.",
        "",
        "| Metric | Value | Definition |",
        "| --- | ---: | --- |",
        *_metric_markdown_rows(metrics),
        "",
        "## Repository breakdown",
        "",
        "| Repository | Files | FP | FN | Review | Errors | Missing | Resolved accuracy |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        *_repository_markdown_rows(_repository_metrics(evaluated_rows)),
        "",
        "## False positives",
        "",
        *_error_lines(evaluated_rows, outcome="false_positive"),
        "",
        "## False negatives",
        "",
        *_error_lines(evaluated_rows, outcome="false_negative"),
        "",
    ]
    return "\n".join(lines)


def _matrix_row(human_decision: str, counts: dict[str, int]) -> str:
    return (
        f"| {human_decision} | {counts['pass']} | {counts['not_found']} | {counts['review']} | "
        f"{counts['model_error']} | {counts['missing_prediction']} |"
    )


def _error_lines(rows: list[dict[str, str]], *, outcome: str) -> list[str]:
    errors = [row for row in rows if row["outcome"] == outcome]
    if not errors:
        return ["None."]
    return [
        (
            f"- [{row['repository']}/{row['markdown_path']}]({row['github_url']}) — "
            f"confidence {row['confidence']}; {_single_line(row['model_reason'])}"
        )
        for row in errors
    ]


def _single_line(value: str) -> str:
    return " ".join(value.splitlines())


def _repository_metrics(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    rows_by_repository: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        rows_by_repository[row["repository"]].append(row)
    metrics = [
        _repository_metric(repository, repository_rows) for repository, repository_rows in rows_by_repository.items()
    ]
    return sorted(
        metrics,
        key=lambda row: (-int(row["false_positives"]), -int(row["false_negatives"]), row["repository"].casefold()),
    )


def _repository_scope(rows: list[dict[str, str]]) -> dict[str, int]:
    return {
        "human_labeled_repositories": len({row["repository"] for row in rows}),
        "human_pass_repositories": len(
            {row["repository"] for row in rows if row["human_decision"] == "pass"},
        ),
        "llm_pass_repositories": len(
            {row["repository"] for row in rows if row["llm_decision"] == "pass"},
        ),
    }


def _repository_metric(repository: str, rows: list[dict[str, str]]) -> dict[str, str]:
    outcome_counts = {outcome: sum(row["outcome"] == outcome for row in rows) for outcome in _outcomes()}
    resolved = sum(
        outcome_counts[outcome] for outcome in ("true_positive", "false_positive", "false_negative", "true_negative")
    )
    correct = outcome_counts["true_positive"] + outcome_counts["true_negative"]
    return {
        "repository": repository,
        "human_labeled_files": str(len(rows)),
        "resolved_predictions": str(resolved),
        "true_positives": str(outcome_counts["true_positive"]),
        "false_positives": str(outcome_counts["false_positive"]),
        "false_negatives": str(outcome_counts["false_negative"]),
        "true_negatives": str(outcome_counts["true_negative"]),
        "review_decisions": str(outcome_counts["review"]),
        "model_errors": str(outcome_counts["model_error"]),
        "missing_predictions": str(outcome_counts["missing_prediction"]),
        "resolved_accuracy": _metric_text(_ratio(correct, resolved)),
        "strict_accuracy": _metric_text(_ratio(correct, len(rows))),
    }


def _outcomes() -> tuple[str, ...]:
    return (
        "true_positive",
        "false_positive",
        "false_negative",
        "true_negative",
        "review",
        "model_error",
        "missing_prediction",
    )


def _repository_markdown_rows(rows: list[dict[str, str]]) -> list[str]:
    return [
        (
            f"| {row['repository']} | {row['human_labeled_files']} | {row['false_positives']} | "
            f"{row['false_negatives']} | {row['review_decisions']} | {row['model_errors']} | "
            f"{row['missing_predictions']} | {row['resolved_accuracy']} |"
        )
        for row in rows
    ]


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _optional_count(value: int | None) -> str:
    if value is None:
        return "N/A"
    return str(value)


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return 2 * precision * recall / (precision + recall)


def _rounded(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 6)


def _format_metric(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.4f}"


def _metric_markdown_rows(metrics: dict[str, float | None]) -> list[str]:
    definitions = (
        ("resolved_accuracy", "Resolved accuracy", "Agreement among pass and not_found predictions."),
        ("strict_accuracy", "Strict accuracy", "Agreement across every human-labeled file."),
        ("resolution_rate", "Resolution rate", "Share receiving a pass or not_found prediction."),
        ("precision", "Precision", "Share of predicted pass files that humans labeled pass."),
        ("recall", "Recall", "Share of human pass files predicted as pass."),
        ("specificity", "Specificity", "Share of human not_found files predicted as not_found."),
        ("f1", "F1", "Harmonic mean of precision and recall."),
        ("false_positive_rate", "False-positive rate", "Share of human not_found files predicted as pass."),
        ("false_negative_rate", "False-negative rate", "Share of human pass files predicted as not_found."),
    )
    return [
        f"| {label} | {_format_metric(metrics[name])} | {description} |" for name, label, description in definitions
    ]


def _metric_text(value: float | None) -> str:
    rounded = _rounded(value)
    if rounded is None:
        return ""
    return str(rounded)


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.write_text(
        f"{json.dumps(document, indent=2, ensure_ascii=True, sort_keys=True)}\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
