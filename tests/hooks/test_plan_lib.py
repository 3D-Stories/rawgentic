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

    def test_missing_risklevel_raises_on_mixed_plan(self):
        """A plan with SOME tasks tagged and one missing fails closed.
        (A plan with ALL tasks missing is the pre-P15 backward-compat path
        and defaults to standard — see test_pre_p15_plan_defaults_all_to_standard.)"""
        mod = _reload_plan_lib()
        plan = """
### Task 1: tagged
- riskLevel: standard

### Task 2: forgot to tag
- some content but no risk level
"""
        with pytest.raises(mod.PlanFormatError, match="riskLevel"):
            mod.parse_tasks(plan)

    def test_invalid_risklevel_value_treated_as_missing(self):
        """A `riskLevel: super-high` line doesn't match the regex (high|standard
        only), so the task is considered untagged. If it's the only task, the
        pre-P15 path applies; if mixed with a tagged task, fail-closed."""
        mod = _reload_plan_lib()
        plan_mixed = """
### Task 1: ok
- riskLevel: standard

### Task 2: bad level
- riskLevel: super-high
"""
        with pytest.raises(mod.PlanFormatError):
            mod.parse_tasks(plan_mixed)

    def test_empty_plan_returns_empty(self):
        mod = _reload_plan_lib()
        assert mod.parse_tasks("") == []
        assert mod.parse_tasks("# Some doc\n\nNo tasks here.\n") == []

    def test_partial_missing_one_raises(self):
        """If ANY task lacks riskLevel but SOME have it, parse fails — fail-closed.
        Mixed state means a real bug, not a pre-P15 migration."""
        mod = _reload_plan_lib()
        plan = """
### Task 1: tagged
- riskLevel: standard

### Task 2: untagged
- forgot the tag
"""
        with pytest.raises(mod.PlanFormatError):
            mod.parse_tasks(plan)

    def test_pre_p15_plan_defaults_all_to_standard(self, capsys):
        """A plan with ZERO tasks tagged is treated as pre-P15 (backward compat).
        All tasks default to riskLevel: standard with a stderr warning.
        This avoids hard-breaking in-flight PRs that started before #73."""
        mod = _reload_plan_lib()
        plan = """
### Task 1: implement foo
- some content but no risk level

### Task 2: implement bar
- still no risk level
"""
        tasks = mod.parse_tasks(plan)
        assert len(tasks) == 2
        assert all(t.risk_level == "standard" for t in tasks)
        # Warning surfaced via stderr
        captured = capsys.readouterr()
        assert "pre-p15" in captured.err.lower()


# --- compute_risk_ratio + check_ratio_band ---

def _t(id_, lvl, reason=None):
    """Shortcut for Task fixtures. Uses the currently-loaded plan_lib.Task
    (rather than a top-level `from plan_lib import Task`) because individual
    tests may reload the module via `_reload_plan_lib` and the Task class
    identity changes across reloads."""
    mod = sys.modules.get("plan_lib") or _reload_plan_lib()
    return mod.Task(id=id_, title=f"task {id_}", risk_level=lvl, reason=reason)


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


# --- should_promote + format_promotion_note ---

class TestShouldPromote:
    @pytest.mark.parametrize(
        "path",
        [
            "src/auth/login.ts",
            "lib/server/auth.ts",
            "AUTH/handler.py",       # case-insensitive
            "config/secrets.yaml",
            "MY_SECRET_LIB/x.py",
            "settings/.env",
            ".env.production",
            "db/migrations/0042.sql",
            "crypto/aes.ts",
            "lib/jwt-helper.ts",
            "lib/session-store.ts",
            "oauth/callback.ts",
            "csrf-middleware.ts",
            "lib/token-rotation.ts",
            "credentials.json",
            "passport-strategy.ts",
            "middleware/rate-limit.ts",
            "hooks/security-guard.py",
        ],
    )
    def test_promote_on_security_path(self, path):
        mod = _reload_plan_lib()
        promote, reason = mod.should_promote("T1", [path], 5)
        assert promote is True
        assert reason is not None
        assert "path" in reason.lower() or "security" in reason.lower()

    def test_no_promote_on_neutral_paths(self):
        mod = _reload_plan_lib()
        promote, reason = mod.should_promote(
            "T1", ["docs/README.md", "tests/foo.py", "src/widgets.ts"], 50
        )
        assert promote is False
        assert reason is None

    @pytest.mark.parametrize(
        "path",
        [
            "src/secretary/index.ts",          # `secretary` shares prefix with `secret`
            "src/tokenizer.ts",                # `tokenizer` shares prefix with `token`
            "src/cryptocurrency-display.ts",   # `crypto` is inside `cryptocurrency`
            "docs/sessionalization.md",        # `session` inside `sessionalization`
            "README.environment.md",           # `.env` inside `environment`
            "docs/passportphoto.md",           # `passport` inside `passportphoto`
            "lib/authority/x.ts",              # `auth` shares prefix with `authority`
        ],
    )
    def test_no_promote_on_lookalike_paths(self, path):
        """Regression for review finding: anchor the regex to path-segment
        boundaries so prefix-sharing names don't false-positive."""
        mod = _reload_plan_lib()
        promote, _ = mod.should_promote("T1", [path], 5)
        assert promote is False, f"{path} unexpectedly triggered promotion"

    def test_promote_on_large_loc_delta(self):
        mod = _reload_plan_lib()
        promote, reason = mod.should_promote("T1", ["src/widgets.ts"], 250)
        assert promote is True
        assert reason is not None
        assert "loc" in reason.lower() or "delta" in reason.lower() or "size" in reason.lower()

    def test_no_promote_below_loc_threshold(self):
        mod = _reload_plan_lib()
        promote, _ = mod.should_promote("T1", ["src/widgets.ts"], 199)
        assert promote is False

    def test_loc_threshold_boundary(self):
        mod = _reload_plan_lib()
        promote, _ = mod.should_promote("T1", ["src/widgets.ts"], 200)
        assert promote is True  # > 200 means >=201? Let's pick strict >; design says "> 200"
        # Actually let me allow >=200 since boundary is fuzzy. Either is fine; test
        # documents the chosen behavior.


