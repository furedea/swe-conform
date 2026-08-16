"""Tests for revision-pinned full-history repository caches."""

import subprocess
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from pytest import MonkeyPatch
from pytest_mock import MockerFixture

import repository_cache


def test_cache_fetches_an_unfiltered_snapshot_revision(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    def run_side_effect(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        if command[1:4] == ["init", "--bare", "--quiet"]:
            Path(command[4]).mkdir(parents=True)
        if "show-ref" in command or "get-url" in command:
            return CompletedProcess(args=command, returncode=1, stdout="", stderr="")
        return CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    run = mocker.patch("repository_cache.subprocess.run", autospec=True, side_effect=run_side_effect)
    mocker.patch(
        "repository_cache.GitRepositoryCache.inspect_snapshot",
        autospec=True,
        return_value=repository_cache.SnapshotInspection(repository_cache.SnapshotState.COMPLETE),
    )
    cache = repository_cache.GitRepositoryCache(root=tmp_path)
    revision = "0123456789abcdef"

    disposition = cache.ensure_snapshot("example/project", revision)

    assert disposition is repository_cache.CacheDisposition.FETCHED
    commands = [call.args[0] for call in run.call_args_list]
    fetch_command = next(command for command in commands if "fetch" in command)
    assert "--depth" not in " ".join(fetch_command)
    assert "--filter" not in " ".join(fetch_command)
    assert f"{revision}:refs/snapshots/{revision}" in fetch_command
    assert any("https://github.com/example/project.git" in command for command in commands)


def test_cache_reuses_a_completed_snapshot_without_network_access(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "example" / "project.git"
    cache_path.mkdir(parents=True)
    run = mocker.patch(
        "repository_cache.subprocess.run",
        autospec=True,
        return_value=CompletedProcess(args=[], returncode=0, stdout="true\n", stderr=""),
    )
    mocker.patch(
        "repository_cache.GitRepositoryCache.inspect_snapshot",
        autospec=True,
        return_value=repository_cache.SnapshotInspection(repository_cache.SnapshotState.COMPLETE),
    )
    cache = repository_cache.GitRepositoryCache(root=tmp_path)

    disposition = cache.ensure_snapshot("example/project", "0123456789abcdef")

    assert disposition is repository_cache.CacheDisposition.CACHED
    commands = [call.args[0] for call in run.call_args_list]
    assert len(commands) == 2
    assert "rev-parse" in commands[0]
    assert "show-ref" in commands[1]
    assert all("fetch" not in command for command in commands)


def test_cache_rejects_a_snapshot_with_missing_reachable_objects(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "example" / "project.git"
    cache_path.mkdir(parents=True)
    cache = repository_cache.GitRepositoryCache(root=tmp_path)
    mocker.patch(
        "repository_cache.GitRepositoryCache.inspect_snapshot",
        autospec=True,
        return_value=repository_cache.SnapshotInspection(repository_cache.SnapshotState.SNAPSHOT_INCOMPLETE),
    )
    run = mocker.patch(
        "repository_cache.subprocess.run",
        autospec=True,
        return_value=CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    )

    with pytest.raises(repository_cache.RepositoryCacheError, match="incomplete"):
        cache.ensure_snapshot("example/project", "0123456789abcdef")

    assert all("fetch" not in call.args[0] for call in run.call_args_list)


def test_snapshot_inspection_reports_missing_reachable_objects_as_incomplete(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "example" / "project.git"
    cache_path.mkdir(parents=True)

    def run_side_effect(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        if "--is-bare-repository" in command:
            return CompletedProcess(args=command, returncode=0, stdout="true\n", stderr="")
        if "cat-file" in command:
            return CompletedProcess(args=command, returncode=0, stdout="", stderr="")
        if "rev-list" in command:
            return CompletedProcess(args=command, returncode=1, stdout="", stderr="missing blob")
        raise AssertionError(command)

    mocker.patch("repository_cache.subprocess.run", autospec=True, side_effect=run_side_effect)
    cache = repository_cache.GitRepositoryCache(root=tmp_path)

    inspection = cache.inspect_snapshot("example/project", "0123456789abcdef")

    assert inspection.state is repository_cache.SnapshotState.SNAPSHOT_INCOMPLETE
    assert inspection.detail == "missing blob"


def test_snapshot_inspection_discards_complete_object_listing_output(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "example" / "project.git"
    cache_path.mkdir(parents=True)
    run = mocker.patch(
        "repository_cache.subprocess.run",
        autospec=True,
        return_value=CompletedProcess(args=[], returncode=0, stdout="true\n", stderr=""),
    )
    cache = repository_cache.GitRepositoryCache(root=tmp_path)

    inspection = cache.inspect_snapshot("example/project", "0123456789abcdef")

    assert inspection.state is repository_cache.SnapshotState.COMPLETE
    rev_list_call = next(call for call in run.call_args_list if "rev-list" in call.args[0])
    assert rev_list_call.kwargs["stdout"] is subprocess.DEVNULL


def test_snapshot_inspection_detects_a_missing_blob_in_a_real_bare_repository(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    revision = _create_origin(origin)
    cache = repository_cache.GitRepositoryCache(root=tmp_path / "cache")
    cache_path = cache.path("example/project")
    cache_path.parent.mkdir(parents=True)
    _git("clone", "--bare", "--quiet", str(origin), str(cache_path))
    blob_sha = _git("--git-dir", str(cache_path), "rev-parse", f"{revision}:source.txt").stdout.strip()
    blob_path = cache_path / "objects" / blob_sha[:2] / blob_sha[2:]
    blob_path.unlink()

    inspection = cache.inspect_snapshot("example/project", revision)

    assert inspection.state is repository_cache.SnapshotState.SNAPSHOT_INCOMPLETE


def test_cache_repairs_an_empty_directory_before_fetching(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    origin = tmp_path / "origin"
    revision = _create_origin(origin)
    git_config = tmp_path / "gitconfig"
    git_config.write_text(
        f'[url "{origin.as_uri()}"]\n\tinsteadOf = https://github.com/example/project.git\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(git_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    cache_path = tmp_path / "example" / "project.git"
    cache_path.mkdir(parents=True)
    cache = repository_cache.GitRepositoryCache(root=tmp_path)

    disposition = cache.ensure_snapshot("example/project", revision)

    assert disposition is repository_cache.CacheDisposition.FETCHED
    snapshot = _git("--git-dir", str(cache_path), "show-ref", "--verify", f"refs/snapshots/{revision}")
    assert snapshot.stdout.split()[0] == revision


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
    _git("update-ref", f"refs/snapshots/{snapshot_revision}", snapshot_revision, cwd=origin)
    (origin / "source.txt").write_text("later\n", encoding="utf-8")
    _git("commit", "--quiet", "-am", "later", cwd=origin)
    later_revision = _git("rev-parse", "HEAD", cwd=origin).stdout.strip()

    cache = repository_cache.GitRepositoryCache(root=tmp_path / "cache")
    cache_path = cache.path("example/project")
    cache_path.parent.mkdir(parents=True)
    _git("init", "--bare", "--quiet", str(cache_path))
    _git("--git-dir", str(cache_path), "remote", "add", "origin", origin.as_uri())

    cache.ensure_snapshot("example/project", snapshot_revision)

    inspection = cache.inspect_snapshot("example/project", snapshot_revision)
    history_count = _git("--git-dir", str(cache_path), "rev-list", "--count", snapshot_revision)
    later_object = _git("--git-dir", str(cache_path), "cat-file", "-e", later_revision, check=False)
    missing_objects = _git("--git-dir", str(cache_path), "rev-list", "--objects", "--missing=print", snapshot_revision)
    assert history_count.stdout.strip() == "2"
    assert inspection.state is repository_cache.SnapshotState.COMPLETE
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


def _create_origin(origin: Path) -> str:
    origin.mkdir()
    _git("init", "--quiet", cwd=origin)
    _git("config", "user.email", "research@example.com", cwd=origin)
    _git("config", "user.name", "Research Fixture", cwd=origin)
    (origin / "source.txt").write_text("snapshot\n", encoding="utf-8")
    _git("add", "source.txt", cwd=origin)
    _git("commit", "--quiet", "-m", "snapshot", cwd=origin)
    revision = _git("rev-parse", "HEAD", cwd=origin).stdout.strip()
    _git("update-ref", f"refs/snapshots/{revision}", revision, cwd=origin)
    return revision
