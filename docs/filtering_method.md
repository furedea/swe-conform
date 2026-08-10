# Repository Filtering Method

## Scope

The automated filter selects repositories that contain natural-language
statements that directly constrain the content, structure, or behavior of
source code or test code. Statements governing developer actions or pull
requests do not qualify. Human reviewers decide whether qualifying guidance is
specific to the project. All repository evidence is evaluated at the candidate
CSV's `lastCommitSHA` rather than the moving default branch.

## Stage 1: Guideline candidate screening

The acquisition stage fetches the candidate CSV's exact `lastCommitSHA` into a
persistent bare repository cache. It uses neither shallow history nor partial
clone filters, so the cache contains the commit ancestry, trees, and ordinary
blobs reachable from the snapshot revision. It does not fetch descendants of
that revision. Input `lastCommit` timestamps before `2026-01-01T00:00:00Z` are
rejected. No upper timestamp bound is enforced.

The classification stage makes a shared local clone from the HDD cache into a
disposable SSD workspace, checks out the exact revision, and removes its Git
metadata before Codex starts. Codex CLI starts in the workspace root, with the
source snapshot nested under `repository/`, and searches the complete snapshot
instead of receiving a path-ranked document subset. No repository download
occurs during this cache-backed classification stage.

The direct-download workspace remains available for small pilots. The research
run uses the cache-backed workspace so network acquisition and model exploration
are separate experimental stages.

Codex uses `gpt-5.6-luna` with maximum reasoning effort. Each invocation runs
inside a disposable Docker container with a read-only root filesystem, dropped
Linux capabilities, no privilege escalation, and a process limit. The host
mounts only the source snapshot as read-only, a temporary output directory,
and a temporary Codex home containing selected runtime authentication files.
The real user home and Docker socket are not mounted.

Every model-generated command runs through the image's system bubblewrap. The
Codex-bundled bubblewrap is removed at image build time, and no Landlock or
unsandboxed fallback is configured. A fixed permission profile extends Codex's
read-only profile, disables approvals, and denies tool reads of the temporary
Codex home. Browser, plugin, skill-search, multi-agent, memory, and other
optional harness features are disabled. The shell inherits only Codex's core
environment.

Before any repository submission, preflight verifies the system bubblewrap
binary, a Codex sandbox launch, repository reads, repository write denial,
credential read denial, and direct network denial. Any failed probe aborts the
batch. The outer container retains network connectivity for Codex model
requests, while bubblewrap gives model-generated tools a separate network
namespace. The prompt independently treats every repository file as untrusted
evidence and prohibits file changes and network access.

The request uses strict JSON Schema with two outcomes:

- `pass`: the repository contains at least one file with a natural-language
  statement that directly constrains its source code or test code
- `not_found`: no qualifying file can be verified after repository-wide
  exploration

The model does not decide whether guidance is project-specific or reusable
across unrelated projects. That distinction belongs to human scope review. The
classifier does not infer unstated guidance from source code or configuration.
It searches without limiting candidate file names or locations in advance and
uses the title, headings, introduction, and body when interpreting a file.

A model `pass` must include one evidence item for every qualifying file it
finds. Each item contains a repository-root-relative path and one short,
self-contained verbatim quote that supports the file classification; it does
not enumerate every rule in the file. The filter downgrades the result to
internal `review` if any path is duplicated, escapes the snapshot, is not a
file, or does not contain the quote as an exact substring. Every verified file
is copied byte-for-byte into the run output.

License metadata from the candidate CSV is preserved unchanged in output
reports. It is not classified and cannot exclude a repository. Project scope
and license eligibility are reviewed manually after automated classification.

## Reproducibility and recovery

The run configuration records the model, reasoning effort, Codex runtime,
Docker image tag and content ID, Codex and Git commands, worker count, snapshot
cutoff, repository source, workspace roots, checkout and model timeouts, filter
order, SHA-256 digest of every input CSV, and a SHA-256 digest of the
classification prompt and schema. The output directory cannot be resumed with
a conflicting configuration.

Repository evaluations run in a fixed-size thread pool. Each worker launches
its own Git and Codex CLI subprocesses. The default worker count is four and can
be changed with `--workers`. Each checkout has a 900-second timeout by default.

An append-only JSONL checkpoint is written after each repository. Reports use
the latest record for a repository revision and restore the original input
order. Retrieval and model errors remain retryable on later runs. Each result
records separate checkout and model elapsed seconds. `summary.json` records
their sums and aggregate input, output, and total model token usage.

## Limitations

The agent can still miss guidelines or misclassify their scope. Network access
is disabled, so evidence available only on external websites cannot qualify.
Results should be validated against an independently labeled sample before
they are used as benchmark ground truth. Git LFS payloads and submodule working
trees are not materialized; ordinary Git blobs and the pinned repository's own
working tree are the inspection boundary.