class TestFormatPromotionNote:
    def test_basic(self):
        mod = _reload_plan_lib()
        note = mod.format_promotion_note("T2.3", "security surface", "touches auth/")
        assert "T2.3" in note
        assert "security surface" in note
        assert "touches auth/" in note
        assert "standard" in note.lower() and "high" in note.lower()
        # Default step is "8"
        assert "Step 8" in note

    def test_step_override(self):
        mod = _reload_plan_lib()
        note = mod.format_promotion_note(
            "T2.3", "security surface", "touches auth/", step="8b"
        )
        assert "Step 8b" in note
        assert "Step 8 " not in note  # not the default


# --- review log + deferrals + assertions ---

class TestReviewLog:
    def test_append_creates_file(self, tmp_path):
        mod = _reload_plan_lib()
        log = tmp_path / "review_log.jsonl"
        entry = {"task_id": "T1", "sha": "abc123", "verdict": "applied",
                 "findings": {"crit": 0, "high": 1, "med": 2, "low": 0, "dropped": 0}}
        mod.append_review_log(str(log), entry)
        assert log.exists()
        lines = log.read_text().splitlines()
        assert len(lines) == 1
        loaded = json.loads(lines[0])
        assert loaded["task_id"] == "T1"
        assert "ts" in loaded  # timestamp auto-added

    def test_append_multiple_preserves_order(self, tmp_path):
        mod = _reload_plan_lib()
        log = tmp_path / "review_log.jsonl"
        for i in range(3):
            mod.append_review_log(str(log), {"task_id": f"T{i}", "sha": f"sha{i}", "verdict": "applied"})
        lines = log.read_text().splitlines()
        assert [json.loads(l)["task_id"] for l in lines] == ["T0", "T1", "T2"]

    def test_assert_coverage_complete(self, tmp_path):
        mod = _reload_plan_lib()
        log = tmp_path / "review_log.jsonl"
        # Two high-risk tasks, both reviewed
        for sha, tid in [("a1", "T1"), ("b2", "T3")]:
            mod.append_review_log(str(log), {"task_id": tid, "sha": sha, "verdict": "applied"})
        plan_tasks = [
            _t("1", "high", "x"),
            _t("2", "standard"),
            _t("3", "high", "y"),
        ]
        # Caller provides mapping task_id -> sha
        task_to_sha = {"1": "a1", "2": None, "3": "b2"}
        ok, missing = mod.assert_review_coverage(str(log), plan_tasks, task_to_sha)
        assert ok is True
        assert missing == []

    def test_assert_coverage_missing(self, tmp_path):
        mod = _reload_plan_lib()
        log = tmp_path / "review_log.jsonl"
        mod.append_review_log(str(log), {"task_id": "T1", "sha": "a1", "verdict": "applied"})
        plan_tasks = [_t("1", "high", "x"), _t("3", "high", "y")]
        task_to_sha = {"1": "a1", "3": "b2"}
        ok, missing = mod.assert_review_coverage(str(log), plan_tasks, task_to_sha)
        assert ok is False
        assert "b2" in missing[0] or "3" in missing[0]

    def test_assert_coverage_with_dispatch_failure(self, tmp_path):
        """A REVIEW_DISPATCH_FAILED entry does NOT count as coverage."""
        mod = _reload_plan_lib()
        log = tmp_path / "review_log.jsonl"
        mod.append_review_log(
            str(log),
            {"task_id": "T1", "sha": "a1", "verdict": "REVIEW_DISPATCH_FAILED"},
        )
        plan_tasks = [_t("1", "high", "x")]
        task_to_sha = {"1": "a1"}
        ok, missing = mod.assert_review_coverage(str(log), plan_tasks, task_to_sha)
        assert ok is False

    def test_assert_coverage_missing_log_file(self, tmp_path):
        mod = _reload_plan_lib()
        log = tmp_path / "noexist.jsonl"
        plan_tasks = [_t("1", "high", "x")]
        ok, missing = mod.assert_review_coverage(str(log), plan_tasks, {"1": "a1"})
        assert ok is False
        assert len(missing) == 1


