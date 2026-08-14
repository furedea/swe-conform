# ADR-0015: Keep raw provider responses in checkpoints

- Status: Accepted
- Date: 2026-08-15

In the context of resumable file classification across hundreds of repositories,
facing CSV parser limits and hundreds of megabytes of duplicated raw model
responses, we decided for retaining lossless provider responses only in each
repository's append-only checkpoint and deriving compact CSV and aggregate JSONL
reports from those checkpoints, and against duplicating raw responses in every
report or globally raising CSV field limits, to preserve auditability and resume
safety while keeping operational reports bounded and portable, accepting that
raw-response inspection requires resolving a file decision back to its
repository checkpoint.
