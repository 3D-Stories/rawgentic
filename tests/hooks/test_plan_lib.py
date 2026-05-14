"""Tests for hooks/plan_lib.py — WF2 tiered code review utilities (#73).

Covers:
- Env-var loading with frozen Final constants + clamping
- parse_tasks with format contract + fail-closed on missing riskLevel
- compute_risk_ratio + check_ratio_band (8 criteria, N<3 edge, ≥80% decompose)
- should_promote heuristics
- format_promotion_note
- consume_loopback per-source budgets
- append_review_log + assert_review_coverage
- get_deferred_findings + assert_no_unresolved_high_deferrals
- scan_prior_commits_for_trigger
"""
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))


def _reload_plan_lib(env_override: dict | None = None):
    """Reload plan_lib with optional env overrides applied first.

    plan_lib reads env vars at import time and freezes them as Final.
    To test different env values, we must reload the module.
    """
    saved = {}
    if env_override:
        for k, v in env_override.items():
            saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    try:
        if "plan_lib" in sys.modules:
            mod = importlib.reload(sys.modules["plan_lib"])
        else:
            import plan_lib as mod
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# --- Env-var loading + clamping ---

class TestEnvVars:
    def test_defaults(self):
        mod = _reload_plan_lib(
            {"WF2_HIGH_RISK_RATIO_WARN_PCT": None, "WF2_HIGH_RISK_RATIO_HALT_PCT": None}
        )
        assert mod.WF2_HIGH_RISK_RATIO_WARN_PCT == 30
        assert mod.WF2_HIGH_RISK_RATIO_HALT_PCT == 50
        assert mod.PER_TASK_REVIEW_CONFIDENCE_THRESHOLD == 0.80

    def test_valid_override(self):
        mod = _reload_plan_lib(
            {"WF2_HIGH_RISK_RATIO_WARN_PCT": "20", "WF2_HIGH_RISK_RATIO_HALT_PCT": "40"}
        )
        assert mod.WF2_HIGH_RISK_RATIO_WARN_PCT == 20
        assert mod.WF2_HIGH_RISK_RATIO_HALT_PCT == 40

    @pytest.mark.parametrize(
        "warn_val,expected",
        [
            ("999", 95),    # over-cap clamps to 95
            ("-5", 5),      # under-cap clamps to 5
            ("0", 5),       # below min
            ("5", 5),       # at min
            ("95", 95),     # at max
        ],
    )
    def test_warn_clamping(self, warn_val, expected):
        mod = _reload_plan_lib({"WF2_HIGH_RISK_RATIO_WARN_PCT": warn_val})
        assert mod.WF2_HIGH_RISK_RATIO_WARN_PCT == expected

    @pytest.mark.parametrize(
        "halt_val,expected",
        [
            ("999", 95),
            ("90", 90),
            ("95", 95),
        ],
    )
    def test_halt_clamping_above_min(self, halt_val, expected):
        # Default warn=30, so halt floor is 40 via the halt>=warn+10 rule.
        # These cases test pure clamping above the floor.
        mod = _reload_plan_lib({"WF2_HIGH_RISK_RATIO_HALT_PCT": halt_val})
        assert mod.WF2_HIGH_RISK_RATIO_HALT_PCT == expected

    def test_halt_clamping_at_floor(self):
        # When warn is at its minimum (5), halt floor is 15.
        # halt=5 should clamp to 10 then enforce halt>=warn+10=15.
        mod = _reload_plan_lib(
            {"WF2_HIGH_RISK_RATIO_WARN_PCT": "5", "WF2_HIGH_RISK_RATIO_HALT_PCT": "5"}
        )
        assert mod.WF2_HIGH_RISK_RATIO_HALT_PCT == 15

    def test_halt_must_exceed_warn_by_10(self):
        # If halt=35 and warn=30, halt must be adjusted to >= 40
        mod = _reload_plan_lib(
            {"WF2_HIGH_RISK_RATIO_WARN_PCT": "30", "WF2_HIGH_RISK_RATIO_HALT_PCT": "35"}
        )
        assert mod.WF2_HIGH_RISK_RATIO_HALT_PCT >= 40

    @pytest.mark.parametrize(
        "bad_val",
        ["", "NaN", "30; rm -rf", "中文", "30.5", "thirty"],
    )
    def test_malicious_warn_fallback_to_default(self, bad_val):
        mod = _reload_plan_lib({"WF2_HIGH_RISK_RATIO_WARN_PCT": bad_val})
        assert mod.WF2_HIGH_RISK_RATIO_WARN_PCT == 30  # default

    @pytest.mark.parametrize(
        "bad_val",
        ["", "NaN", "50; echo pwned", "ÿÿ", "50.0", "fifty"],
    )
    def test_malicious_halt_fallback_to_default(self, bad_val):
        mod = _reload_plan_lib({"WF2_HIGH_RISK_RATIO_HALT_PCT": bad_val})
        assert mod.WF2_HIGH_RISK_RATIO_HALT_PCT == 50  # default

    def test_frozen_at_import(self):
        """Mutating os.environ after import must not change constants."""
        mod = _reload_plan_lib({"WF2_HIGH_RISK_RATIO_WARN_PCT": "30"})
        original = mod.WF2_HIGH_RISK_RATIO_WARN_PCT
        os.environ["WF2_HIGH_RISK_RATIO_WARN_PCT"] = "10"
        try:
            assert mod.WF2_HIGH_RISK_RATIO_WARN_PCT == original
        finally:
            del os.environ["WF2_HIGH_RISK_RATIO_WARN_PCT"]

    def test_negative_halt_clamps_to_min_then_floor(self):
        # halt=-100 -> clamp to 10 -> halt>=warn(30)+10=40 -> 40
        mod = _reload_plan_lib({"WF2_HIGH_RISK_RATIO_HALT_PCT": "-100"})
        assert mod.WF2_HIGH_RISK_RATIO_HALT_PCT == 40


