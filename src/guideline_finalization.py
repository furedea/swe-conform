"""Validate and materialize one final project-guideline collection bundle."""

import csv
import hashlib
import io
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import guideline_license
import guideline_review

_CONFIGURATION_FILENAME = "collection_configuration.json"
_SELECTED_REPOSITORIES_FILENAME = "selected_repositories.csv"
_REPOSITORIES_FILENAME = "repositories.csv"
_GUIDELINE_FILES_FILENAME = "guideline_files.csv"
_SUMMARY_FILENAME = "summary.json"
_PROVENANCE_FILENAME = "provenance.json"
_COLLECTION_SCHEMA_VERSION = 4
_FINAL_FILENAMES = frozenset(
    {
        _REPOSITORIES_FILENAME,
        _GUIDELINE_FILES_FILENAME,
        _SUMMARY_FILENAME,
        _PROVENANCE_FILENAME,
    },
)
_SELECTED_FIELDS = (
    "repository",
    "revision",
    "sampling_language",
    "origin",
    "sample_order",
)
_REPOSITORY_FIELDS = (
    "repository",
    "revision",
    "sampling_language",
    "license_name",
    "origin",
    "sample_order",
    "guideline_file_count",
)
_GUIDELINE_FIELDS = (
    "repository",
    "revision",
    "sampling_language",
    "origin",
    "sample_order",
    "file",
    "github_url",
    "review_origin",
    "llm_decision",
    "human_decision",
    "duplicate_of",
    "codex_decision",
    "codex_reason",
    "note",
    "source_checklist",
)
_CLASSIFICATION_CONFIGURATION_FIELDS = (
    "classification_contract_sha256",
    "filter",
    "max_input_bytes",
    "max_output_tokens",
    "model",
    "provider",
    "reasoning_effort",
    "region",
)
_COLLECTION_CONFIGURATION_FIELDS = (
    "languages",
    "license_allowlist",
    "license_allowlist_fingerprints",
    "license_ineligible_reviewed_repositories",
    "sample_seed",
    "sampling_method",
    "target_total_repositories",
    "target_repositories_by_language",
)
_COMMIT_SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True, slots=True)
class GuidelineFinalizationReport:
    """Counts for one validated final guideline collection."""

    repositories: int
    guideline_files: int
    baseline_repositories: int
    new_repositories: int
    baseline_guideline_files: int
    new_guideline_files: int
    output_dir: Path


