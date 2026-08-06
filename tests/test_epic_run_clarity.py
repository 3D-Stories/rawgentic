"""Drift guards for the epic-run skill's harness task list (#517).

Field evidence: on the epic #509 auto-run (2026-07-19) the operator had no
on-screen checklist of children and had to interrupt mid-run to put one up by
hand. The skill now creates a harness task list at setup end and keeps it
honest per child. Section-sliced, one canonical sentence per surface,
whitespace-normalized (repo drift-guard convention, mistake #6).
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / "skills" / "epic-run" / "SKILL.md"


def _section(text: str, header: str, next_header: str) -> str:
    start = text.index(header)
    end = text.index(next_header, start)
    return " ".join(text[start:end].split())


def _text() -> str:
    return SKILL.read_text(encoding="utf-8")


class TestEpicRunTaskList:
    def _step3b(self) -> str:
        return _section(_text(), "## Step 3b:", "## Step 4:")

    def test_step3b_creates_one_task_per_child(self):
        s = self._step3b()
        assert "one task per queued child" in s, (
            "Step 3b must create one harness task per queued child (#517)")
        assert "close-epic task" in s.lower()

    def test_step3b_dedups_on_resume(self):
        s = self._step3b()
        assert "Check `TaskList` first" in s, (
            "Step 3b must check TaskList before creating (#517)")
        assert "refresh it instead of creating a second list" in s, (
            "a resumed run must refresh the existing list, never duplicate")

    def test_step3b_fails_open_when_tools_unavailable(self):
        s = self._step3b()
        assert ("skip with the one-line session-note marker" in s
                and "never blocks the run" in s), (
            "Task tools unavailable must be a visible, non-blocking skip (#517)")

    def test_step4_flips_status_per_child(self):
        s = _section(_text(), "## Step 4:", "## Step 5:")
        assert "the active child `in_progress` (at most one)" in s, (
            "Step 4 must keep the task list honest as children progress (#517)")

    def test_step5_completes_close_epic_task(self):
        s = _section(_text(), "## Step 5:", "## Common mistakes")
        assert "complete the close-epic task" in s.lower(), (
            "Step 5 must complete the close-epic task at wrap-up (#517)")


class TestEpicRunOwnerNotification:
    """Drift guards for #526 (epic #529): notify-at-block + launcher-at-start.

    Field evidence: epic #509 lever 1 — one 56.3-min owner-away stall between a
    Step-11 review verdict landing and the owner's resume (18% of run wall)."""

    def test_step2_recommends_launcher_at_start(self):
        s = _section(_text(), "## Step 2:", "## Step 3:")
        assert "recommend arming the durable resume launcher" in s, (
            "Step 2 must recommend the durable resume launcher at run start (#526)")
        assert "RUN START" in s
        assert "epic #509 lever 1" in s, (
            "the launcher recommendation must cite its measured basis (#526)")

    def test_step4_notifies_owner_at_human_blocked_points(self):
        s = _section(_text(), "## Step 4:", "## Step 5:")
        assert "Notify the owner at every point the run blocks on human input" in s, (
            "Step 4 must direct owner notification at human-blocked points (#526)")
        assert "skipped (notify-owner unavailable)" in s, (
            "notify-at-block must fail open with a visible skip marker (#526)")
        assert "never blocks the run" in s


