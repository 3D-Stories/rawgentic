"""#471 W8 — pure run-status deriver: read_sentinel / derive_state lifts + run_status rows.

AC-J1 (per-seat rows), AC-J2 (JSON contract — the CLI wraps these rows), AC-J3 (read-only:
everything I/O-shaped is injected). Stale registry entries are visible, never filtered;
`state` (derived) + `recorded_state` (registry) together distinguish every OQ-8 state.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from phase_executor import contract, supervisor
from phase_executor.registry import JOB_STATES, JobRecord
from phase_executor.worktree import WorktreeIdentity

DIGEST = "sha256:" + "0" * 64


def _idn(run="run1", seat="build", attempt="0-aaaa1111"):
    return WorktreeIdentity(run_id=run, seat=seat, attempt=attempt)


def _rec(**kw):
    base = dict(
        identity=_idn(), session_name="rg-x", run_socket="/run/user/1000/rg-x.sock",
        pane_pid=100, pane_pgid=100, provider_pgid=101, pane_start_time="55",
        worktree_path="/wt/a", worktree_base_sha="0" * 40, worktree_root="/wt",
        worktree_gitdir="/repo/.git/worktrees/a", worktree_repo="/repo",
        capture_dir="/cap/a", attempt_id="0-aaaa1111", permit_ref="pool:default",
        command_digest="sha256:abc", provider_session_id=None, provider_exit_code=None,
        resume_attempts=0, state="running", created_at=1000.0, quarantine_reason=None,
    )
    base.update(kw)
    return JobRecord(**base)


def _obs(record, **kw):
    """A schema-valid observation matching ``record``'s identity (read_sentinel validity)."""
    d = supervisor.synthetic_observation(
        run_id=record.identity.run_id, seat=record.identity.seat,
        attempt_id=record.attempt_id, engine="claude", requested_model="claude-sonnet-5",
        prompt="hello", parse_status=contract.TIMEOUT, reason="t",
        routing_config_digest=DIGEST)
    d.update(kw)
    return d


def _spec():
    return {"engine": "claude",
            "request": {"seat": "build", "requested_model": "claude-sonnet-5",
                        "effort": "high", "prompt": "hello"}}


# ---- read_sentinel (lifted module-level; same validity rule as the method) ------------


def test_read_sentinel_valid(tmp_path):
    rec = _rec(capture_dir=str(tmp_path))
    (tmp_path / "observation.json").write_text(json.dumps(_obs(rec)), encoding="utf-8")
    got = supervisor.read_sentinel(rec)
    assert got is not None and got["attempt_id"] == rec.attempt_id


def test_read_sentinel_missing_file(tmp_path):
    assert supervisor.read_sentinel(_rec(capture_dir=str(tmp_path))) is None


def test_read_sentinel_malformed_json(tmp_path):
    (tmp_path / "observation.json").write_text("{not json", encoding="utf-8")
    assert supervisor.read_sentinel(_rec(capture_dir=str(tmp_path))) is None


def test_read_sentinel_identity_mismatch(tmp_path):
    rec = _rec(capture_dir=str(tmp_path))
    other = _rec(identity=_idn(run="OTHER"), capture_dir=str(tmp_path))
    (tmp_path / "observation.json").write_text(json.dumps(_obs(other)), encoding="utf-8")
    assert supervisor.read_sentinel(rec) is None


def test_read_sentinel_schema_invalid(tmp_path):
    rec = _rec(capture_dir=str(tmp_path))
    bad = _obs(rec)
    del bad["run_id"]  # schema-required field
    (tmp_path / "observation.json").write_text(json.dumps(bad), encoding="utf-8")
    assert supervisor.read_sentinel(rec) is None


# ---- derive_state (pure; byte-identical semantics to TmuxSupervisor.status) -----------

TERMINAL = ("completed", "completed_with_residue", "failed", "quarantined",
            "quota_paused", "timed_out")


@pytest.mark.parametrize("state", TERMINAL)
def test_derive_state_terminal_passthrough(state):
    assert supervisor.derive_state(_rec(state=state), sentinel=None, live=True) == state


def test_derive_state_sentinel_wins_over_live():
    rec = _rec(state="running")
    assert supervisor.derive_state(rec, sentinel={"x": 1}, live=True) == "completed"


@pytest.mark.parametrize("state", ("launched", "running"))
def test_derive_state_live_running(state):
    assert supervisor.derive_state(_rec(state=state), sentinel=None, live=True) == "running"


def test_derive_state_dead_no_sentinel():
    assert supervisor.derive_state(_rec(state="running"), sentinel=None, live=False) == "exited_no_sentinel"


# ---- run_status rows -------------------------------------------------------------------


