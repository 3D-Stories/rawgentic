"""#638 (epic #635 C2) — TmuxSupervisor's backend RESOLVER: which TerminalBackend a call
concerns is resolved from `record.terminal_backend` (never a single fixed `self._backend`),
so a mixed-backend run (some jobs tmux, some herdr) recovers/reaps/kills correctly. Step-4
review Finding #1 (High): the original design under-scoped this — `launch()`/`_live()`/
`_kill_job()`/`reap()` all needed this, not just the three construction call sites.

Every test here uses STUB backends (never a real tmux/herdr subprocess) — `launch()`'s
success path uses THIS TEST PROCESS's own pid as the fake `pane_pid` (a well-established
idiom in this file's siblings, e.g. test_supervisor_recover.py) so `os.getpgid`/
`_proc_start_time` read a genuinely-alive process without spawning anything.
"""
from __future__ import annotations

import os
import subprocess
import uuid

import pytest

from phase_executor import routing
from phase_executor.quota import QuotaCoordinator
from phase_executor.registry import JobRegistry, JobRecord
from phase_executor.supervisor import SupervisorError, TmuxSupervisor
from phase_executor.worktree import WorktreeHandle, WorktreeIdentity


def _lane():
    return {"provider": "anthropic", "transport": "native",
            "auth_mode": "subscription_oauth", "credential_ref": None, "pool": "claude"}


def _snapshot(concurrency=2):
    return routing.RoutingSnapshot.from_table({
        "schema_version": "1",
        "pools": {"claude": {"concurrency": concurrency}},
        "seats": {"build": {"primary": {"model": "claude-sonnet-5", "lane": _lane()}, "chain": []}},
        "forbidden_combinations": [],
    })


class StubBackend:
    """Records every call; `new_session`/`pane_pid` return a controllable, always-successful
    result by default (THIS test process's own pid, so downstream /proc reads succeed)."""

    def __init__(self, name):
        self.name = name
        self.calls = []

    def resolve_endpoint(self, run_id):
        self.calls.append(("resolve_endpoint", run_id))
        return f"{self.name}-endpoint"

    def preflight(self, endpoint):
        self.calls.append(("preflight", endpoint))
        from phase_executor.supervisor import PreflightResult  # noqa: PLC0415
        return PreflightResult(True, "")

    def new_session(self, endpoint, name, cwd, argv, timeout=30):
        self.calls.append(("new_session", endpoint, name, cwd, tuple(argv)))
        return subprocess.CompletedProcess([], 0, "", "")

    def pane_pid(self, endpoint, name, timeout=30):
        self.calls.append(("pane_pid", endpoint, name))
        return subprocess.CompletedProcess([], 0, str(os.getpid()), "")

    def has_session(self, endpoint, name, timeout=30):
        self.calls.append(("has_session", endpoint, name))
        return subprocess.CompletedProcess([], 0, "", "")

    def list_sessions(self, endpoint, timeout=30):
        self.calls.append(("list_sessions", endpoint))
        return subprocess.CompletedProcess([], 0, "", "")

    def kill_session(self, endpoint, name, timeout=30):
        self.calls.append(("kill_session", endpoint, name))
        return subprocess.CompletedProcess([], 0, "", "")

    def teardown_endpoint(self, endpoint, timeout=30):
        self.calls.append(("teardown_endpoint", endpoint))
        return subprocess.CompletedProcess([], 0, "", "")


def _sup(tmp_path, *, herdr=None, tmux=None):
    quota = QuotaCoordinator(str(tmp_path / "quota"), {"claude": 2})
    registry = JobRegistry(str(tmp_path / "reg"))
    return TmuxSupervisor(
        snapshot=_snapshot(), quota=quota, capture_root=str(tmp_path / "cap"),
        registry_root=str(tmp_path / "reg"), registry=registry,
        backend=tmux or StubBackend("tmux"), herdr_backend=herdr)


