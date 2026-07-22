"""#430 — driver-bench harness: fixture loading/validation, per-dimension scoring through the REAL
orchestration engine (stub dispatch, no live call), the 72-cell reproducibility matrix, and
fail-closed behavior. A discrimination test proves the scorers actually compare (dock a wrong
expected to 0) rather than rubber-stamp."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
HOOKS = REPO / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
import driver_bench_lib as db  # noqa: E402

db._pe()
# pylint: disable=no-name-in-module
import phase_executor as pe  # noqa: E402
# pylint: enable=no-name-in-module

TABLE = REPO / "phase_executor" / "src" / "phase_executor" / "routing" / "rawgentic.routing-table.json"


@pytest.fixture
def env(tmp_path):
    snap = pe.snapshot_from_file(TABLE)
    return {"snapshot": snap,
            "quota": pe.QuotaCoordinator(tmp_path / "permits", snap.pool_concurrency()),
            "capture_root": tmp_path / "cap"}


def _fx(name):
    return db.load_fixture(db.FIXTURE_DIR / f"{name}.json")


# ---- fixtures load / validate -----------------------------------------------------------------
def test_all_12_fixtures_load():
    fxs = db.load_fixtures()
    assert len(fxs) == 12
    assert all(set(fx["dimensions"]) <= set(db.DIMENSIONS) for fx in fxs)


def test_malformed_fixture_fails_closed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"id": "x"}', encoding="utf-8")           # missing dimensions
    with pytest.raises(db.FixtureError):
        db.load_fixture(bad)
    bad.write_text('{"id":"x","dimensions":["nope"]}', encoding="utf-8")  # bad dimension
    with pytest.raises(db.FixtureError):
        db.load_fixture(bad)
    bad.write_text('{"id":"x","dimensions":["gate"],"responses":{"frobnicate":[{}]}}', encoding="utf-8")
    with pytest.raises(db.FixtureError):                       # unknown seat
        db.load_fixture(bad)


# ---- per-dimension scoring through the real engine --------------------------------------------
def test_seat_selection_and_token_burn(env):
    s = db.run_fixture(_fx("f01-intake-clean"), **env)
    assert s["seat_selection"] == 1.0 and s["token_burn"] == 1.0


def test_recovery_exercises_real_fallback(env):
    # attempt-0 nonzero_exit must make run_seat fall back to attempt 1 (claude-opus-4-8) -> recovered
    s = db.run_fixture(_fx("f03-ship-recovery"), **env)
    assert s["recovery"] == 1.0 and s["seat_selection"] == 1.0


def test_gate_both_directions(env):
    assert db.run_fixture(_fx("f05-gate-bakeoff"), **env)["gate"] == 1.0
    assert db.run_fixture(_fx("f06-gate-nobakeoff"), **env)["gate"] == 1.0


def test_enforcement_ok_and_breach(env):
    assert db.run_fixture(_fx("f07-enforcement-ok"), **env)["enforcement"] == 1.0
    assert db.run_fixture(_fx("f08-enforcement-breach"), **env)["enforcement"] == 1.0


def test_winner_propagation(env):
    for name in ("f09-winner-draft1", "f10-winner-draft2"):
        assert db.run_fixture(_fx(name), **env)["winner_propagation"] == 1.0


def test_audit_completeness(env):
    assert db.run_fixture(_fx("f11-audit-intake"), **env)["audit_completeness"] == 1.0
    s = db.run_fixture(_fx("f12-audit-ship-recovery"), **env)
    assert s["audit_completeness"] == 1.0 and s["token_burn"] == 1.0


# ---- discrimination: the scorers actually compare (red-if-broken) -----------------------------
def test_wrong_expected_docks_the_dimension(env):
    fx = copy.deepcopy(_fx("f01-intake-clean"))
    fx["expected"]["seat_model"] = "claude-sonnet-5"      # the run returns opus-4-8, so this must fail
    assert db.run_fixture(fx, **env)["seat_selection"] == 0.0
    fx2 = copy.deepcopy(_fx("f08-enforcement-breach"))
    fx2["enforcement"]["expected_ok"] = True              # it's a real breach (ok=False), so expect 0
    assert db.run_fixture(fx2, **env)["enforcement"] == 0.0


def test_token_burn_absent_usage_fails_closed(env):
    fx = copy.deepcopy(_fx("f01-intake-clean"))
    fx["responses"]["intake"][0].pop("usage")             # no usage on a token_burn cell -> 0, never silent 1.0
    assert db.run_fixture(fx, **env)["token_burn"] == 0.0


def test_audit_on_build_seat_scores_with_gate(env):
    # #464 §E gave build an audit path via the SYNC dispatch_seat; #558 AC2 (design r7
    # item 6, A-F1) rejects a MUTATING manifest on the sync path fail-loud — build's
    # sync audit cell is therefore structurally unavailable until a capped mutating
    # composition exists (supervised dispatch owns mutating work). The honest bench
    # verdict is fail-closed, never a vacuous 1.0.
    fx = copy.deepcopy(_fx("f11-audit-intake"))
    fx["primary_seat"] = "build"
    fx["responses"] = {"build": [{"parse_status": "ok", "actual_model": "claude-sonnet-5",
                                  "payload": "x", "usage": {"input": 1, "output": 1}}]}
    assert db.run_fixture(fx, **env)["audit_completeness"] == 0.0


def test_audit_on_nonwired_seat_fails_closed(env):
    # a seat OUTSIDE the executor vocabulary (WIRED_SEATS) has no audit path -> fail closed to 0.0.
    fx = copy.deepcopy(_fx("f11-audit-intake"))
    fx["primary_seat"] = "ghost"                          # not in WIRED_SEATS
    fx["responses"] = {}
    result = db.run_fixture(fx, **env)["audit_completeness"]
    assert isinstance(result, dict) and result["score"] == 0.0 and "error" in result


# ---- matrix -----------------------------------------------------------------------------------
def test_matrix_72_cells_deterministic(env):
    fxs = db.load_fixtures()
    qf = lambda: pe.QuotaCoordinator(env["capture_root"] / "q", env["snapshot"].pool_concurrency())
    r1 = db.run_matrix(fxs, snapshot=env["snapshot"], quota_factory=qf, capture_root=env["capture_root"])
    r2 = db.run_matrix(fxs, snapshot=env["snapshot"], quota_factory=qf, capture_root=env["capture_root"])
    assert r1["n_cells"] == 72 and r1["n_fixtures"] == 12
    assert r1["cells"] == r2["cells"]                               # deterministic per-cell, not just mean
    assert all(m == 1.0 for m in r1["dimension_means"].values())    # the real code passes every fixture
    assert "reproducibility" in r1["note"].lower() or "model-independent" in r1["note"].lower()


# ---- discrimination for EVERY dimension (a rubber-stamp scorer must fail at least one) ---------
@pytest.mark.parametrize("name,patch,dim", [
    ("f03-ship-recovery", lambda fx: fx["expected"].__setitem__("recovered", False), "recovery"),
    ("f05-gate-bakeoff", lambda fx: fx["expected"].__setitem__("gate", False), "gate"),
    ("f09-winner-draft1", lambda fx: fx["expected"].__setitem__("winner_propagates", False), "winner_propagation"),
    ("f11-audit-intake", lambda fx: fx["expected"].__setitem__("audit_complete", False), "audit_completeness"),
    ("f01-intake-clean", lambda fx: fx["expected"].__setitem__("token_burn_max", 1), "token_burn"),
])
def test_dimension_discriminates(env, name, patch, dim):
    fx = copy.deepcopy(_fx(name))
    patch(fx)                       # flip the expected to the WRONG value -> the scorer must dock to 0
    assert db.run_fixture(fx, **env)[dim] == 0.0


def test_winner_draft1_and_draft2_pick_different_originals(env):
    # a stuck/inverted remap (always the same original) would make these identical; they must differ.
    verdicts = {}
    for name in ("f09-winner-draft1", "f10-winner-draft2"):
        fx = _fx(name)
        # replay the same derivation the scorer uses to get the concrete expected model
        drafts = [db._Draft(c["payload"]) for c in
                  sorted(fx["bakeoff"]["candidates"], key=lambda c: db.bakeoff_policy.DESIGN_MODELS.index(c["model"]))]
        _s, order = db.bakeoff_policy.anonymize_and_shuffle(drafts, seed=1)
        verdicts[name] = db.bakeoff_policy.DESIGN_MODELS[order[fx["bakeoff"]["winner_draft"] - 1]]
    assert verdicts["f09-winner-draft1"] != verdicts["f10-winner-draft2"]


# ---- fail-closed guards -----------------------------------------------------------------------
def test_short_responses_list_fails_closed(env):
    fx = copy.deepcopy(_fx("f03-ship-recovery"))
    fx["responses"]["ship"] = fx["responses"]["ship"][:1]   # only attempt 0 (a fail) -> fallback has no response
    result = db.run_fixture(fx, **env)["recovery"]
    assert isinstance(result, dict) and result["score"] == 0.0 and "error" in result


def test_empty_fixture_dir_fails_closed(tmp_path):
    with pytest.raises(db.FixtureError):
        db.load_fixtures(tmp_path)


# ---- CLI ---------------------------------------------------------------------------------------
def test_cli_runs_and_writes_report():
    env = {**os.environ, "PYTHONPATH": str(HOOKS)}
    proc = subprocess.run([sys.executable, str(HOOKS / "driver_bench_lib.py")],
                          cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-500:]
    assert "72 cells" in proc.stdout
    report = json.loads((REPO / "docs" / "measurements" / "driver-bench" / "stubbed-baseline.json").read_text())
    assert report["n_cells"] == 72 and all(m == 1.0 for m in report["dimension_means"].values())


# --- #445: bench resolves through the shared helper ---

class TestBenchTableResolution:
    def test_default_resolution_matches_package_digest(self):
        pe = db._pe()
        rt = db.resolve_bench_table()
        assert rt.source == "package_default"
        assert rt.snapshot.config_digest == pe.snapshot_from_file(TABLE).config_digest

    def test_tmp_project_override_no_monkeypatch(self, tmp_path):
        import json as _json
        repo = tmp_path / "proj"
        dst = repo / "conf" / "t.json"
        dst.parent.mkdir(parents=True)
        dst.write_bytes(TABLE.read_bytes())
        (repo / ".rawgentic.json").write_text(_json.dumps({
            "version": 1, "project": {"type": "application"},
            "repo": {"fullName": "o/f", "defaultBranch": "main"},
            "phaseExecutorTable": {"version": 1, "file": "conf/t.json"}}), encoding="utf-8")
        rt = db.resolve_bench_table(repo)
        assert rt.source == "project_file"
        assert rt.path == dst.resolve()

    def test_import_failure_is_structured(self, monkeypatch):
        def boom():
            raise ImportError("forced")
        monkeypatch.setattr(db, "_pe", boom)
        with pytest.raises(db.FixtureError, match="phase_executor unavailable"):
            db.resolve_bench_table()

    def test_module_global_table_constant_retired(self):
        assert not hasattr(db, "TABLE")


# ---- #449 T1: live dispatch via the real adapter path ------------------------------------------
class TestLiveDispatch:
    def _req(self):
        # a minimal AdapterRequest-shaped object is NOT needed — the dispatch seam passes req
        # through opaquely; a stub with the two attrs the seam reads suffices.
        class R:  # pylint: disable=too-few-public-methods
            seat = "intake"
            requested_model = "claude-opus-4-8"
            transport = "native"
        return R()

    def test_delegates_to_dispatch_real_with_threaded_kwargs(self):
        calls = {}
        sentinel = object()

        def fake_real(engine, req, *, run_id, attempt_id, capture_root, digest, queued_ms, fallback_reason):
            calls.update(engine=engine, req=req, run_id=run_id, attempt_id=attempt_id,
                         capture_root=capture_root, digest=digest, queued_ms=queued_ms,
                         fallback_reason=fallback_reason)
            return sentinel

        d = db.live_dispatch(dispatch_real=fake_real)
        req = self._req()
        out = d("claude", req, run_id="r1", attempt_id="0-a", capture_root="/tmp/c",
                digest="dg", queued_ms=5, fallback_reason=None)
        assert out is sentinel
        assert calls["engine"] == "claude" and calls["req"] is req
        assert calls["run_id"] == "r1" and calls["attempt_id"] == "0-a"
        assert calls["digest"] == "dg" and calls["queued_ms"] == 5

    def test_composition_refusal_translates_to_unsupported(self):
        def refusing(engine, req, **kw):
            raise pe.contract.CompositionError("mutating manifest on sync path (#558 AC2)")

        d = db.live_dispatch(dispatch_real=refusing)
        with pytest.raises(db.UnsupportedCellError, match="558"):
            d("claude", self._req(), run_id="r", attempt_id="0-a", capture_root="c",
              digest="d", queued_ms=0, fallback_reason=None)

    def test_other_errors_are_not_swallowed(self):
        def broken(engine, req, **kw):
            raise ValueError("some other failure")

        d = db.live_dispatch(dispatch_real=broken)
        with pytest.raises(ValueError):
            d("claude", self._req(), run_id="r", attempt_id="0-a", capture_root="c",
              digest="d", queued_ms=0, fallback_reason=None)

    def test_unsupported_cell_is_typed_not_zero(self, env):
        # an UnsupportedCellError inside a scorer becomes a typed report cell
        # {"score": None, "status": "unsupported"} — distinguishable from a genuine 0.0 failure.
        fx = copy.deepcopy(_fx("f01-intake-clean"))

        def raise_unsupported(*a, **k):
            raise db.UnsupportedCellError("build-seat sync dispatch unsupported (#558 A-F1)")

        original = db._SCORERS["seat_selection"]
        db._SCORERS["seat_selection"] = raise_unsupported
        try:
            cell = db.run_fixture(fx, **env)["seat_selection"]
        finally:
            db._SCORERS["seat_selection"] = original
        assert isinstance(cell, dict) and cell["status"] == "unsupported"
        assert cell["score"] is None and "558" in cell["error"]

    def test_matrix_mean_excludes_unsupported_cells(self, env):
        # dimension_means must SKIP {"score": None} unsupported cells (a missing capability is
        # not a failure) while still counting genuine 0.0 error cells.
        fxs = [_fx("f01-intake-clean")]

        def raise_unsupported(*a, **k):
            raise db.UnsupportedCellError("unsupported")

        original = db._SCORERS["token_burn"]
        db._SCORERS["token_burn"] = raise_unsupported
        try:
            qf = lambda: pe.QuotaCoordinator(env["capture_root"] / "q2", env["snapshot"].pool_concurrency())
            r = db.run_matrix(fxs, snapshot=env["snapshot"], quota_factory=qf,
                              capture_root=env["capture_root"])
        finally:
            db._SCORERS["token_burn"] = original
        assert r["dimension_means"]["token_burn"] is None          # every cell unsupported -> no mean
        assert r["dimension_means"]["seat_selection"] == 1.0       # untouched dims still score


class TestGlmCredential:
    def test_absent_credential_detected(self, monkeypatch):
        for var in ("ZHIPUAI_API_KEY", "ZHIPU_API_KEY", "GLM_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        assert db.glm_credential_present() is False

    def test_present_credential_detected(self, monkeypatch):
        monkeypatch.setenv("ZHIPUAI_API_KEY", "k")
        assert db.glm_credential_present() is True


# ---- #449 T2: live matrix entry + HARD ceilings + live report -----------------------------------
def _no_glm(monkeypatch):
    for var in ("ZHIPUAI_API_KEY", "ZHIPU_API_KEY", "GLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)


class TestLiveMatrix:
    def _live_env(self, env, fx):
        """A live=True run with the fixture's OWN canned responses injected as the 'real'
        dispatch — proves the live wiring (threading, ceilings, report) with zero live calls."""
        canned = db._seat_dispatch(fx)

        def fake_real(engine, req, **kw):
            return canned(engine, req, **kw)
        return fake_real

    def test_live_matrix_routes_through_injected_dispatch(self, env, monkeypatch):
        _no_glm(monkeypatch)
        fx = _fx("f01-intake-clean")
        calls = []
        fake = self._live_env(env, fx)

        def counting(engine, req, **kw):
            calls.append(req.seat)
            return fake(engine, req, **kw)

        qf = lambda: pe.QuotaCoordinator(env["capture_root"] / "ql", env["snapshot"].pool_concurrency())
        r = db.run_matrix([fx], snapshot=env["snapshot"], quota_factory=qf,
                          capture_root=env["capture_root"], models=("live",), reps=1,
                          live=True, dispatch=db.live_dispatch(dispatch_real=counting))
        assert r["n_cells"] == 1 and calls, "live cells must flow through the injected dispatch"
        assert r["live"] is True
        assert "live" in r["note"].lower()

    def test_hard_call_ceiling_aborts(self, env, monkeypatch):
        _no_glm(monkeypatch)
        fxs = [_fx("f01-intake-clean"), _fx("f03-ship-recovery")]
        fake = self._live_env(env, fxs[0])
        seen = []

        def counting(engine, req, **kw):
            seen.append(1)
            # respond from whichever fixture matches the seat
            for fx in fxs:
                if req.seat in (fx.get("responses") or {}):
                    return db._seat_dispatch(fx)(engine, req, **kw)
            return fake(engine, req, **kw)

        qf = lambda: pe.QuotaCoordinator(env["capture_root"] / "qc", env["snapshot"].pool_concurrency())
        r = db.run_matrix(fxs, snapshot=env["snapshot"], quota_factory=qf,
                          capture_root=env["capture_root"], models=("live",), reps=1,
                          live=True, dispatch=db.live_dispatch(dispatch_real=counting),
                          max_calls=1)
        assert r["aborted"] == "budget_exceeded"
        assert len(seen) <= 2, "must stop dispatching promptly after the ceiling"

    def test_budget_ceiling_aborts_on_reported_cost(self, env, monkeypatch):
        _no_glm(monkeypatch)
        fx = copy.deepcopy(_fx("f01-intake-clean"))
        # make the canned response carry a reported cost the guard can accumulate
        fx["responses"]["intake"][0]["usage"] = {"input": 10, "output": 5, "cost_usd": 9.99}
        qf = lambda: pe.QuotaCoordinator(env["capture_root"] / "qb", env["snapshot"].pool_concurrency())
        r = db.run_matrix([fx, fx], snapshot=env["snapshot"], quota_factory=qf,
                          capture_root=env["capture_root"], models=("live",), reps=1,
                          live=True, dispatch=db.live_dispatch(dispatch_real=db._seat_dispatch(fx)),
                          max_budget_usd=5.0)
        assert r["aborted"] == "budget_exceeded"

    def test_winner_cells_skip_without_glm_credential(self, env, monkeypatch):
        _no_glm(monkeypatch)
        fx = _fx("f09-winner-draft1")
        cell = db.run_fixture(fx, **env, live=True)["winner_propagation"]
        assert isinstance(cell, dict) and cell["status"] == "skipped"
        assert "glm" in cell["reason"].lower()

    def test_live_report_written_with_cost_line(self, env, tmp_path, monkeypatch):
        _no_glm(monkeypatch)
        fx = _fx("f01-intake-clean")
        qf = lambda: pe.QuotaCoordinator(env["capture_root"] / "qr", env["snapshot"].pool_concurrency())
        r = db.run_matrix([fx], snapshot=env["snapshot"], quota_factory=qf,
                          capture_root=env["capture_root"], models=("live",), reps=1,
                          live=True, dispatch=db.live_dispatch(dispatch_real=db._seat_dispatch(fx)))
        out = db.write_live_report(r, out_dir=tmp_path)
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data["live"] is True
        assert "billable_calls" in data["cost"] and "reported_cost_usd" in data["cost"]
        assert "dispatches" in data  # per-seat requested/actual observability

    def test_live_dispatch_records_observability(self, env, monkeypatch):
        _no_glm(monkeypatch)
        fx = _fx("f01-intake-clean")
        qf = lambda: pe.QuotaCoordinator(env["capture_root"] / "qo", env["snapshot"].pool_concurrency())
        r = db.run_matrix([fx], snapshot=env["snapshot"], quota_factory=qf,
                          capture_root=env["capture_root"], models=("live",), reps=1,
                          live=True, dispatch=db.live_dispatch(dispatch_real=db._seat_dispatch(fx)))
        assert r["dispatches"], "live run must record per-dispatch observability"
        d0 = r["dispatches"][0]
        assert {"seat", "requested_model", "actual_model", "session_policy"} <= set(d0)

    def test_cli_live_refuses_without_run_live_env(self):
        env2 = {**os.environ, "PYTHONPATH": str(HOOKS)}
        env2.pop("RUN_LIVE", None)
        proc = subprocess.run([sys.executable, str(HOOKS / "driver_bench_lib.py"), "--live"],
                              cwd=str(REPO), env=env2, capture_output=True, text=True, timeout=60)
        assert proc.returncode != 0
        assert "RUN_LIVE" in (proc.stderr + proc.stdout)


# ---- #449 T4: the deferred LIVE cell (#138 — real billable calls, owner-attended) ---------------
@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1",
                    reason="live driver-bench: REAL billable model calls; set RUN_LIVE=1 (glm judge "
                           "cells additionally need ZHIPUAI_API_KEY; see docs/model-routing.md)")
def test_live_matrix_end_to_end_small():
    """One tightly-capped live pass (3 fixtures, ceilings 8 calls / $3) through the REAL
    adapters — the #449 deferred verification cell. Asserts the report SHAPE and the
    ceiling discipline, not model quality (that's the campaign's question)."""
    pe2 = db._pe()
    rt = db.resolve_bench_table()
    snap = rt.snapshot
    tmp = REPO / ".rawgentic" / "driver-bench-live-test"
    import shutil as _sh
    _sh.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    fxs = [db.load_fixture(db.FIXTURE_DIR / f"{n}.json")
           for n in ("f01-intake-clean", "f05-gate-bakeoff", "f07-enforcement-ok")]
    r = db.run_matrix(fxs, snapshot=snap,
                      quota_factory=lambda: pe2.QuotaCoordinator(tmp / "q", snap.pool_concurrency()),
                      capture_root=tmp, models=("live",), reps=1, live=True,
                      dispatch=db.live_dispatch(), max_calls=8, max_budget_usd=3.0)
    assert r["live"] is True and r["cost"]["billable_calls"] <= 8
    out = db.write_live_report(r)
    assert Path(out).exists()
