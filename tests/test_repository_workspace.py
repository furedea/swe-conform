"""Tests for revision-pinned repository snapshots."""

import subprocess
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from pytest_mock import MockerFixture

import repository_workspace


def test_workspace_materializes_a_revision_without_git_metadata(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    def run_side_effect(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        if command[1:3] == ["init", "--quiet"]:
            Path(command[3]).mkdir()
            (Path(command[3]) / ".git").mkdir()
        if "checkout" in command:
            repository_path = Path(command[2])
            (repository_path / "docs").mkdir()
            (repository_path / "docs" / "api_conventions.md").write_text("A project rule.\n", encoding="utf-8")
        return CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    run = mocker.patch("repository_workspace.subprocess.run", autospec=True, side_effect=run_side_effect)
    workspace = repository_workspace.GitRepositoryWorkspace(root=tmp_path)

    with workspace.checkout("example/project", "0123456789abcdef") as workspace_path:
        repository_path = workspace_path / "repository"
        assert (repository_path / "docs" / "api_conventions.md").is_file()
        assert not (repository_path / ".git").exists()

    commands = [call.args[0] for call in run.call_args_list]
    assert ["git", "init", "--quiet"] == commands[0][:3]
    assert ["git", "-C"] == commands[1][:2]
    assert "https://github.com/example/project.git" in commands[1]
    assert "0123456789abcdef" in commands[2]


def test_workspace_reports_a_checkout_timeout_as_a_retrieval_error(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    mocker.patch(
        "repository_workspace.subprocess.run",
        autospec=True,
        side_effect=subprocess.TimeoutExpired(cmd=["git", "fetch"], timeout=300),
    )
    workspace = repository_workspace.GitRepositoryWorkspace(root=tmp_path)

    with pytest.raises(repository_workspace.RepositoryCheckoutError, match="timed out after 300 seconds"):
        with workspace.checkout("example/project", "0123456789abcdef"):
            pytest.fail("checkout should not yield after a timeout")


def test_cached_workspace_stages_only_source_files_on_the_ssd(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    cache_root = tmp_path / "hdd"
    cache_path = cache_root / "example" / "project.git"
    cache_path.mkdir(parents=True)
    workspace_root = tmp_path / "ssd"
    workspace_root.mkdir()

    def run_side_effect(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        if "clone" in command:
            repository_path = Path(command[-1])
            repository_path.mkdir()
            (repository_path / ".git").mkdir()
        if "checkout" in command:
            repository_path = Path(command[2])
            (repository_path / "docs").mkdir()
            (repository_path / "docs" / "api_conventions.md").write_text("A project rule.\n", encoding="utf-8")
        return CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    run = mocker.patch("repository_workspace.subprocess.run", autospec=True, side_effect=run_side_effect)
    workspace = repository_workspace.CachedGitRepositoryWorkspace(
        cache_root=cache_root,
        root=workspace_root,
    )

    with workspace.checkout("example/project", "0123456789abcdef") as workspace_path:
        repository_path = workspace_path / "repository"
        assert (repository_path / "docs" / "api_conventions.md").is_file()
        assert not (repository_path / ".git").exists()

    commands = [call.args[0] for call in run.call_args_list]
    assert any(str(cache_path) in command for command in commands)
    assert all("https://" not in argument for command in commands for argument in command)
