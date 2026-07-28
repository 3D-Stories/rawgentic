"""Tests for the interactive mid-child session handoff (#665, epic #667).

What this file is defending, in the order the design's own failure list puts it:

1. **A guard that never armed.** The live hand-rolled handoff on 2026-07-27 handed an AUTO-MERGE
   run to a session with no completion guard. So every check here is artifact-derived and
   fail-closed, and an unreported check counts as FAILED.
2. **A predecessor that would not die**, and its mirror image — **a predecessor destroyed too
   early**. The second is worse: the predecessor holds the live working context. Hence
   `position_rebuilt` (live git state, in the recorded repository) and the pre-clear
   guard-still-in-force re-check.
3. **Shape assumptions that were never probed.** `prompt_landed` matches its marker as a plain
   SUBSTRING because a live probe (2026-07-28) found pasted prompt text persisted in
   `queue-operation` / `attachment` rows, NOT in a `type: "user"` row — the same class of defect
   as #611's invented `goal_status` shape, which passed its own tests by feeding them the
   invention back. The fixture here carries the REAL row shapes.
4. **A second handoff mechanism.** `test_no_parallel_handoff_path` (AC7) fails the suite if one
   appears, and its own negative cases prove it bites rather than merely passing today.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS = REPO_ROOT / "hooks"
CLI = HOOKS / "launcher_lib.py"

if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

import driver_lib as dl  # noqa: E402
import launcher_lib as ll  # noqa: E402


class FakeProc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


# --- real-shaped transcript rows, from the 2026-07-28 live probe --------------------------

def _queue_op_row(text: str) -> str:
    """A prompt pasted into a BUSY pane lands here — not in a `type: "user"` row."""
    return json.dumps({"type": "queue-operation", "operation": "enqueue", "content": text,
                       "sessionId": "succ-1", "timestamp": "2026-07-28T00:00:00.000Z"})


def _queued_command_row(text: str) -> str:
    return json.dumps({"type": "attachment", "cwd": "/x",
                       "attachment": {"type": "queued_command", "prompt": text,
                                      "commandMode": "prompt",
                                      "timestamp": "2026-07-28T00:00:01.000Z"}})


def _goal_row(condition: str, met: bool) -> str:
    return json.dumps({"type": "user", "sessionId": "succ-1",
                       "attachment": {"type": "goal_status", "met": met, "sentinel": True,
                                      "condition": condition}})


class TestMidChildLadder:
    def test_ladder_is_six_causal_steps_naming_on_disk_artifacts(self):
        steps = ll.mid_child_verification_steps()
        assert [s["step"] for s in steps] == [
            "spawned", "goal_armed", "prompt_landed", "project_switched",
            "position_rebuilt", "state_claimed"]
        for s in steps:
            assert s["artifact"].strip()
            assert "pane text" not in s["artifact"].lower()

    def test_launch_ladder_is_unchanged(self):
        """#611's three-step contract is pinned by its own test; adding a ladder must not
        mutate it, which is why the mid-child ladder is a separate tuple."""
        assert [s["step"] for s in ll.handoff_verification_steps()] == [
            "spawned", "goal_armed", "project_switched"]

    def test_evaluate_stops_at_the_first_missing_mid_child_step(self):
        results = {"spawned": True, "goal_armed": True, "prompt_landed": True,
                   "project_switched": True}          # position_rebuilt / state_claimed absent
        ok, failed, checked = ll.evaluate_verifications(
            results, steps=ll.mid_child_verification_steps())
        assert ok is False and failed == "position_rebuilt"
        assert checked[-1] == "position_rebuilt"

    def test_teardown_refused_until_all_six_pass(self):
        results = {"spawned": True, "goal_armed": True, "prompt_landed": True,
                   "project_switched": True, "position_rebuilt": False, "state_claimed": True}
        allowed, reason = ll.teardown_allowed(
            results, steps=ll.mid_child_verification_steps())
        assert allowed is False and "position_rebuilt" in reason

    def test_teardown_allowed_when_all_six_pass(self):
        results = {s["step"]: True for s in ll.mid_child_verification_steps()}
        allowed, _ = ll.teardown_allowed(results, steps=ll.mid_child_verification_steps())
        assert allowed is True


class TestPromptLanded:
    def test_marker_matches_in_a_queue_operation_row(self):
        marker = dl.mid_child_marker(665, 5)
        text = "\n".join([_queue_op_row(f"{marker} Mid-child resume for epic #667")])
        assert ll.transcript_has_marker(text, marker) is True

    def test_marker_matches_in_a_queued_command_attachment_row(self):
        marker = dl.mid_child_marker(665, 5)
        assert ll.transcript_has_marker(_queued_command_row(f"do the thing {marker}"),
                                        marker) is True

    def test_a_previous_generations_marker_does_not_match(self):
        """The marker is generation-bound precisely so a stale prompt cannot satisfy it."""
        text = _queue_op_row(f"{dl.mid_child_marker(665, 4)} older handoff")
        assert ll.transcript_has_marker(text, dl.mid_child_marker(665, 5)) is False

    def test_empty_marker_is_refused_rather_than_matching_everything(self):
        with pytest.raises(ll.LauncherError):
            ll.transcript_has_marker("anything", "")


class TestClearedGoalReader:
    def test_met_true_with_sentinel_is_a_cleared_guard(self):
        assert ll.transcript_has_cleared_goal(_goal_row("cond", met=True)) is True

    def test_an_unmet_row_is_not_a_cleared_guard(self):
        assert ll.transcript_has_cleared_goal(_goal_row("cond", met=False)) is False

    def test_latest_row_decides_whether_the_guard_is_still_in_force(self):
        """R4/pass-3 finding 4: historical presence of an unmet row does not prove the guard is
        live NOW. A later clear must flip the answer."""
        cond = "PR open with green CI"
        armed_then_cleared = "\n".join([_goal_row(cond, met=False), _goal_row(cond, met=True)])
        assert ll.goal_currently_unmet(armed_then_cleared, cond) is False
        cleared_then_rearmed = "\n".join([_goal_row(cond, met=True), _goal_row(cond, met=False)])
        assert ll.goal_currently_unmet(cleared_then_rearmed, cond) is True

    def test_a_different_conditions_rows_are_ignored(self):
        text = "\n".join([_goal_row("mine", met=False), _goal_row("someone else's", met=True)])
        assert ll.goal_currently_unmet(text, "mine") is True


class TestRegistryProjectBinding:
    def _line(self, session, project, path):
        return json.dumps({"session_id": session, "project": project, "project_path": path,
                           "started": "2026-07-28T00:00:00Z", "cwd": "/home/x/rawgentic"})

    def test_session_id_alone_is_not_enough(self):
        """pass-2 finding 2: a successor bound to the WRONG project would otherwise pass this
        check and then authorise teardown of a healthy predecessor."""
        text = self._line("succ-1", "thewanderinginn", "./projects/thewanderinginn")
        assert ll.registry_has_session(text, "succ-1") is True      # #611 behaviour, unchanged
        assert ll.registry_has_session(
            text, "succ-1", expected_project="rawgentic",
            expected_project_path="./projects/rawgentic") is False

    def test_all_three_must_be_on_the_same_line(self):
        text = "\n".join([
            self._line("other", "rawgentic", "./projects/rawgentic"),
            self._line("succ-1", "thewanderinginn", "./projects/thewanderinginn"),
        ])
        assert ll.registry_has_session(
            text, "succ-1", expected_project="rawgentic",
            expected_project_path="./projects/rawgentic") is False

    def test_matching_line_passes(self):
        text = self._line("succ-1", "rawgentic", "./projects/rawgentic")
        assert ll.registry_has_session(
            text, "succ-1", expected_project="rawgentic",
            expected_project_path="./projects/rawgentic") is True

    def test_path_mismatch_alone_is_refused(self):
        text = self._line("succ-1", "rawgentic", "./projects/rawgentic-next")
        assert ll.registry_has_session(
            text, "succ-1", expected_project="rawgentic",
            expected_project_path="./projects/rawgentic") is False


class TestCmdHandoffRefusesForeignKinds:
    """#611's launcher entry point reads the SAME state file. A mid-child record there means a
    resume is already in flight; building a fresh-child handoff from it would put two
    successors on one generation. The contract is an allowlist, not an equality test — a
    misspelled or non-string kind must not fall through to the legacy branch."""

    def _state(self, tmp_path, kind):
        pend = {"generation": 4, "next_issue": 665, "written_ts": 1}
        if kind is not _ABSENT:
            pend["kind"] = kind
        state = {"schema_version": 2, "campaign": "epic-667", "epic": 667,
                 "generation": 4, "session_mode": "fresh-session",
                 "issues": [{"number": 665, "status": "in_progress"}],
                 "handoff_pending": pend}
        p = tmp_path / "state.json"
        p.write_text(json.dumps(state), encoding="utf-8")
        return p

    @pytest.mark.parametrize("kind", ["mid_child", "MID_CHILD", "mid-child", "future_kind", 42])
    def test_present_kinds_are_refused(self, tmp_path, kind):
        state = self._state(tmp_path, kind)
        proc = subprocess.run(
            [sys.executable, str(CLI), "handoff", "--driver-state", str(state),
             "--anchor-pane", "w1:p1", "--name", "succ", "--project-root", str(tmp_path),
             "--cwd", str(tmp_path), "--registry", str(tmp_path / "reg.jsonl"),
             "--transcript-dir", str(tmp_path), "--goal-condition", "c",
             "--herdr-mode", "herdr"],
            capture_output=True, text=True, check=False)
        assert proc.returncode != 0
        assert "kind" in (proc.stdout + proc.stderr).lower()

    def test_cancelled_records_are_refused(self, tmp_path):
        state = json.loads(self._state(tmp_path, _ABSENT).read_text(encoding="utf-8"))
        state["handoff_pending"]["cancelled"] = True
        p = tmp_path / "state.json"
        p.write_text(json.dumps(state), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(CLI), "handoff", "--driver-state", str(p),
             "--anchor-pane", "w1:p1", "--name", "succ", "--project-root", str(tmp_path),
             "--cwd", str(tmp_path), "--registry", str(tmp_path / "reg.jsonl"),
             "--transcript-dir", str(tmp_path), "--goal-condition", "c",
             "--herdr-mode", "herdr"],
            capture_output=True, text=True, check=False)
        assert proc.returncode != 0
        assert "cancel" in (proc.stdout + proc.stderr).lower()


_ABSENT = object()
