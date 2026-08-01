"""Tests for isolated Codex CLI structured classification."""

import json
from dataclasses import astuple
from pathlib import Path
from subprocess import CompletedProcess

from pytest_mock import MockerFixture

import codex_cli_client


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

    result = client.complete_json(
        instructions="system",
        input_text="documents",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=800,
        schema_name="classification",
        schema={"type": "object"},
    )

    command = run.call_args.args[0]
    assert command[:2] == ["codex", "exec"]
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--ephemeral" in command
    assert "--skip-git-repo-check" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--model") + 1] == "gpt-5.6-luna"
    assert command[command.index("--config") + 1] == 'model_reasoning_effort="max"'
    assert "--json" in command
    assert schemas == [{"type": "object"}]
    assert run.call_args.kwargs["input"] == "System instructions:\nsystem\n\nUser request:\ndocuments"
    assert result.value == {"status": "pass"}
    assert astuple(result.usage) == (100, 20, 120)
