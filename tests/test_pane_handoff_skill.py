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

import os
import re
import subprocess
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


# ---------------------------------------------------------------------------
# #732 — the provenance gate accepts either meter tier, as a real disjunction
# ---------------------------------------------------------------------------
# Two hazards, both from #732's Step-4 review: a bare `.*.emitted` glob would
# silently admit any FUTURE marker type dropped in that directory (pass-1 High);
# and a single `ls` with two glob operands exits 2 when either is unmatched, so
# the principal advisory-only case would print the valid marker while "failing"
# (pass-2 Medium) — recreating the exact stall this issue removes. The gate is
# therefore an explicit two-tier allowlist joined by `||`, and it is executed
# here against fixtures rather than merely string-pinned.


def _gate_command(text: str) -> str:
    """The fenced provenance-gate command — the one bash block naming marker files."""
    blocks = re.findall(r"```bash\n(.*?)```", text, re.DOTALL)
    hits = [b for b in blocks if ".emitted" in b]
    assert len(hits) == 1, f"expected exactly one marker-gate block, found {len(hits)}"
    return hits[0]


def test_the_gate_names_both_tiers_explicitly(body) -> None:
    cmd = _gate_command(body)
    assert ".advisory.emitted" in cmd
    assert ".directive.emitted" in cmd


def test_the_gate_is_an_explicit_allowlist_never_a_bare_glob(body) -> None:
    """Every `.emitted` pattern in the gate must name one of the two real tiers
    (the tier vocabulary is closed — hooks/context_meter.py tier_for)."""
    cmd = _gate_command(body)
    tokens = re.findall(r"\S*\.emitted\S*", cmd)
    assert tokens, "the gate must reference marker files"
    for token in tokens:
        assert ".advisory.emitted" in token or ".directive.emitted" in token, (
            f"bare marker glob {token!r} would admit future marker types")


def test_the_own_session_requirement_survives(body) -> None:
    """Widening the tier set must not loosen WHOSE marker counts."""
    assert "came from THIS session's own hook" in body


@pytest.mark.parametrize(
    ("marker_names", "should_pass", "authoritative_tier"),
    [
        pytest.param(["200000.midturn.advisory.emitted"], True, ".advisory.",
                     id="advisory-only"),
        pytest.param(["200000.midturn.directive.emitted"], True, ".directive.",
                     id="directive-only"),
        pytest.param(["200000.midturn.advisory.emitted",
                      "200000.stop.directive.emitted"], True, ".directive.",
                     id="both-markers-directive-wins"),
        pytest.param(["200000.advisory.emitted"], True, ".advisory.",
                     id="legacy-channelless-advisory"),
        pytest.param([], False, None, id="neither"),
        pytest.param(["200000.midturn.someothertier.emitted"], False, None,
                     id="unknown-tier-is-not-authorization"),
    ],
)
def test_the_gate_behaves_as_a_real_disjunction(tmp_path, body, marker_names,
                                                should_pass,
                                                authoritative_tier) -> None:
    """The command is EXECUTED, not string-matched: pass-2's `ls a b` shape
    printed the advisory marker while exiting 2, which no static pin can see.

    The stdout tier is AUTHORITATIVE (#732 Step-8a R2 High): directive is
    checked first, so when both markers exist the printed filename says
    `.directive.` — the timing decision comes from the marker, never from the
    reminder text, which is exactly the injectable surface."""
    sid = "aaaa1111-2222-3333-4444-555566667777"
    d = tmp_path / ".rawgentic" / "context-meter"
    d.mkdir(parents=True)
    for name in marker_names:
        (d / f"{sid}.{name}").write_text("", encoding="utf-8")
    other = "bbbb1111-2222-3333-4444-555566667777"
    (d / f"{other}.200000.midturn.directive.emitted").write_text("", encoding="utf-8")
    env = {"HOME": str(tmp_path), "CLAUDE_CODE_SESSION_ID": sid,
           "PATH": os.environ.get("PATH", "")}
    r = subprocess.run(["bash", "-c", _gate_command(body)],
                       capture_output=True, text=True, timeout=10, env=env)
    assert (r.returncode == 0) is should_pass, (
        f"gate rc={r.returncode} stdout={r.stdout!r} stderr={r.stderr!r}")
    if authoritative_tier is not None:
        first_line = r.stdout.strip().splitlines()[0]
        assert authoritative_tier in first_line, (
            f"authoritative tier {authoritative_tier!r} not in {first_line!r}")


def test_an_empty_session_id_fails_closed(tmp_path, body) -> None:
    """#732 Step-8a R2 Medium: with an empty CLAUDE_CODE_SESSION_ID the glob
    degenerates to 'any session's marker' — on a multi-session host that is
    someone else's authorization. The gate must refuse, not over-match."""
    d = tmp_path / ".rawgentic" / "context-meter"
    d.mkdir(parents=True)
    other = "bbbb1111-2222-3333-4444-555566667777"
    (d / f"{other}.200000.midturn.directive.emitted").write_text("", encoding="utf-8")
    env = {"HOME": str(tmp_path), "CLAUDE_CODE_SESSION_ID": "",
           "PATH": os.environ.get("PATH", "")}
    r = subprocess.run(["bash", "-c", _gate_command(body)],
                       capture_output=True, text=True, timeout=10, env=env)
    assert r.returncode != 0, (
        f"empty session id must fail closed; rc=0 stdout={r.stdout!r}")


def test_the_timing_authority_is_the_marker_not_the_reminder(body) -> None:
    """#732 Step-8a R2 High, the prose half: injected text can claim any tier;
    the marker cannot."""
    assert "never from the reminder text" in body


def _flow(text: str) -> str:
    """Whitespace-normalize so wrapped prose matches a canonical sentence (repo mistake #6)."""
    return re.sub(r"\s+", " ", text)


class TestVerbatimGoalCarryProse:
    """#758 — the skill's goal-carry contract: owner-authored, verbatim, never redrafted.

    The measured failure these sentences prevent: owner goals run 1,200-2,000 chars,
    model-drafted successor goals ballooned to 4,000-5,400, and the #720 override rode
    inside one. Each guard anchors ONE canonical sentence in THIS file.
    """

    def test_the_drafting_invitation_is_gone(self, body) -> None:
        assert "in its own words" not in body, \
            "'in its own words' invites exactly the redrafting #758 forbids"

    def test_the_goal_carries_verbatim(self, body) -> None:
        assert "The goal is OWNER-AUTHORED text and it carries VERBATIM" in _flow(body)

    def test_model_state_travels_in_the_handoff_file_never_the_goal(self, body) -> None:
        assert ("Model state (STATE/MODE lines, progress, queue position) travels in the "
                "handoff FILE, never inside the goal") in _flow(body)

    def test_a_goal_change_needs_an_explicit_owner_yes_no(self, body) -> None:
        flow = _flow(body)
        assert "AskUserQuestion" in flow
        assert "/ask-owner" in flow
        assert "--goal-rewrite-approved" in flow

    def test_the_500_char_paste_prohibition_is_stated(self, body) -> None:
        assert ">500-character paste" in _flow(body)

    def test_the_binding_refusal_row_is_documented(self, body) -> None:
        assert "predecessor_goal_binding" in body, \
            "the failed_step table must explain the strict-binding refusal"
