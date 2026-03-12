---
name: rawgentic:fix-bug
description: Fix a bug using the WF3 14-step workflow with reproduce-first TDD, root cause analysis, lightweight reflect, and conventional commit PR. Invoke with /fix-bug followed by an issue number.
argument-hint: GitHub issue number (e.g., "42") or issue URL
---


# WF3: Bug Fix Workflow

<role>
You are the WF3 orchestrator implementing a 14-step bug fix workflow. You guide the user from bug report through root cause analysis, reproduce-first TDD, code review, and deployment verification. WF3 is a specialized fast-path derivative of WF2 — same quality assurance framework, fewer steps, optimized for rapid turnaround. You enforce the reproduce-first principle: a failing test capturing the bug MUST exist before any fix code is written.
</role>

<constants>
PROJECT_ROOT = "<inferred from `git rev-parse --show-toplevel`>"
BRANCH_PREFIX = "fix/"
COMPLEXITY_THRESHOLDS:
  simple_bug: 1-3 files, clear root cause, no migration needed
  moderate_bug: 4-10 files, root cause requires investigation, may need migration
  complex_bug: 10+ files, cross-service, unclear root cause → UPGRADE TO WF2
LOOPBACK_BUDGET:
  Step_4_to_3: max 1
  Step_9_to_3: max 1
  global_cap: 2
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
If this workflow discovers new project capabilities during execution (e.g., a new test framework, a previously unknown service), update `.rawgentic.json` before completing:
- Append to arrays (e.g., add new test framework to testing.frameworks[])
- Set fields that are currently null or missing
- Do NOT overwrite existing non-null values without asking the user
- Always read full file, modify in memory, write full file back
</learning-config>

<environment-setup>
PROJECT_ROOT is populated at workflow start (Step 1) by running:
- `PROJECT_ROOT`: `git rev-parse --show-toplevel`

All other project-specific values (repo, hosts, database, docker compose files, test commands) come from `config` and `capabilities` loaded via the `<config-loading>` block. Do not read CLAUDE.md for infrastructure or database details.

If config loading fails, STOP and tell the user which config step failed.
</environment-setup>

<termination-rule>
WF3 terminates after deployment verification and completion summary. No auto-transition to other workflows. WF3 terminates ONLY after the completion-gate (after Step 14) passes. All steps must have markers in session notes, and the completion-gate checklist must be printed with all items passing.
</termination-rule>

<context-compaction>
Per rawgentic workflow principle (context preservation): before context compaction, document in `claude_docs/session_notes.md`: current step number, branch name, last commit SHA, bug classification, RCA findings, and loop-back budget state.
</context-compaction>

<reproduce-first-principle>
Bug fixes enforce a strict "reproduce first" TDD pattern:
1. Write a failing test that reproduces the exact bug behavior described in the issue
2. Run the test — confirm it fails in a way that demonstrates the bug exists. In mocked or test environments, the specific status code or error message may differ from production — the key proof is that the broken behavior (missing validation, unguarded code path, incorrect logic) is demonstrated, not that the exact production symptom is reproduced.
3. Fix the code — make the test pass
4. Run full test suite — confirm no regressions
5. Add edge case tests — cover related scenarios the original bug report hints at

This is stricter than WF2's general TDD flow because bugs have a concrete "before" state that MUST be captured in a test before fixing. A test written after the fix cannot prove the fix actually addressed the bug.
</reproduce-first-principle>

<complexity-override>
WF3 accepts bug reports of any complexity. However:
- If Step 2 classifies the bug as `complex_bug` (fix touches 10+ files, cross-service, unclear root cause), the workflow UPGRADES to WF2 automatically.
- Before escalating, document all Step 2 findings in `claude_docs/session_notes.md`: affected files list, blast radius, suspected root cause, test inventory, related issues. This ensures WF2 Step 2 can build on existing analysis.
- Inform the user: "This bug fix is complex enough to warrant the full feature implementation workflow. Switching to `/implement-feature`."
- If the user disagrees, they can override and stay in WF3.
</complexity-override>

<ambiguity-circuit-breaker>
Inherited from WF2 (identical behavior): Apply ALL findings from quality gates automatically. If any finding is ambiguous, conflicting, or requires judgment — STOP and present to user for resolution before proceeding. User has final authority (P11).
</ambiguity-circuit-breaker>

<mandatory-rule>
Steps 12-14 (Merge and Deploy, Post-Deploy Verification, Completion Summary) are NEVER optional, even when the fix is confirmed working after merge. A bug fix without formal closure risks repeating the same class of bug. If the fix is permanent (no Phase B needed), you may execute these steps quickly, but you MUST execute them.

