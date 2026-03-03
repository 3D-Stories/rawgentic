# Phase 3 Workflow: Feature Implementation (v1.1)

**Date:** 2026-03-01
**Author:** Workflow Design Agent
**Revision:** v1.1 -- critique fixes (1 Critical, 4 High, 8 Medium, 7 Low)
**Inputs:** phase2-principles.md, phase3-workflow-issue-creation.md (WF1 v2.3), phase2b-official-comparison.md, user task description
**Purpose:** Define the end-to-end workflow for implementing features and bug fixes from GitHub issues through to merged PRs. This is the counterpart to WF1 (Issue Creation) -- WF1 creates issues, WF2 implements them.

---

## Workflow: Feature Implementation

**Invocation:** `/implement-feature` skill (custom Claude Code skill), taking a GitHub issue number or URL as input
**Trigger:** User invokes `/implement-feature <issue-number>` or `/implement-feature <issue-URL>`
**Inputs:**

- GitHub issue number or URL (created by WF1 or manually)
- Existing codebase context (CLAUDE.md, MEMORY.md, session notes)
- Phase 2 principles (for quality gate placement)
- Runtime environment access (SSH to darwin and chorestory-dev for testing and deployment)

**Outputs:**

- Merged PR implementing the feature or fix described in the GitHub issue
- Updated test suites (unit, integration, E2E as applicable)
- Updated documentation (CLAUDE.md, MEMORY.md, session notes)
- Deployed and verified changes in the dev environment
- Memorized insights (if quality gates surface reusable patterns)

**Tracking:** The GitHub issue from WF1 is the SOLE requirements source. Implementation progress is tracked via PR status and commit history. No separate tracking artifacts are created.

**Principles enforced:**

- P1: Branch Isolation (feature branch for all changes)
- P2: Code Formatting (automated formatting before every commit)
- P3: Frequent Local Commits (commit every 5 minutes of active work)
- P4: Regular Remote Sync (push to origin every 30 minutes)
- P5: TDD Enforcement (tests before code, tests pass before PR)
- P6: Main-to-Dev Sync (deploy after merge -- chorestory-dev automated, darwin manual)
- P7: Triple-Gate Testing (local pre-PR, CI post-PR, dev server post-merge)
- P8: Shift-Left Critique (full critique post-design, reflect post-plan/post-TDD/post-deploy)
- P9: Continuous Memorization (memorize after quality gates with reusable insights)
- P10: Diagram-Driven Design (Excalidraw diagrams for architectural changes)
- P11: User-in-the-Loop Quality Gates (ambiguity circuit breaker at every gate)
- P12: Conventional Commit Discipline (all commits follow conventional format)
- P13: Pre-PR Code Review (multi-agent review before PR creation)
- P14: Documentation-Gated PRs (docs updated before PR)

**Diagram:** `.claude/framework-build/diagrams/workflow-feature-implementation.excalidraw`

**Termination:** WF2 terminates after deployment verification. If deployment fails, the workflow provides rollback instructions and terminates. No auto-transition to other workflows.

---

## Fast Path for Simple Bug Fixes

**Criteria:** A "simple bug fix" qualifies for the fast path when ALL of the following hold:

1. The GitHub issue has complexity estimate S (small)
2. The fix touches 3 or fewer files
3. No architecture or API surface changes
4. No database migration required
5. No new dependencies added

**Fast path behavior:** Skip Step 4 (full `/reflexion:critique`) -- replace with a lightweight `/reflexion:reflect` instead. All other steps remain. Rationale: the cost of reversing a small bug fix is low (revert 1-3 files), so the full 3-judge debate is disproportionate. The fast path saves approximately 1-3 minutes of sub-agent time without sacrificing quality for truly simple changes.

**Detection:** Step 2 (Analyze Codebase and Classify Complexity) classifies the issue as `simple_bugfix | standard_feature | complex_feature` using the same criteria listed above. Step 2's classification is the authoritative source -- it overrides any complexity label from the GitHub issue. The classification determines the gate level at Step 4.

**WF1-validated fast path:** If a `standard_feature` issue was created by WF1 (and therefore already has a critique-validated specification with acceptance criteria, scope, and risk assessment), Step 4 MAY use `/reflexion:reflect` instead of the full `/reflexion:critique`. Rationale: WF1 already ran a full 3-judge critique on the issue specification, so re-running a full critique on the design is partially redundant. Detection: Step 1 checks if the issue has the `wf1-created` label (set by WF1 Step 8). This fast path does NOT apply to `complex_feature` issues, which always get the full critique regardless of origin.

---

## Finding Application and Ambiguity Circuit Breaker

**Inherited from WF1 (identical behavior):** Apply ALL findings from quality gates automatically, with the ambiguity circuit breaker ALWAYS active. No attended/unattended distinction.

**Ambiguity circuit breaker triggers when:**

- Any finding has ambiguous interpretation (multiple valid implementation approaches)
- Two or more findings conflict (applying both would require contradictory code changes)
- A finding requires a judgment call not captured in the GitHub issue

**When triggered:** The workflow STOPS at the current step and waits for user input. It does NOT auto-apply the unambiguous findings separately -- the full set is applied together after resolution.

---

## Git Worktree Decision

**Decision: Do NOT use git worktrees for WF2.** Rationale:

1. This is a solo developer project -- parallel feature implementation is rare
2. Each worktree requires its own `node_modules` install and Docker container setup (significant overhead)
3. Simple `git checkout -b` branching is sufficient for sequential feature work
4. If parallel work is needed in the future, worktrees can be added as an enhancement

The workflow uses standard branching: `git checkout -b feature/<issue-number>-<short-description>` or `git checkout -b fix/<issue-number>-<short-description>`.

---

## Multi-PR Strategy

**Decision: Single PR per issue is the default.** If during Step 3 (Design) or Step 5 (Plan) it becomes clear that the implementation is too large for a single PR (more than 500 lines changed, or distinct logical phases), the plan should decompose into ordered sub-PRs:

1. Sub-PR 1: Database migrations + backend logic
2. Sub-PR 2: API endpoints + backend tests
3. Sub-PR 3: Frontend changes + E2E tests

Each sub-PR follows Steps 8-14 independently (TDD, review, PR, CI, deploy). The plan (Step 5) documents the decomposition. The GitHub issue tracks all sub-PRs via cross-references.

**Multi-PR execution mechanism:** The plan output (Step 5) includes a `sub_prs` array when decomposition is needed. The skill file iterates Steps 8-14 for each sub-PR in order. Each sub-PR gets its own branch off main (e.g., `feature/155-part1-migrations`, `feature/155-part2-api`). Memorization (Step 10) runs once after the final sub-PR's Step 9. If a flaw in an already-merged sub-PR is discovered during a later sub-PR, create a separate fix PR targeting the flaw (do not try to amend the merged PR). The GitHub issue tracks all sub-PRs via cross-reference comments.

---

## Workflow Resumption and Session Interruption

WF2 is a 16-step pipeline that may span multiple Claude Code sessions due to context compaction, rate limits, terminal disconnects, or machine restarts. This section defines how to detect and resume from any step.

**Checkpoint artifacts (persist across sessions):**

| Artifact            | Location                       | Created at   | Purpose                |
| ------------------- | ------------------------------ | ------------ | ---------------------- |
| GitHub issue        | GitHub (remote)                | Before WF2   | Requirements source    |
| Feature branch      | Git (remote)                   | Step 7       | Code state             |
| Design document     | `docs/plans/` or session notes | Step 3       | Architecture reference |
| Implementation plan | Session notes or plan file     | Step 5       | Task list              |
| PR                  | GitHub (remote)                | Step 12      | Review/merge state     |
| Session notes       | `claude_docs/session_notes.md` | Continuously | Progress log           |

