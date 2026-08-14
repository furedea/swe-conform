"""Tests for resumable Markdown candidate extraction execution."""

from pathlib import Path

from pytest_mock import MockerFixture

import markdown_candidate_extraction
import markdown_candidate_store
import markdown_filename_audit
import repository
import repository_cache


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


def test_candidate_extraction_skips_incomplete_snapshots_without_scanning(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate = _candidate(0)
    store = markdown_candidate_store.MarkdownCandidateStore(tmp_path, configuration={"cache_only": True})
    store.initialize()
    auditor = mocker.Mock(spec=markdown_filename_audit.MarkdownFilenameAuditor)
    inspector = mocker.Mock()
    inspector.inspect_snapshot.return_value = repository_cache.SnapshotInspection(
        repository_cache.SnapshotState.SNAPSHOT_INCOMPLETE,
    )

    stats = markdown_candidate_extraction.run_candidate_extraction(
        (candidate,),
        auditor=auditor,
        store=store,
        workers=1,
        snapshot_inspector=inspector,
        skip_incomplete_repositories=True,
    )

    auditor.audit.assert_not_called()
    assert store.report().results[0].status is markdown_filename_audit.MarkdownFilenameAuditStatus.SNAPSHOT_INCOMPLETE
    assert stats.incomplete_repositories == 1
    assert stats.processed_repositories == 0


def test_candidate_extraction_records_snapshot_inspection_failures_without_stopping(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate = _candidate(0)
    store = markdown_candidate_store.MarkdownCandidateStore(tmp_path, configuration={"cache_only": True})
    store.initialize()
    auditor = mocker.Mock(spec=markdown_filename_audit.MarkdownFilenameAuditor)
    inspector = mocker.Mock()
    inspector.inspect_snapshot.side_effect = repository_cache.RepositoryCacheError("inspection timed out")

    stats = markdown_candidate_extraction.run_candidate_extraction(
        (candidate,),
        auditor=auditor,
        store=store,
        workers=1,
        snapshot_inspector=inspector,
        skip_incomplete_repositories=True,
    )

    auditor.audit.assert_not_called()
    result = store.report().results[0]
    assert result.status is markdown_filename_audit.MarkdownFilenameAuditStatus.SNAPSHOT_INCOMPLETE
    assert result.error == "RepositoryCacheError: inspection timed out"
    assert stats.incomplete_repositories == 1


def test_candidate_extraction_collapses_duplicate_repository_revisions(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate = _candidate(0)
    store = markdown_candidate_store.MarkdownCandidateStore(tmp_path, configuration={"cache_only": True})
    store.initialize()
    auditor = mocker.Mock(spec=markdown_filename_audit.MarkdownFilenameAuditor)
    auditor.audit.return_value = _result(candidate, markdown_filename_audit.MarkdownFilenameAuditStatus.COMPLETED)

    stats = markdown_candidate_extraction.run_candidate_extraction(
        (candidate, candidate),
        auditor=auditor,
        store=store,
        workers=2,
    )

    auditor.audit.assert_called_once_with(candidate)
    assert stats.requested == 1


def test_candidate_extraction_skips_explicitly_excluded_repositories(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    candidate = _candidate(0)
    store = markdown_candidate_store.MarkdownCandidateStore(tmp_path, configuration={"cache_only": True})
    store.initialize()
    auditor = mocker.Mock(spec=markdown_filename_audit.MarkdownFilenameAuditor)

    stats = markdown_candidate_extraction.run_candidate_extraction(
        (candidate,),
        auditor=auditor,
        store=store,
        workers=1,
        excluded_repositories=(candidate.repository,),
    )

    auditor.audit.assert_not_called()
    assert stats.explicitly_excluded_repositories == 1
    assert stats.processed_repositories == 0


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
