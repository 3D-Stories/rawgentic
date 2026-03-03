---
name: rawgentic:implement-feature
description: Implement a feature or bug fix from a GitHub issue through the WF2 16-step workflow with TDD, multi-agent code review, quality gates, and automated deployment. Invoke with /implement-feature followed by a GitHub issue number or URL.
argument-hint: GitHub issue number (e.g., 155) or URL
---

# WF2: Feature Implementation Workflow

<role>
You are the WF2 orchestrator implementing a 16-step feature implementation workflow. You take a GitHub issue (created by WF1 or manually) and guide it through codebase analysis, design, critique, TDD implementation, code review, PR creation, CI verification, deployment, and post-deploy verification. You enforce quality gates, the ambiguity circuit breaker, the global loop-back budget, and NEVER auto-transition to WF1 or restart WF2 after completion.
</role>

<constants>
MAX_DESIGN_LOOPBACK_ITERATIONS = 2
MAX_TDD_DESIGN_LOOPBACK = 1
MAX_REVIEW_DESIGN_LOOPBACK = 1
GLOBAL_LOOPBACK_BUDGET = 3
VOLUME_THRESHOLDS:
  Critical: 5
  High: 5
  Medium: 10
  Low: 10
REPO = "<inferred from `git remote -v` at workflow start>"
PROJECT_ROOT = "<inferred from `git rev-parse --show-toplevel`>"
DEV_HOST = "<from CLAUDE.md infrastructure section>"
ENGINE_HOST = "<from CLAUDE.md infrastructure section>"
DB_NAME = "<from CLAUDE.md database section>"
DB_USER = "<from CLAUDE.md database section>"
POSTGRES_CONTAINER = "<from docker compose config>"
COMPOSE_INFRA = "<from docker compose config>"
COMPOSE_ENGINE = "<from docker compose config>"
BRANCH_PREFIX_FEATURE = "feature"
BRANCH_PREFIX_FIX = "fix"
CI_POLL_INTERVAL_SECONDS = 30
CI_MAX_WAIT_MINUTES = 10
REVIEW_CONFIDENCE_THRESHOLD = 0.80
</constants>

<environment-setup>
Constants are populated at workflow start (Step 1) by running:
- `REPO`: `git remote get-url origin | sed 's|.*github.com[:/]||;s|\.git$||'`
- `PROJECT_ROOT`: `git rev-parse --show-toplevel`
- `DEV_HOST`, `ENGINE_HOST`, `DB_NAME`, `DB_USER`, `POSTGRES_CONTAINER`: Read from CLAUDE.md infrastructure and database sections
- `COMPOSE_INFRA`, `COMPOSE_ENGINE`: Read from project root docker compose files

If any constant cannot be resolved, STOP and ask the user. Do not assume values.
</environment-setup>

<termination-rule>
WF2 ALWAYS terminates after deployment verification and completion summary. Do NOT suggest "shall I create another issue?" or "would you like to start WF1?" Do NOT restart WF2 for the same issue. The workflow produces a single completion summary and stops.
</termination-rule>

<loop-back-budget>
Track all design loop-backs across the workflow:
- Step 4 -> Step 3: max 2 iterations (MAX_DESIGN_LOOPBACK_ITERATIONS)
- Step 8 -> Step 3: max 1 iteration (MAX_TDD_DESIGN_LOOPBACK)
- Step 11 -> Step 3: max 1 iteration (MAX_REVIEW_DESIGN_LOOPBACK)

Global cap: GLOBAL_LOOPBACK_BUDGET = 3
If global cap reached, STOP and escalate to user with full summary of all loop-back triggers.
The user must decide: (a) continue with current state, (b) narrow scope, (c) abandon.

Track loop-back state:
design_loopback_count = 0
tdd_loopback_used = false
review_loopback_used = false
global_loopback_total = 0
</loop-back-budget>

<resumption-protocol>
WF2 may span multiple Claude Code sessions. On resumption, detect the current step:

1. PR exists and is merged? -> Resume at Step 15 (post-deploy verification)
2. PR exists and CI passed? -> Resume at Step 14 (merge + deploy)
3. PR exists? -> Resume at Step 13 (CI verification)
4. Feature branch has code changes with passing tests? -> Resume at Step 11 (code review)
5. Feature branch has code changes? -> Resume at Step 9 (implementation drift check)
6. Feature branch exists but is empty? -> Resume at Step 8 (TDD implementation)
7. Design document exists in session notes? -> Resume at Step 5 (create plan)
8. Issue is validated in session notes? -> Resume at Step 2 (analyze codebase)
9. None of the above? -> Start from Step 1

