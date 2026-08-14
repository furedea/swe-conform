"""Minimal GitHub API client for revision-pinned repository documents."""

import base64
import threading
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


@dataclass(frozen=True, slots=True)
class GitHubRequestMetrics:
    """HTTP request and rate-limit wait totals for one client."""

    requests: int
    rate_limit_wait_seconds: float


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

    __slots__ = (
        "_base_url",
        "_client",
        "_headers",
        "_max_attempts",
        "_primary_rate_limit_reset_at",
        "_rate_limit_wait_seconds",
        "_raw_base_url",
        "_request_count",
        "_request_lock",
    )

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
        self._request_lock = threading.Lock()
        self._request_count = 0
        self._rate_limit_wait_seconds = 0.0
        self._primary_rate_limit_reset_at: float | None = None

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

    def metrics(self) -> GitHubRequestMetrics:
        """Return completed HTTP request and rate-limit wait totals."""
        with self._request_lock:
            return GitHubRequestMetrics(
                requests=self._request_count,
                rate_limit_wait_seconds=self._rate_limit_wait_seconds,
            )

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
        with self._request_lock:
            return self._get_response_serialized(url, params=params)

    def _get_response_serialized(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        for attempt in range(self._max_attempts):
            try:
                self._wait_for_primary_rate_limit()
                self._request_count += 1
                response = self._client.get(url, headers=self._headers, params=params)
                self._remember_primary_rate_limit(response)
                response.raise_for_status()
            except httpx.TransportError as error:
                if self._should_retry(attempt):
                    self._wait_before_retry(attempt)
                    continue
                msg = f"GitHub request failed after {self._max_attempts} attempts: {error}"
                raise GitHubRetrievalError(msg) from error
            except httpx.HTTPStatusError as error:
                failed_response = error.response
                if self._is_retryable_response(failed_response) and self._should_retry(attempt):
                    self._wait_before_retry(attempt, response=failed_response)
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
    def _is_retryable_response(response: httpx.Response) -> bool:
        return (
            response.status_code in _RETRYABLE_STATUS_CODES
            or response.status_code >= 500
            or "retry-after" in response.headers
            or response.headers.get("x-ratelimit-remaining") == "0"
        )

    def _wait_before_retry(self, attempt: int, *, response: httpx.Response | None = None) -> None:
        retry_after = response.headers.get("retry-after") if response is not None else None
        try:
            if retry_after is not None:
                delay_seconds = float(retry_after)
            elif response is not None and response.headers.get("x-ratelimit-remaining") == "0":
                delay_seconds = float(response.headers["x-ratelimit-reset"]) - time.time() + 1.0
            else:
                delay_seconds = float(2**attempt)
        except KeyError, ValueError:
            delay_seconds = float(2**attempt)
        self._primary_rate_limit_reset_at = None
        self._sleep(
            delay_seconds,
            count_as_rate_limit=response is not None and self._is_rate_limited_response(response),
        )

    @staticmethod
    def _is_rate_limited_response(response: httpx.Response) -> bool:
        return (
            response.status_code == 429
            or "retry-after" in response.headers
            or response.headers.get("x-ratelimit-remaining") == "0"
        )

    def _remember_primary_rate_limit(self, response: httpx.Response) -> None:
        headers = getattr(response, "headers", {})
        if headers.get("x-ratelimit-remaining") != "0":
            return
        try:
            self._primary_rate_limit_reset_at = float(headers["x-ratelimit-reset"])
        except KeyError, ValueError:
            self._primary_rate_limit_reset_at = None

    def _wait_for_primary_rate_limit(self) -> None:
        reset_at = self._primary_rate_limit_reset_at
        self._primary_rate_limit_reset_at = None
        if reset_at is not None:
            self._sleep(reset_at - time.time() + 1.0, count_as_rate_limit=True)

    def _sleep(self, delay_seconds: float, *, count_as_rate_limit: bool) -> None:
        bounded_delay_seconds = max(0.0, delay_seconds)
        if count_as_rate_limit:
            self._rate_limit_wait_seconds += bounded_delay_seconds
        time.sleep(bounded_delay_seconds)

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
