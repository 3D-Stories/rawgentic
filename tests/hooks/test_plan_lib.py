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

    def test_no_issue_kwarg_byte_identical_to_today(self):
        # Backward compat: captured from the pre-#341 output. Must not change
        # for existing callers that don't pass issue=.
        mod = _reload_plan_lib()
        note = mod.format_promotion_note("3", "security surface", "touches auth")
        assert note == (
            "### WF2 Step 8 — Promoted 3: standard -> high "
            "(criterion: security surface; rationale: touches auth)"
        )

    def test_issue_int_keys_the_detail(self):
        mod = _reload_plan_lib()
        note = mod.format_promotion_note(
            "3", "security surface", "touches auth", issue=341
        )
        # Detail follows "Promoted {task_id}: " — the key must lead it as "#341: ".
        assert "Promoted 3: #341: standard -> high" in note

    def test_issue_string_behaves_identically(self):
        mod = _reload_plan_lib()
        note_str = mod.format_promotion_note(
            "3", "security surface", "touches auth", issue="341"
        )
        note_int = mod.format_promotion_note(
            "3", "security surface", "touches auth", issue=341
        )
        assert note_str == note_int
        assert "Promoted 3: #341: standard -> high" in note_str


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


class TestClassifyLoopbackSource:
    """#223: fold per-finding Loopback-class tags into the loop-back source.

    Fail-closed: ONLY a non-empty all-spec-tightening list folds to the cheap
    spec_tighten source; any design-flaw, unknown, untagged, or empty input
    folds to the full design path.
    """

    def test_all_spec_tightening_folds_cheap(self):
        mod = _reload_plan_lib()
        assert mod.classify_loopback_source(
            ["spec-tightening", "spec-tightening"]) == "spec_tighten"

    def test_any_design_flaw_folds_full(self):
        mod = _reload_plan_lib()
        assert mod.classify_loopback_source(
            ["spec-tightening", "design-flaw"]) == "design"

    def test_empty_list_folds_full(self):
        mod = _reload_plan_lib()
        assert mod.classify_loopback_source([]) == "design"

    def test_untagged_among_spec_folds_full(self):
        # One-entry-per-finding contract: a finding without the field
        # contributes "untagged" — it must drag the fold to the full path.
        mod = _reload_plan_lib()
        assert mod.classify_loopback_source(
            ["spec-tightening", "untagged"]) == "design"

    def test_unknown_value_folds_full(self):
        mod = _reload_plan_lib()
        assert mod.classify_loopback_source(["speling-fix"]) == "design"
        assert mod.classify_loopback_source([""]) == "design"

    def test_case_and_whitespace_tolerant(self):
        mod = _reload_plan_lib()
        assert mod.classify_loopback_source(
            ["  Spec-Tightening ", "SPEC-TIGHTENING"]) == "spec_tighten"


