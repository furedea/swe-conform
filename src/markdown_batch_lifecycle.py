"""Submission and result collection for Markdown classification Batch jobs."""

import csv
import json
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

import guideline
import markdown_classification
import openai_responses_client

_SUBMISSION_FILENAME = "batch_submission.json"
_INPUT_FILENAME = "batch_input.jsonl"
_OUTPUT_FILENAME = "batch_output.jsonl"
_ERROR_FILENAME = "batch_errors.jsonl"
_MANIFEST_FILENAME = "sample_manifest.csv"
_RESULT_FILENAME = "classified_files.csv"
_COST_FILENAME = "cost_summary.json"
_STRATUM_COUNT = 5


class BatchStatusClient(Protocol):
    """Retrieve one asynchronous Batch job."""

    def retrieve_batch(self, batch_id: str) -> Mapping[str, object]:
        """Retrieve one Batch job."""
        ...


class BatchLifecycleClient(BatchStatusClient, Protocol):
    """Manage one OpenAI Batch job and its Files API resources."""

    def upload_input(self, *, filename: str, content: bytes) -> Mapping[str, object]:
        """Upload one Batch input file."""
        ...

    def create_batch(self, *, input_file_id: str) -> Mapping[str, object]:
        """Create one Batch job."""
        ...

    def download_file(self, file_id: str) -> bytes:
        """Download one result file."""
        ...


def submit_cost_pilot(*, output_dir: Path, client: BatchLifecycleClient) -> Mapping[str, object]:
    """Upload a prepared JSONL file and create its Batch job once."""
    input_path = output_dir / _INPUT_FILENAME
    if not input_path.is_file():
        msg = f"prepared Batch input does not exist: {input_path}"
        raise FileNotFoundError(msg)
    submission_path = output_dir / _SUBMISSION_FILENAME
    submission = _read_optional_json(submission_path)
    if submission.get("batch_id"):
        msg = f"cost pilot was already submitted: {submission['batch_id']}"
        raise FileExistsError(msg)
    input_file_id = str(submission.get("input_file_id", ""))
    if not input_file_id:
        uploaded = client.upload_input(filename=input_path.name, content=input_path.read_bytes())
        input_file_id = _required_identifier(uploaded, resource="uploaded file")
        submission = {"provider": "openai", "input_file_id": input_file_id, "uploaded_file": uploaded}
        _write_json(submission_path, submission)
    batch = client.create_batch(input_file_id=input_file_id)
    batch_id = _required_identifier(batch, resource="batch")
    result = {
        "provider": "openai",
        "input_file_id": input_file_id,
        "batch_id": batch_id,
        "status": str(batch.get("status", "")),
        "batch": batch,
    }
    _write_json(submission_path, result)
    return result


def retrieve_cost_pilot(*, output_dir: Path, client: BatchStatusClient) -> Mapping[str, object]:
    """Retrieve and persist the current state of a submitted Batch job."""
    batch_id = _submitted_batch_id(output_dir)
    batch = client.retrieve_batch(batch_id)
    _write_json(output_dir / "batch_status.json", batch)
    return batch


def collect_cost_pilot(
    *,
    output_dir: Path,
    client: BatchLifecycleClient,
    input_usd_per_million_tokens: float,
    cached_input_usd_per_million_tokens: float,
    cache_write_input_usd_per_million_tokens: float,
    output_usd_per_million_tokens: float,
) -> Mapping[str, object]:
    """Download, verify, and summarize one completed or partially completed Batch."""
    batch = retrieve_cost_pilot(output_dir=output_dir, client=client)
    output_file_id = str(batch.get("output_file_id", ""))
    if not output_file_id:
        msg = f"Batch results are not available: status={batch.get('status', '')}"
        raise RuntimeError(msg)
    output_content = client.download_file(output_file_id)
    error_file_id = str(batch.get("error_file_id", ""))
    error_content = client.download_file(error_file_id) if error_file_id else b""
    return _collect_results(
        output_dir,
        output_content=output_content,
        error_content=error_content,
        input_usd_per_million_tokens=input_usd_per_million_tokens,
        cached_input_usd_per_million_tokens=cached_input_usd_per_million_tokens,
        cache_write_input_usd_per_million_tokens=cache_write_input_usd_per_million_tokens,
        output_usd_per_million_tokens=output_usd_per_million_tokens,
    )


