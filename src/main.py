"""Command-line interface for guideline-first repository filtering."""

import argparse
import hashlib
import json
import logging
import os
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import batch_runner
import github_client
import guideline_classifier
import guideline_evidence
import license_filter
import openai_responses_client
import pipeline
import repository
import result_store

_DEFAULT_INPUT_DIR = Path("docs/data/repository-candidates")
_DEFAULT_OUTPUT_DIR = Path("output/repository-selection")
_DEFAULT_MODEL = "gpt-5.6-luna"
_DEFAULT_WORKERS = 4
_LOGGER = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> None:
    """Validate candidate data or run the repository filtering pipeline."""
    parser = _parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "validate":
        _validate(arguments.input_dir)
        return
    if arguments.command == "filter":
        _filter(arguments)
        return
    parser.print_help()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swe-guideline-refactor")
    subparsers = parser.add_subparsers(dest="command")

    validate_parser = subparsers.add_parser("validate", help="Validate repository candidate CSV files")
    validate_parser.add_argument("--input-dir", type=Path, default=_DEFAULT_INPUT_DIR)

    filter_parser = subparsers.add_parser("filter", help="Run guideline-first repository filtering")
    filter_parser.add_argument("--input-dir", type=Path, default=_DEFAULT_INPUT_DIR)
    filter_parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    filter_parser.add_argument("--model", default=_DEFAULT_MODEL)
    filter_parser.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        default="medium",
    )
    filter_parser.add_argument("--workers", type=_positive_integer, default=_DEFAULT_WORKERS)
    filter_parser.add_argument("--limit", type=_positive_integer)
    filter_parser.add_argument("--max-documents", type=_positive_integer, default=12)
    filter_parser.add_argument("--github-base-url", default="https://api.github.com")
    filter_parser.add_argument("--openai-base-url", default="https://api.openai.com/v1")
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
        "languages": dict(sorted(language_counts.items())),
        "input_sha256": _input_fingerprints(input_dir),
    }
    print(json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True))


def _filter(arguments: argparse.Namespace) -> None:
    github_credential = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    openai_credential = os.environ.get("OPENAI_API_KEY")
    if not github_credential:
        msg = "GITHUB_TOKEN or GH_TOKEN must be set"
        raise RuntimeError(msg)
    if not openai_credential:
        msg = "OPENAI_API_KEY must be set"
        raise RuntimeError(msg)

    candidates = repository.load_repository_candidates(arguments.input_dir)
    configuration = {
        "schema_version": 1,
        "filter_order": ["project_guideline", "osi_approved_license"],
        "input_sha256": _input_fingerprints(arguments.input_dir),
        "model": arguments.model,
        "reasoning_effort": arguments.reasoning_effort,
        "max_documents": arguments.max_documents,
        "classification_contract_sha256": guideline_classifier.contract_fingerprint(),
        "github_base_url": arguments.github_base_url,
        "openai_base_url": arguments.openai_base_url,
        "license_source": "SPDX License List 3.28.0 isOsiApproved",
    }
    store = result_store.ResultStore(arguments.output_dir, configuration=configuration)
    store.initialize()

    github_api = github_client.GitHubClient(
        token=github_credential,
        base_url=arguments.github_base_url,
    )
    model_api = openai_responses_client.OpenAIResponsesClient(
        api_key=openai_credential,
        base_url=arguments.openai_base_url,
    )
    try:
        selector = guideline_evidence.CandidateDocumentSelector(max_documents=arguments.max_documents)
        collector = guideline_evidence.GuidelineEvidenceCollector(client=github_api, selector=selector)
        guideline_checker = guideline_classifier.ModelGuidelineChecker(
            collector=collector,
            model_client=model_api,
            model=arguments.model,
            reasoning_effort=arguments.reasoning_effort,
        )
        repository_filter = pipeline.RepositoryFilter(
            guideline_checker=guideline_checker,
            license_checker=license_filter.SpdxLicenseChecker(),
        )
        runner = batch_runner.BatchRunner(repository_filter=repository_filter, workers=arguments.workers)
        stats = runner.run(
            candidates,
            store,
            limit=arguments.limit,
            on_progress=_log_progress,
        )
    finally:
        model_api.close()
        github_api.close()
    print(json.dumps(_stats_report(stats), indent=2, ensure_ascii=True, sort_keys=True))


def _log_progress(completed: int, total: int, result: pipeline.RepositoryResult) -> None:
    if completed in {1, total} or completed % 25 == 0:
        _LOGGER.info(
            "Repository filter progress",
            extra={
                "completed": completed,
                "total": total,
                "repository": result.candidate.repository,
                "guideline_status": result.guideline.status.value,
                "license_status": result.license.status.value,
            },
        )


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