class TestSpecTightenLoopbackSource:
    """#223: spec_tighten is a fifth budgeted loop-back source (cap 2)."""

    def test_consume_spec_tighten(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "counters.json"
        ok, state = mod.consume_loopback(str(path), "spec_tighten")
        assert ok is True
        assert state["spec_tighten"] == 1
        assert state["total"] == 1

    def test_spec_tighten_cap_2(self, tmp_path):
        mod = _reload_plan_lib()
        path = tmp_path / "counters.json"
        mod.consume_loopback(str(path), "spec_tighten")
        mod.consume_loopback(str(path), "spec_tighten")
        ok, _ = mod.consume_loopback(str(path), "spec_tighten")
        assert ok is False

    def test_spec_tighten_counts_toward_global_cap(self, tmp_path):
        # D1 (option A): spec passes share the global budget of 3. Two spec
        # passes + one design loop-back exhaust it; a further design consume
        # is refused by the GLOBAL cap even though design's own cap (2) has
        # room. Starvation is the accepted, pinned trade-off.
        mod = _reload_plan_lib()
        path = tmp_path / "counters.json"
        mod.consume_loopback(str(path), "spec_tighten")
        mod.consume_loopback(str(path), "spec_tighten")
        ok3, _ = mod.consume_loopback(str(path), "design")
        assert ok3 is True
        ok4, state = mod.consume_loopback(str(path), "design")
        assert ok4 is False
        assert state["total"] == 3

    def test_old_counters_file_backfills_spec_tighten(self, tmp_path):
        # Resume: a pre-#223 counters file has no spec_tighten key — it must
        # backfill to 0 and total must recompute over all five sources.
        mod = _reload_plan_lib()
        path = tmp_path / "counters.json"
        path.write_text(json.dumps(
            {"design": 1, "tdd": 0, "review": 0, "review_design": 0, "total": 1}))
        state = mod._read_loopback_state(str(path))
        assert state["spec_tighten"] == 0
        assert state["total"] == 1
        ok, state = mod.consume_loopback(str(path), "spec_tighten")
        assert ok is True
        assert state["total"] == 2


class TestEstimateAgents:
    """#224: pure path-cost estimator surfaced at Step 2."""

    def test_full_spine_baseline(self):
        # Step 4 (1) + 8a (2×0) + Step 11 full (3) = 4 agents.
        # Stages: 1 (Step 4) + 0 (no high-risk) + 1 (Step 11) = 2 → 10 min @5.
        mod = _reload_plan_lib()
        est = mod.estimate_agents(0, lane=False)
        assert est == {"agents": 4, "minutes": 10}

    def test_lane_baseline(self):
        # Step 4 (1) + 8a (0) + Step 11 lane (1) = 2 agents; stages 2 → 10 min.
        mod = _reload_plan_lib()
        est = mod.estimate_agents(0, lane=True)
        assert est == {"agents": 2, "minutes": 10}

    def test_step11_term_is_lane_keyed(self):
        mod = _reload_plan_lib()
        assert mod.STEP11_REVIEW_AGENT_COUNT_FULL == 3
        assert mod.STEP11_REVIEW_AGENT_COUNT_LANE == 1
        diff = (mod.estimate_agents(0, lane=False)["agents"]
                - mod.estimate_agents(0, lane=True)["agents"])
        assert diff == 2  # the 3-vs-1 Step-11 saving, nothing else

    def test_high_risk_tasks_multiply_by_two(self):
        # Each high-risk task adds PER_TASK_REVIEW_AGENT_COUNT (2) agents
        # and one stage: high=2 full → 1+4+3=8 agents, 4 stages → 20 min.
        mod = _reload_plan_lib()
        est = mod.estimate_agents(2, lane=False)
        assert est == {"agents": 8, "minutes": 20}

    def test_optins_add_agents_not_stages(self):
        # adversarial + peer_consult + diff_review are concurrent within
        # existing stages: +3 agents, minutes unchanged.
        mod = _reload_plan_lib()
        est = mod.estimate_agents(0, lane=False, adversarial=True,
                                  peer_consult=True, diff_review=True)
        assert est == {"agents": 7, "minutes": 10}

    def test_lane_forces_design_ceremony_off(self):
        # The lane drops adversarial-on-design + peer consult; diff_review
        # still counts on both paths.
        mod = _reload_plan_lib()
        est = mod.estimate_agents(0, lane=True, adversarial=True,
                                  peer_consult=True, diff_review=True)
        assert est == {"agents": 3, "minutes": 10}

    def test_negative_high_risk_raises(self):
        mod = _reload_plan_lib()
        with pytest.raises(ValueError):
            mod.estimate_agents(-1, lane=False)

    def test_minutes_env_override(self):
        mod = _reload_plan_lib({"WF2_EST_MINUTES_PER_AGENT": "10"})
        assert mod.WF2_EST_MINUTES_PER_AGENT == 10
        assert mod.estimate_agents(0, lane=False)["minutes"] == 20

    def test_minutes_env_malformed_falls_back(self):
        mod = _reload_plan_lib({"WF2_EST_MINUTES_PER_AGENT": "fast"})
        assert mod.WF2_EST_MINUTES_PER_AGENT == 5

    def test_minutes_env_clamped(self):
        mod = _reload_plan_lib({"WF2_EST_MINUTES_PER_AGENT": "500"})
        assert mod.WF2_EST_MINUTES_PER_AGENT == 60
        mod = _reload_plan_lib({"WF2_EST_MINUTES_PER_AGENT": "0"})
        assert mod.WF2_EST_MINUTES_PER_AGENT == 1


class TestReviewState:
    """Per-branch review-state pointer (.rawgentic/review-state/, local git-excluded)."""

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

    def test_ensure_rawgentic_git_excluded_adds_pattern(self, tmp_path):
        # #231 AC2: keep the review-state pointer out of an app PR by appending
        # .rawgentic/ to the repo's LOCAL .git/info/exclude (never committed).
        import subprocess as sp
        mod = _reload_plan_lib()
        sp.run(["git", "init", "-q", str(tmp_path)], check=True)
        assert mod._ensure_rawgentic_git_excluded(str(tmp_path)) is True
        excl = (tmp_path / ".git" / "info" / "exclude").read_text()
        assert ".rawgentic/" in excl
        # idempotent — a second call does not duplicate the entry
        assert mod._ensure_rawgentic_git_excluded(str(tmp_path)) is True
        excl2 = (tmp_path / ".git" / "info" / "exclude").read_text()
        assert excl2.count(".rawgentic/") == 1

    def test_ensure_rawgentic_git_excluded_noop_outside_repo(self, tmp_path):
        # No .git -> best-effort no-op (never raises), so write_review_state stays
        # usable in the non-git test/fixtures path.
        mod = _reload_plan_lib()
        assert mod._ensure_rawgentic_git_excluded(str(tmp_path)) is False

    def test_write_review_state_auto_excludes_rawgentic(self, tmp_path):
        # Writing the pointer inside a real repo auto-excludes .rawgentic/ so it
        # can never be accidentally staged into the feature PR.
        import subprocess as sp
        mod = _reload_plan_lib()
        sp.run(["git", "init", "-q", str(tmp_path)], check=True)
        mod.write_review_state(str(tmp_path), "feature/x", "applied")
        excl = (tmp_path / ".git" / "info" / "exclude").read_text()
        assert ".rawgentic/" in excl

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

    # --- #143: markdown-is-product opt-in (laneImplExtensions) ---

    def test_default_still_excludes_skill_markdown(self):
        # default (no opt-in) is byte-for-byte the old behavior
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["skills/foo/SKILL.md"]) == 0

    def test_opt_in_counts_skill_markdown(self):
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["skills/foo/SKILL.md"], impl_extensions=[".md"]) == 1

    def test_opt_in_counts_arbitrary_product_markdown(self):
        # a non-doc .md (not a docs/ dir, not a well-known doc basename) counts when opted in
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["skills/foo/reference.md"], impl_extensions=[".md"]) == 1

    def test_opt_in_still_excludes_well_known_root_docs(self):
        # #143 review F1: README/CHANGELOG are docs even in markdown-is-product mode
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["README.md"], impl_extensions=[".md"]) == 0
        assert mod.count_impl_files(["CHANGELOG.md"], impl_extensions=[".md"]) == 0

    def test_default_excludes_uppercase_md_doc_case_insensitive(self):
        # #143 review F4: matching is case-insensitive both with and without opt-in
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["README.MD"]) == 0
        assert mod.count_impl_files(["skills/x/GUIDE.MD"], impl_extensions=[".md"]) == 1

    def test_opt_in_bare_ext_without_dot_is_normalized(self):
        # #143 review F3: a direct caller passing "md" (no dot) still works
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["skills/x/SKILL.md"], impl_extensions=["md"]) == 1

    def test_opt_in_still_excludes_docs_dir_markdown(self):
        # a genuine docs/ dir stays docs even in markdown-is-product mode
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["docs/x.md"], impl_extensions=[".md"]) == 0

    def test_opt_in_still_excludes_test_markdown(self):
        mod = _reload_plan_lib()
        assert mod.count_impl_files(["tests/fixtures/test_x.md"], impl_extensions=[".md"]) == 0

    def test_opt_in_mixed_counts_product_md_plus_code(self):
        mod = _reload_plan_lib()
        paths = ["skills/a/SKILL.md", "hooks/b.py", "docs/c.md", "README.md", "tests/test_d.py"]
        # SKILL.md + b.py = 2; docs/c.md, README.md (well-known doc), and the test excluded
        assert mod.count_impl_files(paths, impl_extensions=[".md"]) == 2