def finalize_guideline_collection(
    *,
    collection_dir: Path,
    baseline_checklist_paths: Sequence[Path],
    human_checklist_path: Path,
    license_allowlist_path: Path,
    output_dir: Path,
) -> GuidelineFinalizationReport:
    """Validate all final inputs before writing deterministic collection artifacts."""
    _validate_output_dir(output_dir)
    configuration_path = collection_dir / _CONFIGURATION_FILENAME
    selected_path = collection_dir / _SELECTED_REPOSITORIES_FILENAME
    configuration = _read_json(configuration_path)
    _validate_collection_configuration_schema(configuration)
    selected = _selected_repositories(selected_path)
    license_allowlist = _validated_license_allowlist(configuration, path=license_allowlist_path)
    _validate_selected_licenses(license_allowlist, selected=selected)
    _validate_baseline_fingerprints(configuration, baseline_checklist_paths)
    sources = [
        *(
            _review_source(path, origin="baseline", require_duplicate_column=False)
            for path in baseline_checklist_paths
        ),
        _review_source(human_checklist_path, origin="new", require_duplicate_column=True),
    ]
    license_ineligible_repositories = _license_ineligible_reviewed_repositories(configuration)
    accepted = tuple(
        (source_path, origin, row)
        for source_path, origin, rows in sources
        for row in rows
        if _is_accepted(row)
        if row["repository"].strip().casefold() not in license_ineligible_repositories
    )
    repositories_by_name = {row["repository"].casefold(): row for row in selected}
    _validate_repository_sets(configuration, selected=selected, accepted=accepted)
    guideline_rows = _guideline_rows(accepted, repositories_by_name=repositories_by_name)
    repository_rows = _repository_rows(selected, guideline_rows=guideline_rows)
    summary = _summary(
        configuration,
        sources=sources,
        guideline_rows=guideline_rows,
        selected=selected,
        license_ineligible_repositories=license_ineligible_repositories,
    )
    repository_text = _csv_text(_REPOSITORY_FIELDS, repository_rows)
    guideline_text = _csv_text(_GUIDELINE_FIELDS, guideline_rows)
    summary_text = _json_text(summary)
    provenance = _provenance(
        configuration,
        configuration_path=configuration_path,
        selected_path=selected_path,
        baseline_checklist_paths=baseline_checklist_paths,
        human_checklist_path=human_checklist_path,
        license_allowlist_path=license_allowlist_path,
        artifacts={
            _REPOSITORIES_FILENAME: _sha256_text(repository_text),
            _GUIDELINE_FILES_FILENAME: _sha256_text(guideline_text),
            _SUMMARY_FILENAME: _sha256_text(summary_text),
        },
    )
    _write_bundle(
        output_dir,
        {
            _REPOSITORIES_FILENAME: repository_text,
            _GUIDELINE_FILES_FILENAME: guideline_text,
            _SUMMARY_FILENAME: summary_text,
            _PROVENANCE_FILENAME: _json_text(provenance),
        },
    )
    baseline_files = sum(row["origin"] == "baseline" for row in guideline_rows)
    baseline_repositories = sum(row["origin"] == "baseline" for row in repository_rows)
    return GuidelineFinalizationReport(
        repositories=len(repository_rows),
        guideline_files=len(guideline_rows),
        baseline_repositories=baseline_repositories,
        new_repositories=len(repository_rows) - baseline_repositories,
        baseline_guideline_files=baseline_files,
        new_guideline_files=len(guideline_rows) - baseline_files,
        output_dir=output_dir,
    )


