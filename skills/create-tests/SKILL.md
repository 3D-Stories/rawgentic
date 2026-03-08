---
name: rawgentic:create-tests
description: Create or improve a project's test suite using the WF12 14-step workflow with brainstorming-driven test strategy, context7 framework docs lookup, test harness generation, coverage gap analysis, and verified test execution. Invoke with /create-tests optionally followed by a specific file or module path. Use this skill whenever the user mentions adding tests, creating a test suite, improving test coverage, bootstrapping testing, setting up a test harness, or auditing existing tests — even if they don't say "create-tests" explicitly.
argument-hint: Optional file/module path (e.g., "src/auth/") or omit for whole project
---


# WF12: Test Suite Creation Workflow

<role>
You are the WF12 orchestrator implementing a 14-step test creation workflow. You guide the user from zero tests (or incomplete coverage) through test strategy design, framework setup, harness creation, test generation, execution, and verification. WF12 operates in two modes: **greenfield** (no tests exist — build everything from scratch for the entire project) and **coverage-gap** (tests exist — audit against best practices, identify gaps, and fill them). You use the brainstorming skill to collaboratively design the testing strategy before writing any test code, and context7 to pull up-to-date framework documentation so tests follow current idioms.
</role>

<constants>
PROJECT_ROOT = "<inferred from `git rev-parse --show-toplevel`>"
BRANCH_PREFIX = "test/"
MODE_THRESHOLDS:
  greenfield: config has no testing section, OR testing.frameworks is empty, AND no test files found on disk
  coverage_gap: config.testing exists with frameworks, OR test files found on disk
LOOPBACK_BUDGET:
  Step_9_to_7: max 2   # fix failing generated tests
  Step_11_to_7: max 1  # review findings require new tests
  global_cap: 3
</constants>

<config-loading>
Before executing any workflow steps, load the project configuration:

1. Determine the active project using this fallback chain:
   **Level 1 -- Conversation context:** If a previous `/rawgentic:switch` in this session set the active project, use that.
   **Level 2 -- Session registry:** Read `claude_docs/session_registry.jsonl`. Grep for your session_id. If found, use the project from the most recent matching line.
   **Level 3 -- Workspace default:** Read `.rawgentic_workspace.json` from the Claude root directory. If exactly one project has `active == true`, use it. If multiple projects are active, STOP and tell user: "Multiple active projects. Run `/rawgentic:switch <name>` to bind this session."

   At any level:
   - `.rawgentic_workspace.json` missing -> STOP. Tell user: "No rawgentic workspace found. Run /rawgentic:new-project."
   - `.rawgentic_workspace.json` malformed -> STOP. Tell user: "Workspace file is corrupted. Run /rawgentic:new-project to regenerate, or fix manually."
   - No active project found at any level -> STOP. Tell user: "No active project. Run /rawgentic:new-project to set one up, or /rawgentic:switch to bind this session."
   - **Path resolution:** The `activeProject.path` may be relative (e.g., `./projects/my-app`). Resolve it against the Claude root directory (the directory containing `.rawgentic_workspace.json`) to get the absolute path for file operations.

2. Read `<activeProject.path>/.rawgentic.json`.
   - Missing -> STOP. Tell user: "Active project <name> has no config. Run /rawgentic:setup."
   - Malformed JSON -> STOP. Tell user: "Project config is corrupted. Run /rawgentic:setup to regenerate."
   - Check `config.version`. If version > 1 (or missing), warn user about version mismatch.
   - Parse full JSON into `config` object.

3. Build the `capabilities` object from config:
   - has_tests: config.testing exists AND config.testing.frameworks.length > 0
   - test_commands: config.testing.frameworks[].command
   - has_ci: config.ci exists AND config.ci.provider exists
   - has_deploy: config.deploy exists AND config.deploy.method exists and != "manual"
   - has_database: config.database exists AND config.database.type exists
   - has_docker: config.infrastructure exists AND config.infrastructure.docker.composeFiles.length > 0
   - project_type: config.project.type
   - repo: config.repo.fullName
   - default_branch: config.repo.defaultBranch

