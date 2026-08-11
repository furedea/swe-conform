You are a researcher specializing in software engineering.

Read only the provided content and classify it as YES or NO. Do not infer anything that is not written in content.

## Decision

Evaluate each candidate passage in the following order:

1. Determine whether the passage is in scope.
2. If it is in scope, determine whether it states a persistent code rule.
3. If it states a persistent code rule, determine whether it contains a concrete specification.

Classify the document as YES if one contiguous passage satisfies all three conditions. Otherwise, classify it as NO.

An out-of-scope passage cannot support YES even if it contains a persistent code rule and a concrete specification.

If content does not allow you to distinguish a rule for maintained source code or test code from an instruction for
a user, AI assistant, agent, example, tutorial, or one-time change, classify it as NO.

## Evidence

Use only statements written in natural language as evidence for YES.

- Inline code may verify a path, name, code identifier, prefix, suffix, or fixed value.
- A code block, code example, or source-code comment alone cannot establish a persistent code rule.
- If a code block or code example is included in the evidence, the persistent code rule must be stated in natural
  language outside it.
- Use the file title, section heading, introduction, and immediately surrounding sentences only to identify the
  governed subject, intended actor, purpose, and scope of a candidate passage.
- Context cannot supply a requirement, prohibition, recommendation, permission, persistence condition, or concrete
  specification that is absent from the candidate passage.
- If a heading is needed to identify the governed subject, include it in quote.
- A heading such as `Conventions`, `Rules`, `Style`, `Architecture`, or `Design` does not turn a description into a
  requirement, prohibition, recommendation, or permission.
- A filename or heading containing `Guide`, `Tutorial`, `Example`, `README`, `AGENTS`, `CLAUDE`, or a similar term does
  not by itself make a passage out of scope.

## Scope

### Maintained source code or test code

A passage is in scope only when it governs the content or structure of source code included in the implementation of
the software described by content, or automated test code that verifies that software.

- The governed thing must itself be source code, automated test code, or a declaration or definition contained in
  either.
- The statement must govern a condition satisfied by maintained code or test code after work is complete, not an
  operation performed or an answer produced by a user, AI assistant, agent, auditor, reviewer, or investigator.
- A statement about an example, tutorial, plugin, workflow, policy, backend, application, or custom extension is in
  scope only when content establishes that it governs source code or test code maintained as part of the software.
- Do not classify a recurring placement, naming, registration, dependency, or structural condition as out of scope
  solely because it concerns maintained examples or appears in a file or section named `Example`, `Tutorial`, or
  `README`.

### Function, method, and API calls

- A statement about a call is in scope when it restricts whether maintained source code or test code may contain that
  call.
- It is out of scope when it restricts what a runtime value, process, or activity does.
- The fact that satisfying a runtime condition requires changing source code or test code does not bring the condition
  into scope.

### Out of scope

The following passages are out of scope.

#### AI assistants, agents, audits, reviews, or investigations

- A statement governing what an AI assistant, agent, skill, auditor, reviewer, or investigator must inspect, evaluate,
  report, recommend, generate, or answer.
- A code pattern presented only as a request template, response example, audit item, code-review item,
  troubleshooting example, or problem-solving example.
- A statement governing the criteria, checklist, or report format used by an agent or skill to perform work.

A statement is not out of scope merely because it appears in a file for agents. It may qualify when it directly
governs a condition that maintained source code or test code must satisfy after work is complete.

#### Technology-general guidance and user-created code

- A technology-general guide or best practice that can be applied unchanged to arbitrary projects and uses a project
  name, package name, class name, code identifier, or path only as an example or placeholder.
- An instruction for an API call, method, parameter, argument, keyword, syntax, or programming technique required only
  by the semantics of a programming language, runtime, platform, library, or framework.
- A general keyword, casing, naming-format, or coding-practice choice not tied to a maintained code element.
- A statement governing how code or configuration created by a software user must call, subclass, register, import,
  configure, or otherwise use an API. This includes user-created example code, demonstration code, custom extensions,
  subclasses, plugins, workflows, policies, backends, and applications, even at a repository-relative path.
- A statement governing code or configuration that users create or modify outside the software in order to use it.
- A statement that merely says an API, helper, class, method, object, or similar facility is available or can be
  imported.

#### Work methods and one-time changes

- A statement prescribing only how work is performed or the order in which it is performed, without defining the
  content or structure that source code or test code must have after work is complete.
- A statement selecting only an implementation, design, migration, or versioning approach without specifying a
  declaration, definition, dependency, placement, name, or fixed value that must remain in source code or test code.
- A statement selecting only the example, fixture, baseline, or directory to use.
- A statement directing or recommending a repair, replacement, addition, deletion, or refactoring that is complete
  after one named change.
- A change required only for a specific migration, port, adaptation, fix, troubleshooting task, or problem-solving
  task.
- A statement governing pull requests.

#### States, plans, explanations, and runtime conditions

- A statement that merely describes a current or past implementation, structure, placement, dependency relationship,
  supported environment, or behavior.
