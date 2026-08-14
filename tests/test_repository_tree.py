"""Tests for repository-tree retrieval with local Git-cache reuse."""

import subprocess
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from pytest_mock import MockerFixture

import github_client
import repository_cache
import repository_tree


def test_tree_client_reads_the_pinned_revision_from_the_local_cache(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    revision, cache_root = _create_cached_repository(tmp_path)
    fallback = mocker.Mock(spec=github_client.GitHubClient)
    client = repository_tree.CachedRepositoryTreeClient(
        cache=repository_cache.GitRepositoryCache(root=cache_root),
        fallback=fallback,
    )

    tree = client.get_complete_tree("example/project", revision)

    assert tree == github_client.RepositoryTree(
        entries=(
            github_client.TreeEntry(
                path="README.md",
                sha=mocker.ANY,
                size=len("# Example\n"),
                mode="100644",
            ),
        ),
        truncated=False,
    )
    fallback.get_complete_tree.assert_not_called()


def test_tree_client_reads_text_blobs_from_the_local_cache(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    revision, cache_root = _create_cached_repository(tmp_path)
    fallback = mocker.Mock(spec=github_client.GitHubClient)
    client = repository_tree.CachedRepositoryTreeClient(
        cache=repository_cache.GitRepositoryCache(root=cache_root),
        fallback=fallback,
    )
    tree = client.get_complete_tree("example/project", revision)

    content = client.get_text_blob("example/project", tree.entries[0].sha)

    assert content == "# Example\n"
    fallback.get_text_blob.assert_not_called()


def test_local_tree_client_reads_multiple_text_blobs_in_one_git_process(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    revision, cache_root = _create_cached_repository(tmp_path)
    client = repository_tree.LocalRepositoryTreeClient(
        cache=repository_cache.GitRepositoryCache(root=cache_root),
    )
    tree = client.get_complete_tree("example/project", revision)
    run = mocker.spy(repository_tree.subprocess, "run")

    contents = client.get_text_blobs("example/project", (tree.entries[0].sha,))

    assert contents == {tree.entries[0].sha: "# Example\n"}
    assert run.call_count == 1


def test_tree_client_falls_back_when_the_pinned_revision_is_not_cached(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    _, cache_root = _create_cached_repository(tmp_path)
    expected = github_client.RepositoryTree(entries=(), truncated=False)
    fallback = mocker.Mock(spec=github_client.GitHubClient)
    fallback.get_complete_tree.return_value = expected
    client = repository_tree.CachedRepositoryTreeClient(
        cache=repository_cache.GitRepositoryCache(root=cache_root),
        fallback=fallback,
    )

    tree = client.get_complete_tree("example/project", "0" * 40)

    assert tree is expected
    fallback.get_complete_tree.assert_called_once_with("example/project", "0" * 40)


def test_local_tree_client_rejects_a_missing_pinned_revision(tmp_path: Path) -> None:
    _, cache_root = _create_cached_repository(tmp_path)
    client = repository_tree.LocalRepositoryTreeClient(
        cache=repository_cache.GitRepositoryCache(root=cache_root),
    )

    with pytest.raises(repository_tree.CachedRepositoryTreeError, match="pinned revision is absent"):
        client.get_complete_tree("example/project", "0" * 40)


def test_tree_client_excludes_gitlinks_from_blob_entries(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    revision, cache_root = _create_cached_repository(tmp_path, include_gitlink=True)
    fallback = mocker.Mock(spec=github_client.GitHubClient)
    client = repository_tree.CachedRepositoryTreeClient(
        cache=repository_cache.GitRepositoryCache(root=cache_root),
        fallback=fallback,
    )

    tree = client.get_complete_tree("example/project", revision)

    assert [entry.path for entry in tree.entries] == ["README.md"]


def _create_cached_repository(tmp_path: Path, *, include_gitlink: bool = False) -> tuple[str, Path]:
    source = tmp_path / "source"
    source.mkdir()
    _git("init", "--quiet", cwd=source)
    _git("config", "user.email", "research@example.com", cwd=source)
    _git("config", "user.name", "Research Fixture", cwd=source)
    (source / "README.md").write_text("# Example\n", encoding="utf-8")
    _git("add", "README.md", cwd=source)
    _git("commit", "--quiet", "-m", "docs: add readme", cwd=source)
    if include_gitlink:
        gitlink_revision = _git("rev-parse", "HEAD", cwd=source).stdout.strip()
        _git("update-index", "--add", "--cacheinfo", f"160000,{gitlink_revision},vendor/module", cwd=source)
        _git("commit", "--quiet", "-m", "build: add submodule pointer", cwd=source)
    revision = _git("rev-parse", "HEAD", cwd=source).stdout.strip()
    cache_root = tmp_path / "cache"
    cache_path = cache_root / "example" / "project.git"
    cache_path.parent.mkdir(parents=True)
    _git("clone", "--bare", "--quiet", str(source), str(cache_path))
    return revision, cache_root


def _git(*arguments: str, cwd: Path | None = None) -> CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
