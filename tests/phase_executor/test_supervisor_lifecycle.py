"""#467 W4 Task 4 — TmuxSupervisor lifecycle: two-group kill, recover (adopt / quarantine /
relaunch / fail), resume-identity assert, reaper.

Real-tmux integration (skips when tmux is absent) + pure recover/reap wiring tests.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from phase_executor import routing, supervisor
from phase_executor.quota import QuotaCoordinator
from phase_executor.registry import MAX_RESUME, JobRegistry, session_name
from phase_executor.supervisor import SupervisorError, TmuxSupervisor
from phase_executor.worktree import WorktreeHandle, WorktreeIdentity

REPO = Path(__file__).resolve().parents[2]
PKG_SRC = REPO / "phase_executor" / "src"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

HAS_TMUX = shutil.which("tmux") is not None
tmux_required = pytest.mark.skipif(not HAS_TMUX, reason="tmux not installed")


def _lane(pool="claude"):
    return {"provider": "anthropic", "transport": "native",
            "auth_mode": "subscription_oauth", "credential_ref": None, "pool": pool}


def _snapshot(concurrency=4):
    return routing.RoutingSnapshot.from_table({
        "schema_version": "1",
        "pools": {"claude": {"concurrency": concurrency}},
        "seats": {"build": {"primary": {"model": "claude-sonnet-5", "lane": _lane()}, "chain": []}},
        "forbidden_combinations": [],
    })


class FakeWorktreeManager:
    """Records retain calls — W3's disposition machinery is not under test here."""

    def __init__(self):
        self.finalized = []

    def finalize(self, handle, observation_status, *, live_identities=()):
        self.finalized.append((handle.path, observation_status))
        return None


class Env:
    def __init__(self, tmp_path, sock_scratch, mode="ok", concurrency=4, extra_env=None):
        self.tmp = tmp_path
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.registry = JobRegistry(str(tmp_path / "reg"))
        self.quota = QuotaCoordinator(str(tmp_path / "quota"), {"claude": concurrency})
        self.identity = WorktreeIdentity(run_id=f"r{uuid.uuid4().hex[:6]}", seat="build", attempt=1)
        wt = tmp_path / "wt"
        wt.mkdir()
        self.handle = WorktreeHandle(path=str(wt), identity=self.identity, base_sha="deadbeef",
                                     root=str(tmp_path), gitdir=str(tmp_path / "g"), repo=str(tmp_path))
        self.manager = FakeWorktreeManager()
        self._sock_scratch = sock_scratch
        env = {"PYTHONPATH": f"{PKG_SRC}:{FIXTURES}",
               "RAWGENTIC_PANE_ADAPTER": "stub_pane_adapter",
               "RAWGENTIC_STUB_MODE": mode}
        env.update(extra_env or {})
        self.sup = TmuxSupervisor(
            snapshot=_snapshot(concurrency), quota=self.quota,
            capture_root=str(tmp_path / "cap"), registry_root=str(tmp_path / "reg"),
            registry=self.registry,
            runtime_dir=str(sock_scratch / "run"), state_dir=str(sock_scratch / "state"),
            pane_env=env, worktree_manager=self.manager, allow_adapter_override=True)

    def launch(self, **kw):
        return self.sup.launch("build", "hello", identity=self.identity, handle=self.handle, **kw)

    def sup_with_mode(self, mode, extra_env=None):
        """A second supervisor over the SAME durable state (registry/quota/sockets) — the
        post-compaction recovery shape — with a different stub mode for the relaunch."""
        env = {"PYTHONPATH": f"{PKG_SRC}:{FIXTURES}",
               "RAWGENTIC_PANE_ADAPTER": "stub_pane_adapter",
               "RAWGENTIC_STUB_MODE": mode}
        env.update(extra_env or {})
        return TmuxSupervisor(
            snapshot=_snapshot(), quota=self.quota,
            capture_root=str(self.tmp / "cap"), registry_root=str(self.tmp / "reg"),
            registry=self.registry,
            runtime_dir=str(self._sock_scratch / "run"), state_dir=str(self._sock_scratch / "state"),
            pane_env=env, worktree_manager=self.manager, allow_adapter_override=True)

    def cleanup(self):
        try:
            self.sup.kill_server(self.identity.run_id)
        except Exception:
            pass


