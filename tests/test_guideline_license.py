"""Tests for reviewing and applying repository license names."""

import csv
import json
from pathlib import Path

import pytest

import guideline_license
import repository


def test_collection_license_policy_keeps_all_repositories_pending_before_review() -> None:
    candidates = (
        _candidate("example/mit", "MIT License"),
        _candidate("example/gpl", "GNU GPL v3.0"),
    )

    policy = guideline_license.load_collection_license_policy(candidates, allowlist_path=None)

    assert not policy.is_reviewed
    assert policy.allowlist is None
    assert policy.eligible_repositories == frozenset({"example/mit", "example/gpl"})


def test_license_review_uses_provisional_selected_repositories(tmp_path: Path) -> None:
    collection_dir = tmp_path / "collection"
    collection_dir.mkdir()
    _write_collection_configuration(
        collection_dir,
        license_policy_status="pending",
        selected_repositories=2,
    )
    _write_collection_summary(collection_dir, target_reached=True, selected_repositories=2)
    _write_selected_repositories(
        collection_dir / "selected_repositories.csv",
        (
            ("example/baseline", "MIT License", "baseline"),
            ("example/new", "Apache License 2.0", "new_pending"),
        ),
    )
    output_dir = tmp_path / "license-review"

    guideline_license.prepare_collection_license_review(
        collection_dir=collection_dir,
        output_dir=output_dir,
    )

    assert _read_csv(output_dir / "repository_licenses.csv") == [
        {"repository": "example/baseline", "license_name": "MIT License"},
        {"repository": "example/new", "license_name": "Apache License 2.0"},
    ]


def test_license_review_rejects_an_incomplete_provisional_selection(tmp_path: Path) -> None:
    collection_dir = tmp_path / "collection"
    collection_dir.mkdir()
    _write_collection_configuration(
        collection_dir,
        license_policy_status="pending",
        selected_repositories=1,
    )
    _write_collection_summary(collection_dir, target_reached=False, selected_repositories=1)
    _write_selected_repositories(
        collection_dir / "selected_repositories.csv",
        (("example/pending", "MIT License", "new_pending"),),
    )
    output_dir = tmp_path / "license-review"

    with pytest.raises(ValueError, match="provisional repository target is not reached"):
        guideline_license.prepare_collection_license_review(
            collection_dir=collection_dir,
            output_dir=output_dir,
        )

    assert not output_dir.exists()


def test_license_review_rejects_a_collection_with_an_applied_policy(tmp_path: Path) -> None:
    collection_dir = tmp_path / "collection"
    collection_dir.mkdir()
    _write_collection_configuration(
        collection_dir,
        license_policy_status="applied",
        selected_repositories=1,
    )
    _write_collection_summary(collection_dir, target_reached=True, selected_repositories=1)
    _write_selected_repositories(
        collection_dir / "selected_repositories.csv",
        (("example/selected", "MIT License", "new_pending"),),
    )

    with pytest.raises(ValueError, match="collection license review is already complete"):
        guideline_license.prepare_collection_license_review(
            collection_dir=collection_dir,
            output_dir=tmp_path / "license-review",
        )


def test_license_review_rejects_a_selection_that_does_not_match_its_recorded_quota(
    tmp_path: Path,
) -> None:
    collection_dir = tmp_path / "collection"
    collection_dir.mkdir()
    _write_collection_configuration(
        collection_dir,
        license_policy_status="pending",
        selected_repositories=2,
    )
    _write_collection_summary(
        collection_dir,
        target_reached=True,
        selected_repositories=2,
    )
    _write_selected_repositories(
        collection_dir / "selected_repositories.csv",
        (("example/only-one", "MIT License", "new_pending"),),
    )

    with pytest.raises(ValueError, match="provisional selection does not match recorded quotas"):
        guideline_license.prepare_collection_license_review(
            collection_dir=collection_dir,
            output_dir=tmp_path / "license-review",
        )


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


def test_loaded_license_allowlist_reuses_the_human_policy_for_collection(tmp_path: Path) -> None:
    allowlist_path = tmp_path / "license_allowlist.csv"
    _write_csv(
        allowlist_path,
        ("license_name",),
        (
            {"license_name": "MIT License"},
            {"license_name": "Other"},
        ),
    )

    allowlist = guideline_license.load_license_allowlist(allowlist_path)

    assert allowlist.allows("MIT License")
    assert not allowlist.allows("GNU GPL v3.0")
    assert not allowlist.allows("Other")
    assert not allowlist.allows("")


