---
name: rawgentic:refactor
description: Refactor code using the WF4 14-step workflow with characterization tests, behavioral preservation, category-based critique, and conventional commit PR. Invoke with /refactor followed by a scope description or issue number.
argument-hint: Scope description (e.g., "extract post-trade steps") or issue number
---


# WF4: Refactoring Workflow

<role>
You are the WF4 orchestrator implementing a 14-step refactoring workflow. You guide the user through scope validation, code analysis, characterization testing, structural changes, and behavioral verification. WF4's defining constraint is behavioral preservation — every refactoring must prove that external behavior is identical before and after.
</role>

<constants>
BRANCH_PREFIX = "refactor/"
REFACTORING_CATEGORIES:
  rename: 1-5 files, symbol renames → lightweight reflect
  extract: 3-15 files, pull function/class/module → full critique
  restructure: 10+ files, move modules, change architecture → full critique
  simplify: 1-10 files, reduce complexity → lightweight reflect
LOOPBACK_BUDGET:
  Step_4_to_3: max 2 (extract/restructure) or max 1 (rename/simplify)
  Step_9_to_7b: max 1
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

1b. **Disabled skill check:** After resolving the active project, read `.rawgentic_workspace.json` (if not already read in step 1) and find the active project's entry.
   - If the project entry has a `disabledSkills` array and this skill's bare name appears in it: **STOP.**
     - If the skill is one of {implement-feature, fix-bug, create-tests, update-docs}, tell user:
       "You chose [mapped BMAD alternative] for [skill] in [project]. To change, re-run `/rawgentic:setup` or edit `disabledSkills` in `.rawgentic_workspace.json`."
       Mapping: implement-feature -> bmad-dev-story, fix-bug -> bmad-dev-story, create-tests -> bmad-tea agent / bmad-testarch-* workflows, update-docs -> BMAD tech-writer.
     - Otherwise, tell user:
       "Skill [name] is disabled in [project]. Remove it from `disabledSkills` in `.rawgentic_workspace.json` to re-enable."
   - If workspace `bmadDetected` is true but the project entry has **no** `disabledSkills` field: **STOP.** Tell user:
     "BMAD detected but no skill preferences configured for [project]. Run `/rawgentic:switch` or `/rawgentic:setup` to configure."
   - Otherwise: proceed to step 2.

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
If this workflow discovers new project capabilities during refactoring, update `.rawgentic.json` before completing:
- Append to arrays
- Set fields that are currently null or missing
- Do NOT overwrite existing non-null values without asking the user
- Always read full file, modify in memory, write full file back
</learning-config>

<environment-setup>
Constants are populated at workflow start (Step 1) by running `<config-loading>`.
- `capabilities.repo`, `config.project`, `config.infrastructure` etc. are available after config load.
- `PROJECT_ROOT`: `git rev-parse --show-toplevel`

If config loading fails, follow the STOP instructions in `<config-loading>`. Do not assume values.
</environment-setup>

<termination-rule>
WF4 terminates after deployment verification. No auto-transition to other workflows. WF4 terminates ONLY after the completion-gate (after Step 14) passes. All steps must have markers in session notes, and the completion-gate checklist must be printed with all items passing.
</termination-rule>

<context-compaction>
Before context compaction, document in `claude_docs/session_notes.md`: current step number, branch name, last commit SHA, refactoring category, characterization test status, and loop-back budget state.
</context-compaction>

<ambiguity-circuit-breaker>
STOP and ask the user when critique findings are ambiguous, conflicting, or require judgment calls. For refactoring workflows, this is especially critical when the behavioral-preservation invariant is uncertain — if a proposed refactoring MIGHT change external behavior, STOP and ask rather than guess. The circuit breaker fires in Step 4 (quality gate) but applies throughout the workflow.
</ambiguity-circuit-breaker>

<behavioral-preservation-invariant>
Every refactoring must prove:
1. All existing tests pass without modification (tests verifying external behavior must remain unchanged)
2. API contracts are identical (same request/response shapes, same error codes)
3. Database schemas are unchanged (no migrations)
4. Redis pub/sub message formats are unchanged
5. CLI/skill invocation interfaces are unchanged

If a refactoring requires behavior changes, it is NOT a refactoring — redirect to WF2 (`/implement-feature`).
</behavioral-preservation-invariant>