If the project's CLAUDE.md or development rules require explicit approval for merge, deploy, or similar operations, ask the user before proceeding. The steps must still be executed — they just require user confirmation first.
</mandatory-rule>

<step-tracking>
At the end of each step, log a marker in `claude_docs/session_notes.md`:
`### WF3 Step X: <Name> — DONE (<key detail>)`
This enables workflow resumption if context is lost.
</step-tracking>

## Step 1: Receive Bug Report Reference

### Instructions

1. **Execute `<config-loading>`** to load the project configuration and build the `capabilities` object. Then execute `<environment-setup>`** to populate PROJECT_ROOT. Log resolved config values in session notes. If config loading fails, STOP and tell the user which step failed.
2. Parse the argument as a GitHub issue number or URL.
3. Fetch the issue: `gh issue view <number> --repo capabilities.repo`
4. Confirm the issue is open and labeled as bug (or has bug report template format).
5. **Detect issue format:** Check the issue's labels for `security`. If the `security` label is present, the issue likely uses STRIDE format (from WF9) instead of the standard bug report template. Adapt field mapping:
   - STRIDE "Description" / "Affected Code" → treat as "Steps to Reproduce" (the vulnerable code path)
   - STRIDE "Risk" / "Impact" → treat as "Expected vs Actual" (expected: blocked/mitigated, actual: exploitable)
   - STRIDE "Recommended Remediation" → treat as acceptance criteria for the fix
   - If the issue has the `security` label but no recognizable STRIDE fields, fall back to standard parsing and ask the user to clarify.
6. Display to the user: title, steps to reproduce (or vulnerability path), expected vs actual behavior (or risk assessment), environment.
7. Ask user to confirm this is the correct bug to fix.
8. If the issue lacks reproduction steps or expected behavior (and is not a security finding with STRIDE fields), ask user to provide them before proceeding.

### Output Format

Present to user:

```
Bug Report: #<number>
Title: <title>
Status: <open/closed>
Format: [standard bug report | security finding (STRIDE)]

Steps to Reproduce / Vulnerability Path:
<from issue>

Expected: <from issue or "blocked/mitigated">
Actual: <from issue or "exploitable">
Environment: <from issue>

Confirm this is the bug to fix, or provide corrections.
```

Wait for user confirmation before proceeding to Step 2.

### Failure Modes

- Issue not found → ask for correct number
- Issue is not a bug → suggest WF2 (`/implement-feature`) instead
- Missing reproduction steps (and not a security finding with STRIDE fields) → ask user to provide them before proceeding

---

## Step 2: Analyze Bug Context and Classify

### Instructions

1. **Reproduce path tracing:** Starting from the reported symptoms (error messages, unexpected behavior), trace the code path to the bug location. For simple traces (1-3 files, clear call chain), Grep and Read are sufficient and faster. Use Serena MCP (`find_symbol`, `find_referencing_symbols`) for complex call chains involving multiple services or deep symbol resolution where grep alone would miss indirect references.
2. **Blast radius assessment:** Identify all files and functions in the call chain from entry point to bug location.
3. **Test inventory:** Find existing tests covering the affected code paths.
4. **Complexity classification:**
   - `simple_bug`: 1-3 files, clear root cause, no migration needed
   - `moderate_bug`: 4-10 files, root cause requires investigation, may need migration
   - `complex_bug`: 10+ files, cross-service, unclear root cause → **prompt upgrade to WF2**
5. **Related issues check:** `gh issue list --repo capabilities.repo --search "<keywords>" --limit 10`

### Output

Bug analysis (internal working artifact):

- Affected files and call chain
- Existing test coverage and gaps
- Complexity classification
- Related issues
- Suspected root cause

### Failure Modes

- Cannot reproduce from description → ask user for more details
- Bug is in a dependency, not our code → document and suggest upstream report
- Classified as `complex_bug` → prompt upgrade to WF2 (user can override)

---

## Step 3: Root Cause Analysis

### Instructions

1. **Hypothesis generation:** Based on the code trace from Step 2, generate 1-3 hypotheses for the root cause.
2. **Evidence collection:** For each hypothesis, gather evidence from code, logs, and test behavior.
3. **Root cause determination:** Select the hypothesis with strongest evidence.
4. **Fix approach:** Design the minimal fix that addresses the root cause (not symptoms).
5. **Regression risk assessment:** Identify code paths that could break from the fix.

