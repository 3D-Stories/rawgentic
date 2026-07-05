"""#193 drift guards — WF1 (create-issue) decomposes an over-large ask into an
epic + driver-consumable child issues, behind a hard approval gate.

Prose guards (the skill is LLM-executed): they pin the load-bearing contract so a
future edit can't silently drop the epic/depends_on shape, the approval gate, or
the ≥3 threshold. Anchored to the Step 2c section, not a whole-corpus regex.
"""
from pathlib import Path

from tests.corpus import skill_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent


def _step2c() -> str:
    text = skill_corpus("create-issue")
    assert "## Step 2c" in text, "create-issue must carry the Step 2c decompose section (#193)"
    start = text.index("## Step 2c")
    end = text.index("## Step 3", start)
    return text[start:end]


def test_step1_offers_decomposition_on_over_large():
    text = skill_corpus("create-issue")
    # Step 1 detects over-large and OFFERS to decompose (AC1)
    assert "Over-large check" in text or "offer to decompose" in text.lower()


def test_decomposition_is_driver_consumable():
    """AC2: epic anchor (epic: label + task-list) + depends_on edges children."""
    s = _step2c()
    assert "epic:" in s, "the epic must carry the epic: label"
    # pin the actual driver-parseable checkbox shape, not just the word "task-list"
    assert "- [ ]" in s, "epic task-list must use the driver-parseable `- [ ] #N` checkbox shape"
    assert "parse_depends_on" in s, "children must use the depends_on phrasing the driver reads"
    assert "Depends on #" in s


def test_partial_decomposition_is_resumable():
    """#193 Step-11 F1: a partial decomposition (some children filed, epic not) must
    not be mistaken for a completed single issue — the resumption contract records
    filed children and refuses to report complete until the epic is filed."""
    text = skill_corpus("create-issue")
    assert "COMPLETE" in text and "decompose" in text.lower()
    assert "never re-file a child" in text.lower() or "do not re-file" in text.lower() \
        or "never re-file" in text.lower()


def test_hard_approval_gate_files_nothing_first():
    """AC3: present the whole decomposition; file nothing until 'go'."""
    s = _step2c()
    assert "File NOTHING until" in s
    # the whole set (epic + every child + edges) is presented together
    assert "WHOLE decomposition" in s


def test_epic_threshold_three_children():
    """AC4: reserve an epic for >=3 children; 2 = plain cross-linked issues."""
    s = _step2c()
    assert "≥3 children" in s and "epic" in s
    assert "2 ⇒ no epic" in s or "no epic" in s.lower()


def test_lean_default_optin_wf5():
    """AC5: single-pass + quality-bar by default; opt-in WF5 for architectural asks."""
    s = _step2c()
    assert "quality-bar" in s.lower()
    assert "adversarial-review" in s and "WF5" in s
