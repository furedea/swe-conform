"""Command-line interface for guideline-first repository filtering."""

import argparse
import hashlib
import json
import logging
import os
import subprocess
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import batch_runner
import cache_runner
import codex_cli_client
import guideline_classifier
import markdown_audit
import pipeline
import repository
import repository_cache
import repository_workspace
import result_store

_DEFAULT_INPUT_DIR = Path("docs/data/repository-candidates-new")
_DEFAULT_OUTPUT_DIR = Path("output/repository-selection")
_DEFAULT_FETCH_RESULT_PATH = Path("output/repository-cache/fetch_results.jsonl")
_DEFAULT_MODEL = "gpt-5.6-luna"
_DEFAULT_CODEX_IMAGE = "swe-conform-codex:0.146.0"
_DEFAULT_WORKERS = 4
_LOGGER = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> None:
    """Validate candidate data or run the repository filtering pipeline."""
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "validate":
        _validate(arguments.input_dir)
        return
    if arguments.command == "fetch":
        _fetch(arguments)
        return
    if arguments.command == "preflight":
        _preflight(arguments)
        return
    if arguments.command == "filter":
        _filter(arguments)
        return
    if arguments.command == "audit-markdown":
        _audit_markdown(arguments)
        return
    parser.print_help()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swe-conform")
    subparsers = parser.add_subparsers(dest="command")

    validate_parser = subparsers.add_parser("validate", help="Validate repository candidate CSV files")
    validate_parser.add_argument("--input-dir", type=Path, default=_DEFAULT_INPUT_DIR)

    fetch_parser = subparsers.add_parser("fetch", help="Fetch full-history repository snapshots into an HDD cache")
    fetch_parser.add_argument("--input-dir", type=Path, default=_DEFAULT_INPUT_DIR)
    fetch_parser.add_argument("--cache-root", type=Path, required=True)
    fetch_parser.add_argument("--workers", type=_positive_integer, default=_DEFAULT_WORKERS)
    fetch_parser.add_argument("--limit", type=_positive_integer)
    fetch_parser.add_argument("--git-command", default="git")
    fetch_parser.add_argument("--fetch-timeout-seconds", type=_positive_integer, default=3600)
    fetch_parser.add_argument("--result-path", type=Path, default=_DEFAULT_FETCH_RESULT_PATH)

    preflight_parser = subparsers.add_parser("preflight", help="Verify the Docker Codex sandbox")
    preflight_parser.add_argument("--codex-image", default=_DEFAULT_CODEX_IMAGE)
    preflight_parser.add_argument("--codex-home", type=Path)
    preflight_parser.add_argument("--docker-command", default="docker")

    filter_parser = subparsers.add_parser("filter", help="Run guideline-first repository filtering")
    filter_parser.add_argument("--input-dir", type=Path, default=_DEFAULT_INPUT_DIR)
    filter_parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    filter_parser.add_argument("--provider", choices=("codex-cli",), default="codex-cli")
    filter_parser.add_argument("--model", default=_DEFAULT_MODEL)
    filter_parser.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "xhigh", "max"),
    )
    filter_parser.add_argument("--workers", type=_positive_integer, default=_DEFAULT_WORKERS)
    filter_parser.add_argument("--limit", type=_positive_integer)
    filter_parser.add_argument("--codex-runtime", choices=("docker", "host"), default="docker")
    filter_parser.add_argument("--codex-image", default=_DEFAULT_CODEX_IMAGE)
    filter_parser.add_argument("--codex-home", type=Path)
    filter_parser.add_argument("--docker-command", default="docker")
    filter_parser.add_argument("--codex-command", default="codex")
    filter_parser.add_argument("--git-command", default="git")
    filter_parser.add_argument("--cache-root", type=Path)
    filter_parser.add_argument("--workspace-root", type=Path)
    filter_parser.add_argument("--checkout-timeout-seconds", type=_positive_integer, default=900)
    filter_parser.add_argument("--model-timeout-seconds", type=_positive_integer, default=1800)
    filter_parser.add_argument(
        "--allow-out-of-window-snapshots",
        action="store_false",
        dest="enforce_snapshot_window",
        help="Allow revision-pinned replay inputs outside the collection window",
    )

    audit_parser = subparsers.add_parser(
        "audit-markdown",
        help="List Markdown files containing configured terms and compare agent evidence",
    )
    audit_parser.add_argument("--input-dir", type=Path, required=True)
    audit_parser.add_argument("--output-dir", type=Path, required=True)
    audit_parser.add_argument("--evidence-csv", type=Path, action="append", required=True)
    audit_parser.add_argument("--workers", type=_positive_integer, default=_DEFAULT_WORKERS)
    audit_parser.add_argument("--limit", type=_positive_integer)
    audit_parser.add_argument("--git-command", default="git")
    audit_parser.add_argument("--cache-root", type=Path)
    audit_parser.add_argument("--workspace-root", type=Path)
    audit_parser.add_argument("--checkout-timeout-seconds", type=_positive_integer, default=900)
    audit_parser.add_argument(
        "--allow-out-of-window-snapshots",
        action="store_false",
        dest="enforce_snapshot_window",
        help="Allow revision-pinned replay inputs outside the collection window",
    )
    return parser


