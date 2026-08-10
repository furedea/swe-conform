"""Read revision-pinned repository trees from local Git caches."""

import os
import subprocess
from pathlib import Path
from typing import Protocol

import github_client
import repository_cache

_DEFAULT_TIMEOUT_SECONDS = 60.0


class RepositoryTreeClient(Protocol):
    """Retrieve complete repository trees and their text blobs."""

    def get_complete_tree(self, repository: str, revision: str) -> github_client.RepositoryTree:
        """Return every blob entry reachable from a repository revision."""
        ...

    def get_text_blob(self, repository: str, blob_sha: str) -> str:
        """Return one UTF-8-decoded Git blob."""
        ...


class CachedRepositoryTreeError(RuntimeError):
    """A tree could not be read from an available local Git cache."""


class CachedRepositoryTreeClient:
    """Prefer local bare Git caches and fall back when a revision is absent."""

    __slots__ = ("_cache", "_command", "_fallback", "_timeout_seconds")

    def __init__(
        self,
        *,
        cache: repository_cache.GitRepositoryCache,
        fallback: RepositoryTreeClient,
        command: str = "git",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._cache = cache
        self._fallback = fallback
        self._command = command
        self._timeout_seconds = timeout_seconds

    def get_complete_tree(self, repository: str, revision: str) -> github_client.RepositoryTree:
        """Return a cached tree, or delegate when the pinned revision is unavailable."""
        cache_path = self._cache.path(repository)
        if not self._revision_exists(cache_path, revision):
            return self._fallback.get_complete_tree(repository, revision)
        return self._read_tree(cache_path, revision)

    def get_text_blob(self, repository: str, blob_sha: str) -> str:
        """Return a cached text blob, or delegate when the object is unavailable."""
        cache_path = self._cache.path(repository)
        if cache_path.is_dir():
            completed = self._execute(
                [
                    self._command,
                    "--git-dir",
                    str(cache_path),
                    "cat-file",
                    "blob",
                    blob_sha,
                ],
            )
            if completed.returncode == 0:
                return completed.stdout.decode("utf-8", errors="replace")
        return self._fallback.get_text_blob(repository, blob_sha)

    def _revision_exists(self, cache_path: Path, revision: str) -> bool:
        if not cache_path.is_dir():
            return False
        completed = self._execute(
            [
                self._command,
                "--git-dir",
                str(cache_path),
                "cat-file",
                "-e",
                f"{revision}^{{commit}}",
            ],
        )
        return completed.returncode == 0

    def _read_tree(self, cache_path: Path, revision: str) -> github_client.RepositoryTree:
        completed = self._execute(
            [
                self._command,
                "--git-dir",
                str(cache_path),
                "ls-tree",
                "-r",
                "-l",
                "-z",
                "--full-tree",
                revision,
            ],
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()[-1000:]
            msg = f"Cached repository tree read failed: returncode={completed.returncode} stderr={stderr!r}"
            raise CachedRepositoryTreeError(msg)
        return github_client.RepositoryTree(
            entries=tuple(
                entry
                for record in completed.stdout.split(b"\0")
                if record
                if (entry := self._parse_entry(record)) is not None
            ),
            truncated=False,
        )

    @staticmethod
    def _parse_entry(record: bytes) -> github_client.TreeEntry | None:
        metadata, path = record.split(b"\t", maxsplit=1)
        mode, object_type, sha, raw_size = metadata.split(maxsplit=3)
        if object_type != b"blob":
            return None
        return github_client.TreeEntry(
            path=path.decode("utf-8", errors="replace"),
            sha=sha.decode("ascii"),
            size=int(raw_size),
            mode=mode.decode("ascii"),
        )

    def _execute(self, command: list[str]) -> subprocess.CompletedProcess[bytes]:
        environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            return subprocess.run(
                command,
                capture_output=True,
                check=False,
                env=environment,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            msg = f"Cached repository tree read timed out: {error}"
            raise CachedRepositoryTreeError(msg) from error
