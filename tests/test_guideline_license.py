"""Tests for reviewing and applying repository license names."""

import csv
import json
from pathlib import Path

import pytest

import guideline_license


def test_license_review_contains_only_repositories_with_non_duplicate_human_passes(tmp_path: Path) -> None:
    input_dir = tmp_path / "candidates"
    _write_candidates(
        input_dir,
        (
            ("example/accepted", "MIT License"),
            ("example/duplicate-only", "Apache License 2.0"),
            ("example/rejected", "BSD 3-Clause New or Revised License"),
        ),
    )
    checklist_path = tmp_path / "checklist.csv"
    _write_checklist(
        checklist_path,
        (
            _review_row("example/accepted", "accepted.md", human_decision="pass"),
            _review_row(
                "example/duplicate-only",
                "duplicate.md",
                human_decision="pass",
                duplicate_of="accepted.md",
            ),
            _review_row("example/rejected", "rejected.md", human_decision="not_found"),
        ),
    )
    output_dir = tmp_path / "license-review"

    guideline_license.prepare_license_review(
        input_dir=input_dir,
        baseline_checklist_paths=(),
        human_checklist_path=checklist_path,
        output_dir=output_dir,
    )

    assert _read_csv(output_dir / "repository_licenses.csv") == [
        {"repository": "example/accepted", "license_name": "MIT License"},
    ]


def test_legacy_baseline_without_duplicate_column_treats_pass_as_non_duplicate(tmp_path: Path) -> None:
    input_dir = tmp_path / "candidates"
    _write_candidates(
        input_dir,
        (
            ("example/baseline", "MIT License"),
            ("example/current", "Apache License 2.0"),
        ),
    )
    baseline_path = tmp_path / "baseline.csv"
    baseline_row = _review_row("example/baseline", "baseline.md", human_decision="pass")
    del baseline_row["duplicate_of"]
    _write_checklist(baseline_path, (baseline_row,))
    human_path = tmp_path / "human.csv"
    _write_checklist(
        human_path,
        (_review_row("example/current", "current.md", human_decision="pass"),),
    )
    output_dir = tmp_path / "license-review"

    guideline_license.prepare_license_review(
        input_dir=input_dir,
        baseline_checklist_paths=(baseline_path,),
        human_checklist_path=human_path,
        output_dir=output_dir,
    )

    assert _read_csv(output_dir / "repository_licenses.csv") == [
        {"repository": "example/baseline", "license_name": "MIT License"},
        {"repository": "example/current", "license_name": "Apache License 2.0"},
    ]


def test_license_review_rejects_an_accepted_repository_without_license_metadata(tmp_path: Path) -> None:
    input_dir = tmp_path / "candidates"
    _write_candidates(input_dir, (("example/available", "MIT License"),))
    checklist_path = tmp_path / "checklist.csv"
    _write_checklist(
        checklist_path,
        (_review_row("example/missing", "rules.md", human_decision="pass"),),
    )
    output_dir = tmp_path / "license-review"

    with pytest.raises(ValueError, match=r"license metadata is missing.*example/missing"):
        guideline_license.prepare_license_review(
            input_dir=input_dir,
            baseline_checklist_paths=(),
            human_checklist_path=checklist_path,
            output_dir=output_dir,
        )

    assert not output_dir.exists()


def test_license_allowlist_always_rejects_blank_and_other_license_names(tmp_path: Path) -> None:
    repository_licenses_path = tmp_path / "repository_licenses.csv"
    _write_csv(
        repository_licenses_path,
        ("repository", "license_name"),
        (
            {"repository": "example/allowed", "license_name": "MIT License"},
            {"repository": "example/blank", "license_name": ""},
            {"repository": "example/other", "license_name": "Other"},
            {"repository": "example/unlisted", "license_name": "Proprietary"},
        ),
    )
    allowlist_path = tmp_path / "license_allowlist.csv"
    _write_csv(
        allowlist_path,
        ("license_name",),
        (
            {"license_name": "MIT License"},
            {"license_name": "Other"},
        ),
    )
    output_dir = tmp_path / "applied"

    guideline_license.apply_license_allowlist(
        repository_licenses_path=repository_licenses_path,
        allowlist_path=allowlist_path,
        output_dir=output_dir,
    )

    assert _read_csv(output_dir / "accepted_repositories.csv") == [
        {"repository": "example/allowed", "license_name": "MIT License"},
    ]
    assert _read_csv(output_dir / "rejected_repositories.csv") == [
        {"repository": "example/blank", "license_name": ""},
        {"repository": "example/other", "license_name": "Other"},
        {"repository": "example/unlisted", "license_name": "Proprietary"},
    ]


