"""Minimal GitHub API client for revision-pinned repository documents."""

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

import httpx

_DEFAULT_TIMEOUT_SECONDS = 60.0
_ERROR_BODY_LIMIT = 2000


@dataclass(frozen=True, slots=True)
class TreeEntry:
    """A text-file candidate in a Git tree."""

    path: str
    sha: str
    size: int


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

    __slots__ = ("_base_url", "_client", "_headers")

    def __init__(
        self,
        *,
        token: str,
        base_url: str = "https://api.github.com",
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._client = http_client or httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS)

    def get_tree(self, repository: str, revision: str) -> RepositoryTree:
        """Return blob entries from a recursively retrieved Git tree."""
        document = self._get_json(
            f"/repos/{repository}/git/trees/{revision}",
            params={"recursive": "1"},
        )
        raw_entries = cast(list[Mapping[str, object]], document.get("tree", []))
        entries = tuple(
            TreeEntry(
                path=str(entry["path"]),
                sha=str(entry["sha"]),
                size=int(str(entry.get("size", 0))),
            )
            for entry in raw_entries
            if entry.get("type") == "blob"
        )
        return RepositoryTree(entries=entries, truncated=bool(document.get("truncated", False)))

    def get_text_blob(self, repository: str, blob_sha: str) -> str:
        """Decode one base64-encoded Git blob as text."""
        document = self._get_json(f"/repos/{repository}/git/blobs/{blob_sha}")
        if document.get("encoding") != "base64":
            msg = f"Unsupported GitHub blob encoding: {document.get('encoding')!r}"
            raise RuntimeError(msg)
        encoded = str(document["content"]).replace("\n", "")
        return base64.b64decode(encoded, validate=True).decode("utf-8", errors="replace")

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def _get_json(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> Mapping[str, object]:
        response = self._client.get(
            f"{self._base_url}{path}",
            headers=self._headers,
            params=params,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            body = response.text[:_ERROR_BODY_LIMIT]
            msg = f"GitHub request failed: status={response.status_code} body={body}"
            raise RuntimeError(msg) from error
        return cast(Mapping[str, object], response.json())
