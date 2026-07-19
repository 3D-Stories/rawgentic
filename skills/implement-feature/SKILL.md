---
name: rawgentic:implement-feature
description: Implement a feature (or a design-heavy/complex bug fix) from a GitHub issue through the WF2 16-step workflow with TDD, multi-agent code review, quality gates, and — when the project configures them — CI and deployment verification. Invoke with /implement-feature followed by a GitHub issue number or URL. For a narrow, reproducible bug fix prefer /rawgentic:fix-bug (WF3); implement-feature is the home for features and for bugs that need full design + implementation. Only trigger when the user explicitly invokes /implement-feature or /rawgentic:implement-feature.
argument-hint: GitHub issue number (e.g., 155) or URL
---

# WF2: Feature Implementation Workflow (v2.0)

<role>
You are the WF2 orchestrator implementing a 16-step feature implementation workflow. You take a GitHub issue (created by WF1 or manually) and guide it through codebase analysis, design, critique, implementation, code review, PR creation, and optional CI/deployment verification. You adapt your behavior based on project capabilities detected at startup — not all projects have tests, CI, or automated deployment, and the workflow gracefully handles each case.
</role>

<happy-path>
The always-run spine, in order. Parenthesized steps are conditional (skipped only
when their condition is unmet). When you lose the thread under context pressure,
this is the sequence to return to:

  1 → 2 → 3 → 4 → 5 → (6) → 7 → 8 → (8a) → 9 → (10) → 11 → 11.5 → 12 → (13) → (14) → (15) → 16

- (6) Plan Drift — fast; run unless time-critical or in the small-standard lane
- (8a) Per-task Review — only when a task is `riskLevel: high` (P15)
- (10) Memorize — background, never blocks
- (13) CI — only if `has_ci`
- (14) Merge/Deploy — only if the user requests merge
- (15) Post-Deploy — only if a deployment happened

Steps **1, 2, 3, 4, 5, 7, 8, 9, 11, 11.5, 12, 16 always run** (see <mandatory-steps>).
The per-step detail lives in references/steps.md (read §N before executing Step N);
the cross-cutting protocols live in this spine's blocks and references/state-and-resume.md.
Consult them situationally — not top-to-bottom on every run.
</happy-path>

<constants>
MAX_DESIGN_LOOPBACK_ITERATIONS = 2
MAX_TDD_DESIGN_LOOPBACK = 1
MAX_REVIEW_DESIGN_LOOPBACK = 1
MAX_REVIEW_DESIGN_LOOPBACK_STEP_8A = 1   # P15: Step 8a per-task review loopback (separate from tdd)
MAX_SPEC_TIGHTEN_LOOPBACK = 2            # #223: Step 4 in-gate spec-tightening pass (no Step-3 return)
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
PER_TASK_REVIEW_AGENT_COUNT = 2                        # Step 8a's single accumulated wave uses 2 reviewer roles (#492)
# Severity-banded confidence applied to Step 8a AND Step 11 reviewer findings.
# Critical and High get a lower bar because hiding them is more dangerous than
# flagging false-positives. These values mirror plan_lib.SEVERITY_BANDED_CONFIDENCE
SEVERITY_BANDED_CONFIDENCE:
  Critical: 0.50
  High:     0.65
  Medium:   0.80
  Low:      0.90
WF2_HIGH_RISK_RATIO_WARN_PCT = ${WF2_HIGH_RISK_RATIO_WARN_PCT:-30}   # warn band; clamped [5,95]
WF2_HIGH_RISK_RATIO_HALT_PCT = ${WF2_HIGH_RISK_RATIO_HALT_PCT:-50}   # halt band; clamped [10,95]; halt>=warn+10
# Source of truth in hooks/plan_lib.py: the SEVERITY_BANDED_CONFIDENCE dict (mirrored
# above; a drift-guard test asserts the two stay equal) and WF2_HIGH_RISK_RATIO_*
# (env-var freeze at import).
</constants>

<mandatory-steps>
The following steps are MANDATORY and must NEVER be skipped, abbreviated, or combined — regardless of context window pressure, session length, perceived simplicity, or any other justification:

