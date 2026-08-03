# ADR-0005: Run Codex in containers

- Status: Accepted
- Date: 2026-08-03

In the context of repository-wide exploration over untrusted OSS snapshots on
Linux 5.4 research servers, facing the need to keep model access while denying
tool access to the host, credentials, writes, and the network, we decided for a
restricted Docker container plus a required system bubblewrap sandbox and
against Landlock, bundled-sandbox fallback, or unsandboxed continuation, to
make the boundary portable, testable, and fail-closed, accepting image build
overhead and a mandatory per-host preflight.
