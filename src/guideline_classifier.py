"""Repository-wide project guideline classification."""

import hashlib
import json
import time
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import replace
from functools import cache
from pathlib import Path
from typing import Protocol, cast

import guideline
import guideline_evidence
import openai_responses_client
import repository
import repository_workspace

_MAX_DOCUMENT_CHARACTERS = 30_000
_MAX_INPUT_CHARACTERS = 120_000
_MAX_ERROR_CHARACTERS = 500
_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "repository_guideline_exploration.md"
_OUTPUT_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "prompts" / "repository_guideline_exploration_schema.json"


@cache
def _system_instructions() -> str:
    return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


@cache
def _output_schema() -> Mapping[str, object]:
    schema = json.loads(_OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        msg = f"{_OUTPUT_SCHEMA_PATH} must contain a JSON object"
        raise ValueError(msg)
    return cast(dict[str, object], schema)


def contract_fingerprint() -> str:
    """Return a stable digest of the model classification contract."""
    contract = {
        "instructions": _system_instructions(),
        "schema": _output_schema(),
        "repository_input": "revision-pinned-snapshot-v1",
        "evidence_verification": "snapshot-verbatim-substring-v2",
    }
    encoded = json.dumps(contract, ensure_ascii=True, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


class StructuredModelClient(Protocol):
    """Complete one strict structured model request."""

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
        """Return structured model output."""
        ...


class RepositoryWorkspace(Protocol):
    """Materialize one repository revision for model inspection."""

    def checkout(self, repository: str, revision: str) -> AbstractContextManager[Path]:
        """Yield a source-only repository workspace."""
        ...


class ModelGuidelineChecker:
    """Explore and classify one revision-pinned repository snapshot."""

    __slots__ = ("_max_output_units", "_model", "_model_client", "_reasoning_effort", "_workspace")

    def __init__(
        self,
        *,
        workspace: RepositoryWorkspace,
        model_client: StructuredModelClient,
        model: str,
        reasoning_effort: str = "medium",
        max_output_tokens: int = 800,
    ) -> None:
        self._workspace = workspace
        self._model_client = model_client
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._max_output_units = max_output_tokens

    def check(self, candidate: repository.RepositoryCandidate) -> guideline.GuidelineResult:
        """Return an evidence-checked classification for one repository."""
        checkout_started_at = time.monotonic()
        try:
            with self._workspace.checkout(candidate.repository, candidate.revision) as workspace_path:
                model_started_at = time.monotonic()
                checkout_seconds = model_started_at - checkout_started_at
                try:
                    result = self._classify(workspace_path)
                except Exception as error:
                    return guideline.GuidelineResult(
                        status=guideline.GuidelineStatus.MODEL_ERROR,
                        reason=_error_reason("Model classification failed", error),
                        model_called=True,
                        checkout_seconds=checkout_seconds,
                        model_seconds=time.monotonic() - model_started_at,
                    )
                return replace(
                    result,
                    checkout_seconds=checkout_seconds,
                    model_seconds=time.monotonic() - model_started_at,
                )
        except repository_workspace.RepositoryCheckoutError as error:
            return guideline.GuidelineResult(
                status=guideline.GuidelineStatus.RETRIEVAL_ERROR,
                reason=_error_reason("Repository checkout failed", error),
                checkout_seconds=time.monotonic() - checkout_started_at,
            )
        except Exception as error:
            return guideline.GuidelineResult(
                status=guideline.GuidelineStatus.MODEL_ERROR,
                reason=_error_reason("Model classification failed", error),
                model_called=True,
                checkout_seconds=time.monotonic() - checkout_started_at,
            )

    def _classify(
        self,
        workspace_path: Path,
    ) -> guideline.GuidelineResult:
        response = self._model_client.complete_json(
            instructions=_system_instructions(),
            input_text="Inspect the repository snapshot in repository/.",
            model=self._model,
            reasoning_effort=self._reasoning_effort,
            max_output_tokens=self._max_output_units,
            schema_name="guideline_classification",
            schema=_output_schema(),
            working_directory=workspace_path,
        )
        return _result_from_response(response, workspace_path / "repository")


def classification_payload(
    candidate: repository.RepositoryCandidate,
    evidence: guideline_evidence.RepositoryEvidence,
) -> dict[str, object]:
    """Return the exact evidence payload supplied to the model."""
    remaining = _MAX_INPUT_CHARACTERS
    documents: list[dict[str, str]] = []
    for document in evidence.documents:
        content = _excerpt(document.content, min(_MAX_DOCUMENT_CHARACTERS, remaining))
        if not content:
            break
        documents.append({"path": document.path, "content": content})
        remaining -= len(content)
    return {
        "repository": candidate.repository,
        "revision": candidate.revision,
        "tree_truncated": evidence.tree_truncated,
        "documents": documents,
    }


def _excerpt(content: str, limit: int) -> str:
    if limit <= 0 or len(content) <= limit:
        return content[:limit] if limit > 0 else ""
    marker = "\n...[middle truncated by evaluator]...\n"
    head_length = (limit - len(marker)) * 2 // 3
    tail_length = limit - len(marker) - head_length
    return f"{content[:head_length]}{marker}{content[-tail_length:]}"


def _result_from_response(
    response: openai_responses_client.JsonResponse,
    repository_path: Path,
) -> guideline.GuidelineResult:
    status = guideline.GuidelineStatus(str(response.value["status"]))
    raw_evidence = response.value["evidence"]
    evidence, evidence_issues = _verified_evidence(raw_evidence, repository_path)
    if status is guideline.GuidelineStatus.NOT_FOUND:
        if bool(raw_evidence):
            status = guideline.GuidelineStatus.REVIEW
            reason = "Model returned evidence for a not_found classification"
        else:
            reason = "Model reported no project guideline"
    elif not evidence:
        status = guideline.GuidelineStatus.REVIEW
        reason = "Model pass evidence could not be verified against the repository snapshot"
    elif evidence_issues:
        status = guideline.GuidelineStatus.REVIEW
        reason = (
            f"Model returned {len(evidence) + len(evidence_issues)} evidence items: "
            f"{len(evidence)} verified and {len(evidence_issues)} unverified"
        )
    else:
        reason = "Verified project guideline evidence"
    return guideline.GuidelineResult(
        status=status,
        reason=reason,
        evidence=evidence,
        evidence_issues=evidence_issues,
        model_response_json=json.dumps(response.value, ensure_ascii=True, sort_keys=True),
        model_called=True,
        usage=response.usage,
    )


def _verified_evidence(
    raw_evidence: object,
    repository_path: Path,
) -> tuple[tuple[guideline.GuidelineEvidence, ...], tuple[guideline.GuidelineEvidenceIssue, ...]]:
    if not isinstance(raw_evidence, list):
        return (), ()
    root = repository_path.resolve()
    verified: list[guideline.GuidelineEvidence] = []
    issues: list[guideline.GuidelineEvidenceIssue] = []
    paths: set[str] = set()
    for index, item in enumerate(raw_evidence, start=1):
        evidence, reason = _verified_evidence_item(item, root, paths)
        if evidence is None:
            issues.append(_evidence_issue(index, item, reason))
            continue
        paths.add(evidence.path)
        verified.append(evidence)
    return tuple(verified), tuple(issues)


def _evidence_issue(index: int, raw_item: object, reason: str) -> guideline.GuidelineEvidenceIssue:
    item = cast(dict[str, object], raw_item) if isinstance(raw_item, dict) else {}
    return guideline.GuidelineEvidenceIssue(
        index=index,
        path=str(item.get("path", "")),
        quote=str(item.get("quote", "")),
        reason=reason,
    )


def _verified_evidence_item(
    raw_item: object,
    root: Path,
    existing_paths: set[str],
) -> tuple[guideline.GuidelineEvidence | None, str]:
    fields, reason = _evidence_fields(raw_item)
    if fields is None:
        return None, reason
    path, quote = fields
    evidence_path = (root / path).resolve()
    try:
        normalized_path = evidence_path.relative_to(root).as_posix()
    except ValueError:
        return None, "path resolves outside repository"
    if normalized_path in existing_paths:
        return None, "path duplicates another verified evidence item"
    if not evidence_path.is_file():
        return None, "path is not a file"
    content = evidence_path.read_bytes()
    if quote not in content.decode(encoding="utf-8", errors="replace"):
        return None, "quote is not an exact substring of the file"
    return guideline.GuidelineEvidence(path=normalized_path, quote=quote, content=content), ""


def _evidence_fields(raw_item: object) -> tuple[tuple[str, str] | None, str]:
    if not isinstance(raw_item, dict):
        return None, "item is not an object"
    item = cast(dict[str, object], raw_item)
    path = str(item.get("path", ""))
    quote = str(item.get("quote", ""))
    if not path:
        return None, "path is empty"
    if not quote:
        return None, "quote is empty"
    return (path, quote), ""


def _error_reason(prefix: str, error: Exception) -> str:
    return f"{prefix}: {type(error).__name__}: {error}"[:_MAX_ERROR_CHARACTERS]
