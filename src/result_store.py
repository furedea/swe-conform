"""Append-only checkpoints and deterministic repository selection reports."""

import csv
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import astuple
from pathlib import Path
from typing import cast
from urllib.parse import quote

import guideline
import pipeline

_CHECKPOINT_FILE = "results.jsonl"
_CONFIGURATION_FILE = "run_configuration.json"
_RETRYABLE_STATUSES = frozenset(
    {
        guideline.GuidelineStatus.MODEL_ERROR.value,
        guideline.GuidelineStatus.RETRIEVAL_ERROR.value,
    },
)


class ResultStore:
    """Persist per-repository results and materialize stage-specific reports."""

    __slots__ = ("_configuration", "_output_dir", "_records")

    def __init__(self, output_dir: Path, *, configuration: Mapping[str, object]) -> None:
        self._output_dir = output_dir
        self._configuration = dict(configuration)
        self._records: dict[tuple[str, str], dict[str, object]] = {}

    def initialize(self) -> None:
        """Create a run or load a compatible existing checkpoint."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._validate_or_write_configuration()
        self._records = self._load_records()

    def append(self, result: pipeline.RepositoryResult) -> None:
        """Append one durable result and update the in-memory latest record."""
        record = _record_from_result(result)
        checkpoint_path = self._output_dir / _CHECKPOINT_FILE
        with checkpoint_path.open("a", encoding="utf-8") as checkpoint:
            checkpoint.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            checkpoint.write("\n")
            checkpoint.flush()
        self._records[_identity(record)] = record

    def completed_repositories(self) -> set[tuple[str, str]]:
        """Return successful or terminal classifications that need no retry."""
        return {
            identity
            for identity, record in self._records.items()
            if record["guideline_status"] not in _RETRYABLE_STATUSES
        }

    def write_reports(self) -> None:
        """Materialize ordered CSV reports and an aggregate JSON summary."""
        records = sorted(self._records.values(), key=lambda record: int(record["input_index"]))
        fieldnames = _fieldnames(records)
        _write_csv(self._output_dir / "all_classified.csv", records, fieldnames=fieldnames)
        _write_csv(
            self._output_dir / "guideline_passed.csv",
            [record for record in records if record["guideline_status"] == "pass"],
            fieldnames=fieldnames,
        )
        _write_csv(
            self._output_dir / "guideline_review.csv",
            [record for record in records if record["guideline_status"] != "pass"],
            fieldnames=fieldnames,
        )
        _write_csv(
            self._output_dir / "license_excluded_or_review.csv",
            [
                record
                for record in records
                if record["guideline_status"] == "pass" and record["license_status"] != "pass"
            ],
            fieldnames=fieldnames,
        )
        selected = [record for record in records if bool(record["selected"])]
        _write_csv(self._output_dir / "selected_repositories.csv", selected, fieldnames=fieldnames)
        _write_json(self._output_dir / "summary.json", _summary(records, selected))

    def _validate_or_write_configuration(self) -> None:
        configuration_path = self._output_dir / _CONFIGURATION_FILE
        if not configuration_path.exists():
            _write_json(configuration_path, self._configuration)
            return
        existing = json.loads(configuration_path.read_text(encoding="utf-8"))
        if existing != self._configuration:
            msg = f"Existing output configuration does not match this run: {configuration_path}"
            raise ValueError(msg)

    def _load_records(self) -> dict[tuple[str, str], dict[str, object]]:
        checkpoint_path = self._output_dir / _CHECKPOINT_FILE
        if not checkpoint_path.exists():
            return {}
        records: dict[tuple[str, str], dict[str, object]] = {}
        lines = checkpoint_path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, start=1):
            try:
                record = cast(dict[str, object], json.loads(line))
            except json.JSONDecodeError:
                if line_number == len(lines):
                    break
                raise
            records[_identity(record)] = record
        return records


def _record_from_result(result: pipeline.RepositoryResult) -> dict[str, object]:
    candidate = result.candidate
    input_units, output_units, total_units = astuple(result.guideline.usage)
    evidence_url = ""
    if result.guideline.evidence_path:
        path = quote(result.guideline.evidence_path)
        evidence_url = f"https://github.com/{candidate.repository}/blob/{candidate.revision}/{path}"
    return {
        "source_file": candidate.source_file,
        "input_index": candidate.input_index,
        **dict(candidate.fields),
        "repository_url": f"https://github.com/{candidate.repository}",
        "guideline_status": result.guideline.status.value,
        "guideline_reason": result.guideline.reason,
        "guideline_path": result.guideline.evidence_path,
        "guideline_url": evidence_url,
        "guideline_evidence": result.guideline.evidence_quote,
        "candidate_count": result.guideline.candidate_count,
        "tree_truncated": result.guideline.tree_truncated,
        "model_called": result.guideline.model_called,
        "model_input_tokens": input_units,
        "model_output_tokens": output_units,
        "model_total_tokens": total_units,
        "license_spdx_id": result.license.spdx_id,
        "license_status": result.license.status.value,
        "license_reason": result.license.reason,
        "selected": result.is_selected,
    }


def _identity(record: Mapping[str, object]) -> tuple[str, str]:
    return str(record["name"]), str(record["lastCommitSHA"])


def _write_csv(
    output_path: Path,
    records: Sequence[Mapping[str, object]],
    *,
    fieldnames: Sequence[str],
) -> None:
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    temporary_path.replace(output_path)


def _fieldnames(records: Sequence[Mapping[str, object]]) -> list[str]:
    fieldnames: list[str] = []
    for record in records:
        fieldnames.extend(name for name in record if name not in fieldnames)
    return fieldnames


def _summary(
    records: Sequence[Mapping[str, object]],
    selected: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    return {
        "processed": len(records),
        "selected": len(selected),
        "model_calls": sum(bool(record["model_called"]) for record in records),
        "guideline_status": dict(Counter(str(record["guideline_status"]) for record in records)),
        "license_status": dict(Counter(str(record["license_status"]) for record in records)),
        "usage": {
            "input_tokens": sum(int(str(record["model_input_tokens"])) for record in records),
            "output_tokens": sum(int(str(record["model_output_tokens"])) for record in records),
            "total_tokens": sum(int(str(record["model_total_tokens"])) for record in records),
        },
    }


def _write_json(output_path: Path, value: Mapping[str, object]) -> None:
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary_path.write_text(
        f"{json.dumps(value, indent=2, ensure_ascii=True, sort_keys=True)}\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
