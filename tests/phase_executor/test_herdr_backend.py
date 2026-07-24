"""#638 (epic #635 C2) — HerdrBackend: mocked-herdr-runner tests (no real herdr binary is
ever invoked — confirmed absent from CI, `.github/workflows/ci.yml`).
"""
import json
import subprocess

import pytest

from phase_executor.herdr_backend import HerdrBackend, _PaneGetError, _PaneListError


def _herdr_run(cmd, *, env=None, timeout=30):  # pragma: no cover - overridden per test
    raise AssertionError(f"unexpected herdr call: {cmd}")


def _json_ok(cmd, payload):
    return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")


def _json_err(cmd, code="pane_not_found", message="not found"):
    return subprocess.CompletedProcess(
        cmd, 1, "", json.dumps({"error": {"code": code, "message": message}}))


def _split_ok(pane_id="w1:p9", workspace_id="w1"):
    """A faithful `pane split` success body. Real herdr ALWAYS includes `workspace_id` in
    `result.pane` (confirmed live against the pinned binary), and since the Step-11 pass-4
    workspace-mismatch fix `new_session`/`preflight` verify it against the endpoint -- so a
    fixture omitting it is not a realistic response."""
    return _json_ok(None, {"result": {"pane": {"pane_id": pane_id,
                                               "workspace_id": workspace_id}}})


def _capturing_run(calls, responses):
    """`responses` is a list of CompletedProcess to return in call order."""
    def run(cmd, *, env=None, timeout=30):
        calls.append(list(cmd))
        return responses[len(calls) - 1]
    return run


# ---- new_session (3-call, non-atomic) --------------------------------------

