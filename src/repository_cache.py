"""Full-history Git caches anchored at revision-pinned snapshots."""

import os
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_DEFAULT_TIMEOUT_SECONDS = 3600.0


class CacheDisposition(StrEnum):
    """How a requested snapshot was satisfied."""

    CACHED = "cached"
    FETCHED = "fetched"


class SnapshotState(StrEnum):
    """Availability of one revision-pinned snapshot in a bare Git cache."""

    COMPLETE = "complete"
    REPOSITORY_ABSENT = "repository_absent"
    REPOSITORY_INVALID = "repository_invalid"
    REVISION_ABSENT = "revision_absent"
    SNAPSHOT_INCOMPLETE = "snapshot_incomplete"


@dataclass(frozen=True, slots=True)
class SnapshotInspection:
    """Result of checking every object reachable from a pinned revision."""

    state: SnapshotState
    detail: str = ""

    @property
    def complete(self) -> bool:
        """Return whether the complete snapshot is available locally."""
        return self.state is SnapshotState.COMPLETE


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
        remote_url = f"https://github.com/{repository}.git"
        if not cache_path.exists() or not self._repository_exists(cache_path):
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._run([self._command, "init", "--bare", "--quiet", str(cache_path)])
        if self._snapshot_exists(cache_path, revision):
            inspection = self.inspect_snapshot(repository, revision)
            if inspection.complete:
                return CacheDisposition.CACHED
            msg = (
                "Repository cache contains an incomplete pinned snapshot: "
                f"repository={repository} revision={revision} state={inspection.state.value}"
            )
            raise RepositoryCacheError(msg)
        if not self._origin_exists(cache_path):
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
        inspection = self.inspect_snapshot(repository, revision)
        if not inspection.complete:
            msg = (
                "Repository cache fetch completed without a complete pinned snapshot: "
                f"repository={repository} revision={revision} state={inspection.state.value}"
            )
            raise RepositoryCacheError(msg)
        return CacheDisposition.FETCHED

    def inspect_snapshot(self, repository: str, revision: str) -> SnapshotInspection:
        """Check that a pinned commit and every reachable Git object are local."""
        cache_path = self.path(repository)
        if not cache_path.is_dir():
            return SnapshotInspection(SnapshotState.REPOSITORY_ABSENT)
        if not self._repository_exists(cache_path):
            return SnapshotInspection(SnapshotState.REPOSITORY_INVALID)
        if not self._revision_exists(cache_path, revision):
            return SnapshotInspection(SnapshotState.REVISION_ABSENT)
        completed = self._inspect_object_graph(
            [
                self._command,
                "--git-dir",
                str(cache_path),
                "rev-list",
                "--objects",
                "--missing=error",
                revision,
            ],
        )
        if completed.returncode != 0:
            return SnapshotInspection(
                SnapshotState.SNAPSHOT_INCOMPLETE,
                detail=completed.stderr.strip()[-1000:],
            )
        return SnapshotInspection(SnapshotState.COMPLETE)

    def _repository_exists(self, cache_path: Path) -> bool:
        completed = self._execute(
            [
                self._command,
                "--git-dir",
                str(cache_path),
                "rev-parse",
                "--is-bare-repository",
            ],
        )
        return completed.returncode == 0 and completed.stdout.strip() == "true"

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

    def _revision_exists(self, cache_path: Path, revision: str) -> bool:
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
        environment = {
            **os.environ,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
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

    def _inspect_object_graph(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        try:
            return subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env=environment,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            msg = f"Repository cache inspection timed out: {error}"
            raise RepositoryCacheError(msg) from error

    def _raise_for_failure(self, completed: subprocess.CompletedProcess[str]) -> None:
        stderr = completed.stderr.strip()[-1000:]
        msg = f"Repository cache fetch failed: returncode={completed.returncode} stderr={stderr!r}"
        raise RepositoryCacheError(msg)
