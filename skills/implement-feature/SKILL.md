---
name: rawgentic:implement-feature
description: Implement a feature or bug fix from a GitHub issue through the WF2 16-step workflow with TDD, multi-agent code review, quality gates, and automated deployment. Invoke with /implement-feature followed by a GitHub issue number or URL. DO NOT use this skill if the user is working within a BMAD workflow, has BMAD story files, or is using bmad-dev-story. Only trigger when the user explicitly invokes /implement-feature or /rawgentic:implement-feature, or is working in a rawgentic-only project without BMAD.
argument-hint: GitHub issue number (e.g., 155) or URL
---

# WF2: Feature Implementation Workflow (v2.0)

<role>
You are the WF2 orchestrator implementing a 16-step feature implementation workflow. You take a GitHub issue (created by WF1 or manually) and guide it through codebase analysis, design, critique, implementation, code review, PR creation, and optional CI/deployment verification. You adapt your behavior based on project capabilities detected at startup — not all projects have tests, CI, or automated deployment, and the workflow gracefully handles each case.
</role>

<constants>
MAX_DESIGN_LOOPBACK_ITERATIONS = 2
MAX_TDD_DESIGN_LOOPBACK = 1
MAX_REVIEW_DESIGN_LOOPBACK = 1
MAX_REVIEW_DESIGN_LOOPBACK_STEP_8A = 1   # P15: Step 8a per-task review loopback (separate from tdd)
GLOBAL_LOOPBACK_BUDGET = 3
VOLUME_THRESHOLDS:
  Critical: 5
  High: 5
  Medium: 10
  Low: 10
BRANCH_PREFIX_FEATURE = "feature"
BRANCH_PREFIX_FIX = "fix"
CI_POLL_INTERVAL_SECONDS = 30
CI_MAX_WAIT_MINUTES = 10
REVIEW_CONFIDENCE_THRESHOLD = 0.80                    # Flat fallback (legacy, retained)
# P15 — Risk-stratified Review (tiered code review):
PER_TASK_REVIEW_AGENT_COUNT = 2                        # Step 8a uses 2 inline reviewer roles
# Severity-banded confidence applied to Step 8a AND Step 11 reviewer findings.
# Critical and High get a lower bar because hiding them is more dangerous than
# flagging false-positives. Banded values are documented in hooks/plan_lib.py.
SEVERITY_BANDED_CONFIDENCE:
  Critical: 0.50
  High:     0.65
  Medium:   0.80
  Low:      0.90
WF2_HIGH_RISK_RATIO_WARN_PCT = ${WF2_HIGH_RISK_RATIO_WARN_PCT:-30}   # warn band; clamped [5,95]
WF2_HIGH_RISK_RATIO_HALT_PCT = ${WF2_HIGH_RISK_RATIO_HALT_PCT:-50}   # halt band; clamped [10,95]; halt>=warn+10
# Source of truth for these constants is hooks/plan_lib.py (env-var freeze at import).
</constants>

<state-files>
P15 (tiered review) introduces session-scoped state files under
`claude_docs/.wf2-state/<issue-number>/`:

- `review_log.jsonl` — append-only Step 8a review entries (`task_id`, `sha`,
  `reviewers`, `verdicts`, `findings_count`, `dropped_count`, `ts`). Read by
  Step 9 (coverage assertion) and Step 11 (already-reviewed SHA list).
- `deferrals.json` — finding-level deferrals re-presented at Step 11. Each
  entry: `finding_id`, `severity`, `status`, `defer_count`,
  `originator_reviewer_slot`, `concurrences`, `user_ack`.
- `loopback_counters.json` — per-source loop-back counters (`design`, `tdd`,
  `review_design`, `review`) plus `total`. Persisted across sessions via
  `plan_lib.consume_loopback`.

In addition, a small COMMITTED status pointer lives at
`.rawgentic/review-state.json` (single object: `{branch, last_review_log_status,
ts}`). Step 12 and Step 14 read this file and refuse to ship if the last
status is not `"applied"`. The committed pointer survives across sessions and
worktrees; the session-scoped files do not.

The session-scoped directory is cleaned up on Step 14 merge success.
</state-files>

<mandatory-steps>
The following steps are MANDATORY and must NEVER be skipped, abbreviated, or combined — regardless of context window pressure, session length, perceived simplicity, or any other justification:

| Step | Name | Why mandatory |
|------|------|---------------|
| 1 | Receive Issue | Foundation — wrong issue = wrong implementation |
| 2 | Analyze Codebase | Complexity classification drives all downstream decisions |
| 3 | Design Solution | Architecture before code — always |
| 4 | Quality Gate (Design) | Catches design flaws BEFORE implementation. Full critique for complex_feature, reflect for fast path. |
| 5 | Implementation Plan | Task decomposition enables TDD and progress tracking |
| 7 | Create Branch | Git isolation is non-negotiable |
| 8 | Implementation | The actual work |
| 9 | Quality Gate (Drift) | Verifies implementation matches design and all ACs covered |
| 11 | Code Review | **NON-NEGOTIABLE.** Full 3-agent review for complex_feature. Minimum 1-agent for simple/standard. This step found 2 Critical security issues (HTML injection + path traversal) when the orchestrator attempted to skip it. |
| 12 | Create PR | Deliverable — no PR means no review trail |

Conditional steps (skip ONLY when their condition is not met):
- Step 6 (Plan Drift): lightweight, fast — run it unless time-critical
- **Step 8a (Per-task Review, P15):** mandatory when ANY task has `riskLevel: high`. Dispatched as a sub-step of Step 8 after each high-risk task's commit. Marker: `### WF2 Step 8a [task <id>, sha <abc>]: DONE (<N findings>)` in session notes.
- Step 10 (Memorize): background, never blocks
- Step 13 (CI): skip only if has_ci == false
- Step 14 (Merge/Deploy): skip only if user does not request merge
- Step 15 (Post-Deploy): skip only if no deployment performed

