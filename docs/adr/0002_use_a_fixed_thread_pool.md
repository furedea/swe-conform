# ADR-0002: Use a fixed thread pool

- Status: Accepted
- Date: 2026-08-02

In the context of evaluating thousands of repositories through Git and Codex
CLI subprocesses, facing a workload dominated by external-process and network
waits, we decided for a fixed thread pool with four workers by default and
against multiprocessing or an asyncio rewrite, to overlap independent waits
while retaining one coordinator for checkpoint writes, accepting that the best
worker count depends on Codex limits and network conditions and must be measured
before increasing it.
