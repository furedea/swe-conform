"""Tests for revision-pinned GitHub API retrieval."""

import base64
from unittest.mock import MagicMock

import httpx
import pytest_mock

import github_client


def test_client_follows_github_redirects_by_default(
    mocker: pytest_mock.MockerFixture,
) -> None:
    http_client_factory = mocker.patch("github_client.httpx.Client", autospec=True)

    github_client.GitHubClient(token="test-credential")

    http_client_factory.assert_called_once_with(timeout=60.0, follow_redirects=True)


def test_client_reads_recursive_tree_and_decodes_blob() -> None:
    tree_response = MagicMock(spec=httpx.Response)
    tree_response.json.return_value = {
        "tree": [
            {"path": "CONTRIBUTING.md", "sha": "blob-sha", "size": 42, "type": "blob"},
            {"path": "docs", "sha": "tree-sha", "type": "tree"},
        ],
        "truncated": False,
    }
    blob_response = MagicMock(spec=httpx.Response)
    blob_response.json.return_value = {
        "encoding": "base64",
        "content": base64.b64encode(b"Use snake_case.\n").decode("ascii"),
    }
    http_client = MagicMock(spec=httpx.Client)
    http_client.get.side_effect = (tree_response, blob_response)
    client = github_client.GitHubClient(token="test-credential", http_client=http_client)

    tree = client.get_tree("example/project", "0123456789abcdef")
    content = client.get_text_blob("example/project", "blob-sha")

    assert tree.entries == (github_client.TreeEntry(path="CONTRIBUTING.md", sha="blob-sha", size=42),)
    assert not tree.truncated
    assert content == "Use snake_case.\n"
    tree_call = http_client.get.call_args_list[0]
    assert tree_call.args[0].endswith("/repos/example/project/git/trees/0123456789abcdef")
    assert tree_call.kwargs["params"] == {"recursive": "1"}
    assert tree_call.kwargs["headers"]["Authorization"] == "Bearer test-credential"
