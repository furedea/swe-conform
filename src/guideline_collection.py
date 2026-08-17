"""Collect repositories selected by revision-pinned per-file classifications."""

import csv
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol, cast

import guideline_review
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
_SAMPLING_MANIFEST_FIELDS = (
    "sample_order",
    "round_number",
    "sampling_language",
    "language_population",
    "name",
    "lastCommitSHA",
    "source_file",
    "source_input_index",
)
_PRIOR_COMPATIBILITY_FIELDS = (
    "repository_source",
    "sample_seed",
    "languages",
    "excluded_repositories",
    "input_fingerprints",
    "exclude_csv_fingerprints",
    "classification_contract_sha256",
    "filter",
    "provider",
    "region",
    "model",
    "reasoning_effort",
    "max_output_tokens",
    "max_input_bytes",
    "enforce_snapshot_window",
)
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
    carried_screened_repositories: int = 0
    screening_limit_reached: bool = False
    target_repositories_by_language: Mapping[str, int] = field(default_factory=dict)
    baseline_repositories_by_language: Mapping[str, int] = field(default_factory=dict)
    confirmed_new_repositories_by_language: Mapping[str, int] = field(default_factory=dict)
    pending_new_repositories_by_language: Mapping[str, int] = field(default_factory=dict)
    selected_repositories_by_language: Mapping[str, int] = field(default_factory=dict)
    remaining_repositories_by_language: Mapping[str, int] = field(default_factory=dict)


ManualReviewState = guideline_review.GuidelineReviewState


@dataclass(frozen=True, slots=True)
class EligibilityFilteredReviewState:
    """Reviewed repositories retained by an external eligibility policy."""

    reviewed_baseline_repositories: frozenset[str]
    baseline_repositories: frozenset[str]
    manual_review: ManualReviewState
    eligible_repositories: frozenset[str]
    ineligible_accepted_repositories: frozenset[str]


@dataclass(frozen=True, slots=True)
class PriorCollection:
    """Read-only screening evidence carried from an earlier collection."""

    output_dir: Path
    configuration: Mapping[str, object]
    screenings: tuple[RepositoryScreening, ...]


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
            candidate_csv=candidate_dir / "markdown_unique_files.csv",
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

    def selected_by_language(
        self,
        limits: Mapping[str, int],
        *,
        excluded_repositories: set[str] | None = None,
    ) -> tuple[RepositoryScreening, ...]:
        """Return the first positive repositories up to each language limit."""
        if any(limit < 0 for limit in limits.values()):
            raise ValueError("language limits must not be negative")
        excluded = {name.casefold() for name in excluded_repositories or ()}
        counts: Counter[str] = Counter()
        selected = []
        for result in self.results():
            language = result.scheduled.language
            if result.status != "pass" or result.scheduled.candidate.repository.casefold() in excluded:
                continue
            if counts[language] >= limits.get(language, 0):
                continue
            selected.append(result)
            counts[language] += 1
        return tuple(selected)

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


def load_prior_collection(output_dir: Path) -> PriorCollection:
    """Load an existing collection as read-only continuation evidence."""
    configuration_path = output_dir / _CONFIGURATION_FILENAME
    configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
    if not isinstance(configuration, dict):
        raise ValueError(f"collection configuration must be a JSON object: {configuration_path}")
    attempts_path = output_dir / _ATTEMPTS_FILENAME
    if not attempts_path.is_file():
        raise FileNotFoundError(f"prior collection attempts do not exist: {attempts_path}")
    store = RepositoryCollectionStore(output_dir, configuration=configuration)
    store.initialize()
    screenings = store.results()
    if not screenings:
        raise ValueError(f"prior collection has no screening outcomes: {output_dir}")
    return PriorCollection(output_dir, configuration, screenings)


def validate_prior_screenings(
    screenings: Sequence[RepositoryScreening],
    *,
    schedule: Sequence[repository_sampling.ScheduledRepository],
) -> None:
    """Require prior outcomes to identify the same seeded schedule positions."""
    current_by_order = {item.sample_order: item for item in schedule}
    for screening in screenings:
        prior = screening.scheduled
        current = current_by_order.get(prior.sample_order)
        if current is None or _schedule_identity(current) != _schedule_identity(prior):
            raise ValueError(
                f"prior screening does not match the current schedule: {prior.candidate.repository}",
            )


