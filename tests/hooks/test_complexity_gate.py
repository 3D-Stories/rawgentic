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