<characterization-testing>
Before modifying any code, WF4 requires characterization tests that capture current behavior:
1. Snapshot existing behavior: write tests asserting exact current outputs (including edge cases)
2. Run characterization tests: they MUST all pass against CURRENT code
3. Perform refactoring: modify code structure
4. Re-run characterization tests: they MUST still pass after refactoring
5. Clean up: keep tests that add coverage, remove duplicates
</characterization-testing>

<step-tracking>
At the end of each step, log a marker in `claude_docs/session_notes.md`:
`### WF4 Step X: <Name> — DONE (<key detail>)`
This enables workflow resumption if context is lost.
</step-tracking>

## Step 1: Receive Refactoring Scope

### Instructions

1. **Execute `<config-loading>`** to populate `config` and `capabilities`. Log resolved values in session notes. If config loading fails, follow the STOP instructions.
2. If argument is an issue number: fetch via `gh issue view <number> --repo <capabilities.repo>`
3. If free text: confirm scope understanding with user
4. Verify the scope is a true refactoring (no behavior changes)
5. If scope implies behavior changes, suggest WF2 instead
6. Update `claude_docs/session_notes.md` with: scope, category (preliminary), initial assessment.

### Output Format

```
Refactoring Scope:
- Description: [what is being refactored]
- Issue: [#NNN or "none"]
- Preliminary area: [affected code area]

Confirm this scope. I'll analyze the code to classify the category next.
```

Wait for user confirmation before proceeding to Step 2.

### Failure Modes

- Scope is actually a feature → redirect to WF2 (`/implement-feature`)
- Scope too vague → ask user for specifics before proceeding

---

## Step 2: Analyze Code Structure and Classify

### Instructions

1. **Symbol mapping:** Use Serena MCP (`find_symbol`, `get_symbols_overview`) to map all symbols in scope.
2. **Reference graph:** Trace all callers and callees of affected symbols via `find_referencing_symbols`.
3. **Test coverage assessment:** Identify existing tests for affected code. Note coverage gaps.
4. **Coupling analysis:** Count cross-module references — high coupling = higher risk.
5. **Category classification:** Classify as Rename, Extract, Restructure, or Simplify.
6. **Risk assessment:** Rate risk (low/medium/high) based on coupling and test coverage.
7. **Memory search for prior decisions (Layer 3 — proactive recall).** If a mempalace MCP server is available (`mcp__mempalace__*` tools loaded), call `mempalace_search` and `mempalace_kg_query` for the affected area. Past architectural choices (especially DECISION-flagged drawers) often explain why code looks the way it does. Avoid undoing decisions that have documented reasoning — surface them before proposing changes. If no mempalace MCP server is configured, skip silently.

### Output

Analysis (internal): { symbols, reference_graph, existing_tests, test_gaps, category, risk, coupling_score }

Present category and risk to user for confirmation.

### Failure Modes

- Scope is larger than expected → inform user of true blast radius before proceeding
- No existing tests for affected code → high risk; suggest adding tests first (via WF3 or as Phase A priority)
- Cannot map symbol references → code may use dynamic dispatch or reflection; note gaps in analysis

---

## Step 3: Design Refactoring Approach

### Instructions

**For Rename/Simplify:** Document the rename map or simplification approach. List every file and symbol affected.

**For Extract/Restructure:**

1. Invoke `/superpowers:brainstorming` to generate 2-3 approaches with trade-offs.
2. Recommend approach with rationale.
3. Design target structure (new files, new symbols, moved code).
4. Document migration path (order of changes to keep tests passing at each step).

**For all categories:** Explicitly list the behavioral contracts that MUST be preserved (API contracts, DB interactions, pub/sub messages).

### Output

Refactoring design (internal): { approach, target_structure, migration_steps, behavioral_contracts }

### Failure Modes

- No safe migration path exists (all paths break tests temporarily) → suggest incremental approach or ask user for guidance
- Design reveals the code is too coupled to refactor safely → document and present trade-offs to user
- For Extract/Restructure: brainstorming produces no clearly superior approach → present all options to user (P11)

---

## Step 4: Quality Gate — Design Critique

### Instructions

**Critique method preference:** Before running the critique, check the active project entry's `critiqueMethod` field in `.rawgentic_workspace.json`. If set to `"bmad-party-mode"`, use bmad-party-mode instead of the critique below. If missing or `"reflexion"`, proceed as normal.

**For Extract/Restructure (full critique):** Invoke `/reflexion:critique` — three judges evaluate:

- Does the refactoring preserve all documented behavioral contracts?
- Is the target structure actually better (reduced coupling, improved cohesion)?
- Is the migration path safe (tests pass at every intermediate step)?
- Are there hidden behavioral changes masquerading as "refactoring"?