**Step detection on resume:** When resuming WF2 after an interruption, determine the current step by checking these markers in order:

1. **PR exists and is merged?** → Resume at Step 15 (post-deploy verification)
2. **PR exists and CI passed?** → Resume at Step 14 (merge + deploy)
3. **PR exists?** → Resume at Step 13 (CI verification)
4. **Feature branch has code changes with passing tests?** → Resume at Step 11 (code review)
5. **Feature branch has code changes?** → Resume at Step 9 (implementation drift check)
6. **Feature branch exists but is empty?** → Resume at Step 8 (TDD implementation)
7. **Design document exists in session notes?** → Resume at Step 5 (create plan)
8. **Issue is validated in session notes?** → Resume at Step 2 (analyze codebase)
9. **None of the above?** → Start from Step 1

**Context compaction protocol:** Before context compacts, the session notes should document:

- Current step number and sub-step (e.g., "Step 8, task 7 of 15")
- Any quality gate findings not yet applied
- The feature branch name and last commit SHA
- Any unresolved circuit breaker state

**Rate limit handling during multi-agent steps:** Steps 4, 8, and 11 dispatch multiple sub-agents. If a rate limit is hit:

- Log the partial results in session notes
- Wait for the rate limit reset (do not retry in a loop)
- Resume the step with remaining agents after reset

---

## Global Loop-Back Budget

Loop-backs across the workflow are capped to prevent unbounded rework:

- Step 4 (design critique) → Step 3: max 2 iterations (`MAX_DESIGN_LOOPBACK_ITERATIONS = 2`)
- Step 8 (TDD implementation) → Step 3: max 1 iteration
- Step 11 (code review) → Step 3: max 1 iteration

**Global cap: Maximum 3 design loop-backs per WF2 invocation.** If the global cap is reached, the workflow STOPS and escalates to the user with a full summary of all loop-back triggers. The user must decide whether to continue, narrow scope, or abandon.

---

## Steps

### Step 1: Receive Issue Reference

**Type:** user decision
**Actor:** human
**Command:** `/implement-feature <issue-number>` (user invokes this skill to start WF2)
**Input:** GitHub issue number or URL. The user provides this as argument to `/implement-feature`.
**Action:** The user invokes `/implement-feature` with a GitHub issue reference. Claude Code:

1. Validates the issue exists via `gh issue view <number>`
2. Confirms the issue is open (not already closed or in-progress)
3. Displays the issue title, description, and acceptance criteria to the user
4. Asks the user to confirm this is the correct issue to implement
5. If the issue was created by WF1, it should have the full specification (acceptance criteria, scope, affected components, risk assessment, complexity estimate). If manually created, Claude asks clarifying questions to fill any gaps.

**Output:** Validated issue reference: { issue_number: number, title: string, body: string, labels: string[], complexity: string }
**Failure mode:** (1) Issue does not exist. Recovery: ask user for correct issue number. (2) Issue is already closed. Recovery: ask if user wants to reopen or reference a different issue. (3) Issue lacks acceptance criteria (manually created without WF1). Recovery: Claude generates acceptance criteria from the description and asks user to confirm before proceeding.
**Principle alignment:** P11 (User-in-the-Loop) -- the user chooses which issue to implement and confirms the requirements.
**Critique level:** N/A (not a quality gate)
**User selection:** yes -- user confirms the issue is correct and requirements are complete

---

### Step 2: Analyze Codebase and Classify Complexity

**Type:** automated
**Actor:** sub-agent (via Serena MCP + Context7 MCP + codebase exploration)
**Command:** Serena `find_symbol`, `find_referencing_symbols`, `get_symbols_overview` + Context7 for library docs
**Input:** Validated issue from Step 1, plus codebase context (CLAUDE.md architecture section, affected components from issue, existing code)
**Action:** Claude performs deep codebase analysis:

1. **Component mapping:** Using Serena MCP, identify all files, functions, and classes that will need to change. Map the issue's "affected components" to actual code symbols.
2. **Dependency analysis:** Trace call chains from affected entry points (API routes, scheduler jobs, strategy methods) to understand the full blast radius.
3. **Existing test inventory:** Identify existing test files and test cases that cover the affected code. Note gaps.
4. **Library research:** If the feature requires new libraries or uses existing libraries in new ways, use Context7 MCP to fetch current documentation.
5. **Complexity classification:** Based on the analysis, classify the implementation:
   - `simple_bugfix`: 1-3 files, no architecture change, no migration, no new deps
   - `standard_feature`: 4-15 files, contained architecture change, may need migration
   - `complex_feature`: 15+ files, cross-service changes, multiple migrations, new deps

6. **Diagram assessment:** Determine if the feature requires an Excalidraw architecture diagram (criteria: new service interactions, new data flows, new database tables, or changes to existing architecture patterns).

**Output:** Analysis document: { affected_files: string[], affected_symbols: object[], dependency_graph: object, existing_tests: string[], test_gaps: string[], complexity: "simple_bugfix" | "standard_feature" | "complex_feature", needs_diagram: boolean, library_docs: object }
**Failure mode:** (1) Serena MCP unavailable. Recovery: fall back to Grep/Glob for code search -- less precise but functional. (2) Issue references components that don't exist (hallucinated by WF1 brainstorm). Recovery: flag the discrepancy and ask user for guidance. (3) Complexity assessment is uncertain. Recovery: default to `standard_feature` (the middle path).
**Principle alignment:** P10 (Diagram-Driven Design) -- identifies if diagrams are needed; P8 (Shift-Left Critique) -- thorough analysis before design reduces late-stage surprises.
**Critique level:** N/A (not a quality gate -- this produces the analysis that feeds design)
**User selection:** no -- automated analysis

---

### Step 3: Design Solution Architecture

**Type:** automated
**Actor:** sub-agent
**Command:** `/superpowers:brainstorming` (design pipeline: context analysis, clarifying questions, 2-3 approach proposals, validation)
**Input:** Analysis document from Step 2, GitHub issue requirements, codebase context
**Action:** The brainstorm sub-agent designs the solution:

1. **Clarifying questions:** If any aspect of the issue is ambiguous after analysis, the sub-agent generates targeted questions. These are resolved by checking the issue body, CLAUDE.md, or (if still ambiguous) asking the user.
2. **Approach proposals:** Generate 2-3 implementation approaches with trade-offs:
   - Approach A: The straightforward path (lowest risk, may not be optimal)
   - Approach B: The optimal path (best architecture, may be more complex)
   - Approach C (if applicable): Alternative that addresses specific constraints
3. **Approach selection:** Based on the issue's complexity classification and acceptance criteria, recommend one approach with rationale.
4. **Design document:** Produce a structured design:
   - Component changes (which files, which functions, what modifications)
   - Data flow changes (new/modified API routes, database queries, Redis pub/sub messages)
   - Database migrations (if any)
   - Error handling strategy
   - Security implications
   - Performance implications
5. **Diagram creation (conditional):** If Step 2 flagged `needs_diagram: true`, create an Excalidraw diagram showing the new/modified architecture. Use Excalidraw MCP if available; otherwise write native Excalidraw JSON.

**Output:** Design document (structured markdown) + optional Excalidraw diagram
**Failure mode:** (1) All approaches have significant trade-offs with no clear winner. Recovery: present all approaches to the user and let them choose. (2) The design reveals the issue scope is much larger than estimated. Recovery: flag for user decision -- should the scope be narrowed, or should the plan accommodate the full scope? (3) Excalidraw MCP unavailable. Recovery: write diagram as native JSON file (fontFamily: 2, Helvetica).
**Principle alignment:** P8 (Shift-Left Critique) -- design is the artifact that will be critiqued in Step 4; P10 (Diagram-Driven Design) -- architecture diagrams created before implementation.
**Critique level:** N/A (not a quality gate -- this is the artifact being gated)
**User selection:** no -- automated generation (user reviews indirectly via critique in Step 4)

