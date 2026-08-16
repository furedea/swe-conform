"""Build a full Codex-reviewed checklist from Markdown candidates."""

import csv
import hashlib
import json
import logging
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, cast

import openai_responses_client

DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_REASONING_EFFORT = "max"
DEFAULT_WORKERS = 4
DEFAULT_TIMEOUT_SECONDS = 1_800.0
MAX_BATCH_CHARACTERS = 80_000
MAX_BATCH_DOCUMENTS = 12

OUTPUT_FIELDS = (
    "repository",
    "file",
    "github_url",
    "review_origin",
    "llm_decision",
    "human_decision",
    "duplicate_of",
    "codex_decision",
    "codex_reason",
    "note",
)
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "custom_id": {"type": "string"},
                    "decision": {
                        "type": "string",
                        "enum": ["pass", "not_found"],
                    },
                    "quote": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": [
                    "custom_id",
                    "decision",
                    "quote",
                    "reason",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["reviews"],
    "additionalProperties": False,
}
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CandidateDocument:
    """One Markdown candidate and its exact revision-pinned content."""

    custom_id: str
    repository: str
    path: str
    url: str
    content: str
    v7_decision: str


@dataclass(frozen=True, slots=True)
class CodexReview:
    """One independently verified Codex decision."""

    custom_id: str
    decision: str
    quote: str
    reason: str


@dataclass(frozen=True, slots=True)
class FullChecklistReport:
    """Progress and output details for one full-checklist build."""

    existing: int
    codex_added: int
    reviewed: int
    remaining: int
    rows: int
    output_path: Path
    output_written: bool


class CodexReviewClient(Protocol):
    """Return one schema-constrained Codex response."""

    def complete_json(
        self,
        *,
        instructions: str,
        input_text: str,
        model: str,
        reasoning_effort: str,
        max_output_tokens: int,
        schema_name: str,
        schema: Mapping[str, object],
        working_directory: Path,
    ) -> openai_responses_client.JsonResponse:
        """Run one structured Codex request."""
        ...


def build_full_checklist(
    *,
    candidate_csv: Path,
    classified_files_path: Path,
    batch_input_path: Path,
    existing_checklist_path: Path,
    checkpoint_path: Path,
    output_path: Path,
    prompt_path: Path,
    client: CodexReviewClient,
    model: str,
    reasoning_effort: str,
    workers: int,
    max_batches: int | None = None,
) -> FullChecklistReport:
    """Review candidates absent from the original checklist and merge all rows."""
    if workers < 1:
        msg = "workers must be at least 1"
        raise ValueError(msg)
    if max_batches is not None and max_batches < 1:
        msg = "max_batches must be at least 1"
        raise ValueError(msg)
    existing_rows = _csv_rows(existing_checklist_path)
    documents = _candidate_documents(
        candidate_csv=candidate_csv,
        classified_files_path=classified_files_path,
        batch_input_path=batch_input_path,
    )
    documents_by_id = {document.custom_id: document for document in documents}
    reviews = _checkpoint_reviews(checkpoint_path, documents_by_id=documents_by_id)
    existing_urls = {row["github_url"] for row in existing_rows}
    _validate_existing_rows(existing_rows, documents=documents)
    pending = tuple(
        document for document in documents if document.url not in existing_urls and document.custom_id not in reviews
    )
    batches = _document_batches(pending)
    if max_batches is not None:
        batches = batches[:max_batches]
    if batches:
        new_reviews = _run_batches(
            batches,
            checkpoint_path=checkpoint_path,
            prompt_path=prompt_path,
            client=client,
            model=model,
            reasoning_effort=reasoning_effort,
            workers=workers,
        )
        reviews.update({review.custom_id: review for review in new_reviews})
    remaining = tuple(
        document for document in documents if document.url not in existing_urls and document.custom_id not in reviews
    )
    if remaining:
        return FullChecklistReport(
            existing=len(existing_rows),
            codex_added=len(documents) - len(existing_rows),
            reviewed=len(reviews),
            remaining=len(remaining),
            rows=0,
            output_path=output_path,
            output_written=False,
        )
    rows = _full_checklist_rows(
        documents=documents,
        existing_rows=existing_rows,
        reviews=reviews,
    )
    _write_csv(output_path, rows)
    return FullChecklistReport(
        existing=len(existing_rows),
        codex_added=len(documents) - len(existing_rows),
        reviewed=len(reviews),
        remaining=0,
        rows=len(rows),
        output_path=output_path,
        output_written=True,
    )


