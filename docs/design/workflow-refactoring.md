# Phase 3 Workflow: Refactoring (v1.0)

**Date:** 2026-03-02
**Author:** Orchestrator (direct authoring)
**Inputs:** phase2-principles.md, phase3-workflow-feature-implementation.md (WF2 v1.1), phase2b-official-comparison.md
**Purpose:** Define the workflow for improving code structure without changing external behavior. WF4 emphasizes behavioral preservation through comprehensive testing before any code changes.

---

## Workflow: Refactoring

**Invocation:** `/refactor <scope-description>` skill (custom Claude Code skill)
**Trigger:** User invokes `/refactor "extract order execution post-trade steps"` or `/refactor <issue-number>`
**Inputs:**

- Refactoring scope description (free text) or GitHub issue number
- Existing codebase context (CLAUDE.md, MEMORY.md, session notes)
- Phase 2 principles
- Runtime environment access

**Outputs:**

- Merged PR with refactored code
- Updated/added test cases proving behavioral preservation
- Updated documentation (CLAUDE.md, MEMORY.md if patterns changed)
- Deployed and verified refactoring in dev environment

**Tracking:** GitHub issue (if exists) or session notes (if ad-hoc refactoring).

**Principles enforced:**

- P1: Branch Isolation (refactor/ branch)
- P2: Code Formatting
- P3: Frequent Local Commits
- P4: Regular Remote Sync
- P5: TDD Enforcement (characterization tests FIRST)
- P6: Main-to-Dev Sync
- P7: Triple-Gate Testing
- P8: Shift-Left Critique (full critique for structural refactoring)
- P9: Continuous Memorization
- P11: User-in-the-Loop
- P12: Conventional Commit Discipline
- P13: Pre-PR Code Review
- P14: Documentation-Gated PRs

**Diagram:** `diagrams/workflow-refactoring.excalidraw`

**Termination:** WF4 terminates after deployment verification. No auto-transition.

---

## Refactoring Categories

| Category        | Scope                                        | Critique Level      | Example                                             |
| --------------- | -------------------------------------------- | ------------------- | --------------------------------------------------- |
| **Rename**      | 1-5 files, symbol renames                    | Lightweight reflect | Rename `_post_trade_steps` to `_finalize_trade`     |
| **Extract**     | 3-15 files, pull function/class/module       | Full critique       | Extract shared validation logic into utils          |
| **Restructure** | 10+ files, move modules, change architecture | Full critique       | Split monolithic strategy into strategy + evaluator |
| **Simplify**    | 1-10 files, reduce complexity                | Lightweight reflect | Replace nested ifs with guard clauses               |

Step 2 classifies the refactoring into one of these categories. The category determines the critique level at Step 4.

---

## Core Invariant: No Behavior Change

WF4's defining constraint is **behavioral preservation**. Every refactoring must prove:

1. All existing tests pass without modification (tests that verify internal implementation may be updated, but tests that verify external behavior must remain unchanged)
2. API contracts are identical (same request/response shapes, same error codes)
3. Database schemas are unchanged (no migrations)
4. Redis pub/sub message formats are unchanged
5. CLI/skill invocation interfaces are unchanged

If a refactoring requires behavior changes, it is NOT a refactoring — redirect to WF2.

---

## Characterization Testing Strategy

Before modifying any code, WF4 requires **characterization tests** that capture current behavior:

1. **Snapshot existing behavior:** Write tests that call the code being refactored and assert on its exact current outputs (including edge cases)
2. **Run characterization tests:** They MUST all pass against the CURRENT code (proving they accurately capture behavior)
3. **Perform refactoring:** Modify the code structure
4. **Re-run characterization tests:** They MUST still pass after refactoring (proving behavior is preserved)
5. **Clean up:** Characterization tests may be kept (if they add coverage) or removed (if they duplicate existing tests)

This is stricter than WF2/WF3's TDD because refactoring has no "new behavior" to test for — only preservation.

---

## Finding Application and Ambiguity Circuit Breaker

**Inherited from WF2 (identical behavior):** Apply ALL findings automatically. Circuit breaker on ambiguity.

---

## Workflow Resumption and Session Interruption

**Checkpoint artifacts:**

| Artifact              | Location                       | Created at   | Purpose             |
| --------------------- | ------------------------------ | ------------ | ------------------- |
| GitHub issue (if any) | GitHub (remote)                | Before WF4   | Scope definition    |
| Refactor branch       | Git (remote)                   | Step 6       | Code state          |
| Refactoring plan      | Session notes                  | Step 3       | Approach reference  |
| Char. tests snapshot  | Git (committed)                | Step 7a      | Behavioral baseline |
| PR                    | GitHub (remote)                | Step 10      | Review/merge state  |
| Session notes         | `claude_docs/session_notes.md` | Continuously | Progress log        |

