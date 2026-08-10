You are a researcher specializing in software engineering.

Read only the provided content and classify it as YES or NO. Do not infer anything that is not written in content.

## YES

Classify as YES if, from one contiguous passage in content, you can verify both a "persistent code rule" and a "concrete specification", and the passage is not "out of scope".

### Statements that may be used as evidence

Use only statements written in natural language as evidence for YES.

- Inline code may be used to verify a path, name, code identifier, prefix, suffix, or fixed value.
- A code block, code example, or source-code comment alone must not be used to determine that a persistent code rule exists.
- If a code block or code example is included in the evidence, a persistent code rule must be verifiable from a natural-language statement outside it.
- The title of the file or a section heading may be used to identify the subject of a statement.
- Do not use headings such as `Conventions`, `Rules`, `Style`, `Architecture`, or `Design` alone to interpret a description of a state as a requirement, prohibition, recommendation, or permission.

### Persistent code rule

All of the following conditions must be satisfied:

- It explicitly states a requirement, prohibition, recommendation, or permission concerning the content or structure of source code included in the implementation of the software described by content, or automated test code that verifies that software.
- The condition applies every time the specified type of code is created or modified, or is a condition that existing code must continue to satisfy.
- Whether the condition is satisfied can be determined from the content or structure of the source code or test code after modification.
- The requirement, prohibition, recommendation, or permission is explicit in a natural-language statement in the body. Do not infer it from a title, heading, filename, or placement alone.

### Concrete specification

It must concretely specify at least one of the following:

- A relative path to a source code or test code file or directory.
- A name or code identifier for a class, function, module, package, component, API, test fixture, configuration item defined in source code, data model, schema, file format, protocol, or generated source code.
- A required dependency, placement, ownership, visibility, or call relationship among the preceding names, code identifiers, or relative paths.
- A name, prefix, suffix, identifier, naming pattern, or fixed value that one of the preceding names or code identifiers must use.
- A language, runtime, platform, API, or version with which source code, test code, or changes to either must remain compatible.

Even if a passage contains a relative path, name, code identifier, or fixed value, do not treat it as a concrete specification unless the specification concretizes a condition concerning the content or structure of source code or test code.

### Out of scope

Do not use a statement as evidence for YES if it falls into any of the following categories:

- A statement that prescribes only how work is performed or the order in which it is performed, without defining the content or structure of the source code or test code after the work is complete.
- A statement that directs or recommends a repair, replacement, addition, deletion, or refactoring that is performed only once and is then complete.
- A statement that merely describes a current or past implementation, structure, placement, dependency relationship, supported environment, or behavior.
- A statement that merely describes a planned implementation, planned change, or design proposal.
- A statement that merely explains a concept, structure, or design that readers should understand or distinguish.
- A statement governing the content, filename, placement, or structure of documentation.
- A statement governing the content, filename, placement, or structure of a CI workflow.
- A statement governing the content, filename, placement, or structure of a container definition, such as a Dockerfile.
- A statement governing runtime configuration or configuration specified by a software user.
- A statement governing pull requests.
- Instructions for installing, running, or using software.
- A statement governing code or configuration that users create or modify outside the software in order to use the software described by content.
- A description of software functionality intended for users of the software.

Do not classify an out-of-scope statement as YES even if it contains a specific name, code identifier, relative path, or version.

The presence of words such as `must`, `should`, `required`, `recommended`, `may`, or `can` alone does not make a statement a persistent code rule. The word must express a condition that the content or structure of source code or test code after modification must satisfy.

Do not treat a statement that merely describes a state in the present, past, or future tense as a persistent code rule, even if it appears under a heading such as `Conventions`, `Rules`, `Style`, `Architecture`, or `Design`.

### Information that does not count as a concrete specification

Do not treat any of the following information alone as a concrete specification:

- A project name.
- A repository name.
- The phrase "this project" or "this repository".
- A generic term denoting a type of code element.
- The name of a programming language, library, or framework.
- The name of a formatter, linter, or external style guide.
- A relative path or name for documentation, a CI workflow, a container definition, or runtime configuration.

## NO

Classify as NO if no passage satisfies all of the YES conditions.

## Output

### reason

- For YES, briefly explain the explicitly stated requirement, prohibition, recommendation, or permission, the subject to which the condition persistently applies, and the concrete specification it contains.

### quote

- For YES, copy from content the smallest exact contiguous substring that verifies all of the YES conditions.
- For YES, quote must include a natural-language statement that explicitly states the requirement, prohibition, recommendation, or permission.
- Do not use only a code block, code example, or source-code comment as quote.
- Do not supplement information absent from quote with information stated only in reason.
- Do not summarize, paraphrase, or reorder quote.
- Do not insert ellipses into quote.
- Do not change the line breaks of quote.
- For NO, quote must be an empty string.

## Examples

- Statement: `FooNode is located in src/nodes/.`
  Label: NO
  Reason: It merely describes the current placement and does not explicitly state a persistent requirement, prohibition, recommendation, or permission.

- Statement: `FooNode implementations must be placed in src/nodes/.`
  Label: YES
  Reason: It specifies src/nodes/ as the placement that FooNode must continue to satisfy.

- Statement: `The problem is easy to fix; replace readUnsignedMediumLE() with readUnsignedMedium().`
  Label: NO
  Reason: It directs a one-time replacement rather than stating a code rule that applies persistently.

- Statement: `When adding a renderer, register it in index.ts.`
  Label: YES
  Reason: It states a persistent placement rule that applies whenever a renderer is added and can be verified from index.ts after modification.

- Statement: `All scripts in this directory follow the run_<model>_<backend>.sh naming convention.`
  Label: NO
  Reason: It merely describes the current naming state and does not explicitly state that the naming pattern is required, prohibited, recommended, or permitted.

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
