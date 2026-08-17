"""Deterministic outputs for independently judged project-rule candidates."""

from collections.abc import Mapping
from pathlib import Path

import guideline_organization_io
import guideline_organization_model
import guideline_organization_responses


def finalize_organization(*, output_dir: Path) -> guideline_organization_model.OrganizationFinalization:
    """Validate every judgment and materialize accepted, rejected, and review outputs."""
    final_dir = output_dir / "final"
    review_dir = output_dir / "manual-review"
    if final_dir.exists() or review_dir.exists():
        msg = f"organization final outputs already exist below {output_dir}"
        raise FileExistsError(msg)

    sources = guideline_organization_io.manifest_sources(output_dir / "source_manifest.csv")
    candidates = guideline_organization_responses.candidate_documents_by_id(
        output_dir / "extraction" / "candidates.jsonl",
    )
    candidate_ids_by_source = _candidate_ids_by_source(candidates, sources=frozenset(sources))
    values = guideline_organization_responses.response_values(
        output_dir / "judgment" / "responses_output.jsonl",
        expected_ids=frozenset(candidate_ids_by_source),
    )
    judgments = tuple(
        judgment
        for source_id, candidate_ids in candidate_ids_by_source.items()
        for judgment in guideline_organization_responses.validated_judgments(
            values[source_id],
            expected_candidate_ids=tuple(candidate_ids),
            candidates=candidates,
        )
    )
    accepted = tuple(row for row in judgments if row["llm_decision"] == "pass")
    rejected = tuple(row for row in judgments if row["llm_decision"] == "not_found")

    final_dir.mkdir()
    review_dir.mkdir()
    guideline_organization_io.write_jsonl(output_dir / "judgment" / "judgments.jsonl", judgments)
    guideline_organization_io.write_jsonl(final_dir / "accepted_rules.jsonl", accepted)
    guideline_organization_io.write_csv(
        final_dir / "accepted_rules.csv",
        accepted,
        fieldnames=result_fieldnames(),
    )
    guideline_organization_io.write_csv(
        final_dir / "rejected_candidates.csv",
        rejected,
        fieldnames=result_fieldnames(),
    )
    _write_candidate_checklist(review_dir / "candidate_checklist.csv", judgments)
    _write_file_checklist(
        review_dir / "file_checklist.csv",
        sources=tuple(sources.values()),
        judgments=judgments,
    )
    _write_summary(
        final_dir / "summary.json",
        sources=tuple(sources.values()),
        judgments=judgments,
        accepted=accepted,
        rejected=rejected,
        sources_with_candidates=frozenset(candidate_ids_by_source),
    )
    return guideline_organization_model.OrganizationFinalization(
        sources=len(sources),
        candidates=len(judgments),
        accepted=len(accepted),
        rejected=len(rejected),
        output_dir=output_dir,
    )


def result_fieldnames() -> tuple[str, ...]:
    """Return the stable columns shared by machine and human-review outputs."""
    return (
        "candidate_id",
        "source_id",
        "repository",
        "revision",
        "file",
        "github_url",
        "evidence_start_line",
        "evidence_end_line",
        "evidence_quote",
        "context_start_line",
        "context_end_line",
        "context_quote",
        "constraint",
        *guideline_organization_model.JUDGMENT_FLAGS,
        "llm_decision",
        "llm_reason",
    )


def _candidate_ids_by_source(
    candidates: Mapping[str, Mapping[str, object]],
    *,
    sources: frozenset[str],
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for candidate_id, candidate in candidates.items():
        source_id = str(candidate["source_id"])
        if source_id not in sources:
            msg = f"candidate references an unknown source: {source_id}"
            raise ValueError(msg)
        grouped.setdefault(source_id, []).append(candidate_id)
    return grouped


def _write_candidate_checklist(path: Path, judgments: tuple[dict[str, object], ...]) -> None:
    rows = tuple(
        {
            **judgment,
            "human_decision": "",
            "human_constraint": "",
            "human_reason": "",
        }
        for judgment in judgments
    )
    guideline_organization_io.write_csv(
        path,
        rows,
        fieldnames=(*result_fieldnames(), "human_decision", "human_constraint", "human_reason"),
    )


def _write_file_checklist(
    path: Path,
    *,
    sources: tuple[guideline_organization_model.SourceDocument, ...],
    judgments: tuple[dict[str, object], ...],
) -> None:
    extracted_counts = {source.source_id: 0 for source in sources}
    accepted_counts = {source.source_id: 0 for source in sources}
    for judgment in judgments:
        source_id = str(judgment["source_id"])
        extracted_counts[source_id] += 1
        accepted_counts[source_id] += judgment["llm_decision"] == "pass"
    rows = tuple(
        {
            "source_id": source.source_id,
            "repository": source.repository,
            "revision": source.revision,
            "file": source.file,
            "github_url": source.github_url,
            "extracted_candidates": extracted_counts[source.source_id],
            "accepted_candidates": accepted_counts[source.source_id],
            "human_extraction_complete": "",
            "human_missing_constraints": "",
            "human_reason": "",
        }
        for source in sources
    )
    guideline_organization_io.write_csv(
        path,
        rows,
        fieldnames=(
            "source_id",
            "repository",
            "revision",
            "file",
            "github_url",
            "extracted_candidates",
            "accepted_candidates",
            "human_extraction_complete",
            "human_missing_constraints",
            "human_reason",
        ),
    )


def _write_summary(
    path: Path,
    *,
    sources: tuple[guideline_organization_model.SourceDocument, ...],
    judgments: tuple[dict[str, object], ...],
    accepted: tuple[dict[str, object], ...],
    rejected: tuple[dict[str, object], ...],
    sources_with_candidates: frozenset[str],
) -> None:
    repositories_with_accepted = {str(row["repository"]) for row in accepted}
    guideline_organization_io.write_json(
        path,
        {
            "schema_version": 1,
            "sources": len(sources),
            "repositories": len({source.repository for source in sources}),
            "candidates": len(judgments),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "repositories_with_accepted_rules": len(repositories_with_accepted),
            "sources_without_candidates": len(sources) - len(sources_with_candidates),
            "acceptance_rule": "in_scope && persistent && concrete && atomic && diff_closed && objective && grounded",
        },
    )