class TestLaneImplExtensions:
    def test_default_empty(self):
        mod = _reload_plan_lib()
        assert mod.lane_impl_extensions({}) == ()
        assert mod.lane_impl_extensions({"laneImplExtensions": []}) == ()

    def test_normalizes_leading_dot_and_case(self):
        mod = _reload_plan_lib()
        assert mod.lane_impl_extensions({"laneImplExtensions": ["md", ".MD"]}) == (".md",)

    def test_preserves_multiple_distinct(self):
        mod = _reload_plan_lib()
        assert mod.lane_impl_extensions({"laneImplExtensions": [".md", "txt"]}) == (".md", ".txt")

    def test_malformed_config_fails_closed_to_empty(self):
        # a non-list value must not crash lane sizing — default to current behavior
        mod = _reload_plan_lib()
        assert mod.lane_impl_extensions({"laneImplExtensions": "md"}) == ()
        assert mod.lane_impl_extensions(None) == ()

    def test_wired_end_to_end(self):
        # the config helper feeds count_impl_files: a skill-md repo counts its product
        mod = _reload_plan_lib()
        exts = mod.lane_impl_extensions({"laneImplExtensions": ["md"]})
        assert mod.count_impl_files(["skills/x/SKILL.md"], impl_extensions=exts) == 1


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


