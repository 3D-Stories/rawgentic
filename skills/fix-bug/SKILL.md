---
name: rawgentic:fix-bug
description: Fix a bug using the WF3 14-step workflow with reproduce-first TDD, root cause analysis, lightweight reflect, and conventional commit PR. Invoke with /fix-bug followed by an issue number. Only trigger when the user explicitly invokes /fix-bug or /rawgentic:fix-bug.
argument-hint: GitHub issue number (e.g., "42") or issue URL
---


# WF3: Bug Fix Workflow

<role>
You are the WF3 orchestrator implementing a 14-step bug fix workflow. You guide the user from bug report through root cause analysis, reproduce-first TDD, code review, and deployment verification. WF3 is a specialized fast-path derivative of WF2 — same quality assurance framework, fewer steps, optimized for rapid turnaround. You enforce the reproduce-first principle: a failing test capturing the bug MUST exist before any fix code is written.
</role>

<overview>
The always-run spine, in order. Steps 11-13 are conditional (skipped only when
their condition is unmet). When you lose the thread under context pressure, this
is the sequence to return to:

  1 -> 1b -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10 -> (11) -> (12) -> (13) -> 14

Steps **1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 14 always run** (see <mandatory-steps>).
The per-step detail lives in references/steps.md (read the step's section before
executing it); the cross-cutting protocols live in this spine's blocks. Consult
them situationally - not top-to-bottom on every run. WF3 keeps its bug-first
identity: reproduce-first TDD (<reproduce-first-principle>), root-cause analysis
before any fix, and the complexity escalation to WF2 (<complexity-override>) are
all first-class.
</overview>

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

<mandatory-steps>
The following steps are MANDATORY and must NEVER be skipped, abbreviated, or combined — regardless of context window pressure, session length, perceived simplicity, or any other justification:

| Step | Name | Why mandatory |
|------|------|---------------|
| 1 | Receive Bug Report | Foundation — wrong bug = wrong fix |
| 2 | Analyze Bug Context | Complexity classification + reproduction context |
| 3 | Root Cause Analysis | Fixing symptoms without RCA causes regressions |
| 4 | Quality Gate (Reflect) | Validates the RCA before implementation |
| 5 | Create Fix Plan | Task decomposition for TDD |
| 6 | Create Branch | Git isolation is non-negotiable |
| 7 | TDD Bug Fix | Reproduce-first is the core WF3 principle |
| 8 | Verification | Confirms fix works and no regressions |
| 9 | Code Review | **NON-NEGOTIABLE.** Catches security issues, logic errors, and regression risks in the fix. |
| 10 | Create PR | Deliverable — no PR means no review trail |

Conditional steps (skip ONLY when their condition is not met):
- Step 11 (CI): skip only if has_ci == false
- Step 12 (Merge/Deploy): skip only if user does not request merge
- Step 13 (Post-Deploy): skip only if no deployment performed

**ENFORCEMENT:** You MUST NOT rationalize skipping a mandatory step. Common invalid justifications:
- "This is a simple one-line fix" — one-line fixes can introduce injection vulnerabilities
- "The session is getting long" — checkpoint in session notes and resume, do not skip
- "I already reviewed the code while writing it" — self-review is not code review

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

2. Load the config and derive capabilities with the helper CLI (one tested
   source of truth — never hand-derive the `capabilities` object, so every
   config-driven skill and the docs table cannot drift apart):
   ```bash
   python3 hooks/capabilities_lib.py derive \
     --config <activeProject.path>/.rawgentic.json
   ```
   - **Non-zero exit** -> the config is missing, corrupt, or invalid. **STOP** and relay the printed message (it directs the user to `/rawgentic:setup`). A `config.version` mismatch is only a stderr warning and does NOT stop the workflow.
   - **Exit 0** -> stdout is `{"config": {...}, "capabilities": {...}}`. Use the parsed `config` object and the derived `capabilities` object for all subsequent steps. The `capabilities` fields are: `has_tests`, `test_commands`, `has_ci`, `ci_quarantined`, `ci_quarantine_reason`, `ci_quarantined_since`, `has_deploy`, `deploy_method`, `has_database`, `has_docker`, `project_type`, `repo`, `default_branch`, `migration_dir`. Carry these values as literals into later commands (each step is its own Bash call, so shell variables do not persist across them).

All subsequent steps use `config` and `capabilities` — never probe the filesystem for information that should be in the config.
</config-loading>

<model-routing-resolve>
Resolve model routing (optional, fail-open) right after `<config-loading>`, before any subagent dispatch. For the `review` role this skill dispatches, resolve the configured model:
```bash
python3 hooks/model_routing_lib.py resolve \
  --workspace .rawgentic_workspace.json --project <name> --role review
```
Exit is always 0; stdout is a model name or `inherit`. If `hooks/model_routing_lib.py` is missing (e.g. a stale plugin cache), the invocation may exit non-zero — treat that, and any non-zero/absent output, as `inherit`. Carry the resolved value as a literal into later steps (fresh-shell rule). When the value is `inherit`, dispatch review subagents with NO `model:` parameter (session model). Otherwise pass `model: <value>` on every Agent dispatch for review. A stderr warning is advisory — never treat it as failure.

Also resolve the `review` role's effort tier with a second invocation appending `--effort`, printing the effort string or `none`; carry both the model and the effort as literals. When the resolved effort is `none`, dispatch exactly as today. When it is non-`none`: the Agent tool has no per-invocation effort parameter, so effort is carried dual-path — (a) pass it where the dispatch layer supports effort (the Workflow tool's `agent(prompt, {effort: <value>})` option, or a Codex dispatch's reasoning-effort flag), and (b) always record it in the dispatch's session-note/audit line (e.g. `dispatch review: model <model>, effort <effort>`) so the resolved tier stays observable even where delivery is definition-level only (bundled agent-definition files are an M3 follow-up, out of scope here).
</model-routing-resolve>

