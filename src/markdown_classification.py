"""Shared validation and accounting for per-file Markdown classifications."""

from collections.abc import Mapping

import guideline

LONG_CONTEXT_THRESHOLD = 272_000


def classification_fields(value: Mapping[str, object], *, content: str) -> dict[str, object]:
    """Validate one structured model value and derive its screening status."""
    label = value["label"]
    model_reason = value["reason"]
    quote = value["quote"]
    confidence = value["confidence"]
    if not isinstance(label, str):
        raise TypeError("label must be a string")
    if not isinstance(model_reason, str):
        raise TypeError("reason must be a string")
    if not isinstance(quote, str):
        raise TypeError("quote must be a string")
    if isinstance(confidence, bool) or not isinstance(confidence, int) or not 1 <= confidence <= 10:
        raise ValueError("confidence must be an integer from 1 to 10")
    status, reason = classification_status(label=label, quote=quote, content=content)
    return {
        "model_label": label,
        "model_reason": model_reason,
        "quote": quote,
        "confidence": confidence,
        "status": status,
        "reason": reason,
    }


def classification_status(*, label: str, quote: str, content: str) -> tuple[str, str]:
    """Map a structured decision and its verified quote to a screening status."""
    if label == "YES":
        if not quote or quote not in content:
            return "review", "yes_without_quote"
        return "pass", "verified_quote"
    if label == "NO":
        if quote:
            return "review", "no_with_quote"
        return "not_found", "model_not_found"
    return "review", "invalid_label"


def request_cost(
    usage: guideline.TokenUsage,
    *,
    input_usd_per_million_tokens: float,
    cached_input_usd_per_million_tokens: float,
    cache_write_input_usd_per_million_tokens: float,
    output_usd_per_million_tokens: float,
) -> float:
    """Calculate one request cost using the provider's context multipliers."""
    input_multiplier = 2.0 if usage.input_tokens > LONG_CONTEXT_THRESHOLD else 1.0
    output_multiplier = 1.5 if usage.input_tokens > LONG_CONTEXT_THRESHOLD else 1.0
    value = (
        usage.uncached_input_tokens * input_usd_per_million_tokens * input_multiplier
        + usage.cached_input_tokens * cached_input_usd_per_million_tokens * input_multiplier
        + usage.cache_write_input_tokens * cache_write_input_usd_per_million_tokens * input_multiplier
        + usage.output_tokens * output_usd_per_million_tokens * output_multiplier
    ) / 1_000_000
    return round(value, 9)
