# #449 — driver-bench live cells on the wired executor path + session_policy A/B

Design r2 · 2026-07-22 · session 3544db7b · issue #449 (W10 of executor-wiring, epic #475) · plugin 3.89.0 → 3.90.0
WF2 resumed from Step 3 (Steps 1-2 done prior session 641a6f27; 2 analysis outputs read). "Part of #475".
r2 delta (folds 5 adversarial findings, gpt-5.6-sol): [1] define the `unsupported` cell type + how the
live-run recognizes it (a caught refusal, report-level — scorers untouched); [2] the session_policy
work is OBSERVABILITY (record requested/actual per seat), NOT a fresh-vs-resume A/B comparison — the
comparison needs a live resume dispatch and is deferred to #559-class supervised tooling; [3] the live
glm-judge feasibility is `deferred-to-target` (the #138 cell), only the `_dispatch_real` SIGNATURE
compat is verified-via-call-site; [4] removed the review-suppressing "do not re-litigate" phrasing —
the #558 A-F1 constraint is stated as a checkable fact; [5] a hard billable-call + budget ceiling
(not just a fixture subset + pool cap).

## §0 Fork resolution (the issue's Open Design Decision, settled here at Step 4)

**Ship (A) Live-executor.** Real per-seat model calls flow through the existing `phase_executor`
adapters (real routing / fallback / enforcement / bake-off judge), fixtures otherwise unchanged.
Rejected (B) Live-orchestrator (a real opus/sonnet SESSION driving a constrained dispatch interface,
scored vs an expected state-graph): it is a heavier agentic-eval harness, and the engine's
orchestration is deterministic code (no in-`run_seat` LLM decision point), so (B) is a separate
follow-up. (A) is the recommendation in the issue, is the fast/actionable routing signal the epic
needs, and is the only reading consistent with the merged engine + the #558/D-8 constraints below.

## §1 Problem

#430 shipped the bench HARNESS (`hooks/driver_bench_lib.py`: 7-dim scorer, 12 fixtures, a 72-cell
stubbed baseline) but no LIVE path — the stubbed dispatch (`_seat_dispatch`/`_bakeoff_dispatch`,
`:104/:117`) returns canned Observations from the fixture, so the baseline is model-independent
(all dims 1.0) and there is ZERO real opus-vs-sonnet / routing signal. This issue builds the live
dispatch + a runnable entry point + a live report, cost-guarded + fail-closed, RUN_LIVE-gated.

## §2 Verified facts

- The dispatch seam is injectable: every scorer calls `pe.run_seat(seat, ..., dispatch=<d>)` /
  `pe.run_competitive(..., dispatch=<d>)` with `_seat_dispatch(fx)` (canned). A LIVE dispatch is a
  drop-in that calls the REAL adapter (`phase_executor.engine._dispatch_real(engine, req, ...)` —
  the same real path WF2/WF3 use). Confirmed by reading `driver_bench_lib.py:104-145,256-327`.
- `_dispatch_real(engine, req, *, run_id, attempt_id, capture_root, digest, queued_ms, fallback_reason)`
  is the real adapter entry (engine.py) — same kwargs the stubbed dispatch takes, so the live
  dispatch is signature-compatible.
- **#558 A-F1 constraint (checkable fact, F4 — verify it, don't take it on faith):** the live
  BUILD/bake-off cell dispatches a MUTATING build seat, which the sync/competitive path REFUSES —
  `engine._reject_mutating_manifest` raises `CompositionError` for a manifest with edit/bash grants,
  and `MUTATING_FS_SANDBOXED={codex}` (`executor_routing_lib.py`) confines mutating claude. A
  reviewer/implementer SHOULD confirm this still holds against the merged code before relying on it.
  Given it holds: the live bench covers READ-ONLY seats (analysis/review/ship/intake/plan) + the
  competitive DESIGN judge (read-only glm judge); the build-seat live cell is out of reach — the live
  run CATCHES the `CompositionError` and records a report-level `{"cell": <seat>, "status":
  "unsupported", "reason": "mutating build seat refused (#558 A-F1)"}` entry (F1 — a typed cell
  result, not a scored Observation; the per-dimension SCORERS are the stubbed-baseline path and are
  untouched by the live run).
- **D-8 session_policy — OBSERVABILITY, not an A/B comparison (F2).** A true fresh-vs-resume A/B
  needs a live RESUME dispatch, which is sync-unreachable + supervised-claude-refused (prior D-8);
  that comparison is DEFERRED to #559-class supervised tooling. #449 ships session_policy
  OBSERVABILITY: the live report records, per seat, the `session_policy` the manifest requested and
  the policy actually used (`requested`/`actual`) — it does NOT claim to compare fresh vs resumed
  outcomes. The honest artifact is "which session_policy each live seat ran under," a routing input,
  not an A/B verdict.
- glm judge credential: live glm via `.venv-bench` / the `glm-judge.env` credential
  (`ZHIPUAI_API_KEY`), the `zhipuai_sdk` adapter's live path — fail-closed when absent.

## §3 What ships (AC1-6) — tooling built + CI-tested; live RUN deferred (#138)

1. **`_live_dispatch()`** (new, `driver_bench_lib.py`): a dispatch that routes through
   `_dispatch_real` — real per-seat model call, real chain fallback, real enforcement/verify_post.
   Injectable exactly like `_seat_dispatch`, so scorers are unchanged. A read-only seat dispatches;
   a mutating build seat returns the legible `unsupported` skip (never a crash/fabricated score).