### Output

RCA document (internal working artifact):

- Root cause with evidence
- Fix approach (minimal change)
- Files to modify
- Regression risks

### Failure Modes

- Multiple equally likely root causes → present to user for guidance
- Root cause is in a design flaw (not a code bug) → suggest WF2 for redesign
- Fix would be a band-aid → flag that proper fix may need WF2

---

## Step 4: Quality Gate — Lightweight Reflect

### Instructions

Invoke `/reflexion:reflect` with focus on root cause correctness. Single-pass reflection checking:

1. Does the identified root cause actually explain ALL symptoms in the bug report?
2. Is the fix in the right layer (not a band-aid when the real issue is upstream)?
3. Are there unintended side effects of the proposed fix?
4. Does the fix handle edge cases mentioned in the bug report?
5. Is the fix backward-compatible (especially for API/DB changes)?

**Critique level:** Lightweight reflect ONLY. RATIONALE: Bug fixes have lower reversal cost than new features. A full 3-judge critique adds 2-3 minutes of latency for diminishing returns on small-scope changes.

### Output

Amended RCA (findings applied) OR blocked state (circuit breaker triggered).

### Failure Modes

- Reflect finds the root cause is wrong → loop back to Step 3 (max 1 time per loop-back budget)
- Fix has significant side effects → suggest WF2 for broader approach

---

## Step 5: Create Fix Plan

### Instructions

1. Break the fix into ordered TDD tasks:
   - Task 1: Write failing reproduction test
   - Task 2: Implement the fix (minimal change)
   - Task 3: Add regression/edge case tests
   - Task 4: Update documentation if behavior changes
2. Document the fix branch name: `fix/<issue-number>-<short-desc>`
3. Estimate: most bugs should have 3-6 tasks.

### Output

Fix plan with ordered tasks, file paths, and test expectations.

### Failure Modes

- Plan reveals fix is larger than expected → suggest upgrading to WF2

---

## Step 6: Create Fix Branch

### Instructions

1. Ensure the default branch is up to date:
   ```bash
   git fetch origin capabilities.default_branch
   ```
2. Create branch from the default branch:
   ```bash
   git checkout -b fix/<issue-number>-<short-desc> origin/capabilities.default_branch
   ```
3. Verify branch created successfully.
4. **Pre-flight dependency check:** If the project's `config.techStack` includes npm/yarn/pnpm-based technologies (node, react, vue, angular, etc.) or a `package.json` exists in the project root, verify `node_modules` exists. If missing, run the appropriate install command (`npm install`, `yarn install`, or `pnpm install`) before proceeding to Step 7. Similarly, for Python projects with a `requirements.txt` or `pyproject.toml`, verify the virtual environment is active or dependencies are installed. This prevents test failures due to missing dependencies rather than actual bugs.

### Output

Active fix branch with dependencies installed.

### Failure Modes

- Working directory is dirty → stash changes first (`git stash`), create branch, then ask user if stash should be applied
- Branch name already exists → ask user if they want to resume (checkout existing branch) or start fresh (delete and recreate)
- Push fails (network) → continue locally, push will be retried by P4 remote sync

---

## Step 7: TDD Bug Fix (Reproduce-First Pattern)

### Instructions

Execute the plan from Step 5 using strict reproduce-first TDD:

1. **RED — Reproduction test:** Write a test that captures the exact bug behavior. Run it — it MUST fail in a way that demonstrates the bug exists. In mocked environments, the specific status code or error message may differ from production — the key proof is that the security boundary, validation, or logic flaw is exposed, not that the exact production symptom is reproduced. If the test passes, the bug may already be fixed or the test doesn't capture the right behavior. Investigate before proceeding.
2. **GREEN — Minimal fix:** Make the reproduction test pass with the smallest possible code change. Resist the urge to refactor surrounding code.
3. **REFACTOR (minimal):** Only refactor if the fix introduced obvious code smells. Bug fix PRs should be focused, not cleanup opportunities.
4. **Regression tests:** Add 2-3 edge case tests around the fix boundary.
5. **Full suite:** Run test commands from `capabilities.test_commands` to confirm no regressions. Iterate over all configured test frameworks.
6. **Commit frequently:** Follow P3 (every 5 min active work) and P12 (conventional commits): `fix(scope): brief description`

### Test Commands

