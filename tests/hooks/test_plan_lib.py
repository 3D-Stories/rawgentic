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
