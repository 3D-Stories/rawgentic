# Phase 3 Workflow: Bug Fix (v1.0)

**Date:** 2026-03-02
**Author:** Orchestrator (direct authoring)
**Inputs:** phase2-principles.md, phase3-workflow-feature-implementation.md (WF2 v1.1), phase2b-official-comparison.md
**Purpose:** Define the streamlined workflow for fixing bugs reported via GitHub issues. WF3 is a specialized fast-path derivative of WF2 — same quality assurance framework, fewer steps, optimized for rapid turnaround.

---

## Workflow: Bug Fix

**Invocation:** `/fix-bug <issue-number>` skill (custom Claude Code skill)
**Trigger:** User invokes `/fix-bug <issue-number>` or `/fix-bug <issue-URL>`
**Inputs:**

- GitHub issue number or URL (bug report from WF1 or manual)
- Existing codebase context (CLAUDE.md, MEMORY.md, session notes)
- Phase 2 principles (quality gate placement)
- Runtime environment access (SSH to ${ENGINE_HOST} and ${DEV_HOST})

**Outputs:**

- Merged PR fixing the reported bug
- Updated/added test cases proving the fix
- Updated session notes
- Deployed and verified fix in dev environment

**Tracking:** GitHub issue is the SOLE requirements source. PR tracks implementation.

**Principles enforced:**

- P1: Branch Isolation (fix/ branch)
- P2: Code Formatting (automated before every commit)
- P3: Frequent Local Commits (every 5 minutes active work)
- P4: Regular Remote Sync (push to origin every 30 minutes)
- P5: TDD Enforcement (reproduce bug in test FIRST, then fix)
- P6: Main-to-Dev Sync (deploy after merge)
- P7: Triple-Gate Testing (local, CI, dev server)
- P8: Shift-Left Critique (lightweight reflect for bug fixes)
- P9: Continuous Memorization (memorize if fix reveals pattern)
- P11: User-in-the-Loop (circuit breaker on ambiguity)
- P12: Conventional Commit Discipline
- P13: Pre-PR Code Review (automated review)
- P14: Documentation-Gated PRs

**Diagram:** `diagrams/workflow-bug-fix.excalidraw`

**Termination:** WF3 terminates after deployment verification. No auto-transition.

---

## Relationship to WF2

WF3 is a **proper subset** of WF2. Specifically:

- WF2 Steps 1, 2 → WF3 Steps 1, 2 (receive issue, analyze)
- WF2 Step 3 (design) → WF3 Step 3 (root cause analysis — simpler scope)
- WF2 Step 4 (full critique) → WF3 Step 4 (lightweight reflect ONLY — bugs are low-reversal-cost)
- WF2 Steps 5-6 (plan + plan drift) → WF3 Step 5 (plan — no drift check for small fixes)
- WF2 Steps 7-8 (branch + TDD) → WF3 Steps 6-7 (branch + TDD with reproduce-first pattern)
- WF2 Step 9 (impl drift) → WF3 Step 8 (lightweight verification)
- WF2 Steps 10-11 (memorize + review) → WF3 Step 9 (combined review — memorize only if pattern-worthy)
- WF2 Steps 12-14 (PR + CI + merge) → WF3 Steps 10-12
- WF2 Step 15-16 (post-deploy + summary) → WF3 Steps 13-14

