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
from phase_executor.supervisor import SupervisorError, TmuxSupervisor, resolve_backend
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

    def __init__(self, name, *, raise_has_session=False, raise_list_sessions=False,
                 list_sessions_returncode=0, raise_list_sessions_for_endpoints=frozenset(),
                 pane_pid_returncode=0, kill_session_returncode=0):
        self.name = name
        self.calls = []
        # #638 Step-11 finding (round 2): a PROPERLY CONFIGURED backend whose underlying
        # liveness/list check itself transiently fails must be distinguishable from
        # TmuxBackend's routine nonzero "no sessions on this socket" -- the former raises,
        # the latter returns a plain nonzero CompletedProcess (see terminal_backend.py).
        self._raise_has_session = raise_has_session
        self._raise_list_sessions = raise_list_sessions
        self._list_sessions_returncode = list_sessions_returncode
        # per-ENDPOINT failure, for the round-2-confirming-pass finding: one backend OBJECT
        # serving two DIFFERENT endpoints (e.g. two herdr workspaces under one run) must not
        # let a failure on one endpoint silently swallow the other's real records.
        self._raise_list_sessions_for_endpoints = raise_list_sessions_for_endpoints
        # Step-11 pass 5: drive the launch teardown path -- a failing pane_pid raises inside
        # launch(), and an UNCONFIRMED kill_session must then leave the quota permit HELD.
        self._pane_pid_returncode = pane_pid_returncode
        self._kill_session_returncode = kill_session_returncode

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
        if self._pane_pid_returncode != 0:
            return subprocess.CompletedProcess([], self._pane_pid_returncode, "", "pane_pid boom")
        return subprocess.CompletedProcess([], 0, str(os.getpid()), "")

    def has_session(self, endpoint, name, timeout=30):
        self.calls.append(("has_session", endpoint, name))
        if self._raise_has_session:
            raise RuntimeError("herdr pane list failed: daemon unreachable")
        return subprocess.CompletedProcess([], 0, "", "")

    def list_sessions(self, endpoint, timeout=30):
        self.calls.append(("list_sessions", endpoint))
        if self._raise_list_sessions or endpoint in self._raise_list_sessions_for_endpoints:
            raise RuntimeError("herdr pane list failed: daemon unreachable")
        return subprocess.CompletedProcess([], self._list_sessions_returncode, "", "")

    def kill_session(self, endpoint, name, timeout=30):
        self.calls.append(("kill_session", endpoint, name))
        return subprocess.CompletedProcess([], self._kill_session_returncode, "",
                                           "close failed" if self._kill_session_returncode else "")

    # -- tri-state surface (#638 Step-11 pass 7) -- derived from the raw knobs above so a
    # test's intent is expressed once. A raising probe is INDETERMINATE; a nonzero
    # kill_session models an UNCONFIRMED teardown (the stub's "close failed" is an
    # operational error, not an absence message); a nonzero list_sessions models tmux's
    # routine "no sessions on this socket", which is a RELIABLE empty enumeration.
    def probe_session(self, endpoint, name, timeout=30):
        from phase_executor.terminal_backend import Liveness  # noqa: PLC0415
        try:
            res = self.has_session(endpoint, name, timeout=timeout)
        except Exception:  # noqa: BLE001
            return Liveness.INDETERMINATE
        return Liveness.CONFIRMED_ALIVE if res.returncode == 0 else Liveness.CONFIRMED_GONE

    def close_session(self, endpoint, name, timeout=30):
        from phase_executor.terminal_backend import Liveness  # noqa: PLC0415
        try:
            res = self.kill_session(endpoint, name, timeout=timeout)
        except Exception:  # noqa: BLE001
            return Liveness.INDETERMINATE
        return Liveness.CONFIRMED_GONE if res.returncode == 0 else Liveness.INDETERMINATE

    def enumerate_sessions(self, endpoint, timeout=30):
        from phase_executor.terminal_backend import Liveness  # noqa: PLC0415
        try:
            res = self.list_sessions(endpoint, timeout=timeout)
        except Exception:  # noqa: BLE001
            return Liveness.INDETERMINATE, []
        names = [l.strip() for l in (res.stdout or "").splitlines() if l.strip()]
        return Liveness.CONFIRMED_ALIVE, names

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


# ---- the lifted module-level resolver (#647) --------------------------------

