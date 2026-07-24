"""#638 (epic #635 C2) — HerdrBackend: mocked-herdr-runner tests (no real herdr binary is
ever invoked — confirmed absent from CI, `.github/workflows/ci.yml`).
"""
import json
import subprocess

import pytest

from phase_executor.herdr_backend import HerdrBackend


def _herdr_run(cmd, *, env=None, timeout=30):  # pragma: no cover - overridden per test
    raise AssertionError(f"unexpected herdr call: {cmd}")


def _json_ok(cmd, payload):
    return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")


def _json_err(cmd, code="pane_not_found", message="not found"):
    return subprocess.CompletedProcess(
        cmd, 1, "", json.dumps({"error": {"code": code, "message": message}}))


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
        _json_ok(None, {"result": {"pane": {"pane_id": "w1:p9"}}}),  # split
        _json_ok(None, {"result": {"pane": {"pane_id": "w1:p9", "label": "sess1"}}}),  # rename
        subprocess.CompletedProcess(None, 0, "", ""),  # run
    ]
    be = HerdrBackend(run=_capturing_run(calls, responses), workspace_id="w1")
    res = be.new_session("w1", "sess1", "/wt/path", ["python3", "-m", "phase_executor.pane_runner", "spec.json"])
    assert res.returncode == 0
    assert calls[0] == ["herdr", "pane", "split", "--workspace", "w1", "--cwd", "/wt/path"]
    assert calls[1] == ["herdr", "pane", "rename", "w1:p9", "sess1"]
    assert calls[2] == ["herdr", "pane", "run", "w1:p9", "exec",
                       "python3", "-m", "phase_executor.pane_runner", "spec.json"]


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
        _json_ok(None, {"result": {"pane": {"pane_id": "w1:p9"}}}),  # split
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
        _json_ok(None, {"result": {"pane": {"pane_id": "w1:p9"}}}),  # split
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
        _json_ok(None, {"result": {"pane": {"pane_id": "w1:p9"}}}),
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

def test_preflight_all_verbs_pass():
    calls = []

    def run(cmd, *, env=None, timeout=30):
        calls.append(list(cmd))
        if cmd[:2] == ["herdr", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "herdr 0.7.5\n", "")
        if cmd[2] == "split":
            return _json_ok(cmd, {"result": {"pane": {"pane_id": "w1:pprobe"}}})
        if cmd[2] == "close":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    be = HerdrBackend(run=run, workspace_id="w1")
    result = be.preflight("w1")
    assert result.supported is True
    verbs = [c[2] for c in calls if c[0] == "herdr" and c[1] == "pane"]
    assert verbs == ["split", "rename", "run", "get", "process-info", "list", "close"]


def test_preflight_stops_on_first_verb_failure():
    def run(cmd, *, env=None, timeout=30):
        if cmd[:2] == ["herdr", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "herdr 0.7.5\n", "")
        if cmd[2] == "split":
            return _json_ok(cmd, {"result": {"pane": {"pane_id": "w1:pprobe"}}})
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
    def run(cmd, *, env=None, timeout=30):
        if cmd[:2] == ["herdr", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "herdr 0.7.5\n", "")
        if cmd[2] == "split":
            return _json_ok(cmd, {"result": {"pane": {"pane_id": "w1:pprobe"}}})
        if cmd[2] == "close":
            return _json_err(cmd, code="pane_not_found", message="already gone")
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    be = HerdrBackend(run=run, workspace_id="w1")
    result = be.preflight("w1")
    assert result.supported is True


def test_preflight_probe_cleanup_genuine_failure_is_reported():
    def run(cmd, *, env=None, timeout=30):
        if cmd[:2] == ["herdr", "--version"]:
            return subprocess.CompletedProcess(cmd, 0, "herdr 0.7.5\n", "")
        if cmd[2] == "split":
            return _json_ok(cmd, {"result": {"pane": {"pane_id": "w1:pprobe"}}})
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
