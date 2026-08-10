"""Tests for the OpenRouter Responses API adapter."""

import json

import httpx

import openrouter_responses_client


def test_client_qualifies_luna_and_retries_a_temporary_error() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503, headers={"Retry-After": "0"}, text="temporarily unavailable")
        body = json.loads(request.content)
        assert body["model"] == "openai/gpt-5.6-luna"
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"label":"NO"}'}],
                    },
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "total_tokens": 110,
                    "cost": 0.000008,
                },
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handle))
    client = openrouter_responses_client.OpenRouterResponsesClient(
        api_key=__name__,
        http_client=http_client,
        retry_wait=lambda _seconds: None,
    )

    result = client.complete_json(
        instructions="Classify the file.",
        input_text="document",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=500,
        schema_name="classification",
        schema={"type": "object"},
    )

    assert result.value == {"label": "NO"}
    assert result.cost_usd == 0.000008
    assert len(requests) == 2
    assert all(request.url.path == "/api/v1/responses" for request in requests)