All subsequent steps use `config` and `capabilities` — never probe the filesystem for information that should be in the config.
</config-loading>

<learning-config>
WF12 is a heavy learner — it will almost always discover or create testing capabilities. Update `.rawgentic.json` before completing:
- If greenfield: add the entire `testing` section with frameworks, commands, config files, test directories
- If coverage-gap: append newly discovered frameworks or update test directories
- Append to arrays, set null/missing fields, never overwrite existing non-null values without asking
- Always read full file, modify in memory, write full file back
</learning-config>

<environment-setup>
PROJECT_ROOT is populated at workflow start (Step 1) by running:
- `PROJECT_ROOT`: `git rev-parse --show-toplevel`

All other project-specific values come from `config` and `capabilities` loaded via the `<config-loading>` block.

If config loading fails, STOP and tell the user which config step failed.
</environment-setup>

<termination-rule>
WF12 terminates after test execution verification and completion summary. No auto-transition to other workflows. WF12 terminates ONLY after the completion-gate (after Step 14) passes. All steps must have markers in session notes, and the completion-gate checklist must be printed with all items passing.
</termination-rule>

<context-compaction>
Per rawgentic workflow principle: before context compaction, document in `claude_docs/session_notes.md`: current step number, branch name, last commit SHA, mode (greenfield/coverage-gap), testing strategy decisions, frameworks chosen, and loop-back budget state.
</context-compaction>

<ambiguity-circuit-breaker>
If test strategy decisions are ambiguous (e.g., multiple valid frameworks, unclear what to mock vs. integration test), STOP and present options to the user for resolution. The brainstorming step should surface most of these, but edge cases may appear during implementation.
</ambiguity-circuit-breaker>

<language-framework-map>
WF12 is language-agnostic. Use config.techStack and filesystem detection to identify the language(s), then select appropriate test frameworks. This map provides defaults — the brainstorming step (Step 2) may override these based on user preference.

| Language | Default Framework | Config File | Test Pattern | Dep Install | Coverage Tool |
|----------|------------------|-------------|--------------|-------------|---------------|
| Python | pytest | pyproject.toml / pytest.ini | test_*.py / *_test.py | pip install pytest | pytest-cov |
| JavaScript | vitest (or jest) | vitest.config.ts / jest.config.js | *.test.js / *.spec.js | npm install --save-dev | v8/istanbul |
| TypeScript | vitest (or jest) | vitest.config.ts | *.test.ts / *.spec.ts | npm install --save-dev | v8/istanbul |
| Go | go test (stdlib) | none | *_test.go | none | go test -cover |
| Rust | cargo test (stdlib) | Cargo.toml | #[cfg(test)] mod tests | none (dev-deps in Cargo.toml) | cargo-tarpaulin |
| C/C++ | GoogleTest / CTest | CMakeLists.txt | *_test.cpp | cmake FetchContent or apt | gcov/lcov |
| Shell/Bash | bats-core | none | *.bats | git clone / brew install | kcov (optional) |
| Ruby | rspec | .rspec / spec_helper.rb | *_spec.rb | gem install rspec | simplecov |
| PHP | phpunit | phpunit.xml | *Test.php | composer require --dev | phpunit --coverage |
| Java | JUnit 5 | build.gradle / pom.xml | *Test.java | gradle/maven dep | jacoco |
| Kotlin | JUnit 5 + MockK | build.gradle.kts | *Test.kt | gradle dep | jacoco |
| Swift | XCTest | Package.swift | *Tests.swift | built-in | xcodebuild |

For **multi-language projects** (e.g., Python backend + JavaScript frontend + shell scripts for deployment):
- Detect ALL languages present in the project via file extensions and config.techStack
- Brainstorming (Step 2) decides which languages get tests and in what order
- Each language gets its own test infrastructure (Step 5) and test files (Step 7)
- A single top-level `make test` or script should run all test suites in sequence

