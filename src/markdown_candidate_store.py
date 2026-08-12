"""Append-only checkpoints for revision-pinned Markdown candidate extraction."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import markdown_filename_audit
import repository

_CHECKPOINT_FILENAME = "candidate_extraction_checkpoint.jsonl"
_CONFIGURATION_FILENAME = "candidate_extraction_configuration.json"


class MarkdownCandidateStore:
    """Persist the latest candidate-extraction result for each repository revision."""

    __slots__ = ("_configuration", "_output_dir", "_records")

    def __init__(self, output_dir: Path, *, configuration: Mapping[str, object]) -> None:
        self._output_dir = output_dir
        self._configuration = dict(configuration)
        self._records: dict[tuple[str, str], dict[str, object]] = {}

    def initialize(self) -> None:
        """Create a candidate-extraction run or resume a compatible checkpoint."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._validate_or_write_configuration()
        self._records = self._load_records()

    def append(self, result: markdown_filename_audit.RepositoryMarkdownFilenameAudit) -> None:
        """Durably append one repository result and retain it as the latest result."""
        record = _record(result)
        checkpoint_path = self._output_dir / _CHECKPOINT_FILENAME
        with checkpoint_path.open("a", encoding="utf-8") as checkpoint:
            checkpoint.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            checkpoint.write("\n")
            checkpoint.flush()
        self._records[_identity(record)] = record

    def completed_repositories(self) -> set[tuple[str, str]]:
        """Return repository revisions whose candidate extraction completed successfully."""
        terminal_statuses = {
            markdown_filename_audit.MarkdownFilenameAuditStatus.COMPLETED.value,
            markdown_filename_audit.MarkdownFilenameAuditStatus.EXPLICITLY_EXCLUDED.value,
        }
        return {identity for identity, record in self._records.items() if record["status"] in terminal_statuses}

    def report(self) -> markdown_filename_audit.MarkdownFilenameAuditReport:
        """Return all latest checkpoint records in original input order."""
        results = tuple(
            _result(record)
            for record in sorted(self._records.values(), key=lambda item: int(str(item["input_index"])))
        )
        completed = sum(
            result.status is markdown_filename_audit.MarkdownFilenameAuditStatus.COMPLETED for result in results
        )
        return markdown_filename_audit.MarkdownFilenameAuditReport(
            results=results,
            stats=markdown_filename_audit.MarkdownFilenameAuditStats(
                requested=len(results),
                completed=completed,
                errors=len(results) - completed,
                elapsed_seconds=0.0,
            ),
        )

    def write_reports(self) -> None:
        """Materialize deterministic CSV reports from the latest checkpoint records."""
        markdown_filename_audit.write_reports(self.report(), self._output_dir)

    def _validate_or_write_configuration(self) -> None:
        path = self._output_dir / _CONFIGURATION_FILENAME
        if not path.exists():
            _write_json(path, self._configuration)
            return
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != self._configuration:
            msg = f"Existing candidate extraction configuration does not match this run: {path}"
            raise ValueError(msg)

    def _load_records(self) -> dict[tuple[str, str], dict[str, object]]:
        path = self._output_dir / _CHECKPOINT_FILENAME
        if not path.exists():
            return {}
        records: dict[tuple[str, str], dict[str, object]] = {}
        lines = path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, start=1):
            try:
                record = cast(dict[str, object], json.loads(line))
            except json.JSONDecodeError:
                if line_number == len(lines):
                    break
                raise
            records[_identity(record)] = record
        return records


def _record(result: markdown_filename_audit.RepositoryMarkdownFilenameAudit) -> dict[str, object]:
    candidate = result.candidate
    return {
        "repository": candidate.repository,
        "revision": candidate.revision,
        "license_name": candidate.license_name,
        "source_file": candidate.source_file,
        "input_index": candidate.input_index,
        "fields": dict(candidate.fields),
        "status": result.status.value,
        "filename_files": [
            {
                "path": item.path,
                "matched_terms": list(item.matched_terms),
                "matched_content_terms": list(item.matched_content_terms),
                "blob_sha": item.blob_sha,
                "size_bytes": item.size_bytes,
            }
            for item in result.filename_files
        ],
        "agent_evidence": [
            {
                "path": item.path,
                "is_markdown": item.is_markdown,
                "filename_match": item.filename_match,
                "matched_terms": list(item.matched_terms),
                "content_match": item.content_match,
                "matched_content_terms": list(item.matched_content_terms),
            }
            for item in result.agent_evidence
        ],
        "error": result.error,
    }


def _result(record: Mapping[str, object]) -> markdown_filename_audit.RepositoryMarkdownFilenameAudit:
    candidate = repository.RepositoryCandidate(
        repository=str(record["repository"]),
        revision=str(record["revision"]),
        license_name=str(record["license_name"]),
        source_file=str(record["source_file"]),
        input_index=int(str(record["input_index"])),
        fields={str(key): str(value) for key, value in cast(Mapping[object, object], record["fields"]).items()},
    )
    return markdown_filename_audit.RepositoryMarkdownFilenameAudit(
        candidate=candidate,
        status=markdown_filename_audit.MarkdownFilenameAuditStatus(str(record["status"])),
        filename_files=tuple(
            markdown_filename_audit.MarkdownFilenameFile(
                path=str(item["path"]),
                matched_terms=tuple(str(value) for value in cast(list[object], item["matched_terms"])),
                matched_content_terms=tuple(str(value) for value in cast(list[object], item["matched_content_terms"])),
                blob_sha=str(item["blob_sha"]),
                size_bytes=int(str(item["size_bytes"])),
            )
            for item in cast(list[Mapping[str, object]], record["filename_files"])
        ),
        agent_evidence=tuple(
            markdown_filename_audit.AgentEvidenceFilenameCoverage(
                path=str(item["path"]),
                is_markdown=bool(item["is_markdown"]),
                filename_match=cast(bool | None, item["filename_match"]),
                matched_terms=tuple(str(value) for value in cast(list[object], item["matched_terms"])),
                content_match=cast(bool | None, item["content_match"]),
                matched_content_terms=tuple(str(value) for value in cast(list[object], item["matched_content_terms"])),
            )
            for item in cast(list[Mapping[str, object]], record["agent_evidence"])
        ),
        error=str(record["error"]),
    )


def _identity(record: Mapping[str, object]) -> tuple[str, str]:
    return str(record["repository"]), str(record["revision"])


def _write_json(path: Path, document: Mapping[str, object]) -> None:
    value = f"{json.dumps(document, indent=2, ensure_ascii=True, sort_keys=True)}\n"
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(value, encoding="utf-8")
    temporary_path.replace(path)
