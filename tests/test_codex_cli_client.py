"""Tests for isolated Codex CLI structured classification."""

import json
from dataclasses import astuple
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from pytest_mock import MockerFixture

import codex_cli_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _command_path(command: list[str], option: str) -> Path:
    return Path(command[command.index(option) + 1])


def test_client_runs_luna_max_without_user_harness(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    schemas: list[dict[str, object]] = []

    def run_side_effect(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        schemas.append(json.loads(_command_path(command, "--output-schema").read_text(encoding="utf-8")))
        _command_path(command, "--output-last-message").write_text(
            '{"status":"pass"}',
            encoding="utf-8",
        )
        events = json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20},
            },
        )
        return CompletedProcess(args=command, returncode=0, stdout=events, stderr="")

    run = mocker.patch("codex_cli_client.subprocess.run", autospec=True, side_effect=run_side_effect)
    client = codex_cli_client.CodexCliClient(command="codex", workspace_root=tmp_path)
    working_directory = tmp_path / "workspace"
    working_directory.mkdir()

    result = client.complete_json(
        instructions="system",
        input_text="documents",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=800,
        schema_name="classification",
        schema={"type": "object"},
        working_directory=working_directory,
    )

    command = run.call_args.args[0]
    assert command[:2] == ["codex", "exec"]
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--strict-config" in command
    assert "--ephemeral" in command
    assert "--skip-git-repo-check" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--model") + 1] == "gpt-5.6-luna"
    assert command[command.index("--config") + 1] == 'model_reasoning_effort="max"'
    assert 'shell_environment_policy.inherit="core"' in command
    disabled_features = {command[index + 1] for index, value in enumerate(command) if value == "--disable"}
    assert {"browser_use", "multi_agent", "plugins", "skill_search"}.issubset(disabled_features)
    assert "--json" in command
    assert schemas == [{"type": "object"}]
    assert run.call_args.kwargs["input"] == "System instructions:\nsystem\n\nUser request:\ndocuments"
    assert run.call_args.kwargs["cwd"] == working_directory
    assert result.value == {"status": "pass"}
    assert astuple(result.usage) == (100, 20, 120, 0, 0)


