"""Structured Codex CLI client with benchmark harness isolation."""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import guideline
import openai_responses_client

_DEFAULT_TIMEOUT_SECONDS = 600.0
_PREFLIGHT_TIMEOUT_SECONDS = 30.0
_PREFLIGHT_REPOSITORY_CONTENT = "sandbox-read-probe\n"
_ERROR_OUTPUT_TAIL_CHARACTERS = 1200
_LOGGER = logging.getLogger(__name__)
_CODEX_RUNTIME_FILENAMES = ("auth.json", "models_cache.json", "version.json", "installation_id")
_PERMISSION_PROFILE_NAME = "guideline-readonly"
_SECURITY_CONFIG = f"""approval_policy = "never"
default_permissions = "{_PERMISSION_PROFILE_NAME}"
allow_login_shell = false

[permissions.{_PERMISSION_PROFILE_NAME}]
extends = ":read-only"

[permissions.{_PERMISSION_PROFILE_NAME}.filesystem]
"/runtime-home/.codex" = "deny"
"""
_NETWORK_PROBE = (
    'const net=require("node:net");try{const socket=net.connect({host:"1.1.1.1",port:443});'
    'socket.once("connect",()=>process.exit(0));socket.once("error",()=>process.exit(42));'
    "setTimeout(()=>process.exit(43),2000)}catch(error){process.exit(42)}"
)
_DISABLED_FEATURES = (
    "apps",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "goals",
    "hooks",
    "image_generation",
    "in_app_browser",
    "memories",
    "multi_agent",
    "plugins",
    "remote_plugin",
    "skill_search",
    "tool_suggest",
    "workspace_dependencies",
)


class CodexCliError(RuntimeError):
    """A failed Codex CLI process without prompt content in its message."""

    __slots__ = ("returncode", "stderr", "stdout")

    def __init__(self, *, returncode: int, stdout: str, stderr: str) -> None:
        super().__init__(_error_message(returncode=returncode, stdout=stdout, stderr=stderr))
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class CodexSandboxError(RuntimeError):
    """The container cannot enforce the required Codex tool sandbox."""


class CodexCliClient:
    """Run a structured Codex explorer without additional harnesses."""

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
        working_directory: Path,
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
                working_directory=working_directory,
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
                cwd=working_directory,
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

    def preflight(self) -> None:
        """Require no additional host preflight in debug mode."""

    def _command_arguments(
        self,
        *,
        model: str,
        reasoning_effort: str,
        working_directory: Path,
        schema_path: Path,
        output_path: Path,
    ) -> list[str]:
        command = [
            self._command,
            "exec",
            "--strict-config",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--cd",
            str(working_directory),
            "--model",
            model,
            "--config",
            f'model_reasoning_effort="{reasoning_effort}"',
            "--config",
            'shell_environment_policy.inherit="core"',
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "--json",
            "--color",
            "never",
        ]
        for feature in _DISABLED_FEATURES:
            command.extend(("--disable", feature))
        command.append("-")
        return command


