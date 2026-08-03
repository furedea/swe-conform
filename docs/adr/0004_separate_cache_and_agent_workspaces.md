# ADR-0004: Separate cache and agent workspaces

- Status: Accepted
- Date: 2026-08-03

In the context of repository-wide guideline discovery followed by commit-level
mining, facing the need for both complete historical objects and network-free
agent execution, we decided for full revision-anchored bare caches on HDD and
source-only disposable workspaces on SSD and against shallow per-run clones or
full Git repositories inside agent workspaces, to preserve reproducible history
while isolating exploration from acquisition, accepting additional HDD storage
and a two-stage operating procedure.
