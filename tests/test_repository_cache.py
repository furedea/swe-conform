"""Tests for revision-pinned full-history repository caches."""

import subprocess
from pathlib import Path
from subprocess import CompletedProcess

from pytest_mock import MockerFixture

import repository_cache


def test_cache_fetches_complete_history_and_blobs_for_the_snapshot_revision(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    def run_side_effect(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        if command[1:4] == ["init", "--bare", "--quiet"]:
            Path(command[4]).mkdir(parents=True)
        return CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    run = mocker.patch("repository_cache.subprocess.run", autospec=True, side_effect=run_side_effect)
    cache = repository_cache.GitRepositoryCache(root=tmp_path)
    revision = "0123456789abcdef"

    disposition = cache.ensure_snapshot("example/project", revision)

    assert disposition is repository_cache.CacheDisposition.FETCHED
    commands = [call.args[0] for call in run.call_args_list]
    fetch_command = next(command for command in commands if "fetch" in command)
    assert "--depth" not in " ".join(fetch_command)
    assert "--filter" not in " ".join(fetch_command)
    assert "--no-tags" in fetch_command
    assert f"{revision}:refs/snapshots/{revision}" in fetch_command
    assert "https://github.com/example/project.git" in commands[1]


def test_cache_reuses_a_completed_snapshot_without_network_access(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "example" / "project.git"
    cache_path.mkdir(parents=True)
    run = mocker.patch(
        "repository_cache.subprocess.run",
        autospec=True,
        return_value=CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    )
    cache = repository_cache.GitRepositoryCache(root=tmp_path)

    disposition = cache.ensure_snapshot("example/project", "0123456789abcdef")

    assert disposition is repository_cache.CacheDisposition.CACHED
    commands = [call.args[0] for call in run.call_args_list]
    assert len(commands) == 1
    assert "show-ref" in commands[0]
    assert "fetch" not in commands[0]


def test_cache_repairs_an_interrupted_initialization_before_fetching(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "example" / "project.git"
    cache_path.mkdir(parents=True)

    def run_side_effect(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        if "show-ref" in command or "get-url" in command:
            return CompletedProcess(args=command, returncode=1, stdout="", stderr="")
        return CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    run = mocker.patch("repository_cache.subprocess.run", autospec=True, side_effect=run_side_effect)
    cache = repository_cache.GitRepositoryCache(root=tmp_path)

    disposition = cache.ensure_snapshot("example/project", "0123456789abcdef")

    assert disposition is repository_cache.CacheDisposition.FETCHED
    commands = [call.args[0] for call in run.call_args_list]
    assert any("remote" in command and "add" in command for command in commands)
    assert any("fetch" in command for command in commands)


def test_cache_contains_snapshot_ancestors_but_not_later_descendants(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "--quiet", cwd=origin)
    _git("config", "user.email", "research@example.com", cwd=origin)
    _git("config", "user.name", "Research Fixture", cwd=origin)
    (origin / "source.txt").write_text("first\n", encoding="utf-8")
    _git("add", "source.txt", cwd=origin)
    _git("commit", "--quiet", "-m", "first", cwd=origin)
    (origin / "source.txt").write_text("second\n", encoding="utf-8")
    _git("commit", "--quiet", "-am", "second", cwd=origin)
    snapshot_revision = _git("rev-parse", "HEAD", cwd=origin).stdout.strip()
    (origin / "source.txt").write_text("later\n", encoding="utf-8")
    _git("commit", "--quiet", "-am", "later", cwd=origin)
    later_revision = _git("rev-parse", "HEAD", cwd=origin).stdout.strip()

    cache = repository_cache.GitRepositoryCache(root=tmp_path / "cache")
    cache_path = cache.path("example/project")
    cache_path.parent.mkdir(parents=True)
    _git("init", "--bare", "--quiet", str(cache_path))
    _git("--git-dir", str(cache_path), "remote", "add", "origin", str(origin))

    cache.ensure_snapshot("example/project", snapshot_revision)

    history_count = _git("--git-dir", str(cache_path), "rev-list", "--count", snapshot_revision)
    later_object = _git("--git-dir", str(cache_path), "cat-file", "-e", later_revision, check=False)
    missing_objects = _git("--git-dir", str(cache_path), "rev-list", "--objects", "--missing=print", snapshot_revision)
    assert history_count.stdout.strip() == "2"
    assert later_object.returncode != 0
    assert not any(line.startswith("?") for line in missing_objects.stdout.splitlines())


def _git(*arguments: str, cwd: Path | None = None, check: bool = True) -> CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )
