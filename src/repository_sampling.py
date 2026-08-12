"""Create reproducible language-stratified repository samples."""

import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import repository

DEFAULT_LANGUAGES = repository.SELECTION_LANGUAGES
_MANIFEST_FIELDS = (
    "sample_order",
    "sampling_language",
    "language_population",
    "language_sample_size",
    "inclusion_probability",
    "name",
    "lastCommitSHA",
    "source_file",
    "source_input_index",
)
_SCHEDULE_FIELDS = (
    "sample_order",
    "round_number",
    "sampling_language",
    "language_population",
    "name",
    "lastCommitSHA",
    "source_file",
    "source_input_index",
)


@dataclass(frozen=True, slots=True)
class SampledRepository:
    """One repository selected from a language stratum."""

    candidate: repository.RepositoryCandidate
    sample_order: int
    language: str
    language_population: int
    language_sample_size: int


@dataclass(frozen=True, slots=True)
class ScheduledRepository:
    """One repository in a deterministic stratified draw schedule."""

    candidate: repository.RepositoryCandidate
    sample_order: int
    round_number: int
    language: str
    language_population: int


@dataclass(frozen=True, slots=True)
class RepositorySamplingReport:
    """Population and sample counts for one persisted sampling run."""

    population: int
    excluded: int
    eligible: int
    sampled: int
    language_populations: Mapping[str, int]
    language_sample_sizes: Mapping[str, int]
    output_dir: Path


def stratified_schedule(
    candidates: Sequence[repository.RepositoryCandidate],
    *,
    sample_seed: int,
    excluded_repositories: set[str],
    languages: Sequence[str] = DEFAULT_LANGUAGES,
) -> tuple[ScheduledRepository, ...]:
    """Order complete rounds containing one random repository per language."""
    normalized_exclusions = {name.casefold() for name in excluded_repositories}
    _validate_unique_repositories(candidates)
    strata: defaultdict[str, list[repository.RepositoryCandidate]] = defaultdict(list)
    for candidate in candidates:
        language = candidate.fields.get("mainLanguage", "")
        if language in languages and candidate.repository.casefold() not in normalized_exclusions:
            strata[language].append(candidate)
    if any(not strata[language] for language in languages):
        missing = next(language for language in languages if not strata[language])
        raise ValueError(f"language stratum {missing} has no eligible repositories")
    random_source = random.Random(sample_seed)
    shuffled = {
        language: random_source.sample(
            sorted(
                strata[language],
                key=lambda candidate: (candidate.repository.casefold(), candidate.revision.casefold()),
            ),
            len(strata[language]),
        )
        for language in languages
    }
    rounds = min(len(population) for population in shuffled.values())
    scheduled: list[ScheduledRepository] = []
    for round_index in range(rounds):
        for language in languages:
            scheduled.append(
                ScheduledRepository(
                    candidate=shuffled[language][round_index],
                    sample_order=len(scheduled) + 1,
                    round_number=round_index + 1,
                    language=language,
                    language_population=len(strata[language]),
                ),
            )
    return tuple(scheduled)


def stratified_sample(
    candidates: Sequence[repository.RepositoryCandidate],
    *,
    sample_size: int,
    sample_seed: int,
    excluded_repositories: set[str],
    languages: Sequence[str] = DEFAULT_LANGUAGES,
) -> tuple[SampledRepository, ...]:
    """Sample repositories without replacement using near-equal language quotas."""
    if sample_size < len(languages):
        msg = "sample_size must be at least the number of language strata"
        raise ValueError(msg)
    normalized_exclusions = {name.casefold() for name in excluded_repositories}
    _validate_unique_repositories(candidates)
    strata: defaultdict[str, list[repository.RepositoryCandidate]] = defaultdict(list)
    for candidate in candidates:
        language = candidate.fields.get("mainLanguage", "")
        if language in languages and candidate.repository.casefold() not in normalized_exclusions:
            strata[language].append(candidate)
    random_source = random.Random(sample_seed)
    sample_sizes = _language_sample_sizes(languages, sample_size=sample_size, random_source=random_source)
    selected: list[SampledRepository] = []
    for language in languages:
        population = sorted(
            strata[language],
            key=lambda candidate: (candidate.repository.casefold(), candidate.revision.casefold()),
        )
        quota = sample_sizes[language]
        if len(population) < quota:
            msg = f"language stratum {language} has fewer than {quota} eligible repositories"
            raise ValueError(msg)
        for candidate in random_source.sample(population, quota):
            selected.append(
                SampledRepository(
                    candidate=candidate,
                    sample_order=len(selected) + 1,
                    language=language,
                    language_population=len(population),
                    language_sample_size=quota,
                ),
            )
    return tuple(selected)