**ENFORCEMENT:** You MUST NOT rationalize skipping a mandatory step. Common invalid justifications:
- "This session is very long" — NOT a valid reason to skip code review
- "The architecture was already critiqued in WF1" — WF1 critiqued the SPEC, not the CODE
- "The changes are mechanical" — mechanical changes can still have injection vulnerabilities
- "I'll do a quick check instead" — a "quick check" is not a substitute for the full step
- "Context window is running low" — checkpoint in session notes and resume, do not skip

If you catch yourself about to skip a mandatory step, STOP and acknowledge: "I was about to skip Step N which is mandatory. Proceeding with the full step."
</mandatory-steps>

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
   - If the project entry has a `disabledSkills` array and this skill's bare name appears in it:
     **[Headless cleanup]:** Before stopping, check if `claude_docs/headless_suspend.json` exists. If it does, delete it, remove `rawgentic:ai-waiting` label from the issue (read issue number from suspend file), and add `rawgentic:ai-error` with a comment: "This skill was disabled after a headless session was suspended. The pending question can no longer be processed." Then **STOP.**
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
If this workflow discovers new project capabilities during execution (e.g., a new test framework, a previously unknown service), update `.rawgentic.json` before completing:
- Append to arrays (e.g., add new test framework to testing.frameworks[])
- Set fields that are currently null or missing
- Do NOT overwrite existing non-null values without asking the user
- Always read full file, modify in memory, write full file back
</learning-config>

<headless-interaction>
When the workflow hits a user interaction point, check whether headless mode is active
(additionalContext contains "HEADLESS MODE active"). If NOT in headless mode, behave
as normal (STOP and wait for terminal input). If in headless mode, follow this protocol:

**Interaction types and headless behavior:**

AUTO-RESOLVE interactions (no user input needed in headless mode):
- Step 1: Accept auto-generated ACs for WF1-created issues
- Step 1: Accept capabilities for WF1-created issues
- Step 5: Remove excess tasks on scope creep (document in session notes)
- Step 7: Always stash dirty directory
- Step 7: Always resume existing branch
- Step 8: Rewrite failing RED test (up to 2 attempts)
- Step 13: Wait up to 2x CI_MAX_WAIT before erroring

QUESTION interactions (post comment, suspend, exit):
- Step 1: Confirm capabilities/ACs for manually-created issues
- Step 2: Component discrepancy
- Step 3: Design approach trade-offs
- Step 3: Scope larger than estimated
- Step 4: Ambiguity circuit breaker findings
- Step 14: Manual deploy confirmation

ERROR interactions (post error comment, exit WITHOUT ai-waiting label):
- Step 4: Design loop-back budget exhausted
- Step 4: Global loop-back budget exhausted
- Step 8: Design flaw + budget exhausted
- Step 11: Design flaw in review + budget exhausted

**QUESTION protocol (post → label → suspend → exit):**

1. Generate a structured comment using `hooks/headless_interaction.py`:
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, 'hooks')
   from headless_interaction import format_comment, format_suspend_state, write_suspend_state
   comment = format_comment(
       step=STEP_NUMBER,
       title='QUESTION_TITLE',
       context='WHAT_THE_WORKFLOW_IS_DOING',
       question='THE_DECISION_NEEDED',
       options=['(a) Option 1', '(b) Option 2'],
       metadata={'question_id': 'GENERATED_UUID', 'step': STEP_NUMBER, 'type': 'INTERACTION_TYPE'}
   )
   print(comment)
   "
   ```

2. Post the comment to the GitHub issue:
   ```bash
   gh issue comment ISSUE_NUMBER --repo ${capabilities.repo} --body "COMMENT_BODY"
   ```

3. Add the waiting label:
   ```bash
   gh issue edit ISSUE_NUMBER --repo ${capabilities.repo} --add-label "rawgentic:ai-waiting"
   ```
   Create the label first if it doesn't exist:
   ```bash
   gh label create "rawgentic:ai-waiting" --repo ${capabilities.repo} \
     --description "Rawgentic headless: waiting for user reply" --color "FBCA04" 2>/dev/null || true
   ```

4. Write the suspend state file:
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, 'hooks')
   from headless_interaction import format_suspend_state, write_suspend_state
   state = format_suspend_state(
       session_id='N/A',
       issue=ISSUE_NUMBER,
       step=STEP_NUMBER,
       question_id='GENERATED_UUID',
       comment_url='COMMENT_URL',
       clarification_round=0
   )
   write_suspend_state('claude_docs/headless_suspend.json', state)
   "
   ```

5. Write a SUSPEND WAL entry:
   ```bash
   bash hooks/wal-suspend
   ```

6. Write a rich checkpoint to session notes (see <headless-checkpoint>).

7. **EXIT the workflow cleanly.** Do NOT continue to the next step. The orchestrator
   will re-invoke the skill in a fresh session after the user replies.

**ERROR protocol (post error → exit WITHOUT label):**

1. Post an error comment to the issue describing what went wrong and what the
   user needs to do to unblock.
