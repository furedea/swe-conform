"""Structured Codex CLI client with benchmark harness isolation."""

import json
import logging
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import guideline
import openai_responses_client

_DEFAULT_TIMEOUT_SECONDS = 600.0
_ERROR_OUTPUT_TAIL_CHARACTERS = 1200
_LOGGER = logging.getLogger(__name__)


class CodexCliError(RuntimeError):
    """A failed Codex CLI process without prompt content in its message."""

    __slots__ = ("returncode", "stderr", "stdout")

    def __init__(self, *, returncode: int, stdout: str, stderr: str) -> None:
        super().__init__(_error_message(returncode=returncode, stdout=stdout, stderr=stderr))
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class CodexCliClient:
    """Run a one-shot Codex classifier without user or project harnesses."""

    __slots__ = ("_command", "_timeout_seconds", "_workspace_root")

    def __init__(
        self,
        *,
        command: str = "codex",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        workspace_root: Path | None = None,
    ) -> None:
        self._command = command
        self._timeout_seconds = timeout_seconds
        self._workspace_root = workspace_root

    def complete_json(
        self,
        *,
        instructions: str,
        input_text: str,
        model: str,
        reasoning_effort: str,
        max_output_tokens: int,
        schema_name: str,
        schema: Mapping[str, object],
    ) -> openai_responses_client.JsonResponse:
        """Return one schema-constrained Codex CLI response."""
        _ = max_output_tokens, schema_name
        with tempfile.TemporaryDirectory(
            prefix="swe-guideline-refactor-codex-",
            dir=self._workspace_root,
        ) as workspace:
            workspace_path = Path(workspace)
            schema_path = workspace_path / "output_schema.json"
            output_path = workspace_path / "last_message.json"
            schema_path.write_text(json.dumps(schema, ensure_ascii=True, sort_keys=True), encoding="utf-8")
            command = self._command_arguments(
                model=model,
                reasoning_effort=reasoning_effort,
                workspace_path=workspace_path,
                schema_path=schema_path,
                output_path=output_path,
            )
            _LOGGER.info({"action": "codex_exec", "model": model, "status": "start"})
            completed = subprocess.run(
                command,
                input=_combined_prompt(instructions=instructions, input_text=input_text),
                capture_output=True,
                text=True,
                check=False,
                cwd=workspace_path,
                timeout=self._timeout_seconds,
            )
            if completed.returncode != 0:
                _LOGGER.error(
                    {
                        "action": "codex_exec",
                        "model": model,
                        "status": "error",
                        "returncode": completed.returncode,
                    },
                )
                raise CodexCliError(
                    returncode=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                )
            value = json.loads(output_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                msg = "Codex CLI structured output must be a JSON object"
                raise RuntimeError(msg)
            _LOGGER.info({"action": "codex_exec", "model": model, "status": "complete"})
            return openai_responses_client.JsonResponse(
                value=cast(dict[str, object], value),
                usage=_usage(completed.stdout),
            )

    def close(self) -> None:
        """Provide the same lifecycle contract as the HTTP client."""

    def _command_arguments(
        self,
        *,
        model: str,
        reasoning_effort: str,
        workspace_path: Path,
        schema_path: Path,
        output_path: Path,
    ) -> list[str]:
        return [
            self._command,
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--cd",
            str(workspace_path),
            "--model",
            model,
            "--config",
            f'model_reasoning_effort="{reasoning_effort}"',
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "--json",
            "--color",
            "never",
            "-",
        ]


def _combined_prompt(*, instructions: str, input_text: str) -> str:
    return f"System instructions:\n{instructions}\n\nUser request:\n{input_text}"


def _usage(event_stream: str) -> guideline.TokenUsage:
    input_units = 0
    output_units = 0
    for line in event_stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        input_units += _integer(usage.get("input_tokens"))
        output_units += _integer(usage.get("output_tokens"))
    return guideline.TokenUsage(
        input_tokens=input_units,
        output_tokens=output_units,
        total_tokens=input_units + output_units,
    )


def _integer(value: object) -> int:
    return int(str(value)) if value is not None else 0


def _error_message(*, returncode: int, stdout: str, stderr: str) -> str:
    parts = [f"Codex CLI exited with returncode={returncode}"]
    for name, value in (("stderr", stderr), ("stdout", stdout)):
        tail = value.strip()[-_ERROR_OUTPUT_TAIL_CHARACTERS:]
        if tail:
            parts.append(f"{name}_tail={tail!r}")
    return "; ".join(parts)
