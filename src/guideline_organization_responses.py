"""Structured Responses stages for project-rule organization."""

import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import cast

import guideline
import guideline_organization_io
import guideline_organization_model
import markdown_classification
import markdown_responses_runner
import openai_responses_client
import responses_provider

_ROOT = Path(__file__).resolve().parents[1]
_CLASSIFICATION_PROMPT_PATH = _ROOT / "prompts" / "markdown_file_classification.md"
_EXTRACTION_PROMPT_PATH = _ROOT / "prompts" / "guideline_rule_extraction.md"
_EXTRACTION_SCHEMA_PATH = _ROOT / "prompts" / "guideline_rule_extraction_schema.json"
_JUDGMENT_PROMPT_PATH = _ROOT / "prompts" / "guideline_rule_judgment.md"
_JUDGMENT_SCHEMA_PATH = _ROOT / "prompts" / "guideline_rule_judgment_schema.json"
_STAGES = frozenset({"extraction", "judgment"})


def run_stage(
    *,
    output_dir: Path,
    stage: str,
    client: markdown_responses_runner.ResponsesClient,
    provider: str,
    region: str | None,
    workers: int,
) -> Mapping[str, object]:
    """Run one prepared organization stage with resumable raw checkpoints."""
    if stage not in _STAGES:
        msg = f"unsupported organization stage: {stage}"
        raise ValueError(msg)
    stage_dir = output_dir / stage
    input_path = stage_dir / "batch_input.jsonl"
    if input_path.is_file() and not input_path.read_text(encoding="utf-8").strip():
        return _complete_empty_stage(
            output_dir=output_dir,
            stage_dir=stage_dir,
            provider=provider,
            region=region,
            workers=workers,
        )
    return markdown_responses_runner.run_prepared_responses(
        output_dir=stage_dir,
        client=client,
        provider=provider,
        region=region,
        workers=workers,
        collect_results=_collect_stage_results,
        validate_response=_stage_response_validator(output_dir, stage=stage),
    )


def extraction_request(
    document: guideline_organization_model.SourceDocument,
    *,
    model: str,
    reasoning_effort: str,
    max_output_tokens: int,
) -> dict[str, object]:
    """Build one exhaustive project-rule extraction request."""
    input_document = {
        "source_id": document.source_id,
        "repository": document.repository,
        "revision": document.revision,
        "file": document.file,
        "github_url": document.github_url,
        "content": guideline_organization_io.numbered_content(document.content),
    }
    return _request(
        custom_id=document.source_id,
        input_document=input_document,
        instructions=extraction_instructions(),
        schema_name="project_rule_extraction",
        schema=extraction_schema(),
        model=model,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
    )


def judgment_request(
    source: guideline_organization_model.SourceDocument,
    candidates: list[guideline_organization_model.ExtractedCandidate],
    *,
    model: str,
    reasoning_effort: str,
    max_output_tokens: int,
) -> dict[str, object]:
    """Build one independent multi-criterion judgment request."""
    input_document = {
        "source_id": source.source_id,
        "repository": source.repository,
        "revision": source.revision,
        "file": source.file,
        "github_url": source.github_url,
        "candidates": [candidate_document(candidate) for candidate in candidates],
    }
    return _request(
        custom_id=source.source_id,
        input_document=input_document,
        instructions=judgment_instructions(),
        schema_name="project_rule_judgment",
        schema=judgment_schema(),
        model=model,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
    )


def response_values(path: Path, *, expected_ids: frozenset[str]) -> dict[str, Mapping[str, object]]:
    """Parse complete structured responses and verify exact source coverage."""
    documents = guideline_organization_io.jsonl_objects(path)
    by_id: dict[str, Mapping[str, object]] = {}
    for document in documents:
        custom_id = str(document.get("custom_id", ""))
        if not custom_id or custom_id in by_id:
            msg = f"invalid or duplicate response custom_id in {path}: {custom_id!r}"
            raise ValueError(msg)
        response = cast(Mapping[str, object], document.get("response") or {})
        if int(str(response.get("status_code", 0))) != 200:
            msg = f"response is incomplete for {custom_id}: {document.get('error')!r}"
            raise ValueError(msg)
        parsed = openai_responses_client.parse_json_response(
            cast(Mapping[str, object], response.get("body") or {}),
        )
        by_id[custom_id] = parsed.value
    actual_ids = frozenset(by_id)
    if actual_ids != expected_ids:
        missing = sorted(expected_ids.difference(actual_ids))
        extra = sorted(actual_ids.difference(expected_ids))
        msg = f"response IDs do not match sources: missing={missing!r} extra={extra!r}"
        raise ValueError(msg)
    return by_id