def write_stratified_sample(
    *,
    input_dir: Path,
    output_dir: Path,
    sample_size: int,
    sample_seed: int,
    exclude_csvs: Sequence[Path] = (),
) -> RepositorySamplingReport:
    """Select and persist a reproducible held-out repository sample."""
    candidates = repository.load_repository_candidates(input_dir)
    excluded_repositories = load_excluded_repositories(exclude_csvs)
    normalized_exclusions = {name.casefold() for name in excluded_repositories}
    sampled = stratified_sample(
        candidates,
        sample_size=sample_size,
        sample_seed=sample_seed,
        excluded_repositories=excluded_repositories,
    )
    eligible = tuple(
        candidate
        for candidate in candidates
        if candidate.repository.casefold() not in normalized_exclusions
        and candidate.fields.get("mainLanguage", "") in DEFAULT_LANGUAGES
    )
    excluded_population_count = sum(
        candidate.repository.casefold() in normalized_exclusions
        and candidate.fields.get("mainLanguage", "") in DEFAULT_LANGUAGES
        for candidate in candidates
    )
    language_populations = Counter(candidate.fields.get("mainLanguage", "") for candidate in eligible)
    language_sample_sizes = Counter(item.language for item in sampled)
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_input_dir = output_dir / "input"
    sample_input_dir.mkdir(parents=True, exist_ok=True)
    _write_candidate_csv(sample_input_dir / "candidates.csv", sampled)
    _write_manifest(output_dir / "sampling_manifest.csv", sampled)
    _write_json(
        output_dir / "sampling_configuration.json",
        {
            "schema_version": 1,
            "sampling_unit": "repository",
            "sampling_method": "stratified_random_without_replacement",
            "sample_size": sample_size,
            "sample_seed": sample_seed,
            "languages": list(DEFAULT_LANGUAGES),
            "population": len(candidates),
            "requested_exclusion_count": len(excluded_repositories),
            "excluded_repository_count": excluded_population_count,
            "eligible_population": len(eligible),
            "language_populations": dict(language_populations),
            "language_sample_sizes": dict(language_sample_sizes),
            "input_sha256": _input_fingerprints(input_dir),
            "exclude_csv_sha256": {str(path): _sha256(path.read_bytes()) for path in exclude_csvs},
        },
    )
    return RepositorySamplingReport(
        population=len(candidates),
        excluded=excluded_population_count,
        eligible=len(eligible),
        sampled=len(sampled),
        language_populations=dict(language_populations),
        language_sample_sizes=dict(language_sample_sizes),
        output_dir=output_dir,
    )


def write_stratified_schedule(path: Path, scheduled: Sequence[ScheduledRepository]) -> None:
    """Persist the complete deterministic draw order for sequential collection."""
    rows = [
        {
            "sample_order": item.sample_order,
            "round_number": item.round_number,
            "sampling_language": item.language,
            "language_population": item.language_population,
            "name": item.candidate.repository,
            "lastCommitSHA": item.candidate.revision,
            "source_file": item.candidate.source_file,
            "source_input_index": item.candidate.input_index,
        }
        for item in scheduled
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(path, rows, fieldnames=_SCHEDULE_FIELDS)


def load_excluded_repositories(paths: Sequence[Path]) -> set[str]:
    """Load repository names from candidate or gold-label CSV files."""
    repositories: set[str] = set()
    for path in paths:
        with path.open(encoding="utf-8", newline="") as input_file:
            reader = csv.DictReader(input_file)
            repository_field = _repository_field(reader.fieldnames)
            repositories.update(
                row[repository_field].strip() for row in reader if row.get(repository_field, "").strip()
            )
    return repositories


def _language_sample_sizes(
    languages: Sequence[str],
    *,
    sample_size: int,
    random_source: random.Random,
) -> dict[str, int]:
    base_size, remainder = divmod(sample_size, len(languages))
    extra_languages = set(random_source.sample(list(languages), remainder))
    return {language: base_size + (language in extra_languages) for language in languages}


def _validate_unique_repositories(candidates: Sequence[repository.RepositoryCandidate]) -> None:
    repository_counts = Counter(candidate.repository.casefold() for candidate in candidates)
    duplicates = sorted(name for name, count in repository_counts.items() if count > 1)
    if duplicates:
        msg = f"duplicate repository sampling unit: {duplicates[0]}"
        raise ValueError(msg)


def _repository_field(fieldnames: Sequence[str] | None) -> str:
    fields = set(fieldnames or ())
    for candidate in ("name", "repository"):
        if candidate in fields:
            return candidate
    raise ValueError("exclusion CSV must contain a name or repository column")


def _write_candidate_csv(path: Path, sampled: Sequence[SampledRepository]) -> None:
    if not sampled:
        raise ValueError("repository sample must not be empty")
    fieldnames = tuple(sampled[0].candidate.fields)
    rows = [dict(item.candidate.fields) for item in sampled]
    _write_csv(path, rows, fieldnames=fieldnames)


def _write_manifest(path: Path, sampled: Sequence[SampledRepository]) -> None:
    rows = [
        {
            "sample_order": item.sample_order,
            "sampling_language": item.language,
            "language_population": item.language_population,
            "language_sample_size": item.language_sample_size,
            "inclusion_probability": item.language_sample_size / item.language_population,
            "name": item.candidate.repository,
            "lastCommitSHA": item.candidate.revision,
            "source_file": item.candidate.source_file,
            "source_input_index": item.candidate.input_index,
        }
        for item in sampled
    ]
    _write_csv(path, rows, fieldnames=_MANIFEST_FIELDS)


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, object]],
    *,
    fieldnames: Sequence[str],
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


def _input_fingerprints(input_dir: Path) -> dict[str, str]:
    return {path.name: _sha256(path.read_bytes()) for path in sorted(input_dir.glob("*.csv"))}


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
