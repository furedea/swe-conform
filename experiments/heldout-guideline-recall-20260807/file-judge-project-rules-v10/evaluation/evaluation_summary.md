# Markdown classification evaluation

> Metrics cover only the manually reviewed checklist subset.

## Scope

| Item | Count |
| --- | ---: |
| Checklist files | 727 |
| Human-labeled files | 727 |
| Resolved model decisions | 726 |
| Review decisions | 1 |
| Model errors | 0 |
| Missing predictions | 0 |

## Confusion matrix

| Human \ LLM | pass | not_found | review | model_error | missing |
| --- | ---: | ---: | ---: | ---: | ---: |
| pass | 56 | 12 | 1 | 0 | 0 |
| not_found | 0 | 658 | 0 | 0 | 0 |

## Metrics

Resolved accuracy uses only pass and not_found predictions. Strict accuracy uses every human-labeled file. Review, model_error, and missing predictions count as incorrect.

| Metric | Value | Definition |
| --- | ---: | --- |
| Resolved accuracy | 0.9835 | Agreement among pass and not_found predictions. |
| Strict accuracy | 0.9821 | Agreement across every human-labeled file. |
| Resolution rate | 0.9986 | Share receiving a pass or not_found prediction. |
| Precision | 1.0000 | Share of predicted pass files that humans labeled pass. |
| Recall | 0.8235 | Share of human pass files predicted as pass. |
| Specificity | 1.0000 | Share of human not_found files predicted as not_found. |
| F1 | 0.9032 | Harmonic mean of precision and recall. |
| False-positive rate | 0.0000 | Share of human not_found files predicted as pass. |
| False-negative rate | 0.1765 | Share of human pass files predicted as not_found. |

## Repository breakdown

| Repository | Files | FP | FN | Review | Errors | Missing | Resolved accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| carbon-design-system/carbon | 74 | 0 | 4 | 0 | 0 | 0 | 0.945946 |
| agronholm/anyio | 1 | 0 | 1 | 0 | 0 | 0 | 0.0 |
| autonumeric/autonumeric | 2 | 0 | 1 | 0 | 0 | 0 | 0.5 |
| clerk/javascript | 57 | 0 | 1 | 0 | 0 | 0 | 0.982456 |
| destinyitemmanager/dim | 4 | 0 | 1 | 0 | 0 | 0 | 0.75 |
| kubernetes-client/python | 50 | 0 | 1 | 0 | 0 | 0 | 0.98 |
| module-federation/core | 46 | 0 | 1 | 0 | 0 | 0 | 0.978261 |
| openlayers/openlayers | 6 | 0 | 1 | 0 | 0 | 0 | 0.833333 |
| puppeteer/puppeteer | 10 | 0 | 1 | 0 | 0 | 0 | 0.9 |
| apache/incubator-kie-drools | 24 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| apache/rocketmq | 17 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| apache/shardingsphere | 158 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| c3js/c3 | 1 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| decaporg/decap-cms | 7 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| deepstreamio/deepstream.io | 1 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| fastrepl/hyprnote | 7 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| getgauge/taiko | 5 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| jetty/jetty.project | 2 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| karakeep-app/karakeep | 15 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| lexiforest/curl_cffi | 3 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| lionsoul2014/ip2region | 3 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| logisim-evolution/logisim-evolution | 4 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| modernweb-dev/web | 12 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| nodeca/js-yaml | 1 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| NVIDIA/earth2studio | 19 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| NVIDIA/TransformerEngine | 3 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| onnx/onnx | 10 | 0 | 0 | 1 | 0 | 0 | 1.0 |
| opendatalab/MinerU | 2 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| openid/appauth-android | 5 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| papermc/velocity | 2 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| parse-community/parse-server-example | 1 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| plankanban/planka | 4 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| plantuml/plantuml | 27 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| RunMaestro/Maestro | 17 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| salesforce/lwc | 15 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| shaka-project/shaka-player | 9 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| sofastack/sofa-tracer | 2 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| spesmilo/electrum | 9 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| svgdotjs/svg.js | 1 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| tensorflow/recommenders | 1 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| torakiki/pdfsam | 2 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| ui-lovelace-minimalist/ui | 23 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| upstash/context7 | 20 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| verl-project/verl | 26 | 0 | 0 | 0 | 0 | 0 | 1.0 |
| vitejs/vite-plugin-react | 19 | 0 | 0 | 0 | 0 | 0 | 1.0 |

## False positives

None.

## False negatives

