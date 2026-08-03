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

Exact prompts, gold labels, contract hashes, confusion matrices, elapsed times,
and token usage are stored beside this file.

## Reproduction

Create one split input:

```bash
uv run --frozen python src/calibration.py split-input \
  experiments/prompt-calibration-20260801/input/candidates.csv \
  output/prompt-calibration/input-tuning \
  --split tuning
```

Run the current frozen v3 classifier:

```bash
uv run --frozen python src/main.py filter \
  --provider codex-cli \
  --input-dir output/prompt-calibration/input-tuning \
  --output-dir output/prompt-calibration/reproduction-v3-tuning \
  --model gpt-5.6-luna \
  --reasoning-effort max \
  --workers 2 \
  --model-timeout-seconds 900
```

Evaluate the run:

```bash
uv run --frozen python src/calibration.py evaluate \
  experiments/prompt-calibration-20260801/gold_labels.csv \
  output/prompt-calibration/reproduction-v3-tuning/all_classified.csv \
  --split tuning
```

The Codex client runs in a temporary empty directory with user configuration,
rules, persistence, and project instructions disabled. It uses a read-only
sandbox and records Codex-reported input and output token usage.