def test_lifted_resolve_backend_agrees_with_the_method(tmp_path):
    """#647: the read-only status surface CANNOT construct a TmuxSupervisor to reach the
    resolver — `__init__` does `self._registry = registry or JobRegistry(registry_root)`
    and JobRegistry's own `__init__` mkdir/chmods the root, a metadata write the AC-J3
    read-only surface must not perform. So the resolution RULE is lifted to module level
    and the method delegates to it: one rule, two callers, no second source of truth.
    """
    tmux, herdr = StubBackend("tmux"), StubBackend("herdr")
    sup = _sup(tmp_path, tmux=tmux, herdr=herdr)
    for tb in (None, "tmux", "herdr"):
        assert resolve_backend(tb, tmux=tmux, herdr=herdr) is sup._resolve_backend(tb)  # noqa: SLF001


def test_lifted_resolve_backend_herdr_absent_refuses_loud():
    """Same fail-loud contract as the method: a herdr record must never silently fall back
    to tmux, which would probe the WRONG runtime for a real live job."""
    with pytest.raises(SupervisorError, match="herdr"):
        resolve_backend("herdr", tmux=StubBackend("tmux"), herdr=None)


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


def test_live_raises_supervisor_error_when_has_session_raises(tmp_path):
    # #638 Step-11 finding (round 2): has_session() raising (a herdr list-command failure,
    # NOT a clean "not found") must translate to SupervisorError here, never read as
    # "definitively dead" -- that distinction is what lets recover()/reap() exclude the
    # record instead of misclassifying a possibly-live job.
    herdr = StubBackend("herdr", raise_has_session=True)
    sup = _sup(tmp_path, herdr=herdr)
    identity = _identity()
    rec = JobRecord(
        identity=identity, session_name="rg-h", run_socket="herdr-endpoint",
        pane_pid=os.getpid(), pane_pgid=os.getpid(), provider_pgid=None, pane_start_time="0",
        worktree_path="/wt", worktree_base_sha="0" * 40, worktree_root="/wt", worktree_gitdir="/g",
        worktree_repo="/r", capture_dir="/cap", attempt_id="0-a", permit_ref="p", command_digest="d",
        provider_session_id=None, provider_exit_code=None, resume_attempts=0, state="running",
        created_at=1.0, quarantine_reason=None, terminal_backend="herdr")
    with pytest.raises(SupervisorError):
        sup._live(rec)  # noqa: SLF001


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


def test_reap_tmux_no_sessions_return_is_not_excluded(tmp_path):
    # Regression guard: TmuxBackend's routine "no sessions on this socket" is a PLAIN
    # nonzero return (never raised) -- it must NOT be treated the same as a herdr genuine
    # list failure. Excluding on a bare nonzero broke the ordinary dead-job sweep (a real
    # tmux session that already exited closes its own socket entry, so list-sessions
    # legitimately returns nonzero for a completely normal dead job) -- this must still
    # reach dead_fn/kill_tree/retain_worktree as before, not be silently excluded.
    tmux = StubBackend("tmux", list_sessions_returncode=1)
    sup = _sup(tmp_path, tmux=tmux)
    identity = WorktreeIdentity(run_id=f"r{uuid.uuid4().hex[:6]}", seat="build", attempt=1)
    rec = _dead_pid_record(identity, terminal_backend="tmux")
    sup._registry.upsert(rec)  # noqa: SLF001
    plan = sup.reap(rec.identity.run_id, clean_fn=lambda _r: True)
    # a dead-pid record with no live session name still finds its way into a real tier
    # (never silently dropped from consideration the way an EXCLUDED record would be)
    assert (rec in plan.kill_tree or rec in plan.kill_session
            or rec in plan.quarantine or rec in plan.retain_worktree or rec in plan.keep)