class TestLaneSecondarySignal:
    """#225: bounded multi-defect secondary signal + operator override."""

    def _call(self, mod, count, **kw):
        return mod.lane_decision("standard_feature", count, False, False, False,
                                 False, **kw)

    # --- operator override ---

    def test_override_elects_lane_over_file_count(self):
        mod = _reload_plan_lib()
        tier, reason = self._call(mod, 15, operator_override=True)
        assert tier == "lane"
        assert "operator override" in reason

    def test_override_cannot_bypass_hard_guards(self):
        # AC2: arch / migration / new dep / complex_feature all force full
        # even with the override.
        mod = _reload_plan_lib()
        for args in (("standard_feature", 15, True, False, False, False),
                     ("standard_feature", 15, False, True, False, False),
                     ("standard_feature", 15, False, False, True, False),
                     ("complex_feature", 15, False, False, False, False)):
            tier, _ = mod.lane_decision(*args, operator_override=True)
            assert tier == "full", args

    # --- secondary signal ---

    def test_bounded_multi_defect_elects_lane(self):
        mod = _reload_plan_lib()
        tier, reason = self._call(mod, 15, defect_file_counts=[5, 5, 5])
        assert tier == "lane"
        assert "secondary signal" in reason
        assert "5+5+5" in reason  # per-defect counts enumerated verbatim

    def test_one_defect_over_per_defect_cap_is_full(self):
        mod = _reload_plan_lib()
        tier, _ = self._call(mod, 10, defect_file_counts=[8, 2])
        assert tier == "full"

    def test_single_defect_list_is_full(self):
        # One big defect over the total is just a big change, not multi-defect.
        mod = _reload_plan_lib()
        tier, _ = self._call(mod, 8, defect_file_counts=[7])
        assert tier == "full"

    def test_defect_count_cap(self):
        # More than MAX_LANE_DEFECTS defects → full, even if each is small.
        mod = _reload_plan_lib()
        assert mod.MAX_LANE_DEFECTS == 3
        tier, _ = self._call(mod, 12, defect_file_counts=[3, 3, 3, 3])
        assert tier == "full"

    def test_aggregate_ceiling(self):
        # [7,7,7,7,7] = 35 must NOT take the lane (F1); and even a valid
        # 3-defect list cannot sanction a total over 21.
        mod = _reload_plan_lib()
        tier, _ = self._call(mod, 35, defect_file_counts=[7, 7, 7, 7, 7])
        assert tier == "full"
        tier, _ = self._call(mod, 22, defect_file_counts=[7, 7, 7])
        assert tier == "full"

    def test_aggregate_boundary_exactly_21_is_lane(self):
        mod = _reload_plan_lib()
        tier, _ = self._call(mod, 21, defect_file_counts=[7, 7, 7])
        assert tier == "lane"

    def test_malformed_counts_fail_closed(self):
        mod = _reload_plan_lib()
        for bad in ([], None, [5, True], [5, "5"], [5, 0], [5, -1]):
            tier, _ = self._call(mod, 15, defect_file_counts=bad)
            assert tier == "full", bad

    def test_secondary_signal_cannot_bypass_hard_guards(self):
        mod = _reload_plan_lib()
        tier, _ = mod.lane_decision("standard_feature", 15, True, False, False,
                                    False, defect_file_counts=[5, 5, 5])
        assert tier == "full"

    def test_positional_calls_unchanged(self):
        # Backward compat: no kwargs → pre-#225 behavior byte-identical.
        mod = _reload_plan_lib()
        tier, reason = mod.lane_decision(
            "standard_feature", 15, False, False, False, False)
        assert tier == "full"
        assert "15 impl files > 7" in reason


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

    def test_deferral_reason_with_nested_parens(self):
        # #231 AC1 (confirmed bug): a reason containing inner parens must not
        # truncate at the first ) and raise "without a (reason)".
        mod = _reload_plan_lib()
        plan = """
### Task 1: cfg-gated build
- riskLevel: standard
- verification: deferred-to-target (needs #[cfg(target_os="windows")] build)
"""
        tasks = mod.parse_tasks(plan)
        assert tasks[0].deferral_reason == 'needs #[cfg(target_os="windows")] build'

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