**For Rename/Simplify (lightweight reflect):** Invoke `/reflexion:reflect` — single-pass checking:

- Are all references updated? (Serena `find_referencing_symbols` cross-check)
- Any behavioral side effects?

### Output

Amended design OR blocked state (circuit breaker).

### Failure Modes

- Critique finds the refactoring would change external behavior → reject and redesign (loop back to Step 3, within budget)
- Critique finds target structure is not actually better → discuss with user whether to proceed or abandon
- Rename/reflect finds missed references → update reference list and re-verify via Serena

---

## Step 5: Create Refactoring Plan

### Instructions

1. Order tasks to keep tests passing at each step:
   - **Phase A:** Write characterization tests (before any code changes)
   - **Phase B:** Perform refactoring (structure changes)
   - **Phase C:** Clean up (remove temporary scaffolding, update docs)
2. Each task: file path, action description, expected test result after completion.
3. Branch name: `refactor/<issue-number>-<short-desc>` or `refactor/<short-desc>`

### Output

Ordered task list with behavioral verification points.

### Failure Modes

- Plan ordering cannot guarantee tests pass at every step → consider smaller incremental steps or scaffolding (adapter pattern)
- Scope grew during analysis (more files than originally estimated) → re-confirm with user before proceeding (P11)

---

## Step 6: Create Refactoring Branch

### Instructions

```bash
git fetch origin <capabilities.default_branch>
git checkout -b refactor/<desc> origin/<capabilities.default_branch>
```

### Failure Modes

- Branch name already exists → delete stale branch or append a suffix
- Uncommitted changes on current branch → stash or commit before switching

---

## Step 7a: Write Characterization Tests

### Instructions

For each symbol being refactored, write tests that capture CURRENT behavior:

- Input/output pairs for functions
- State transitions for classes
- API request/response pairs for endpoints
- DB query results for data access functions

Run all characterization tests — they MUST pass against current (unmodified) code.

Commit separately: `test(scope): add characterization tests for <refactoring>`

This commit is the behavioral baseline.

### Failure Modes

- Can't write characterization tests due to side effects → use mocking
- Tests reveal existing bugs → document but don't fix (that's WF3's job). If refactoring uncovers existing bugs, document them as new GitHub issues using `/create-issue` rather than fixing them inline. Bug fixes during refactoring violate the behavioral preservation invariant.
- Characterization tests fail against current code → tests are wrong, not the code. Fix the test assertions to match actual behavior.

---

## Step 7b: Execute Refactoring

### Instructions

Execute each refactoring task in order:

1. After EACH task: run full test suite (characterization + existing tests)
2. If ANY test fails: stop, diagnose, and fix before proceeding
3. Commit after each logical step: `refactor(scope): <description>`
4. Push regularly (P4)

### Failure Modes

- Test fails after a refactoring step → undo that step, re-analyze
- Refactoring harder than planned → flag for user
- Behavioral change detected → this is NOT a refactoring — redirect to WF2

---

## Step 8: Post-Refactoring Verification

### Instructions

Quick self-check:

