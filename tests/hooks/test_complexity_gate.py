"""#429 — deterministic complexity gate (plan_lib.needs_bakeoff): triggers, fail-closed metadata,
security-surface glob, reason codes, snapshot + policy digest."""
import sys
import types
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent.parent / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
import complexity_gate as pl  # noqa: E402


def _clean(**over):
    task = {"risk_level": "standard"}
    issue = {"complexity": "standard"}
    plan_est = {"files": ["hooks/foo.py"], "lines": 10, "file_count": 1}
    task.update(over.get("task", {}))
    issue.update(over.get("issue", {}))
    plan_est.update(over.get("plan_est", {}))
    return task, issue, plan_est


# --- hits_security_surface ---------------------------------------------------------------------
@pytest.mark.parametrize("path", ["src/auth/login.py", "config/secrets.yaml", "billing/payment.rs",
                                  "db/migrations/001.sql", "lib/crypto.py", ".github/workflows/ci.yml",
                                  "ci/deploy.sh"])
def test_security_surface_hits(path):
    assert pl.hits_security_surface([path]) is True


@pytest.mark.parametrize("path", ["src/author.py", "lib/special.py", "docs/readme.md", "notice.txt"])
def test_security_surface_misses(path):
    assert pl.hits_security_surface([path]) is False


def test_security_surface_non_list_is_false():
    assert pl.hits_security_surface("auth") is False
    assert pl.hits_security_surface(None) is False


# --- needs_bakeoff: triggers -------------------------------------------------------------------
def test_all_clean_no_bakeoff():
    task, issue, plan_est = _clean()
    d = pl.needs_bakeoff(task, issue, plan_est)
    assert d.decision is False and d.reason_codes == ()


def test_risk_high_triggers():
    task, issue, plan_est = _clean(task={"risk_level": "high"})
    d = pl.needs_bakeoff(task, issue, plan_est)
    assert d.decision is True and "risk_high" in d.reason_codes


def test_complexity_complex_triggers():
    task, issue, plan_est = _clean(issue={"complexity": "complex"})
    d = pl.needs_bakeoff(task, issue, plan_est)
    assert d.decision is True and "complexity_complex" in d.reason_codes


def test_security_surface_triggers():
    task, issue, plan_est = _clean(plan_est={"files": ["src/auth/login.py"]})
    d = pl.needs_bakeoff(task, issue, plan_est)
    assert d.decision is True and "security_surface" in d.reason_codes


def test_diff_lines_over_triggers():
    task, issue, plan_est = _clean(plan_est={"lines": 401})
    d = pl.needs_bakeoff(task, issue, plan_est)
    assert d.decision is True and "diff_lines_over" in d.reason_codes


def test_file_count_over_triggers():
    task, issue, plan_est = _clean(plan_est={"file_count": 11})
    d = pl.needs_bakeoff(task, issue, plan_est)
    assert d.decision is True and "file_count_over" in d.reason_codes


def test_cfg_override_thresholds():
    task, issue, plan_est = _clean(plan_est={"lines": 50, "file_count": 3})
    d = pl.needs_bakeoff(task, issue, plan_est, cfg={"BAKEOFF_DIFF_LINES": 40, "BAKEOFF_FILE_COUNT": 2})
    assert d.decision is True
    assert "diff_lines_over" in d.reason_codes and "file_count_over" in d.reason_codes


# --- needs_bakeoff: fail-closed on missing/invalid metadata ------------------------------------
def test_missing_risk_level_fails_closed():
    d = pl.needs_bakeoff({}, {"complexity": "standard"}, {"files": [], "lines": 1, "file_count": 1})
    assert d.decision is True and "fail_closed:risk_level=missing" in d.reason_codes


def test_invalid_complexity_fails_closed():
    d = pl.needs_bakeoff({"risk_level": "standard"}, {"complexity": "simple"},
                         {"files": [], "lines": 1, "file_count": 1})
    assert d.decision is True and any(r.startswith("fail_closed:complexity=") for r in d.reason_codes)


def test_missing_files_fails_closed():
    d = pl.needs_bakeoff({"risk_level": "standard"}, {"complexity": "standard"},
                         {"lines": 1, "file_count": 1})
    assert d.decision is True and "fail_closed:files=missing" in d.reason_codes


@pytest.mark.parametrize("bad", ["10", None, True, 1.5])
def test_invalid_lines_fails_closed(bad):
    d = pl.needs_bakeoff({"risk_level": "standard"}, {"complexity": "standard"},
                         {"files": [], "lines": bad, "file_count": 1})
    assert d.decision is True and "fail_closed:lines=invalid" in d.reason_codes