Before context compacts, document in session notes:

- Current step number and sub-step
- Quality gate findings not yet applied
- Feature branch name and last commit SHA
- Loop-back budget state (design_loopback_count, tdd/review used, global total)
- Any unresolved circuit breaker state
- If in Step 8 (TDD): current task index, TDD phase (RED/GREEN/REFACTOR), and last passing test count
  </resumption-protocol>

<fast-path-detection>
Two fast path variants reduce Step 4 from full critique to lightweight reflect:

1. **Simple bugfix fast path:** Step 2 classifies as simple_bugfix (1-3 files, no architecture change, no migration, no new deps). Step 4 uses /reflexion:reflect instead of /reflexion:critique.

2. **WF1-validated fast path:** Step 1 detects the issue has the "wf1-created" label AND Step 2 classifies as standard_feature (not complex_feature). Step 4 uses /reflexion:reflect. Rationale: WF1 already ran a full 3-judge critique on the issue specification.

Neither fast path applies to complex_feature -- those always get full /reflexion:critique.
</fast-path-detection>

<ambiguity-circuit-breaker>
Active at ALL quality gates (Steps 4, 6, 9, 11, 15). Triggers when:
- Any finding has ambiguity_flag == "ambiguous"
- Two or more findings conflict (contradictory recommendations)
- A finding requires judgment not captured in the GitHub issue

When triggered: STOP the workflow at the current step. Present ALL problematic findings to the user. Wait for resolution. Do NOT auto-apply unambiguous findings separately -- the full set is applied together after resolution.
</ambiguity-circuit-breaker>

## Step 1: Receive Issue Reference

### Instructions

1. Parse the user's input to extract the GitHub issue number. Accept:
   - Bare number: `155`
   - Hash-prefixed: `#155`
   - URL: `https://github.com/${REPO}/issues/155`

2. Fetch the issue via gh CLI:

   ```bash
   gh issue view <number> --repo ${REPO} --json number,title,body,labels,state
   ```

3. Validate:
   - Issue exists (gh returns valid JSON)
   - Issue is open (`state == "OPEN"`)
   - If closed: ask user if they want to reopen or reference a different issue

4. Check for WF1 origin:
   - If labels include "wf1-created": set `is_wf1_created = true`
   - Extract acceptance criteria, affected components, complexity from the issue body
   - If any are missing (manually created issue without WF1 structure): generate them from the description and ask user to confirm

5. Display to user:

   ```
   ISSUE #NNN: [title]
   State: Open
   Labels: [label list]
   WF1 Origin: [yes/no]
   Complexity: [S/M/L/XL or "not specified"]

   Acceptance Criteria:
   1. [criterion 1]
   2. [criterion 2]
   ...

   Affected Components: [list]

   Confirm this is the correct issue to implement, or provide corrections.
   ```

6. Update `claude_docs/session_notes.md` with: issue reference, initial scope assessment, classification (simple_bugfix/standard_feature/complex_feature), WF1 origin flag.
7. Wait for user confirmation before proceeding.

### Output

Validated issue reference with extracted acceptance criteria, components, and WF1 origin flag.

### Failure Modes

- Issue does not exist → ask user for correct issue number
- Issue is already closed → ask if user wants to reopen or reference a different issue
- Issue lacks acceptance criteria (manually created without WF1) → generate acceptance criteria from the description and ask user to confirm before proceeding

---

## Step 2: Analyze Codebase and Classify Complexity

### Instructions

1. **Component mapping:** Using Serena MCP (`find_symbol`, `find_referencing_symbols`, `get_symbols_overview`), identify all files, functions, and classes that will need to change. Map the issue's "affected components" to actual code symbols.

2. **Dependency analysis:** Trace call chains from affected entry points (API routes, scheduler jobs, strategy methods) to understand the full blast radius. Use `find_referencing_symbols` to trace callers.

3. **Existing test inventory:** Identify existing test files and test cases that cover the affected code. Note gaps.

4. **Library research:** If the feature requires new libraries or uses existing libraries in new ways, use Context7 MCP (`resolve-library-id` then `query-docs`) to fetch current documentation.

5. **Complexity classification:** Based on the analysis, classify the implementation:
   - `simple_bugfix`: 1-3 files, no architecture change, no migration, no new deps
   - `standard_feature`: 4-15 files, contained architecture change, may need migration
   - `complex_feature`: 15+ files, cross-service changes, multiple migrations, new deps

   This classification is AUTHORITATIVE -- it overrides any complexity label from the GitHub issue.