2. Do NOT add `rawgentic:ai-waiting` label (errors don't expect a reply).
3. Add `rawgentic:ai-error` label instead (create if missing, color "D93F0B").
4. Write session notes with the error state.
5. EXIT the workflow.

**Label management:**
- `rawgentic:ai-waiting` — set by skill on QUESTION suspend, removed by skill on resume
- `rawgentic:ai-error` — set by skill on terminal error
- `rawgentic:ai-in-progress` — set/removed by orchestrator (NOT by the skill)
</headless-interaction>

<headless-checkpoint>
Before exiting in headless mode (either QUESTION suspend or ERROR), write a rich
checkpoint to session notes. This checkpoint must contain enough context for a
FRESH session (no --resume, no conversation history) to reconstruct the workflow state.

**Always include in the checkpoint:**
- Current step number and sub-step
- Feature branch name and last commit SHA
- Loop-back budget state
- The question that was posted (for QUESTION suspends)
- The question_id (for reply correlation)

**Include if available (from prior session notes or current conversation):**
- Design approach selected and rationale (if past Step 3)
- Key critique findings and how they were resolved (if past Step 4)
- Implementation plan summary (if past Step 5)
- Implementation progress: which tasks are done, current task index (if in Step 8)

**Format in session notes:**
```
### WF2 Headless Checkpoint — Step N (SUSPENDED)
- Branch: feature/43-foo
- Last commit: abc123
- Loop-back budget: 1/3 used
- Pending question: [question_id] — [brief description]
- Design: [approach name] — [1-line rationale]
- Plan: [N tasks, M complete]
- Key decisions: [bullet list of non-obvious choices made]
```

This checkpoint is what enables the fresh-session resumption pattern. Without it,
a fresh session can only detect "Step 8 has code changes" but not "Step 8 task 5
of 7, using approach B because of critique finding #3."
</headless-checkpoint>

<headless-resume>
On every fresh session start, BEFORE the normal resumption protocol, check for a
pending headless interaction:

0. **Load project configuration** per `<config-loading>` to populate `capabilities.repo`.
   If config-loading fails, post error comment and exit.
1. Check if `claude_docs/headless_suspend.json` exists.
2. If missing → no pending question. Proceed with normal resumption protocol.
3. If present → read the suspend state:
   - Extract `question_id`, `comment_url`, `issue`, `step`, `clarification_round`
4. Fetch the user's reply from GitHub issue comments:
   ```bash
   gh api repos/${capabilities.repo}/issues/ISSUE/comments \
     --jq '[.[] | select(.created_at > "SUSPEND_TIMESTAMP")] | map(select(.user.login != "github-actions[bot]")) | last.body // empty'
   ```
   Use the `suspended_at` timestamp from the suspend file to find comments posted AFTER the bot's question.
5. If no reply found → the user hasn't responded yet. Post a reminder comment if
   `clarification_round < 2`, otherwise post an error. Exit.
6. If reply found → parse the reply for the user's choice:
   - Look for option letters/numbers ("a", "1", "option a", etc.)
   - Look for natural language ("proceed", "approved", "go with the first one")
   - If unparseable → increment `clarification_round`, post a clarification comment,
     re-add `rawgentic:ai-waiting`, update suspend file (update ONLY `clarification_round` —
     do NOT update `suspended_at`, as the next resume must still see comments posted after
     the original question), exit.
7. If reply parsed successfully:
   - Delete `claude_docs/headless_suspend.json`
   - Remove `rawgentic:ai-waiting` label (and `rawgentic:ai-error` if stale from prior run):
     ```bash
     gh issue edit ISSUE --repo ${capabilities.repo} --remove-label "rawgentic:ai-waiting" --remove-label "rawgentic:ai-error" 2>/dev/null || true
     ```
   - Inject the user's choice into the workflow context
   - Continue with the normal resumption protocol (the session notes checkpoint
     tells us exactly where to resume and what the pending decision was)
</headless-resume>

<termination-rule>
WF2 ALWAYS terminates after the completion summary. Do NOT suggest "shall I create another issue?" or restart WF2 for the same issue. WF2 terminates ONLY after the completion-gate passes. All steps must have markers in session notes.
</termination-rule>

<loop-back-budget>
Track all design loop-backs across the workflow:
- Step 4 -> Step 3: max 2 iterations (MAX_DESIGN_LOOPBACK_ITERATIONS)
- Step 8 -> Step 3: max 1 iteration (MAX_TDD_DESIGN_LOOPBACK)
- Step 11 -> Step 3: max 1 iteration (MAX_REVIEW_DESIGN_LOOPBACK)

Global cap: GLOBAL_LOOPBACK_BUDGET = 3
If global cap reached, STOP and escalate to user with full summary of all loop-back triggers. **[Headless: ERROR — post error comment with full loop-back summary, add rawgentic:ai-error label, exit.]**

Track loop-back state:
design_loopback_count = 0
tdd_loopback_used = false
review_loopback_used = false
global_loopback_total = 0
</loop-back-budget>

<resumption-protocol>
WF2 may span multiple Claude Code sessions. On resumption, detect the current step:

-1. **Headless resume check (FIRST):** If in headless mode, execute `<headless-resume>` before any other check. If a pending question was answered, inject the reply and resume at the step indicated in the session notes checkpoint. If no reply yet, exit cleanly.
0. All step markers present but completion-gate not printed? -> Run completion-gate, then terminate.
1. PR exists and is merged? -> Resume at Step 15 (post-deploy verification)
2. PR exists and CI passed (or no CI)? -> Resume at Step 14 (merge + deploy)
3. PR exists? -> Resume at Step 13 (CI verification)
4. Feature branch has code changes with passing tests (or verified)? -> Resume at Step 11 (code review)
5. Feature branch has code changes? -> Resume at Step 9 (implementation drift check)
6. Feature branch exists but is empty? -> Resume at Step 8 (implementation)
7. Design document exists in session notes? -> Resume at Step 5 (create plan)
8. Issue is validated in session notes? -> Resume at Step 2 (analyze codebase)
9. None of the above? -> Start from Step 1

Before context compacts, document in session notes:
- Current step number and sub-step
- Quality gate findings not yet applied
- Feature branch name and last commit SHA
- Loop-back budget state (design_loopback_count, tdd/review used, global total)
- Any unresolved circuit breaker state
- Detected capabilities summary
- If in Step 8: current task index, implementation phase, and verification status
</resumption-protocol>

<fast-path-detection>
Two fast path variants reduce Step 4 from full critique to lightweight reflect:

1. **Simple change fast path:** Step 2 classifies as simple_change (1-3 files, no architecture change, no migration, no new deps). Step 4 uses /reflexion:reflect instead of /reflexion:critique.

2. **WF1-validated fast path:** Step 1 detects the issue has the "wf1-created" label AND Step 2 classifies as standard_feature (not complex_feature). Step 4 uses /reflexion:reflect. Rationale: WF1 already ran a full 3-judge critique on the issue specification.

Neither fast path applies to complex_feature -- those always get full /reflexion:critique.
</fast-path-detection>

<ambiguity-circuit-breaker>
Active at ALL quality gates (Steps 4, 6, 9, 11, 15). Triggers when:
- Any finding has ambiguity_flag == "ambiguous"
- Two or more findings conflict (contradictory recommendations)
- A finding requires judgment not captured in the GitHub issue

When triggered: STOP the workflow at the current step. Present ALL problematic findings to the user. Wait for resolution. Do NOT auto-apply unambiguous findings separately -- the full set is applied together after resolution. **[Headless: QUESTION — post comment with all ambiguous/conflicting findings and resolution options, suspend.]**
</ambiguity-circuit-breaker>

<step-tracking>
At the end of each step, log a marker in `claude_docs/session_notes.md`:
`### WF2 Step X: <Name> — DONE (<key detail>)`
This enables workflow resumption if context is lost.
</step-tracking>

---

## Step 1: Receive Issue Reference and Detect Capabilities

### Instructions

1. **Load project configuration** per `<config-loading>`. The `config` and `capabilities` objects are now available for all subsequent steps. Log all detected capabilities in session notes.

2. Parse the user's input to extract the GitHub issue number. Accept:
   - Bare number: `1`
   - Hash-prefixed: `#1`
   - URL: `https://github.com/<owner>/<repo>/issues/1`

3. Fetch the issue via gh CLI:
   ```bash
   gh issue view <number> --repo ${capabilities.repo} --json number,title,body,labels,state
   ```

4. Validate:
   - Issue exists and is open
   - If closed: ask user if they want to reopen or use a different issue. **[Headless: ERROR — post error comment explaining issue is closed, add rawgentic:ai-error label, exit.]**

5. Check for WF1 origin:
   - If labels include "wf1-created": set `is_wf1_created = true`
   - Extract acceptance criteria, affected components, complexity from the issue body
   - If any are missing (manually created issue): generate them from the description and ask user to confirm. **[Headless: AUTO-RESOLVE for WF1-created issues (accept generated ACs). QUESTION for manual issues — post comment with generated ACs for confirmation, suspend.]**

6. Display to user:
   ```
   ISSUE #NNN: [title]
   State: Open | Labels: [list] | WF1 Origin: [yes/no] | Complexity: [S/M/L/XL]

   Detected Capabilities:
   - Tests: [yes (command) / no]
   - CI: [yes (N workflows) / no]
   - Deploy: [method / no]
   - Infrastructure: [hosts / none]
   - Project type: [type]

   Acceptance Criteria:
   1. [criterion 1]
   ...

   Confirm this issue and capabilities are correct, or provide corrections.
   ```

7. Update session notes. Wait for user confirmation. **[Headless: AUTO-RESOLVE for WF1-created issues (accept and proceed). QUESTION for manual issues — post summary comment for confirmation, suspend.]**

### Failure Modes
- Issue does not exist -> ask for correct number
- Issue is closed -> ask if user wants to reopen or use different issue
- Issue lacks acceptance criteria -> generate from description, ask user to confirm

---

## Step 2: Analyze Codebase and Classify Complexity

### Instructions

1. **Component mapping:** Using Serena MCP (`find_symbol`, `get_symbols_overview`) or Grep/Glob as fallback, identify all files and code that will need to change. Map the issue's "affected components" to actual project artifacts.

2. **Dependency analysis:** Trace relationships from affected components to understand the blast radius. The scope depends on project type:
   - `application`: trace call chains from entry points (routes, handlers, main functions)
   - `infrastructure`: identify dependent containers, networks, volumes, config files
   - `scripts`: identify shared utilities, imports, configuration dependencies
   - `library`: trace public API surface and consumers
   - `docs`: identify cross-references, linked pages, publishing scripts
   - `research`: primarily analysis notebooks, data pipelines, or literature review — testing means validation of results and reproducibility

3. **Live environment probe (infrastructure projects only):** When `capabilities.project_type == "infrastructure"` and target hosts are known (from `config.infrastructure.hosts[]`), SSH to each target host to discover current state. This catches discrepancies between issue specs (which may be outdated) and reality.

   Probe for:
   - **Server capacity:** `nproc` (CPU count), `free -g` (RAM), `df -h` (disk) — compare against issue requirements
   - **Running containers:** `docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"` — discover what's actually running vs what the issue assumes
   - **Docker Compose version:** `docker compose version` — determines syntax choices (e.g., `deploy.resources` vs deprecated `mem_limit`)
   - **Port usage:** `ss -tlnp` — verify target ports are actually free
   - **Existing configs:** check relevant compose files and `.env` files on the host for patterns to follow
   - **Docker images:** inspect target images for capabilities (e.g., `docker run --rm <image> ls /path/` to check for migration files, installed packages)

   Log probe results in session notes. Flag any discrepancies between the issue spec and actual server state — these often reveal outdated assumptions that would cause deployment failures.

4. **Memory search (Layer 3 — proactive recall).** If a mempalace MCP server is available (`mcp__mempalace__*` tools loaded), call `mempalace_search` with the feature topic and `mempalace_kg_query` for entity-specific facts. Surface prior architectural decisions, known gotchas, and related implementations in this area. Reference findings explicitly when designing the implementation. If no mempalace MCP server is configured, skip silently.

4. **Existing test/verification inventory:** Identify any existing tests, verification scripts, or validation mechanisms that cover the affected code. Note gaps.

5. **Library and image research:** If the feature uses libraries in new ways, use Context7 MCP to fetch current documentation. For infrastructure projects, inspect Docker images that will be used — check for built-in migration files, supported database drivers, pre-installed packages (e.g., `psycopg2` in a Python image), and default configurations. This prevents designing around incorrect assumptions about image capabilities.

6. **Complexity classification:**
   - `simple_change`: 1-3 files, no architecture change, no migration, no new deps
   - `standard_feature`: 4-15 files, contained scope, may need configuration changes
   - `complex_feature`: 15+ files, cross-service changes, multiple configuration changes, new deps

   This classification is AUTHORITATIVE — it overrides any complexity label from the GitHub issue.

7. **Fast path eligibility:**
   - If `simple_change`: `fast_path_eligible = true`
   - If `standard_feature` AND `is_wf1_created`: `fast_path_eligible = true`
   - Otherwise: `fast_path_eligible = false`

### Output
Codebase analysis with complexity classification, fast path eligibility, and (for infrastructure projects) live environment probe results. Do NOT present to user — feeds into Step 3.

### Failure Modes
- Serena MCP unavailable: fall back to Grep/Glob
- Issue references components that do not exist: flag discrepancy and ask user. **[Headless: QUESTION — post comment listing missing components with options (skip, create, abort), suspend.]**
- Complexity uncertain: default to `standard_feature`
- SSH to target host fails: log the failure but do not halt — proceed with issue-stated values and flag that live verification was not possible

---

## Step 3: Design Solution Architecture

### Instructions

1. **Design approach:** For complex features, use the Agent tool with a brainstorming prompt to generate 2-3 implementation approaches. For standard features, design inline with 1-2 approaches.

2. **Each approach includes:**
   - Name and description
   - Pros and cons
   - Estimated effort
   - Risk assessment

3. **Select approach** based on complexity classification and acceptance criteria. Recommend one with rationale.

4. **Design document** — adapt structure to project type:

   **For all project types:**
   - File changes (which files, what modifications)
   - Configuration changes (env vars, YAML, Docker compose)
   - Error handling and failure modes
   - Security implications

   **Additional for `application` projects:**
   - Data flow changes (routes, queries, message flows)
   - Database migrations (with rollback strategy)

   **Additional for `infrastructure` projects:**
   - Container/service changes (images, ports, networks, volumes)
   - Resource allocation (CPU, memory, storage)
   - Dependency ordering (what must start before what)
   - Rollback strategy (how to revert to previous state)
   - Init script design: when using database Docker images (postgres, mysql, etc.), note that `/docker-entrypoint-initdb.d/` scripts behave differently by file type — `.sql` files do NOT support shell environment variable substitution, while `.sh` scripts do. If credentials must come from env vars (e.g., `.env` file), use a `.sh` init script with heredoc, not raw `.sql`.
   - Upstream image capabilities: incorporate findings from Step 2's image inspection (e.g., if the image ships native migration files for your target database, reference those rather than assuming they don't exist)

   **Additional for `scripts`/`docs` projects:**
   - Script interface changes (arguments, outputs)
   - Documentation updates needed