class DockerCodexCliClient:
    """Run Codex with only the snapshot, outputs, and temporary runtime home mounted."""

    __slots__ = (
        "_docker_command",
        "_image",
        "_source_codex_home",
        "_timeout_seconds",
        "_workspace_root",
    )

    def __init__(
        self,
        *,
        docker_command: str = "docker",
        image: str,
        source_codex_home: Path | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        workspace_root: Path | None = None,
    ) -> None:
        self._docker_command = docker_command
        self._image = image
        self._source_codex_home = source_codex_home or Path.home() / ".codex"
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
        working_directory: Path,
    ) -> openai_responses_client.JsonResponse:
        """Return one schema-constrained response from an isolated container."""
        _ = max_output_tokens, schema_name
        with tempfile.TemporaryDirectory(
            prefix="swe-guideline-refactor-docker-codex-",
            dir=self._workspace_root,
        ) as temporary_directory:
            temporary_path = Path(temporary_directory)
            output_path = temporary_path / "output"
            runtime_home = temporary_path / "runtime-home"
            output_path.mkdir()
            _copy_codex_runtime(self._source_codex_home, runtime_home)
            schema_path = output_path / "output_schema.json"
            last_message_path = output_path / "last_message.json"
            schema_path.write_text(json.dumps(schema, ensure_ascii=True, sort_keys=True), encoding="utf-8")
            container_name = f"swe-guideline-refactor-{uuid.uuid4().hex}"
            command = self._command_arguments(
                container_name=container_name,
                model=model,
                reasoning_effort=reasoning_effort,
                working_directory=working_directory,
                output_path=output_path,
                runtime_home=runtime_home,
            )
            _LOGGER.info({"action": "docker_codex_exec", "model": model, "status": "start"})
            try:
                completed = subprocess.run(
                    command,
                    input=_combined_prompt(instructions=instructions, input_text=input_text),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=self._timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                _remove_container(self._docker_command, container_name)
                raise
            if completed.returncode != 0:
                raise CodexCliError(
                    returncode=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                )
            value = json.loads(last_message_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                msg = "Codex CLI structured output must be a JSON object"
                raise RuntimeError(msg)
            _LOGGER.info({"action": "docker_codex_exec", "model": model, "status": "complete"})
            return openai_responses_client.JsonResponse(
                value=cast(dict[str, object], value),
                usage=_usage(completed.stdout),
            )

    def close(self) -> None:
        """Provide the same lifecycle contract as the host client."""

    def preflight(self) -> None:
        """Fail unless the complete Docker and bubblewrap boundary is enforced."""
        with tempfile.TemporaryDirectory(
            prefix="swe-guideline-refactor-docker-preflight-",
            dir=self._workspace_root,
        ) as temporary_directory:
            temporary_path = Path(temporary_directory)
            runtime_home = temporary_path / "runtime-home"
            working_directory = temporary_path / "workspace"
            repository_path = working_directory / "repository"
            repository_path.mkdir(parents=True)
            probe_path = repository_path / "preflight.txt"
            probe_path.write_text(_PREFLIGHT_REPOSITORY_CONTENT, encoding="utf-8")
            _copy_codex_runtime(self._source_codex_home, runtime_home)
            bwrap_result = self._run_bwrap_probe(runtime_home)
            if bwrap_result.returncode != 0 or not bwrap_result.stdout.startswith("bubblewrap "):
                raise CodexSandboxError(_preflight_error("System bubblewrap is unavailable", bwrap_result))
            launch_result = self._run_preflight_probe(runtime_home, working_directory, ["/bin/true"])
            if launch_result.returncode != 0:
                raise CodexSandboxError(_preflight_error("Bubblewrap sandbox failed to start", launch_result))
            read_result = self._run_preflight_probe(
                runtime_home,
                working_directory,
                ["cat", "/workspace/repository/preflight.txt"],
            )
            if read_result.returncode != 0 or read_result.stdout != _PREFLIGHT_REPOSITORY_CONTENT:
                raise CodexSandboxError(_preflight_error("Repository is not readable", read_result))
            write_result = self._run_preflight_probe(
                runtime_home,
                working_directory,
                ["sh", "-c", "printf modified > repository/preflight.txt"],
            )
            if write_result.returncode == 0 or probe_path.read_text(encoding="utf-8") != _PREFLIGHT_REPOSITORY_CONTENT:
                raise CodexSandboxError(_preflight_error("Repository write sandbox is not enforced", write_result))
            credential_result = self._run_preflight_probe(
                runtime_home,
                working_directory,
                ["cat", "/runtime-home/.codex/auth.json"],
            )
            if credential_result.returncode == 0:
                raise CodexSandboxError(_preflight_error("Codex credentials are readable by tools", credential_result))
            network_result = self._run_preflight_probe(
                runtime_home,
                working_directory,
                ["node", "-e", _NETWORK_PROBE],
            )
            if network_result.returncode != 42:
                raise CodexSandboxError(_preflight_error("Tool network sandbox is not enforced", network_result))

    def _run_bwrap_probe(self, runtime_home: Path) -> subprocess.CompletedProcess[str]:
        container_name = f"swe-guideline-bwrap-preflight-{uuid.uuid4().hex}"
        command = [
            *self._container_arguments(container_name, runtime_home),
            "--entrypoint",
            "/usr/bin/bwrap",
            self._image,
            "--version",
        ]
        return self._run_preflight_command(container_name, command)

    def _command_arguments(
        self,
        *,
        container_name: str,
        model: str,
        reasoning_effort: str,
        working_directory: Path,
        output_path: Path,
        runtime_home: Path,
    ) -> list[str]:
        command = [
            *self._container_arguments(container_name, runtime_home),
            "--mount",
            _bind_mount(working_directory, "/workspace", read_only=True),
            "--mount",
            _bind_mount(output_path, "/output"),
            self._image,
            "exec",
            "--strict-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--cd",
            "/workspace",
            "--model",
            model,
            "--config",
            f'model_reasoning_effort="{reasoning_effort}"',
            "--config",
            'shell_environment_policy.inherit="core"',
            "--output-schema",
            "/output/output_schema.json",
            "--output-last-message",
            "/output/last_message.json",
            "--json",
            "--color",
            "never",
        ]
        for feature in _DISABLED_FEATURES:
            command.extend(("--disable", feature))
        command.append("-")
        return command

    def _run_preflight_probe(
        self,
        runtime_home: Path,
        working_directory: Path,
        probe: list[str],
    ) -> subprocess.CompletedProcess[str]:
        container_name = f"swe-guideline-preflight-{uuid.uuid4().hex}"
        command = [
            *self._container_arguments(container_name, runtime_home),
            "--mount",
            _bind_mount(working_directory, "/workspace", read_only=True),
            self._image,
            "sandbox",
            "-P",
            _PERMISSION_PROFILE_NAME,
            "-C",
            "/workspace",
            "--sandbox-state-disable-network",
            "--",
            *probe,
        ]
        return self._run_preflight_command(container_name, command)

    def _run_preflight_command(
        self,
        container_name: str,
        command: list[str],
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=_PREFLIGHT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            _remove_container(self._docker_command, container_name)
            raise

    def _container_arguments(self, container_name: str, runtime_home: Path) -> list[str]:
        return [
            self._docker_command,
            "run",
            "--rm",
            "--name",
            container_name,
            "--init",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "256",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=256m",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--env",
            "HOME=/runtime-home",
            "--env",
            "CODEX_HOME=/runtime-home/.codex",
            "--mount",
            _bind_mount(runtime_home, "/runtime-home"),
        ]


def _copy_codex_runtime(source_codex_home: Path, runtime_home: Path) -> None:
    codex_home = runtime_home / ".codex"
    codex_home.mkdir(parents=True)
    for filename in _CODEX_RUNTIME_FILENAMES:
        source_path = source_codex_home / filename
        if source_path.is_file():
            shutil.copy2(source_path, codex_home / filename)
    (codex_home / "config.toml").write_text(_SECURITY_CONFIG, encoding="utf-8")


def _bind_mount(source: Path, destination: str, *, read_only: bool = False) -> str:
    options = f"type=bind,src={source.resolve()},dst={destination}"
    return f"{options},readonly" if read_only else options


def _remove_container(docker_command: str, container_name: str) -> None:
    subprocess.run(
        [docker_command, "rm", "--force", container_name],
        capture_output=True,
        text=True,
        check=False,
    )


def _preflight_error(prefix: str, completed: subprocess.CompletedProcess[str]) -> str:
    detail = _error_message(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    return f"{prefix}: {detail}"


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
