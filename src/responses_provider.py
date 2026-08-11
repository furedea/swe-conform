"""Provider-specific identifiers for Responses classification execution."""

from dataclasses import dataclass
from enum import StrEnum

BEDROCK_LUNA_REGIONS = ("us-east-1", "us-east-2", "us-west-2")


class ResponsesProvider(StrEnum):
    """Supported providers for per-file Responses classification."""

    BEDROCK = "bedrock"
    OPENROUTER = "openrouter"


@dataclass(frozen=True, slots=True)
class ResponsesPricing:
    """Published token rates for one Responses execution provider."""

    input_usd_per_million_tokens: float
    cached_input_usd_per_million_tokens: float
    cache_write_input_usd_per_million_tokens: float
    output_usd_per_million_tokens: float
    source: str
    date: str


_OPENROUTER_PRICING = ResponsesPricing(
    input_usd_per_million_tokens=0.10,
    cached_input_usd_per_million_tokens=0.01,
    cache_write_input_usd_per_million_tokens=0.125,
    output_usd_per_million_tokens=0.60,
    source="openrouter_calculated_pricing",
    date="2026-08-06",
)
_BEDROCK_PRICING = ResponsesPricing(
    input_usd_per_million_tokens=0.22,
    cached_input_usd_per_million_tokens=0.022,
    cache_write_input_usd_per_million_tokens=0.275,
    output_usd_per_million_tokens=1.32,
    source="bedrock_published_pricing",
    date="2026-08-11",
)


def model_id(provider: str, requested_model: str) -> str:
    """Return the provider-specific identifier for a prepared model name."""
    selected = ResponsesProvider(provider)
    if selected is ResponsesProvider.BEDROCK:
        if requested_model not in {"gpt-5.6-luna", "openai.gpt-5.6-luna"}:
            msg = f"Amazon Bedrock classification supports only GPT-5.6 Luna: {requested_model}"
            raise ValueError(msg)
        return "openai.gpt-5.6-luna"
    if not requested_model or "/" in requested_model:
        return requested_model
    return f"openai/{requested_model}"


def pricing(provider: str) -> ResponsesPricing:
    """Return published token rates for a Responses provider."""
    selected = ResponsesProvider(provider)
    if selected is ResponsesProvider.BEDROCK:
        return _BEDROCK_PRICING
    return _OPENROUTER_PRICING