def _positive_integer(raw_value: str) -> int:
    value = int(raw_value)
    if value < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return value


def _validate(input_dir: Path) -> None:
    candidates = repository.load_repository_candidates(input_dir)
    language_counts = Counter(candidate.fields.get("mainLanguage", "") for candidate in candidates)
    report = {
        "input_dir": str(input_dir),
        "repositories": len(candidates),
        "unique_revisions": len({(candidate.repository, candidate.revision) for candidate in candidates}),
        "snapshot_start": repository.SNAPSHOT_START.isoformat(),
        "snapshot_cutoff": repository.SNAPSHOT_CUTOFF.isoformat(),
        "languages": dict(sorted(language_counts.items())),
        "input_sha256": _input_fingerprints(input_dir),
    }
    print(json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True))


def _fetch(arguments: argparse.Namespace) -> None:
    candidates = repository.load_repository_candidates(arguments.input_dir)
    cache = repository_cache.GitRepositoryCache(
        root=arguments.cache_root,
        command=arguments.git_command,
        timeout_seconds=arguments.fetch_timeout_seconds,
    )
    runner = cache_runner.CacheBatchRunner(cache=cache, workers=arguments.workers)
    report = runner.run(candidates, limit=arguments.limit, on_progress=_log_fetch_progress)
    _write_fetch_results(arguments.result_path, report.results)
    stats = report.stats
    payload = {
        "requested": stats.requested,
        "fetched": stats.fetched,
        "cached": stats.cached,
        "errors": stats.errors,
        "elapsed_seconds": round(stats.elapsed_seconds, 3),
        "result_path": str(arguments.result_path),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))


def _preflight(arguments: argparse.Namespace) -> None:
    image_id = docker_image_id(arguments.docker_command, arguments.codex_image)
    client = codex_cli_client.DockerCodexCliClient(
        docker_command=arguments.docker_command,
        image=arguments.codex_image,
        source_codex_home=arguments.codex_home,
    )
    client.preflight()
    payload = {
        "codex_image": arguments.codex_image,
        "codex_image_id": image_id,
        "sandbox": "ready",
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))


