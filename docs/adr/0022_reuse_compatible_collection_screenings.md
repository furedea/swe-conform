# ADR-0022: Reuse compatible collection screenings

- Status: Accepted
- Date: 2026-08-18

In the context of continuing a balanced collection under a new configuration
schema, facing incompatible checkpoint directories and the cost and sampling
bias of classifying the same repositories again, we decided to reuse terminal
screening outcomes from an explicitly supplied prior collection only after its
inputs, classification contract, complete seeded schedule manifest,
repositories, and revisions match the current run, require every carried
positive to have a completed human review, reconstruct confirmed selection
metadata from the unchanged schedule, and preserve the prior configuration,
attempt log, and schedule manifest as fingerprinted provenance, and against
trusting the checklist alone, migrating old checkpoints, or repeating prior
terminal screenings, to continue from the first unprocessed eligible position
while keeping the current run's screening budget independent, accepting that
unresolved prior outcomes are processed again in the new collection.