5. **Multi-PR assessment:** If the design suggests more than 500 lines of change or has clearly separable phases, flag for multi-PR decomposition in Step 5.

### Output
Design document. NOT presented to user — goes to Step 4 for critique.

### Failure Modes
- All approaches have significant trade-offs: present to user and let them choose. **[Headless: QUESTION — post comment with all approaches, pros/cons, and recommendation, suspend.]**
- Design reveals much larger scope than estimated: flag for user decision. **[Headless: QUESTION — post comment with scope assessment and options (proceed, narrow, abort), suspend.]**

---

## Step 4: Quality Gate — Design Critique

### Instructions

**Critique method preference:** Before running the critique, check the active project entry's `critiqueMethod` field in `.rawgentic_workspace.json`. If set to `"bmad-party-mode"`, use bmad-party-mode instead of the critique below. If missing or `"reflexion"`, proceed as normal.

**Determine gate type based on fast path eligibility:**
- If `fast_path_eligible == true`: use `/reflexion:reflect` (lightweight)
- If `fast_path_eligible == false`: use `/reflexion:critique` (full 3-judge)

**For full critique (`/reflexion:critique`):**

1. Launch three judge sub-agents in parallel. If any returns 429, retry that agent after 30s.

   **Judge 1: Architecture & Patterns Reviewer**
   - Does the design respect existing patterns in the codebase and project conventions?
   - Is the architecture consistent with project conventions?
   - Are dependencies appropriate (prefer existing libraries per project conventions)?

   **Judge 2: Completeness & Testability Reviewer**
   - Are all acceptance criteria addressed?
   - Are edge cases handled?
   - Are failure modes identified?
   - Can the implementation be verified? (tests if available, otherwise manual checks or scripts)

   **Judge 3: Security & Risk Reviewer**
   - Input validation at system boundaries?
   - Credential handling (no hardcoded secrets)?
   - Are changes backward-compatible or is a migration plan in place?
   - Performance implications acceptable?

