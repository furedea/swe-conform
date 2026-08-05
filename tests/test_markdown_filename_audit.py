"""Tests for Markdown filename candidate audits."""

import csv
import threading
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

import github_client
import markdown_filename_audit
import repository


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

    assert terms == ("guideline",)


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
    assert markdown_filename_audit.matched_filename_terms(f"coding-{singular}.md") == (term,)
    assert markdown_filename_audit.matched_filename_terms(f"coding-{plural}.md") == (term,)


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


def test_scan_uses_the_github_tree_without_fetching_file_contents(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    client.get_complete_tree.return_value = github_client.RepositoryTree(
        entries=(
            github_client.TreeEntry(path="README.MD", sha="blob-readme", size=10),
            github_client.TreeEntry(path="CONTRIBUTING.mdx", sha="blob-mdx", size=20),
            github_client.TreeEntry(path="src/main.py", sha="blob-python", size=30),
        ),
        truncated=False,
    )

    matches = markdown_filename_audit.scan_github_markdown_filenames(
        client,
        "example/project",
        "0123456789abcdef",
    )

    assert matches == (
        markdown_filename_audit.MarkdownFilenameFile(
            path="README.MD",
            matched_terms=("readme",),
        ),
    )
    client.get_complete_tree.assert_called_once_with("example/project", "0123456789abcdef")
    client.get_text_file.assert_not_called()


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
        ),
    )

    coverage = markdown_filename_audit.compare_agent_evidence(
        matches,
        evidence_paths=("CONTRIBUTING.md", "docs/testing.md", "src/example.py"),
    )

    assert coverage == (
        markdown_filename_audit.AgentEvidenceFilenameCoverage(
            path="CONTRIBUTING.md",
            is_markdown=True,
            filename_match=True,
            matched_terms=("contributing",),
        ),
        markdown_filename_audit.AgentEvidenceFilenameCoverage(
            path="docs/testing.md",
            is_markdown=True,
            filename_match=False,
            matched_terms=(),
        ),
        markdown_filename_audit.AgentEvidenceFilenameCoverage(
            path="src/example.py",
            is_markdown=False,
            filename_match=False,
            matched_terms=(),
        ),
    )


def test_auditor_compares_filename_candidates_with_agent_evidence(mocker: MockerFixture) -> None:
    client = mocker.Mock()
    client.get_complete_tree.return_value = github_client.RepositoryTree(
        entries=(github_client.TreeEntry(path="README.md", sha="blob-readme", size=10),),
        truncated=False,
    )
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
            ),
        ),
        agent_evidence=(
            markdown_filename_audit.AgentEvidenceFilenameCoverage(
                path="CONTRIBUTING.md",
                is_markdown=True,
                filename_match=True,
                matched_terms=("contributing",),
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
            "markdown_url": ("https://github.com/example/project/blob/0123456789abcdef/CONTRIBUTING.md"),
            "matched_filename_terms": "contributing",
            "agent_evidence": "True",
        },
    ]
    with (tmp_path / "agent_evidence_filename_coverage.csv").open(
        encoding="utf-8",
        newline="",
    ) as input_file:
        coverage_rows = list(csv.DictReader(input_file))
    assert coverage_rows[0]["filename_match"] == "True"
    with (tmp_path / "repository_filename_summary.csv").open(
        encoding="utf-8",
        newline="",
    ) as input_file:
        summary_rows = list(csv.DictReader(input_file))
    assert summary_rows[0]["markdown_filename_file_count"] == "1"
    assert not (tmp_path / "markdown_term_files.csv").exists()
