"""#700 AC2 — the pane-handoff skill must never hand-roll the delivery sequence.

The risk this guards is drift, not a present bug. `perform_handoff` already encodes the gated
send order and #700's recovery for a paste that arrives intact but unsubmitted; a later edit
"simplifying" the skill into direct terminal-primitive calls would silently reinstate #696's
defect, whose only symptom (a prompt that appears not to have arrived) argues for exactly the
wrong response — re-send it, or shorten it. Prose cannot prevent that. A test can.

Two-sided on purpose. Forbidding the primitives alone would pass on a skill with every command
deleted; requiring the sanctioned invocation alone would pass on a skill that calls it AND
hand-rolls a send beside it.

Anchored to the ONE skill file, never a corpus regex (repo CLAUDE.md mistake #6): a whole-corpus
scan for these strings would false-positive on the runbook, which has to quote them to document
them, and on `hooks/launcher_lib.py`, which legitimately builds them.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / "skills" / "pane-handoff" / "SKILL.md"

# The primitives the skill must never issue. Deliberately forbidden ANYWHERE in the file, prose
# included, rather than only inside code fences: the first draft of this guard scanned code
# contexts only, and the #700 design review pointed out that an unbackticked prose instruction to
# hand-roll a send would sail through it. The skill points at the runbook for that discussion
# instead of restating it, which costs nothing and closes the hole.
# Matched on WORD BOUNDARIES, not as bare substrings. A plain `"pane run" in body` check fired on
# the ordinary English "leaving your pane running", which is repo CLAUDE.md mistake #6 in miniature:
# a substring pin false-positives on prose that merely contains the letters. The contract is the
# herdr SUBCOMMAND, so the boundary is part of the contract.
FORBIDDEN = (r"\bsend-text\b", r"\bsend-keys\b", r"\bpane run\b")


@pytest.fixture(scope="module")
def body() -> str:
    assert SKILL.is_file(), f"the pane-handoff skill is missing: {SKILL}"
    return SKILL.read_text(encoding="utf-8")


@pytest.mark.parametrize("primitive", FORBIDDEN)
def test_the_skill_never_names_a_raw_terminal_primitive(primitive, body) -> None:
    assert not re.search(primitive, body), (
        f"skills/pane-handoff/SKILL.md matches {primitive!r}. The delivery sequence has exactly "
        "one tested implementation and the skill must reach it through "
        "`launcher_lib.py ad-hoc-handoff` only (#700 AC2). If you are documenting the primitive "
        "rather than invoking it, that belongs in docs/runbooks/herdr.md §7.1.2.")


def test_the_skill_calls_the_sanctioned_subcommand(body) -> None:
    """The other half: a skill with no commands at all satisfies the check above."""
    assert "launcher_lib.py" in body
    assert "ad-hoc-handoff" in body


def test_the_subcommand_is_reached_by_a_path_that_works_from_any_project(body) -> None:
    """A bare `python3 hooks/launcher_lib.py` only resolves when the session happens to be bound to
    THIS repo, and the whole point of the skill is to be callable wherever the user is working.
    The installed plugin ships its own hooks/, so the plugin root is the portable path."""
    assert "CLAUDE_PLUGIN_ROOT" in body


def test_the_skill_requires_a_unique_prompt_marker(body) -> None:
    """`prompt_landed` is a plain substring scan, so a common word would match unrelated transcript
    content and pass the gate before the prompt ever submitted (#700 review, High 1)."""
    assert "--prompt-marker" in body
    assert "unique" in body.lower()


def test_retiring_the_callers_pane_is_documented_as_the_default(body) -> None:
    """Owner decision 2026-07-29, REVERSING #700 AC4 — and the reversal is why this test changed
    rather than the skill: AC4's reasoning ("an ad-hoc handoff hands off work, not the caller's own
    session") was sound in the abstract and refuted in practice on the first real handoff, where the
    OFF default left a live pane re-prompting itself from an armed goal.

    What must stay documented is the escape hatch and its cost, because that is the part a user
    cannot infer: `--no-teardown` exists, and it leaves the guard armed.
    """
    assert "--no-teardown" in body
    assert "DEFAULT" in body, "the skill must say retirement is the default, not bury it"
    assert "/goal clear" in body, "the additive path's cost must be named"
