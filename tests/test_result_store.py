"""Tests for resumable filtering results and report generation."""

import csv
import json
from pathlib import Path

import guideline
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
            evidence=(
                guideline.GuidelineEvidence(
                    path="CONTRIBUTING.md",
                    quote="Changes must preserve public API compatibility.",
                    content=b"Changes must preserve public API compatibility.\n",
                ),
                guideline.GuidelineEvidence(
                    path="docs/testing.md",
                    quote="Integration tests must use the shared cluster fixture.",
                    content=b"Integration tests must use the shared cluster fixture.\n",
                ),
            ),
            candidate_count=1,
            model_called=True,
            checkout_seconds=2.5,
            model_seconds=7.5,
            usage=guideline.TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
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
    assert "license_status" not in rows[0]
    assert rows[0]["checkout_seconds"] == "2.5"
    assert rows[0]["model_seconds"] == "7.5"
    assert rows[0]["guideline_file_count"] == "2"
    assert rows[0]["manual_review_path"] == ("manual-review/example/project/0123456789abcdef/index.md")
    guideline_files = json.loads(rows[0]["guideline_files_json"])
    assert [item["path"] for item in guideline_files] == ["CONTRIBUTING.md", "docs/testing.md"]
    with (output_dir / "guideline_files.csv").open(encoding="utf-8", newline="") as output_file:
        evidence_rows = list(csv.DictReader(output_file))
    assert [row["guideline_path"] for row in evidence_rows] == ["CONTRIBUTING.md", "docs/testing.md"]
    artifact_root = output_dir / "guideline-files" / "example" / "project" / "0123456789abcdef"
    assert (artifact_root / "CONTRIBUTING.md").read_bytes() == (b"Changes must preserve public API compatibility.\n")
    assert (artifact_root / "docs" / "testing.md").read_bytes() == (
        b"Integration tests must use the shared cluster fixture.\n"
    )
    review_page = output_dir / rows[0]["manual_review_path"]
    review_text = review_page.read_text(encoding="utf-8")
    assert "# Manual review: example/project" in review_text
    assert "https://github.com/example/project/tree/0123456789abcdef" in review_text
    assert "../../../../guideline-files/example/project/0123456789abcdef/CONTRIBUTING.md" in review_text
    assert "Changes must preserve public API compatibility." in review_text
    review_index = (output_dir / "manual-review" / "index.md").read_text(encoding="utf-8")
    assert "example/project/0123456789abcdef/index.md" in review_index
    summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["processed"] == 1
    assert summary["selected"] == 1
    assert summary["model_calls"] == 1
    assert summary["timing"]["checkout_seconds"] == 2.5
    assert summary["timing"]["model_seconds"] == 7.5
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
        ),
    )

    assert store.completed_repositories() == set()
    store.write_reports()
    header = (tmp_path / "output" / "selected_repositories.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "name" in header


def test_store_saves_the_model_response_with_unverified_evidence_details(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    model_response = {
        "status": "pass",
        "evidence": [
            {
                "path": "CONTRIBUTING.md",
                "quote": "Changes must preserve public API compatibility.",
            },
            {
                "path": "missing.md",
                "quote": "Tests must use the shared fixture.",
            },
        ],
    }
    result = pipeline.RepositoryResult(
        candidate=_candidate(),
        guideline=guideline.GuidelineResult(
            status=guideline.GuidelineStatus.PASS,
            reason="Verified evidence with one unverified item.",
            evidence=(
                guideline.GuidelineEvidence(
                    path="CONTRIBUTING.md",
                    quote="Changes must preserve public API compatibility.",
                    content=b"Changes must preserve public API compatibility.\n",
                ),
            ),
            evidence_issues=(
                guideline.GuidelineEvidenceIssue(
                    index=2,
                    path="missing.md",
                    quote="Tests must use the shared fixture.",
                    reason="path is not a file",
                ),
            ),
            model_response_json=json.dumps(model_response, ensure_ascii=True, sort_keys=True),
            model_called=True,
        ),
    )
    store = result_store.ResultStore(output_dir, configuration={"model": "gpt-5.6-luna"})
    store.initialize()

    store.append(result)

    record = json.loads((output_dir / "results.jsonl").read_text(encoding="utf-8"))
    response_path = "model-responses/example/project/0123456789abcdef/response.json"
    assert record["model_response_path"] == response_path
    assert record["unverified_evidence_count"] == 1
    unverified_evidence = [
        {
            "index": 2,
            "path": "missing.md",
            "quote": "Tests must use the shared fixture.",
            "reason": "path is not a file",
        },
    ]
    assert json.loads(record["unverified_evidence_json"]) == unverified_evidence
    assert json.loads((output_dir / response_path).read_text(encoding="utf-8")) == {
        "model_response": model_response,
        "unverified_evidence": unverified_evidence,
    }


def test_store_preserves_evidence_details_for_a_review_result(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    result = pipeline.RepositoryResult(
        candidate=_candidate(),
        guideline=guideline.GuidelineResult(
            status=guideline.GuidelineStatus.REVIEW,
            reason="Model returned 2 evidence items: 1 verified and 1 unverified",
            evidence=(
                guideline.GuidelineEvidence(
                    path="CONTRIBUTING.md",
                    quote="Changes must preserve public API compatibility.",
                    content=b"Changes must preserve public API compatibility.\n",
                ),
            ),
            evidence_issues=(
                guideline.GuidelineEvidenceIssue(
                    index=2,
                    path="missing.md",
                    quote="Tests must use the shared fixture.",
                    reason="path is not a file",
                ),
            ),
            model_called=True,
        ),
    )
    store = result_store.ResultStore(output_dir, configuration={"model": "gpt-5.6-luna"})
    store.initialize()

    store.append(result)

    record = json.loads((output_dir / "results.jsonl").read_text(encoding="utf-8"))
    assert record["guideline_status"] == "review"
    assert record["selected"] is False
    assert record["guideline_file_count"] == 1
    assert record["manual_review_path"] == "manual-review/example/project/0123456789abcdef/index.md"
    artifact_path = output_dir / "guideline-files/example/project/0123456789abcdef/CONTRIBUTING.md"
    assert artifact_path.read_bytes() == b"Changes must preserve public API compatibility.\n"
    review_text = (output_dir / record["manual_review_path"]).read_text(encoding="utf-8")
    assert "- Verified files: 1" in review_text
    assert "- Unverified evidence: 1" in review_text
    assert "## Unverified evidence 2: missing.md" in review_text
    assert "- Verification failure: path is not a file" in review_text
    assert "> Tests must use the shared fixture." in review_text


def test_store_creates_manual_review_for_only_unverified_evidence(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    result = pipeline.RepositoryResult(
        candidate=_candidate(),
        guideline=guideline.GuidelineResult(
            status=guideline.GuidelineStatus.REVIEW,
            reason="Model pass evidence could not be verified against the repository snapshot",
            evidence_issues=(
                guideline.GuidelineEvidenceIssue(
                    index=1,
                    path="repository/tests/README.md",
                    quote="Tests must use the shared fixture.",
                    reason="path is not a file",
                ),
            ),
            model_called=True,
        ),
    )
    store = result_store.ResultStore(output_dir, configuration={"model": "gpt-5.6-luna"})
    store.initialize()

    store.append(result)
    store.write_reports()

    record = json.loads((output_dir / "results.jsonl").read_text(encoding="utf-8"))
    assert record["manual_review_path"] == "manual-review/example/project/0123456789abcdef/index.md"
    review_text = (output_dir / record["manual_review_path"]).read_text(encoding="utf-8")
    assert "- Verified files: 0" in review_text
    assert "- Unverified evidence: 1" in review_text
    assert "## Unverified evidence 1: repository/tests/README.md" in review_text
    review_index = (output_dir / "manual-review/index.md").read_text(encoding="utf-8")
    assert "example/project/0123456789abcdef/index.md" in review_index
