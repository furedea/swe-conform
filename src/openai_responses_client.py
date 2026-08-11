"""OpenAI Responses API adapter with strict structured output."""

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import cast

import httpx

import guideline

_DEFAULT_TIMEOUT_SECONDS = 300.0
_DEFAULT_MAX_ATTEMPTS = 3
_ERROR_BODY_LIMIT = 2000
_RETRYABLE_STATUS_CODES = frozenset({408, 429})


class ResponsesRequestError(RuntimeError):
    """A Responses HTTP failure with its provider status code."""

    __slots__ = ("status_code",)

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class JsonResponse:
    """Parsed structured output and provider-reported usage."""

    value: Mapping[str, object]
    usage: guideline.TokenUsage
    cost_usd: float | None = None
    document: Mapping[str, object] = field(default_factory=dict)


class OpenAIResponsesClient:
    """Send single-turn structured requests to the OpenAI Responses API."""

    __slots__ = ("_base_url", "_client", "_headers", "_max_attempts", "_provider_name", "_retry_wait")

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        http_client: httpx.Client | None = None,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        provider_name: str = "OpenAI",
        retry_wait: Callable[[float], None] | None = None,
    ) -> None:
        if max_attempts < 1:
            msg = "max_attempts must be at least 1"
            raise ValueError(msg)
        self._base_url = base_url.rstrip("/")
        self._max_attempts = max_attempts
        self._provider_name = provider_name
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._client = http_client or httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS)
        self._retry_wait = retry_wait or time.sleep

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
    ) -> JsonResponse:
        """Return a strict JSON-Schema response from one model request."""
        response = self._post(
            {
                "model": model,
                "instructions": instructions,
                "input": input_text,
                "reasoning": {"effort": reasoning_effort},
                "max_output_tokens": max_output_tokens,
                "store": False,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": dict(schema),
                    },
                },
            },
        )
        document = cast(Mapping[str, object], response.json())
        return parse_json_response(document)

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def _post(self, body: Mapping[str, object]) -> httpx.Response:
        for attempt in range(self._max_attempts):
            try:
                response = self._client.post(
                    f"{self._base_url}/responses",
                    headers=self._headers,
                    json=body,
                )
                response.raise_for_status()
            except httpx.TransportError as error:
                if self._should_retry(attempt):
                    self._retry_wait(float(2**attempt))
                    continue
                msg = f"{self._provider_name} Responses request failed after {self._max_attempts} attempts: {error}"
                raise RuntimeError(msg) from error
            except httpx.HTTPStatusError as error:
                if self._is_retryable_status(error.response.status_code) and self._should_retry(attempt):
                    self._retry_wait(_retry_delay(error.response, attempt))
                    continue
                response_body = error.response.text[:_ERROR_BODY_LIMIT]
                msg = (
                    f"{self._provider_name} Responses request failed: "
                    f"status={error.response.status_code} body={response_body}"
                )
                raise ResponsesRequestError(msg, status_code=error.response.status_code) from error
            else:
                return response
        msg = f"{self._provider_name} Responses request retry loop ended unexpectedly"
        raise RuntimeError(msg)

    def _should_retry(self, attempt: int) -> bool:
        return attempt + 1 < self._max_attempts

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code in _RETRYABLE_STATUS_CODES or status_code >= 500


def parse_json_response(document: Mapping[str, object]) -> JsonResponse:
    """Parse structured output and usage from a Responses API document."""
    value = cast(Mapping[str, object], json.loads(_extract_output_text(document)))
    return JsonResponse(
        value=value,
        usage=_extract_usage(document),
        cost_usd=_extract_cost(document),
        document=document,
    )


def _extract_output_text(document: Mapping[str, object]) -> str:
    output = cast(list[Mapping[str, object]], document.get("output", []))
    text_parts: list[str] = []
    for item in output:
        content = cast(list[Mapping[str, object]], item.get("content", []))
        text_parts.extend(str(part["text"]) for part in content if part.get("type") == "output_text")
    if not text_parts:
        msg = "OpenAI Responses result did not contain output_text"
        raise RuntimeError(msg)
    return "".join(text_parts)


def _extract_usage(document: Mapping[str, object]) -> guideline.TokenUsage:
    usage = cast(Mapping[str, object], document.get("usage", {}))
    input_details = cast(Mapping[str, object], usage.get("input_tokens_details", {}))
    return guideline.TokenUsage(
        input_tokens=int(str(usage.get("input_tokens", 0))),
        output_tokens=int(str(usage.get("output_tokens", 0))),
        total_tokens=int(str(usage.get("total_tokens", 0))),
        cached_input_tokens=int(str(input_details.get("cached_tokens", 0))),
        cache_write_input_tokens=int(str(input_details.get("cache_write_tokens", 0))),
    )


def _extract_cost(document: Mapping[str, object]) -> float | None:
    usage = cast(Mapping[str, object], document.get("usage", {}))
    cost = usage.get("cost")
    if cost is None:
        return None
    if isinstance(cost, bool) or not isinstance(cost, int | float) or cost < 0:
        msg = "Responses usage.cost must be a non-negative number"
        raise ValueError(msg)
    return float(cost)


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            value = float(retry_after)
        except ValueError:
            pass
        else:
            if value >= 0:
                return value
    return float(2**attempt)
