# ADR-0009: Centralize classification experiment defaults

- Status: Accepted
- Date: 2026-08-12

In the context of repeated full-corpus Markdown classification experiments,
facing responses that exhausted the previous output limit before producing a
final answer and configuration drift from repeatedly entered command options,
we decided for one immutable set of default experiment settings while retaining
explicit CLI overrides and against relying on copied command literals, to make
the intended configuration consistent and auditable, accepting that an
overridden run is a distinct recorded configuration and cannot resume a
checkpoint created with different execution settings.
