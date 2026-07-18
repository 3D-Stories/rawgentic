"""#430 — driver-bench: score the orchestrator/driver role against synthetic fixtures (epic #422).

Owner decision: HARNESS + DETERMINISTIC BASELINE. The stubbed matrix drives the REAL orchestration
code (phase_executor.run_seat / run_competitive, complexity_gate.needs_bakeoff, enforce.verify_post,
executor_routing_lib.dispatch_seat, bakeoff_policy) through a fixture-controlled stub dispatch and
scores the resulting trace across 7 dimensions — it never re-models the driver logic. The stubbed
cells are model-independent (the code path does not branch on driver model), so the matrix is a
reproducibility / regression baseline; the opus-vs-sonnet signal comes from the live cells.

Per-dimension mechanism (Step-4 rev-2 — each dimension uses the path that actually produces it):
  seat_selection / recovery  -> real run_seat (fixture stub dispatch keyed by seat + attempt index)
  winner_propagation         -> real run_competitive via bakeoff_policy (stub dispatch keyed by MODEL
                                + a stubbed judge, so no live glm call)
  gate                       -> complexity_gate.needs_bakeoff (direct)
  enforcement                -> enforce.verify_post (direct)
  audit_completeness         -> executor_routing_lib.dispatch_seat (WIRED seats only) + RoutingAuditLog
  token_burn                 -> sum Observation.usage (fail-closed 0 when a scored cell's usage absent)
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import bakeoff_policy  # sibling hook
import complexity_gate
import executor_routing_lib as _er

_REPO = Path(__file__).resolve().parent.parent
FIXTURE_DIR = _REPO / "docs" / "measurements" / "driver-bench" / "fixtures"
DEFAULT_REPORT = _REPO / "docs" / "measurements" / "driver-bench" / "stubbed-baseline.json"
TABLE = _REPO / "phase_executor" / "src" / "phase_executor" / "routing" / "rawgentic.routing-table.json"

VALID_SEATS = frozenset({"intake", "plan", "build", "review", "ship"})
WIRED_SEATS = _er.WIRED_SEATS  # the full 7-seat executor vocabulary (#464 §B); build now has a GATED audit path
DIMENSIONS = ("seat_selection", "recovery", "gate", "enforcement", "winner_propagation",
              "audit_completeness", "token_burn")


class FixtureError(RuntimeError):
    """A malformed fixture — fail closed on load, never score a broken fixture."""


def _pe():
    src = str(_REPO / "phase_executor" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    import phase_executor as pe  # noqa: PLC0415
    import phase_executor.contract  # noqa: PLC0415,F401
    import phase_executor.routing  # noqa: PLC0415,F401
    import phase_executor.enforce  # noqa: PLC0415,F401
    return pe


# ---- fixtures ---------------------------------------------------------------------------------
def load_fixture(path) -> dict:
    """Load + validate a fixture. Fail-closed on any structural problem (never score a broken one)."""
    try:
        fx = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FixtureError(f"cannot load fixture {path}: {exc}") from None
    if not isinstance(fx, dict) or "id" not in fx or "dimensions" not in fx:
        raise FixtureError(f"fixture {path}: missing id/dimensions")
    dims = fx["dimensions"]
    if not isinstance(dims, list) or not dims or any(d not in DIMENSIONS for d in dims):
        raise FixtureError(f"fixture {fx.get('id')}: dimensions must be a non-empty subset of {DIMENSIONS}")
    for seat, resps in (fx.get("responses") or {}).items():
        if seat not in VALID_SEATS:
            raise FixtureError(f"fixture {fx['id']}: unknown seat {seat!r} (valid: {sorted(VALID_SEATS)})")
        if not isinstance(resps, list) or not resps:
            raise FixtureError(f"fixture {fx['id']}: responses[{seat}] must be a non-empty list")
    return fx


def load_fixtures(directory=FIXTURE_DIR) -> list:
    files = sorted(Path(directory).glob("*.json"))
    if not files:
        raise FixtureError(f"no fixtures in {directory}")
    return [load_fixture(f) for f in files]


# ---- stub dispatch (builds real Observations from canned responses) ---------------------------
def _obs_from_response(engine, req, resp, *, run_id, attempt_id, digest):
    c = _pe().contract
    status = resp["parse_status"]
    # A successful dispatch reports actual_model == the model that was ROUTED to (req.requested_model,
    # = the chain target run_seat selected). Mirroring the routed model here (not a fixture-canned
    # value) is what makes seat_selection/recovery actually measure routing: a routing-table
    # regression changes req.requested_model, which the scorer then sees. A non-ok attempt carries no
    # envelope, so no identity (Step-11 finding).
    actual = req.requested_model if status == "ok" else resp.get("actual_model")
    return c.Observation(
        run_id=run_id, attempt_id=attempt_id, correlation_id=None, seat=req.seat, engine=engine,
        transport=req.transport, requested_model=req.requested_model, actual_model=actual,
        prompt_hash="h", context_hashes=[], usage=resp.get("usage"), timing_ms=1, queued_ms=0,
        process={"exit_code": 0 if status == "ok" else 1, "timed_out": status == "timeout"},
        parse_status=status, parsed_payload=resp.get("payload"), raw_capture_path=None,
        fallback_reason=None, routing_config_digest=digest)


def _seat_dispatch(fixture):
    """Dispatch for run_seat: key responses by seat + the attempt index in attempt_id (f'{i}-...')."""
    responses = fixture.get("responses") or {}

    def dispatch(engine, req, *, run_id, attempt_id, capture_root, digest, queued_ms, fallback_reason):
        i = int(str(attempt_id).split("-", 1)[0])
        seat_resps = responses.get(req.seat) or []
        if i >= len(seat_resps):  # L1: guard short lists instead of a bare IndexError mid-run
            raise FixtureError(f"fixture {fixture['id']}: no response for seat {req.seat!r} attempt {i}")
        return _obs_from_response(engine, req, seat_resps[i], run_id=run_id, attempt_id=attempt_id, digest=digest)
    return dispatch


def _bakeoff_dispatch(fixture):
    """Dispatch for run_competitive: candidates run concurrently sharing seat='design', so key the
    canned responses by requested MODEL (not attempt index)."""
    by_model = {c["model"]: c for c in fixture["bakeoff"]["candidates"]}

    def dispatch(engine, req, *, run_id, attempt_id, capture_root, digest, queued_ms, fallback_reason):
        resp = by_model.get(req.requested_model)
        if resp is None:
            raise FixtureError(f"fixture {fixture['id']}: no bakeoff response for model {req.requested_model!r}")
        return _obs_from_response(engine, req, resp, run_id=run_id, attempt_id=attempt_id, digest=digest)
    return dispatch


# ---- per-dimension scorers (each returns 0.0..1.0) --------------------------------------------
def _score_seat_selection(fx, snapshot, quota, capture_root):
    pe = _pe()
    seat = fx["primary_seat"]
    obs = pe.run_seat(seat, "p", snapshot=snapshot, quota=quota, capture_root=capture_root,
                      dispatch=_seat_dispatch(fx))
    return 1.0 if obs.actual_model == fx["expected"]["seat_model"] else 0.0


def _score_recovery(fx, snapshot, quota, capture_root):
    pe = _pe()
    seat = fx["primary_seat"]
    obs = pe.run_seat(seat, "p", snapshot=snapshot, quota=quota, capture_root=capture_root,
                      dispatch=_seat_dispatch(fx))
    recovered = obs.parse_status == pe.contract.OK and obs.actual_model == fx["expected"]["seat_model"]
    return 1.0 if recovered == fx["expected"]["recovered"] else 0.0


def _score_gate(fx, *_):
    gi = fx["gate_inputs"]
    gd = complexity_gate.needs_bakeoff(gi["task"], gi["issue"], gi["plan_est"], cfg=gi.get("cfg"))
    return 1.0 if gd.decision == fx["expected"]["gate"] else 0.0


def _score_enforcement(fx, snapshot, quota, capture_root):
    pe = _pe()
    resp = fx["enforcement"]["observation"]
    # build a real Observation, run the REAL verify_post, compare ok-verdict to expected
    obs = pe.contract.Observation(
        run_id="r", attempt_id="a", correlation_id=None, seat=fx.get("primary_seat", "build"),
        engine=resp.get("engine", "claude"), transport="native",
        requested_model=resp["requested_model"], actual_model=resp.get("actual_model"),
        prompt_hash="h", context_hashes=[], usage=None, timing_ms=1, queued_ms=0,
        process={"exit_code": 0, "timed_out": False}, parse_status=resp["parse_status"],
        parsed_payload=resp.get("payload"), raw_capture_path=None, fallback_reason=None,
        routing_config_digest="d")
    return 1.0 if pe.enforce.verify_post(obs).ok == fx["enforcement"]["expected_ok"] else 0.0


class _Draft:
    """Minimal Observation-like for replaying the judge's anonymize+shuffle to derive the EXPECTED
    winner independently (so the check actually tests the label->index remap, not a tautology)."""
    def __init__(self, payload):
        self.parse_status = "ok"
        self.parsed_payload = payload


def _score_winner(fx, snapshot, quota, capture_root):
    # The judge picks winner_draft (1-based, post-shuffle); make_glm_judge maps it to the original
    # candidate via order[winner_draft-1]. To verify that remap (not just that SOME winner came back),
    # replay the SAME seeded shuffle over the candidates in DESIGN_MODELS order and derive the model
    # the winning draft must resolve to; assert run_design_round returned exactly that.
    seed, wd = 1, fx["bakeoff"]["winner_draft"]
    by_model = {c["model"]: c for c in fx["bakeoff"]["candidates"]}
    drafts = [_Draft(by_model[m]["payload"]) for m in bakeoff_policy.DESIGN_MODELS]
    _shuffled, order = bakeoff_policy.anonymize_and_shuffle(drafts, seed=seed)
    expected_model = bakeoff_policy.DESIGN_MODELS[order[wd - 1]]
    verdict = json.dumps({"winner_draft": wd, "scores": {}, "confidence": 1.0})
    winner, _losers, _judge_obs, _record = bakeoff_policy.run_design_round(
        "p", snapshot=snapshot, quota=quota, capture_root=capture_root, headless=True, seed=seed,
        sink_path=capture_root / "bakeoff.jsonl", complete_fn=lambda prompt: (verdict, ""),
        dispatch=_bakeoff_dispatch(fx))
    propagates = winner.requested_model == expected_model
    return 1.0 if propagates == fx["expected"].get("winner_propagates", True) else 0.0


def _score_audit(fx, snapshot, quota, capture_root):
    pe = _pe()
    seat = fx["primary_seat"]
    if seat not in WIRED_SEATS:  # H1: a seat OUTSIDE the executor vocabulary has no audit path; build
        # is IN the vocabulary now (#464 §B) but its audit path is GATED — the gate is threaded below.
        raise FixtureError(f"fixture {fx['id']}: audit_completeness needs a WIRED seat {sorted(WIRED_SEATS)}, got {seat!r}")
    # Per-cell isolation (Step-11 finding): a hardcoded run_id + shared capture_root would accumulate
    # every cell's dispatches into ONE audit file, so records() would read the cross-cell total. Give
    # each call a fresh, cleared audit dir keyed by fixture id so it reads ONLY its own dispatches.
    audit_root = Path(capture_root) / f"audit-{fx['id']}"
    shutil.rmtree(audit_root, ignore_errors=True)
    audit_root.mkdir(parents=True, exist_ok=True)
    audit = pe.enforce.RoutingAuditLog(str(audit_root), "run")
    # #464 §E: a build-role seat's audit path requires an authenticated, launch-bound gate. Mint a
    # fixture #429 gate whose benign inputs yield a SINGLE outcome (no bake-off) + a matching plan
    # context so complexity_gate.verified_decision authenticates it (passed via injected params, not
    # files — the bench drives the INTERNAL dispatch_seat). Non-build seats need no gate.
    gate_kwargs = {}
    if snapshot.seat(seat).get("role") == "build":
        gd = complexity_gate.needs_bakeoff(
            {"risk_level": "standard"}, {"complexity": "standard"},
            {"files": [], "lines": 1, "file_count": 1})
        gate_kwargs = {"gate_decision": gd,
                       "plan_context": {k: gd.input_snapshot[k]
                                        for k in complexity_gate.REQUIRED_PLAN_CONTEXT_KEYS}}
    _er.dispatch_seat(
        seat=seat, prompt="p", run_id="run", correlation_id=None, author_provider=None,
        effort=None, timeout=300.0, context=(), snapshot=snapshot, quota=quota, audit=audit,
        capture_root=str(audit_root), routing=pe.routing, enforce=pe.enforce,
        run_seat=pe.run_seat, dispatch_real=_seat_dispatch(fx), **gate_kwargs)
    records = audit.records()
    receipts = [r for r in records if r.get("kind") == "receipt"]
    observations = [r for r in records if r.get("kind") == "observation"]
    # complete = a receipt AND an observation for EVERY dispatch (balanced ==). A pre-check denial
    # leaves a lone receipt (receipts > observations) -> incomplete, which is the honest verdict.
    complete = bool(receipts) and len(receipts) == len(observations)
    return 1.0 if complete == fx["expected"].get("audit_complete", True) else 0.0


def _score_token_burn(fx, snapshot, quota, capture_root):
    pe = _pe()
    seat = fx["primary_seat"]
    obs = pe.run_seat(seat, "p", snapshot=snapshot, quota=quota, capture_root=capture_root,
                      dispatch=_seat_dispatch(fx))
    # M1: an unmeasurable cell fails closed (never a silent 1.0 on Σ=0) — require BOTH token fields,
    # not just a non-empty dict (a usage lacking input/output is not a real measurement).
    if not obs.usage or "input" not in obs.usage or "output" not in obs.usage:
        return 0.0
    burn = int(obs.usage.get("input", 0)) + int(obs.usage.get("output", 0))
    return 1.0 if burn <= fx["expected"]["token_burn_max"] else 0.0


_SCORERS = {
    "seat_selection": _score_seat_selection, "recovery": _score_recovery, "gate": _score_gate,
    "enforcement": _score_enforcement, "winner_propagation": _score_winner,
    "audit_completeness": _score_audit, "token_burn": _score_token_burn,
}


# ---- run + matrix -----------------------------------------------------------------------------
def run_fixture(fixture, *, snapshot, quota, capture_root) -> dict:
    """Score the dimensions this fixture declares. Returns {dimension: score 0..1}. A scorer that
    raises (malformed fixture / unrunnable seat) fails closed to 0.0 for that dimension."""
    scores = {}
    for dim in fixture["dimensions"]:
        try:
            scores[dim] = _SCORERS[dim](fixture, snapshot, quota, capture_root)
        except Exception as exc:  # noqa: BLE001 — a broken cell scores 0, never a silent pass
            scores[dim] = {"score": 0.0, "error": f"{type(exc).__name__}: {exc}"[:200]}
    return scores


def run_matrix(fixtures, *, snapshot, quota_factory, capture_root, models=("opus", "sonnet"), reps=3) -> dict:
    """The stubbed baseline matrix: len(fixtures) x models x reps cells. Deterministic + model-
    independent (the code path does not branch on driver model), so this is a reproducibility /
    regression baseline — the report says so plainly; opus-vs-sonnet signal is the live cells."""
    cells = []
    for model in models:
        for rep in range(reps):
            for fx in fixtures:
                scores = run_fixture(fx, snapshot=snapshot, quota=quota_factory(),
                                     capture_root=capture_root)
                cells.append({"fixture": fx["id"], "model": model, "rep": rep, "scores": scores})
    # per-dimension mean over numeric scores
    dim_totals: dict = {d: [] for d in DIMENSIONS}
    for cell in cells:
        for dim, val in cell["scores"].items():
            dim_totals[dim].append(val if isinstance(val, (int, float)) else val.get("score", 0.0))
    dim_means = {d: (sum(v) / len(v) if v else None) for d, v in dim_totals.items()}
    return {
        "n_cells": len(cells), "n_fixtures": len(fixtures), "models": list(models), "reps": reps,
        "dimension_means": dim_means, "cells": cells,
        "note": ("STUBBED BASELINE — model-independent deterministic code path; the model x rep axes "
                 "replicate the per-fixture result to prove determinism and establish the matrix shape. "
                 "The opus-vs-sonnet signal comes from the 3 live cells (RUN_LIVE), not this matrix."),
    }


def _run_cli(out_path=DEFAULT_REPORT, fixture_dir=FIXTURE_DIR):
    pe = _pe()
    snapshot = pe.snapshot_from_file(TABLE)
    tmp = _REPO / ".rawgentic" / "driver-bench-cap"
    shutil.rmtree(tmp, ignore_errors=True)  # hermetic run — never read stale accumulated capture state
    tmp.mkdir(parents=True, exist_ok=True)
    report = run_matrix(load_fixtures(fixture_dir), snapshot=snapshot,
                        quota_factory=lambda: pe.QuotaCoordinator(tmp / "permits", snapshot.pool_concurrency()),
                        capture_root=tmp)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"driver-bench stubbed baseline: {report['n_cells']} cells, dimension means:")
    for dim, mean in report["dimension_means"].items():
        print(f"  {dim}: {mean}")
    print(f"report -> {out_path}")


if __name__ == "__main__":
    _run_cli()