1. All characterization tests still pass
2. All existing tests still pass
3. No unrelated changes in `git diff --stat`
4. All behavioral contracts from Step 3 are preserved
5. No new dependencies added
6. No database migration files created (if any exist, this isn't a pure refactoring)

### Failure Modes

- Unrelated changes appear in `git diff --stat` → revert unrelated changes and re-commit
- Database migration files exist → this is not a pure refactoring; redirect to WF2
- Characterization tests pass but existing tests fail → refactoring broke undocumented behavior; investigate and fix

---

## Step 9: Code Review + Memorize

### Instructions

**Code Review:** Launch 4-agent review (subagent_type per PR review toolkit) focused on:

- Behavioral preservation
- Improved code quality
- No silent failures introduced
- Style consistency

**Memorize:** Refactorings ALWAYS produce patterns worth documenting. Run `/reflexion:memorize` to capture: new code patterns, deprecated patterns, architectural decisions.

Apply findings. Circuit breaker on ambiguity.

### Failure Modes

- Review finds behavioral change → loop back to Step 7b (max 1 time)
- Review finds refactoring didn't improve things → discuss with user

---

## Step 10: Create Pull Request

### Instructions

1. Push: `git push -u origin refactor/<desc>`
2. Create PR:

   ```bash
   gh pr create --repo <capabilities.repo> \
     --title "refactor(scope): description" \
     --body "$(cat <<'EOF'
   ## Summary
   - [what was refactored and why]
   - Category: [rename/extract/restructure/simplify]

   ## Behavioral Preservation Evidence
   - Characterization tests: [N tests, all passing]
   - Existing test suite: [all passing, no modifications needed]
   - No database migrations
   - No API contract changes

   ## Test plan
   - [ ] Characterization tests pass
   - [ ] Full test suite passes
   - [ ] CI passes

   Generated with [Claude Code](https://claude.com/claude-code) using WF4
   EOF
   )"
   ```

### Failure Modes

- PR creation fails due to branch not pushed → push branch first with `git push -u origin refactor/<desc>`
- PR body too long or contains special characters → simplify the behavioral preservation evidence section

---

## Step 11: CI Verification

### Instructions

Wait for CI via `gh run list --branch <branch>`. Max 2 fix-and-retry cycles.

### Failure Modes

- CI fails on tests that pass locally → investigate environment differences (Node version, Python version, missing env vars)
- CI fails after 2 fix-and-retry cycles → escalate to user for guidance

---

## Step 12: Merge and Deploy

### Instructions

1. Squash-merge: `gh pr merge <number> --squash --delete-branch --repo <capabilities.repo>`
2. Deploy per `config.deploy` settings (skip if `capabilities.has_deploy` is false)
3. Verify health.

### Failure Modes

- Merge conflict on squash → rebase branch on latest main, re-run tests, force-push
- Deploy script fails → check SSH connectivity and container state on target VM
- Health check fails after deploy → check Docker logs for runtime errors

---

## Step 13: Post-Deploy Verification

### Instructions

1. All services healthy post-deployment
2. Quick smoke test of refactored functionality
3. No new errors in logs
4. E2E tests pass (if applicable)

### Failure Modes

- Service unhealthy after deploy → check Docker logs, compare pre/post-deploy state
- E2E tests fail on refactored functionality → investigate runtime behavior vs unit test behavior; may indicate subtle behavioral change missed by unit tests
- New errors in logs not caught by tests → document as new issue via `/create-issue`

---

## Step 14: Completion Summary

### Instructions

1. Update `claude_docs/session_notes.md`
2. Close GitHub issue (if exists) with PR reference
3. Present summary:

```
WF4 COMPLETE
=============

PR: <URL> (#<pr-number>)
Category: [rename/extract/restructure/simplify]
Scope: [what was refactored]

Behavioral Preservation:
- Characterization tests: [N tests written, all passing]
- Existing tests: [all passing, zero modifications]
- Migrations: none
- API changes: none

Quality Gates:
- Critique/Reflect: [passed / N findings applied]
- Code review: [4-agent review passed]
- Memorized patterns: [N insights saved]
- CI: [passed]

Loop-backs used: N / 3 (global cap)

WF4 complete.
```

### Failure Modes

- GitHub issue close fails → verify issue number and repo; may already be closed
- Session notes file missing → create new file

---

<completion-gate>
Before declaring WF4 complete, verify ALL of the following. Print the checklist with pass/fail for each item:

1. [ ] Step markers logged for ALL executed steps in session notes
2. [ ] Final step output (completion summary) presented to user
3. [ ] Session notes updated with completion summary
4. [ ] PR URL documented
5. [ ] Behavioral equivalence verified (tests pass, no regressions)
6. [ ] E2E passed

If ANY item fails, go back and complete it before declaring "WF4 complete."
You may NOT output "WF4 complete" until all items pass.
</completion-gate>

---

## Workflow Resumption

If this skill is invoked mid-conversation, detect the current state:

0. All step markers present but completion-gate not printed? → Run completion-gate, then terminate.
1. PR merged? → Step 13 (post-deploy)
2. PR exists and CI passed? → Step 12 (merge)
3. PR exists? → Step 11 (CI)
4. Refactor branch has refactored code with passing tests? → Step 9 (review)
5. Refactor branch has characterization tests committed? → Step 7b (start refactoring)
6. Refactor branch exists (empty)? → Step 7a (characterization tests)
7. Refactoring plan in session notes? → Step 5 (plan)
8. None → Step 1

Announce detected state: "Detected prior progress. Resuming at Step N."

---

## Conditional Memorization (P9)

Refactorings ALWAYS produce patterns worth documenting (D5 from design):

- What was the old pattern?
- What's the new pattern?
- Why the change was made?

This prevents the next session from re-introducing the old pattern. Memorization runs in Step 9 (not conditional).
