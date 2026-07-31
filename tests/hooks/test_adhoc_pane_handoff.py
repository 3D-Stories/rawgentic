"""#700 — the ad-hoc pane handoff: a thin `perform_handoff` adapter, and the unsubmitted-Enter fix.

Two things are under test here and they have different owners:

1. **`ad-hoc-handoff`**, a CLI subcommand that reaches `perform_handoff` with no driver-state file
   and no `--launcher-armed`. It adds no sequencing of its own, so what has to be pinned is that
   every argument arrives intact, that teardown defaults OFF (AC4), and that none of the #694
   preconditions were relaxed to make the ad-hoc case convenient (AC5).

2. **The nudge recovery**, live-found while this issue was being written by hand: `project_switched`
   proves the bind's row LANDED, not that its TURN ENDED, so the resume prompt's Enter can be eaten
   by the still-running bind turn and the prompt sits in the input box unsubmitted. The recovery is
   a bare `send-keys Enter` — never a re-paste (double submission) and never a truncation (silent
   corruption), which is #696's rule.

The nudge is the dangerous half, so most of these tests are about when it must NOT fire. An Enter
sent into unknown UI state can accept a dialog nobody authorised, and a bounded count is not a
bound on privilege — so every unknown resolves to "do not nudge", which is byte-identical to the
behaviour before this change.
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
# The armed condition is what the row carries, so build the fixture from the module's own helper
# rather than retyping it — a capped goal arms truncated text (see `armed_condition`).
GOAL_ROW = json.dumps({"attachment": {"type": "goal_status", "met": False, "sentinel": True,
                                      "condition": ll.armed_condition(GOAL_CONDITION)[0]}})
# What a pane looks like with a collapsed paste sitting in the input box, and what it looks like
# sitting on a permission dialog. Both strings are Claude Code's own affordances (runbook §7.1.2).
PANE_PASTE_WAITING = "> [Pasted text #1 +9 lines]\n  paste again to expand\n"
PANE_PERMISSION_DIALOG = ("Do you want to proceed?\n"
                          "> 1. Yes\n  2. Yes, and don't ask again for python3 commands\n"
                          "  3. No, and tell Claude what to do differently (esc)\n")


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
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv), \
            "runner must only ever receive a list[str] argv"
        self.calls.append(list(argv))
        k = self.key(argv)
        if self.fail_on and k.startswith(self.fail_on):
            return FakeProc(returncode=1)
        if k == "herdr pane list" and k not in self.responses:
            return FakeProc(0, PANE_LIST_ANCHOR_ONLY)
        return FakeProc(0, self.responses.get(k, ""))

    def nudges(self) -> list[list[str]]:
        """Every bare `send-keys Enter` that was NOT paired with a preceding send-text.

        Counted positionally: the sequence sends text then Enter for the bind, the prompt and the
        goal, so an Enter whose immediately preceding call is not a `send-text` is a nudge.
        """
        out = []
        for i, c in enumerate(self.calls):
            if c[:3] != ["herdr", "pane", "send-keys"]:
                continue
            prev = self.calls[i - 1] if i else []
            if prev[:3] != ["herdr", "pane", "send-text"]:
                out.append(c)
        return out

    def sent_text(self) -> list[str]:
        return [c[4] for c in self.calls if c[:3] == ["herdr", "pane", "send-text"]]


class Artifacts:
    """The registry and the successor transcript as they behave over a launch.

    The FIRST read of each path is `perform_handoff` capturing its pre-launch baseline, and the
    evidence that authorises anything must land after it — so the first read returns nothing, as it
    really does for a successor whose transcript does not exist until it is spawned. Seeding a row
    before the baseline must never satisfy a check (#611 Step-11 Medium 4).

    `marker_after_nudges` is the point of this fake: the marker appears only once the runner has
    issued that many bare Enters, which is exactly the live failure — the paste is intact and
    unsubmitted until something submits it.
    """

    def __init__(self, runner, *, marker_after_nudges=0, goal_row=GOAL_ROW):
        self.runner = runner
        self.marker_after_nudges = marker_after_nudges
        self.goal_row = goal_row
        self.reads: dict[str, int] = {}

    def __call__(self, path):
        n = self.reads.get(path, 0) + 1
        self.reads[path] = n
        if n == 1:
            return ""
        if path == REGISTRY_PATH:
            return REGISTRY_ROW + "\n"
        text = ""
        if len(self.runner.nudges()) >= self.marker_after_nudges:
            text += RESUME_PROMPT + "\n"
        return text + self.goal_row + "\n"


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


def _responses(pane_read=PANE_PASTE_WAITING):
    return {"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK,
            "herdr pane read": pane_read}


# ---------------------------------------------------------------------------
# the nudge recovery
# ---------------------------------------------------------------------------

class TestPromptNudge:
    def test_a_prompt_that_lands_first_time_is_never_nudged(self) -> None:
        """The happy path must be byte-identical to before this change."""
        r = Runner(_responses())
        out = ll.perform_handoff(**_handoff(r, read_text=Artifacts(r, marker_after_nudges=0)))
        assert out["ok"] is True, out["failed_step"]
        assert r.nudges() == [], "a landed prompt must not be nudged"
        assert not any(Runner.key(c) == "herdr pane read" for c in r.calls), \
            "no pane read either — the recovery path must not run at all"

    def test_one_nudge_recovers_the_live_failure(self) -> None:
        """The measured case: the bind's turn ate the prompt's Enter, and a bare Enter submitted
        the intact buffer."""
        r = Runner(_responses())
        out = ll.perform_handoff(**_handoff(r, read_text=Artifacts(r, marker_after_nudges=1)))
        assert out["ok"] is True, out["failed_step"]
        assert out["results"]["prompt_landed"] is True
        assert len(r.nudges()) == 1
        assert r.nudges()[0] == ["herdr", "pane", "send-keys", "w1:pZZ", "Enter"]

    def test_the_nudge_never_re_sends_the_prompt_text(self) -> None:
        """#696's rule: a re-paste risks double submission and a truncation silently corrupts."""
        r = Runner(_responses())
        ll.perform_handoff(**_handoff(r, read_text=Artifacts(r, marker_after_nudges=2)))
        assert r.sent_text().count(RESUME_PROMPT) == 1, "the prompt text was sent more than once"

    def test_nudging_is_bounded_and_then_fails_closed(self) -> None:
        r = Runner(_responses())
        out = ll.perform_handoff(**_handoff(r, read_text=Artifacts(r, marker_after_nudges=99)))
        assert out["ok"] is False and out["failed_step"] == "prompt_landed"
        assert len(r.nudges()) <= ll.PROMPT_NUDGE_ROUNDS
        assert not any(c[:3] == ["herdr", "pane", "close"] and c[3] == "w1:p1"
                       for c in r.calls), "the predecessor must survive a failed handoff"

    def test_each_nudge_is_recorded_as_its_own_step(self) -> None:
        """AC3: every gate and every recovery attempt is verifiable from the returned record."""
        r = Runner(_responses())
        out = ll.perform_handoff(**_handoff(r, read_text=Artifacts(r, marker_after_nudges=1)))
        assert [s for s in out["steps"] if s["kind"] == "send_resume_nudge"]

    def test_no_marker_means_no_nudge(self) -> None:
        """Without a marker there is no signal to decide on, so there is nothing to recover from."""
        r = Runner(_responses())
        out = ll.perform_handoff(**_handoff(r, prompt_marker=None,
                                            read_text=Artifacts(r, marker_after_nudges=99)))
        assert out["ok"] is True
        assert r.nudges() == []

    def test_the_goal_is_still_sent_only_after_the_prompt_lands(self) -> None:
        """The send ORDER is untouched: recovery lives inside send 2, it does not reorder sends."""
        r = Runner(_responses())
        ll.perform_handoff(**_handoff(r, read_text=Artifacts(r, marker_after_nudges=1)))
        texts = r.sent_text()
        assert len(texts) == 3
        assert texts[0].startswith(ll._driver_lib().BIND_DIRECTIVE)
        assert texts[1] == RESUME_PROMPT
        assert texts[2].startswith("/goal")

    def test_a_failed_nudge_send_is_its_own_failure_not_poll_exhaustion(self) -> None:
        """Review finding: a broken `send-keys` must not be reported as `prompt_landed` timing out.
        Only the NUDGE may fail here, so the bind and prompt sends still succeed."""
        class NudgeFails(Runner):
            """Only a BARE Enter fails — the paired sends still succeed, so the run really does
            reach the recovery instead of dying earlier for an unrelated reason."""

            def __call__(self, argv, timeout=180):
                proc = super().__call__(argv, timeout)
                if argv[:3] == ["herdr", "pane", "send-keys"] \
                        and self.calls[-2][:3] != ["herdr", "pane", "send-text"]:
                    return FakeProc(returncode=1)
                return proc

        r = NudgeFails(_responses())
        out = ll.perform_handoff(**_handoff(r, read_text=Artifacts(r, marker_after_nudges=99)))
        assert out["ok"] is False
        assert out["failed_step"] == "send_resume_nudge", out["failed_step"]


class TestNudgeSafety:
    """An Enter accepts whatever is on screen. Every unknown must resolve to "do not nudge"."""

    @pytest.mark.parametrize("pane_read,why", [
        (PANE_PERMISSION_DIALOG, "a permission dialog would be accepted"),
        ("", "an empty read proves nothing"),
        ("some unrelated scrollback\n", "no paste affordance means no known buffer"),
    ])
    def test_an_unsafe_or_unknown_pane_is_not_nudged(self, pane_read, why) -> None:
        r = Runner(_responses(pane_read=pane_read))
        out = ll.perform_handoff(**_handoff(r, read_text=Artifacts(r, marker_after_nudges=99)))
        assert out["ok"] is False and out["failed_step"] == "prompt_landed"
        assert r.nudges() == [], why

    def test_a_failed_pane_read_is_not_a_licence_to_nudge(self) -> None:
        r = Runner(_responses(), fail_on="herdr pane read")
        out = ll.perform_handoff(**_handoff(r, read_text=Artifacts(r, marker_after_nudges=99)))
        assert out["ok"] is False and r.nudges() == []

    def test_the_read_is_recorded_so_the_refusal_is_explainable(self) -> None:
        r = Runner(_responses(pane_read=PANE_PERMISSION_DIALOG))
        out = ll.perform_handoff(**_handoff(r, read_text=Artifacts(r, marker_after_nudges=99)))
        notes = " ".join(str(s.get("note")) for s in out["steps"])
        assert "nudge" in notes.lower()


class TestPaneStateReader:
    """The pure predicate behind the safety check."""

    def test_a_collapsed_paste_is_recognised(self) -> None:
        ok, _ = ll.pane_shows_unsubmitted_paste(PANE_PASTE_WAITING)
        assert ok is True

    def test_either_affordance_alone_is_enough(self) -> None:
        assert ll.pane_shows_unsubmitted_paste("x [Pasted text #2 +40 lines]")[0] is True
        assert ll.pane_shows_unsubmitted_paste("y paste again to expand")[0] is True

    def test_a_permission_dialog_wins_over_a_paste_affordance(self) -> None:
        """The dialog can be on screen while a paste marker sits in the scrollback above it, so
        the dialog signature must veto rather than merely be absent."""
        ok, reason = ll.pane_shows_unsubmitted_paste(
            PANE_PASTE_WAITING + PANE_PERMISSION_DIALOG)
        assert ok is False
        assert "dialog" in reason.lower()

    @pytest.mark.parametrize("text", ["", None, "unrelated output"])
    def test_anything_unrecognised_is_not_safe(self, text) -> None:
        assert ll.pane_shows_unsubmitted_paste(text)[0] is False


# ---------------------------------------------------------------------------
# the CLI subcommand (black-box, per docs/testing.md)
# ---------------------------------------------------------------------------

def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True,
                          text=True, check=False)