class TestFreshSessionPerChild:
    """Drift guards for the per-child process boundary.

    #569 built it as an OPT-IN mode chosen at setup. #927 inverted that: the transport is PROBED
    at campaign creation and `pane_chain` is the DEFAULT, so these guards moved with the contract
    rather than being deleted -- what they protect is that Step 2 never goes back to ASKING, and
    that the boundary still launches a genuinely fresh successor.
    """

    def test_step2_probes_the_transport_instead_of_asking(self):
        s = _section(_text(), "## Step 2:", "## Step 3:")
        assert "transport resolve-creation" in s, (
            "Step 2 must RECORD the transport by probing (#927 AC 1)")
        assert "asks exactly TWO questions" in s, (
            "the session-mode question is gone -- merge policy and the launcher are the two")
        assert "`pane_chain` is the DEFAULT" in s, "the default is inverted (#927 AC 1)"
        assert "Never assert the capability from `HERDR_ENV` or a flag" in s, (
            "a recorded/asserted capability goes stale while the real one moves")

    def test_step4_ends_session_on_any_terminal_outcome(self):
        s = _section(_text(), "## Step 4:", "## Step 5:")
        assert "The child boundary is the DEFAULT" in s
        assert "with NO `--resume`" in s, (
            "the fresh successor must launch without --resume (else AC1 fails, #569)")

    def test_step4_documents_the_one_successor_fence_rather_than_its_absence(self):
        """#845, closed inside #927. This prose used to state the OPPOSITE -- twice, in two
        adjacent paragraphs -- because the fence genuinely did not exist. Shipping it without
        rewriting them would have left the skill telling an operator to expect a double launch.
        """
        s = _section(_text(), "## Step 4:", "## Step 5:")
        assert "exactly-one-successor fence IS here now" in s
        assert "rc 7" in s, "the losing contender's distinct exit code must be documented"
        assert "no exactly-one-successor" not in s, "the old absence claim must be gone"

    def test_step4_resolves_the_terminal_backend_verdict_at_the_boundary(self):
        """#611 Step-11 pass-3 High 2: deciding the launch mode only inside the launcher is too
        late — by then the driver has already ended the session believing the boundary was
        available, and 'keep the current loop' is no longer possible."""
        s = _section(_text(), "## Step 4:", "## Step 5:")
        assert "select-mode" in s, "the boundary must resolve the terminal-backend verdict"
        assert "`launch_mode`" in s, (
            "the verdict must be passed to fresh_session_available as launch_mode")
        assert "fail-open" in s.lower() and "single-session fallback" in s, (
            "the boundary must fail-open to single-session (#569 AC6)")


def test_epic_run_points_decisions_at_the_durable_store():
    """#847: the epic decision log must name the append-only store, not the
    markdown notes file the trimmer used to destroy. Pinned because the old
    instruction is exactly the habit this change exists to break."""
    text = (Path(__file__).resolve().parents[1] / "skills" / "epic-run" / "SKILL.md").read_text()
    assert "hooks/decision_log.py append" in text
    assert "claude_docs/decisions/<project>.jsonl" in text
    assert "--overturnable" in text
    assert "Do NOT hand-append" in text


class TestBoundaryLearningsSweep:
    """#769 — the owner's D181 standing order, named in the skill that executes it.

    Before this, a grep of the skill and `docs/multi-issue-driver.md` for
    reassess / learnings sweep / boundary sweep returned ZERO: the sweep was done by hand at
    every boundary and named nowhere, so a fresh-session successor could not tell whether it
    had happened.
    """

    def _step4(self) -> str:
        return _section(_text(), "## Step 4:", "## Step 5:")

    def test_the_boundary_section_names_the_learnings_sweep(self):
        assert "learnings sweep" in self._step4().lower()

    def test_the_canonical_sentence_covers_every_condition_the_GATE_enforces(self):
        """A sentence narrower than the code sends an operator into an unexplained rc 8.

        The Step-4 design review caught exactly that: the draft said "after every merged child"
        while the gate also fires for deferred/abandoned children and for a head move with no
        completion.
        """
        s = self._step4()
        assert "After every merged, deferred, or abandoned child" in s
        assert "without a completion" in s
        assert "before selecting or handing off the next child" in s

    def test_the_five_part_procedure_is_spelled_out(self):
        s = self._step4()
        for part in ("list", "sweep", "comment", "decision entry", "only then"):
            assert part in s.lower(), part

    def test_the_skill_states_what_the_validator_does_NOT_check(self):
        """Over-claiming is how `depth` became "an instruction to the auditor, not a property
        the validator checks" (#944). This one says so up front."""
        s = self._step4().lower()
        assert "coverage" in s
        assert "not" in s and "judgment" in s

    def test_the_field_precedent_is_the_D181_sweeps_not_the_retired_D6_pointer(self):
        s = self._step4()
        assert "D181" in s
        assert "epic-756-autorun-log" not in s, \
            "that log was trimmed 2026-08-02 and carries no D6; the issue's own correction " \
            "comment retires the citation"

    def test_the_skill_names_the_command_that_clears_the_gate(self):
        assert "sweep record" in self._step4()
