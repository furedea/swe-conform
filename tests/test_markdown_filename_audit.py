"""Tests for Markdown filename candidate audits."""

import csv
import threading
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

import github_client
import markdown_filename_audit
import repository
import repository_tree


def _candidate() -> repository.RepositoryCandidate:
    return repository.RepositoryCandidate(
        repository="example/project",
        revision="0123456789abcdef",
        license_name="MIT License",
        source_file="candidates.csv",
        input_index=0,
        fields={
            "name": "example/project",
            "lastCommitSHA": "0123456789abcdef",
        },
    )


def _indexed_candidate(index: int) -> repository.RepositoryCandidate:
    return repository.RepositoryCandidate(
        repository=f"example/project-{index}",
        revision=f"{index + 1:040x}",
        license_name="MIT License",
        source_file="candidates.csv",
        input_index=index,
        fields={},
    )


def test_filename_terms_ignore_letter_case() -> None:
    terms = markdown_filename_audit.matched_filename_terms("CoDiNg_GuIdElInE.MD")

    assert terms == ("guideline", "coding")


@pytest.mark.parametrize(
    ("term", "filename"),
    [
        ("develop", "DEVELOPMENT.md"),
        ("develop", "Developers.md"),
        ("develop", "developing.md"),
        ("architecture", "ARCHITECTURES.md"),
        ("design", "redesign.md"),
        ("coding", "CODING.md"),
        ("hacking", "HACKING.md"),
    ],
)
def test_filename_terms_match_added_convention_name_substrings(term: str, filename: str) -> None:
    assert markdown_filename_audit.matched_filename_terms(filename) == (term,)


@pytest.mark.parametrize(
    ("term", "singular", "plural"),
    [
        ("style", "style", "styles"),
        ("guide", "guide", "guides"),
        ("guideline", "guideline", "guidelines"),
        ("standard", "standard", "standards"),
        ("convention", "convention", "conventions"),
        ("rule", "rule", "rules"),
    ],
)
def test_filename_terms_match_singular_and_plural_forms(
    term: str,
    singular: str,
    plural: str,
) -> None:
    assert markdown_filename_audit.matched_filename_terms(f"project-{singular}.md") == (term,)
    assert markdown_filename_audit.matched_filename_terms(f"project-{plural}.md") == (term,)


@pytest.mark.parametrize(
    ("term", "filename"),
    [
        ("readme", "ReAdMe-ja.md"),
        ("contributing", "CoNtRiBuTiNg.md"),
        ("agents", "AgEnTs.local.md"),
        ("claude", "ClAuDe-project.md"),
        ("copilot-instructions", "CoPiLoT-InStRuCtIoNs.md"),
    ],
)
def test_filename_terms_match_named_files_without_letter_case(
    term: str,
    filename: str,
) -> None:
    assert markdown_filename_audit.matched_filename_terms(filename) == (term,)


def test_content_terms_match_configured_forms_without_letter_case() -> None:
    content = (
        "STYLES guides Guidelines standard conventions RULES "
        "architectures designs development developers developing.\n"
    )

    terms = markdown_filename_audit.matched_content_terms(content)

    assert terms == (
        "style",
        "guide",
        "guideline",
        "standard",
        "convention",
        "rule",
        "architecture",
        "design",
        "develop",
    )


