"""Tests for the Amazon Bedrock Responses API adapter."""

import json

import httpx
import pytest

import bedrock_responses_client


def test_client_routes_luna_through_bedrock_mantle() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"label":"NO"}'}],
                    },
                ],
                "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handle))
    client = bedrock_responses_client.BedrockResponsesClient(
        api_key=__name__,
        region="us-east-1",
        http_client=http_client,
    )

    result = client.complete_json(
        instructions="Classify the file.",
        input_text="document",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=16_000,
        schema_name="classification",
        schema={"type": "object"},
    )

    request = requests[0]
    body = json.loads(request.content)
    assert request.url == "https://bedrock-mantle.us-east-1.api.aws/openai/v1/responses"
    assert request.headers["Authorization"] == f"Bearer {__name__}"
    assert body["model"] == "openai.gpt-5.6-luna"
    assert body["reasoning"] == {"effort": "max"}
    assert body["store"] is False
    assert body["text"]["format"]["strict"] is True
    assert result.value == {"label": "NO"}


def test_client_rejects_a_model_other_than_luna() -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("unsupported models must not be sent")

    client = bedrock_responses_client.BedrockResponsesClient(
        api_key=__name__,
        region="us-east-1",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )

    with pytest.raises(ValueError, match=r"supports only GPT-5\.6 Luna"):
        client.complete_json(
            instructions="Classify the file.",
            input_text="document",
            model="gpt-5.6-terra",
            reasoning_effort="max",
            max_output_tokens=16_000,
            schema_name="classification",
            schema={"type": "object"},
        )

    client.close()
