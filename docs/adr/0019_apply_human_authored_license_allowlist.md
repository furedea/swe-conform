# ADR-0019: Apply a human-authored license allowlist

- Status: Accepted
- Date: 2026-08-17
- Supersedes: ADR-0003

In the context of selecting repositories whose contents may be redistributed in
a benchmark, facing the need to make license eligibility both a human decision
and reproducible across collection rounds, we decided for a human-authored
allowlist of reported license names followed by deterministic machine filtering
and against automatic SPDX or OSI classification and repeated per-repository
decisions, to preserve human control while making the selection auditable,
accepting that reported license metadata is trusted and that blank or ambiguous
license names are rejected.
