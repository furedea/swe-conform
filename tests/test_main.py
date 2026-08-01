import json
import os
from subprocess import CompletedProcess

import pytest
from pytest_mock import MockerFixture

import main


def test_main() -> None:
    main.main([])


def test_codex_cli_provider_defaults_to_max_reasoning_effort() -> None:
    assert main.effective_reasoning_effort(provider="codex-cli", configured=None) == "max"


def test_github_credential_falls_back_to_authenticated_gh_cli(mocker: MockerFixture) -> None:
    mocker.patch.dict(os.environ, {"GITHUB_TOKEN": "", "GH_TOKEN": ""})
    run = mocker.patch(
        "main.subprocess.run",
        autospec=True,
        return_value=CompletedProcess(args=[], returncode=0, stdout="github-credential\n", stderr=""),
    )

    credential = main.github_credential()

    assert credential == "github-credential"
    run.assert_called_once_with(
        ["gh", "auth", "token"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_validate_reports_tracked_repository_candidates(capsys: pytest.CaptureFixture[str]) -> None:
    main.main(["validate"])

    report = json.loads(capsys.readouterr().out)
    assert report["repositories"] == 5331
    assert report["unique_revisions"] == 5331
    assert report["languages"] == {
        "Java": 739,
        "JavaScript": 1007,
        "Python": 1803,
        "TypeScript": 1782,
    }
