"""Persist revision-pinned GitHub repository content for resumable screening."""

import re
import threading
from dataclasses import dataclass
from pathlib import Path

import github_client

_OBJECT_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{7,64}$")


@dataclass(frozen=True, slots=True)
class GitHubContentMetrics:
    """Downloaded and reused GitHub blob totals for one run."""

    downloads: int
    cache_hits: int


class PersistentGitHubRepositoryClient:
    """Read GitHub trees while storing every retrieved text blob on disk."""

    __slots__ = ("_cache_hits", "_client", "_content_root", "_downloads", "_lock")

    def __init__(self, *, client: github_client.GitHubClient, content_root: Path) -> None:
        self._client = client
        self._content_root = content_root
        self._lock = threading.Lock()
        self._downloads = 0
        self._cache_hits = 0

    def get_complete_tree(self, repository: str, revision: str) -> github_client.RepositoryTree:
        """Return every entry from one exact GitHub revision."""
        return self._client.get_complete_tree(repository, revision)

    def get_text_blob(self, repository: str, blob_sha: str) -> str:
        """Return one text blob, retrieving and persisting it when absent."""
        path = self._blob_path(repository, blob_sha)
        with self._lock:
            if path.exists():
                self._cache_hits += 1
                return path.read_text(encoding="utf-8")
            content = self._client.get_text_blob(repository, blob_sha)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = path.with_suffix(".tmp")
            temporary_path.write_text(content, encoding="utf-8")
            temporary_path.replace(path)
            self._downloads += 1
            return content

    def get_text_blobs(self, repository: str, blob_shas: tuple[str, ...]) -> dict[str, str]:
        """Return unique text blobs keyed by immutable Git object IDs."""
        return {blob_sha: self.get_text_blob(repository, blob_sha) for blob_sha in dict.fromkeys(blob_shas)}

    def metrics(self) -> GitHubContentMetrics:
        """Return downloaded and reused blob totals for this process."""
        with self._lock:
            return GitHubContentMetrics(downloads=self._downloads, cache_hits=self._cache_hits)

    def report_metrics(self) -> dict[str, object]:
        """Return source metrics ready for a collection report."""
        requests = self._client.metrics()
        contents = self.metrics()
        return {
            "github_requests": requests.requests,
            "github_rate_limit_wait_seconds": requests.rate_limit_wait_seconds,
            "source_content_downloads": contents.downloads,
            "source_content_cache_hits": contents.cache_hits,
        }

    def _blob_path(self, repository: str, blob_sha: str) -> Path:
        owner, separator, name = repository.partition("/")
        if separator != "/" or owner in {"", ".", ".."} or name in {"", ".", ".."} or "/" in name:
            raise ValueError(f"Repository name must be owner/repository: {repository!r}")
        if _OBJECT_ID_PATTERN.fullmatch(blob_sha) is None:
            raise ValueError(f"Git object ID is invalid: {blob_sha!r}")
        return self._content_root / owner.casefold() / name.casefold() / "blobs" / blob_sha.casefold()
