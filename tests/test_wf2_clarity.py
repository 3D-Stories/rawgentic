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
REFERENCES = REPO_ROOT / "skills" / "implement-feature" / "references"


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


# --- Tier 1 (progressive disclosure): run-record schema extracted to references/ ---

class TestRunRecordReference:
    def test_run_record_reference_exists_with_schema(self):
        ref = REFERENCES / "run-record.md"
        assert ref.exists(), "the run-record schema should live in references/run-record.md"
        text = ref.read_text()
        for key in ("workflow_version", '"gates"', "security_scan", "loop_backs", "follow_ups"):
            assert key in text, f"run-record reference is missing the {key} field"

    def test_step16_points_to_reference_and_keeps_invocation(self):
        text = _text()
        step16 = text[text.index("## Step 16:"):]
        assert "references/run-record.md" in step16, "Step 16 must point at the extracted schema reference"
        # The load-bearing CLI invocation + rc handling stay in the base (test_work_summary pins it too).
        assert "work_summary.py summarize" in step16

    def test_full_schema_not_reinlined_in_base(self):
        text = _text()
        step16 = text[text.index("## Step 16:"):text.index("<completion-gate>")]
        assert step16.count('"workflow_version"') == 0, (
            "the full run-record JSON schema should be in references/run-record.md, "
            "not re-inlined in the base Step 16"
        )


# --- Task 5 (#131): WF2 Step 11 opt-in adversarial diff-review sub-step ---

GITIGNORE = REPO_ROOT / ".gitignore"


def _step11() -> str:
    """Text of Step 11 only (up to Step 11.5), where the sub-step lives."""
    text = _text()
    start = text.index("## Step 11: Pre-PR Code Review")
    end = text.index("## Step 11.5:")
    return text[start:end]


class TestStep11DiffReview:
    def test_marker_template_four_states(self):
        s11 = _step11()
        assert "### WF2 Step 11 — Adversarial Diff Review:" in s11
        for state in ("findings_present", "no_findings", "failed (", "skipped ("):
            assert state in s11, f"Step 11 diff-review marker missing state {state!r}"

    def test_gate_probe_is_enabled_for_this_skill(self):
        s11 = _step11()
        assert "is-enabled" in s11, "Step 11 must reuse the is-enabled enablement probe"
        assert "--skill implement-feature" in s11

    def test_should_run_diff_review_referenced(self):
        assert "plan_lib.should_run_diff_review" in _step11()

    def test_dispatch_command_flags(self):
        s11 = _step11()
        for flag in ("--type diff", "--findings-json", "--headless"):
            assert flag in s11, f"Step 11 dispatch command missing {flag!r}"

    def test_patch_construction_and_failure_strings(self):
        s11 = _step11()
        assert "high-risk-first" in s11, "patch must be built high-risk-first"
        assert "truncated" in s11 and "failed (truncated)" in s11
        assert "base ref unavailable" in s11

    def test_stale_sweep_and_confidence_mapping(self):
        s11 = _step11()
        assert ".rawgentic-diff-review-" in s11, "stale-temp sweep must name the patch glob prefix"
        assert re.search(r"stale|cleanup|leftover", s11, re.IGNORECASE), (
            "the sweep must carry cleanup/stale language (crash recovery)"
        )
        assert "ADV_CONFIDENCE_TO_FLOAT" in s11, "confidence enum must map via ADV_CONFIDENCE_TO_FLOAT"

    def test_secrets_surfacing_in_marker(self):
        assert "secrets detected" in _step11()

    def test_completion_gate_conditional_marker(self):
        gate = _block(_text(), "completion-gate")
        assert "Adversarial Diff Review" in gate, (
            "completion-gate must require the 4-state diff-review marker when opted in"
        )

    def test_gitignore_has_diff_review_globs(self):
        gi = GITIGNORE.read_text()
        assert ".rawgentic-diff-review-*.patch" in gi
        assert ".rawgentic-diff-findings-*.json" in gi
