# Prompt Calibration Pilot

This pilot calibrates the project-guideline classifier without using an API-billed
model endpoint. It uses the signed-in Codex CLI with `gpt-5.6-luna`, maximum
reasoning effort, one structured turn per repository, and the same deterministic
evidence collector as the repository filter.

## Protocol

1. Select 20 repositories deterministically: five per primary language, sampled
   from prior automatic `pass`, `review`, and `not_found` strata. Those prior
   statuses are sampling strata only, not ground truth.
2. Fix a 12-repository tuning split and an 8-repository holdout split before
   annotation or prompt inspection.
3. Run the v1 prompt on all 20 repositories.
4. Export repository documents without predictions and adjudicate all 20 with
   Codex sol at xhigh reasoning.
5. Inspect only tuning predictions, revise general decision boundaries, and
   stop at the tuning accuracy ceiling.
6. Freeze v3 and evaluate the holdout once.

The blind adjudication initially missed the test-authoring rules in
`harry0703/MoneyPrinterTurbo/test/README.md`. After v1 comparison, the gold label
was corrected from `not_found` to `pass` before prompt tuning. This post-hoc
annotation correction is retained in `results.json` and is a limitation of the
pilot.

## Results

| Prompt                  | Split   | Correct | Accuracy | Macro-F1 |
| ----------------------- | ------- | ------: | -------: | -------: |
| v1 baseline             | tuning  |    9/12 |    0.750 |    0.536 |
| v2 guideline boundaries | tuning  |   10/12 |    0.833 |    0.778 |
| v3 explicit review      | tuning  |   12/12 |    1.000 |    1.000 |
| v3 frozen               | holdout |     8/8 |    1.000 |    0.667 |

The holdout contains seven `pass`, one `review`, and no `not_found` labels.
Consequently, accuracy is more informative than three-class macro-F1 for this
small holdout. The result establishes pipeline viability, not a precise estimate
of population accuracy.

v2 excluded consumer-facing API descriptions and made unresolved external code
guides and unnamed mandatory style tools `review`. v3 further required an
explicit code-guidance context for unresolved links and excluded tool badges or
metadata from `review`. No repository names or repository-specific examples were
added to any prompt.

Gold labels, contract hashes, confusion matrices, elapsed times, and token usage
are stored beside this file. Historical prompt texts and reproduction commands
are not retained; only the active classifier contract is supported.
