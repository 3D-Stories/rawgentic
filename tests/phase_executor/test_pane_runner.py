"""#467 W4 Task 2 — pane_runner: the in-pane emitter.

Covers: the /proc descendant walk (CF-17's load-bearing kill primitive), spec parsing +
LaunchProfile reconstruction (incl. the init=False effective_grants), the direct-adapter
call with a supervisor-fixed attempt_id/capture_root, the best-effort provider-pgid
sidecar, the SIGTERM handler killing the whole provider tree (graceful teardown), and
the resume-launch surface (AdapterRequest.resume_session_id: claude composes --resume,
codex/zhipuai refuse fail-loud).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from phase_executor import contract, pane_runner
from phase_executor.adapters import claude_cli
from phase_executor.adapters.base import AdapterRequest
from phase_executor.capture import sanitize_component

REPO = Path(__file__).resolve().parents[2]
PKG_SRC = REPO / "phase_executor" / "src"


# ---------------------------------------------------------------------------
# _descendants
# ---------------------------------------------------------------------------


def test_descendants_finds_grandchild():
    # child spawns a grandchild in its OWN session (mimics start_new_session providers)
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess,sys,time;"
         "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True);"
         "time.sleep(30)"])
    try:
        deadline = time.time() + 10
        got = set()
        while time.time() < deadline:
            got = pane_runner._descendants(os.getpid())
            if child.pid in got and len(got) >= 2:
                break
            time.sleep(0.05)
        assert child.pid in got
        # the grandchild is in a DIFFERENT session/group yet still found via the PPID chain
        others = got - {child.pid}
        assert others, "grandchild not found by descendant walk"
    finally:
        for pid in pane_runner._descendants(os.getpid()):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        child.wait(timeout=10)


def test_descendants_of_dead_pid_is_empty():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)
    assert pane_runner._descendants(proc.pid) == set()


# ---------------------------------------------------------------------------
# spec parsing / profile reconstruction
# ---------------------------------------------------------------------------


def _spec_dict(tmp_path, **over):
    spec = {
        "engine": "claude",
        "run_id": "run1",
        "attempt_id": "a1",
        "capture_root": str(tmp_path / "cap"),
        "routing_config_digest": "sha256:deadbeef",
        "request": {
            "seat": "build",
            "requested_model": "claude-sonnet-5",
            "prompt": "hello",
            "transport": "native",
            "context": [],
            "correlation_id": None,
            "effort": None,
            "timeout": 30.0,
            "credential_ref": None,
            "containment_root": None,
            "profile": {
                "session_policy": "fresh",
                "mutating": False,
                "worktree": None,
                "tool_grants": [],
                "max_budget_usd": None,
                "effective_grants": [],
            },
        },
    }
    spec.update(over)
    return spec


def test_request_from_spec_reconstructs_profile():
    spec = _spec_dict(Path("/nonexistent"))
    spec["request"]["profile"] = {
        "session_policy": "resume", "mutating": True, "worktree": "/wt",
        "tool_grants": ["edit"], "max_budget_usd": 2.5, "effective_grants": ["edit", "read"],
    }
    req = pane_runner._request_from_spec(spec)
    assert isinstance(req, AdapterRequest)
    assert req.profile.session_policy == "resume"
    assert req.profile.mutating is True
    assert req.profile.worktree == "/wt"
    assert req.profile.max_budget_usd == 2.5
    # init=False field restored verbatim — the adapters' pre-spawn assert depends on it
    assert req.profile.effective_grants == ("edit", "read")
    assert req.containment_root is None


@pytest.mark.parametrize("missing", ["engine", "run_id", "attempt_id", "capture_root", "request"])
def test_spec_missing_field_fails_loud(tmp_path, missing):
    spec = _spec_dict(tmp_path)
    del spec[missing]
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    assert pane_runner.main([str(path)]) != 0


def test_unknown_engine_fails_loud(tmp_path):
    spec = _spec_dict(tmp_path, engine="not-an-engine")
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    assert pane_runner.main([str(path)]) != 0


# ---------------------------------------------------------------------------
# main(): direct adapter call, exit codes, sidecar
# ---------------------------------------------------------------------------


def _stub_adapter(record):
    def run(req, *, run_id, attempt_id, capture_root, routing_config_digest,
            queued_ms=0, fallback_reason=None, **kw):
        record.update(req=req, run_id=run_id, attempt_id=attempt_id,
                      capture_root=capture_root, kw=kw)
        from phase_executor.capture import create_capture
        cap = create_capture(capture_root, run_id, req.seat, attempt_id)
        cap.write_observation({"stub": True})
        cap.finalize()
        return SimpleNamespace(raw_capture_path=str(cap.path), parse_status="ok")

    return SimpleNamespace(run=run)


def test_main_calls_adapter_with_fixed_identity_and_exits_zero(tmp_path, monkeypatch):
    record = {}
    monkeypatch.setitem(pane_runner.ADAPTERS, "claude", _stub_adapter(record))
    spec = _spec_dict(tmp_path)
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    rc = pane_runner.main([str(path)])
    assert rc == 0
    # the supervisor-fixed identity went to the adapter VERBATIM (single writer, known path)
    assert record["run_id"] == "run1"
    assert record["attempt_id"] == "a1"
    assert record["capture_root"] == str(tmp_path / "cap")
    assert record["req"].seat == "build"
    obs_path = Path(record["capture_root"]) / "run1" / "build" / "a1" / "observation.json"
    assert obs_path.exists()


def test_main_codex_passes_cwd(tmp_path, monkeypatch):
    record = {}
    monkeypatch.setitem(pane_runner.ADAPTERS, "codex", _stub_adapter(record))
    spec = _spec_dict(tmp_path, engine="codex")
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    assert pane_runner.main([str(path)]) == 0
    assert record["kw"].get("cwd") == os.getcwd()


def test_main_adapter_raise_exits_nonzero(tmp_path, monkeypatch):
    def boom(req, **kw):
        raise RuntimeError("adapter died")

    monkeypatch.setitem(pane_runner.ADAPTERS, "claude", SimpleNamespace(run=boom))
    spec = _spec_dict(tmp_path)
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    assert pane_runner.main([str(path)]) != 0


def test_expected_capture_dir_matches_create_capture_layout(tmp_path):
    # the supervisor derives the SAME path create_capture will use — sanitization included
    spec = _spec_dict(tmp_path, run_id="run:1", attempt_id="a/2")
    got = pane_runner.expected_capture_dir(
        spec["capture_root"], spec["run_id"], spec["request"]["seat"], spec["attempt_id"])
    want = (Path(spec["capture_root"]).resolve()
            / sanitize_component("run:1") / sanitize_component("build") / sanitize_component("a/2"))
    assert got == want


def test_sidecar_provider_pgid_written(tmp_path, monkeypatch):
    # stub adapter spawns a start_new_session "provider" (own pgid) — the scanner thread
    # must surface that pgid to the sibling sidecar while the adapter call is in flight
    def run(req, *, run_id, attempt_id, capture_root, routing_config_digest,
            queued_ms=0, fallback_reason=None, **kw):
        provider = subprocess.Popen(
            [sys.executable, "-c", "import time;time.sleep(5)"], start_new_session=True)
        deadline = time.time() + 5
        sidecar = pane_runner.expected_capture_dir(
            capture_root, run_id, req.seat, attempt_id)
        sidecar = sidecar.with_name(sidecar.name + ".provider_pgid")
        try:
            while time.time() < deadline and not sidecar.exists():
                time.sleep(0.05)
            assert sidecar.exists(), "sidecar not written while provider alive"
            assert int(sidecar.read_text().strip()) == os.getpgid(provider.pid)
        finally:
            provider.kill()
            provider.wait(timeout=10)
        from phase_executor.capture import create_capture
        cap = create_capture(capture_root, run_id, req.seat, attempt_id)
        cap.write_observation({"stub": True})
        cap.finalize()
        return SimpleNamespace(raw_capture_path=str(cap.path))

    monkeypatch.setitem(pane_runner.ADAPTERS, "claude", SimpleNamespace(run=run))
    spec = _spec_dict(tmp_path)
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    assert pane_runner.main([str(path)]) == 0


# ---------------------------------------------------------------------------
# SIGTERM handler — graceful teardown kills the whole provider tree (CF-17)
# ---------------------------------------------------------------------------


def test_sigterm_kills_provider_tree(tmp_path):
    """Real-process integration: pane_runner runs a stub adapter whose 'provider' is in its
    OWN session (start_new_session) and spawns a grandchild. SIGTERM to pane_runner must
    kill provider AND grandchild — a pane-group-only kill would miss both."""
    stub_mod = tmp_path / "stub_sleep_adapter.py"
    pids_file = tmp_path / "pids.json"
    stub_mod.write_text(
        "import json, subprocess, sys, time\n"
        "def run(req, **kw):\n"
        "    prov = subprocess.Popen([sys.executable, '-c',\n"
        "        'import subprocess,sys,time;'\n"
        "        'g=subprocess.Popen([sys.executable,\"-c\",\"import time;time.sleep(60)\"]);'\n"
        "        'print(g.pid, flush=True);'\n"
        "        'time.sleep(60)'],\n"
        "        start_new_session=True, stdout=subprocess.PIPE, text=True)\n"
        "    gpid = int(prov.stdout.readline())\n"
        f"    open({str(pids_file)!r}, 'w').write(json.dumps({{'provider': prov.pid, 'grandchild': gpid}}))\n"
        "    time.sleep(60)\n",
        encoding="utf-8")
    spec = _spec_dict(tmp_path)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{PKG_SRC}:{tmp_path}"
    env["RAWGENTIC_PANE_ADAPTER"] = "stub_sleep_adapter"
    runner = subprocess.Popen(
        [sys.executable, "-m", "phase_executor.pane_runner", str(spec_path)],
        env=env, start_new_session=True)
    try:
        deadline = time.time() + 15
        while time.time() < deadline and not pids_file.exists():
            time.sleep(0.05)
        assert pids_file.exists(), "stub adapter never started"
        pids = json.loads(pids_file.read_text())
        os.kill(runner.pid, signal.SIGTERM)
        runner.wait(timeout=15)
        deadline = time.time() + 10
        while time.time() < deadline:
            if not _alive(pids["provider"]) and not _alive(pids["grandchild"]):
                break
            time.sleep(0.05)
        assert not _alive(pids["provider"]), "provider survived SIGTERM teardown"
        assert not _alive(pids["grandchild"]), "grandchild survived SIGTERM teardown"
    finally:
        for pid in [runner.pid] + list(json.loads(pids_file.read_text()).values()) if pids_file.exists() else [runner.pid]:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie counts as dead (tmux/parent reaps it)
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
            state = fh.read().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except OSError:
        return False


# ---------------------------------------------------------------------------
# resume-launch surface (spike #455 wiring)
# ---------------------------------------------------------------------------


def test_build_command_resume_appends_flag():
    prof = contract.LaunchProfile(session_policy="resume")
    cmd = claude_cli.build_command("claude-sonnet-5", profile=prof, resume_session_id="sess-abc")
    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == "sess-abc"
    assert "--no-session-persistence" not in cmd


def test_build_command_resume_requires_resume_policy():
    with pytest.raises(contract.CompositionError):
        claude_cli.build_command("claude-sonnet-5",
                                 profile=contract.LaunchProfile(session_policy="fresh"),
                                 resume_session_id="sess-abc")


def test_build_command_no_resume_unchanged():
    assert claude_cli.build_command("claude-sonnet-5") == [
        "claude", "--print", "--model", "claude-sonnet-5", "--output-format", "json",
        "--no-session-persistence"]


@pytest.mark.parametrize("engine", ["codex", "zhipuai"])
def test_non_claude_adapters_refuse_resume(engine, tmp_path):
    from phase_executor.adapters import codex_cli, zhipuai_sdk
    mod = {"codex": codex_cli, "zhipuai": zhipuai_sdk}[engine]
    req = AdapterRequest(seat="build", requested_model="m", prompt="p",
                         resume_session_id="sess-abc")
    with pytest.raises(contract.CompositionError):
        mod.run(req, run_id="r", attempt_id="a", capture_root=str(tmp_path),
                routing_config_digest="sha256:d")