Test commands are derived from `capabilities.test_commands` (loaded from `config.testing.frameworks[].command`). If `capabilities.has_docker`, run tests via the compose files from `config.infrastructure.docker.composeFiles[]`. If tests are configured to run on remote hosts, use `config.infrastructure.hosts[]` to determine connection details.

Do not hardcode test runners or compose file names — always derive from config.

### Output

Fixed code with passing tests on fix branch.

### Failure Modes

- Reproduction test passes immediately → bug may not be reproducible in current code. Ask user to verify.
- Fix breaks other tests → investigate shared state or wrong approach
- Fix requires changes beyond plan scope → flag and decide: expand plan or split into multiple fixes

---

## Step 8: Lightweight Verification

### Instructions

Quick self-check (no sub-agent needed):

1. Verify all acceptance criteria from the bug report (or all risk mitigations from the security finding) are addressed.
2. Verify the reproduction test genuinely captures the original bug.
3. Verify no unrelated changes crept in: `git diff --stat` should show only planned files.
4. Verify all tests pass.

### Output

Verification pass/fail.

### Failure Modes

- Unrelated changes detected → `git checkout -- <file>` to revert strays
- Missing acceptance criteria → add tests/code for missed items

---

## Step 9: Code Review + Conditional Memorize

### Instructions

**Part A: Code Review**

Launch a focused 2-agent code review in parallel using Agent tool calls (subagent_type per the PR review toolkit):

