# ADR-0013: Bound and persist classification retries

- Status: Accepted
- Date: 2026-08-13

In the context of long-running per-file classification over revision-pinned bare
repositories, facing transient model and local retrieval failures that should
not exclude a repository and persistent failures that could otherwise be
retried without limit, we decided to append every file attempt to a durable
checkpoint, retry model failures at most three times and retrieval failures at
most twice by default, retry repository-level extraction failures at most three
times, and never count `model_error`, `retrieval_error`, `input_too_large`, or
`snapshot_incomplete` as positive evidence, and against either treating failures
as `review` or retrying indefinitely, to retain an auditable file-level history
and bounded cost, accepting that exhausted failures remain unresolved and
require cache repair, a larger input policy, or manual handling.