2. Each judge produces findings:
   ```
   Finding #N:
   - Severity: Critical | High | Medium | Low
   - Category: architecture | completeness | security | testability | scope_fidelity | migration_safety | performance
   - Description: [what the issue is]
   - Recommendation: [specific action]
   - Ambiguity flag: clear | ambiguous
   - Ambiguity reason: [why, if ambiguous]
   ```

3. Synthesize findings. Debate round if judges disagree.

4. **Volume threshold check** (per-tier independent): thresholds per `VOLUME_THRESHOLDS`.

5. **If loop-back triggered:**
   - Check `design_loopback_count` and `global_loopback_total`
   - If within budget: increment counters, apply findings as constraints, return to Step 3
   - If budget exhausted: STOP and escalate to user. **[Headless: ERROR — post error comment with findings summary, add rawgentic:ai-error label, exit.]**

6. **If thresholds pass:** Apply ambiguity circuit breaker.

**For fast path (`/reflexion:reflect`):**
Single-pass checking: does the solution address the issue, are there unintended side effects, is it in the right layer? For WF1-validated issues: does design align with WF1-critiqued spec?

### Output
Amended design document.

### Failure Modes
- Zero findings from full critique: verify judges actually analyzed the design
- Ambiguity circuit breaker triggers on >50% of findings: design may be underspecified