def extracted_candidates(
    source: guideline_organization_model.SourceDocument,
    value: Mapping[str, object],
) -> tuple[guideline_organization_model.ExtractedCandidate, ...]:
    """Validate source ranges and assign stable candidate identifiers."""
    raw_candidates = value.get("candidates")
    if not isinstance(raw_candidates, list):
        msg = f"extraction response for {source.source_id} must contain a candidates array"
        raise ValueError(msg)
    lines = source.content.split("\n")
    candidates: list[guideline_organization_model.ExtractedCandidate] = []
    identities: set[tuple[int, int, int, int, str]] = set()
    for index, raw_candidate in enumerate(raw_candidates, start=1):
        if not isinstance(raw_candidate, dict):
            msg = f"candidate {index} for {source.source_id} must be an object"
            raise ValueError(msg)
        candidate = cast(Mapping[str, object], raw_candidate)
        evidence_start = _line_number(candidate, "evidence_start_line")
        evidence_end = _line_number(candidate, "evidence_end_line")
        context_start = _line_number(candidate, "context_start_line")
        context_end = _line_number(candidate, "context_end_line")
        if not (1 <= context_start <= evidence_start <= evidence_end <= context_end <= len(lines)):
            msg = f"candidate {index} for {source.source_id} has invalid or non-enclosing line ranges"
            raise ValueError(msg)
        constraint = str(candidate.get("constraint", "")).strip()
        if not constraint:
            msg = f"candidate {index} for {source.source_id} has an empty constraint"
            raise ValueError(msg)
        identity = evidence_start, evidence_end, context_start, context_end, constraint
        if identity in identities:
            msg = f"duplicate extracted candidate for {source.source_id}: {identity!r}"
            raise ValueError(msg)
        identities.add(identity)
        candidates.append(
            guideline_organization_model.ExtractedCandidate(
                candidate_id=f"{source.source_id}-rule-{index:03d}",
                source=source,
                evidence_start_line=evidence_start,
                evidence_end_line=evidence_end,
                context_start_line=context_start,
                context_end_line=context_end,
                evidence_quote=_line_quote(lines, evidence_start, evidence_end),
                context_quote=_line_quote(lines, context_start, context_end),
                constraint=constraint,
            ),
        )
    return tuple(candidates)


def candidate_document(candidate: guideline_organization_model.ExtractedCandidate) -> dict[str, object]:
    """Serialize one candidate with complete source provenance."""
    return {
        "candidate_id": candidate.candidate_id,
        "source_id": candidate.source.source_id,
        "repository": candidate.source.repository,
        "revision": candidate.source.revision,
        "file": candidate.source.file,
        "github_url": candidate.source.github_url,
        "evidence_start_line": candidate.evidence_start_line,
        "evidence_end_line": candidate.evidence_end_line,
        "evidence_quote": candidate.evidence_quote,
        "context_start_line": candidate.context_start_line,
        "context_end_line": candidate.context_end_line,
        "context_quote": candidate.context_quote,
        "constraint": candidate.constraint,
    }


def candidate_documents_by_id(path: Path) -> dict[str, Mapping[str, object]]:
    """Load unique, materialized extraction candidates."""
    candidates: dict[str, Mapping[str, object]] = {}
    for candidate in guideline_organization_io.jsonl_objects(path):
        candidate_id = str(candidate.get("candidate_id", ""))
        if not candidate_id or candidate_id in candidates:
            msg = f"invalid or duplicate candidate_id in {path}: {candidate_id!r}"
            raise ValueError(msg)
        candidates[candidate_id] = candidate
    return candidates


