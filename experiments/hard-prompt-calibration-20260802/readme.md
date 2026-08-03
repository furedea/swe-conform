# Current-Prompt Hard-Case Pilot

This directory records one repository-wide project-guideline classification
run over 20 fixed, difficult repository revisions. The run used the prompt in
`prompt.txt`, the signed-in Codex CLI, `gpt-5.6-luna`, maximum reasoning
effort, and classification contract
`8c077d5ffe1cdeaeac9884477d61757e49306db756cbfd7567baa360a927e85c`.

## Input

The input contains five repositories from each of Java, JavaScript, Python,
and TypeScript. `input/candidates.csv` is revision-pinned and has SHA-256
`6517bd70972e779f6687176e5fec3aaa9173fc837b3d1c61c5aadb9cb5f8eb32`.

## Execution

The initial run evaluated all 20 repositories with four workers and a
300-second checkout timeout. It produced 15 `pass`, three `review`, one
`not_found`, and one `model_error`. The error was a checkout timeout for
`Azure/Azure-Sentinel`.

That repository was retried once under the same classification contract with
one worker and a 900-second checkout timeout. The retry returned `pass`.
`final_results.jsonl` replaces only the failed Azure classification while
preserving its original input index.

## Final Results

| Status      | Count |
| ----------- | ----: |
| `pass`      |    16 |
| `review`    |     3 |
| `not_found` |     1 |

The 21 model calls used 3,386,956 input tokens, 53,107 output tokens, and
3,440,063 total tokens.

## Artifacts

- `prompt.txt`: exact classification prompt
- `input/candidates.csv`: the 20 revision-pinned inputs
- `initial_results.jsonl`: unmodified initial results
- `retry_result.jsonl`: unmodified Azure retry result
- `final_results.jsonl`: final 20 results after the documented replacement
- `run_configuration.json`: initial and retry configurations and merge rule
- `summary.json`: final distribution and aggregate usage

This pilot records model predictions, not classifier accuracy. It has no
manually adjudicated gold labels. License fields in the raw records are
retained as execution provenance but are not part of this guideline result.
