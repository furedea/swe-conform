# ADR-0010: Stream classification from bare Git caches

- Status: Accepted
- Date: 2026-08-12

In the context of classifying Markdown candidates from approximately 5,000
revision-pinned bare repositories, facing a large intermediate file when every
candidate body is materialized and redundant network retrieval when the exact
Git objects already exist locally, we decided to stream bounded batches of
blobs directly from the bare repository cache into one-file LLM requests with
append-only checkpoints while retaining the prepared-JSONL pilot workflow, and
against materializing the full corpus or falling back to GitHub, to bound disk
and memory use and preserve revision-level reproducibility, accepting a
separate production execution path and explicit retrieval errors when cached
objects are unavailable.
