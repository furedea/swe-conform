"""Deterministic discovery of project coding-guideline documents."""

from dataclasses import dataclass
from pathlib import PurePosixPath

import github_client

_MAX_DOCUMENT_BYTES = 200_000
_DOCUMENT_EXTENSIONS = frozenset({"", ".adoc", ".md", ".rst", ".txt"})
_EXCLUDED_SEGMENTS = frozenset(
    {
        ".git",
        "fixtures",
        "generated",
        "node_modules",
        "snapshots",
        "testdata",
        "third_party",
        "vendor",
    },
)
_EXCLUDED_NAMES = frozenset(
    {
        "agents.md",
        "changelog.md",
        "claude.md",
        "code_of_conduct.md",
        "copilot-instructions.md",
        "release.md",
        "security.md",
    },
)
_STRONG_STEMS = frozenset(
    {
        "code-style",
        "code_style",
        "coding-style",
        "coding_style",
        "contributing",
        "contributing-guidelines",
        "contributing_guidelines",
        "development",
        "developer-guide",
        "developer_guide",
        "developing",
        "guidelines",
        "hacking",
        "style-guide",
        "style_guide",
        "styleguide",
    },
)


@dataclass(frozen=True, slots=True)
class GuidelineDocument:
    """A candidate guideline document and its repository path."""

    path: str
    content: str


@dataclass(frozen=True, slots=True)
class RepositoryEvidence:
    """Candidate documents discovered in one revision-pinned tree."""

    documents: tuple[GuidelineDocument, ...]
    tree_truncated: bool


class CandidateDocumentSelector:
    """Rank likely project coding-guideline files without using an LLM."""

    __slots__ = ("_max_documents",)

    def __init__(self, *, max_documents: int = 12) -> None:
        self._max_documents = max_documents

    def select(self, tree: github_client.RepositoryTree) -> tuple[github_client.TreeEntry, ...]:
        """Return a bounded, relevance-ranked set of candidate blobs."""
        ranked = (
            (self._score(entry.path), entry.path.casefold(), entry)
            for entry in tree.entries
            if self._is_candidate(entry)
        )
        selected = sorted(ranked, key=lambda item: (-item[0], item[1]))[: self._max_documents]
        return tuple(item[2] for item in selected)

    def _is_candidate(self, entry: github_client.TreeEntry) -> bool:
        path = PurePosixPath(entry.path)
        segments = {part.casefold() for part in path.parts}
        if entry.size > _MAX_DOCUMENT_BYTES or segments.intersection(_EXCLUDED_SEGMENTS):
            return False
        if path.suffix.casefold() not in _DOCUMENT_EXTENSIONS or path.name.casefold() in _EXCLUDED_NAMES:
            return False
        return self._score(entry.path) > 0

    def _score(self, raw_path: str) -> int:
        path = PurePosixPath(raw_path)
        stem = path.stem.casefold()
        if stem in _STRONG_STEMS:
            score = 350
        elif path.name.casefold().startswith("readme"):
            score = 100
        elif any(keyword in stem for keyword in ("coding", "contribut", "develop", "guideline", "style")):
            score = 250
        else:
            return 0
        if len(path.parts) == 1:
            score += 50
        if {part.casefold() for part in path.parts}.intersection({"contributing", "development", "developer"}):
            score += 20
        return score


class GuidelineEvidenceCollector:
    """Retrieve the candidate documents for one repository revision."""

    __slots__ = ("_client", "_selector")

    def __init__(
        self,
        *,
        client: github_client.RepositoryDocumentClient,
        selector: CandidateDocumentSelector | None = None,
    ) -> None:
        self._client = client
        self._selector = selector or CandidateDocumentSelector()

    def collect(self, repository: str, revision: str) -> RepositoryEvidence:
        """Retrieve and decode all selected candidate documents."""
        tree = self._client.get_tree(repository, revision)
        entries = self._selector.select(tree)
        documents = tuple(
            GuidelineDocument(
                path=entry.path,
                content=self._client.get_text_blob(repository, entry.sha),
            )
            for entry in entries
        )
        return RepositoryEvidence(documents=documents, tree_truncated=tree.truncated)