def _handle(tmp_path, identity):
    wt = tmp_path / "wt"
    wt.mkdir(exist_ok=True)
    return WorktreeHandle(path=str(wt), identity=identity, base_sha="deadbeef",
                          root=str(tmp_path), gitdir=str(tmp_path / "g"), repo=str(tmp_path))


def _identity():
    return WorktreeIdentity(run_id=f"r{uuid.uuid4().hex[:6]}", seat="build", attempt=1)


# ---- _resolve_backend --------------------------------------------------------

def test_resolve_backend_none_and_tmux_both_resolve_to_tmux_slot(tmp_path):
    tmux = StubBackend("tmux")
    sup = _sup(tmp_path, tmux=tmux)
    assert sup._resolve_backend(None) is tmux  # noqa: SLF001
    assert sup._resolve_backend("tmux") is tmux  # noqa: SLF001


def test_resolve_backend_herdr_resolves_to_herdr_slot(tmp_path):
    herdr = StubBackend("herdr")
    sup = _sup(tmp_path, herdr=herdr)
    assert sup._resolve_backend("herdr") is herdr  # noqa: SLF001


def test_resolve_backend_herdr_unconfigured_refuses_loud(tmp_path):
    sup = _sup(tmp_path)  # no herdr_backend
    with pytest.raises(SupervisorError, match="herdr"):
        sup._resolve_backend("herdr")  # noqa: SLF001


# ---- launch() routes to the resolved backend + stamps the record -----------

def test_launch_default_uses_tmux_and_stamps_tmux(tmp_path):
    tmux = StubBackend("tmux")
    sup = _sup(tmp_path, tmux=tmux)
    identity = _identity()
    rec = sup.launch("build", "hello", identity=identity, handle=_handle(tmp_path, identity))
    assert rec.terminal_backend == "tmux"
    assert any(c[0] == "new_session" for c in tmux.calls)


def test_launch_herdr_uses_herdr_backend_and_stamps_herdr(tmp_path):
    tmux = StubBackend("tmux")
    herdr = StubBackend("herdr")
    sup = _sup(tmp_path, tmux=tmux, herdr=herdr)
    identity = _identity()
    rec = sup.launch("build", "hello", identity=identity, handle=_handle(tmp_path, identity),
                     terminal_backend="herdr")
    assert rec.terminal_backend == "herdr"
    assert any(c[0] == "new_session" for c in herdr.calls)
    assert not any(c[0] == "new_session" for c in tmux.calls)  # never touched the tmux slot


def test_launch_herdr_unconfigured_refuses_before_any_spawn(tmp_path):
    tmux = StubBackend("tmux")
    sup = _sup(tmp_path, tmux=tmux)  # no herdr_backend
    identity = _identity()
    with pytest.raises(SupervisorError, match="herdr"):
        sup.launch("build", "hello", identity=identity, handle=_handle(tmp_path, identity),
                  terminal_backend="herdr")
    assert tmux.calls == []  # refused before touching ANY backend -- no orphaned pane


# ---- _live resolves per-record --------------------------------------------

def test_live_resolves_backend_from_record(tmp_path):
    tmux = StubBackend("tmux")
    herdr = StubBackend("herdr")
    sup = _sup(tmp_path, tmux=tmux, herdr=herdr)
    identity = _identity()
    tmux_rec = JobRecord(
        identity=identity, session_name="rg-t", run_socket="tmux-endpoint",
        pane_pid=os.getpid(), pane_pgid=os.getpid(), provider_pgid=None, pane_start_time="0",
        worktree_path="/wt", worktree_base_sha="0" * 40, worktree_root="/wt", worktree_gitdir="/g",
        worktree_repo="/r", capture_dir="/cap", attempt_id="0-a", permit_ref="p", command_digest="d",
        provider_session_id=None, provider_exit_code=None, resume_attempts=0, state="running",
        created_at=1.0, quarantine_reason=None, terminal_backend="tmux")
    herdr_rec = JobRecord(**{**tmux_rec.__dict__, "session_name": "rg-h", "terminal_backend": "herdr"})
    sup._live(tmux_rec)  # noqa: SLF001
    sup._live(herdr_rec)  # noqa: SLF001
    assert any(c[0] == "has_session" for c in tmux.calls)
    assert any(c[0] == "has_session" for c in herdr.calls)


