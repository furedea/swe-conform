import json
import os
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from pytest_mock import MockerFixture

import batch_runner
import cache_runner
import codex_cli_client
import main
import repository_workspace


def test_main() -> None:
    main.main([])


def test_command_is_named_swe_conform() -> None:
    assert main._parser().prog == "swe-conform"


def test_codex_cli_provider_defaults_to_max_reasoning_effort() -> None:
    assert main.effective_reasoning_effort(provider="codex-cli", configured=None) == "max"


def test_filter_defaults_to_four_concurrent_codex_processes() -> None:
    arguments = main._parser().parse_args(["filter"])

    assert arguments.workers == 4


def test_filter_runs_codex_in_the_pinned_docker_image_by_default() -> None:
    arguments = main._parser().parse_args(["filter"])

    client = main._model_client(arguments)

    assert arguments.codex_runtime == "docker"
    assert arguments.codex_image == "swe-conform-codex:0.146.0"
    assert isinstance(client, codex_cli_client.DockerCodexCliClient)


def test_docker_image_id_returns_the_local_content_digest(mocker: MockerFixture) -> None:
    run = mocker.patch(
        "main.subprocess.run",
        autospec=True,
        return_value=CompletedProcess(
            args=[],
            returncode=0,
            stdout="sha256:7ee758b81b82\n",
            stderr="",
        ),
    )

    image_id = main.docker_image_id("docker", "swe-conform-codex:0.146.0")

    assert image_id == "sha256:7ee758b81b82"
    run.assert_called_once_with(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            "swe-conform-codex:0.146.0",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_filter_completes_sandbox_preflight_before_repository_submissions(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    mocker.patch("main.repository.load_repository_candidates", autospec=True, return_value=())
    mocker.patch("main._repository_workspace", autospec=True)
    mocker.patch("main.docker_image_id", autospec=True, return_value="sha256:image")
    mocker.patch("main.result_store.ResultStore", autospec=True)
    model_client = mocker.Mock(spec=codex_cli_client.DockerCodexCliClient)
    mocker.patch("main._model_client", autospec=True, return_value=model_client)
    mocker.patch("main.guideline_classifier.ModelGuidelineChecker", autospec=True)
    mocker.patch("main.pipeline.RepositoryFilter", autospec=True)
    runner = mocker.patch("main.batch_runner.BatchRunner", autospec=True)
    runner.return_value.run.return_value = batch_runner.RunStats(
        requested=0,
        skipped=0,
        evaluated=0,
        elapsed_seconds=0.0,
    )
    calls = mocker.Mock()
    calls.attach_mock(model_client.preflight, "preflight")
    calls.attach_mock(runner.return_value.run, "run")

    main.main(["filter", "--output-dir", str(tmp_path / "output")])

    assert [call[0] for call in calls.method_calls] == ["preflight", "run"]


def test_filter_aborts_before_submissions_when_sandbox_preflight_fails(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    mocker.patch("main.repository.load_repository_candidates", autospec=True, return_value=())
    mocker.patch("main._repository_workspace", autospec=True)
    mocker.patch("main.docker_image_id", autospec=True, return_value="sha256:image")
    mocker.patch("main.result_store.ResultStore", autospec=True)
    model_client = mocker.Mock(spec=codex_cli_client.DockerCodexCliClient)
    model_client.preflight.side_effect = codex_cli_client.CodexSandboxError("bwrap unavailable")
    mocker.patch("main._model_client", autospec=True, return_value=model_client)
    runner = mocker.patch("main.batch_runner.BatchRunner", autospec=True)

    with pytest.raises(codex_cli_client.CodexSandboxError, match="bwrap unavailable"):
        main.main(["filter", "--output-dir", str(tmp_path / "output")])

    runner.assert_not_called()
    model_client.close.assert_called_once_with()


def test_preflight_command_checks_the_configured_docker_image(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mocker.patch("main.docker_image_id", autospec=True, return_value="sha256:image")
    client = mocker.patch("main.codex_cli_client.DockerCodexCliClient", autospec=True)

    main.main(["preflight"])

    client.assert_called_once_with(
        docker_command="docker",
        image="swe-conform-codex:0.146.0",
        source_codex_home=None,
    )
    client.return_value.preflight.assert_called_once_with()
    assert json.loads(capsys.readouterr().out) == {
        "codex_image": "swe-conform-codex:0.146.0",
        "codex_image_id": "sha256:image",
        "sandbox": "ready",
    }


def test_filter_allows_fifteen_minutes_for_repository_checkout() -> None:
    arguments = main._parser().parse_args(["filter"])

    assert arguments.checkout_timeout_seconds == 900


def test_fetch_requires_hdd_cache_and_allows_one_hour_per_repository() -> None:
    arguments = main._parser().parse_args(["fetch", "--cache-root", "/mnt/hdd/repositories"])

    assert str(arguments.cache_root) == "/mnt/hdd/repositories"
    assert arguments.fetch_timeout_seconds == 3600
    assert arguments.workers == 4


def test_filter_accepts_separate_hdd_cache_and_ssd_workspace_roots() -> None:
    arguments = main._parser().parse_args(
        [
            "filter",
            "--cache-root",
            "/mnt/hdd/repositories",
            "--workspace-root",
            "/mnt/ssd/workspaces",
        ],
    )

    assert str(arguments.cache_root) == "/mnt/hdd/repositories"
    assert str(arguments.workspace_root) == "/mnt/ssd/workspaces"


def test_filter_uses_the_local_cache_when_cache_and_workspace_roots_are_given() -> None:
    arguments = main._parser().parse_args(
        [
            "filter",
            "--cache-root",
            "/mnt/hdd/repositories",
            "--workspace-root",
            "/mnt/ssd/workspaces",
        ],
    )

    workspace = main._repository_workspace(arguments)

    assert isinstance(workspace, repository_workspace.CachedGitRepositoryWorkspace)


def test_filter_keeps_the_direct_checkout_mode_for_small_pilots() -> None:
    arguments = main._parser().parse_args(["filter", "--limit", "20"])

    workspace = main._repository_workspace(arguments)

    assert isinstance(workspace, repository_workspace.GitRepositoryWorkspace)


def test_fetch_command_runs_the_resumable_cache_batch(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    candidates = mocker.patch("main.repository.load_repository_candidates", autospec=True, return_value=())
    cache = mocker.patch("main.repository_cache.GitRepositoryCache", autospec=True)
    runner = mocker.patch("main.cache_runner.CacheBatchRunner", autospec=True)
    runner.return_value.run.return_value = cache_runner.CacheBatchReport(
        results=(),
        stats=cache_runner.CacheRunStats(
            requested=0,
            fetched=0,
            cached=0,
            errors=0,
            elapsed_seconds=1.25,
        ),
    )

    main.main(
        [
            "fetch",
            "--cache-root",
            "/mnt/hdd/repositories",
            "--result-path",
            str(tmp_path / "fetch_results.jsonl"),
            "--limit",
            "20",
        ],
    )

    candidates.assert_called_once()
    cache.assert_called_once_with(
        root=main.Path("/mnt/hdd/repositories"),
        command="git",
        timeout_seconds=3600,
    )
    runner.assert_called_once_with(cache=cache.return_value, workers=4)
    assert json.loads(capsys.readouterr().out)["elapsed_seconds"] == 1.25


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
    assert report["input_dir"] == "docs/data/repository-candidates-new"
    assert report["repositories"] == 4359
    assert report["unique_revisions"] == 4359
    assert report["snapshot_start"] == "2026-01-01T00:00:00+00:00"
    assert report["snapshot_cutoff"] == "2026-08-01T00:00:00+00:00"
    assert report["languages"] == {
        "Java": 658,
        "JavaScript": 837,
        "Python": 1271,
        "TypeScript": 1593,
    }
