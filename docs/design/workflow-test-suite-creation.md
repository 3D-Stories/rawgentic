# Workflow: Test Suite Creation (WF12, v1.0)

**Date:** 2026-03-07
**Author:** Orchestrator (skill-creator evaluation loop)
**Purpose:** Define the workflow for bootstrapping test suites from scratch or auditing existing tests and filling coverage gaps across any programming language. WF12 uses brainstorming to design the testing strategy before writing code and context7 MCP for up-to-date framework documentation.

---

## Workflow: Test Suite Creation

**Invocation:** `/rawgentic:create-tests` or `/rawgentic:create-tests <path>`
**Trigger:** User wants to add tests, create a test suite, improve test coverage, or audit existing tests.

**Inputs:**
- Optional file/module path to scope the test creation
- Project config from `.rawgentic.json` (tech stack, existing testing frameworks)
- Filesystem scan for source files and existing test files

**Outputs:**
- Testing strategy design document (`docs/plans/YYYY-MM-DD-testing-strategy.md`)
- Test infrastructure (config files, dependencies, helpers, fixtures)
- Test files with meaningful assertions
- Coverage report (if framework supports it)
- Updated `.rawgentic.json` with discovered testing section
- PR with test results in description

**Tracking:** Branch `test/bootstrap-test-suite` or `test/improve-coverage`

**Principles enforced:**
- P1: Branch Isolation (test/ branch)
- P3: Frequent Local Commits (commit after each test batch)
- P8: Shift-Left Critique (brainstorming before implementation)
- P11: User-in-the-Loop (strategy approval before test generation)
- P12: Conventional Commit (`test: bootstrap test suite with <framework>`)
- P14: Documentation-Gated PRs (strategy doc + coverage report in PR)

---

## Mode Detection

WF12 operates in two modes, auto-detected from project state:

| Mode | Condition | Behavior |
|------|-----------|----------|
| **Greenfield** | No test frameworks in config AND no test files on disk | Full bootstrap: infrastructure + tests for entire project |
| **Coverage-gap** | Test frameworks in config OR test files found | Audit existing tests, identify gaps, fill them |

---

## Language Support

WF12 is language-agnostic, supporting 12 languages with default frameworks:

| Language | Default Framework | Config File | Coverage Tool |
|----------|------------------|-------------|---------------|
| Python | pytest | pyproject.toml | pytest-cov |
| JavaScript/TypeScript | vitest (or jest) | vitest.config.ts | v8/istanbul |
| Go | go test (stdlib) | none | go test -cover |
| Rust | cargo test (stdlib) | Cargo.toml | cargo-tarpaulin |
| C/C++ | GoogleTest / CTest | CMakeLists.txt | gcov/lcov |
| Shell/Bash | bats-core | none | kcov (optional) |
| Ruby | rspec | .rspec | simplecov |
| PHP | phpunit | phpunit.xml | phpunit --coverage |
| Java/Kotlin | JUnit 5 | build.gradle | jacoco |
| Swift | XCTest | Package.swift | xcodebuild |

Multi-language projects get per-language test infrastructure + a unified top-level runner.

---

## Steps (14)

| Step | Name | Loopback? |
|------|------|-----------|
| 1 | Receive Request and Detect Testing Landscape | No |
| 2 | Brainstorm Testing Strategy (via superpowers:brainstorming) | No |
| 3 | Resolve Framework Documentation via Context7 | No |
| 4 | Create Test Branch | No |
| 5 | Generate Test Infrastructure | No |
| 6 | Analyze Source Code for Test Targets | No |
| 7 | Generate Test Files | Target of loopbacks |
| 8 | Run Test Suite — First Pass | No |
| 9 | Fix Failing Tests | → Step 7 (max 2) |
| 10 | Coverage Analysis | → Step 7 (max 1) |
| 11 | Test Quality Review | → Step 7 (max 1) |
| 12 | Update .rawgentic.json | No |
| 13 | Create Pull Request | No |
| 14 | Completion Summary | No |

**Loopback budget:** Step 9→7 (max 2), Step 11→7 (max 1), global cap: 3

---

## Key Design Decisions

### Brainstorming-first strategy
Instead of jumping straight to writing tests, WF12 uses the `superpowers:brainstorming` skill to collaboratively design the testing strategy. This surfaces important decisions early: what to mock vs. test against real deps, which test types matter most, fixture strategy, and test organization.

### Context7 for fresh docs
Test framework APIs evolve. Using context7 MCP ensures generated tests follow current idioms rather than stale patterns from training data. Falls back to training knowledge for frameworks not in context7's index (bats-core, Go stdlib).

### Shell script testing via bats-core
Shell scripts are first-class test targets. The `<language-framework-map>` includes bats-core as the standard framework, with guidance on test helpers, mock commands via PATH manipulation, and focusing on exit codes, stdout/stderr, and file side effects.

### Source bugs flagged, not fixed
When a test correctly identifies a real bug in source code, WF12 documents it as a finding but does NOT fix it. Test creation and bug fixing are separate concerns — the fix should go through WF3 (bug fix workflow) with its own reproduce-first TDD cycle.

---

## Evaluation Results (Iteration 1)

Benchmarked against gpu-fan-controller project with 3 test cases:

| Metric | With Skill | Baseline | Delta |
|--------|-----------|----------|-------|
| Assertion Pass Rate | 100% | 78% | +22pp |
| Avg Time | 590s | 320s | +270s |
| Avg Tokens | 93.7K | 45.8K | +47.9K |

**Key differentiators over baseline:**
- Always produces a testing strategy document
- Selects standard frameworks (bats-core for shell vs. custom bash frameworks)
- Creates unified multi-language test runners
- Attempts context7 for fresh framework docs

Full eval results in `skills/create-tests-workspace/`.