- [agronholm/anyio/AGENTS.md](https://github.com/agronholm/anyio/blob/003e5d6bc3eba8f4e75bf2b2b5fb3f7dd11e6330/AGENTS.md) — confidence 9; The document contains PR/work instructions and generic Python style guidance. Although it says to always add the future import, that is a language-general coding practice rather than a concrete specification tied to a maintained source/test path or project-defined identifier; the PR, test-checklist, and changelog statements are out of scope.
- [autonumeric/autonumeric/doc/CONTRIBUTING.md](https://github.com/autonumeric/autonumeric/blob/7e7a2c3a5a03fc7ea4899f046b411926f831e82f/doc/CONTRIBUTING.md) — confidence 9; The document provides contribution workflow, testing, linting, branching, and commit instructions. Its requirements govern contributor actions, test execution results, or commit contents rather than a persistent content or structural rule for maintained source or test code with a qualifying concrete specification.
- [carbon-design-system/carbon/AGENTS.md](https://github.com/carbon-design-system/carbon/blob/79d912be2d8af31bacd73944075bab4944ef56e0/AGENTS.md) — confidence 10; No passage states a persistent requirement, prohibition, recommendation, or permission that directly governs maintained source or automated test code with a concrete code specification. The content mainly describes repository state, workflow methods, documentation, pull requests, runtime tooling, package relationships, and end-user guidance.
- [carbon-design-system/carbon/docs/developer-handbook.md](https://github.com/carbon-design-system/carbon/blob/79d912be2d8af31bacd73944075bab4944ef56e0/docs/developer-handbook.md) — confidence 9; No passage explicitly states a persistent rule for maintained source or test code together with a concrete specification. The component-location statements merely describe placement, while the explicit requirements elsewhere govern commits, workflows, runtime behavior, assets, documentation, or configuration.
- [carbon-design-system/carbon/packages/themes/src/dtcg/README.md](https://github.com/carbon-design-system/carbon/blob/79d912be2d8af31bacd73944075bab4944ef56e0/packages/themes/src/dtcg/README.md) — confidence 9; The normative-looking passages govern DTCG token JSON contents and contributor/build/documentation work, not maintained source code or automated test code. The remaining statements describe the current token structure or give one-time build and validation instructions, so no in-scope persistent code rule with a concrete specification is established.
- [carbon-design-system/carbon/packages/web-components/src/coding-conventions.md](https://github.com/carbon-design-system/carbon/blob/79d912be2d8af31bacd73944075bab4944ef56e0/packages/web-components/src/coding-conventions.md) — confidence 9; The document mostly describes current implementation practices or runtime/user behavior. Its direct advice, such as null handling, uses only generic language syntax, while the other convention-like passages lack an explicit persistent rule tied to a concrete maintained source or test identifier or path.
- [clerk/javascript/AGENTS.md](https://github.com/clerk/javascript/blob/3088d7d2f0042c4c84af81ab622cc995df99ce1d/AGENTS.md) — confidence 9; No passage both governs maintained source or test code and provides a qualifying concrete specification. The compatibility and API statements concern runtime compatibility or major-version release policy, while the other rules concern tooling, PRs, commits, changesets, or generic comment style; the comment rule has no specific source path, identifier, naming pattern, fixed value, or compatibility target.
- [destinyitemmanager/dim/docs/CONTRIBUTING.md](https://github.com/destinyitemmanager/dim/blob/2c2ec8563c5f6c042e212cc8b7ed0bca3581e00f/docs/CONTRIBUTING.md) — confidence 9; No passage states a persistent rule for maintained source or automated test code with a qualifying concrete specification. The document mainly covers PRs, setup, runtime use, and tooling; its explicit prohibition concerns the localization JSON file `src/locale/en.json`, not source or test code.
- [kubernetes-client/python/CONTRIBUTING.md](https://github.com/kubernetes-client/python/blob/cac97ce26896ab8b55fc7e20431f0b70c89be7e1/CONTRIBUTING.md) — confidence 9; No passage states a persistent, explicit rule governing maintained source or test code with a concrete specification. The apparent instructions concern contributor workflow, dependency manifests, formatter use, test execution, or descriptive test locations and naming.
- [module-federation/core/arch-doc/implementation-guide.md](https://github.com/module-federation/core/blob/641a0b6edc0f30865586e7d021522bfa27051c4c/arch-doc/implementation-guide.md) — confidence 9; No natural-language passage both establishes a persistent rule for maintained source or test code and provides a concrete code specification. The prose is implementation routing, prerequisites, one-time workflow guidance, current-state description, or runtime behavior, while the code blocks and comments cannot independently establish such a rule.
- [openlayers/openlayers/DEVELOPING.md](https://github.com/openlayers/openlayers/blob/140be96d1712cabf4f4e1d1dcc06d68500bfc34b/DEVELOPING.md) — confidence 9; The document provides developer setup, tool/editor configuration, testing and runtime instructions, and descriptions or one-time guidance for examples and package linking. It does not explicitly state a persistent rule governing maintained source or test code with a concrete specification.
- [puppeteer/puppeteer/docs/contributing.md](https://github.com/puppeteer/puppeteer/blob/33566d2dbb6485a459b9fc3826914b986c08e01e/docs/contributing.md) — confidence 9; No passage satisfies all YES conditions. The document mainly gives contributor, documentation, dependency, testing-process, runtime, or one-time maintenance instructions; the code-style statements are generic practices or govern documentation comments rather than a concrete persistent rule for maintained source or test code.