def test_reap_excludes_records_when_list_sessions_raises_transiently(tmp_path):
    # #638 Step-11 finding (round 2): a CONFIGURED herdr backend whose list_sessions()
    # itself transiently fails (raises) must exclude its records from the WHOLE sweep --
    # dead_fn is an independent OS-level PID check, so a genuinely-alive record (real pid
    # of THIS test process) would otherwise be correctly reported "not dead" and routed to
    # kill_tree, killing a healthy process on a transient herdr failure.
    herdr = StubBackend("herdr", raise_list_sessions=True)
    sup = _sup(tmp_path, herdr=herdr)
    identity = WorktreeIdentity(run_id=f"r{uuid.uuid4().hex[:6]}", seat="build", attempt=1)
    rec = JobRecord(
        identity=identity, session_name="rg-h", run_socket="herdr-endpoint",
        pane_pid=os.getpid(), pane_pgid=os.getpid(), provider_pgid=None, pane_start_time="0",
        worktree_path="/wt", worktree_base_sha="0" * 40, worktree_root="/wt", worktree_gitdir="/g",
        worktree_repo="/r", capture_dir="/cap", attempt_id="0-a", permit_ref="p", command_digest="d",
        provider_session_id=None, provider_exit_code=None, resume_attempts=0, state="running",
        created_at=1.0, quarantine_reason=None, terminal_backend="herdr")
    sup._registry.upsert(rec)  # noqa: SLF001
    plan = sup.reap(rec.identity.run_id, clean_fn=lambda _r: True)
    assert rec not in plan.kill_tree
    assert rec not in plan.kill_session
    assert rec not in plan.quarantine
    assert rec not in plan.retain_worktree
    assert rec not in plan.keep


def test_reap_excludes_only_the_failing_endpoint_when_one_backend_serves_two(tmp_path):
    # Step-11 finding (round 2, confirming pass): `by_backend` was keyed on the backend
    # OBJECT alone, so two records sharing one herdr backend but DIFFERENT run_socket
    # endpoints (two workspaces under one run) silently shared only the FIRST endpoint
    # seen -- the second endpoint's sessions were never listed, and a genuinely-alive
    # record there could still reach kill_tree. Endpoint "w1" fails; endpoint "w2" is
    # healthy and must be listed and protected independently.
    herdr = StubBackend("herdr", raise_list_sessions_for_endpoints=frozenset({"w1"}))
    sup = _sup(tmp_path, herdr=herdr)
    run_id = f"r{uuid.uuid4().hex[:6]}"
    bad_identity = WorktreeIdentity(run_id=run_id, seat="build", attempt=1)
    good_identity = WorktreeIdentity(run_id=run_id, seat="build", attempt=2)
    bad_rec = JobRecord(**{**_dead_pid_record(bad_identity, terminal_backend="herdr").__dict__,
                          "session_name": "rg-w1", "run_socket": "w1"})
    good_rec = JobRecord(**{**_dead_pid_record(good_identity, terminal_backend="herdr").__dict__,
                            "session_name": "rg-w2", "run_socket": "w2"})
    sup._registry.upsert(bad_rec)  # noqa: SLF001
    sup._registry.upsert(good_rec)  # noqa: SLF001
    plan = sup.reap(run_id, clean_fn=lambda _r: True)
    # the failing endpoint's record is excluded from every tier (no destructive guess)
    assert bad_rec not in plan.kill_tree
    assert bad_rec not in plan.kill_session
    assert bad_rec not in plan.quarantine
    assert bad_rec not in plan.retain_worktree
    assert bad_rec not in plan.keep
    # the healthy endpoint's record was still queried and actually considered (landed in
    # a REAL tier, not silently dropped the way an excluded record would be) -- both are
    # confirmed-dead pids, so it lands in the ordinary dead-session cleanup tier
    assert any(c == ("list_sessions", "w2") for c in herdr.calls)
    assert good_rec in plan.kill_session


def test_recover_excludes_records_when_live_check_raises_transiently(tmp_path):
    # Same class of finding as above, in recover(): a CONFIGURED backend's transient
    # has_session() failure must exclude the record from this cycle, not crash recovery
    # for every other record nor read as "definitively dead".
    herdr = StubBackend("herdr", raise_has_session=True)
    sup = _sup(tmp_path, herdr=herdr)
    identity = WorktreeIdentity(run_id=f"r{uuid.uuid4().hex[:6]}", seat="build", attempt=1)
    rec = JobRecord(
        identity=identity, session_name="rg-h", run_socket="herdr-endpoint",
        pane_pid=os.getpid(), pane_pgid=os.getpid(), provider_pgid=None, pane_start_time="0",
        worktree_path="/wt", worktree_base_sha="0" * 40, worktree_root="/wt", worktree_gitdir="/g",
        worktree_repo="/r", capture_dir="/cap", attempt_id="0-a", permit_ref="p", command_digest="d",
        provider_session_id=None, provider_exit_code=None, resume_attempts=0, state="running",
        created_at=1.0, quarantine_reason=None, terminal_backend="herdr")
    sup._registry.upsert(rec)  # noqa: SLF001

    def gate(*, record, correlation_id, recovered_from):  # pragma: no cover - never reached
        raise AssertionError("should never be reached for an excluded record")

    actions = sup.recover(identity.run_id, dispatch_gate=gate)  # must not raise
    assert actions == []


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


