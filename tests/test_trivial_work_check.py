"""Drift guards for the trivial-work check added to WF2 (implement-feature) and
WF3 (fix-bug).

The check fires at Step 2: when a change is genuinely trivial (~1 file, ~<=10
lines, mechanical), the orchestrator surfaces a one-time SUGGESTION to do it
directly instead of running the full workflow — a human-in-the-loop recommendation
("Pause & recommend"), never automatic routing and never a hard gate. These assert
the block, its Step 2 trigger, the headless auto-continue behavior, the
mandatory-steps carve-out (WF2), and the consolidation D2 reconciliation all stay
present, so a later edit can't silently drop them.

Companion to tests/test_wf2_parallelism.py and
tests/hooks/test_adversarial_review_registration.py.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WF2 = REPO_ROOT / "skills" / "implement-feature" / "SKILL.md"
WF3 = REPO_ROOT / "skills" / "fix-bug" / "SKILL.md"
CONSOLIDATION = REPO_ROOT / "docs" / "consolidation.md"


def _section(text: str, header: str, next_header: str) -> str:
    start = text.index(header)
    end = text.index(next_header, start)
    return text[start:end]


@pytest.mark.parametrize("skill_path", [WF2, WF3], ids=["wf2", "wf3"])
def test_trivial_work_check_block_present(skill_path):
    text = skill_path.read_text(encoding="utf-8")
    assert "<trivial-work-check>" in text
    assert "</trivial-work-check>" in text
    block = _section(text, "<trivial-work-check>", "</trivial-work-check>")
    # It is a suggestion, never a hard gate, and must not bail autonomously.
    assert "suggestion, never a hard gate" in block
    assert "must NOT bail on its own" in block
    # It offers the two-option choice (do directly / continue) and recommends direct.
    assert "do it directly" in block.lower()
    assert "[recommended]" in block.lower()
    # Trivial threshold is spelled out, not vibes.
    assert "1 file" in block and "10 " in block


@pytest.mark.parametrize("skill_path", [WF2, WF3], ids=["wf2", "wf3"])
def test_step2_triggers_trivial_work_check(skill_path):
    text = skill_path.read_text(encoding="utf-8")
    step2 = _section(text, "## Step 2", "## Step 3")
    assert "trivial-work-check" in step2.lower()


@pytest.mark.parametrize("skill_path", [WF2, WF3], ids=["wf2", "wf3"])
def test_headless_auto_continues_trivial_suggestion(skill_path):
    # The AUTO-RESOLVE interaction list lives in the skill's references/headless.md.
    text = (skill_path.parent / "references" / "headless.md").read_text(encoding="utf-8")
    auto = _section(text, "AUTO-RESOLVE interactions", "QUESTION interactions")
    assert "trivial" in auto.lower()
    # Headless must CONTINUE the full workflow (no interactive user to hand off to),
    # not bail to a direct edit.
    assert "continue" in auto.lower()


def test_wf2_mandatory_steps_carveout_for_trivial_exit():
    """The trivial-work exit must be reconciled with <mandatory-steps>, so a future
    orchestrator doesn't read it as 'skipping a mandatory step'."""
    text = WF2.read_text(encoding="utf-8")
    mand = _section(text, "<mandatory-steps>", "</mandatory-steps>")
    assert "trivial-work-check" in mand
    # flatten whitespace so the match is robust to markdown line-wrapping
    mand_flat = " ".join(mand.split())
    assert "NOT skipping a mandatory step mid-run" in mand_flat


def test_consolidation_d2_reconciled():
    """docs/consolidation.md D2 previously claimed there was 'no penalty' for using a
    bigger workflow on a small task; that must be corrected to acknowledge the
    trivial-work suggestion (the old claim may survive only as an explicitly-corrected
    quote, not as standing rationale)."""
    text = CONSOLIDATION.read_text(encoding="utf-8")
    d2 = _section(text, "### D2:", "### D3:")
    assert "trivial-work-check" in d2
    assert "suggestion" in d2.lower()
    # the stale "no penalty / a few seconds" claim must now be framed as wrong, not
    # presented as the current rationale.
    assert "wrong for" in d2.lower()