def test_bool_is_not_valid_int_metadata():
    # bool is an int subclass — must NOT be accepted as a lines/file_count measurement
    d = pl.needs_bakeoff({"risk_level": "standard"}, {"complexity": "standard"},
                         {"files": [], "lines": True, "file_count": False})
    assert "fail_closed:lines=invalid" in d.reason_codes and "fail_closed:file_count=invalid" in d.reason_codes


def test_reason_codes_accumulate():
    d = pl.needs_bakeoff({"risk_level": "high"}, {"complexity": "complex"},
                         {"files": ["src/auth.py"], "lines": 999, "file_count": 99})
    for r in ("risk_high", "complexity_complex", "security_surface", "diff_lines_over", "file_count_over"):
        assert r in d.reason_codes


# --- snapshot + policy digest ------------------------------------------------------------------
def test_snapshot_and_digest_present_and_stable():
    task, issue, plan_est = _clean()
    d1 = pl.needs_bakeoff(task, issue, plan_est)
    d2 = pl.needs_bakeoff(task, issue, plan_est)
    assert d1.policy_digest.startswith("sha256:")
    assert d1.policy_digest == d2.policy_digest  # deterministic for identical inputs
    assert d1.input_snapshot["thresholds"]["BAKEOFF_DIFF_LINES"] == pl.DEFAULT_BAKEOFF_DIFF_LINES
    assert set(d1.input_snapshot) >= {"risk_level", "complexity", "security_surface_hit", "lines",
                                      "file_count", "thresholds"}


def test_digest_changes_with_inputs():
    a = pl.needs_bakeoff(*_clean())
    b = pl.needs_bakeoff(*_clean(plan_est={"lines": 42}))
    assert a.policy_digest != b.policy_digest


def test_accepts_objects_not_only_dicts():
    task = types.SimpleNamespace(risk_level="high")
    issue = types.SimpleNamespace(complexity="standard")
    plan_est = types.SimpleNamespace(files=["x.py"], lines=1, file_count=1)
    d = pl.needs_bakeoff(task, issue, plan_est)
    assert d.decision is True and "risk_high" in d.reason_codes


# --- Step-11 F1: non-serializable metadata fail-CLOSES, never crashes _policy_digest -----------
import enum  # noqa: E402


class _Cx(enum.Enum):
    COMPLEX = "complex"


@pytest.mark.parametrize("bad_rl", [_Cx.COMPLEX, b"high", {"high"}, object()])
def test_non_serializable_risk_level_fails_closed_not_crash(bad_rl):
    d = pl.needs_bakeoff({"risk_level": bad_rl}, {"complexity": "standard"},
                         {"files": [], "lines": 1, "file_count": 1})
    assert d.decision is True
    assert any(r.startswith("fail_closed:risk_level=") for r in d.reason_codes)
    assert d.policy_digest.startswith("sha256:")  # digest computed, did not raise


def test_non_serializable_complexity_fails_closed_not_crash():
    d = pl.needs_bakeoff({"risk_level": "standard"}, {"complexity": b"complex"},
                         {"files": [], "lines": 1, "file_count": 1})
    assert d.decision is True
    assert any(r.startswith("fail_closed:complexity=") for r in d.reason_codes)


# --- Step-11 F2: unparseable cfg threshold fail-CLOSES (does not silently loosen to default) ----
def test_bad_threshold_fails_closed_not_silent_loosen():
    # operator meant a strict 200 but wrote it as a string; must NOT quietly run at the looser 400
    d = pl.needs_bakeoff({"risk_level": "standard"}, {"complexity": "standard"},
                         {"files": [], "lines": 300, "file_count": 1},
                         cfg={"BAKEOFF_DIFF_LINES": "200"})
    assert d.decision is True
    assert "fail_closed:BAKEOFF_DIFF_LINES_invalid" in d.reason_codes


def test_absent_threshold_uses_default_silently():
    # a genuinely-absent threshold is fine (default), no fail_closed reason
    d = pl.needs_bakeoff({"risk_level": "standard"}, {"complexity": "standard"},
                         {"files": [], "lines": 10, "file_count": 1}, cfg={})
    assert d.decision is False
    assert not any("threshold" in r or "BAKEOFF" in r for r in d.reason_codes)


# --- verified_decision: single-sourced gate authentication (#464, extracted from bakeoff_policy) --
def _gate(*, bake):
    """A genuine GateDecision (snapshot+digest+decision consistent). bake=True -> risk_high fires."""
    task = {"risk_level": "high" if bake else "standard"}
    issue = {"complexity": "standard"}
    plan_est = {"files": ["src/app.py"], "lines": 1, "file_count": 1}
    return pl.needs_bakeoff(task, issue, plan_est, cfg={})


