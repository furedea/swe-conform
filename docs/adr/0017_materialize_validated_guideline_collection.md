# ADR-0017: Materialize one validated guideline collection

- Status: Accepted
- Date: 2026-08-17

In the context of combining baseline samples with multiple completed human-review
rounds, facing scattered intermediate reports and the risk that repository and
file manifests drift apart, we decided for one deterministic finalization command
that validates source fingerprints, repository sets, revisions, and duplicate
decisions before writing normalized manifests, counts, and provenance, and against
manual CSV concatenation or treating an intermediate selection report as final,
to produce one auditable benchmark bundle, accepting that finalization refuses to
write results until every human decision and cross-file invariant is complete.