1. `pr-review-toolkit:silent-failure-hunter` — silent failure detection (critical for bug fixes — ensure the fix doesn't suppress errors)
2. `pr-review-toolkit:code-reviewer` — project standards compliance + general review

For bug fixes, focus reviewers on: (a) is the fix correct and complete, (b) are there any new silent failures, (c) is the code simple and focused. Type design and code simplification are deferred — bug fixes should be minimal and targeted.

Apply findings automatically. Circuit breaker on ambiguity.

**Part B: Conditional Memorize**

If the bug fix reveals a pattern worth remembering (new pitfall, gotcha, or recurring issue), invoke `/reflexion:memorize` to curate insights into project knowledge. Skip if the fix is routine.

Memorize triggers:

- New database gotcha discovered
- Race condition pattern identified
- Security vulnerability pattern
- Environment-specific behavior surprise
- Recurring bug class (third instance of similar bug)

### Output

Review-clean code + optional project knowledge updates.

### Failure Modes

- Review finds fundamental flaw → loop back to Step 3 (max 1 time per loop-back budget)
- Review agents hit rate limit → log partial results, resume after reset

---

## Step 10: Create Pull Request

### Instructions

1. Stage all changes: `git add <specific files>` (never `git add -A`)
2. Create final commit with conventional format:
   ```bash
   git commit -m "fix(scope): description (closes #<issue>)"
   ```
3. Push branch:
   ```bash
   git push -u origin fix/<issue-number>-<short-desc>
   ```
4. Create PR:

   ```bash
   gh pr create --repo capabilities.repo \
     --title "fix(scope): description" \
     --body "$(cat <<'EOF'
   ## Summary
   - Fixes #<issue-number>
   - Root cause: [brief RCA]
   - Fix: [brief description of fix]

   ## Test plan
   - [ ] Reproduction test passes (was failing before fix)
   - [ ] Regression tests added
   - [ ] Full test suite passes
   - [ ] CI passes

   Generated with [Claude Code](https://claude.com/claude-code) using WF3
   EOF
   )" \
     --label "bug"
   ```

### Output

PR URL.

### Failure Modes

- Tests fail (Gate 1 blocks PR creation) → fix and retry
- Push fails → retry after 5 seconds; if persistent, save PR body for manual creation
- gh auth failure → verify PAT with `gh auth status`
- Branch has conflicts with default branch → rebase (`git pull --rebase origin capabilities.default_branch`), resolve conflicts, re-push

---

## Step 11: CI Verification

### Instructions

1. Wait for CI pipeline to complete:
   ```bash
   gh run list --repo capabilities.repo --branch fix/<branch-name> --limit 3
   ```
2. If CI passes → proceed to Step 12.
3. If CI fails → analyze failure with `gh run view <id> --log-failed`, fix, push, and re-check (max 2 retries).

**Note:** `gh pr checks` does NOT work with fine-grained PATs. Use `gh run list` / `gh run view` instead.

### Output

CI pass/fail status.

### Failure Modes

- CI flaky failure → retry once
- Genuine test failure → fix and push
- CI timeout → wait and check again; if persistent, ask user for explicit approval before proceeding with local test results only

---

## Step 12: Merge and Deploy

### Instructions

**Pre-merge check:** If the project's CLAUDE.md or development rules require explicit user approval for merge or deploy operations, ask the user before proceeding. Do not auto-merge in projects with approval gates.

1. Squash-merge PR:
   ```bash
   gh pr merge <number> --squash --delete-branch --repo capabilities.repo
   ```
2. Deploy to dev: If `capabilities.has_deploy`, use the deploy method and commands from `config.deploy`. Otherwise, ask the user for deployment instructions.
3. If the fix includes a database migration and `capabilities.has_database`, run it using the database CLI from `config.database.cli` against the database specified in `config.database`. If the database runs in a container, derive the container name and credentials from `config.database` and `config.infrastructure.docker`.
4. Verify deployment health.

### Output

Merged PR + deployed dev environment.

### Failure Modes

- Merge conflicts → rebase on default branch, resolve, push
- Deploy fails → check logs, rollback if needed via `git revert` on the default branch

---

## Step 13: Post-Deploy Verification

### Instructions

1. **Symptom verification:** Check that the original bug symptoms no longer occur in the dev environment.
2. **E2E verification (if applicable):** If `capabilities.has_tests` and config includes E2E test commands, run the relevant E2E specs using the test command from `config.testing.frameworks[]` (filtered for E2E type). If tests run on a remote host, use the appropriate host from `config.infrastructure.hosts[]`.
3. **Health check:** Verify all services are healthy after deployment.
4. **Quick reflect:** Does the deployed fix match what was intended?
5. **Same-class bug scan:** If the root cause was a missing/incorrect parameter at a call site, grep ALL callers of the affected function to check for the same class of bug at other call sites. Document findings in session notes.

### Output

Deployment verified OR rollback needed.

### Failure Modes

- Bug still reproduces in dev → investigate env-specific differences
- New issues introduced → rollback via `git revert` on the default branch

---

## Step 14: Completion Summary

### Instructions

1. Update `claude_docs/session_notes.md` with fix summary.
2. Close GitHub issue with closing comment:
   ```bash
   gh issue close <number> --repo capabilities.repo \
     --comment "Fixed in PR #<pr-number>. Root cause: <brief>. Fix: <brief>."
   ```
3. Present completion summary to user:

```
WF3 COMPLETE
=============

GitHub Issue: #<number> (CLOSED)
PR: <URL> (#<pr-number>)

Root Cause: <one-line summary>
Fix: <one-line summary>

Quality Gates:
- Reflect: [passed / looped back N times]
- Code review: [2-agent focused review passed / N findings applied]
- Memorized insights: [N insights saved / none]
- CI: [passed]
- Post-deploy: [verified]

Loop-backs used: N / 2 (global cap)

WF3 complete.
```

### Failure Modes

- None — this is an informational step. If previous steps had partial failures, this step reports the partial completion status.

<completion-gate>
Before declaring WF3 complete, verify ALL of the following. Print the checklist with pass/fail for each item:

1. [ ] Step markers logged for ALL executed steps in session notes
2. [ ] Final step output (completion summary) presented to user
3. [ ] Session notes updated with completion summary
4. [ ] PR URL documented
5. [ ] Root cause documented in session notes
6. [ ] Same-class bug scan completed
7. [ ] E2E passed

If ANY item fails, go back and complete it before declaring "WF3 complete."
You may NOT output "WF3 complete" until all items pass.
</completion-gate>

---

## Workflow Resumption

If this skill is invoked mid-conversation, detect the current state:

0. All step markers present but completion-gate not printed? → Run completion-gate, then terminate.
1. PR merged? → Step 13 (post-deploy verification)
2. PR exists and CI passed? → Step 12 (merge)
3. PR exists? → Step 11 (CI check)
4. Fix branch has passing tests? → Step 9 (code review)
5. Fix branch has code changes? → Step 8 (verification)
6. Fix branch exists (empty)? → Step 7 (TDD)
7. RCA in session notes? → Step 5 (plan)
8. None → Step 1 (start from scratch)

Announce the detected state before resuming: "Detected prior progress. Resuming at Step N."

---

## Conditional Memorization (P9)

After completing the bug fix, check if the fix revealed patterns worth memorizing:

- New database gotcha or query pitfall
- Race condition or timing-related bug class
- Security vulnerability pattern
- Environment-specific behavior (dev vs prod differences)
- Third or more instance of a similar bug category

If insights are found, they are curated via `/reflexion:memorize` in Step 9. This is conditional — skip for routine, one-off fixes.
