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


# ---------------------------------------------------------------------------
# #558 AC1/AC3 — collection-time quota detection (unit-style: dead runner, no tmux)
# ---------------------------------------------------------------------------

import hashlib as _hashlib

from phase_executor import quota_detect
from phase_executor.capture import hash_text as _hash_text
from phase_executor.registry import JobRecord as _JR, classify_recovery as _classify

QUOTA_STDERR = "5-hour limit reached resets 3am"
_CALIB = frozenset({(quota_detect.CLASSIFIER_VERSION, quota_detect.RULE_TABLE_DIGEST)})


def _dead_run(cmd, **kw):
    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no session")


def _nonzero_obs(identity, attempt_id, *, exit_code=1, actual_model=None):
    d = contract.Observation(
        run_id=identity.run_id, attempt_id=attempt_id, correlation_id="c1",
        seat=identity.seat, engine="claude", transport="native", requested_model="m",
        actual_model=actual_model, prompt_hash="sha256:x", context_hashes=[], usage=None,
        timing_ms=0, queued_ms=0, process={"exit_code": exit_code, "timed_out": False},
        parse_status=contract.NONZERO_EXIT, parsed_payload=None, raw_capture_path=None,
        fallback_reason="nonzero exit", routing_config_digest="sha256:d").to_dict()
    contract.validate_observation(d)
    return d


_DEFAULT_PROFILE = {"session_policy": "resume", "mutating": False, "worktree": None,
                    "tool_grants": [], "max_budget_usd": None, "effective_grants": []}


class _QuotaCollect:
    """One dead-writer NONZERO_EXIT collect, fully on disk (no tmux needed —
    pane_pid=1 trips _kill_job's hard guard and verifies dead immediately)."""

    def __init__(self, tmp_path, *, engine="claude", profile=None, stderr=QUOTA_STDERR,
                 envelope='{"session_id": "sess-1", "subtype": "success"}',
                 exit_code=1, tamper_spec=False, actual_model=None):
        self.reg = JobRegistry(str(tmp_path / "reg"))
        self.identity = WorktreeIdentity(run_id="r1", seat="build", attempt=1)
        name = session_name(self.identity)
        self.cap = tmp_path / "capd"
        self.cap.mkdir()
        prof = _DEFAULT_PROFILE if profile is None else profile
        spec = {"engine": engine, "run_id": "r1", "attempt_id": "a",
                "request": {"seat": "build", "requested_model": "m", "prompt": "p",
                            "correlation_id": "c1", "profile": prof}}
        specs = tmp_path / "reg" / "specs"
        specs.mkdir(parents=True, exist_ok=True)
        text = json.dumps(spec, indent=2, sort_keys=True)
        (specs / f"{name}.json").write_text(text, encoding="utf-8")
        digest = "sha256:tampered" if tamper_spec else _hash_text(text)
        self.permit = tmp_path / "permit.tok"
        self.permit.write_text("t", encoding="utf-8")
        obs = _nonzero_obs(self.identity, "a", exit_code=exit_code, actual_model=actual_model)
        (self.cap / "observation.json").write_text(json.dumps(obs), encoding="utf-8")
        if stderr is not None:
            raw = stderr.encode("utf-8") if isinstance(stderr, str) else stderr
            (self.cap / "stderr.txt").write_bytes(raw)
        if envelope is not None:
            (self.cap / "transport.stdout.txt").write_text(envelope, encoding="utf-8")
        self.record = _JR(
            identity=self.identity, session_name=name, run_socket="s", pane_pid=1,
            pane_pgid=1, provider_pgid=None, pane_start_time="0", worktree_path="w",
            worktree_base_sha="b", worktree_root="r", worktree_gitdir="g",
            worktree_repo="rp", capture_dir=str(self.cap), attempt_id="a",
            permit_ref=str(self.permit), command_digest="sha256:x",
            provider_session_id=None, provider_exit_code=None, resume_attempts=0,
            state="running", created_at=0.0, quarantine_reason=None, spec_digest=digest)
        self.reg.upsert(self.record)
        self.sup = TmuxSupervisor(
            snapshot=None, quota=None, capture_root=str(tmp_path / "cap"),
            registry_root=str(tmp_path / "reg"), registry=self.reg, run=_dead_run)

    def collect(self):
        return self.sup.await_job(self.record, poll_s=0.1, timeout_s=5)

    def stored(self):
        return self.reg.get(self.identity)


