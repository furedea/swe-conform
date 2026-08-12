# swe-conform

This repository builds a reusable candidate set for evaluating coding agents on
project guidelines and refactoring tasks.

## Repository filter

The filter processes repositories in this order:

1. Fetch every Git object reachable from each input `lastCommitSHA` into the
   persistent HDD cache without shallow or partial-clone filters.
2. Materialize that exact revision from the HDD cache in a disposable SSD
   workspace without Git metadata or network access.
3. Run Codex CLI in a restricted container to search the complete snapshot for
   natural-language statements that directly constrain the content, structure,
   or behavior of source code or test code.
4. Verify every `pass` quote at its reported path and preserve every verified
   guideline file as an output artifact.

License metadata from the candidate CSV remains in the reports but does not
affect classification or selection. License eligibility is reviewed manually.

The container receives only a read-only snapshot, a writable output directory,
and a temporary Codex home containing selected authentication runtime files.
Bubblewrap hides that temporary Codex home from model-generated tools. The real
user home and Docker socket are not mounted. User configuration, exec-policy
rules, persistence, browser, plugins, skill search, multi-agent features, and
other optional harness components are disabled. Repository contents are treated
as untrusted evidence.

See [docs/filtering_method.md](docs/filtering_method.md) for the complete
classification contract and limitations.

## Setup

```bash
uv sync --frozen
```

The filter uses the signed-in Codex CLI and does not require `OPENAI_API_KEY`.
Build the pinned exploration image once on each execution host:

```bash
docker build \
  --file docker/codex.Dockerfile \
  --tag swe-conform-codex:0.146.0 \
  docker

uv run --frozen python src/main.py preflight
```

Preflight must succeed on every execution host before a filter run. It verifies
the system bubblewrap installation, sandbox launch, repository read access,
repository write denial, Codex credential denial, and tool network denial. A
failure exits before any repository is submitted to the model.

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

This direct pilot mode downloads each source snapshot immediately and remains
available for quick local checks. It still uses the container by default. Use
`--codex-runtime host` only for local debugging. The research run separates network
acquisition from Codex exploration. First fetch the pinned repositories to the
HDD:

```bash
uv run --frozen python src/main.py fetch \
  --cache-root /mnt/hdd/swe-conform-repositories
```

Then run the complete dataset with temporary workspaces on the SSD:

```bash
uv run --frozen python src/main.py filter \
  --cache-root /mnt/hdd/swe-conform-repositories \
  --workspace-root /mnt/ssd/swe-conform-workspaces \
  --output-dir output/repository-selection
```

The HDD and SSD mount points must be available, and the SSD workspace root must
exist before the filter starts.

The same two commands accept `--limit 20` for a cache-backed pilot. Re-running
`fetch` skips snapshot refs already present in the cache and retries missing or
failed repositories. Per-repository acquisition results are written to
`output/repository-cache/fetch_results.jsonl` by default.

## HDD-backed Markdown classification

The production per-file pipeline first applies the mechanical Markdown filename
and content filters directly to the pinned bare repositories:

```bash
uv run --frozen python src/main.py audit-markdown-filenames \
  --input-dir docs/repository-candidates \
  --output-dir output/markdown-candidates \
  --cache-root /mnt/hdd/swe-conform-repositories \
  --cache-only \
  --skip-incomplete-repositories \
  --exclude-repository revanced/revanced-patches \
  --workers 16
```

It then reads each selected blob from the same pinned snapshot and submits one
file per model request:

```bash
uv run --frozen --env-file .env python src/main.py classify-markdown run-cache \
  --candidate-csv output/markdown-candidates/markdown_filename_files.csv \
  --repository-summary-csv output/markdown-candidates/repository_filename_summary.csv \
  --output-dir output/markdown-classification \
  --cache-root /mnt/hdd/swe-conform-repositories \
  --skip-incomplete-repositories \
  --exclude-repository revanced/revanced-patches \
  --provider bedrock \
  --bedrock-region us-east-1 \
  --workers 16
```

Both stages verify every Git object reachable from `lastCommitSHA` before
processing a repository. They never fall back to GitHub. Incomplete and
explicitly excluded repositories are recorded in `skipped_repositories.csv`.
Classification records Markdown files larger than `--max-input-bytes` as
`input_too_large` and leaves their repository in `review`; it neither truncates
nor submits those files. Reusing an output directory is allowed only when the
candidate inputs, prompt, schema, model settings, size limit, and cache-safety
options are unchanged.

The default execution uses four workers, `gpt-5.6-luna`, and `max` reasoning
effort. Use `--workers`, `--model`, and `--reasoning-effort` to make an explicit
experimental configuration. Each worker evaluates one repository with an
independent Git checkout and Codex CLI process. Repository checkout has a
15-minute timeout by default and can be changed with
`--checkout-timeout-seconds`. Model evaluation has a 30-minute timeout by
default and can be changed with `--model-timeout-seconds`. HDD acquisition has
a one-hour per-repository timeout and can be changed with
`--fetch-timeout-seconds`.

The default candidate set contains 4,935 repositories whose latest recorded
commit is on or after 2026-01-01. The source CSV files were collected on
2026-08-07 at approximately 15:00 JST. The candidate `lastCommitSHA` is the
reproducible snapshot identifier. Input rows before `2026-01-01T00:00:00Z` are
rejected. There is no upper bound on `lastCommit`. Acquisition does not query
the current moving default branch.

Use `--allow-out-of-window-snapshots` only to replay a revision-pinned
experiment whose recorded inputs predate the current collection window. The
run configuration records whether the window was enforced.

## Resume and outputs

Each repository result is appended immediately to `results.jsonl`. Re-run the
same command with the same output directory to skip terminal results. Retrieval
and model errors are retried. A changed model, reasoning effort, input hash, or
classification setting requires a new output directory.

Each run materializes:

- `all_classified.csv`: all latest processed results
- `guideline_review.csv`: guideline non-passes and errors
- `selected_repositories.csv`: guideline candidates for manual scope and license review
- `guideline_files.csv`: one row for every verified guideline file
- `guideline-files/`: exact copies of all verified guideline files
- `summary.json`: status counts, model calls, stage timings, and model token usage
- `run_configuration.json`: reproducibility metadata and input hashes

Each result records separate checkout and model elapsed seconds. The command
also reports requested, skipped, and evaluated repositories and total elapsed
wall-clock seconds when it exits.