# --- parse_tasks: format contract + fail-closed ---

class TestParseTasks:
    def test_wellformed_single_task(self):
        mod = _reload_plan_lib()
        plan = """
### Task 1: implement foo
- riskLevel: standard
- some content

### Other heading
not a task
"""
        tasks = mod.parse_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].id == "1"
        assert tasks[0].title == "implement foo"
        assert tasks[0].risk_level == "standard"
        assert tasks[0].reason is None

    def test_high_with_reason(self):
        mod = _reload_plan_lib()
        plan = """
### Task 2: refactor auth
- riskLevel: high (security surface)
- some content
"""
        tasks = mod.parse_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].risk_level == "high"
        assert tasks[0].reason == "security surface"

    def test_multiple_tasks(self):
        mod = _reload_plan_lib()
        plan = """
### Task 1: foo
- riskLevel: standard

### Task 2: bar
- riskLevel: high (module boundary)

### Task 3.5: baz
- riskLevel: standard
"""
        tasks = mod.parse_tasks(plan)
        assert [t.id for t in tasks] == ["1", "2", "3.5"]
        assert [t.risk_level for t in tasks] == ["standard", "high", "standard"]

    def test_missing_risklevel_raises(self):
        mod = _reload_plan_lib()
        plan = """
### Task 1: forgot to tag
- some content but no risk level
"""
        with pytest.raises(mod.PlanFormatError, match="riskLevel"):
            mod.parse_tasks(plan)

    def test_invalid_risklevel_value_raises(self):
        mod = _reload_plan_lib()
        plan = """
### Task 1: bad level
- riskLevel: super-high
"""
        with pytest.raises(mod.PlanFormatError, match="riskLevel"):
            mod.parse_tasks(plan)

    def test_empty_plan_returns_empty(self):
        mod = _reload_plan_lib()
        assert mod.parse_tasks("") == []
        assert mod.parse_tasks("# Some doc\n\nNo tasks here.\n") == []

    def test_partial_missing_one_raises(self):
        """If ANY task lacks riskLevel, parse fails — fail-closed."""
        mod = _reload_plan_lib()
        plan = """
### Task 1: tagged
- riskLevel: standard

### Task 2: untagged
- forgot the tag
"""
        with pytest.raises(mod.PlanFormatError):
            mod.parse_tasks(plan)


