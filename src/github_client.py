"""Minimal GitHub API client for revision-pinned repository documents."""

import base64
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import quote

import httpx

_DEFAULT_TIMEOUT_SECONDS = 60.0
_DEFAULT_MAX_ATTEMPTS = 3
_ERROR_BODY_LIMIT = 2000
_RETRYABLE_STATUS_CODES = frozenset({408, 429})


class GitHubRetrievalError(RuntimeError):
    """A GitHub request could not be completed after retrying."""


@dataclass(frozen=True, slots=True)
class TreeEntry:
    """A text-file candidate in a Git tree."""

    path: str
    sha: str
    size: int
    mode: str = ""


@dataclass(frozen=True, slots=True)
class RepositoryTree:
    """A recursively retrieved Git tree and its completeness flag."""

    entries: tuple[TreeEntry, ...]
    truncated: bool


class RepositoryDocumentClient(Protocol):
    """Retrieve repository trees and text blobs at pinned revisions."""

    def get_tree(self, repository: str, revision: str) -> RepositoryTree:
        """Return the recursive tree for a repository revision."""
        ...

    def get_text_blob(self, repository: str, blob_sha: str) -> str:
        """Return one UTF-8-decoded Git blob."""
        ...


class GitHubClient:
    """Read public repository data through the GitHub REST API."""

    __slots__ = ("_base_url", "_client", "_headers", "_max_attempts", "_raw_base_url")

    def __init__(
        self,
        *,
        token: str,
        base_url: str = "https://api.github.com",
        raw_base_url: str = "https://raw.githubusercontent.com",
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        http_client: httpx.Client | None = None,
    ) -> None:
        if max_attempts < 1:
            msg = "max_attempts must be at least 1"
            raise ValueError(msg)
        self._base_url = base_url.rstrip("/")
        self._raw_base_url = raw_base_url.rstrip("/")
        self._max_attempts = max_attempts
        self._headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._client = http_client or httpx.Client(
            timeout=_DEFAULT_TIMEOUT_SECONDS,
            follow_redirects=True,
        )

    def get_tree(self, repository: str, revision: str) -> RepositoryTree:
        """Return blob entries from a recursively retrieved Git tree."""
        document = self._get_tree_document(repository, revision, recursive=True)
        entries = self._blob_entries(document)
        return RepositoryTree(entries=entries, truncated=bool(document.get("truncated", False)))

    def get_complete_tree(self, repository: str, revision: str) -> RepositoryTree:
        """Return every blob entry, recovering from GitHub recursive-tree limits."""
        document = self._get_tree_document(repository, revision, recursive=True)
        if not document.get("truncated", False):
            return RepositoryTree(entries=self._blob_entries(document), truncated=False)
        entries = self._complete_subtree_entries(repository, revision, prefix="")
        return RepositoryTree(entries=tuple(sorted(entries, key=lambda entry: entry.path)), truncated=False)

    def get_text_blob(self, repository: str, blob_sha: str) -> str:
        """Decode one base64-encoded Git blob as text."""
        document = self._get_json(f"/repos/{repository}/git/blobs/{blob_sha}")
        if document.get("encoding") != "base64":
            msg = f"Unsupported GitHub blob encoding: {document.get('encoding')!r}"
            raise RuntimeError(msg)
        encoded = str(document["content"]).replace("\n", "")
        return base64.b64decode(encoded, validate=True).decode("utf-8", errors="replace")

    def get_text_file(self, repository: str, revision: str, path: str) -> str:
        """Return one UTF-8-decoded file from an exact revision-pinned raw URL."""
        encoded_repository = quote(repository, safe="/")
        encoded_revision = quote(revision, safe="")
        encoded_path = quote(path, safe="/")
        response = self._get_response(
            f"{self._raw_base_url}/{encoded_repository}/{encoded_revision}/{encoded_path}",
        )
        return response.content.decode("utf-8", errors="replace")

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def _get_json(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> Mapping[str, object]:
        response = self._get_response(f"{self._base_url}{path}", params=params)
        return cast(Mapping[str, object], response.json())

    def _get_response(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        for attempt in range(self._max_attempts):
            try:
                response = self._client.get(url, headers=self._headers, params=params)
                response.raise_for_status()
            except httpx.TransportError as error:
                if self._should_retry(attempt):
                    self._wait_before_retry(attempt)
                    continue
                msg = f"GitHub request failed after {self._max_attempts} attempts: {error}"
                raise GitHubRetrievalError(msg) from error
            except httpx.HTTPStatusError as error:
                failed_response = error.response
                if self._is_retryable_status(failed_response.status_code) and self._should_retry(attempt):
                    self._wait_before_retry(attempt)
                    continue
                body = failed_response.text[:_ERROR_BODY_LIMIT]
                msg = f"GitHub request failed: status={failed_response.status_code} body={body}"
                raise GitHubRetrievalError(msg) from error
            else:
                return response
        msg = "GitHub request retry loop ended unexpectedly"
        raise GitHubRetrievalError(msg)

    def _should_retry(self, attempt: int) -> bool:
        return attempt + 1 < self._max_attempts

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code in _RETRYABLE_STATUS_CODES or status_code >= 500

    @staticmethod
    def _wait_before_retry(attempt: int) -> None:
        time.sleep(float(2**attempt))

    def _get_tree_document(
        self,
        repository: str,
        treeish: str,
        *,
        recursive: bool,
    ) -> Mapping[str, object]:
        params = {"recursive": "1"} if recursive else None
        return self._get_json(f"/repos/{repository}/git/trees/{treeish}", params=params)

    def _complete_subtree_entries(
        self,
        repository: str,
        treeish: str,
        *,
        prefix: str,
    ) -> tuple[TreeEntry, ...]:
        document = self._get_tree_document(repository, treeish, recursive=False)
        if document.get("truncated", False):
            msg = f"GitHub returned a truncated non-recursive tree: {repository}@{treeish}"
            raise GitHubRetrievalError(msg)
        entries: list[TreeEntry] = []
        for raw_entry in self._raw_tree_entries(document):
            path = self._prefixed_path(prefix, str(raw_entry["path"]))
            if raw_entry.get("type") == "blob":
                entries.append(self._blob_entry(raw_entry, path=path))
                continue
            if raw_entry.get("type") != "tree":
                continue
            subtree = self._get_tree_document(repository, str(raw_entry["sha"]), recursive=True)
            if subtree.get("truncated", False):
                entries.extend(
                    self._complete_subtree_entries(
                        repository,
                        str(raw_entry["sha"]),
                        prefix=path,
                    ),
                )
            else:
                entries.extend(self._blob_entries(subtree, prefix=path))
        return tuple(entries)

    @classmethod
    def _blob_entries(
        cls,
        document: Mapping[str, object],
        *,
        prefix: str = "",
    ) -> tuple[TreeEntry, ...]:
        return tuple(
            cls._blob_entry(
                entry,
                path=cls._prefixed_path(prefix, str(entry["path"])),
            )
            for entry in cls._raw_tree_entries(document)
            if entry.get("type") == "blob"
        )

    @staticmethod
    def _blob_entry(entry: Mapping[str, object], *, path: str) -> TreeEntry:
        return TreeEntry(
            path=path,
            sha=str(entry["sha"]),
            size=int(str(entry.get("size", 0))),
            mode=str(entry.get("mode", "")),
        )

    @staticmethod
    def _raw_tree_entries(document: Mapping[str, object]) -> list[Mapping[str, object]]:
        return cast(list[Mapping[str, object]], document.get("tree", []))

    @staticmethod
    def _prefixed_path(prefix: str, path: str) -> str:
        return f"{prefix}/{path}" if prefix else path
