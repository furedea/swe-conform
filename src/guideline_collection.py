"""Collect repositories selected by revision-pinned per-file classifications."""

import csv
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol, cast

import markdown_cache_classification
import markdown_cache_results
import markdown_candidate_extraction
import markdown_candidate_store
import markdown_filename_audit
import markdown_responses_runner
import repository
import repository_sampling

_ATTEMPTS_FILENAME = "repository_attempts.jsonl"
_CONFIGURATION_FILENAME = "collection_configuration.json"
SCREENING_FIELDS = (
    "sample_order",
    "round_number",
    "language",
    "language_population",
    "repository",
    "revision",
    "license_name",
    "source_file",
    "input_index",
    "fields",
    "status",
    "attempt_count",
    "candidate_file_count",
    "pass_count",
    "review_count",
    "not_found_count",
    "model_error_count",
    "retrieval_error_count",
    "input_too_large_count",
    "retryable",
    "error",
)


@dataclass(frozen=True, slots=True)
class RepositoryScreening:
    """Latest file-classification aggregate for one scheduled repository."""

    scheduled: repository_sampling.ScheduledRepository
    status: str
    attempt_count: int = 0
    candidate_file_count: int = 0
    pass_count: int = 0
    review_count: int = 0
    not_found_count: int = 0
    model_error_count: int = 0
    retrieval_error_count: int = 0
    input_too_large_count: int = 0
    retryable: bool = False
    error: str = ""


class RepositoryProcessor(Protocol):
    """Classify every mechanically selected file in one repository."""

    def process(self, scheduled: repository_sampling.ScheduledRepository) -> RepositoryScreening:
        """Return the repository aggregate after persisting its file decisions."""
        ...


@dataclass(frozen=True, slots=True)
class RepositoryCollectionReport:
    """Observable result of one resumable collection run."""

    baseline_repositories: int
    new_repository_target: int
    confirmed_new_repositories: int
    pending_new_repositories: int
    selected_new_repositories: int
    processed_repositories: int
    target_reached: bool
    human_target_reached: bool
    screening_limit_reached: bool = False


@dataclass(frozen=True, slots=True)
class ManualReviewState:
    """Repository outcomes derived from completed file-level human decisions."""

    confirmed_repositories: set[str]
    rejected_repositories: set[str]


@dataclass(frozen=True, slots=True)
class RepositoryFileProcessor:
    """Extract and classify one scheduled repository through a blob client."""

    output_dir: Path
    auditor: markdown_filename_audit.MarkdownFilenameAuditor
    repository_client: markdown_cache_classification.BlobClient
    snapshot_inspector: markdown_candidate_extraction.SnapshotInspector | None
    skip_incomplete_repositories: bool
    responses_client: markdown_responses_runner.ResponsesClient
    provider: str
    region: str | None
    model: str
    reasoning_effort: str
    max_output_tokens: int
    file_workers: int
    blob_batch_size: int
    max_input_bytes: int
    max_model_attempts: int
    max_retrieval_attempts: int
    candidate_configuration: Mapping[str, object]

    def process(self, scheduled: repository_sampling.ScheduledRepository) -> RepositoryScreening:
        """Persist file-level outcomes and return their repository aggregate."""
        repository_dir = self.output_dir / "repositories" / f"{scheduled.sample_order:05d}"
        candidate_dir = repository_dir / "candidates"
        candidate_store = markdown_candidate_store.MarkdownCandidateStore(
            candidate_dir,
            configuration={
                **self.candidate_configuration,
                "repository": scheduled.candidate.repository,
                "revision": scheduled.candidate.revision,
            },
        )
        candidate_store.initialize()
        markdown_candidate_extraction.run_candidate_extraction(
            (scheduled.candidate,),
            auditor=self.auditor,
            store=candidate_store,
            workers=1,
            snapshot_inspector=self.snapshot_inspector,
            skip_incomplete_repositories=self.skip_incomplete_repositories,
        )
        extraction = candidate_store.report().results[0]
        if extraction.status is not markdown_filename_audit.MarkdownFilenameAuditStatus.COMPLETED:
            return RepositoryScreening(
                scheduled,
                status="unresolved",
                retryable=extraction.status
                in {
                    markdown_filename_audit.MarkdownFilenameAuditStatus.RETRIEVAL_ERROR,
                    markdown_filename_audit.MarkdownFilenameAuditStatus.SCAN_ERROR,
                    markdown_filename_audit.MarkdownFilenameAuditStatus.SNAPSHOT_INCOMPLETE,
                },
                error=extraction.error,
            )
        classification_dir = repository_dir / "classification"
        markdown_cache_classification.run_cache_classification(
            candidate_csv=candidate_dir / "markdown_filename_files.csv",
            repository_summary_csv=candidate_dir / "repository_filename_summary.csv",
            output_dir=classification_dir,
            repository_client=self.repository_client,
            snapshot_inspector=self.snapshot_inspector,
            skip_incomplete_repositories=self.skip_incomplete_repositories,
            responses_client=self.responses_client,
            provider=self.provider,
            region=self.region,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            max_output_tokens=self.max_output_tokens,
            workers=self.file_workers,
            blob_batch_size=self.blob_batch_size,
            max_input_bytes=self.max_input_bytes,
            max_model_attempts=self.max_model_attempts,
            max_retrieval_attempts=self.max_retrieval_attempts,
        )
        row = _single_csv_row(classification_dir / "repository_classification_summary.csv")
        return _repository_screening(scheduled, row, classification_dir=classification_dir)