# ---- Step-11 pass 5: an UNCONFIRMED launch teardown must not release the quota permit ----

class _FakeCM:
    """Minimal permit context-manager stand-in: records that __exit__ ran."""

    def __init__(self):
        self.exited = False

    def __exit__(self, *_a):
        self.exited = True
        return False


def _permit_held(sup, name):
    return name in sup._permits  # noqa: SLF001


def test_launch_failure_with_confirmed_teardown_releases_the_permit(tmp_path):
    # baseline: pane_pid fails (so launch raises), kill_session CONFIRMS teardown (rc 0) --
    # nothing can still be alive, so the slot must be returned as it always was (AC-E5).
    be = StubBackend("herdr", pane_pid_returncode=1, kill_session_returncode=0)
    sup = _sup(tmp_path, herdr=be)
    identity = _identity()
    with pytest.raises(SupervisorError):
        sup.launch("build", "hello", identity=identity, handle=_handle(tmp_path, identity),
                   terminal_backend="herdr")
    assert any(c[0] == "kill_session" for c in be.calls)
    names = [c[2] for c in be.calls if c[0] == "kill_session"]
    assert not any(_permit_held(sup, n) for n in names), "confirmed teardown must free the slot"


def test_launch_failure_with_unconfirmed_teardown_releases_and_documents_the_limitation(tmp_path):
    """Owner scope decision (2026-07-24, pass 8): on a launch failure whose teardown cannot be
    CONFIRMED, the permit is released -- a DELIBERATE, documented limitation, not an oversight.

    Passes 5-8 each tried a cheaper substitute for real process-tree death proof and each was
    itself a defect: release unconditionally over-admits (p5); hold it in memory alone is
    unreclaimable (p6); persist a pid-0 residue record to make it reclaimable made "session
    absent" masquerade as "process dead", let a recovery relaunch erase it and free the new
    permit, never released from reap's `keep` tier, and was not crash-durable (p7-p8). The
    invariant needs a death-proof protocol and is filed as its own issue; this fails toward the
    PRE-#638 behaviour (unchanged for tmux), with reap()'s CF-11 unknown-session reporting
    surfacing any pane live on the endpoint but absent from the registry."""
    be = StubBackend("herdr", pane_pid_returncode=1, kill_session_returncode=1)
    sup = _sup(tmp_path, herdr=be)
    identity = _identity()
    with pytest.raises(SupervisorError):
        sup.launch("build", "hello", identity=identity, handle=_handle(tmp_path, identity),
                   terminal_backend="herdr")
    assert any(c[0] == "kill_session" for c in be.calls), "teardown must still be ATTEMPTED"
    assert sup._permits == {}, (                                    # noqa: SLF001
        "the documented limitation is that the slot is released; a held slot here was the "
        "pass-6/7/8 defect chain")
    # and no residue JobRecord is invented (that record WAS the root of 3 pass-8 findings)
    assert list(sup._registry.by_run(identity.run_id)) == []         # noqa: SLF001


class _RefusingSpawn(StubBackend):
    """new_session returns NONZERO -- which since Step-11 pass 6 means creation is
    INDETERMINATE, not 'nothing happened' (a backend's new_session is not atomic: herdr
    splits + renames + runs before returning, and tmux's new-session can time out after the
    server accepted it)."""

    def new_session(self, endpoint, name, cwd, argv, timeout=30):
        self.calls.append(("new_session", endpoint, name, cwd, tuple(argv)))
        return subprocess.CompletedProcess([], 1, "", "new_session refused")


