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
import bedrock_responses_client
import cache_runner
import codex_cli_client
import github_client
import guideline_classifier
import markdown_audit
import markdown_batch
import markdown_evaluation
import markdown_filename_audit
import markdown_full_review
import markdown_responses_runner
import markdown_review
import openai_batch_client
import openrouter_responses_client
import pipeline
import repository
import repository_cache
import repository_sampling
import repository_tree
import repository_workspace
import responses_provider
import result_store

_DEFAULT_INPUT_DIR = Path("docs/repository-candidates")
_DEFAULT_OUTPUT_DIR = Path("output/repository-selection")
_DEFAULT_FETCH_RESULT_PATH = Path("output/repository-cache/fetch_results.jsonl")
_DEFAULT_MODEL = "gpt-5.6-luna"
_DEFAULT_CODEX_IMAGE = "swe-conform-codex:0.146.0"
_DEFAULT_WORKERS = 4
_DEFAULT_BATCH_SAMPLE_SIZE = 20
_DEFAULT_BATCH_SAMPLE_SEED = 20260806
_DEFAULT_REPOSITORY_SAMPLE_SEED = 20260807
_LOGGER = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> None:
    """Validate candidate data or run the repository filtering pipeline."""
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "validate":
        _validate(arguments.input_dir)
    elif arguments.command == "fetch":
        _fetch(arguments)
    elif arguments.command == "preflight":
        _preflight(arguments)
    elif arguments.command == "filter":
        _filter(arguments)
    elif arguments.command == "sample-repositories":
        _sample_repositories(arguments)
    elif arguments.command == "audit-markdown":
        _audit_markdown(arguments)
    elif arguments.command == "audit-markdown-filenames":
        _audit_markdown_filenames(arguments)
    elif arguments.command == "batch-markdown":
        _batch_markdown(arguments)
    elif arguments.command == "classify-markdown":
        _classify_markdown(arguments)
    else:
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
        help="Allow revision-pinned replay inputs whose last commit predates 2026-01-01",
    )

    sample_parser = subparsers.add_parser(
        "sample-repositories",
        help="Create a reproducible language-stratified repository sample",
    )
    sample_parser.add_argument("--input-dir", type=Path, default=_DEFAULT_INPUT_DIR)
    sample_parser.add_argument("--output-dir", type=Path, required=True)
    sample_parser.add_argument("--sample-size", type=_positive_integer, default=50)
    sample_parser.add_argument("--sample-seed", type=int, default=_DEFAULT_REPOSITORY_SAMPLE_SEED)
    sample_parser.add_argument("--exclude-csv", type=Path, action="append")

    audit_parser = subparsers.add_parser(
        "audit-markdown",
        help="List Markdown files containing configured terms and compare agent evidence",
    )
    _add_markdown_audit_arguments(audit_parser)

    filename_audit_parser = subparsers.add_parser(
        "audit-markdown-filenames",
        help="Filter Markdown files by configured filename and content terms",
    )
    _add_markdown_audit_arguments(filename_audit_parser)
    filename_audit_parser.add_argument("--cache-root", type=Path)
    filename_audit_parser.add_argument("--git-command", default="git")
    _add_batch_markdown_arguments(subparsers)
    _add_classify_markdown_arguments(subparsers)
    return parser


