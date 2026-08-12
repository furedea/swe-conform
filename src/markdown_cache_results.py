"""Checkpoint and report materialization for local-cache Markdown classification."""

import csv
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import markdown_classification

_CHECKPOINT_FILENAME = "cache_classification_checkpoint.jsonl"
_CONFIGURATION_FILENAME = "cache_classification_configuration.json"
_CLASSIFIED_FILES_FILENAME = "classified_files.csv"
_REPOSITORY_SUMMARY_FILENAME = "repository_classification_summary.csv"
_SELECTED_REPOSITORIES_FILENAME = "selected_repositories.csv"
_COST_FILENAME = "cost_summary.json"
_RUN_FILENAME = "responses_run.json"
_RETRYABLE_STATUSES = frozenset({"model_error", "retrieval_error"})
_CLASSIFIED_FIELDS = (
    "custom_id",
    "input_index",
    "name",
    "lastCommitSHA",
    "markdown_path",
    "blob_sha",
    "size_bytes",
    "markdown_url",
    "matched_filename_terms",
    "matched_content_terms",
    "status",
    "model_label",
    "model_reason",
    "quote",
    "confidence",
    "reason",
    "input_tokens",
    "uncached_input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "total_tokens",
    "cost_usd",
    "elapsed_seconds",
    "provider_result",
)
_REPOSITORY_FIELDS = (
    "name",
    "lastCommitSHA",
    "status",
    "candidate_file_count",
    "classified_file_count",
    "pass_count",
    "not_found_count",
    "review_count",
    "model_error_count",
    "retrieval_error_count",
    "extraction_status",
    "error",
)