def _filter(arguments: argparse.Namespace) -> None:
    candidates = repository.load_repository_candidates(
        arguments.input_dir,
        enforce_snapshot_window=arguments.enforce_snapshot_window,
    )
    workspace = _repository_workspace(arguments)
    reasoning_effort = effective_reasoning_effort(
        provider=arguments.provider,
        configured=arguments.reasoning_effort,
    )
    image_id = (
        docker_image_id(arguments.docker_command, arguments.codex_image)
        if arguments.codex_runtime == "docker"
        else None
    )
    configuration = {
        "schema_version": 2,
        "filter_order": ["project_guideline"],
        "input_sha256": _input_fingerprints(arguments.input_dir),
        "provider": arguments.provider,
        "model": arguments.model,
        "reasoning_effort": reasoning_effort,
        "workers": arguments.workers,
        "snapshot_start": repository.SNAPSHOT_START.isoformat(),
        "snapshot_cutoff": repository.SNAPSHOT_CUTOFF.isoformat(),
        "enforce_snapshot_window": arguments.enforce_snapshot_window,
        "classification_contract_sha256": guideline_classifier.contract_fingerprint(),
        "codex_runtime": arguments.codex_runtime,
        "codex_image": arguments.codex_image if arguments.codex_runtime == "docker" else None,
        "codex_image_id": image_id,
        "docker_command": arguments.docker_command if arguments.codex_runtime == "docker" else None,
        "codex_command": arguments.codex_command,
        "git_command": arguments.git_command,
        "repository_source": "cache" if arguments.cache_root is not None else "github",
        "cache_root": str(arguments.cache_root) if arguments.cache_root is not None else None,
        "workspace_root": str(arguments.workspace_root) if arguments.workspace_root is not None else None,
        "checkout_timeout_seconds": arguments.checkout_timeout_seconds,
        "model_timeout_seconds": arguments.model_timeout_seconds,
    }
    store = result_store.ResultStore(arguments.output_dir, configuration=configuration)
    store.initialize()

    model_api = _model_client(arguments)
    try:
        model_api.preflight()
        guideline_checker = guideline_classifier.ModelGuidelineChecker(
            workspace=workspace,
            model_client=model_api,
            model=arguments.model,
            reasoning_effort=reasoning_effort,
        )
        repository_filter = pipeline.RepositoryFilter(guideline_checker=guideline_checker)
        runner = batch_runner.BatchRunner(repository_filter=repository_filter, workers=arguments.workers)
        stats = runner.run(
            candidates,
            store,
            limit=arguments.limit,
            on_progress=_log_progress,
        )
    finally:
        model_api.close()
    print(json.dumps(_stats_report(stats), indent=2, ensure_ascii=True, sort_keys=True))


def _audit_markdown(arguments: argparse.Namespace) -> None:
    candidates = repository.load_repository_candidates(
        arguments.input_dir,
        enforce_snapshot_window=arguments.enforce_snapshot_window,
    )
    workspace = _repository_workspace(arguments)
    agent_evidence = markdown_audit.load_agent_evidence(arguments.evidence_csv)
    auditor = markdown_audit.MarkdownAuditor(
        workspace=workspace,
        agent_evidence=agent_evidence,
    )
    runner = markdown_audit.MarkdownAuditRunner(auditor=auditor, workers=arguments.workers)
    report = runner.run(candidates, limit=arguments.limit)
    markdown_audit.write_reports(report, arguments.output_dir)
    payload = {
        "requested": report.stats.requested,
        "completed": report.stats.completed,
        "errors": report.stats.errors,
        "elapsed_seconds": round(report.stats.elapsed_seconds, 3),
        "output_dir": str(arguments.output_dir),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))


def _repository_workspace(
    arguments: argparse.Namespace,
) -> repository_workspace.GitRepositoryWorkspace | repository_workspace.CachedGitRepositoryWorkspace:
    if arguments.cache_root is not None:
        if arguments.workspace_root is None:
            msg = "--workspace-root is required when --cache-root is used"
            raise ValueError(msg)
        return repository_workspace.CachedGitRepositoryWorkspace(
            cache_root=arguments.cache_root,
            root=arguments.workspace_root,
            command=arguments.git_command,
            timeout_seconds=arguments.checkout_timeout_seconds,
        )
    return repository_workspace.GitRepositoryWorkspace(
        command=arguments.git_command,
        root=arguments.workspace_root,
        timeout_seconds=arguments.checkout_timeout_seconds,
    )