def _add_batch_markdown_arguments(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    batch_parser = subparsers.add_parser(
        "batch-markdown",
        help="Run a per-file Markdown classification cost pilot with a supported Batch API",
    )
    batch_actions = batch_parser.add_subparsers(dest="batch_action", required=True)
    prepare_parser = batch_actions.add_parser("prepare", help="Sample candidates and prepare Batch JSONL")
    _add_markdown_preparation_arguments(prepare_parser, default_reasoning_effort="medium")
    for action in ("submit", "status", "collect"):
        action_parser = batch_actions.add_parser(action)
        action_parser.add_argument("--output-dir", type=Path, required=True)
        if action == "submit":
            action_parser.add_argument(
                "--provider",
                choices=("openai",),
                default="openai",
            )


def _add_classify_markdown_arguments(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    classify_parser = subparsers.add_parser(
        "classify-markdown",
        help="Classify prepared Markdown files through a Responses API provider",
    )
    actions = classify_parser.add_subparsers(dest="classification_action", required=True)
    prepare_parser = actions.add_parser("prepare", help="Sample candidates and prepare per-file requests")
    _add_markdown_preparation_arguments(prepare_parser, default_reasoning_effort="max")
    settings = markdown_batch.PROJECT_RULE_CLASSIFICATION_SETTINGS
    prepare_parser.set_defaults(
        provider=settings.provider.value,
        bedrock_region=settings.region,
        model=settings.model,
        reasoning_effort=settings.reasoning_effort,
        max_output_tokens=settings.max_output_tokens,
        workers=settings.workers,
    )
    run_parser = actions.add_parser("run", help="Run prepared requests through a Responses API provider")
    run_parser.add_argument("--output-dir", type=Path, required=True)
    run_parser.add_argument(
        "--provider",
        choices=tuple(provider.value for provider in responses_provider.ResponsesProvider),
        default=settings.provider.value,
    )
    run_parser.add_argument(
        "--bedrock-region",
        choices=responses_provider.BEDROCK_LUNA_REGIONS,
        default=settings.region,
    )
    run_parser.add_argument("--workers", type=_positive_integer, default=settings.workers)
    export_parser = actions.add_parser(
        "export-pass",
        help="Materialize pass- and review-classified files for manual review",
    )
    export_parser.add_argument("--output-dir", type=Path, required=True)
    export_parser.add_argument("--review-dir", type=Path, required=True)
    candidate_export_parser = actions.add_parser(
        "export-candidates",
        help="Materialize mechanically selected files for blind manual review",
    )
    candidate_export_parser.add_argument("--candidate-csv", type=Path, required=True)
    candidate_export_parser.add_argument("--output-dir", type=Path, required=True)
    candidate_export_parser.add_argument("--review-dir", type=Path, required=True)
    evaluate_parser = actions.add_parser(
        "evaluate",
        help="Compare model classifications with a human-decision checklist",
    )
    evaluate_parser.add_argument("--output-dir", type=Path, required=True)
    evaluate_parser.add_argument("--checklist-csv", type=Path, required=True)
    evaluate_parser.add_argument("--repository-csv", type=Path)
    codex_review_parser = actions.add_parser(
        "codex-review",
        help="Extend an existing checklist with Codex-reviewed candidates",
    )
    codex_review_parser.add_argument("--candidate-csv", type=Path, required=True)
    codex_review_parser.add_argument("--classified-files", type=Path, required=True)
    codex_review_parser.add_argument("--batch-input", type=Path, required=True)
    codex_review_parser.add_argument("--existing-checklist", type=Path, required=True)
    codex_review_parser.add_argument("--checkpoint-jsonl", type=Path, required=True)
    codex_review_parser.add_argument("--output-csv", type=Path, required=True)
    codex_review_parser.add_argument("--prompt-path", type=Path, required=True)
    codex_review_parser.add_argument("--model", default=markdown_full_review.DEFAULT_MODEL)
    codex_review_parser.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default=markdown_full_review.DEFAULT_REASONING_EFFORT,
    )
    codex_review_parser.add_argument(
        "--workers",
        type=_positive_integer,
        default=markdown_full_review.DEFAULT_WORKERS,
    )
    codex_review_parser.add_argument("--codex-command", default="codex")
    codex_review_parser.add_argument(
        "--model-timeout-seconds",
        type=_positive_integer,
        default=int(markdown_full_review.DEFAULT_TIMEOUT_SECONDS),
    )
    codex_review_parser.add_argument("--max-batches", type=_positive_integer)


def _add_markdown_preparation_arguments(
    parser: argparse.ArgumentParser,
    *,
    default_reasoning_effort: str,
) -> None:
    parser.add_argument("--candidate-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    selection_group = parser.add_mutually_exclusive_group()
    selection_group.add_argument("--sample-size", type=_positive_integer, default=_DEFAULT_BATCH_SAMPLE_SIZE)
    selection_group.add_argument("--all-candidates", action="store_true")
    parser.add_argument("--sample-seed", type=int, default=_DEFAULT_BATCH_SAMPLE_SEED)
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    parser.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default=default_reasoning_effort,
    )
    parser.add_argument(
        "--max-output-tokens",
        type=_positive_integer,
        default=markdown_batch.DEFAULT_MAX_OUTPUT_TOKENS,
    )
    parser.add_argument("--workers", type=_positive_integer, default=_DEFAULT_WORKERS)


def _add_markdown_audit_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--evidence-csv", type=Path, action="append")
    parser.add_argument("--workers", type=_positive_integer, default=_DEFAULT_WORKERS)
    parser.add_argument("--limit", type=_positive_integer)
    parser.add_argument(
        "--allow-out-of-window-snapshots",
        action="store_false",
        dest="enforce_snapshot_window",
        help="Allow revision-pinned replay inputs whose last commit predates 2026-01-01",
    )


def _positive_integer(raw_value: str) -> int:
    value = int(raw_value)
    if value < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return value


def _validate(input_dir: Path) -> None:
    candidates = repository.load_repository_candidates(input_dir)
    repository.validate_selection_criteria(candidates)
    language_counts = Counter(candidate.fields.get("mainLanguage", "") for candidate in candidates)
    report = {
        "status": "valid",
        "input_dir": str(input_dir),
        "repositories": len(candidates),
        "unique_revisions": len({(candidate.repository, candidate.revision) for candidate in candidates}),
        "last_commit_minimum": repository.SNAPSHOT_START.isoformat(),
        "languages": dict(sorted(language_counts.items())),
        "selection_criteria": repository.selection_criteria_report(),
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
        "last_commit_minimum": repository.SNAPSHOT_START.isoformat(),
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


def _sample_repositories(arguments: argparse.Namespace) -> None:
    report = repository_sampling.write_stratified_sample(
        input_dir=arguments.input_dir,
        output_dir=arguments.output_dir,
        sample_size=arguments.sample_size,
        sample_seed=arguments.sample_seed,
        exclude_csvs=tuple(arguments.exclude_csv or ()),
    )
    payload = {
        "population": report.population,
        "excluded": report.excluded,
        "eligible": report.eligible,
        "sampled": report.sampled,
        "language_populations": report.language_populations,
        "language_sample_sizes": report.language_sample_sizes,
        "output_dir": str(report.output_dir),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))


def _audit_markdown(arguments: argparse.Namespace) -> None:
    candidates = repository.load_repository_candidates(
        arguments.input_dir,
        enforce_snapshot_window=arguments.enforce_snapshot_window,
    )
    agent_evidence = markdown_audit.load_agent_evidence(arguments.evidence_csv or ())
    client = github_client.GitHubClient(token=github_credential())
    try:
        auditor = markdown_audit.MarkdownAuditor(
            client=client,
            agent_evidence=agent_evidence,
        )
        runner = markdown_audit.MarkdownAuditRunner(auditor=auditor, workers=arguments.workers)
        report = runner.run(candidates, limit=arguments.limit)
    finally:
        client.close()
    markdown_audit.write_reports(report, arguments.output_dir)
    payload = {
        "requested": report.stats.requested,
        "completed": report.stats.completed,
        "errors": report.stats.errors,
        "elapsed_seconds": round(report.stats.elapsed_seconds, 3),
        "output_dir": str(arguments.output_dir),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))