---

### Step 4: Quality Gate -- Design Critique

**Type:** quality gate
**Actor:** sub-agent (3-judge multi-agent debate for standard/complex; single-pass reflect for simple_bugfix)
**Command:** `/reflexion:critique` (standard/complex) OR `/reflexion:reflect` (simple_bugfix fast path)
**Input:** Design document from Step 3, analysis from Step 2, GitHub issue requirements, existing codebase architecture
**Action:**

**For standard_feature / complex_feature (full critique):**
Three judge sub-agents independently analyze the design, then engage in debate rounds. Each judge evaluates:

- **Architecture alignment:** Does the design respect existing patterns (order execution pattern, broker mode routing, feature flag conventions, exception handling in critical paths)?
- **Completeness:** Are all acceptance criteria addressed? Are edge cases handled? Are failure modes identified?
- **Security:** Does the design follow security standards (auth on all data channels, input validation, no secrets in code)?
- **Testability:** Is every component of the design testable? Are TDD boundaries clear?
- **Scope fidelity:** Does the design match the issue scope exactly -- no scope creep, no missing requirements?
- **Migration safety:** If database changes are involved, are they backward-compatible? Is the migration idempotent?
- **Performance:** Are there potential bottlenecks, expensive queries, or resource exhaustion risks?

The critique produces findings in four severity tiers (Critical / High / Medium / Low), each tagged with an ambiguity flag.

**For simple_bugfix (fast path -- lightweight reflect):**
Single-pass reflection checking:

- Does the fix actually address the reported bug?
- Are there unintended side effects?
- Is the fix in the right layer (not a band-aid fix when the root cause is elsewhere)?

**Ambiguity circuit breaker (ALWAYS active, both paths):** Same behavior as WF1 Step 4. If any finding is ambiguous or conflicting, STOP and wait for user resolution. Otherwise, apply all findings automatically to the design document.

**Finding volume thresholds (critique path only):** >5 Critical OR >5 High OR >10 Medium OR >10 Low triggers loop-back to Step 3 (re-design with findings as constraints). Maximum 2 loop-back iterations before escalating to user. Configurable: `MAX_DESIGN_LOOPBACK_ITERATIONS = 2`.

**Output:** Amended design document (findings applied) OR blocked state (circuit breaker triggered, awaiting user)
**Failure mode:** (1) Critique finds zero issues (suspicious). Recovery: verify judges actually analyzed the design against the codebase. (2) Volume threshold exceeded twice. Recovery: escalate to user with full finding list -- the issue may need refinement before implementation. (3) Critique identifies that the issue itself is flawed (acceptance criteria are contradictory). Recovery: suggest updating the GitHub issue before proceeding (may require returning to WF1).
**Principle alignment:** P8 (Shift-Left Critique) -- this is the pre-execution gate, the last cheap point to catch design flaws before code is written; P11 (User-in-the-Loop) -- circuit breaker escalates on ambiguity.
**Critique level:** Full critique (`/reflexion:critique`) for standard/complex; lightweight reflect (`/reflexion:reflect`) for simple_bugfix. RATIONALE: Architecture decisions are expensive to reverse. Once code is written, changing the approach costs hours of work. The full 3-judge debate is justified at this gate because: (a) catching a flawed design here prevents wasted implementation effort, (b) the cost-of-defect curve shows design-stage fixes are 10-50x cheaper than post-implementation fixes, (c) multi-agent review catches architectural blind spots that single-pass analysis misses. For simple bug fixes, the cost of reversal is low (1-3 files), so a lightweight reflect suffices.
**User selection:** only on circuit breaker trigger -- otherwise fully automated

---

### Step 5: Create Implementation Plan

**Type:** automated
**Actor:** sub-agent
**Command:** `/superpowers:writing-plans` (TDD-optimized bite-sized tasks with exact file paths)
**Input:** Amended design document from Step 4, codebase analysis from Step 2, GitHub issue
**Action:** The planning sub-agent breaks the design into an ordered list of implementation tasks:

1. **Branch naming convention:** Document the branch name for Step 7 to create: `feature/<issue-number>-<short-desc>` or `fix/<issue-number>-<short-desc>`. (Step 5 plans the name; Step 7 executes the branch creation.)
2. **Database migrations (if any):** Write migration SQL files in `postgres-migrations/`
3. **TDD task decomposition:** Each task is a 2-5 minute unit following Red-Green-Refactor:
   - RED: Write the failing test(s) for this specific behavior
   - GREEN: Write the minimum code to make the test(s) pass
   - REFACTOR: Clean up without changing behavior
4. **Task ordering:** Dependencies between tasks are explicit. Tasks that can run in parallel are marked.
5. **Test strategy:** For each task, specify:
   - Which test file to create or modify
   - What test cases to write (with expected behavior descriptions)
   - Whether unit, integration, or E2E test
6. **Documentation tasks:** Identify which documentation needs updating (CLAUDE.md sections, migration readme, API docs)
7. **Multi-PR decomposition (if applicable):** If the plan exceeds 500 lines of change, decompose into sub-PRs with explicit ordering and cross-references

The plan includes explicit checkpoints where progress should be verified before continuing.

**Output:** Implementation plan document with ordered tasks, each specifying: task name, type (migration | test | implementation | refactor | documentation), file paths, test strategy, estimated time, dependencies, checkpoint (yes/no)
**Failure mode:** (1) Plan has too many tasks (>30 for a single feature). Recovery: this may indicate the design is too broad -- flag for user review and suggest scope narrowing or multi-PR decomposition. (2) Plan has circular dependencies between tasks. Recovery: re-order to break the cycle. (3) Plan references files or functions that don't exist. Recovery: verify against Step 2 analysis; if discrepancy, update plan.
**Principle alignment:** P5 (TDD Enforcement) -- every task includes RED-GREEN-REFACTOR; P3 (Frequent Local Commits) -- plan includes commit checkpoints; P12 (Conventional Commit Discipline) -- plan specifies commit message format for each checkpoint.
**Critique level:** N/A (not a quality gate -- this is the translation of the critiqued design into actionable steps)
**User selection:** no -- automated generation

---

### Step 6: Quality Gate -- Plan Drift Check

**Type:** quality gate
**Actor:** sub-agent (single-pass reflection)
**Command:** `/reflexion:reflect`
**Input:** Implementation plan from Step 5, amended design document from Step 4, GitHub issue requirements
**Action:** Lightweight single-pass reflection checking for drift:

1. **Design-plan alignment:** Does every design component map to at least one plan task? Are there plan tasks that weren't in the design (scope creep)?
2. **TDD completeness:** Does every implementation task have a corresponding test task preceding it?
3. **Acceptance criteria coverage:** Does the plan, if fully executed, satisfy all acceptance criteria from the GitHub issue?
4. **Task ordering validity:** Are dependencies correctly ordered? Are there implicit dependencies not captured?
5. **Commit checkpoint adequacy:** Are checkpoints placed at logical boundaries (after each test-pass, after each component complete)?

Produces findings with a confidence score. Applies the ambiguity circuit breaker (same rules as Step 4).

