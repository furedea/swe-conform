"""Orchestrate two-stage extraction and independent project-rule judgment."""

from collections.abc import Mapping
from pathlib import Path

import guideline_organization_io
import guideline_organization_model
import guideline_organization_reports
import guideline_organization_responses
import markdown_responses_runner

OrganizationFinalization = guideline_organization_model.OrganizationFinalization
OrganizationPreparation = guideline_organization_model.OrganizationPreparation
JudgmentPreparation = guideline_organization_model.JudgmentPreparation

_ROOT = Path(__file__).resolve().parents[1]
_EXTRACTION_SCHEMA_PATH = _ROOT / "prompts" / "guideline_rule_extraction_schema.json"
_JUDGMENT_SCHEMA_PATH = _ROOT / "prompts" / "guideline_rule_judgment_schema.json"


def prepare_extraction(
    *,
    guideline_files_path: Path,
    repository_list_path: Path,
    source_root: Path,
    output_dir: Path,
    expected_repositories: int,
    model: str,
    reasoning_effort: str,
    max_output_tokens: int,
) -> OrganizationPreparation:
    """Resolve selected repositories and prepare one extraction request per file."""
    if expected_repositories < 1:
        msg = "expected_repositories must be at least 1"
        raise ValueError(msg)
    guideline_organization_io.require_new_output_directory(output_dir)
    repositories = guideline_organization_io.selected_repositories(repository_list_path)
    if len(repositories) != expected_repositories:
        msg = f"expected {expected_repositories} repositories, found {len(repositories)}"
        raise ValueError(msg)
    documents = guideline_organization_io.source_documents(
        guideline_files_path,
        selected=repositories,
        source_root=source_root,
    )

    output_dir.mkdir(parents=True)
    extraction_dir = output_dir / "extraction"
    extraction_dir.mkdir()
    guideline_organization_io.write_source_manifest(output_dir / "source_manifest.csv", documents)
    guideline_organization_io.write_jsonl(
        extraction_dir / "batch_input.jsonl",
        tuple(
            guideline_organization_responses.extraction_request(
                document,
                model=model,
                reasoning_effort=reasoning_effort,
                max_output_tokens=max_output_tokens,
            )
            for document in documents
        ),
    )
    guideline_organization_io.write_json(
        output_dir / "run_configuration.json",
        _run_configuration(
            guideline_files_path=guideline_files_path,
            repository_list_path=repository_list_path,
            source_root=source_root,
            repository_count=len(repositories),
            file_count=len(documents),
            model=model,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
        ),
    )
    return OrganizationPreparation(
        repositories=len(repositories),
        files=len(documents),
        output_dir=output_dir,
    )


def prepare_judgment(*, output_dir: Path) -> JudgmentPreparation:
    """Validate extraction results and prepare one judgment request per source."""
    configuration = guideline_organization_io.json_object(output_dir / "run_configuration.json")
    sources = guideline_organization_io.manifest_sources(output_dir / "source_manifest.csv")
    values = guideline_organization_responses.response_values(
        output_dir / "extraction" / "responses_output.jsonl",
        expected_ids=frozenset(sources),
    )
    candidates = tuple(
        candidate
        for source_id, source in sources.items()
        for candidate in guideline_organization_responses.extracted_candidates(source, values[source_id])
    )

    judgment_dir = output_dir / "judgment"
    if judgment_dir.exists():
        msg = f"judgment directory already exists: {judgment_dir}"
        raise FileExistsError(msg)
    judgment_dir.mkdir()
    candidate_path = output_dir / "extraction" / "candidates.jsonl"
    guideline_organization_io.write_jsonl(
        candidate_path,
        tuple(guideline_organization_responses.candidate_document(candidate) for candidate in candidates),
    )
    candidates_by_source = _candidates_by_source(candidates)
    requests = tuple(
        guideline_organization_responses.judgment_request(
            sources[source_id],
            source_candidates,
            model=str(configuration["model"]),
            reasoning_effort=str(configuration["reasoning_effort"]),
            max_output_tokens=int(str(configuration["max_output_tokens"])),
        )
        for source_id, source_candidates in candidates_by_source.items()
    )
    guideline_organization_io.write_jsonl(judgment_dir / "batch_input.jsonl", requests)
    guideline_organization_io.write_json(
        judgment_dir / "preparation.json",
        {
            "schema_version": 1,
            "source_count": len(sources),
            "candidate_count": len(candidates),
            "request_count": len(requests),
            "candidate_sha256": guideline_organization_io.file_sha256(candidate_path),
            "project_rule_definition_sha256": guideline_organization_io.sha256(
                guideline_organization_responses.project_rule_definition().encode(),
            ),
            "judgment_prompt_sha256": guideline_organization_io.sha256(
                guideline_organization_responses.judgment_instructions().encode(),
            ),
            "judgment_schema_sha256": guideline_organization_io.file_sha256(_JUDGMENT_SCHEMA_PATH),
        },
    )
    return JudgmentPreparation(
        sources=len(sources),
        candidates=len(candidates),
        output_dir=output_dir,
    )


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
    return guideline_organization_responses.run_stage(
        output_dir=output_dir,
        stage=stage,
        client=client,
        provider=provider,
        region=region,
        workers=workers,
    )


def finalize_organization(*, output_dir: Path) -> OrganizationFinalization:
    """Materialize deterministic accepted, rejected, and human-review outputs."""
    return guideline_organization_reports.finalize_organization(output_dir=output_dir)


def _candidates_by_source(
    candidates: tuple[guideline_organization_model.ExtractedCandidate, ...],
) -> dict[str, list[guideline_organization_model.ExtractedCandidate]]:
    grouped: dict[str, list[guideline_organization_model.ExtractedCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.source.source_id, []).append(candidate)
    return grouped


def _run_configuration(
    *,
    guideline_files_path: Path,
    repository_list_path: Path,
    source_root: Path,
    repository_count: int,
    file_count: int,
    model: str,
    reasoning_effort: str,
    max_output_tokens: int,
) -> Mapping[str, object]:
    return {
        "schema_version": 1,
        "workflow": "two_stage_extract_then_judge",
        "guideline_files_csv": str(guideline_files_path.resolve()),
        "guideline_files_csv_sha256": guideline_organization_io.file_sha256(guideline_files_path),
        "repository_list_csv": str(repository_list_path.resolve()),
        "repository_list_csv_sha256": guideline_organization_io.file_sha256(repository_list_path),
        "source_root": str(source_root.resolve()),
        "repository_count": repository_count,
        "file_count": file_count,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "max_output_tokens": max_output_tokens,
        "project_rule_definition_sha256": guideline_organization_io.sha256(
            guideline_organization_responses.project_rule_definition().encode(),
        ),
        "extraction_prompt_sha256": guideline_organization_io.sha256(
            guideline_organization_responses.extraction_instructions().encode(),
        ),
        "extraction_schema_sha256": guideline_organization_io.file_sha256(_EXTRACTION_SCHEMA_PATH),
    }
