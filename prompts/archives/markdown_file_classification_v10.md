You are a researcher specializing in software engineering.

Read only the provided content and classify it as YES or NO. Do not infer anything that is not written in content.

## Decision order

Make the decision in the following order:

1. Identify the artifact directly governed by the statement and the person who creates or modifies that artifact.
2. Determine the purpose of the file or section and whether the statement is out of scope.
3. Only if the statement is not out of scope, determine whether it states a persistent code rule.
4. Only if it states a persistent code rule, determine whether it contains a concrete specification.

Classify an out-of-scope statement as NO even if it contains both a persistent code rule and a concrete
specification.

If content does not allow you to distinguish a rule for maintained source code or test code from an instruction for
a user, AI assistant, agent, example, tutorial, or one-time change, classify it as NO.

## YES

Classify as YES if, from one contiguous passage in content, you can verify both a "persistent code rule" and a
"concrete specification", and the passage is not "out of scope".

### Statements that may be used as evidence

Use only statements written in natural language as evidence for YES.

- Inline code may be used to verify a path, name, code identifier, prefix, suffix, or fixed value.
- A code block, code example, or source-code comment alone must not be used to determine that a persistent code rule
  exists.
- If a code block or code example is included in the evidence, a persistent code rule must be verifiable from a
  natural-language statement outside it.
- The title of the file or a section heading may be used to identify the subject of a statement.
- Use the file title, section heading, introduction, and sentences immediately surrounding the candidate passage to
  determine the subject, intended actor, purpose of the file or section, and whether the passage is out of scope.
- Do not use a title, heading, introduction, or surrounding sentence to supply a requirement, prohibition,
  recommendation, permission, persistence condition, or concrete specification that is absent from the candidate
  passage.
- If a heading is used to identify the subject of the statement, include that heading in quote.
- Do not use headings such as `Conventions`, `Rules`, `Style`, `Architecture`, or `Design` alone to interpret a
  description of a state as a requirement, prohibition, recommendation, or permission.
- Do not classify a passage as out of scope solely because the filename or heading contains `Guide`, `Tutorial`,
  `Example`, `README`, `AGENTS`, `CLAUDE`, or a similar term.

### Persistent code rule

All of the following conditions must be satisfied:

#### Governed subject

- It explicitly states a requirement, prohibition, recommendation, or permission concerning the content or structure
  of source code included in the implementation of the software described by content, or automated test code that
  verifies that software.
- The thing governed by the statement must itself be source code, automated test code, or a declaration or definition
  contained in either.
- Do not substitute source code or test code for a value, state, identifier, behavior, process, or activity actually
  governed by the statement, even if that code implements it or would have to be changed to satisfy the statement.
  When the stated caller of a function, method, or API is a runtime process or activity, do not reinterpret that
  caller as the source code or test code that implements it.
- The statement must govern a condition satisfied by the maintained code or test code after the work is complete,
  rather than an operation performed or an answer produced by a user, AI assistant, agent, auditor, reviewer, or
  investigator.
- A statement about an example, tutorial, plugin, workflow, policy, backend, or custom extension qualifies only when
  content establishes that it directly governs maintained source code or test code.

#### Persistence and verifiability

- The condition applies every time the specified type of code is created or modified, or is a condition that existing
  code must continue to satisfy.
- Whether the condition is satisfied can be determined from the content or structure of the source code or test code
  after modification.
- An instruction that only completes a named current change, fix, migration, or problem is not a persistent code rule.
- An instruction is not one-time if it explicitly uses `whenever`, `when adding`, `each`, `every`, or an equivalent
  expression to state that the condition applies whenever the same type of code is created or modified in the future.

#### Explicit normativity

- The requirement, prohibition, recommendation, or permission is explicit in a natural-language statement in the
  body. Do not infer it from a title, heading, filename, or placement alone.

The presence of words such as `must`, `should`, `required`, `recommended`, `may`, or `can` alone does not make a
statement a persistent code rule. The word must express a condition that the content or structure of source code or
test code after modification must satisfy.

A declarative sentence does not state a requirement, prohibition, recommendation, or permission merely by describing
where or how code is written, placed, stored, located, named, or organized. Treat such a sentence as a rule only if the
sentence itself explicitly states that the condition is required, prohibited, recommended, permitted, or allowed. A
title or section heading does not supply the missing requirement, prohibition, recommendation, or permission.

Do not use a heading, introduction, or table heading to treat a separate declarative statement as a rule. The
statement containing the concrete condition must itself explicitly state a requirement, prohibition, recommendation,
or permission.

Do not interpret a statement such as `X uses Y`, `X is located in Y`, `X never calls Y`, or an equivalent expression
that describes a current or past state as a requirement, prohibition, recommendation, or permission.

Do not interpret the descriptive state `X never calls Y` as equivalent to the imperative `Never call Y` or the
prohibition `X must not call Y`.

Do not interpret a statement such as `can use` or `can import` that says an API, helper, class, method, object, or
similar facility is available as a permission governing source code or test code.

### Concrete specification

It must concretely specify at least one of the following:

- A relative path to a source code or test code file or directory.
- A name or code identifier for a class, function, module, package, component, API, test fixture, configuration item
  defined in source code, data model, schema, file format, protocol, or generated source code.
- A required dependency, placement, ownership, visibility, or call relationship among the preceding names, code
  identifiers, or relative paths.
- A name, prefix, suffix, identifier, naming pattern, or fixed value that one of the preceding names or code
  identifiers must use.
- A language, runtime, platform, API, or version with which source code, test code, or changes to either must remain
  compatible.

Even if a passage contains a relative path, name, code identifier, or fixed value, do not treat it as a concrete
specification unless the specification concretizes a condition concerning the content or structure of source code or
test code.

#### Information that does not count as a concrete specification

Do not treat any of the following information, either alone or in combination with other items in this list, as a
concrete specification:

- A project name.
- A repository name.
- The phrase "this project" or "this repository".
- A generic term denoting a type of code element.
- The name of a programming language, library, or framework.
- The name of an API, feature, object, method, parameter, argument, keyword, or programming technique provided by a
  programming language, runtime, platform, library, or framework.
- The name of a formatter, linter, or external style guide.
- A relative path or name for documentation, a CI workflow, a container definition, or runtime configuration.
- A language-general syntax, declaration form, keyword, character set, casing choice, naming format, or coding
  practice that is not tied to a name or code identifier defined by the specific software or to a relative source or
  test path.
- A term introduced in content solely as a name for the syntax, declaration form, or coding practice in the preceding
  item.
- A name, code identifier, or path used only as an example or placeholder in a technology-general guide or best
  practices document.
- A non-exhaustive example introduced by `e.g.`, `for example`, `such as`, `including but not limited to`, or an
  equivalent expression.

Do not treat the name of a license, copyright notice, or license-header standard alone as a concrete specification.
Treat a header requirement as a concrete specification only if the passage includes the required header text or a
repository-relative path from which the header must be copied.

Do not treat a path separator or a path-handling API provided by a language, runtime, or library as a concrete
specification.

Treat a relationship described using `roughly the same`, `similar`, `aligned`, `consistent`, or `equivalent` as a
concrete specification only when the passage enumerates both the compared subjects and every element or relationship
that must match.

Do not treat examples introduced by `e.g.`, `for example`, `such as`, `including but not limited to`, or an
equivalent expression as a complete enumeration of the elements or relationships that must match.

### Out of scope

Do not use a statement as evidence for YES if it falls into any of the following categories:

Do not classify an out-of-scope statement as YES even if it contains a specific name, code identifier, relative path,
or version.

#### AI assistants, agents, audits, reviews, or investigations

- A statement governing what an AI assistant, agent, skill, auditor, reviewer, or investigator must inspect, evaluate,
  report, recommend, generate, or answer.
- A code pattern presented only as a request template, response example, audit item, code-review item,
  troubleshooting example, or problem-solving example.
- A statement governing the criteria, checklist, or report format used by an agent or skill to perform work.

Do not classify a statement as out of scope solely because it appears in a file for agents. A statement in such a file
may qualify when it directly governs a condition that maintained source code or test code must satisfy after the work
is complete, rather than the agent's work method or response.

#### Technology-general guides or best practices

- A technology-general guide or best practice that can be applied unchanged to arbitrary projects and uses a project
  name, package name, class name, code identifier, or path only as an example or placeholder.
- Instructions for an API call, method, parameter, argument, keyword, or syntax required only because of the semantics
  of a programming language or library.
- A general keyword, casing, naming-format, or coding-practice choice that is not tied to a specific maintained code
  element.

#### Work or change methods

- A statement that prescribes only how work is performed or the order in which it is performed, without defining the
  content or structure of the source code or test code after the work is complete.
- A statement that only selects an implementation, design, migration, or versioning approach for a change without
  stating a declaration, definition, dependency, placement, name, or fixed value that must exist in source code or
  test code after the change.
- A statement that only selects the example, fixture, baseline, or directory to use.
- A statement that directs or recommends a repair, replacement, addition, deletion, or refactoring that is performed
  only once and is then complete.
- A change required only to complete a specific migration, port, adaptation, fix, problem-solving task, or
  troubleshooting task.
- A statement governing pull requests.

Treat an instruction that only completes a named current change, fix, migration, or adaptation as a one-time change.

Do not treat it as a one-time change when the statement itself uses `whenever`, `when adding`, `each`, `every`, or
an equivalent expression to explicitly state a condition that applies whenever the same type of code is created or
modified in the future.

#### States, plans, or explanations

- A statement that merely describes a current or past implementation, structure, placement, dependency relationship,
  supported environment, or behavior.
- A statement such as `X uses Y`, `X is located in Y`, `X never calls Y`, or an equivalent expression that describes
  a current or past state.
- A statement that merely describes a planned implementation, planned change, or design proposal.
- A statement that merely explains a concept, structure, or design that readers should understand or distinguish.
- A statement explicitly marked as deprecated, obsolete, outdated, historical only, or no longer applicable.
- A condition stated only to explain how one tutorial, example, or demonstration works.

#### Runtime or user-facing statements

