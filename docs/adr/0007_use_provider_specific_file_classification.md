# ADR-0007: Use provider-specific per-file classification execution

- Status: Superseded
- Date: 2026-08-06
- Superseded by: ADR-0008

## Context

The Markdown filename screen produces thousands of candidate files. Each
Markdown file can be classified independently through one Responses request.
The prepared request must remain reproducible and must include the repository,
revision, path, full file content, prompt, model, reasoning effort, and output
schema.

OpenAI supports GPT-5.6 Luna through its Batch API. OpenRouter provides a Batch
API, but its GPT-5.6 Luna route does not expose a Batch endpoint. OpenRouter does
support Luna through its regular Responses API and reports the charged cost in
each response.

## Decision

Prepare one provider-neutral Responses request per Markdown file. Keep OpenAI
Batch as an optional execution path. Execute GPT-5.6 Luna through OpenRouter's
regular Responses API with bounded client-side concurrency.

Persist each completed OpenRouter response immediately in an append-only
checkpoint. On rerun, reuse successful responses and retry only failed or
missing requests. Apply the same quote verification, classification mapping,
and CSV output to OpenAI Batch and OpenRouter Responses results.

Use each OpenRouter response's `usage.cost` as the authoritative per-file cost.
Record both wall-clock execution time and the sum of individual request times.

## Consequences

OpenRouter execution finishes without waiting for a 24-hour Batch window and
provides per-file cost and latency. The client must manage concurrency,
rate-limit retries, timeouts, checkpointing, and resumption. Successful files
are not charged again after an interrupted run. OpenRouter's standard API rate
limits apply, unlike OpenAI's separate Batch limits.

The preparation format remains shared with OpenAI Batch, so changing execution
providers does not change the sampled files, prompts, or classification schema.