def test_license_review_reports_repository_and_license_name_counts(tmp_path: Path) -> None:
    input_dir = tmp_path / "candidates"
    _write_candidates(
        input_dir,
        (
            ("example/blank", ""),
            ("example/mit", "MIT License"),
            ("example/other", "Other"),
        ),
    )
    checklist_path = tmp_path / "checklist.csv"
    _write_checklist(
        checklist_path,
        tuple(
            _review_row(repository, f"{repository.rsplit('/', 1)[1]}.md", human_decision="pass")
            for repository in ("example/blank", "example/mit", "example/other")
        ),
    )
    output_dir = tmp_path / "license-review"

    report = guideline_license.prepare_license_review(
        input_dir=input_dir,
        baseline_checklist_paths=(),
        human_checklist_path=checklist_path,
        output_dir=output_dir,
    )

    assert report.repositories == 3
    assert report.license_names == 3
    assert report.blank_license_repositories == 1
    assert report.other_license_repositories == 1
    assert report.legacy_baseline_checklists == 0
    assert report.output_dir == output_dir


def test_license_allowlist_reports_accepted_and_rejected_counts(tmp_path: Path) -> None:
    repository_licenses_path = tmp_path / "repository_licenses.csv"
    _write_csv(
        repository_licenses_path,
        ("repository", "license_name"),
        (
            {"repository": "example/allowed", "license_name": "MIT License"},
            {"repository": "example/rejected", "license_name": "Other"},
        ),
    )
    allowlist_path = tmp_path / "license_allowlist.csv"
    _write_csv(
        allowlist_path,
        ("license_name",),
        ({"license_name": "MIT License"},),
    )
    output_dir = tmp_path / "applied"

    report = guideline_license.apply_license_allowlist(
        repository_licenses_path=repository_licenses_path,
        allowlist_path=allowlist_path,
        output_dir=output_dir,
    )

    assert report.input_repositories == 2
    assert report.accepted_repositories == 1
    assert report.rejected_repositories == 1
    assert report.allowlisted_license_names == 1
    assert report.output_dir == output_dir


def test_license_review_persists_its_summary(tmp_path: Path) -> None:
    input_dir = tmp_path / "candidates"
    _write_candidates(input_dir, (("example/project", "MIT License"),))
    checklist_path = tmp_path / "checklist.csv"
    _write_checklist(
        checklist_path,
        (_review_row("example/project", "rules.md", human_decision="pass"),),
    )
    output_dir = tmp_path / "license-review"

    guideline_license.prepare_license_review(
        input_dir=input_dir,
        baseline_checklist_paths=(),
        human_checklist_path=checklist_path,
        output_dir=output_dir,
    )

    assert json.loads((output_dir / "summary.json").read_text(encoding="utf-8")) == {
        "blank_license_repositories": 0,
        "legacy_baseline_checklists": 0,
        "license_names": 1,
        "other_license_repositories": 0,
        "repositories": 1,
    }


def test_license_allowlist_persists_its_summary(tmp_path: Path) -> None:
    repository_licenses_path = tmp_path / "repository_licenses.csv"
    _write_csv(
        repository_licenses_path,
        ("repository", "license_name"),
        (
            {"repository": "example/allowed", "license_name": "MIT License"},
            {"repository": "example/rejected", "license_name": "Other"},
        ),
    )
    allowlist_path = tmp_path / "license_allowlist.csv"
    _write_csv(allowlist_path, ("license_name",), ({"license_name": "MIT License"},))
    output_dir = tmp_path / "applied"

    guideline_license.apply_license_allowlist(
        repository_licenses_path=repository_licenses_path,
        allowlist_path=allowlist_path,
        output_dir=output_dir,
    )

    assert json.loads((output_dir / "summary.json").read_text(encoding="utf-8")) == {
        "accepted_repositories": 1,
        "allowlisted_license_names": 1,
        "input_repositories": 2,
        "rejected_repositories": 1,
    }


