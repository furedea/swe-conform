# ADR-0012: Collect stratified repositories until the positive target

- Status: Accepted
- Date: 2026-08-13

In the context of constructing a 120-repository benchmark where 34 repositories
from two prior stratified samples are already human-confirmed, facing the cost
of classifying every repository in the remaining corpus and the need to preserve
the four-language sampling design, we decided to exclude all 70 repositories in
the prior samples, create one deterministic random order within each language
stratum, classify complete four-language rounds from that fixed schedule, and
stop after selecting 86 additional repositories with at least one `pass` or
`review` file, and against classifying the complete corpus or drawing a new
sample after each manual-review round, to make the stopping process reproducible
and allow human-rejected repositories to be replaced from the same schedule,
accepting that the sequential positive sample is appropriate for benchmark
construction but not for estimating guideline prevalence and that the final
selected repositories need not be balanced by language.