# ---- _kill_job resolves per-record for its bookkeeping kill_session call ----

def _dead_pid_record(identity, *, terminal_backend):
    # a pane_pid that certainly does not exist -> _kill_job's identity check fails closed,
    # sends NO real signals, and reaches the bookkeeping backend.kill_session call directly
    # (mirrors this file's siblings' pid=1 / reused-pid tests -- no real process needed).
    return JobRecord(
        identity=identity, session_name="rg-x", run_socket="endpoint",
        pane_pid=999999999, pane_pgid=999999999, provider_pgid=None, pane_start_time="0",
        worktree_path="/wt", worktree_base_sha="0" * 40, worktree_root="/wt", worktree_gitdir="/g",
        worktree_repo="/r", capture_dir="/cap", attempt_id="0-a", permit_ref="p", command_digest="d",
        provider_session_id=None, provider_exit_code=None, resume_attempts=0, state="running",
        created_at=1.0, quarantine_reason=None, terminal_backend=terminal_backend)


def test_kill_job_resolves_backend_from_record(tmp_path):
    tmux = StubBackend("tmux")
    herdr = StubBackend("herdr")
    sup = _sup(tmp_path, tmux=tmux, herdr=herdr)
    identity = _identity()
    assert sup._kill_job(_dead_pid_record(identity, terminal_backend="tmux")) is True  # noqa: SLF001
    assert sup._kill_job(_dead_pid_record(identity, terminal_backend="herdr")) is True  # noqa: SLF001
    assert any(c[0] == "kill_session" for c in tmux.calls)
    assert any(c[0] == "kill_session" for c in herdr.calls)


def test_kill_job_tolerates_unresolvable_backend_on_already_dead_process(tmp_path):
    # Self-review catch: the bookkeeping kill_session call is POST-MORTEM only (the process
    # is already verified dead above it) -- a backend-resolution failure here (a
    # misconfigured supervisor missing herdr_backend for a herdr-tagged record) must not
    # un-verify a real, already-confirmed kill. _kill_job must still return True.
    tmux = StubBackend("tmux")
    sup = _sup(tmp_path, tmux=tmux)  # no herdr_backend configured
    identity = _identity()
    rec = _dead_pid_record(identity, terminal_backend="herdr")
    assert sup._kill_job(rec) is True  # noqa: SLF001 -- never raises, never returns False


# ---- reap()'s list_sessions union across mixed backends --------------------

def test_reap_list_sessions_unions_across_mixed_backends(tmp_path):
    tmux = StubBackend("tmux")
    herdr = StubBackend("herdr")
    sup = _sup(tmp_path, tmux=tmux, herdr=herdr)
    run_id = f"r{uuid.uuid4().hex[:6]}"
    # DIFFERENT identities (distinct attempt) sharing one run_id: JobRegistry.upsert keys
    # storage by session_name(identity), not the record's own session_name field -- two
    # records under the SAME identity would silently overwrite each other.
    tmux_identity = WorktreeIdentity(run_id=run_id, seat="build", attempt=1)
    herdr_identity = WorktreeIdentity(run_id=run_id, seat="build", attempt=2)
    tmux_rec = _dead_pid_record(tmux_identity, terminal_backend="tmux")
    herdr_rec = JobRecord(**{**_dead_pid_record(herdr_identity, terminal_backend="herdr").__dict__,
                            "session_name": "rg-h2"})
    sup._registry.upsert(tmux_rec)  # noqa: SLF001
    sup._registry.upsert(herdr_rec)  # noqa: SLF001
    sup.reap(run_id)
    assert any(c[0] == "list_sessions" for c in tmux.calls)
    assert any(c[0] == "list_sessions" for c in herdr.calls)


