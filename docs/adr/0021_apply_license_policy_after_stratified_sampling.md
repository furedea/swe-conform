# ADR-0021: Apply license policy after stratified sampling

- Status: Superseded
- Date: 2026-08-18
- Superseded by: ADR-0023

In the context of balancing a redistributable benchmark across four language
strata, facing both license-based repository rejection and the need to preserve
the established seeded order within each stratum, we decided to construct the
complete deterministic schedule before applying the human-authored license
allowlist and to count only eligible baseline, reviewed, and new repositories
toward each quota, and against filtering the sampling population first or
counting repositories before license review, to keep both the licensed final
set and its replacement order reproducible, requiring finalization to verify
the allowlist fingerprint and preserve it as provenance, accepting that
ineligible schedule positions are skipped and prior collection configurations
require a new run.
