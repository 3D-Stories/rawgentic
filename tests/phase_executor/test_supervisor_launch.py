"""#467 W4 Task 3 — TmuxSupervisor: preflight, resolve_socket, launch, status, await_job.

Pure tests (socket resolution, preflight negatives via a stub runner) plus integration
tests over a REAL tmux 3.4 private socket with the stub pane adapter
(fixtures/stub_pane_adapter.py via RAWGENTIC_PANE_ADAPTER). Integration tests skip when
tmux is absent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from phase_executor import contract, routing, supervisor
from phase_executor.quota import QuotaCoordinator, QuotaTimeout
from phase_executor.registry import JobRegistry, session_name
from phase_executor.supervisor import SupervisorError, TmuxSupervisor, resolve_socket
from phase_executor.worktree import WorktreeHandle, WorktreeIdentity

REPO = Path(__file__).resolve().parents[2]
PKG_SRC = REPO / "phase_executor" / "src"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

HAS_TMUX = shutil.which("tmux") is not None
tmux_required = pytest.mark.skipif(not HAS_TMUX, reason="tmux not installed")


# ---------------------------------------------------------------------------
# resolve_socket (CF-2 / CF-20)
# ---------------------------------------------------------------------------
# pytest's tmp_path lives under /tmp, which resolve_socket REJECTS by design (#452) —
# socket-dir tests use a HOME-based scratch instead.


@pytest.fixture(name="home_scratch")
def _home_scratch():
    base = Path.home() / ".cache" / "rg-w4-tests" / uuid.uuid4().hex[:10]
    base.mkdir(parents=True)
    yield base
    shutil.rmtree(base, ignore_errors=True)


def test_resolve_socket_prefers_runtime_dir(home_scratch):
    ru = home_scratch / "ru"
    ru.mkdir()
    sock = resolve_socket("run1", runtime_dir=str(ru), state_dir=str(home_scratch / "st"))
    assert Path(sock).parent == ru
    assert Path(sock).name.startswith("rg-") and sock.endswith(".sock")


def test_resolve_socket_falls_back_to_state_dir(home_scratch):
    st = home_scratch / "state"
    sock = resolve_socket("run1", runtime_dir=str(home_scratch / "absent"), state_dir=str(st))
    assert Path(sock).parent == st
    assert st.is_dir()
    assert (st.stat().st_mode & 0o777) == 0o700


def test_resolve_socket_rejects_tmp(home_scratch):
    with pytest.raises(SupervisorError):
        # an existing, writable /tmp base is CHOSEN then rejected (never silently accepted)
        resolve_socket("run1", runtime_dir="/tmp", state_dir=str(home_scratch / "st"))
    with pytest.raises(SupervisorError):
        # $TMPDIR containment, not just literal /tmp
        resolve_socket("run1", runtime_dir=str(home_scratch / "t"),
                       state_dir=str(home_scratch / "t2"), tmpdir=str(home_scratch))


def test_resolve_socket_rejects_long_path(home_scratch):
    deep = home_scratch / ("x" * 120)
    deep.mkdir()
    with pytest.raises(SupervisorError):
        resolve_socket("run1", runtime_dir=str(deep), state_dir=str(deep))


# ---------------------------------------------------------------------------
# preflight (AC-E1, fail-closed both ways)
# ---------------------------------------------------------------------------


def _fail_runner(fail_on: str):
    def run(cmd, **kw):
        joined = " ".join(cmd)
        if fail_on in joined:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=f"stub fail: {fail_on}")
        if cmd[-1] == "-V" or "-V" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="tmux 3.4\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    return run


def test_preflight_fail_closed_on_missing_verb(tmp_path):
    sup = TmuxSupervisor(snapshot=None, quota=None, capture_root=str(tmp_path / "cap"),
                         registry_root=str(tmp_path / "reg"), run=_fail_runner("new-session"))
    res = sup.preflight(str(tmp_path / "s.sock"))
    assert res.supported is False
    assert "new-session" in res.reason


def test_preflight_fail_closed_on_old_version(tmp_path):
    def run(cmd, **kw):
        if "-V" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="tmux 2.9a\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    sup = TmuxSupervisor(snapshot=None, quota=None, capture_root=str(tmp_path / "cap"),
                         registry_root=str(tmp_path / "reg"), run=run)
    res = sup.preflight(str(tmp_path / "s.sock"))
    assert res.supported is False
    assert "version" in res.reason


def test_preflight_fail_closed_on_unusable_socket_dir(tmp_path):
    sup = TmuxSupervisor(snapshot=None, quota=None, capture_root=str(tmp_path / "cap"),
                         registry_root=str(tmp_path / "reg"), run=_fail_runner("nothing"))
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        res = sup.preflight(str(ro / "sub" / "s.sock"))
        assert res.supported is False
    finally:
        ro.chmod(0o700)


@tmux_required
def test_preflight_positive_real_tmux(tmp_path):
    sup = TmuxSupervisor(snapshot=None, quota=None, capture_root=str(tmp_path / "cap"),
                         registry_root=str(tmp_path / "reg"))
    sock = str(tmp_path / "pf.sock")
    res = sup.preflight(sock)
    assert res.supported is True, res.reason
    # preflight cleaned up after itself: no probe session left on the private server
    probe = subprocess.run(["tmux", "-S", sock, "list-sessions"],
                           capture_output=True, text=True, check=False)
    assert "rg-preflight" not in (probe.stdout or "")


# ---------------------------------------------------------------------------
# integration harness
# ---------------------------------------------------------------------------


def _lane(pool="claude"):
    return {"provider": "anthropic", "transport": "native",
            "auth_mode": "subscription_oauth", "credential_ref": None, "pool": pool}


def _snapshot(concurrency=2):
    return routing.RoutingSnapshot.from_table({
        "schema_version": "1",
        "pools": {"claude": {"concurrency": concurrency}},
        "seats": {"build": {"primary": {"model": "claude-sonnet-5", "lane": _lane()}, "chain": []}},
        "forbidden_combinations": [],
    })


class Env:
    def __init__(self, tmp_path, sock_scratch, mode="ok", concurrency=2):
        self.tmp = tmp_path
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.registry = JobRegistry(str(tmp_path / "reg"))
        self.quota = QuotaCoordinator(str(tmp_path / "quota"), {"claude": concurrency})
        self.identity = WorktreeIdentity(run_id=f"r{uuid.uuid4().hex[:6]}", seat="build", attempt=1)
        wt = tmp_path / "wt"
        wt.mkdir()
        self.handle = WorktreeHandle(path=str(wt), identity=self.identity, base_sha="deadbeef",
                                     root=str(tmp_path), gitdir=str(tmp_path / "g"), repo=str(tmp_path))
        self.sup = TmuxSupervisor(
            snapshot=_snapshot(concurrency), quota=self.quota,
            capture_root=str(tmp_path / "cap"), registry_root=str(tmp_path / "reg"),
            registry=self.registry,
            runtime_dir=str(sock_scratch / "run"), state_dir=str(sock_scratch / "state"),
            pane_env={"PYTHONPATH": f"{PKG_SRC}:{FIXTURES}",
                      "RAWGENTIC_PANE_ADAPTER": "stub_pane_adapter",
                      "RAWGENTIC_STUB_MODE": mode},
            allow_adapter_override=True)

    def launch(self, **kw):
        return self.sup.launch("build", "hello", identity=self.identity, handle=self.handle, **kw)

    def cleanup(self):
        try:
            self.sup.kill_server(self.identity.run_id)
        except Exception:
            pass


@pytest.fixture(name="env_factory")
def _env_factory(tmp_path):
    envs = []
    scratch = Path.home() / ".cache" / "rg-w4-tests" / uuid.uuid4().hex[:10]
    scratch.mkdir(parents=True)
    scratch.joinpath("run").mkdir()

    def make(mode="ok", concurrency=2):
        e = Env(tmp_path / uuid.uuid4().hex[:8], scratch, mode=mode, concurrency=concurrency)
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


# ---------------------------------------------------------------------------
# launch (AC-E2/E3/E5)
# ---------------------------------------------------------------------------


@tmux_required
def test_launch_records_running_job_and_holds_permit(env_factory):
    env = env_factory(mode="ok_then_sleep")
    rec = env.launch()
    try:
        assert rec.state == "running"
        assert rec.pane_pid > 0
        assert rec.session_name == session_name(env.identity)
        # permit held (AC-E5: acquired at launch, held by the supervisor for the job)
        assert env.quota.live_permits("claude") == 1
        assert rec.permit_ref and rec.permit_ref != "unbounded"
        # durable registry record
        stored = env.registry.get(env.identity)
        assert stored is not None and stored.state == "running"
        assert stored.command_digest.startswith("sha256:")
        # invisible on the DEFAULT tmux server (private socket isolation)
        default = subprocess.run(["tmux", "list-sessions"], capture_output=True, text=True, check=False)
        assert rec.session_name not in (default.stdout or "")
        # the pane process IS pane_runner (command-as-initial-pane-process, F7: never send-keys)
        out = subprocess.run(["tmux", "-S", rec.run_socket, "display-message", "-p", "-t",
                              rec.session_name, "#{pane_pid}"], capture_output=True, text=True, check=False)
        assert int(out.stdout.strip()) == rec.pane_pid
    finally:
        env.sup.cancel(rec)


@tmux_required
def test_launch_unknown_seat_fails_loud_no_permit_leak(env_factory):
    env = env_factory()
    with pytest.raises(routing.RoutingError):  # unknown seat = config error, fail-loud
        env.sup.launch("nope", "x", identity=env.identity, handle=env.handle)
    assert env.quota.live_permits("claude") == 0  # nothing leaked


@tmux_required
def test_launch_full_pool_raises_quota_timeout(env_factory):
    env = env_factory(mode="ok_then_sleep", concurrency=1)
    rec = env.launch()
    try:
        id2 = WorktreeIdentity(run_id=env.identity.run_id, seat="build", attempt=2)
        with pytest.raises(QuotaTimeout):
            env.sup.launch("build", "x", identity=id2, handle=env.handle, quota_timeout=0.5)
    finally:
        env.sup.cancel(rec)


# ---------------------------------------------------------------------------
# status derivation + await_job (AC-E3/E4, CF-9/CF-12)
# ---------------------------------------------------------------------------


@tmux_required
def test_await_collects_valid_sentinel_and_kills(env_factory):
    env = env_factory(mode="ok")
    rec = env.launch()
    state, obs = env.sup.await_job(rec, poll_s=0.2, timeout_s=30)
    assert state == "completed"
    assert obs is not None and obs["run_id"] == env.identity.run_id
    assert env.quota.live_permits("claude") == 0  # permit released on collect
    live = subprocess.run(["tmux", "-S", rec.run_socket, "has-session", "-t", rec.session_name],
                          capture_output=True, text=True, check=False)
    assert live.returncode != 0  # session gone: collect => kill, zero residue
    assert env.registry.get(env.identity).state == "completed"


@tmux_required
def test_await_valid_obs_with_incomplete_marker_still_collected(env_factory):
    # CF-9: validity is INDEPENDENT of .incomplete
    env = env_factory(mode="ok_then_sleep")
    rec = env.launch()
    state, obs = env.sup.await_job(rec, poll_s=0.2, timeout_s=30)
    assert state == "completed"
    assert obs is not None


@tmux_required
def test_await_timeout_writes_supervisor_timeout_obs(env_factory):
    env = env_factory(mode="sleep")
    rec = env.launch()
    state, obs = env.sup.await_job(rec, poll_s=0.2, timeout_s=2)
    assert state == "timed_out"
    assert obs is not None and obs["parse_status"] == contract.TIMEOUT
    assert env.registry.get(env.identity).state == "timed_out"
    assert env.quota.live_permits("claude") == 0
    # the pane process is DEAD (two-group kill + verify)
    assert not supervisor._pid_alive(rec.pane_pid)


@tmux_required
def test_launch_threads_correlation_id_into_spec_and_timeout_obs(env_factory):
    """#472 D2: launch(correlation_id=...) lands in the pane spec's request (the child's
    observation reads it from there — pane_runner already propagates it) AND in the
    supervisor's synthetic timeout observation, so the WF2 correlation join survives
    both the happy path and the pane-death path."""
    env = env_factory(mode="sleep")
    rec = env.launch(correlation_id="472-step8-t2")
    spec = json.loads(Path(env.tmp / "reg" / "specs" / f"{rec.session_name}.json").read_text())
    assert spec["request"]["correlation_id"] == "472-step8-t2"
    state, obs = env.sup.await_job(rec, poll_s=0.2, timeout_s=2)
    assert state == "timed_out"
    assert obs["correlation_id"] == "472-step8-t2"


def test_synthetic_observation_carries_correlation_id():
    d = supervisor.synthetic_observation(
        run_id="r1", seat="build", attempt_id="0-abcd1234", engine="claude",
        requested_model="m", prompt="p", parse_status=contract.TIMEOUT,
        reason="t", routing_config_digest="sha256:x", correlation_id="cid-9")
    assert d["correlation_id"] == "cid-9"


@tmux_required
def test_await_timeout_prefers_childs_valid_obs(env_factory):
    # CF-12: the obs-writer is the pane_runner, killed before the re-check — a valid child
    # obs found AFTER the kill wins; the supervisor's timeout obs never clobbers it.
    env = env_factory(mode="ok_then_sleep")
    rec = env.launch()
    assert _wait(lambda: Path(rec.capture_dir, "observation.json").exists(), 20)
    state, obs = env.sup.await_job(rec, poll_s=10.0, timeout_s=0.1)  # forces the timeout path
    assert state == "completed"
    assert obs is not None and obs["parse_status"] == contract.OK


@tmux_required
def test_malformed_obs_is_exited_no_sentinel(env_factory):
    env = env_factory(mode="malformed")
    rec = env.launch(correlation_id="557-mal")
    state, obs = env.sup.await_job(rec, poll_s=0.2, timeout_s=30)
    assert state == "exited_no_sentinel"
    # #557 AC1/AC2 + 8a review: a death that left an UNPARSEABLE observation is a
    # SUSPICIOUS terminal state — the synthetic is schema-valid + correlation-bound (so
    # the failure is recorded, not lost) but carries PARSE_ERROR, which reconcile treats
    # as a breach a verified sibling cannot launder.
    assert obs is not None and obs["parse_status"] == contract.PARSE_ERROR
    assert obs["correlation_id"] == "557-mal"
    contract.validate_observation(obs)
    on_disk = json.loads(Path(rec.capture_dir, "observation.json").read_text(encoding="utf-8"))
    assert on_disk["parse_status"] == contract.PARSE_ERROR
    # the malformed original is preserved as forensics, not silently destroyed
    forensics = json.loads(Path(rec.capture_dir, "observation.malformed.json").read_text(encoding="utf-8"))
    assert forensics == {"not": "an observation"}
    assert env.registry.get(env.identity).state == "exited_no_sentinel"
    assert env.quota.live_permits("claude") == 0


@tmux_required
def test_effectful_crash_no_obs_is_suspicious_not_forgivable(env_factory):
    # #557 Step-11 review: a child that ran the provider (transport.stdout.txt captured)
    # then died before writing observation.json is EFFECTFUL — it must NOT classify as a
    # clean, sibling-forgivable availability failure. transport-present + obs-absent →
    # PARSE_ERROR (a breach reconcile refuses), closing the refuse->forgive laundering the
    # observation.json-only discriminator would have opened.
    env = env_factory(mode="transport_then_fail")
    rec = env.launch(correlation_id="557-eff")
    state, obs = env.sup.await_job(rec, poll_s=0.2, timeout_s=30)
    assert state == "exited_no_sentinel"
    assert obs is not None and obs["parse_status"] == contract.PARSE_ERROR
    assert obs["correlation_id"] == "557-eff"
    # the provider transport survives on disk as evidence the model ran
    assert Path(rec.capture_dir, "transport.stdout.txt").exists()


@tmux_required
def test_nonzero_exit_no_sentinel_not_auto_resumed(env_factory):
    env = env_factory(mode="exit_nonzero")
    rec = env.launch()
    state, obs = env.sup.await_job(rec, poll_s=0.2, timeout_s=30)
    assert state == "exited_no_sentinel"  # DEFAULT for ambiguous nonzero exit — never quota_paused
    # #557 AC1: a CLEAN death (no observation.json at all) is a genuine availability
    # failure → NO_RESPONSE (forgivable by a verified sibling); the synthetic carries the
    # REAL routing digest from the spec (binds to its receipt in reconcile), never the
    # sha256:unknown fallback.
    assert obs is not None and obs["parse_status"] == contract.NO_RESPONSE
    assert obs["routing_config_digest"].startswith("sha256:")
    assert obs["routing_config_digest"] != "sha256:unknown"
    # no malformed file existed, so none is fabricated
    assert not Path(rec.capture_dir, "observation.malformed.json").exists()


@tmux_required
def test_run_seat_tmux_round_trip(env_factory):
    env = env_factory(mode="ok")
    state, obs = env.sup.run_seat_tmux("build", "hello", identity=env.identity,
                                       handle=env.handle, poll_s=0.2, timeout_s=30)
    assert state == "completed"
    assert obs["seat"] == "build"


def test_status_quota_paused_only_injected(tmp_path):
    # mark_quota_paused is the ONE entry into quota_paused (W9 owns the discriminator),
    # and it is fail-closed: dead + no sentinel + non-terminal + non-empty session id
    reg = JobRegistry(str(tmp_path / "reg"))
    identity = WorktreeIdentity(run_id="r1", seat="build", attempt=1)

    def dead_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no session")

    sup = TmuxSupervisor(snapshot=None, quota=None, capture_root=str(tmp_path / "cap"),
                         registry_root=str(tmp_path / "reg"), registry=reg, run=dead_run)
    from phase_executor.registry import JobRecord
    rec = JobRecord(identity=identity, session_name=session_name(identity), run_socket="s",
                    pane_pid=1, pane_pgid=1, provider_pgid=None, pane_start_time="0",
                    worktree_path="w", worktree_base_sha="b", worktree_root="r",
                    worktree_gitdir="g", worktree_repo="rp", capture_dir=str(tmp_path / "cd"),
                    attempt_id="a", permit_ref="unbounded", command_digest="sha256:x",
                    provider_session_id=None, provider_exit_code=None, resume_attempts=0,
                    state="exited_no_sentinel", created_at=0.0, quarantine_reason=None)
    reg.upsert(rec)
    with pytest.raises(SupervisorError):  # empty session id refused
        sup.mark_quota_paused(identity, provider_session_id=None)
    got = sup.mark_quota_paused(identity, provider_session_id="sess-1")
    assert got.state == "quota_paused"
    assert got.provider_session_id == "sess-1"
    assert reg.get(identity).state == "quota_paused"
    with pytest.raises(SupervisorError):  # already terminal-for-recover — refused
        sup.mark_quota_paused(identity, provider_session_id="sess-2")


def test_mark_quota_paused_sentinel_guard_boundary(tmp_path):
    # #557: the completion guard keys on the sentinel's SHAPE, not its existence —
    # an exited_no_sentinel record now always carries the supervisor's synthetic
    # availability observation (no_response = recorded failure, resumable), while an
    # envelope-producing sentinel (ok = effectful child result) still refuses.
    reg = JobRegistry(str(tmp_path / "reg"))
    identity = WorktreeIdentity(run_id="r1", seat="build", attempt=1)

    def dead_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no session")

    sup = TmuxSupervisor(snapshot=None, quota=None, capture_root=str(tmp_path / "cap"),
                         registry_root=str(tmp_path / "reg"), registry=reg, run=dead_run)
    from phase_executor.registry import JobRecord
    cap = tmp_path / "cd"
    cap.mkdir()
    rec = JobRecord(identity=identity, session_name=session_name(identity), run_socket="s",
                    pane_pid=1, pane_pgid=1, provider_pgid=None, pane_start_time="0",
                    worktree_path="w", worktree_base_sha="b", worktree_root="r",
                    worktree_gitdir="g", worktree_repo="rp", capture_dir=str(cap),
                    attempt_id="a", permit_ref="unbounded", command_digest="sha256:x",
                    provider_session_id=None, provider_exit_code=None, resume_attempts=0,
                    state="exited_no_sentinel", created_at=0.0, quarantine_reason=None)
    reg.upsert(rec)
    # availability synthetic (identity-matching) → pause ACCEPTED
    synth = supervisor.synthetic_observation(
        run_id="r1", seat="build", attempt_id="a", engine="claude",
        requested_model="m", prompt="p", parse_status=contract.NO_RESPONSE,
        reason="child exited without sentinel", routing_config_digest="sha256:d",
        correlation_id="c1")
    (cap / "observation.json").write_text(json.dumps(synth), encoding="utf-8")
    got = sup.mark_quota_paused(identity, provider_session_id="sess-1")
    assert got.state == "quota_paused"
    # envelope-producing OK sentinel → still refused (effects would duplicate)
    reg.upsert(rec)  # reset state to exited_no_sentinel
    ok_obs = contract.Observation(
        run_id="r1", attempt_id="a", seat="build", engine="claude", transport="native",
        requested_model="m", actual_model="m", prompt_hash="sha256:x",
        usage={"input": 1, "output": 1}, timing_ms=1, queued_ms=0,
        process={"exit_code": 0, "timed_out": False}, parse_status="ok",
        parsed_payload=None, raw_capture_path=None, fallback_reason=None,
        routing_config_digest="sha256:d").to_dict()
    (cap / "observation.json").write_text(json.dumps(ok_obs), encoding="utf-8")
    with pytest.raises(SupervisorError):
        sup.mark_quota_paused(identity, provider_session_id="sess-2")
    # #557 8a review: an availability-STATUS sentinel that nonetheless ATTESTED a model
    # is effectful (the model ran / billed) → still refused, even though no_response is an
    # availability failure. Only the supervisor's actual_model=None synthetic is resumable.
    reg.upsert(rec)
    effectful = dict(synth)
    effectful["actual_model"] = "m"  # provider attested → effectful, not resumable
    contract.validate_observation(effectful)
    (cap / "observation.json").write_text(json.dumps(effectful), encoding="utf-8")
    with pytest.raises(SupervisorError):
        sup.mark_quota_paused(identity, provider_session_id="sess-3")
