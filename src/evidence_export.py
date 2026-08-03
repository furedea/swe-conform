"""Export prediction-blind evidence for calibration adjudication."""

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import github_client
import guideline_classifier
import guideline_evidence
import main as application
import repository


def write_blind_evidence(
    candidates: Sequence[repository.RepositoryCandidate],
    collector: guideline_evidence.GuidelineEvidenceCollector,
    output_path: Path,
) -> None:
    """Write the exact classifier payloads without model predictions."""
    records = [
        guideline_classifier.classification_payload(
            candidate,
            collector.collect(candidate.repository, candidate.revision),
        )
        for candidate in candidates
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)


def main(argv: Sequence[str] | None = None) -> None:
    """Collect and export revision-pinned evidence for one input directory."""
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_path", type=Path)
    parser.add_argument("--max-documents", type=int, default=12)
    parser.add_argument("--github-base-url", default="https://api.github.com")
    arguments = parser.parse_args(argv)
    candidates = repository.load_repository_candidates(arguments.input_dir)
    client = github_client.GitHubClient(
        token=application.github_credential(),
        base_url=arguments.github_base_url,
    )
    try:
        selector = guideline_evidence.CandidateDocumentSelector(max_documents=arguments.max_documents)
        collector = guideline_evidence.GuidelineEvidenceCollector(client=client, selector=selector)
        write_blind_evidence(candidates, collector, arguments.output_path)
    finally:
        client.close()
    print(arguments.output_path)


if __name__ == "__main__":
    main()