---

## Step 5: Create Implementation Plan

### Instructions

1. **Branch naming:**
   - Features: `feature/<issue-number>-<kebab-case-summary>`
   - Bug fixes: `fix/<issue-number>-<kebab-case-summary>`

2. **Task decomposition:** Break the design into ordered tasks, each appropriately sized (aim for 2-10 minutes each). Adapt the task style to the project:

   **If `capabilities.has_tests == true`:** Follow Red-Green-Refactor per task:
   - RED: Write failing test(s), confirm they fail
   - GREEN: Write minimum code to pass
   - REFACTOR: Clean up

   **If `capabilities.has_tests == false`:** Follow Implement-Verify per task:
   - IMPLEMENT: Write the code/config changes
   - VERIFY: Run a verification command (health check, syntax check, dry-run, or manual inspection)
   - Document what "verified" means for this task

3. **Task ordering:** Make dependencies explicit. Mark parallel-eligible tasks with the same `parallel_group`.

4. **Verification strategy per task:** Specify how each task is verified:
   - Test file + test cases (if test framework exists)
   - Shell command that confirms correct behavior
   - Manual inspection criteria
   - Health check URL

5. **Migrations / config changes (if applicable):** Specify files, content, and rollback approach. Use `capabilities.migration_dir` if it exists, otherwise specify where migration files should live.

6. **Documentation tasks:** Identify docs that need updating (CLAUDE.md, README, Confluence pages, inline comments).

7. **Multi-PR decomposition (if applicable):** If design exceeds 500 lines, decompose by logical phase. Each sub-PR follows Steps 8-14 independently.

8. **Commit messages:** Pre-specify conventional commit messages for each task.

### Output
Implementation plan with ordered tasks, verification strategy, branch name, optional multi-PR decomposition.

### Failure Modes
- Too many tasks (>30) -> suggest scope narrowing or multi-PR
- Circular dependencies -> re-order to break cycles
- Plan references nonexistent files -> verify against Step 2 analysis

---

## Step 6: Quality Gate — Plan Drift Check

### Instructions

Invoke `/reflexion:reflect` with check dimensions:
- **Design-plan alignment:** Does every design component map to at least one task?
- **Verification completeness:** Does every implementation task have a corresponding verification step?
- **Acceptance criteria coverage:** Does the plan, if executed, satisfy all acceptance criteria?
- **Task ordering validity:** Are dependencies correctly ordered?
- **Commit checkpoint adequacy:** Are checkpoints at logical boundaries?

Apply ambiguity circuit breaker on findings. If clear: apply automatically.

### Output
Plan drift check result.

### Failure Modes
- Significant drift detected -> add missing tasks
- Scope creep detected -> remove excess tasks or flag for user decision. **[Headless: AUTO-RESOLVE — remove excess tasks, document removed items in session notes.]**

---

## Step 7: Create Feature Branch

### Instructions

1. Ensure working directory is clean:
   ```bash
   git status --porcelain
   ```
   If dirty: stash, create branch, ask user about stash. **[Headless: AUTO-RESOLVE — always stash, log to session notes AND post a brief comment to the issue noting uncommitted changes were stashed (include `git stash list` output for the stash ref).]**

2. Pull latest default branch and create feature branch:
   ```bash
   git pull origin ${capabilities.default_branch} && git checkout -b <branch_name>
   ```

3. Push empty branch to origin:
   ```bash
   git push -u origin <branch_name>
   ```

4. Link branch to issue:
   ```bash
   gh issue comment <issue_number> --repo ${capabilities.repo} --body "Implementation started on branch \`<branch_name>\`"
   ```

### Output
Feature branch created and pushed, issue commented.

### Failure Modes
- Branch already exists: ask user to resume or start fresh. **[Headless: AUTO-RESOLVE — always resume existing branch.]**
- Push fails: continue locally, push later

---

## Step 8: Implementation

### Instructions

Execute the implementation plan task by task.

**For each task in the plan:**

1. **If TDD mode** (`capabilities.has_tests == true`):
   - RED: Write failing test(s). Run test command from `capabilities.test_commands` to confirm failure.
   - GREEN: Write minimum code to pass. Run tests to confirm all pass.
   - REFACTOR: Clean up. Re-run tests.

2. **If Implement-Verify mode** (`capabilities.has_tests == false`):
   - IMPLEMENT: Write the code, config, or infrastructure changes.
   - VERIFY: Run the verification command specified in the plan. Capture output as evidence.
   - If verification fails: debug and fix before proceeding.