def test_license_review_rejects_conflicting_metadata_for_one_repository(tmp_path: Path) -> None:
    input_dir = tmp_path / "candidates"
    input_dir.mkdir()
    _write_candidate_file(input_dir / "first.csv", (("example/project", "MIT License"),))
    _write_candidate_file(input_dir / "second.csv", (("example/project", "Apache License 2.0"),))
    checklist_path = tmp_path / "checklist.csv"
    _write_checklist(
        checklist_path,
        (_review_row("example/project", "rules.md", human_decision="pass"),),
    )
    output_dir = tmp_path / "license-review"

    with pytest.raises(ValueError, match=r"license metadata is ambiguous.*example/project"):
        guideline_license.prepare_license_review(
            input_dir=input_dir,
            baseline_checklist_paths=(),
            human_checklist_path=checklist_path,
            output_dir=output_dir,
        )

    assert not output_dir.exists()


def test_current_human_checklist_requires_duplicate_column(tmp_path: Path) -> None:
    input_dir = tmp_path / "candidates"
    _write_candidates(input_dir, (("example/project", "MIT License"),))
    checklist_path = tmp_path / "checklist.csv"
    review_row = _review_row("example/project", "rules.md", human_decision="pass")
    del review_row["duplicate_of"]
    _write_checklist(checklist_path, (review_row,))
    output_dir = tmp_path / "license-review"

    with pytest.raises(ValueError, match=r"checklist is missing required columns.*duplicate_of"):
        guideline_license.prepare_license_review(
            input_dir=input_dir,
            baseline_checklist_paths=(),
            human_checklist_path=checklist_path,
            output_dir=output_dir,
        )

    assert not output_dir.exists()


def test_license_allowlist_requires_one_license_name_column(tmp_path: Path) -> None:
    repository_licenses_path = tmp_path / "repository_licenses.csv"
    _write_csv(
        repository_licenses_path,
        ("repository", "license_name"),
        ({"repository": "example/project", "license_name": "MIT License"},),
    )
    allowlist_path = tmp_path / "license_allowlist.csv"
    _write_csv(
        allowlist_path,
        ("license_name", "decision"),
        ({"license_name": "MIT License", "decision": "pass"},),
    )
    output_dir = tmp_path / "applied"

    with pytest.raises(ValueError, match=r"CSV columns must be license_name"):
        guideline_license.apply_license_allowlist(
            repository_licenses_path=repository_licenses_path,
            allowlist_path=allowlist_path,
            output_dir=output_dir,
        )

    assert not output_dir.exists()


def _review_row(
    repository: str,
    file: str,
    *,
    human_decision: str,
    duplicate_of: str = "",
) -> dict[str, str]:
    return {
        "repository": repository,
        "file": file,
        "github_url": f"https://example.test/{file}",
        "human_decision": human_decision,
        "duplicate_of": duplicate_of,
    }


def _write_checklist(path: Path, rows: tuple[dict[str, str], ...]) -> None:
    _write_csv(path, tuple(rows[0]), rows)


def _write_candidates(path: Path, rows: tuple[tuple[str, str], ...]) -> None:
    path.mkdir()
    _write_candidate_file(path / "python.csv", rows)


def _write_candidate_file(path: Path, rows: tuple[tuple[str, str], ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=("name", "lastCommitSHA", "lastCommit", "defaultBranch", "license"),
        )
        writer.writeheader()
        writer.writerows(
            {
                "name": repository,
                "lastCommitSHA": "a" * 40,
                "lastCommit": "2026-08-01T00:00:00+00:00",
                "defaultBranch": "main",
                "license": license_name,
            }
            for repository, license_name in rows
        )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as input_file:
        return list(csv.DictReader(input_file))


def _write_csv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: tuple[dict[str, str], ...],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