def _argv(tmp_path, **over) -> list[str]:
    prompt = tmp_path / "resume.md"
    prompt.write_text(RESUME_PROMPT, encoding="utf-8")
    flags = {
        "--anchor-pane": "w1:p1", "--name": "successor", "--project": PROJECT,
        "--project-path": PROJECT_PATH, "--cwd": str(REPO_ROOT),
        "--project-root": str(REPO_ROOT), "--registry": REGISTRY_PATH,
        "--transcript-dir": str(tmp_path), "--resume-prompt-file": str(prompt),
        "--goal-condition": GOAL_CONDITION, "--prompt-marker": MARKER,
        # Retirement is ON by default since the owner's 2026-07-29 decision, and it runs a live
        # `herdr pane get` ownership probe before anything else. These cases are about argument
        # plumbing and refusals, so they opt out; the retirement path has its own class below.
        "--no-teardown": None,
    }
    flags.update(over)
    argv = ["ad-hoc-handoff"]
    for key, value in flags.items():
        if value is None:
            argv.append(key)
        else:
            argv += [key, value]
    return argv


class TestAdHocSubcommand:
    def test_the_subcommand_exists_and_needs_no_campaign(self, tmp_path) -> None:
        """AC1: the ad-hoc case has no driver-state file and no armed launcher to assert."""
        proc = _cli("ad-hoc-handoff", "--help")
        assert proc.returncode == 0, proc.stderr
        assert "--anchor-pane" in proc.stdout and "--prompt-marker" in proc.stdout
        assert "--driver-state" not in proc.stdout
        assert "--launcher-armed" not in proc.stdout

    def test_every_argument_reaches_perform_handoff(self, tmp_path, monkeypatch) -> None:
        seen = {}

        def fake(**kw):
            seen.update(kw)
            return {"ok": True, "results": {}, "steps": [], "new_pane": "w1:pZZ",
                    "session_id": "s", "cleanup": None, "truncated": False,
                    "failed_step": None, "teardown_skipped": None, "predecessor_guard": None}

        monkeypatch.setattr(ll, "perform_handoff", fake)
        assert ll.main(_argv(tmp_path)) == 0
        assert seen["anchor_pane"] == "w1:p1"
        assert seen["expected_project"] == PROJECT
        assert seen["expected_project_path"] == PROJECT_PATH
        assert seen["resume_prompt"] == RESUME_PROMPT
        assert seen["goal_condition"] == GOAL_CONDITION
        assert seen["prompt_marker"] == MARKER
        assert seen.get("steps") is None, "the canonical launch ladder must be used"

    def test_teardown_defaults_ON(self, tmp_path, monkeypatch) -> None:
        """Owner decision 2026-07-29, reversing #700 AC4: the phrasings that trigger this skill
        mean RETIRE THIS ONE, and an OFF default left a live pane re-prompting itself from an armed
        goal on the first real handoff. Retirement stays gated on every verification AND on the goal
        being provably cleared, so the expected path is also the guarded one."""
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "my-own-session")
        monkeypatch.setattr(ll, "_default_runner", lambda argv, timeout=180: FakeProc(0, json.dumps(
            {"result": {"pane": {"pane_id": "w1:p1", "agent_session": {
                "agent": "claude", "kind": "id", "source": "herdr:claude",
                "value": "my-own-session"}}}})))
        seen = {}
        monkeypatch.setattr(ll, "perform_handoff",
                            lambda **kw: (seen.update(kw), {"ok": True, "results": {},
                                                            "steps": [], "new_pane": None,
                                                            "session_id": None, "cleanup": None,
                                                            "truncated": False,
                                                            "failed_step": None,
                                                            "teardown_skipped": None, "predecessor_guard": None})[1])
        argv = [a for a in _argv(tmp_path) if a != "--no-teardown"]
        ll.main(argv)
        assert seen["teardown"] is True
        assert seen["predecessor_session"] == "my-own-session"

    # The opt-IN half of AC4 lives in `TestTeardownOwnership`: since the Step-11 diff review,
    # `--teardown-predecessor` also has to prove the anchor pane is this session's own, so the
    # assertion that `teardown=True` reaches `perform_handoff` cannot be made without that setup —
    # see `test_teardown_proceeds_when_the_pane_is_provably_ours`.

    def test_an_inline_prompt_and_a_file_prompt_agree(self, tmp_path, monkeypatch) -> None:
        seen = []
        monkeypatch.setattr(ll, "perform_handoff",
                            lambda **kw: (seen.append(kw), {"ok": True, "results": {},
                                                            "steps": [], "new_pane": None,
                                                            "session_id": None, "cleanup": None,
                                                            "truncated": False,
                                                            "failed_step": None,
                                                            "teardown_skipped": None, "predecessor_guard": None})[1])
        ll.main(_argv(tmp_path))
        argv = [a for a in _argv(tmp_path) if a != "--resume-prompt-file"]
        argv = [a for a in argv if not a.endswith("resume.md")]
        ll.main(argv + ["--resume-prompt", RESUME_PROMPT])
        assert seen[0]["resume_prompt"] == seen[1]["resume_prompt"] == RESUME_PROMPT

    def test_a_goal_condition_can_come_from_a_file(self, tmp_path, monkeypatch) -> None:
        seen = {}
        monkeypatch.setattr(ll, "perform_handoff",
                            lambda **kw: (seen.update(kw), {"ok": True, "results": {},
                                                            "steps": [], "new_pane": None,
                                                            "session_id": None, "cleanup": None,
                                                            "truncated": False,
                                                            "failed_step": None,
                                                            "teardown_skipped": None, "predecessor_guard": None})[1])
        cond = tmp_path / "goal.txt"
        cond.write_text("multi\nline\ncondition", encoding="utf-8")
        argv = [a for a in _argv(tmp_path) if a not in ("--goal-condition", GOAL_CONDITION)]
        ll.main(argv + ["--goal-condition-file", str(cond)])
        assert seen["goal_condition"] == "multi\nline\ncondition"

    def test_a_resume_prompt_carrying_its_own_bind_is_refused(self, tmp_path) -> None:
        """AC5: #694's precondition must not be relaxed to make the ad-hoc case convenient."""
        bad = tmp_path / "bad.md"
        bad.write_text(f"{MARKER} /rawgentic:switch rawgentic then do the work",
                       encoding="utf-8")
        proc = _cli(*_argv(tmp_path, **{"--resume-prompt-file": str(bad)}))
        assert proc.returncode == 2, proc.stdout
        assert "694" in proc.stderr or "SEND 1" in proc.stderr

    def test_a_marker_absent_from_the_prompt_is_refused(self, tmp_path) -> None:
        proc = _cli(*_argv(tmp_path, **{"--prompt-marker": "[not-in-there]"}))
        assert proc.returncode == 2, proc.stdout
        assert "prompt_landed" in proc.stderr

    def test_a_multiline_marker_is_refused(self, tmp_path) -> None:
        """A literal newline is stored ESCAPED in the JSONL transcript, so a marker carrying one
        can never match the substring scan — `prompt_landed` would fail closed after a pane, a
        session and an armed guard already existed."""
        prompt = tmp_path / "p.md"
        prompt.write_text("first line\nsecond line of the handoff", encoding="utf-8")
        proc = _cli(*_argv(tmp_path, **{"--resume-prompt-file": str(prompt),
                                        "--prompt-marker": "first line\nsecond"}))
        assert proc.returncode == 2, proc.stdout
        assert "control character" in proc.stderr

    def test_a_marker_too_short_to_be_distinctive_is_refused(self, tmp_path) -> None:
        """The contract is membership, not uniqueness (review High 1): `the` is in the prompt and
        would also match unrelated transcript content, passing the gate before the prompt landed."""
        prompt = tmp_path / "p.md"
        prompt.write_text("the handoff prompt", encoding="utf-8")
        proc = _cli(*_argv(tmp_path, **{"--resume-prompt-file": str(prompt),
                                        "--prompt-marker": "the"}))
        assert proc.returncode == 2, proc.stdout
        assert str(ll.PROMPT_MARKER_MIN_LEN) in proc.stderr

    def test_project_path_is_required(self, tmp_path) -> None:
        """It binds `project_switched` to the registry's own path as well as the name, and the
        skill derives it from the registry anyway — so optional bought nothing (review finding)."""
        argv = [a for a in _argv(tmp_path) if a not in ("--project-path", PROJECT_PATH)]
        proc = _cli(*argv)
        assert proc.returncode == 2
        assert "--project-path" in proc.stderr

    def test_an_empty_prompt_file_is_refused(self, tmp_path) -> None:
        empty = tmp_path / "empty.md"
        empty.write_text("   \n", encoding="utf-8")
        proc = _cli(*_argv(tmp_path, **{"--resume-prompt-file": str(empty)}))
        assert proc.returncode == 2, proc.stdout

    def test_a_missing_prompt_file_names_the_path(self, tmp_path) -> None:
        proc = _cli(*_argv(tmp_path, **{"--resume-prompt-file": str(tmp_path / "nope.md")}))
        assert proc.returncode == 2
        assert "nope.md" in proc.stderr

    def test_the_two_prompt_forms_are_mutually_exclusive(self, tmp_path) -> None:
        proc = _cli(*_argv(tmp_path), "--resume-prompt", RESUME_PROMPT)
        assert proc.returncode == 2
        assert "not allowed with" in proc.stderr

    def test_a_marker_containing_whitespace_is_refused(self, tmp_path) -> None:
        """The length floor alone does not make a marker distinctive — the #700 Step-11 diff review
        named that gap. A whitespace-free token is far less likely to occur in the bind turn's own
        transcript output than a phrase is, and it matches the documented `[handoff-700]` shape."""
        prompt = tmp_path / "p.md"
        prompt.write_text("the handoff prompt for this run", encoding="utf-8")
        proc = _cli(*_argv(tmp_path, **{"--resume-prompt-file": str(prompt),
                                        "--prompt-marker": "the handoff"}))
        assert proc.returncode == 2, proc.stdout
        assert "whitespace" in proc.stderr

    def test_a_failed_handoff_exits_nonzero_with_the_step_named(self, tmp_path,
                                                                monkeypatch) -> None:
        monkeypatch.setattr(ll, "perform_handoff",
                            lambda **kw: {"ok": False, "results": {}, "steps": [],
                                          "new_pane": None, "session_id": None,
                                          "cleanup": None, "truncated": False,
                                          "failed_step": "prompt_landed",
                                          "teardown_skipped": None, "predecessor_guard": None})
        assert ll.main(_argv(tmp_path)) == 4