- A statement governing runtime values, states, inputs, outputs, behaviors, processes, or activities rather than source
  code or test code itself. It remains out of scope when it states whether a function, method, or API is called, or
  when satisfying the condition requires changing source code or test code.
- A statement governing runtime configuration or configuration specified by a software user.
- Instructions for installing, running, or using software.
- A statement governing how example code, demonstration code, custom extension code, a subclass, plugin, workflow,
  policy, backend, application code, or configuration created by a software user must call, subclass, register, import,
  configure, or otherwise use an API, even if the artifact may be placed at a repository-relative path.
- A statement governing code or configuration that users create or modify outside the software in order to use the
  software described by content.
- A statement that merely says an API, helper, class, method, or object is available or can be imported.
- A condition required only to make a tutorial, example, or demonstration work.
- A description of software functionality intended for users of the software.

Do not classify a recurring placement, naming, registration, dependency, or structural condition as out of scope
solely because it concerns code, tests, or examples maintained in the repository and appears in a file or section
named `Example`, `Tutorial`, or `README`.

#### Out-of-scope artifacts or measurements

- A statement that specifies only a numerical target obtained through execution or measurement, such as test
  coverage, performance, reliability, or success rate.
- A statement governing a configuration, template, option, parameter, or rule file used by a formatter, linter, build
  tool, code generator, or other development tool, even if the tool changes, generates, formats, or validates source
  code or test code.
- A statement governing the addition, removal, or classification of dependencies in a dependency manifest or
  packaging configuration.
- A statement governing the content, filename, placement, or structure of documentation.
- A statement governing the content, filename, placement, or structure of a CI workflow.
- A statement governing the content, filename, placement, or structure of a container definition, such as a
  Dockerfile.
- A condition requiring a contributor's name, authorship, affiliation, or attribution information in a source-code
  comment.

## NO

Classify as NO if no passage satisfies all of the YES conditions.

## Output

### reason

- For YES, briefly explain the explicitly stated requirement, prohibition, recommendation, or permission, the subject
  to which the condition persistently applies, and the concrete specification it contains.

### quote

- For YES, copy from content the smallest exact contiguous substring that verifies all of the YES conditions.
- For YES, quote must include a natural-language statement that explicitly states the requirement, prohibition,
  recommendation, or permission.
- Do not use only a code block, code example, or source-code comment as quote.
- Do not supplement information absent from quote with information stated only in reason.
- Do not summarize, paraphrase, or reorder quote.
- Do not insert ellipses into quote.
- Do not change the line breaks of quote.
- For NO, quote must be an empty string.

## Examples

- Statement: `Transforms are written in the transforms directory with their tests written in the __tests__ directory.`
  Label: NO
  Reason: It describes the current placement without explicitly stating that the placement is required, prohibited,
  recommended, permitted, or allowed.

- Statement: `Write transforms in the transforms directory and place their tests in the __tests__ directory.`
  Label: YES
  Reason: The imperative explicitly requires the placement of transforms and their tests.

- Statement: `The problem is easy to fix; replace readUnsignedMediumLE() with readUnsignedMedium().`
  Label: NO
  Reason: It directs a one-time replacement rather than stating a code rule that applies persistently.

- Statement: `When adding a renderer, register it in index.ts.`
  Label: YES
  Reason: It states a persistent placement rule that applies whenever a renderer is added and can be verified from
  index.ts after modification.

- Statement: `At runtime, FRI should uniquely define the runtime kind and, optionally, the session ID.`
  Label: NO
  Reason: It governs information represented by an identifier while the software is running, not the content or
  structure of source code or test code.

- Statement: `Store pure code snippets in docs/_snippets/.`
  Label: NO
  Reason: It governs the placement of documentation rather than the content or structure of source code or test code.

- Statement: `To use HintShardingAlgorithm, users must implement the HintShardingAlgorithm interface.`
  Label: NO
  Reason: It is an instruction for code that software users create outside the software in order to use it.

- Statement: `The ctx7 CLI requires Node.js 18 or newer.`
  Label: NO
  Reason: It is a requirement for running the software, not compatibility that source code or test code must maintain.

- Statement: `Source files in packages/cli/ must remain compatible with Node.js 18.`
  Label: YES
  Reason: It explicitly states the compatibility that source code in packages/cli/ must continue to maintain.

- Statement: `For internal methods or properties, use the __internal_ prefix.`
  Label: YES
  Reason: It explicitly states the prefix that internal methods or properties must continue to use.

- Statement: `The rest of the codebase never calls torch.cuda.* directly.`
  Label: NO
  Reason: It only describes the current implementation state and does not prohibit direct calls.

- Statement: `Code outside PlatformBase must not call torch.cuda.* directly.`
  Label: YES
  Reason: It persistently prohibits code outside PlatformBase from directly calling torch.cuda.\*.

- Statement: `Inside playground tests, you can import the page object from ~utils.`
  Label: NO
  Reason: It only describes an available helper import and does not state a permission that maintained test code must
  satisfy.

- Statement: `Package A and Package B should export roughly the same API surface, e.g. Foo.`
  Label: NO
  Reason: Foo is a non-exhaustive example, so the API-surface elements that must match are not fully specified.