def validated_judgments(
    value: Mapping[str, object],
    *,
    expected_candidate_ids: tuple[str, ...],
    candidates: Mapping[str, Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """Validate exact candidate coverage and calculate deterministic decisions."""
    raw_judgments = value.get("judgments")
    if not isinstance(raw_judgments, list):
        msg = "judgment response must contain a judgments array"
        raise ValueError(msg)
    judgment_objects: list[Mapping[str, object]] = []
    for raw_judgment in raw_judgments:
        if not isinstance(raw_judgment, dict):
            msg = "each judgment must be an object"
            raise ValueError(msg)
        judgment_objects.append(cast(Mapping[str, object], raw_judgment))
    actual_ids = tuple(str(judgment.get("candidate_id", "")) for judgment in judgment_objects)
    if actual_ids != expected_candidate_ids:
        msg = (
            "judgment candidate IDs do not match input order: "
            f"expected={expected_candidate_ids!r} actual={actual_ids!r}"
        )
        raise ValueError(msg)
    rows: list[dict[str, object]] = []
    for judgment in judgment_objects:
        candidate_id = str(judgment["candidate_id"])
        flags = {
            field: _judgment_flag(judgment, field, candidate_id=candidate_id)
            for field in guideline_organization_model.JUDGMENT_FLAGS
        }
        reason = str(judgment.get("reason", "")).strip()
        if not reason:
            msg = f"judgment reason is empty for {candidate_id}"
            raise ValueError(msg)
        rows.append(
            {
                **candidates[candidate_id],
                **flags,
                "llm_decision": "pass" if all(flags.values()) else "not_found",
                "llm_reason": reason,
            },
        )
    return tuple(rows)


@cache
def project_rule_definition() -> str:
    """Return the current classifier's evidence and rule-definition sections verbatim."""
    prompt = _CLASSIFICATION_PROMPT_PATH.read_text(encoding="utf-8")
    try:
        start = prompt.index("## Evidence\n")
        end = prompt.index("## Output\n", start)
    except ValueError as error:
        msg = f"classification prompt lacks definition boundary markers: {_CLASSIFICATION_PROMPT_PATH}"
        raise ValueError(msg) from error
    return prompt[start:end].rstrip()


@cache
def extraction_instructions() -> str:
    """Return extraction instructions with the shared project-rule definition."""
    return _instructions(_EXTRACTION_PROMPT_PATH)


@cache
def extraction_schema() -> Mapping[str, object]:
    """Return the strict extraction response schema."""
    return _schema(_EXTRACTION_SCHEMA_PATH)


@cache
def judgment_instructions() -> str:
    """Return independent judgment instructions with the shared project-rule definition."""
    return _instructions(_JUDGMENT_PROMPT_PATH)


@cache
def judgment_schema() -> Mapping[str, object]:
    """Return the strict independent-judgment response schema."""
    return _schema(_JUDGMENT_SCHEMA_PATH)


def _stage_response_validator(
    output_dir: Path,
    *,
    stage: str,
) -> markdown_responses_runner.ResponseValidator:
    if stage == "extraction":
        sources = guideline_organization_io.manifest_sources(output_dir / "source_manifest.csv")

        def validate_extraction(custom_id: str, response: openai_responses_client.JsonResponse) -> None:
            if custom_id not in sources:
                msg = f"extraction response references an unknown source: {custom_id}"
                raise ValueError(msg)
            extracted_candidates(sources[custom_id], response.value)

        return validate_extraction
    candidates = candidate_documents_by_id(output_dir / "extraction" / "candidates.jsonl")
    candidate_ids_by_source: dict[str, list[str]] = {}
    for candidate_id, candidate in candidates.items():
        candidate_ids_by_source.setdefault(str(candidate["source_id"]), []).append(candidate_id)

    def validate_judgment(custom_id: str, response: openai_responses_client.JsonResponse) -> None:
        expected_ids = candidate_ids_by_source.get(custom_id)
        if expected_ids is None:
            msg = f"judgment response references an unknown source: {custom_id}"
            raise ValueError(msg)
        validated_judgments(
            response.value,
            expected_candidate_ids=tuple(expected_ids),
            candidates=candidates,
        )

    return validate_judgment


def _complete_empty_stage(
    *,
    output_dir: Path,
    stage_dir: Path,
    provider: str,
    region: str | None,
    workers: int,
) -> Mapping[str, object]:
    configuration = guideline_organization_io.json_object(output_dir / "run_configuration.json")
    report = dict(
        _collect_stage_results(
            output_dir=stage_dir,
            output_content=b"",
            error_content=b"",
            provider=provider,
        ),
    )
    requested_model = str(configuration["model"])
    report.update(
        {
            "provider": provider,
            "region": region,
            "requested_model": requested_model,
            "provider_model": responses_provider.model_id(provider, requested_model),
            "reasoning_effort": str(configuration["reasoning_effort"]),
            "max_output_tokens": int(str(configuration["max_output_tokens"])),
            "requested": 0,
            "attempted": 0,
            "resumed": 0,
            "workers": workers,
            "elapsed_seconds": 0.0,
            "request_seconds": 0.0,
        },
    )
    (stage_dir / "responses_checkpoint.jsonl").touch()
    guideline_organization_io.write_json(stage_dir / "cost_summary.json", report)
    guideline_organization_io.write_json(stage_dir / "responses_run.json", report)
    return report


def _collect_stage_results(
    *,
    output_dir: Path,
    output_content: bytes,
    error_content: bytes,
    provider: str,
) -> Mapping[str, object]:
    output_path = output_dir / "responses_output.jsonl"
    error_path = output_dir / "responses_errors.jsonl"
    output_path.write_bytes(output_content)
    error_path.write_bytes(error_content)
    outputs = _unique_documents(output_content, source=output_path)
    errors = _unique_documents(error_content, source=error_path)
    if outputs.keys() & errors.keys():
        msg = "organization Responses output and error IDs overlap"
        raise ValueError(msg)
    pricing = responses_provider.pricing(provider)
    rows: list[dict[str, object]] = []
    provider_costs: list[float] = []
    for custom_id, output in outputs.items():
        response = cast(Mapping[str, object], output.get("response") or {})
        parsed = openai_responses_client.parse_json_response(
            cast(Mapping[str, object], response.get("body") or {}),
        )
        calculated_cost = markdown_classification.request_cost(
            parsed.usage,
            input_usd_per_million_tokens=pricing.input_usd_per_million_tokens,
            cached_input_usd_per_million_tokens=pricing.cached_input_usd_per_million_tokens,
            cache_write_input_usd_per_million_tokens=pricing.cache_write_input_usd_per_million_tokens,
            output_usd_per_million_tokens=pricing.output_usd_per_million_tokens,
        )
        if parsed.cost_usd is not None:
            provider_costs.append(parsed.cost_usd)
        rows.append(
            _usage_row(
                custom_id,
                status="completed",
                usage=parsed.usage,
                cost_usd=parsed.cost_usd if parsed.cost_usd is not None else calculated_cost,
                error="",
            ),
        )
    for custom_id, error in errors.items():
        rows.append(
            _usage_row(
                custom_id,
                status="model_error",
                usage=None,
                cost_usd=0.0,
                error=json.dumps(error.get("error") or {}, ensure_ascii=True, sort_keys=True),
            ),
        )
    ordered_rows = tuple(sorted(rows, key=lambda row: str(row["custom_id"])))
    guideline_organization_io.write_csv(
        output_dir / "response_results.csv",
        ordered_rows,
        fieldnames=_usage_fieldnames(),
    )
    return _cost_report(ordered_rows, provider_costs=provider_costs, output_count=len(outputs), provider=provider)


def _cost_report(
    rows: tuple[dict[str, object], ...],
    *,
    provider_costs: list[float],
    output_count: int,
    provider: str,
) -> Mapping[str, object]:
    pricing = responses_provider.pricing(provider)
    completed = tuple(row for row in rows if row["status"] == "completed")
    provider_cost = round(sum(provider_costs), 9) if len(provider_costs) == output_count else None
    calculated_cost = round(sum(float(str(row["cost_usd"])) for row in completed), 6)
    return {
        "sampled": len(rows),
        "completed": len(completed),
        "errors": len(rows) - len(completed),
        "status_counts": {"completed": len(completed), "model_error": len(rows) - len(completed)},
        "input_tokens": sum(int(str(row["input_tokens"])) for row in rows),
        "uncached_input_tokens": sum(int(str(row["uncached_input_tokens"])) for row in rows),
        "cached_input_tokens": sum(int(str(row["cached_input_tokens"])) for row in rows),
        "cache_write_input_tokens": sum(int(str(row["cache_write_input_tokens"])) for row in rows),
        "output_tokens": sum(int(str(row["output_tokens"])) for row in rows),
        "calculated_pilot_cost_usd": calculated_cost,
        "provider_reported_cost_usd": provider_cost,
        "pilot_cost_usd": provider_cost if provider_cost is not None else calculated_cost,
        "average_completed_cost_usd": round(calculated_cost / len(completed), 9) if completed else 0.0,
        "short_context_requests": sum(
            int(str(row["input_tokens"])) <= markdown_classification.LONG_CONTEXT_THRESHOLD for row in completed
        ),
        "long_context_requests": sum(
            int(str(row["input_tokens"])) > markdown_classification.LONG_CONTEXT_THRESHOLD for row in completed
        ),
        "estimated_full_batch_usd": None,
        "cost_source": "provider_reported" if provider_cost is not None else pricing.source,
        "pricing_date": pricing.date,
        "input_usd_per_million_tokens": pricing.input_usd_per_million_tokens,
        "cached_input_usd_per_million_tokens": pricing.cached_input_usd_per_million_tokens,
        "cache_write_input_usd_per_million_tokens": pricing.cache_write_input_usd_per_million_tokens,
        "output_usd_per_million_tokens": pricing.output_usd_per_million_tokens,
    }


def _request(
    *,
    custom_id: str,
    input_document: Mapping[str, object],
    instructions: str,
    schema_name: str,
    schema: Mapping[str, object],
    model: str,
    reasoning_effort: str,
    max_output_tokens: int,
) -> dict[str, object]:
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/responses",
        "body": {
            "model": model,
            "instructions": instructions,
            "input": json.dumps(input_document, ensure_ascii=False, sort_keys=True),
            "reasoning": {"effort": reasoning_effort},
            "max_output_tokens": max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        },
    }


def _instructions(path: Path) -> str:
    task = path.read_text(encoding="utf-8").rstrip()
    return f"{task}\n\n{project_rule_definition()}\n"


def _schema(path: Path) -> Mapping[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        msg = f"{path} must contain a JSON object"
        raise ValueError(msg)
    return cast(dict[str, object], document)


def _line_number(candidate: Mapping[str, object], field: str) -> int:
    value = candidate.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{field} must be an integer"
        raise ValueError(msg)
    return value


def _line_quote(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1 : end])


def _judgment_flag(judgment: Mapping[str, object], field: str, *, candidate_id: str) -> bool:
    value = judgment.get(field)
    if not isinstance(value, bool):
        msg = f"judgment {field} must be Boolean for {candidate_id}"
        raise ValueError(msg)
    return value


def _unique_documents(content: bytes, *, source: Path) -> dict[str, Mapping[str, object]]:
    documents = tuple(
        cast(Mapping[str, object], json.loads(line)) for line in content.decode("utf-8").splitlines() if line.strip()
    )
    by_id: dict[str, Mapping[str, object]] = {}
    for document in documents:
        custom_id = str(document.get("custom_id", ""))
        if not custom_id or custom_id in by_id:
            msg = f"invalid or duplicate custom_id in {source}: {custom_id!r}"
            raise ValueError(msg)
        by_id[custom_id] = document
    return by_id


def _usage_row(
    custom_id: str,
    *,
    status: str,
    usage: guideline.TokenUsage | None,
    cost_usd: float,
    error: str,
) -> dict[str, object]:
    actual = usage or guideline.TokenUsage()
    return {
        "custom_id": custom_id,
        "status": status,
        "input_tokens": actual.input_tokens,
        "uncached_input_tokens": actual.uncached_input_tokens,
        "cached_input_tokens": actual.cached_input_tokens,
        "cache_write_input_tokens": actual.cache_write_input_tokens,
        "output_tokens": actual.output_tokens,
        "total_tokens": actual.total_tokens,
        "cost_usd": cost_usd,
        "error": error,
    }


def _usage_fieldnames() -> tuple[str, ...]:
    return (
        "custom_id",
        "status",
        "input_tokens",
        "uncached_input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "total_tokens",
        "cost_usd",
        "error",
    )