- A statement that merely describes a planned implementation, planned change, or design proposal.
- A statement that merely explains a concept, structure, or design that readers should understand or distinguish.
- A statement explicitly marked as deprecated, obsolete, outdated, historical only, or no longer applicable.
- A condition stated only to explain how one tutorial, example, or demonstration works.
- A statement governing a runtime value, state, input, output, behavior, process, or activity rather than source code or
  test code.
- A statement governing runtime configuration or configuration specified by a software user.
- Instructions for installing, running, or using software.
- A condition required only to make a tutorial, example, or demonstration work.
- A description of software functionality intended for users of the software.

#### Other artifacts and measurements

- A statement specifying only a numerical target obtained through execution or measurement, such as test coverage,
  performance, reliability, or success rate.
- A statement governing a configuration, template, option, parameter, or rule file used by a formatter, linter, build
  tool, code generator, or other development tool, even if the tool changes, generates, formats, or validates source
  code or test code.
- A statement governing the addition, removal, or classification of dependencies in a dependency manifest or
  packaging configuration.
- A statement governing the content, filename, placement, or structure of documentation, a CI workflow, or a
  container definition such as a Dockerfile.
- A condition requiring a contributor's name, authorship, affiliation, or attribution in a source-code comment.

## Persistent code rule

A passage states a persistent code rule only when all of the following conditions are satisfied.

### Explicit normativity

- A natural-language statement in the body explicitly states a requirement, prohibition, recommendation, or
  permission concerning the content or structure of maintained source code or test code.
- A word such as `must`, `should`, `required`, `recommended`, `may`, or `can` counts only when it expresses a condition
  that the code or test code must satisfy after modification.
- A declarative sentence describing where or how code is written, placed, stored, located, named, organized, used, or
  called is not a rule unless that sentence itself states that the condition is required, prohibited, recommended,
  permitted, or allowed.
- A title, heading, introduction, table heading, filename, or placement cannot supply missing normativity.
- A description that a facility `can be used` or `can be imported` does not state permission governing maintained
  source code or test code.

### Persistence and verifiability

- The condition applies whenever the specified type of code is created or modified, or existing code must continue to
  satisfy it.
- Whether the condition is satisfied can be determined from the content or structure of the source code or test code
  after modification.
- An instruction that only completes a named current change, fix, migration, adaptation, or problem is not persistent.
- An expression such as `whenever`, `when adding`, `each`, or `every` establishes persistence only when it explicitly
  says that the condition applies whenever the same type of code is created or modified in the future.

## Concrete specification

A persistent code rule contains a concrete specification only when its condition specifies at least one of the
following:

- A relative path to a source-code or test-code file or directory.
- A name or code identifier for a class, function, module, package, component, API, test fixture, configuration item
  defined in source code, data model, schema, file format, protocol, or generated source code.
- A required dependency, placement, ownership, visibility, or call relationship among those names, code identifiers,
  or relative paths.
- A name, prefix, suffix, identifier, naming pattern, or fixed value that one of those names or code identifiers must
  use.
- A language, runtime, platform, API, or version with which source code, test code, or changes to either must remain
  compatible.

A relative path, name, code identifier, or fixed value counts only when it concretizes the condition imposed on the
content or structure of source code or test code.

### Information that is not a concrete specification

None of the following information counts as a concrete specification, alone or in combination with another item in
this list:

- A project name, repository name, or the phrase `this project` or `this repository`.
- A generic term denoting a type of code element.
- The name of a programming language, library, or framework.
- The name of an API, feature, object, method, parameter, argument, keyword, or programming technique provided by a
  programming language, runtime, platform, library, or framework.
- The name of a formatter, linter, or external style guide.
- A relative path or name for documentation, a CI workflow, a container definition, or runtime configuration.
- Language-general syntax, declaration form, keyword, character set, casing choice, naming format, or coding practice
  not tied to a name or code identifier defined by the software or to a relative source-code or test-code path.
- A term introduced in content solely as a name for the preceding language-general syntax or practice.
- A name, code identifier, or path used only as an example or placeholder in a technology-general guide or best
  practices document.
- A non-exhaustive example introduced by `e.g.`, `for example`, `such as`, `including but not limited to`, or an
  equivalent expression.

The following additional restrictions apply:

- A license, copyright notice, or license-header standard alone is not concrete. A header requirement is concrete only
  when the passage contains the required header text or a repository-relative path from which to copy it.
- A path separator or path-handling API provided by a language, runtime, or library is not concrete.
- A relationship described as `roughly the same`, `similar`, `aligned`, `consistent`, or `equivalent` is concrete only
  when the passage enumerates both compared subjects and every element or relationship that must match.
- A non-exhaustive example cannot complete an enumeration of elements or relationships that must match.

## Output

### reason

- For YES, briefly explain the explicitly stated requirement, prohibition, recommendation, or permission, the subject
  to which it persistently applies, and its concrete specification.

### quote

- For YES, copy the smallest exact contiguous substring that verifies every YES condition.
- The quote must contain the natural-language statement that explicitly states the requirement, prohibition,
  recommendation, or permission.
- Do not use only a code block, code example, or source-code comment as quote.
- Do not supplement information absent from quote with information stated only in reason.
- Do not summarize, paraphrase, reorder, or insert ellipses into quote.
- Do not change the line breaks of quote.
- For NO, quote must be an empty string.