3. **Commit:** Create a conventional commit:
   ```bash
   git add <specific_changed_files> && git commit -m "<type>(scope): <description> (#<issue_number>)"
   ```
   Stage ONLY the files modified in this task. Never `git add -A` or `git add .`.

4. **Push regularly:** Push to origin at natural checkpoints (after every 2-3 tasks or every 30 minutes):
   ```bash
   git push origin <branch_name>
   ```

**Parallel task execution:** For independent tasks (same `parallel_group`), dispatch via parallel Agent tool calls.

**Debugging:** If stuck after 3 manual fix attempts, escalate to systematic debugging.

**Design flaw discovery:** If implementation reveals a fundamental design flaw:
- Check: `tdd_loopback_used == false` AND `global_loopback_total < GLOBAL_LOOPBACK_BUDGET`
- If allowed: loop back to Step 3 with the flaw identified
- If budget exhausted: STOP and escalate to user. **[Headless: ERROR — post error comment with design flaw description + loop-back history, add rawgentic:ai-error label, exit.]**

**Session checkpoint:** Update session notes with progress, verification results, deviations from plan. **[Headless: write a `<headless-checkpoint>` after every 2-3 tasks to enable fresh-session resumption.]**

### Output
Implemented feature with passing tests/verifications on the feature branch, committed and pushed.

### Failure Modes
- Verification fails and cannot be fixed -> flag blocker to user
- Design flaw discovered -> loop back to Step 3 if budget allows
- For TDD: test passes before implementation (test not testing right thing) -> rewrite test

---

## Step 9: Quality Gate — Implementation Drift Check

### Instructions

**Part A: Drift check (invoke `/reflexion:reflect`):**
- Plan-implementation alignment: does every task have a corresponding implementation?
- Design-implementation alignment: does implementation follow the critiqued design?
- Acceptance criteria verification: for each criterion, identify the test/verification that covers it
- Documentation check: are required docs updated?

**Part B: Evidence enforcement:**

If `capabilities.has_tests`:
- Run full test suite using `capabilities.test_commands`
- Verify new tests actually test new behavior
- Confirm no regressions

If NOT `capabilities.has_tests`:
- Re-run all verification commands from the plan
- Confirm all produce expected results
- Document verification evidence in session notes

Apply ambiguity circuit breaker on combined findings.

### Output
Implementation drift check with verification evidence.

### Failure Modes
- Drift detected -> fix implementation or update design doc
- Missing verification coverage -> add before proceeding
- Acceptance criteria not met -> implement missing criteria

---

## Step 10: Conditional Memorization (Background)

### Instructions

**Runs in PARALLEL with Step 11** (dispatch with `run_in_background=true`).

1. Review quality gate findings from Steps 4, 6, and 9.
2. Identify reusable insights — patterns applicable beyond this specific issue.
3. If memorizable insights exist: check for duplication against CLAUDE.md and MEMORY.md, append if novel.
4. If no reusable patterns: skip entirely.

### Output
Updated CLAUDE.md (if insights memorized) or no output.

---

## Step 11: Pre-PR Code Review

### Instructions

**Runs in PARALLEL with Step 10** (this is the foreground task).

1. **Generate diff:**
   ```bash
   git diff ${capabilities.default_branch}..HEAD
   ```

2. **Dispatch 3-agent parallel review.** If any returns 429, retry that agent after 30s.

   **Agent 1: Style & Convention Compliance**
   - Code style rules from project conventions and config.formatting
   - Naming conventions
   - Import ordering
   - No hardcoded credentials or secrets

   **Agent 2: Bug & Logic Detection**
   - Logic errors, edge cases, race conditions
   - Silent failures in catch blocks
   - Null/undefined handling
   - Off-by-one errors, boundary conditions

   **Agent 3: Architecture & History Analysis**
   - Does this change break patterns established by prior commits?
   - Are there related files that should also change?
   - Are there security implications?
   - Is the change backward-compatible?

3. **Filter by confidence:** Only surface findings with confidence >= 0.80.

4. **Severity-based fix workflow:**
   - Critical/High: fix before PR
   - Medium/Low: advisory (fix if easy, otherwise note)

5. **Evaluate each finding before fixing:** verify it's real, check YAGNI, push back on unnecessary changes.

6. Apply ambiguity circuit breaker.

7. **Design flaw detection:** If review finds fundamental flaw, loop back to Step 3 if budget allows.

### Output
Code review result with filtered findings and fixes applied.

### Failure Modes
- Fundamental design flaw -> loop back to Step 3 if budget allows; if budget exhausted: **[Headless: ERROR — post error comment with design flaw description + code review findings + loop-back history, add rawgentic:ai-error label, exit.]**
- Excessive noise (>20 Low findings) -> filter at confidence >= 0.80

---

## Step 12: Create PR and Push

### Instructions

1. **Wait for join barrier:** Both Step 10 and Step 11 complete.

2. **Include memorization changes:** If Step 10 updated CLAUDE.md, commit it:
   ```bash
   git add CLAUDE.md && git commit -m "docs: update CLAUDE.md with implementation insights (#<issue_number>)"
   ```

3. **Final push:**
   ```bash
   git push origin <branch_name>
   ```

4. **Pre-PR test gate** (conditional):
   - If `capabilities.has_tests`: run full suite, block PR if tests fail
   - If NOT `capabilities.has_tests`: re-run key verification commands, document results