class TestTeardownOwnership:
    """#700 Step-11 diff review, Medium: nothing bound the anchor pane to the CALLING session, and
    `--teardown-predecessor` closes that pane. A stale or mistyped `$HERDR_PANE_ID` would therefore
    split from, and then close, a stranger's pane.

    `retire_predecessor` already holds the pattern this mirrors (`hooks/launcher_lib.py:2108`): a
    destructive target must PROVE it hosts the session claiming authority over it. The check is
    scoped to the destructive request — without teardown a wrong anchor is merely a pane split in
    the wrong place, and demanding a live herdr probe for the harmless case would refuse every
    environment that has no herdr at all.
    """

    OTHER = json.dumps({"result": {"pane": {
        "pane_id": "w1:p1", "agent_status": "idle",
        "agent_session": {"agent": "claude", "kind": "id", "source": "herdr:claude",
                          "value": "someone-elses-session"}}}})

    def _own(self, session):
        return json.dumps({"result": {"pane": {
            "pane_id": "w1:p1", "agent_status": "idle",
            "agent_session": {"agent": "claude", "kind": "id", "source": "herdr:claude",
                              "value": session}}}})

    def test_teardown_refuses_a_pane_that_hosts_another_session(self, tmp_path,
                                                               monkeypatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "my-own-session")
        monkeypatch.setattr(ll, "_default_runner",
                            lambda argv, timeout=180: FakeProc(0, self.OTHER))
        called = []
        monkeypatch.setattr(ll, "perform_handoff", lambda **kw: called.append(kw))
        assert ll.main([a for a in _argv(tmp_path) if a != "--no-teardown"]) == 2
        assert called == [], "nothing may be launched once the teardown target is unproven"

    def test_teardown_proceeds_when_the_pane_is_provably_ours(self, tmp_path,
                                                             monkeypatch) -> None:
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "my-own-session")
        monkeypatch.setattr(ll, "_default_runner",
                            lambda argv, timeout=180: FakeProc(0, self._own("my-own-session")))
        seen = {}
        monkeypatch.setattr(ll, "perform_handoff",
                            lambda **kw: (seen.update(kw), {"ok": True, "results": {},
                                                            "steps": [], "new_pane": None,
                                                            "session_id": None, "cleanup": None,
                                                            "truncated": False,
                                                            "failed_step": None,
                                                            "teardown_skipped": None, "predecessor_guard": None})[1])
        assert ll.main([a for a in _argv(tmp_path) if a != "--no-teardown"]) == 0
        assert seen["teardown"] is True

    def test_teardown_refuses_when_the_probe_cannot_prove_anything(self, tmp_path,
                                                                  monkeypatch) -> None:
        """Fail CLOSED: an unreadable pane is not evidence of ownership, and what it gates is
        irreversible."""
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "my-own-session")
        monkeypatch.setattr(ll, "_default_runner", lambda argv, timeout=180: FakeProc(1, ""))
        assert ll.main([a for a in _argv(tmp_path) if a != "--no-teardown"]) == 2

    def test_teardown_refuses_a_session_that_cannot_prove_its_own_identity(self, tmp_path,
                                                                          monkeypatch) -> None:
        """Mirrors `_own_session_id(require_env=True)`: no environment at all is not a state a real
        Claude session is in, so it cannot be allowed to authorise a close."""
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
        monkeypatch.setattr(ll, "_default_runner",
                            lambda argv, timeout=180: FakeProc(0, self._own("whatever")))
        assert ll.main([a for a in _argv(tmp_path) if a != "--no-teardown"]) == 2

    def test_no_teardown_means_no_ownership_probe_at_all(self, tmp_path, monkeypatch) -> None:
        """`--no-teardown` is the additive path: it closes nothing, so it must not demand a live
        `herdr pane get` ownership proof. (The DEFAULT path does retire, and therefore does — that
        is the trade the owner's 2026-07-29 decision accepts.)"""
        calls = []

        def runner(argv, timeout=180):
            calls.append(argv)
            return FakeProc(0, "")

        monkeypatch.setattr(ll, "_default_runner", runner)
        monkeypatch.setattr(ll, "perform_handoff",
                            lambda **kw: {"ok": True, "results": {}, "steps": [],
                                          "new_pane": None, "session_id": None, "cleanup": None,
                                          "truncated": False, "failed_step": None,
                                          "teardown_skipped": None, "predecessor_guard": None})
        assert ll.main(_argv(tmp_path)) == 0
        assert calls == []