For **shell scripts** specifically:
- bats-core is the standard testing framework — it supports `@test` blocks, `run` command capture, and assertions
- Context7 may not have bats docs; fall back to training knowledge and the bats-core GitHub README
- Test helpers: `setup()` and `teardown()` functions, temp directory fixtures
- Mock external commands by prepending to PATH with stub scripts
- Focus on: exit codes, stdout/stderr output, file side effects, argument parsing
</language-framework-map>

<step-tracking>
At the end of each step, log a marker in `claude_docs/session_notes.md`:
`### WF12 Step X: <Name> — DONE (<key detail>)`
This enables workflow resumption if context is lost.
</step-tracking>

---

## Step 1: Receive Request and Detect Testing Landscape

**Input:** Optional file/module path from user. If omitted, scope is the entire project.

1. Run config-loading block. Set `PROJECT_ROOT`.
2. Parse user argument:
   - If a path was provided, set `SCOPE = "targeted"` and `TARGET_PATH = <user's path>`.
   - If no path, set `SCOPE = "project-wide"`.
3. Detect current testing landscape:
   - Check `capabilities.has_tests` from config.
   - **Detect all languages** in the project: scan file extensions, check config.techStack, inspect build files (package.json, pyproject.toml, Cargo.toml, CMakeLists.txt, go.mod, Gemfile, composer.json, build.gradle, Package.swift). Build a `detected_languages` list.
   - For each detected language, scan for test files using the language-specific patterns from `<language-framework-map>`:
     - Python: `test_*.py`, `*_test.py`, `conftest.py`
     - JS/TS: `*.test.js`, `*.spec.ts`, `*.test.tsx`
     - Go: `*_test.go`
     - Rust: `#[cfg(test)]` in source files
     - C/C++: `*_test.cpp`, `*_test.c`, `test_*.cpp`
     - Shell: `*.bats`, `test_*.sh`
     - Ruby: `*_spec.rb`
     - PHP: `*Test.php`
     - Java/Kotlin: `*Test.java`, `*Test.kt`
   - Check for test config files per language: `pytest.ini`, `vitest.config.*`, `jest.config.*`, `playwright.config.*`, `.rspec`, `phpunit.xml`, `CMakeLists.txt` with `enable_testing()`, etc.
4. Classify mode:
   - **Greenfield:** No test frameworks in config AND no test files on disk.
   - **Coverage-gap:** Test frameworks exist in config OR test files found on disk.
5. Report to user:

> **Mode:** greenfield / coverage-gap
> **Scope:** project-wide / targeted (<path>)
> **Tech stack:** <from config.techStack>
> **Existing test frameworks:** <list or "none">
> **Test files found:** <count or "none">

**Loopback:** None — this is the entry point.

---

## Step 2: Brainstorm Testing Strategy

Invoke the `superpowers:brainstorming` skill to collaboratively design the testing strategy with the user. The brainstorming skill will ask questions one at a time and produce a design document.

**Seed the brainstorming with this context** (include in your prompt to the brainstorming flow):

- Project name, type, description from config
- Tech stack from config
- Current mode (greenfield vs. coverage-gap)
- Scope (project-wide vs. targeted)
- Source file inventory (list key source files/modules — not contents, just paths and brief purpose)

**Topics the brainstorming should cover:**

- What types of tests matter most for this project? (unit, integration, e2e, property-based, snapshot)
- Which framework(s) to use and why?
- What should be mocked vs. tested against real dependencies?
- Critical paths that need the most coverage
- Test organization (file naming, directory structure)
- Fixture and helper strategy
- If coverage-gap mode: what's working well in existing tests vs. what needs improvement

**Output:** A design document saved to `docs/plans/YYYY-MM-DD-testing-strategy.md` with the agreed testing strategy.

**Loopback:** None — brainstorming handles its own iteration internally.

---

## Step 3: Resolve Framework Documentation via Context7

Use the context7 MCP server to pull up-to-date documentation for the chosen test framework(s). This ensures generated tests follow current idioms rather than stale patterns from training data.

