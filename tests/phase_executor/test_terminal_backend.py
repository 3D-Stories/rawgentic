"""#636 (epic #635 C1) — TerminalBackend protocol + TmuxBackend: byte-identical argv proof.

Each test asserts TmuxBackend's method constructs the EXACT tmux argv the pre-extraction
inline `self._tmux(sock, "verb", ...)` call in supervisor.py used (see the docstring's
file:line citations) — the mechanical-extraction contract, not a redesign.
"""
import subprocess

import pytest

from phase_executor.terminal_backend import TmuxBackend


def _capturing_run(calls):
    def run(cmd, *, env=None, cwd=None, timeout=30):
        calls.append({"cmd": list(cmd), "env": env, "cwd": cwd, "timeout": timeout})
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")
    return run


def test_new_session_matches_supervisor_py_524():
    # supervisor.py:524 — self._tmux(sock, "new-session", "-d", "-s", name, "-c", handle.path, "--", *argv)
    calls = []
    be = TmuxBackend(run=_capturing_run(calls), env={"X": "1"})
    be.new_session("SOCK", "sess1", "/wt/path", ["python3", "-m", "phase_executor.pane_runner", "spec.json"])
    assert calls[0]["cmd"] == ["tmux", "-S", "SOCK", "new-session", "-d", "-s", "sess1",
                              "-c", "/wt/path", "--", "python3", "-m", "phase_executor.pane_runner", "spec.json"]
    assert calls[0]["env"] == {"X": "1"}


def test_pane_pid_matches_supervisor_py_528():
    # supervisor.py:528 — self._tmux(sock, "display-message", "-p", "-t", name, "#{pane_pid}")
    calls = []
    be = TmuxBackend(run=_capturing_run(calls))
    be.pane_pid("SOCK", "sess1")
    assert calls[0]["cmd"] == ["tmux", "-S", "SOCK", "display-message", "-p", "-t", "sess1", "#{pane_pid}"]


def test_has_session_matches_supervisor_py_567():
    # supervisor.py:567 — self._tmux(record.run_socket, "has-session", "-t", record.session_name)
    calls = []
    be = TmuxBackend(run=_capturing_run(calls))
    be.has_session("SOCK", "sess1")
    assert calls[0]["cmd"] == ["tmux", "-S", "SOCK", "has-session", "-t", "sess1"]


def test_list_sessions_matches_supervisor_py_1286():
    # supervisor.py:1286 — self._tmux(records[0].run_socket, "list-sessions", "-F", "#{session_name}")
    calls = []
    be = TmuxBackend(run=_capturing_run(calls))
    be.list_sessions("SOCK")
    assert calls[0]["cmd"] == ["tmux", "-S", "SOCK", "list-sessions", "-F", "#{session_name}"]


def test_kill_session_matches_supervisor_py_558_750_1313():
    # supervisor.py:558/750/1313 — self._tmux(sock, "kill-session", "-t", name)
    calls = []
    be = TmuxBackend(run=_capturing_run(calls))
    be.kill_session("SOCK", "sess1")
    assert calls[0]["cmd"] == ["tmux", "-S", "SOCK", "kill-session", "-t", "sess1"]


def test_teardown_endpoint_matches_supervisor_py_1002():
    # supervisor.py:1002 — self._tmux(self.resolve_socket(run_id), "kill-server")
    calls = []
    be = TmuxBackend(run=_capturing_run(calls))
    be.teardown_endpoint("SOCK")
    assert calls[0]["cmd"] == ["tmux", "-S", "SOCK", "kill-server"]


def test_resolve_endpoint_delegates_to_module_resolve_socket(monkeypatch):
    # Self-contained addressing (Step-4 finding 2): TmuxBackend owns runtime_dir/state_dir/
    # tmpdir, threading them into the module resolve_socket() itself — never reaching into
    # a supervisor's private attributes.
    import phase_executor.supervisor as _sup
    seen = {}
    def fake_resolve_socket(run_id, *, runtime_dir=None, state_dir=None, tmpdir=None):
        seen.update(run_id=run_id, runtime_dir=runtime_dir, state_dir=state_dir, tmpdir=tmpdir)
        return "/fake/rg-x.sock"
    monkeypatch.setattr(_sup, "resolve_socket", fake_resolve_socket)
    be = TmuxBackend(runtime_dir="/rt", state_dir="/st", tmpdir="/tmpd")
    endpoint = be.resolve_endpoint("run-abc")
    assert endpoint == "/fake/rg-x.sock"
    assert seen == {"run_id": "run-abc", "runtime_dir": "/rt", "state_dir": "/st", "tmpdir": "/tmpd"}


def test_preflight_all_verbs_pass(tmp_path):
    calls = []
    def run(cmd, *, env=None, timeout=30):
        calls.append(list(cmd))
        if cmd[:2] == ["tmux", "-V"]:
            return subprocess.CompletedProcess(cmd, 0, "tmux 3.4\n", "")
        return subprocess.CompletedProcess(cmd, 0, "1234\n", "")
    be = TmuxBackend(run=run)
    sock = str(tmp_path / "run" / "rg-x.sock")
    result = be.preflight(sock)
    assert result.supported is True
    verbs = [c[3] for c in calls if len(c) > 3 and c[0] == "tmux" and c[1] == "-S"]
    assert verbs == ["new-session", "has-session", "display-message", "list-sessions", "kill-session"]


def test_preflight_stops_on_first_verb_failure(tmp_path):
    def run(cmd, *, env=None, timeout=30):
        if cmd[:2] == ["tmux", "-V"]:
            return subprocess.CompletedProcess(cmd, 0, "tmux 3.4\n", "")
        if len(cmd) > 3 and cmd[3] == "has-session":
            return subprocess.CompletedProcess(cmd, 1, "", "no session")
        return subprocess.CompletedProcess(cmd, 0, "1234\n", "")
    be = TmuxBackend(run=run)
    result = be.preflight(str(tmp_path / "run" / "rg-x.sock"))
    assert result.supported is False
    assert "has-session" in result.reason


def test_preflight_version_floor_enforced():
    def run(cmd, *, env=None, timeout=30):
        if cmd[:2] == ["tmux", "-V"]:
            return subprocess.CompletedProcess(cmd, 0, "tmux 2.9\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    be = TmuxBackend(run=run)
    result = be.preflight("/tmp/does/not/matter.sock")
    assert result.supported is False
    assert "version" in result.reason.lower()


def test_bare_construction_default_run_identity_matches_supervisor(monkeypatch, tmp_path):
    # A TmuxBackend() with no run= override must default to supervisor._default_run
    # ITSELF (not a locally-duplicated copy) so preflight's `self._run is _default_run`
    # tmux-binary-presence short-circuit fires correctly regardless of construction path.
    import phase_executor.supervisor as _sup
    from phase_executor.terminal_backend import TmuxBackend
    be = TmuxBackend()
    assert be._run is _sup._default_run  # pylint: disable=protected-access
    monkeypatch.setattr("shutil.which", lambda _: None)
    result = be.preflight(str(tmp_path / "run" / "rg-x.sock"))
    assert result.supported is False
    assert "tmux binary not found" in result.reason


def test_env_none_default_matches_run_subprocess_env_none():
    # A TmuxBackend constructed with no env= must pass env=None through (byte-identical to
    # the pre-extraction _tmux, which always passed self._env — never omitted the kwarg).
    calls = []
    be = TmuxBackend(run=_capturing_run(calls))
    be.has_session("SOCK", "s")
    assert calls[0]["env"] is None
