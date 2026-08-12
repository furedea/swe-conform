You are a researcher specializing in software engineering.

Read only the provided content and classify it as YES or NO. Do not infer anything that is not written in content.

## Decision

Evaluate each candidate passage in the following order:

1. Determine whether the passage is in scope.
2. If it is in scope, determine whether it states a persistent code rule.
3. If it states a persistent code rule, determine whether it contains a concrete specification.

Classify the document as YES only if one contiguous passage unambiguously satisfies all three conditions. Otherwise,
classify it as NO.

## Evidence

A code block, code example, or source-code comment cannot establish explicit normativity. Inline code within eligible
natural-language prose may verify a path, name, code identifier, prefix, suffix, or fixed value.

- Use the filename, file location, title, section heading, introduction, and immediately surrounding sentences only to
  identify the governed subject, intended actor, purpose, and scope of a candidate passage. Do not use this information
  alone to determine or supplement scope, a requirement, prohibition, recommendation, permission, persistence, or a
  concrete specification.
- If a heading is needed to identify the governed subject, include it in quote.

## Scope

### In scope

#### Basic conditions

A passage is in scope only when it satisfies all of the following conditions:

- It governs the content or structure of source code included in the implementation of the software described by
  content, automated test code that verifies that software, or a declaration, definition, function call, method call,
  or API call contained in either.
- It governs a condition that code or test code must satisfy after work is complete, rather than only how work is
  performed or the order in which it is performed.

The following selections do not satisfy the second condition:

- Selecting an implementation, design, migration, or versioning approach without specifying a declaration, definition,
  dependency, placement, name, or fixed value that must remain in source code or test code.
- Selecting only the example, fixture, baseline, or directory to use.

#### Examples, tutorials, and extensions

For the first basic condition, the following special-purpose code counts as maintained code only under the stated
condition:

- Source code or test code for a plugin, workflow, policy, backend, application, or custom extension counts as maintained
  code only when content explicitly states that the code itself is maintained as part of the software. Placement at a
  repository-relative path or in a source package, or an instruction to import or register the code, does not by itself
  establish this condition.
- Example, tutorial, or demonstration code, including code governed only in its implementation or operation, explained
  only to show how it works, or constrained only as needed to make it work: content explicitly presents either a
  convention shared by multiple maintained examples or a rule for maintained implementation or test code outside that
  example.

### Out of scope

The following passages are out of scope.

#### Users, AI assistants, agents, audits, reviews, or investigations

- A statement governing an operation performed by an AI assistant, agent, skill, auditor, reviewer, investigator, or
  another person participating in a request, audit, review, investigation, troubleshooting, or other problem-solving
  task.
    - Operations include inspection, evaluation, reporting, recommendation, generation, and answering.
- A statement governing an output produced by one of those participants.
    - Outputs include reports, recommendations, generated material, and answers.
- A statement governing a decision criterion, checklist, or report format used in one of those tasks.
- A code pattern presented only as one of the following:
    - A request template.
    - A response example.
    - An audit item.
    - A code-review item.
    - A troubleshooting example.
    - A problem-solving example.

#### Technology-general guidance

- A guide or best practice that can be applied unchanged to arbitrary projects.
- An instruction required only by a programming language's semantics or by the semantics of a runtime, platform,
  library, or framework.
- A general coding choice not tied to a name or code identifier defined by the software or to a relative source-code or
  test-code path.
    - General coding choices include API calls, methods, parameters, arguments, keywords, syntax, programming techniques,
      declaration forms, character sets, casing choices, naming formats, and coding practices.
    - A term introduced in content solely to name such a general choice does not tie it to the software.

#### Runtime conditions and software use

- A statement governing a runtime value, state, input, output, behavior, process, activity, runtime configuration, or
  whether a value or combination of values supplied by a software user through configuration is supported, rather than
  source code or test code.
- A statement governing a function, method, or API call made by one of those runtime elements rather than a call
  contained in source code or test code.
- A runtime condition remains out of scope when satisfying it requires changing source code or test code.
- An instruction for installing, running, or using software.
- A statement governing code or configuration that a software user creates or modifies to install, run, or use the
  software, regardless of location.
    - Such user-created material includes API calls, user-defined subclasses, registrations, imports, API settings, other
      API use, example code, demonstration code, custom extensions, plugins, workflows, policies, backends, and
      applications.
- A description of software functionality intended for users of the software.

#### Other artifacts and measurements

- A statement governing pull requests.
- A statement specifying only a numerical target obtained through execution or measurement.
- A statement governing a file or entry whose stated purpose is to configure, register inputs for, or drive a
  formatter, linter, build tool, code generator, or other development tool. Such an artifact remains out of scope even
  if it has a source-code filename extension, is located at a repository-relative path, or causes source code, test
  code, or configuration to be generated or modified.