def _audit_markdown_filenames(arguments: argparse.Namespace) -> None:
    candidates = repository.load_repository_candidates(
        arguments.input_dir,
        enforce_snapshot_window=arguments.enforce_snapshot_window,
    )
    agent_evidence = markdown_audit.load_agent_evidence(arguments.evidence_csv or ())
    client = github_client.GitHubClient(token=github_credential())
    try:
        tree_client = _markdown_filename_tree_client(arguments, fallback=client)
        auditor = markdown_filename_audit.MarkdownFilenameAuditor(
            client=tree_client,
            agent_evidence=agent_evidence,
        )
        runner = markdown_filename_audit.MarkdownFilenameAuditRunner(
            auditor=auditor,
            workers=arguments.workers,
        )
        report = runner.run(candidates, limit=arguments.limit)
    finally:
        client.close()
    markdown_filename_audit.write_reports(report, arguments.output_dir)
    payload = {
        "requested": report.stats.requested,
        "completed": report.stats.completed,
        "errors": report.stats.errors,
        "elapsed_seconds": round(report.stats.elapsed_seconds, 3),
        "output_dir": str(arguments.output_dir),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True))


def _markdown_filename_tree_client(
    arguments: argparse.Namespace,
    *,
    fallback: repository_tree.RepositoryTreeClient,
) -> repository_tree.RepositoryTreeClient:
    if arguments.cache_root is None:
        return fallback
    cache = repository_cache.GitRepositoryCache(
        root=arguments.cache_root,
        command=arguments.git_command,
    )
    return repository_tree.CachedRepositoryTreeClient(
        cache=cache,
        fallback=fallback,
        command=arguments.git_command,
    )