class TestDeferralGateHardening:
    """Codex Step-11 findings folded (#138)."""

    def _deferred(self, mod, ids):
        return [mod.Task(id=i, title=f"t{i}", risk_level="standard", reason=None,
                         deferral_reason="cannot exercise locally") for i in ids]

    def test_recorded_entry_missing_evidence_fails(self):
        """F1 [High]: an entry with only task_id (no reason/local_proxy/target_check)
        must NOT satisfy the gate — that was a fail-open evidence bypass."""
        mod = _reload_plan_lib()
        ok, errs = mod.assert_deferrals_recorded(
            self._deferred(mod, ["2"]), [{"task_id": "2"}])
        assert ok is False
        assert any("reason" in e or "local_proxy" in e or "target_check" in e for e in errs)

    def test_recorded_entry_full_evidence_ok(self):
        mod = _reload_plan_lib()
        ok, errs = mod.assert_deferrals_recorded(
            self._deferred(mod, ["2"]),
            [{"task_id": "2", "reason": "r", "local_proxy": "compile", "target_check": "run"}])
        assert ok is True, errs

    def test_recorded_entry_empty_evidence_field_fails(self):
        mod = _reload_plan_lib()
        ok, errs = mod.assert_deferrals_recorded(
            self._deferred(mod, ["2"]),
            [{"task_id": "2", "reason": "r", "local_proxy": "", "target_check": "run"}])
        assert ok is False
        assert any("local_proxy" in e for e in errs)

    def test_pr_body_section_present_ok(self):
        mod = _reload_plan_lib()
        body = "## Summary\nx\n## Deferred verification\n- 2 (r): run on target\n"
        ok, errs = mod.assert_pr_body_has_deferred_section(body, self._deferred(mod, ["2"]))
        assert ok is True, errs

    def test_pr_body_section_missing_fails(self):
        """F2 [Medium]: deferrals exist but the PR body omits the canonical section."""
        mod = _reload_plan_lib()
        body = "## Summary\nx\n"
        ok, errs = mod.assert_pr_body_has_deferred_section(body, self._deferred(mod, ["2"]))
        assert ok is False
        assert any("Deferred verification" in e for e in errs)

    def test_pr_body_no_deferrals_no_section_ok(self):
        mod = _reload_plan_lib()
        ok, errs = mod.assert_pr_body_has_deferred_section("## Summary\n", [])
        assert ok is True and errs == []


# --- #139: branch-protection probe classification ---