def _candidate_documents(
    *,
    candidate_csv: Path,
    classified_files_path: Path,
    batch_input_path: Path,
) -> tuple[CandidateDocument, ...]:
    candidate_rows = _csv_rows(candidate_csv)
    candidates = _unique_rows(candidate_rows, key="markdown_url", source=candidate_csv)
    classified_rows = _csv_rows(classified_files_path)
    classified = _unique_rows(classified_rows, key="markdown_url", source=classified_files_path)
    inputs = _batch_inputs(batch_input_path)
    if not candidates or len(candidates) != len(classified) or len(classified) != len(inputs):
        msg = "Candidate, v7 classification, and prepared-input counts must be equal and nonzero."
        raise ValueError(msg)
    documents = []
    for row in classified.values():
        url = row["markdown_url"]
        candidate = candidates.get(url)
        if candidate is None:
            msg = f"v7 decision has no filename-filter candidate: {url}"
            raise ValueError(msg)
        custom_id = row["custom_id"]
        input_document = inputs.get(custom_id)
        if input_document is None:
            msg = f"v7 decision has no prepared input: {custom_id}"
            raise ValueError(msg)
        _validate_document_identity(row, candidate=candidate, input_document=input_document)
        documents.append(
            CandidateDocument(
                custom_id=custom_id,
                repository=row["name"],
                path=row["markdown_path"],
                url=url,
                content=str(input_document["content"]),
                v7_decision=row["status"],
            ),
        )
    return tuple(sorted(documents, key=lambda item: (item.repository.casefold(), item.path.casefold())))


def _batch_inputs(path: Path) -> dict[str, Mapping[str, object]]:
    inputs: dict[str, Mapping[str, object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        request = cast(Mapping[str, object], json.loads(line))
        body = cast(Mapping[str, object], request["body"])
        input_document = cast(Mapping[str, object], json.loads(str(body["input"])))
        custom_id = str(request["custom_id"])
        if custom_id in inputs:
            msg = f"Duplicate prepared custom_id in {path}: {custom_id}"
            raise ValueError(msg)
        inputs[custom_id] = input_document
    return inputs


def _unique_rows(
    rows: Sequence[Mapping[str, str]],
    *,
    key: str,
    source: Path,
) -> dict[str, Mapping[str, str]]:
    unique_rows: dict[str, Mapping[str, str]] = {}
    for row in rows:
        value = row.get(key, "")
        if not value:
            msg = f"Missing {key} in {source}."
            raise ValueError(msg)
        if value in unique_rows:
            msg = f"Duplicate {key} in {source}: {value}"
            raise ValueError(msg)
        unique_rows[value] = row
    return unique_rows


def _validate_document_identity(
    classified: Mapping[str, str],
    *,
    candidate: Mapping[str, str],
    input_document: Mapping[str, object],
) -> None:
    expected = {
        "repository": classified["name"],
        "path": classified["markdown_path"],
    }
    actual = {
        "repository": str(input_document.get("repository", "")),
        "path": str(input_document.get("path", "")),
    }
    candidate_identity = {
        "repository": candidate["name"],
        "path": candidate["markdown_path"],
    }
    if actual != expected or candidate_identity != expected:
        msg = f"Candidate identity differs across inputs: expected={expected}"
        raise ValueError(msg)
    if classified["status"] not in {"pass", "not_found", "review"}:
        msg = f"Invalid v7 decision for {classified['custom_id']}: {classified['status']}"
        raise ValueError(msg)


def _validate_existing_rows(
    rows: Sequence[Mapping[str, str]],
    *,
    documents: Sequence[CandidateDocument],
) -> None:
    by_url = _unique_rows(rows, key="github_url", source=Path("existing checklist"))
    document_urls = {document.url for document in documents}
    missing_urls = set(by_url) - document_urls
    if missing_urls:
        msg = f"Existing checklist contains URLs absent from candidates: {sorted(missing_urls)}"
        raise ValueError(msg)
    for row in rows:
        for field in ("human_decision", "codex_decision"):
            if row.get(field) not in {"pass", "not_found"}:
                msg = f"Invalid {field} for {row['github_url']}: {row.get(field, '')}"
                raise ValueError(msg)


def _document_batches(documents: Sequence[CandidateDocument]) -> tuple[tuple[CandidateDocument, ...], ...]:
    batches: list[tuple[CandidateDocument, ...]] = []
    current: list[CandidateDocument] = []
    current_characters = 0
    current_repository = ""
    for document in documents:
        document_characters = len(document.content)
        must_split = current and (
            document.repository != current_repository
            or len(current) >= MAX_BATCH_DOCUMENTS
            or current_characters + document_characters > MAX_BATCH_CHARACTERS
        )
        if must_split:
            batches.append(tuple(current))
            current = []
            current_characters = 0
        current.append(document)
        current_characters += document_characters
        current_repository = document.repository
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def _run_batches(
    batches: Sequence[Sequence[CandidateDocument]],
    *,
    checkpoint_path: Path,
    prompt_path: Path,
    client: CodexReviewClient,
    model: str,
    reasoning_effort: str,
    workers: int,
) -> tuple[CodexReview, ...]:
    reviews: list[CodexReview] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _review_batch,
                client,
                batch,
                prompt_path=prompt_path,
                model=model,
                reasoning_effort=reasoning_effort,
            ): batch
            for batch in batches
        }
        for future in as_completed(futures):
            batch = futures[future]
            try:
                batch_reviews = future.result()
            except Exception:
                LOGGER.exception(
                    "Codex full-review batch failed",
                    extra={"repository": batch[0].repository, "documents": len(batch)},
                )
                continue
            _append_checkpoint(checkpoint_path, batch_reviews)
            reviews.extend(batch_reviews)
            LOGGER.info(
                {
                    "action": "codex_full_review",
                    "repository": batch[0].repository,
                    "reviewed": len(batch_reviews),
                    "status": "complete",
                },
            )
    return tuple(reviews)


