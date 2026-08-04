"""Append-only checkpoints and deterministic repository selection reports."""

import csv
import html
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
_GUIDELINE_FILES_DIR = "guideline-files"
_GUIDELINE_FILES_REPORT = "guideline_files.csv"
_MANUAL_REVIEW_DIR = "manual-review"
_MODEL_RESPONSES_DIR = "model-responses"
_GUIDELINE_FILE_FIELDS = (
    "name",
    "lastCommitSHA",
    "guideline_path",
    "guideline_url",
    "guideline_evidence",
    "artifact_path",
)
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
        model_response_path = _save_model_response(result, self._output_dir)
        evidence_records = _save_guideline_files(result, self._output_dir)
        review_path = _save_manual_review_page(result, evidence_records, self._output_dir)
        record = _record_from_result(result, evidence_records, review_path, model_response_path)
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
            self._output_dir / "guideline_review.csv",
            [record for record in records if record["guideline_status"] != "pass"],
            fieldnames=fieldnames,
        )
        selected = [record for record in records if bool(record["selected"])]
        _write_csv(self._output_dir / "selected_repositories.csv", selected, fieldnames=fieldnames)
        _write_csv(
            self._output_dir / _GUIDELINE_FILES_REPORT,
            _guideline_file_records(records),
            fieldnames=_GUIDELINE_FILE_FIELDS,
        )
        _write_manual_review_index(self._output_dir, selected)
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


def _record_from_result(
    result: pipeline.RepositoryResult,
    evidence_records: Sequence[Mapping[str, str]],
    review_path: str,
    model_response_path: str,
) -> dict[str, object]:
    candidate = result.candidate
    input_units, output_units, total_units = astuple(result.guideline.usage)
    first_evidence = evidence_records[0] if evidence_records else {}
    return {
        "source_file": candidate.source_file,
        "input_index": candidate.input_index,
        **dict(candidate.fields),
        "repository_url": f"https://github.com/{candidate.repository}",
        "guideline_status": result.guideline.status.value,
        "guideline_reason": result.guideline.reason,
        "guideline_path": first_evidence.get("path", ""),
        "guideline_url": first_evidence.get("url", ""),
        "guideline_evidence": first_evidence.get("quote", ""),
        "guideline_file_count": len(evidence_records),
        "guideline_files_json": json.dumps(evidence_records, ensure_ascii=True, sort_keys=True),
        "unverified_evidence_count": len(result.guideline.evidence_issues),
        "model_response_path": model_response_path,
        "manual_review_path": review_path,
        "candidate_count": result.guideline.candidate_count,
        "tree_truncated": result.guideline.tree_truncated,
        "model_called": result.guideline.model_called,
        "model_input_tokens": input_units,
        "model_output_tokens": output_units,
        "model_total_tokens": total_units,
        "checkout_seconds": round(result.guideline.checkout_seconds, 3),
        "model_seconds": round(result.guideline.model_seconds, 3),
        "selected": result.is_selected,
    }


def _save_model_response(result: pipeline.RepositoryResult, output_dir: Path) -> str:
    raw_response = result.guideline.model_response_json
    if not raw_response:
        return ""
    candidate = result.candidate
    artifact_path = Path(
        _MODEL_RESPONSES_DIR,
        *_safe_parts(candidate.repository, name="repository"),
        *_safe_parts(candidate.revision, name="revision"),
        "response.json",
    )
    issues = [
        {
            "index": issue.index,
            "reason": issue.reason,
        }
        for issue in result.guideline.evidence_issues
    ]
    audit = {
        "model_response": json.loads(raw_response),
        "unverified_evidence": issues,
    }
    target_path = output_dir / artifact_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(target_path, audit)
    return artifact_path.as_posix()


def _save_guideline_files(
    result: pipeline.RepositoryResult,
    output_dir: Path,
) -> list[dict[str, str]]:
    if result.guideline.status is not guideline.GuidelineStatus.PASS:
        return []
    candidate = result.candidate
    repository_parts = _safe_parts(candidate.repository, name="repository")
    revision_parts = _safe_parts(candidate.revision, name="revision")
    records: list[dict[str, str]] = []
    for evidence in result.guideline.evidence:
        evidence_parts = _safe_parts(evidence.path, name="guideline path")
        if evidence.quote not in evidence.content.decode(encoding="utf-8", errors="replace"):
            msg = f"Guideline quote is absent from captured content: {evidence.path}"
            raise ValueError(msg)
        artifact_path = Path(_GUIDELINE_FILES_DIR, *repository_parts, *revision_parts, *evidence_parts)
        target_path = output_dir / artifact_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = target_path.with_name(f".{target_path.name}.tmp")
        temporary_path.write_bytes(evidence.content)
        temporary_path.replace(target_path)
        records.append(
            {
                "path": evidence.path,
                "url": _github_file_url(candidate.repository, candidate.revision, evidence.path),
                "quote": evidence.quote,
                "artifact_path": artifact_path.as_posix(),
            },
        )
    return records