6. **Diagram assessment:** Determine if the feature requires an Excalidraw architecture diagram. Criteria: new service interactions, new data flows, new database tables, or changes to existing architecture patterns.

7. **Fast path eligibility:** Evaluate:
   - If `simple_bugfix`: `fast_path_eligible = true`, reason = "Simple bugfix (1-3 files, no architecture change)"
   - If `standard_feature` AND `is_wf1_created`: `fast_path_eligible = true`, reason = "WF1-validated standard feature"
   - Otherwise: `fast_path_eligible = false`

### Output

Codebase analysis with complexity classification and fast path eligibility. Do NOT present the full analysis to the user -- it feeds into Step 3.

### Failure Modes

- Serena MCP unavailable: fall back to Grep/Glob for code search (less precise but functional)
- Issue references components that do not exist: flag the discrepancy and ask user for guidance
- Complexity assessment uncertain: default to `standard_feature`

---

## Step 3: Design Solution Architecture

### Instructions

1. **Invoke brainstorming sub-agent:** Use the Agent tool with a brainstorming prompt to generate 2-3 implementation approaches. The sub-agent receives:
   - Codebase analysis from Step 2
   - GitHub issue requirements
   - CLAUDE.md architecture section
   - Relevant source files identified in Step 2

2. **Approach proposals:** Each approach includes:
   - Name and description
   - Pros and cons
   - Estimated effort
   - Risk assessment

3. **Approach selection:** Based on complexity classification and acceptance criteria, recommend one approach with rationale.

4. **Design document:** Produce a structured design covering:
   - Component changes (which files, which functions, what modifications)
   - Data flow changes (new/modified API routes, DB queries, Redis pub/sub messages)
   - Database migrations (if any, with rollback SQL)
   - Error handling strategy
   - Security implications
   - Performance implications

5. **Diagram creation (conditional):** If Step 2 flagged `needs_diagram: true`:
   - Use Excalidraw MCP if available
   - Otherwise write native Excalidraw JSON (fontFamily: 2 / Helvetica, never fontFamily: 1)
   - Save to `.claude/framework-build/diagrams/feature-<issue-number>.excalidraw`

6. **Multi-PR assessment:** If the design suggests more than 500 lines of change, flag for multi-PR decomposition in Step 5.

### Output

Design document. The design is NOT presented to the user directly -- it goes to Step 4 for critique.

### Failure Modes

- All approaches have significant trade-offs: present all to user and let them choose
- Design reveals scope much larger than estimated: flag for user decision (narrow scope or accommodate full scope)
- Excalidraw MCP unavailable: write diagram as native JSON file

---

## Step 4: Quality Gate -- Design Critique

### Instructions

**Determine gate type based on fast path eligibility:**

- If `fast_path_eligible == true`: use `/reflexion:reflect` (lightweight single-pass)
- If `fast_path_eligible == false`: use `/reflexion:critique` (full 3-judge debate)

**For standard/complex (full critique -- `/reflexion:critique`):**

1. Launch three judge sub-agents in a single message with three parallel Agent tool calls. If any agent returns a rate limit error (429), retry that single agent after 30 seconds -- do not re-dispatch all three.

   **Judge 1: Architecture Reviewer**
   - Does the design respect existing patterns (order execution, broker mode routing, feature flags, exception handling in critical paths)?
   - Is the architecture consistent with CLAUDE.md architecture decisions?

   **Judge 2: Completeness Reviewer**
   - Are all acceptance criteria addressed?
   - Are edge cases handled?
   - Are failure modes identified?
   - Is TDD testability clear?

   **Judge 3: Security Reviewer**
   - Does the design follow security standards (auth on all data channels, input validation)?
   - Are migrations backward-compatible?
   - Are performance implications acceptable?

2. Each judge produces findings with:

   ```
   Finding #N:
   - Severity: Critical | High | Medium | Low
   - Category: architecture | completeness | security | testability | scope_fidelity | migration_safety | performance
   - Description: [what the issue is]
   - Recommendation: [specific action]
   - Ambiguity flag: clear | ambiguous
   - Ambiguity reason: [why, if ambiguous]
   ```

3. Synthesize findings. Conduct debate round if judges disagree.

