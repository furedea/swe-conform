"""Tests for resumable Markdown candidate extraction execution."""

from pathlib import Path

from pytest_mock import MockerFixture

import markdown_candidate_extraction
import markdown_candidate_store
import markdown_filename_audit
import repository


def test_candidate_extraction_skips_completed_repositories_and_retries_failures(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidates = (_candidate(0), _candidate(1))
    store = markdown_candidate_store.MarkdownCandidateStore(tmp_path, configuration={"cache_only": True})
    store.initialize()
    store.append(_result(candidates[0], markdown_filename_audit.MarkdownFilenameAuditStatus.COMPLETED))
    store.append(_result(candidates[1], markdown_filename_audit.MarkdownFilenameAuditStatus.RETRIEVAL_ERROR))
    auditor = mocker.Mock(spec=markdown_filename_audit.MarkdownFilenameAuditor)
    auditor.audit.return_value = _result(candidates[1], markdown_filename_audit.MarkdownFilenameAuditStatus.COMPLETED)

    stats = markdown_candidate_extraction.run_candidate_extraction(
        candidates,
        auditor=auditor,
        store=store,
        workers=2,
    )

    auditor.audit.assert_called_once_with(candidates[1])
    assert stats.requested == 2
    assert stats.skipped == 1
    assert stats.evaluated == 1
    assert store.completed_repositories() == {
        (candidates[0].repository, candidates[0].revision),
        (candidates[1].repository, candidates[1].revision),
    }
    assert (tmp_path / "markdown_filename_files.csv").exists()


def _candidate(index: int) -> repository.RepositoryCandidate:
    return repository.RepositoryCandidate(
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


def _result(
    candidate: repository.RepositoryCandidate,
    status: markdown_filename_audit.MarkdownFilenameAuditStatus,
) -> markdown_filename_audit.RepositoryMarkdownFilenameAudit:
    return markdown_filename_audit.RepositoryMarkdownFilenameAudit(
        candidate=candidate,
        status=status,
        error="missing revision"
        if status is not markdown_filename_audit.MarkdownFilenameAuditStatus.COMPLETED
        else "",
    )