def test_launch_refusal_that_created_nothing_attempts_no_teardown(tmp_path):
    """Pass 9 replaces the pass-6 contract these two tests used to assert. Pass 6 set
    `spawned = True` BEFORE new_session so a nonzero return was treated as possibly-created and
    always triggered teardown. That was unnecessary AND destructive: unnecessary because each
    backend already cleans up its own partial spawn (HerdrBackend closes the pane it created if
    rename/run fails; tmux's new-session is atomic), and destructive because deterministic
    session names meant tmux's `duplicate session` refusal made cleanup kill the ALREADY-EXISTING
    same-name session. `spawned` therefore flips only after a CONFIRMED-successful new_session,
    matching origin/main -- so a refusal attempts no teardown, and the permit is still freed."""
    be = _RefusingSpawn("herdr")
    sup = _sup(tmp_path, herdr=be)
    identity = _identity()
    with pytest.raises(SupervisorError):
        sup.launch("build", "hello", identity=identity, handle=_handle(tmp_path, identity),
                   terminal_backend="herdr")
    assert not any(c[0] == "kill_session" for c in be.calls), (
        "nothing was created, so nothing may be torn down (pass-9 duplicate-session finding)")
    assert sup._permits == {}, "the permit must still be released"     # noqa: SLF001
    assert list(sup._registry.by_run(identity.run_id)) == [], (        # noqa: SLF001
        "no residue record may be invented on this path")


def test_launch_post_spawn_failure_does_attempt_teardown(tmp_path):
    """The other side: once new_session SUCCEEDED, a later failure (an unreadable pane_pid) has
    a real session to clean up, so teardown IS attempted -- and the permit is released either
    way per the documented KNOWN LIMITATION (#648)."""
    be = StubBackend("herdr", pane_pid_returncode=1)
    sup = _sup(tmp_path, herdr=be)
    identity = _identity()
    with pytest.raises(SupervisorError):
        sup.launch("build", "hello", identity=identity, handle=_handle(tmp_path, identity),
                   terminal_backend="herdr")
    assert any(c[0] == "kill_session" for c in be.calls), (
        "a CONFIRMED spawn followed by a failure must tear the session down")
    assert sup._permits == {}                                          # noqa: SLF001


def _running_record(tmp_path, identity, name="rg-h"):
    return JobRecord(
        identity=identity, session_name=name, run_socket="herdr-endpoint",
        pane_pid=os.getpid(), pane_pgid=os.getpid(), provider_pgid=None, pane_start_time="0",
        worktree_path=str(tmp_path), worktree_base_sha="0" * 40, worktree_root=str(tmp_path),
        worktree_gitdir="/g", worktree_repo="/r", capture_dir=str(tmp_path / "cap"),
        attempt_id="0-a", permit_ref="unbounded", command_digest="d",
        provider_session_id=None, provider_exit_code=None, resume_attempts=0, state="running",
        created_at=1.0, quarantine_reason=None, terminal_backend="herdr")


def test_await_deadline_unverified_kill_with_late_sentinel_holds_the_permit(tmp_path):
    # THE finding (High, pass 6): at the deadline _kill_job() can return False, and a
    # concurrently-written valid sentinel then wins -> state completed_with_residue. That
    # _finish() call omitted `release_permit=`, taking the default True, so the slot was freed
    # while residue may still have been executing. completed_with_residue is BY DEFINITION a
    # death-not-verified state; the release must be gated on kill_clean like every other
    # residue path. Existing coverage only exercised the clean-kill branch.
    be = StubBackend("herdr")
    sup = _sup(tmp_path, herdr=be)
    identity = _identity()
    rec = _running_record(tmp_path, identity)
    sup._registry.upsert(rec)                                        # noqa: SLF001
    sup._permits[rec.session_name] = _FakeCM()                       # noqa: SLF001
    sup._kill_job = lambda _r: False                                 # noqa: SLF001 — UNVERIFIED
    # the sentinel must be ABSENT on the first poll and appear only at the DEADLINE re-check --
    # that is the branch under test. (A sentinel present from poll 1 takes the top-of-loop
    # branch instead, which already gated the release correctly; an earlier draft of this test
    # did exactly that and passed against the pre-fix code -- a false green.)
    seen = {"n": 0}

    def _late_sentinel(_r):
        seen["n"] += 1
        return None if seen["n"] == 1 else {"parse_status": "ok", "exit_code": 0}

    sup._sentinel = _late_sentinel                                   # noqa: SLF001
    state, obs = sup.await_job(rec, poll_s=0.01, timeout_s=0.0)
    assert seen["n"] >= 2, "the deadline re-check branch was never reached"
    assert state == "completed_with_residue", state
    assert obs is not None
    assert rec.session_name in sup._permits, (                       # noqa: SLF001
        "an UNVERIFIED kill must keep the slot held even when the child's result wins")


