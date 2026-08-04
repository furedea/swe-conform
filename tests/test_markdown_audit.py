"""Tests for Markdown keyword coverage audits."""

import csv
import threading
from contextlib import nullcontext
from pathlib import Path

from pytest_mock import MockerFixture

import markdown_audit
import repository
import repository_workspace


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


def test_scan_markdown_files_matches_complete_singular_and_plural_keywords(tmp_path: Path) -> None:
    (tmp_path / "candidate.md").write_text(
        "Style guides define standards, conventions, and rules.\n",
        encoding="utf-8",
    )
    (tmp_path / "unrelated.md").write_text(
        "Hairstyle guider standardized unconventional overrule.\n",
        encoding="utf-8",
    )

    matches = markdown_audit.scan_markdown_files(tmp_path)

    assert matches == (
        markdown_audit.MarkdownKeywordFile(
            path="candidate.md",
            matched_keywords=(
                "style",
                "guide",
                "standard",
                "convention",
                "rule",
            ),
        ),
    )


def test_scan_markdown_files_matches_guideline_in_singular_and_plural_forms(tmp_path: Path) -> None:
    (tmp_path / "singular.md").write_text("Follow the project guideline.\n", encoding="utf-8")
    (tmp_path / "plural.md").write_text("Follow the project guidelines.\n", encoding="utf-8")

    matches = markdown_audit.scan_markdown_files(tmp_path)

    assert matches == (
        markdown_audit.MarkdownKeywordFile(
            path="plural.md",
            matched_keywords=("guideline",),
        ),
        markdown_audit.MarkdownKeywordFile(
            path="singular.md",
            matched_keywords=("guideline",),
        ),
    )


def test_scan_markdown_files_reads_only_regular_md_files(tmp_path: Path) -> None:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    (repository_path / "UPPER.MD").write_text("A style rule.\n", encoding="utf-8")
    (repository_path / "document.mdx").write_text("A style rule.\n", encoding="utf-8")
    outside_path = tmp_path / "outside.md"
    outside_path.write_text("A style rule.\n", encoding="utf-8")
    (repository_path / "linked.md").symlink_to(outside_path)

    matches = markdown_audit.scan_markdown_files(repository_path)

    assert [match.path for match in matches] == ["UPPER.MD"]


def test_compare_agent_evidence_reports_each_path_presence_in_keyword_candidates() -> None:
    matches = (
        markdown_audit.MarkdownKeywordFile(
            path="CONTRIBUTING.md",
            matched_keywords=("style", "rule"),
        ),
    )

    coverage = markdown_audit.compare_agent_evidence(
        matches,
        evidence_paths=("CONTRIBUTING.md", "docs/testing.md", "src/example.py"),
    )

    assert coverage == (
        markdown_audit.AgentEvidenceCoverage(
            path="CONTRIBUTING.md",
            is_markdown=True,
            keyword_match=True,
            matched_keywords=("style", "rule"),
        ),
        markdown_audit.AgentEvidenceCoverage(
            path="docs/testing.md",
            is_markdown=True,
            keyword_match=False,
            matched_keywords=(),
        ),
        markdown_audit.AgentEvidenceCoverage(
            path="src/example.py",
            is_markdown=False,
            keyword_match=False,
            matched_keywords=(),
        ),
    )


def test_load_agent_evidence_merges_and_deduplicates_csv_files(tmp_path: Path) -> None:
    first_path = tmp_path / "first.csv"
    first_path.write_text(
        "name,lastCommitSHA,guideline_path\nexample/project,0123456789abcdef,CONTRIBUTING.md\n",
        encoding="utf-8",
    )
    second_path = tmp_path / "second.csv"
    second_path.write_text(
        "name,lastCommitSHA,guideline_path\n"
        "example/project,0123456789abcdef,CONTRIBUTING.md\n"
        "example/project,0123456789abcdef,docs/testing.md\n",
        encoding="utf-8",
    )

    evidence = markdown_audit.load_agent_evidence((first_path, second_path))

    assert evidence == {
        ("example/project", "0123456789abcdef"): (
            "CONTRIBUTING.md",
            "docs/testing.md",
        ),
    }