def test_collect_quota_paused_activated_path(tmp_path, monkeypatch):
    """The ACTIVATED path, pinned via a test-scoped allowlist entry — #559's genuine
    calibration only has to add its one (version, digest) pair to flip this live."""
    q = _QuotaCollect(tmp_path)
    monkeypatch.setattr(supervisor, "CALIBRATED_CLASSIFIERS", _CALIB)
    upserts = []
    orig = q.reg.upsert
    monkeypatch.setattr(q.reg, "upsert", lambda r: (upserts.append(r), orig(r))[1])
    state, obs = q.collect()
    assert state == "quota_paused"
    assert obs is not None
    stored = q.stored()
    assert stored.state == "quota_paused"
    assert stored.provider_session_id == "sess-1"
    qc = stored.quota_classification
    assert qc["paused"] is True and qc["verdict"] is True
    assert qc["classifier_version"] == quota_detect.CLASSIFIER_VERSION
    # classification lands in the SAME upsert as the state flip (no torn window)
    final = upserts[-1]
    assert final.state == "quota_paused" and final.quota_classification is not None
    # permit released on the verified-kill pause path
    assert not q.permit.exists()
    # the paused record is exactly what recovery relaunches
    assert _classify(stored, live=False, identity_matches=True,
                     sentinel_valid=True) == "relaunch"


def test_collect_quota_shadow_default_empty_allowlist(tmp_path):
    """SHADOW is the shipped default (D-12): allowlist EMPTY, v1 verdict recorded but
    the pause refused as uncalibrated_classifier — behavior stays exactly pre-#558."""
    q = _QuotaCollect(tmp_path)
    state, _ = q.collect()
    assert state == "completed"
    qc = q.stored().quota_classification
    assert qc["verdict"] is True and qc["paused"] is False
    assert qc["refusal"] == "uncalibrated_classifier"
    assert not q.permit.exists()


def test_collect_quota_unknown_future_version_stays_shadow(tmp_path, monkeypatch):
    # regression pin: a future version/digest NOT in the allowlist never auto-pauses
    q = _QuotaCollect(tmp_path)
    monkeypatch.setattr(supervisor, "CALIBRATED_CLASSIFIERS",
                        frozenset({(99, "sha-of-a-future-table")}))
    state, _ = q.collect()
    assert state == "completed"
    assert q.stored().quota_classification["refusal"] == "uncalibrated_classifier"


def test_collect_quota_refusal_fresh_policy(tmp_path, monkeypatch):
    prof = dict(_DEFAULT_PROFILE, session_policy="fresh")
    q = _QuotaCollect(tmp_path, profile=prof)
    monkeypatch.setattr(supervisor, "CALIBRATED_CLASSIFIERS", _CALIB)
    state, _ = q.collect()
    assert state == "completed"
    assert q.stored().quota_classification["refusal"] == "session_not_persisted"


def test_collect_quota_refusal_mutating_takes_precedence(tmp_path, monkeypatch):
    # deterministic single-reason: read-only guard precedes resume-policy + calibration
    prof = dict(_DEFAULT_PROFILE, session_policy="fresh", mutating=True)
    q = _QuotaCollect(tmp_path, profile=prof)
    monkeypatch.setattr(supervisor, "CALIBRATED_CLASSIFIERS", _CALIB)
    state, _ = q.collect()
    assert state == "completed"
    assert q.stored().quota_classification["refusal"] == "mutating_requires_manual"


def test_collect_quota_refusal_no_envelope(tmp_path, monkeypatch):
    q = _QuotaCollect(tmp_path, envelope=None)
    monkeypatch.setattr(supervisor, "CALIBRATED_CLASSIFIERS", _CALIB)
    state, _ = q.collect()
    assert state == "completed"
    qc = q.stored().quota_classification
    assert qc["refusal"] == "no_resumable_session"
    assert qc["envelope_error"] == "missing"


