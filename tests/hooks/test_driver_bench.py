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


def test_audit_on_nonwired_seat_fails_closed(env):
    fx = copy.deepcopy(_fx("f11-audit-intake"))
    fx["primary_seat"] = "build"                          # build has no audit path (hard-denied)
    fx["responses"] = {"build": [{"parse_status": "ok", "actual_model": "claude-sonnet-5", "payload": "x"}]}
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