**Output:** Validated plan (findings applied) OR blocked state (circuit breaker triggered, awaiting user)
**Failure mode:** (1) Reflect identifies significant drift (plan misses a design component). Recovery: add missing tasks to the plan. (2) Reflect identifies scope creep (plan adds tasks not in design). Recovery: remove excess tasks or flag for user decision if they represent legitimate enhancements discovered during planning.
**Principle alignment:** P8 (Shift-Left Critique) -- lightweight reflect at translation gate; P5 (TDD Enforcement) -- validates TDD coverage in plan.
**Critique level:** Lightweight reflect (`/reflexion:reflect`). RATIONALE: The design was already critiqued by a full 3-judge panel in Step 4. This step checks whether the plan faithfully translates the critiqued design -- a drift check, not a re-evaluation. The cost of change is still moderate (no code written yet, but planning effort would be wasted). A single-pass reflect is sufficient because the input (critiqued design) has already been validated.
**User selection:** only on circuit breaker trigger -- otherwise fully automated

---

### Step 7: Create Feature Branch

**Type:** automated
**Actor:** hook + sub-agent
**Command:** `git checkout -b feature/<issue-number>-<short-desc>` or `fix/<issue-number>-<short-desc>`
**Input:** Validated plan from Step 6, issue metadata (number, type, short description)
**Action:**

1. Ensure working directory is clean (`git status` shows no uncommitted changes)
2. Pull latest main: `git pull origin main`
3. Create feature branch with conventional naming:
   - Features: `feature/<issue-number>-<kebab-case-summary>` (e.g., `feature/155-add-trailing-stop`)
   - Bug fixes: `fix/<issue-number>-<kebab-case-summary>` (e.g., `fix/156-sentiment-null-crash`)
4. Push the empty branch to origin: `git push -u origin <branch-name>`
5. Link the branch to the GitHub issue via comment: `gh issue comment <number> --body "Implementation started on branch \`<branch-name>\`"`

**Output:** Feature branch created and pushed, issue commented with branch link
**Failure mode:** (1) Working directory is dirty. Recovery: stash changes first (`git stash`), create branch, then ask user if stash should be applied. (2) Branch name already exists. Recovery: the issue may already be in progress -- ask user if they want to resume (checkout existing branch) or start fresh (delete and recreate). (3) Push fails (network). Recovery: continue locally, push will be retried by the regular remote sync (P4).
**Principle alignment:** P1 (Branch Isolation) -- feature branch created; P4 (Regular Remote Sync) -- initial push for remote backup; P12 (Conventional Commit Discipline) -- branch naming follows convention.
**Critique level:** N/A (not a quality gate)
**User selection:** no -- automated (branch naming derived from issue metadata)

---

### Step 8: TDD Implementation

**Type:** automated
**Actor:** sub-agent (via `/superpowers:test-driven-development` + `/superpowers:executing-plans`)
**Command:** `/superpowers:test-driven-development` for TDD enforcement, `/superpowers:executing-plans` for batch execution
**Input:** Validated plan from Step 6, feature branch from Step 7, codebase
**Action:** Execute the implementation plan task by task following strict TDD:

**For each task in the plan:**

1. **RED phase:** Write the failing test(s) specified in the plan. Run the test suite to confirm the new tests FAIL (they must fail -- if they pass before implementation, the test is not testing the right thing).
2. **GREEN phase:** Write the minimum code to make the failing tests pass. No more, no less. Run the test suite to confirm ALL tests pass (both new and existing).
3. **REFACTOR phase:** Clean up the code without changing behavior. Run tests again to confirm nothing broke.
4. **Commit:** Create a conventional commit: `feat(<scope>): <description> (#<issue-number>)` or `fix(<scope>): <description> (#<issue-number>)`. For intermediate commits, use `wip(<scope>): <checkpoint-description>`.
5. **Checkpoint (if plan specifies):** Verify accumulated progress: run full test suite, check that the feature branch is in a deployable state.

