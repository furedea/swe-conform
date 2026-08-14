"""Tests for revision-pinned GitHub API retrieval."""

import base64
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import cast
from unittest.mock import MagicMock

import httpx
import pytest_mock

import github_client


class ConcurrentHTTPClient:
    """Record concurrent calls while behaving like a successful HTTP client."""

    def __init__(self) -> None:
        self.active_requests = 0
        self.maximum_active_requests = 0
        self.lock = threading.Lock()

    def get(self, *_args: object, **_kwargs: object) -> MagicMock:
        with self.lock:
            self.active_requests += 1
            self.maximum_active_requests = max(self.maximum_active_requests, self.active_requests)
        time.sleep(0.02)
        with self.lock:
            self.active_requests -= 1
        response = MagicMock(spec=httpx.Response)
        response.content = b"content\n"
        return response

    def close(self) -> None:
        """Match the httpx client boundary."""


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


def test_client_recovers_a_complete_tree_from_truncated_recursive_subtrees() -> None:
    root_recursive = MagicMock(spec=httpx.Response)
    root_recursive.json.return_value = {"tree": [], "truncated": True}
    root_shallow = MagicMock(spec=httpx.Response)
    root_shallow.json.return_value = {
        "tree": [
            {"path": "ROOT.md", "sha": "blob-root", "size": 10, "type": "blob"},
            {"path": "docs", "sha": "tree-docs", "type": "tree"},
            {"path": "src", "sha": "tree-src", "type": "tree"},
        ],
        "truncated": False,
    }
    docs_recursive = MagicMock(spec=httpx.Response)
    docs_recursive.json.return_value = {
        "tree": [
            {"path": "guide.md", "sha": "blob-guide", "size": 20, "type": "blob"},
            {"path": "nested/rules.md", "sha": "blob-rules", "size": 30, "type": "blob"},
        ],
        "truncated": False,
    }
    src_recursive = MagicMock(spec=httpx.Response)
    src_recursive.json.return_value = {
        "tree": [
            {"path": "main.py", "sha": "blob-main", "size": 40, "type": "blob"},
        ],
        "truncated": False,
    }
    http_client = MagicMock(spec=httpx.Client)
    http_client.get.side_effect = (
        root_recursive,
        root_shallow,
        docs_recursive,
        src_recursive,
    )
    client = github_client.GitHubClient(token="test-credential", http_client=http_client)

    tree = client.get_complete_tree("example/project", "0123456789abcdef")

    assert [entry.path for entry in tree.entries] == [
        "ROOT.md",
        "docs/guide.md",
        "docs/nested/rules.md",
        "src/main.py",
    ]
    assert tree.truncated is False


def test_client_reads_one_text_file_from_the_pinned_raw_url() -> None:
    response = MagicMock(spec=httpx.Response)
    response.content = b"Use snake_case.\n"
    http_client = MagicMock(spec=httpx.Client)
    http_client.get.return_value = response
    client = github_client.GitHubClient(token="test-credential", http_client=http_client)

    content = client.get_text_file(
        "example/project",
        "0123456789abcdef",
        "docs/Coding Guide.md",
    )

    assert content == "Use snake_case.\n"
    request = http_client.get.call_args
    assert request.args == (
        "https://raw.githubusercontent.com/example/project/0123456789abcdef/docs/Coding%20Guide.md",
    )


def test_client_retries_a_transient_network_failure(
    mocker: pytest_mock.MockerFixture,
) -> None:
    response = MagicMock(spec=httpx.Response)
    response.content = b"Use snake_case.\n"
    http_client = MagicMock(spec=httpx.Client)
    http_client.get.side_effect = (httpx.ReadTimeout("timed out"), response)
    sleep = mocker.patch("github_client.time.sleep", autospec=True)
    client = github_client.GitHubClient(token="test-credential", http_client=http_client)

    content = client.get_text_file(
        "example/project",
        "0123456789abcdef",
        "CONTRIBUTING.md",
    )

    assert content == "Use snake_case.\n"
    assert http_client.get.call_count == 2
    sleep.assert_called_once_with(1.0)


def test_client_honors_retry_after_before_retrying_a_rate_limit(
    mocker: pytest_mock.MockerFixture,
) -> None:
    request = httpx.Request("GET", "https://api.github.com/repos/example/project/git/trees/revision")
    rate_limited = httpx.Response(403, headers={"retry-after": "7"}, request=request)
    success = MagicMock(spec=httpx.Response)
    success.content = b"Use snake_case.\n"
    http_client = MagicMock(spec=httpx.Client)
    http_client.get.side_effect = (rate_limited, success)
    sleep = mocker.patch("github_client.time.sleep", autospec=True)
    client = github_client.GitHubClient(token="test-credential", http_client=http_client)

    content = client.get_text_file(
        "example/project",
        "0123456789abcdef",
        "CONTRIBUTING.md",
    )

    assert content == "Use snake_case.\n"
    sleep.assert_called_once_with(7.0)


def test_client_waits_until_the_primary_rate_limit_resets(
    mocker: pytest_mock.MockerFixture,
) -> None:
    request = httpx.Request("GET", "https://api.github.com/repos/example/project/git/trees/revision")
    rate_limited = httpx.Response(
        403,
        headers={
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset": "110",
        },
        request=request,
    )
    success = MagicMock(spec=httpx.Response)
    success.content = b"Use snake_case.\n"
    http_client = MagicMock(spec=httpx.Client)
    http_client.get.side_effect = (rate_limited, success)
    mocker.patch("github_client.time.time", autospec=True, return_value=100.0)
    sleep = mocker.patch("github_client.time.sleep", autospec=True)
    client = github_client.GitHubClient(token="test-credential", http_client=http_client)

    content = client.get_text_file(
        "example/project",
        "0123456789abcdef",
        "CONTRIBUTING.md",
    )

    assert content == "Use snake_case.\n"
    sleep.assert_called_once_with(11.0)


def test_client_serializes_github_requests() -> None:
    http_client = ConcurrentHTTPClient()
    client = github_client.GitHubClient(
        token="test-credential",
        http_client=cast(httpx.Client, http_client),
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = tuple(
            executor.submit(
                client.get_text_file,
                "example/project",
                "0123456789abcdef",
                f"docs/{index}.md",
            )
            for index in range(2)
        )
        assert [future.result() for future in futures] == ["content\n", "content\n"]

    assert http_client.maximum_active_requests == 1
