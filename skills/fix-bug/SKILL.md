---
name: fix-bug
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
**Where WF3 work runs (D174, the executor retreat — 2026-08-03).** Root-cause analysis and the
fix itself run INLINE in this session — reproduce-first TDD, no dispatch machinery, no seats,
no per-phase model routing. A broad read-only gather MAY fan out harness subagents (Agent tool,
Explore-style) when the investigation is wide; inline when narrow (D182). Subagent results get
the vacuous-result check below before anything is consumed.

**Cross-model review (Step 9, and the opt-in Step 4 adversarial pass) runs through ONE entry
point** — `hooks/review_runner.py` (D179) — dispatched from a read-only harness subagent so the
inline self-review and the cross-model review run in parallel. The WF3 command shape:
```bash
python3 hooks/review_runner.py review-code --base <default branch> --brief <brief.md> \
  --author-model <your own model id, verbatim> --reviewer <reviewer, below> \
  [--backend gpt|glm] [--reopen-token <token.json>] --out <result.json> --project-root .
```
The runner composes the diff itself from `--base`, refuses oversize input, and binds freshness
(`base_sha`/`head_sha`/`input_sha256` in the result) — the artifact-delivery guarantee that
`--requires-context` used to provide (#826) is now structural: a route that cannot carry the
bytes cannot be called.

<review-severity>
Severity is not a mood. It is the ONLY trigger for loop-backs (`steps.md` Step 8a/11
triage and the `Loopback-class` rule), so an uncalibrated label spends real budget. Before
this block these four words had **no definition anywhere in this repo** (a grep of `skills/`
and `shared/` returned nothing), while Critical/High alone decided whether the workflow
looped. Every reviewer invented their own scale.

**The rubric. Judge IMPACT and PRECONDITIONS separately, then band.**

- **Critical** — a plausible catastrophic, system-wide, or security/integrity failure, with
  preconditions that occur in NORMAL operation, and no downstream control that catches it.
  All three. Data loss, a gate that silently passes what it exists to refuse, a credential
  leak. Blocks: must fix before Step 9.
- **High** — a major correctness or workflow-integrity failure, but bounded: it needs an
  unusual precondition, OR a downstream control detects it, OR it is recoverable once seen.
  **Deferrable with rationale** via `plan_lib.append_deferral` — re-presented at Step 11.
  A High is NOT an automatic blocker, and treating it as one is how a PR reaches round 13.
- **Medium** — real but contained: a wrong error message, a missing guard on a path with a
  working sibling, prose contradicting code without changing behaviour. Advisory.
- **Low** — cosmetic, stylistic, or speculative. Advisory.

**Worked calibration, from #840 round 13.** `observe_head` fetched without a refspec, so a
narrow `remote.origin.fetch` let a STALE sha pass as freshly observed — defeating the gate's
own freshness clause. Impact is catastrophic (the gate stops gating). But it requires a
non-default git configuration and had no observed production incidence, so it is **High, not
Critical**. Promote to Critical only if the configuration is common or the stale decision
triggers something irreversible with nothing else in the way.

**Severity is not confidence.** Report BOTH. `plan_lib.SEVERITY_BANDED_CONFIDENCE` drops a
finding whose confidence is below its band (Critical 0.50, High 0.65, Medium 0.80, Low 0.90)
— a filter that **cannot run if the brief never asks for a confidence score**, which is
exactly what happened across #840 rounds 4-13. Every review brief MUST request
`severity`, `confidence` (0.0-1.0), and a one-line precondition/impact rationale. A Critical
requires explicit disposition regardless of confidence; a low-confidence severe claim
triggers targeted verification, never an automatic fix.

**Brief hygiene — measured, not stylistic.** #840 round 13 ran three adversarial briefs
(19.5-19.8 KB, carrying an accumulated 13-round failure history) against three neutral ones
(3.6 KB) on the same commit, seat and lane. All six FAILed on the same real defects, so the
adversarial framing bought nothing — but the NEUTRAL arm found a Critical the adversarial
arm missed, because the bloated brief had told reviewers that clause was "stable for eight
rounds" and all three duly looked elsewhere. Therefore:

- **Never tell a reviewer what verdict to reach** ("do not approve this") and never state
  which areas are settled. Both suppress findings.
- **Never accumulate round history in a brief.** Carry the diff, the scope, the deferred
  list, and unresolved claims — never past conclusions. History grew these briefs 6.5 KB →
  19.8 KB across rounds for a strictly worse review.
- **Keep a review brief under ~8 KB.** If it will not fit, the change under review is too
  large to review in one pass — split it instead of enlarging the brief.
</review-severity>

**Reviewer identity is pinned, never inherited.** The current default reviewer id is
**`gpt-5.6-sol`** (single-sourced in `shared/blocks/model-routing-resolve.md` — a retired id
fails loudly and is updated there). Alternate backend: `--backend glm` (model `glm-5.2`). The
runner refuses author==reviewer and unresolvable identities; pass your own model id as
`--author-model`, verbatim. Exit codes: `0` success (check `diagnostic` in the result JSON) ·
`2` refused (no egress) · `3` terminal backend failure · `4` empty/invalid output. The runner
owns transport policy (#857): one bounded transport retry, org-wide 429 terminal, one permitted
backend switch on a per-account 429 — never add your own retry loop around it. Retry a FAILED
review dispatch once; a second failure follows the ERROR protocol — never a silent skip.

**Actionable vs diagnostic — the reopen choke point (#855).** A review result may open a fix
round ONLY when the run carried a reopen token minted first via
`python3 hooks/plan_lib.py review-reopen --state-file claude_docs/.wf3-state/<issue>/loopback_counters.json --source review --out <token.json> --project-root .`
— the mint debits the loop-back budget (see `LOOPBACK_BUDGET` in `<constants>`); exhaustion
refuses and the gate escalates. A tokenless result carries `diagnostic: true` and MUST NOT open
a fix round. A subagent or runner dispatch is never a gate bypass — every mandatory review gate
runs with identical semantics whether a pass ran inline or through `hooks/review_runner.py`,
and a review that may open a fix round carries a reopen token minted first.

**The vacuous-result gate — subagent results are hypotheses.** Before consuming any subagent
result: the `--out` file exists and is non-empty; it parses as JSON with a `status` matching the
exit code; `head_sha`/`input_sha256` still match the current state (a stale result is rejected
and re-run); load-bearing claims are spot-verified against the cited file:line. A dead subagent,
an empty file, or a missing status is a FAILED dispatch — never a pass, never "still running"
(#766). Keep ≤ 3 concurrent Claude subagents.
</model-routing-resolve>

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
Inherited from WF2 (identical behavior): Apply ALL findings from quality gates automatically. If any finding is ambiguous, conflicting, or requires judgment — STOP and present to user for resolution before proceeding. User has final authority (P11). In an unattended run (e.g. an epic-run child), post the ambiguous/conflicting findings as an issue comment via the ERROR protocol and stop this child — never resolve them silently.
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
suggestion, unattended advisories) are declared deferrals, not misses.
Step-entry state (#480, hook-emitted since #499): the PostToolUse hook (`hooks/step_state_post.py`) stamps later steps from step DONE markers and signature commands — but ONLY once the step-state pointer already names this session, which a DONE marker or an explicit write creates. It does NOT stamp unaided: a run that creates no pointer contributes no timing at all (#976 measured exactly that). The manual `python3 hooks/step_state.py write --project <project> --workflow wf3 --step <N> --step-title "<step name>" --issue <issue number> --session-id "$CLAUDE_CODE_SESSION_ID"` call is MANDATORY once per run at the branch cut (see that step) and useful belt-and-suspenders for entry-time precision on prose-only steps. Fail-open either way (never gates; any failure is ignored and the step proceeds).
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
