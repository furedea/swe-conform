"""OpenAI Responses API adapter with strict structured output."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import httpx

import guideline

_DEFAULT_TIMEOUT_SECONDS = 300.0
_ERROR_BODY_LIMIT = 2000


@dataclass(frozen=True, slots=True)
class JsonResponse:
    """Parsed structured output and provider-reported usage."""

    value: Mapping[str, object]
    usage: guideline.TokenUsage


class OpenAIResponsesClient:
    """Send single-turn structured requests to the OpenAI Responses API."""

    __slots__ = ("_base_url", "_client", "_headers")

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._client = http_client or httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS)

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
        response = self._client.post(
            f"{self._base_url}/responses",
            headers=self._headers,
            json={
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
        self._raise_for_status(response)
        document = cast(Mapping[str, object], response.json())
        value = cast(Mapping[str, object], json.loads(_extract_output_text(document)))
        return JsonResponse(value=value, usage=_extract_usage(document))

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def _raise_for_status(self, response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            body = response.text[:_ERROR_BODY_LIMIT]
            msg = f"OpenAI Responses request failed: status={response.status_code} body={body}"
            raise RuntimeError(msg) from error


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
    return guideline.TokenUsage(
        input_tokens=int(str(usage.get("input_tokens", 0))),
        output_tokens=int(str(usage.get("output_tokens", 0))),
        total_tokens=int(str(usage.get("total_tokens", 0))),
    )
