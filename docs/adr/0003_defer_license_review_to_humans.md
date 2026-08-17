# ADR-0003: Defer license review to humans

- Status: Superseded
- Date: 2026-08-02
- Superseded by: ADR-0019

In the context of screening project-guideline candidates before human
validation, facing a requirement that license eligibility remain a manual
decision, we decided for preserving input license metadata without classifying
or filtering it and against automatic SPDX or OSI exclusion, to prevent silent
candidate removal by a policy outside the classifier contract, accepting the
additional manual-review workload.