4. **Volume threshold check** (per-tier independent):
   - More than 5 Critical: TRIGGER LOOP-BACK
   - More than 5 High: TRIGGER LOOP-BACK
   - More than 10 Medium: TRIGGER LOOP-BACK
   - More than 10 Low: TRIGGER LOOP-BACK

5. **If loop-back triggered:**
   - Check `design_loopback_count` and `global_loopback_total`
   - If `design_loopback_count < MAX_DESIGN_LOOPBACK_ITERATIONS` AND `global_loopback_total < GLOBAL_LOOPBACK_BUDGET`:
     - Increment both counters
     - Apply findings to design as constraints
     - Return to Step 3 (re-design)
   - If limits reached: STOP and escalate to user

6. **If volume thresholds pass:** Apply ambiguity circuit breaker.

**For simple_bugfix or WF1-validated standard_feature (fast path -- `/reflexion:reflect`):**

1. Single-pass reflection checking:
   - Does the fix address the reported issue?
   - Are there unintended side effects?
   - Is the fix in the right layer (not a band-aid)?
   - (For WF1-validated): Does the design align with the WF1-critiqued specification?

2. Apply ambiguity circuit breaker on findings.

### Output

Amended design document (findings applied).

### Failure Modes

- **Zero findings (suspicious):** If the full 3-judge critique returns 0 findings, verify judges actually analyzed the design. Zero findings from a complex feature design likely indicates a prompt or context issue.
- **All judges return identical findings:** Possible context contamination. Re-run with explicit diversity instructions.
- **Ambiguity circuit breaker triggers on >50% of findings:** The design may be underspecified. Consider returning to Step 1 for more requirements.

---

## Step 5: Create Implementation Plan

### Instructions

1. **Branch naming:** Document the branch name for Step 7:
   - Features: `feature/<issue-number>-<kebab-case-summary>` (e.g., `feature/155-add-trailing-stop`)
   - Bug fixes: `fix/<issue-number>-<kebab-case-summary>` (e.g., `fix/156-sentiment-null-crash`)

2. **Database migrations (if any):** Specify migration file names and content in `postgres-migrations/`. Include rollback SQL as `-- ROLLBACK:` comment.

3. **TDD task decomposition:** Break the design into ordered tasks, each 2-5 minutes, following Red-Green-Refactor:
   - RED: Write the failing test(s)
   - GREEN: Write minimum code to pass
   - REFACTOR: Clean up without changing behavior

4. **Task ordering:** Make dependencies explicit. Mark parallel-eligible tasks with the same `parallel_group`.

5. **Test strategy per task:** Specify test file, test cases, and test type (unit/integration/E2E).

6. **Documentation tasks:** Identify CLAUDE.md sections, migration docs, API docs that need updating.

7. **Multi-PR decomposition (if applicable):** If design exceeds 500 lines of change:
   - Sub-PR 1: Database migrations + backend logic
   - Sub-PR 2: API endpoints + backend tests
   - Sub-PR 3: Frontend changes + E2E tests

8. **Commit messages:** Pre-specify conventional commit messages for each task checkpoint.

### Output

Implementation plan with ordered tasks, test strategy, branch name, and optional multi-PR decomposition.

### Failure Modes

- Plan has too many tasks (>30 for a single feature) → flag for user review and suggest scope narrowing or multi-PR decomposition
- Plan has circular dependencies between tasks → re-order to break the cycle
- Plan references files or functions that do not exist → verify against Step 2 analysis; if discrepancy, update plan

---

## Step 6: Quality Gate -- Plan Drift Check

### Instructions

1. Invoke `/reflexion:reflect` with check dimensions:
   - **Design-plan alignment:** Does every design component map to at least one plan task? Are there plan tasks not in the design (scope creep)?
   - **TDD completeness:** Does every implementation task have a corresponding test task preceding it?
   - **Acceptance criteria coverage:** Does the plan, if fully executed, satisfy all acceptance criteria?
   - **Task ordering validity:** Are dependencies correctly ordered? Are there implicit dependencies not captured?
   - **Commit checkpoint adequacy:** Are checkpoints at logical boundaries?

2. Apply ambiguity circuit breaker on findings.

3. If findings are clear: apply automatically and proceed.

### Output

Plan drift check result (pass/findings applied).

### Failure Modes

- Reflect identifies significant drift (plan misses a design component) → add missing tasks to the plan
- Reflect identifies scope creep (plan adds tasks not in design) → remove excess tasks or flag for user decision if they represent legitimate enhancements discovered during planning

---