def test_collect_quota_refusal_tampered_spec(tmp_path):
    """Digest mismatch: classify NOTHING — no evidence read is trusted; the collect
    persists the typed spec_unverified marker only."""
    q = _QuotaCollect(tmp_path, tamper_spec=True)
    state, _ = q.collect()
    assert state == "completed"
    qc = q.stored().quota_classification
    assert qc == {"paused": False, "refusal": "spec_unverified",
                  "classifier_version": quota_detect.CLASSIFIER_VERSION}


def test_collect_quota_refusal_malformed_profile_in_valid_spec(tmp_path, monkeypatch):
    # a malformed profile inside a digest-valid spec is spec_unverified too (pass-4 S-F9)
    q = _QuotaCollect(tmp_path, profile={"session_policy": "weird", "mutating": False})
    monkeypatch.setattr(supervisor, "CALIBRATED_CLASSIFIERS", _CALIB)
    state, _ = q.collect()
    assert state == "completed"
    qc = q.stored().quota_classification
    assert qc["refusal"] == "spec_unverified"
    assert qc["verdict"] is True  # full classification present — evidence self-describes


def test_collect_quota_refusal_effectful_sentinel(tmp_path, monkeypatch):
    # an availability-status sentinel that ATTESTED a model is effectful — never resumed
    q = _QuotaCollect(tmp_path, actual_model="m")
    monkeypatch.setattr(supervisor, "CALIBRATED_CLASSIFIERS", _CALIB)
    state, _ = q.collect()
    assert state == "completed"
    assert q.stored().quota_classification["refusal"] == "effectful_sentinel"


def test_collect_quota_kill_unverified_residue_permit_retained(tmp_path, monkeypatch):
    q = _QuotaCollect(tmp_path)
    monkeypatch.setattr(supervisor, "CALIBRATED_CLASSIFIERS", _CALIB)
    monkeypatch.setattr(q.sup, "_kill_job", lambda record, **kw: False)
    state, _ = q.collect()
    assert state == "completed_with_residue"
    qc = q.stored().quota_classification
    assert qc["paused"] is False and qc["refusal"] == "kill_unverified"
    assert q.permit.exists()  # permit RETAINED until death is confirmed


def test_collect_quota_oversized_envelope_refuses_and_retains_live_permit(tmp_path, monkeypatch):
    big = '{"session_id": "sess-1", "pad": "' + "x" * (256 * 1024) + '"}'
    q = _QuotaCollect(tmp_path, envelope=big)
    monkeypatch.setattr(supervisor, "CALIBRATED_CLASSIFIERS", _CALIB)
    monkeypatch.setattr(q.sup, "_kill_job", lambda record, **kw: False)  # writer may be live
    state, _ = q.collect()
    assert state == "completed_with_residue"
    qc = q.stored().quota_classification
    assert qc["envelope_error"] == "oversized"
    assert qc["paused"] is False
    assert q.permit.exists()  # a live permit is never released on an envelope_error


def test_collect_evidence_reads_after_kill(tmp_path, monkeypatch):
    # S-F7 evidence-snapshot ordering: kill-verified precedes stderr/envelope reads
    q = _QuotaCollect(tmp_path)
    calls = []
    orig_kill, orig_stderr, orig_env = q.sup._kill_job, q.sup._read_stderr, q.sup._envelope_meta
    monkeypatch.setattr(q.sup, "_kill_job",
                        lambda r, **kw: (calls.append("kill"), orig_kill(r, **kw))[1])
    monkeypatch.setattr(q.sup, "_read_stderr",
                        lambda r: (calls.append("stderr"), orig_stderr(r))[1])
    monkeypatch.setattr(q.sup, "_envelope_meta",
                        lambda r: (calls.append("envelope"), orig_env(r))[1])
    q.collect()
    assert calls and calls[0] == "kill"
    assert {"stderr", "envelope"} <= set(calls[1:])