def collect_precomputed_cost_pilot(
    *,
    output_dir: Path,
    output_content: bytes,
    error_content: bytes,
    input_usd_per_million_tokens: float,
    cached_input_usd_per_million_tokens: float,
    cache_write_input_usd_per_million_tokens: float,
    output_usd_per_million_tokens: float,
) -> Mapping[str, object]:
    """Verify and summarize Responses results produced outside a Batch API."""
    return _collect_results(
        output_dir,
        output_content=output_content,
        error_content=error_content,
        input_usd_per_million_tokens=input_usd_per_million_tokens,
        cached_input_usd_per_million_tokens=cached_input_usd_per_million_tokens,
        cache_write_input_usd_per_million_tokens=cache_write_input_usd_per_million_tokens,
        output_usd_per_million_tokens=output_usd_per_million_tokens,
        provider_reported_cost_usd=_provider_cost_from_outputs(output_content),
    )


def _collect_results(
    output_dir: Path,
    *,
    output_content: bytes,
    error_content: bytes,
    input_usd_per_million_tokens: float,
    cached_input_usd_per_million_tokens: float,
    cache_write_input_usd_per_million_tokens: float,
    output_usd_per_million_tokens: float,
    provider_reported_cost_usd: float | None = None,
) -> Mapping[str, object]:
    _write_bytes(output_dir / _OUTPUT_FILENAME, output_content)
    _write_bytes(output_dir / _ERROR_FILENAME, error_content)
    rows = _classification_rows(
        output_dir,
        output_content=output_content,
        error_content=error_content,
        input_usd_per_million_tokens=input_usd_per_million_tokens,
        cached_input_usd_per_million_tokens=cached_input_usd_per_million_tokens,
        cache_write_input_usd_per_million_tokens=cache_write_input_usd_per_million_tokens,
        output_usd_per_million_tokens=output_usd_per_million_tokens,
    )
    _write_results(output_dir / _RESULT_FILENAME, rows)
    report = _cost_report(rows, provider_reported_cost_usd=provider_reported_cost_usd)
    _write_json(output_dir / _COST_FILENAME, report)
    return report


def _classification_rows(
    output_dir: Path,
    *,
    output_content: bytes,
    error_content: bytes,
    input_usd_per_million_tokens: float,
    cached_input_usd_per_million_tokens: float,
    cache_write_input_usd_per_million_tokens: float,
    output_usd_per_million_tokens: float,
) -> tuple[dict[str, object], ...]:
    manifest = _manifest_by_id(output_dir / _MANIFEST_FILENAME)
    contents = _input_contents_by_id(output_dir / _INPUT_FILENAME)
    outputs = _documents_by_id(output_content)
    errors = _documents_by_id(error_content)
    unknown_ids = (outputs.keys() | errors.keys()).difference(manifest)
    if unknown_ids:
        msg = f"Batch results contain unknown custom IDs: {sorted(unknown_ids)!r}"
        raise ValueError(msg)
    rows = []
    for custom_id, manifest_row in sorted(manifest.items()):
        result = classify_result(
            output=outputs.get(custom_id),
            error=errors.get(custom_id),
            content=contents[custom_id],
        )
        usage = result.pop("usage")
        assert isinstance(usage, guideline.TokenUsage)
        provider_cost_usd = result.pop("provider_cost_usd")
        calculated_cost = markdown_classification.request_cost(
            usage,
            input_usd_per_million_tokens=input_usd_per_million_tokens,
            cached_input_usd_per_million_tokens=cached_input_usd_per_million_tokens,
            cache_write_input_usd_per_million_tokens=cache_write_input_usd_per_million_tokens,
            output_usd_per_million_tokens=output_usd_per_million_tokens,
        )
        cost = calculated_cost if provider_cost_usd is None else float(str(provider_cost_usd))
        rows.append(
            {
                **manifest_row,
                **result,
                "input_tokens": usage.input_tokens,
                "uncached_input_tokens": usage.uncached_input_tokens,
                "cached_input_tokens": usage.cached_input_tokens,
                "cache_write_input_tokens": usage.cache_write_input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "cost_usd": cost,
            },
        )
    return tuple(rows)