## Step 7: Create Feature Branch

### Instructions

1. Ensure working directory is clean:

   ```bash
   cd ${PROJECT_ROOT} && git status --porcelain
   ```

   If dirty: stash changes (`git stash`), create branch, ask user if stash should be applied.

2. Pull latest main:

   ```bash
   cd ${PROJECT_ROOT} && git pull origin main
   ```

3. Create feature branch:

   ```bash
   cd ${PROJECT_ROOT} && git checkout -b <branch_name>
   ```

4. Push empty branch to origin:

   ```bash
   cd ${PROJECT_ROOT} && git push -u origin <branch_name>
   ```

5. Link branch to issue:
   ```bash
   gh issue comment <issue_number> --repo ${REPO} --body "Implementation started on branch \`<branch_name>\`"
   ```

### Output

Feature branch created and pushed, issue commented with branch link.

### Failure Modes

- Branch name already exists: ask user to resume (checkout existing) or start fresh (delete and recreate)
- Push fails: continue locally, push will be retried by P4 remote sync

---

## Step 8: TDD Implementation

### Instructions

Execute the implementation plan task by task following strict TDD.

**For each task in the plan:**

1. **RED phase:** Write the failing test(s). Run the test suite to confirm the new tests FAIL:
   - Python: `cd ${PROJECT_ROOT}/engine && python -m pytest tests/<test_file> -v -k "<test_name>"`
   - Node.js: `cd ${PROJECT_ROOT}/dashboard && npx vitest run <test_file>`
     If the test passes before implementation, the test is not testing the right thing -- rewrite it.

2. **GREEN phase:** Write the minimum code to make the failing tests pass. Run the test suite to confirm ALL tests pass:
   - Python: `cd ${PROJECT_ROOT}/engine && python -m pytest tests/ -v`
   - Node.js: `cd ${PROJECT_ROOT}/dashboard && npx vitest run`

3. **REFACTOR phase:** Clean up the code. Run tests again to confirm nothing broke.

4. **Commit:** Create a conventional commit:

   ```bash
   cd ${PROJECT_ROOT} && git add <specific_changed_files> && git commit -m "<commit_message> (#<issue_number>)"
   ```

   Stage ONLY the files modified in this task. Never use `git add -A` or `git add .` (P3 principle).

5. **Checkpoint (if plan specifies):** Run full test suite, verify feature branch is deployable.

**Parallel task execution:** For independent tasks (same `parallel_group`), dispatch via parallel Agent tool calls.

**Debugging:** If a test fails unexpectedly during GREEN phase and 3 manual fixes fail, escalate to systematic debugging: root cause investigation, pattern analysis, hypothesis testing, implementation.

**Formatting:** After every file write, the PostToolUse hook runs Prettier (JS/JSX) or Ruff (Python) automatically (P2).

**Commit frequency:** Commit at minimum every 5 minutes of active work (P3).

**Push frequency:** Push to origin at minimum every 30 minutes (P4):

```bash
cd ${PROJECT_ROOT} && git push origin <branch_name>
```

**Design flaw discovery:** If implementation reveals a fundamental design flaw:

- Check: `tdd_loopback_used == false` AND `global_loopback_total < GLOBAL_LOOPBACK_BUDGET`
- If allowed: set `tdd_loopback_used = true`, increment `global_loopback_total`, return to Step 3 with the flaw identified
- If budget exhausted: STOP and escalate to user

**Multi-PR guidance:** If implementation is too large for a single PR (more than 500 lines of change or distinct logical phases), split into sub-PRs per the plan from Step 5. Each sub-PR follows Steps 8-14 independently. Memorization (Step 10) runs once after the final sub-PR's Step 9.

**Session checkpoint:** Update `claude_docs/session_notes.md` with implementation progress, test results, and any deviations from plan.

### Output

Implemented feature with passing tests on the feature branch, committed and pushed.

### Failure Modes

- Test fails and cannot be fixed after systematic debugging → flag the blocker to the user with the debugging analysis; user may need to clarify requirements or accept a design change
- Implementation reveals the design was flawed (unforeseen technical constraint) → loop back to Step 3 if `tdd_loopback_used == false` AND `global_loopback_total < GLOBAL_LOOPBACK_BUDGET`; otherwise STOP and escalate to user
- TDD discipline breaks (code written before test) → require the missing test before proceeding; flag as a process violation

---

## Step 9: Quality Gate -- Implementation Drift Check

### Instructions

Two-part post-implementation quality check:

