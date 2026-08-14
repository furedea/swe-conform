"""Materialize file-level and repository-level guideline collection reports."""

import csv
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import guideline_collection
import markdown_cache_classification
import markdown_cache_results
import markdown_review
import repository
import repository_sampling

_CLASSIFIED_FILES_FILENAME = "classified_files.csv"
_FILE_ATTEMPTS_FILENAME = "file_attempts.jsonl"
_SCREENED_REPOSITORIES_FILENAME = "screened_repositories.csv"
_SELECTED_REPOSITORIES_FILENAME = "selected_repositories.csv"
_SUMMARY_FILENAME = "collection_summary.json"
_UNRESOLVED_FILES_FILENAME = "unresolved_files.csv"


def write_collection_reports(
    *,
    output_dir: Path,
    population: Sequence[repository.RepositoryCandidate],
    store: guideline_collection.RepositoryCollectionStore,
    baseline_repositories: set[str],
    target_total_repositories: int,
    repository_client: markdown_cache_classification.BlobClient,
    confirmed_repositories: set[str] | None = None,
    rejected_repositories: set[str] | None = None,
    max_screened_repositories: int | None = None,
    screening_limit_reached: bool = False,
    source_metrics: Callable[[], Mapping[str, object]] | None = None,
) -> None:
    """Materialize file and repository views from durable checkpoints."""
    new_target = target_total_repositories - len(baseline_repositories)
    confirmed = set(confirmed_repositories or ())
    rejected = set(rejected_repositories or ())
    remaining_target = new_target - len(confirmed)
    pending = store.selected(remaining_target, excluded_repositories=confirmed | rejected)
    confirmed_names = {name.casefold() for name in confirmed}
    confirmed_results = tuple(
        result for result in store.results() if result.scheduled.candidate.repository.casefold() in confirmed_names
    )
    selected = (*confirmed_results, *pending)
    selected_names = {result.scheduled.candidate.repository.casefold() for result in selected}
    file_rows, file_attempts = _file_records(output_dir, store.results(), selected_names=selected_names)
    _write_csv(output_dir / _CLASSIFIED_FILES_FILENAME, file_rows, fallback_fields=_classified_fields())
    _write_jsonl(output_dir / _FILE_ATTEMPTS_FILENAME, file_attempts)
    unresolved = [
        row
        for row in file_rows
        if row["status"] in {"model_error", "retrieval_error", "input_too_large", "snapshot_incomplete"}
    ]
    _write_csv(output_dir / _UNRESOLVED_FILES_FILENAME, unresolved, fallback_fields=_classified_fields())
    _write_repository_reports(
        output_dir=output_dir,
        population=population,
        store=store,
        baseline_repositories=baseline_repositories,
        selected=selected,
        confirmed_repositories=confirmed,
    )
    selected_file_rows = tuple(row for row in file_rows if str(row["name"]).casefold() in selected_names)
    markdown_review.export_cached_review_files(
        classified_rows=selected_file_rows,
        repository_client=repository_client,
        output_dir=output_dir / "manual-review",
    )
    _write_summary(
        output_dir=output_dir,
        baseline_count=len(baseline_repositories),
        confirmed_count=len(confirmed),
        pending_count=len(pending),
        rejected_count=len(rejected),
        processed_count=len(store.results()),
        target_total=target_total_repositories,
        file_rows=file_rows,
        selected_file_rows=selected_file_rows,
        unresolved_count=len(unresolved),
        max_screened_repositories=max_screened_repositories,
        screening_limit_reached=screening_limit_reached,
        source_metrics=source_metrics() if source_metrics is not None else {},
    )


