# ADR-0016: Aggregate exact Markdown content before classification

- Status: Accepted
- Date: 2026-08-16

In the context of file-level project-rule screening, facing identical Markdown
content stored at multiple paths and redundant model calls that inflate file
counts, we decided for retaining every raw candidate while classifying one
deterministic representative per exact SHA-256 content identity, with Git blob
identity as a replay fallback for existing reports, and against deleting source
occurrences or automatically merging semantically similar and versioned files,
to reduce classification cost without obscuring provenance, accepting that
translations and old-versus-current versions still require a separate human
decision.