**Part A: Drift check (invoke `/reflexion:reflect`):**

Check dimensions:

- **Plan-implementation alignment:** Does every plan task have a corresponding implementation? Are there implemented changes not in the plan?
- **Design-implementation alignment:** Does the implementation follow the critiqued design architecture?
- **Acceptance criteria verification:** For each criterion, identify the test(s) that verify it. Flag uncovered criteria.
- **Test coverage assessment:** Are there implemented code paths without tests?
- **Documentation check:** Are CLAUDE.md, migration docs, API docs updated?

**Part B: Evidence enforcement:**

1. Run the full test suite on both Python and Node.js:

   ```bash
   cd ${PROJECT_ROOT}/engine && python -m pytest tests/ -v 2>&1 | tail -20
   cd ${PROJECT_ROOT}/dashboard && npx vitest run 2>&1 | tail -20
   ```

2. Verify all new tests actually test the new behavior (not vacuously passing).
3. Confirm no test regressions.

Apply ambiguity circuit breaker on combined findings.

### Output

Implementation drift check result with test evidence.

### Failure Modes

- Drift detected (implementation diverged from plan/design) → determine if the divergence is an improvement or a regression; if improvement, update design doc to match; if regression, fix the implementation
- Missing test coverage → add the missing tests before proceeding
- Acceptance criteria not met → implement the missing criteria before proceeding; do not skip to PR

---

## Step 10: Conditional Memorization (Post-Implementation)

### Instructions

**This step runs in PARALLEL with Step 11.** Dispatch Step 10 and Step 11 as two Agent tool calls in a single message: Step 10 with `run_in_background=true`, Step 11 in the foreground.

1. Review all quality gate findings from Steps 4, 6, and 9.
2. Identify reusable insights -- patterns applicable beyond this specific issue:
   - New code patterns established
   - Debugging insights that would prevent future issues
   - Architecture constraints discovered during critique
   - Testing patterns that proved effective

3. If memorizable insights exist:
   - Invoke `/reflexion:memorize` (ACE pattern):
     a. Extract insight from critique context
     b. Check for duplication against CLAUDE.md and MEMORY.md
     c. If novel, append to appropriate CLAUDE.md section
     d. If at capacity, suggest archiving stale entries

4. If no reusable patterns: skip entirely.

**Join barrier:** Step 12 must wait for BOTH Step 10 and Step 11 to complete. Step 12 checks for CLAUDE.md changes from Step 10 and includes them in the commit.

### Output

Updated CLAUDE.md (if insights memorized) or no output (if skipped).

### Failure Modes

- Over-memorization: storing trivial or context-specific findings as general principles → the memorize command should filter for novelty and generalizability
- CLAUDE.md at capacity → suggest archiving stale entries per the existing MEMORY.md warning pattern
- Duplicate memorization → the memorize command deduplicates against existing content

---

## Step 11: Pre-PR Code Review

### Instructions

**This step runs in PARALLEL with Step 10.** Step 11 is the foreground task.

1. **Generate diff for review:**

   ```bash
   cd ${PROJECT_ROOT} && git diff main..HEAD
   ```

2. **Dispatch 4-agent parallel review** via Agent tool calls in a single message. If any agent returns a rate limit error (429), retry that single agent after 30 seconds -- do not re-dispatch all four.

   **Agent 1: CLAUDE.md Compliance (Primary)**
   - Code style rules (JavaScript: no semicolons, single quotes, 2-space indent; Python: PEP 8)
   - Naming conventions
   - Architecture patterns (order execution, broker mode routing)

   **Agent 2: CLAUDE.md Compliance (Redundant)**
   - Same check as Agent 1 (catches blind spots through independent review)
   - Feature flag conventions
   - Exception handling in critical paths

   **Agent 3: Bug Detection**
   - Logic errors, edge cases, race conditions
   - Silent failures in catch blocks
   - Null/undefined handling

   **Agent 4: History Analysis**
   - Does this change break patterns established by prior commits?
   - Are there related files that should also change?

3. **Supplementary review (selective):**
   - Test coverage analysis: are behavioral tests sufficient?
   - Code simplification opportunities

4. **Filter by confidence:** Only surface findings with confidence >= REVIEW_CONFIDENCE_THRESHOLD (0.80).

5. **Severity-based fix workflow:**
   - Critical/High: MUST be fixed before PR
   - Medium/Low: advisory (fix if easy, otherwise note for future)

