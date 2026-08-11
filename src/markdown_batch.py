"""Prepare and evaluate per-file Markdown rule classification requests."""

import csv
import hashlib
import json
import random
from collections import defaultdict
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Protocol, cast

import github_client
import markdown_batch_lifecycle
import responses_provider

STRATUM_COUNT = 5
DEFAULT_MAX_OUTPUT_TOKENS = 16_000
CLASSIFICATION_PROMPT_VERSION = "code-test-rule-v15"
BATCH_INPUT_USD_PER_MILLION_TOKENS = 0.10
BATCH_CACHED_INPUT_USD_PER_MILLION_TOKENS = 0.01
BATCH_CACHE_WRITE_INPUT_USD_PER_MILLION_TOKENS = 0.125
BATCH_OUTPUT_USD_PER_MILLION_TOKENS = 0.60
PRICING_DATE = "2026-08-06"
_CLASSIFICATION_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "markdown_file_classification_v15.md"
_CLASSIFICATION_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "prompts" / "markdown_file_classification_schema.json"
)
_CANDIDATE_COLUMNS = frozenset(
    {
        "name",
        "lastCommitSHA",
        "markdown_path",
        "markdown_url",
        "matched_filename_terms",
    },
)


@dataclass(frozen=True, slots=True)
class ClassificationSettings:
    """Immutable settings for one Markdown classification experiment."""

    provider: responses_provider.ResponsesProvider
    region: str | None
    model: str
    reasoning_effort: str
    max_output_tokens: int
    workers: int


PROJECT_RULE_CLASSIFICATION_SETTINGS = ClassificationSettings(
    provider=responses_provider.ResponsesProvider.BEDROCK,
    region="us-east-1",
    model="gpt-5.6-luna",
    reasoning_effort="max",
    max_output_tokens=32_000,
    workers=16,
)


@cache
def _classification_instructions() -> str:
    return _CLASSIFICATION_PROMPT_PATH.read_text(encoding="utf-8")


