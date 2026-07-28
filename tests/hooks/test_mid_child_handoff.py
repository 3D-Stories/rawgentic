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
import os
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
                if self.clear_text_rc == 0 and self.confirm_clear:
                    self._append_pred(_goal_row(COND, met=True))
                return FakeProc(self.clear_text_rc, "")
            if self.confirm_rearm:                       # the re-arm paste
                self._append_pred(_goal_row(COND, met=False))
            return FakeProc(0, "")
        if argv[:3] == ["herdr", "pane", "send-keys"]:
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
        proc = subprocess.run(
            [sys.executable, str(CLI), "retire-predecessor", "--driver-state", str(state),
             "--session-id", SUCC, "--anchor-pane", ANCHOR,
             "--transcript-dir", str(tmp_path / "transcripts"),
             "--registry", str(tmp_path / "reg.jsonl")],
            capture_output=True, text=True, check=False)
        assert proc.returncode != 0
        assert "cancel" in (proc.stdout + proc.stderr).lower()


class TestMidChildHandoffCommand:
    """The predecessor's side. `perform_handoff` is stubbed because it needs a live herdr server,
    but the STATE MACHINE around it is the load-bearing half and is exercised for real: a
    subcommand whose only test is `--help` is the disconnected-module smell #611 shipped twice."""

    def _args(self, tmp_path, state_path, **over):
        from types import SimpleNamespace
        kw = {"driver_state": str(state_path), "anchor_pane": ANCHOR, "name": "succ",
              "project_root": str(tmp_path), "cwd": str(tmp_path),
              "registry": str(tmp_path / "reg.jsonl"),
              "transcript_dir": str(tmp_path / "transcripts"), "issue": ISSUE, "step": "8",
              "branch": BRANCH, "test_baseline": "5362 passed", "project": "rawgentic",
              "project_path": "./projects/rawgentic", "repo_root": str(tmp_path / "repo"),
              "predecessor_session": PRED, "launch_mode": "fresh", "goal_condition": COND,
              "goal_condition_from": None}
        kw.update(over)
        return SimpleNamespace(**kw)

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