def test_reap_excludes_unresolvable_backend_records_from_every_tier(tmp_path):
    # Step-11 finding (round 2): the EARLIER "tolerate it" fix here was itself wrong. A
    # record whose backend cannot be resolved must be excluded from the WHOLE sweep, not
    # merely have its list_sessions/kill_session bookkeeping skipped -- skipping only the
    # listing left a genuinely-alive record outside live_fresh, which reap_plan's REAL
    # OS-level dead_fn would then (correctly, on its own terms) call "not dead" -- routing
    # it to kill_tree and killing a healthy process. Excluding it upstream means NO action
    # (destructive or not) is taken on it this cycle -- it appears in NO tier.
    tmux = StubBackend("tmux")
    sup = _sup(tmp_path, tmux=tmux)  # no herdr_backend configured
    identity = WorktreeIdentity(run_id=f"r{uuid.uuid4().hex[:6]}", seat="build", attempt=1)
    rec = _dead_pid_record(identity, terminal_backend="herdr")
    sup._registry.upsert(rec)  # noqa: SLF001
    plan = sup.reap(rec.identity.run_id, clean_fn=lambda _r: True)
    assert rec not in plan.kill_session
    assert rec not in plan.kill_tree
    assert rec not in plan.quarantine
    assert rec not in plan.retain_worktree
    assert rec not in plan.keep
    assert tmux.calls == []  # never even touched the resolvable (tmux) backend for this record


def test_recover_excludes_unresolvable_backend_records(tmp_path):
    # The same class of bug also existed in recover() (found by inspection, not by the
    # reviewer) -- self._live(record) raised uncaught, which would crash recovery for
    # EVERY OTHER record in the run, not just the one with the unresolvable backend.
    tmux = StubBackend("tmux")
    sup = _sup(tmp_path, tmux=tmux)  # no herdr_backend configured
    identity = WorktreeIdentity(run_id=f"r{uuid.uuid4().hex[:6]}", seat="build", attempt=1)
    unresolvable = _dead_pid_record(identity, terminal_backend="herdr")
    sup._registry.upsert(unresolvable)  # noqa: SLF001

    def gate(*, record, correlation_id, recovered_from):  # pragma: no cover - never reached
        raise AssertionError("should never be reached for an excluded record")

    actions = sup.recover(identity.run_id, dispatch_gate=gate)  # must not raise
    assert actions == []


# ---- kill_server tears down every configured backend ------------------------

def test_kill_server_tears_down_both_backends_when_herdr_configured(tmp_path):
    tmux = StubBackend("tmux")
    herdr = StubBackend("herdr")
    sup = _sup(tmp_path, tmux=tmux, herdr=herdr)
    sup.kill_server("run1")
    assert any(c[0] == "teardown_endpoint" for c in tmux.calls)
    assert any(c[0] == "teardown_endpoint" for c in herdr.calls)


def test_kill_server_skips_herdr_teardown_when_unconfigured(tmp_path):
    tmux = StubBackend("tmux")
    sup = _sup(tmp_path, tmux=tmux)  # no herdr_backend
    sup.kill_server("run1")  # must not raise despite no herdr configured
    assert any(c[0] == "teardown_endpoint" for c in tmux.calls)


# ---- preflight / resolve_socket terminal_backend param ----------------------

def test_preflight_terminal_backend_param_delegates_to_herdr(tmp_path):
    herdr = StubBackend("herdr")
    sup = _sup(tmp_path, herdr=herdr)
    result = sup.preflight("endpoint", terminal_backend="herdr")
    assert result.supported is True
    assert any(c[0] == "preflight" for c in herdr.calls)


def test_resolve_socket_terminal_backend_param_delegates_to_herdr(tmp_path):
    herdr = StubBackend("herdr")
    sup = _sup(tmp_path, herdr=herdr)
    endpoint = sup.resolve_socket("run1", terminal_backend="herdr")
    assert endpoint == "herdr-endpoint"
