"""Tests for hooks/plan_lib.py — build_goal_text (#156 Task 1).

Covers:
- wf2/wf3 template assembly + escape disjunct (AC2)
- AC numbering/bullet stripping when compressing ac_lines
- 4000-char cap on the final assembled string, with list-replacement fallback
- headless param is currently a no-op on wording (documented, not yet divergent)
- fail-closed ValueError on unknown variant
"""
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import plan_lib  # noqa: E402

ESCAPE_DISJUNCT = " — or a blocker is posted to the issue via the ERROR protocol"


def _wf2_len(issue_number: int, compressed: str) -> int:
    """Length of the wf2 template assembled with the given compressed AC text.

    Used to compute cap-boundary test inputs without hardcoding the
    implementation's fixed overhead.
    """
    return len(
        f"Issue #{issue_number} done: ACs met ({compressed}), "
        f"PR open with green CI, run-record persisted{ESCAPE_DISJUNCT}"
    )


class TestWf2HappyPath:
    def test_three_acs(self):
        result = plan_lib.build_goal_text(156, ["Foo", "Bar", "Baz"])
        assert "Issue #156 done:" in result
        assert "Foo" in result
        assert "Bar" in result
        assert "Baz" in result
        assert "PR open with green CI" in result
        assert "run-record persisted" in result
        assert result.endswith(ESCAPE_DISJUNCT)

    def test_numbering_and_bullets_stripped(self):
        result = plan_lib.build_goal_text(156, ["1. Foo", "2) Bar", "- Baz"])
        assert "Foo; Bar; Baz" in result


class TestCap:
    def test_oversized_ac_lines_falls_back(self):
        ac_lines = ["x" * 100] * 100
        result = plan_lib.build_goal_text(156, ac_lines)
        assert len(result) <= 4000
        assert "all numbered acceptance criteria of issue #156" in result

    def test_empty_ac_lines_uses_fallback(self):
        result = plan_lib.build_goal_text(156, [])
        assert "all numbered acceptance criteria of issue #156" in result

    def test_blank_ac_lines_use_fallback(self):
        result = plan_lib.build_goal_text(156, ["", "   "])
        assert "all numbered acceptance criteria of issue #156" in result

    def test_just_under_cap_keeps_full_list(self):
        overhead = _wf2_len(156, "")
        compressed_len = 4000 - overhead
        ac_lines = ["x" * compressed_len]
        result = plan_lib.build_goal_text(156, ac_lines)
        assert len(result) == 4000
        assert "all numbered acceptance criteria" not in result

    def test_just_over_cap_falls_back(self):
        overhead = _wf2_len(156, "")
        compressed_len = 4000 - overhead + 1
        ac_lines = ["x" * compressed_len]
        result = plan_lib.build_goal_text(156, ac_lines)
        assert len(result) <= 4000
        assert "all numbered acceptance criteria of issue #156" in result


class TestWf3:
    def test_wf3_ignores_ac_lines(self):
        result = plan_lib.build_goal_text(
            12, ["should never appear"], variant="wf3"
        )
        assert "Bug #12 fixed:" in result
        assert "repro documented" in result
        assert "regression test red→green" in result
        assert "PR open with green CI" in result
        assert result.endswith(ESCAPE_DISJUNCT)
        assert "should never appear" not in result


class TestHeadless:
    def test_headless_matches_non_headless(self):
        wf2_a = plan_lib.build_goal_text(156, ["Foo"], headless=True)
        wf2_b = plan_lib.build_goal_text(156, ["Foo"], headless=False)
        assert wf2_a == wf2_b
        assert "merged" not in wf2_a

        wf3_a = plan_lib.build_goal_text(12, [], variant="wf3", headless=True)
        wf3_b = plan_lib.build_goal_text(12, [], variant="wf3", headless=False)
        assert wf3_a == wf3_b
        assert "merged" not in wf3_a


class TestEscapeDisjunctAlwaysPresent:
    def test_present_in_happy_path(self):
        assert plan_lib.build_goal_text(1, ["A"]).endswith(ESCAPE_DISJUNCT)

    def test_present_in_fallback(self):
        assert plan_lib.build_goal_text(1, []).endswith(ESCAPE_DISJUNCT)

    def test_present_in_wf3(self):
        assert plan_lib.build_goal_text(1, [], variant="wf3").endswith(
            ESCAPE_DISJUNCT
        )


class TestInvalidVariant:
    def test_unknown_variant_raises(self):
        with pytest.raises(ValueError):
            plan_lib.build_goal_text(1, ["A"], variant="wf9")


class TestCampaignVariant:
    """#192: the campaign variant sets ONE goal over an epic's ordered child
    issues, with a tolerant escape clause so real campaign outcomes clear it."""

    def test_enumerates_ordered_children(self):
        text = plan_lib.build_goal_text(
            188, [], variant="campaign", child_issues=[190, 191, 192])
        assert "Epic #188" in text
        assert "#190" in text and "#191" in text and "#192" in text
        # order preserved
        assert text.index("#190") < text.index("#191") < text.index("#192")

    def test_tolerant_escape_clause(self):
        text = plan_lib.build_goal_text(
            188, [], variant="campaign", child_issues=[190])
        # a data-gated not-planned close counts as satisfied; owner may pause
        assert "not-planned" in text.lower()
        assert "pause" in text.lower()

    def test_empty_children_falls_back(self):
        text = plan_lib.build_goal_text(188, [], variant="campaign", child_issues=[])
        assert "all ordered child issues of epic #188" in text
        assert "pause" in text.lower()  # escape clause still present

    def test_none_children_falls_back(self):
        text = plan_lib.build_goal_text(188, [], variant="campaign")
        assert "all ordered child issues of epic #188" in text

    def test_many_children_stays_under_cap_via_fallback(self):
        many = list(range(1000, 1000 + 900))  # would overflow 4000 chars
        text = plan_lib.build_goal_text(
            188, [], variant="campaign", child_issues=many)
        assert len(text) <= 4000
        assert "all ordered child issues of epic #188" in text

    def test_child_issues_ignored_for_wf2(self):
        # child_issues is campaign-only; wf2 behavior unchanged when it's passed
        a = plan_lib.build_goal_text(5, ["A"], variant="wf2")
        b = plan_lib.build_goal_text(5, ["A"], variant="wf2", child_issues=[9, 9])
        assert a == b