6. **Evaluate each finding before fixing:** Verify the finding is real (not a false positive), check YAGNI, push back on unnecessary changes.

7. **Apply ambiguity circuit breaker** on review findings.

8. **Design flaw detection:** If review finds a fundamental design flaw:
   - Check: `review_loopback_used == false` AND `global_loopback_total < GLOBAL_LOOPBACK_BUDGET`
   - If allowed: set `review_loopback_used = true`, increment `global_loopback_total`, return to Step 3
   - If budget exhausted: STOP and escalate to user

### Output

Code review result with filtered findings and fixes applied.

### Failure Modes

- Review finds a fundamental design flaw missed by Step 4 critique → loop back to Step 3 if `review_loopback_used == false` AND `global_loopback_total < GLOBAL_LOOPBACK_BUDGET`; otherwise STOP and escalate to user
- Review generates excessive noise (>20 Low findings) → filter at confidence >= 0.80 and focus on Critical/High
- Review sub-agents disagree on a finding → the receiving-code-review skill adjudicates by verifying the claim against actual code

---

## Step 12: Create PR and Push

### Instructions

1. **Wait for join barrier:** Verify both Step 10 and Step 11 have completed.

2. **Include memorization changes:** If Step 10 updated CLAUDE.md, commit the changes:

   ```bash
   cd ${PROJECT_ROOT} && git add CLAUDE.md && git commit -m "docs: update CLAUDE.md with implementation insights (#<issue_number>)"
   ```

3. **Final commit:** Ensure all changes are committed with proper conventional commit messages.

4. **Push to origin:**

   ```bash
   cd ${PROJECT_ROOT} && git push origin <branch_name>
   ```

5. **Pre-PR test gate (P7 Gate 1):** Run the full test suite before PR creation:

   ```bash
   cd ${PROJECT_ROOT}/engine && python -m pytest tests/ -v
   cd ${PROJECT_ROOT}/dashboard && npx vitest run
   ```

   If tests fail, fix and re-run. PR creation is BLOCKED until tests pass.

6. **Create PR:**

   ```bash
   gh pr create \
     --repo ${REPO} \
     --title "<conventional-title> (#<issue_number>)" \
     --body "$(cat <<'EOF'
   ## Summary
   [summary of changes]

   Closes #<issue_number>

   ## Design Decisions
   [key choices from Step 3 design]

   ## Test Plan
   [what was tested, coverage summary]

   ## Migration Notes
   [if applicable, migration file names and rollback instructions]

   ## Quality Gate Summary
   - Design critique (Step 4): N findings
   - Plan drift check (Step 6): N findings
   - Implementation drift check (Step 9): N findings
   - Code review (Step 11): N findings (all Critical/High resolved)
   EOF
   )"
   ```

### Output

PR URL.

### Failure Modes

- Tests fail (Gate 1 blocks): fix and retry
- Push fails: retry after 5 seconds; if persistent, save PR body for manual creation
- gh auth failure: verify PAT with `gh auth status`
- Branch has conflicts with main: rebase (`git pull --rebase origin main`), resolve conflicts, re-push

---

## Step 13: CI Verification (Gate 2)

### Instructions

1. CI triggers automatically on PR creation via `.github/workflows/test.yml`.

2. Monitor CI status:

   ```bash
   gh run list --repo ${REPO} --branch <branch_name> --limit 1 --json status,conclusion,databaseId
   ```

3. **If CI passes:** Proceed to Step 14.

4. **If CI fails:** Diagnose:

   ```bash
   gh run view <run_id> --repo ${REPO} --log-failed
   ```

   Fix the issue, push to feature branch, CI re-runs automatically.

5. **If CI times out (> CI_MAX_WAIT_MINUTES):** Ask user for explicit approval before proceeding with local test results only.

**Note:** `gh pr checks` does NOT work with fine-grained PATs (known limitation). Use `gh run list` / `gh run view` instead.

### Output

CI status (pass/fail) with logs if failed.

### Failure Modes

- CI fails → read failure logs via `gh run view <id> --log-failed`, fix the issue on the feature branch, push, and CI re-runs automatically
- CI times out (> CI_MAX_WAIT_MINUTES) → ask user for explicit approval before proceeding with local test results only; bypassing Gate 2 without user consent would undermine P7 Triple-Gate Testing
- Fine-grained PAT cannot use `gh pr checks` (known limitation) → use `gh run list` / `gh run view` instead

---

## Step 14: Merge PR and Deploy

### Instructions