class CacheClassificationStore:
    """Persist final per-file classifications in an append-only checkpoint."""

    __slots__ = ("_configuration", "_output_dir", "_records")

    def __init__(self, output_dir: Path, *, configuration: Mapping[str, object]) -> None:
        self._output_dir = output_dir
        self._configuration = dict(configuration)
        self._records: dict[str, dict[str, object]] = {}

    def initialize(self) -> None:
        """Create a run or resume a compatible local-cache classification."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._validate_or_write_configuration()
        self._records = self._load_records()

    def append(self, record: Mapping[str, object]) -> None:
        """Durably append one candidate result and retain it as the latest result."""
        document = dict(record)
        checkpoint_path = self._output_dir / _CHECKPOINT_FILENAME
        with checkpoint_path.open("a", encoding="utf-8") as checkpoint:
            checkpoint.write(json.dumps(document, ensure_ascii=True, sort_keys=True))
            checkpoint.write("\n")
            checkpoint.flush()
        self._records[str(document["custom_id"])] = document

    def completed_ids(self) -> set[str]:
        """Return file IDs whose classifications need no retry."""
        return {
            custom_id for custom_id, record in self._records.items() if record["status"] not in _RETRYABLE_STATUSES
        }

    def records(self) -> tuple[dict[str, object], ...]:
        """Return latest candidate results in deterministic input order."""
        return tuple(
            sorted(
                self._records.values(),
                key=lambda record: (int(str(record["input_index"])), str(record["markdown_path"])),
            ),
        )

    def write_reports(self) -> dict[str, object]:
        """Materialize deterministic file and cost reports from checkpoint records."""
        records = self.records()
        _write_csv(
            self._output_dir / _CLASSIFIED_FILES_FILENAME,
            records,
            fieldnames=_CLASSIFIED_FIELDS,
        )
        report = _cost_report(records)
        _write_json(self._output_dir / _COST_FILENAME, report)
        return report

    def _validate_or_write_configuration(self) -> None:
        path = self._output_dir / _CONFIGURATION_FILENAME
        if not path.exists():
            _write_json(path, self._configuration)
            return
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != self._configuration:
            msg = f"Existing cache classification configuration does not match this run: {path}"
            raise ValueError(msg)

    def _load_records(self) -> dict[str, dict[str, object]]:
        path = self._output_dir / _CHECKPOINT_FILENAME
        if not path.exists():
            return {}
        records: dict[str, dict[str, object]] = {}
        lines = path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, start=1):
            try:
                record = cast(dict[str, object], json.loads(line))
            except json.JSONDecodeError:
                if line_number == len(lines):
                    break
                raise
            records[str(record["custom_id"])] = record
        return records


def write_repository_reports(
    repository_summary_csv: Path,
    records: Sequence[Mapping[str, object]],
    *,
    output_dir: Path,
) -> None:
    """Aggregate per-file classifications over every extracted repository."""
    classified: defaultdict[tuple[str, str], list[Mapping[str, object]]] = defaultdict(list)
    for record in records:
        classified[(str(record["name"]), str(record["lastCommitSHA"]))].append(record)
    rows: list[dict[str, object]] = []
    with repository_summary_csv.open(encoding="utf-8", newline="") as input_file:
        for source in csv.DictReader(input_file):
            identity = source["name"], source["lastCommitSHA"]
            file_records = classified.get(identity, [])
            counts = Counter(str(record["status"]) for record in file_records)
            candidate_count = int(source["markdown_filename_and_content_file_count"])
            rows.append(
                {
                    "name": identity[0],
                    "lastCommitSHA": identity[1],
                    "status": _repository_status(
                        extraction_status=source["status"],
                        candidate_count=candidate_count,
                        classified_count=len(file_records),
                        counts=counts,
                    ),
                    "candidate_file_count": candidate_count,
                    "classified_file_count": len(file_records),
                    "pass_count": counts["pass"],
                    "not_found_count": counts["not_found"],
                    "review_count": counts["review"],
                    "model_error_count": counts["model_error"],
                    "retrieval_error_count": counts["retrieval_error"],
                    "extraction_status": source["status"],
                    "error": source["error"],
                },
            )
    _write_csv(output_dir / _REPOSITORY_SUMMARY_FILENAME, rows, fieldnames=_REPOSITORY_FIELDS)
    _write_csv(
        output_dir / _SELECTED_REPOSITORIES_FILENAME,
        [row for row in rows if row["status"] == "pass"],
        fieldnames=_REPOSITORY_FIELDS,
    )


def write_run_report(output_dir: Path, report: Mapping[str, object]) -> None:
    """Persist the final cumulative cost and execution reports."""
    _write_json(output_dir / _COST_FILENAME, report)
    _write_json(output_dir / _RUN_FILENAME, report)


def _cost_report(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    completed = tuple(record for record in records if record["status"] not in _RETRYABLE_STATUSES)
    cost = round(sum(float(str(record["cost_usd"])) for record in records), 6)
    return {
        "sampled": len(records),
        "completed": len(completed),
        "errors": len(records) - len(completed),
        "status_counts": dict(sorted(Counter(str(record["status"]) for record in records).items())),
        "input_tokens": sum(int(str(record["input_tokens"])) for record in records),
        "uncached_input_tokens": sum(int(str(record["uncached_input_tokens"])) for record in records),
        "cached_input_tokens": sum(int(str(record["cached_input_tokens"])) for record in records),
        "cache_write_input_tokens": sum(int(str(record["cache_write_input_tokens"])) for record in records),
        "output_tokens": sum(int(str(record["output_tokens"])) for record in records),
        "calculated_pilot_cost_usd": cost,
        "provider_reported_cost_usd": None,
        "pilot_cost_usd": cost,
        "average_completed_cost_usd": (
            round(sum(float(str(record["cost_usd"])) for record in completed) / len(completed), 9)
            if completed
            else 0.0
        ),
        "short_context_requests": sum(
            int(str(record["input_tokens"])) <= markdown_classification.LONG_CONTEXT_THRESHOLD for record in completed
        ),
        "long_context_requests": sum(
            int(str(record["input_tokens"])) > markdown_classification.LONG_CONTEXT_THRESHOLD for record in completed
        ),
        "estimated_full_batch_usd": None,
    }


def _repository_status(
    *,
    extraction_status: str,
    candidate_count: int,
    classified_count: int,
    counts: Counter[str],
) -> str:
    if extraction_status != "completed":
        return extraction_status
    if candidate_count == 0:
        return "no_candidates"
    if counts["pass"]:
        return "pass"
    if counts["review"] or counts["model_error"] or counts["retrieval_error"]:
        return "review"
    if classified_count != candidate_count:
        return "review"
    return "not_found"


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, object]],
    *,
    fieldnames: Sequence[str],
) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(path)


def _write_json(path: Path, document: Mapping[str, object]) -> None:
    value = f"{json.dumps(document, indent=2, ensure_ascii=True, sort_keys=True)}\n"
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(value, encoding="utf-8")
    temporary_path.replace(path)
