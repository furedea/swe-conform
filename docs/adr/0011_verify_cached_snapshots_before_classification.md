# ADR-0011: Verify cached snapshots before classification

- Status: Accepted
- Date: 2026-08-13

In the context of classifying Markdown candidates from a large HDD corpus where
interrupted repository transfers can leave bare repositories and snapshot refs
present without every reachable object, facing biased results if partial
snapshots are treated as complete and unbounded requests if individual Markdown
files exceed the intended model input size, we decided to verify every object
reachable from each pinned revision before candidate extraction or
classification, record incomplete and explicitly excluded repositories instead
of submitting them, and retain oversized files as unresolved review items
instead of truncating them, and against trusting directory or ref existence or
silently shortening input, to preserve revision-level reproducibility and make
all omissions auditable, accepting an additional local Git traversal and manual
handling for unresolved files.
