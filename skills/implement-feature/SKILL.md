---
name: implement-feature
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
BRANCH_PREFIX_FEATURE = "feat"
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
| 4 | Quality Gate (Design) | Catches design flaws BEFORE implementation. The in-repo quality-bar rubric + the platform-feasibility check (#226) run for all lanes; the full spine adds the opt-in adversarial-on-design + peer consult. |
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
- Step 14 (Merge/Deploy): skip only if user does not request merge — in an unattended run (e.g. an epic-run child) PR creation is the terminal deliverable, so the run never merges or deploys.
- Step 15 (Post-Deploy): skip only if no deployment performed.

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
   - **Exit 0** -> stdout is `{"config": {...}, "capabilities": {...}}`. Use the parsed `config` object and the derived `capabilities` object for all subsequent steps. The `capabilities` fields are: `has_tests`, `test_commands`, `has_ci`, `ci_quarantined`, `ci_quarantine_reason`, `ci_quarantined_since`, `has_deploy`, `deploy_method`, `has_database`, `has_docker`, `project_type`, `repo`, `default_branch`, `migration_dir`. Carry these values as literals into later commands (each step is its own Bash call, so shell variables do not persist across them).

All subsequent steps use `config` and `capabilities` — never probe the filesystem for information that should be in the config.
</config-loading>

<model-routing-resolve>
**Where work runs (D174, the executor retreat — 2026-08-03).** Analysis and implementation run
INLINE in the orchestrating session. There is no dispatch machinery, no seat table, and no
per-phase model routing. Broad read-only gathers MAY fan out harness subagents (Agent tool,
Explore-style) to keep file dumps out of the main window — inline when narrow; judgment by
breadth (D182). Genuinely parallel implementation tasks MAY use Agent-tool worktree subagents;
the default is inline TDD. What is retired is the executor seat, not subagents.

**Cross-model review runs through ONE entry point** — `hooks/review_runner.py` (D179) —
dispatched from a read-only harness subagent so the inline self-review and the cross-model
review run in parallel. The subagent's ONLY job is to run one runner command and report back
the result path plus the exit code; it must not modify project files — its only permitted
write is the runner's declared `--out` result file. Command shapes:

```bash
# Text artifact (design/plan/spec/…): WF2 Step 4, WF5
python3 hooks/review_runner.py review-artifact --artifact <file> --type <design|plan|diff|…> \
  --author-model <your own model id, verbatim> --reviewer <reviewer, below> \
  [--backend gpt|glm] [--reopen-token <token.json>] --out <result.json> --project-root .

# Code diff vs a base ref: WF2 Steps 8a/11, WF3 Step 9
python3 hooks/review_runner.py review-code --base <base ref> --brief <brief.md> \
  --author-model <your model id> --reviewer <reviewer> [--backend gpt|glm] \
  [--reopen-token <token.json>] --out <result.json> --project-root .

# Independent peer proposal: WF13
python3 hooks/review_runner.py consult --artifact <problem.md> \
  --author-model <your model id> --reviewer <reviewer> [--backend gpt|glm] \
  --out <result.json> --project-root .
```

**The `consult` verb is supervision-gated when unattended (#947 Part B AC6).** If this session
is running away/sleeping (`supervision_lib.py nobody-to-ask` exits 0), run the check FIRST —
zero payload construction on a refusal:

```bash
python3 hooks/supervision_route.py consult-check --workspace-root <workspace root> \
  --project-root . --campaign-id <campaign id> --backend <gpt|glm>
```

Exit 0 → permitted; append `--allowed-backends` from the printed JSON's `allowed_backends` to
the `consult` invocation above (so a mid-flight 429 switch cannot land on an ungranted
provider). Exit 1 → refused; skip the dispatch and report the printed `reason` — never egress
anyway. An ATTENDED session skips this check entirely (a human is present to object).

**Reviewer identity is pinned, never inherited.** The current default reviewer id is
**`gpt-5.6-sol`** (single-sourced HERE — a retired id fails loudly at invocation and is updated
on this one line). The alternate backend is `--backend glm` (model `glm-5.2`). The runner
REFUSES author==reviewer and unresolvable identities; pass your own model id as
`--author-model`, verbatim. Exit codes: `0` success (check `diagnostic` in the result JSON) ·
`2` refused (validation/identity/token — no egress) · `3` terminal backend failure ·
`4` empty/invalid backend output. The runner owns transport policy (#857): one bounded
transport retry, org-wide 429 terminal, one permitted backend switch on a per-account 429 —
callers NEVER add their own retry loop around it.

**Actionable vs diagnostic — the reopen choke point (#855).** A review that may open a fix
round needs a reopen token minted FIRST:

```bash
python3 hooks/plan_lib.py review-reopen --state-file claude_docs/.wf2-state/<issue>/loopback_counters.json \
  --source <design|spec_tighten|tdd|review|review_design> --out <token.json> --project-root .
```

The mint itself debits the atomic loop-back budget; exhaustion refuses (exit 3) and the gate
escalates instead of looping. A tokenless run still reviews, but its result carries
`diagnostic: true`, and the disposition step MUST refuse to open a fix round on a diagnostic
result. Transport retries inside one runner invocation never re-debit. A spent or malformed
token refuses outright.

**The vacuous-result gate — subagent results are hypotheses.** Before consuming ANY subagent
result (review or gather):
1. the artifact it claims exists and is non-empty (for the runner: the `--out` file);
2. the shape parses (for the runner: JSON with a `status` field that matches the exit code);
3. freshness: the result's `head_sha`/`input_sha256` still match the current HEAD/artifact —
   a result whose subject moved before disposition is REJECTED and re-run against the new
   state; and any load-bearing claim is spot-verified against the cited file:line.
A dead subagent, an empty file, or a missing status is a FAILED dispatch — never a pass,
never "still running" (#766). Retry a failed review dispatch once; a second failure follows
the ERROR protocol.

**Disposition.** Findings flow to the gate's normal handling: the severity-banded confidence
filter, High-deferral discipline, the ambiguity circuit breaker, and the loop-back budget.
Fix, defer with rationale, or decline with reason — never silently drop. Concurrency courtesy:
keep ≤ 3 concurrent Claude subagents (token burn; a session-limit hit kills all in-flight
agents with vacuous results). A subagent or runner dispatch is never a gate bypass — every
mandatory review gate runs with identical semantics whether a pass ran inline or through
`hooks/review_runner.py`, and a review that may open a fix round carries a reopen token
minted first.
</model-routing-resolve>

<error-protocol>
When a step hits an unrecoverable blocker (a base mismatch, an exhausted loop-back
budget, a fail-closed parse/security finding the user must resolve), post a legible
blocker and STOP — never silently continue. The mechanics are mode-specific (#232):

- **Interactive (default):** post a **blocker comment** to the issue describing what
  went wrong and exactly what the user must do to unblock, write the error state to
  session notes, then STOP and tell the user. This IS "a blocker posted to the issue
  via the ERROR protocol" — it satisfies the `/goal` guard's escape disjunct with **no
  label**: `rawgentic:ai-error` is an unattended-run signal, not a requirement of
  the interactive protocol, so do NOT add it interactively.
- **Unattended (e.g. an epic-run child):** blocker comment + create-and-add the
  `rawgentic:ai-error` label + stop this child (the epic driver watches that label).

Either way the blocker is *posted*, so the goal guard clears honestly instead of the
run hanging on an unsatisfiable "PR open with green CI".

**Unattended waits:** in an unattended run, a step that WAITS for a user decision
auto-resolves conservatively where its own text defines an auto-resolution (WF1-created
issue confirmation, the lane/trivial suggestions); where none is defined, treat the wait
as a blocker via this protocol — never an indefinite wait.
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
If the global cap is reached, STOP and escalate to user with a full summary of all loop-back triggers.

**One carve-out — the Step-4 design gate closes instead (#798).** When the `design` SOURCE cap is
reached (and the global cap is NOT, and the ambiguity breaker returned `clear`), Step 4 closes
budget-exhausted rather than escalating: six consecutive epic-#756 children hit that escalation and
all seven owner answers were identical. The carve-out is deliberately narrow and does NOT
generalize — exhaustion of `spec_tighten`, `tdd`, `review`, or `review_design`, a refusal caused by
the GLOBAL cap (`consume_loopback` tests the source cap first and returns, so a state with both
exhausted must not be read as design-cap-caused), and any ambiguous or conflicting finding all
still STOP and escalate. Mechanics and the exact command: `references/steps.md` §4.

Track loop-back state (mirror of the canonical counters file — one var per source):
design_loopback_count = 0
spec_tighten_loopback_count = 0
tdd_loopback_used = false
review_loopback_used = false
review_design_loopback_used = false
global_loopback_total = 0

**Source of truth:** once it exists, `claude_docs/.wf2-state/<issue>/loopback_counters.json` (written via `plan_lib.consume_loopback`) is canonical for all *successfully persisted* counts — it survives context compaction, fresh sessions, and worktrees. The in-context variables above are a convenience mirror: on resume, initialize them from the file when it is present, otherwise from the defaults above (a missing file means "no loop-backs consumed yet," not an error). Do not write the in-context values back over a more-advanced file. If a `consume_loopback` call increments the in-context counter but fails to persist, treat that as a blocker — reconcile or STOP rather than blindly trusting either side, since a stale file would silently restore spent budget.
</loop-back-budget>

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

<ambiguity-circuit-breaker>
Active at ALL quality gates (Steps 4, 6, 9, 11, 15). Triggers when:
- Any finding has ambiguity_flag == "ambiguous"
- Two or more findings conflict (contradictory recommendations)
- A finding requires judgment not captured in the GitHub issue

When triggered: STOP the workflow at the current step. Present ALL problematic findings to the user. Wait for resolution. Do NOT auto-apply unambiguous findings separately -- the full set is applied together after resolution.
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
skill/doc prose → the guard file
that pins it (e.g. `tests/test_wf2_clarity.py`); when no mirror exists, the nearest
enclosing test directory is the scope. Exactly-twice admits only evidence-driven
exceptions, never habitual re-runs: (a) Step 12's pre-PR gate re-runs the full suite
ONLY when a commit landed after the Step 9 run touching code or a test-pinned surface —
otherwise it consumes the Step 9 result as its evidence. Prose-only tightening (#527):
when EVERY post-Step-9 commit touches ONLY prose/doc files (`*.md`, `docs/`) plus their
own guard test files under `tests/` (no `hooks/`, no `scripts/`,
no shared behavior code, and no shared test infrastructure — `conftest.py`,
`tests/corpus.py`, cross-file test helpers), the pre-PR gate instead runs the affected guard test files
plus `tests/hooks/test_adversarial_review_registration.py` (the version pin) SCOPED and
consumes the Step 9 full-suite result as the regression evidence — with a session-note
marker naming the scoped set; any code-bearing commit keeps the full re-run (measured
basis: epic #509 lever 2, ~2.4 min × 5/9 children). (b) a baseline discovered
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
Review lenses are BRIEF EMPHASIS, not model routing (#491's per-lens model tiering retired
with the executor — D174; there is no per-lens model selection any more). Each review pass
carries a LENS naming what it hunts: `mechanical` (style, imports, hardcoded credentials,
off-by-one), `bug_logic` (logic errors, race conditions, silent failures), `security` (auth,
injection, traversal, ReDoS — this lens caught the ReDoS + FIFO-DoS on #466), `architecture`
(pattern breaks, missing sibling changes, backward compatibility), plus `ac_completeness`
and `test_coverage` as Step 4/9 emphases. The pairing at every review site: the INLINE
self-review carries the mechanical + bug_logic lenses; the cross-model runner pass carries
the architecture + security lenses — the security lens is never the one dropped (#492), so
in the small-standard lane's single-reviewer form the one reviewer carries it. Lens map:
Step 4 self-review → security; Step 8a Reviewer 1 → mechanical, Reviewer 2 (silent-failure
hunt) → security; Step 11 Reviewer 1 → mechanical + bug_logic, Reviewer 2 → architecture +
security (#492). State each pass's lens in its brief as emphasis only — never a verdict
instruction (`<review-severity>`).

**Coverage is asserted, not assumed (#1002).** Step 11 writes its dispatch manifest to
`claude_docs/.wf2-state/<issue>/step11_dispatch.json`, runs
`python3 hooks/plan_lib.py assert-lens-coverage --manifest <that> --issue <n> --project-root .`,
and dispatches ONLY on exit 0 — a non-zero exit blocks the dispatch, because a check that runs
after dispatch prevents nothing. Coverage is over the UNION of the wave's briefs, so the
two-reviewer split above passes and a single-reviewer wave must carry all four. The matrix of what
each task class runs, the guard's full contract, and the `disposable` definition of done live in
`references/class-gate-matrix.md`.
</review-lens-routing>

<early-smoke-install>
A deploy-bearing child that defers ALL live verification to the final Step-14/15 deploy
surfaces a crash-on-boot or an environment/port clash hours later at the live cutover, when
it was a 2-minute fix right after the commit that introduced it (#494; the 3dstories-fleet
timing post-mortem's move #2 — a Config crash and a mempalace port clash were exactly such
finds). The canonical directive: on a deploy-bearing project (`capabilities.has_deploy`),
after the first runnable commit boots something, run a cheap live smoke-install/boot check
(install / start / health) before continuing implementation — crash-on-boot and
environment/port clashes surface while they are still a 2-minute fix. The directive is
capability-gated: code-only projects (`has_deploy == false`, e.g. rawgentic itself) are
unaffected — the directive never runs there. The early smoke is distinct from and additional
to the mandatory Step-15 post-deploy smoketest — Step 15 is never weakened or replaced by
it: an early boot check proves the commit starts, not that the deployed app works.
</early-smoke-install>

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
path-estimate refresh, trivial-work suggestion, unattended Step 14/15 skip) are declared
deferrals, not misses — they are print-and-continue advisories no consumer attributes.
Step-entry state (#480, hook-emitted since #499): the PostToolUse hook (`hooks/step_state_post.py`) stamps later steps from step DONE markers and signature commands — but ONLY once the step-state pointer already names this session, which a DONE marker or an explicit write creates. It does NOT stamp unaided: a run that creates no pointer contributes no timing at all (#976 measured exactly that). The manual `python3 hooks/step_state.py write --project <project> --workflow wf2 --step <N> --step-title "<step name>" --issue <issue number> --session-id "$CLAUDE_CODE_SESSION_ID"` call is MANDATORY once per run at the branch cut (see that step) and useful belt-and-suspenders for entry-time precision on prose-only steps. Fail-open either way (never gates; any failure is ignored and the step proceeds).
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
  or writing a session-scoped state file.
- `references/run-record.md` — the run-record schema. Read before the Step 16
  run-record assembly.
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
- **Step 4 — Quality gate: design critique.** the in-repo quality-bar rubric for all lanes (#190 retired the 3-judge panel; #205 replaced the reflexion dependency) + the platform-feasibility check (#226) + opt-in adversarial-on-design (via the review runner) on the full spine; the breaker runs EXACTLY once. (read references/steps.md §4 before executing)
- **Step 5 — Create implementation plan.** Decompose into risk-tagged tasks (`riskLevel`), optional parallel-group/files declarations, verification strategy; checklist form in the lane. (read references/steps.md §5 before executing)
- **Step 6 — Quality gate: plan drift (conditional).** The quality-bar rubric + opt-in adversarial-on-plan; skipped when time-critical or in the lane. (read references/steps.md §6 before executing)
- **Step 7 — Create feature branch.** Branch from a freshly-fetched `origin/<default>` and assert the base; never pull into the current checkout. (read references/steps.md §7 before executing)
- **Step 8 — Implementation.** Execute the plan task-by-task (TDD/implement-verify), commit per task; early smoke-install after the first runnable commit on deploy-bearing projects (`<early-smoke-install>`, #494); inline TDD with optional worktree-subagent parallelism or whole-issue delegation, mid-flight risk promotion + a mid-flight platform-feasibility check for gate-bypassing changes (#226). (read references/steps.md §8 before executing)
- **Step 8a — Per-task review (conditional).** Fires when any `riskLevel: high` task exists: ONE accumulated wave of 2 review passes (inline + runner) over the set of high-risk commits (#492), deferrals persisted, one coverage marker per covered task. (read references/steps.md §8a before executing)
- **Step 9 — Quality gate: implementation drift.** Alignment self-review (Part A) + evidence (Part B); P15 review-coverage check; runtime-surface feasibility — spike OR a deferred-to-target naming the likeliest-wrong claim (#226); lane runs evidence-only + the lane cross-check. (read references/steps.md §9 before executing)
- **Step 10 — Conditional memorization (background).** Runs in parallel with Step 11; never blocks. (read references/steps.md §10 before executing)
- **Step 11 — Pre-PR code review.** 2-agent review (≥1 in the lane; #492) + opt-in adversarial diff review; severity-banded confidence, deferred-resolution exit gate. NON-NEGOTIABLE. (read references/steps.md §11 before executing)
- **Step 11.5 — Tool-based security scan (pre-PR gate).** `hooks/security_scan.py` for secrets/SCA/SAST/IaC; fail-closed on real findings; visible skips, never a silent pass. (read references/steps.md §11.5 before executing)
- **Step 12 — Create PR & push.** Join Steps 10+11, update README/docs, review-completeness check, open the PR with the templated body. (read references/steps.md §12 before executing)
- **Step 13 — CI verification (conditional).** Monitor/fix CI when `has_ci`; quarantine handled as a visible non-gate with a trust guard. (read references/steps.md §13 before executing)
- **Step 14 — Merge & deploy (conditional).** Only on user-requested merge (unattended runs stop at the PR); pre-merge quarantine×protection contradiction checks. (read references/steps.md §14 before executing)
- **Step 15 — Post-deploy verification (conditional).** Only if a deployment happened. (read references/steps.md §15 before executing)
- **Step 16 — Completion summary + run-record.** WF2 terminates here (stub below). (read references/steps.md §16 before executing)

## Step 16: Workflow Completion Summary

WF2 terminates here. Assemble the structured run-record from the workflow's
data and drive the summary through `hooks/work_summary.py` — its stdout IS the
completion summary and it appends the record to the Tier-2 telemetry store. The
full schema, field-presence rules, and per-gate `status` conventions live in
`references/run-record.md` — read it before assembling. Render + persist with
`python3 hooks/work_summary.py summarize --record-file /tmp/wf2-run-record-<issue>-<session-id>.json --project-root <activeProject.path>`
(rc 0 = persisted; rc 1 = summary rendered but the record failed validation — a
telemetry gap; rc 2 = usage error). Full detail in `references/steps.md` §16.

<completion-gate>
Before declaring WF2 complete, verify the following. Items marked (conditional) only apply if the capability exists:

1. [ ] Step markers logged for ALL executed steps in session notes, and the completion summary presented to the user + recorded in session notes
2. [ ] PR URL documented; all commits pushed
3. [ ] (conditional: has_ci) CI passed — **OR** (`ci_quarantined`) the quarantine notice (reason + run status, "not gating") is recorded in session notes + PR body. A legible skip, never a silent one; a quarantined run is never reported as green.
4. [ ] (conditional: has_deploy) Deployment verified or manual deploy confirmed — auto-satisfied in an unattended run, where Steps 14/15 are skipped (PR is the terminal deliverable)
5. [ ] All Critical/High code review findings resolved
6. [ ] (conditional: adversarialReview opt-in for implement-feature) A "### WF2 Step 11 — Adversarial Diff Review:" 4-state marker exists in session notes — opt-in ⇒ marker, unconditionally (skipped (<reason>) is a legitimate marker; silent omission is not; no gate-time diff recompute — a post-merge recompute sees an empty diff and would waive the check exactly in the merge path)
7. [ ] Security scan (Step 11.5) ran; all blocking findings resolved (or, if no scanners were installed, the skips are recorded in session notes + PR body)
8. [ ] Completion summary rendered via `work_summary.py` (Step 16) and the run-record persisted (rc 0) — or, if validation failed (rc 1), the telemetry gap is recorded in session notes
9. [ ] (conditional: any `plan_lib.deferred_tasks(tasks)` — verification deferred to target, #138) Every deferred task is recorded on BOTH surfaces. **RUN the check; do not re-derive it (#796):**
    ```bash
    python3 hooks/plan_lib.py assert-pr-body \
      --plan-file <impl-plan.md> --pr-body-file <pr-body.md> \
      [--record-file <run-record.json>] \
      --project-root .
    ```
    `0` gate holds · `1` gate FAILS (findings on stdout) · `2` caller error. It executes both pure functions — `assert_pr_body_has_deferred_section` (the PR body carries the canonical `## Deferred verification` section) and, when `--record-file` is given, `assert_deferrals_recorded` (each recorded entry carries non-empty `task_id` + `reason` + `local_proxy` + `target_check`, and the plan↔record task ids match exactly: missing/duplicate/foreign ⇒ fail). Both were previously invoked NOWHERE in production, which is why the #781 H1 slip fired after merge.
    One refusal is a caller error rather than a gate result, deliberately: a plan that parses to **no tasks at all** is rc 2, never a vacuous pass (absence of tasks is a wrong path or a malformed plan, not a plan with nothing deferred).
    rc 0 ⇒ gate satisfied-with-note. Any failure (an **unrecorded** deferral, evidence-less entry, or a missing PR section) ⇒ gate FAILURE — a deferral must never silently vanish into a pass.

If ANY applicable item fails, complete it before declaring "WF2 complete."
</completion-gate>
