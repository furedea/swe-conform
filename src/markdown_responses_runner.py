"""Concurrent per-file Markdown classification through a Responses API."""

import hashlib
import json
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

import markdown_batch
import openai_responses_client
import responses_provider

_INPUT_FILENAME = "batch_input.jsonl"
_CHECKPOINT_FILENAME = "responses_checkpoint.jsonl"
_EXECUTION_FILENAME = "responses_execution.json"
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
    provider: str,
    region: str | None,
    workers: int,
) -> Mapping[str, object]:
    """Classify prepared files concurrently and persist each completed response."""
    if workers < 1:
        msg = "workers must be at least 1"
        raise ValueError(msg)
    input_path = output_dir / _INPUT_FILENAME
    requests = _prepared_requests(input_path)
    requested_model, reasoning_effort, max_output_tokens = _request_configuration(requests)
    provider_model = responses_provider.model_id(provider, requested_model)
    checkpoint_path = output_dir / _CHECKPOINT_FILENAME
    _ensure_execution_configuration(
        output_dir=output_dir,
        input_path=input_path,
        checkpoint_path=checkpoint_path,
        provider=provider,
        region=region,
        requested_model=requested_model,
        provider_model=provider_model,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
        workers=workers,
    )
    results = _checkpoint_results(checkpoint_path)
    pending = tuple(request for custom_id, request in requests.items() if not _is_success(results.get(custom_id)))
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    concurrent_pending = pending
    if pending:
        preflight_result = execute_request(client, pending[0])
        preflight_id = str(preflight_result["custom_id"])
        _append_checkpoint(checkpoint_path, preflight_result)
        results[preflight_id] = preflight_result
        raise_for_fatal_preflight(preflight_result)
        concurrent_pending = pending[1:]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(execute_request, client, request): request for request in concurrent_pending}
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
            provider=provider,
        ),
    )
    report.update(
        {
            "provider": provider,
            "region": region,
            "requested_model": requested_model,
            "provider_model": provider_model,
            "reasoning_effort": reasoning_effort,
            "max_output_tokens": max_output_tokens,
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


def execute_request(client: ResponsesClient, request: Mapping[str, object]) -> Mapping[str, object]:
    """Execute one provider-neutral request and capture its result or error."""
    custom_id = str(request.get("custom_id", ""))
    started = time.perf_counter()
    try:
        response = _complete_request(client, request)
    except Exception as error:
        error_document: dict[str, object] = {
            "type": type(error).__name__,
            "message": str(error),
        }
        if isinstance(error, openai_responses_client.ResponsesRequestError):
            error_document["status_code"] = error.status_code
        return {
            "custom_id": custom_id,
            "response": None,
            "error": error_document,
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


def raise_for_fatal_preflight(result: Mapping[str, object]) -> None:
    """Reject configuration or authentication failures before concurrent execution."""
    error = cast(Mapping[str, object], result.get("error") or {})
    status_code = int(str(error.get("status_code", 0)))
    if status_code not in {400, 401, 403}:
        return
    msg = f"Responses preflight failed: {error.get('message', '')}"
    raise RuntimeError(msg)


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


def _request_configuration(requests: Mapping[str, Mapping[str, object]]) -> tuple[str, str, int]:
    configurations = set()
    for request in requests.values():
        body = cast(Mapping[str, object], request.get("body") or {})
        reasoning = cast(Mapping[str, object], body.get("reasoning") or {})
        configurations.add(
            (
                str(body.get("model", "")),
                str(reasoning.get("effort", "")),
                int(str(body.get("max_output_tokens", 0))),
            ),
        )
    if len(configurations) != 1:
        msg = "prepared Responses requests must use one model, reasoning effort, and output-token limit"
        raise ValueError(msg)
    return configurations.pop()


def _ensure_execution_configuration(
    *,
    output_dir: Path,
    input_path: Path,
    checkpoint_path: Path,
    provider: str,
    region: str | None,
    requested_model: str,
    provider_model: str,
    reasoning_effort: str,
    max_output_tokens: int,
    workers: int,
) -> None:
    path = output_dir / _EXECUTION_FILENAME
    expected = {
        "schema_version": 2,
        "provider": provider,
        "region": region,
        "requested_model": requested_model,
        "provider_model": provider_model,
        "reasoning_effort": reasoning_effort,
        "max_output_tokens": max_output_tokens,
        "workers": workers,
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
    }
    if not path.exists():
        if checkpoint_path.exists():
            msg = f"cannot verify provider for existing checkpoint without {path.name}"
            raise ValueError(msg)
        _write_json(path, expected)
        return
    actual = cast(Mapping[str, object], json.loads(path.read_text(encoding="utf-8")))
    if actual != expected:
        msg = f"Responses execution configuration does not match {path}"
        raise ValueError(msg)


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