def effective_reasoning_effort(*, provider: str, configured: str | None) -> str:
    """Resolve the provider-specific default reasoning effort."""
    if configured is not None:
        return configured
    return "max" if provider == "codex-cli" else "medium"


def github_credential() -> str:
    """Load GitHub authentication from the environment or the authenticated gh CLI."""
    credential = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if credential:
        return credential
    completed = subprocess.run(
        ["gh", "auth", "token"],
        capture_output=True,
        text=True,
        check=False,
    )
    credential = completed.stdout.strip()
    if completed.returncode == 0 and credential:
        return credential
    msg = "GITHUB_TOKEN, GH_TOKEN, or an authenticated gh CLI is required"
    raise RuntimeError(msg)


def docker_image_id(command: str, image: str) -> str:
    """Return the immutable content identifier for a local Docker image."""
    completed = subprocess.run(
        [command, "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        check=False,
    )
    image_id = completed.stdout.strip()
    if completed.returncode == 0 and image_id:
        return image_id
    message = completed.stderr.strip() or f"Docker image is unavailable: {image}"
    raise RuntimeError(message)


def _model_client(
    arguments: argparse.Namespace,
) -> codex_cli_client.CodexCliClient | codex_cli_client.DockerCodexCliClient:
    if arguments.codex_runtime == "docker":
        return codex_cli_client.DockerCodexCliClient(
            docker_command=arguments.docker_command,
            image=arguments.codex_image,
            source_codex_home=arguments.codex_home,
            timeout_seconds=arguments.model_timeout_seconds,
        )
    return codex_cli_client.CodexCliClient(
        command=arguments.codex_command,
        timeout_seconds=arguments.model_timeout_seconds,
    )


def _log_progress(completed: int, total: int, result: pipeline.RepositoryResult) -> None:
    if completed in {1, total} or completed % 25 == 0:
        _LOGGER.info(
            "Repository filter progress",
            extra={
                "completed": completed,
                "total": total,
                "repository": result.candidate.repository,
                "guideline_status": result.guideline.status.value,
            },
        )


def _log_fetch_progress(completed: int, total: int, result: cache_runner.CacheFetchResult) -> None:
    if result.error:
        _LOGGER.error(
            {
                "action": "repository_fetch",
                "completed": completed,
                "total": total,
                "repository": result.candidate.repository,
                "status": "error",
                "error": result.error,
            },
        )
    elif completed in {1, total} or completed % 25 == 0:
        _LOGGER.info(
            {
                "action": "repository_fetch",
                "completed": completed,
                "total": total,
                "repository": result.candidate.repository,
                "status": result.disposition.value if result.disposition is not None else "error",
            },
        )


def _write_fetch_results(path: Path, results: Sequence[cache_runner.CacheFetchResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(_fetch_result_payload(result), ensure_ascii=True, sort_keys=True) for result in results]
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    temporary_path.replace(path)


def _fetch_result_payload(result: cache_runner.CacheFetchResult) -> dict[str, object]:
    candidate = result.candidate
    return {
        "repository": candidate.repository,
        "snapshot_sha": candidate.revision,
        "snapshot_committed_at": candidate.fields.get("lastCommit", ""),
        "default_branch": candidate.fields.get("defaultBranch", ""),
        "source_file": candidate.source_file,
        "input_index": candidate.input_index,
        "status": result.disposition.value if result.disposition is not None else "error",
        "elapsed_seconds": round(result.elapsed_seconds, 3),
        "error": result.error,
    }


def _stats_report(stats: batch_runner.RunStats) -> dict[str, int | float]:
    return {
        "requested": stats.requested,
        "skipped": stats.skipped,
        "evaluated": stats.evaluated,
        "elapsed_seconds": round(stats.elapsed_seconds, 3),
    }


def _input_fingerprints(input_dir: Path) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for input_path in sorted(input_dir.glob("*.csv")):
        with input_path.open("rb") as input_file:
            fingerprints[input_path.name] = hashlib.file_digest(input_file, "sha256").hexdigest()
    return fingerprints


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    main()