def _review_batch(
    client: CodexReviewClient,
    documents: Sequence[CandidateDocument],
    *,
    prompt_path: Path,
    model: str,
    reasoning_effort: str,
) -> tuple[CodexReview, ...]:
    with tempfile.TemporaryDirectory(prefix="swe-conform-full-review-") as working_directory:
        response = client.complete_json(
            instructions=_review_instructions(prompt_path, document_count=len(documents)),
            input_text=_review_input(documents),
            model=model,
            reasoning_effort=reasoning_effort,
            max_output_tokens=16_000,
            schema_name="full_markdown_review",
            schema=REVIEW_SCHEMA,
            working_directory=Path(working_directory),
        )
    reviews = _response_reviews(response.value)
    expected_ids = {document.custom_id for document in documents}
    actual_ids = {review.custom_id for review in reviews}
    if actual_ids != expected_ids or len(reviews) != len(documents):
        msg = f"Codex review IDs differ: expected={sorted(expected_ids)} actual={sorted(actual_ids)}"
        raise ValueError(msg)
    documents_by_id = {document.custom_id: document for document in documents}
    for review in reviews:
        _validate_review(review, documents_by_id[review.custom_id])
    return reviews


def _review_instructions(prompt_path: Path, *, document_count: int) -> str:
    classification_contract = prompt_path.read_text(encoding="utf-8")
    return f"""Apply the classification contract below independently to each of the {document_count} input documents.

Return exactly one review for every custom_id and preserve each custom_id exactly.
Map YES to pass and NO to not_found.
Write reason in concise Japanese.
For pass, copy the shortest exact contiguous quote that verifies the decision.
For not_found, return an empty quote.
Do not use repository metadata or any other file.

--- Classification contract ---

{classification_contract}
"""


def _review_input(documents: Sequence[CandidateDocument]) -> str:
    sections = []
    for document in documents:
        payload = {
            "custom_id": document.custom_id,
            "repository": document.repository,
            "path": document.path,
            "content": document.content,
        }
        sections.append(json.dumps(payload, ensure_ascii=False))
    return "\n\n--- NEXT DOCUMENT ---\n\n".join(sections)


def _response_reviews(value: Mapping[str, object]) -> tuple[CodexReview, ...]:
    raw_reviews = value.get("reviews")
    if not isinstance(raw_reviews, list):
        msg = "Codex response must contain a reviews array."
        raise ValueError(msg)
    reviews = []
    for raw_review in raw_reviews:
        if not isinstance(raw_review, dict):
            msg = "Each Codex review must be an object."
            raise ValueError(msg)
        review_value = cast(Mapping[str, object], raw_review)
        reviews.append(
            CodexReview(
                custom_id=str(review_value.get("custom_id", "")),
                decision=str(review_value.get("decision", "")),
                quote=str(review_value.get("quote", "")),
                reason=str(review_value.get("reason", "")),
            ),
        )
    return tuple(reviews)


