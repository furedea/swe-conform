# ADR-0006: Separate candidate screening from scope review

- Status: Accepted
- Date: 2026-08-04

In the context of identifying repositories with project-specific coding and
testing conventions, facing an interpretation-dependent boundary between
project-specific rules and reusable style guidance, we decided for broad LLM
screening of natural-language coding and testing guidance followed by human
scope review and against asking the model to decide project specificity during
initial screening, to derive explicit exclusion rules from reviewed false
positives, accepting a larger manual-review workload.