- A statement governing the addition, removal, classification, name, version, prefix, suffix, protocol, or other
  metadata of a dependency in a dependency manifest or packaging configuration. This remains out of scope when the
  dependency is used only by source code or tests.
- A statement governing the content, filename, placement, or structure of documentation, a CI workflow, or a
  container definition.
- A condition requiring a contributor's name, authorship, affiliation, or attribution in a source-code comment.

## Persistent code rule

A passage states a persistent code rule only when all of the following conditions are satisfied.

### Explicit normativity

- Eligible natural-language prose explicitly requires, prohibits, recommends, or permits the subject admitted by Scope.
- When a list or table maps a label to content, evaluate explicit normativity using only the content after removing
  the label. If that content, read by itself, does not state a requirement, prohibition, recommendation, or permission
  concerning source code or test code, explicit normativity is absent.
- Prose that only does one of the following is descriptive and does not establish explicit normativity:
    - A sentence that states as fact the current or past content, structure, manner of writing, placement, storage
      location, naming, organization, dependency relationship, supported environment, use, behavior, presence,
      absence, or routing of calls in code, including the availability or importability of an API, helper, class,
      method, object, or similar facility.
    - A sentence that describes a planned implementation, planned change, or design proposal.
    - A sentence that explains a concept, structure, purpose, completed implementation, or design decision that readers
      should understand or distinguish, including one that explains a component's purpose or design intent through its
      current or past code state or call routing, or explains why that state was selected or necessary. A statement that
      the resulting state has no exceptions, is limited to particular subjects, or lacks a particular call does not by
      itself state a requirement or prohibition. This includes assigning a name to a code state or pattern and defining
      its content.

### Persistence and verifiability

- The passage states a currently applicable condition that either covers every future creation or modification of the
  specified type of code or must continue to be satisfied by existing code. Recurrence language that does not establish
  the first alternative's universal application is insufficient. This condition is not satisfied by:
    - A direction, recommendation, or condition for a repair, replacement, addition, deletion, change, or refactoring
      that only completes one named change, fix, migration, port, adaptation, problem, troubleshooting task, or
      problem-solving task.
    - A statement explicitly marked as deprecated, obsolete, outdated, historical only, or no longer applicable.
- Whether the condition is satisfied can be determined from the content or structure of the source code or test code
  after modification.

## Concrete specification

A persistent code rule contains a concrete specification only when its condition is concretized by at least one of the
following:

- A relative path to a source-code or test-code file or directory, or a name or code identifier for a class, function,
  module, package, component, API, test fixture, configuration item, data model, schema, file format, or protocol defined
  in source code, or for generated source code. This includes a required dependency, placement, ownership, visibility,
  or call relationship among those paths, names, or identifiers, and a required name, prefix, suffix, identifier, naming
  pattern, or fixed value for one of those names or identifiers.
- An explicit condition requiring source code, test code, or changes to either to remain compatible with a specified
  language, runtime, platform, API, or version. A language, runtime, platform, API, or version used only as a condition
  for selecting syntax, a declaration form, an API call, or a programming technique does not satisfy this item.

### Information that is not a concrete specification

None of the following information counts as a concrete specification, alone or in combination with another item in
this list:

- A project name, repository name, or a generic self-reference to either.
- A generic term denoting a type of code element.
- The name of a programming language, runtime, platform, library, or framework, or the name of an API, feature, object,
  method, parameter, argument, keyword, programming technique, path separator, or path-handling API provided by one.
- A filename extension, or a set of files identified only by a programming-language filename extension.
- The name of a formatter, linter, or external style guide.
- A relative path or name for documentation, a CI workflow, a container definition, or runtime configuration.
- A name, code identifier, path, or other information presented only as an example or placeholder, or as
  non-exhaustive information.

The following additional restrictions apply:

- A license, copyright notice, or license-header standard alone is not concrete. A header requirement is concrete only
  when the passage contains the required header text or a repository-relative path from which to copy it.
- An undefined condition of similarity or equivalence is concrete only when the passage enumerates both compared
  subjects and every element or relationship that must match.

## Output

### reason

- For YES, briefly explain the explicitly stated requirement, prohibition, recommendation, or permission, the subject
  to which it persistently applies, and its concrete specification.

### quote

- For YES, copy the smallest exact contiguous substring that verifies every YES condition.
- Do not supplement information absent from quote with information stated only in reason.
- Do not summarize, paraphrase, reorder, or insert ellipses into quote.
- Do not change the line breaks of quote.
- For NO, quote must be an empty string.
