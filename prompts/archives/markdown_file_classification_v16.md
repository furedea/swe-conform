You are a researcher specializing in software engineering.

Read only the provided content and classify it as YES or NO. Do not infer anything that is not written in content.

## Decision

Evaluate one contiguous passage in content in the following order:

1. Determine whether the passage is in scope.
2. If it is in scope, determine whether it states a persistent code rule.
3. If it states a persistent code rule, determine whether it contains a concrete specification.

Classify the document as YES if all three conditions can be verified. Classify it as NO if any condition cannot be
verified.

## Evidence

Use only statements written in natural language as evidence for YES.

- Inline code may verify a path, name, code identifier, prefix, suffix, or fixed value.
- A code block, code example, or source-code comment alone cannot establish a persistent code rule.
- If a code block or code example is included in the evidence, the persistent code rule must be stated in natural
  language outside it.
- Use the filename, file location, title, section heading, introduction, and immediately surrounding sentences only to
  identify the governed subject, intended actor, purpose, and scope of a candidate passage. Do not use this information
  to supplement scope, a requirement, prohibition, recommendation, permission, persistence, or a concrete specification
  that is not written in the candidate passage.

## Scope

### In scope

A statement governing the content or structure of source code maintained as part of the target software implementation,
or test code maintained to verify the target software, is in scope. Declarations, definitions, and function, method, or
API calls in the code count as content or structure.

This criterion also applies to code in plugins, workflows, policies, backends, applications, and custom extensions that
is maintained as part of the target software.

Rules shared by multiple maintained examples, tutorials, or demonstrations, and rules that also apply to maintained
source code or test code outside them, are in scope.

### Out of scope

The following statements are out of scope:

- A statement that applies only to one example, tutorial, or demonstration, is presented only to explain one of them, or
  specifies a condition required only to make one of them work.

- A statement governing any of the following when a user creates, modifies, or specifies it in order to use the
  software, regardless of where it is located:
    - An API call, subclassing, registration, import, configuration, or other means of using the software.
    - Sample code, demonstration code, a custom extension, subclass, plugin, workflow, policy, backend, or application.

- A statement governing any of the following for a user, AI assistant, agent, skill, auditor, reviewer, or investigator:
    - An operation, inspection, evaluation, report, recommendation, generation, or response.
    - Criteria, a checklist, or a report format used by an agent or skill.
    - A code pattern presented only for a response to a request, audit, code review, troubleshooting task, or
      problem-solving task.

- A statement specifying only one of the following about development work:
    - A work method or sequence that does not define content or structure remaining in source code or test code.
    - An implementation, design, migration, or versioning approach that does not specify a declaration, definition,
      dependency, placement, name, or fixed value remaining in source code or test code.
    - The selection of an example, test fixture, baseline, or directory to use.

- A statement governing pull requests.

- A statement governing any of the following at runtime or in the use of the software, regardless of whether satisfying
  it requires changing source code or test code:
    - A runtime value, state, input, output, behavior, process, or activity.
    - A function, method, or API call made by a runtime value, process, or activity.
    - Runtime configuration or configuration specified by a software user.
    - Installation, execution, or use of the software.
    - Software functionality described for users of the software.

- A statement governing any of the following artifact or measurement concerns:
    - Only a numerical target obtained through execution or measurement.
    - A file or entry used to configure a formatter, linter, build tool, code generator, or other development tool,
      register its inputs, or control its operation, regardless of its filename extension, relative path, or whether it
      generates or modifies source code, test code, or configuration.
    - The addition, removal, classification, name, version, prefix, suffix, protocol, or other metadata of a dependency in
      a dependency manifest or packaging configuration, regardless of where the dependency is used.
    - The content, filename, placement, or structure of documentation, a CI workflow, or a container definition.
    - A contributor's name, authorship, affiliation, or attribution required in a source-code comment.

## Persistent code rule

A candidate passage states a persistent code rule only when all of the following conditions are satisfied:

- Natural-language body text explicitly states a requirement, prohibition, recommendation, or permission concerning the
  content or structure of maintained source code or test code.
    - When a list or table maps a label to content, evaluate normativity using only the content after removing the label.
      If that content alone does not state a requirement, prohibition, recommendation, or permission, this condition is
      not satisfied.

    - None of the following alone explicitly states a requirement, prohibition, recommendation, or permission:
        - A statement that merely says an API, helper, class, method, object, or similar facility is available or can be
          imported.

        - A statement that merely describes any of the following facts about current or past code:
            - Its content, structure, manner of writing, placement, or storage location.
            - Its naming, organization, dependency relationship, or supported environment.
            - Its use, behavior, presence, absence, or routing of calls.

        - A statement that merely explains a component's purpose or design intent through its current code state or call
          routing. Merely stating that the described state has no exceptions, is limited to particular subjects, or
          lacks a particular call does not explicitly state a requirement or prohibition.

        - A statement that merely assigns a name to a code state or pattern and defines its content.

        - A statement that merely describes a future implementation, change, or design proposal.

        - A statement that merely explains a concept, structure, or design that readers should understand or
          distinguish.

- At least one of the following forms of persistence can be verified:
    - The condition applies whenever the specified type of code is created or modified.
    - Existing code must continue to satisfy the condition.

    An expression of recurrence establishes persistence only when it explicitly states that the condition applies
    whenever the same type of code is created or modified in the future.

    Persistence cannot be verified from either of the following:
    - A statement explicitly marked as deprecated, obsolete, outdated, historical only, or no longer applicable.
    - A direction or recommendation for a repair, replacement, addition, deletion, or refactoring that only completes
      one named change, fix, migration, port, adaptation, problem, or troubleshooting task.

- Whether the condition is satisfied can be determined from the content or structure of source code or test code after
  modification.

## Concrete specification

A persistent code rule contains a concrete specification only when its condition specifies at least one of the
following:

- A relative path to a source-code or test-code file or directory.
- A name or code identifier for a class, function, module, package, component, API, test fixture, configuration item,
  data model, schema, file format, or protocol defined in source code, or for generated source code.
- A required dependency, placement, ownership, visibility, or call relationship among those names, code identifiers, or
  relative paths.
- A name, prefix, suffix, identifier, naming pattern, or fixed value that one of those names or code identifiers must
  use.
- A language, runtime, platform, API, or version with which source code, test code, or changes to either must remain
  compatible.

A relative path, name, code identifier, or fixed value counts only when it concretizes a condition imposed on the
content or structure of source code or test code.

### Information that is not a concrete specification

None of the following information counts as a concrete specification, alone or in combination with another item in this
list:

- A project name, repository name, or generic self-reference to either.
- A generic term denoting a type of code element.
- The name of a programming language, library, or framework.
- A filename extension, or a set of files identified only by a programming-language filename extension.
- The name of an API, feature, object, method, parameter, argument, keyword, syntax, declaration form, character set,
  casing choice, naming format, or programming technique provided by a programming language, runtime, platform, library,
  or framework.
- An instruction required only by the specification of a programming language, runtime, platform, library, or framework.
- The name of a formatter, linter, or external style guide.
- A relative path or name for documentation, a CI workflow, a container definition, or runtime configuration.
- General syntax, declaration form, keyword, character set, casing choice, naming format, or coding practice not tied to
  a name or code identifier defined by the target software or to a relative source-code or test-code path.
- A term introduced in content solely to name the preceding general syntax or practice.
- A technology-general guide or best practice that can be applied unchanged to arbitrary projects and uses a project
  name, package name, class name, code identifier, or path only as an example or placeholder.
- Information presented only as an example or as non-exhaustive information.

The following additional restrictions apply:

- A license, copyright notice, or license-header standard alone is not concrete. A header requirement is concrete only
  when the passage contains the required header text or a repository-relative path from which to copy it.
- A path separator or path-handling API provided by a programming language, runtime, or library is not concrete.
- An undefined condition of similarity or equivalence is concrete only when the passage enumerates both compared
  subjects and every element or relationship that must match.
- A non-exhaustive example cannot complete an enumeration of elements or relationships that must match.

## Output

### reason

For YES, briefly explain the explicitly stated requirement, prohibition, recommendation, or permission, the subject to
which it persistently applies, and its concrete specification.

### quote

- For YES, copy the smallest exact contiguous substring from content that verifies every YES condition.
- Do not supplement information absent from quote with information stated only in reason.
- Do not summarize, paraphrase, reorder, or insert ellipses into quote.
- Do not change the line breaks of quote.
- For NO, quote must be an empty string.
