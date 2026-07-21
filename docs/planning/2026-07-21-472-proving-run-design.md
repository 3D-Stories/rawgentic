# #472 proving run — design r6 (W9, epic #475) — NARROWED per owner decompose decision

**Status:** r6 for WF2 Step 4 pass 6 · **Author:** WF2 orchestrator session 1bce2405 ·
**Date:** 2026-07-21 · **Scope:** owner decision 2026-07-21 — **DECOMPOSE**. Five design passes
(r1→r5) proved the wired path (merged W1–W8) is NOT end-to-end-ready: the remaining Critical/High
are missing-engine-capability defects, not design-prose defects. #472 therefore ships (1) the
committed **proving-run report** as its primary deliverable (AC4 — the answer to "does the work
actually work?"), (2) the **safe, self-contained code fixes** that stand alone, and (3) a filed
**hardening epic** decomposing the six capability builds. AC2 (live codex cell) and AC3 (live
account-switch/quota cycle) are DEFERRED to the epic children that build their prerequisites.

This is the convergent scope: the six contentious deep-machinery items are removed from #472, so
this design carries only work the five review passes never disputed.

## What #472 ships

### A. Proving-run report (AC4 — primary deliverable)
`docs/planning/2026-07-21-472-proving-run-report.md` (+ rendered `.html` via `render_artifact.py`,
committed, published as an Artifact per the design-doc mandate). Contents:
- **What WAS proven on the wired path:** the run flipped `executorRouting` (analysis/review/build
  → executor) and executed real executor dispatches — 3 Step-2 analysis seats + 5 Step-4 review
  seats (self-review ×5 passes on gpt-5.6-sol cross-model, `requested==actual` verified on every
  one, receipts + schema-v2 Observations in `.rawgentic/runs/wf2-472-1bce2405/routing-audit.jsonl`).
  This is the first sustained real use of the wired dispatch path and it held: cross-model routing,
  the enforcement receipt (author_provider on review seats), and the Observation contract all
  behaved as specified.
- **The six capability gaps that block a full end-to-end proving run** (each with file:line
  evidence from this session's verification), and for each: why it blocks which AC, and the fix
  shape. This inventory IS the proving-run's finding.
- **The safe fixes this PR lands** (Part B) with red→green evidence.
- **A4/F11 disposition:** the SIGKILL-proxy assumption for quota residue remains UNCONFIRMED (a
  genuine usage-limit exit was NOT captured — the capability to detect+reconcile it doesn't exist
  yet; epic child #4/#5). The AC-doc correction records this honestly rather than claiming a
  capture that didn't happen.

### B. Safe self-contained code fixes (real bug fixes to merged code; TDD, never disputed across 5 passes)
- **D1 — supervised codex composition.** `_run_supervised` (executor_routing_lib.py:1309) calls
  `build_command(model, effort=, profile=)`; codex `build_command(model, cwd, *, effort)` has no
  `profile` kwarg → TypeError. Fix: select the composition by engine+`profile.mutating`; mutating
  codex → `build_mutating_command(model, worktree, effort=, containment_root=)`; mismatch →
  `CompositionError`. RED: a `_do_dispatch` mutating-build cell fails TypeError on main.
- **D2 — correlation threading.** `TmuxSupervisor.launch` and `synthetic_observation` hardcode
  `correlation_id=None` (supervisor.py:392-395, 151-160). Fix: additive `correlation_id` param on
  both (default None preserves every existing caller). Makes a supervised Observation bindable by
  reconcile.
- **D3 — supervised audit append.** The supervised branch appends its receipt but never appends the
  collected Observation (executor_routing_lib.py:921-978). Fix: append the Observation to the audit
  for every terminal state that HAS one (completed / completed_with_residue / timed_out), stamped
  with `dispatched_lane` + correlation — mirroring the sync path (executor_routing_lib.py:698-701).
  (`exited_no_sentinel` legitimately has no Observation — a reconcilable failure-Observation for
  that state is epic child #4, not #472.)
- **D7 — promote path-policy required.** `promote(path_policy=None)` skips the changed-path check
  (worktree.py:558-573). Fix: make `path_policy` a REQUIRED keyword; add a `PROMOTE_ANY` explicit
  allow-all sentinel; update the ~9 existing test callers (test_worktree_promote.py,
  test_work_product.py) to pass it. Closes the fail-open promotion boundary before the epic's
  behavioral-canary child ever drives a real mutating promotion.
- **F7 — receipt_nonce durability.** `receipt_nonce` is a `JobRecord` field (registry.py:66) but
  omitted from serialize/deserialize (registry.py:177-206) → lost on crash/resume. Fix: serialize
  it; round-trip test.

These five are coherent: "make the supervised dispatch path composable, correlation-bound,
auditable, and its promotion boundary fail-closed, and its recovery join durable" — the
foundation the epic's deeper builds sit on, with zero new machinery and no disputed surface.

### C. Hardening epic (filed, not built here)
A new epic decomposing the six capability builds the proving run surfaced, dependency-ordered:
1. **Reconcile pause/recover model** — `reconcile_run` must treat a quota_paused→recovered call as
   one logical expected call satisfied by the recovery (enforce.py:513-537 has no such model today).
2. **Ledger-aware dispatch chokepoint** — `_do_dispatch` must consult the expected-call ledger and
   reject a dispatch after `run_closed` (executor_routing_lib.py:1361 is ledger-blind); includes the
   expected-call ledger + `reconcile` verb (deferred here because a ledger without the chokepoint is
   escapable — a Critical).
3. **Behavioral codex canary** — launch a sandboxed child with external positive/negative write
   verification before mutation (canary.py:341-355 is composition-validation only; spike #452 §5
   spec). Unblocks AC2's "confined, negative-control passed."
4. **Reconcilable failure Observations** — `exited_no_sentinel` returns None + synthetic timeout obs
   has `correlation_id=None` (supervisor.py:151-160, 682-687); every terminal state needs a
   correlation-bound Observation.
5. **Per-call caps + genuine quota detection/classification** — AC-B4 hard token/turn/$ caps
   (currently absent), plus quota detection in `await_job` keyed on capture `stderr.txt` finalizing
   `quota_paused` directly, plus the classifier evidence field on `JobRecord`. Unblocks AC3's live
   quota cycle.
6. **Account-switch recovery proof harness** — the AC3 account-state machine (account-identity
   capture, audited resume, cross-account verdict) on top of builds 1–5. This is where the owner's
   "does it recover on a new account?" question gets its live answer.

## File changes (this PR only)
| File | Change |
|---|---|
| hooks/executor_routing_lib.py | D1 composition select; D3 supervised audit append |
| phase_executor/src/phase_executor/supervisor.py | D2 correlation_id on launch + synthetic_observation |
| phase_executor/src/phase_executor/worktree.py | D7 promote path_policy required + PROMOTE_ANY |
| phase_executor/src/phase_executor/registry.py | F7 receipt_nonce serialize/deserialize |
| tests/hooks/test_executor_routing.py, tests/phase_executor/{test_supervisor_launch,test_worktree_promote,test_work_product,test_registry}.py | RED-first cells; update 9 promote callers |
| docs/planning/2026-07-21-472-proving-run-{design,report}.md + .html | this design + the report |
| docs/planning/2026-07-17-orchestrator-executor-acceptance-criteria.md | A4/F11 honest disposition (no genuine capture; deferred to epic) |
| 6 version surfaces + README changelog | 3.79.1→3.80.0 (plugin ×4, feat) + phase_executor 0.1.0→0.2.0 (promote API change) |
| docs/measurements/run_records.jsonl | rides (#471+#552 rows dirty; #472 row at Step 16) |

## Error handling / failure modes
- D1 composition mismatch → `CompositionError` → structured exit 5 (fail-closed).
- D2/D3 additive — default None preserves all existing behavior; a supervised dispatch that has no
  Observation (exited_no_sentinel) appends only its receipt, exactly as the sync path treats an
  availability failure (reconcilable failure-Observations = epic child #4).
- D7 — a caller with no explicit policy is a TypeError at call time (loud); PROMOTE_ANY is the
  visible opt-in for allow-all.
- F7 — additive field with a backward-read default; old jobs.json rows load unchanged.

## Security implications
- D7 closes the fail-open promotion boundary (no caller can silently promote out-of-scope paths).
- No credential reads, no live burn, no account switch in this PR (all deferred to the epic).
- No new external dependency; no new machinery.

## Platform / external dependencies
platform_apis: none
The five fixes are pure in-repo bug fixes against merged code + a report; no platform/external API
is exercised (the codex/claude/tmux live paths that r1–r5 declared are all deferred to the epic
children that actually drive them). The one API touched — `codex_cli.build_mutating_command` — is
only COMPOSED in D1 (its argv is validated by the existing self-check), not executed live in #472.

## Multi-PR assessment
Single PR. Code diff ~180 lines (D1 ~50, D2 ~25, D3 ~40, D7 ~30, F7 ~15) + tests ~200 + the report
prose. Well under any split threshold; the six deep builds are the filed epic, not this PR.

## Revision history + loop-back accounting
r1 (10 High) → r2 (13 High/1 Crit) → r3 (2 Crit/8 High) → r4 (2 Crit/9 High) → r5 (2 Crit/12 High):
each pass surfaced real, code-confirmed defects, converging on the finding that the remaining
Critical/High are missing-engine-capability defects requiring builds, not design edits. Owner
decisions: (D-a) fix-and-prove full; (D-b) accept burn + AC3=account-switch; (D-c 2026-07-21)
DECOMPOSE — report + safe fixes here, six builds to a hardening epic. Design loop-backs: 3 consumed
(r1/r2/r3 volume loops) under the owner's budget-raise-to-5; r6 is the narrowed convergent design,
expected to pass its gate because every disputed surface is removed.