# ---------------------------------------------------------------------------
# #758 — verbatim goal carry: the pure helpers
# ---------------------------------------------------------------------------

def _goal_row(met, cond, sentinel=True):
    return json.dumps({"attachment": {"type": "goal_status", "met": met,
                                      "sentinel": sentinel, "condition": cond}})


class TestLiveOwnerGoal:
    """#758 — sentinel-only trust, liveness from the LAST sentinel row.

    `_find_goal_status` is deliberately recursive, so a forged `type: goal_status` object
    embedded in tool output can reach any reader that does not key on the sentinel. Both
    forgery directions must be dead: a sentinel-less unmet row cannot inject a phantom
    goal, and a sentinel-less met:true row carrying the GENUINE condition cannot spoof
    "already cleared" (the pass-2 bypass through `goal_currently_unmet`).
    """

    def test_the_last_sentinel_unmet_row_is_the_live_goal_verbatim(self) -> None:
        text = "\n".join([_goal_row(False, "goal A"), _goal_row(False, "goal B")])
        assert ll.live_owner_goal(text) == "goal B"

    def test_a_later_sentinel_clear_means_no_live_goal(self) -> None:
        text = "\n".join([_goal_row(False, "goal A"), _goal_row(True, "goal A")])
        assert ll.live_owner_goal(text) is None

    def test_a_forged_sentinelless_clear_cannot_spoof_already_cleared(self) -> None:
        """p2-F1 regression: genuine sentinel unmet A, then a forged sentinel-less met:true
        carrying the SAME condition — the live goal must still be A."""
        text = "\n".join([_goal_row(False, "goal A"),
                          _goal_row(True, "goal A", sentinel=False)])
        assert ll.live_owner_goal(text) == "goal A"

    def test_a_forged_sentinelless_unmet_row_cannot_inject_a_phantom_goal(self) -> None:
        text = _goal_row(False, "attacker goal", sentinel=False)
        assert ll.live_owner_goal(text) is None

    def test_a_forged_row_nested_in_tool_output_is_ignored(self) -> None:
        nested = json.dumps({"tool_result": {"content": {
            "type": "goal_status", "met": True, "condition": "goal A"}}})
        text = "\n".join([_goal_row(False, "goal A"), nested])
        assert ll.live_owner_goal(text) == "goal A"

    def test_no_rows_means_no_live_goal(self) -> None:
        assert ll.live_owner_goal("") is None
        assert ll.live_owner_goal("not json at all\n{\"other\": 1}") is None

    def test_a_sentinel_row_with_a_blank_condition_is_not_a_goal(self) -> None:
        text = _goal_row(False, "   ")
        assert ll.live_owner_goal(text) is None