def classify_result(
    *,
    output: Mapping[str, object] | None,
    error: Mapping[str, object] | None,
    content: str,
) -> dict[str, object]:
    """Validate one provider result against the exact source content."""
    if output is None:
        return _model_error(error, reason="request_error" if error else "missing_result")
    return _classify_output(output, content=content)


def _classify_output(output: Mapping[str, object], *, content: str) -> dict[str, object]:
    response = cast(Mapping[str, object], output.get("response") or {})
    if int(str(response.get("status_code", 0))) != 200:
        return _model_error(output, reason="response_status_error")
    try:
        parsed = openai_responses_client.parse_json_response(
            cast(Mapping[str, object], response.get("body") or {}),
        )
        fields = markdown_classification.classification_fields(parsed.value, content=content)
    except (KeyError, TypeError, ValueError, RuntimeError) as exception:
        return _model_error(output, reason=f"invalid_model_response:{exception}")
    base = {
        **fields,
        "usage": parsed.usage,
        "provider_result": json.dumps(output, ensure_ascii=True, sort_keys=True),
        "provider_cost_usd": parsed.cost_usd,
    }
    return base


def _model_error(document: Mapping[str, object] | None, *, reason: str) -> dict[str, object]:
    return {
        "status": "model_error",
        "model_label": "",
        "model_reason": "",
        "quote": "",
        "confidence": 0,
        "reason": reason,
        "usage": guideline.TokenUsage(),
        "provider_result": json.dumps(document or {}, ensure_ascii=True, sort_keys=True),
        "provider_cost_usd": None,
    }


def _cost_report(
    rows: tuple[dict[str, object], ...],
    *,
    provider_reported_cost_usd: float | None = None,
) -> dict[str, object]:
    completed_rows = tuple(row for row in rows if row["status"] != "model_error")
    costs_by_stratum: defaultdict[int, list[float]] = defaultdict(list)
    populations: dict[int, int] = {}
    for row in rows:
        stratum = int(str(row["stratum"]))
        populations[stratum] = int(str(row["stratum_population"]))
        if row["status"] != "model_error":
            costs_by_stratum[stratum].append(float(str(row["cost_usd"])))
    estimated_cost: float | None = None
    if set(costs_by_stratum) == set(range(1, _STRATUM_COUNT + 1)):
        estimated_cost = round(
            sum(populations[stratum] * sum(costs) / len(costs) for stratum, costs in costs_by_stratum.items()),
            6,
        )
    calculated_pilot_cost = round(sum(float(str(row["cost_usd"])) for row in rows), 6)
    if provider_reported_cost_usd is not None and calculated_pilot_cost and estimated_cost is not None:
        estimated_cost = round(estimated_cost * provider_reported_cost_usd / calculated_pilot_cost, 6)
    pilot_cost = provider_reported_cost_usd if provider_reported_cost_usd is not None else calculated_pilot_cost
    average_completed_cost = (
        round(sum(float(str(row["cost_usd"])) for row in completed_rows) / len(completed_rows), 9)
        if completed_rows
        else 0.0
    )
    return {
        "sampled": len(rows),
        "completed": len(completed_rows),
        "errors": sum(row["status"] == "model_error" for row in rows),
        "status_counts": dict(sorted(_status_counts(rows).items())),
        "input_tokens": sum(int(str(row["input_tokens"])) for row in rows),
        "uncached_input_tokens": sum(int(str(row["uncached_input_tokens"])) for row in rows),
        "cached_input_tokens": sum(int(str(row["cached_input_tokens"])) for row in rows),
        "cache_write_input_tokens": sum(int(str(row["cache_write_input_tokens"])) for row in rows),
        "output_tokens": sum(int(str(row["output_tokens"])) for row in rows),
        "calculated_pilot_cost_usd": calculated_pilot_cost,
        "provider_reported_cost_usd": provider_reported_cost_usd,
        "pilot_cost_usd": round(pilot_cost, 6),
        "average_completed_cost_usd": average_completed_cost,
        "short_context_requests": sum(
            int(str(row["input_tokens"])) <= markdown_classification.LONG_CONTEXT_THRESHOLD for row in completed_rows
        ),
        "long_context_requests": sum(
            int(str(row["input_tokens"])) > markdown_classification.LONG_CONTEXT_THRESHOLD for row in completed_rows
        ),
        "estimated_full_batch_usd": estimated_cost,
    }


