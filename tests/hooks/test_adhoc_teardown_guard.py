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
            if self.clear_lands and self._clear_was_sent():
                return CLEARED_ROW + "\n"
            return ""
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
        accepted, a row belonging to a DIFFERENT guard confirms our clear and a live pane gets
        closed. Supplying the condition binds the receipt to the guard we cleared."""
        r = Runner(_responses())
        other = json.dumps({"attachment": {"type": "goal_status", "met": True, "sentinel": True,
                                           "condition": "an entirely different guard"}})

        class OtherGuard(Artifacts):
            def __call__(self, path):
                text = super().__call__(path)
                if path.endswith(f"{PRED_SESSION}.jsonl") and text:
                    return other + "\n"
                return text

        out = ll.perform_handoff(**_handoff(
            r, teardown=True, predecessor_session=PRED_SESSION,
            predecessor_goal_condition=PRED_CONDITION, read_text=OtherGuard(r)))
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