def _validate_review(review: CodexReview, document: CandidateDocument) -> None:
    if review.decision not in {"pass", "not_found"}:
        msg = f"Invalid Codex decision for {review.custom_id}: {review.decision}"
        raise ValueError(msg)
    if not review.reason.strip():
        msg = f"Codex reason is empty for {review.custom_id}."
        raise ValueError(msg)
    if review.decision == "not_found" and review.quote:
        msg = f"not_found quote must be empty for {review.custom_id}."
        raise ValueError(msg)
    if review.decision == "pass" and (not review.quote or review.quote not in document.content):
        msg = f"pass quote is not an exact substring for {review.custom_id}."
        raise ValueError(msg)


def _checkpoint_reviews(
    path: Path,
    *,
    documents_by_id: Mapping[str, CandidateDocument],
) -> dict[str, CodexReview]:
    if not path.exists():
        return {}
    reviews: dict[str, CodexReview] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                break
            raise
        review = CodexReview(
            custom_id=str(value["custom_id"]),
            decision=str(value["decision"]),
            quote=str(value["quote"]),
            reason=str(value["reason"]),
        )
        document = documents_by_id.get(review.custom_id)
        if document is None:
            msg = f"Checkpoint contains an unknown custom_id: {review.custom_id}"
            raise ValueError(msg)
        _validate_review(review, document)
        reviews[review.custom_id] = review
    return reviews


def _append_checkpoint(path: Path, reviews: Iterable[CodexReview]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as output_file:
        for review in sorted(reviews, key=lambda item: item.custom_id):
            output_file.write(
                json.dumps(
                    {
                        "custom_id": review.custom_id,
                        "decision": review.decision,
                        "quote": review.quote,
                        "reason": review.reason,
                    },
                    ensure_ascii=False,
                ),
            )
            output_file.write("\n")
        output_file.flush()


def _full_checklist_rows(
    *,
    documents: Sequence[CandidateDocument],
    existing_rows: Sequence[Mapping[str, str]],
    reviews: Mapping[str, CodexReview],
) -> tuple[dict[str, str], ...]:
    existing_by_url = {row["github_url"]: row for row in existing_rows}
    rows = []
    for document in documents:
        existing = existing_by_url.get(document.url)
        if existing is not None:
            rows.append(
                {
                    "repository": existing["repository"],
                    "file": existing["file"],
                    "github_url": existing["github_url"],
                    "review_origin": "existing_166",
                    "llm_decision": document.v7_decision,
                    "human_decision": existing["human_decision"],
                    "duplicate_of": existing.get("duplicate_of", ""),
                    "codex_decision": existing["codex_decision"],
                    "codex_reason": existing["codex_reason"],
                    "note": existing["note"],
                },
            )
            continue
        review = reviews[document.custom_id]
        rows.append(
            {
                "repository": document.repository,
                "file": _local_filename(document),
                "github_url": document.url,
                "review_origin": "codex_added_561",
                "llm_decision": document.v7_decision,
                "human_decision": review.decision,
                "duplicate_of": "",
                "codex_decision": review.decision,
                "codex_reason": _codex_reason(review, document),
                "note": "",
            },
        )
    return tuple(rows)


def _local_filename(document: CandidateDocument) -> str:
    repository = _safe_component(document.repository.replace("/", "--"))
    filename = _safe_component(document.path.replace("/", "__"))
    return str(PurePosixPath(repository) / filename)


def _safe_component(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in value)


def _codex_reason(review: CodexReview, document: CandidateDocument) -> str:
    if review.decision == "not_found":
        return f"理由\uff1a{review.reason}"
    start_index = document.content.index(review.quote)
    line_start = document.content.count("\n", 0, start_index) + 1
    line_end = line_start + review.quote.count("\n")
    line_label = f"L{line_start}" if line_start == line_end else f"L{line_start}-L{line_end}"
    compact_quote = " ".join(review.quote.splitlines())
    return f"Evidence ({line_label}): {compact_quote} 理由\uff1a{review.reason}"


def _csv_rows(path: Path) -> tuple[dict[str, str], ...]:
    previous_limit = csv.field_size_limit()
    csv.field_size_limit(sys.maxsize)
    try:
        with path.open(encoding="utf-8", newline="") as input_file:
            return tuple(dict(row) for row in csv.DictReader(input_file))
    finally:
        csv.field_size_limit(previous_limit)


def _write_csv(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f".{hashlib.sha256(str(path).encode()).hexdigest()[:8]}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(path)