# --- compute_risk_ratio + check_ratio_band ---

def _t(id_, lvl, reason=None):
    """Shortcut for Task fixtures."""
    from plan_lib import Task
    return Task(id=id_, title=f"task {id_}", risk_level=lvl, reason=reason)


class TestRiskRatio:
    def test_all_standard(self):
        mod = _reload_plan_lib()
        tasks = [_t(str(i), "standard") for i in range(1, 6)]
        ratio, high, total = mod.compute_risk_ratio(tasks)
        assert (ratio, high, total) == (0.0, 0, 5)

    def test_all_high(self):
        mod = _reload_plan_lib()
        tasks = [_t(str(i), "high", "x") for i in range(1, 4)]
        ratio, high, total = mod.compute_risk_ratio(tasks)
        assert ratio == pytest.approx(1.0)
        assert (high, total) == (3, 3)

    def test_mixed(self):
        mod = _reload_plan_lib()
        tasks = [_t("1", "high", "x"), _t("2", "standard"), _t("3", "high", "y"), _t("4", "standard")]
        ratio, high, total = mod.compute_risk_ratio(tasks)
        assert ratio == 0.5
        assert (high, total) == (2, 4)

    def test_empty(self):
        mod = _reload_plan_lib()
        ratio, high, total = mod.compute_risk_ratio([])
        assert (ratio, high, total) == (0.0, 0, 0)


class TestCheckRatioBand:
    def test_skip_for_small_plans(self):
        mod = _reload_plan_lib()
        assert mod.check_ratio_band(0.5, 2) == "skip"
        assert mod.check_ratio_band(1.0, 1) == "skip"
        assert mod.check_ratio_band(0.0, 0) == "skip"

    def test_pass(self):
        mod = _reload_plan_lib()
        # default warn=30, halt=50
        assert mod.check_ratio_band(0.0, 10) == "implausible_zero"  # 0% with N>=5
        assert mod.check_ratio_band(0.15, 10) == "pass"
        assert mod.check_ratio_band(0.30, 10) == "pass"  # at warn boundary
        assert mod.check_ratio_band(0.2999, 10) == "pass"

    def test_warn(self):
        mod = _reload_plan_lib()
        assert mod.check_ratio_band(0.31, 10) == "warn"
        assert mod.check_ratio_band(0.49, 10) == "warn"

    def test_halt(self):
        mod = _reload_plan_lib()
        assert mod.check_ratio_band(0.51, 10) == "halt"
        assert mod.check_ratio_band(0.79, 10) == "halt"

    def test_decompose_at_80(self):
        mod = _reload_plan_lib()
        assert mod.check_ratio_band(0.80, 10) == "decompose"
        assert mod.check_ratio_band(1.0, 10) == "decompose"

    def test_zero_below_implausibility_floor(self):
        mod = _reload_plan_lib()
        # N<3: skip. 3 <= N < 5 with 0%: pass (small plan). N>=5: implausible_zero.
        assert mod.check_ratio_band(0.0, 2) == "skip"
        assert mod.check_ratio_band(0.0, 3) == "pass"
        assert mod.check_ratio_band(0.0, 4) == "pass"
        assert mod.check_ratio_band(0.0, 5) == "implausible_zero"
        assert mod.check_ratio_band(0.0, 100) == "implausible_zero"


class TestRiskCriteria:
    """Each of the 8 documented criteria, when present in a parenthesized
    reason, should yield a valid high-risk task via parse_tasks."""

    @pytest.mark.parametrize(
        "criterion",
        [
            "security surface",
            "module boundary",
            "non-trivial error flow",
            "infra/persistence",
            "security middleware",
            "deserialization of external data",
            "subprocess construction",
            "regex on untrusted input",
        ],
    )
    def test_criterion_parses_as_high(self, criterion):
        mod = _reload_plan_lib()
        plan = f"""
### Task 1: foo
- riskLevel: high ({criterion})
"""
        tasks = mod.parse_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].risk_level == "high"
        assert tasks[0].reason == criterion
