# ADR-0018: Separate rule extraction and judgment

- Status: Accepted
- Date: 2026-08-17

In the context of organizing accepted guideline files into benchmark constraints,
facing the need to measure extraction completeness separately from the validity of
each selected constraint, we decided for exhaustive file-level extraction followed
by an independent seven-condition judgment and deterministic conjunction, and
against one model response that silently filters while extracting or one call for
every individual substep, to preserve source provenance and distinguish omitted
rules from rejected candidates, accepting two model requests for each source file
that yields at least one candidate.
