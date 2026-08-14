"""Tests for persistently cached GitHub repository content."""

from pathlib import Path

from pytest_mock import MockerFixture

import github_client
import github_repository


def test_github_repository_client_reuses_a_downloaded_blob_after_restart(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    first_client = mocker.Mock(spec=github_client.GitHubClient)
    first_client.get_text_blob.return_value = "Use ProjectNode in src/nodes/.\n"
    first_source = github_repository.PersistentGitHubRepositoryClient(
        client=first_client,
        content_root=tmp_path,
    )

    first_content = first_source.get_text_blob("example/project", "a" * 40)

    resumed_client = mocker.Mock(spec=github_client.GitHubClient)
    resumed_source = github_repository.PersistentGitHubRepositoryClient(
        client=resumed_client,
        content_root=tmp_path,
    )
    resumed_content = resumed_source.get_text_blob("example/project", "a" * 40)

    assert first_content == "Use ProjectNode in src/nodes/.\n"
    assert resumed_content == first_content
    first_client.get_text_blob.assert_called_once_with("example/project", "a" * 40)
    resumed_client.get_text_blob.assert_not_called()
