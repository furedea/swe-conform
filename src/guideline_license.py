"""Prepare repository license review inputs and apply human allowlists."""

import csv
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import repository

_REPOSITORY_LICENSES_FILENAME = "repository_licenses.csv"
_ACCEPTED_REPOSITORIES_FILENAME = "accepted_repositories.csv"
_REJECTED_REPOSITORIES_FILENAME = "rejected_repositories.csv"
_REPOSITORY_LICENSE_FIELDS = ("repository", "license_name")
_ALLOWLIST_FIELDS = ("license_name",)
_SELECTED_REPOSITORY_FIELDS = (
    "repository",
    "revision",
    "sampling_language",
    "license_name",
    "origin",
    "sample_order",
)
_AMBIGUOUS_LICENSE_NAME = "other"


@dataclass(frozen=True, slots=True)
class LicenseReviewReport:
    """Counts produced while preparing repository license review."""

    repositories: int
    license_names: int
    blank_license_repositories: int
    other_license_repositories: int
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


@dataclass(frozen=True, slots=True)
class CollectionLicensePolicy:
    """License eligibility state for one staged repository collection."""

    is_reviewed: bool
    allowlist: LicenseAllowlist | None
    eligible_repositories: frozenset[str]


def load_collection_license_policy(
    candidates: Sequence[repository.RepositoryCandidate],
    *,
    allowlist_path: Path | None,
) -> CollectionLicensePolicy:
    """Resolve provisional or reviewed repository license eligibility."""
    if allowlist_path is None:
        return CollectionLicensePolicy(
            is_reviewed=False,
            allowlist=None,
            eligible_repositories=frozenset(candidate.repository.casefold() for candidate in candidates),
        )
    allowlist = load_license_allowlist(allowlist_path)
    return CollectionLicensePolicy(
        is_reviewed=True,
        allowlist=allowlist,
        eligible_repositories=frozenset(
            candidate.repository.casefold() for candidate in candidates if allowlist.allows(candidate.license_name)
        ),
    )


def prepare_collection_license_review(
    *,
    collection_dir: Path,
    output_dir: Path,
) -> LicenseReviewReport:
    """Write license names for one collection's provisional selection."""
    configuration = json.loads(
        (collection_dir / "collection_configuration.json").read_text(encoding="utf-8"),
    )
    if not isinstance(configuration, dict) or configuration.get("license_policy_status") != "pending":
        raise ValueError("collection license review is already complete")
    summary = json.loads((collection_dir / "collection_summary.json").read_text(encoding="utf-8"))
    if not isinstance(summary, dict) or summary.get("target_reached") is not True:
        raise ValueError("provisional repository target is not reached")
    selected = _read_csv(
        collection_dir / "selected_repositories.csv",
        expected_fields=_SELECTED_REPOSITORY_FIELDS,
    )
    _validate_unique_selected_repositories(selected)
    _validate_provisional_selection(configuration, summary, selected)
    rows = tuple(
        {
            "repository": row["repository"],
            "license_name": row["license_name"],
        }
        for row in selected
    )
    report = LicenseReviewReport(
        repositories=len(rows),
        license_names=len({row["license_name"] for row in rows}),
        blank_license_repositories=sum(not row["license_name"].strip() for row in rows),
        other_license_repositories=sum(
            row["license_name"].strip().casefold() == _AMBIGUOUS_LICENSE_NAME for row in rows
        ),
        output_dir=output_dir,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / _REPOSITORY_LICENSES_FILENAME, _REPOSITORY_LICENSE_FIELDS, rows)
    _write_json(
        output_dir / "summary.json",
        {
            "blank_license_repositories": report.blank_license_repositories,
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


def _validate_unique_selected_repositories(rows: Sequence[Mapping[str, str]]) -> None:
    seen: set[str] = set()
    for row in rows:
        repository_name = row["repository"].strip().casefold()
        if repository_name in seen:
            raise ValueError(f"duplicate selected repository: {repository_name}")
        seen.add(repository_name)


def _validate_provisional_selection(
    configuration: Mapping[str, object],
    summary: Mapping[str, object],
    rows: Sequence[Mapping[str, str]],
) -> None:
    target_total = configuration.get("target_total_repositories")
    raw_target_counts = configuration.get("target_repositories_by_language")
    if not isinstance(target_total, int) or not isinstance(raw_target_counts, dict):
        raise ValueError("provisional selection does not match recorded quotas")
    target_counts: dict[str, int] = {}
    for language, count in raw_target_counts.items():
        if not isinstance(language, str) or not isinstance(count, int):
            raise ValueError("provisional selection does not match recorded quotas")
        target_counts[language] = count
    selected_counts = Counter(row["sampling_language"] for row in rows)
    if (
        len(rows) != target_total
        or summary.get("selected_total_repositories") != target_total
        or sum(target_counts.values()) != target_total
        or any(selected_counts[language] != count for language, count in target_counts.items())
        or any(language not in target_counts for language in selected_counts)
    ):
        raise ValueError("provisional selection does not match recorded quotas")


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