@pytest.fixture(name="env_factory")
def _env_factory(tmp_path):
    envs = []
    scratch = Path.home() / ".cache" / "rg-w4-tests" / uuid.uuid4().hex[:10]
    scratch.joinpath("run").mkdir(parents=True)

    def make(mode="ok", concurrency=4, extra_env=None):
        e = Env(tmp_path / uuid.uuid4().hex[:8], scratch, mode=mode,
                concurrency=concurrency, extra_env=extra_env)
        envs.append(e)
        return e

    yield make
    for e in envs:
        e.cleanup()
    shutil.rmtree(scratch, ignore_errors=True)


def _wait(pred, timeout=15.0, poll=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(poll)
    return pred()


def _sidecar(rec) -> Path:
    p = Path(rec.capture_dir)
    return p.with_name(p.name + ".provider_pgid")


# ---------------------------------------------------------------------------
# two-group kill (CF-17) — both probed paths from the spike
# ---------------------------------------------------------------------------


@tmux_required
def test_cancel_kills_provider_in_own_group_graceful(env_factory):
    env = env_factory(mode="provider_sleep")
    rec = env.launch()
    assert _wait(lambda: _sidecar(rec).exists(), 20), "provider pgid never surfaced"
    provider_pgid = int(_sidecar(rec).read_text().split()[0])
    assert provider_pgid != rec.pane_pgid  # the spike's finding: distinct groups
    state = env.sup.cancel(rec)
    assert state == "failed"  # verified-dead cancel
    assert supervisor._group_pids(provider_pgid) == set()
    assert supervisor._group_pids(rec.pane_pgid) == set()
    assert env.quota.live_permits("claude") == 0


@tmux_required
def test_kill_reaches_provider_when_pane_runner_sigkilled(env_factory):
    # pane_runner never runs its SIGTERM handler — the SUPERVISOR's surfaced-pgid +
    # descendant-snapshot path must still kill the orphaned provider (probed live, F1c)
    env = env_factory(mode="provider_sleep")
    rec = env.launch()
    assert _wait(lambda: _sidecar(rec).exists(), 20)
    provider_pgid = int(_sidecar(rec).read_text().split()[0])
    os.kill(rec.pane_pid, signal.SIGKILL)
    _wait(lambda: not supervisor._pid_alive(rec.pane_pid), 10)
    assert supervisor._group_pids(provider_pgid), "provider should have survived the pane SIGKILL"
    assert env.sup._kill_job(rec) is True
    assert supervisor._group_pids(provider_pgid) == set()


# ---------------------------------------------------------------------------
# recover (OQ-8, CF-6/CF-7/CF-10)
# ---------------------------------------------------------------------------


@tmux_required
def test_recover_adopts_live_matching_job(env_factory):
    env = env_factory(mode="ok_then_sleep")
    rec = env.launch()
    try:
        actions = env.sup.recover(env.identity.run_id)
        assert len(actions) == 1
        assert actions[0].action == "adopt"
        assert env.registry.get(env.identity).state == "running"
    finally:
        env.sup.cancel(env.registry.get(env.identity))


@tmux_required
def test_recover_quarantines_digest_mismatch_kills_and_retains(env_factory):
    env = env_factory(mode="provider_sleep")
    rec = env.launch()
    # tamper the spec: the recomputed command digest no longer matches the record
    spec_path = Path(env.tmp / "reg" / "specs" / f"{rec.session_name}.json")
    spec_path.write_text(json.dumps({"tampered": True}), encoding="utf-8")
    actions = env.sup.recover(env.identity.run_id)
    assert actions[0].action == "quarantine"
    stored = env.registry.get(env.identity)
    assert stored.state == "quarantined"
    assert stored.quarantine_reason
    # the untrusted writer was KILLED (no live writer survives a mismatch)
    assert not supervisor._pid_alive(rec.pane_pid)
    # and the worktree was retained as evidence (W3 disposition, failure-status path)
    assert env.manager.finalized and env.manager.finalized[0][0] == env.handle.path
    assert env.quota.live_permits("claude") == 0


@tmux_required
def test_recover_relaunches_quota_paused_under_cap(env_factory):
    # a REAL quota-pause shape: provider exits 1 with no sentinel -> injected classification
    env = env_factory(mode="exit_nonzero")
    rec = env.launch()
    state, _ = env.sup.await_job(rec, poll_s=0.2, timeout_s=30)
    assert state == "exited_no_sentinel"
    env.sup.mark_quota_paused(env.identity, provider_session_id="sess-42")
    # recovery happens in a SECOND supervisor over the same durable state (post-compaction)
    sup2 = env.sup_with_mode("resume_ok", extra_env={"RAWGENTIC_STUB_SESSION_ID": "sess-42"})
    actions = sup2.recover(env.identity.run_id)
    assert actions[0].action == "relaunch"
    new = actions[0].record
    assert new.resume_attempts == rec.resume_attempts + 1
    assert new.state == "running"
    assert new.provider_session_id == "sess-42"  # persisted onto the relaunched record
    # the relaunched pane's spec carries the persisted session id for --resume
    spec = json.loads((Path(env.tmp / "reg" / "specs" / f"{new.session_name}.json")).read_text())
    assert spec["resume_session_id"] == "sess-42"
    assert spec["request"]["profile"]["session_policy"] == "resume"
    # resume-identity assert fires AUTOMATICALLY (resume_attempts > 0), no caller param
    state, obs = sup2.await_job(new, poll_s=0.2, timeout_s=30)
    assert state == "completed"
    assert obs is not None


@tmux_required
def test_relaunched_job_auto_asserts_resume_identity(env_factory):
    env = env_factory(mode="exit_nonzero")
    rec = env.launch()
    state, _ = env.sup.await_job(rec, poll_s=0.2, timeout_s=30)
    assert state == "exited_no_sentinel"
    env.sup.mark_quota_paused(env.identity, provider_session_id="sess-42")
    sup2 = env.sup_with_mode("resume_ok", extra_env={"RAWGENTIC_STUB_SESSION_ID": "sess-WRONG"})
    actions = sup2.recover(env.identity.run_id)
    assert actions[0].action == "relaunch"
    with pytest.raises(SupervisorError):  # no expect_session_id passed — the auto-assert fires
        sup2.await_job(actions[0].record, poll_s=0.2, timeout_s=30)
    assert env.registry.get(env.identity).state == "failed"


@tmux_required
def test_mark_quota_paused_rejects_completed_job(env_factory):
    env = env_factory(mode="ok")
    rec = env.launch()
    state, _ = env.sup.await_job(rec, poll_s=0.2, timeout_s=30)
    assert state == "completed"
    with pytest.raises(SupervisorError):  # resuming a completed job would duplicate effects
        env.sup.mark_quota_paused(env.identity, provider_session_id="sess-42")
    assert env.registry.get(env.identity).state == "completed"


@tmux_required
def test_resume_identity_mismatch_fails_loud(env_factory):
    env = env_factory(mode="resume_ok", extra_env={"RAWGENTIC_STUB_SESSION_ID": "sess-WRONG"})
    rec = env.launch()
    with pytest.raises(SupervisorError):
        env.sup.await_job(rec, poll_s=0.2, timeout_s=30, expect_session_id="sess-42")
    assert env.registry.get(env.identity).state == "failed"
    assert env.quota.live_permits("claude") == 0


def _make_record(identity, tmp_path, **over):
    from phase_executor.registry import JobRecord
    base = dict(
        identity=identity, session_name=session_name(identity), run_socket="s",
        pane_pid=1, pane_pgid=1, provider_pgid=None, pane_start_time="0",
        worktree_path=str(tmp_path), worktree_base_sha="b", worktree_root="r",
        worktree_gitdir="g", worktree_repo="rp", capture_dir=str(tmp_path / "cd"),
        attempt_id="a", permit_ref="unbounded", command_digest="sha256:x",
        provider_session_id=None, provider_exit_code=None, resume_attempts=0,
        state="running", created_at=0.0, quarantine_reason=None)
    base.update(over)
    return JobRecord(**base)


@pytest.fixture(name="recording_signals")
def _recording_signals(monkeypatch):
    """Neuter real signalling so a guard REGRESSION records instead of re-running the
    incident (the unguarded sweep killed the operator's tmux server + the test runner).
    sig 0 (liveness probe) passes through; every real signal is recorded, never sent."""
    sent = []
    real_kill = os.kill

    def fake_kill(pid, sig):
        if sig == 0:
            return real_kill(pid, 0)
        sent.append(("kill", pid, sig))
        return None

    def fake_killpg(pgid, sig):
        sent.append(("killpg", pgid, sig))
        return None

    monkeypatch.setattr(os, "kill", fake_kill)
    monkeypatch.setattr(os, "killpg", fake_killpg)
    return sent


def test_kill_job_refuses_pid_one(tmp_path, recording_signals):
    """Regression for the 2026-07-19 incident: a record carrying pane_pid=1 made
    _descendants(1) the ENTIRE host tree and the SIGKILL loop swept the caller's own
    processes (killed the operator's tmux server + the test runner). A pid/pgid <= 1
    record must produce ZERO signal attempts."""
    sup = TmuxSupervisor(snapshot=None, quota=None, capture_root=str(tmp_path / "cap"),
                         registry_root=str(tmp_path / "reg"),
                         run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", ""))
    rec = _make_record(WorktreeIdentity(run_id="r1", seat="build", attempt=1), tmp_path,
                       pane_pid=1, pane_pgid=1, provider_pgid=1)
    sup._kill_job(rec, grace_s=0.2)
    assert recording_signals == [], f"signals attempted on a pid<=1 record: {recording_signals}"


def test_kill_job_refuses_reused_pid(tmp_path, recording_signals):
    """A pane pid whose /proc start-time no longer matches the record is a FOREIGN process
    (PID reuse) — it must never be snapshotted or signalled."""
    bystander = subprocess.Popen(["sleep", "60"])
    try:
        sup = TmuxSupervisor(snapshot=None, quota=None, capture_root=str(tmp_path / "cap"),
                             registry_root=str(tmp_path / "reg"),
                             run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", ""))
        rec = _make_record(WorktreeIdentity(run_id="r1", seat="build", attempt=1), tmp_path,
                           pane_pid=bystander.pid, pane_pgid=os.getpgid(bystander.pid),
                           pane_start_time="not-the-real-start-time")
        sup._kill_job(rec, grace_s=0.2)
        touched = [s for s in recording_signals if s[1] in (bystander.pid, os.getpgid(bystander.pid))]
        assert touched == [], f"foreign (reused-pid) process was signalled: {touched}"
        assert bystander.poll() is None
    finally:
        # os.kill is monkeypatched to record — tear down via the external kill binary
        subprocess.run(["kill", "-9", str(bystander.pid)], check=False)
        bystander.wait(timeout=10)


def test_provider_target_requires_verified_starttime(tmp_path, recording_signals):
    """A sidecar pgid WITHOUT a matching leader start-time is never a killpg target —
    PGIDs recycle like PIDs (the incident class on the pgid axis)."""
    bystander = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        sup = TmuxSupervisor(snapshot=None, quota=None, capture_root=str(tmp_path / "cap"),
                             registry_root=str(tmp_path / "reg"),
                             run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", ""))
        rec = _make_record(WorktreeIdentity(run_id="r1", seat="build", attempt=1), tmp_path,
                           capture_dir=str(tmp_path / "cd"))
        side = Path(tmp_path / "cd")
        side = side.with_name(side.name + ".provider_pgid")
        pgid = os.getpgid(bystander.pid)
        # bare pgid (no start-time) → unverifiable → not a target
        side.parent.mkdir(parents=True, exist_ok=True)
        side.write_text(f"{pgid}\n", encoding="ascii")
        assert sup._provider_target(rec) is None
        # wrong start-time → recycled/foreign → not a target
        side.write_text(f"{pgid} 999999999\n", encoding="ascii")
        assert sup._provider_target(rec) is None
        sup._kill_job(rec, grace_s=0.2)
        touched = [s for s in recording_signals if s[1] == pgid]
        assert touched == [], f"unverified provider group was signalled: {touched}"
    finally:
        subprocess.run(["kill", "-9", str(bystander.pid)], check=False)
        bystander.wait(timeout=10)


def test_registry_corrupt_fails_loud(tmp_path):
    """A present-but-corrupt jobs.json raises — never a silent empty view that the next
    upsert would persist (orphaned providers, leaked permits)."""
    from phase_executor.registry import RegistryCorrupt
    reg = JobRegistry(str(tmp_path / "reg"))
    identity = WorktreeIdentity(run_id="r1", seat="build", attempt=1)
    reg.upsert(_make_record(identity, tmp_path))
    (Path(tmp_path) / "reg" / "jobs.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(RegistryCorrupt):
        reg.all()
    with pytest.raises(RegistryCorrupt):
        reg.upsert(_make_record(identity, tmp_path))


def test_recover_fail_at_resume_cap(tmp_path):
    # pure: a quota_paused record at MAX_RESUME fails, never relaunches
    reg = JobRegistry(str(tmp_path / "reg"))
    identity = WorktreeIdentity(run_id="r1", seat="build", attempt=1)
    from phase_executor.registry import JobRecord
    rec = JobRecord(identity=identity, session_name=session_name(identity), run_socket="s",
                    pane_pid=1, pane_pgid=1, provider_pgid=None, pane_start_time="0",
                    worktree_path=str(tmp_path), worktree_base_sha="b", worktree_root="r",
                    worktree_gitdir="g", worktree_repo="rp", capture_dir=str(tmp_path / "cd"),
                    attempt_id="a", permit_ref="unbounded", command_digest="sha256:x",
                    provider_session_id="sess-1", provider_exit_code=None,
                    resume_attempts=MAX_RESUME, state="quota_paused", created_at=0.0,
                    quarantine_reason=None)
    reg.upsert(rec)

    def dead_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no session")

    sup = TmuxSupervisor(snapshot=_snapshot(), quota=QuotaCoordinator(str(tmp_path / "q"), {}),
                         capture_root=str(tmp_path / "cap"), registry_root=str(tmp_path / "reg"),
                         registry=reg, run=dead_run)
    sup._identity_matches = lambda r: True  # isolate the CAP rule from spec-file plumbing
    actions = sup.recover("r1")
    assert actions[0].action == "fail"
    assert reg.get(identity).state == "failed"


# ---------------------------------------------------------------------------
# reap (AC-E6, CF-8/CF-11/CF-19)
# ---------------------------------------------------------------------------


@tmux_required
def test_reap_kills_finalized_keeps_live_fresh_releases_orphan_permit(env_factory):
    env = env_factory(mode="ok_then_sleep")
    live_rec = env.launch()
    try:
        # a finalized record whose session is DEAD, with an orphaned permit file left behind
        id2 = WorktreeIdentity(run_id=env.identity.run_id, seat="build", attempt=2)
        orphan_permit = Path(env.tmp) / "quota" / "claude" / "default" / "permit-orphan-x"
        orphan_permit.parent.mkdir(parents=True, exist_ok=True)
        orphan_permit.write_text("1\n0\n", encoding="utf-8")
        from dataclasses import replace
        dead_child = subprocess.Popen(["true"])
        dead_child.wait(timeout=10)
        dead_rec = replace(
            live_rec, identity=id2, session_name=session_name(id2),
            pane_pid=dead_child.pid, pane_pgid=dead_child.pid,
            capture_dir=str(Path(env.tmp) / "cap-dead"), attempt_id="dead",
            permit_ref=str(orphan_permit), state="completed")
        env.registry.upsert(dead_rec)
        summary = env.sup.reap(env.identity.run_id)
        assert any(r.identity == id2 for r in summary.kill_session)
        assert not orphan_permit.exists()  # CF-19: orphaned permit released
        # the live+fresh job was NEVER swept
        assert any(r.identity == env.identity for r in summary.keep)
        live = env.sup._live(live_rec)
        assert live is True
    finally:
        env.sup.cancel(env.registry.get(env.identity))


@tmux_required
def test_reap_retains_dirty_dead_worktree(env_factory):
    env = env_factory(mode="exit_nonzero")
    rec = env.launch()
    state, _ = env.sup.await_job(rec, poll_s=0.2, timeout_s=30)
    assert state == "exited_no_sentinel"
    (Path(env.handle.path) / "dirty.txt").write_text("residue", encoding="utf-8")

    def dirty_clean_fn(record):
        return False

    summary = env.sup.reap(env.identity.run_id, clean_fn=dirty_clean_fn)
    assert any(r.identity == env.identity for r in summary.retain_worktree)
    assert env.manager.finalized  # W3 retain invoked, evidence preserved
    # repeat-safety: a second sweep must NOT re-invoke W3 finalize on the same record
    n_finalized = len(env.manager.finalized)
    env.sup.reap(env.identity.run_id, clean_fn=dirty_clean_fn)
    assert len(env.manager.finalized) == n_finalized
    assert env.registry.get(env.identity).quarantine_reason.startswith("reaped:")
