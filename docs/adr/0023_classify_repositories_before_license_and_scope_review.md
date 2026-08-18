# ADR-0023: Classify repositories before license and scope review

- Status: Accepted
- Date: 2026-08-18
- Supersedes: ADR-0021

In the context of collecting a language-balanced, redistributable guideline
benchmark, facing a license policy that is independent of guideline screening
and costly human file review, we decided to construct the complete fixed
per-language schedule before license review, classify scheduled repositories
provisionally regardless of license, apply the human-authored license allowlist
to positive repositories, count only eligible repositories toward each final
language quota, and export only eligible positives for human scope review, and
against either filtering the classification population by license or counting
ineligible repositories toward a final quota, to preserve the screening
contract and seeded replacement order while avoiding unnecessary human review,
requiring finalization to verify and preserve the allowlist fingerprint as
provenance, accepting model cost for provisional repositories later rejected by
license policy and iterative replenishment after license or scope rejection.