def test_verified_decision_returns_snapshot_decision_true():
    gd = _gate(bake=True)
    assert gd.decision is True
    assert pl.verified_decision(gd) is True


def test_verified_decision_returns_snapshot_decision_false():
    gd = _gate(bake=False)
    assert gd.decision is False
    assert pl.verified_decision(gd) is False


def test_verified_decision_uses_snapshot_not_decision_field():
    # authoritative bool comes from the digest-verified snapshot, never the (unbound) decision field
    real = _gate(bake=True)
    tampered = pl.GateDecision(decision=False, reason_codes=(), input_snapshot=real.input_snapshot,
                               policy_digest=real.policy_digest)
    assert pl.verified_decision(tampered) is True


def test_verified_decision_tampered_digest_raises():
    gd = pl.GateDecision(decision=True, reason_codes=(), input_snapshot={"a": 1},
                         policy_digest="sha256:deadbeef")
    with pytest.raises(pl.GateTamperError, match="policy_digest mismatch"):
        pl.verified_decision(gd)


def test_verified_decision_expected_context_match_passes():
    gd = _gate(bake=True)  # snapshot risk_level == "high"
    assert pl.verified_decision(gd, expected_context={"risk_level": "high"}) is True


def test_verified_decision_expected_context_mismatch_raises_naming_key():
    gd = _gate(bake=True)  # snapshot risk_level == "high", not "standard"
    with pytest.raises(pl.GateTamperError, match="context mismatch") as exc:
        pl.verified_decision(gd, expected_context={"risk_level": "standard"})
    assert "risk_level" in str(exc.value)  # names the offending key


def test_verified_decision_expected_context_none_is_digest_only():
    # bakeoff carve-out: ctx=None skips the cross-check; the digest is still enforced
    gd = _gate(bake=False)
    assert pl.verified_decision(gd, expected_context=None) is False


def test_verified_decision_context_mismatch_does_not_leak_value():
    # the expected value may carry plan text -> it must never appear in the raised message
    gd = _gate(bake=True)
    with pytest.raises(pl.GateTamperError) as exc:
        pl.verified_decision(gd, expected_context={"risk_level": "SECRET_PLAN_TEXT"})
    assert "SECRET_PLAN_TEXT" not in str(exc.value)


def test_decision_from_snapshot_matches_needs_bakeoff():
    # #428 M7 single-source contract: the decision re-derived from a snapshot ALONE must equal the
    # decision needs_bakeoff computed, across representative inputs — incl. a threshold-invalid case
    # (now recorded in the snapshot so it is re-derivable) and a clean no-bake-off case.
    cases = [
        ({"risk_level": "high"}, {"complexity": "standard"}, {"files": ["a.py"], "lines": 1, "file_count": 1}, {}),
        ({"risk_level": "standard"}, {"complexity": "complex"}, {"files": ["a.py"], "lines": 1, "file_count": 1}, {}),
        ({"risk_level": "standard"}, {"complexity": "standard"}, {"files": [".github/ci.yml"], "lines": 1, "file_count": 1}, {}),
        ({"risk_level": "standard"}, {"complexity": "standard"}, {"files": ["a.py"], "lines": 1, "file_count": 1}, {}),
        ({"risk_level": "standard"}, {"complexity": "standard"}, {"files": ["a.py"], "lines": 1, "file_count": 1},
         {"BAKEOFF_DIFF_LINES": "not-an-int"}),  # threshold-invalid -> fail-closed, recorded in snapshot
    ]
    for task, issue, plan_est, cfg in cases:
        gd = pl.needs_bakeoff(task, issue, plan_est, cfg=cfg)
        assert pl.decision_from_snapshot(gd.input_snapshot) == gd.decision
        assert pl.reasons_from_snapshot(gd.input_snapshot) == gd.reason_codes


def test_verified_decision_empty_context_refused_464():
    """#464 Step-8a (R2 Medium): {} must NOT silently degrade to digest-only — only None is the
    sanctioned carve-out. An emptied programmatic context would otherwise disable the
    stale-decision defense with no error (adopted-P5: omission can never silently disable)."""
    gd = _gate(bake=True)
    with pytest.raises(pl.GateTamperError, match="empty expected_context"):
        pl.verified_decision(gd, expected_context={})


def test_verified_decision_absent_key_with_none_value_refused_464():
    """#464 Step-8a (R1 Low): expected_context={'k': None} where k is ABSENT from the snapshot
    must fail (missing-key), not silently pass via None == .get() collapse."""
    gd = _gate(bake=True)
    assert "no_such_key" not in gd.input_snapshot
    with pytest.raises(pl.GateTamperError, match="missing"):
        pl.verified_decision(gd, expected_context={"no_such_key": None})