def validate_prior_schedule_manifest(
    path: Path,
    *,
    schedule: Sequence[repository_sampling.ScheduledRepository],
) -> None:
    """Require the complete prior manifest to match the current seeded schedule."""
    with path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        if tuple(reader.fieldnames or ()) != _SAMPLING_MANIFEST_FIELDS:
            raise ValueError(f"prior sampling manifest has invalid columns: {path}")
        manifest_identities = tuple(_manifest_schedule_identity(row, path=path) for row in reader)
    current_identities = tuple(_schedule_manifest_identity(item) for item in schedule)
    if manifest_identities != current_identities:
        raise ValueError(f"prior sampling manifest does not match the current schedule: {path}")


def validate_prior_collection_compatibility(
    prior: PriorCollection,
    *,
    current_configuration: Mapping[str, object],
) -> None:
    """Require carried outcomes to use the current sampling and classification inputs."""
    for configuration_field in _PRIOR_COMPATIBILITY_FIELDS:
        prior_value = _portable_configuration_value(
            configuration_field,
            prior.configuration.get(configuration_field),
        )
        current_value = _portable_configuration_value(
            configuration_field,
            current_configuration.get(configuration_field),
        )
        if prior_value != current_value:
            raise ValueError(
                f"prior collection {configuration_field} does not match the current collection",
            )


def _portable_configuration_value(field: str, value: object) -> object:
    if field not in {"input_fingerprints", "exclude_csv_fingerprints"}:
        return value
    if not isinstance(value, dict) or any(not isinstance(item, str) for item in value.values()):
        raise ValueError(f"collection {field} must map paths to SHA-256 values")
    return tuple(sorted(value.values()))


def _schedule_identity(item: repository_sampling.ScheduledRepository) -> tuple[object, ...]:
    candidate = item.candidate
    return (
        item.sample_order,
        item.round_number,
        item.language,
        item.language_population,
        candidate.repository.casefold(),
        candidate.revision.casefold(),
        candidate.license_name,
        candidate.source_file,
        candidate.input_index,
    )


def _schedule_manifest_identity(item: repository_sampling.ScheduledRepository) -> tuple[object, ...]:
    candidate = item.candidate
    return (
        item.sample_order,
        item.round_number,
        item.language,
        item.language_population,
        candidate.repository.casefold(),
        candidate.revision.casefold(),
        candidate.source_file,
        candidate.input_index,
    )