def _batch_markdown(arguments: argparse.Namespace) -> None:
    if arguments.batch_action == "prepare":
        _prepare_markdown_batch(arguments)
        return
    client = openai_batch_client.OpenAIBatchClient(api_key=openai_credential())
    try:
        if arguments.batch_action == "submit":
            report = markdown_batch.submit_cost_pilot(output_dir=arguments.output_dir, client=client)
        elif arguments.batch_action == "status":
            report = markdown_batch.retrieve_cost_pilot(output_dir=arguments.output_dir, client=client)
        else:
            report = markdown_batch.collect_cost_pilot(output_dir=arguments.output_dir, client=client)
    finally:
        client.close()
    print(json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True))


def _classify_markdown(arguments: argparse.Namespace) -> None:
    if arguments.classification_action == "prepare":
        _prepare_markdown_batch(arguments)
        return
    if arguments.classification_action == "export-pass":
        report = markdown_review.export_pass_files(
            classified_files_path=arguments.output_dir / "classified_files.csv",
            batch_input_path=arguments.output_dir / "batch_input.jsonl",
            output_dir=arguments.review_dir,
        )
        _print_markdown_review_report(report)
        return
    if arguments.classification_action == "export-candidates":
        report = markdown_review.export_candidate_files(
            candidate_csv=arguments.candidate_csv,
            batch_input_path=arguments.output_dir / "batch_input.jsonl",
            output_dir=arguments.review_dir,
        )
        _print_markdown_review_report(report)
        return
    if arguments.classification_action == "evaluate":
        evaluation_dir = arguments.output_dir / "evaluation"
        report = markdown_evaluation.evaluate_classifications(
            classified_files_path=arguments.output_dir / "classified_files.csv",
            checklist_path=arguments.checklist_csv,
            repository_csv_path=arguments.repository_csv,
            output_dir=evaluation_dir,
        )
        print(
            json.dumps(
                {
                    "human_labeled_files": report.human_labeled_files,
                    "input_repositories": report.input_repositories,
                    "human_labeled_repositories": report.human_labeled_repositories,
                    "human_pass_repositories": report.human_pass_repositories,
                    "llm_pass_repositories": report.llm_pass_repositories,
                    "resolved_predictions": report.resolved_predictions,
                    "true_positives": report.true_positives,
                    "false_positives": report.false_positives,
                    "false_negatives": report.false_negatives,
                    "true_negatives": report.true_negatives,
                    "review_decisions": report.review_decisions,
                    "model_errors": report.model_errors,
                    "missing_predictions": report.missing_predictions,
                    "resolved_accuracy": report.resolved_accuracy,
                    "strict_accuracy": report.strict_accuracy,
                    "resolution_rate": report.resolution_rate,
                    "evaluation_dir": str(report.output_dir),
                },
                indent=2,
                ensure_ascii=True,
                sort_keys=True,
            ),
        )
        return
    if arguments.classification_action == "codex-review":
        _build_full_codex_checklist(arguments)
        return
    client = _classification_client(arguments)
    try:
        report = markdown_responses_runner.run_prepared_classification(
            output_dir=arguments.output_dir,
            client=client,
            provider=arguments.provider,
            region=(
                arguments.bedrock_region
                if arguments.provider == responses_provider.ResponsesProvider.BEDROCK.value
                else None
            ),
            workers=arguments.workers,
        )
    finally:
        client.close()
    print(json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True))