<headless-mode>
When `additionalContext` contains "HEADLESS MODE active", you operate without a terminal
user: the QUESTION (post→label→suspend→exit), ERROR, rich-checkpoint, and fresh-session
resume protocols live in `references/headless.md`. **Read that file in full before acting on
any of the per-step headless annotations in `references/steps.md`.** When NOT in headless mode, ignore them and behave
normally (STOP and wait for terminal input at each interaction point).
</headless-mode>

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
Inherited from WF2 (identical behavior): Apply ALL findings from quality gates automatically. If any finding is ambiguous, conflicting, or requires judgment — STOP and present to user for resolution before proceeding. User has final authority (P11). **[Headless: QUESTION — post comment with all ambiguous/conflicting findings and resolution options, suspend.]**
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

<references>
Progressive disclosure. This spine carries the always-run protocols and a
one-line-per-step overview; the full detail lives in per-skill reference files
(all under skills/fix-bug/ - a marketplace plugin cache excludes paths outside a
skill's own directory, so WF3 never reads another skill's references), read on
demand by this contract:
- `references/steps.md` - the full per-step instructions (verbatim per-step
  sections for every step, plus Workflow Resumption and Conditional Memorization).
  Read the step's section before executing it. It also holds the
  `<trivial-work-check>` and `<learning-config>` step-semantic blocks.
- `references/headless.md` - the headless interaction protocols. Read IN FULL
  when `additionalContext` has "HEADLESS MODE active" (see <headless-mode>).
- `references/incident.md` - the incident lane. When a bug fix is an INCIDENT
  (production down, time-critical), WF3's hotfix lane PLUS this comms + post-mortem
  checklist (the deprecated standalone WF11 flow's surviving content) replaces
  WF11. Read it for incident-severity bugs.
</references>

## Steps

One line per step; read `references/steps.md` (the step's section) before
executing each step. The ordered spine is in `<overview>`; MANDATORY vs
conditional is in `<mandatory-steps>`.

- **Step 1 - Receive bug report.** Load config + environment, fetch/validate the issue (STRIDE-aware), memory-search bug history, confirm with user. (read references/steps.md before executing)
- **Step 1b - AC-derived goal guard (`/goal`).** Build the goal text via `plan_lib.build_goal_text(..., variant="wf3")` and fold it into Step 1's confirmation; optional, never blocks. (read references/steps.md before executing)
- **Step 2 - Analyze bug context & classify.** Trace the reproduce path, blast radius, test inventory; set complexity (simple/moderate/complex -> WF2) and the trivial-work suggestion (`<trivial-work-check>`). (read references/steps.md before executing)
- **Step 3 - Root cause analysis.** Hypotheses -> evidence -> root cause -> minimal fix approach -> regression-risk assessment. (read references/steps.md before executing)
- **Step 4 - Quality gate: lightweight reflect.** `/reflexion:reflect` on RCA correctness; opt-in default-off cross-model adversarial review; the breaker runs on the merged findings. (read references/steps.md before executing)
- **Step 5 - Create fix plan.** Ordered TDD tasks (reproduction test -> minimal fix -> regression tests -> docs), branch name, 3-6 task estimate. (read references/steps.md before executing)
- **Step 6 - Create fix branch.** Branch from a freshly-fetched `origin/<default>`; pre-flight dependency install. (read references/steps.md before executing)
- **Step 7 - TDD bug fix (reproduce-first).** RED reproduction test -> GREEN minimal fix -> minimal refactor -> regression tests -> full suite -> frequent conventional commits. (read references/steps.md before executing)
- **Step 8 - Lightweight verification.** Self-check: ACs addressed, reproduction genuine, no stray changes, all tests pass. (read references/steps.md before executing)
- **Step 9 - Code review + conditional memorize.** 2-agent review (silent-failure-hunter + code-reviewer) with `model: <review>` routing; conditional `/reflexion:memorize`. NON-NEGOTIABLE. (read references/steps.md before executing)
- **Step 10 - Create pull request.** Stage named files, conventional `fix(scope):` commit closing the issue, push, open the templated PR. (read references/steps.md before executing)
- **Step 11 - CI verification (conditional).** Monitor/fix CI when `has_ci`; quarantine handled as a visible non-gate with a trust guard. (read references/steps.md before executing)
- **Step 12 - Merge & deploy (conditional).** Only on user-requested merge; squash-merge, deploy, migration. (read references/steps.md before executing)
- **Step 13 - Post-deploy verification (conditional).** Only if a deployment happened; symptom + E2E + health + same-class bug scan. (read references/steps.md before executing)
- **Step 14 - Completion summary + run-record.** WF3 terminates here; assemble the run-record and render via `work_summary.py`. (read references/steps.md before executing)

<completion-gate>
Before declaring WF3 complete, verify ALL of the following. Print the checklist with pass/fail for each item:

1. [ ] Step markers logged for ALL executed steps in session notes
2. [ ] Final step output (completion summary) presented to user
3. [ ] Session notes updated with completion summary
4. [ ] PR URL documented
5. [ ] Root cause documented in session notes
6. [ ] Same-class bug scan completed
7. [ ] E2E passed
8. [ ] Completion summary rendered via `work_summary.py` (Step 14) and the run-record persisted (rc 0) — or, if validation failed (rc 1), the telemetry gap is recorded in session notes

If ANY item fails, go back and complete it before declaring "WF3 complete."
You may NOT output "WF3 complete" until all items pass.
</completion-gate>