def _validate_output_dir(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    unexpected = sorted(path.name for path in output_dir.iterdir() if path.name not in _FINAL_FILENAMES)
    if unexpected:
        raise ValueError(f"unexpected final output artifact: {unexpected[0]}")


def _validate_collection_configuration_schema(configuration: Mapping[str, object]) -> None:
    if configuration.get("schema_version") != _COLLECTION_SCHEMA_VERSION:
        raise ValueError("unsupported collection configuration schema")


def _review_source(
    path: Path,
    *,
    origin: str,
    require_duplicate_column: bool,
) -> tuple[Path, str, tuple[dict[str, str], ...]]:
    _, rows = guideline_review.load_completed_guideline_rows(
        path,
        require_duplicate_column=require_duplicate_column,
    )
    return path, origin, rows


def _selected_repositories(path: Path) -> tuple[dict[str, str], ...]:
    with path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = tuple(reader.fieldnames or ())
        missing = set(_SELECTED_FIELDS).difference(fieldnames)
        if missing:
            raise ValueError(f"selected repositories are missing required columns: {', '.join(sorted(missing))}")
        rows = tuple(dict(row) for row in reader)
    names = [row["repository"].strip().casefold() for row in rows]
    if len(names) != len(set(names)):
        raise ValueError("selected repositories must be unique")
    for row in rows:
        if not row["repository"].strip() or not row["revision"].strip():
            raise ValueError("selected repository and revision must be non-empty")
        if _COMMIT_SHA.fullmatch(row["revision"].strip()) is None:
            raise ValueError(
                f"revision must be a 40-character commit SHA: {row['repository']}",
            )
        row["origin"] = _final_origin(row["origin"])
    return rows


def _final_origin(value: str) -> str:
    origin = value.strip()
    if origin == "baseline":
        return origin
    if origin in {"new", "new_confirmed", "new_pending"}:
        return "new"
    raise ValueError(f"unknown selected repository origin: {origin}")


def _validate_baseline_fingerprints(
    configuration: Mapping[str, object],
    baseline_checklist_paths: Sequence[Path],
) -> None:
    fingerprints = configuration.get("baseline_checklist_fingerprints")
    if not isinstance(fingerprints, dict):
        raise ValueError("collection configuration has no baseline checklist fingerprints")
    expected = sorted(str(value) for value in fingerprints.values())
    actual = sorted(_sha256_file(path) for path in baseline_checklist_paths)
    if actual != expected:
        raise ValueError("baseline checklist fingerprints do not match collection configuration")


def _validate_repository_sets(
    configuration: Mapping[str, object],
    *,
    selected: Sequence[Mapping[str, str]],
    accepted: Sequence[tuple[Path, str, Mapping[str, str]]],
) -> None:
    target = configuration.get("target_total_repositories")
    if not isinstance(target, int) or len(selected) != target:
        raise ValueError(f"selected repository count does not match target: {len(selected)} != {target}")
    _validate_repository_language_counts(configuration, selected=selected)
    selected_by_origin = {
        origin: {row["repository"].strip().casefold() for row in selected if row["origin"] == origin}
        for origin in ("baseline", "new")
    }
    accepted_by_origin = {
        origin: {row["repository"].strip().casefold() for _, row_origin, row in accepted if row_origin == origin}
        for origin in ("baseline", "new")
    }
    configured_baseline = configuration.get("baseline_repositories")
    if not isinstance(configured_baseline, list):
        raise ValueError("collection configuration has no baseline repositories")
    configured_names = {str(name).casefold() for name in configured_baseline}
    if selected_by_origin["baseline"] != configured_names:
        raise ValueError("selected baseline repositories do not match collection configuration")
    for origin in ("baseline", "new"):
        if selected_by_origin[origin] != accepted_by_origin[origin]:
            raise ValueError(f"selected {origin} repositories do not match human-accepted repositories")


def _validate_repository_language_counts(
    configuration: Mapping[str, object],
    *,
    selected: Sequence[Mapping[str, str]],
) -> None:
    targets = configuration.get("target_repositories_by_language")
    languages = configuration.get("languages")
    if not isinstance(targets, dict) or not isinstance(languages, list):
        raise ValueError("collection configuration has invalid language targets")
    parsed_targets: dict[str, int] = {}
    for language, count in targets.items():
        if not isinstance(language, str) or not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError("collection configuration has invalid language targets")
        parsed_targets[language] = count
    if set(parsed_targets) != {str(language) for language in languages}:
        raise ValueError("collection configuration has invalid language targets")
    if sum(parsed_targets.values()) != configuration["target_total_repositories"]:
        raise ValueError("collection configuration has invalid language targets")
    expected = Counter(parsed_targets)
    actual = Counter(row["sampling_language"] for row in selected)
    if actual != expected:
        raise ValueError("selected repository language counts do not match target")


def _validated_license_allowlist(
    configuration: Mapping[str, object],
    *,
    path: Path,
) -> guideline_license.LicenseAllowlist:
    raw_allowlist = configuration.get("license_allowlist")
    if not isinstance(raw_allowlist, list) or any(not isinstance(name, str) for name in raw_allowlist):
        raise ValueError("collection configuration has no license allowlist")
    configured_names = tuple(name for name in raw_allowlist if isinstance(name, str))
    configured = guideline_license.LicenseAllowlist(frozenset(configured_names))
    if len(configured.license_names) != len(configured_names) or any(
        not configured.allows(name) for name in configured_names
    ):
        raise ValueError("collection configuration has an invalid license allowlist")
    raw_fingerprints = configuration.get("license_allowlist_fingerprints")
    if not isinstance(raw_fingerprints, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in raw_fingerprints.items()
    ):
        raise ValueError("collection configuration has no license allowlist fingerprint")
    fingerprints = tuple(value for value in raw_fingerprints.values() if isinstance(value, str))
    if len(fingerprints) != 1 or _sha256_file(path) != fingerprints[0]:
        raise ValueError("license allowlist fingerprint does not match collection configuration")
    loaded = guideline_license.load_license_allowlist(path)
    if loaded != configured:
        raise ValueError("license allowlist does not match collection configuration")
    return loaded


def _validate_selected_licenses(
    allowlist: guideline_license.LicenseAllowlist,
    *,
    selected: Sequence[Mapping[str, str]],
) -> None:
    for row in selected:
        if not allowlist.allows(row.get("license_name", "")):
            raise ValueError(
                f"selected repository license is not allowlisted: {row['repository']}",
            )


def _license_ineligible_reviewed_repositories(configuration: Mapping[str, object]) -> set[str]:
    raw_repositories = configuration.get("license_ineligible_reviewed_repositories")
    if not isinstance(raw_repositories, list) or any(
        not isinstance(repository_name, str) or not repository_name.strip() for repository_name in raw_repositories
    ):
        raise ValueError("collection configuration has invalid license-ineligible reviewed repositories")
    repositories = tuple(repository_name for repository_name in raw_repositories if isinstance(repository_name, str))
    return {repository_name.strip().casefold() for repository_name in repositories}


def _guideline_rows(
    accepted: Sequence[tuple[Path, str, Mapping[str, str]]],
    *,
    repositories_by_name: Mapping[str, Mapping[str, str]],
) -> tuple[dict[str, str], ...]:
    rows = []
    for source_path, origin, row in accepted:
        repository = row["repository"].strip()
        selected = repositories_by_name[repository.casefold()]
        revision = _github_revision(row["github_url"], repository=repository)
        if revision != selected["revision"]:
            raise ValueError(f"reviewed revision does not match selected repository: {repository}")
        rows.append(
            {
                "repository": selected["repository"],
                "revision": selected["revision"],
                "sampling_language": selected["sampling_language"],
                "origin": origin,
                "sample_order": selected["sample_order"],
                "file": row["file"],
                "github_url": row["github_url"],
                "review_origin": row.get("review_origin", ""),
                "llm_decision": row.get("llm_decision", ""),
                "human_decision": row["human_decision"],
                "duplicate_of": row.get("duplicate_of", ""),
                "codex_decision": row.get("codex_decision", ""),
                "codex_reason": row.get("codex_reason", ""),
                "note": row.get("note", ""),
                "source_checklist": str(source_path),
            },
        )
    _require_nonempty(rows, field="file")
    _require_unique(rows, field="file")
    _require_unique(rows, field="github_url")
    return tuple(sorted(rows, key=lambda row: (row["repository"].casefold(), row["file"].casefold())))


def _github_revision(url: str, *, repository: str) -> str:
    parsed = urlsplit(url)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.netloc.casefold() != "github.com"
        or len(parts) < 5
        or parts[2] != "blob"
        or "/".join(parts[:2]).casefold() != repository.casefold()
    ):
        raise ValueError(f"GitHub URL does not identify the reviewed repository: {url}")
    return parts[3]


