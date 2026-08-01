# Repository Filtering Method

## Scope

The filter selects repositories that contain concrete project coding
guidelines and have a recognized OSI-approved software license. It intentionally
favors precision over recall. All repository evidence is evaluated at the
candidate CSV's `lastCommitSHA` rather than the moving default branch.

## Stage 1: Project coding guideline

The GitHub recursive tree API provides file paths and blob identifiers for the
pinned revision. Deterministic path rules rank up to 12 likely documents. The
selector considers root and documentation files associated with contributing,
development, coding style, style guides, hacking, guidelines, and README files.

The selector excludes generated, vendored, third-party, fixture, snapshot, and
test-data directories. It also excludes agent instruction files, changelogs,
codes of conduct, release documents, and security policies. Files larger than
200,000 bytes are not sent to the model.

When candidate documents exist, the filter makes one structured model call per
repository. The default provider is the OpenAI Responses API. The optional
Codex CLI provider runs in a temporary empty directory with user configuration,
rules, persistence, and project instructions disabled. Both providers default
to `gpt-5.6-luna`; Codex defaults to maximum reasoning effort. The request uses
strict JSON Schema with these outcomes:

- `pass`: a concrete normative rule applies to implementing or modifying source
  code or tests
- `review`: evidence is ambiguous or incomplete
- `not_found`: supplied documents contain no qualifying rule

Formatting, linting, naming, imports, type usage, compatibility, source
structure, function, method, class, and test-authoring requirements qualify. A
requirement to add tests for implementation changes qualifies, as does an
explicitly required and named external coding standard. Merely running existing
checks does not qualify.

Contribution workflow, issue and pull-request process, commit messages, release
notes, documentation-only style, license terms, security reporting, consumer API
documentation, and vague requests to follow existing style do not qualify.
Tool badges and metadata are not contributor requirements.

An unnamed mandatory linter or formatter is `review` unless the document states
a concrete rule. A linked but unavailable developer, coding, or style guide is
also `review`. Generic contribution, setup, and build links do not create that
uncertainty and remain `not_found` when no qualifying rule exists.

A model `pass` must include a repository path and a verbatim quote. The filter
downgrades the result to `review` if the path was not retrieved or the quote is
not an exact substring of that document. A `not_found` result from a truncated
Git tree is also downgraded to `review`.

## Stage 2: OSS license

Only guideline `pass` rows enter this stage. GitHub's license label is mapped to
an SPDX identifier using the labels present in the candidate dataset. The
mapping is pinned to SPDX License List 3.28.0.

- `pass`: the SPDX entry has `isOsiApproved=true`
- `exclude_non_osi`: SPDX recognizes the license without OSI approval
- `review_unrecognized`: the label is empty, `Other`, or unknown
- `not_evaluated`: the guideline stage did not pass

Unrecognized licenses are not assumed to be open source.

## Reproducibility and recovery

The run configuration records the model, reasoning effort, API base URLs,
document limit, filter order, SPDX source, SHA-256 digest of every input CSV,
and a SHA-256 digest of the classification prompt and schema. The output
directory cannot be resumed with a conflicting configuration.

An append-only JSONL checkpoint is written after each repository. Reports use
the latest record for a repository revision and restore the original input
order. Retrieval and model errors remain retryable on later runs. `summary.json`
records aggregate input, output, and total model token usage.

## Limitations

The deterministic document selector can miss rules stored under unrelated file
names or only on external websites. Large documents are represented by bounded
head and tail excerpts in the model request. The one-shot classifier should be
validated against an independently labeled sample before its outputs are used
as benchmark ground truth.