@cache
def _classification_schema() -> Mapping[str, object]:
    schema = json.loads(_CLASSIFICATION_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        msg = f"{_CLASSIFICATION_SCHEMA_PATH} must contain a JSON object"
        raise ValueError(msg)
    return cast(dict[str, object], schema)


@dataclass(frozen=True, slots=True)
class MarkdownCandidate:
    """One revision-pinned Markdown file selected by its basename."""

    repository: str
    revision: str
    path: str
    url: str
    matched_terms: tuple[str, ...]

    @property
    def identity(self) -> tuple[str, str, str]:
        """Return the stable repository revision path identity."""
        return self.repository, self.revision, self.path


@dataclass(frozen=True, slots=True)
class SizedMarkdownCandidate:
    """A Markdown candidate with its Git blob size."""

    candidate: MarkdownCandidate
    size_bytes: int


@dataclass(frozen=True, slots=True)
class SampledMarkdownCandidate:
    """A deterministic cost-pilot sample item."""

    custom_id: str
    sized_candidate: SizedMarkdownCandidate
    stratum: int
    stratum_population: int


@dataclass(frozen=True, slots=True)
class MarkdownBatchPreparation:
    """Counts and paths produced by one cost-pilot preparation."""

    candidates: int
    sampled: int
    output_dir: Path


class MarkdownRepositoryClient(Protocol):
    """Retrieve revision-pinned GitHub trees and Markdown contents."""

    def get_complete_tree(self, repository: str, revision: str) -> github_client.RepositoryTree:
        """Return all blobs for one repository revision."""
        ...

    def get_text_file(self, repository: str, revision: str, path: str) -> str:
        """Return one Markdown file from a repository revision."""
        ...


def submit_cost_pilot(
    *,
    output_dir: Path,
    client: markdown_batch_lifecycle.BatchLifecycleClient,
) -> Mapping[str, object]:
    """Submit one prepared cost pilot without duplicating completed submissions."""
    return markdown_batch_lifecycle.submit_cost_pilot(output_dir=output_dir, client=client)


def retrieve_cost_pilot(
    *,
    output_dir: Path,
    client: markdown_batch_lifecycle.BatchStatusClient,
) -> Mapping[str, object]:
    """Retrieve and persist the state of one submitted cost pilot."""
    return markdown_batch_lifecycle.retrieve_cost_pilot(output_dir=output_dir, client=client)


def collect_cost_pilot(
    *,
    output_dir: Path,
    client: markdown_batch_lifecycle.BatchLifecycleClient,
) -> Mapping[str, object]:
    """Download and verify one submitted cost pilot."""
    return markdown_batch_lifecycle.collect_cost_pilot(
        output_dir=output_dir,
        client=client,
        input_usd_per_million_tokens=BATCH_INPUT_USD_PER_MILLION_TOKENS,
        cached_input_usd_per_million_tokens=BATCH_CACHED_INPUT_USD_PER_MILLION_TOKENS,
        cache_write_input_usd_per_million_tokens=BATCH_CACHE_WRITE_INPUT_USD_PER_MILLION_TOKENS,
        output_usd_per_million_tokens=BATCH_OUTPUT_USD_PER_MILLION_TOKENS,
    )


def collect_precomputed_cost_pilot(
    *,
    output_dir: Path,
    output_content: bytes,
    error_content: bytes,
    provider: str = "openrouter",
) -> Mapping[str, object]:
    """Verify Responses results produced without a Batch API."""
    pricing = responses_provider.pricing(provider)
    report = dict(
        markdown_batch_lifecycle.collect_precomputed_cost_pilot(
            output_dir=output_dir,
            output_content=output_content,
            error_content=error_content,
            input_usd_per_million_tokens=pricing.input_usd_per_million_tokens,
            cached_input_usd_per_million_tokens=pricing.cached_input_usd_per_million_tokens,
            cache_write_input_usd_per_million_tokens=pricing.cache_write_input_usd_per_million_tokens,
            output_usd_per_million_tokens=pricing.output_usd_per_million_tokens,
        ),
    )
    report.update(
        {
            "cost_source": (
                "provider_reported" if report["provider_reported_cost_usd"] is not None else pricing.source
            ),
            "pricing_date": pricing.date,
            "input_usd_per_million_tokens": pricing.input_usd_per_million_tokens,
            "cached_input_usd_per_million_tokens": pricing.cached_input_usd_per_million_tokens,
            "cache_write_input_usd_per_million_tokens": pricing.cache_write_input_usd_per_million_tokens,
            "output_usd_per_million_tokens": pricing.output_usd_per_million_tokens,
        },
    )
    return report


def load_candidates(path: Path) -> tuple[MarkdownCandidate, ...]:
    """Load unique Markdown filename candidates from an audit CSV."""
    candidates: list[MarkdownCandidate] = []
    identities: set[tuple[str, str, str]] = set()
    with path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        missing_columns = _CANDIDATE_COLUMNS.difference(reader.fieldnames or ())
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            msg = f"{path} is missing required columns: {missing}"
            raise ValueError(msg)
        for row in reader:
            candidate = MarkdownCandidate(
                repository=row["name"].strip(),
                revision=row["lastCommitSHA"].strip(),
                path=row["markdown_path"].strip(),
                url=row["markdown_url"].strip(),
                matched_terms=tuple(filter(None, row["matched_filename_terms"].split("|"))),
            )
            if candidate.identity in identities:
                msg = f"duplicate Markdown candidate: {candidate.identity!r}"
                raise ValueError(msg)
            identities.add(candidate.identity)
            candidates.append(candidate)
    return tuple(candidates)


def stratified_sample(
    candidates: tuple[SizedMarkdownCandidate, ...],
    *,
    sample_size: int,
    sample_seed: int,
) -> tuple[SampledMarkdownCandidate, ...]:
    """Select equal counts from five file-size strata reproducibly."""
    if sample_size < STRATUM_COUNT or sample_size % STRATUM_COUNT != 0:
        msg = f"sample_size must be a positive multiple of {STRATUM_COUNT}"
        raise ValueError(msg)
    if sample_size > len(candidates):
        msg = "sample_size exceeds the candidate population"
        raise ValueError(msg)
    ordered = sorted(
        candidates,
        key=lambda item: (
            item.size_bytes,
            item.candidate.repository,
            item.candidate.revision,
            item.candidate.path,
        ),
    )
    per_stratum = sample_size // STRATUM_COUNT
    # Reproducible experimental sampling does not need cryptographic randomness.
    random_source = random.Random(sample_seed)
    selected: list[tuple[int, int, SizedMarkdownCandidate]] = []
    for index in range(STRATUM_COUNT):
        start = len(ordered) * index // STRATUM_COUNT
        end = len(ordered) * (index + 1) // STRATUM_COUNT
        stratum = ordered[start:end]
        if len(stratum) < per_stratum:
            msg = f"size stratum {index + 1} has fewer than {per_stratum} candidates"
            raise ValueError(msg)
        sampled = random_source.sample(stratum, per_stratum)
        selected.extend((index + 1, len(stratum), item) for item in sampled)
    selected.sort(key=lambda item: (item[0], item[2].candidate.identity))
    return tuple(
        SampledMarkdownCandidate(
            custom_id=f"candidate-{index:04d}",
            sized_candidate=item,
            stratum=stratum,
            stratum_population=population,
        )
        for index, (stratum, population, item) in enumerate(selected, start=1)
    )


def all_candidates(
    candidates: tuple[SizedMarkdownCandidate, ...],
) -> tuple[SampledMarkdownCandidate, ...]:
    """Select every candidate in stable repository-revision-path order."""
    ordered = sorted(candidates, key=lambda item: item.candidate.identity)
    if not ordered:
        raise ValueError("candidate population must not be empty")
    return tuple(
        SampledMarkdownCandidate(
            custom_id=f"candidate-{index:04d}",
            sized_candidate=item,
            stratum=0,
            stratum_population=len(ordered),
        )
        for index, item in enumerate(ordered, start=1)
    )


def size_candidates(
    client: MarkdownRepositoryClient,
    candidates: tuple[MarkdownCandidate, ...],
) -> tuple[SizedMarkdownCandidate, ...]:
    """Attach Git blob sizes using one tree request per repository revision."""
    grouped: defaultdict[tuple[str, str], list[MarkdownCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[(candidate.repository, candidate.revision)].append(candidate)
    sized: list[SizedMarkdownCandidate] = []
    for identity, repository_candidates in grouped.items():
        tree = client.get_complete_tree(*identity)
        sizes = {entry.path: entry.size for entry in tree.entries}
        for candidate in repository_candidates:
            size_bytes = sizes.get(candidate.path)
            if size_bytes is None:
                msg = f"Markdown candidate is absent from the GitHub tree: {candidate.identity!r}"
                raise ValueError(msg)
            sized.append(SizedMarkdownCandidate(candidate=candidate, size_bytes=size_bytes))
    return tuple(sorted(sized, key=lambda item: item.candidate.identity))


def batch_request(
    sampled: SampledMarkdownCandidate,
    *,
    content: str,
    model: str,
    reasoning_effort: str,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> dict[str, object]:
    """Build one Responses API Batch request for one Markdown file."""
    candidate = sampled.sized_candidate.candidate
    input_text = json.dumps(
        {
            "repository": candidate.repository,
            "revision": candidate.revision,
            "path": candidate.path,
            "content": content,
        },
        ensure_ascii=False,
    )
    return {
        "custom_id": sampled.custom_id,
        "method": "POST",
        "url": "/v1/responses",
        "body": {
            "model": model,
            "instructions": _classification_instructions(),
            "input": input_text,
            "reasoning": {"effort": reasoning_effort},
            "max_output_tokens": max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "code_test_rule_label",
                    "strict": True,
                    "schema": _classification_schema(),
                },
            },
        },
    }


def prepare_cost_pilot(
    *,
    candidate_csv: Path,
    output_dir: Path,
    client: MarkdownRepositoryClient,
    sample_size: int | None,
    sample_seed: int,
    model: str,
    reasoning_effort: str,
    workers: int,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> MarkdownBatchPreparation:
    """Prepare a reproducible per-file Batch API cost pilot."""
    if workers < 1:
        msg = "workers must be at least 1"
        raise ValueError(msg)
    candidates = load_candidates(candidate_csv)
    sized_candidates = size_candidates(client, candidates)
    sampled = (
        all_candidates(sized_candidates)
        if sample_size is None
        else stratified_sample(
            sized_candidates,
            sample_size=sample_size,
            sample_seed=sample_seed,
        )
    )
    contents = _sample_contents(client, sampled, workers=workers)
    requests = tuple(
        batch_request(
            item,
            content=contents[item.custom_id],
            model=model,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
        )
        for item in sampled
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_manifest(output_dir / "sample_manifest.csv", sampled)
    _write_jsonl(output_dir / "batch_input.jsonl", requests)
    _write_json(
        output_dir / "run_configuration.json",
        {
            "schema_version": 1,
            "candidate_csv": str(candidate_csv),
            "candidate_csv_sha256": _sha256(candidate_csv.read_bytes()),
            "candidate_count": len(candidates),
            "selection_mode": "all_candidates" if sample_size is None else "size_stratified_sample",
            "sample_size": sample_size,
            "sample_seed": sample_seed,
            "stratum_count": STRATUM_COUNT,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "max_output_tokens": max_output_tokens,
            "workers": workers,
            "prompt_version": CLASSIFICATION_PROMPT_VERSION,
            "classification_contract_sha256": _classification_contract_sha256(),
            "pricing_date": PRICING_DATE,
            "batch_input_usd_per_million_tokens": BATCH_INPUT_USD_PER_MILLION_TOKENS,
            "batch_cached_input_usd_per_million_tokens": BATCH_CACHED_INPUT_USD_PER_MILLION_TOKENS,
            "batch_cache_write_input_usd_per_million_tokens": BATCH_CACHE_WRITE_INPUT_USD_PER_MILLION_TOKENS,
            "batch_output_usd_per_million_tokens": BATCH_OUTPUT_USD_PER_MILLION_TOKENS,
        },
    )
    return MarkdownBatchPreparation(
        candidates=len(candidates),
        sampled=len(sampled),
        output_dir=output_dir,
    )


def _sample_contents(
    client: MarkdownRepositoryClient,
    sampled: tuple[SampledMarkdownCandidate, ...],
    *,
    workers: int,
) -> dict[str, str]:
    contents: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                client.get_text_file,
                item.sized_candidate.candidate.repository,
                item.sized_candidate.candidate.revision,
                item.sized_candidate.candidate.path,
            ): item.custom_id
            for item in sampled
        }
        for future in as_completed(futures):
            contents[futures[future]] = future.result()
    return contents


def _write_manifest(path: Path, sampled: tuple[SampledMarkdownCandidate, ...]) -> None:
    fieldnames = (
        "custom_id",
        "stratum",
        "stratum_population",
        "name",
        "lastCommitSHA",
        "markdown_path",
        "markdown_url",
        "matched_filename_terms",
        "size_bytes",
    )
    rows = []
    for item in sampled:
        sized_candidate = item.sized_candidate
        candidate = sized_candidate.candidate
        rows.append(
            {
                "custom_id": item.custom_id,
                "stratum": item.stratum,
                "stratum_population": item.stratum_population,
                "name": candidate.repository,
                "lastCommitSHA": candidate.revision,
                "markdown_path": candidate.path,
                "markdown_url": candidate.url,
                "matched_filename_terms": "|".join(candidate.matched_terms),
                "size_bytes": sized_candidate.size_bytes,
            },
        )
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(path)


def _write_jsonl(path: Path, documents: tuple[Mapping[str, object], ...]) -> None:
    value = "".join(f"{json.dumps(document, ensure_ascii=True, sort_keys=True)}\n" for document in documents)
    _write_text(path, value)


def _write_json(path: Path, document: Mapping[str, object]) -> None:
    _write_text(path, f"{json.dumps(document, indent=2, ensure_ascii=True, sort_keys=True)}\n")


def _write_text(path: Path, value: str) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(value, encoding="utf-8")
    temporary_path.replace(path)


def _classification_contract_sha256() -> str:
    document = {
        "instructions": _classification_instructions(),
        "schema": _classification_schema(),
    }
    return _sha256(json.dumps(document, ensure_ascii=True, sort_keys=True).encode())


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
