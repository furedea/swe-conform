# Hard-Case Prompt Calibration

This experiment tests the project-guideline classifier on 20 fixed, difficult
repository revisions. Every prompt version is run on all 20 cases with the
signed-in Codex CLI, `gpt-5.6-luna`, maximum reasoning effort, and one
structured turn per repository. No API-billed OpenAI endpoint is used.

## Selection

The sample contains five repositories from each of Java, JavaScript, Python,
and TypeScript. Each language uses three strata from the previous deterministic
document detector:

- one lowest-density prior `pass` (`subtle_confirmed`),
- two highest-density prior `review` cases (`dense_unconfirmed`), and
- two lowest-density prior `review` cases (`weak_unconfirmed`).

The score is `10 * strong + 4 * normative + min(code, 25) + document count`,
plus a path-ambiguity adjustment for prior `pass` cases. Previous pilot
repositories, truncated trees, duplicate owners, and duplicate project names
are excluded. Prior statuses and document scores select the sample but are
removed from classifier input.

An initial design that selected only the highest-density cases was discarded
before any Luna prediction was generated. Blind adjudication showed that it
was overwhelmingly positive and therefore unsuitable for three-class prompt
calibration.

## Adjudication

The exact classifier evidence payloads were exported before baseline
prediction. Codex sol at xhigh reasoning labeled them without seeing Luna
outputs. The final support is 12 `pass`, two `review`, and six `not_found`.
Every positive evidence quote is verified as a substring of the specified
revision-pinned document.

The initial blind label for `custom-cards/button-card` missed a mandatory but
unnamed linting and formatting condition in a secondary candidate document.
It was corrected from `not_found` to `review` after baseline comparison and
before prompt revision. This post-hoc correction is retained in `results.json`.

## Results

| Prompt                                | Correct | Accuracy | Macro-F1 | Seconds |  Tokens |
| ------------------------------------- | ------: | -------: | -------: | ------: | ------: |
| v3 baseline                           |   18/20 |    0.900 |    0.844 | 198.529 | 686,166 |
| v4 required review and verbatim lines |   19/20 |    0.950 |    0.960 | 193.363 | 688,446 |
| v5 named-tool adoption                |   20/20 |    1.000 |    1.000 | 176.940 | 689,054 |

v4 limits unnamed-tool `review` decisions to mandatory compliance and requires
quotes to preserve line breaks exactly. v5 counts a named formatter or linter
adoption statement in an explicit contributor code-style section while still
excluding badges, command lists, and optional tool suggestions. No repository
names or repository-specific examples appear in a prompt.

The stopping rule was accuracy 1.0 or no improvement from a generalized prompt
revision. v5 reached the finite-sample ceiling.

Contract hashes, confusion matrices, elapsed times, token usage, and limitations
are recorded in `results.json`. Historical prompt texts and reproduction
commands are not retained; only the active classifier contract is supported.

## Interpretation

This is a calibration result, not an unbiased generalization estimate. All 20
cases influenced prompt development, only two cases have `review` labels, and
each prompt uses one stochastic model call per repository. A new untouched and
larger evaluation set is required before reporting final classifier accuracy.
