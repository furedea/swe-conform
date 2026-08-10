"""Concurrent per-file Markdown classification through a Responses API."""

import json
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

import markdown_batch
import openai_responses_client

_INPUT_FILENAME = "batch_input.jsonl"
_CHECKPOINT_FILENAME = "responses_checkpoint.jsonl"
_COST_FILENAME = "cost_summary.json"
_RUN_FILENAME = "responses_run.json"


class ResponsesClient(Protocol):
    """Return one structured response for one prepared Markdown file."""

    def complete_json(
        self,
        *,
        instructions: str,
        input_text: str,
        model: str,
        reasoning_effort: str,
        max_output_tokens: int,
        schema_name: str,
        schema: Mapping[str, object],
    ) -> openai_responses_client.JsonResponse:
        """Run one structured Responses request."""
        ...


def run_prepared_classification(
    *,
    output_dir: Path,
    client: ResponsesClient,
    workers: int,
) -> Mapping[str, object]:
    """Classify prepared files concurrently and persist each completed response."""
    if workers < 1:
        msg = "workers must be at least 1"
        raise ValueError(msg)
    requests = _prepared_requests(output_dir / _INPUT_FILENAME)
    checkpoint_path = output_dir / _CHECKPOINT_FILENAME
    results = _checkpoint_results(checkpoint_path)
    pending = tuple(request for custom_id, request in requests.items() if not _is_success(results.get(custom_id)))
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_execute_request, client, request): request for request in pending}
        for future in as_completed(futures):
            result = future.result()
            custom_id = str(result["custom_id"])
            _append_checkpoint(checkpoint_path, result)
            results[custom_id] = result
    elapsed_seconds = round(time.perf_counter() - started, 6)
    output_documents = tuple(results[custom_id] for custom_id in requests if _is_success(results.get(custom_id)))
    error_documents = tuple(
        results[custom_id] for custom_id in requests if custom_id in results and not _is_success(results[custom_id])
    )
    report = dict(
        markdown_batch.collect_precomputed_cost_pilot(
            output_dir=output_dir,
            output_content=_jsonl_bytes(output_documents),
            error_content=_jsonl_bytes(error_documents),
        ),
    )
    report.update(
        {
            "provider": "openrouter",
            "requested": len(requests),
            "attempted": len(pending),
            "resumed": len(requests) - len(pending),
            "workers": workers,
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "elapsed_seconds": elapsed_seconds,
            "request_seconds": round(sum(_elapsed_seconds(result) for result in results.values()), 6),
        },
    )
    _write_json(output_dir / _COST_FILENAME, report)
    _write_json(output_dir / _RUN_FILENAME, report)
    return report


def _execute_request(client: ResponsesClient, request: Mapping[str, object]) -> Mapping[str, object]:
    custom_id = str(request.get("custom_id", ""))
    started = time.perf_counter()
    try:
        response = _complete_request(client, request)
    except Exception as error:
        return {
            "custom_id": custom_id,
            "response": None,
            "error": {
                "type": type(error).__name__,
                "message": str(error),
            },
            "elapsed_seconds": round(time.perf_counter() - started, 6),
        }
    return {
        "custom_id": custom_id,
        "response": {
            "status_code": 200,
            "body": response.document,
        },
        "error": None,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
    }


def _complete_request(
    client: ResponsesClient,
    request: Mapping[str, object],
) -> openai_responses_client.JsonResponse:
    if request.get("method") != "POST" or request.get("url") != "/v1/responses":
        msg = "prepared request must POST to /v1/responses"
        raise ValueError(msg)
    body = cast(Mapping[str, object], request.get("body") or {})
    reasoning = cast(Mapping[str, object], body.get("reasoning") or {})
    text = cast(Mapping[str, object], body.get("text") or {})
    output_format = cast(Mapping[str, object], text.get("format") or {})
    schema = cast(Mapping[str, object], output_format.get("schema") or {})
    return client.complete_json(
        instructions=str(body.get("instructions", "")),
        input_text=str(body.get("input", "")),
        model=str(body.get("model", "")),
        reasoning_effort=str(reasoning.get("effort", "")),
        max_output_tokens=int(str(body.get("max_output_tokens", 0))),
        schema_name=str(output_format.get("name", "")),
        schema=schema,
    )


def _prepared_requests(path: Path) -> dict[str, Mapping[str, object]]:
    if not path.is_file():
        msg = f"prepared Responses input does not exist: {path}"
        raise FileNotFoundError(msg)
    requests: dict[str, Mapping[str, object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        request = cast(Mapping[str, object], json.loads(line))
        custom_id = str(request.get("custom_id", ""))
        if not custom_id:
            msg = f"prepared request in {path} is missing custom_id"
            raise ValueError(msg)
        if custom_id in requests:
            msg = f"duplicate prepared custom_id: {custom_id}"
            raise ValueError(msg)
        requests[custom_id] = request
    if not requests:
        msg = f"prepared Responses input is empty: {path}"
        raise ValueError(msg)
    return requests


def _checkpoint_results(path: Path) -> dict[str, Mapping[str, object]]:
    if not path.exists():
        return {}
    results: dict[str, Mapping[str, object]] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        try:
            result = cast(Mapping[str, object], json.loads(line))
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                break
            raise
        custom_id = str(result.get("custom_id", ""))
        if custom_id:
            results[custom_id] = result
    return results


def _is_success(result: Mapping[str, object] | None) -> bool:
    return result is not None and result.get("response") is not None


def _append_checkpoint(path: Path, result: Mapping[str, object]) -> None:
    with path.open("a", encoding="utf-8") as output_file:
        output_file.write(f"{json.dumps(result, ensure_ascii=True, sort_keys=True)}\n")
        output_file.flush()


def _elapsed_seconds(result: Mapping[str, object]) -> float:
    return float(str(result.get("elapsed_seconds", 0.0)))


def _jsonl_bytes(documents: tuple[Mapping[str, object], ...]) -> bytes:
    value = "".join(f"{json.dumps(document, ensure_ascii=True, sort_keys=True)}\n" for document in documents)
    return value.encode()


def _write_json(path: Path, document: Mapping[str, object]) -> None:
    value = f"{json.dumps(document, indent=2, ensure_ascii=True, sort_keys=True)}\n"
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(value, encoding="utf-8")
    temporary_path.replace(path)
