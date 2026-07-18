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
| 14 | Completion Summary + run-record | Always-run closure — WF3's terminal deliverable and Tier-2 telemetry substrate. Runs even when Steps 11-13 are skipped (a PR-terminal run). |

Conditional steps (skip ONLY when their condition is not met):
- Step 11 (CI): skip only if has_ci == false
- Step 12 (Merge/Deploy): skip only if user does not request merge
- Step 13 (Post-Deploy): skip only if no deployment performed

(Step 14 sits in the mandatory set above even though it follows the conditional Steps
11-13: only 11-13 are conditional, 14 always runs — see `<mandatory-rule>`.)

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

**Canonical DISPATCH audit line (#330).** The lowercase start-time line above stays as-is (observability only, never parsed). At the point each `review` dispatch decision COMPLETES — or the orchestrator declares it dead/abandoned — ALSO append one uppercase canonical line carrying the issue number and all six schema fields, fixed key order, single-space-separated, one line. WF3 dispatches only the `review` role, so `role` is always `review`:
```
DISPATCH issue=<n> role=review type=<subagent_type> model=<model|null> effort=<effort|null> outcome=<ok|error|retried|dead> resolution=<primary|fallback|generic>
```
Canonical regex (assembly's scoped grep + validator):
```
^DISPATCH issue=(\d+) role=(review) type=([A-Za-z0-9_.:/-]+) model=(null|[A-Za-z0-9_.:/-]+) effort=(null|[A-Za-z0-9_.:/-]+) outcome=(ok|error|retried|dead) resolution=(primary|fallback|generic)$
```
Emission rules:
- One line per SUBAGENT INVOCATION dispatched (not per attempt) — WF3 Step 9's two review agents = two lines at a single tier; a slot that descends on a RUNTIME ERROR adds the abandoned tier's terminal line, while a resolve-failure descent adds none (an unresolvable tier never ran).
- Descent emission splits by TRIGGER (#331): a RESOLVE-FAILURE descent (the tier's agent type is not installed / does not resolve) emits NO line for the unresolved tier — only the tier that actually RAN emits (a line claiming an uninstalled agent "ran and errored" would be a fabricated audit record); double-unavailability (tiers 1 AND 2 unresolvable) therefore emits ONE line total for that slot (the tier-3 line). A RUNTIME-ERROR descent (the tier was attempted, retried once, and errored again) emits TWO lines: the abandoned tier's terminal line with `outcome=error` and THAT TIER's own resolution value (tier 1 → `resolution=primary`, tier 2 → `resolution=fallback`) plus the new tier's line.
- Write each line flush-left at column 0 as its own physical line — never inside a list item, blockquote, or fenced code block (the assembler greps `^DISPATCH` anchored to line start; an indented or bulleted line is rescued only into the MALFORMED count, never into `dispatches[]`).
- Retry semantics: a single retry of the SAME invocation is ONE line — retried-then-succeeded → `outcome=retried`; retried-and-still-failed → `outcome=error`. A hung/vacuous dispatch abandoned by the orchestrator → `outcome=dead`. A dispatch that errors into a failure handler or a suspend still gets its canonical line (`outcome=error` or `dead`) BEFORE the handler/suspend proceeds.
- `type`/`model`/`effort` values are stable slugs matching `[A-Za-z0-9_.:/-]+` (no spaces, no commas). Write the literal `null` when the role resolved `inherit` (model) or `none` (effort) — never an empty string or "unknown".
- A generic inline-prompt review dispatch (no bundled agent type ran) uses the stable `subagent_type` token `generic-review` and carries `resolution=generic`.
- `issue=<n>` is this run's issue number (scoping key — assembly greps the whole session-notes file for `^DISPATCH issue=<n> `). `DISPATCH` is uppercase so it can never collide with the lowercase start-time prose line.

Resolution decision table (WF3 dispatches only `review`):

| Dispatch path | resolution |
|---|---|
| Tier 1: the named `pr-review-toolkit:*` agent ran | `primary` |
| Tier 2: `rawgentic:rawgentic-reviewer` ran as the SUBSTITUTE for an unresolvable named type | `fallback` — the first real producer (#331) |
| Tier 3: generic inline-prompt dispatch (`subagent_type` = `generic-review`) | `generic` |

**Seat fallback chains + circuit breaker (#417).** WF3's `review` seat model is a config-declared chain (the routing table, interim fable → sol → sonnet, #426), tried in order on an AVAILABILITY failure with a chain-aware cross-model skip; a chain that exhausts its eligible entries is a handled hard failure, never a silent downgrade. This is DISTINCT from the per-slot agent-TYPE descent in the tier table above (which selects the reviewer type, not the model).

**Concurrency ceiling (#417).** ≤ 3 concurrent Claude subagents (the standing cap); reserve one slot for the driver when it dispatches Claude work alongside them → an effective working ceiling of 2. Prose rule, no programmatic clamp — honor it when fanning out review agents (a codex/zhipu candidate consumes no Claude slot).

**Driver seat — guidance, not enforcement (#417).** opus-4-8 is the recommended session/orchestrator model (the strong-model-on-top reliability floor; unbenchmarked until the driver-bench, #430). Guidance only — the harness owns the session model.
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
WF3 terminates after the completion summary (Step 14) — plus deployment verification when a deployment occurred (Step 13 is conditional). No auto-transition to other workflows. WF3 terminates ONLY after the completion-gate (after Step 14) passes. All steps must have markers in session notes, and the completion-gate checklist must be printed with all items passing.
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
**Step 14 (Completion Summary + run-record) always runs — it is the workflow's
mandatory closure and is never skipped**, even when the fix is confirmed working: a bug
fix without a recorded completion risks repeating the same class of bug.

Steps 11-13 (CI Verification, Merge/Deploy, Post-Deploy Verification) are **conditional**,
exactly as `<mandatory-steps>` defines them — CI runs only when `has_ci`, and merge/deploy
happen only when the user requests a merge. Merge is **owner-gated**: a WF3 run is
PR-terminal (mirroring WF2), so the terminal deliverable is normally an OPEN PR. Do NOT
treat merge/deploy as unconditional.

**Issue closure follows the merge, not the workflow.** Step 10 commits with
`(closes #<issue>)`, so GitHub closes the issue automatically when the owner merges the
PR. Step 14 therefore does NOT close the issue on its own unless a merge was **verified as
completed** during this run (Step 12) — closing an issue whose fix never merged is the
exact defect this rule guards against. If the project's development rules require explicit
approval for merge or deploy, ask the user before those conditional steps; the always-run
Step 14 closure summary still runs regardless.
</mandatory-rule>

<step-tracking>
At the end of each step, log a marker in `claude_docs/session_notes.md`:
`### WF3 Step X: <Name> — DONE (#<issue>: <key detail>)`
This enables workflow resumption if context is lost.

On every marker line the run key is read from the marker type's canonical slot —
concurrent runs share one notes file and un-keyed markers are mechanically
un-attributable (#341). The key is read ONLY from that marker type's slot (below); a
`#N` in a free-text tail is never the key, and a marker whose slot holds no `#<n>` is
legacy/un-keyed (section-header fallback, attribution-ambiguous, never an error). The fallback exists for PRE-#341 notes and
stale-cache (≤3.27) emitters ONLY: a run executing THIS contract that emits a
prescribed marker without its slot key has violated the contract.

| Marker type | Canonical key slot |
| --- | --- |
| DONE-parens (`— DONE (…)`) | first token inside the parens: `— DONE (#<issue>: <detail>)` |
| enum-parens with trailing detail (Step 1b) | first token of the trailing detail: `(set\|deferred\|skipped): #<issue> — <detail>` |
| bare-enum, no trailing detail (Step 10 design artifact) | post-label, pre-enum: `— design artifact #<issue> (updated\|skipped)` |
| parens-state (Step 4 adversarial) | key leads inside the parens: `(#<issue>, invoked\|skipped)` |

This slot table is AUTHORITATIVE: every prescribed marker literal in references/ must
conform to its type's slot, and when a literal and this table diverge the table wins.
Emitters: the key MUST land in the type's slot — a key anywhere else on the line is
ignored by consumers. Deliberately un-keyed informational markers (trivial-work
suggestion, headless advisories) are declared deferrals, not misses.
Step-entry state (#480, observational): at each numbered step ENTRY, run `python3 hooks/step_state.py write --project <project> --workflow wf3 --step <N> --step-title "<step name>" --issue <issue number> --session-id "$CLAUDE_CODE_SESSION_ID"` — fail-open (never gates; any failure is ignored and the step proceeds).
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
- **Step 3 - Root cause analysis.** Hypotheses -> evidence -> root cause -> minimal fix approach -> regression-risk assessment -> platform-feasibility check when the fix relies on a platform/external API (#226). (read references/steps.md before executing)
- **Step 4 - Quality gate: lightweight reflect.** the quality-bar rubric (`references/quality-bar.md`) on RCA correctness incl. the platform-feasibility lens (#226); opt-in default-off cross-model adversarial review; the breaker runs on the merged findings. (read references/steps.md before executing)
- **Step 5 - Create fix plan.** Ordered TDD tasks (reproduction test -> minimal fix -> regression tests -> docs), branch name, 3-6 task estimate. (read references/steps.md before executing)
- **Step 6 - Create fix branch.** Branch from a freshly-fetched `origin/<default>`; pre-flight dependency install. (read references/steps.md before executing)
- **Step 7 - TDD bug fix (reproduce-first).** RED reproduction test -> GREEN minimal fix -> minimal refactor -> regression tests -> full suite -> frequent conventional commits. (read references/steps.md before executing)
- **Step 8 - Lightweight verification.** Self-check: ACs addressed, reproduction genuine, no stray changes, all tests pass. (read references/steps.md before executing)
- **Step 9 - Code review + conditional memorize.** 2-agent review (silent-failure-hunter + code-reviewer) with `model: <review>` routing; conditional memory curation (mempalace if available, else `CLAUDE.md` / `MEMORY.md`). NON-NEGOTIABLE. (read references/steps.md before executing)
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
6. [ ] Same-class bug scan completed — **only if a deployment occurred (Step 13 ran); N/A for a PR-terminal run where merge/deploy were skipped**
7. [ ] E2E passed — **only if a deployment occurred (Step 13 ran); N/A for a PR-terminal run**
8. [ ] Completion summary rendered via `work_summary.py` (Step 14) and the run-record persisted (rc 0) — or, if validation failed (rc 1), the telemetry gap is recorded in session notes

If ANY item fails, go back and complete it before declaring "WF3 complete."
You may NOT output "WF3 complete" until all items pass.
</completion-gate>