def test_auditor_scans_the_pinned_repository_and_compares_agent_evidence(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    repository_path = tmp_path / "repository"
    repository_path.mkdir()
    (repository_path / "CONTRIBUTING.md").write_text("Follow the code style.\n", encoding="utf-8")
    workspace = mocker.Mock(spec=repository_workspace.GitRepositoryWorkspace)
    workspace.checkout.return_value = nullcontext(tmp_path)
    auditor = markdown_audit.MarkdownAuditor(
        workspace=workspace,
        agent_evidence={
            ("example/project", "0123456789abcdef"): ("CONTRIBUTING.md",),
        },
    )

    result = auditor.audit(_candidate())

    workspace.checkout.assert_called_once_with("example/project", "0123456789abcdef")
    assert result.status is markdown_audit.MarkdownAuditStatus.COMPLETED
    assert [match.path for match in result.keyword_files] == ["CONTRIBUTING.md"]
    assert result.agent_evidence[0].keyword_match is True


def test_auditor_preserves_unevaluated_agent_evidence_after_checkout_failure(
    mocker: MockerFixture,
) -> None:
    workspace = mocker.Mock(spec=repository_workspace.GitRepositoryWorkspace)
    workspace.checkout.side_effect = repository_workspace.RepositoryCheckoutError("unavailable")
    auditor = markdown_audit.MarkdownAuditor(
        workspace=workspace,
        agent_evidence={
            ("example/project", "0123456789abcdef"): ("CONTRIBUTING.md",),
        },
    )

    result = auditor.audit(_candidate())

    assert result.status is markdown_audit.MarkdownAuditStatus.RETRIEVAL_ERROR
    assert result.agent_evidence == (
        markdown_audit.AgentEvidenceCoverage(
            path="CONTRIBUTING.md",
            is_markdown=True,
            keyword_match=None,
            matched_keywords=(),
        ),
    )


def test_audit_runner_uses_the_configured_workers_concurrently(mocker: MockerFixture) -> None:
    candidates = (_indexed_candidate(0), _indexed_candidate(1))
    rendezvous = threading.Barrier(2)

    def audit(candidate: repository.RepositoryCandidate) -> markdown_audit.RepositoryMarkdownAudit:
        rendezvous.wait(timeout=5)
        return markdown_audit.RepositoryMarkdownAudit(
            candidate=candidate,
            status=markdown_audit.MarkdownAuditStatus.COMPLETED,
        )

    auditor = mocker.Mock(spec=markdown_audit.MarkdownAuditor)
    auditor.audit.side_effect = audit
    runner = markdown_audit.MarkdownAuditRunner(auditor=auditor, workers=2)

    report = runner.run(candidates)

    assert report.stats.completed == 2


def test_write_reports_lists_matching_files_and_agent_evidence_coverage(tmp_path: Path) -> None:
    result = markdown_audit.RepositoryMarkdownAudit(
        candidate=_candidate(),
        status=markdown_audit.MarkdownAuditStatus.COMPLETED,
        keyword_files=(
            markdown_audit.MarkdownKeywordFile(
                path="CONTRIBUTING.md",
                matched_keywords=("style", "rule"),
            ),
        ),
        agent_evidence=(
            markdown_audit.AgentEvidenceCoverage(
                path="CONTRIBUTING.md",
                is_markdown=True,
                keyword_match=True,
                matched_keywords=("style", "rule"),
            ),
            markdown_audit.AgentEvidenceCoverage(
                path="docs/testing.md",
                is_markdown=True,
                keyword_match=False,
                matched_keywords=(),
            ),
        ),
    )
    report = markdown_audit.MarkdownAuditReport(
        results=(result,),
        stats=markdown_audit.MarkdownAuditStats(
            requested=1,
            completed=1,
            errors=0,
            elapsed_seconds=1.0,
        ),
    )

    markdown_audit.write_reports(report, tmp_path)

    with (tmp_path / "markdown_term_files.csv").open(encoding="utf-8", newline="") as input_file:
        keyword_rows = list(csv.DictReader(input_file))
    assert keyword_rows == [
        {
            "name": "example/project",
            "lastCommitSHA": "0123456789abcdef",
            "markdown_path": "CONTRIBUTING.md",
            "markdown_url": "https://github.com/example/project/blob/0123456789abcdef/CONTRIBUTING.md",
            "matched_keywords": "style|rule",
            "agent_evidence": "True",
        },
    ]
    with (tmp_path / "agent_evidence_coverage.csv").open(encoding="utf-8", newline="") as input_file:
        coverage_rows = list(csv.DictReader(input_file))
    assert [row["keyword_match"] for row in coverage_rows] == ["True", "False"]
    with (tmp_path / "repository_summary.csv").open(encoding="utf-8", newline="") as input_file:
        summary_rows = list(csv.DictReader(input_file))
    assert summary_rows[0]["markdown_keyword_file_count"] == "1"
    assert summary_rows[0]["agent_evidence_keyword_match_count"] == "1"