def test_scan_applies_content_terms_only_after_the_filename_filter(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    client.get_complete_tree.return_value = github_client.RepositoryTree(
        entries=(
            github_client.TreeEntry(path="README.MD", sha="blob-readme", size=10),
            github_client.TreeEntry(path="DESIGN.md", sha="blob-design", size=10),
            github_client.TreeEntry(path="CONTRIBUTING.mdx", sha="blob-mdx", size=20),
            github_client.TreeEntry(path="src/main.py", sha="blob-python", size=30),
        ),
        truncated=False,
    )
    contents = {
        "blob-design": "Runtime overview without a candidate term.\n",
        "blob-readme": "Development guidelines.\n",
    }
    client.get_text_blob.side_effect = lambda _repository, blob_sha: contents[blob_sha]

    matches = markdown_filename_audit.scan_github_markdown_filenames(
        client,
        "example/project",
        "0123456789abcdef",
    )

    assert matches == (
        markdown_filename_audit.MarkdownFilenameFile(
            path="DESIGN.md",
            matched_terms=("design",),
            matched_content_terms=(),
            blob_sha="blob-design",
            size_bytes=10,
        ),
        markdown_filename_audit.MarkdownFilenameFile(
            path="README.MD",
            matched_terms=("readme",),
            matched_content_terms=("guideline", "develop"),
            blob_sha="blob-readme",
            size_bytes=10,
        ),
    )
    client.get_complete_tree.assert_called_once_with("example/project", "0123456789abcdef")
    assert client.get_text_blob.call_args_list == [
        mocker.call("example/project", "blob-design"),
        mocker.call("example/project", "blob-readme"),
    ]


def test_scan_excludes_markdown_symlinks(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    client.get_complete_tree.return_value = github_client.RepositoryTree(
        entries=(
            github_client.TreeEntry(
                path="RULES.md",
                sha="blob-link",
                size=10,
                mode="120000",
            ),
        ),
        truncated=False,
    )

    matches = markdown_filename_audit.scan_github_markdown_filenames(
        client,
        "example/project",
        "0123456789abcdef",
    )

    assert matches == ()


def test_scan_records_the_revision_pinned_blob_identity(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    client.get_complete_tree.return_value = github_client.RepositoryTree(
        entries=(github_client.TreeEntry(path="RULES.md", sha="blob-rules", size=42),),
        truncated=False,
    )
    client.get_text_blob.return_value = "Project conventions.\n"

    matches = markdown_filename_audit.scan_github_markdown_filenames(
        client,
        "example/project",
        "0123456789abcdef",
    )

    assert matches[0].blob_sha == "blob-rules"
    assert matches[0].size_bytes == 42


def test_scan_reads_all_local_candidates_through_one_blob_batch() -> None:
    class BatchTreeClient:
        def __init__(self) -> None:
            self.batch_calls = 0

        def get_complete_tree(self, repository: str, revision: str) -> github_client.RepositoryTree:
            del repository, revision
            return github_client.RepositoryTree(
                entries=(
                    github_client.TreeEntry(path="CONTRIBUTING.md", sha="blob-contributing", size=10),
                    github_client.TreeEntry(path="README.md", sha="blob-readme", size=20),
                ),
                truncated=False,
            )

        def get_text_blob(self, repository: str, blob_sha: str) -> str:
            del repository, blob_sha
            raise AssertionError("single-blob retrieval must not be used")

        def get_text_blobs(self, repository: str, blob_shas: tuple[str, ...]) -> dict[str, str]:
            del repository
            self.batch_calls += 1
            return dict.fromkeys(blob_shas, "Coding standards.\n")

    client = BatchTreeClient()

    matches = markdown_filename_audit.scan_github_markdown_filenames(
        client,
        "example/project",
        "0123456789abcdef",
    )

    assert len(matches) == 2
    assert client.batch_calls == 1


def test_scan_ignores_candidate_terms_found_only_in_directory_names(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    client.get_complete_tree.return_value = github_client.RepositoryTree(
        entries=(
            github_client.TreeEntry(
                path="docs/guides/setup.md",
                sha="blob-setup",
                size=10,
            ),
        ),
        truncated=False,
    )

    matches = markdown_filename_audit.scan_github_markdown_filenames(
        client,
        "example/project",
        "0123456789abcdef",
    )

    assert matches == ()


def test_compare_agent_evidence_reports_filename_candidate_coverage() -> None:
    matches = (
        markdown_filename_audit.MarkdownFilenameFile(
            path="CONTRIBUTING.md",
            matched_terms=("contributing",),
            matched_content_terms=("style",),
        ),
        markdown_filename_audit.MarkdownFilenameFile(
            path="README.md",
            matched_terms=("readme",),
            matched_content_terms=(),
        ),
    )

    coverage = markdown_filename_audit.compare_agent_evidence(
        matches,
        evidence_paths=("CONTRIBUTING.md", "README.md", "docs/testing.md", "src/example.py"),
    )

    assert coverage == (
        markdown_filename_audit.AgentEvidenceFilenameCoverage(
            path="CONTRIBUTING.md",
            is_markdown=True,
            filename_match=True,
            matched_terms=("contributing",),
            content_match=True,
            matched_content_terms=("style",),
        ),
        markdown_filename_audit.AgentEvidenceFilenameCoverage(
            path="README.md",
            is_markdown=True,
            filename_match=True,
            matched_terms=("readme",),
            content_match=False,
            matched_content_terms=(),
        ),
        markdown_filename_audit.AgentEvidenceFilenameCoverage(
            path="docs/testing.md",
            is_markdown=True,
            filename_match=False,
            matched_terms=(),
            content_match=None,
            matched_content_terms=(),
        ),
        markdown_filename_audit.AgentEvidenceFilenameCoverage(
            path="src/example.py",
            is_markdown=False,
            filename_match=False,
            matched_terms=(),
            content_match=None,
            matched_content_terms=(),
        ),
    )


def test_auditor_compares_filename_candidates_with_agent_evidence(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    client.get_complete_tree.return_value = github_client.RepositoryTree(
        entries=(github_client.TreeEntry(path="README.md", sha="blob-readme", size=10),),
        truncated=False,
    )
    client.get_text_blob.return_value = "Coding standards.\n"
    auditor = markdown_filename_audit.MarkdownFilenameAuditor(
        client=client,
        agent_evidence={
            ("example/project", "0123456789abcdef"): ("README.md",),
        },
    )

    result = auditor.audit(_candidate())

    assert result.status is markdown_filename_audit.MarkdownFilenameAuditStatus.COMPLETED
    assert result.filename_files == (
        markdown_filename_audit.MarkdownFilenameFile(
            path="README.md",
            matched_terms=("readme",),
            matched_content_terms=("standard",),
            blob_sha="blob-readme",
            size_bytes=10,
        ),
    )
    assert result.agent_evidence[0].filename_match is True


def test_auditor_preserves_unevaluated_evidence_after_github_failure(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    client.get_complete_tree.side_effect = github_client.GitHubRetrievalError("unavailable")
    auditor = markdown_filename_audit.MarkdownFilenameAuditor(
        client=client,
        agent_evidence={
            ("example/project", "0123456789abcdef"): ("README.md",),
        },
    )

    result = auditor.audit(_candidate())

    assert result.status is markdown_filename_audit.MarkdownFilenameAuditStatus.RETRIEVAL_ERROR
    assert result.agent_evidence[0].filename_match is None


def test_auditor_records_a_missing_local_revision_as_a_retrieval_error(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    client.get_complete_tree.side_effect = repository_tree.CachedRepositoryTreeError("pinned revision is absent")
    auditor = markdown_filename_audit.MarkdownFilenameAuditor(client=client, agent_evidence={})

    result = auditor.audit(_candidate())

    assert result.status is markdown_filename_audit.MarkdownFilenameAuditStatus.RETRIEVAL_ERROR


def test_audit_runner_uses_the_configured_workers_concurrently(mocker: MockerFixture) -> None:
    candidates = (_indexed_candidate(0), _indexed_candidate(1))
    rendezvous = threading.Barrier(2)

    def audit(candidate: repository.RepositoryCandidate) -> markdown_filename_audit.RepositoryMarkdownFilenameAudit:
        rendezvous.wait(timeout=5)
        return markdown_filename_audit.RepositoryMarkdownFilenameAudit(
            candidate=candidate,
            status=markdown_filename_audit.MarkdownFilenameAuditStatus.COMPLETED,
        )

    auditor = mocker.Mock(spec=markdown_filename_audit.MarkdownFilenameAuditor)
    auditor.audit.side_effect = audit
    runner = markdown_filename_audit.MarkdownFilenameAuditRunner(auditor=auditor, workers=2)

    report = runner.run(candidates)

    assert report.stats.completed == 2


def test_write_reports_keeps_filename_results_separate_from_content_results(tmp_path: Path) -> None:
    result = markdown_filename_audit.RepositoryMarkdownFilenameAudit(
        candidate=_candidate(),
        status=markdown_filename_audit.MarkdownFilenameAuditStatus.COMPLETED,
        filename_files=(
            markdown_filename_audit.MarkdownFilenameFile(
                path="CONTRIBUTING.md",
                matched_terms=("contributing",),
                matched_content_terms=("style", "guideline"),
            ),
            markdown_filename_audit.MarkdownFilenameFile(
                path="DESIGN.md",
                matched_terms=("design",),
                matched_content_terms=(),
            ),
        ),
        agent_evidence=(
            markdown_filename_audit.AgentEvidenceFilenameCoverage(
                path="CONTRIBUTING.md",
                is_markdown=True,
                filename_match=True,
                matched_terms=("contributing",),
                content_match=True,
                matched_content_terms=("style", "guideline"),
            ),
        ),
    )
    report = markdown_filename_audit.MarkdownFilenameAuditReport(
        results=(result,),
        stats=markdown_filename_audit.MarkdownFilenameAuditStats(
            requested=1,
            completed=1,
            errors=0,
            elapsed_seconds=1.0,
        ),
    )

    markdown_filename_audit.write_reports(report, tmp_path)

    with (tmp_path / "markdown_filename_files.csv").open(encoding="utf-8", newline="") as input_file:
        filename_rows = list(csv.DictReader(input_file))
    assert filename_rows == [
        {
            "name": "example/project",
            "lastCommitSHA": "0123456789abcdef",
            "markdown_path": "CONTRIBUTING.md",
            "blob_sha": "",
            "size_bytes": "0",
            "markdown_url": ("https://github.com/example/project/blob/0123456789abcdef/CONTRIBUTING.md"),
            "matched_filename_terms": "contributing",
            "matched_content_terms": "style|guideline",
            "agent_evidence": "True",
        },
    ]
    with (tmp_path / "agent_evidence_filename_coverage.csv").open(
        encoding="utf-8",
        newline="",
    ) as input_file:
        coverage_rows = list(csv.DictReader(input_file))
    assert coverage_rows[0]["filename_match"] == "True"
    assert coverage_rows[0]["content_match"] == "True"
    assert coverage_rows[0]["matched_content_terms"] == "style|guideline"
    with (tmp_path / "repository_filename_summary.csv").open(
        encoding="utf-8",
        newline="",
    ) as input_file:
        summary_rows = list(csv.DictReader(input_file))
    assert summary_rows[0]["markdown_filename_file_count"] == "2"
    assert summary_rows[0]["markdown_filename_and_content_file_count"] == "1"
    assert not (tmp_path / "markdown_term_files.csv").exists()


def test_write_reports_lists_skipped_repositories_and_reasons(tmp_path: Path) -> None:
    results = (
        markdown_filename_audit.RepositoryMarkdownFilenameAudit(
            candidate=_indexed_candidate(0),
            status=markdown_filename_audit.MarkdownFilenameAuditStatus.SNAPSHOT_INCOMPLETE,
            error="snapshot_incomplete",
        ),
        markdown_filename_audit.RepositoryMarkdownFilenameAudit(
            candidate=_indexed_candidate(1),
            status=markdown_filename_audit.MarkdownFilenameAuditStatus.EXPLICITLY_EXCLUDED,
            error="explicitly_excluded",
        ),
    )
    report = markdown_filename_audit.MarkdownFilenameAuditReport(
        results=results,
        stats=markdown_filename_audit.MarkdownFilenameAuditStats(
            requested=2,
            completed=0,
            errors=2,
            elapsed_seconds=0.0,
        ),
    )

    markdown_filename_audit.write_reports(report, tmp_path)

    with (tmp_path / "skipped_repositories.csv").open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert rows == [
        {
            "repository": "example/project-0",
            "snapshot_sha": f"{1:040x}",
            "status": "snapshot_incomplete",
            "reason": "snapshot_incomplete",
        },
        {
            "repository": "example/project-1",
            "snapshot_sha": f"{2:040x}",
            "status": "explicitly_excluded",
            "reason": "explicitly_excluded",
        },
    ]