def _require_unique(rows: Sequence[Mapping[str, str]], *, field: str) -> None:
    values = [row[field] for row in rows]
    if len(values) != len(set(values)):
        raise ValueError(f"accepted guideline {field} values must be unique")


def _require_nonempty(rows: Sequence[Mapping[str, str]], *, field: str) -> None:
    if any(not row[field].strip() for row in rows):
        raise ValueError(f"accepted guideline {field} must be non-empty")


def _repository_rows(
    selected: Sequence[Mapping[str, str]],
    *,
    guideline_rows: Sequence[Mapping[str, str]],
) -> tuple[dict[str, object], ...]:
    counts = Counter(row["repository"].casefold() for row in guideline_rows)
    return tuple(
        {
            **{field: row[field] for field in _SELECTED_FIELDS},
            "license_name": row.get("license_name", ""),
            "guideline_file_count": counts[row["repository"].casefold()],
        }
        for row in sorted(selected, key=lambda row: row["repository"].casefold())
    )


def _summary(
    configuration: Mapping[str, object],
    *,
    sources: Sequence[tuple[Path, str, Sequence[Mapping[str, str]]]],
    guideline_rows: Sequence[Mapping[str, str]],
    selected: Sequence[Mapping[str, str]],
    license_ineligible_repositories: set[str],
) -> dict[str, object]:
    repository_origins = Counter(row["origin"] for row in selected)
    file_origins = Counter(row["origin"] for row in guideline_rows)
    languages = Counter(row["sampling_language"] for row in selected)
    review_origins = Counter(row["review_origin"] for row in guideline_rows)
    reviewed_rows = [row for _, _, rows in sources for row in rows]
    license_ineligible_files = sum(
        _is_accepted(row) and row["repository"].strip().casefold() in license_ineligible_repositories
        for row in reviewed_rows
    )
    return {
        "schema_version": 1,
        "status": "passed",
        "repositories": {
            "total": len(selected),
            "baseline": repository_origins["baseline"],
            "new": repository_origins["new"],
            "by_language": dict(sorted(languages.items(), key=lambda item: item[0].casefold())),
        },
        "guideline_files": {
            "total": len(guideline_rows),
            "baseline": file_origins["baseline"],
            "new": file_origins["new"],
            "by_review_origin": dict(sorted(review_origins.items(), key=lambda item: item[0].casefold())),
        },
        "reviewed_files": {
            "total": len(reviewed_rows),
            "accepted": len(guideline_rows),
            "duplicates": sum(bool(row.get("duplicate_of", "").strip()) for row in reviewed_rows),
            "license_ineligible": license_ineligible_files,
            "not_found": sum(row["human_decision"].strip() == "not_found" for row in reviewed_rows),
        },
        "target_total_repositories": configuration["target_total_repositories"],
        "validation": {
            "baseline_fingerprints_match": True,
            "duplicate_references_valid": True,
            "file_ids_unique": True,
            "github_urls_unique": True,
            "human_decisions_complete": True,
            "licenses_allowlisted": True,
            "repository_count_matches_target": True,
            "repository_sets_match": True,
            "revisions_match": True,
        },
    }