For each framework decided in Step 2:

1. **Resolve the library ID:**
   Call `mcp__plugin_context7_context7__resolve-library-id` with the framework name (e.g., "pytest", "vitest", "jest", "playwright", "go testing").

2. **Query relevant docs:**
   Call `mcp__plugin_context7_context7__query-docs` with the resolved library ID and targeted topics:
   - Getting started / configuration
   - Assertions and matchers
   - Mocking and fixtures
   - Test organization best practices
   - Common patterns for the project's domain (e.g., "testing REST APIs", "testing React components")

3. **Extract key patterns** into a working reference:
   - Config file format and required fields
   - Import patterns and assertion syntax
   - Fixture/setup/teardown patterns
   - Mocking approach (module mocks, dependency injection, test doubles)
   - Async testing patterns (if applicable)

Store the extracted patterns in memory — these guide all subsequent test generation steps.

**Fallback strategy when context7 lacks docs:**
Some frameworks (bats-core, GoogleTest, cargo test) may not be in context7's library index. When `resolve-library-id` returns no match:
- Fall back to your training knowledge for that framework
- Note to user: "Context7 didn't have docs for <framework>. Using built-in knowledge — patterns may not reflect the very latest version."
- For shell/bats: the bats-core README on GitHub is the authoritative reference
- For Go/Rust stdlib testing: training knowledge is reliable since the stdlib APIs are stable

**Loopback:** None.

---

## Step 4: Create Test Branch

1. Ensure working tree is clean (`git status --porcelain`). If dirty, ask user to commit or stash.
2. Fetch latest: `git fetch origin`.
3. Create branch from default branch:
   - Greenfield: `git checkout -b test/bootstrap-test-suite origin/<default_branch>`
   - Coverage-gap: `git checkout -b test/improve-coverage origin/<default_branch>`
4. Confirm branch created.

**Loopback:** None.

---

## Step 5: Generate Test Infrastructure

**Greenfield mode:**

For each language in `detected_languages` (from Step 1), set up its test infrastructure using the `<language-framework-map>` defaults (or overrides from the brainstorming strategy):

1. **Config file:** Generate the framework's config file using patterns from Step 3 context7 docs.
2. **Dependencies:** Install test dependencies using the language's package manager:
   - Python: `pip install pytest pytest-cov` (or add to pyproject.toml/requirements-dev.txt)
   - Node/TS: `npm install --save-dev vitest @vitest/coverage-v8` (or jest, etc.)
   - Go: stdlib — no install needed
   - Rust: add dev-dependencies to Cargo.toml
   - C/C++: add GoogleTest via CMake FetchContent, or install via apt/brew
   - Shell: install bats-core (`git clone https://github.com/bats-core/bats-core && ./install.sh /usr/local`)
   - Ruby: `gem install rspec` or add to Gemfile
   - PHP: `composer require --dev phpunit/phpunit`
   - Java/Kotlin: add JUnit 5 to build.gradle/pom.xml
3. **Test directory structure:** Create directories matching the strategy from Step 2. For multi-language projects, each language typically has its own test directory (e.g., `tests/` for Python, `__tests__/` or colocated for JS/TS, `*_test.go` alongside source for Go).
4. **Shared helpers:** Create test utilities per language — fixtures, factories, common mocks. For shell scripts, create a `test/test_helper.bash` with common setup functions.
5. **Top-level test runner:** Add a way to run all test suites:
   - If Makefile exists: add `test` target that runs each language's tests in sequence
   - If package.json: add `test` script
   - If neither: create a simple `run_tests.sh` script

**Coverage-gap mode:**

1. Review existing test infrastructure against best practices from Step 3.
2. Identify gaps: missing config options, outdated patterns, missing helper utilities.
3. Present findings to user with recommended improvements.
4. Apply approved improvements.

**Loopback:** None.

---

## Step 6: Analyze Source Code for Test Targets

Build a map of what needs tests:

1. **Inventory source files** in scope (project-wide or targeted path).
2. For each source file, extract:
   - Exported functions/classes/methods
   - Dependencies (imports, injected services)
   - Side effects (I/O, network calls, database queries)
   - Complexity indicators (branches, loops, error handling paths)
3. **Greenfield:** All source files are targets. Prioritize by:
   - Critical business logic first
   - Public API surface
   - Error handling paths
   - Edge cases identified in brainstorming
4. **Coverage-gap:** Cross-reference with existing test files:
   - Which source files have no tests?
   - Which have tests but with obvious gaps (no error paths, no edge cases)?
   - Which existing tests are poorly structured (no assertions, brittle mocking)?

Present the test target map to the user:
> **Files needing tests:** X
> **Functions/methods to cover:** Y
> **Priority order:** <top 5 files with reasoning>

**Loopback:** None.

---

## Step 7: Generate Test Files

This is the core generation step. For each test target (prioritized from Step 6):

1. **Read the source file** to understand its behavior.
2. **Generate the test file** following patterns from Step 3:
   - Use the project's naming convention (e.g., `test_*.py`, `*.test.ts`, `*_test.go`)
   - Import the module under test
   - Create test groups/describe blocks matching the source structure
   - Write test cases covering:
     - **Happy path:** Normal inputs, expected outputs
     - **Edge cases:** Boundary values, empty inputs, null/undefined
     - **Error paths:** Invalid inputs, thrown exceptions, error returns
     - **Integration points:** Verify correct interaction with dependencies (mocked appropriately)
   - Use descriptive test names that read as specifications
   - Add inline comments only where the test setup is non-obvious
3. **Coverage-gap mode — modify existing tests:**
   - Fix poorly structured tests (add missing assertions, reduce brittleness)
   - Add missing test cases to existing test files
   - Create new test files for uncovered source files

Write tests in batches — commit after each logical group (e.g., all tests for one module).

**Loopback target:** Steps 9 and 11 may loop back here.

---

## Step 8: Run Test Suite — First Pass

Run the full test suite using the framework command:

1. Execute the test command (from config or as determined in Step 5).
2. Capture output: pass count, fail count, error count, total time.
3. If coverage tool is available, run with coverage and capture the report.

**Report to user:**
> **Results:** X passed, Y failed, Z errors
> **Coverage:** N% (if available)
> **Duration:** Xs

If all tests pass on first run, proceed to Step 10.
If tests fail, proceed to Step 9.

**Loopback:** None.

---

## Step 9: Fix Failing Tests

For each failing test:

1. Read the failure output — understand whether it's a test bug or a source code issue.
2. **Test bug** (wrong assertion, bad mock, import error): Fix the test.
3. **Source code issue** (test correctly identified a real bug): Flag to user — do NOT fix source code in this workflow. Document it as a finding.
4. Re-run the specific failing test to confirm the fix.

After fixing all test bugs, re-run the full suite.

**Loopback:** If tests still fail after fixes, loop back to Step 7 with updated understanding. Budget: max 2 iterations (Step_9_to_7). If budget exhausted, report remaining failures to user and proceed.

---

## Step 10: Coverage Analysis

1. Run tests with coverage enabled (if the framework supports it).
2. Parse coverage report:
   - Overall line/branch/function coverage percentage
   - Per-file coverage breakdown
   - Uncovered lines/branches
3. Compare against the testing strategy from Step 2:
   - Are critical paths covered?
   - Are error handling paths tested?
   - Any surprising gaps?
4. Present coverage report to user.

If coverage meets the strategy goals from Step 2, proceed to Step 11.
If significant gaps remain and user wants them filled, loop back to Step 7 (uses Step_11_to_7 budget).

**Loopback:** Step_11_to_7: max 1 iteration.

---

## Step 11: Test Quality Review

Review the generated test suite for quality:

1. **Assertion quality:** Tests have meaningful assertions (not just "doesn't throw").
2. **Independence:** Tests don't depend on execution order or shared mutable state.
3. **Readability:** Test names describe behavior, setup is clear, no magic numbers.
4. **Maintainability:** DRY where appropriate (shared fixtures), but each test is self-contained enough to understand in isolation.
5. **Mocking discipline:** Only mock what's necessary (external services, I/O), don't mock the module under test.
6. **Best practice alignment:** Compare against patterns from Step 3 context7 docs.

Present findings to user. Apply approved improvements.

**Loopback:** If review reveals tests that need rewriting, loop back to Step 7 (uses Step_11_to_7 budget).

---

## Step 12: Update .rawgentic.json

Update the project config with discovered/created testing capabilities:

1. Read `.rawgentic.json`.
2. Add or update the `testing` section:
   ```json
   {
     "testing": {
       "frameworks": [
         {
           "name": "<framework>",
           "type": "<unit|integration|e2e>",
           "command": "<run command>",
           "configFile": "<config file path>",
           "testDir": "<test directory>"
         }
       ]
     }
   }
   ```
3. Write the file back. Follow learning-config rules (append, don't overwrite).

**Loopback:** None.

---

## Step 13: Create Pull Request

1. Stage and commit all test files with a conventional commit:
   - Greenfield: `test: bootstrap test suite with <framework>`
   - Coverage-gap: `test: improve coverage for <scope>`
2. Push branch: `git push -u origin <branch-name>`
3. Create PR using `gh pr create`:
   ```
   ## Summary
   - <Mode: greenfield bootstrap / coverage improvement>
   - <Framework(s) used>
   - <Number of test files created/modified>
   - <Coverage: X% overall>

   ## Test Strategy
   <Link to docs/plans/YYYY-MM-DD-testing-strategy.md>

   ## Test Results
   - Passed: X
   - Failed: 0
   - Coverage: X%
   ```
4. If CI exists (`capabilities.has_ci`), wait for CI to pass.

**Loopback:** None.

---

## Step 14: Completion Summary

Present final summary to user:

```
## WF12 Complete: Test Suite <Created / Improved>

**Mode:** greenfield / coverage-gap
**Scope:** project-wide / targeted (<path>)
**Framework:** <name>
**Tests:** X files, Y test cases
**Coverage:** Z%
**PR:** <url>

### Key Decisions (from brainstorming)
- <decision 1>
- <decision 2>

### Findings
- <any source bugs discovered>
- <any architectural concerns>

### Next Steps
- Merge PR when ready
- Consider adding CI test step if not present
- Run /rawgentic:implement-feature with TDD now that test infrastructure exists
```

**Loopback:** None.

---

<completion-gate>
Before declaring WF12 complete, verify ALL of the following. Print the checklist with pass/fail for each item:

1. [ ] Step markers logged for ALL executed steps in session notes
2. [ ] Testing strategy design doc created and committed
3. [ ] Test infrastructure set up (config, dependencies, helpers)
4. [ ] Test files generated and committed
5. [ ] All tests passing (or failures documented as source bugs)
6. [ ] Coverage report generated (if framework supports it)
7. [ ] .rawgentic.json updated with testing section
8. [ ] PR created with test results in description
9. [ ] Final summary presented to user

If ANY item fails, go back and complete it before declaring "WF12 complete."
You may NOT output "WF12 complete" until all items pass.
</completion-gate>

---

## Workflow Resumption

If this skill is invoked mid-conversation, detect the current state:

0. All step markers present but completion-gate not printed? -> Run completion-gate, then terminate.
1. PR exists and CI passed? -> Step 14 (completion summary)
2. PR exists? -> Step 13 (wait for CI)
3. Tests passing and config updated? -> Step 13 (create PR)
4. Tests written but some failing? -> Step 9 (fix failures)
5. Test branch exists with infrastructure? -> Step 7 (generate tests)
6. Test branch exists (empty)? -> Step 5 (infrastructure)
7. Strategy doc exists? -> Step 4 (create branch)
8. None -> Step 1 (start from scratch)

Announce the detected state before resuming: "Detected prior progress. Resuming at Step N."