def test_collect_symlinked_stderr_is_unreadable_irregular(tmp_path):
    # readers are O_NOFOLLOW + regular-file + fstat — a symlink never classifies
    q = _QuotaCollect(tmp_path, stderr=None)
    target = tmp_path / "target.txt"
    target.write_text(QUOTA_STDERR, encoding="utf-8")
    (q.cap / "stderr.txt").symlink_to(target)
    state, _ = q.collect()
    assert state == "completed"
    qc = q.stored().quota_classification
    assert qc["verdict"] is False
    assert qc["read_error"] == "unreadable: irregular"


def test_collect_negative_stderr_persists_evidence(tmp_path):
    # invocation-triggered persistence: a NEGATIVE claude nonzero collect still records
    q = _QuotaCollect(
        tmp_path, stderr="OAuth token has expired. Please run /login to authenticate again.")
    state, _ = q.collect()
    assert state == "completed"
    qc = q.stored().quota_classification
    assert qc["verdict"] is False and qc["paused"] is False
    assert "refusal" not in qc  # ordinary negative — evidence self-describes
    assert qc["read_error"] is None
    assert not q.permit.exists()


def test_collect_non_claude_nonzero_skips_classifier(tmp_path):
    q = _QuotaCollect(tmp_path, engine="codex")
    state, _ = q.collect()
    assert state == "completed"
    assert q.stored().quota_classification is None


def test_collect_unknown_envelope_subtype_hashed(tmp_path):
    q = _QuotaCollect(
        tmp_path, envelope='{"session_id": "sess-1", "subtype": "totally_new_thing"}')
    q.collect()
    qc = q.stored().quota_classification
    assert qc["envelope_subtype"] == "unknown"
    assert qc["envelope_subtype_sha256"] == _hashlib.sha256(b"totally_new_thing").hexdigest()


def test_mark_quota_paused_injects_classification_evidence(tmp_path):
    # pass-2 F9: the injected writer records {"injected": true, "paused": true} via _finish
    reg = JobRegistry(str(tmp_path / "reg"))
    identity = WorktreeIdentity(run_id="r1", seat="build", attempt=1)
    sup = TmuxSupervisor(snapshot=None, quota=None, capture_root=str(tmp_path / "cap"),
                         registry_root=str(tmp_path / "reg"), registry=reg, run=_dead_run)
    rec = _JR(identity=identity, session_name=session_name(identity), run_socket="s",
              pane_pid=1, pane_pgid=1, provider_pgid=None, pane_start_time="0",
              worktree_path="w", worktree_base_sha="b", worktree_root="r",
              worktree_gitdir="g", worktree_repo="rp", capture_dir=str(tmp_path / "cd"),
              attempt_id="a", permit_ref="unbounded", command_digest="sha256:x",
              provider_session_id=None, provider_exit_code=None, resume_attempts=0,
              state="exited_no_sentinel", created_at=0.0, quarantine_reason=None)
    reg.upsert(rec)
    got = sup.mark_quota_paused(identity, provider_session_id="sess-1")
    assert got.state == "quota_paused"
    assert got.quota_classification == {"injected": True, "paused": True}
    assert reg.get(identity).quota_classification == {"injected": True, "paused": True}


def test_relaunch_carries_quota_classification(tmp_path, monkeypatch):
    # SR2: the relaunched record inherits the classification evidence
    q = _QuotaCollect(tmp_path)
    monkeypatch.setattr(supervisor, "CALIBRATED_CLASSIFIERS", _CALIB)
    state, _ = q.collect()
    assert state == "quota_paused"
    paused = q.stored()
    captured = {}

    def fake_launch(seat, prompt, **kw):
        captured.update(kw)
        return paused

    monkeypatch.setattr(q.sup, "launch", fake_launch)
    q.sup._relaunch(paused)
    assert captured["quota_classification"] == paused.quota_classification
    assert captured["resume_session_id"] == "sess-1"


@tmux_required
def test_launch_accepts_quota_classification_passthrough(env_factory):
    env = env_factory(mode="ok")
    rec = env.launch(quota_classification={"carried": True})
    try:
        assert rec.quota_classification == {"carried": True}
        assert env.registry.get(env.identity).quota_classification == {"carried": True}
    finally:
        env.sup.cancel(rec)