def _row(records, *, live=lambda r: False, sentinel=lambda r: None,
         spec=lambda r: None, activity=lambda r: None, clock=lambda: 2000.0):
    return supervisor.run_status(records, live_fn=live, sentinel_fn=sentinel,
                                 spec_fn=spec, activity_fn=activity, clock=clock)


def test_run_status_empty():
    assert _row([]) == []


def test_run_status_row_fields():
    rec = _rec(created_at=1000.0, resume_attempts=1)
    obs = _obs(rec, actual_model="claude-sonnet-5", correlation_id="c-1")
    rows = _row([rec], sentinel=lambda r: obs, spec=lambda r: _spec(),
                activity=lambda r: {"file": "transport.txt", "age_s": 3, "tail": "last line"})
    (row,) = rows
    assert row["seat"] == "build" and row["attempt"] == "0-aaaa1111"
    assert row["state"] == "completed"           # sentinel wins
    assert row["recorded_state"] == "running"    # registry state stays visible
    assert row["session_name"] == rec.session_name
    assert row["run_socket"] == rec.run_socket
    assert row["worktree_path"] == rec.worktree_path
    assert row["capture_dir"] == rec.capture_dir
    assert row["requested_model"] == "claude-sonnet-5"
    assert row["effort"] == "high"
    assert row["engine"] == "claude"
    assert row["actual_model"] == "claude-sonnet-5"
    assert row["correlation_id"] == "c-1"
    assert row["eta"] == "no estimate"           # AC-J1c: honest until AC-I3 history exists
    assert row["elapsed_s"] == 1000
    assert row["run_elapsed_s"] == 1000
    assert row["resume_attempts"] == 1
    assert row["quarantine_reason"] is None
    assert row["last_activity"] == {"file": "transport.txt", "age_s": 3, "tail": "last line"}


def test_run_status_missing_spec_and_sentinel_are_nulls():
    (row,) = _row([_rec()])
    assert row["requested_model"] is None and row["effort"] is None and row["engine"] is None
    assert row["actual_model"] is None and row["correlation_id"] is None
    assert row["last_activity"] is None
    assert row["state"] == "exited_no_sentinel"


def test_run_status_every_recorded_state_visible_never_filtered():
    recs = [_rec(identity=_idn(seat=f"s{i}"), attempt_id=f"{i}-x", state=s,
                 quarantine_reason="mismatch" if s == "quarantined" else None)
            for i, s in enumerate(sorted(JOB_STATES))]
    rows = _row(recs)
    assert len(rows) == len(JOB_STATES)  # stale/abnormal entries included, not hidden
    assert {r["recorded_state"] for r in rows} == set(JOB_STATES)
    assert all(r["state"] in JOB_STATES for r in rows)
    quarantined = next(r for r in rows if r["recorded_state"] == "quarantined")
    assert quarantined["quarantine_reason"] == "mismatch"


def test_run_status_run_elapsed_uses_earliest_start():
    a = _rec(identity=_idn(seat="a"), created_at=100.0)
    b = _rec(identity=_idn(seat="b"), created_at=900.0)
    rows = _row([a, b], clock=lambda: 1000.0)
    assert [r["elapsed_s"] for r in rows] == [900, 100]
    assert all(r["run_elapsed_s"] == 900 for r in rows)


def test_run_status_terminal_records_skip_probes():
    calls = []
    rec = _rec(state="quarantined")
    _row([rec], live=lambda r: calls.append("live"), sentinel=lambda r: calls.append("sent"))
    assert calls == []  # a dead-terminal record never triggers tmux/capture probes


# ---- method delegation (the lift is behavior-preserving) -------------------------------


def test_status_method_delegates_to_derive_state(tmp_path):
    """TmuxSupervisor.status routes through the lifted functions — one derivation source."""
    import inspect
    src = inspect.getsource(supervisor.TmuxSupervisor.status)
    assert "derive_state(" in src
    src2 = inspect.getsource(supervisor.TmuxSupervisor._sentinel)
    assert "read_sentinel(" in src2


def test_status_method_skips_live_probe_when_sentinel_valid():
    """8a R1#1/R2#3: the method keeps the pre-lift short-circuit — a sentinel-bearing job
    never spawns the tmux probe (and a hung socket can't convert 'completed' into a raise)."""
    import types as _types
    sup2 = object.__new__(supervisor.TmuxSupervisor)
    rec = _rec()
    calls = []
    sup2._registry = _types.SimpleNamespace(get=lambda i: rec)
    sup2._sentinel = lambda r: {"ok": 1}
    sup2._live = lambda r: calls.append("live") or True
    assert sup2.status(rec.identity) == "completed"
    assert calls == []
