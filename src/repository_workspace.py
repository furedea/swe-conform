"""Revision-pinned repository snapshots for isolated agent inspection."""

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import repository_cache

_DEFAULT_TIMEOUT_SECONDS = 900.0


class RepositoryCheckoutError(RuntimeError):
    """A repository revision could not be materialized."""


class GitRepositoryWorkspace:
    """Materialize public GitHub revisions in disposable workspaces."""

    __slots__ = ("_command", "_root", "_timeout_seconds")

    def __init__(
        self,
        *,
        command: str = "git",
        root: Path | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._command = command
        self._root = root
        self._timeout_seconds = timeout_seconds

    @contextmanager
    def checkout(self, repository: str, revision: str) -> Iterator[Path]:
        """Yield a workspace containing a detached source snapshot."""
        with tempfile.TemporaryDirectory(prefix="swe-conform-repository-", dir=self._root) as workspace:
            workspace_path = Path(workspace)
            repository_path = workspace_path / "repository"
            _run_git([self._command, "init", "--quiet", str(repository_path)], self._timeout_seconds)
            _run_git(
                [
                    self._command,
                    "-C",
                    str(repository_path),
                    "remote",
                    "add",
                    "origin",
                    f"https://github.com/{repository}.git",
                ],
                self._timeout_seconds,
            )
            _run_git(
                [
                    self._command,
                    "-C",
                    str(repository_path),
                    "fetch",
                    "--quiet",
                    "--depth=1",
                    "origin",
                    revision,
                ],
                self._timeout_seconds,
            )
            _run_git(
                [
                    self._command,
                    "-C",
                    str(repository_path),
                    "checkout",
                    "--quiet",
                    "--detach",
                    "FETCH_HEAD",
                ],
                self._timeout_seconds,
            )
            shutil.rmtree(repository_path / ".git")
            yield workspace_path


class CachedGitRepositoryWorkspace:
    """Stage cached revisions on local workspace storage without network access."""

    __slots__ = ("_cache", "_command", "_root", "_timeout_seconds")

    def __init__(
        self,
        *,
        cache_root: Path,
        root: Path,
        command: str = "git",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._cache = repository_cache.GitRepositoryCache(root=cache_root, command=command)
        self._command = command
        self._root = root
        self._timeout_seconds = timeout_seconds

    @contextmanager
    def checkout(self, repository: str, revision: str) -> Iterator[Path]:
        """Yield a source-only workspace staged from a local bare cache."""
        cache_path = self._cache.path(repository)
        if not cache_path.is_dir():
            msg = f"Repository cache is missing: {cache_path}"
            raise RepositoryCheckoutError(msg)
        with tempfile.TemporaryDirectory(prefix="swe-conform-repository-", dir=self._root) as workspace:
            workspace_path = Path(workspace)
            repository_path = workspace_path / "repository"
            _run_git(
                [
                    self._command,
                    "clone",
                    "--quiet",
                    "--shared",
                    "--no-checkout",
                    str(cache_path),
                    str(repository_path),
                ],
                self._timeout_seconds,
            )
            _run_git(
                [
                    self._command,
                    "-C",
                    str(repository_path),
                    "checkout",
                    "--quiet",
                    "--detach",
                    revision,
                ],
                self._timeout_seconds,
            )
            shutil.rmtree(repository_path / ".git")
            yield workspace_path


def _run_git(command: list[str], timeout_seconds: float) -> None:
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        msg = f"Repository checkout failed: {error}"
        raise RepositoryCheckoutError(msg) from error
    if completed.returncode == 0:
        return
    stderr = completed.stderr.strip()[-1000:]
    msg = f"Repository checkout failed: returncode={completed.returncode} stderr={stderr!r}"
    raise RepositoryCheckoutError(msg)