def _manifest_schedule_identity(row: Mapping[str, str], *, path: Path) -> tuple[object, ...]:
    try:
        return (
            int(row["sample_order"]),
            int(row["round_number"]),
            row["sampling_language"],
            int(row["language_population"]),
            row["name"].casefold(),
            row["lastCommitSHA"].casefold(),
            row["source_file"],
            int(row["source_input_index"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"prior sampling manifest has an invalid row: {path}") from error


def load_baseline_repositories(paths: Sequence[Path]) -> set[str]:
    """Return repositories with at least one human-confirmed file."""
    repositories: dict[str, str] = {}
    for path in paths:
        with path.open(encoding="utf-8", newline="") as input_file:
            reader = csv.DictReader(input_file)
            fields = set(reader.fieldnames or ())
            if not {"repository", "human_decision"}.issubset(fields):
                raise ValueError(f"baseline checklist must contain repository and human_decision columns: {path}")
            for row in reader:
                repository_name = row["repository"].strip()
                if (
                    row["human_decision"].strip() == "pass"
                    and repository_name
                    and not row.get("duplicate_of", "").strip()
                ):
                    repositories.setdefault(repository_name.casefold(), repository_name)
    return set(repositories.values())


def baseline_repository_counts_by_language(
    baseline_repositories: set[str],
    *,
    population: Sequence[repository.RepositoryCandidate],
) -> dict[str, int]:
    """Count baseline repositories by their candidate language stratum."""
    candidates_by_name: dict[str, repository.RepositoryCandidate] = {}
    for candidate in population:
        repository_key = candidate.repository.casefold()
        existing = candidates_by_name.get(repository_key)
        if existing is not None and existing.fields.get("mainLanguage", "") != candidate.fields.get(
            "mainLanguage",
            "",
        ):
            raise ValueError(f"candidate repository has conflicting languages: {candidate.repository}")
        candidates_by_name.setdefault(repository_key, candidate)
    counts = dict.fromkeys(repository_sampling.DEFAULT_LANGUAGES, 0)
    for repository_name in baseline_repositories:
        candidate = candidates_by_name.get(repository_name.casefold())
        if candidate is None:
            raise ValueError(f"baseline repository is absent from the candidate population: {repository_name}")
        language = candidate.fields.get("mainLanguage", "")
        if language not in counts:
            raise ValueError(f"baseline repository has an unsupported language: {repository_name}")
        counts[language] += 1
    return counts


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


def validate_schedule_covers_language_deficits(
    schedule: Sequence[repository_sampling.ScheduledRepository],
    *,
    baseline_repository_counts: Mapping[str, int],
    target_total_repositories: int,
) -> None:
    """Require an eligible candidate for every language whose quota is not full."""
    targets = target_repository_counts_by_language(target_total_repositories)
    scheduled_languages = {item.language for item in schedule}
    missing = [
        language
        for language in repository_sampling.DEFAULT_LANGUAGES
        if baseline_repository_counts[language] < targets[language] and language not in scheduled_languages
    ]
    if missing:
        raise ValueError(
            f"eligible repository schedule has no candidates for required language: {missing[0]}",
        )


def load_manual_review_state(path: Path | None) -> ManualReviewState:
    """Aggregate completed file-level human decisions."""
    state = guideline_review.load_completed_review_state(path)
    return ManualReviewState(
        confirmed_repositories=_normalized_repository_names(state.confirmed_repositories),
        rejected_repositories=_normalized_repository_names(state.rejected_repositories),
    )


def filter_review_state_by_repository_eligibility(
    *,
    baseline_repositories: set[str],
    manual_review: ManualReviewState,
    eligible_repositories: set[str],
    population: Sequence[repository.RepositoryCandidate],
) -> EligibilityFilteredReviewState:
    """Validate reviewed repository metadata before applying an eligibility policy."""
    available = {candidate.repository.casefold() for candidate in population}
    reviewed = {name.casefold() for name in baseline_repositories}
    reviewed.update(manual_review.confirmed_repositories)
    reviewed.update(manual_review.rejected_repositories)
    missing = sorted(reviewed.difference(available))
    if missing:
        raise ValueError(f"reviewed repository is absent from the candidate population: {missing[0]}")
    eligible = {name.casefold() for name in eligible_repositories}
    filtered_baseline = frozenset(name for name in baseline_repositories if name.casefold() in eligible)
    filtered_manual = ManualReviewState(
        confirmed_repositories=manual_review.confirmed_repositories.intersection(eligible),
        rejected_repositories=manual_review.rejected_repositories.intersection(eligible),
    )
    ineligible_accepted = {name.casefold() for name in baseline_repositories if name.casefold() not in eligible}
    ineligible_accepted.update(manual_review.confirmed_repositories.difference(eligible))
    return EligibilityFilteredReviewState(
        reviewed_baseline_repositories=frozenset(baseline_repositories),
        baseline_repositories=filtered_baseline,
        manual_review=filtered_manual,
        eligible_repositories=frozenset(eligible),
        ineligible_accepted_repositories=frozenset(ineligible_accepted),
    )


def validate_manual_review_state(
    state: ManualReviewState,
    *,
    store: RepositoryCollectionStore,
    prior_screenings: Sequence[RepositoryScreening] = (),
) -> None:
    """Require every human-reviewed repository to have positive screening evidence."""
    local = {result.scheduled.candidate.repository.casefold(): result.status for result in store.results()}
    prior = {result.scheduled.candidate.repository.casefold(): result.status for result in prior_screenings}
    reviewed = state.confirmed_repositories | state.rejected_repositories
    contradictory = sorted(name for name in reviewed if local.get(name.casefold()) not in {None, "pass"})
    if contradictory:
        raise ValueError(f"manual review repository contradicts its persisted screening: {contradictory[0]}")
    missing = sorted(
        name for name in reviewed if local.get(name.casefold()) != "pass" and prior.get(name.casefold()) != "pass"
    )
    if missing:
        raise ValueError(f"manual review repository has no positive screening evidence: {missing[0]}")
    reviewed_names = {name.casefold() for name in reviewed}
    unreviewed_prior_positives = sorted(
        result.scheduled.candidate.repository
        for result in prior_screenings
        if result.status == "pass"
        if result.scheduled.candidate.repository.casefold() not in reviewed_names
    )
    if unreviewed_prior_positives:
        raise ValueError(
            f"prior positive screening has no human review: {unreviewed_prior_positives[0]}",
        )


def collect_repositories(
    schedule: Sequence[repository_sampling.ScheduledRepository],
    *,
    baseline_repository_counts: Mapping[str, int],
    target_total_repositories: int,
    confirmed_repositories: set[str] | None = None,
    rejected_repositories: set[str] | None = None,
    prior_screenings: Sequence[RepositoryScreening] = (),
    store: RepositoryCollectionStore,
    processor: RepositoryProcessor,
    workers: int,
    max_repository_attempts: int = 3,
    max_screened_repositories: int | None = None,
) -> RepositoryCollectionReport:
    """Process each fixed language order until every language reaches its quota."""
    confirmed = _normalized_repository_names(confirmed_repositories or set())
    rejected = _normalized_repository_names(rejected_repositories or set())
    target_counts = target_repository_counts_by_language(target_total_repositories)
    new_targets = new_repository_targets_by_language(
        baseline_repository_counts,
        target_total_repositories=target_total_repositories,
    )
    confirmed_counts = _repository_counts_by_language(confirmed, schedule=schedule)
    _validate_collection_parameters(
        confirmed_repositories=confirmed,
        rejected_repositories=rejected,
        workers=workers,
        max_repository_attempts=max_repository_attempts,
        max_screened_repositories=max_screened_repositories,
    )
    remaining_targets = remaining_repository_targets_by_language(new_targets, confirmed_counts=confirmed_counts)
    reviewed = confirmed | rejected
    carried_terminal = {
        result.scheduled.candidate.repository.casefold()
        for result in prior_screenings
        if result.status in {"pass", "not_found", "no_candidates"}
    }
    already_screened = reviewed | carried_terminal
    rounds: defaultdict[int, list[repository_sampling.ScheduledRepository]] = defaultdict(list)
    for item in schedule:
        rounds[item.round_number].append(item)
    screening_limit_reached = False
    for round_number in sorted(rounds):
        pending = store.selected_by_language(remaining_targets, excluded_repositories=reviewed)
        pending_counts = Counter(result.scheduled.language for result in pending)
        active_languages = {
            language for language, limit in remaining_targets.items() if pending_counts[language] < limit
        }
        if not active_languages:
            break
        next_wave = tuple(
            item
            for item in rounds[round_number]
            if (
                item.language in active_languages
                and item.sample_order not in store.completed_orders()
                and item.candidate.repository.casefold() not in already_screened
            )
        )
        if max_screened_repositories is not None and len(store.results()) + len(next_wave) > max_screened_repositories:
            screening_limit_reached = True
            break
        _process_repositories(next_wave, processor=processor, store=store, workers=workers)
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
    pending = store.selected_by_language(remaining_targets, excluded_repositories=reviewed)
    pending_counts = Counter(result.scheduled.language for result in pending)
    target_reached = all(
        pending_counts[language] >= remaining_targets[language] for language in repository_sampling.DEFAULT_LANGUAGES
    )
    human_target_reached = all(
        confirmed_counts.get(language, 0) >= new_targets[language]
        for language in repository_sampling.DEFAULT_LANGUAGES
    )
    baseline_repository_count = sum(baseline_repository_counts.values())
    new_target = sum(new_targets.values())
    confirmed_counts_by_language = {
        language: confirmed_counts.get(language, 0) for language in repository_sampling.DEFAULT_LANGUAGES
    }
    pending_counts_by_language = {
        language: pending_counts.get(language, 0) for language in repository_sampling.DEFAULT_LANGUAGES
    }
    selected_counts = {
        language: (
            baseline_repository_counts[language]
            + confirmed_counts_by_language[language]
            + pending_counts_by_language[language]
        )
        for language in repository_sampling.DEFAULT_LANGUAGES
    }
    return RepositoryCollectionReport(
        baseline_repositories=baseline_repository_count,
        new_repository_target=new_target,
        confirmed_new_repositories=len(confirmed),
        pending_new_repositories=len(pending),
        selected_new_repositories=len(confirmed) + len(pending),
        processed_repositories=len(store.results()),
        target_reached=target_reached,
        human_target_reached=human_target_reached,
        carried_screened_repositories=len(carried_terminal),
        screening_limit_reached=screening_limit_reached and not target_reached,
        target_repositories_by_language=target_counts,
        baseline_repositories_by_language=dict(baseline_repository_counts),
        confirmed_new_repositories_by_language=confirmed_counts_by_language,
        pending_new_repositories_by_language=pending_counts_by_language,
        selected_repositories_by_language=selected_counts,
        remaining_repositories_by_language={
            language: target_counts[language] - selected_counts[language]
            for language in repository_sampling.DEFAULT_LANGUAGES
        },
    )


def _validate_collection_parameters(
    *,
    confirmed_repositories: set[str],
    rejected_repositories: set[str],
    workers: int,
    max_repository_attempts: int,
    max_screened_repositories: int | None,
) -> None:
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


def target_repository_count_per_language(target_total_repositories: int) -> int:
    """Return an equal per-language target or reject an indivisible total."""
    language_count = len(repository_sampling.DEFAULT_LANGUAGES)
    target_per_language, remainder = divmod(target_total_repositories, language_count)
    if not target_per_language or remainder:
        raise ValueError("target_total_repositories must divide evenly across configured languages")
    return target_per_language


def target_repository_counts_by_language(target_total_repositories: int) -> dict[str, int]:
    """Return the equal target for every configured language."""
    target_per_language = target_repository_count_per_language(target_total_repositories)
    return dict.fromkeys(repository_sampling.DEFAULT_LANGUAGES, target_per_language)


def new_repository_targets_by_language(
    baseline_repository_counts: Mapping[str, int],
    *,
    target_total_repositories: int,
) -> dict[str, int]:
    """Return new-repository targets after validating baseline counts."""
    validate_baseline_repository_counts_by_language(
        baseline_repository_counts,
        target_total_repositories=target_total_repositories,
    )
    target_counts = target_repository_counts_by_language(target_total_repositories)
    return {language: target - baseline_repository_counts[language] for language, target in target_counts.items()}


def validate_baseline_repository_counts_by_language(
    baseline_repository_counts: Mapping[str, int],
    *,
    target_total_repositories: int,
) -> None:
    """Reject baseline counts that cannot fit the equal language targets."""
    target_counts = target_repository_counts_by_language(target_total_repositories)
    if set(baseline_repository_counts) != set(target_counts):
        raise ValueError("baseline_repository_counts must contain every configured language")
    if any(count < 0 for count in baseline_repository_counts.values()):
        raise ValueError("baseline repository counts must not be negative")
    if any(baseline_repository_counts[language] > target for language, target in target_counts.items()):
        raise ValueError("baseline repository count exceeds the language target")


def remaining_repository_targets_by_language(
    new_repository_targets: Mapping[str, int],
    *,
    confirmed_counts: Mapping[str, int],
) -> dict[str, int]:
    """Return pending targets after validating human-confirmed counts."""
    if any(confirmed_counts.get(language, 0) > target for language, target in new_repository_targets.items()):
        raise ValueError("confirmed repositories exceed a language target")
    return {
        language: target - confirmed_counts.get(language, 0) for language, target in new_repository_targets.items()
    }


def _repository_counts_by_language(
    repositories: set[str],
    *,
    schedule: Sequence[repository_sampling.ScheduledRepository],
) -> Counter[str]:
    languages_by_repository = {item.candidate.repository.casefold(): item.language for item in schedule}
    counts: Counter[str] = Counter()
    for repository_name in repositories:
        language = languages_by_repository.get(repository_name.casefold())
        if language is None:
            raise ValueError(f"reviewed repository is absent from the sampling schedule: {repository_name}")
        counts[language] += 1
    return counts


def _normalized_repository_names(repositories: set[str]) -> set[str]:
    return {name.strip().casefold() for name in repositories if name.strip()}


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
