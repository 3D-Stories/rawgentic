"""#467 D-12 — recover-adopt re-establishes the quota permit under the ADOPTING orchestrator's
pid, CAS/serialized so an ownership race during recover-adopt cannot over-admit past the ceiling.

Two layers:
- pure QuotaCoordinator.reestablish_permit tests (the CAS mechanism + fail-closed refusal) —
  no tmux, distinct adopter pids simulated via monkeypatch, real fcntl flock serializing;
- real-tmux supervisor.recover() wiring tests (adopt re-permits; quarantine NEVER re-permits) —
  skipped when tmux is absent.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from phase_executor import quota as qmod
from phase_executor import routing
from phase_executor.quota import QuotaCoordinator, QuotaTimeout
from phase_executor.registry import JobRegistry
from phase_executor.supervisor import TmuxSupervisor
from phase_executor.worktree import WorktreeHandle, WorktreeIdentity

REPO = Path(__file__).resolve().parents[2]
PKG_SRC = REPO / "phase_executor" / "src"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

HAS_TMUX = shutil.which("tmux") is not None
tmux_required = pytest.mark.skipif(not HAS_TMUX, reason="tmux not installed")


def _dead_pid() -> int:
    """A pid that is now dead (a reaped child) — the launching orchestrator, post-exit."""
    p = subprocess.Popen(["true"])  # noqa: S607,S603 — trivial reap-to-get-a-dead-pid
    p.wait()
    return p.pid


# ---------------------------------------------------------------------------
# pure CAS mechanism (contracts 2 + 4) — no tmux
# ---------------------------------------------------------------------------


def _seed_token(pool_dir: Path, pid: int, name: str = "abc") -> Path:
    pool_dir.mkdir(parents=True, exist_ok=True)
    token = pool_dir / f"permit-{pid}-{name}"
    token.write_text(f"{pid}\n0\n", encoding="utf-8")
    return token


def test_reestablish_rekeys_dead_launcher_to_live_owner(tmp_path):
    """The D-12 core: a permit token naming the EXITED launcher is re-keyed in place to the
    adopting live pid, so live_permits keeps counting it (no stale-reap, no over-admit)."""
    qc = QuotaCoordinator(str(tmp_path), {"claude": 1})
    pool_dir = tmp_path / "claude" / "default"
    _seed_token(pool_dir, _dead_pid())
    # before: the dead launcher owns it -> a reap would free the slot (the D-12 bug)
    assert qc.live_permits("claude") == 0
    # re-seed (live_permits reaped it) and re-establish under this live process
    token = _seed_token(pool_dir, _dead_pid())
    assert qc.reestablish_permit("claude", str(token)) is True
    assert int(token.read_text().splitlines()[0].strip()) == os.getpid()
    assert qc.live_permits("claude") == 1  # survives a reap now — the adopted job still counts


def test_two_adopters_race_exactly_one_wins_ceiling_holds(tmp_path, monkeypatch):
    """CAS: two orchestrators adopt the SAME live job. The pool flock serializes them; the
    first (finding the launcher pid dead) re-keys the one token and wins, the second sees a
    LIVE owner that is not itself and stands down. One token, ceiling of 1 holds — no
    double-admit."""
    qc = QuotaCoordinator(str(tmp_path), {"claude": 1})
    pool_dir = tmp_path / "claude" / "default"
    launcher = 111  # the exited launching orchestrator
    token = _seed_token(pool_dir, launcher)
    alive = {222, 333}  # two live adopter pids; the launcher (111) is DEAD
    monkeypatch.setattr(qmod, "_pid_alive", lambda pid: pid in alive)

    monkeypatch.setattr(qmod.os, "getpid", lambda: 222)  # adopter A takes the lock first
    assert qc.reestablish_permit("claude", str(token)) is True

    monkeypatch.setattr(qmod.os, "getpid", lambda: 333)  # adopter B races for the same job
    assert qc.reestablish_permit("claude", str(token)) is False  # stands down — already owned

    tokens = list(pool_dir.glob("permit-*"))
    assert len(tokens) == 1  # never a second token: the ceiling holds
    assert tokens[0].read_text().splitlines()[0].strip() == "222"  # the winner owns it
    assert qc.live_permits("claude") == 1  # exactly one live permit for the adopted job


def test_reestablish_idempotent_when_already_owned(tmp_path):
    """Re-adopting a permit this process already owns is a no-op success (no second token)."""
    qc = QuotaCoordinator(str(tmp_path), {"claude": 1})
    pool_dir = tmp_path / "claude" / "default"
    token = _seed_token(pool_dir, os.getpid())
    assert qc.reestablish_permit("claude", str(token)) is True
    assert len(list(pool_dir.glob("permit-*"))) == 1


def test_reestablish_refuses_when_slot_gone_and_pool_full(tmp_path, monkeypatch):
    """Fail-closed (contract 4): the adopted job's token was already reaped and another live
    job now fills the ceiling — re-taking a slot would over-admit, so refuse (QuotaTimeout)."""
    qc = QuotaCoordinator(str(tmp_path), {"claude": 1})
    pool_dir = tmp_path / "claude" / "default"
    other = _seed_token(pool_dir, 999, name="live")  # a different live job fills the pool
    monkeypatch.setattr(qmod, "_pid_alive", lambda pid: pid == 999)
    gone = pool_dir / "permit-111-gone"  # the adopted job's token no longer exists
    with pytest.raises(QuotaTimeout):
        qc.reestablish_permit("claude", str(gone))
    assert list(pool_dir.glob("permit-*")) == [other]  # ceiling never exceeded


def test_reestablish_reclaims_when_slot_gone_but_pool_has_room(tmp_path, monkeypatch):
    """Slot gone but the pool is under the ceiling -> reclaim it (a live adopted job must count)."""
    qc = QuotaCoordinator(str(tmp_path), {"claude": 2})
    pool_dir = tmp_path / "claude" / "default"
    other = _seed_token(pool_dir, 999, name="live")
    monkeypatch.setattr(qmod, "_pid_alive", lambda pid: pid in (999, os.getpid()))
    gone = pool_dir / "permit-111-gone"
    assert qc.reestablish_permit("claude", str(gone)) is True
    assert gone.exists() and int(gone.read_text().splitlines()[0].strip()) == os.getpid()
    assert len(list(pool_dir.glob("permit-*"))) == 2  # both live jobs counted, ceiling ok
    assert other.exists()


def test_reestablish_unbounded_pool_is_noop(tmp_path):
    """An unbounded-pool job carries no token — re-establishment is a trivial success."""
    qc = QuotaCoordinator(str(tmp_path), {})  # no limit configured for 'claude'
    assert qc.reestablish_permit("claude", "unbounded") is True
    assert qc.reestablish_permit("claude", str(tmp_path / "claude" / "d" / "permit-x")) is True


# ---------------------------------------------------------------------------
# real-tmux supervisor.recover() wiring (contracts 1 + 3)
# ---------------------------------------------------------------------------


def _lane(pool="claude"):
    return {"provider": "anthropic", "transport": "native",
            "auth_mode": "subscription_oauth", "credential_ref": None, "pool": pool}


def _snapshot(concurrency=1):
    return routing.RoutingSnapshot.from_table({
        "schema_version": "1",
        "pools": {"claude": {"concurrency": concurrency}},
        "seats": {"build": {"primary": {"model": "claude-sonnet-5", "lane": _lane()}, "chain": []}},
        "forbidden_combinations": [],
    })


class _FakeWorktreeManager:
    def __init__(self):
        self.finalized = []

    def finalize(self, handle, observation_status, *, live_identities=()):
        self.finalized.append((handle.path, observation_status))
        return None


class _Env:
    def __init__(self, tmp_path, sock_scratch, mode="ok", concurrency=1):
        self.tmp = tmp_path
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.registry = JobRegistry(str(tmp_path / "reg"))
        self.quota = QuotaCoordinator(str(tmp_path / "quota"), {"claude": concurrency})
        self.identity = WorktreeIdentity(run_id=f"r{uuid.uuid4().hex[:6]}", seat="build", attempt=1)
        wt = tmp_path / "wt"
        wt.mkdir()
        self.handle = WorktreeHandle(path=str(wt), identity=self.identity, base_sha="deadbeef",
                                     root=str(tmp_path), gitdir=str(tmp_path / "g"),
                                     repo=str(tmp_path))
        self.manager = _FakeWorktreeManager()
        self._sock_scratch = sock_scratch
        self._concurrency = concurrency
        self.sup = self._make_sup(mode)

    def _make_sup(self, mode):
        env = {"PYTHONPATH": f"{PKG_SRC}:{FIXTURES}",
               "RAWGENTIC_PANE_ADAPTER": "stub_pane_adapter",
               "RAWGENTIC_STUB_MODE": mode}
        return TmuxSupervisor(
            snapshot=_snapshot(self._concurrency), quota=self.quota,
            capture_root=str(self.tmp / "cap"), registry_root=str(self.tmp / "reg"),
            registry=self.registry,
            runtime_dir=str(self._sock_scratch / "run"), state_dir=str(self._sock_scratch / "state"),
            pane_env=env, worktree_manager=self.manager, allow_adapter_override=True)

    def launch(self):
        return self.sup.launch("build", "hello", identity=self.identity, handle=self.handle)

    def sup_with_mode(self, mode):
        """A SECOND supervisor over the same durable state — the post-compaction adopter."""
        return self._make_sup(mode)

    def cleanup(self):
        try:
            self.sup.kill_server(self.identity.run_id)
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture(name="env_factory")
def _env_factory(tmp_path):
    envs = []
    scratch = Path.home() / ".cache" / "rg-d12-tests" / uuid.uuid4().hex[:10]
    scratch.joinpath("run").mkdir(parents=True)

    def make(mode="ok", concurrency=1):
        e = _Env(tmp_path / uuid.uuid4().hex[:8], scratch, mode=mode, concurrency=concurrency)
        envs.append(e)
        return e

    yield make
    for e in envs:
        e.cleanup()
    shutil.rmtree(scratch, ignore_errors=True)


@tmux_required
def test_recover_adopt_reestablishes_permit_under_adopting_pid(env_factory):
    """Contract 1: after the launcher exits, a NEW orchestrator recover()-adopts the live job
    and re-keys its permit to the adopting pid, so the pool ceiling keeps counting it (no
    stale-reap, no over-admit across the recovery boundary)."""
    env = env_factory(mode="ok_then_sleep", concurrency=1)
    rec = env.launch()
    assert env.quota.live_permits("claude") == 1
    # simulate the LAUNCHING orchestrator having exited: its permit token names a dead pid
    token = Path(rec.permit_ref)
    token.write_text(f"{_dead_pid()}\n0\n", encoding="utf-8")
    try:
        sup2 = env.sup_with_mode("ok_then_sleep")
        actions = sup2.recover(env.identity.run_id)
        assert [a.action for a in actions] == ["adopt"]
        # the permit is re-keyed to the ADOPTING (live) pid — no stale-reap, ceiling holds
        assert int(token.read_text().splitlines()[0].strip()) == os.getpid()
        assert env.quota.live_permits("claude") == 1
    finally:
        env.sup.cancel(env.registry.get(env.identity))


@tmux_required
def test_recover_quarantine_never_repermits(env_factory):
    """Contract 3: an identity-mismatch recovery quarantines and RELEASES the permit — it never
    re-keys the token to a live pid (the adopted-vs-quarantined asymmetry)."""
    env = env_factory(mode="provider_sleep", concurrency=1)
    rec = env.launch()
    token = Path(rec.permit_ref)
    token.write_text(f"{_dead_pid()}\n0\n", encoding="utf-8")  # launcher exited
    # tamper the spec -> identity mismatch -> quarantine (never adopt, never re-permit)
    spec_path = Path(env.tmp / "reg" / "specs" / f"{rec.session_name}.json")
    spec_path.write_text(json.dumps({"tampered": True}), encoding="utf-8")
    sup2 = env.sup_with_mode("provider_sleep")
    actions = sup2.recover(env.identity.run_id)
    assert [a.action for a in actions] == ["quarantine"]
    assert env.registry.get(env.identity).state == "quarantined"
    # released, not re-keyed: the permit is gone and the pool is empty (asymmetry vs adopt)
    assert not token.exists()
    assert env.quota.live_permits("claude") == 0


def test_adopt_permit_oserror_quarantines_and_sweep_continues(env_factory, monkeypatch):
    """8a F4: an OSError from permit re-establishment is handled like QuotaTimeout — the record
    quarantines and recover() RETURNS (no propagation aborting the sweep; the relaunch arm's
    R1 contract, now symmetric)."""
    env = env_factory(mode="ok_then_sleep", concurrency=1)
    rec = env.launch()
    token = Path(rec.permit_ref)
    token.write_text(f"{_dead_pid()}\n0\n", encoding="utf-8")  # launcher exited
    sup2 = env.sup_with_mode("ok_then_sleep")
    monkeypatch.setattr(sup2, "_reestablish_adopt_permit",
                        lambda record: (_ for _ in ()).throw(OSError("disk full")))
    actions = sup2.recover(env.identity.run_id)  # must NOT raise (pre-fix: OSError propagated)
    assert [a.action for a in actions] == ["quarantine"]