# ---- #558 AC2: supervised-path cap threading (SR1 spec round-trip + S-F6) -----

def _manifested_snapshot(timeout_s=7, budget=10.0):
    return routing.RoutingSnapshot.from_table({
        "schema_version": "1",
        "pools": {"claude": {"concurrency": 2}},
        "seats": {"build": {
            "manifest": {"session_policy": "fresh", "tool_grants": ["read"],
                         "effort": "medium", "confinement": {"anthropic": "hooks"},
                         "bounds": {"timeout_s": timeout_s, "max_budget_usd": budget}},
            "primary": {"model": "claude-sonnet-5", "lane": _lane()}, "chain": []}},
        "forbidden_combinations": [],
    })


@tmux_required
def test_supervised_launch_min_timeout_and_max_tokens_round_trip(env_factory):
    """S-F6: the tighter manifest bound wins in the pane spec (today the caller timeout
    is written raw); SR1: profile.max_tokens survives the spec write site."""
    env = env_factory(mode="ok")
    env.sup._snapshot = _manifested_snapshot(timeout_s=7)
    prof = contract.LaunchProfile(session_policy="fresh", tool_grants=("read",),
                                  max_tokens=512)
    object.__setattr__(prof, "effective_grants", ("read",))
    rec = env.launch(timeout=9999.0, profile=prof)
    try:
        spec = json.loads(Path(env.tmp / "reg" / "specs" / f"{rec.session_name}.json").read_text())
        assert spec["request"]["timeout"] == 7.0   # min(caller 9999, bounds 7)
        assert spec["request"]["profile"]["max_tokens"] == 512  # SR1 write site
        # SR1 read site: pane_runner reconstructs the field
        from phase_executor import pane_runner as _pr
        assert _pr._profile_from_spec(spec["request"]["profile"]).max_tokens == 512
    finally:
        env.sup.cancel(rec)


def test_relaunch_profile_rebuild_preserves_max_tokens(tmp_path, monkeypatch):
    # SR1 third site: _relaunch's profile rebuild carries max_tokens
    q = _QuotaCollect(tmp_path, profile=dict(_DEFAULT_PROFILE, max_tokens=512))
    monkeypatch.setattr(supervisor, "CALIBRATED_CLASSIFIERS", _CALIB)
    state, _ = q.collect()
    assert state == "quota_paused"
    paused = q.stored()
    captured = {}

    def fake_launch(seat, prompt, **kw):
        captured.update(kw)
        return paused

    monkeypatch.setattr(q.sup, "launch", fake_launch)
    q.sup._relaunch(paused)
    assert captured["profile"].max_tokens == 512


# ---- 8a security wave (M1/M2): envelope unicode + session-id bounds -----------

def test_collect_surrogate_subtype_never_crashes(tmp_path):
    # a lone-surrogate JSON escape in the subtype must become "unknown"+sha evidence,
    # never a UnicodeEncodeError aborting collection after the kill
    q = _QuotaCollect(tmp_path,
                      envelope='{"session_id": "sess-1", "subtype": "\\ud800bad"}')
    state, _ = q.collect()
    assert state == "completed"
    qc = q.stored().quota_classification
    assert qc["envelope_subtype"] == "unknown"
    assert isinstance(qc["envelope_subtype_sha256"], str) and qc["envelope_subtype_sha256"]


def test_collect_oversized_session_id_not_persisted(tmp_path, monkeypatch):
    # a session id beyond the provider's plausible bound is refused as evidence,
    # never persisted into jobs.json / a future --resume argv
    big_sid = "s" * 300
    q = _QuotaCollect(tmp_path,
                      envelope='{"session_id": "' + big_sid + '", "subtype": "success"}')
    monkeypatch.setattr(supervisor, "CALIBRATED_CLASSIFIERS", _CALIB)
    state, _ = q.collect()
    assert state == "completed"  # no resumable session -> refused, evidence persisted
    qc = q.stored().quota_classification
    assert qc["refusal"] == "no_resumable_session"
    assert q.stored().provider_session_id is None
