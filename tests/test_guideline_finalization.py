"""Tests for materializing the final guideline collection."""

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

import guideline_finalization


@dataclass(frozen=True, slots=True)
class FinalizationInputs:
    """Paths and revisions for one valid finalization input set."""

    collection_dir: Path
    baseline_path: Path
    human_path: Path
    output_dir: Path
    baseline_revision: str
    new_revision: str


def test_validated_collection_is_materialized_as_one_final_bundle(tmp_path: Path) -> None:
    inputs = _valid_inputs(tmp_path)

    report = guideline_finalization.finalize_guideline_collection(
        collection_dir=inputs.collection_dir,
        baseline_checklist_paths=(inputs.baseline_path,),
        human_checklist_path=inputs.human_path,
        output_dir=inputs.output_dir,
    )

    assert report.repositories == 2
    assert report.guideline_files == 2
    assert _read_csv(inputs.output_dir / "repositories.csv") == [
        {
            "repository": "example/baseline",
            "revision": inputs.baseline_revision,
            "sampling_language": "Python",
            "origin": "baseline",
            "sample_order": "",
            "guideline_file_count": "1",
        },
        {
            "repository": "example/new",
            "revision": inputs.new_revision,
            "sampling_language": "Java",
            "origin": "new",
            "sample_order": "7",
            "guideline_file_count": "1",
        },
    ]
    guideline_rows = _read_csv(inputs.output_dir / "guideline_files.csv")
    assert [row["file"] for row in guideline_rows] == [
        "example--baseline/CONTRIBUTING.md",
        "example--new/AGENTS.md",
    ]
    assert json.loads((inputs.output_dir / "summary.json").read_text(encoding="utf-8"))["status"] == "passed"
    provenance = json.loads((inputs.output_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["classification"]["model"] == "gpt-5.6-luna"
    assert set(provenance["artifacts"]) == {"guideline_files.csv", "repositories.csv", "summary.json"}
    first_bundle = {path.name: path.read_bytes() for path in inputs.output_dir.iterdir()}

    guideline_finalization.finalize_guideline_collection(
        collection_dir=inputs.collection_dir,
        baseline_checklist_paths=(inputs.baseline_path,),
        human_checklist_path=inputs.human_path,
        output_dir=inputs.output_dir,
    )

    assert {path.name: path.read_bytes() for path in inputs.output_dir.iterdir()} == first_bundle


def test_unrelated_existing_output_is_rejected_before_final_artifacts_are_written(tmp_path: Path) -> None:
    inputs = _valid_inputs(tmp_path)
    inputs.output_dir.mkdir()
    unrelated_path = inputs.output_dir / "notes.txt"
    unrelated_path.write_text("keep me\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"unexpected final output artifact.*notes\.txt"):
        guideline_finalization.finalize_guideline_collection(
            collection_dir=inputs.collection_dir,
            baseline_checklist_paths=(inputs.baseline_path,),
            human_checklist_path=inputs.human_path,
            output_dir=inputs.output_dir,
        )

    assert tuple(inputs.output_dir.iterdir()) == (unrelated_path,)


def test_non_github_review_url_is_rejected_before_outputs_are_written(tmp_path: Path) -> None:
    inputs = _valid_inputs(tmp_path)
    rows = _read_csv(inputs.human_path)
    rows[0]["github_url"] = f"https://example.test/example/new/blob/{inputs.new_revision}/AGENTS.md"
    _write_csv(inputs.human_path, tuple(rows))

    with pytest.raises(ValueError, match=r"GitHub URL does not identify.*example\.test"):
        guideline_finalization.finalize_guideline_collection(
            collection_dir=inputs.collection_dir,
            baseline_checklist_paths=(inputs.baseline_path,),
            human_checklist_path=inputs.human_path,
            output_dir=inputs.output_dir,
        )

    assert not inputs.output_dir.exists()


def test_blank_accepted_file_identifier_is_rejected_before_outputs_are_written(tmp_path: Path) -> None:
    inputs = _valid_inputs(tmp_path)
    rows = _read_csv(inputs.human_path)
    rows[0]["file"] = ""
    _write_csv(inputs.human_path, tuple(rows))

    with pytest.raises(ValueError, match="accepted guideline file must be non-empty"):
        guideline_finalization.finalize_guideline_collection(
            collection_dir=inputs.collection_dir,
            baseline_checklist_paths=(inputs.baseline_path,),
            human_checklist_path=inputs.human_path,
            output_dir=inputs.output_dir,
        )

    assert not inputs.output_dir.exists()


def test_unpinned_selected_revision_is_rejected_before_outputs_are_written(tmp_path: Path) -> None:
    inputs = _valid_inputs(tmp_path)
    selected_rows = _read_csv(inputs.collection_dir / "selected_repositories.csv")
    selected_rows[1]["revision"] = "main"
    _write_csv(inputs.collection_dir / "selected_repositories.csv", tuple(selected_rows))
    human_rows = _read_csv(inputs.human_path)
    human_rows[0]["github_url"] = "https://github.com/example/new/blob/main/AGENTS.md"
    _write_csv(inputs.human_path, tuple(human_rows))

    with pytest.raises(ValueError, match=r"revision must be a 40-character commit SHA.*example/new"):
        guideline_finalization.finalize_guideline_collection(
            collection_dir=inputs.collection_dir,
            baseline_checklist_paths=(inputs.baseline_path,),
            human_checklist_path=inputs.human_path,
            output_dir=inputs.output_dir,
        )

    assert not inputs.output_dir.exists()


def test_selected_repository_without_an_accepted_guideline_is_rejected(tmp_path: Path) -> None:
    inputs = _valid_inputs(tmp_path)
    human_rows = _read_csv(inputs.human_path)
    human_rows[0]["human_decision"] = "not_found"
    _write_csv(inputs.human_path, tuple(human_rows))

    with pytest.raises(ValueError, match="selected new repositories do not match human-accepted repositories"):
        guideline_finalization.finalize_guideline_collection(
            collection_dir=inputs.collection_dir,
            baseline_checklist_paths=(inputs.baseline_path,),
            human_checklist_path=inputs.human_path,
            output_dir=inputs.output_dir,
        )

    assert not inputs.output_dir.exists()


def test_changed_baseline_checklist_is_rejected_by_its_recorded_fingerprint(tmp_path: Path) -> None:
    inputs = _valid_inputs(tmp_path)
    with inputs.baseline_path.open("a", encoding="utf-8") as output_file:
        output_file.write("\n")

    with pytest.raises(ValueError, match="baseline checklist fingerprints do not match"):
        guideline_finalization.finalize_guideline_collection(
            collection_dir=inputs.collection_dir,
            baseline_checklist_paths=(inputs.baseline_path,),
            human_checklist_path=inputs.human_path,
            output_dir=inputs.output_dir,
        )

    assert not inputs.output_dir.exists()


def _valid_inputs(tmp_path: Path) -> FinalizationInputs:
    collection_dir = tmp_path / "collection"
    collection_dir.mkdir()
    baseline_path = tmp_path / "baseline.csv"
    human_path = tmp_path / "human.csv"
    output_dir = collection_dir / "final"
    baseline_revision = "a" * 40
    new_revision = "b" * 40
    _write_csv(
        collection_dir / "selected_repositories.csv",
        (
            {
                "repository": "example/baseline",
                "revision": baseline_revision,
                "sampling_language": "Python",
                "origin": "baseline",
                "sample_order": "",
            },
            {
                "repository": "example/new",
                "revision": new_revision,
                "sampling_language": "Java",
                "origin": "new_pending",
                "sample_order": "7",
            },
        ),
    )
    _write_csv(
        baseline_path,
        (
            _review_row(
                repository="example/baseline",
                file="example--baseline/CONTRIBUTING.md",
                revision=baseline_revision,
                review_origin="baseline_review",
            ),
        ),
    )
    _write_csv(
        human_path,
        (
            {
                **_review_row(
                    repository="example/new",
                    file="example--new/AGENTS.md",
                    revision=new_revision,
                    review_origin="added_round_2",
                ),
                "duplicate_of": "",
            },
        ),
    )
    configuration = {
        "baseline_checklist_fingerprints": {str(baseline_path.resolve()): _sha256(baseline_path)},
        "baseline_repositories": ["example/baseline"],
        "classification_contract_sha256": "contract-sha256",
        "filter": {"filename_terms": ["agents"]},
        "languages": ["Java", "Python"],
        "model": "gpt-5.6-luna",
        "provider": "bedrock",
        "reasoning_effort": "max",
        "sample_seed": 20260807,
        "sampling_method": "stratified_random_round_robin_until_target",
        "target_total_repositories": 2,
    }
    (collection_dir / "collection_configuration.json").write_text(
        f"{json.dumps(configuration, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )
    return FinalizationInputs(
        collection_dir=collection_dir,
        baseline_path=baseline_path,
        human_path=human_path,
        output_dir=output_dir,
        baseline_revision=baseline_revision,
        new_revision=new_revision,
    )


def _review_row(
    *,
    repository: str,
    file: str,
    revision: str,
    review_origin: str,
) -> dict[str, str]:
    return {
        "repository": repository,
        "file": file,
        "github_url": f"https://github.com/{repository}/blob/{revision}/RULES.md",
        "review_origin": review_origin,
        "llm_decision": "pass",
        "human_decision": "pass",
        "codex_decision": "pass",
        "codex_reason": "The file contains a project rule.",
        "note": "",
    }


def _write_csv(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as input_file:
        return list(csv.DictReader(input_file))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
