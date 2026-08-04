"""#700 field defects — the ad-hoc teardown left a live pane with its guard still armed.

Found during a REAL handoff on 2026-07-29 (thewanderinginn epic #7 child #5). The handoff itself
succeeded — all four gates passed — but the predecessor pane stayed open with its `/goal` armed and
looped its Stop hook four times before the owner intervened. That is the #694 pathology reached
through a different door, and three separate defects produced it:

1. **Contradictory defaults.** `perform_handoff` defaulted `teardown=True` while the
   `ad-hoc-handoff` CLI defaulted it False, so a library caller and a CLI caller got opposite
   behaviour for one operation. Fixed by removing the library default entirely rather than picking a
   side: every caller must now STATE its intent, which is also what keeps the "teardown is blocked
   until every check passes" tests meaningful instead of vacuous.

2. **Teardown closed a pane whose guard was still armed.** `build_teardown_argv` is close-only,
   while the successor-owned `retire-predecessor` path (#665) correctly does clear -> confirm ->
   close. Usually masked because the session dies with the pane — but when the close fails or is
   skipped the owner is left with a session that can never stop, which is what happened.

3. **Teardown OFF said nothing.** The default path left the guard armed and never mentioned it, so
   the surprise was silent. It now reports the condition and names `/goal clear`.

The campaign path deliberately keeps its previous observable behaviour when no predecessor session
is supplied (close-only, plus a recorded warning): it is an unattended path that cannot be
live-tested from here, so it is not given new refusal semantics on the strength of a unit test.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS = REPO_ROOT / "hooks"

if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

import launcher_lib as ll  # noqa: E402

PROJECT = "rawgentic"
PROJECT_PATH = "./projects/rawgentic"
REGISTRY_PATH = "/reg.jsonl"
MARKER = "[handoff-700]"
RESUME_PROMPT = f"{MARKER} Resume epic #626: build the ad-hoc handoff subcommand."
GOAL_CONDITION = "PR open with green CI, or a blocker is posted to the issue"

SPLIT_OK = json.dumps({"result": {"pane_id": "w1:pZZ"}})
PANE_LIST_ANCHOR_ONLY = json.dumps({"result": {"panes": [{"pane_id": "w1:p1"}]}})
PANE_GET_OK = json.dumps({"result": {"pane": {
    "pane_id": "w1:pZZ", "agent_status": "idle",
    "agent_session": {"agent": "claude", "kind": "id", "source": "herdr:claude",
                      "value": "sess-new-123"}}}})
REGISTRY_ROW = json.dumps({"session_id": "sess-new-123", "project": PROJECT,
                           "project_path": PROJECT_PATH})


class FakeProc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class Runner:
    """Replays canned responses by command shape and records every argv."""

    def __init__(self, responses=None, fail_on=None):
        self.calls: list[list[str]] = []
        self.responses = dict(responses or {})
        self.fail_on = fail_on

    @staticmethod
    def key(argv):
        return " ".join(argv[:3])

    def __call__(self, argv, timeout=180):
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
        self.calls.append(list(argv))
        k = self.key(argv)
        if self.fail_on and k.startswith(self.fail_on):
            return FakeProc(returncode=1)
        if k == "herdr pane list" and k not in self.responses:
            return FakeProc(0, PANE_LIST_ANCHOR_ONLY)
        return FakeProc(0, self.responses.get(k, ""))


PRED_SESSION = "pred-session-0001"
PRED_CONDITION = "ship #700 or post a blocker"
# The predecessor's own `/goal clear` receipt: met AND sentinel, which is what distinguishes a
# guard row from any other record carrying a met flag.
CLEARED_ROW = json.dumps({"attachment": {"type": "goal_status", "met": True, "sentinel": True,
                                         "condition": PRED_CONDITION}})
# The predecessor's guard as it stands BEFORE teardown. Present in the fixture because since #707 the
# code READS it: liveness is decided from the transcript, so a fake with no armed row models a
# predecessor with nothing to clear — a different case entirely, covered by TestThreeGuardStates.
ARMED_ROW = json.dumps({"attachment": {"type": "goal_status", "met": False, "sentinel": True,
                                       "condition": PRED_CONDITION}})
SUCCESSOR_GOAL_ROW = json.dumps({"attachment": {
    "type": "goal_status", "met": False, "sentinel": True,
    "condition": ll.armed_condition(GOAL_CONDITION)[0]}})


class Artifacts:
    """Registry, successor transcript and PREDECESSOR transcript over one launch.

    The predecessor's cleared row appears only after a `/goal clear` has actually been transported,
    so a teardown that closes without clearing cannot accidentally look confirmed.
    """

    def __init__(self, runner, *, clear_lands=True):
        self.runner = runner
        self.clear_lands = clear_lands
        self.reads: dict[str, int] = {}

    def _clear_was_sent(self) -> bool:
        return any(c[:3] == ["herdr", "pane", "send-text"] and c[4] == ll._CLEAR_COMMAND
                   for c in self.runner.calls)

    def __call__(self, path):
        n = self.reads.get(path, 0) + 1
        self.reads[path] = n
        if n == 1:
            return ""
        if path == REGISTRY_PATH:
            return REGISTRY_ROW + "\n"
        if path.endswith(f"{PRED_SESSION}.jsonl"):
            rows = [ARMED_ROW]
            if self.clear_lands and self._clear_was_sent():
                rows.append(CLEARED_ROW)
            return "".join(r + "\n" for r in rows)
        return RESUME_PROMPT + "\n" + SUCCESSOR_GOAL_ROW + "\n"


def _handoff(runner, **over):
    kw = dict(
        anchor_pane="w1:p1", cwd=str(REPO_ROOT), project_root=str(REPO_ROOT),
        name="successor", goal_condition=GOAL_CONDITION, resume_prompt=RESUME_PROMPT,
        expected_project=PROJECT, expected_project_path=PROJECT_PATH,
        registry_path=REGISTRY_PATH, transcript_dir="/tmp", prompt_marker=MARKER,
        teardown=False, sleeper=lambda _s: None, runner=runner,
    )
    kw.update(over)
    kw.setdefault("read_text", Artifacts(runner))
    return kw


def _responses():
    return {"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK,
            "herdr pane read": "[Pasted text #1 +9 lines]"}


def _closed(runner, pane):
    return [c for c in runner.calls if c[:3] == ["herdr", "pane", "close"] and c[3] == pane]


def _sent(runner):
    return [c[4] for c in runner.calls if c[:3] == ["herdr", "pane", "send-text"]]


# ---------------------------------------------------------------------------
# defect 1 — one default, and it is "state your intent"
# ---------------------------------------------------------------------------

def test_teardown_has_no_library_default_at_all() -> None:
    """The disagreement is REMOVED, not documented: there is no library default left to disagree
    with the CLI's. A caller that says nothing is a caller that has not decided, and deciding for
    it is how the two surfaces drifted apart."""
    kw = _handoff(Runner(_responses()))
    del kw["teardown"]
    with pytest.raises(TypeError, match="teardown"):
        ll.perform_handoff(**kw)


# ---------------------------------------------------------------------------
# defect 3 — teardown OFF must SAY the guard is still armed
# ---------------------------------------------------------------------------

def test_teardown_off_reports_that_the_predecessor_is_still_guarded() -> None:
    """The observed harm. The pane stayed open, the guard stayed armed, the Stop hook looped four
    times, and nothing in the output had said so."""
    r = Runner(_responses())
    out = ll.perform_handoff(**_handoff(r, teardown=False))
    assert out["ok"] is True
    assert out["predecessor_guard"], "teardown OFF must report the guard state"
    assert "/goal clear" in out["predecessor_guard"]
    assert not _closed(r, "w1:p1")


def test_a_successful_handoff_with_teardown_on_reports_the_guard_cleared() -> None:
    r = Runner(_responses())
    out = ll.perform_handoff(**_handoff(r, teardown=True, predecessor_session=PRED_SESSION,
                                        predecessor_goal_condition=PRED_CONDITION))
    assert out["ok"] is True, out["failed_step"]
    assert "/goal clear" not in (out["predecessor_guard"] or "")


# ---------------------------------------------------------------------------
# defect 2 — clear, CONFIRM, then close
# ---------------------------------------------------------------------------

class TestClearBeforeClose:
    def test_the_guard_is_cleared_before_the_pane_is_closed(self) -> None:
        r = Runner(_responses())
        out = ll.perform_handoff(**_handoff(r, teardown=True, predecessor_session=PRED_SESSION,
                                           predecessor_goal_condition=PRED_CONDITION))
        assert out["ok"] is True, out["failed_step"]
        kinds = [Runner.key(c) for c in r.calls]
        clear_at = next(i for i, c in enumerate(r.calls)
                        if c[:3] == ["herdr", "pane", "send-text"] and c[4] == ll._CLEAR_COMMAND)
        close_at = next(i for i, c in enumerate(r.calls)
                        if c[:3] == ["herdr", "pane", "close"] and c[3] == "w1:p1")
        assert clear_at < close_at, f"closed before clearing: {kinds}"

    def test_the_clear_goes_to_the_predecessor_not_the_successor(self) -> None:
        r = Runner(_responses())
        ll.perform_handoff(**_handoff(r, teardown=True, predecessor_session=PRED_SESSION,
                                      predecessor_goal_condition=PRED_CONDITION))
        clears = [c for c in r.calls
                  if c[:3] == ["herdr", "pane", "send-text"] and c[4] == ll._CLEAR_COMMAND]
        assert clears and all(c[3] == "w1:p1" for c in clears)

    def test_an_unconfirmed_clear_leaves_the_pane_OPEN(self) -> None:
        """Fail-safe direction: a guard that may still be armed keeps its pane, because closing it
        is the irreversible half. The report has to name the ambiguity."""
        r = Runner(_responses())
        out = ll.perform_handoff(**_handoff(
            r, teardown=True, predecessor_session=PRED_SESSION,
            predecessor_goal_condition=PRED_CONDITION,
            read_text=Artifacts(r, clear_lands=False)))
        assert out["ok"] is False
        assert out["failed_step"] == "predecessor_goal_clear"
        assert not _closed(r, "w1:p1"), "an unconfirmed clear must not close the pane"
        assert "/goal clear" in (out["predecessor_guard"] or "")

    def test_a_failed_clear_send_aborts_before_the_close(self) -> None:
        r = Runner(_responses(), fail_on="herdr pane send-keys")
        out = ll.perform_handoff(**_handoff(r, teardown=True, predecessor_session=PRED_SESSION,
                                            predecessor_goal_condition=PRED_CONDITION))
        assert out["ok"] is False
        assert not _closed(r, "w1:p1")

    def test_the_clear_is_never_confirmed_by_a_row_for_another_guard(self) -> None:
        """`transcript_has_cleared_goal`'s own lesson (#665 Step 11 pass-3): with any met:true row
        accepted, a row belonging to a DIFFERENT guard confirms our clear and a live pane gets closed.

        Rewritten for #707. The original scenario put ONLY a foreign met:true row in the transcript,
        which since #707 means "nothing is armed" — so closing is now the correct answer and the test
        was asserting a failure that no longer makes sense. The intent survives with a coherent
        history: a foreign receipt exists AND our own guard is genuinely live and newest, so the
        foreign row must not be allowed to confirm a clear of ours.
        """
        r = Runner(_responses())
        foreign_receipt = json.dumps({"attachment": {"type": "goal_status", "met": True,
                                                     "sentinel": True,
                                                     "condition": "an entirely different guard"}})

        class ForeignReceipt(Artifacts):
            def __call__(self, path):
                if path.endswith(f"{PRED_SESSION}.jsonl"):
                    # Our guard is NEWEST and unmet; the foreign receipt sits behind it.
                    return foreign_receipt + "\n" + ARMED_ROW + "\n"
                return super().__call__(path)

        out = ll.perform_handoff(**_handoff(
            r, teardown=True, predecessor_session=PRED_SESSION,
            predecessor_goal_condition=PRED_CONDITION, read_text=ForeignReceipt(r)))
        assert ll._CLEAR_COMMAND in _sent(r), "our guard was live, so a clear must be sent"
        assert out["ok"] is False and out["failed_step"] == "predecessor_goal_clear"
        assert not _closed(r, "w1:p1")

    def test_the_campaign_path_keeps_close_only_but_records_the_warning(self) -> None:
        """No predecessor session id means the clear cannot be CONFIRMED, and the campaign launcher
        does not know one. Its behaviour is therefore unchanged — close only — and the missing clear
        is recorded rather than silently assumed to be fine."""
        r = Runner(_responses())
        out = ll.perform_handoff(**_handoff(r, teardown=True))
        assert out["ok"] is True, out["failed_step"]
        assert _closed(r, "w1:p1"), "the campaign path must still close the predecessor"
        assert ll._CLEAR_COMMAND not in _sent(r)
        notes = " ".join(str(s.get("note")) for s in out["steps"])
        assert "not cleared" in notes.lower() or "guard" in notes.lower()

    def test_teardown_still_refuses_until_every_verification_passes(self) -> None:
        """The clear must not become a way around the ladder: a failed gate means no clear and no
        close, exactly as before."""
        r = Runner({"herdr pane split": SPLIT_OK,
                    "herdr pane get": json.dumps({"result": {"pane": {}}}),
                    "herdr pane read": "[Pasted text"})
        out = ll.perform_handoff(**_handoff(r, teardown=True, predecessor_session=PRED_SESSION,
                                            predecessor_goal_condition=PRED_CONDITION))
        assert out["ok"] is False and out["failed_step"] == "spawned"
        assert ll._CLEAR_COMMAND not in _sent(r)
        assert not _closed(r, "w1:p1")

# ---------------------------------------------------------------------------
# #707 — three states, not two
# ---------------------------------------------------------------------------


class PredState:
    """Registry, successor transcript, and a PREDECESSOR transcript with a chosen guard history.

    `history` is the predecessor's goal_status rows as they stand BEFORE the teardown runs. That is
    the whole point of #707: the guard was armed (or cleared) earlier in the session, long before the
    pre-send baseline, so the liveness question can only be answered from the WHOLE file.
    """

    def __init__(self, runner, *, history=(), clear_lands=True):
        self.runner = runner
        self.history = list(history)
        self.clear_lands = clear_lands
        self.reads: dict[str, int] = {}

    def _clear_was_sent(self) -> bool:
        return any(c[:3] == ["herdr", "pane", "send-text"] and c[4] == ll._CLEAR_COMMAND
                   for c in self.runner.calls)

    def __call__(self, path):
        n = self.reads.get(path, 0) + 1
        self.reads[path] = n
        if path == REGISTRY_PATH:
            return "" if n == 1 else REGISTRY_ROW + "\n"
        if path.endswith(f"{PRED_SESSION}.jsonl"):
            rows = list(self.history)
            if self.clear_lands and self._clear_was_sent():
                rows.append(CLEARED_ROW)
            return "".join(r + "\n" for r in rows)
        if n == 1:
            return ""
        return RESUME_PROMPT + "\n" + SUCCESSOR_GOAL_ROW + "\n"


def _teardown(runner, **over):
    kw = dict(teardown=True, predecessor_session=PRED_SESSION,
              predecessor_goal_condition=PRED_CONDITION)
    kw.update(over)
    return _handoff(runner, **kw)


class TestThreeGuardStates:
    """The reported defect: state 3 was being reported as state 2.

    Reported live 2026-07-29 from a lumenquire session on 3.106.1 — all four handoff gates passed,
    then teardown refused to close with "guard state is AMBIGUOUS" when the operator had simply
    cleared their own goal by hand earlier. It is the COMMON path, not an edge case: the skill tells
    the operator to run `clear-prep` first and `clear-prep` step 6 clears the guard.
    """

    def test_state_1_armed_and_the_clear_lands_closes_the_pane(self) -> None:
        r = Runner(_responses())
        out = ll.perform_handoff(**_teardown(
            r, read_text=PredState(r, history=[ARMED_ROW])))
        assert out["ok"] is True, out["failed_step"]
        assert ll._CLEAR_COMMAND in _sent(r)
        assert _closed(r, "w1:p1")

    def test_state_2_armed_and_the_clear_does_not_land_leaves_it_OPEN(self) -> None:
        """Unchanged, and must stay so — an unconfirmed clear on a really-armed guard is the one
        case where leaving the pane open is right."""
        r = Runner(_responses())
        out = ll.perform_handoff(**_teardown(
            r, read_text=PredState(r, history=[ARMED_ROW], clear_lands=False)))
        assert out["ok"] is False
        assert out["failed_step"] == "predecessor_goal_clear"
        assert not _closed(r, "w1:p1")

    def test_state_3_nothing_armed_closes_the_pane_without_clearing(self) -> None:
        """The regression test the report asked for. No guard was ever armed, so there is no receipt
        to wait for and nothing to clear."""
        r = Runner(_responses())
        out = ll.perform_handoff(**_teardown(r, read_text=PredState(r, history=[])))
        assert out["ok"] is True, out["failed_step"]
        assert ll._CLEAR_COMMAND not in _sent(r), "nothing was armed — no clear should be sent"
        assert _closed(r, "w1:p1")
        assert out["results"].get("predecessor_goal_clear") == "already_clear"

    def test_state_3_also_covers_a_guard_the_operator_already_cleared(self) -> None:
        """The exact reported precondition: armed earlier, then cleared BY HAND, so the newest row
        is already met:true."""
        r = Runner(_responses())
        out = ll.perform_handoff(**_teardown(
            r, read_text=PredState(r, history=[ARMED_ROW, CLEARED_ROW])))
        assert out["ok"] is True, out["failed_step"]
        assert ll._CLEAR_COMMAND not in _sent(r)
        assert _closed(r, "w1:p1")
        assert out["results"].get("predecessor_goal_clear") == "already_clear"

    def test_an_already_clear_predecessor_is_not_reported_as_a_failure(self) -> None:
        """Exit code and message both mattered in the report: exit 4 with "AMBIGUOUS" reads as
        "something broke" when nothing did."""
        r = Runner(_responses())
        out = ll.perform_handoff(**_teardown(r, read_text=PredState(r, history=[])))
        assert out["failed_step"] is None
        assert "AMBIGUOUS" not in (out["predecessor_guard"] or "")

    def test_a_stale_supplied_condition_does_NOT_read_as_already_clear(self) -> None:
        """The hazard in the fix, and why liveness is keyed on the NEWEST row rather than on the
        caller's condition. Here the caller's condition was cleared, but a DIFFERENT guard was armed
        afterwards and is live. Keying on the supplied condition would conclude "already clear" and
        close a pane that is still guarded — the same trap #665 Step-11 pass-3 documented for the
        confirm side."""
        replaced = json.dumps({"attachment": {"type": "goal_status", "met": False,
                                              "sentinel": True,
                                              "condition": "a replacement guard"}})
        r = Runner(_responses())
        out = ll.perform_handoff(**_teardown(
            r, read_text=PredState(r, history=[ARMED_ROW, CLEARED_ROW, replaced],
                                   clear_lands=False)))
        assert out["results"].get("predecessor_goal_clear") != "already_clear"
        assert not _closed(r, "w1:p1"), "a live replacement guard must not be closed over"

    def test_the_clear_receipt_is_bound_to_the_guard_actually_in_force(self) -> None:
        """When a replacement guard IS the live one, clearing it must be confirmed against ITS
        condition — not the caller's stale one, which no receipt would ever carry."""
        replaced_cond = "a replacement guard"
        replaced = json.dumps({"attachment": {"type": "goal_status", "met": False,
                                              "sentinel": True, "condition": replaced_cond}})
        replaced_receipt = json.dumps({"attachment": {"type": "goal_status", "met": True,
                                                     "sentinel": True,
                                                     "condition": replaced_cond}})

        class ReplacedGuard(PredState):
            def __call__(self, path):
                if path.endswith(f"{PRED_SESSION}.jsonl"):
                    rows = [ARMED_ROW, CLEARED_ROW, replaced]
                    if self._clear_was_sent():
                        rows.append(replaced_receipt)
                    return "".join(r + "\n" for r in rows)
                return super().__call__(path)

        r = Runner(_responses())
        out = ll.perform_handoff(**_teardown(r, read_text=ReplacedGuard(r)))
        assert out["ok"] is True, out["failed_step"]
        assert _closed(r, "w1:p1")

    def test_an_unreadable_predecessor_transcript_stays_conservative(self) -> None:
        """Not evidence of "already clear". The pane keeps its guard and its life."""
        def unreadable(path):
            if path.endswith(f"{PRED_SESSION}.jsonl"):
                raise OSError("nope")
            return PredState(Runner())(path)

        r = Runner(_responses())
        out = ll.perform_handoff(**_teardown(r, read_text=unreadable))
        assert out["ok"] is False
        assert not _closed(r, "w1:p1")