def _file_records(
    output_dir: Path,
    results: Sequence[guideline_collection.RepositoryScreening],
    *,
    selected_names: set[str],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    latest: list[dict[str, object]] = []
    attempts: list[dict[str, object]] = []
    for result in results:
        scheduled = result.scheduled
        classification_dir = output_dir / "repositories" / f"{scheduled.sample_order:05d}" / "classification"
        checkpoint_path = classification_dir / "cache_classification_checkpoint.jsonl"
        checkpoint_attempts = markdown_cache_results.read_checkpoint_attempts(checkpoint_path)
        if result.candidate_file_count and not checkpoint_attempts:
            raise FileNotFoundError(f"classification checkpoint is absent or empty: {checkpoint_path}")
        latest_records = markdown_cache_results.latest_checkpoint_records(checkpoint_attempts)
        if classification_dir.exists():
            markdown_cache_results.write_classified_file_report(
                classification_dir / "classified_files.csv",
                latest_records,
            )
        attempts.extend(
            _collection_file_record(record, scheduled=scheduled, selected_names=selected_names)
            for record in checkpoint_attempts
        )
        latest.extend(
            _collection_file_record(record, scheduled=scheduled, selected_names=selected_names)
            for record in latest_records
        )
    return sorted(latest, key=_file_record_order), sorted(attempts, key=_file_record_order)


def _collection_file_record(
    record: Mapping[str, object],
    *,
    scheduled: repository_sampling.ScheduledRepository,
    selected_names: set[str],
) -> dict[str, object]:
    return {
        "sample_order": scheduled.sample_order,
        "round_number": scheduled.round_number,
        "sampling_language": scheduled.language,
        "selected_repository": scheduled.candidate.repository.casefold() in selected_names,
        **markdown_cache_results.classification_report_record(record),
    }


def _file_record_order(row: dict[str, object]) -> tuple[int, str]:
    return int(str(row["sample_order"])), str(row["markdown_path"]).casefold()


def _write_repository_reports(
    *,
    output_dir: Path,
    population: Sequence[repository.RepositoryCandidate],
    store: guideline_collection.RepositoryCollectionStore,
    baseline_repositories: set[str],
    selected: Sequence[guideline_collection.RepositoryScreening],
    confirmed_repositories: set[str],
) -> None:
    screened_rows = [guideline_collection.screening_record(result) for result in store.results()]
    _write_csv(
        output_dir / _SCREENED_REPOSITORIES_FILENAME,
        screened_rows,
        fallback_fields=guideline_collection.SCREENING_FIELDS,
    )
    candidates_by_name = {candidate.repository.casefold(): candidate for candidate in population}
    selected_rows = [
        _baseline_repository_row(name, candidates_by_name=candidates_by_name) for name in sorted(baseline_repositories)
    ]
    confirmed_names = {name.casefold() for name in confirmed_repositories}
    selected_rows.extend(_selected_repository_row(result, confirmed_names=confirmed_names) for result in selected)
    _write_csv(
        output_dir / _SELECTED_REPOSITORIES_FILENAME,
        selected_rows,
        fallback_fields=("repository", "revision", "sampling_language", "origin", "sample_order"),
    )


def _selected_repository_row(
    result: guideline_collection.RepositoryScreening,
    *,
    confirmed_names: set[str],
) -> dict[str, object]:
    candidate = result.scheduled.candidate
    return {
        "repository": candidate.repository,
        "revision": candidate.revision,
        "sampling_language": result.scheduled.language,
        "origin": "new_confirmed" if candidate.repository.casefold() in confirmed_names else "new_pending",
        "sample_order": result.scheduled.sample_order,
    }


def _baseline_repository_row(
    repository_name: str,
    *,
    candidates_by_name: Mapping[str, repository.RepositoryCandidate],
) -> dict[str, object]:
    candidate = candidates_by_name.get(repository_name.casefold())
    return {
        "repository": repository_name,
        "revision": candidate.revision if candidate is not None else "",
        "sampling_language": candidate.fields.get("mainLanguage", "") if candidate is not None else "",
        "origin": "baseline",
        "sample_order": "",
    }


def _write_summary(
    *,
    output_dir: Path,
    baseline_count: int,
    confirmed_count: int,
    pending_count: int,
    rejected_count: int,
    processed_count: int,
    target_total: int,
    file_rows: Sequence[Mapping[str, object]],
    selected_file_rows: Sequence[Mapping[str, object]],
    unresolved_count: int,
    max_screened_repositories: int | None,
    screening_limit_reached: bool,
    source_metrics: Mapping[str, object],
) -> None:
    new_target = target_total - baseline_count
    _write_json(
        output_dir / _SUMMARY_FILENAME,
        {
            "baseline_repositories": baseline_count,
            "new_repository_target": new_target,
            "confirmed_new_repositories": confirmed_count,
            "pending_new_repositories": pending_count,
            "rejected_new_repositories": rejected_count,
            "selected_new_repositories": confirmed_count + pending_count,
            "selected_total_repositories": baseline_count + confirmed_count + pending_count,
            "processed_repositories": processed_count,
            "max_screened_repositories": max_screened_repositories,
            "screening_limit_reached": screening_limit_reached,
            "target_total_repositories": target_total,
            "target_reached": confirmed_count + pending_count >= new_target,
            "human_target_reached": confirmed_count >= new_target,
            "classified_files": len(file_rows),
            "manual_review_files": sum(row["status"] in {"pass", "review"} for row in selected_file_rows),
            "unresolved_files": unresolved_count,
            **source_metrics,
        },
    )


def _classified_fields() -> tuple[str, ...]:
    return (
        "sample_order",
        "round_number",
        "sampling_language",
        "selected_repository",
        *markdown_cache_results.classified_fields(),
    )


def _write_json(path: Path, document: Mapping[str, object]) -> None:
    value = f"{json.dumps(document, indent=2, ensure_ascii=True, sort_keys=True)}\n"
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(value, encoding="utf-8")
    temporary_path.replace(path)


def _write_jsonl(path: Path, records: Sequence[Mapping[str, object]]) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            output_file.write("\n")
    temporary_path.replace(path)


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, object]],
    *,
    fallback_fields: Sequence[str],
) -> None:
    fieldnames = tuple(rows[0]) if rows else tuple(fallback_fields)
    if any(tuple(row) != fieldnames for row in rows):
        raise ValueError(f"CSV rows have inconsistent columns: {path}")
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(path)
