"""Tests for resumable Markdown candidate extraction results."""

from pathlib import Path

import markdown_candidate_store
import markdown_filename_audit
import repository


def test_candidate_store_resumes_only_completed_repository_audits(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    completed = _result(0, status=markdown_filename_audit.MarkdownFilenameAuditStatus.COMPLETED)
    failed = _result(1, status=markdown_filename_audit.MarkdownFilenameAuditStatus.RETRIEVAL_ERROR)
    store = markdown_candidate_store.MarkdownCandidateStore(output_dir, configuration={"cache_only": True})
    store.initialize()
    store.append(completed)
    store.append(failed)

    resumed = markdown_candidate_store.MarkdownCandidateStore(output_dir, configuration={"cache_only": True})
    resumed.initialize()

    assert resumed.completed_repositories() == {("example/project-0", f"{1:040x}")}
    assert resumed.report().results == (completed, failed)


def _result(
    index: int,
    *,
    status: markdown_filename_audit.MarkdownFilenameAuditStatus,
) -> markdown_filename_audit.RepositoryMarkdownFilenameAudit:
    candidate = repository.RepositoryCandidate(
        repository=f"example/project-{index}",
        revision=f"{index + 1:040x}",
        license_name="MIT License",
        source_file="candidates.csv",
        input_index=index,
        fields={
            "name": f"example/project-{index}",
            "lastCommitSHA": f"{index + 1:040x}",
            "license": "MIT License",
        },
    )
    files = (
        markdown_filename_audit.MarkdownFilenameFile(
            path="CONTRIBUTING.md",
            matched_terms=("contributing",),
            matched_content_terms=("guideline",),
            blob_sha=f"{index + 11:040x}",
            size_bytes=42,
        ),
    )
    return markdown_filename_audit.RepositoryMarkdownFilenameAudit(
        candidate=candidate,
        status=status,
        filename_files=files if status is markdown_filename_audit.MarkdownFilenameAuditStatus.COMPLETED else (),
        error="missing revision"
        if status is not markdown_filename_audit.MarkdownFilenameAuditStatus.COMPLETED
        else "",
    )