**Step detection on resume:**

1. PR merged? → Step 13
2. PR exists and CI passed? → Step 12
3. PR exists? → Step 11
4. Refactor branch has refactored code with passing tests? → Step 9
5. Refactor branch has characterization tests committed? → Step 7b (start refactoring)
6. Refactor branch exists (empty)? → Step 7a (write characterization tests)
7. Refactoring plan in session notes? → Step 5
8. None → Step 1

---

## Global Loop-Back Budget

- Step 4 (critique) → Step 3: max 2 iterations (Extract/Restructure) or max 1 (Rename/Simplify)
- Step 9 (review) → Step 7b: max 1 iteration (behavioral change found → re-execute refactoring)
- **Global cap: 3 loop-backs per WF4 invocation.**

---

## Steps

### Step 1: Receive Refactoring Scope

**Type:** user decision
**Actor:** human
**Command:** `/refactor <scope>` or `/refactor <issue-number>`
**Input:** Free-text description or GitHub issue number
**Action:**

1. If issue number provided, fetch and display via `gh issue view`
2. If free text, confirm scope understanding with user
3. Verify the scope is a true refactoring (no behavior changes)
4. If scope implies behavior changes, suggest WF2 instead

**Output:** Validated scope: { description, issue_number (optional), affected_area }
**Failure mode:** (1) Scope is actually a feature → redirect to WF2. (2) Scope too vague → ask for specifics.
**Principle alignment:** P11 (User-in-the-Loop)
**User selection:** yes

---

### Step 2: Analyze Code Structure and Classify

**Type:** automated
**Actor:** Claude (Serena MCP + Grep/Glob)
**Command:** Serena `find_symbol`, `get_symbols_overview`, `find_referencing_symbols`
**Input:** Scope from Step 1, codebase context
**Action:**

1. **Symbol mapping:** Map all symbols (functions, classes, methods) in the refactoring scope
2. **Reference graph:** Trace all callers and callees of affected symbols
3. **Test coverage assessment:** Identify existing tests for affected code. Note coverage gaps.
4. **Coupling analysis:** Measure how tightly the code is coupled (number of cross-module references)
5. **Category classification:** Classify as Rename, Extract, Restructure, or Simplify based on scope
6. **Risk assessment:** Rate refactoring risk (low/medium/high) based on coupling and test coverage

**Output:** Analysis: { symbols, reference_graph, existing_tests, test_gaps, category, risk, coupling_score }
**Failure mode:** (1) Scope is larger than expected → inform user of true blast radius. (2) No existing tests → high risk, suggest adding tests first.
**Principle alignment:** P8 (Shift-Left)

---

### Step 3: Design Refactoring Approach

**Type:** automated
**Actor:** Claude
**Command:** `/superpowers:brainstorming` (for Extract/Restructure) or inline analysis (for Rename/Simplify)
**Input:** Analysis from Step 2, scope definition
**Action:**

1. **For Rename/Simplify:** Document the rename map or simplification approach. List every file and symbol affected.
2. **For Extract/Restructure:**
   - Generate 2-3 approaches with trade-offs
   - Recommend approach with rationale
   - Design the target structure (new files, new symbols, moved code)
   - Document the migration path (order of changes to keep tests passing at each step)
3. **Behavioral contract:** Explicitly list the behaviors that MUST be preserved (API contracts, DB interactions, pub/sub messages)

**Output:** Refactoring design: { approach, target_structure, migration_steps, behavioral_contracts }
**Failure mode:** (1) No safe migration path exists (all paths break tests temporarily) → suggest incremental approach or user guidance. (2) Design reveals the code is too coupled to refactor safely → document and present trade-offs.
**Principle alignment:** P8 (Shift-Left), P10 (Diagram-Driven — for Restructure category)

---

### Step 4: Quality Gate — Design Critique

**Type:** quality gate
**Actor:** sub-agent (critique or reflect depending on category)
**Command:** `/reflexion:critique` (Extract/Restructure) OR `/reflexion:reflect` (Rename/Simplify)
**Input:** Refactoring design from Step 3, analysis from Step 2
**Action:**

**For Extract/Restructure (full critique):**
Three judges evaluate:

- Does the refactoring preserve all documented behavioral contracts?
- Is the target structure actually better (reduced coupling, improved cohesion)?
- Is the migration path safe (tests pass at every intermediate step)?
- Are there hidden behavioral changes masquerading as "refactoring"?

**For Rename/Simplify (lightweight reflect):**
Single-pass checking:

- Are all references updated? (Serena `find_referencing_symbols` cross-check)
- Any behavioral side effects of the rename/simplification?

**Output:** Amended design OR blocked state (circuit breaker)
**Principle alignment:** P8 (Shift-Left Critique)
**Critique level:** Proportional to risk. Restructure (high risk, high reversal cost) gets full critique. Rename (low risk) gets reflect.

---

### Step 5: Create Refactoring Plan

**Type:** automated
**Actor:** Claude
**Command:** `/superpowers:writing-plans`
**Input:** Amended design from Step 4
**Action:**

1. Order tasks to keep tests passing at each step:
   - Phase A: Write characterization tests (before any code changes)
   - Phase B: Perform refactoring (structure changes)
   - Phase C: Clean up (remove temporary scaffolding, update docs)
2. Each task has: file path, action description, expected test result after completion
3. Branch name: `refactor/<issue-number>-<short-desc>` or `refactor/<short-desc>`

**Output:** Ordered task list with behavioral verification points
**Principle alignment:** P5 (TDD)

---

### Step 6: Create Refactoring Branch

**Type:** automated
**Actor:** Claude
**Command:** `git checkout -b refactor/<desc>`
**Input:** Branch name from Step 5
**Action:**

1. `git fetch origin main`
2. `git checkout -b refactor/<desc> origin/main`
3. Verify branch created

**Output:** Active refactoring branch
**Principle alignment:** P1 (Branch Isolation)

---

### Step 7a: Write Characterization Tests

**Type:** automated
**Actor:** Claude (TDD)
**Command:** Execute Phase A of plan
**Input:** Plan Phase A, codebase analysis from Step 2
**Action:**

1. For each symbol being refactored, write tests that capture its CURRENT behavior:
   - Input/output pairs for functions
   - State transitions for classes
   - API request/response pairs for endpoints
   - DB query results for data access functions
2. Run all characterization tests — they MUST pass against current (unmodified) code
3. Commit characterization tests separately: `test(scope): add characterization tests for <refactoring>`

This commit is the behavioral baseline. If anything breaks during refactoring, the characterization tests will catch it.

**Output:** Characterization tests committed on refactoring branch, all passing
**Failure mode:** (1) Can't write characterization tests because code has side effects (network calls, file I/O) → use mocking. (2) Tests reveal existing bugs → document but don't fix (that's WF3's job).
**Principle alignment:** P5 (TDD — tests before code changes)

---

### Step 7b: Execute Refactoring

**Type:** automated
**Actor:** Claude (via `/superpowers:executing-plans`)
**Input:** Plan Phase B, characterization tests as safety net
**Action:**

1. Execute each refactoring task in order
2. After EACH task: run full test suite (characterization + existing tests)
3. If ANY test fails: stop, diagnose, and fix before proceeding
4. Commit after each logical step: `refactor(scope): <description>`
5. Push regularly (P4)

**Output:** Refactored code with all tests passing on branch
**Failure mode:** (1) Test fails after a refactoring step → undo that step, re-analyze. (2) Refactoring is harder than planned → flag for user. (3) Tests pass but behavior has subtly changed (not caught by tests) → this is why characterization tests must be thorough.
**Principle alignment:** P5 (TDD), P3 (Frequent Commits), P12 (Conventional Commits)

---

### Step 8: Post-Refactoring Verification

**Type:** quality gate (lightweight)
**Actor:** Claude
**Command:** Quick self-check
**Input:** Refactored code, characterization tests, original scope
**Action:**