def _classification_client(
    arguments: argparse.Namespace,
) -> bedrock_responses_client.BedrockResponsesClient | openrouter_responses_client.OpenRouterResponsesClient:
    if arguments.provider == responses_provider.ResponsesProvider.BEDROCK.value:
        return bedrock_responses_client.BedrockResponsesClient(
            api_key=bedrock_credential(),
            region=arguments.bedrock_region,
        )
    return openrouter_responses_client.OpenRouterResponsesClient(api_key=openrouter_credential())


def _build_full_codex_checklist(arguments: argparse.Namespace) -> None:
    client = codex_cli_client.CodexCliClient(
        command=arguments.codex_command,
        timeout_seconds=arguments.model_timeout_seconds,
    )
    try:
        report = markdown_full_review.build_full_checklist(
            candidate_csv=arguments.candidate_csv,
            classified_files_path=arguments.classified_files,
            batch_input_path=arguments.batch_input,
            existing_checklist_path=arguments.existing_checklist,
            checkpoint_path=arguments.checkpoint_jsonl,
            output_path=arguments.output_csv,
            prompt_path=arguments.prompt_path,
            client=client,
            model=arguments.model,
            reasoning_effort=arguments.reasoning_effort,
            workers=arguments.workers,
            max_batches=arguments.max_batches,
        )
    finally:
        client.close()
    print(
        json.dumps(
            {
                "existing": report.existing,
                "codex_added": report.codex_added,
                "reviewed": report.reviewed,
                "remaining": report.remaining,
                "rows": report.rows,
                "output": str(report.output_path),
                "output_written": report.output_written,
            },
            indent=2,
            ensure_ascii=True,
            sort_keys=True,
        ),
    )


def _print_markdown_review_report(report: markdown_review.MarkdownReviewReport) -> None:
    print(
        json.dumps(
            {"files": report.files, "output_dir": str(report.output_dir)},
            indent=2,
            ensure_ascii=True,
            sort_keys=True,
        ),
    )


def _prepare_markdown_batch(arguments: argparse.Namespace) -> None:
    client = github_client.GitHubClient(token=github_credential())
    try:
        preparation = markdown_batch.prepare_cost_pilot(
            candidate_csv=arguments.candidate_csv,
            output_dir=arguments.output_dir,
            client=client,
            sample_size=None if arguments.all_candidates else arguments.sample_size,
            sample_seed=arguments.sample_seed,
            model=arguments.model,
            reasoning_effort=arguments.reasoning_effort,
            max_output_tokens=arguments.max_output_tokens,
            workers=arguments.workers,
        )
    finally:
        client.close()
    report = {
        "candidates": preparation.candidates,
        "sampled": preparation.sampled,
        "output_dir": str(preparation.output_dir),
    }
    print(json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True))


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


def openai_credential() -> str:
    """Load OpenAI API authentication from the environment."""
    credential = os.environ.get("OPENAI_API_KEY")
    if credential:
        return credential
    msg = "OPENAI_API_KEY is required"
    raise RuntimeError(msg)


def openrouter_credential() -> str:
    """Load OpenRouter API authentication from the environment."""
    credential = os.environ.get("OPENROUTER_API_KEY")
    if credential:
        return credential
    msg = "OPENROUTER_API_KEY is required"
    raise RuntimeError(msg)


def bedrock_credential() -> str:
    """Load Amazon Bedrock API authentication from the environment."""
    credential = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
    if credential:
        return credential
    msg = "AWS_BEARER_TOKEN_BEDROCK is required"
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