**Parallel task execution:** If the plan identified independent tasks (e.g., backend API endpoint and frontend component that don't share imports), use `/superpowers:subagent-driven-development` to execute them in parallel with two-stage review (spec compliance + code quality) per task.

**Debugging:** If a test fails unexpectedly during GREEN phase, invoke `/superpowers:systematic-debugging` (4-phase: root cause investigation, pattern analysis, hypothesis testing, implementation). If 3 fixes fail, the systematic-debugging skill escalates to architectural review.

**Formatting:** P2 enforcement -- after every file write, run Prettier (JS/JSX) or Ruff (Python) automatically via PostToolUse hook.

**Commit frequency:** P3 enforcement -- commit at minimum every 5 minutes via PostToolUse timer hook.

**Push frequency:** P4 enforcement -- push to origin at minimum every 30 minutes via PostToolUse timer hook.

**Output:** Implemented feature with passing tests on the feature branch, committed and pushed
**Failure mode:** (1) Test fails and cannot be fixed after systematic debugging. Recovery: flag the blocker to the user with the debugging analysis. The user may need to clarify requirements or accept a design change. (2) Implementation reveals the design was flawed (unforeseen technical constraint). Recovery: flag for user decision -- may need to loop back to Step 3 (Design) with the new constraint. Maximum 1 design loop-back from TDD (counts toward the global loop-back budget of 3). Re-entry at Step 3 re-executes Steps 3-8. (3) TDD discipline breaks (code written before test). Recovery: the superpowers:test-driven-development skill has iron-law enforcement -- it should prevent this. If it happens anyway, flag it as a process violation and require the missing test before proceeding.
**Principle alignment:** P5 (TDD Enforcement) -- strict RED-GREEN-REFACTOR; P2 (Code Formatting) -- automated formatting; P3 (Frequent Local Commits) -- 5-minute timer; P4 (Regular Remote Sync) -- 30-minute push timer; P12 (Conventional Commit Discipline) -- all commits follow format.
**Critique level:** N/A (not a quality gate -- this is execution. Quality gates bookend this step: Step 6 before, Step 9 after.)
**User selection:** no during normal execution -- only if a blocker or design-level issue is discovered

---

### Step 9: Quality Gate -- Implementation Drift Check

**Type:** quality gate
**Actor:** sub-agent (single-pass reflection + evidence enforcement)
**Command:** `/reflexion:reflect` + `superpowers:verification-before-completion`
**Input:** Implemented code on feature branch, validated plan from Step 6, amended design from Step 4, GitHub issue acceptance criteria
**Action:** Two-part post-implementation quality check:

**Part A: Drift check (`/reflexion:reflect`):**

1. **Plan-implementation alignment:** Does every plan task have a corresponding implementation? Are there implemented changes not in the plan (scope creep)?
2. **Design-implementation alignment:** Does the implementation follow the critiqued design architecture? Were any design decisions overridden during implementation?
3. **Acceptance criteria verification:** For each acceptance criterion in the GitHub issue, identify the test(s) that verify it. Flag any uncovered criteria.
4. **Test coverage assessment:** Are there implemented code paths without corresponding tests?
5. **Documentation check:** Are CLAUDE.md, migration docs, and API docs updated for the changes?

**Part B: Evidence enforcement (`superpowers:verification-before-completion`):**

1. Run the full test suite (Python + Node.js) and collect results
2. Verify all new tests actually test the new behavior (not vacuously passing)
3. Confirm no test regressions

Produces findings with confidence scores. Applies the ambiguity circuit breaker.

**Output:** Verified implementation (findings applied) OR blocked state (circuit breaker, awaiting user)
**Failure mode:** (1) Drift detected (implementation diverged from plan/design). Recovery: determine if the divergence is an improvement or a regression. If improvement, update the design doc to match. If regression, fix the implementation. (2) Missing test coverage. Recovery: add the missing tests before proceeding. (3) Acceptance criteria not met. Recovery: implement the missing criteria before proceeding -- do not skip to PR.
**Principle alignment:** P8 (Shift-Left Critique) -- post-execution reflect catches drift before review; P5 (TDD Enforcement) -- validates test coverage; P14 (Documentation-Gated PRs) -- checks documentation is updated.
**Critique level:** Lightweight reflect (`/reflexion:reflect`). RATIONALE: The design was critiqued at Step 4. The plan was validated at Step 6. This step checks whether the implementation faithfully realizes the plan and design -- a drift check. Code has been written, so the cost of change is moderate (refactoring is cheaper than re-architecture). A single-pass reflect with evidence enforcement is sufficient because the architecture has already been validated by a full critique.
**User selection:** only on circuit breaker trigger -- otherwise fully automated

---

### Step 10: Conditional Memorization (Post-Implementation)

**Type:** conditional automated (knowledge capture -- not a scrutiny gate, no circuit breaker)
**Actor:** sub-agent
**Command:** `/reflexion:memorize`
**Input:** All quality gate findings from Steps 4, 6, and 9. Implementation experience (debugging insights, unexpected constraints, code patterns discovered).
**Action:** If any quality gate surfaced reusable insights -- patterns that would apply to future implementations -- invoke `/reflexion:memorize` to curate them into CLAUDE.md. Examples of memorizable insights:

- A new code pattern established during this implementation (e.g., "all new scheduler jobs must have a cooldown guard")
- A debugging insight that would prevent future issues (e.g., "asyncpg connection pool exhaustion occurs when queries inside transactions are not awaited")
- An architecture constraint discovered during design critique (e.g., "Redis pub/sub messages must be under 512KB to avoid truncation")
- A testing pattern that proved effective (e.g., "E2E tests for Settings page must navigate away and back to bypass TanStack Query cache")

The memorize command uses ACE to:

1. Extract insights from critique/reflect findings and implementation experience
2. Check for duplication against existing CLAUDE.md and MEMORY.md
3. If novel, append to the appropriate section
4. If at capacity, suggest archiving stale entries

If no reusable patterns were surfaced, this step is skipped entirely.

This step runs in **parallel** with Step 11 (Code Review) -- both start as soon as Step 9 completes. The skill file dispatches Step 10 and Step 11 as two parallel Agent tool calls in a single message (Step 10 with `run_in_background=true`, Step 11 in the foreground). A join barrier before Step 12 ensures both complete: Step 12 must check for CLAUDE.md changes from Step 10 and include them in the commit.

**Output:** Updated CLAUDE.md (if insights were memorized) or no output (if skipped)
**Failure mode:** (1) Over-memorization. Recovery: memorize command should filter for novelty and generalizability. (2) CLAUDE.md at capacity. Recovery: suggest archiving stale entries per the existing MEMORY.md warning pattern. (3) Duplicate. Recovery: memorize command deduplicates.
**Principle alignment:** P9 (Continuous Memorization) -- curate reusable insights from implementation into persistent knowledge.
**Critique level:** Memorize (`/reflexion:memorize`). RATIONALE: Not a scrutiny gate but a knowledge capture gate. Minimal cost. Conditional -- only fires if novel, generalizable insights exist.
**User selection:** no -- automated knowledge capture

---

### Step 11: Pre-PR Code Review

**Type:** automated
**Actor:** sub-agent (multi-agent review)
**Command:** `/superpowers:requesting-code-review` (dispatch reviewer) + `/code-review` (4-agent parallel review)
**Input:** All changes on feature branch (diff from main), design document, implementation plan
**Action:** Multi-agent automated code review:

1. **Dispatch review:** `/superpowers:requesting-code-review` creates a SHA-based diff and dispatches the review sub-agent with template-driven prompts.
2. **4-agent parallel review (`/code-review`):**
   - Agent 1: CLAUDE.md compliance (code style, naming, patterns)
   - Agent 2: CLAUDE.md compliance (redundant check for critical rules)
   - Agent 3: Bug detection (logic errors, edge cases, race conditions)
   - Agent 4: Git-blame history analysis (does this change break patterns established by prior commits?)
3. **Supplementary review (pr-review-toolkit agents, selective):**
   - `pr-test-analyzer`: Evaluates behavioral test coverage and critical gaps
   - `silent-failure-hunter`: Scans for silent failures in catch blocks
   - `code-simplifier`: Checks for unnecessary complexity
4. **Confidence threshold:** Only findings with confidence >= 80 are surfaced (filtering noise).
5. **Severity-based fix workflow:** Critical/High findings must be fixed before PR. Medium/Low are advisory.
6. **Ambiguity circuit breaker:** Same rules apply. If any finding is ambiguous, STOP for user resolution.

After review findings are applied:

- `/superpowers:receiving-code-review` evaluates each finding: verifies before implementing, checks YAGNI, pushes back on unnecessary changes. No performative agreement.

**Output:** Review findings applied to code, all Critical/High items resolved
**Failure mode:** (1) Review finds a fundamental design flaw missed by Step 4 critique. Recovery: this is expensive but necessary -- loop back to Step 3 (Design) with the flaw identified. Maximum 1 review-triggered design loop-back (counts toward the global loop-back budget of 3). Re-entry at Step 3 re-executes Steps 3-11. (2) Review generates excessive noise (>20 Low findings). Recovery: filter at confidence >= 80 and focus on Critical/High. (3) Review sub-agents disagree on a finding. Recovery: the receiving-code-review skill adjudicates by verifying the claim against actual code.
**Principle alignment:** P13 (Pre-PR Code Review) -- multi-agent review before PR; P2 (Code Formatting) -- formatting issues caught here.
**Critique level:** N/A (this is a code review, not a reflexion gate -- but it serves as a de facto quality gate with its own enforcement mechanisms: confidence thresholds, severity-based fix requirements, circuit breaker, and design loop-back capability)
**User selection:** only on circuit breaker trigger -- otherwise automated fix for Critical/High, advisory for Medium/Low

---

### Step 12: Create PR and Push

**Type:** automated
**Actor:** sub-agent (via `/commit-push-pr` + `gh` CLI)
**Command:** `/commit-push-pr` for final commit/push, `gh pr create` for PR creation
**Input:** Reviewed and fixed code from Step 11, design document, implementation plan, test results
**Action:**

1. **Final commit:** Ensure all changes are committed with proper conventional commit messages referencing the issue number. Note: WIP commits are NOT squashed here -- the `--squash` flag on `gh pr merge` in Step 14 handles history cleanup at merge time. Do NOT use `git rebase -i` (interactive rebase is unsupported in Claude Code).
2. **Push to origin:** `git push origin <branch-name>`
3. **Create PR:** `gh pr create` with:
   - **Title:** Conventional format matching the issue title (e.g., `feat(engine): add trailing stop exit mode (#155)`)
   - **Body:** Structured PR description including:
     - Summary of changes
     - Link to GitHub issue: `Closes #<issue-number>`
     - Design decisions made (from Step 3)
     - Test plan (what was tested, how)
     - Migration notes (if applicable)
     - Screenshots (if UI changes)
   - **Labels:** Matching the issue labels
   - **Base branch:** main
4. **Gate 1 check (P7 Triple-Gate):** Before creating the PR, the PreToolUse hook runs the full test suite (Python + Node.js). If tests fail, PR creation is BLOCKED until they pass.

**Output:** PR URL (e.g., `https://github.com/3D-Stories/millions/pull/NNN`)
**Failure mode:** (1) Test suite fails (Gate 1 blocks PR creation). Recovery: fix the failing tests, re-run, retry PR creation. (2) Push fails (network). Recovery: retry after 5 seconds; if persistent, save PR body locally for manual creation. (3) `gh` CLI auth failure. Recovery: verify PAT is valid with Issues and PRs (r/w) scope. (4) PR creation fails (branch has conflicts with main). Recovery: rebase feature branch on main, resolve conflicts, re-push.
**Principle alignment:** P7 (Triple-Gate Testing) -- Gate 1 (local pre-PR tests); P12 (Conventional Commit Discipline) -- PR title and commits follow conventional format; P14 (Documentation-Gated PRs) -- PR body includes documentation changes.
**Critique level:** N/A (not a quality gate -- this is an execution step)
**User selection:** no -- automated (user already approved design and reviewed findings)

---

### Step 13: CI Verification (Gate 2)

**Type:** automated
**Actor:** CI (GitHub Actions via `.github/workflows/test.yml`)
**Command:** N/A (triggered automatically by PR creation)
**Input:** PR on GitHub, feature branch pushed to origin
**Action:** GitHub Actions CI runs automatically:

1. Python tests: `pytest engine/tests/ -v`
2. Node.js tests: `vitest run` in dashboard
3. (Future: lint checks via Ruff + Prettier, once CI is extended per P7)

Claude monitors CI status via `gh run list --branch <branch>` and `gh run view <id>`.

**Waiting behavior:** Claude waits for CI to complete (polling `gh run list` every 30 seconds, max 10 minutes). If CI passes, proceed to Step 14. If CI fails, diagnose and fix.

**Output:** CI status (pass/fail) with logs if failed
**Failure mode:** (1) CI fails. Recovery: read failure logs via `gh run view <id> --log-failed`, fix the issue on the feature branch, push, and CI re-runs automatically. (2) CI times out. Recovery: check GitHub Actions status page; if systemic, ask user for explicit approval before proceeding with local test results only -- bypassing Gate 2 without user consent would undermine P7 Triple-Gate Testing. (3) Fine-grained PAT cannot use `gh pr checks` (known limitation). Recovery: use `gh run list` / `gh run view` instead.
**Principle alignment:** P7 (Triple-Gate Testing) -- Gate 2 (CI post-PR tests); environment isolation catches dependency-specific failures.
**Critique level:** N/A (not a quality gate -- this is CI infrastructure)
**User selection:** no -- automated CI process

---

### Step 14: Merge PR and Deploy

**Type:** automated
**Actor:** sub-agent + hook (deploy-on-push)
**Command:** `gh pr merge <number> --squash --delete-branch`, then deploy-dev.sh (triggered by PostToolUse hook on push)
**Input:** Passing CI from Step 13, PR number
**Action:**

1. **Merge PR:** `gh pr merge <number> --squash --delete-branch` -- squash merge to keep main history clean, auto-delete feature branch.
2. **Pull main:** `git checkout main && git pull origin main`
3. **Deploy:** The PostToolUse deploy hook triggers `scripts/deploy-dev.sh` which:
   - SSHs to chorestory-dev (10.0.17.203), pulls latest main, restarts containers
   - Runs E2E tests on chorestory-dev via Playwright
   - (Note: darwin deployment is currently manual -- deploy script only covers chorestory-dev)
4. **Darwin deploy (manual step):** SSH to darwin (root@10.0.17.204), pull main, restart engine container: `ssh root@10.0.17.204 "cd /opt/millions && git pull origin main && docker compose -f docker-compose.engine.dev.yml up -d --build --force-recreate"`

**Output:** Merged PR, deployed to dev environment on both VMs
**Failure mode:** (1) Merge conflicts. Recovery: should not happen if CI passed on a clean branch, but if they do, rebase and re-push. (2) Deploy fails on chorestory-dev. Recovery: check deploy script logs, SSH to chorestory-dev and inspect container logs. (3) Deploy fails on darwin. Recovery: SSH to darwin, check engine logs, rollback if needed (`git checkout <previous-sha>`). (4) E2E tests fail post-deploy. Recovery: this is Gate 3 -- fix the issue (may require a hotfix branch) or rollback.
**Principle alignment:** P6 (Main-to-Dev Sync) -- deploy after merge; P7 (Triple-Gate Testing) -- Gate 3 (dev server post-deploy E2E).
**Critique level:** N/A (not a quality gate -- this is deployment execution)
**User selection:** no -- automated deployment (user already approved via PR)

---

### Step 15: Quality Gate -- Post-Deploy Verification

**Type:** quality gate
**Actor:** sub-agent (single-pass reflection)
**Command:** `/reflexion:reflect`
**Input:** Deployment results (deploy script output, E2E test results), GitHub issue acceptance criteria, design document
**Action:** Post-deployment smoke check:

1. **E2E results verification:** Did all E2E tests pass? If any failed, which acceptance criteria are affected?
2. **Health check verification:** Are all services healthy?
   - Dashboard: `curl http://10.0.17.203:8082/api/health`
   - Engine: `curl http://10.0.17.204:8888/health`
3. **Acceptance criteria spot-check:** For each acceptance criterion, verify there is evidence of correct behavior (either from E2E test results or manual API call results).
4. **Regression check:** Did any existing functionality break? (Covered by E2E suite, but reflect may identify untested regressions based on the change scope.)

Applies the ambiguity circuit breaker.

**Output:** Deployment verification report (pass/fail with details)
**Failure mode:** (1) E2E tests fail post-deploy. Recovery: diagnose whether the failure is a test environment issue or a real bug. If real bug: create a hotfix branch, fix, push, re-deploy. If test environment: fix the test and document the flaky test pattern. (2) Health checks fail. Recovery: inspect container logs, restart services, check for configuration issues. (3) Acceptance criteria not verifiable (no E2E test covers it). Recovery: flag as a test gap for future improvement, verify manually if possible.
**Principle alignment:** P7 (Triple-Gate Testing) -- Gate 3 verification; P8 (Shift-Left Critique) -- post-deploy reflect catches deployment-specific issues.
**Critique level:** Lightweight reflect (`/reflexion:reflect`). RATIONALE: The code has been designed (Step 4 critique), planned (Step 6 reflect), implemented (Step 9 reflect), and reviewed (Step 11 code review). At this point, we are checking that the deployment itself is correct -- the code is already validated. A lightweight reflect is appropriate because: (a) the cost of change is higher now (merged to main, deployed), but (b) deployment issues are typically environmental, not architectural, and a single-pass check suffices.
**User selection:** only on circuit breaker trigger -- otherwise automated

---

### Step 16: Workflow Completion Summary

**Type:** automated
**Actor:** sub-agent
**Command:** N/A
**Input:** All outputs from previous steps: PR URL, deployment status, quality gate summaries, memorized insights
**Action:** Present a completion summary to the user including:

- GitHub PR URL with clickable link
- GitHub issue URL (with `Closes #<number>` status)
- Summary of quality gates: findings at each gate, how many applied, how many triggered circuit breaker
- Deployment status: pass/fail for each VM (chorestory-dev, darwin)
- Test results: total tests run, passed, failed, new tests added
- Memorized insights (if any)
- Any open follow-up items (e.g., "darwin deployment needs manual verification", "E2E test gap identified for X")
- Explicit termination notice: "WF2 complete for issue #NNN. Feature deployed to dev environment."

**Output:** Completion summary message
**Failure mode:** None -- this is an informational step. If previous steps had partial failures, this step reports the partial completion status with clear next steps.
**Principle alignment:** P11 (User-in-the-Loop) -- clear termination gives the user control over next actions.
**Critique level:** N/A (not a quality gate)
**User selection:** no -- informational output only. WF2 terminates.

---

## Quality Gate Summary

| Gate                      | Step    | Command                                                                          | Rationale                                                                                                                                                                                                                                                                            | User Selection       |
| ------------------------- | ------- | -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------- |
| Design Gate               | Step 4  | `/reflexion:critique` (standard/complex) OR `/reflexion:reflect` (simple_bugfix) | Pre-execution gate. Architecture decisions are expensive to reverse (10-50x cost multiplier). Full 3-judge debate catches design flaws, security gaps, and scope issues before any code is written. Simple bug fixes use lightweight reflect since reversal cost is low (1-3 files). | Circuit breaker only |
| Plan Drift Gate           | Step 6  | `/reflexion:reflect`                                                             | Translation validation. Checks that the plan faithfully translates the critiqued design. No re-evaluation of architecture -- just drift detection. Cost of change is moderate (planning effort, no code yet).                                                                        | Circuit breaker only |
| Implementation Drift Gate | Step 9  | `/reflexion:reflect` + `superpowers:verification-before-completion`              | Post-execution gate. Checks that implementation matches plan and design, verifies test coverage, confirms acceptance criteria are met. Evidence enforcement ensures claims are backed by test results.                                                                               | Circuit breaker only |
| Knowledge Capture         | Step 10 | `/reflexion:memorize` (conditional)                                              | After implementation, reusable insights from all gates are curated into CLAUDE.md. Conditional -- only fires if novel, generalizable insights exist. Minimal cost. Not a scrutiny gate.                                                                                              | No                   |
| Post-Deploy Verification  | Step 15 | `/reflexion:reflect`                                                             | Smoke check. Verifies deployment is correct and all acceptance criteria are met in the live environment. Code is already validated; this checks environmental correctness.                                                                                                           | Circuit breaker only |

**Note on gate level selection:** The critique strategy matrix from Phase 2 principles specifies:

- Full critique (`/reflexion:critique`) at ideation and pre-execution gates -- applied at Step 4 (Design Gate)
- Lightweight reflect (`/reflexion:reflect`) at translation and post-execution gates -- applied at Steps 6, 9, and 15
- Memorize (`/reflexion:memorize`) after any gate with reusable insights -- applied at Step 10

This matches the WF2 placement specified in the matrix: `design --> /reflexion:critique --> plan --> /reflexion:reflect --> TDD --> /reflexion:reflect --> PR --> deploy`.

---

## Principle Violation Analysis

The workflow was validated against all 14 Phase 2 principles. Findings:

### Principles Fully Enforced

- **P1 (Branch Isolation):** Feature branch created at Step 7. No worktrees (by design decision for solo developer).
- **P2 (Code Formatting):** Automated via PostToolUse hooks during Step 8.
- **P3 (Frequent Local Commits):** 5-minute timer hook during Step 8.
- **P4 (Regular Remote Sync):** 30-minute push timer during Step 8.
- **P5 (TDD Enforcement):** Strict RED-GREEN-REFACTOR in Step 8 via superpowers:test-driven-development. Gate 1 blocks PR in Step 12.
- **P7 (Triple-Gate Testing):** Gate 1 (Step 12 pre-PR), Gate 2 (Step 13 CI), Gate 3 (Step 14/15 post-deploy E2E).
- **P8 (Shift-Left Critique):** Full critique at Step 4 (design), reflect at Steps 6, 9, 15.
- **P9 (Continuous Memorization):** Memorize at Step 10 after implementation quality gates.
- **P11 (User-in-the-Loop):** Ambiguity circuit breaker active at all quality gates. User confirms issue in Step 1. **Note:** P11's principle statement says findings are "never auto-applied," but WF2 (like WF1) auto-applies ALL unambiguous findings. This is a deliberate design choice: the circuit breaker catches ambiguous findings for user resolution, while unambiguous findings are applied automatically to reduce cycle time. This deviation was accepted during WF1 design (Q7) and inherited by WF2.
- **P12 (Conventional Commit Discipline):** All commits, branch names, and PR titles follow conventional format.
- **P13 (Pre-PR Code Review):** Multi-agent review at Step 11.
- **P14 (Documentation-Gated PRs):** Documentation check in Step 9 reflect and Step 12 PR body.

### Principles Partially Enforced

- **P6 (Main-to-Dev Sync):** Deploy script covers chorestory-dev automatically but darwin requires manual SSH (Step 14). This is a known gap documented in CLAUDE.md ("deploy script only covers one of two VMs").
- **P10 (Diagram-Driven Design):** Conditional diagram creation in Step 3 (only when architecture changes are significant). Not every feature needs a diagram -- the condition is assessed in Step 2.

### Gaps Identified

1. **Darwin auto-deploy not in deploy script.** Impact: Step 14 requires manual darwin deployment. Mitigation: documented as a manual step with explicit SSH command. Future enhancement: extend deploy-dev.sh.
2. **E2E tests not in CI (Gate 2).** Impact: Gate 2 only runs unit/integration tests. E2E runs at Gate 3 (post-deploy). Mitigation: this is the existing CI design; adding E2E to CI requires self-hosted runner or SSH action.

### Rollback Plan

If a deployed feature causes issues, follow this rollback sequence:

1. **Pre-merge (Steps 1-12):** No rollback needed -- delete the feature branch (`git branch -D <branch>`) and close the PR. No artifacts on main are affected.
2. **Post-merge, pre-deploy (Step 14 merge completed, deploy not started):** Revert the merge commit on main: `git revert <merge-sha> && git push origin main`. Then deploy the reverted main.
3. **Post-deploy (Steps 14-16):**
   - Record the pre-merge SHA from the deploy log or `git log --oneline -5 main`
   - Revert on main: `git revert <merge-sha> && git push origin main`
   - Re-deploy to chorestory-dev: `ssh root@10.0.17.203 "cd /opt/millions && git pull origin main && docker compose -f docker-compose.infra.dev.yml up -d --build --force-recreate"`
   - Re-deploy to darwin: `ssh root@10.0.17.204 "cd /opt/millions && git pull origin main && docker compose -f docker-compose.engine.dev.yml up -d --build --force-recreate"`
   - If database migrations were applied: run the corresponding rollback migration (migration files should include a `-- ROLLBACK:` comment with the reverse SQL)
   - Verify health checks pass after rollback
4. **Multi-PR rollback:** Revert sub-PRs in reverse order (last merged first). Each revert is its own PR with CI verification.

---

## Data Flow Diagram (Text Representation)

```
[User: /implement-feature #NNN]
     |
     v
Step 1: Receive Issue Reference ---- { issue_number, title, body, labels, complexity }
     |                                  [USER CONFIRMS]
     v
Step 2: Analyze Codebase ---- { affected_files, symbols, test_gaps, complexity_class, needs_diagram }
     |                          [Serena MCP + Context7 MCP]
     v
Step 3: Design Solution ---- Design Document + Optional Excalidraw Diagram
     |                        [/superpowers:brainstorming]
     v
Step 4: /reflexion:critique ---- Amended Design (findings applied)
     |    (or /reflexion:reflect      |
     |     for simple_bugfix)     [VOLUME CHECK: >5 Crit OR >5 High OR >10 Med OR >10 Low?]
     |                                |
     |                       YES: loop back to Step 3 (max 2 loops)
     |                       NO: proceed
     |                                |
     |                       [CIRCUIT BREAKER: stop if ambiguous]
     v
Step 5: Create Plan ---- Implementation Plan (ordered TDD tasks)
     |                   [/superpowers:writing-plans]
     v
Step 6: /reflexion:reflect ---- Validated Plan (drift check passed)
     |                            [CIRCUIT BREAKER: stop if ambiguous]
     v
Step 7: Create Branch ---- feature/<issue>-<desc> branch on origin
     |                     [git checkout -b + git push]
     v
Step 8: TDD Implementation ---- Implemented code with passing tests
     |    [/superpowers:test-driven-development]     |
     |    [/superpowers:executing-plans]              |---- DESIGN FLAW? loop back to Step 3 (max 1)
     |    [/superpowers:systematic-debugging]         |
     |    P2: format, P3: commit, P4: push           |
     v                                                |
Step 9: /reflexion:reflect ---- Verified implementation
     |    + verification-before-completion    |
     |                              [CIRCUIT BREAKER: stop if ambiguous]
     |                                       |
     v                                       v
Step 10: /reflexion:memorize          Step 11: Code Review
  (conditional, background)             [/code-review + /superpowers:requesting-code-review]
     |                                       |---- DESIGN FLAW? loop back to Step 3 (max 1)
     v                                       v
Updated CLAUDE.md                    Reviewed code (Critical/High fixed)
  (joins at Step 12)                        |
                                            v
                                     Step 12: Create PR
                                       [/commit-push-pr + gh pr create]
                                       [GATE 1: tests must pass]
                                            |
                                            v
                                     Step 13: CI Verification
                                       [GitHub Actions -- GATE 2]
                                            |
                                            v
                                     Step 14: Merge PR + Deploy
                                       [gh pr merge + deploy-dev.sh]
                                       [GATE 3: E2E post-deploy]
                                            |
                                            v
                                     Step 15: /reflexion:reflect
                                       [Post-deploy verification]
                                            |
                                            v
                                     Step 16: Completion Summary
                                            |
                                            v
                                     [WF2 TERMINATES]
```

---

## Excalidraw Diagram

The Excalidraw diagram is saved to `.claude/framework-build/diagrams/workflow-feature-implementation.excalidraw`.

**Color coding applied:**

- **Blue (#4A90D9):** Automated steps -- Steps 2, 3, 5, 7, 8, 12, 13, 14, 16 (Step 3 is blue because its Type is "automated"; the conditional diagram creation is annotated but does not change its primary color)
- **Green (#7BC67E):** User decision points -- Step 1
- **Yellow (#F5A623):** Quality gates -- Steps 4, 6, 9, 15 (Step 10 is conditional automated, not a scrutiny gate -- see Step 10 Type field)
- **Gray (#808080):** Terminal node -- WF2 TERMINATES (neutral completion, not a failure)
- **Red (#D0021B):** Failure/rollback paths -- annotated on Steps 4, 8, 11, 14

**Data flow annotations:**

- Arrows between nodes show what artifact moves between steps
- Each node annotated with the principle(s) it enforces
- Quality gate nodes show the reflexion command used
- Step 4 includes volume threshold loop-back and circuit breaker annotations
- Step 8 includes TDD enforcement annotations (RED-GREEN-REFACTOR)
- Steps 10 and 11 show parallel execution
- Steps 12-14 show the triple-gate testing sequence (Gate 1, Gate 2, Gate 3)
- Terminal node explicitly shows WF2 termination

---

## Design Decisions Made

### D1: Step count -- 16 steps (vs WF1's 9)

WF2 is inherently more complex than WF1 because it covers the full implementation lifecycle: analysis, design, planning, implementation (with TDD), review, PR, CI, deploy, and verification. Each of these phases is a distinct step with different actors, tools, and failure modes. Collapsing steps would obscure the data flow and make failure recovery ambiguous.

### D2: Fast path for simple bug fixes -- YES

Simple bug fixes (1-3 files, no architecture change) get a lightweight `/reflexion:reflect` at Step 4 instead of the full 3-judge `/reflexion:critique`. This saves approximately 1-3 minutes of sub-agent time per simple fix. The complexity classification at Step 2 determines eligibility. The fast path only affects Step 4 -- all other steps remain identical.

### D3: Git worktrees -- NOT USED

For a solo developer, the overhead of worktree setup (node_modules, Docker containers per worktree) outweighs the benefit. Standard branching suffices for sequential feature work. If parallel development becomes common, worktrees can be added as a Step 7 enhancement.

### D4: TDD cycle -- strict RED-GREEN-REFACTOR per task

Each plan task is a 2-5 minute TDD unit. The cycle is enforced by `/superpowers:test-driven-development` which has an iron-law preventing code before tests. Debugging uses `/superpowers:systematic-debugging` with a 3-fix-failure escalation. This is more rigorous than "write tests sometime during implementation."

### D5: Code review -- automated-only (no mandatory user review)

Multi-agent review (Step 11) with 4+ agents provides comprehensive automated review. The ambiguity circuit breaker escalates to the user when findings are unclear. A mandatory user review step was considered but rejected because: (a) the user already approved the design at Step 1/4, (b) automated review catches mechanical issues more reliably, (c) adding a blocking user review step significantly increases cycle time. The user can always review the PR on GitHub before merge if they wish.

### D6: Deploy strategy -- always deploy after merge

Deployment is not optional -- the dev environment should always reflect main. This is consistent with P6 (Main-to-Dev Sync). If a feature should not be deployed yet, it should be behind a feature flag (per CLAUDE.md's "feature flags" code quality standard), not kept off main.

### D7: Multi-PR handling -- single PR default, plan-level decomposition for large features

Single PR per issue is the default. If the plan (Step 5) identifies more than 500 lines of change or logically distinct phases, it decomposes into sub-PRs. Each sub-PR follows Steps 8-14 independently. The plan documents the decomposition and ordering.

---

## Open Questions (All Resolved in v1.0)

### Q1: Should WF2 auto-trigger from WF1? -- RESOLVED

**Decision:** NO. WF2 is ALWAYS invoked separately via `/implement-feature`. This was a carry-over constraint from WF1 v2.3 Q5. Clean separation of concerns.

### Q2: Fast path for simple bug fixes -- RESOLVED

**Decision:** YES. Simple bug fixes (classified at Step 2) get `/reflexion:reflect` instead of `/reflexion:critique` at Step 4. All other steps identical.

### Q3: Git worktrees -- RESOLVED

**Decision:** NOT USED. Standard branching for solo developer workflow. Worktrees can be added later.

### Q4: TDD cycle design -- RESOLVED

**Decision:** Strict RED-GREEN-REFACTOR per task with `/superpowers:test-driven-development` iron-law enforcement. Each task is 2-5 minutes. Debugging via `/superpowers:systematic-debugging`.

### Q5: Code review model -- RESOLVED

**Decision:** Automated multi-agent review only. No mandatory user review step. Ambiguity circuit breaker escalates to user when needed.

### Q6: Deploy strategy -- RESOLVED

**Decision:** Always deploy after merge. Features not ready for deployment use feature flags.

### Q7: Multi-PR handling -- RESOLVED

**Decision:** Single PR default. Plan-level decomposition for >500 line changes into ordered sub-PRs.

---

## Plugin Interface Contracts

**Deferred to implementation-spec-agent.** Same rationale as WF1: this design focuses on _what_ each step does and _what data flows between them_. The implementation spec will define _how_ the data is structured and passed (function signatures, data structures, error handling protocols).

---

## Revision History

| Version | Date       | Changes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| v1.0    | 2026-03-01 | Initial workflow design with 16 steps, 5 quality gates, fast path for simple bug fixes, multi-PR strategy, design decisions documented                                                                                                                                                                                                                                                                                                                                                     |
| v1.1    | 2026-03-02 | Critique fixes (1C, 4H, 8M, 7L): workflow resumption section, global loop-back budget, structured rollback plan, WF1-validated fast path, brainstorm→brainstorming rename, remove git rebase -i, CI timeout requires user approval, P6 header corrected, P11 deviation documented, Step 5/7 branch overlap resolved, parallel execution mechanism specified, multi-PR execution subsection, Step 10 reclassified, Step 3 color corrected, diagram arrows added, fast path criteria aligned |