1. All characterization tests still pass
2. All existing tests still pass
3. No unrelated changes in `git diff --stat`
4. All behavioral contracts from Step 3 are preserved
5. No new dependencies added
6. No database migration files created (if any exist, this isn't a pure refactoring)

**Output:** Verification pass/fail
**Principle alignment:** P8 (Shift-Left)

---

### Step 9: Code Review + Memorize

**Type:** automated
**Actor:** sub-agent (4-agent code review) + `/reflexion:memorize`
**Command:** `/code-review:code-review` + `/reflexion:memorize`
**Input:** All changes on refactoring branch
**Action:**

1. **Code review:** 4-agent review focused on: (a) behavioral preservation, (b) improved code quality, (c) no silent failures introduced, (d) style consistency
2. **Memorize:** Refactorings often reveal patterns worth documenting. Run `/reflexion:memorize` to capture: new code patterns, deprecated patterns, architectural decisions.
3. **Apply findings:** Auto-apply. Circuit breaker on ambiguity.

**Output:** Review-clean code + CLAUDE.md/MEMORY.md updates
**Failure mode:** (1) Review finds behavioral change → loop back to Step 7b. (2) Review finds the refactoring didn't actually improve things → discuss with user.
**Principle alignment:** P13 (Pre-PR Review), P9 (Memorization)

---

### Step 10: Create Pull Request

**Type:** automated
**Actor:** Claude
**Command:** `/commit-commands:commit-push-pr`
**Input:** Reviewed code on refactoring branch
**Action:**

1. Final commit if needed: `refactor(scope): description`
2. Push: `git push -u origin refactor/<desc>`
3. Create PR:
   - Title: `refactor(scope): description`
   - Body: What was refactored, why, behavioral preservation evidence (test results), before/after structure
   - Labels: refactoring, size/S|M|L

**Output:** PR URL
**Principle alignment:** P12, P14

---

### Step 11: CI Verification (Gate 2)

**Type:** automated
**Actor:** GitHub Actions
**Command:** Monitor CI via `gh run list`
**Input:** PR from Step 10
**Action:** Same as WF3 Step 11 — wait for CI, fix failures, retry (max 2).
**Principle alignment:** P7 (Gate 2)

---

### Step 12: Merge and Deploy (Gate 3)

**Type:** automated
**Actor:** Claude
**Command:** `gh pr merge --squash` + deploy script
**Input:** CI-passing PR
**Action:** Same as WF3 Step 12 — squash-merge, deploy, verify health.
**Principle alignment:** P6, P7 (Gate 3)

---

### Step 13: Post-Deploy Verification

**Type:** quality gate
**Actor:** Claude
**Command:** `/reflexion:reflect`
**Input:** Deployed refactored code, behavioral contracts
**Action:**

1. All services healthy post-deployment
2. Quick smoke test of refactored functionality
3. No new errors in logs
4. E2E tests pass (if applicable)

**Output:** Deployment verified
**Principle alignment:** P7 (Gate 3 verification)

---

### Step 14: Completion Summary

**Type:** automated
**Actor:** Claude
**Input:** All artifacts
**Action:**

1. Update session notes with refactoring summary
2. Close GitHub issue (if exists) with PR reference
3. Present summary: scope, approach, behavioral preservation evidence, PR link

**Output:** Session notes updated, issue closed
**Principle alignment:** P14

---

## Design Decisions

### D1: Characterization Tests Before Code Changes

**Rationale:** Refactoring's promise is "same behavior, better structure." Without characterization tests, you're relying on existing test coverage (which may be incomplete). Writing explicit behavioral tests BEFORE changing code creates a safety net specific to the refactoring scope. This is the single most important step in WF4.

### D2: Category-Based Critique Level

**Rationale:** A rename (low risk, easy to revert) doesn't warrant 3-judge debate. A restructure (high risk, many files, hard to revert) does. Matching critique cost to refactoring risk avoids over-engineering simple changes.

### D3: No Database Migrations

**Rationale:** If a "refactoring" requires schema changes, it's changing external behavior (query interfaces, data shapes). This is a feature change disguised as a refactoring — redirect to WF2.

### D4: Separate Characterization Test Commit

**Rationale:** Committing characterization tests separately (before refactoring) creates a clear git history: the behavioral baseline commit, then the structural change commits. If anything goes wrong, you can `git diff` between the baseline and current state to see exactly what changed.

### D5: Memorize Always Runs

**Rationale:** Unlike WF3 (where memorize is conditional), refactorings ALWAYS produce patterns worth documenting: what was the old pattern, what's the new pattern, why the change was made. This prevents the next developer (or Claude session) from re-introducing the old pattern.

---

## Principle Coverage Matrix

| Principle                | Enforced    | How                                  |
| ------------------------ | ----------- | ------------------------------------ |
| P1 Branch Isolation      | Yes         | Step 6: `refactor/` branch           |
| P2 Code Formatting       | Yes         | Automated in commits                 |
| P3 Frequent Commits      | Yes         | Step 7b                              |
| P4 Remote Sync           | Yes         | During Step 7b, Step 10              |
| P5 TDD Enforcement       | Yes         | Steps 7a-7b: characterization-first  |
| P6 Main-to-Dev Sync      | Yes         | Step 12                              |
| P7 Triple-Gate           | Yes         | Steps 8, 11, 13                      |
| P8 Shift-Left Critique   | Yes         | Step 4 (full or reflect by category) |
| P9 Memorization          | Yes         | Step 9 (always)                      |
| P10 Diagram-Driven       | Conditional | Only for Restructure category        |
| P11 User-in-the-Loop     | Yes         | Step 1, circuit breakers             |
| P12 Conventional Commits | Yes         | Steps 7a, 7b, 10                     |
| P13 Pre-PR Review        | Yes         | Step 9                               |
| P14 Documentation-Gated  | Yes         | Steps 10, 14                         |