def test_docker_client_exposes_only_the_snapshot_outputs_and_temporary_codex_home(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    source_codex_home = tmp_path / "real-codex-home"
    source_codex_home.mkdir()
    (source_codex_home / "auth.json").write_text('{"tokens":"secret"}', encoding="utf-8")
    (source_codex_home / "config.toml").write_text("untrusted = true", encoding="utf-8")
    working_directory = tmp_path / "workspace"
    (working_directory / "repository").mkdir(parents=True)
    captured_schema: dict[str, object] = {}
    copied_runtime_files: set[str] = set()
    runtime_config = ""

    def run_side_effect(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        nonlocal runtime_config
        mounts = [command[index + 1] for index, item in enumerate(command) if item == "--mount"]
        output_mount = next(item for item in mounts if "dst=/output" in item)
        runtime_mount = next(item for item in mounts if "dst=/runtime-home" in item)
        output_source = Path(dict(part.split("=", 1) for part in output_mount.split(","))["src"])
        runtime_source = Path(dict(part.split("=", 1) for part in runtime_mount.split(","))["src"])
        captured_schema.update(json.loads((output_source / "output_schema.json").read_text(encoding="utf-8")))
        copied_runtime_files.update(path.name for path in (runtime_source / ".codex").iterdir())
        runtime_config = (runtime_source / ".codex" / "config.toml").read_text(encoding="utf-8")
        (output_source / "last_message.json").write_text('{"status":"not_found"}', encoding="utf-8")
        return CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    run = mocker.patch("codex_cli_client.subprocess.run", autospec=True, side_effect=run_side_effect)
    client = codex_cli_client.DockerCodexCliClient(
        docker_command="docker",
        image="swe-conform-codex:0.146.0",
        source_codex_home=source_codex_home,
        workspace_root=tmp_path,
    )

    result = client.complete_json(
        instructions="system",
        input_text="documents",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=800,
        schema_name="classification",
        schema={"type": "object"},
        working_directory=working_directory,
    )

    command = run.call_args.args[0]
    assert command[:3] == ["docker", "run", "--rm"]
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges:true"
    assert command[command.index("--pids-limit") + 1] == "256"
    mounts = [command[index + 1] for index, item in enumerate(command) if item == "--mount"]
    assert f"type=bind,src={working_directory.resolve()},dst=/workspace,readonly" in mounts
    assert all(str(source_codex_home) not in mount for mount in mounts)
    assert copied_runtime_files == {"auth.json", "config.toml"}
    assert 'default_permissions = "guideline-readonly"' in runtime_config
    assert 'approval_policy = "never"' in runtime_config
    assert 'extends = ":read-only"' in runtime_config
    assert '"/runtime-home/.codex" = "deny"' in runtime_config
    assert captured_schema == {"type": "object"}
    assert "swe-conform-codex:0.146.0" in command
    image_index = command.index("swe-conform-codex:0.146.0")
    assert command[image_index + 1 : image_index + 3] == ["exec", "--strict-config"]
    assert "use_legacy_landlock" not in command
    assert "--sandbox" not in command
    assert "--ignore-user-config" not in command
    assert result.value == {"status": "not_found"}


def test_codex_docker_image_installs_the_pinned_cli_version() -> None:
    dockerfile = (PROJECT_ROOT / "docker" / "codex.Dockerfile").read_text(encoding="utf-8")
    version_line = next(line for line in dockerfile.splitlines() if line.startswith("ARG CODEX_VERSION="))

    assert version_line.removeprefix("ARG CODEX_VERSION=") == "0.146.0"
    assert "@openai/codex@${CODEX_VERSION}" in dockerfile


def test_codex_docker_image_uses_codex_as_its_command_boundary() -> None:
    dockerfile = (PROJECT_ROOT / "docker" / "codex.Dockerfile").read_text(encoding="utf-8")
    instructions = [line.strip() for line in dockerfile.splitlines() if line.strip()]

    assert instructions[-1] == 'ENTRYPOINT ["codex"]'


def test_docker_preflight_verifies_bwrap_filesystem_credentials_and_network(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    source_codex_home = tmp_path / "real-codex-home"
    source_codex_home.mkdir()
    (source_codex_home / "auth.json").write_text('{"tokens":"secret"}', encoding="utf-8")
    run = mocker.patch(
        "codex_cli_client.subprocess.run",
        autospec=True,
        side_effect=(
            CompletedProcess(args=[], returncode=0, stdout="bubblewrap 0.8.0\n", stderr=""),
            CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            CompletedProcess(args=[], returncode=0, stdout="sandbox-read-probe\n", stderr=""),
            CompletedProcess(args=[], returncode=1, stdout="", stderr="read-only filesystem"),
            CompletedProcess(args=[], returncode=1, stdout="", stderr="permission denied"),
            CompletedProcess(args=[], returncode=42, stdout="", stderr="network blocked"),
        ),
    )
    client = codex_cli_client.DockerCodexCliClient(
        image="swe-conform-codex:0.146.0",
        source_codex_home=source_codex_home,
        workspace_root=tmp_path,
    )

    client.preflight()

    assert run.call_count == 6
    bwrap_command = run.call_args_list[0].args[0]
    sandbox_commands = [call.args[0] for call in run.call_args_list[1:]]
    assert bwrap_command[bwrap_command.index("--entrypoint") + 1] == "/usr/bin/bwrap"
    assert bwrap_command[-1] == "--version"
    for command in sandbox_commands:
        assert "--read-only" in command
        assert command[command.index("--cap-drop") + 1] == "ALL"
        assert command[command.index("--security-opt") + 1] == "no-new-privileges:true"
        image_index = command.index("swe-conform-codex:0.146.0")
        assert command[image_index + 1 : image_index + 4] == ["sandbox", "-P", "guideline-readonly"]
        assert "--sandbox-state-disable-network" in command
        assert "use_legacy_landlock" not in command
    assert sandbox_commands[0][-1] == "/bin/true"
    assert sandbox_commands[1][-1] == "/workspace/repository/preflight.txt"
    assert sandbox_commands[2][-3:] == ["sh", "-c", "printf modified > repository/preflight.txt"]
    assert sandbox_commands[3][-1] == "/runtime-home/.codex/auth.json"
    assert sandbox_commands[4][-2] == "-e"


def test_docker_preflight_fails_closed_when_system_bwrap_is_unavailable(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    source_codex_home = tmp_path / "real-codex-home"
    source_codex_home.mkdir()
    (source_codex_home / "auth.json").write_text('{"tokens":"secret"}', encoding="utf-8")
    run = mocker.patch(
        "codex_cli_client.subprocess.run",
        autospec=True,
        return_value=CompletedProcess(args=[], returncode=127, stdout="", stderr="bwrap not found"),
    )
    client = codex_cli_client.DockerCodexCliClient(
        image="swe-conform-codex:0.146.0",
        source_codex_home=source_codex_home,
        workspace_root=tmp_path,
    )

    with pytest.raises(codex_cli_client.CodexSandboxError, match="System bubblewrap is unavailable"):
        client.preflight()

    assert run.call_count == 1