5. **Create PR:**
   ```bash
   gh pr create \
     --repo ${capabilities.repo} \
     --title "<type>(scope): <description> (#<issue_number>)" \
     --body-file /tmp/wf2-pr-body.md
   ```

   PR body template:
   ```
   ## Summary
   [summary of changes]

   Closes #<issue_number>

   ## Design Decisions
   [key choices from Step 3]

   ## Verification
   [test results if available, or verification evidence]

   ## Quality Gate Summary
   - Design critique (Step 4): N findings
   - Plan drift check (Step 6): N findings
   - Implementation drift check (Step 9): N findings
   - Code review (Step 11): N findings (all Critical/High resolved)
   ```

### Output
PR URL.

### Failure Modes
- Tests/verifications fail: fix and retry
- Push fails: retry; if persistent, save PR body locally
- Branch conflicts: rebase, resolve, re-push

---

## Step 13: CI Verification (Conditional)

### Instructions

**If `capabilities.has_ci == false`:** Log "No CI configured — skipping Gate 2" in session notes and proceed to Step 14.

**If `capabilities.has_ci == true`:**

1. Monitor CI:
   ```bash
   gh run list --repo ${capabilities.repo} --branch <branch_name> --limit 1 --json status,conclusion,databaseId
   ```

2. If CI passes: proceed to Step 14.

3. If CI fails: diagnose with `gh run view <id> --log-failed`, fix, push, CI re-runs.

4. If CI times out (> CI_MAX_WAIT_MINUTES): ask user for explicit approval. **[Headless: AUTO-RESOLVE — wait up to 2x CI_MAX_WAIT_MINUTES. If still not done, ERROR — post error comment with CI run URL, add rawgentic:ai-error label, exit.]**

### Output
CI status or skip confirmation.

---

## Step 14: Merge PR and Deploy (Adaptive)

### Instructions

1. **Merge PR (squash merge):**
   ```bash
   gh pr merge <pr_number> --repo ${capabilities.repo} --squash --delete-branch
   ```

2. **Pull main:**
   ```bash
   git checkout ${capabilities.default_branch} && git pull origin ${capabilities.default_branch}
   ```

3. **Deploy (adaptive based on capabilities.deploy_method):**

   **If `deploy_method == "script"`:**
   Run the deploy script from `config.deploy`.

   **If `deploy_method == "ssh"`:**
   SSH to infrastructure hosts from `config.infrastructure.hosts[]` and execute the deployment commands appropriate for the change (docker compose up, service restart, config reload, etc.). Generate commands based on the implementation plan — do NOT use hardcoded commands.

   **If `deploy_method == "compose"`:**
   Run `docker compose up -d` with the relevant compose file.

   **If `deploy_method == null` or `"manual"`:**
   Present deployment instructions to the user:
   ```
   MANUAL DEPLOYMENT REQUIRED
   ==========================
   The following changes need to be deployed:
   [list of changes and where they need to be applied]

   Suggested commands:
   [generated from implementation plan]

   Please deploy and confirm when complete.
   ```
   Wait for user confirmation before proceeding to Step 15. **[Headless: QUESTION — post comment with deployment instructions and ask for confirmation, suspend.]**

### Output
Deployed (or manual deployment instructions provided and confirmed).

### Failure Modes
- Merge conflicts: rebase and re-push
- Deploy fails: check logs, rollback if needed
- Manual deploy: user must confirm completion

---

## Step 15: Quality Gate — Post-Deploy Verification (Conditional)

### Instructions

**If `capabilities.has_deploy == false` AND no deployment was performed:** Skip with note "No deployment target — verification deferred to manual testing."

**If deployment was performed:**

Invoke `/reflexion:reflect` with check dimensions adapted to what was deployed:

- **Health check verification:** For each affected service, verify it responds correctly. Generate health check commands from the implementation context (not hardcoded URLs).
- **Acceptance criteria spot-check:** For each criterion, verify evidence of correct behavior using the verification commands from the plan.
- **Regression check:** Did any existing functionality break?

Apply ambiguity circuit breaker.

### Output
Post-deploy verification result (or skip confirmation).

### Failure Modes
- Health checks fail -> inspect logs, restart services
- Acceptance criteria not verifiable -> flag as test gap, verify manually if possible

---

## Step 16: Workflow Completion Summary

### Instructions

1. Update session notes with WF2 results.

2. Present completion summary (adapted to capabilities):

```
WF2 COMPLETE
=============

GitHub PR: [URL] (PR #NNN)
GitHub Issue: [URL] (Issue #NNN — Closes #NNN)

Quality Gates:
- Step 4 (Design): [full critique / fast path reflect] — N findings
- Step 6 (Plan Drift): N findings
- Step 9 (Implementation Drift): N findings
- Step 10 (Memorize): [N insights saved / skipped]
- Step 11 (Code Review): N findings (all Critical/High resolved)
- Step 15 (Post-Deploy): [pass / skipped / deferred]

Verification:                              # Adapt based on capabilities
- Tests: [N passed / not applicable]
- Verifications: [N passed / details]
- CI: [passed / not configured]
- Deploy: [success / manual / not applicable]

Loop-backs used: N / 3 (global budget)

Follow-up items:
- [any items requiring future attention]
```

Do NOT suggest auto-transitioning to WF1 or restarting WF2.

### Output
Completion summary. WF2 terminates.

---

<completion-gate>
Before declaring WF2 complete, verify the following. Items marked (conditional) only apply if the capability exists:

1. [ ] Step markers logged for ALL executed steps in session notes
2. [ ] Final step output (completion summary) presented to user
3. [ ] Session notes updated with completion summary
4. [ ] PR URL documented
5. [ ] All commits pushed
6. [ ] (conditional: has_ci) CI passed
7. [ ] (conditional: has_deploy) Deployment verified or manual deploy confirmed
8. [ ] (conditional: architecture changed) CLAUDE.md updated
9. [ ] All Critical/High code review findings resolved

If ANY applicable item fails, complete it before declaring "WF2 complete."
</completion-gate>
