# #449 — driver-bench live cells on the wired executor path + session_policy A/B

Design r1 · 2026-07-22 · session 3544db7b · issue #449 (W10 of executor-wiring, epic #475) · plugin 3.89.0 → 3.90.0
WF2 resumed from Step 3 (Steps 1-2 done prior session 641a6f27; 2 analysis outputs read). "Part of #475".

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
- **#558 A-F1 constraint (do NOT re-litigate):** the live BUILD/bake-off cell dispatches a MUTATING
  build seat, which the sync/competitive path REFUSES (mutating-claude, `MUTATING_FS_SANDBOXED={codex}`).
  So the live bench covers READ-ONLY seats (analysis/review/ship/intake/plan) + the competitive
  DESIGN judge (read-only glm judge); the build-seat live cell is out of reach by design — the live
  report records it as a legible `unsupported (mutating build seat, #558 A-F1)` skip, not a failure.
- **D-8 session_policy A/B:** the fresh-vs-resume A/B cell is sync-unreachable + supervised-claude-
  refused → the evidence-backed reading (prior D-8) is **defer the live A/B to #559-class supervised
  tooling**; #449 ships the A/B cell as a SHADOW/parametrized dimension (the tooling records which
  session_policy each seat used, requested==actual) but does not force a live resume dispatch.
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
4. **Cheap-guarded:** default = the 3-live-cell subset (#430 design) or a named `--fixtures`;
   an explicit cost/usage line; no unbounded fan-out (pool caps + the subset).
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
- api: live glm-5.2 judge via the zhipuai_sdk adapter (.venv-bench / glm-judge.env ZHIPUAI_API_KEY)
  feasibility: verified via existing-call-site — zhipuai_sdk adapter live path (shipped #426/#428); RUN_LIVE-gated
  failure: fail-silent
  surface: fail-closed per-cell skip in the live report (AC5) — a missing credential yields `skipped: no glm credential`, never a fabricated score; the actual authenticated live RUN is the #138 deferred owner-attended cell
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
entry + live-report writer (`docs/measurements/driver-bench/`) + cost line + tests · T3 session_policy
A/B shadow dimension (record requested/actual session_policy) + test · T4 RUN_LIVE `@live` deferred cell +
docs (how to run) · T5 versions ×4 + README changelog + design md+html.
