"""Drift guards for the WF2 (implement-feature) clarity additions from the
2026-06-16 assessment (Tier 3):

- a single ordered happy-path "spine" so an orchestrator under context pressure
  has one anchor for "what always runs, in order";
- Step 11.5 (security scan) and Step 16 (completion summary + run-record) added to
  <mandatory-steps> so the must-not-skip set matches the <completion-gate> set;
- the missing 4th loop-back source (review_design, consumed by Step 8a) listed in
  <loop-back-budget>;
- a single authoritative "Breaker decision" table in Step 4 so the run-exactly-once
  invariant can't drift across the disabled/enabled/non-success/loop-back branches.

These assert content that should EXIST in SKILL.md; they fail before the edits land.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / "skills" / "implement-feature" / "SKILL.md"


def _text() -> str:
    return SKILL.read_text()


def _block(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    assert m, f"<{tag}> block not found in SKILL.md"
    return m.group(1)


def test_happy_path_spine_present_and_ordered():
    text = _text()
    spine = _block(text, "happy-path")
    # The always-run security gate and completion step must be in the spine,
    # in order, around the PR step.
    assert "11 → 11.5 → 12" in spine, "spine must show 11 -> 11.5 -> 12 in order"
    assert "16" in spine and "(8a)" in spine


def test_mandatory_steps_includes_security_scan_and_completion():
    block = _block(_text(), "mandatory-steps")
    assert "| 11.5 |" in block, "Step 11.5 (security scan) must be in the mandatory table"
    assert "| 16 |" in block, "Step 16 (completion summary + run-record) must be in the mandatory table"


def test_loopback_budget_lists_review_design_source():
    block = _block(_text(), "loop-back-budget")
    assert "review_design" in block, "the 4th loop-back source (review_design) must be listed"
    assert "review_design_loopback_used" in block, "review_design needs an in-context mirror counter"


def test_step4_has_single_breaker_decision_table():
    text = _text()
    # Scope to Step 4.
    start = text.index("## Step 4: Quality Gate")
    end = text.index("## Step 5:")
    step4 = text[start:end]
    assert "Breaker decision" in step4, "Step 4 must carry a single authoritative breaker-decision table"
    assert "Breaker runs over" in step4, "the table must state which findings the one breaker runs over"
    # The volume-loopback row skips the breaker; the non-success row still runs it.
    assert "SKIP" in step4