def _save_manual_review_page(
    result: pipeline.RepositoryResult,
    evidence_records: Sequence[Mapping[str, str]],
    output_dir: Path,
) -> str:
    if not evidence_records:
        return ""
    candidate = result.candidate
    review_path = _manual_review_path(candidate.repository, candidate.revision)
    title = html.escape(candidate.repository, quote=False)
    revision_url = f"https://github.com/{candidate.repository}/tree/{candidate.revision}"
    lines = [
        f"# Manual review: {title}",
        "",
        f"- Repository: [{title}]({revision_url})",
        f"- Revision: [{candidate.revision}]({revision_url})",
        f"- Verified files: {len(evidence_records)}",
    ]
    for index, evidence in enumerate(evidence_records, start=1):
        path = str(evidence["path"])
        artifact_path = Path(str(evidence["artifact_path"]))
        local_path = Path("../../../..") / artifact_path
        local_url = quote(local_path.as_posix(), safe="/.")
        evidence_url = str(evidence["url"])
        rendered_path = html.escape(path, quote=False)
        rendered_quote = _markdown_quote(str(evidence["quote"]))
        lines.extend(
            (
                "",
                f"## Evidence {index}: {rendered_path}",
                "",
                f"- Saved file: [open local snapshot]({local_url})",
                f"- GitHub: [open pinned source]({evidence_url})",
                "",
                "### Model evidence quote",
                "",
                rendered_quote,
            ),
        )
    _write_text(output_dir / review_path, "\n".join(lines))
    return review_path.as_posix()


def _write_manual_review_index(
    output_dir: Path,
    records: Sequence[Mapping[str, object]],
) -> None:
    lines = [
        "# Manual guideline review",
        "",
        "| Repository | Revision | Files | Review |",
        "| --- | --- | ---: | --- |",
    ]
    for record in records:
        review_path = str(record.get("manual_review_path", ""))
        if not review_path:
            continue
        repository = _markdown_table_text(str(record["name"]))
        revision = _markdown_table_text(str(record["lastCommitSHA"]))
        relative_review_path = Path(review_path).relative_to(_MANUAL_REVIEW_DIR).as_posix()
        lines.append(
            f"| {repository} | {revision} | {record['guideline_file_count']} | "
            f"[open]({quote(relative_review_path, safe='/')}) |",
        )
    _write_text(output_dir / _MANUAL_REVIEW_DIR / "index.md", "\n".join(lines))


def _manual_review_path(repository: str, revision: str) -> Path:
    return Path(
        _MANUAL_REVIEW_DIR,
        *_safe_parts(repository, name="repository"),
        *_safe_parts(revision, name="revision"),
        "index.md",
    )


def _markdown_quote(raw_text: str) -> str:
    return "\n".join(f"> {html.escape(line, quote=False)}" for line in raw_text.splitlines())


def _markdown_table_text(raw_text: str) -> str:
    return html.escape(raw_text, quote=False).replace("|", "&#124;")


def _safe_parts(raw_path: str, *, name: str) -> tuple[str, ...]:
    path = Path(raw_path)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        msg = f"Invalid {name}: {raw_path!r}"
        raise ValueError(msg)
    return path.parts


def _github_file_url(repository: str, revision: str, path: str) -> str:
    encoded_path = quote(path, safe="/")
    return f"https://github.com/{repository}/blob/{revision}/{encoded_path}"


def _guideline_file_records(records: Sequence[Mapping[str, object]]) -> list[dict[str, str]]:
    file_records: list[dict[str, str]] = []
    for record in records:
        raw_files = json.loads(str(record.get("guideline_files_json", "[]")))
        if not isinstance(raw_files, list):
            continue
        for raw_file in raw_files:
            if not isinstance(raw_file, dict):
                continue
            file_records.append(
                {
                    "name": str(record["name"]),
                    "lastCommitSHA": str(record["lastCommitSHA"]),
                    "guideline_path": str(raw_file.get("path", "")),
                    "guideline_url": str(raw_file.get("url", "")),
                    "guideline_evidence": str(raw_file.get("quote", "")),
                    "artifact_path": str(raw_file.get("artifact_path", "")),
                },
            )
    return file_records


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
        "timing": {
            "checkout_seconds": round(
                sum(float(str(record.get("checkout_seconds", 0))) for record in records),
                3,
            ),
            "model_seconds": round(
                sum(float(str(record.get("model_seconds", 0))) for record in records),
                3,
            ),
        },
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


def _write_text(output_path: Path, value: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary_path.write_text(f"{value}\n", encoding="utf-8")
    temporary_path.replace(output_path)
