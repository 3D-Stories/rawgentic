# #430 — Driver-bench: measure the orchestrator role (epic #422) — design

**Lane:** full spine (Large, `feat`). Depends on #426 (seat config ✓), #428 (bakeoff policy ✓ merged).
Owner decision (2026-07-17): **harness + deterministic baseline** — the 72 stubbed cells score the
DETERMINISTIC orchestration code (model-independent, so the model axis is a reproducibility label);
the real opus-vs-sonnet comparison is the 3 live cells (`RUN_LIVE=1`).

## 1. What already exists (reuse, don't rebuild)

The orchestration decisions are already code + tested: `phase_executor.run_seat` (chain-aware fallback
on availability failures), `run_competitive` (#428), `complexity_gate.needs_bakeoff` (#429),
`enforce.check_pre/verify_post/RoutingAuditLog` (#425), `bakeoff_policy` (#428). Driver-bench does NOT
re-implement any of it — it drives fixtures THROUGH the real engine with a fixture-controlled stub
dispatch and scores the resulting trace, so the bench measures reality, not a re-model.

## 2. Design — `hooks/driver_bench_lib.py`

### Fixtures (`docs/measurements/driver-bench/fixtures/*.json`, 12)
Each: synthetic issue + the seat path + **stubbed executor responses** (per seat/attempt) + **injected
failures** (`quota` / `model_retired` / `judge_timeout`) + the **expected** outcome per scored
dimension. Schema (validated on load; fail-closed on a malformed fixture):
```
{ "id": "f01-clean-standard", "issue": {"number": 1, "complexity": "standard"},
  "seats": [{"seat": "intake"}, {"seat": "build", "gate_inputs": {...}}, ...],
  "responses": { "intake": [{"parse_status":"ok","actual_model":"claude-opus-4-8","payload":"..."}],
                 "build":  [{"parse_status":"nonzero_exit"}, {"parse_status":"ok","actual_model":"claude-opus-4-8"}] },
  "expected": { "seat_models": {"build":"claude-opus-4-8"}, "gate": {"build": true},
                "enforcement_ok": true, "winner": {...}, "audit_complete": true,
                "recovered": true, "token_burn_max": 5000 } }
```
`responses[seat]` is a LIST indexed by attempt — attempt 0 nonzero_exit forces `run_seat`'s real
fallback to attempt 1 (the next eligible chain target), exactly as production would.

### Driver run — `run_fixture(fixture, *, snapshot, quota, capture_root, dispatch=None) -> DriverRun`
For each seat in the fixture path, call the REAL `run_seat` (or, for a gated build seat,
`complexity_gate.needs_bakeoff` then `bakeoff_policy.run_build_bakeoff`) with a **fixture dispatch**
that returns `responses[seat][attempt]` as a real `Observation` (or an injected-failure Observation).
Collect: chosen model per seat, gate decisions, `verify_post` enforcement outcomes, the
`RoutingAuditLog` entries, recovery (did a fallback chain reach an ok seat), and token burn (Σ usage).
`dispatch` is injectable — default = the fixture stub; the live cells pass a real driver dispatch.

### Scoring — `score_run(run, fixture) -> DimensionScores`
Seven dimensions, each 0.0–1.0 vs `fixture.expected`:
`seat_selection` (chosen model == expected, after fallback), `gate` (bake-off decision matches),
`enforcement` (cross-model/verify_post outcomes match), `winner_propagation` (winner Observation is
the expected candidate), `audit_completeness` (an audit line per dispatch, chain-of-custody intact),
`recovery` (fallback reached an ok seat when a failure was injected), `token_burn` (≤ expected max).
Overall = mean. A missing/failed dimension scores 0 (fail-closed — never silently full-marks).

### Matrix + report — `run_matrix(fixtures, models, reps, *, dispatch=None) -> MatrixReport`
12 fixtures × `models` (default `("opus","sonnet")`) × `reps` (default 3) = 72 cells. Each cell =
`run_fixture` + `score_run`. Deterministic (stubbed) → cells are reproducible; the report records
per-dimension means + per-fixture breakdown + a NAMED note that the stubbed matrix is
model-independent (the code path does not branch on driver model), so the model axis is a
reproducibility label and the opus-vs-sonnet SIGNAL comes from the 3 live cells.

### CLI
`python3 hooks/driver_bench_lib.py` → runs the stubbed 72-cell matrix against the shipped fixtures,
writes the JSON report to `docs/measurements/driver-bench/stubbed-baseline.json`, prints the
per-dimension summary. (Bare entry point — no argparse flags; ponytail: the fixture dir / out path are
module constants, not a speculative CLI surface.) The 3 LIVE end-to-end cells (a real opus/sonnet
driver dispatch) are a documented follow-up campaign, not part of this PR — run offline with a real
driver dispatch when the orchestrator seat is evaluated; never in CI.

## 3. Fail-closed
Malformed fixture (missing required key, responses not a list, unknown failure kind) → raise on load
(never score a broken fixture). A dimension with no expected value or an unrunnable seat → 0, not
skipped. Unknown model in a fixture → the existing `_candidates_for`/routing ValueError.

## 4. Tests (`tests/hooks/test_driver_bench.py`; all deterministic/stubbed, no LLM/network)
1. Fixture loader validates the 12 shipped fixtures; a malformed fixture raises.
2. `run_fixture` on a CLEAN fixture → chosen models == expected, no fallback.
3. `run_fixture` on an INJECTED-quota fixture → real `run_seat` fallback to attempt 1; `recovered` true.
4. `run_fixture` on a GATE fixture → `needs_bakeoff` decision matches expected.
5. `score_run` → 1.0 on a matching run; a deliberately-wrong expected value docks the right dimension
   to 0 (red-if-broken: proves scoring actually compares, not rubber-stamps).
6. `run_matrix` aggregates 72 cells; deterministic (two runs → identical scores); per-dimension means
   present.
7. enforcement/audit dimensions: an audit line per dispatch; a cross-model breach fixture scores
   `enforcement` < 1.
8. CLI subprocess test (`python3 hooks/driver_bench_lib.py` → rc 0, "72 cells", report written). The
   live end-to-end cells are a documented offline follow-up (no pytest — they need a real driver LLM).
9. Per-dimension discrimination: for EACH of the 7 dimensions, a flipped `expected` docks it to 0.0 —
   proving no scorer is stuck-at-1.0.

## 5. Versioning / docs / diagram
`feat` → minor: 3.50.0 → **3.51.0** ×3 surfaces + `test_plugin_version_bumped`. README changelog
(diagram decision: **no workflow-spine change → no diagram REV** — a measurement tool, not a WF2
station). A short driver-bench note in `docs/model-routing.md`. The generated stubbed report ships as
a committed artifact under `docs/measurements/driver-bench/`. Step-16 run-record.

## rev 1 → rev 2 (Step-4 adversarial findings — all verified, all adopted)

The rev-1 "drive all 7 dimensions through `run_seat` and read the audit log" mechanism was wrong.
`run_seat`/`run_competitive` (engine.py) import NO `enforce` and never populate `RoutingAuditLog`.
Revised to a **per-dimension hybrid** — each dimension uses the mechanism that actually produces its
signal, and a fixture declares WHICH dimensions it exercises (not all 7 per fixture):

- **seat_selection / recovery** — driven through the REAL `run_seat` with a fixture stub dispatch
  keyed by `req.seat` + the attempt index parsed from `attempt_id` (`int(attempt_id.split("-")[0])`,
  the pattern executor_routing_lib.py:243 already uses). A `nonzero_exit`/`timeout` at attempt 0 forces
  the real fallback to attempt 1; `recovery` = the final Observation is `ok`.
- **winner_propagation** — driven through `run_competitive`, but candidates run CONCURRENTLY with
  `attempt_id = uuid[:8]` (no index) all sharing `seat="build"` (H3). So the fixture keys competitive
  responses **by requested model** (`req.requested_model`), not attempt index, AND supplies a
  **stubbed `complete_fn`** (judge) returning a fixed `winner_draft` — otherwise the cell makes a LIVE
  glm call (H2). `run_fixture` threads `complete_fn`; the fixture's `GateDecision` is built via the real
  `needs_bakeoff` (so `_verified_decision`'s digest check passes — bakeoff_policy.py:289).
- **gate** — call `complexity_gate.needs_bakeoff(fixture inputs)` directly; compare `.decision`.
- **enforcement** — call `enforce.verify_post` (and `check_pre` where applicable) directly on the
  fixture's receipt/observation pair; compare the outcome.
- **audit_completeness** — driven through `executor_routing_lib.dispatch_seat` with the fixture stub
  injected as its `dispatch_real`, scoped to `WIRED_SEATS = {intake, plan, ship}` (H1 — `build` has no
  audit path: `check_pre` still hard-denies `role=="build"`, enforce.py:121-126); score = a
  receipt+observation audit line per dispatch, chain-of-custody intact.
- **token_burn** — sum `Observation.usage` (fixture responses MUST carry a `usage` block, M1);
  **fail-closed 0** when a scored cell's usage is absent (never a silent 1.0 on Σ=0).

Fixture schema additions: `responses[seat]` (list by attempt, each with `usage`), `bakeoff` (candidate
responses keyed by model + a stubbed `winner_draft`), `enforcement` (receipt/observation pair +
expected outcome), `dimensions` (which of the 7 this fixture scores). Load-time validation:
`len(responses[seat]) >= len(eligible_targets(seat))` (L1), seat names ∈
`{intake, plan, build, review, ship}` (L2), a `review` fixture supplies `author_provider`.

Matrix (M2/L3): the issue's 12×3×2 = 72 shape is kept (owner-approved), but the report states plainly
that the stubbed matrix is a **reproducibility / regression baseline** — the deterministic code path
does not branch on driver model, so the 72 cells hold 12 distinct results replicated across the
model×rep axes to prove determinism and establish the shape the LIVE campaign (3 cells) fills with the
real opus-vs-sonnet signal. Not presented as 72 independent measurements.

Out-of-scope (filed as a follow-up, not #430): `enforce.check_pre` never got the #429 gate wiring — it
still hard-denies `role=="build"` (enforce.py:121-126), which is why build has no audit path.

## Platform / external dependencies

platform_apis:
- api: none — the stubbed 72-cell matrix drives the in-repo engine with a fixture stub dispatch; the 3 live cells reuse the already-declared adapter path (#427) behind RUN_LIVE, adding no new external surface
  feasibility: verified via existing-call-site — phase_executor.run_seat / run_competitive / complexity_gate / bakeoff_policy are all in-repo, imported under the repo CPython via the same phase_executor/src sys.path shim #427/#428 use (live this session); the stubbed path makes NO provider call
  failure: fail-loud
  surface: a malformed fixture raises on load; an unrunnable seat scores 0; the live path is gated behind RUN_LIVE and is never exercised on merge
