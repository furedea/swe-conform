"""Prepare repository license review inputs and apply human allowlists."""

import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import guideline_review
import repository

_REPOSITORY_LICENSES_FILENAME = "repository_licenses.csv"
_ACCEPTED_REPOSITORIES_FILENAME = "accepted_repositories.csv"
_REJECTED_REPOSITORIES_FILENAME = "rejected_repositories.csv"
_REPOSITORY_LICENSE_FIELDS = ("repository", "license_name")
_ALLOWLIST_FIELDS = ("license_name",)
_AMBIGUOUS_LICENSE_NAME = "other"


@dataclass(frozen=True, slots=True)
class LicenseReviewReport:
    """Counts produced while preparing repository license review."""

    repositories: int
    license_names: int
    blank_license_repositories: int
    other_license_repositories: int
    legacy_baseline_checklists: int
    output_dir: Path


@dataclass(frozen=True, slots=True)
class LicenseAllowlistReport:
    """Counts produced while applying a license-name allowlist."""

    input_repositories: int
    accepted_repositories: int
    rejected_repositories: int
    allowlisted_license_names: int
    output_dir: Path


@dataclass(frozen=True, slots=True)
class LicenseAllowlist:
    """Human-approved reported license names used across selection stages."""

    license_names: frozenset[str]

    def allows(self, license_name: str) -> bool:
        """Return whether one reported license name is eligible."""
        normalized = license_name.strip()
        return (
            bool(normalized) and normalized.casefold() != _AMBIGUOUS_LICENSE_NAME and normalized in self.license_names
        )


def prepare_license_review(
    *,
    input_dir: Path,
    baseline_checklist_paths: Sequence[Path],
    human_checklist_path: Path,
    output_dir: Path,
) -> LicenseReviewReport:
    """Write license names for repositories accepted by completed reviews."""
    accepted_repositories, legacy_baseline_checklists = _accepted_repositories(
        baseline_checklist_paths=baseline_checklist_paths,
        human_checklist_path=human_checklist_path,
    )
    candidates = repository.load_repository_candidates(input_dir)
    candidates_by_name = _candidates_by_name(candidates)
    missing = sorted(accepted_repositories.difference(candidates_by_name))
    if missing:
        raise ValueError(f"license metadata is missing for accepted repository: {missing[0]}")
    rows = tuple(
        {
            "repository": candidates_by_name[name].repository,
            "license_name": candidates_by_name[name].license_name,
        }
        for name in sorted(accepted_repositories)
    )
    report = LicenseReviewReport(
        repositories=len(rows),
        license_names=len({row["license_name"] for row in rows}),
        blank_license_repositories=sum(not str(row["license_name"]).strip() for row in rows),
        other_license_repositories=sum(
            str(row["license_name"]).strip().casefold() == _AMBIGUOUS_LICENSE_NAME for row in rows
        ),
        legacy_baseline_checklists=legacy_baseline_checklists,
        output_dir=output_dir,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / _REPOSITORY_LICENSES_FILENAME, _REPOSITORY_LICENSE_FIELDS, rows)
    _write_json(
        output_dir / "summary.json",
        {
            "blank_license_repositories": report.blank_license_repositories,
            "legacy_baseline_checklists": report.legacy_baseline_checklists,
            "license_names": report.license_names,
            "other_license_repositories": report.other_license_repositories,
            "repositories": report.repositories,
        },
    )
    return report


def apply_license_allowlist(
    *,
    repository_licenses_path: Path,
    allowlist_path: Path,
    output_dir: Path,
) -> LicenseAllowlistReport:
    """Partition repositories through a human-authored license-name allowlist."""
    repository_rows = _read_csv(repository_licenses_path, expected_fields=_REPOSITORY_LICENSE_FIELDS)
    allowlist = load_license_allowlist(allowlist_path)
    accepted = tuple(row for row in repository_rows if allowlist.allows(row["license_name"]))
    rejected = tuple(row for row in repository_rows if not allowlist.allows(row["license_name"]))
    report = LicenseAllowlistReport(
        input_repositories=len(repository_rows),
        accepted_repositories=len(accepted),
        rejected_repositories=len(rejected),
        allowlisted_license_names=len(allowlist.license_names),
        output_dir=output_dir,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / _ACCEPTED_REPOSITORIES_FILENAME, _REPOSITORY_LICENSE_FIELDS, accepted)
    _write_csv(output_dir / _REJECTED_REPOSITORIES_FILENAME, _REPOSITORY_LICENSE_FIELDS, rejected)
    _write_json(
        output_dir / "summary.json",
        {
            "accepted_repositories": report.accepted_repositories,
            "allowlisted_license_names": report.allowlisted_license_names,
            "input_repositories": report.input_repositories,
            "rejected_repositories": report.rejected_repositories,
        },
    )
    return report


def load_license_allowlist(path: Path) -> LicenseAllowlist:
    """Load the human-authored license-name policy used by repository selection."""
    rows = _read_csv(path, expected_fields=_ALLOWLIST_FIELDS)
    names = frozenset(
        normalized
        for row in rows
        if (normalized := row["license_name"].strip())
        if normalized.casefold() != _AMBIGUOUS_LICENSE_NAME
    )
    return LicenseAllowlist(names)


def _accepted_repositories(
    *,
    baseline_checklist_paths: Sequence[Path],
    human_checklist_path: Path,
) -> tuple[set[str], int]:
    repositories: set[str] = set()
    legacy_baseline_checklists = 0
    for checklist_path in baseline_checklist_paths:
        fieldnames, rows = guideline_review.load_completed_guideline_rows(
            checklist_path,
            require_duplicate_column=False,
        )
        legacy_baseline_checklists += "duplicate_of" not in fieldnames
        repositories.update(_accepted_repository_names(rows))
    _, human_rows = guideline_review.load_completed_guideline_rows(human_checklist_path)
    repositories.update(_accepted_repository_names(human_rows))
    return repositories, legacy_baseline_checklists


def _candidates_by_name(
    candidates: Sequence[repository.RepositoryCandidate],
) -> dict[str, repository.RepositoryCandidate]:
    indexed: dict[str, repository.RepositoryCandidate] = {}
    for candidate in candidates:
        normalized = candidate.repository.casefold()
        existing = indexed.get(normalized)
        if existing is not None and (
            existing.revision != candidate.revision or existing.license_name != candidate.license_name
        ):
            raise ValueError(f"license metadata is ambiguous for repository: {candidate.repository}")
        indexed[normalized] = candidate
    return indexed


def _accepted_repository_names(rows: Sequence[Mapping[str, str]]) -> set[str]:
    return {
        row["repository"].strip().casefold()
        for row in rows
        if row["human_decision"].strip() == "pass"
        if not row["duplicate_of"].strip()
        if row["repository"].strip()
    }


def _read_csv(path: Path, *, expected_fields: Sequence[str]) -> tuple[dict[str, str], ...]:
    with path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = tuple(reader.fieldnames or ())
        if fieldnames != tuple(expected_fields):
            raise ValueError(f"CSV columns must be {', '.join(expected_fields)}: {path}")
        return tuple(dict(row) for row in reader)


def _write_csv(
    path: Path,
    fieldnames: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(path)


def _write_json(path: Path, document: Mapping[str, object]) -> None:
    value = f"{json.dumps(document, indent=2, ensure_ascii=True, sort_keys=True)}\n"
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(value, encoding="utf-8")
    temporary_path.replace(path)
