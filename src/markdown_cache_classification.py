"""Stream revision-pinned Markdown blobs from bare Git caches into LLM classification."""

import csv
import hashlib
import json
import time
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, cast

import guideline
import markdown_batch
import markdown_batch_lifecycle
import markdown_cache_results
import markdown_classification
import markdown_responses_runner
import responses_provider

_CANDIDATE_COLUMNS = frozenset(
    {
        "name",
        "lastCommitSHA",
        "markdown_path",
        "blob_sha",
        "size_bytes",
        "markdown_url",
        "matched_filename_terms",
        "matched_content_terms",
    },
)


class LocalBlobClient(Protocol):
    """Read multiple blobs from one local bare repository."""

    def get_text_blobs(self, repository: str, blob_shas: tuple[str, ...]) -> dict[str, str]:
        """Return UTF-8-decoded blobs keyed by Git object ID."""
        ...


@dataclass(frozen=True, slots=True)
class CachedMarkdownCandidate:
    """One machine-filtered Markdown blob at an exact repository revision."""

    input_index: int
    repository: str
    revision: str
    path: str
    blob_sha: str
    size_bytes: int
    url: str
    matched_filename_terms: tuple[str, ...]
    matched_content_terms: tuple[str, ...]

    @property
    def custom_id(self) -> str:
        """Return a stable request ID derived from immutable Git identity."""
        value = json.dumps(
            [self.repository, self.revision, self.path, self.blob_sha],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return f"candidate-{hashlib.sha256(value.encode()).hexdigest()}"


@dataclass(frozen=True, slots=True)
class CandidateContent:
    """One cached candidate with its full Markdown content or retrieval error."""

    candidate: CachedMarkdownCandidate
    content: str = ""
    error: str = ""


def run_cache_classification(
    *,
    candidate_csv: Path,
    repository_summary_csv: Path | None = None,
    output_dir: Path,
    repository_client: LocalBlobClient,
    responses_client: markdown_responses_runner.ResponsesClient,
    provider: str,
    region: str | None,
    model: str,
    reasoning_effort: str,
    max_output_tokens: int,
    workers: int,
    blob_batch_size: int,
) -> dict[str, object]:
    """Classify cached Markdown candidates with bounded memory and durable progress."""
    if workers < 1:
        msg = "workers must be at least 1"
        raise ValueError(msg)
    if blob_batch_size < 1:
        msg = "blob_batch_size must be at least 1"
        raise ValueError(msg)
    candidates = load_cached_candidates(candidate_csv)
    configuration = {
        "schema_version": 1,
        "candidate_csv": str(candidate_csv),
        "candidate_csv_sha256": _sha256_file(candidate_csv),
        "repository_summary_csv": str(repository_summary_csv) if repository_summary_csv is not None else None,
        "repository_summary_csv_sha256": (
            _sha256_file(repository_summary_csv) if repository_summary_csv is not None else None
        ),
        "classification_contract_sha256": markdown_batch.classification_contract_sha256(),
        "provider": provider,
        "region": region,
        "model": model,
        "provider_model": responses_provider.model_id(provider, model),
        "reasoning_effort": reasoning_effort,
        "max_output_tokens": max_output_tokens,
        "workers": workers,
        "blob_batch_size": blob_batch_size,
    }
    store = markdown_cache_results.CacheClassificationStore(output_dir, configuration=configuration)
    store.initialize()
    completed_ids = store.completed_ids()
    pending = tuple(candidate for candidate in candidates if candidate.custom_id not in completed_ids)
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    try:
        attempted = _classify_pending(
            pending,
            repository_client=repository_client,
            responses_client=responses_client,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            workers=workers,
            blob_batch_size=blob_batch_size,
            store=store,
        )
    finally:
        report = store.write_reports()
        if repository_summary_csv is not None:
            markdown_cache_results.write_repository_reports(
                repository_summary_csv,
                store.records(),
                output_dir=output_dir,
            )
    elapsed_seconds = round(time.perf_counter() - started, 6)
    report.update(
        {
            "provider": provider,
            "region": region,
            "requested_model": model,
            "provider_model": responses_provider.model_id(provider, model),
            "reasoning_effort": reasoning_effort,
            "max_output_tokens": max_output_tokens,
            "workers": workers,
            "blob_batch_size": blob_batch_size,
            "requested": len(candidates),
            "attempted": attempted,
            "resumed": len(candidates) - len(pending),
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "elapsed_seconds": elapsed_seconds,
            "request_seconds": round(
                sum(float(str(record["elapsed_seconds"])) for record in store.records()),
                6,
            ),
        },
    )
    markdown_cache_results.write_run_report(output_dir, report)
    return report


def load_cached_candidates(path: Path) -> tuple[CachedMarkdownCandidate, ...]:
    """Load unique revision-pinned candidates with immutable blob identities."""
    candidates: list[CachedMarkdownCandidate] = []
    custom_ids: set[str] = set()
    with path.open(encoding="utf-8", newline="") as input_file:
        reader = csv.DictReader(input_file)
        missing = _CANDIDATE_COLUMNS.difference(reader.fieldnames or ())
        if missing:
            msg = f"{path} is missing required columns: {', '.join(sorted(missing))}"
            raise ValueError(msg)
        for input_index, row in enumerate(reader):
            candidate = CachedMarkdownCandidate(
                input_index=input_index,
                repository=row["name"].strip(),
                revision=row["lastCommitSHA"].strip(),
                path=row["markdown_path"].strip(),
                blob_sha=row["blob_sha"].strip(),
                size_bytes=int(row["size_bytes"]),
                url=row["markdown_url"].strip(),
                matched_filename_terms=tuple(filter(None, row["matched_filename_terms"].split("|"))),
                matched_content_terms=tuple(filter(None, row["matched_content_terms"].split("|"))),
            )
            if candidate.custom_id in custom_ids:
                identity = f"{candidate.repository}@{candidate.revision}:{candidate.path}"
                msg = f"duplicate cached Markdown candidate: {identity}"
                raise ValueError(msg)
            custom_ids.add(candidate.custom_id)
            candidates.append(candidate)
    return tuple(candidates)


def _classify_pending(
    candidates: Sequence[CachedMarkdownCandidate],
    *,
    repository_client: LocalBlobClient,
    responses_client: markdown_responses_runner.ResponsesClient,
    provider: str,
    model: str,
    reasoning_effort: str,
    max_output_tokens: int,
    workers: int,
    blob_batch_size: int,
    store: markdown_cache_results.CacheClassificationStore,
) -> int:
    contents = iter(_candidate_contents(candidates, repository_client, blob_batch_size=blob_batch_size))
    attempted = 0
    first_content = _next_content(contents, store)
    if first_content is not None:
        preflight = _classify_candidate(
            first_content,
            responses_client=responses_client,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
        )
        store.append(preflight)
        attempted += 1
        markdown_responses_runner.raise_for_fatal_preflight(_provider_execution(preflight))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures: dict[Future[dict[str, object]], None] = {}
        exhausted = _fill_futures(
            futures,
            contents,
            executor=executor,
            store=store,
            responses_client=responses_client,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            workers=workers,
        )
        while futures:
            future = next(as_completed(tuple(futures)))
            futures.pop(future)
            store.append(future.result())
            attempted += 1
            if not exhausted:
                exhausted = _fill_futures(
                    futures,
                    contents,
                    executor=executor,
                    store=store,
                    responses_client=responses_client,
                    provider=provider,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    max_output_tokens=max_output_tokens,
                    workers=workers,
                )
    return attempted


def _next_content(
    contents: Iterator[CandidateContent],
    store: markdown_cache_results.CacheClassificationStore,
) -> CandidateContent | None:
    for item in contents:
        if item.error:
            store.append(_retrieval_error(item.candidate, item.error))
            continue
        return item
    return None


def _fill_futures(
    futures: dict[Future[dict[str, object]], None],
    contents: Iterator[CandidateContent],
    *,
    executor: ThreadPoolExecutor,
    store: markdown_cache_results.CacheClassificationStore,
    responses_client: markdown_responses_runner.ResponsesClient,
    provider: str,
    model: str,
    reasoning_effort: str,
    max_output_tokens: int,
    workers: int,
) -> bool:
    while len(futures) < workers:
        try:
            item = next(contents)
        except StopIteration:
            return True
        if item.error:
            store.append(_retrieval_error(item.candidate, item.error))
            continue
        future = executor.submit(
            _classify_candidate,
            item,
            responses_client=responses_client,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
        )
        futures[future] = None
    return False


def _candidate_contents(
    candidates: Sequence[CachedMarkdownCandidate],
    client: LocalBlobClient,
    *,
    blob_batch_size: int,
) -> Iterator[CandidateContent]:
    grouped: defaultdict[str, list[CachedMarkdownCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate.repository].append(candidate)
    for repository, repository_candidates in grouped.items():
        for start in range(0, len(repository_candidates), blob_batch_size):
            chunk = repository_candidates[start : start + blob_batch_size]
            try:
                contents = client.get_text_blobs(repository, tuple(candidate.blob_sha for candidate in chunk))
            except Exception as error:
                reason = f"{type(error).__name__}: {error}"[:1000]
                yield from (CandidateContent(candidate=candidate, error=reason) for candidate in chunk)
                continue
            for candidate in chunk:
                content = contents.get(candidate.blob_sha)
                if content is None:
                    yield CandidateContent(
                        candidate=candidate,
                        error=f"CachedRepositoryTreeError: cached blob is absent: {candidate.blob_sha}",
                    )
                    continue
                yield CandidateContent(candidate=candidate, content=content)


def _classify_candidate(
    item: CandidateContent,
    *,
    responses_client: markdown_responses_runner.ResponsesClient,
    provider: str,
    model: str,
    reasoning_effort: str,
    max_output_tokens: int,
) -> dict[str, object]:
    candidate = item.candidate
    request = markdown_batch.classification_request(
        markdown_batch.MarkdownCandidate(
            repository=candidate.repository,
            revision=candidate.revision,
            path=candidate.path,
            url=candidate.url,
            matched_terms=candidate.matched_filename_terms,
        ),
        custom_id=candidate.custom_id,
        content=item.content,
        model=model,
        reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
    )
    execution = markdown_responses_runner.execute_request(responses_client, request)
    result = markdown_batch_lifecycle.classify_result(
        output=execution if execution.get("response") is not None else None,
        error=execution if execution.get("response") is None else None,
        content=item.content,
    )
    usage = cast(guideline.TokenUsage, result.pop("usage"))
    provider_cost = result.pop("provider_cost_usd")
    pricing = responses_provider.pricing(provider)
    calculated_cost = markdown_classification.request_cost(
        usage,
        input_usd_per_million_tokens=pricing.input_usd_per_million_tokens,
        cached_input_usd_per_million_tokens=pricing.cached_input_usd_per_million_tokens,
        cache_write_input_usd_per_million_tokens=pricing.cache_write_input_usd_per_million_tokens,
        output_usd_per_million_tokens=pricing.output_usd_per_million_tokens,
    )
    return {
        **_candidate_fields(candidate),
        **result,
        "input_tokens": usage.input_tokens,
        "uncached_input_tokens": usage.uncached_input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "cache_write_input_tokens": usage.cache_write_input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
        "cost_usd": calculated_cost if provider_cost is None else float(str(provider_cost)),
        "elapsed_seconds": execution["elapsed_seconds"],
    }


def _retrieval_error(candidate: CachedMarkdownCandidate, reason: str) -> dict[str, object]:
    return {
        **_candidate_fields(candidate),
        "status": "retrieval_error",
        "model_label": "",
        "model_reason": "",
        "quote": "",
        "confidence": 0,
        "reason": reason,
        "input_tokens": 0,
        "uncached_input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "elapsed_seconds": 0.0,
        "provider_result": "",
    }


def _candidate_fields(candidate: CachedMarkdownCandidate) -> dict[str, object]:
    return {
        "custom_id": candidate.custom_id,
        "input_index": candidate.input_index,
        "name": candidate.repository,
        "lastCommitSHA": candidate.revision,
        "markdown_path": candidate.path,
        "blob_sha": candidate.blob_sha,
        "size_bytes": candidate.size_bytes,
        "markdown_url": candidate.url,
        "matched_filename_terms": "|".join(candidate.matched_filename_terms),
        "matched_content_terms": "|".join(candidate.matched_content_terms),
    }


def _provider_execution(record: Mapping[str, object]) -> Mapping[str, object]:
    provider_result = str(record.get("provider_result", ""))
    if not provider_result:
        return {}
    document = cast(Mapping[str, object], json.loads(provider_result))
    return document


def _sha256_file(path: Path) -> str:
    with path.open("rb") as input_file:
        return hashlib.file_digest(input_file, "sha256").hexdigest()
