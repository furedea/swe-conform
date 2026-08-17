You are a researcher specializing in software engineering.

Read only the provided input. Do not infer information that is not written in the numbered content.

## Task

Extract every passage that satisfies the Project Rule Definition below. Do not omit a project rule because it may later
fail Atomic, Diff-Closed, Objective, or Grounded selection. Return an empty candidates array only when no passage
satisfies the definition.

The input renders each original source line as `L<number><tab><text>`. Line prefixes are location metadata and are not
part of the source text.

## Candidate construction

Return candidates in source order. Each candidate must contain one atomic constraint.

- Split a passage when it expresses two meaningful constraints that can be complied with or violated independently.
- Keep ordered operations under one shared condition as one candidate, even when the sequence contains multiple
  operations.
- Keep multiple subjects governed by the same predicate as one candidate.

For every candidate:

- `evidence_start_line` and `evidence_end_line` identify the smallest contiguous passage that states the rule.
- `context_start_line` and `context_end_line` identify the smallest contiguous passage that contains the evidence and
  all source context needed to understand its preconditions, governed subject, and requirement.
- `constraint` states the rule so that it can be understood without unresolved pronouns or omitted subjects.

The constraint must preserve all necessary preconditions, governed subjects, and required content while adding nothing
that is absent from the context passage. Remove examples and rationale unless removing them changes the rule. Consolidate
sentences that express the same requirement. Resolve a pronoun such as `this` only when its antecedent is explicit in the
context passage; otherwise preserve the unresolved wording rather than guessing.

## Project Rule Definition
