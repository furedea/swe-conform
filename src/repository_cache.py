"""Full-history Git caches anchored at revision-pinned snapshots."""

import os
import subprocess
from enum import StrEnum
from pathlib import Path

_DEFAULT_TIMEOUT_SECONDS = 3600.0


class CacheDisposition(StrEnum):
    """How a requested snapshot was satisfied."""

    CACHED = "cached"
    FETCHED = "fetched"


class RepositoryCacheError(RuntimeError):
    """A repository snapshot could not be cached."""


class GitRepositoryCache:
    """Store complete Git object graphs for pinned GitHub revisions."""

    __slots__ = ("_command", "_root", "_timeout_seconds")

    def __init__(
        self,
        *,
        root: Path,
        command: str = "git",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._root = root
        self._command = command
        self._timeout_seconds = timeout_seconds

    def ensure_snapshot(self, repository: str, revision: str) -> CacheDisposition:
        """Fetch every Git object reachable from a pinned revision."""
        cache_path = self.path(repository)
        if cache_path.exists() and self._snapshot_exists(cache_path, revision):
            return CacheDisposition.CACHED
        remote_url = f"https://github.com/{repository}.git"
        if not cache_path.exists():
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._run([self._command, "init", "--bare", "--quiet", str(cache_path)])
            self._add_origin(cache_path, remote_url)
        elif not self._origin_exists(cache_path):
            self._add_origin(cache_path, remote_url)
        self._run(
            [
                self._command,
                "--git-dir",
                str(cache_path),
                "fetch",
                "--quiet",
                "--no-tags",
                "origin",
                f"{revision}:refs/snapshots/{revision}",
            ],
        )
        return CacheDisposition.FETCHED

    def path(self, repository: str) -> Path:
        """Return the bare-cache path for an owner/repository name."""
        owner, separator, name = repository.partition("/")
        if separator != "/" or owner in {"", ".", ".."} or name in {"", ".", ".."} or "/" in name:
            msg = f"Repository name must be owner/repository: {repository!r}"
            raise ValueError(msg)
        return self._root / owner / f"{name}.git"

    def _origin_exists(self, cache_path: Path) -> bool:
        completed = self._execute(
            [
                self._command,
                "--git-dir",
                str(cache_path),
                "remote",
                "get-url",
                "origin",
            ],
        )
        return completed.returncode == 0

    def _add_origin(self, cache_path: Path, remote_url: str) -> None:
        self._run(
            [
                self._command,
                "--git-dir",
                str(cache_path),
                "remote",
                "add",
                "origin",
                remote_url,
            ],
        )

    def _snapshot_exists(self, cache_path: Path, revision: str) -> bool:
        completed = self._execute(
            [
                self._command,
                "--git-dir",
                str(cache_path),
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/snapshots/{revision}",
            ],
        )
        if completed.returncode in {0, 1}:
            return completed.returncode == 0
        self._raise_for_failure(completed)
        return False

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        completed = self._execute(command)
        if completed.returncode == 0:
            return completed
        self._raise_for_failure(completed)
        return completed

    def _execute(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                env=environment,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            msg = f"Repository cache fetch failed: {error}"
            raise RepositoryCacheError(msg) from error

    def _raise_for_failure(self, completed: subprocess.CompletedProcess[str]) -> None:
        stderr = completed.stderr.strip()[-1000:]
        msg = f"Repository cache fetch failed: returncode={completed.returncode} stderr={stderr!r}"
        raise RepositoryCacheError(msg)