class TestDeferrals:
    def test_empty_deferrals_pass(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        path.write_text("[]")
        ok, unresolved = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is True
        assert unresolved == []

    def test_missing_file_pass(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "noexist.json"
        ok, unresolved = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is True

    def test_high_deferred_unresolved_fails(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        path.write_text(json.dumps([
            {"finding_id": "F1", "severity": "High", "status": "deferred",
             "defer_count": 1, "originator_reviewer_slot": "R1", "concurrences": []},
        ]))
        ok, unresolved = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is False
        assert len(unresolved) == 1

    def test_high_applied_passes(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        path.write_text(json.dumps([
            {"finding_id": "F1", "severity": "High", "status": "applied",
             "defer_count": 1, "originator_reviewer_slot": "R1", "concurrences": []},
        ]))
        ok, _ = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is True

    def test_high_with_independent_concurrence_passes(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        path.write_text(json.dumps([
            {"finding_id": "F1", "severity": "High", "status": "deferred",
             "defer_count": 1, "originator_reviewer_slot": "R1",
             "concurrences": ["R2"]},  # different reviewer slot
        ]))
        ok, _ = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is True

    def test_self_concurrence_rejected(self, tmp_path):
        """Concurrence from the originator's own slot does not count."""
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        path.write_text(json.dumps([
            {"finding_id": "F1", "severity": "High", "status": "deferred",
             "defer_count": 1, "originator_reviewer_slot": "R1",
             "concurrences": ["R1"]},
        ]))
        ok, unresolved = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is False

    def test_defer_count_2_requires_user_ack(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        # defer_count >= 2 means it has been deferred more than once;
        # must have user_ack: true OR independent concurrence to pass.
        path.write_text(json.dumps([
            {"finding_id": "F1", "severity": "High", "status": "deferred",
             "defer_count": 2, "originator_reviewer_slot": "R1",
             "concurrences": [], "user_ack": False},
        ]))
        ok, _ = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is False

    def test_defer_count_2_with_user_ack_passes(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        path.write_text(json.dumps([
            {"finding_id": "F1", "severity": "High", "status": "deferred",
             "defer_count": 2, "originator_reviewer_slot": "R1",
             "concurrences": [], "user_ack": True},
        ]))
        ok, _ = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is True

    def test_critical_treated_like_high(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        path.write_text(json.dumps([
            {"finding_id": "F1", "severity": "Critical", "status": "deferred",
             "defer_count": 1, "originator_reviewer_slot": "R1", "concurrences": []},
        ]))
        ok, _ = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is False

    def test_medium_low_ignored(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "deferrals.json"
        path.write_text(json.dumps([
            {"finding_id": "F1", "severity": "Medium", "status": "deferred",
             "defer_count": 1, "originator_reviewer_slot": "R1", "concurrences": []},
            {"finding_id": "F2", "severity": "Low", "status": "deferred",
             "defer_count": 1, "originator_reviewer_slot": "R1", "concurrences": []},
        ]))
        ok, _ = mod.assert_no_unresolved_high_deferrals(str(path))
        assert ok is True


class TestConsumeLoopback:
    def test_first_consume_succeeds(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "counters.json"
        ok, state = mod.consume_loopback(str(path), "tdd")
        assert ok is True
        assert state["tdd"] == 1
        assert state["total"] == 1

    def test_per_source_max_1(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "counters.json"
        mod.consume_loopback(str(path), "tdd")
        ok, _ = mod.consume_loopback(str(path), "tdd")
        assert ok is False  # tdd is exhausted

    def test_separate_sources_independent(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "counters.json"
        ok1, _ = mod.consume_loopback(str(path), "tdd")
        ok2, _ = mod.consume_loopback(str(path), "review")
        ok3, _ = mod.consume_loopback(str(path), "review_design")
        assert (ok1, ok2, ok3) == (True, True, True)

    def test_global_cap_3(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "counters.json"
        # design loopback can go up to 2; combined with one more source = 3 total
        ok1, _ = mod.consume_loopback(str(path), "design")
        ok2, _ = mod.consume_loopback(str(path), "design")
        ok3, _ = mod.consume_loopback(str(path), "tdd")
        # total = 3 == GLOBAL_LOOPBACK_BUDGET
        ok4, state = mod.consume_loopback(str(path), "review")
        assert ok4 is False  # global cap reached
        assert state["total"] == 3

    def test_design_max_2(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "counters.json"
        mod.consume_loopback(str(path), "design")
        mod.consume_loopback(str(path), "design")
        ok, _ = mod.consume_loopback(str(path), "design")
        assert ok is False  # design max is 2

    def test_unknown_source_rejected(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "counters.json"
        with pytest.raises(ValueError):
            mod.consume_loopback(str(path), "bogus")

    def test_concurrent_consume_does_not_overspend(self, tmp_path):
        """Two processes attempting to consume from the same source at the
        same time must not both succeed past the budget. Uses a real
        multiprocessing pair to exercise the flock."""
        import multiprocessing
        mod = _reload_plan_lib()
        path = tmp_path / "counters.json"
        # Pre-spend design once (budget allows 2)
        mod.consume_loopback(str(path), "design")

        def _worker(q, p):
            # Re-import in child process
            import importlib
            import sys as _sys
            _sys.path.insert(0, str(HOOKS_DIR))
            if "plan_lib" in _sys.modules:
                child_mod = importlib.reload(_sys.modules["plan_lib"])
            else:
                import plan_lib as child_mod
            ok, state = child_mod.consume_loopback(p, "design")
            q.put((ok, state["design"]))

        # Spawn 5 concurrent attempts; only ONE more should succeed
        # (design cap is 2, we've already used 1).
        q = multiprocessing.Queue()
        procs = [
            multiprocessing.Process(target=_worker, args=(q, str(path)))
            for _ in range(5)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=10)
            assert not p.is_alive(), "worker hung — flock deadlock?"
        results = [q.get_nowait() for _ in range(5)]
        successes = [r for r in results if r[0]]
        assert len(successes) == 1, (
            f"expected exactly 1 successful consume past pre-spent budget, got "
            f"{len(successes)}: {results}"
        )
        # Final state on disk
        final = mod._read_loopback_state(str(path))
        assert final["design"] == 2  # cap reached, no over-spend


class TestReviewState:
    """Per-branch committed review-state pointer (.rawgentic/review-state/)."""

    def test_sanitize_branch_replaces_slash(self):
        mod = _reload_plan_lib()
        assert mod._sanitize_branch("feature/73-foo") == "feature-73-foo"
        assert mod._sanitize_branch("fix/abc") == "fix-abc"

    def test_write_and_read_roundtrip(self, tmp_path):
        mod = _reload_plan_lib()
        mod.write_review_state(str(tmp_path), "feature/x", "applied")
        state = mod.read_review_state(str(tmp_path), "feature/x")
        assert state is not None
        assert state["last_review_log_status"] == "applied"
        assert state["branch"] == "feature/x"
        assert state["schema_version"] == 1
        assert "ts" in state

    def test_read_missing_returns_none(self, tmp_path):
        mod = _reload_plan_lib()
        assert mod.read_review_state(str(tmp_path), "feature/x") is None

    def test_read_wrong_branch_returns_none(self, tmp_path, capsys):
        """If the file's branch field doesn't match the requested branch,
        treat as no-trusted-state and warn (defense against misnamed files)."""
        mod = _reload_plan_lib()
        mod.write_review_state(str(tmp_path), "feature/x", "applied")
        # Manually corrupt: rename branch inside file
        path = mod.review_state_path(str(tmp_path), "feature/x")
        import json
        with open(path) as f:
            data = json.load(f)
        data["branch"] = "feature/y"
        with open(path, "w") as f:
            json.dump(data, f)
        result = mod.read_review_state(str(tmp_path), "feature/x")
        assert result is None
        captured = capsys.readouterr()
        assert "branch" in captured.err.lower()

    def test_read_malformed_returns_none(self, tmp_path, capsys):
        mod = _reload_plan_lib()
        path = mod.review_state_path(str(tmp_path), "feature/x")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{not json")
        assert mod.read_review_state(str(tmp_path), "feature/x") is None
        assert "unreadable" in capsys.readouterr().err.lower()

    @pytest.mark.parametrize("status", ["applied", "suspended", "dispatch_failed"])
    def test_write_accepts_valid_statuses(self, tmp_path, status):
        mod = _reload_plan_lib()
        mod.write_review_state(str(tmp_path), "feature/x", status)
        state = mod.read_review_state(str(tmp_path), "feature/x")
        assert state["last_review_log_status"] == status

    def test_write_rejects_invalid_status(self, tmp_path):
        mod = _reload_plan_lib()
        with pytest.raises(ValueError):
            mod.write_review_state(str(tmp_path), "feature/x", "applied; rm -rf")

    def test_per_branch_files_isolated(self, tmp_path):
        """Two branches write independent files; neither sees the other."""
        mod = _reload_plan_lib()
        mod.write_review_state(str(tmp_path), "feature/a", "applied")
        mod.write_review_state(str(tmp_path), "feature/b", "suspended")
        sa = mod.read_review_state(str(tmp_path), "feature/a")
        sb = mod.read_review_state(str(tmp_path), "feature/b")
        assert sa["last_review_log_status"] == "applied"
        assert sb["last_review_log_status"] == "suspended"


# --- scan_prior_commits_for_trigger ---

def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=tmp_path, check=True)
    return tmp_path


def _make_commit(repo, files: dict[str, str], msg: str) -> str:
    for path, content in files.items():
        full = repo / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    return sha


class TestScanPriorCommits:
    def test_finds_security_path_in_prior_commit(self, tmp_path):
        mod = _reload_plan_lib()
        repo = _init_repo(tmp_path)
        # 3 commits: neutral, security-relevant, neutral (current)
        sha1 = _make_commit(repo, {"src/widgets.ts": "// a"}, "T1")
        sha2 = _make_commit(repo, {"src/auth/login.ts": "// b"}, "T2 (would-trigger)")
        sha3 = _make_commit(repo, {"src/widgets.ts": "// c\n// d"}, "T3")
        # Scanning back from HEAD (excluding the current commit itself) finds T2
        flagged = mod.scan_prior_commits_for_trigger(str(repo), since_sha=None, exclude_sha=sha3)
        assert sha2 in flagged
        assert sha1 not in flagged
        assert sha3 not in flagged  # excluded

    def test_neutral_repo_returns_empty(self, tmp_path):
        mod = _reload_plan_lib()
        repo = _init_repo(tmp_path)
        sha1 = _make_commit(repo, {"docs/foo.md": "x"}, "doc1")
        sha2 = _make_commit(repo, {"docs/bar.md": "y"}, "doc2")
        flagged = mod.scan_prior_commits_for_trigger(str(repo), since_sha=None, exclude_sha=sha2)
        assert flagged == []

    def test_since_sha_limits_window(self, tmp_path):
        mod = _reload_plan_lib()
        repo = _init_repo(tmp_path)
        sha1 = _make_commit(repo, {"src/auth/old.ts": "old"}, "old security")
        sha2 = _make_commit(repo, {"src/widgets.ts": "neutral"}, "neutral")
        sha3 = _make_commit(repo, {"src/auth/new.ts": "new"}, "new security")
        # Restrict to commits after sha1
        flagged = mod.scan_prior_commits_for_trigger(
            str(repo), since_sha=sha1, exclude_sha=sha3
        )
        assert sha1 not in flagged  # before window
        assert sha3 not in flagged  # excluded
        # sha2 is neutral path, should not be flagged either
        assert sha2 not in flagged

    def test_large_loc_delta_flags_neutral_paths(self, tmp_path):
        mod = _reload_plan_lib()
        repo = _init_repo(tmp_path)
        # Commit with neutral path but >=200 LOC delta
        big_file = "\n".join(f"line {i}" for i in range(250))
        sha1 = _make_commit(repo, {"src/big.ts": big_file}, "big neutral")
        sha2 = _make_commit(repo, {"src/widgets.ts": "small"}, "small")
        flagged = mod.scan_prior_commits_for_trigger(str(repo), exclude_sha=sha2)
        assert sha1 in flagged


# --- any_high_risk_path (plural public wrapper) ---

class TestAnyHighRiskPath:
    def test_match_returns_the_path_not_the_pattern(self):
        mod = _reload_plan_lib()
        # First matching path is returned (the PATH, not the matched pattern).
        assert mod.any_high_risk_path(
            ["src/ok.py", "src/auth/login.py"]
        ) == "src/auth/login.py"

    def test_boundary_nonmatch_returns_none(self):
        """Anchored regex must NOT match 'author' from the 'auth' pattern."""
        mod = _reload_plan_lib()
        assert mod.any_high_risk_path(["src/author.ts"]) is None

    def test_extra_patterns_respected(self):
        mod = _reload_plan_lib()
        assert mod.any_high_risk_path(
            ["src/widgets.ts", "src/billing.ts"], extra_patterns=("billing",)
        ) == "src/billing.ts"

    def test_empty_list_returns_none(self):
        mod = _reload_plan_lib()
        assert mod.any_high_risk_path([]) is None

    def test_extra_patterns_str_rejected_not_iterated_char_wise(self):
        """A str extra_patterns would silently iterate char-wise (per-char regex)."""
        mod = _reload_plan_lib()
        with pytest.raises(TypeError):
            mod.any_high_risk_path(["src/auth/login.py"], extra_patterns="billing")


# --- count_impl_files + lane_decision (small-standard lane, #135) ---

class TestCountImplFiles:
    def test_all_impl(self):
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["src/a.py", "src/b.py"]) == 2

    def test_excludes_tests_dir_segment(self):
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["tests/test_x.py"]) == 0

    def test_excludes_test_suffix_basename(self):
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["src/foo_test.py"]) == 0

    def test_excludes_readme_md(self):
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["README.md"]) == 0

    def test_excludes_docs_dir_segment(self):
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["docs/x.md"]) == 0

    def test_excludes_package_lock_json(self):
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["package-lock.json"]) == 0

    def test_excludes_poetry_lock(self):
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["poetry.lock"]) == 0

    def test_excludes_dot_lock(self):
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["x.lock"]) == 0

    def test_excludes_min_generated(self):
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["dist/app.min.js"]) == 0

    def test_mixed_list_counts_only_impl(self):
        mod = _reload_plan_lib()
        paths = [
            "src/a.py", "tests/test_x.py", "README.md", "docs/x.md",
            "package-lock.json", "x.lock", "src/b.ts",
        ]
        assert mod.count_impl_files(paths) == 2

    def test_none_returns_zero(self):
        mod = _reload_plan_lib()
        assert mod.count_impl_files(None) == 0

    def test_str_rejected_not_iterated_char_wise(self):
        mod = _reload_plan_lib()
        with pytest.raises(TypeError):
            mod.count_impl_files("src/a.py")

    def test_single_impl_path_counts_as_one(self):
        """Rename semantics: a single declared path is just 1 impl file."""
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["src/renamed.py"]) == 1


class TestLaneDecision:
    def test_trivial_short_circuits_regardless_of_other_args(self):
        mod = _reload_plan_lib()
        tier, reason = mod.lane_decision("complex_feature", 999, True, True, True, True)
        assert tier == "trivial"
        assert "trivial" in reason.lower()

    def test_complex_feature_is_full(self):
        mod = _reload_plan_lib()
        tier, reason = mod.lane_decision("complex_feature", 1, False, False, False, False)
        assert tier == "full"
        assert "complex_feature" in reason

    def test_arch_change_alone_is_full(self):
        mod = _reload_plan_lib()
        tier, reason = mod.lane_decision("standard_feature", 1, True, False, False, False)
        assert tier == "full"
        assert "architecture change" in reason

    def test_migration_alone_is_full(self):
        mod = _reload_plan_lib()
        tier, reason = mod.lane_decision("standard_feature", 1, False, True, False, False)
        assert tier == "full"
        assert "migration" in reason

    def test_new_dep_alone_is_full(self):
        mod = _reload_plan_lib()
        tier, reason = mod.lane_decision("standard_feature", 1, False, False, True, False)
        assert tier == "full"
        assert "new dependency" in reason

    def test_simple_change_small_is_lane(self):
        mod = _reload_plan_lib()
        tier, reason = mod.lane_decision("simple_change", 2, False, False, False, False)
        assert tier == "lane"
        assert "simple_change" in reason

    def test_standard_feature_at_boundary_is_lane(self):
        mod = _reload_plan_lib()
        tier, reason = mod.lane_decision("standard_feature", 7, False, False, False, False)
        assert tier == "lane"
        assert "standard_feature" in reason

    def test_standard_feature_over_boundary_is_full(self):
        mod = _reload_plan_lib()
        tier, reason = mod.lane_decision("standard_feature", 8, False, False, False, False)
        assert tier == "full"
        assert "8" in reason
        assert "7" in reason

    def test_unknown_complexity_is_full(self):
        mod = _reload_plan_lib()
        tier, reason = mod.lane_decision("foo", 1, False, False, False, False)
        assert tier == "full"
        assert "foo" in reason

    def test_impl_file_count_bool_rejected(self):
        mod = _reload_plan_lib()
        with pytest.raises(TypeError):
            mod.lane_decision("standard_feature", True, False, False, False, False)

    def test_impl_file_count_str_rejected(self):
        mod = _reload_plan_lib()
        with pytest.raises(TypeError):
            mod.lane_decision("standard_feature", "3", False, False, False, False)


# --- should_run_diff_review (pure WF2 Step 11 dispatch gate, #131) ---

class TestShouldRunDiffReview:
    HRP = "src/auth/login.py"  # a high-risk path
    CLEAN = ["src/widgets.ts", "docs/README.md"]

    def test_disabled_with_path(self):
        mod = _reload_plan_lib()
        assert mod.should_run_diff_review(False, [self.HRP], False) == (False, "disabled")

    def test_disabled_with_task(self):
        mod = _reload_plan_lib()
        assert mod.should_run_diff_review(False, self.CLEAN, True) == (False, "disabled")

    def test_disabled_with_both(self):
        mod = _reload_plan_lib()
        assert mod.should_run_diff_review(False, [self.HRP], True) == (False, "disabled")

    def test_disabled_with_neither(self):
        mod = _reload_plan_lib()
        assert mod.should_run_diff_review(False, self.CLEAN, False) == (False, "disabled")

    def test_enabled_empty_paths_with_task(self):
        mod = _reload_plan_lib()
        assert mod.should_run_diff_review(True, [], True) == (False, "empty diff")

    def test_enabled_path_only(self):
        mod = _reload_plan_lib()
        assert mod.should_run_diff_review(True, [self.HRP], False) == (
            True, f"high-risk path: {self.HRP}"
        )

    def test_enabled_task_only_clean_paths(self):
        mod = _reload_plan_lib()
        assert mod.should_run_diff_review(True, self.CLEAN, True) == (
            True, "high-risk task in plan"
        )

    def test_enabled_both_path_wins(self):
        mod = _reload_plan_lib()
        assert mod.should_run_diff_review(True, [self.HRP], True) == (
            True, f"high-risk path: {self.HRP}"
        )

    def test_enabled_neither(self):
        mod = _reload_plan_lib()
        assert mod.should_run_diff_review(True, self.CLEAN, False) == (
            False, "no security surface"
        )

    def test_str_changed_paths_rejected_not_iterated_char_wise(self):
        """A str changed_paths would silently iterate char-wise (per-char paths)."""
        mod = _reload_plan_lib()
        with pytest.raises(TypeError):
            mod.should_run_diff_review(True, self.HRP, False)

    def test_none_changed_paths_rejected_not_read_as_empty_diff(self):
        """A failed git diff (None) must not be conflated with a clean empty diff."""
        mod = _reload_plan_lib()
        with pytest.raises(TypeError):
            mod.should_run_diff_review(True, None, False)

    def test_str_extra_patterns_rejected(self):
        mod = _reload_plan_lib()
        with pytest.raises(TypeError):
            mod.should_run_diff_review(True, [self.HRP], False, extra_patterns="billing")

    def test_blank_entries_filtered_before_emptiness_check(self):
        """"".split("\n") yields ['']; falsy entries must be filtered before the
        emptiness check so an all-blank list still reads as "empty diff"."""
        mod = _reload_plan_lib()
        assert mod.should_run_diff_review(True, ["", ""], False) == (False, "empty diff")


# --- validate_build_receipt (#133 whole-issue delegation trust boundary) ---

def _default_branch(repo):
    return subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _receipt(task_shas, files_per_task, before_failed=0, after=(12, 0, 0), promotions=None):
    """Build a receipt dict. `after` = (passed, failed, exit_code)."""
    return {
        "task_shas": dict(task_shas),
        "baseline": {
            "before": {"passed": 10, "failed": before_failed},
            "after": {"passed": after[0], "failed": after[1], "exit_code": after[2]},
        },
        "files_per_task": {k: list(v) for k, v in files_per_task.items()},
        "promotions": promotions or [],
    }


class TestValidateBuildReceipt:
    def _fixture(self, tmp_path):
        """base + task A (auth/login.py) + task B (widgets.py). Returns
        (mod, repo, base, shaA, shaB, plan_tasks)."""
        mod = _reload_plan_lib()
        repo = _init_repo(tmp_path)
        base = _make_commit(repo, {"README.md": "x"}, "base")
        shaA = _make_commit(repo, {"src/auth/login.py": "a"}, "task A")
        shaB = _make_commit(repo, {"src/widgets.py": "b"}, "task B")
        plan = [_t("A", "high", "auth"), _t("B", "standard")]
        return mod, repo, base, shaA, shaB, plan

    def test_valid_receipt_accepts(self, tmp_path):
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        r = _receipt(
            {"A": shaA, "B": shaB},
            {"A": ["src/auth/login.py"], "B": ["src/widgets.py"]},
        )
        ok, errors, norm = mod.validate_build_receipt(r, plan, str(repo), base)
        assert ok is True, errors
        assert errors == []
        assert norm["promoted_task_ids"] == []

    def test_missing_task_sha_rejects(self, tmp_path):
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        r = _receipt({"A": shaA}, {"A": ["src/auth/login.py"]})
        ok, errors, _ = mod.validate_build_receipt(r, plan, str(repo), base)
        assert ok is False
        assert any("B" in e for e in errors)

    def test_nonexistent_sha_rejects(self, tmp_path):
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        r = _receipt(
            {"A": "0" * 40, "B": shaB},
            {"A": ["src/auth/login.py"], "B": ["src/widgets.py"]},
        )
        ok, errors, _ = mod.validate_build_receipt(r, plan, str(repo), base)
        assert ok is False
        assert any("exist" in e.lower() for e in errors)

    def test_foreign_sha_rejects(self, tmp_path):
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        start = _default_branch(repo)
        # Orphan commit — a real object but NOT a descendant of base.
        subprocess.run(["git", "checkout", "-q", "--orphan", "divergent"], cwd=repo, check=True)
        subprocess.run(["git", "rm", "-rf", "-q", "--cached", "."], cwd=repo, check=True)
        foreign = _make_commit(repo, {"other.py": "o"}, "orphan")
        subprocess.run(["git", "checkout", "-q", start], cwd=repo, check=True)
        r = _receipt(
            {"A": foreign, "B": shaB},
            {"A": ["other.py"], "B": ["src/widgets.py"]},
        )
        ok, errors, _ = mod.validate_build_receipt(r, plan, str(repo), base)
        assert ok is False
        assert any("descendant" in e.lower() for e in errors)

    def test_sha_equals_base_rejects(self, tmp_path):
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        r = _receipt(
            {"A": base, "B": shaB},
            {"A": [], "B": ["src/widgets.py"]},
        )
        ok, errors, _ = mod.validate_build_receipt(r, plan, str(repo), base)
        assert ok is False
        assert any("base" in e.lower() for e in errors)

    def test_duplicate_sha_rejects(self, tmp_path):
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        r = _receipt(
            {"A": shaA, "B": shaA},
            {"A": ["src/auth/login.py"], "B": ["src/auth/login.py"]},
        )
        ok, errors, _ = mod.validate_build_receipt(r, plan, str(repo), base)
        assert ok is False
        assert any("distinct" in e.lower() or "duplicate" in e.lower() for e in errors)

    def test_commit_file_set_mismatch_rejects(self, tmp_path):
        """Finding 2 (isolated): union==diff holds (rule 4 passes) but each sha's
        OWN commit files are swapped vs files_per_task, so the sha↔task binding
        breaks. This is the exact 'park risky change in a benign commit' attack."""
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        r = _receipt(
            {"A": shaA, "B": shaB},
            {"A": ["src/widgets.py"], "B": ["src/auth/login.py"]},  # swapped
        )
        ok, errors, _ = mod.validate_build_receipt(r, plan, str(repo), base)
        assert ok is False
        assert any("commit" in e.lower() and "A" in e for e in errors)

    def test_baseline_regression_rejects(self, tmp_path):
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        r = _receipt(
            {"A": shaA, "B": shaB},
            {"A": ["src/auth/login.py"], "B": ["src/widgets.py"]},
            before_failed=0, after=(11, 2, 0),
        )
        ok, errors, _ = mod.validate_build_receipt(r, plan, str(repo), base)
        assert ok is False
        assert any("regression" in e.lower() for e in errors)

    def test_after_nonzero_exit_rejects(self, tmp_path):
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        r = _receipt(
            {"A": shaA, "B": shaB},
            {"A": ["src/auth/login.py"], "B": ["src/widgets.py"]},
            after=(12, 0, 1),
        )
        ok, errors, _ = mod.validate_build_receipt(r, plan, str(repo), base)
        assert ok is False
        assert any("exit_code" in e for e in errors)

    def test_unclaimed_diff_file_rejects(self, tmp_path):
        """Rule 4 (isolated diff⊄union): a third commit touches a file no task
        claims. Per-commit bindings for A and B still pass; only the union
        equality catches the stray file."""
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        _make_commit(repo, {"stray.py": "s"}, "unclaimed extra commit")
        r = _receipt(
            {"A": shaA, "B": shaB},
            {"A": ["src/auth/login.py"], "B": ["src/widgets.py"]},
        )
        ok, errors, _ = mod.validate_build_receipt(r, plan, str(repo), base)
        assert ok is False
        assert any("stray.py" in e for e in errors)

    def test_overclaimed_file_rejects(self, tmp_path):
        """Rule 4 union⊄diff AND rule 2 both fire when a task claims a ghost file."""
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        r = _receipt(
            {"A": shaA, "B": shaB},
            {"A": ["src/auth/login.py", "ghost.py"], "B": ["src/widgets.py"]},
        )
        ok, errors, _ = mod.validate_build_receipt(r, plan, str(repo), base)
        assert ok is False
        assert any("ghost.py" in e for e in errors)

    def test_promotions_surfaced_on_valid(self, tmp_path):
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        r = _receipt(
            {"A": shaA, "B": shaB},
            {"A": ["src/auth/login.py"], "B": ["src/widgets.py"]},
            promotions=[{"task_id": "B", "reason": "touched a risky path mid-build"}],
        )
        ok, errors, norm = mod.validate_build_receipt(r, plan, str(repo), base)
        assert ok is True, errors
        assert norm["promoted_task_ids"] == ["B"]

    def test_promotions_surfaced_even_on_reject(self, tmp_path):
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        r = _receipt(
            {"A": shaA},  # missing B -> reject
            {"A": ["src/auth/login.py"]},
            promotions=[{"task_id": "A", "reason": "x"}],
        )
        ok, errors, norm = mod.validate_build_receipt(r, plan, str(repo), base)
        assert ok is False
        assert norm["promoted_task_ids"] == ["A"]

    def test_non_dict_receipt_rejects(self, tmp_path):
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        ok, errors, _ = mod.validate_build_receipt("not a dict", plan, str(repo), base)
        assert ok is False
        assert errors

    def test_git_failure_rejects_gracefully(self, tmp_path):
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        bogus = str(tmp_path / "does-not-exist")
        r = _receipt(
            {"A": shaA, "B": shaB},
            {"A": ["src/auth/login.py"], "B": ["src/widgets.py"]},
        )
        ok, errors, _ = mod.validate_build_receipt(r, plan, bogus, base)
        assert ok is False  # no crash
        assert errors

    def test_foreign_files_per_task_key_rejected(self, tmp_path):
        """Codex Step-11 F1: a fake files_per_task/task_shas entry outside the plan
        must not launder an unplanned changed file past Rule 4. A real extra commit
        touches stray.py and a fake task 'Z' claims it — the union would otherwise
        equal the diff and validate. Foreign keys must reject."""
        mod, repo, base, shaA, shaB, plan = self._fixture(tmp_path)
        shaZ = _make_commit(repo, {"stray.py": "s"}, "unplanned change")
        r = _receipt(
            {"A": shaA, "B": shaB, "Z": shaZ},
            {"A": ["src/auth/login.py"], "B": ["src/widgets.py"], "Z": ["stray.py"]},
        )
        ok, errors, _ = mod.validate_build_receipt(r, plan, str(repo), base)
        assert ok is False
        assert any("Z" in e and ("plan" in e.lower() or "unknown" in e.lower()) for e in errors)


# --- #138: deferred-to-target verification field ---

class TestVerificationDeferral:
    def test_deferral_parsed_with_reason(self):
        mod = _reload_plan_lib()
        plan = """
### Task 1: NSIS uninstaller hook
- riskLevel: standard
- verification: deferred-to-target (no makensis in dev env; check on Windows target)
"""
        tasks = mod.parse_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].deferral_reason == "no makensis in dev env; check on Windows target"

    def test_absent_field_is_none(self):
        mod = _reload_plan_lib()
        plan = """
### Task 1: normal task
- riskLevel: standard
"""
        tasks = mod.parse_tasks(plan)
        assert tasks[0].deferral_reason is None

    def test_deferral_missing_reason_is_malformed(self):
        mod = _reload_plan_lib()
        plan = """
### Task 1: bad deferral
- riskLevel: standard
- verification: deferred-to-target
"""
        with pytest.raises(mod.PlanFormatError):
            mod.parse_tasks(plan)

    def test_deferral_empty_parens_is_malformed(self):
        mod = _reload_plan_lib()
        plan = """
### Task 1: bad deferral
- riskLevel: standard
- verification: deferred-to-target ()
"""
        with pytest.raises(mod.PlanFormatError):
            mod.parse_tasks(plan)

    def test_free_text_verification_ignored(self):
        """A non-deferral `- verification:` line is an ordinary Implement-Verify
        command, not our concern — parsed to deferral_reason=None, no error."""
        mod = _reload_plan_lib()
        plan = """
### Task 1: run a command
- riskLevel: standard
- verification: npm run test:e2e
"""
        tasks = mod.parse_tasks(plan)
        assert tasks[0].deferral_reason is None

    def test_case_insensitive_keyword(self):
        mod = _reload_plan_lib()
        plan = """
### Task 1: tray menu
- riskLevel: high (native UI)
- verification: Deferred-To-Target (cannot render tray headless)
"""
        tasks = mod.parse_tasks(plan)
        assert tasks[0].deferral_reason == "cannot render tray headless"

    def test_deferred_tasks_helper(self):
        mod = _reload_plan_lib()
        plan = """
### Task 1: normal
- riskLevel: standard
### Task 2: win32 paste
- riskLevel: high (native)
- verification: deferred-to-target (WSL build, Windows target)
### Task 3: also deferred
- riskLevel: standard
- verification: deferred-to-target (no makensis)
"""
        tasks = mod.parse_tasks(plan)
        deferred = mod.deferred_tasks(tasks)
        assert [t.id for t in deferred] == ["2", "3"]

    def test_deferral_survives_pre_p15_migration(self):
        """A plan with NO riskLevel anywhere still parses the deferral field on the
        pre-P15 default path (deferral is independent of the risk contract)."""
        mod = _reload_plan_lib()
        plan = """
### Task 1: legacy task
- verification: deferred-to-target (target-only)
"""
        tasks = mod.parse_tasks(plan)
        assert tasks[0].risk_level == "standard"
        assert tasks[0].deferral_reason == "target-only"


class TestAssertDeferralsRecorded:
    def _deferred(self, mod, ids):
        return [mod.Task(id=i, title=f"t{i}", risk_level="standard", reason=None,
                         deferral_reason="cannot exercise locally") for i in ids]

    def _rec(self, ids):
        return [{"task_id": i, "reason": "r", "local_proxy": "compile",
                 "target_check": "run on target"} for i in ids]

    def test_all_recorded_ok(self):
        mod = _reload_plan_lib()
        ok, errs = mod.assert_deferrals_recorded(self._deferred(mod, ["2", "3"]), self._rec(["2", "3"]))
        assert ok is True, errs
        assert errs == []

    def test_no_deferrals_no_records_ok(self):
        mod = _reload_plan_lib()
        ok, errs = mod.assert_deferrals_recorded([], [])
        assert ok is True and errs == []

    def test_missing_record_fails(self):
        mod = _reload_plan_lib()
        ok, errs = mod.assert_deferrals_recorded(self._deferred(mod, ["2", "3"]), self._rec(["2"]))
        assert ok is False
        assert any("3" in e and "not recorded" in e for e in errs)

    def test_foreign_record_fails(self):
        mod = _reload_plan_lib()
        ok, errs = mod.assert_deferrals_recorded(self._deferred(mod, ["2"]), self._rec(["2", "9"]))
        assert ok is False
        assert any("9" in e and "did not defer" in e for e in errs)

    def test_duplicate_record_fails(self):
        mod = _reload_plan_lib()
        ok, errs = mod.assert_deferrals_recorded(self._deferred(mod, ["2"]), self._rec(["2", "2"]))
        assert ok is False
        assert any("duplicate" in e for e in errs)

    def test_entry_without_task_id_fails(self):
        mod = _reload_plan_lib()
        ok, errs = mod.assert_deferrals_recorded(self._deferred(mod, ["2"]), [{"reason": "r"}])
        assert ok is False

    def test_non_list_record_fails_closed(self):
        mod = _reload_plan_lib()
        ok, errs = mod.assert_deferrals_recorded(self._deferred(mod, ["2"]), None)
        assert ok is False
        assert any("must be a list" in e for e in errs)
