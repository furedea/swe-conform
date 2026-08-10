"""OpenRouter Responses API adapter for structured classification."""

from collections.abc import Callable, Mapping

import httpx

import openai_responses_client

_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterResponsesClient(openai_responses_client.OpenAIResponsesClient):
    """Send structured Responses requests through OpenRouter."""

    __slots__ = ()

    def __init__(
        self,
        *,
        api_key: str,
        http_client: httpx.Client | None = None,
        max_attempts: int = 3,
        retry_wait: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=_BASE_URL,
            http_client=http_client,
            max_attempts=max_attempts,
            provider_name="OpenRouter",
            retry_wait=retry_wait,
        )

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
    ) -> openai_responses_client.JsonResponse:
        """Return one structured response using an OpenRouter model slug."""
        return super().complete_json(
            instructions=instructions,
            input_text=input_text,
            model=_openrouter_model(model),
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            schema_name=schema_name,
            schema=schema,
        )


def _openrouter_model(model: str) -> str:
    if not model or "/" in model:
        return model
    return f"openai/{model}"