class TestBranchProtection:
    def test_protected_with_required_checks(self):
        mod = _reload_plan_lib()
        body = {
            "required_status_checks": {"checks": [{"context": "test"}, {"context": "lint"}]},
            "required_pull_request_reviews": {"required_approving_review_count": 1},
        }
        state, details = mod.classify_branch_protection(200, body)
        assert state == "protected"
        assert set(details["required_checks"]) == {"test", "lint"}
        assert details["required_reviews"] is True

    def test_protected_contexts_legacy_shape(self):
        mod = _reload_plan_lib()
        body = {"required_status_checks": {"contexts": ["ci/build"]}}
        state, details = mod.classify_branch_protection(200, body)
        assert state == "protected"
        assert details["required_checks"] == ["ci/build"]

    def test_unprotected_on_404(self):
        mod = _reload_plan_lib()
        state, details = mod.classify_branch_protection(404, {"message": "Branch not protected"})
        assert state == "unprotected"
        assert details["required_checks"] == []

    def test_unknown_on_403(self):
        mod = _reload_plan_lib()
        state, _ = mod.classify_branch_protection(403, {"message": "Forbidden"})
        assert state == "unknown"

    def test_unknown_on_other_error(self):
        mod = _reload_plan_lib()
        state, _ = mod.classify_branch_protection(500, {})
        assert state == "unknown"

    def test_protection_line_unprotected(self):
        mod = _reload_plan_lib()
        line = mod.branch_protection_line("unprotected", {"required_checks": []})
        assert "none" in line.lower()
        assert "WF2 only" in line or "wf2" in line.lower()

    def test_protection_line_protected(self):
        mod = _reload_plan_lib()
        line = mod.branch_protection_line("protected", {"required_checks": ["test"], "required_reviews": True})
        assert "test" in line

    def test_contradiction_when_quarantined_and_required_checks(self):
        mod = _reload_plan_lib()
        msg = mod.quarantine_protection_contradiction(True, "protected", ["test"])
        assert msg is not None and "quarantin" in msg.lower()

    def test_no_contradiction_when_not_quarantined(self):
        mod = _reload_plan_lib()
        assert mod.quarantine_protection_contradiction(False, "protected", ["test"]) is None

    def test_no_contradiction_when_unprotected(self):
        mod = _reload_plan_lib()
        assert mod.quarantine_protection_contradiction(True, "unprotected", []) is None

    def test_no_contradiction_when_no_required_checks(self):
        mod = _reload_plan_lib()
        assert mod.quarantine_protection_contradiction(True, "protected", []) is None


class TestBranchProtectionHardening:
    """Codex Step-11 folds (#139)."""

    def test_404_only_unprotected_for_not_protected_message(self):
        mod = _reload_plan_lib()
        # genuine "branch not protected"
        st, _ = mod.classify_branch_protection(404, {"message": "Branch not protected"})
        assert st == "unprotected"

    def test_404_generic_not_found_is_unknown(self):
        """A 404 for a wrong repo/branch/inaccessible resource is NOT proof of
        an unprotected branch — must be unknown, not a false 'no protection'."""
        mod = _reload_plan_lib()
        st, _ = mod.classify_branch_protection(404, {"message": "Not Found"})
        assert st == "unknown"

    def test_404_non_dict_body_is_unknown(self):
        mod = _reload_plan_lib()
        st, _ = mod.classify_branch_protection(404, "Not Found")
        assert st == "unknown"

    def test_200_non_dict_body_is_unknown(self):
        mod = _reload_plan_lib()
        st, _ = mod.classify_branch_protection(200, "surprise")
        assert st == "unknown"

    def test_200_unrecognized_body_is_unknown(self):
        """A 200 whose body is not a protection object must not read as protected."""
        mod = _reload_plan_lib()
        st, _ = mod.classify_branch_protection(200, {"foo": "bar"})
        assert st == "unknown"

    def test_200_malformed_required_checks_is_unknown(self):
        mod = _reload_plan_lib()
        st, _ = mod.classify_branch_protection(200, {"required_status_checks": "nope"})
        assert st == "unknown"

    def test_200_enforce_admins_only_is_protected_no_checks(self):
        """A valid protection object with no required checks is still protected."""
        mod = _reload_plan_lib()
        st, details = mod.classify_branch_protection(200, {"enforce_admins": {"enabled": True}})
        assert st == "protected"
        assert details["required_checks"] == []
