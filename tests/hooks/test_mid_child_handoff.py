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

import ast
import json
import os
import re
import subprocess
import sys
import time
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
        """#694 reordered the four predecessor-owned rungs to match the send order: the bind is its
        own send, so its registry row comes first, and `/goal` goes last so `goal_armed` does too."""
        steps = ll.mid_child_verification_steps()
        assert [s["step"] for s in steps] == [
            "spawned", "project_switched", "prompt_landed", "goal_armed",
            "position_rebuilt", "state_claimed"]
        for s in steps:
            assert s["artifact"].strip()
            assert "pane text" not in s["artifact"].lower()

    def test_launch_ladder_is_still_three_steps(self):
        """#611's three-step contract is a SEPARATE tuple from the mid-child ladder, and adding a
        ladder must not change its length. #694 reordered both of them together — the launch ladder
        has no `prompt_landed` rung because `prompt_marker` is optional there, so gating on it would
        fail closed for every caller that supplies none."""
        assert [s["step"] for s in ll.handoff_verification_steps()] == [
            "spawned", "project_switched", "goal_armed"]

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
             "--anchor-pane", "w1:p1", "--name", "succ", "--project", "rawgentic", "--project-root", str(tmp_path),
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
             "--anchor-pane", "w1:p1", "--name", "succ", "--project", "rawgentic", "--project-root", str(tmp_path),
             "--cwd", str(tmp_path), "--registry", str(tmp_path / "reg.jsonl"),
             "--transcript-dir", str(tmp_path), "--goal-condition", "c",
             "--herdr-mode", "herdr"],
            capture_output=True, text=True, check=False)
        assert proc.returncode != 0
        assert "cancel" in (proc.stdout + proc.stderr).lower()


_ABSENT = object()


# --- Task 4: retire_predecessor -----------------------------------------------------------
#
# This is the only destructive path in the change: it clears a LIVE session's completion guard
# and closes its pane. Every test below exists because the design named a way for that to go
# wrong, so the assertions are mostly about what argv was NOT issued.

SUCC = "succ-sess-1"
PRED = "pred-sess-9"
ANCHOR = "w1:p1"
SUCC_PANE = "w1:p9"
GEN = 5
ISSUE = 665
COND = "drive epic #667 to completion"
BRANCH = "feat/665-mid-child-handoff"


class FakeWorld:
    """One injected runner for herdr AND git (design §8: the same runner throughout).

    `pane_close_rcs` is a list consumed per close attempt so a retry can be exercised, and a
    `/goal clear` send-text APPENDS the cleared row to the predecessor transcript — the way the
    real world confirms it — unless `confirm_clear` is False.
    """

    def __init__(self, tmp_path, *, pane_session=PRED, branch=BRANCH, toplevel=None,
                 clear_text_rc=0, clear_keys_rc=0, pane_close_rcs=(0,),
                 confirm_clear=True, confirm_rearm=True, pane_get_rc=0):
        self.tmp = tmp_path
        self.repo = str(tmp_path / "repo")
        self.pane_session = pane_session
        self.branch = branch
        self.toplevel = toplevel if toplevel is not None else self.repo
        self.clear_text_rc = clear_text_rc
        self.clear_keys_rc = clear_keys_rc
        self.pane_close_rcs = list(pane_close_rcs)
        self.confirm_clear = confirm_clear
        self.confirm_rearm = confirm_rearm
        self.pane_get_rc = pane_get_rc
        self.calls: list[list[str]] = []
        self._staged = None
        self.pred_transcript = tmp_path / "transcripts" / f"{PRED}.jsonl"

    def kinds(self) -> list[str]:
        """A compact signature of what actually ran, for the negative assertions."""
        out = []
        for argv in self.calls:
            if argv[:3] == ["herdr", "pane", "get"]:
                out.append("pane_get")
            elif argv[:3] == ["herdr", "pane", "close"]:
                out.append("pane_close")
            elif argv[:3] == ["herdr", "pane", "send-text"]:
                out.append("clear_text" if argv[4].startswith("/goal clear") else "rearm_text")
            elif argv[:3] == ["herdr", "pane", "send-keys"]:
                out.append("send_keys")
            elif argv[0] == "git":
                out.append("git")
        return out

    def __call__(self, argv, timeout=180):
        self.calls.append(list(argv))
        if argv[0] == "git":
            value = self.toplevel if argv[-1] == "--show-toplevel" else self.branch
            return FakeProc(0, value + "\n")
        if argv[:3] == ["herdr", "pane", "get"]:
            if self.pane_get_rc != 0:
                return FakeProc(self.pane_get_rc, "")
            body = {"result": {"pane_id": argv[3],
                               "agent_session": {"value": self.pane_session}}}
            return FakeProc(0, json.dumps(body))
        if argv[:3] == ["herdr", "pane", "send-text"]:
            text = argv[4]
            if text.startswith("/goal clear"):
                self._staged = "clear" if self.clear_text_rc == 0 else None
                return FakeProc(self.clear_text_rc, "")
            self._staged = "rearm"                       # the re-arm paste
            return FakeProc(0, "")
        if argv[:3] == ["herdr", "pane", "send-keys"]:
            # Step 11 pass-2 caught the old fake confirming the clear on send-TEXT, so its tests
            # could not tell whether the Enter mattered. A pasted command only takes effect when it
            # is SUBMITTED, so the row appears here or not at all.
            staged, self._staged = self._staged, None
            if self.clear_keys_rc == 0:
                if staged == "clear" and self.confirm_clear:
                    self._append_pred(_goal_row(COND, met=True))
                elif staged == "rearm" and self.confirm_rearm:
                    self._append_pred(_goal_row(COND, met=False))
            return FakeProc(self.clear_keys_rc, "")
        if argv[:3] == ["herdr", "pane", "close"]:
            rc = self.pane_close_rcs.pop(0) if self.pane_close_rcs else 0
            return FakeProc(rc, "")
        return FakeProc(0, "")

    def _append_pred(self, line: str) -> None:
        with open(self.pred_transcript, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _position(world, **over):
    pos = {"issue": ISSUE, "step": "8", "branch": BRANCH,
           "test_baseline": "5362 passed, 21 skipped, 0 failed, exit 0",
           "predecessor_pane": ANCHOR, "predecessor_session": PRED,
           "goal_condition": COND, "project": "rawgentic",
           "project_path": "./projects/rawgentic", "repo_root": world.repo}
    pos.update(over)
    return pos


def _world(tmp_path, *, marker_gen=GEN, succ_goal_rows=None, registry_project="rawgentic",
           registry_path_value="./projects/rawgentic", **kw):
    """Build the on-disk world a successor would really find, then the runner over it."""
    world = FakeWorld(tmp_path, **kw)
    (tmp_path / "transcripts").mkdir(exist_ok=True)
    os.makedirs(world.repo, exist_ok=True)
    rows = succ_goal_rows if succ_goal_rows is not None else [_goal_row(COND, met=False)]
    rows = list(rows) + [_queue_op_row(f"{dl.mid_child_marker(ISSUE, marker_gen)} resume")]
    (tmp_path / "transcripts" / f"{SUCC}.jsonl").write_text("\n".join(rows) + "\n",
                                                           encoding="utf-8")
    world.pred_transcript.write_text(_goal_row(COND, met=False) + "\n", encoding="utf-8")
    (tmp_path / "reg.jsonl").write_text(json.dumps(
        {"session_id": SUCC, "project": registry_project,
         "project_path": registry_path_value, "started": "2026-07-28T00:00:00Z",
         "cwd": "/home/x/rawgentic"}) + "\n", encoding="utf-8")
    return world


def _write_state(tmp_path, world, *, pend_over=None, position_over=None, claim=None):
    pend = {"generation": GEN, "next_issue": ISSUE, "written_ts": 1,
            "kind": dl.MID_CHILD_HANDOFF_KIND, "cancelled": False, "teardown_phase": None,
            "position": _position(world, **(position_over or {})),
            "successor": {"pane": SUCC_PANE, "session": SUCC}}
    pend.update(pend_over or {})
    state = {"schema_version": 2, "campaign": "epic-667", "epic": 667, "generation": GEN,
             "issues": [{"number": ISSUE, "status": "in_progress"}],
             "handoff_pending": pend}
    if claim is not None:
        state["handoff_claim"] = claim
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state), encoding="utf-8")
    return p


def _retire(state_path, world, tmp_path, **over):
    kw = {"driver_state_path": str(state_path), "session_id": SUCC, "anchor_pane": ANCHOR,
          "transcript_dir": str(tmp_path / "transcripts"),
          "registry_path": str(tmp_path / "reg.jsonl"),
          "runner": world, "sleeper": lambda _s: None, "now_ts": 1000}
    kw.update(over)
    return ll.retire_predecessor(**kw)


def _state_of(state_path):
    return json.loads(Path(state_path).read_text(encoding="utf-8"))