class TestValidateGoalCarry:
    """#758 — byte-exact carry on ARMED forms, one documented newline normalization."""

    def test_identical_goals_pass(self) -> None:
        ok, reason = ll.validate_goal_carry("goal A", "goal A")
        assert ok, reason

    def test_a_single_trailing_file_newline_is_normalized(self) -> None:
        ok, reason = ll.validate_goal_carry("goal A\n", "goal A")
        assert ok, reason

    def test_differing_content_is_refused(self) -> None:
        ok, reason = ll.validate_goal_carry("goal A plus model STATE text", "goal A")
        assert not ok

    def test_the_refusal_reason_carries_no_goal_content(self) -> None:
        """Pass-1 F8: lengths + numeric first-divergence offset only."""
        secret_goal = "SECRETWORD the owner goal"
        ok, reason = ll.validate_goal_carry("totally different", secret_goal)
        assert not ok
        assert "SECRETWORD" not in reason
        assert "totally different" not in reason
        import re as _re
        assert _re.search(r"offset \d+", reason), reason

    def test_stripped_whitespace_alone_is_not_a_pass(self) -> None:
        """strip() equality was pass-1 F4's finding — exactness is the contract now."""
        ok, _ = ll.validate_goal_carry("  goal A  ", "goal A")
        assert not ok

    def test_an_approved_rewrite_passes_and_records_the_answer(self) -> None:
        ok, reason = ll.validate_goal_carry("new goal text", "goal A",
                                            approved_answer="yes — switch to the new goal")
        assert ok
        assert "yes — switch to the new goal" in reason

    def test_an_empty_approval_is_no_approval(self) -> None:
        ok, _ = ll.validate_goal_carry("new goal text", "goal A", approved_answer="   ")
        assert not ok

    def test_no_live_predecessor_goal_passes(self) -> None:
        ok, reason = ll.validate_goal_carry("anything", None)
        assert ok

    def test_over_cap_goals_compare_in_their_armed_forms(self) -> None:
        """A >4000-char goal arms truncated; identical input on both sides must pass."""
        big = "x" * 5000
        ok, reason = ll.validate_goal_carry(big, big)
        assert ok, reason