def test_new_session_split_rename_run_in_order():
    calls = []
    responses = [
        _split_ok(),  # split
        _json_ok(None, {"result": {"pane": {"pane_id": "w1:p9", "label": "sess1"}}}),  # rename
        subprocess.CompletedProcess(None, 0, "", ""),  # run
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.new_session("w1", "sess1", "/wt/path", ["python3", "-m", "phase_executor.pane_runner", "spec.json"])
    assert res.returncode == 0
    # Step-11 finding: `pane split` has no --workspace option (confirmed live against the
    # pinned real binary: "unknown option: --workspace") -- --current + --direction is the
    # correct, live-confirmed targeting.
    assert calls[0] == ["herdr", "pane", "split", "--current", "--direction", "right", "--cwd", "/wt/path"]
    assert calls[1] == ["herdr", "pane", "rename", "w1:p9", "sess1"]
    assert calls[2] == ["herdr", "pane", "run", "w1:p9", "exec",
                       "python3", "-m", "phase_executor.pane_runner", "spec.json"]


def test_new_session_threads_pane_env_into_split():
    calls = []
    responses = [
        _split_ok(),  # split
        _json_ok(None, {"result": {"pane": {"pane_id": "w1:p9", "label": "sess1"}}}),  # rename
        subprocess.CompletedProcess(None, 0, "", ""),  # run
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1",
                      pane_env={"PYTHONPATH": "/repo/src"})
    be.new_session("w1", "sess1", "/wt", ["argv"])
    assert calls[0] == ["herdr", "pane", "split", "--current", "--direction", "right",
                       "--cwd", "/wt", "--env", "PYTHONPATH=/repo/src"]


def test_new_session_split_success_with_malformed_body_is_a_failure():
    # Step-11 finding: a successful split (rc=0) with an unparseable/empty body must not
    # falsely satisfy new_session's success contract by returning rc=0 anyway.
    calls = []
    responses = [subprocess.CompletedProcess(None, 0, "not json", "")]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.new_session("w1", "sess1", "/wt", ["argv"])
    assert res.returncode != 0


def test_new_session_exception_from_rename_still_closes_orphan():
    # Step-11 finding: an EXCEPTION (e.g. a runner timeout) from rename/run must not bypass
    # orphan cleanup the way a bare `if returncode != 0` check would.
    calls = []

    def run(cmd, *, env=None, timeout=30):
        calls.append(list(cmd))
        if cmd[2] == "split":
            return _json_ok(cmd, {"result": {"pane": {"pane_id": "w1:p9", "workspace_id": "w1"}}})
        if cmd[2] == "rename":
            raise TimeoutError("stub: rename timed out")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    be = HerdrBackend(run=run, workspace_id="w1")
    res = be.new_session("w1", "sess1", "/wt", ["argv"])
    assert res.returncode != 0
    assert calls[-1] == ["herdr", "pane", "close", "w1:p9"]


def test_new_session_success_never_closes_the_pane():
    # The bug caught during self-review before it shipped: a bare `finally: close(...)`
    # would immediately kill the just-launched process on the SUCCESS path too.
    calls = []
    responses = [
        _split_ok(),  # split
        _json_ok(None, {"result": {"pane": {"pane_id": "w1:p9", "label": "sess1"}}}),  # rename
        subprocess.CompletedProcess(None, 0, "", ""),  # run succeeds
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.new_session("w1", "sess1", "/wt", ["argv"])
    assert res.returncode == 0
    assert not any(c[2] == "close" for c in calls if len(c) > 2)


def test_new_session_split_failure_returns_failure_no_further_calls():
    calls = []
    responses = [_json_err(None, message="split failed")]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.new_session("w1", "sess1", "/wt", ["argv"])
    assert res.returncode != 0
    assert len(calls) == 1  # never attempted rename/run after a split failure


def test_new_session_rename_failure_closes_orphan_pane():
    calls = []
    responses = [
        _split_ok(),  # split
        _json_err(None, message="rename failed"),  # rename fails
        subprocess.CompletedProcess(None, 0, "", ""),  # close (cleanup)
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.new_session("w1", "sess1", "/wt", ["argv"])
    assert res.returncode != 0
    assert calls[2] == ["herdr", "pane", "close", "w1:p9"]


def test_new_session_run_failure_closes_orphan_pane():
    calls = []
    responses = [
        _split_ok(),  # split
        _json_ok(None, {"result": {"pane": {"pane_id": "w1:p9", "label": "sess1"}}}),  # rename
        subprocess.CompletedProcess(None, 1, "", "exec failed"),  # run fails
        subprocess.CompletedProcess(None, 0, "", ""),  # close (cleanup)
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.new_session("w1", "sess1", "/wt", ["argv"])
    assert res.returncode != 0
    assert calls[3] == ["herdr", "pane", "close", "w1:p9"]


# ---- name resolution (list + filter by label) ------------------------------

def _list_response(panes):
    return _json_ok(None, {"result": {"panes": panes}})


def test_pane_pid_resolves_name_then_process_info():
    calls = []
    responses = [
        _list_response([{"pane_id": "w1:p9", "label": "sess1"}]),  # list (resolve)
        _json_ok(None, {"result": {"process_info": {"shell_pid": 4242}}}),  # process-info
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.pane_pid("w1", "sess1")
    assert res.returncode == 0
    assert res.stdout.strip() == "4242"
    assert calls[0] == ["herdr", "pane", "list", "--workspace", "w1"]
    assert calls[1] == ["herdr", "pane", "process-info", "--pane", "w1:p9"]


def test_pane_pid_unresolvable_name_returns_failure_no_crash():
    calls = []
    responses = [_list_response([])]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.pane_pid("w1", "ghost")
    assert res.returncode != 0
    assert res.stdout == ""


def test_resolve_pane_id_duplicate_label_raises():
    calls = []
    responses = [_list_response([
        {"pane_id": "w1:p1", "label": "dup"}, {"pane_id": "w1:p2", "label": "dup"}])]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    with pytest.raises(RuntimeError, match="duplicate label"):
        be.has_session("w1", "dup")


def test_has_session_resolves_then_get():
    calls = []
    responses = [
        _list_response([{"pane_id": "w1:p9", "label": "sess1"}]),
        _split_ok(),
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.has_session("w1", "sess1")
    assert res.returncode == 0
    assert calls[1] == ["herdr", "pane", "get", "w1:p9"]


def test_has_session_unresolvable_name_returns_dead_shape():
    calls = []
    responses = [_list_response([])]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.has_session("w1", "ghost")
    assert res.returncode != 0


def test_has_session_get_confirmed_not_found_stays_nonzero_not_raised():
    # Step-11 finding (round 2, confirming pass): `list` resolves a real pane_id, but the
    # pane closes itself between resolve and get (a genuine race) -- error.code
    # "pane_not_found" is a CONFIRMED dead signal, must stay a plain nonzero return.
    calls = []
    responses = [
        _list_response([{"pane_id": "w1:p9", "label": "sess1"}]),
        _json_err(None, code="pane_not_found", message="pane gone"),
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.has_session("w1", "sess1")
    assert res.returncode != 0


def test_has_session_get_operational_failure_raises():
    # Step-11 finding (round 2, confirming pass): `list` resolves a real pane_id, but the
    # FOLLOW-UP `get` fails for a reason OTHER than "gone" (daemon hiccup, internal error)
    # -- genuinely indeterminate, must raise, not be forwarded as an ordinary nonzero
    # result that _live() would read as "definitively dead".
    calls = []
    responses = [
        _list_response([{"pane_id": "w1:p9", "label": "sess1"}]),
        _json_err(None, code="internal_error", message="daemon busy"),
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    with pytest.raises(_PaneGetError):
        be.has_session("w1", "sess1")


def test_has_session_get_malformed_error_body_raises():
    calls = []
    responses = [
        _list_response([{"pane_id": "w1:p9", "label": "sess1"}]),
        subprocess.CompletedProcess(None, 1, "", "not json at all"),
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    with pytest.raises(_PaneGetError):
        be.has_session("w1", "sess1")


def test_kill_session_resolves_then_close():
    calls = []
    responses = [
        _list_response([{"pane_id": "w1:p9", "label": "sess1"}]),
        subprocess.CompletedProcess(None, 0, "", ""),
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.kill_session("w1", "sess1")
    assert res.returncode == 0
    assert calls[1] == ["herdr", "pane", "close", "w1:p9"]


def test_kill_session_unresolvable_name_is_idempotent_success():
    # matches the existing kill-session swallow-and-continue contract (#633's confirmed
    # idempotent pane_not_found on a never-existed pane)
    calls = []
    responses = [_list_response([])]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.kill_session("w1", "ghost")
    assert res.returncode == 0


# ---- Step-11 finding: a FAILED/malformed `pane list` is NOT the same as "not found" -----

def _failed_list():
    return subprocess.CompletedProcess(None, 1, "", "daemon unreachable")


def test_kill_session_list_failure_is_not_idempotent_success():
    # THE finding: a transient list failure must never be reported as a clean close --
    # unlike a genuinely-empty successful list, we have no idea whether the pane exists.
    calls = []
    responses = [_failed_list()]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.kill_session("w1", "ghost")
    assert res.returncode != 0


def test_has_session_list_failure_raises_not_confused_with_not_found():
    # Step-11 finding (round 2): a list-command failure must PROPAGATE, not collapse to an
    # ordinary nonzero CompletedProcess -- _live() treats every nonzero result as
    # "definitively dead", so a swallowed failure here would let a transient herdr list
    # error kill a healthy job. Genuinely-not-found (empty list) stays a plain nonzero
    # return (test_has_session_unresolvable_name_returns_dead_shape, above).
    calls = []
    responses = [_failed_list()]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    with pytest.raises(_PaneListError):
        be.has_session("w1", "ghost")


def test_list_sessions_list_command_failure_raises():
    calls = []
    responses = [_failed_list()]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    with pytest.raises(_PaneListError):
        be.list_sessions("w1")


def test_list_sessions_malformed_shape_despite_rc0_raises():
    # Step-11 finding (round 2): rc=0 with a valid-JSON-but-wrong-shape body (missing
    # "panes" entirely) must not silently read as "zero sessions" -- that is a false
    # success that fed reap()'s liveness union an empty-but-wrong live-names set.
    calls = []
    responses = [_json_ok(None, {"result": {}})]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    with pytest.raises(_PaneListError):
        be.list_sessions("w1")


def test_resolve_pane_id_malformed_shape_despite_rc0_raises():
    calls = []
    responses = [_json_ok(None, {"result": {}})]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    with pytest.raises(_PaneListError):
        be.has_session("w1", "ghost")


def test_list_panes_malformed_entry_not_a_dict_raises():
    # Step-11 finding (round 2, confirming pass): a malformed ENTRY (not just a malformed
    # top-level shape) must not silently read as "not one of ours" the way an unlabeled
    # entry legitimately does -- every real pane always carries a pane_id.
    calls = []
    responses = [_list_response([{"pane_id": "w1:p9", "label": "sess1"}, 42])]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    with pytest.raises(_PaneListError):
        be.has_session("w1", "sess1")


def test_list_panes_malformed_entry_missing_pane_id_raises():
    calls = []
    responses = [_list_response([{"label": "sess1"}])]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    with pytest.raises(_PaneListError):
        be.has_session("w1", "sess1")


def test_list_sessions_malformed_entry_raises():
    calls = []
    responses = [_list_response([{"pane_id": "w1:p9", "label": "sess1"}, {"no_pane_id": True}])]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    with pytest.raises(_PaneListError):
        be.list_sessions("w1")


def test_pane_pid_list_failure_does_not_crash():
    calls = []
    responses = [_failed_list()]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.pane_pid("w1", "ghost")
    assert res.returncode != 0


# ---- list_sessions ----------------------------------------------------------

def test_list_sessions_returns_labeled_panes_only():
    calls = []
    responses = [_list_response([
        {"pane_id": "w1:p1", "label": "sess1"},
        {"pane_id": "w1:p2", "label": "sess2"},
        {"pane_id": "w1:p3"},  # no label -- a human's own terminal tab, never surfaced
    ])]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.list_sessions("w1")
    assert res.returncode == 0
    assert set(res.stdout.splitlines()) == {"sess1", "sess2"}


# ---- resolve_endpoint / teardown_endpoint -----------------------------------

def test_resolve_endpoint_returns_configured_workspace_id():
    be = HerdrBackend(run=_herdr_run, workspace_id="w1")
    assert be.resolve_endpoint("run-abc") == "w1"


def test_resolve_endpoint_raises_when_unconfigured():
    be = HerdrBackend(run=_herdr_run, workspace_id=None)
    with pytest.raises(RuntimeError, match="no workspace id configured"):
        be.resolve_endpoint("run-abc")


def test_teardown_endpoint_is_a_documented_noop():
    be = HerdrBackend(run=_herdr_run, workspace_id="w1")
    res = be.teardown_endpoint("w1")
    assert res.returncode == 0


# ---- preflight ---------------------------------------------------------------

def _preflight_run(*, calls=None, close_result=None):
    """Faithful preflight runner: echoes the rename label back through `pane list` so the
    Step-11 pass-4 probe-visibility assertion sees the probe pane (real herdr does exactly
    this). A bare `{}` catch-all is NOT a realistic list body -- the strict `_list_panes`
    shape check rejects it, which is the point of that check."""
    state = {"label": None}

    def run(cmd, *, env=None, timeout=30):
        if calls is not None:
            calls.append(list(cmd))
        if cmd[:2] == ["herdr", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "herdr 0.7.5\n", "")
        verb = cmd[2]
        if verb == "split":
            return _json_ok(cmd, {"result": {"pane": {"pane_id": "w1:pprobe",
                                                      "workspace_id": "w1"}}})
        if verb == "rename":
            state["label"] = cmd[4]
            return _json_ok(cmd, {"result": {"pane": {"pane_id": "w1:pprobe"}}})
        if verb == "list":
            return _json_ok(cmd, {"result": {"panes": [{"pane_id": "w1:pprobe",
                                                        "label": state["label"]}]}})
        if verb == "close":
            return (close_result(cmd) if callable(close_result)
                    else subprocess.CompletedProcess(cmd, 0, "", ""))
        return subprocess.CompletedProcess(cmd, 0, "{}", "")
    return run


def test_preflight_with_injected_run_ignores_missing_real_binary(monkeypatch):
    # CI regression: preflight must mirror TmuxBackend's identity check -- a mocked run=
    # (as EVERY test in this file uses) must never be blocked by shutil.which finding no
    # real herdr binary. This is the exact bug that passed locally on a dev machine with a
    # real herdr installed and failed in CI (no herdr binary at all).
    monkeypatch.setattr("shutil.which", lambda _: None)
    be = HerdrBackend(run=_preflight_run(), workspace_id="w1")
    result = be.preflight("w1")
    assert result.supported is True, result.reason


def test_preflight_bare_construction_checks_real_binary(monkeypatch):
    # The identity check's OTHER half: a HerdrBackend() with NO run= override (defaults to
    # supervisor._default_run itself) DOES check the real host PATH -- mirrors TmuxBackend's
    # own test_bare_construction_default_run_identity_matches_supervisor.
    import phase_executor.supervisor as _sup
    be = HerdrBackend(workspace_id="w1")
    assert be._run is _sup._default_run  # pylint: disable=protected-access
    monkeypatch.setattr("shutil.which", lambda _: None)
    result = be.preflight("w1")
    assert result.supported is False
    assert "herdr binary not found" in result.reason


def test_preflight_all_verbs_pass():
    calls = []
    be = HerdrBackend(run=_preflight_run(calls=calls), workspace_id="w1")
    result = be.preflight("w1")
    assert result.supported is True, result.reason
    verbs = [c[2] for c in calls if c[0] == "herdr" and c[1] == "pane"]
    assert verbs == ["split", "rename", "run", "get", "process-info", "list", "close"]


def test_preflight_stops_on_first_verb_failure():
    def run(cmd, *, env=None, timeout=30):
        if cmd[:2] == ["herdr", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "herdr 0.7.5\n", "")
        if cmd[2] == "split":
            return _json_ok(cmd, {"result": {"pane": {"pane_id": "w1:pprobe", "workspace_id": "w1"}}})
        if cmd[2] == "rename":
            return _json_err(cmd, message="rename failed")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    be = HerdrBackend(run=run, workspace_id="w1")
    result = be.preflight("w1")
    assert result.supported is False
    assert "rename" in result.reason


def test_preflight_runner_raises_returns_unsupported():
    def raising_run(cmd, *, env=None, timeout=30):
        raise OSError("stub: herdr daemon unreachable mid-probe")
    be = HerdrBackend(run=raising_run, workspace_id="w1")
    result = be.preflight("w1")
    assert result.supported is False
    assert "preflight error" in result.reason
    assert "unreachable mid-probe" in result.reason


def test_preflight_version_floor_enforced():
    def run(cmd, *, env=None, timeout=30):
        if cmd[:2] == ["herdr", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "herdr 0.6.0\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    be = HerdrBackend(run=run, workspace_id="w1")
    result = be.preflight("w1")
    assert result.supported is False
    assert "version" in result.reason.lower()


def test_preflight_tolerates_pane_not_found_on_probe_cleanup():
    # #633's own confirmed finding: the exec'd probe process auto-closes its pane on exit --
    # a pane_not_found on the FINAL close is an already-clean outcome, not a failure.
    be = HerdrBackend(
        run=_preflight_run(close_result=lambda cmd: _json_err(
            cmd, code="pane_not_found", message="already gone")),
        workspace_id="w1")
    result = be.preflight("w1")
    assert result.supported is True


def test_preflight_probe_cleanup_genuine_failure_is_reported():
    def run(cmd, *, env=None, timeout=30):
        if cmd[:2] == ["herdr", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "herdr 0.7.5\n", "")
        if cmd[2] == "split":
            return _json_ok(cmd, {"result": {"pane": {"pane_id": "w1:pprobe", "workspace_id": "w1"}}})
        if cmd[2] == "close":
            return _json_err(cmd, code="internal_error", message="daemon crashed")
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    be = HerdrBackend(run=run, workspace_id="w1")
    result = be.preflight("w1")
    assert result.supported is False
    assert "close" in result.reason


def test_env_threaded_through_to_every_herdr_call():
    seen_envs = []

    def run(cmd, *, env=None, timeout=30):
        seen_envs.append(env)
        return _list_response([])

    be = HerdrBackend(run=run, env={"X": "1"}, workspace_id="w1")
    be.has_session("w1", "ghost")
    assert seen_envs == [{"X": "1"}]


def test_env_none_default_passthrough():
    seen_envs = []

    def run(cmd, *, env=None, timeout=30):
        seen_envs.append(env)
        return _list_response([])

    be = HerdrBackend(run=run, workspace_id="w1")  # no env= -> defaults to None
    be.has_session("w1", "ghost")
    assert seen_envs == [None]


# ---- Step-11 pass 4: workspace mismatch must never orphan a live pane -------

def test_new_session_refuses_when_split_lands_in_a_different_workspace():
    # THE finding (High): `--current` splits into the CALLING pane's workspace, which is not
    # necessarily `endpoint`. On a mismatch the pane is created in A while pane_pid /
    # has_session / kill_session all search B -- so launch cleanup's kill_session against B
    # finds B empty, reports IDEMPOTENT SUCCESS, and the process in A stays alive,
    # unregistered, with its permit released. Must fail closed instead.
    calls = []
    responses = [
        _split_ok(pane_id="wOTHER:p3", workspace_id="wOTHER"),  # split landed elsewhere
        subprocess.CompletedProcess(None, 0, "", ""),           # close (cleanup)
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.new_session("w1", "sess1", "/wt", ["argv"])
    assert res.returncode != 0
    assert "wOTHER" in res.stderr and "w1" in res.stderr
    # never ran the payload into a pane it cannot later address...
    assert not any(len(c) > 2 and c[2] == "run" for c in calls)
    # ...and cleaned up the pane it did create (it HAS the id on this path)
    assert calls[1] == ["herdr", "pane", "close", "wOTHER:p3"]


def test_new_session_refuses_when_split_omits_workspace_id():
    # A response with no workspace_id cannot be proven to be in `endpoint` -- fail closed
    # rather than assume membership (real herdr always sends it, confirmed live).
    calls = []
    responses = [
        _json_ok(None, {"result": {"pane": {"pane_id": "w1:p9"}}}),  # no workspace_id
        subprocess.CompletedProcess(None, 0, "", ""),                # close (cleanup)
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.new_session("w1", "sess1", "/wt", ["argv"])
    assert res.returncode != 0
    assert not any(len(c) > 2 and c[2] == "run" for c in calls)


def test_preflight_fails_on_workspace_mismatch():
    calls = []

    def run(cmd, *, env=None, timeout=30):
        calls.append(list(cmd))
        if cmd[1] == "--version":
            return subprocess.CompletedProcess(cmd, 0, "herdr 0.7.5", "")
        if cmd[2] == "split":
            return _json_ok(cmd, {"result": {"pane": {"pane_id": "wOTHER:p3",
                                                      "workspace_id": "wOTHER"}}})
        return subprocess.CompletedProcess(cmd, 0, "", "")

    be = HerdrBackend(run=run, workspace_id="w1")
    pf = be.preflight("w1")
    assert pf.supported is False
    assert "wOTHER" in pf.reason


def test_preflight_fails_when_probe_pane_absent_from_the_listing():
    # Step-11 pass 4: preflight previously checked only `rc == 0` on the list call, so an
    # empty SUCCESSFUL listing of the wrong workspace passed. The probe pane must actually
    # be visible under `endpoint` -- that is the round-trip every _resolve_pane_id depends on.
    calls = []

    def run(cmd, *, env=None, timeout=30):
        calls.append(list(cmd))
        if cmd[1] == "--version":
            return subprocess.CompletedProcess(cmd, 0, "herdr 0.7.5", "")
        if cmd[2] == "split":
            return _json_ok(cmd, {"result": {"pane": {"pane_id": "w1:pprobe",
                                                      "workspace_id": "w1"}}})
        if cmd[2] == "list":
            return _json_ok(cmd, {"result": {"panes": []}})  # rc=0 but probe not there
        return subprocess.CompletedProcess(cmd, 0, "", "")

    be = HerdrBackend(run=run, workspace_id="w1")
    pf = be.preflight("w1")
    assert pf.supported is False
    assert "does not show the probe pane" in pf.reason


def test_preflight_passes_when_probe_pane_is_listed():
    calls = []
    state = {"label": None}

    def run(cmd, *, env=None, timeout=30):
        calls.append(list(cmd))
        if cmd[1] == "--version":
            return subprocess.CompletedProcess(cmd, 0, "herdr 0.7.5", "")
        if cmd[2] == "split":
            return _json_ok(cmd, {"result": {"pane": {"pane_id": "w1:pprobe",
                                                      "workspace_id": "w1"}}})
        if cmd[2] == "rename":
            state["label"] = cmd[4]
            return _json_ok(cmd, {"result": {"pane": {"pane_id": "w1:pprobe"}}})
        if cmd[2] == "list":
            return _json_ok(cmd, {"result": {"panes": [{"pane_id": "w1:pprobe",
                                                        "label": state["label"]}]}})
        return subprocess.CompletedProcess(cmd, 0, "", "")

    be = HerdrBackend(run=run, workspace_id="w1")
    pf = be.preflight("w1")
    assert pf.supported is True, pf.reason


# ---- Step-11 pass 4: a present-but-malformed `label` must raise -------------

def test_list_panes_present_but_non_string_label_raises():
    # THE finding (High): validating only pane_id let {"pane_id": "w1:p9", "label": []}
    # through; the malformed label compares unequal, so _resolve_pane_id returns a
    # DEFINITIVE "not found" for a job that is actually alive -> healthy record quarantined
    # and killed. A falsey malformed label is likewise dropped by list_sessions, letting
    # reap() route the live process into kill_tree.
    for bad in ([], {}, 42, True):
        calls = []
        responses = [_list_response([{"pane_id": "w1:p9", "label": bad}])]
        be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
        with pytest.raises(_PaneListError):
            be.has_session("w1", "sess1")


def test_list_panes_absent_or_none_label_stays_legitimate():
    # An unmanaged pane (a human's own terminal tab) legitimately has no label -- that must
    # keep reading as "not one of ours", never as corruption.
    for panes in ([{"pane_id": "w1:p9"}], [{"pane_id": "w1:p9", "label": None}]):
        calls = []
        responses = [_list_response(panes)]
        be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
        res = be.has_session("w1", "sess1")
        assert res.returncode != 0  # confirmed not-found, and did NOT raise


# ---- Step-11 pass 5: a present-but-BLANK label is corruption, not "unmanaged" ----

def test_list_panes_present_but_blank_label_raises():
    # THE finding (High): `""` / `"   "` / newline-only passed the isinstance check and then
    # vanished exactly like a non-string -- unequal in _resolve_pane_id (definitive not-found
    # for a LIVE job), falsey so list_sessions drops it, and reap() strips whitespace and
    # drops it too. Only an ABSENT/None label denotes an unmanaged pane.
    for blank in ("", "   ", "\n", "\t "):
        calls = []
        responses = [_list_response([{"pane_id": "w1:p9", "label": blank}])]
        be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
        with pytest.raises(_PaneListError):
            be.has_session("w1", "sess1")


def test_list_sessions_blank_label_raises_rather_than_dropping():
    calls = []
    responses = [_list_response([{"pane_id": "w1:p9", "label": "   "}])]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    with pytest.raises(_PaneListError):
        be.list_sessions("w1")


def test_new_session_records_unconfirmed_cleanup():
    # Step-11 pass 5: orphan cleanup stays best-effort, but an UNCONFIRMED close must not be
    # silently swallowed -- it means a possibly-live payload.
    calls = []
    responses = [
        _split_ok(),                                              # split ok
        _json_err(None, code="internal_error", message="boom"),   # rename fails
        _json_err(None, code="internal_error", message="close failed"),  # cleanup close fails
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.new_session("w1", "sess1", "/wt", ["argv"])
    assert res.returncode != 0
    assert be._last_cleanup_confirmed is False   # noqa: SLF001


def test_new_session_pane_not_found_on_cleanup_counts_as_confirmed():
    # #633: the exec'd process auto-closes its own pane on exit, so pane_not_found on the
    # cleanup close is an already-clean outcome, not an unconfirmed teardown.
    calls = []
    responses = [
        _split_ok(),
        _json_err(None, code="internal_error", message="boom"),   # rename fails
        _json_err(None, code="pane_not_found", message="already gone"),  # close: already gone
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.new_session("w1", "sess1", "/wt", ["argv"])
    assert res.returncode != 0
    assert be._last_cleanup_confirmed is True    # noqa: SLF001
