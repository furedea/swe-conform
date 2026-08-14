# ADR-0014: Allow explicit repository sources

- Status: Accepted
- Date: 2026-08-14

In the context of target-based guideline collection that may screen only a few
hundred repositories while full-corpus runs already use revision-pinned bare Git
caches, facing the operational cost of requiring the HDD corpus for every local
run and the reproducibility risk of maintaining separate collection workflows,
we decided for one shared collection pipeline with an explicit `cache` or
`github` repository source and against automatic fallback or source-specific
sampling and classification logic, to preserve identical fixed-SHA selection,
file decisions, checkpoints, and reports across execution environments,
accepting GitHub rate-limit waits and a persistent local blob store for API-backed
runs.