# ---------------------------------------------------------------------------
# #880 AC-D(iii) — the STILL-ARMED wording asserts only what was checked
# ---------------------------------------------------------------------------

SATISFIED_ROW = json.dumps({"attachment": {"type": "goal_status", "met": True,
                                           "reason": "satisfied",
                                           "condition": PRED_CONDITION}})


class TestStillArmedAccuracy:
    def test_no_teardown_with_a_retired_goal_does_not_claim_armed(self) -> None:
        """Predecessor transcript available and its newest state is the
        satisfied evaluation — the guard note must not assert STILL ARMED
        (the #880 false alarm sent the owner to fix a non-problem)."""
        r = Runner(_responses())
        out = ll.perform_handoff(**_handoff(
            r, teardown=False, predecessor_session=PRED_SESSION,
            read_text=PredState(r, history=[ARMED_ROW, SATISFIED_ROW])))
        assert out["ok"] is True
        guard = out["predecessor_guard"]
        assert guard and "STILL ARMED" not in guard
        assert "NO live goal" in guard

    def test_no_teardown_with_an_armed_goal_still_says_so(self) -> None:
        r = Runner(_responses())
        out = ll.perform_handoff(**_handoff(
            r, teardown=False, predecessor_session=PRED_SESSION,
            read_text=PredState(r, history=[ARMED_ROW])))
        assert out["ok"] is True
        assert "STILL ARMED" in out["predecessor_guard"]

    def test_no_teardown_without_transcript_uses_conditional_wording(self) -> None:
        """No predecessor_session (the CLI --no-teardown shape): the message
        must not ASSERT armedness it never checked."""
        r = Runner(_responses())
        out = ll.perform_handoff(**_handoff(r, teardown=False))
        guard = out["predecessor_guard"]
        assert guard and "MAY still" in guard
        assert "is STILL ARMED" not in guard
        assert "/goal clear" in guard

    def test_torn_tail_after_retirement_is_indeterminate_not_no_goal(self) -> None:
        """#880 Step 8a wave finding (adopted): the armedness advisory must
        read STRICTLY — leniently, a torn re-arm line after a retirement is
        skipped and the branch prints a definitive 'NO live goal' for a pane
        whose state is actually indeterminate. Strict raises -> conditional
        wording."""
        torn = '{"attachment": {"type": "goal_status", "met": fal'
        r = Runner(_responses())
        out = ll.perform_handoff(**_handoff(
            r, teardown=False, predecessor_session=PRED_SESSION,
            read_text=PredState(r, history=[ARMED_ROW, SATISFIED_ROW, torn])))
        guard = out["predecessor_guard"]
        assert guard and "NO live goal" not in guard
        assert "MAY still" in guard
