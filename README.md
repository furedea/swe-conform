# SWE Guideline Refactor

This repository builds a reusable candidate set for evaluating coding agents on
project-specific coding guidelines and refactoring tasks.

## Repository filter

The filter processes repositories in this order:

1. Read each repository tree at the input `lastCommitSHA` through the GitHub API.
2. Select likely project guideline documents using deterministic path rules.
3. Classify the documents with one OpenAI Responses API call using
   `gpt-5.6-luna` and strict JSON Schema output.
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
export OPENAI_API_KEY=...
```

`GH_TOKEN` can be used instead of `GITHUB_TOKEN`. The program does not load a
`.env` file.

Validate all tracked input files without using either API:

```bash
uv run --frozen swe-guideline-refactor validate
```

Run a small pilot:

```bash
uv run --frozen swe-guideline-refactor filter \
  --limit 20 \
  --output-dir output/pilot
```

Run the complete dataset:

```bash
uv run --frozen swe-guideline-refactor filter \
  --output-dir output/repository-selection
```

The default execution uses four workers, `gpt-5.6-luna`, and `medium`
reasoning effort. Use `--workers`, `--model`, and `--reasoning-effort` to make
an explicit experimental configuration.

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