**Total: 14 steps** (vs WF2's 16). Dropped: separate plan drift gate and separate memorize step.

---

## Complexity Override

WF3 accepts bug reports of any complexity. However:

- If Step 2 classifies the bug as `complex_bug` (fix touches 10+ files, cross-service, unclear root cause), the workflow **upgrades to WF2** automatically. The user is informed: "This bug fix is complex enough to warrant the full feature implementation workflow. Switching to `/implement-feature`."
- If the user disagrees, they can override and stay in WF3.

---

## Bug-Specific TDD Pattern: Reproduce First

WF3 enforces a strict "reproduce first" TDD pattern in Step 7:

1. **Write a failing test** that reproduces the exact bug behavior described in the issue
2. **Run the test** — confirm it fails in a way that demonstrates the bug exists. In mocked environments, the specific symptom may differ from production — the key proof is that the broken behavior is demonstrated.
3. **Fix the code** — make the test pass
4. **Run full test suite** — confirm no regressions
5. **Add edge case tests** — cover related scenarios the original bug report hints at

This is stricter than WF2's general TDD flow because bugs have a concrete "before" state that MUST be captured in a test before fixing.

---

## Finding Application and Ambiguity Circuit Breaker

**Inherited from WF2 (identical behavior):** Apply ALL findings from quality gates automatically. Ambiguity circuit breaker ALWAYS active.

---

## Workflow Resumption and Session Interruption

**Checkpoint artifacts (same as WF2):**

| Artifact      | Location                       | Created at   | Purpose             |
| ------------- | ------------------------------ | ------------ | ------------------- |
| GitHub issue  | GitHub (remote)                | Before WF3   | Bug report source   |
| Fix branch    | Git (remote)                   | Step 6       | Code state          |
| RCA document  | Session notes                  | Step 3       | Root cause analysis |
| PR            | GitHub (remote)                | Step 10      | Review/merge state  |
| Session notes | `claude_docs/session_notes.md` | Continuously | Progress log        |

**Step detection on resume:**

1. PR merged? → Step 13
2. PR exists and CI passed? → Step 12
3. PR exists? → Step 11
4. Fix branch has passing tests? → Step 9
5. Fix branch has code changes? → Step 8
6. Fix branch exists (empty)? → Step 7
7. RCA in session notes? → Step 5
8. None → Step 1

---

## Global Loop-Back Budget

- Step 4 (reflect) → Step 3: max 1 iteration
- Step 9 (review) → Step 3: max 1 iteration
- **Global cap: 2 loop-backs per WF3 invocation.** Escalate to user if exceeded.

---

## Steps

### Step 1: Receive Bug Report Reference

**Type:** user decision
**Actor:** human
**Command:** `/fix-bug <issue-number>`
**Input:** GitHub issue number or URL
**Action:**

1. Validate issue exists via `gh issue view <number>`
2. Confirm issue is open and labeled as bug (or has bug report template format)
3. Display: title, steps to reproduce, expected vs actual behavior, environment
4. Ask user to confirm this is the correct bug to fix
5. If the issue lacks reproduction steps or expected behavior, ask user to provide them before proceeding

**Output:** Validated bug report: { issue_number, title, body, labels, reproduction_steps, expected_behavior, actual_behavior }
**Failure mode:** (1) Issue not found → ask for correct number. (2) Issue is not a bug → suggest WF2 instead. (3) Missing reproduction steps → ask user to provide them.
**Principle alignment:** P11 (User-in-the-Loop)
**User selection:** yes

---

### Step 2: Analyze Bug Context and Classify

**Type:** automated
**Actor:** Claude (via Serena MCP + Grep/Glob)
**Command:** Serena `find_symbol`, `find_referencing_symbols` + grep for error strings
**Input:** Bug report from Step 1, codebase context
**Action:**

1. **Reproduce path tracing:** Starting from the reported symptoms (error messages, unexpected behavior), trace the code path using Serena symbol navigation and grep for error strings/log messages
2. **Blast radius assessment:** Identify all files and functions in the call chain from entry point to bug location
3. **Test inventory:** Find existing tests covering the affected code paths
4. **Complexity classification:**
   - `simple_bug`: 1-3 files, clear root cause, no migration needed
   - `moderate_bug`: 4-10 files, root cause requires investigation, may need migration
   - `complex_bug`: 10+ files, cross-service, unclear root cause → upgrade to WF2
5. **Related issues check:** Search GitHub issues for similar bugs that might share a root cause

**Output:** Bug analysis: { affected_files, call_chain, existing_tests, test_gaps, complexity, related_issues, suspected_root_cause }
**Failure mode:** (1) Cannot reproduce from description → ask user for more details. (2) Bug is in a dependency, not our code → document and suggest upstream report. (3) Classified as `complex_bug` → prompt upgrade to WF2.
**Principle alignment:** P8 (Shift-Left)

---

### Step 3: Root Cause Analysis

**Type:** automated
**Actor:** Claude (codebase analysis)
**Input:** Bug analysis from Step 2, reproduction path
**Action:**

1. **Hypothesis generation:** Based on the code trace, generate 1-3 hypotheses for the root cause
2. **Evidence collection:** For each hypothesis, gather evidence from code, logs, and test behavior
3. **Root cause determination:** Select the hypothesis with strongest evidence
4. **Fix approach:** Design the minimal fix that addresses the root cause (not symptoms)
5. **Regression risk assessment:** Identify code paths that could break from the fix

**Output:** RCA document: { root_cause, evidence, fix_approach, files_to_modify, regression_risks }
**Failure mode:** (1) Multiple equally likely root causes → present to user for guidance. (2) Root cause is in a design flaw (not a code bug) → suggest WF2 for redesign. (3) Fix would be a band-aid → flag that proper fix may need WF2.
**Principle alignment:** P8 (Shift-Left Critique)

---

### Step 4: Quality Gate — Lightweight Reflect

**Type:** quality gate
**Actor:** Claude (single-pass reflection)
**Command:** `/reflexion:reflect`
**Input:** RCA document from Step 3, original bug report, codebase context
**Action:** Single-pass reflection checking:

1. Does the identified root cause actually explain ALL symptoms in the bug report?
2. Is the fix in the right layer (not a band-aid when the real issue is upstream)?
3. Are there unintended side effects of the proposed fix?
4. Does the fix handle edge cases mentioned in the bug report?
5. Is the fix backward-compatible (especially for API/DB changes)?

**Output:** Amended RCA (findings applied) OR blocked state (circuit breaker)
**Failure mode:** (1) Reflect finds the root cause is wrong → loop back to Step 3 (max 1 time). (2) Fix has significant side effects → suggest WF2 for broader approach.
**Principle alignment:** P8 (Shift-Left Critique) — lightweight reflect is appropriate for bugs because reversal cost is low (revert 1-10 files)
**Critique level:** Lightweight reflect ONLY. RATIONALE: Bug fixes have lower reversal cost than new features. A full 3-judge critique adds 2-3 minutes of latency for diminishing returns on small-scope changes.

---

### Step 5: Create Fix Plan

**Type:** automated
**Actor:** Claude
**Command:** `/superpowers:writing-plans`
**Input:** Amended RCA from Step 4, codebase analysis from Step 2
**Action:**

1. Break the fix into ordered TDD tasks:
   - Task 1: Write failing reproduction test
   - Task 2: Implement the fix (minimal change)
   - Task 3: Add regression/edge case tests
   - Task 4: Update documentation if behavior changes
2. Document the fix branch name: `fix/<issue-number>-<short-desc>`
3. Estimate: most bugs should have 3-6 tasks

**Output:** Fix plan with ordered tasks, file paths, and test expectations
**Failure mode:** (1) Plan reveals fix is larger than expected → suggest upgrading to WF2.
**Principle alignment:** P5 (TDD Enforcement)

---

### Step 6: Create Fix Branch

**Type:** automated
**Actor:** Claude
**Command:** `git checkout -b fix/<issue-number>-<short-desc>`
**Input:** Branch name from Step 5
**Action:**

1. Ensure main is up to date: `git fetch origin main`
2. Create branch from main: `git checkout -b fix/<issue-number>-<short-desc> origin/main`
3. Verify branch created successfully

**Output:** Active fix branch
**Principle alignment:** P1 (Branch Isolation)

---

### Step 7: TDD Bug Fix (Reproduce-First Pattern)

**Type:** automated
**Actor:** Claude (via `/superpowers:test-driven-development` + `/superpowers:executing-plans`)
**Input:** Fix plan from Step 5, codebase context
**Action:** Execute the plan using strict reproduce-first TDD:

1. **RED — Reproduction test:** Write a test that captures the exact bug behavior. Run it — it MUST fail in a way that demonstrates the bug exists. In mocked environments, the specific status code or error may differ from production — the key proof is that the broken behavior is demonstrated. If the test passes, the bug may already be fixed or the test doesn't capture the right behavior.
2. **GREEN — Minimal fix:** Make the reproduction test pass with the smallest possible code change. Resist the urge to refactor surrounding code.
3. **REFACTOR (minimal):** Only refactor if the fix introduced obvious code smells. Bug fix PRs should be focused, not cleanup opportunities.
4. **Regression tests:** Add 2-3 edge case tests around the fix boundary.
5. **Full suite:** Run `pytest` (engine) and/or `vitest` (dashboard) to confirm no regressions.
6. **Commit frequently:** Follow P3 (every 5 min) and P12 (conventional commits): `fix(scope): brief description`

**Output:** Fixed code with passing tests on fix branch
**Failure mode:** (1) Reproduction test passes immediately → bug may not be reproducible. Ask user. (2) Fix breaks other tests → investigate shared state or wrong approach. (3) Fix requires changes beyond plan scope → flag and decide: expand plan or split into multiple fixes.
**Principle alignment:** P5 (TDD), P3 (Frequent Commits), P12 (Conventional Commits)

---

### Step 8: Lightweight Verification

**Type:** quality gate (lightweight)
**Actor:** Claude
**Command:** Quick self-check (no sub-agent)
**Input:** Code changes on fix branch, original bug report, fix plan
**Action:**

1. Verify all acceptance criteria from the bug report are addressed
2. Verify the reproduction test genuinely captures the original bug
3. Verify no unrelated changes crept in (`git diff --stat` should show only planned files)
4. Verify all tests pass

**Output:** Verification pass/fail
**Failure mode:** (1) Unrelated changes detected → `git checkout -- <file>` to revert strays. (2) Missing acceptance criteria → add tests/code for missed items.
**Principle alignment:** P8 (Shift-Left)

---

### Step 9: Code Review + Conditional Memorize

**Type:** automated
**Actor:** sub-agent (code review) + conditional memorize
**Command:** `/code-review:code-review` (4 specialized reviewers) + conditional `/reflexion:memorize`
**Input:** All changes on fix branch
**Action:**

1. **Code review:** Launch the 4-agent review (type-design, silent-failure, simplifier, code-reviewer). For bug fixes, focus on: (a) is the fix correct and complete, (b) are there any new silent failures, (c) is the code simple and focused.
2. **Conditional memorize:** If the bug fix reveals a pattern worth remembering (new pitfall, gotcha, or recurring issue), run `/reflexion:memorize` to curate insights into CLAUDE.md. Skip if the fix is routine.
3. **Apply findings:** Auto-apply review findings. Circuit breaker on ambiguity.

**Output:** Review-clean code + optional CLAUDE.md updates
**Failure mode:** (1) Review finds fundamental flaw → loop back to Step 3 (max 1 time). (2) Review agents hit rate limit → log partial results, resume after reset.
**Principle alignment:** P13 (Pre-PR Code Review), P9 (Continuous Memorization)

---

### Step 10: Create Pull Request

**Type:** automated
**Actor:** Claude
**Command:** `/commit-commands:commit-push-pr`
**Input:** Review-clean code on fix branch, bug report reference
**Action:**

1. Stage all changes: `git add <specific files>`
2. Create final commit (if needed) with conventional format: `fix(scope): description (closes #<issue>)`
3. Push branch: `git push -u origin fix/<issue-number>-<short-desc>`
4. Create PR via `gh pr create`:
   - Title: `fix(scope): description`
   - Body: Bug report reference, root cause summary, fix description, test plan
   - Labels: bug, size/S or size/M
5. Gate 1: PR creation confirms all tests pass locally

**Output:** PR URL
**Principle alignment:** P12 (Conventional Commits), P14 (Documentation-Gated)

---

### Step 11: CI Verification (Gate 2)

**Type:** automated
**Actor:** GitHub Actions
**Command:** `gh run list` / `gh run view <id>` (monitor CI)
**Input:** PR from Step 10
**Action:**

1. Wait for CI pipeline to complete (check via `gh run list --branch <branch>`)
2. If CI passes → proceed to Step 12
3. If CI fails → analyze failure, fix, push, and re-check (max 2 retries)

**Output:** CI pass/fail status
**Failure mode:** (1) CI flaky failure → retry once. (2) Genuine test failure → fix and push. (3) CI timeout → wait and check again.
**Principle alignment:** P7 (Triple-Gate Testing — Gate 2)

---

### Step 12: Merge and Deploy (Gate 3)

**Type:** automated
**Actor:** Claude
**Command:** `gh pr merge --squash` + deploy script
**Input:** CI-passing PR from Step 11
**Action:**

1. Squash-merge PR: `gh pr merge <number> --squash --delete-branch`
2. Deploy to dev via deploy script (auto-triggered by push hook, or manual)
3. Verify deployment health

**Output:** Merged PR + deployed dev environment
**Failure mode:** (1) Merge conflicts → rebase on main, resolve, push. (2) Deploy fails → check docker logs, rollback if needed.
**Principle alignment:** P6 (Main-to-Dev Sync), P7 (Triple-Gate — Gate 3)

---

### Step 13: Post-Deploy Verification

**Type:** quality gate
**Actor:** Claude
**Command:** `/reflexion:reflect` (focused on "does the fix work in production-like environment?")
**Input:** Deployed changes, original bug report reproduction steps
**Action:**

1. **Symptom verification:** Check that the original bug symptoms no longer occur in the dev environment
2. **E2E verification (if applicable):** Run relevant E2E tests on the dev server
3. **Health check:** Verify all services are healthy after deployment
4. **Reflect:** Quick pass — does the deployed fix match what was intended?

**Output:** Deployment verified OR rollback needed
**Failure mode:** (1) Bug still reproduces in dev → investigate env-specific differences. (2) New issues introduced → rollback via `git revert` on main.
**Principle alignment:** P7 (Triple-Gate — Gate 3 verification)

---

### Step 14: Completion Summary

**Type:** automated
**Actor:** Claude
**Command:** Update session notes + close issue
**Input:** All prior artifacts
**Action:**

1. Update `claude_docs/session_notes.md` with fix summary
2. Close GitHub issue with closing comment: root cause, fix description, PR link
3. Present summary to user: issue, root cause, fix approach, PR link, verification status

**Output:** Closed issue + session notes updated
**Principle alignment:** P14 (Documentation)

---

## Design Decisions

### D1: 14 Steps (vs WF2's 16)

**Rationale:** Bug fixes don't need a separate plan drift check (Step 6 in WF2) because the plan is smaller and divergence risk is low. Memorization is conditional and combined with code review (not a separate step). This saves 2 steps without losing quality assurance.

### D2: Lightweight Reflect Only (No Full Critique)

**Rationale:** Bug fixes have low reversal cost. The full 3-judge critique adds 2-3 minutes for diminishing returns on 1-10 file changes. A single-pass reflect catches the important issues (wrong root cause, band-aid fix, side effects) at fraction of the cost.

### D3: Reproduce-First TDD

**Rationale:** Bugs have a concrete "before" state. Capturing this in a failing test BEFORE fixing ensures: (a) the fix actually addresses the reported bug, (b) the bug cannot regress silently, (c) the test documents the bug for future developers.

### D4: Complexity Upgrade to WF2

**Rationale:** Some "bugs" are actually missing features or design flaws. When Step 2 classifies a bug as `complex_bug` (10+ files, cross-service), the fix needs the full WF2 pipeline with design critique and multi-PR strategy. WF3 explicitly hands off to WF2 rather than trying to handle complexity it wasn't designed for.

### D5: No Separate Diagram Step

**Rationale:** Bug fixes almost never require architecture diagrams. If a bug fix reveals an architectural issue, it should be handled via WF2 (which has diagram support in Step 3).

---

## Principle Coverage Matrix

| Principle                | Enforced    | How                            |
| ------------------------ | ----------- | ------------------------------ |
| P1 Branch Isolation      | Yes         | Step 6: `fix/` branch          |
| P2 Code Formatting       | Yes         | Automated in commits           |
| P3 Frequent Commits      | Yes         | Step 7 TDD cycle               |
| P4 Remote Sync           | Yes         | Push during Step 7, Step 10    |
| P5 TDD Enforcement       | Yes         | Step 7: reproduce-first        |
| P6 Main-to-Dev Sync      | Yes         | Step 12: deploy script         |
| P7 Triple-Gate           | Yes         | Steps 8, 11, 13                |
| P8 Shift-Left Critique   | Yes         | Step 4: reflect                |
| P9 Memorization          | Conditional | Step 9: only if pattern-worthy |
| P10 Diagram-Driven       | N/A         | Bug fixes rarely need diagrams |
| P11 User-in-the-Loop     | Yes         | Steps 1, 4 (circuit breaker)   |
| P12 Conventional Commits | Yes         | Step 7, 10                     |
| P13 Pre-PR Review        | Yes         | Step 9                         |
| P14 Documentation-Gated  | Yes         | Step 10, 14                    |