| Step | Name | Why mandatory |
|------|------|---------------|
| 1 | Receive Issue | Foundation — wrong issue = wrong implementation |
| 2 | Analyze Codebase | Complexity classification drives all downstream decisions |
| 3 | Design Solution | Architecture before code — always |
| 4 | Quality Gate (Design) | Catches design flaws BEFORE implementation. The in-repo quality-bar rubric + the mechanical platform-feasibility gate (#226) run for all lanes; the full spine adds the opt-in adversarial-on-design + peer consult. |
| 5 | Implementation Plan | Task decomposition enables TDD and progress tracking |
| 7 | Create Branch | Git isolation is non-negotiable |
| 8 | Implementation | The actual work |
| 9 | Quality Gate (Drift) | Verifies implementation matches design and all ACs covered |
| 11 | Code Review | **NON-NEGOTIABLE.** Full 2-agent review (#492 — the security lens is never the one dropped); ≥1 in the small-standard lane. This step found 2 Critical security issues (HTML injection + path traversal) when the orchestrator attempted to skip it. |
| 11.5 | Security Scan | Tool-based pre-PR gate (secrets / dependency-CVE / SAST / IaC) via `hooks/security_scan.py`. Catches concrete known-pattern problems the LLM review misses; fail-closed on a real finding. The step always runs — absent scanners are a recorded *visible skip*, never a silent pass. |
| 12 | Create PR | Deliverable — no PR means no review trail |
| 16 | Completion Summary + run-record | WF2 terminates here. The run-record (`hooks/work_summary.py`) is the Tier-2 telemetry substrate — a dropped field is a measurement gap, so the step is not optional even when nothing deployed. |

Conditional steps (skip ONLY when their condition is not met):
- Step 6 (Plan Drift): lightweight, fast — run it unless time-critical **or in the small-standard lane** (`<small-standard-lane>`)
- **Step 8a (Per-task Review, P15):** mandatory when ANY task has `riskLevel: high`. Dispatched as ONE accumulated wave after the last plan task's commit, covering every high-risk commit (#492 — was per-task-batch). Marker (one per covered task): `### WF2 Step 8a [task <id>, sha <abc>]: DONE (#<issue>: <N findings>)` in session notes.
- Step 10 (Memorize): background, never blocks
- Step 13 (CI): skip only if has_ci == false
- Step 14 (Merge/Deploy): skip only if user does not request merge — **always skipped in headless mode** (`additionalContext` has "HEADLESS MODE active"): PR creation is the terminal deliverable, so a headless run never merges or deploys (`references/headless.md`).
- Step 15 (Post-Deploy): skip only if no deployment performed — also skipped in headless mode (no deployment occurred).

**Why these hold even under pressure:** the tempting reasons to skip — a long session, a
running-low context window, a change that "looks mechanical," or "WF1 already critiqued
this" (WF1 critiqued the *spec*, not the *code*) — are exactly the conditions under which
the expensive gates earn their cost: Step 11 caught 2 Critical bugs (HTML injection + path
traversal) on a run the orchestrator judged too simple to review. So if you're tempted to
skip a step to save time, checkpoint per `<resumption-protocol>` and resume — don't skip.

**The ONE sanctioned way to not run these steps** is the pre-implementation
`<trivial-work-check>` at Step 2: if the user explicitly chooses "do it directly," you
are declining to run WF2 *at all* (no code has been written yet) — that is NOT skipping
a mandatory step mid-run. Once you proceed past Step 2 into the workflow, every step
above is non-negotiable.

**Small-standard lane reconciliation (`<small-standard-lane>`):** in the small-standard lane,
Steps 3, 4, 5, and 9 run in their **COLLAPSED** form (Step 3 = a brief design note, no
multi-approach brainstorm; Step 4 = the quality-bar rubric; Step 5 = a checklist plan; Step 9 =
Part B evidence only) — they are **not skipped**, so the mandatory-step invariant still holds. Only **Step 6 (Plan Drift)** is skipped in the lane, and it is already a
conditional step. **Step 11 (code review), Step 11.5 (security scan), and Step 8a for any
`riskLevel: high` task remain NON-NEGOTIABLE in the lane**, exactly as on the full spine — the
lane is cheaper on design ceremony, never on review or security.
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
   - **Exit 0** -> stdout is `{"config": {...}, "capabilities": {...}}`. Use the parsed `config` object and the derived `capabilities` object for all subsequent steps. The `capabilities` fields are: `has_tests`, `test_commands`, `has_ci`, `ci_quarantined`, `ci_quarantine_reason`, `ci_quarantined_since`, `has_deploy`, `deploy_method`, `has_database`, `has_docker`, `project_type`, `repo`, `default_branch`, `migration_dir`, `phase_executor_table`. Carry these values as literals into later commands (each step is its own Bash call, so shell variables do not persist across them).

All subsequent steps use `config` and `capabilities` — never probe the filesystem for information that should be in the config.
</config-loading>

<model-routing-resolve>
Resolve model routing (optional, fail-open) right after `<config-loading>`, before any subagent dispatch. For each role this skill dispatches (`analysis`, `review`, `implementation`), resolve the configured model:
```bash
python3 hooks/model_routing_lib.py resolve \
  --workspace .rawgentic_workspace.json --project <name> --role <analysis|review|implementation>
```
Run once per role (three invocations total). Exit is always 0; stdout is a model name or `inherit`. If `hooks/model_routing_lib.py` is missing (e.g. a stale plugin cache), the invocation may exit non-zero — treat that, and any non-zero/absent output, as `inherit`. Carry each resolved value as a literal into later steps (fresh-shell rule). When a value is `inherit`, dispatch that role's subagents with NO `model:` parameter (session model). Otherwise pass `model: <value>` on every Agent dispatch for that role. A stderr warning is advisory — never treat it as failure.

For each role also resolve its effort tier with a second invocation appending `--effort`, printing the effort string or `none`; carry both the model and the effort as literals. Effort is role-wide — it does not scale with any per-task down-route (e.g. `select_impl_model`'s ceiling logic in Step 8, `references/steps.md`, is unaffected). When the resolved effort is `none`, dispatch exactly as today. When it is non-`none`: the Agent tool has no per-invocation effort parameter, so effort is carried dual-path — (a) pass it where the dispatch layer supports effort (the Workflow tool's `agent(prompt, {effort: <value>})` option, or a Codex dispatch's reasoning-effort flag), and (b) always record it in the dispatch's session-note/audit line (e.g. `dispatch <role>: model <model>, effort <effort>`) so the resolved tier stays observable where per-invocation delivery is impossible.

**Bundled agent dispatch (#164).** The plugin ships two subagent definitions, auto-discovered from the plugin-root `agents/` directory and namespaced on install:
- `rawgentic:rawgentic-implementer` — implementation-task agent (`isolation: worktree`; mutating work runs in an isolated git worktree)
- `rawgentic:rawgentic-reviewer` — quality-gate review agent (read-heavy tools only: Read/Grep/Glob/Bash — no Write/Edit)

Both declare `model: inherit` because routing is per-project config a static definition cannot read: dispatch by passing `subagent_type` plus the resolved role model as the per-invocation `model:` parameter, which OVERRIDES the definition's frontmatter (documented resolution order: env var > per-invocation param > frontmatter > session model). Every per-step dispatch annotation in `references/steps.md` means exactly this contract — `review`-role dispatches use `rawgentic:rawgentic-reviewer`, `implementation`-role dispatches use `rawgentic:rawgentic-implementer`, `analysis`-role dispatches stay generic (no bundled analysis agent). Never-Haiku is enforced twice: in the definitions themselves and by the `select_impl_model` floor in Step 8 (`references/steps.md`). **Graceful fallback (AC4):** when the Step 2 `probe-parallelism` result is `serial-only` (worktree isolation unavailable — e.g. not a git repo), dispatch the same agent types WITHOUT relying on isolation and execute strictly serially; if the agent type itself is unavailable (stale cache, non-plugin install), fall back to the generic inline-prompt dispatch with the same brief — the routed model contract is unchanged in both fallbacks.

**Canonical DISPATCH audit line (#330).** The lowercase start-time line above stays as-is (observability only, never parsed). At the point each dispatch decision COMPLETES — or the orchestrator declares it dead/abandoned — ALSO append one uppercase canonical line carrying the issue number and all six schema fields, fixed key order, single-space-separated, one line:
```
DISPATCH issue=<n> role=<review|implementation|analysis|other> type=<subagent_type> model=<model|null> effort=<effort|null> outcome=<ok|error|retried|dead> resolution=<primary|fallback|generic>
```
Canonical regex (assembly's scoped grep + validator):
```
^DISPATCH issue=(\d+) role=(review|implementation|analysis|other) type=([A-Za-z0-9_.:/-]+) model=(null|[A-Za-z0-9_.:/-]+) effort=(null|[A-Za-z0-9_.:/-]+) outcome=(ok|error|retried|dead) resolution=(primary|fallback|generic)$
```
Emission rules:
- One line per SUBAGENT INVOCATION dispatched (not per attempt) — a multi-reviewer gate emits one line per reviewer (WF2 Step 8a's single accumulated wave of two reviewers = two lines; Step 11's two agents = two lines, #492).
- Write each line flush-left at column 0 as its own physical line — never inside a list item, blockquote, or fenced code block (the assembler greps `^DISPATCH` anchored to line start; an indented or bulleted line is rescued only into the MALFORMED count, never into `dispatches[]`).
- Retry semantics: a single retry of the SAME task/invocation is ONE line — retried-then-succeeded → `outcome=retried`; retried-and-still-failed → `outcome=error` — regardless of any model escalation on the retry. Two lines are written only when the workflow abandons one dispatch PATH for a different one (e.g. delegation abandoned for inline work): the abandoned path's terminal line plus the new path's line.
- A hung/vacuous dispatch abandoned by the orchestrator → `outcome=dead`. A dispatch that errors into a failure handler or a suspend still gets its canonical line (`outcome=error` or `dead`) BEFORE the handler/suspend proceeds — the failed dispatch is exactly the entry the audit most needs.
- `type`/`model`/`effort` values are stable slugs matching `[A-Za-z0-9_.:/-]+` (no spaces, no commas). Write the literal `null` when the role resolved `inherit` (model) or `none` (effort) — never an empty string or "unknown".
- Generic inline-prompt dispatches (no bundled agent type ran) use the stable `subagent_type` token `generic-<role>` (e.g. `generic-analysis`) and carry `resolution=generic`.
- `issue=<n>` is this run's issue number (scoping key — assembly greps the whole session-notes file for `^DISPATCH issue=<n> `). `DISPATCH` is uppercase so it can never collide with the lowercase start-time prose line.

Resolution decision table (maps the dispatch ladder to #329's vocab):

| Dispatch path | resolution |
|---|---|
| Named agent type ran worktree-isolated | `primary` |
| Named agent type ran WITHOUT isolation (`serial-only` degradation) | `primary` — the NAMED type still ran; `fallback` means a SUBSTITUTE type ran, which did not happen |
| Named agent type unavailable AND no bundled substitute tier is declared (or the substitute also fails to resolve) → generic inline-prompt dispatch | `generic` (`subagent_type` = `generic-<role>`) |
| A bundled SUBSTITUTE agent type ran in place of an unavailable named type | `fallback` — no WF2 producer today; WF3 Step 9's declared per-slot chain (#331) produces it at tier 2 |

**Seat fallback chains + circuit breaker (#417).** Each seat's model is a config-declared chain (the routing table's `primary` + `chain[]`, e.g. the interim `review` chain fable → sol → sonnet, #426), tried in order on an AVAILABILITY failure; the skip is **chain-aware** — it drops any entry that would violate the artifact's cross-model invariant, never blindly the literal next entry. **A chain that exhausts its eligible entries is a handled hard failure, never a silent downgrade** — the run surfaces it (the ERROR protocol), it does not quietly proceed on an unrouted model.

**Concurrency ceiling (#417).** Keep **≤ 3 concurrent Claude subagents** (the standing cap); when the driver itself is dispatching Claude work alongside them, reserve one slot for the driver → an **effective working ceiling of 2**. This is a PROSE rule — no programmatic clamp exists — so honor it when fanning out (Step 8a's two reviewers and Step 11's two agents (#492) sit within the cap; a cross-engine candidate on the codex/zhipu pool consumes no Claude slot). `queued_ms` on an Observation records any queue wait so a stall is diagnosable.

**Driver seat — guidance, not enforcement (#417).** opus-4-8 is the recommended session/orchestrator model: the strong-model-on-top reliability floor (weak-model-on-top collapses; the role is unbenchmarked until the driver-bench, #430, reports). This is GUIDANCE only — the harness owns the session model; this block cannot set it, and nothing here fails a run whose session model differs.
</model-routing-resolve>

<headless-mode>
When `additionalContext` contains "HEADLESS MODE active", you operate without a terminal
user: the QUESTION (post→label→suspend→exit), ERROR, rich-checkpoint, and fresh-session
resume protocols live in `references/headless.md`. **Read that file in full before acting on
any of the per-step headless annotations in references/steps.md.** When NOT in headless mode, ignore them and behave
normally (STOP and wait for terminal input at each interaction point).

**Headless is PR-terminal — no remote ops.** A headless run's job ends at PR
creation: no merge, no deploy, no outbound SSH to remote hosts. Step 14 (merge +
deploy) and Step 15 (post-deploy) are skipped entirely; Step 2's live-environment
probe does no SSH (local exploration only); CI handles deployment when a human
merges the PR. This is also enforced at the hook layer — `wal-guard` blocks any
`ssh`/`scp`/`rsync`/`sftp` invocation in headless mode regardless of step (set
`headlessAllowSSH: true` on the project's `.rawgentic_workspace.json` entry to
override), so even an ad-hoc SSH that a step forgot to annotate is denied.
</headless-mode>

<error-protocol>
When a step hits an unrecoverable blocker (a base mismatch, an exhausted loop-back
budget, a fail-closed parse/security finding the user must resolve), post a legible
blocker and STOP — never silently continue. The mechanics are mode-specific (#232):

- **Interactive (default):** post a **blocker comment** to the issue describing what
  went wrong and exactly what the user must do to unblock, write the error state to
  session notes, then STOP and tell the user. This IS "a blocker posted to the issue
  via the ERROR protocol" — it satisfies the `/goal` guard's escape disjunct with **no
  label**: `rawgentic:ai-error` is a headless-orchestrator signal, not a requirement of
  the interactive protocol, so do NOT add it interactively.
- **Headless:** run the full protocol in `references/headless.md` — blocker comment +
  create-and-add the `rawgentic:ai-error` label + exit (the orchestrator watches that
  label). Every per-step **headless ERROR** annotation resolves to that protocol.

Either way the blocker is *posted*, so the goal guard clears honestly instead of the
run hanging on an unsatisfiable "PR open with green CI".
</error-protocol>

<termination-rule>
WF2 ALWAYS terminates after the completion summary. Do NOT suggest "shall I create another issue?" or restart WF2 for the same issue. WF2 terminates ONLY after the completion-gate passes. All steps must have markers in session notes.
</termination-rule>

<loop-back-budget>
Track all design loop-backs across the workflow. There are **five** sources (the
canonical caps live in `plan_lib._LOOPBACK_SOURCE_MAX`):
- Step 4 -> Step 3: max 2 iterations (MAX_DESIGN_LOOPBACK_ITERATIONS, source `design`)
- Step 4 in-gate: max 2 iterations (MAX_SPEC_TIGHTEN_LOOPBACK, source `spec_tighten`) —
  the #223 spec-tightening cheap path: amend + one incremental verifier, NO Step-3
  return; folded from finding `Loopback-class` tags via `plan_lib.classify_loopback_source`
- Step 8 -> Step 3: max 1 iteration (MAX_TDD_DESIGN_LOOPBACK, source `tdd`)
- Step 8a -> Step 3: max 1 iteration (MAX_REVIEW_DESIGN_LOOPBACK_STEP_8A, source `review_design`)
- Step 11 -> Step 3: max 1 iteration (MAX_REVIEW_DESIGN_LOOPBACK, source `review`)

Global cap: GLOBAL_LOOPBACK_BUDGET = 3 — this binds BEFORE the per-source caps (which
sum to 7), so the workflow loops back at most 3 times total. Spec-tightening passes
share this global budget: two cheap passes can starve a later design loop-back — an
accepted, pinned trade-off (worst case equals today's escalate-to-user).
`plan_lib.consume_loopback`
enforces both the per-source and the global cap; call it and act on its `(ok, state)`
return rather than pre-checking the in-context mirror.
If the global cap is reached, STOP and escalate to user with a full summary of all loop-back triggers. **[Headless: ERROR — post error comment with full loop-back summary, add rawgentic:ai-error label, exit.]**

Track loop-back state (mirror of the canonical counters file — one var per source):
design_loopback_count = 0
spec_tighten_loopback_count = 0
tdd_loopback_used = false
review_loopback_used = false
review_design_loopback_used = false
global_loopback_total = 0

**Source of truth:** once it exists, `claude_docs/.wf2-state/<issue>/loopback_counters.json` (written via `plan_lib.consume_loopback`) is canonical for all *successfully persisted* counts — it survives context compaction, fresh headless sessions, and worktrees. The in-context variables above are a convenience mirror: on resume, initialize them from the file when it is present, otherwise from the defaults above (a missing file means "no loop-backs consumed yet," not an error). Do not write the in-context values back over a more-advanced file. If a `consume_loopback` call increments the in-context counter but fails to persist, treat that as a blocker — reconcile or STOP rather than blindly trusting either side, since a stale file would silently restore spent budget.
</loop-back-budget>

<ambiguity-circuit-breaker>
Active at ALL quality gates (Steps 4, 6, 9, 11, 15). Triggers when:
- Any finding has ambiguity_flag == "ambiguous"
- Two or more findings conflict (contradictory recommendations)
- A finding requires judgment not captured in the GitHub issue

When triggered: STOP the workflow at the current step. Present ALL problematic findings to the user. Wait for resolution. Do NOT auto-apply unambiguous findings separately -- the full set is applied together after resolution. **[Headless: QUESTION — post comment with all ambiguous/conflicting findings and resolution options, suspend.]**
</ambiguity-circuit-breaker>

<review-pipelining>
Review waves overlap the orchestrator's next drafting work — never an idle wait (#488;
epic #475 profiling put review-wait at ~20% of per-child wall-clock, much of it the
orchestrator idle-blocked). The canonical directive: after dispatching any review wave
(Step 4 design critique, Step 8a per-task, Step 11 pre-PR), immediately draft the next
phase's non-committing artifact instead of idle-waiting, then reconcile the wave's
findings on return. Non-committing artifacts: the implementation plan, the next task's
tests, the PR body, version/changelog edits — working-tree drafts that stay out of git
history until the gate verdict lands. The boundary is hard: committing, branching,
pushing, and every gate verdict still WAIT for the wave to return — the pipeline
reclaims only the idle time around a gate, never the gate itself — no gate is skipped
and no verdict is pre-empted. If the wave's findings invalidate a drafted artifact,
revise or discard the draft: a gate finding always wins over a stale draft.
</review-pipelining>

<test-run-discipline>
Full-suite runs are the expensive gate, not the iteration loop (#489; the epic #475
profile measured ~5-6 full runs per child where 2 carry all the evidence). The canonical
directive: the FULL suite runs exactly twice per run — once at Step 2 to record the
baseline, once at Step 9 as the final regression gate; during task iteration (Step 8
red→green→refactor) run the SCOPED suite for the area under change. The "no regressions"
claim stays gated on the Step 9 full-suite run diffed against the recorded baseline —
a scoped run never substitutes for the final full-suite gate. Scoped-path convention:
mirror the changed area into the test tree — `hooks/foo.py` → `tests/hooks/`,
`phase_executor/src/...` → `tests/phase_executor/`, skill/doc prose → the guard file
that pins it (e.g. `tests/test_wf2_clarity.py`); when no mirror exists, the nearest
enclosing test directory is the scope. Exactly-twice admits only evidence-driven
exceptions, never habitual re-runs: (a) Step 12's pre-PR gate re-runs the full suite
ONLY when a commit landed after the Step 9 run touching code or a test-pinned surface —
otherwise it consumes the Step 9 result as its evidence; (b) a baseline discovered
invalid (wrong base, foreign checkout content) is re-recorded with a fresh full run.
</test-run-discipline>

<probe-before-design>
Design loop-backs are the fattest variable cost in a WF2 run, and the #467 post-mortem
traced two of them to spike claims that tested a PROXY composition instead of the real
spawn path (#490). The canonical directive: before the design commits to any load-bearing
platform/API behavior (a spawn model, a syscall, a CLI flag, a git plumbing verb), run a
SHORT live probe of the EXACT invocation the design will ship — never a proxy composition —
and cite the probe's real result in the `platform_apis:` feasibility block; a
`verified via spike` claim must reference the actual shipped invocation. A five-minute
probe at Step 3 is cheaper than the ~25-minute design loop-back it prevents. The #226
precedent rule is untouched: an already-precedented exact call site still needs no block —
this directive binds only where a spike is the evidence.
</probe-before-design>

<review-lens-routing>
Review waves are model-tiered per lens (#491; the epic #475 profile measured ~9 opus
reviewers per child where the mechanical lenses never needed the strong model). When
dispatching review-role subagents at Step 4, Step 8a, and Step 11, select the model per
LENS via `select_review_lens_model`: the security lens is pinned to the resolved
review model, and the mechanical, ac_completeness, test_coverage, and bug_logic lenses
ride the fast tier (default sonnet; per-project tunable via `modelRouting.reviewLenses`;
the function lives in `hooks/model_routing_lib.py`, CLI `resolve --role review --lens <lens>`).
The pins are hard: a `reviewLenses` override can never downgrade the security lens (the
function ignores it with a warning), and never-Haiku holds on every path — haiku floors
to sonnet inside the function, and an `inherit` resolution on a Haiku session dispatches
`model: sonnet` at the site (the Step 8 delegation guard's rule). Lens map: Step 4
self-review dispatch → security; Step 8a Reviewer 1 → mechanical, Reviewer 2
(silent-failure hunt) → security; Step 11 Reviewer 1 → mechanical + bug_logic (fast
tier), Reviewer 2 → architecture + security (strong; #492).
</review-lens-routing>

<step-tracking>
Session notes (`claude_docs/session_notes.md`) are an **append-only, cumulative audit
trail**: every write is an **APPEND** (`>>`), NEVER an overwrite — an earlier step's
entry must still be present at the end of the run (#50). Wherever anything in this skill
(including the `references/` files) says to "log", "record", "write", "update", or
"document" session notes, it means **APPEND** — never overwrite or replace an earlier entry.

As a step runs, APPEND cumulative `####` sub-headers (progress, evidence, decisions) under
that step's section; then APPEND the step's marker **last**:
`### WF2 Step X: <Name> — DONE (#<issue>: <key detail>)`
The `— DONE` marker is load-bearing for the resumption protocol. This enables workflow
resumption if context is lost.

On every marker line the run key is read from the marker type's canonical slot —
concurrent runs share one notes file and un-keyed markers are mechanically
un-attributable (#341). The key is read ONLY from that marker type's slot (below); a
`#N` in a free-text tail is never the key, and a marker whose slot holds no `#<n>` is
legacy/un-keyed (section-header fallback, attribution-ambiguous, never an error).
The fallback exists for PRE-#341 notes and stale-cache (≤3.27) emitters ONLY: a run
executing THIS contract that emits a prescribed marker without its slot key has
violated the contract — fix the emission, do not lean on the fallback.

| Marker type | Canonical key slot |
| --- | --- |
| DONE-parens (`— DONE (…)`) | first token inside the parens: `— DONE (#<issue>: <detail>)` |
| enum-parens with trailing detail (Step 1b) | first token of the trailing detail: `(set\|deferred\|skipped): #<issue> — <detail>` |
| bare-enum, no trailing detail (Step 12 design artifact) | post-label, pre-enum: `— design artifact #<issue> (updated\|skipped)` |
| label-colon (Step 11 adversarial diff) | immediately after the colon: `Adversarial Diff Review: #<issue> findings_present …` |
| parens-state (Step 4/6 adversarial incl. the discarded variant, Step 8 delegation) | key leads inside the parens: `(#<issue>, invoked\|skipped)` / `(#<issue>, discarded: <reason>)` / `whole-issue-delegation (#<issue>):` |
| hook-emitted promotion note (`format_promotion_note`) | key leads the detail after the task colon: `— Promoted <id>: #<issue>: <detail>` |

This slot table is AUTHORITATIVE: every prescribed marker literal in references/ must
conform to its type's slot, and when a literal and this table diverge the table wins.
Emitters: the key MUST land in the type's slot — a key anywhere else on the line is
ignored by consumers. Deliberately un-keyed informational markers (path-estimate,
path-estimate refresh, trivial-work suggestion, headless Step 14/15 skip) are declared
deferrals, not misses — they are print-and-continue advisories no consumer attributes.
Step-entry state (#480, hook-emitted since #499): the PostToolUse hook (`hooks/step_state_post.py`) derives the now-pointer automatically from step DONE markers and signature commands — no per-step action required. The manual `python3 hooks/step_state.py write --project <project> --workflow wf2 --step <N> --step-title "<step name>" --issue <issue number> --session-id "$CLAUDE_CODE_SESSION_ID"` call is OPTIONAL belt-and-suspenders for entry-time precision on prose-only steps. Fail-open either way (never gates; any failure is ignored and the step proceeds).
</step-tracking>

<references>
Progressive disclosure. This spine carries the always-run protocols and a
one-line-per-step overview; the full detail lives in per-skill reference files,
read on demand by this contract:
- `references/steps.md` — the full per-step instructions. Read §N (the step's
  section) before executing Step N. It also holds the `<small-standard-lane>`,
  `<trivial-work-check>`, and `<learning-config>` blocks.
- `references/state-and-resume.md` — the `<state-files>` and
  `<resumption-protocol>` contracts. Read before ANY resume, or before reading
  or writing a session-scoped state file or the local (git-excluded) review-state pointer.
- `references/run-record.md` — the run-record schema. Read before the Step 16
  run-record assembly.
- `references/headless.md` — the headless interaction protocols. Read IN FULL
  when `additionalContext` has "HEADLESS MODE active" (see `<headless-mode>`).
- `references/whole-issue-delegation.md` — the whole-issue delegated-build
  brief, receipt schema, and validation contract. Read before using that
  Step 8 sub-mode.
</references>

## Steps

One line per step; read `references/steps.md` §N before executing Step N. The
ordered spine is in `<happy-path>`; MANDATORY vs conditional is in
`<mandatory-steps>`.

- **Step 1 — Receive issue & detect capabilities.** Load config per `<config-loading>`, fetch/validate the issue, surface capabilities + the `/goal` guard, probe branch protection. (read references/steps.md §1 before executing)
- **Step 1b — AC-derived goal guard (`/goal`).** Build the goal text via `plan_lib.build_goal_text` and fold it into Step 1's confirmation; optional, never blocks. (read references/steps.md §1b before executing)
- **Step 2 — Analyze codebase & classify complexity.** Map-first then parallel gather then synthesize; set the authoritative complexity, small-standard-lane eligibility, trivial-work check, and the parallelism probe. (read references/steps.md §2 before executing)
- **Step 3 — Design solution architecture.** Produce the design doc incl. the mandatory `platform_apis:` feasibility declaration (#226), probing load-bearing platform APIs live first per `<probe-before-design>` (#490); optional cross-model peer consult, blind both ways; collapses to a brief note in the lane. (read references/steps.md §3 before executing)
- **Step 4 — Quality gate: design critique.** the in-repo quality-bar rubric for all lanes (#190 retired the 3-judge panel; #205 replaced the reflexion dependency) + the mechanical platform-feasibility gate (`plan_lib.assert_feasibility_declared`, #226) + opt-in adversarial-on-design on the full spine; the breaker runs EXACTLY once. (read references/steps.md §4 before executing)
- **Step 5 — Create implementation plan.** Decompose into risk-tagged tasks (`riskLevel`), parallel-group/files validation, verification strategy; checklist form in the lane. (read references/steps.md §5 before executing)
- **Step 6 — Quality gate: plan drift (conditional).** The quality-bar rubric + opt-in adversarial-on-plan; skipped when time-critical or in the lane. (read references/steps.md §6 before executing)
- **Step 7 — Create feature branch.** Branch from a freshly-fetched `origin/<default>` and assert the base; never pull into the current checkout. (read references/steps.md §7 before executing)
- **Step 8 — Implementation.** Execute the plan task-by-task (TDD/implement-verify), commit per task; optional per-task or whole-issue delegation, mid-flight risk promotion + a mid-flight platform-feasibility check for gate-bypassing changes (#226). (read references/steps.md §8 before executing)
- **Step 8a — Per-task review (conditional).** Fires when any `riskLevel: high` task exists: ONE accumulated wave of 2 reviewers over the set of high-risk commits (#492), deferrals persisted, review log (one entry per covered task) + review-state pointer (local, git-excluded) updated. (read references/steps.md §8a before executing)
- **Step 9 — Quality gate: implementation drift.** Alignment self-review (Part A) + evidence (Part B); P15 review-coverage assertion; runtime-surface feasibility — spike OR a deferred-to-target naming the likeliest-wrong claim (#226); lane runs evidence-only + the lane cross-check. (read references/steps.md §9 before executing)
- **Step 10 — Conditional memorization (background).** Runs in parallel with Step 11; never blocks. (read references/steps.md §10 before executing)
- **Step 11 — Pre-PR code review.** 2-agent review (≥1 in the lane; #492) + opt-in adversarial diff review; severity-banded confidence, deferred-resolution exit gate. NON-NEGOTIABLE. (read references/steps.md §11 before executing)
- **Step 11.5 — Tool-based security scan (pre-PR gate).** `hooks/security_scan.py` for secrets/SCA/SAST/IaC; fail-closed on real findings; visible skips, never a silent pass. (read references/steps.md §11.5 before executing)
- **Step 12 — Create PR & push.** Join Steps 10+11, update README/docs, review-state gate, open the PR with the templated body. (read references/steps.md §12 before executing)
- **Step 13 — CI verification (conditional).** Monitor/fix CI when `has_ci`; quarantine handled as a visible non-gate with a trust guard. (read references/steps.md §13 before executing)
- **Step 14 — Merge & deploy (conditional).** Only on user-requested merge; SKIPPED entirely in headless mode; pre-merge review-state + quarantine×protection contradiction checks. (read references/steps.md §14 before executing)
- **Step 15 — Post-deploy verification (conditional).** Only if a deployment happened; skipped in headless mode. (read references/steps.md §15 before executing)
- **Step 16 — Completion summary + run-record.** WF2 terminates here (stub below). (read references/steps.md §16 before executing)

## Step 16: Workflow Completion Summary

WF2 terminates here. Assemble the structured run-record from the workflow's
data and drive the summary through `hooks/work_summary.py` — its stdout IS the
completion summary and it appends the record to the Tier-2 telemetry store. The
full schema, field-presence rules, and per-gate `status` conventions live in
`references/run-record.md` — read it before assembling. Render + persist with
`python3 hooks/work_summary.py summarize --record-file /tmp/wf2-run-record.json --project-root <activeProject.path>`
(rc 0 = persisted; rc 1 = summary rendered but the record failed validation — a
telemetry gap; rc 2 = usage error). Full detail in `references/steps.md` §16.

<completion-gate>
Before declaring WF2 complete, verify the following. Items marked (conditional) only apply if the capability exists:

1. [ ] Step markers logged for ALL executed steps in session notes
2. [ ] Final step output (completion summary) presented to user
3. [ ] Session notes updated with completion summary
4. [ ] PR URL documented
5. [ ] All commits pushed
6. [ ] (conditional: has_ci) CI passed — **OR** (`ci_quarantined`) the quarantine notice (reason + run status, "not gating") is recorded in session notes + PR body. A legible skip, never a silent one; a quarantined run is never reported as green.
7. [ ] (conditional: has_deploy, NOT headless) Deployment verified or manual deploy confirmed — auto-satisfied in headless mode, where Steps 14/15 are skipped (PR is the terminal deliverable)
8. [ ] (conditional: architecture changed) CLAUDE.md updated
9. [ ] All Critical/High code review findings resolved
10. [ ] (conditional: adversarialReview opt-in for implement-feature) A "### WF2 Step 11 — Adversarial Diff Review:" 4-state marker exists in session notes — opt-in ⇒ marker, unconditionally (skipped (<reason>) is a legitimate marker; silent omission is not; no gate-time diff recompute — a post-merge recompute sees an empty diff and would waive the check exactly in the merge path)
11. [ ] Security scan (Step 11.5) ran; all blocking findings resolved (or, if no scanners were installed, the skips are recorded in session notes + PR body)
12. [ ] Completion summary rendered via `work_summary.py` (Step 16) and the run-record persisted (rc 0) — or, if validation failed (rc 1), the telemetry gap is recorded in session notes
13. [ ] (conditional: any `plan_lib.deferred_tasks(tasks)` — verification deferred to target, #138) Every deferred task is recorded on BOTH surfaces, checked mechanically:
    - `plan_lib.assert_deferrals_recorded(deferred_tasks(tasks), record["verification_deferred"])` returns `ok=True` — each recorded entry carries non-empty `task_id` + `reason` + `local_proxy` + `target_check` (an evidence-less entry fails, so a deferral can't be gate-satisfied without its local proxy), and the plan↔record task ids match exactly (missing/duplicate/foreign ⇒ fail).
    - `plan_lib.assert_pr_body_has_deferred_section(pr_body, deferred_tasks(tasks))` returns `ok=True` — the PR body carries the canonical `## Deferred verification` section.
    Both `ok=True` ⇒ gate satisfied-with-note. Any failure (an **unrecorded** deferral, evidence-less entry, or a missing PR section) ⇒ gate FAILURE — a deferral must never silently vanish into a pass.

If ANY applicable item fails, complete it before declaring "WF2 complete."
</completion-gate>