class TestRetireHappyPath:
    def test_all_six_pass_then_clear_then_close(self, tmp_path):
        world = _world(tmp_path)
        state = _write_state(tmp_path, world, position_over={"repo_root": world.repo})
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "retired", out
        assert out["ok"] is True
        assert all(out["results"][s["step"]] for s in ll.mid_child_verification_steps())
        # clear BEFORE close, and the close happened exactly once
        assert world.kinds().count("pane_close") == 1
        assert world.kinds().index("clear_text") < world.kinds().index("pane_close")

    def test_teardown_phase_is_persisted_before_the_clear_and_cleared_after(self, tmp_path):
        """R4: the clear-to-close window is the one place a crash leaves the predecessor
        unguarded, so `teardown_phase` must be on disk BEFORE the send — that is what makes the
        window discoverable rather than invisible."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        seen = {}

        original = world.__call__

        def spy(argv, timeout=180):
            if argv[:3] == ["herdr", "pane", "send-text"] and argv[4].startswith("/goal clear"):
                seen["phase_at_clear"] = _state_of(state)["handoff_pending"].get("teardown_phase")
            return original(argv, timeout)

        out = _retire(state, world, tmp_path, runner=spy)
        assert out["outcome"] == "retired"
        assert seen["phase_at_clear"] == "clearing"
        assert _state_of(state)["handoff_pending"].get("teardown_phase") is None

    def test_the_claim_is_acked_started(self, tmp_path):
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        _retire(state, world, tmp_path)
        claim = _state_of(state)["handoff_claim"]
        assert claim["generation"] == GEN and claim["claimant"] == SUCC
        assert claim["started"] is True

    def test_a_rebuild_receipt_is_written_under_the_lock(self, tmp_path):
        """`position_rebuilt` is an ATTESTATION, and its whole value is that it cannot be
        satisfied by inaction and cannot be satisfied by a stale or foreign receipt."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        _retire(state, world, tmp_path)
        receipt = _state_of(state)["handoff_pending"]["rebuild_receipt"]
        assert receipt["generation"] == GEN and receipt["claimant"] == SUCC
        assert receipt["branch_observed"] == BRANCH
        assert receipt["repo_root_observed"] == world.repo
        assert receipt["step"] == "8"


class TestRetireRefusesBeforeAnythingDestructive:
    """Every case here must reach NEITHER `send-text /goal clear` NOR `pane close`. The
    predecessor is left alive AND still guarded — the designed-safe failure (AC6)."""

    def _assert_nothing_destructive(self, world):
        kinds = world.kinds()
        assert "clear_text" not in kinds and "pane_close" not in kinds, kinds

    def test_a_foreign_kind_is_refused(self, tmp_path):
        world = _world(tmp_path)
        state = _write_state(tmp_path, world, pend_over={"kind": "something_else"})
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "refused" and "kind" in out["reason"]
        self._assert_nothing_destructive(world)

    def test_a_cancelled_record_is_refused(self, tmp_path):
        world = _world(tmp_path)
        state = _write_state(tmp_path, world, pend_over={"cancelled": True})
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "refused" and "cancel" in out["reason"].lower()
        self._assert_nothing_destructive(world)

    def test_a_session_is_never_its_own_predecessor(self, tmp_path):
        world = _world(tmp_path)
        state = _write_state(tmp_path, world, position_over={"predecessor_session": SUCC})
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "refused" and "own predecessor" in out["reason"]
        self._assert_nothing_destructive(world)

    def test_only_the_recorded_successor_session_may_retire(self, tmp_path):
        """R4's identity binding, and it is the strongest check here: a session that was never
        the intended successor cannot authorise a teardown at all."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path, session_id="some-other-session")
        assert out["outcome"] == "refused" and "successor" in out["reason"]
        self._assert_nothing_destructive(world)

    def test_the_anchor_argument_must_agree_with_durable_state(self, tmp_path):
        """Two independent sources must agree before anything destructive happens."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path, anchor_pane="w1:p77")
        assert out["outcome"] == "refused" and "anchor" in out["reason"]
        self._assert_nothing_destructive(world)

    def test_an_invalid_position_is_refused(self, tmp_path):
        world = _world(tmp_path)
        state = _write_state(tmp_path, world, position_over={"branch": ""})
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "refused" and "position" in out["reason"]
        self._assert_nothing_destructive(world)

    def test_a_foreign_claimant_holding_the_claim_is_refused(self, tmp_path):
        world = _world(tmp_path)
        state = _write_state(tmp_path, world,
                             claim={"generation": GEN, "claimant": "someone-else",
                                    "claimed_at": 999, "started": False})
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "claim_refused"
        self._assert_nothing_destructive(world)

    def test_our_own_started_claim_is_a_continuation_not_a_deadlock(self, tmp_path):
        """Probed live: `handoff_claim` returns False for a same-claimant re-claim inside the
        lease AND after `started`. Without this branch one failed teardown would block its own
        retry for the whole 1800 s lease."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world,
                             claim={"generation": GEN, "claimant": SUCC,
                                    "claimed_at": 999, "started": True})
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "retired", out

    def test_position_rebuilt_fails_when_live_head_is_not_the_recorded_branch(self, tmp_path):
        world = _world(tmp_path, branch="some-other-branch")
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "teardown_refused"
        assert out["failed_step"] == "position_rebuilt"
        self._assert_nothing_destructive(world)

    def test_position_rebuilt_fails_when_the_repository_is_not_the_recorded_one(self, tmp_path):
        """`--show-toplevel` is compared BEFORE the branch: a same-named branch in a DIFFERENT
        repository would otherwise satisfy the check and authorise teardown for the wrong tree."""
        world = _world(tmp_path, toplevel="/somewhere/else")
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "teardown_refused"
        assert out["failed_step"] == "position_rebuilt"
        self._assert_nothing_destructive(world)

    def test_a_wrong_project_binding_refuses(self, tmp_path):
        world = _world(tmp_path, registry_project="thewanderinginn")
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "teardown_refused"
        assert out["failed_step"] == "project_switched"
        self._assert_nothing_destructive(world)

    def test_a_stale_generation_marker_fails_prompt_landed(self, tmp_path):
        world = _world(tmp_path, marker_gen=GEN - 1)
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "teardown_refused"
        assert out["failed_step"] == "prompt_landed"
        self._assert_nothing_destructive(world)

    def test_the_pane_must_still_host_the_recorded_session(self, tmp_path):
        """A pane id is a reusable handle; syntax validation cannot detect a recycled one."""
        world = _world(tmp_path, pane_session="somebody-else-entirely")
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "refused" and "identity" in out["reason"]
        assert "clear_text" not in world.kinds() and "pane_close" not in world.kinds()

    def test_our_own_guard_must_still_be_in_force(self, tmp_path):
        """The original defect, reintroduced through the back door: retiring the predecessor
        while the CONTINUING session is already unguarded."""
        world = _world(tmp_path, succ_goal_rows=[_goal_row(COND, met=False),
                                                 _goal_row(COND, met=True)])
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "refused" and "guard" in out["reason"]
        self._assert_nothing_destructive(world)

    def test_a_replacement_guard_also_refuses(self, tmp_path):
        """Design §5: a session that re-armed with a DIFFERENT condition has retired the old
        guard, so the armed condition is stale and teardown must refuse."""
        world = _world(tmp_path, succ_goal_rows=[_goal_row(COND, met=False),
                                                 _goal_row("a different goal entirely",
                                                           met=False)])
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "refused" and "replace" in out["reason"].lower()
        self._assert_nothing_destructive(world)


class TestRetireClearAndCloseFailures:
    def test_a_failed_send_text_aborts_before_close(self, tmp_path):
        world = _world(tmp_path, clear_text_rc=1)
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "clear_failed"
        assert "pane_close" not in world.kinds()

    def test_a_failed_send_keys_aborts_before_close(self, tmp_path):
        """The one both return codes exist for: the text landed, the Enter did not, so the
        predecessor still holds an unsubmitted line and its guard is untouched."""
        world = _world(tmp_path, clear_keys_rc=1, confirm_clear=False)
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "clear_failed"
        assert "pane_close" not in world.kinds()

    def test_a_transported_but_unconfirmed_clear_leaves_the_pane_open(self, tmp_path):
        """rc 0 proves keystrokes were transported, NOT that the slash command was parsed."""
        world = _world(tmp_path, confirm_clear=False)
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "clear_unconfirmed"
        assert "pane_close" not in world.kinds()
        assert out["ok"] is False

    def test_a_close_failure_retries_then_re_arms(self, tmp_path):
        world = _world(tmp_path, pane_close_rcs=[1, 1, 1])
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "alive_and_re_armed", out
        assert world.kinds().count("pane_close") == 3      # first + two bounded retries
        assert "rearm_text" in world.kinds()
        assert out["ok"] is False

    def test_a_close_that_succeeds_on_retry_is_a_clean_retirement(self, tmp_path):
        world = _world(tmp_path, pane_close_rcs=[1, 0])
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "retired"
        assert "rearm_text" not in world.kinds()

    def test_a_failed_re_arm_is_the_one_incident_state(self, tmp_path):
        world = _world(tmp_path, pane_close_rcs=[1, 1, 1], confirm_rearm=False)
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "alive_and_unguarded"
        assert out["ok"] is False


class TestLockedStateUpdate:
    def test_concurrent_writers_do_not_lose_an_update(self, tmp_path):
        """An advisory lock only serialises writers that participate. Both writers this change
        introduces go through this ONE helper, so a claim cannot be clobbered by a generation
        bump landing between another writer's read and its write."""
        import threading

        p = tmp_path / "state.json"
        p.write_text(json.dumps({"generation": 1, "issues": []}), encoding="utf-8")

        def bump(state):
            time.sleep(0.02)                      # widen the read->write window
            state["generation"] = state.get("generation", 0) + 1
            return state

        def claim(state):
            time.sleep(0.02)
            state["handoff_claim"] = {"claimant": "succ", "started": False}
            return state

        threads = [threading.Thread(target=ll._locked_state_update, args=(str(p), bump)),
                   threading.Thread(target=ll._locked_state_update, args=(str(p), claim))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = json.loads(p.read_text(encoding="utf-8"))
        assert final["generation"] == 2                    # the bump landed
        assert final["handoff_claim"]["claimant"] == "succ"  # and so did the claim

    def test_a_mutator_returning_none_writes_nothing(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"generation": 1}), encoding="utf-8")
        ll._locked_state_update(str(p), lambda _s: None)
        assert json.loads(p.read_text(encoding="utf-8")) == {"generation": 1}

    def test_it_locks_the_sidecar_not_the_state_file(self, tmp_path):
        """`flock` follows the opened inode while an atomic write installs a NEW inode at the
        pathname, so a lock on the target would let two waiters hold different inodes."""
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"a": 1}), encoding="utf-8")
        ll._locked_state_update(str(p), lambda s: s)
        assert (tmp_path / "state.json.lock").exists()


