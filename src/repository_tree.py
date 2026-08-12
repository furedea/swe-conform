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


class LocalRepositoryTreeClient:
    """Read revision-pinned trees exclusively from local bare Git caches."""

    __slots__ = ("_cache", "_command", "_timeout_seconds")

    def __init__(
        self,
        *,
        cache: repository_cache.GitRepositoryCache,
        command: str = "git",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._cache = cache
        self._command = command
        self._timeout_seconds = timeout_seconds

    def get_complete_tree(self, repository: str, revision: str) -> github_client.RepositoryTree:
        """Return a cached tree without retrieving missing revisions externally."""
        cache_path = self._cache.path(repository)
        if not self._revision_exists(cache_path, revision):
            msg = f"Cached pinned revision is absent: repository={repository} revision={revision}"
            raise CachedRepositoryTreeError(msg)
        return self._read_tree(cache_path, revision)

    def get_text_blob(self, repository: str, blob_sha: str) -> str:
        """Return a cached text blob without retrieving missing objects externally."""
        cache_path = self._cache.path(repository)
        content = self._read_blob(cache_path, blob_sha)
        if content is None:
            msg = f"Cached blob is absent: repository={repository} blob_sha={blob_sha}"
            raise CachedRepositoryTreeError(msg)
        return content

    def get_text_blobs(self, repository: str, blob_shas: tuple[str, ...]) -> dict[str, str]:
        """Return multiple cached text blobs through one Git batch process."""
        requested = tuple(dict.fromkeys(blob_shas))
        if not requested:
            return {}
        cache_path = self._cache.path(repository)
        if not cache_path.is_dir():
            msg = f"Cached repository is absent: repository={repository}"
            raise CachedRepositoryTreeError(msg)
        completed = self._execute(
            [
                self._command,
                "--git-dir",
                str(cache_path),
                "cat-file",
                "--batch",
            ],
            input_data="".join(f"{blob_sha}\n" for blob_sha in requested).encode(),
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()[-1000:]
            msg = f"Cached repository blob batch read failed: returncode={completed.returncode} stderr={stderr!r}"
            raise CachedRepositoryTreeError(msg)
        return self._parse_blob_batch(completed.stdout, requested)

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

    def _read_blob(self, cache_path: Path, blob_sha: str) -> str | None:
        if not cache_path.is_dir():
            return None
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
        if completed.returncode != 0:
            return None
        return completed.stdout.decode("utf-8", errors="replace")

    def _parse_blob_batch(self, output: bytes, requested: tuple[str, ...]) -> dict[str, str]:
        contents: dict[str, str] = {}
        offset = 0
        for requested_sha in requested:
            header_end = output.find(b"\n", offset)
            if header_end < 0:
                msg = f"Cached blob batch response is incomplete: blob_sha={requested_sha}"
                raise CachedRepositoryTreeError(msg)
            header = output[offset:header_end]
            offset = header_end + 1
            if header.endswith(b" missing"):
                msg = f"Cached blob is absent: blob_sha={requested_sha}"
                raise CachedRepositoryTreeError(msg)
            fields = header.split()
            if len(fields) != 3 or fields[1] != b"blob":
                msg = f"Cached blob batch response has an invalid header: {header!r}"
                raise CachedRepositoryTreeError(msg)
            size = int(fields[2])
            content_end = offset + size
            if content_end >= len(output) or output[content_end : content_end + 1] != b"\n":
                msg = f"Cached blob batch response has an invalid body: blob_sha={requested_sha}"
                raise CachedRepositoryTreeError(msg)
            contents[requested_sha] = output[offset:content_end].decode("utf-8", errors="replace")
            offset = content_end + 1
        return contents

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

    def _execute(
        self,
        command: list[str],
        *,
        input_data: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            return subprocess.run(
                command,
                capture_output=True,
                check=False,
                env=environment,
                input=input_data,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            msg = f"Cached repository tree read timed out: {error}"
            raise CachedRepositoryTreeError(msg) from error


class CachedRepositoryTreeClient(LocalRepositoryTreeClient):
    """Prefer local bare Git caches and fall back when an object is absent."""

    __slots__ = ("_fallback",)

    def __init__(
        self,
        *,
        cache: repository_cache.GitRepositoryCache,
        fallback: RepositoryTreeClient,
        command: str = "git",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(cache=cache, command=command, timeout_seconds=timeout_seconds)
        self._fallback = fallback

    def get_complete_tree(self, repository: str, revision: str) -> github_client.RepositoryTree:
        """Return a cached tree, or delegate when the pinned revision is unavailable."""
        cache_path = self._cache.path(repository)
        if not self._revision_exists(cache_path, revision):
            return self._fallback.get_complete_tree(repository, revision)
        return self._read_tree(cache_path, revision)

    def get_text_blob(self, repository: str, blob_sha: str) -> str:
        """Return a cached text blob, or delegate when the object is unavailable."""
        content = self._read_blob(self._cache.path(repository), blob_sha)
        if content is not None:
            return content
        return self._fallback.get_text_blob(repository, blob_sha)

    def get_text_blobs(self, repository: str, blob_shas: tuple[str, ...]) -> dict[str, str]:
        """Return cached or delegated blobs while preserving fallback behavior."""
        return {blob_sha: self.get_text_blob(repository, blob_sha) for blob_sha in dict.fromkeys(blob_shas)}