def test_await_deadline_verified_kill_with_late_sentinel_frees_the_permit(tmp_path):
    # The other side, so the fix is not just "always hold": a CONFIRMED kill still releases.
    be = StubBackend("herdr")
    sup = _sup(tmp_path, herdr=be)
    identity = _identity()
    rec = _running_record(tmp_path, identity, name="rg-h2")
    sup._registry.upsert(rec)                                        # noqa: SLF001
    sup._permits[rec.session_name] = _FakeCM()                       # noqa: SLF001
    sup._kill_job = lambda _r: True                                  # noqa: SLF001 — VERIFIED
    seen = {"n": 0}

    def _late_sentinel(_r):
        seen["n"] += 1
        return None if seen["n"] == 1 else {"parse_status": "ok", "exit_code": 0}

    sup._sentinel = _late_sentinel                                   # noqa: SLF001
    state, _ = sup.await_job(rec, poll_s=0.01, timeout_s=0.0)
    assert seen["n"] >= 2, "the deadline re-check branch was never reached"
    assert state == "completed", state
    assert rec.session_name not in sup._permits                      # noqa: SLF001


# ---- restored in pass 9: this predates the residue record and guards RETAINED behaviour ----

def test_await_job_indeterminate_probe_does_not_abort_and_is_bounded(tmp_path):
    """Added by e13d812 (pass 6), before the residue record existed, and deleted BY MISTAKE in
    the pass-8 revert (pass-9 finding: the revert removed unrelated, still-load-bearing
    coverage). The behaviour it guards is retained: _live() raises SupervisorError on an
    INDETERMINATE probe, and await_job() must catch it -- letting it propagate aborted a healthy
    dispatch with its permit held (the pass-4 regression). An indeterminate probe means "not
    known dead": keep polling to the EXISTING deadline, then take the ordinary timeout path.
    """
    be = StubBackend("herdr", raise_has_session=True)
    sup = _sup(tmp_path, herdr=be)
    identity = _identity()
    rec = _running_record(tmp_path, identity)
    sup._registry.upsert(rec)  # noqa: SLF001
    # must NOT raise, and must terminate on its own deadline rather than spinning forever
    state, obs = sup.await_job(rec, poll_s=0.01, timeout_s=0.05)
    assert state == "timed_out", state
    assert obs is not None
    assert any(c[0] == "has_session" for c in be.calls), "the probe was never attempted"


def test_launch_tmux_duplicate_session_refusal_never_kills_the_existing_session(tmp_path):
    """Pass-9 High: session names are DETERMINISTIC, so tmux's ordinary
    `duplicate session: <name>` refusal (confirmed in the installed binary, reproduced live)
    meant the pass-6 `spawned = True`-before-new_session made cleanup kill-session the
    ALREADY-EXISTING same-name session -- destroying something this launch never created. No
    teardown may be attempted for a refusal that created nothing; the permit is still released.
    """
    class _Duplicate(StubBackend):
        def new_session(self, endpoint, name, cwd, argv, timeout=30):
            self.calls.append(("new_session", endpoint, name, cwd, tuple(argv)))
            return subprocess.CompletedProcess([], 1, "", f"duplicate session: {name}")

    be = _Duplicate("tmux")
    sup = _sup(tmp_path, tmux=be)
    identity = _identity()
    with pytest.raises(SupervisorError):
        sup.launch("build", "hello", identity=identity, handle=_handle(tmp_path, identity))
    assert not any(c[0] == "kill_session" for c in be.calls), (
        "a refusal that created NOTHING must never tear down the pre-existing same-name session")
    assert sup._permits == {}, "the permit must still be released"  # noqa: SLF001


def test_lifted_resolve_backend_rejects_an_unrecognised_backend():
    """Adversarial-review finding (#647, Medium/correctness): an else-branch returning tmux
    means any unrecognised value silently probes the WRONG runtime and reports the answer as
    fact. `registry.KNOWN_TERMINAL_BACKENDS` makes that unreachable for a decoded JobRecord
    today, so this guards the concrete future slip — a third backend added to that frozenset
    without teaching the resolver about it."""
    with pytest.raises(SupervisorError, match="unsupported terminal backend"):
        resolve_backend("screen", tmux=StubBackend("tmux"), herdr=StubBackend("herdr"))


def test_method_also_rejects_an_unrecognised_backend(tmp_path):
    """The delegation must inherit the guard — one rule, both callers."""
    sup = _sup(tmp_path, tmux=StubBackend("tmux"), herdr=StubBackend("herdr"))
    with pytest.raises(SupervisorError, match="unsupported terminal backend"):
        sup._resolve_backend("screen")  # noqa: SLF001
