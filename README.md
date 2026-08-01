# SWE Guideline Refactor

This repository builds a reusable candidate set for evaluating coding agents on
project-specific coding guidelines and refactoring tasks.

## Repository filter

The filter processes repositories in this order:

1. Read each repository tree at the input `lastCommitSHA` through the GitHub API.
2. Select likely project guideline documents using deterministic path rules.
3. Classify the documents with one structured model call using either the
   OpenAI Responses API or an isolated Codex CLI process.
4. Verify that every `pass` quote occurs verbatim in the retrieved document.
5. Apply the SPDX 3.28.0 `isOsiApproved` license criterion only to guideline
   passes.

No model call is made when a complete Git tree has no candidate guideline
document. Repository documents are treated as untrusted evidence in the model
instructions.

See [docs/filtering_method.md](docs/filtering_method.md) for the complete
classification contract and limitations.

## Setup

```bash
uv sync --frozen
export GITHUB_TOKEN=...
```

`GH_TOKEN` can be used instead of `GITHUB_TOKEN`. The program does not load a
`.env` file. `OPENAI_API_KEY` is required only for the default `openai`
provider.

Validate all tracked input files without using either API:

```bash
uv run --frozen python src/main.py validate
```

Run a small pilot:

```bash
uv run --frozen python src/main.py filter \
  --limit 20 \
  --output-dir output/pilot
```

Run the complete dataset:

```bash
uv run --frozen python src/main.py filter \
  --output-dir output/repository-selection
```

The default execution uses four workers, `gpt-5.6-luna`, and `medium`
reasoning effort. Use `--workers`, `--model`, and `--reasoning-effort` to make
an explicit experimental configuration.

Use the signed-in Codex CLI without user configuration, rules, or project
instructions:

```bash
uv run --frozen python src/main.py filter \
  --provider codex-cli \
  --limit 20 \
  --output-dir output/codex-pilot
```

The Codex CLI provider defaults to `max` reasoning effort and does not require
`OPENAI_API_KEY`.

The 20-repository Codex prompt-calibration pilot and its fixed labels are in
[experiments/prompt-calibration-20260801](experiments/prompt-calibration-20260801).

## Resume and outputs

Each repository result is appended immediately to `results.jsonl`. Re-run the
same command with the same output directory to skip terminal results. Retrieval
and model errors are retried. A changed model, reasoning effort, input hash, or
classification setting requires a new output directory.

Each run materializes:

- `all_classified.csv`: all latest processed results
- `guideline_passed.csv`: repositories entering the license stage
- `guideline_review.csv`: guideline non-passes and errors
- `license_excluded_or_review.csv`: guideline passes without an OSI-approved license
- `selected_repositories.csv`: repositories passing both filters
- `summary.json`: status counts, model calls, and API token usage
- `run_configuration.json`: reproducibility metadata and input hashes

The command also reports requested, skipped, and evaluated repositories and
elapsed wall-clock seconds when it exits.