def _provenance(
    configuration: Mapping[str, object],
    *,
    configuration_path: Path,
    selected_path: Path,
    baseline_checklist_paths: Sequence[Path],
    human_checklist_path: Path,
    license_allowlist_path: Path,
    artifacts: Mapping[str, str],
) -> dict[str, object]:
    sources = [
        _source_record(configuration_path, role="collection_configuration"),
        _source_record(selected_path, role="selected_repositories"),
        *(_source_record(path, role="baseline_checklist") for path in baseline_checklist_paths),
        _source_record(human_checklist_path, role="human_checklist"),
        _source_record(license_allowlist_path, role="license_allowlist"),
    ]
    return {
        "schema_version": 1,
        "artifacts": dict(sorted(artifacts.items())),
        "classification": {field: configuration.get(field) for field in _CLASSIFICATION_CONFIGURATION_FIELDS},
        "collection": {field: configuration.get(field) for field in _COLLECTION_CONFIGURATION_FIELDS},
        "sources": sources,
    }


def _source_record(path: Path, *, role: str) -> dict[str, str]:
    return {"role": role, "path": str(path), "sha256": _sha256_file(path)}


def _is_accepted(row: Mapping[str, str]) -> bool:
    return row["human_decision"].strip() == "pass" and not row.get("duplicate_of", "").strip()


def _read_json(path: Path) -> dict[str, object]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"JSON object expected: {path}")
    return document


def _csv_text(fieldnames: Sequence[str], rows: Sequence[Mapping[str, object]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _json_text(document: Mapping[str, object]) -> str:
    return f"{json.dumps(document, indent=2, ensure_ascii=True, sort_keys=True)}\n"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _write_bundle(output_dir: Path, artifacts: Mapping[str, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, value in artifacts.items():
        path = output_dir / filename
        temporary_path = path.with_suffix(f"{path.suffix}.tmp")
        temporary_path.write_text(value, encoding="utf-8")
        temporary_path.replace(path)
