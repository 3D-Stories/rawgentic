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
