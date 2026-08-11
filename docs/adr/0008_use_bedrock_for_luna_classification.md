# ADR-0008: Use Amazon Bedrock for Luna classification

- Status: Accepted
- Date: 2026-08-11
- Supersedes: ADR-0007

In the context of provider-neutral per-file GPT-5.6 Luna classification,
facing access to institution-funded Amazon Bedrock usage while Luna is
available there only through the on-demand Responses API, we decided for
Amazon Bedrock Mantle as the primary execution provider and against OpenRouter
as the primary provider or an unsupported Bedrock Batch path, to preserve the
existing reproducible request and checkpoint pipeline while charging the
experiment to the institutional AWS account, accepting on-demand pricing and
locally calculated costs from provider-reported token usage.