class TestRetireCLI:
    def test_the_subcommand_exists(self):
        proc = subprocess.run([sys.executable, str(CLI), "retire-predecessor", "--help"],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 0
        assert "--driver-state" in proc.stdout and "--session-id" in proc.stdout

    def test_the_mid_child_handoff_subcommand_exists(self):
        proc = subprocess.run([sys.executable, str(CLI), "mid-child-handoff", "--help"],
                              capture_output=True, text=True, check=False)
        assert proc.returncode == 0
        assert "--anchor-pane" in proc.stdout

    def test_a_refusal_is_a_non_zero_exit(self, tmp_path):
        world = _world(tmp_path)
        state = _write_state(tmp_path, world, pend_over={"cancelled": True})
        env = {**os.environ, "CLAUDE_CODE_SESSION_ID": SUCC}
        proc = subprocess.run(
            [sys.executable, str(CLI), "retire-predecessor", "--driver-state", str(state),
             "--session-id", SUCC, "--anchor-pane", ANCHOR,
             "--transcript-dir", str(tmp_path / "transcripts"),
             "--registry", str(tmp_path / "reg.jsonl")],
            capture_output=True, text=True, check=False, env=env)
        assert proc.returncode != 0
        assert "cancel" in (proc.stdout + proc.stderr).lower()


class TestMidChildHandoffCommand:
    """The predecessor's side. `perform_handoff` is stubbed because it needs a live herdr server,
    but the STATE MACHINE around it is the load-bearing half and is exercised for real: a
    subcommand whose only test is `--help` is the disconnected-module smell #611 shipped twice."""

    @pytest.fixture(autouse=True)
    def _as_the_predecessor(self, monkeypatch):
        """These tests ARE the predecessor session, so its own id must be in the environment:
        since the 8a fix, a `--predecessor-session`/`--session-id` that contradicts
        $CLAUDE_CODE_SESSION_ID is refused rather than silently preferred."""
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", PRED)

    def _args(self, tmp_path, state_path, **over):
        from types import SimpleNamespace
        kw = {"driver_state": str(state_path), "anchor_pane": ANCHOR, "name": "succ",
              "project_root": str(tmp_path), "cwd": str(tmp_path),
              "registry": str(tmp_path / "reg.jsonl"),
              "transcript_dir": str(tmp_path / "transcripts"), "issue": ISSUE, "step": "8",
              "branch": BRANCH, "test_baseline": "5362 passed", "project": "rawgentic",
              "project_path": "./projects/rawgentic", "repo_root": str(tmp_path / "repo"),
              "predecessor_session": PRED, "launch_mode": "fresh", "goal_condition": None,
              "goal_condition_from": self._transcript(tmp_path)}
        kw.update(over)
        return SimpleNamespace(**kw)

    def _transcript(self, tmp_path):
        """The predecessor's own transcript, carrying its live unmet guard. Required since Step 11
        pass-2: the condition must have provenance, and `--goal-condition` alone cannot supply it."""
        t = tmp_path / "own-transcript.jsonl"
        if not t.exists():
            t.write_text(_goal_row(COND, met=False) + "\n", encoding="utf-8")
        return str(t)

    def _bare_state(self, tmp_path, issues=None):
        state = {"schema_version": 2, "campaign": "epic-667", "epic": 667, "generation": 4,
                 "issues": issues if issues is not None
                 else [{"number": ISSUE, "status": "in_progress"}]}
        p = tmp_path / "state.json"
        p.write_text(json.dumps(state), encoding="utf-8")
        return p

    def test_a_ready_handoff_persists_position_and_records_the_successor(self, tmp_path,
                                                                        monkeypatch):
        world = _world(tmp_path)
        state = self._bare_state(tmp_path)
        seen = {}

        def fake_perform(**kw):
            seen.update(kw)
            kw["on_successor"](SUCC_PANE, SUCC)       # what the real one does after `pane get`
            return {"ok": True, "results": {}, "failed_step": None, "new_pane": SUCC_PANE,
                    "session_id": SUCC, "truncated": False, "cleanup": None,
                    "teardown_skipped": None}

        monkeypatch.setattr(ll, "perform_handoff", fake_perform)
        rc = ll._cmd_mid_child_handoff(self._args(tmp_path, state))
        assert rc == 0

        pend = _state_of(state)["handoff_pending"]
        assert pend["kind"] == dl.MID_CHILD_HANDOFF_KIND
        assert pend["generation"] == 5 and _state_of(state)["generation"] == 5
        assert pend["position"]["branch"] == BRANCH
        assert pend["position"]["goal_condition"] == COND
        assert pend["successor"] == {"pane": SUCC_PANE, "session": SUCC}
        assert pend.get("cancelled") is not True
        # the successor is launched with the six-step ladder and a generation-bound marker,
        # and it is NOT the predecessor's job to retire anything
        assert seen["teardown"] is False
        assert [s["step"] for s in seen["steps"]] == [
            s["step"] for s in ll.mid_child_verification_steps()]
        assert seen["prompt_marker"] == dl.mid_child_marker(ISSUE, 5)
        assert seen["expected_project"] == "rawgentic"
        assert world is not None

    def test_a_failed_handoff_cancels_its_own_record(self, tmp_path, monkeypatch):
        """Otherwise the abandoned record IS the current generation and stays claimable, so a
        delayed successor could take a lease on a handoff that already aborted."""
        state = self._bare_state(tmp_path)
        monkeypatch.setattr(ll, "perform_handoff", lambda **kw: {
            "ok": False, "results": {}, "failed_step": "goal_armed", "new_pane": None,
            "session_id": None, "truncated": False, "cleanup": None, "teardown_skipped": None})
        rc = ll._cmd_mid_child_handoff(self._args(tmp_path, state))
        assert rc == 4
        assert _state_of(state)["handoff_pending"]["cancelled"] is True

    def test_no_active_child_writes_nothing(self, tmp_path, monkeypatch):
        state = self._bare_state(tmp_path, issues=[{"number": ISSUE, "status": "merged"}])
        monkeypatch.setattr(ll, "perform_handoff",
                            lambda **kw: pytest.fail("must not launch a successor"))
        rc = ll._cmd_mid_child_handoff(self._args(tmp_path, state))
        assert rc == 3
        assert "handoff_pending" not in _state_of(state)

    def test_a_position_that_names_the_wrong_child_writes_nothing(self, tmp_path, monkeypatch):
        state = self._bare_state(tmp_path)
        monkeypatch.setattr(ll, "perform_handoff",
                            lambda **kw: pytest.fail("must not launch a successor"))
        rc = ll._cmd_mid_child_handoff(self._args(tmp_path, state, issue=612))
        assert rc == 3
        assert "handoff_pending" not in _state_of(state)


# --- Task 5 / AC7: the anti-parallel-path guard --------------------------------------------
#
# What this IS: a source-level drift guard that makes a second handoff path fail the suite when
# someone writes one in the obvious ways. What it is NOT: a proof of architectural
# impossibility — Python offers no such enclosure, and an earlier revision of the design
# overstated it. The scanner is a plain function precisely so its own negative cases are
# testable: a guard that has never been shown to bite is not a guard.

_HANDOFF_BUILDERS = frozenset({
    "build_split_argv", "build_agent_start_argv", "build_agent_wait_argv",
    "build_send_text_argv", "build_send_text_goal_argv", "build_teardown_argv",
    "build_pane_get_argv", "perform_handoff", "retire_predecessor",
})
# A herdr COMMAND, not the bare word: `herdr` also appears legitimately as a terminal-backend
# NAME in capabilities_lib, driver_lib and executor_routing_lib, and a guard that fired on that
# would be a keyword alarm rather than a check.
# The DRIVING verbs only. A read-only `herdr api snapshot` or `herdr pane list` is not a handoff
# path, and flagging it would make this guard fire on any module that merely observes herdr — which
# it did, on #612's watcher. What AC7 protects is a second thing that SPLITS, CLOSES, TYPES INTO or
# STARTS an agent in a pane.
_HERDR_DRIVING_VERBS = ("split", "close", "send-text", "send-keys", "send_input", "start", "wait")
_HERDR_COMMAND_RE = re.compile(
    r"\bherdr\s+(?:pane|agent)\s+(?:" + "|".join(_HERDR_DRIVING_VERBS) + r")\b")


def _handoff_path_findings(source: str, *, filename: str) -> list[str]:
    """Every construction in `source` that would amount to a second handoff path."""
    findings: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        # 1. a raw herdr argv built by hand
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = node.elts[0]
            if isinstance(first, ast.Constant) and first.value == "herdr":
                # Only a DRIVING argv counts (see _HERDR_DRIVING_VERBS): an observing call such as
                # ["herdr","api","snapshot"] or ["herdr","pane","list"] is not a handoff path.
                verbs = {e.value for e in node.elts[1:]
                         if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                if verbs & set(_HERDR_DRIVING_VERBS):
                    findings.append(f"{filename}:{node.lineno} raw herdr DRIVING argv literal")
        # 2. the launcher's own builders, imported or reached through the module
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("launcher_lib"):
            for alias in node.names:
                if alias.name in _HANDOFF_BUILDERS:
                    findings.append(
                        f"{filename}:{node.lineno} imports handoff builder {alias.name!r}")
        if isinstance(node, ast.Name) and node.id in _HANDOFF_BUILDERS:
            findings.append(f"{filename}:{node.lineno} calls handoff builder {node.id!r}")
        if isinstance(node, ast.Attribute) and node.attr in _HANDOFF_BUILDERS:
            findings.append(f"{filename}:{node.lineno} reaches handoff builder {node.attr!r}")
        # 3. shelling out to herdr through a command STRING (the shell=True bypass), including
        #    the f-string form, whose literal segments are plain Constants
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and _HERDR_COMMAND_RE.search(node.value):
            findings.append(f"{filename}:{node.lineno} herdr command string")
    return findings


def _function_names(source: str) -> list[str]:
    return [n.name for n in ast.walk(ast.parse(source))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


class TestNoParallelHandoffPath:
    def test_no_parallel_handoff_path(self):
        """AC7. `launcher_lib` is the ONE module that may drive herdr; anything else doing it is
        a second ordered sequence, which is the defect the issue's rewrite exists to prevent."""
        offenders = {}
        for path in sorted((REPO_ROOT / "hooks").glob("*.py")):
            if path.name == "launcher_lib.py":
                continue
            findings = _handoff_path_findings(path.read_text(encoding="utf-8"),
                                              filename=path.name)
            if findings:
                offenders[path.name] = findings
        assert offenders == {}, f"a parallel handoff path appeared: {offenders}"

    def test_the_launcher_holds_exactly_one_ordered_sequence(self):
        """A second sequence inside the SAME module is just as much a parallel path."""
        names = _function_names((HOOKS / "launcher_lib.py").read_text(encoding="utf-8"))
        assert names.count("perform_handoff") == 1
        assert names.count("retire_predecessor") == 1

    def test_the_launcher_sources_the_state_machine_from_driver_lib(self):
        """The reuse AC1 demands, asserted rather than described: a hand-rolled generation bump
        or claim write inside the launcher fails here."""
        src = (HOOKS / "launcher_lib.py").read_text(encoding="utf-8")
        for attr in ("open_handoff", "handoff_claim", "handoff_ack_started",
                     "mid_child_handoff", "validate_mid_child_position", "mid_child_marker"):
            assert f"driver_lib.{attr}" in src, f"launcher_lib must source {attr} from driver_lib"
        defined = _function_names(src)
        for owned_by_driver_lib in ("open_handoff", "handoff_claim", "handoff_ack_started",
                                    "mid_child_handoff"):
            assert owned_by_driver_lib not in defined, \
                f"launcher_lib defines its own {owned_by_driver_lib} — that is a second mechanism"

    def test_open_handoff_is_the_only_handoff_pending_writer_in_driver_lib(self):
        src = (HOOKS / "driver_lib.py").read_text(encoding="utf-8")
        writers = set()
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Subscript) \
                            and isinstance(target.slice, ast.Constant) \
                            and target.slice.value == "handoff_pending":
                        writers.add(fn.name)
        assert writers == {"open_handoff"}, \
            f"handoff_pending must have exactly one writer in driver_lib, found {writers}"

    @pytest.mark.parametrize("bypass,source", [
        ("raw argv",
         'def go(runner, pane):\n'
         '    return runner(["herdr", "pane", "split", "--pane", pane])\n'),
        ("imported builder",
         'from launcher_lib import build_split_argv\n'
         'def go():\n    return build_split_argv(anchor_pane="w1:p1")\n'),
        ("module attribute",
         'import launcher_lib\n'
         'def go():\n    return launcher_lib.perform_handoff(anchor_pane="w1:p1")\n'),
        ("shell string",
         'import subprocess\n'
         'def go(pane):\n'
         '    subprocess.run("herdr pane split --pane " + pane, shell=True)\n'),
        ("f-string shell",
         'import subprocess\n'
         'def go(pane):\n'
         '    subprocess.run(f"herdr agent start x --pane {pane}", shell=True)\n'),
    ])
    def test_the_scanner_bites_each_bypass_form(self, bypass, source):
        assert _handoff_path_findings(source, filename="synthetic.py"), \
            f"the guard failed to flag the {bypass} bypass — it would pass a real one too"

    def test_an_OBSERVING_herdr_call_is_not_a_handoff_path(self):
        """Narrowed after #612's watcher tripped this guard with `["herdr","api","snapshot"]`.
        Reading herdr's state is not a second handoff path, and a guard that fires on any mention of
        herdr is noise that gets deleted rather than a check that gets kept."""
        for source in (
                'def go(r):\n    return r(["herdr", "api", "snapshot"])\n',
                'def go(r):\n    return r(["herdr", "pane", "list"])\n',
                'def go(r):\n    return r(["herdr", "pane", "get", p])\n'):
            assert _handoff_path_findings(source, filename="observer.py") == [], source

    @pytest.mark.parametrize("verb", ["split", "close", "send-text", "send-keys"])
    def test_a_DRIVING_herdr_argv_is_still_flagged(self, verb):
        source = f'def go(r, p):\n    return r(["herdr", "pane", "{verb}", p])\n'
        assert _handoff_path_findings(source, filename="synthetic.py"), verb

    def test_a_driving_agent_argv_is_still_flagged(self):
        source = 'def go(r, p):\n    return r(["herdr", "agent", "start", "x", "--pane", p])\n'
        assert _handoff_path_findings(source, filename="synthetic.py")

    def test_a_backend_NAME_is_not_a_handoff_path(self):
        """Precision matters as much as sensitivity: `herdr` is a legitimate terminal-backend
        value in three real hooks, and a guard that fired on the bare word would be noise that
        gets deleted rather than a check that gets kept."""
        source = ('BACKENDS = frozenset({"herdr", "native"})\n'
                  'def pick(config):\n'
                  '    return config.get("build") == "herdr"\n')
        assert _handoff_path_findings(source, filename="synthetic.py") == []


# --- WF2 Step 8a findings (two independent cross-model reviewers, both lenses) --------------
#
# Both reviewers converged on the same core defect: the design (§3 R4) requires
# `retire_predecessor` to re-validate `cancelled` AND the claim under the lock immediately
# before the destructive step, and the first implementation only checked them at entry. Every
# test below pins one confirmed finding.

def _spy(world, on_kind, effect):
    """Wrap a FakeWorld so `effect()` runs when a matching argv is issued — the way a real
    concurrent writer lands between our entry read and our destructive step."""
    def runner(argv, timeout=180):
        if on_kind(argv):
            effect()
        return world(argv, timeout)
    return runner


def _mutate_state(path, fn):
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    fn(state)
    Path(path).write_text(json.dumps(state), encoding="utf-8")


class TestCancellationAndGenerationAreFencedAtTheDestructiveStep:
    """8a finding (correctness 1 / destructive 3), CONFIRMED. The entry check is not enough: a
    cancel or a generation bump landing after it must still stop the teardown, because what
    follows clears a live guard and closes a pane."""

    def test_a_cancel_landing_before_the_clear_refuses(self, tmp_path):
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        runner = _spy(world, lambda a: a[:3] == ["herdr", "pane", "get"],
                      lambda: _mutate_state(
                          state, lambda s: s["handoff_pending"].update(cancelled=True)))
        out = _retire(state, world, tmp_path, runner=runner)
        assert out["outcome"] == "refused" and "cancel" in out["reason"].lower(), out
        assert "clear_text" not in world.kinds() and "pane_close" not in world.kinds()

    def test_a_superseding_generation_refuses(self, tmp_path):
        """If `open_handoff` has installed a newer generation, this claim is stale and its
        teardown would clear a guard the NEW handoff is relying on."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        runner = _spy(world, lambda a: a[:3] == ["herdr", "pane", "get"],
                      lambda: _mutate_state(state, lambda s: (
                          s.update(generation=GEN + 1),
                          s["handoff_pending"].update(generation=GEN + 1))))
        out = _retire(state, world, tmp_path, runner=runner)
        assert out["outcome"] == "refused" and "generation" in out["reason"], out
        assert "clear_text" not in world.kinds() and "pane_close" not in world.kinds()

    def test_a_foreign_claimant_taking_over_before_the_clear_refuses(self, tmp_path):
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        runner = _spy(world, lambda a: a[:3] == ["herdr", "pane", "get"],
                      lambda: _mutate_state(state, lambda s: s.update(
                          handoff_claim={"generation": GEN, "claimant": "someone-else",
                                         "claimed_at": 1, "started": True})))
        out = _retire(state, world, tmp_path, runner=runner)
        assert out["outcome"] == "refused" and "claim" in out["reason"].lower(), out
        assert "clear_text" not in world.kinds() and "pane_close" not in world.kinds()

    def test_a_failed_phase_write_aborts_rather_than_being_ignored(self, tmp_path):
        """The phase write is what makes the unguarded window discoverable. If it cannot be
        persisted, proceeding would open that window with nothing on disk to find it by."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path,
                      driver_state_path=str(tmp_path / "does-not-exist.json"))
        assert out["outcome"] in ("refused", "error"), out
        assert "clear_text" not in world.kinds() and "pane_close" not in world.kinds()


class TestPaneIdentityIsProvedBeforeEachDestructiveCall:
    """8a finding (destructive 2), CONFIRMED: identity was checked once and the handle then
    reused across the clear, the close, and every retry, with `pane get`'s return code ignored."""

    def test_a_failed_pane_get_refuses(self, tmp_path):
        world = _world(tmp_path, pane_get_rc=1)
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "refused" and "identity" in out["reason"]
        assert "clear_text" not in world.kinds() and "pane_close" not in world.kinds()

    def test_the_pane_is_re_proved_before_the_close(self, tmp_path):
        """A pane id is a reusable handle. If the predecessor exits after a confirmed clear and
        the id is reassigned, closing it would kill an unrelated session and report success."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)

        def steal_pane():
            world.pane_session = "a-totally-different-session"

        runner = _spy(world, lambda a: a[:3] == ["herdr", "pane", "send-keys"], steal_pane)
        out = _retire(state, world, tmp_path, runner=runner)
        assert out["outcome"] == "target_changed_after_clear", out
        assert "pane_close" not in world.kinds()
        assert "rearm_text" not in world.kinds()      # re-arming would also hit the wrong pane
        assert out["ok"] is False


class TestDestructiveRegionExceptionsAreNotSwallowedAsGenericErrors:
    """8a finding (destructive 4), CONFIRMED: `_default_runner` can raise TimeoutExpired, which
    jumped straight to the outer handler — skipping the close retries AND the re-arm, and
    reporting `error` while the predecessor was alive and unguarded."""

    def test_a_close_timeout_still_retries_and_re_arms(self, tmp_path):
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        calls = {"close": 0}

        def runner(argv, timeout=180):
            if argv[:3] == ["herdr", "pane", "close"]:
                calls["close"] += 1
                raise subprocess.TimeoutExpired(cmd=argv, timeout=180)
            return world(argv, timeout)

        out = _retire(state, world, tmp_path, runner=runner)
        assert calls["close"] == 3, "the remaining bounded retries must still run"
        assert out["outcome"] in ("alive_and_re_armed", "alive_and_unguarded"), out
        assert out["outcome"] != "error"


class TestAFailedEnterLeavesTheClearStaged:
    """8a finding (destructive 5), CONFIRMED: send-text succeeded and send-keys failed, so the
    `/goal clear` sits UNSUBMITTED in the predecessor's input. The old code reset the phase to
    None and reported 'STILL guarded' — but a later stray Enter submits it."""

    def test_the_phase_stays_discoverable_and_the_reason_is_honest(self, tmp_path):
        world = _world(tmp_path, clear_keys_rc=1, confirm_clear=False)
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "clear_failed"
        assert "pane_close" not in world.kinds()
        phase = _state_of(state)["handoff_pending"].get("teardown_phase")
        assert phase == "clear_staged_unsubmitted", phase
        assert "staged" in out["reason"].lower()

    def test_a_failed_send_text_is_still_a_clean_abort(self, tmp_path):
        """Nothing was transported, so there is nothing staged and the phase clears."""
        world = _world(tmp_path, clear_text_rc=1)
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "clear_failed"
        assert _state_of(state)["handoff_pending"].get("teardown_phase") is None


class TestLadderValidation:
    """8a finding (correctness 4), CONFIRMED: `steps` was accepted as complete authority, so an
    empty list authorised teardown vacuously and a hand-built four-step list silently disabled
    the successor-owned half of the gate."""

    def test_an_empty_ladder_is_refused_not_vacuously_passed(self):
        with pytest.raises(ll.LauncherError):
            ll.evaluate_verifications({}, steps=[])

    def test_an_unknown_step_name_is_refused(self):
        with pytest.raises(ll.LauncherError):
            ll.evaluate_verifications({"whatever": True},
                                      steps=[{"step": "whatever", "artifact": "x"}])

    def test_teardown_allowed_refuses_an_empty_ladder_too(self):
        with pytest.raises(ll.LauncherError):
            ll.teardown_allowed({}, steps=())

    def test_both_canonical_ladders_still_work(self):
        assert ll.evaluate_verifications({"spawned": True, "goal_armed": True,
                                          "project_switched": True})[0] is True
        assert ll.evaluate_verifications(
            {s["step"]: True for s in ll.mid_child_verification_steps()},
            steps=ll.mid_child_verification_steps())[0] is True


class TestHandoffRecordIsCancelledOnEveryFailurePath:
    """8a finding (correctness 5), CONFIRMED: `perform_handoff` validates pane/name/transcript
    AFTER the record is persisted and RAISES rather than returning, so a malformed argument
    bumped the generation and left an uncancelled mid_child record nobody could claim or use."""

    @pytest.fixture(autouse=True)
    def _as_the_predecessor(self, monkeypatch):
        """These tests ARE the predecessor session, so its own id must be in the environment:
        since the 8a fix, a `--predecessor-session`/`--session-id` that contradicts
        $CLAUDE_CODE_SESSION_ID is refused rather than silently preferred."""
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", PRED)

    def test_an_exception_from_perform_handoff_still_cancels(self, tmp_path, monkeypatch):
        state = TestMidChildHandoffCommand()._bare_state(tmp_path)
        args = TestMidChildHandoffCommand()._args(tmp_path, state)

        def boom(**kw):
            raise ll.LauncherError("malformed pane id 'nope!'")

        monkeypatch.setattr(ll, "perform_handoff", boom)
        rc = ll._cmd_mid_child_handoff(args)
        assert rc != 0
        assert _state_of(state)["handoff_pending"]["cancelled"] is True


class TestRepoRootIsBoundToTheProject:
    """8a finding (destructive 6), CONFIRMED: `--repo-root` and `--project-root` were
    independent, so `project_switched` could prove rawgentic while `position_rebuilt` proved an
    unrelated repository that happened to carry the same branch name."""

    @pytest.fixture(autouse=True)
    def _as_the_predecessor(self, monkeypatch):
        """These tests ARE the predecessor session, so its own id must be in the environment:
        since the 8a fix, a `--predecessor-session`/`--session-id` that contradicts
        $CLAUDE_CODE_SESSION_ID is refused rather than silently preferred."""
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", PRED)

    def test_a_repo_root_outside_the_project_root_is_refused(self, tmp_path, monkeypatch):
        state = TestMidChildHandoffCommand()._bare_state(tmp_path)
        args = TestMidChildHandoffCommand()._args(tmp_path, state,
                                                  repo_root="/somewhere/else/entirely")
        monkeypatch.setattr(ll, "perform_handoff",
                            lambda **kw: pytest.fail("must not launch a successor"))
        rc = ll._cmd_mid_child_handoff(args)
        assert rc != 0
        assert "handoff_pending" not in _state_of(state)


class TestTeardownAuthorityCannotBeImpersonated:
    """8a finding (destructive 1, CRITICAL), CONFIRMED: `--session-id` overrode
    $CLAUDE_CODE_SESSION_ID, so any session could pass the recorded successor's id and authorise
    the teardown — recreating the predecessor-driven path approach C was rejected for."""

    def test_an_explicit_session_id_may_not_contradict_the_environment(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "the-real-caller")
        with pytest.raises(ll.LauncherError):
            ll._own_session_id("someone-elses-session")

    def test_a_matching_explicit_id_is_accepted(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "the-real-caller")
        assert ll._own_session_id("the-real-caller") == "the-real-caller"

    def test_the_environment_wins_when_no_override_is_given(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "the-real-caller")
        assert ll._own_session_id(None) == "the-real-caller"


# --- WF2 Step 11 findings (two more independent reviewers, both returned FAIL) --------------
#
# Step 11 refuted several of the 8a fixes rather than confirming them: narrowing the identity
# override still left `env -u CLAUDE_CODE_SESSION_ID` open, "known step names" still authorised a
# one-step ladder, and re-proving the pane ONCE after the clear still let a close retry act on a
# reused handle. Three of these were reproduced by direct probe before being fixed.

class TestImpersonationIsClosedNotJustNarrowed:
    def test_an_unset_environment_is_refused_for_a_destructive_teardown(self, monkeypatch):
        """Probed on the shipped code: with the variable removed, `_own_session_id` returned the
        caller's spoofed value, so the predecessor could retire itself."""
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
        with pytest.raises(ll.LauncherError):
            ll._own_session_id("a-stolen-successor-id", require_env=True)

    def test_the_retire_cli_refuses_without_an_environment_identity(self, tmp_path):
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
        proc = subprocess.run(
            [sys.executable, str(CLI), "retire-predecessor", "--driver-state", str(state),
             "--session-id", SUCC, "--anchor-pane", ANCHOR,
             "--transcript-dir", str(tmp_path / "transcripts"),
             "--registry", str(tmp_path / "reg.jsonl")],
            capture_output=True, text=True, check=False, env=env)
        assert proc.returncode != 0
        assert "CLAUDE_CODE_SESSION_ID" in (proc.stdout + proc.stderr)


class TestOnlyACanonicalLadderMayGate:
    def test_a_single_step_ladder_is_refused(self):
        """Probed on the shipped code: this returned
        (True, 'all handoff verifications passed — predecessor may be retired')."""
        with pytest.raises(ll.LauncherError):
            ll.teardown_allowed({"spawned": True}, steps=[{"step": "spawned"}])

    def test_a_reordered_ladder_is_refused(self):
        rungs = ll.mid_child_verification_steps()
        rungs[0], rungs[1] = rungs[1], rungs[0]
        with pytest.raises(ll.LauncherError):
            ll.evaluate_verifications({s["step"]: True for s in rungs}, steps=rungs)

    def test_the_predecessor_owned_prefix_is_permitted(self):
        prefix = [s for s in ll.mid_child_verification_steps()
                  if s.get("owner") != "successor"]
        ok, _, _ = ll.evaluate_verifications({s["step"]: True for s in prefix}, steps=prefix)
        assert ok is True


class TestIdentityIsReProvedBeforeEveryDestructiveCall:
    def test_a_pane_reassigned_between_close_retries_is_not_closed_again(self, tmp_path):
        """The close is explicitly allowed to be ambiguous, so attempt 1 can succeed server-side
        while the client reports failure. If the id is reassigned in the retry delay, attempt 2
        would close a colleague's pane and report `retired`."""
        world = _world(tmp_path, pane_close_rcs=[1, 0])
        state = _write_state(tmp_path, world)
        closes = {"n": 0}

        def runner(argv, timeout=180):
            if argv[:3] == ["herdr", "pane", "close"]:
                closes["n"] += 1
                if closes["n"] == 1:
                    world.pane_session = "somebody-else-entirely"   # id reused
            return world(argv, timeout)

        out = _retire(state, world, tmp_path, runner=runner)
        assert closes["n"] == 1, "the second close must never be issued"
        assert out["outcome"] == "target_changed_after_clear", out

    def test_a_post_clear_identity_timeout_is_not_a_generic_error(self, tmp_path):
        """The probe used to call the runner directly, so a TimeoutExpired there skipped the
        close AND the re-arm and reported `error` with the predecessor possibly unguarded."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        seen = {"clear": False}

        def runner(argv, timeout=180):
            if argv[:3] == ["herdr", "pane", "send-keys"]:
                seen["clear"] = True
                return world(argv, timeout)
            if seen["clear"] and argv[:3] == ["herdr", "pane", "get"]:
                raise subprocess.TimeoutExpired(cmd=argv, timeout=180)
            return world(argv, timeout)

        out = _retire(state, world, tmp_path, runner=runner)
        # A transient/persistent probe failure is NOT a proven mismatch: the close is retried, the
        # re-arm is attempted, and when neither can be proved the honest terminal state is the
        # incident one — never a generic `error`.
        assert out["outcome"] == "alive_and_unguarded", out
        assert out["outcome"] != "error"


class TestPhaseWritesAreGenerationScoped:
    def test_a_superseded_run_cannot_stamp_the_new_generations_record(self, tmp_path):
        """An unfenced phase write validated nothing, so a stale invocation could stamp — or
        CLEAR — teardown_phase on a newer generation's record, hiding its unguarded window."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        def supersede():
            """What `open_handoff` really does: bump the counter and write a FRESH pending record.
            (Renaming the existing record in place is not a supersession — it would carry our own
            teardown_phase across, which is an artifact of the test rather than of the code.)"""
            _mutate_state(state, lambda s: (
                s.update(generation=GEN + 5),
                s.update(handoff_pending={"generation": GEN + 5, "next_issue": ISSUE,
                                          "written_ts": 2,
                                          "kind": dl.MID_CHILD_HANDOFF_KIND,
                                          "cancelled": False, "teardown_phase": None,
                                          "position": _position(world),
                                          "successor": {"pane": "w1:pZ",
                                                        "session": "a-newer-successor"}})))

        runner = _spy(world, lambda a: a[:3] == ["herdr", "pane", "send-text"], supersede)
        out = _retire(state, world, tmp_path, runner=runner)
        # the superseded teardown must stop, not clear-and-close for a generation it no longer owns
        assert out["outcome"] in ("clear_failed", "refused"), out
        # whatever the outcome, the NEW generation's record must not carry our phase
        pend = _state_of(state)["handoff_pending"]
        assert pend["generation"] == GEN + 5
        # the NEW generation's record must carry none of our phase writes at all
        assert pend.get("teardown_phase") is None, pend
        # and the superseded teardown must not have closed anything
        assert "pane_close" not in world.kinds(), world.kinds()


class TestAnAmbiguousClearSendIsTreatedAsStaged:
    def test_a_raising_send_text_records_a_staged_clear(self, tmp_path):
        """A raise is NOT proof nothing was transported: herdr may have accepted the text before
        the client timed out. Resetting the phase would erase the evidence."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)

        def runner(argv, timeout=180):
            if argv[:3] == ["herdr", "pane", "send-text"] and argv[4].startswith("/goal clear"):
                raise subprocess.TimeoutExpired(cmd=argv, timeout=180)
            return world(argv, timeout)

        out = _retire(state, world, tmp_path, runner=runner)
        assert out["outcome"] == "clear_failed"
        assert _state_of(state)["handoff_pending"]["teardown_phase"] == "clear_staged_unsubmitted"
        assert "staged" in out["reason"].lower()


class TestAnUnrecordableSuccessorIsAFailedLaunch:
    def test_a_superseded_generation_cannot_record_its_successor(self, tmp_path, monkeypatch):
        """Step 11 pass-3 (verify 6) caught the previous version of this test never creating a
        supersession at all: it renumbered the OLD record, then the command opened a fresh
        generation of its own before the callback ran, so `on_successor` legitimately returned True
        and the assertion proved nothing. Here the supersession lands BETWEEN the command's own
        `open_handoff` and its callback — the real race — by bumping the generation from inside
        the fake launch."""
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", PRED)
        state = TestMidChildHandoffCommand()._bare_state(tmp_path)
        args = TestMidChildHandoffCommand()._args(tmp_path, state)
        seen = {}

        def fake_perform(**kw):
            # a concurrent mid-child handoff opens the next generation first
            _mutate_state(state, lambda st: (
                st.update(generation=999),
                st.update(handoff_pending={"generation": 999, "next_issue": ISSUE,
                                           "written_ts": 3,
                                           "kind": dl.MID_CHILD_HANDOFF_KIND,
                                           "cancelled": False, "teardown_phase": None})))
            seen["recorded"] = kw["on_successor"](SUCC_PANE, SUCC)
            return {"ok": seen["recorded"], "results": {}, "failed_step": None,
                    "new_pane": SUCC_PANE, "session_id": SUCC, "truncated": False,
                    "cleanup": None, "teardown_skipped": None}

        monkeypatch.setattr(ll, "perform_handoff", fake_perform)
        rc = ll._cmd_mid_child_handoff(args)
        assert seen["recorded"] is False, "a superseded generation must not record a successor"
        assert rc != 0

    def test_perform_handoff_aborts_when_the_successor_cannot_be_recorded(self, tmp_path):
        """The contract itself: `on_successor` returning False is a failed launch."""
        calls = []

        def runner(argv, timeout=180):
            calls.append(argv)
            if argv[:3] == ["herdr", "pane", "list"]:
                return FakeProc(0, json.dumps({"result": {"panes": [{"pane_id": ANCHOR}]}}))
            if argv[:3] == ["herdr", "pane", "split"]:
                return FakeProc(0, json.dumps({"result": {"pane_id": SUCC_PANE}}))
            if argv[:3] == ["herdr", "pane", "get"]:
                return FakeProc(0, json.dumps({"result": {"agent_session": {"value": SUCC}}}))
            return FakeProc(0, "")

        (tmp_path / "t").mkdir()
        out = ll.perform_handoff(
            anchor_pane=ANCHOR, cwd=str(tmp_path), project_root=str(tmp_path), name="succ",
            expected_project="rawgentic",
            # no bind inside the prompt: the launcher sends it as SEND 1 of its own (#694)
            goal_condition=COND, resume_prompt="marker-x — do the thing",
            registry_path=str(tmp_path / "reg.jsonl"), transcript_dir=str(tmp_path / "t"),
            runner=runner, sleeper=lambda _s: None, read_text=lambda p: "",
            prompt_marker="marker-x", steps=ll.mid_child_verification_steps(),
            teardown=False, on_successor=lambda pane, sess: False)
        assert out["failed_step"] == "successor_not_recorded", out
        assert not any(a[:3] == ["herdr", "pane", "send-text"] for a in calls), \
            "nothing may be armed for a successor that cannot be recorded"


class TestTheGoalConditionIsBoundToTheLiveGuard:
    @pytest.fixture(autouse=True)
    def _as_the_predecessor(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", PRED)

    def _argv(self, tmp_path, state, transcript, *extra):
        """Through the REAL parser. Step 11 pass-2 caught the previous version fabricating an
        argparse state the CLI could not produce (it set both `--goal-condition` and
        `--goal-condition-from` while the parser made them mutually exclusive), so the check it
        claimed to pin was unreachable in production."""
        return ["mid-child-handoff", "--driver-state", str(state), "--anchor-pane", ANCHOR,
                "--name", "succ", "--project", "rawgentic", "--project-root", str(tmp_path), "--cwd", str(tmp_path),
                "--registry", str(tmp_path / "reg.jsonl"),
                "--transcript-dir", str(tmp_path / "transcripts"),
                "--issue", str(ISSUE), "--step", "8", "--branch", BRANCH,
                "--test-baseline", "5362 passed", "--project", "rawgentic",
                "--project-path", "./projects/rawgentic",
                "--repo-root", str(tmp_path / "repo"),
                "--predecessor-session", PRED,
                "--goal-condition-from", str(transcript), *extra]

    def test_a_condition_that_has_since_been_met_is_refused(self, tmp_path, monkeypatch):
        """Probed on the shipped code: `last_unmet_goal_condition` returns a condition even when a
        LATER met:true row for it exists, so the successor would be armed with a satisfied guard."""
        state = TestMidChildHandoffCommand()._bare_state(tmp_path)
        transcript = tmp_path / "pred.jsonl"
        transcript.write_text("\n".join([_goal_row(COND, met=False),
                                         _goal_row(COND, met=True)]) + "\n", encoding="utf-8")
        monkeypatch.setattr(ll, "perform_handoff",
                            lambda **kw: pytest.fail("must not launch a successor"))
        assert ll.main(self._argv(tmp_path, state, transcript)) == 3

    def test_an_explicit_condition_must_match_the_live_guard(self, tmp_path, monkeypatch):
        state = TestMidChildHandoffCommand()._bare_state(tmp_path)
        transcript = tmp_path / "pred.jsonl"
        transcript.write_text(_goal_row(COND, met=False) + "\n", encoding="utf-8")
        monkeypatch.setattr(ll, "perform_handoff",
                            lambda **kw: pytest.fail("must not launch a successor"))
        assert ll.main(self._argv(tmp_path, state, transcript,
                                  "--goal-condition", "a weaker typoed condition")) == 3

    def test_the_parser_accepts_both_so_provenance_is_always_available(self, tmp_path):
        """The two flags must NOT be mutually exclusive any more — that exclusivity is precisely
        what made the explicit path unverifiable."""
        state = TestMidChildHandoffCommand()._bare_state(tmp_path)
        transcript = tmp_path / "pred.jsonl"
        transcript.write_text(_goal_row(COND, met=False) + "\n", encoding="utf-8")
        argv = self._argv(tmp_path, state, transcript, "--goal-condition", COND)
        parser_ok = True
        try:
            ll.main(argv + ["--launch-mode", "fresh"])
        except SystemExit:
            parser_ok = False       # argparse rejected the combination
        assert parser_ok, "the CLI must accept a condition together with its provenance"


class TestTheReceiptStepIsCompared:
    def test_a_receipt_step_that_disagrees_fails_position_rebuilt(self, tmp_path):
        """The receipt carried `step` and the read-back never compared it."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        # a stale receipt for the same generation/claimant but a DIFFERENT step
        _mutate_state(state, lambda s: s["handoff_pending"].update(
            rebuild_receipt={"generation": GEN, "claimant": SUCC, "branch_observed": BRANCH,
                             "repo_root_observed": world.repo, "step": "3", "ts": 1}))
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "retired"      # our own fresh receipt overwrites the stale one
        receipt = _state_of(state)["handoff_pending"]["rebuild_receipt"]
        assert receipt["step"] == "8"

    def test_a_receipt_that_cannot_be_refreshed_fails_position_rebuilt(self, tmp_path,
                                                                      monkeypatch):
        """The rejection path itself, which the first version of this test never reached because
        our own write always overwrote the stale receipt. Here the write is prevented, so the
        read-back genuinely sees a receipt for a different step."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        _mutate_state(state, lambda s: s["handoff_pending"].update(
            rebuild_receipt={"generation": GEN, "claimant": SUCC, "branch_observed": BRANCH,
                             "repo_root_observed": world.repo, "step": "3", "ts": 1}))
        real = ll._locked_state_update

        def blocking_update(path, mutate):
            probe = mutate(json.loads(Path(path).read_text(encoding="utf-8")))
            if isinstance(probe, dict) \
                    and isinstance(probe.get("handoff_pending"), dict) \
                    and probe["handoff_pending"].get("rebuild_receipt", {}).get("step") == "8":
                return None                       # the receipt refresh cannot be persisted
            return real(path, mutate)

        monkeypatch.setattr(ll, "_locked_state_update", blocking_update)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "teardown_refused"
        assert out["failed_step"] == "position_rebuilt", out
        assert "clear_text" not in world.kinds() and "pane_close" not in world.kinds()


# --- WF2 Step 11 pass 3 (both reviewers: DO NOT MERGE) -------------------------------------
#
# The decisive finding, reproduced end-to-end by one reviewer: clear confirmation accepted ANY
# `met:true` row, so a replacement guard's own clear "confirmed" ours and an irreversible
# `pane close` followed. Everything here pins one confirmed pass-3 finding.

class TestClearConfirmationIsBoundToTheRecordedCondition:
    def test_a_foreign_conditions_cleared_row_does_not_confirm(self):
        foreign = _goal_row("some completely different guard", met=True)
        assert ll.transcript_has_cleared_goal(foreign, expected_condition=COND) is False
        assert ll.transcript_has_cleared_goal(foreign) is True      # unbound reader, unchanged

    def test_the_recorded_conditions_cleared_row_confirms(self):
        assert ll.transcript_has_cleared_goal(_goal_row(COND, met=True),
                                              expected_condition=COND) is True

    def test_a_replacement_guards_clear_does_not_authorise_a_close(self, tmp_path):
        """The reproduction: the predecessor's own clear of a DIFFERENT guard used to satisfy the
        confirmation poll, and the pane was closed on it."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)

        def runner(argv, timeout=180):
            if argv[:3] == ["herdr", "pane", "send-keys"]:
                # herdr ignores our /goal clear, but a row for another guard appears
                world._staged = None
                world._append_pred(_goal_row("a different guard entirely", met=True))
                return FakeProc(0, "")
            return world(argv, timeout)

        out = _retire(state, world, tmp_path, runner=runner)
        assert out["outcome"] == "clear_unconfirmed", out
        assert "pane_close" not in world.kinds(), world.kinds()


class TestThePredecessorGuardCheckFailsClosed:
    def test_no_goal_status_row_at_all_is_refused(self, tmp_path):
        """It used to refuse only on a DIFFERING condition, so absent evidence passed."""
        world = _world(tmp_path)
        world.pred_transcript.write_text("", encoding="utf-8")
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "refused" and "no goal_status row" in out["reason"]
        assert "clear_text" not in world.kinds() and "pane_close" not in world.kinds()

    def test_an_already_met_newest_row_is_refused(self, tmp_path):
        world = _world(tmp_path)
        world.pred_transcript.write_text(
            "\n".join([_goal_row(COND, met=False), _goal_row(COND, met=True)]) + "\n",
            encoding="utf-8")
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "refused" and "not unmet" in out["reason"]
        assert "clear_text" not in world.kinds()

    def test_a_replacement_condition_is_refused(self, tmp_path):
        world = _world(tmp_path)
        world.pred_transcript.write_text(
            "\n".join([_goal_row(COND, met=False),
                       _goal_row("a replacement guard", met=False)]) + "\n", encoding="utf-8")
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "refused" and "DIFFERENT condition" in out["reason"]
        assert "clear_text" not in world.kinds()


class TestATransientStateReadIsNotProvenRevocation:
    def test_an_unreadable_state_file_after_the_clear_still_retries(self, tmp_path):
        """`_state_fence` used to collapse an I/O error into the same verdict as a proven
        cancellation, so one transient failure after a confirmed clear abandoned every remaining
        close attempt AND the re-arm."""
        world = _world(tmp_path, pane_close_rcs=[1, 0])
        state = _write_state(tmp_path, world)
        real_read = ll._locked_state_read
        flaked = {"n": 0}

        def flaky_read(path):
            if flaked["n"] == 0 and world.kinds().count("pane_close") == 1:
                flaked["n"] += 1
                raise OSError("transient")
            return real_read(path)

        import unittest.mock as mock
        with mock.patch.object(ll, "_locked_state_read", flaky_read):
            out = _retire(state, world, tmp_path)
        assert flaked["n"] == 1, "the transient failure must actually have been exercised"
        assert out["outcome"] == "retired", out


class TestTheReceiptCarriesTheRecordedBaseline:
    def test_the_receipt_echoes_and_compares_the_test_baseline(self, tmp_path):
        """#665's AC4 addendum says the receipt echoes the WF2 step AND the recorded baseline."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "retired", out
        receipt = _state_of(state)["handoff_pending"]["rebuild_receipt"]
        assert receipt["test_baseline_observed"] == \
            "5362 passed, 21 skipped, 0 failed, exit 0"
        assert receipt["step"] == "8"


class TestAnExplicitlyAssertedReplacedGuardIsRefused:
    def test_a_condition_that_is_no_longer_the_newest_is_refused(self, tmp_path, monkeypatch):
        """`goal_currently_unmet` examines only matching rows by design, so with history
        `A/met:false` then `B/met:false` an explicit `--goal-condition A` was accepted."""
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", PRED)
        state = TestMidChildHandoffCommand()._bare_state(tmp_path)
        transcript = tmp_path / "pred.jsonl"
        transcript.write_text("\n".join([_goal_row(COND, met=False),
                                         _goal_row("a replacement guard", met=False)]) + "\n",
                             encoding="utf-8")
        monkeypatch.setattr(ll, "perform_handoff",
                            lambda **kw: pytest.fail("must not launch a successor"))
        rc = ll.main(TestTheGoalConditionIsBoundToTheLiveGuard()._argv(
            tmp_path, state, transcript, "--goal-condition", COND))
        assert rc == 3


class TestNothingHappensBetweenTheBaselineAndTheClearSend:
    """Step 11 pass-4 (Critical). The pass-3 restructure claimed `fence → probe → baseline → send`,
    but `_destructive_call` re-ran its own fence AND a whole `herdr pane get` between the baseline
    and the transport — so a `met:true` row for the RECORDED condition landing during those reads
    sat below the baseline and confirmed a clear herdr never executed. This pins the ORDERING
    structurally, because the timing itself is not observable from a test."""

    def test_nothing_is_read_between_the_baseline_and_the_clear_send(self, tmp_path):
        """The property that matters is not adjacency of *some* probe — the buggy version also ended
        with a `pane get` before the send. It is that the predecessor-transcript BASELINE is the last
        thing that happens before the transport. So both the runner and the reader log into one
        ordered event list, and the assertion is on that interleaving."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        events: list[str] = []
        real_open = open

        def read_text(path):
            if str(path).endswith(f"{PRED}.jsonl"):
                events.append("read_pred_transcript")
            with real_open(path, encoding="utf-8") as fh:
                return fh.read()

        def runner(argv, timeout=180):
            if argv[:3] == ["herdr", "pane", "get"]:
                events.append("pane_get")
            elif argv[:3] == ["herdr", "pane", "send-text"] \
                    and argv[4].startswith("/goal clear"):
                events.append("clear_text")
            elif argv[0] == "git":
                events.append("git")
            return world(argv, timeout)

        out = _retire(state, world, tmp_path, runner=runner, read_text=read_text)
        assert out["outcome"] == "retired", out
        i_clear = events.index("clear_text")
        # the LAST event before the send must be the baseline read, with no herdr round-trip after it
        assert events[i_clear - 1] == "read_pred_transcript", events[:i_clear + 1]

    def test_a_matching_row_appearing_before_the_send_cannot_confirm(self, tmp_path):
        """The residual is now baseline→syscall only. Prove the guard still refuses when the row
        that would confirm us is already present BEFORE the send: the predecessor's guard is then
        provably not unmet, so the pre-clear check refuses and nothing is transported."""
        world = _world(tmp_path)
        world.pred_transcript.write_text(
            "\n".join([_goal_row(COND, met=False), _goal_row(COND, met=True)]) + "\n",
            encoding="utf-8")
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "refused", out
        assert "clear_text" not in world.kinds() and "pane_close" not in world.kinds()


class TestTheReceiptComparisonActuallyBites:
    def test_a_stale_receipt_with_a_wrong_baseline_fails_position_rebuilt(self, tmp_path,
                                                                        monkeypatch):
        """Step 11 pass-4: the echo test could not fail if the read-back comparison were deleted,
        because the implementation writes the value it then compares. This blocks the refresh so a
        genuinely stale receipt — right generation and claimant, WRONG baseline — is read back."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        _mutate_state(state, lambda s: s["handoff_pending"].update(
            rebuild_receipt={"generation": GEN, "claimant": SUCC, "branch_observed": BRANCH,
                             "repo_root_observed": world.repo, "step": "8",
                             "test_baseline_observed": "9999 passed — a stale baseline", "ts": 1}))
        real = ll._locked_state_update

        def blocking_update(path, mutate):
            probe = mutate(json.loads(Path(path).read_text(encoding="utf-8")))
            if isinstance(probe, dict) \
                    and isinstance(probe.get("handoff_pending"), dict) \
                    and "5362" in str(probe["handoff_pending"].get("rebuild_receipt", {})
                                      .get("test_baseline_observed", "")):
                return None                       # the refresh cannot be persisted
            return real(path, mutate)

        monkeypatch.setattr(ll, "_locked_state_update", blocking_update)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "teardown_refused"
        assert out["failed_step"] == "position_rebuilt", out
        assert "clear_text" not in world.kinds() and "pane_close" not in world.kinds()


class TestAReArmedPredecessorIsNotClosed:
    """Step 11 pass-4 (Critical, reproduced by a reviewer): the close path re-checked driver state
    and pane identity but never GUARD state, so a predecessor that armed a new guard between the
    confirmed clear and the close was closed anyway — destroying a live guarded context. The
    declared clear-to-close window assumes it stays unguarded; this is the case where it does not."""

    def test_a_new_guard_after_the_confirmed_clear_stops_the_close(self, tmp_path):
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)

        # The guard must appear during the PRE-CLOSE window, not before the check: appending it
        # right after send-keys (the first version of this test) is caught by the check trivially
        # and proves nothing about the race. Injecting it on the pre-close `pane get` is the real
        # timing a reviewer reproduced.
        seen = {"cleared": False}

        def runner(argv, timeout=180):
            result = world(argv, timeout)
            if argv[:3] == ["herdr", "pane", "send-keys"]:
                seen["cleared"] = True
            elif seen["cleared"] and argv[:3] == ["herdr", "pane", "get"]:
                world._append_pred(_goal_row("a brand new guard it just armed", met=False))
            return result

        out = _retire(state, world, tmp_path, runner=runner)
        assert out["outcome"] == "predecessor_re_armed", out
        assert "pane_close" not in world.kinds(), world.kinds()
        assert "rearm_text" not in world.kinds(), world.kinds()
        assert _state_of(state)["handoff_pending"]["teardown_phase"] == "predecessor_re_armed"
        assert out["ok"] is False


    def test_an_unreadable_transcript_does_not_hide_a_re_arm(self, tmp_path):
        """Step 11 pass-5: the check used to fail SAFE to "not re-armed", and a reviewer hid a real
        re-arm behind one read error and got `retired` plus a `pane close`. Refusing costs a
        recoverable stall; proceeding destroys a live guarded session."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        seen = {"cleared": False}
        real_open = open

        def read_text(path):
            if seen["cleared"] and str(path).endswith(f"{PRED}.jsonl"):
                raise OSError("transient read failure")
            with real_open(path, encoding="utf-8") as fh:
                return fh.read()

        def runner(argv, timeout=180):
            result = world(argv, timeout)
            if argv[:3] == ["herdr", "pane", "send-keys"]:
                seen["cleared"] = True
            return result

        out = _retire(state, world, tmp_path, runner=runner, read_text=read_text)
        assert "pane_close" not in world.kinds(), world.kinds()
        assert out["ok"] is False

    def test_a_normally_cleared_predecessor_is_still_closed(self, tmp_path):
        """The check must not block the ordinary path: after a confirmed clear the newest row is
        our own met:true, which is not a re-arm."""
        world = _world(tmp_path)
        state = _write_state(tmp_path, world)
        out = _retire(state, world, tmp_path)
        assert out["outcome"] == "retired", out
        assert world.kinds().count("pane_close") == 1
