"""Tests for resumable filtering results and report generation."""

import csv
import json
from pathlib import Path

import guideline
import license_filter
import pipeline
import repository
import result_store


def _candidate(index: int = 0) -> repository.RepositoryCandidate:
    return repository.RepositoryCandidate(
        repository="example/project",
        revision="0123456789abcdef",
        license_name="MIT License",
        source_file="python.csv",
        input_index=index,
        fields={
            "name": "example/project",
            "lastCommitSHA": "0123456789abcdef",
            "license": "MIT License",
            "mainLanguage": "Python",
        },
    )


def _selected_result() -> pipeline.RepositoryResult:
    return pipeline.RepositoryResult(
        candidate=_candidate(),
        guideline=guideline.GuidelineResult(
            status=guideline.GuidelineStatus.PASS,
            reason="A concrete rule exists.",
            evidence_path="CONTRIBUTING.md",
            evidence_quote="Use snake_case.",
            candidate_count=1,
            model_called=True,
            usage=guideline.TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
        ),
        license=license_filter.LicenseResult(
            status=license_filter.LicenseStatus.PASS,
            spdx_id="MIT",
            reason="SPDX OSI Approved",
        ),
    )


def test_store_resumes_completed_results_and_writes_reports(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    store = result_store.ResultStore(output_dir, configuration={"model": "gpt-5.6-luna"})
    store.initialize()
    store.append(_selected_result())

    resumed = result_store.ResultStore(output_dir, configuration={"model": "gpt-5.6-luna"})
    resumed.initialize()
    resumed.write_reports()

    assert resumed.completed_repositories() == {("example/project", "0123456789abcdef")}
    with (output_dir / "selected_repositories.csv").open(encoding="utf-8", newline="") as output_file:
        rows = list(csv.DictReader(output_file))
    assert len(rows) == 1
    assert rows[0]["guideline_status"] == "pass"
    assert rows[0]["license_spdx_id"] == "MIT"
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["processed"] == 1
    assert summary["selected"] == 1
    assert summary["model_calls"] == 1
    assert summary["usage"]["total_tokens"] == 120


def test_store_retries_error_results_on_resume(tmp_path: Path) -> None:
    store = result_store.ResultStore(tmp_path / "output", configuration={"model": "gpt-5.6-luna"})
    store.initialize()
    store.append(
        pipeline.RepositoryResult(
            candidate=_candidate(),
            guideline=guideline.GuidelineResult(
                status=guideline.GuidelineStatus.MODEL_ERROR,
                reason="Temporary API failure",
                model_called=True,
            ),
            license=license_filter.LicenseResult(status=license_filter.LicenseStatus.NOT_EVALUATED),
        ),
    )

    assert store.completed_repositories() == set()
    store.write_reports()
    header = (tmp_path / "output" / "selected_repositories.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "name" in header