class RepositoryCollectionStore:
    """Persist repository attempts while exposing only their latest outcomes."""

    __slots__ = ("_configuration", "_output_dir", "_results")

    def __init__(self, output_dir: Path, *, configuration: dict[str, object]) -> None:
        self._output_dir = output_dir
        self._configuration = dict(configuration)
        self._results: dict[int, RepositoryScreening] = {}

    def initialize(self) -> None:
        """Create or resume a compatible repository collection."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._validate_or_write_configuration()
        self._results = self._load_results()

    def append(self, result: RepositoryScreening) -> None:
        """Append one repository attempt and retain it as the latest result."""
        previous = self._results.get(result.scheduled.sample_order)
        attempt_count = 1 if previous is None else previous.attempt_count + 1
        attempted = replace(result, attempt_count=attempt_count)
        path = self._output_dir / _ATTEMPTS_FILENAME
        with path.open("a", encoding="utf-8") as output_file:
            output_file.write(json.dumps(screening_record(attempted), ensure_ascii=True, sort_keys=True))
            output_file.write("\n")
            output_file.flush()
        self._results[result.scheduled.sample_order] = attempted

    def results(self) -> tuple[RepositoryScreening, ...]:
        """Return latest outcomes in fixed sampling order."""
        return tuple(self._results[order] for order in sorted(self._results))

    def selected(
        self,
        limit: int,
        *,
        excluded_repositories: set[str] | None = None,
    ) -> tuple[RepositoryScreening, ...]:
        """Return the first positive repositories in fixed sampling order."""
        if limit < 0:
            raise ValueError("limit must not be negative")
        excluded = {name.casefold() for name in excluded_repositories or ()}
        return tuple(
            result
            for result in self.results()
            if result.status == "pass"
            if result.scheduled.candidate.repository.casefold() not in excluded
        )[:limit]

    def completed_orders(self) -> set[int]:
        """Return sampling positions with at least one persisted outcome."""
        return set(self._results)

    def retryable(self, *, max_attempts: int) -> tuple[RepositoryScreening, ...]:
        """Return repository or file outcomes that remain within their retry budget."""
        return tuple(
            result for result in self.results() if self._is_retryable(result, max_repository_attempts=max_attempts)
        )

    def _is_retryable(
        self,
        result: RepositoryScreening,
        *,
        max_repository_attempts: int,
    ) -> bool:
        if self._has_retryable_file_errors(result):
            return True
        has_classification_error = bool(
            result.model_error_count or result.retrieval_error_count or result.input_too_large_count
        )
        return (
            result.status == "unresolved"
            and not has_classification_error
            and result.retryable
            and result.attempt_count < max_repository_attempts
        )

    def _has_retryable_file_errors(self, result: RepositoryScreening) -> bool:
        checkpoint_path = (
            self._output_dir
            / "repositories"
            / f"{result.scheduled.sample_order:05d}"
            / "classification"
            / "cache_classification_checkpoint.jsonl"
        )
        return markdown_cache_results.checkpoint_has_retryable_file_errors(checkpoint_path)

    def _validate_or_write_configuration(self) -> None:
        path = self._output_dir / _CONFIGURATION_FILENAME
        if not path.exists():
            _write_json(path, self._configuration)
            return
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != self._configuration:
            raise ValueError(f"Existing repository collection configuration does not match this run: {path}")

    def _load_results(self) -> dict[int, RepositoryScreening]:
        path = self._output_dir / _ATTEMPTS_FILENAME
        if not path.exists():
            return {}
        results: dict[int, RepositoryScreening] = {}
        lines = path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, start=1):
            try:
                result = _screening(cast(dict[str, object], json.loads(line)))
            except json.JSONDecodeError:
                if line_number == len(lines):
                    break
                raise
            results[result.scheduled.sample_order] = result
        return results


def load_baseline_repositories(paths: Sequence[Path]) -> set[str]:
    """Return repositories with at least one human-confirmed file."""
    repositories: set[str] = set()
    for path in paths:
        with path.open(encoding="utf-8", newline="") as input_file:
            reader = csv.DictReader(input_file)
            fields = set(reader.fieldnames or ())
            if not {"repository", "human_decision"}.issubset(fields):
                raise ValueError(f"baseline checklist must contain repository and human_decision columns: {path}")
            repositories.update(
                row["repository"].strip()
                for row in reader
                if row["human_decision"].strip() == "pass" and row["repository"].strip()
            )
    return repositories


def validate_baseline_exclusions(
    baseline_repositories: set[str],
    *,
    excluded_repositories: set[str],
) -> None:
    """Require every reused baseline repository to be excluded from new draws."""
    excluded = {name.casefold() for name in excluded_repositories}
    missing = sorted(name for name in baseline_repositories if name.casefold() not in excluded)
    if missing:
        raise ValueError(f"baseline repository is absent from prior-sample exclusions: {missing[0]}")


def load_manual_review_state(path: Path | None) -> ManualReviewState:
    """Aggregate file-level human decisions without treating blanks as negative."""
    if path is None:
        return ManualReviewState(set(), set())
    decisions: defaultdict[str, list[str]] = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        fields = set(reader.fieldnames or ())
        if not {"repository", "human_decision"}.issubset(fields):
            raise ValueError(f"manual checklist must contain repository and human_decision columns: {path}")
        for row in reader:
            repository_name = row["repository"].strip()
            decision = row["human_decision"].strip()
            if decision not in {"", "pass", "not_found"}:
                raise ValueError(f"invalid human_decision for {repository_name}: {decision}")
            if repository_name:
                decisions[repository_name].append(decision)
    confirmed = {name for name, values in decisions.items() if "pass" in values}
    rejected = {
        name
        for name, values in decisions.items()
        if name not in confirmed and values and all(value == "not_found" for value in values)
    }
    return ManualReviewState(confirmed, rejected)


def validate_manual_review_state(
    state: ManualReviewState,
    *,
    store: RepositoryCollectionStore,
) -> None:
    """Require every manually reviewed repository to be a persisted positive."""
    positive = {
        result.scheduled.candidate.repository.casefold() for result in store.results() if result.status == "pass"
    }
    reviewed = state.confirmed_repositories | state.rejected_repositories
    invalid = sorted(name for name in reviewed if name.casefold() not in positive)
    if invalid:
        raise ValueError(f"manual review repository has no persisted positive screening: {invalid[0]}")


def collect_repositories(
    schedule: Sequence[repository_sampling.ScheduledRepository],
    *,
    baseline_repository_count: int,
    target_total_repositories: int,
    confirmed_repositories: set[str] | None = None,
    rejected_repositories: set[str] | None = None,
    store: RepositoryCollectionStore,
    processor: RepositoryProcessor,
    workers: int,
    max_repository_attempts: int = 3,
    max_screened_repositories: int | None = None,
) -> RepositoryCollectionReport:
    """Process complete stratified rounds until enough repositories are positive."""
    new_target = target_total_repositories - baseline_repository_count
    confirmed = set(confirmed_repositories or ())
    rejected = set(rejected_repositories or ())
    _validate_collection_parameters(
        baseline_repository_count=baseline_repository_count,
        target_total_repositories=target_total_repositories,
        confirmed_repositories=confirmed,
        rejected_repositories=rejected,
        workers=workers,
        max_repository_attempts=max_repository_attempts,
        max_screened_repositories=max_screened_repositories,
    )
    remaining_target = new_target - len(confirmed)
    reviewed = confirmed | rejected
    rounds: defaultdict[int, list[repository_sampling.ScheduledRepository]] = defaultdict(list)
    for item in schedule:
        rounds[item.round_number].append(item)
    screening_limit_reached = False
    for round_number in sorted(rounds):
        if len(store.selected(remaining_target, excluded_repositories=reviewed)) >= remaining_target:
            break
        pending = tuple(item for item in rounds[round_number] if item.sample_order not in store.completed_orders())
        if max_screened_repositories is not None and len(store.results()) + len(pending) > max_screened_repositories:
            screening_limit_reached = True
            break
        _process_repositories(pending, processor=processor, store=store, workers=workers)
    while True:
        retryable = store.retryable(max_attempts=max_repository_attempts)
        if not retryable:
            break
        _process_repositories(
            tuple(result.scheduled for result in retryable),
            processor=processor,
            store=store,
            workers=workers,
        )
    pending = store.selected(remaining_target, excluded_repositories=reviewed)
    target_reached = len(confirmed) + len(pending) >= new_target
    return RepositoryCollectionReport(
        baseline_repositories=baseline_repository_count,
        new_repository_target=new_target,
        confirmed_new_repositories=len(confirmed),
        pending_new_repositories=len(pending),
        selected_new_repositories=len(confirmed) + len(pending),
        processed_repositories=len(store.results()),
        target_reached=target_reached,
        human_target_reached=len(confirmed) >= new_target,
        screening_limit_reached=screening_limit_reached and not target_reached,
    )


def _validate_collection_parameters(
    *,
    baseline_repository_count: int,
    target_total_repositories: int,
    confirmed_repositories: set[str],
    rejected_repositories: set[str],
    workers: int,
    max_repository_attempts: int,
    max_screened_repositories: int | None,
) -> None:
    if baseline_repository_count < 0:
        raise ValueError("baseline_repository_count must not be negative")
    if target_total_repositories < baseline_repository_count:
        raise ValueError("target_total_repositories must include every baseline repository")
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if max_repository_attempts < 1:
        raise ValueError("max_repository_attempts must be at least 1")
    if max_screened_repositories is not None and max_screened_repositories < 1:
        raise ValueError("max_screened_repositories must be at least 1")
    if {name.casefold() for name in confirmed_repositories}.intersection(
        name.casefold() for name in rejected_repositories
    ):
        raise ValueError("confirmed and rejected repositories must be disjoint")
    new_target = target_total_repositories - baseline_repository_count
    if len(confirmed_repositories) > new_target:
        raise ValueError("confirmed repositories exceed the new repository target")


def _process_repositories(
    scheduled: Sequence[repository_sampling.ScheduledRepository],
    *,
    processor: RepositoryProcessor,
    store: RepositoryCollectionStore,
    workers: int,
) -> None:
    if not scheduled:
        return
    with ThreadPoolExecutor(max_workers=min(workers, len(scheduled))) as executor:
        futures = {executor.submit(processor.process, item): item for item in scheduled}
        for future in as_completed(futures):
            store.append(future.result())


def _single_csv_row(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as input_file:
        rows = [dict(row) for row in csv.DictReader(input_file)]
    if len(rows) != 1:
        raise ValueError(f"repository report must contain exactly one row: {path}")
    return rows[0]


def _repository_screening(
    scheduled: repository_sampling.ScheduledRepository,
    row: Mapping[str, str],
    *,
    classification_dir: Path,
) -> RepositoryScreening:
    source_status = row["status"]
    status = source_status if source_status in {"pass", "not_found", "no_candidates"} else "unresolved"
    retryable = source_status == "snapshot_incomplete" or markdown_cache_results.checkpoint_has_retryable_file_errors(
        classification_dir / "cache_classification_checkpoint.jsonl",
    )
    return RepositoryScreening(
        scheduled,
        status=status,
        candidate_file_count=int(row["candidate_file_count"]),
        pass_count=int(row["pass_count"]),
        review_count=int(row["review_count"]),
        not_found_count=int(row["not_found_count"]),
        model_error_count=int(row["model_error_count"]),
        retrieval_error_count=int(row["retrieval_error_count"]),
        input_too_large_count=int(row["input_too_large_count"]),
        retryable=retryable,
        error=row["error"],
    )


def screening_record(result: RepositoryScreening) -> dict[str, object]:
    scheduled = result.scheduled
    candidate = scheduled.candidate
    return {
        "sample_order": scheduled.sample_order,
        "round_number": scheduled.round_number,
        "language": scheduled.language,
        "language_population": scheduled.language_population,
        "repository": candidate.repository,
        "revision": candidate.revision,
        "license_name": candidate.license_name,
        "source_file": candidate.source_file,
        "input_index": candidate.input_index,
        "fields": dict(candidate.fields),
        "status": result.status,
        "attempt_count": result.attempt_count,
        "candidate_file_count": result.candidate_file_count,
        "pass_count": result.pass_count,
        "review_count": result.review_count,
        "not_found_count": result.not_found_count,
        "model_error_count": result.model_error_count,
        "retrieval_error_count": result.retrieval_error_count,
        "input_too_large_count": result.input_too_large_count,
        "retryable": result.retryable,
        "error": result.error,
    }


def _screening(record: dict[str, object]) -> RepositoryScreening:
    fields = {str(key): str(value) for key, value in cast(dict[object, object], record["fields"]).items()}
    candidate = repository.RepositoryCandidate(
        repository=str(record["repository"]),
        revision=str(record["revision"]),
        license_name=str(record["license_name"]),
        source_file=str(record["source_file"]),
        input_index=int(str(record["input_index"])),
        fields=fields,
    )
    scheduled = repository_sampling.ScheduledRepository(
        candidate=candidate,
        sample_order=int(str(record["sample_order"])),
        round_number=int(str(record["round_number"])),
        language=str(record["language"]),
        language_population=int(str(record["language_population"])),
    )
    return RepositoryScreening(
        scheduled=scheduled,
        status=str(record["status"]),
        attempt_count=int(str(record["attempt_count"])),
        candidate_file_count=int(str(record["candidate_file_count"])),
        pass_count=int(str(record["pass_count"])),
        review_count=int(str(record["review_count"])),
        not_found_count=int(str(record["not_found_count"])),
        model_error_count=int(str(record["model_error_count"])),
        retrieval_error_count=int(str(record["retrieval_error_count"])),
        input_too_large_count=int(str(record["input_too_large_count"])),
        retryable=bool(record["retryable"]),
        error=str(record["error"]),
    )


def _write_json(path: Path, document: dict[str, object]) -> None:
    value = f"{json.dumps(document, indent=2, ensure_ascii=True, sort_keys=True)}\n"
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(value, encoding="utf-8")
    temporary_path.replace(path)