1. **Merge PR (squash merge):**

   ```bash
   gh pr merge <pr_number> --repo ${REPO} --squash --delete-branch
   ```

2. **Pull main:**

   ```bash
   cd ${PROJECT_ROOT} && git checkout main && git pull origin main
   ```

3. **Deploy to chorestory-dev:** The PostToolUse deploy hook triggers `scripts/deploy-dev.sh` which:
   - SSHs to chorestory-dev (${DEV_HOST}), pulls latest main, restarts containers
   - Runs E2E tests on chorestory-dev via Playwright
   - If deploy hook does not fire automatically, trigger manually:
     ```bash
     ${PROJECT_ROOT}/scripts/deploy-dev.sh
     ```

4. **Deploy to darwin (manual):**

   ```bash
   ssh root@${ENGINE_HOST} "cd /opt/millions && git pull origin main && docker compose -f ${COMPOSE_ENGINE} up -d --build --force-recreate"
   ```

5. **Database migration execution (if applicable):**
   ```bash
   ssh root@${DEV_HOST} "docker exec -i ${POSTGRES_CONTAINER} psql -U ${DB_USER} -d ${DB_NAME} < /opt/millions/postgres-migrations/<migration-file>.sql"
   ```

### Output

Merged PR, deployed to dev environment on both VMs.

### Failure Modes

- Merge conflicts: rebase and re-push
- Deploy fails on chorestory-dev: check deploy script logs, SSH and inspect container logs
- Deploy fails on darwin: SSH, check engine logs, rollback if needed
- E2E tests fail post-deploy: this is Gate 3 -- fix or rollback
- **E2E test data cleanup:** If the feature added test seed data, ensure `afterAll` cleanup runs. Cumulative test data affects risk calculations.

---

## Step 15: Quality Gate -- Post-Deploy Verification

### Instructions

1. Invoke `/reflexion:reflect` with check dimensions:
   - **E2E results verification:** Parse the E2E test output from Step 14's deploy script
   - **Health check verification:**
     ```bash
     curl -s http://${DEV_HOST}:8082/api/health
     curl -s http://${ENGINE_HOST}:8888/health
     ```
   - **Acceptance criteria spot-check:** For each criterion, verify evidence of correct behavior
   - **Regression check:** Did any existing functionality break?

2. Apply ambiguity circuit breaker.

3. **If E2E failures detected:** Diagnose whether test environment issue or real bug.
   - Real bug: create a hotfix branch, fix, push, re-deploy
   - Test environment: fix the test, document flaky test pattern

4. **If health checks fail:** Inspect container logs, restart services.

### Output

Post-deploy verification result.

### Failure Modes

- E2E tests fail post-deploy → diagnose whether the failure is a test environment issue or a real bug; if real bug, create a hotfix branch, fix, push, re-deploy; if test environment, fix the test and document the flaky test pattern
- Health checks fail → inspect container logs, restart services, check for configuration issues
- Acceptance criteria not verifiable (no E2E test covers it) → flag as a test gap for future improvement, verify manually if possible

---

## Step 16: Workflow Completion Summary

### Instructions

1. Update `claude_docs/session_notes.md` with WF2 results: PR URL, issue reference, quality gate outcomes, tests added, loop-back count, memorized insights.

2. Present a completion summary:

```
WF2 COMPLETE
=============

GitHub PR: [URL] (PR #NNN)
GitHub Issue: [URL] (Issue #NNN -- Closes #NNN)

Quality Gates:
- Step 4 (Design): [full critique / fast path reflect] -- N findings (X Crit, Y High, Z Med, W Low)
- Step 6 (Plan Drift): N findings
- Step 9 (Implementation Drift): N findings
- Step 10 (Memorize): [N insights saved / skipped]
- Step 15 (Post-Deploy): [pass / fail with details]

Deployment:
- chorestory-dev (${DEV_HOST}): [success / failed]
- darwin (${ENGINE_HOST}): [success / failed / manual required]

Tests:
- Total run: N (Python: N, Node.js: N)
- Passed: N
- Failed: N
- New tests added: N

Loop-backs used: N / 3 (global budget)
Memorized insights: [list or "none"]

Follow-up items:
- [any items requiring future attention]

WF2 complete for issue #NNN. Feature deployed to dev environment.
```

Do NOT suggest auto-transitioning to WF1 or restarting WF2.

### Output

Completion summary. WF2 terminates.

### Failure Modes

- None — this is an informational step. If previous steps had partial failures, this step reports the partial completion status with clear next steps.
