"""Tests for the OpenAI Responses API adapter."""

from dataclasses import astuple
from unittest.mock import MagicMock

import httpx
import pytest

import openai_responses_client


def test_client_posts_strict_json_schema_to_responses_api() -> None:
    response = MagicMock(spec=httpx.Response)
    response.json.return_value = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": '{"status":"pass"}'}],
            },
        ],
        "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    }
    http_client = MagicMock(spec=httpx.Client)
    http_client.post.return_value = response
    client = openai_responses_client.OpenAIResponsesClient(
        api_key="sk-test",
        http_client=http_client,
    )

    result = client.complete_json(
        instructions="system",
        input_text="documents",
        model="gpt-5.6-luna",
        reasoning_effort="medium",
        max_output_tokens=800,
        schema_name="classification",
        schema={"type": "object"},
    )

    call = http_client.post.call_args
    assert call.args[0] == "https://api.openai.com/v1/responses"
    assert call.kwargs["headers"]["Authorization"] == "Bearer sk-test"
    assert call.kwargs["json"]["model"] == "gpt-5.6-luna"
    assert call.kwargs["json"]["reasoning"] == {"effort": "medium"}
    assert call.kwargs["json"]["store"] is False
    assert call.kwargs["json"]["text"]["format"]["strict"] is True
    assert result.value == {"status": "pass"}
    assert astuple(result.usage) == (100, 20, 120, 0, 0)


def test_client_reports_provider_error_body() -> None:
    response = MagicMock(spec=httpx.Response)
    response.status_code = 400
    response.text = '{"error":{"message":"invalid model"}}'
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "boom",
        request=MagicMock(spec=httpx.Request),
        response=response,
    )
    http_client = MagicMock(spec=httpx.Client)
    http_client.post.return_value = response
    client = openai_responses_client.OpenAIResponsesClient(
        api_key="sk-test",
        http_client=http_client,
    )

    with pytest.raises(openai_responses_client.ResponsesRequestError) as caught:
        client.complete_json(
            instructions="system",
            input_text="documents",
            model="gpt-5.6-luna",
            reasoning_effort="medium",
            max_output_tokens=800,
            schema_name="classification",
            schema={"type": "object"},
        )

    assert caught.value.status_code == 400
    assert "status=400" in str(caught.value)
    assert "invalid model" in str(caught.value)


def test_parse_json_response_reports_cache_read_and_write_tokens() -> None:
    document = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": '{"status":"pass"}'}],
            },
        ],
        "usage": {
            "input_tokens": 4_000,
            "input_tokens_details": {
                "cached_tokens": 1_920,
                "cache_write_tokens": 1_024,
            },
            "output_tokens": 300,
            "total_tokens": 4_300,
            "cost": 0.00042,
        },
    }

    result = openai_responses_client.parse_json_response(document)

    assert result.usage.cached_input_tokens == 1_920
    assert result.usage.cache_write_input_tokens == 1_024
    assert result.cost_usd == 0.00042
    assert result.document is document