def test_license_review_reports_repository_and_license_name_counts(tmp_path: Path) -> None:
    collection_dir = tmp_path / "collection"
    collection_dir.mkdir()
    _write_collection_configuration(
        collection_dir,
        license_policy_status="pending",
        selected_repositories=3,
    )
    _write_collection_summary(collection_dir, target_reached=True, selected_repositories=3)
    _write_selected_repositories(
        collection_dir / "selected_repositories.csv",
        (
            ("example/blank", "", "baseline"),
            ("example/mit", "MIT License", "new_pending"),
            ("example/other", "Other", "new_pending"),
        ),
    )
    output_dir = tmp_path / "license-review"

    report = guideline_license.prepare_collection_license_review(
        collection_dir=collection_dir,
        output_dir=output_dir,
    )

    assert report.repositories == 3
    assert report.license_names == 3
    assert report.blank_license_repositories == 1
    assert report.other_license_repositories == 1
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
    collection_dir = tmp_path / "collection"
    collection_dir.mkdir()
    _write_collection_configuration(
        collection_dir,
        license_policy_status="pending",
        selected_repositories=1,
    )
    _write_collection_summary(collection_dir, target_reached=True, selected_repositories=1)
    _write_selected_repositories(
        collection_dir / "selected_repositories.csv",
        (("example/project", "MIT License", "new_pending"),),
    )
    output_dir = tmp_path / "license-review"

    guideline_license.prepare_collection_license_review(
        collection_dir=collection_dir,
        output_dir=output_dir,
    )

    assert json.loads((output_dir / "summary.json").read_text(encoding="utf-8")) == {
        "blank_license_repositories": 0,
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


def test_license_review_rejects_a_duplicate_selected_repository(tmp_path: Path) -> None:
    collection_dir = tmp_path / "collection"
    collection_dir.mkdir()
    _write_collection_configuration(
        collection_dir,
        license_policy_status="pending",
        selected_repositories=2,
    )
    _write_collection_summary(collection_dir, target_reached=True, selected_repositories=2)
    _write_selected_repositories(
        collection_dir / "selected_repositories.csv",
        (
            ("example/project", "MIT License", "baseline"),
            ("EXAMPLE/PROJECT", "Apache License 2.0", "new_pending"),
        ),
    )
    output_dir = tmp_path / "license-review"

    with pytest.raises(ValueError, match=r"duplicate selected repository.*example/project"):
        guideline_license.prepare_collection_license_review(
            collection_dir=collection_dir,
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


def _candidate(repository_name: str, license_name: str) -> repository.RepositoryCandidate:
    return repository.RepositoryCandidate(
        repository=repository_name,
        revision="a" * 40,
        license_name=license_name,
        source_file="python.csv",
        input_index=0,
        fields={"mainLanguage": "Python"},
    )


def _write_selected_repositories(
    path: Path,
    rows: tuple[tuple[str, str, str], ...],
) -> None:
    _write_csv(
        path,
        ("repository", "revision", "sampling_language", "license_name", "origin", "sample_order"),
        tuple(
            {
                "repository": repository_name,
                "revision": "a" * 40,
                "sampling_language": "Python",
                "license_name": license_name,
                "origin": origin,
                "sample_order": "" if origin == "baseline" else str(index),
            }
            for index, (repository_name, license_name, origin) in enumerate(rows, start=1)
        ),
    )


def _write_collection_summary(
    collection_dir: Path,
    *,
    target_reached: bool,
    selected_repositories: int,
) -> None:
    (collection_dir / "collection_summary.json").write_text(
        f"{json.dumps({'selected_total_repositories': selected_repositories, 'target_reached': target_reached})}\n",
        encoding="utf-8",
    )


def _write_collection_configuration(
    collection_dir: Path,
    *,
    license_policy_status: str,
    selected_repositories: int,
) -> None:
    document = {
        "license_policy_status": license_policy_status,
        "target_total_repositories": selected_repositories,
        "target_repositories_by_language": {"Python": selected_repositories},
    }
    (collection_dir / "collection_configuration.json").write_text(
        f"{json.dumps(document)}\n",
        encoding="utf-8",
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