def _provider_cost_from_outputs(content: bytes) -> float | None:
    costs = []
    for output in _documents_by_id(content).values():
        response = cast(Mapping[str, object], output.get("response") or {})
        body = cast(Mapping[str, object], response.get("body") or {})
        usage = cast(Mapping[str, object], body.get("usage") or {})
        cost = usage.get("cost")
        if cost is None:
            return None
        if isinstance(cost, bool) or not isinstance(cost, int | float) or cost < 0:
            msg = "Responses usage.cost must be a non-negative number"
            raise ValueError(msg)
        costs.append(float(cost))
    return round(sum(costs), 9)


def _status_counts(rows: tuple[dict[str, object], ...]) -> defaultdict[str, int]:
    counts: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row["status"])] += 1
    return counts


def _manifest_by_id(path: Path) -> dict[str, Mapping[str, object]]:
    with path.open(encoding="utf-8", newline="") as input_file:
        rows = tuple(csv.DictReader(input_file))
    return _unique_by_id(rows, source=path)


def _input_contents_by_id(path: Path) -> dict[str, str]:
    documents = _documents_by_id(path.read_bytes())
    contents = {}
    for custom_id, document in documents.items():
        body = cast(Mapping[str, object], document.get("body") or {})
        input_document = cast(Mapping[str, object], json.loads(str(body.get("input", ""))))
        contents[custom_id] = str(input_document["content"])
    return contents


def _documents_by_id(content: bytes) -> dict[str, Mapping[str, object]]:
    documents = [
        cast(Mapping[str, object], json.loads(line)) for line in content.decode("utf-8").splitlines() if line.strip()
    ]
    return _unique_by_id(documents, source="JSONL content")


def _unique_by_id(
    documents: tuple[Mapping[str, object], ...] | list[Mapping[str, object]],
    *,
    source: object,
) -> dict[str, Mapping[str, object]]:
    by_id: dict[str, Mapping[str, object]] = {}
    for document in documents:
        custom_id = str(document.get("custom_id", ""))
        if not custom_id:
            msg = f"custom_id is missing from {source}"
            raise ValueError(msg)
        if custom_id in by_id:
            msg = f"duplicate custom_id in {source}: {custom_id}"
            raise ValueError(msg)
        by_id[custom_id] = document
    return by_id


def _write_results(path: Path, rows: tuple[dict[str, object], ...]) -> None:
    fieldnames = tuple(rows[0]) if rows else ()
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(path)


def _submitted_batch_id(output_dir: Path) -> str:
    submission = _read_optional_json(output_dir / _SUBMISSION_FILENAME)
    batch_id = str(submission.get("batch_id", ""))
    if not batch_id:
        msg = f"submitted Batch ID is missing from {output_dir / _SUBMISSION_FILENAME}"
        raise RuntimeError(msg)
    return batch_id


def _required_identifier(
    document: Mapping[str, object],
    *,
    resource: str,
) -> str:
    identifier = str(document.get("id", ""))
    if not identifier:
        msg = f"OpenAI {resource} response is missing id"
        raise RuntimeError(msg)
    return identifier


def _read_optional_json(path: Path) -> Mapping[str, object]:
    if not path.exists():
        return {}
    return cast(Mapping[str, object], json.loads(path.read_text(encoding="utf-8")))


def _write_json(path: Path, document: Mapping[str, object]) -> None:
    value = f"{json.dumps(document, indent=2, ensure_ascii=True, sort_keys=True)}\n"
    _write_bytes(path, value.encode())


def _write_bytes(path: Path, value: bytes) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_bytes(value)
    temporary_path.replace(path)
