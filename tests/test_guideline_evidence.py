"""Tests for deterministic project-guideline evidence collection."""

import github_client
import guideline_evidence


def _entry(path: str, *, size: int = 100) -> github_client.TreeEntry:
    return github_client.TreeEntry(path=path, sha=f"sha-{path}", size=size)


def test_candidate_selector_prioritizes_project_coding_documents() -> None:
    tree = github_client.RepositoryTree(
        entries=(
            _entry("README.md"),
            _entry("vendor/library/CONTRIBUTING.md"),
            _entry("docs/fixtures/CODING_STYLE.md"),
            _entry("docs/development/style_guide.md"),
            _entry("CONTRIBUTING.md"),
            _entry("src/main.py"),
        ),
        truncated=False,
    )

    selected = guideline_evidence.CandidateDocumentSelector(max_documents=3).select(tree)

    assert [entry.path for entry in selected] == [
        "CONTRIBUTING.md",
        "docs/development/style_guide.md",
        "README.md",
    ]


def test_candidate_selector_excludes_agent_instructions_and_large_files() -> None:
    tree = github_client.RepositoryTree(
        entries=(
            _entry("AGENTS.md"),
            _entry(".github/copilot-instructions.md"),
            _entry("CODING_STYLE.md", size=300_000),
        ),
        truncated=False,
    )

    selected = guideline_evidence.CandidateDocumentSelector().select(tree)

    assert selected == ()