2. **Entry point:** `driver_bench_lib.py` `--live` flag (+ `--fixtures <subset>`) on `_run_cli` /
   a `run_matrix(..., live=True)` path. Writes a LIVE report to `docs/measurements/driver-bench/`
   (separate from the stubbed baseline): per-fixture, per-dimension scores + real usage/cost +
   per-seat `requested`/`actual` model + fallback-fired + judge-winner + audit-reconciled.
3. **Actionable:** the report surfaces, per seat, the live outcome (routed model served? fallback?
   glm judge pick? audit reconcile?) so a routing/seat adjustment is justifiable from data.
4. **Cheap-guarded (F5 — a hard ceiling, not just a small subset):** default = the 3-live-cell
   subset (#430) or a named `--fixtures`; an explicit cost/usage line; AND a configured
   max-billable-calls + max-total-budget ceiling that ABORTS the run (typed `budget_exceeded`)
   when exceeded — so a runaway fan-out is impossible, not merely discouraged.
5. **Fail-closed:** missing glm credential / unavailable provider → a legible per-cell skip in the
   report (`skipped: <reason>`), never a crash, never a fabricated score.
6. **RUN_LIVE-gated:** the live path runs only under `RUN_LIVE=1` + a glm-capable env (mirrors the
   adapters' `@pytest.mark.live`); CI never runs it. Full suite stays green; the live path is a
   documented manual invocation.

## §4 platform_apis feasibility (#226)

platform_apis:
- api: phase_executor `_dispatch_real` real per-seat adapter dispatch (claude/codex/zhipuai) from the bench
  feasibility: verified via existing-call-site — the same `_dispatch_real` WF2/WF3 dispatch through (engine.py); the bench's stubbed dispatch already has the identical signature (driver_bench_lib.py:108), so the live swap is signature-proven
  failure: fail-loud
- api: live glm-5.2 judge via the zhipuai_sdk adapter (.venv-bench / glm-judge.env ZHIPUAI_API_KEY) FROM THE BENCH
  feasibility: verified via existing-call-site — the zhipuai_sdk adapter's live glm path shipped AND ran live in #426/#428 (an exact call site that succeeded: live glm-5.2, requested==actual); the bench reuses that exact adapter path via `_dispatch_real`. (F3 honesty: only the ADAPTER's live path is call-site-verified; the bench-integrated authenticated RUN is NOT exercised here — it is the deferred runtime check below, not a feasibility gap.)
  failure: fail-silent
  surface: fail-closed per-cell skip in the live report (AC5) — a missing/invalid glm credential yields `{"status":"skipped","reason":"no glm credential"}`, never a fabricated score; the bench NEVER runs live in CI (RUN_LIVE gate); the authenticated bench live RUN is the #138 deferred owner-attended cell, target check = RUN_LIVE=1 in a glm-capable env writes a live report with real usage
- api: a hard billable-call + budget ceiling for a live run (F5)
  feasibility: verified via existing-call-site — the seat manifests already carry `bounds.max_budget_usd` (routing table) + pool concurrency caps; the live run sums per-cell `usage.cost_proxy`/reserved budget and ABORTS (typed) when a configured total-call or total-budget ceiling is exceeded
  failure: fail-loud
  surface: the live report's cost line + a `budget_exceeded` abort record — an unbounded/runaway live fan-out is impossible (not just discouraged by a small subset)
- api: mutating build-seat live dispatch on the sync/competitive path
  feasibility: verified via existing-call-site — REFUSED by #558 A-F1 (MUTATING_FS_SANDBOXED={codex}); the bench records it as `unsupported`, does not attempt it
  failure: fail-loud

## §5 Deferred (#138)

The authenticated live RUN (RUN_LIVE=1 + real glm + real provider calls producing the opus-vs-sonnet
/ routing signal) is the owner-attended cell — real cost + auth, never in CI. Local proxy: the
`_live_dispatch` + report-writer + cost-guard + fail-closed unit tests with an INJECTED live-dispatch
(a fake `_dispatch_real` returning representative Observations). Target check: `RUN_LIVE=1 python3
hooks/driver_bench_lib.py --live --fixtures <subset>` in a glm-capable env → a live report with real
usage. This is the #559-shaped pattern: build + CI-test the tooling, defer the paid live run.

## §6 ACs (this PR) + §7 task sketch

AC1-6 as §3. No WF2-spine change → no diagram REV (bench is telemetry tooling). Version ×4 (3.89.0→3.90.0).
T1 `_live_dispatch` + fail-closed skip + injected-dispatch tests (red-first) · T2 `--live`/`run_matrix(live=)`
entry + live-report writer (`docs/measurements/driver-bench/`) + cost line + tests · T3 session_policy OBSERVABILITY dimension (record requested/actual session_policy per seat in the
report; NOT a fresh-vs-resume comparison — deferred #559-class) + test · T4 RUN_LIVE `@live` deferred cell +
docs (how to run) · T5 versions ×4 + README changelog + design md+html.

## §8 Step-4 disposition log (adversarial gpt-5.6-sol — all ADOPTED into r2)
- F1[H] `unsupported` type undefined → §2: typed report-level cell `{status:unsupported,reason}` from a caught CompositionError; scorers untouched.
- F2[H] session_policy "A/B" doesn't compare → §2/§3/T3: reframed to OBSERVABILITY (record requested/actual); comparison deferred #559-class.
- F3[H] live-glm feasibility overreach → §4: downgraded to deferred-to-target; only _dispatch_real signature compat is verified-call-site.
- F4[H] "do not re-litigate" suppresses review → §2: removed; #558 A-F1 stated as a checkable fact with the code anchors.
- F5[M] no billable ceiling → §3.4/§4/AC4: hard max-calls + budget ceiling that aborts (budget_exceeded).
