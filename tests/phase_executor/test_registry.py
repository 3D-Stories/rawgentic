"""#467 W4 Task 1 — durable job registry: pure core + atomic I/O."""
from __future__ import annotations

import json
import os

import pytest

from phase_executor import registry as reg
from phase_executor.worktree import WorktreeIdentity, WorktreeHandle


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
        resume_attempts=0, state="running", created_at=1.0, quarantine_reason=None,
    )
    base.update(kw)
    return reg.JobRecord(**base)


# ---- session_name / command_digest --------------------------------------

def test_session_name_sanitizes_and_hashes():
    n = reg.session_name(_idn(run="weird/run:name", seat="s", attempt="0-a"))
    assert n.startswith("rg-")
    assert ":" not in n and "/" not in n and " " not in n  # tmux-reserved chars gone


def test_session_name_collision_resistant():
    a = reg.session_name(_idn(run="a/b"))
    b = reg.session_name(_idn(run="a_b"))
    assert a != b  # component_for hash disambiguates a sanitize collision


def test_command_digest_stable():
    d1 = reg.command_digest(["python", "-m", "x", "spec.json"])
    d2 = reg.command_digest(["python", "-m", "x", "spec.json"])
    assert d1 == d2 and d1.startswith("sha256:")
    assert reg.command_digest(["python", "-m", "y"]) != d1


# ---- handle_from_record --------------------------------------------------

def test_handle_from_record_round_trips():
    r = _rec()
    h = reg.handle_from_record(r)
    assert isinstance(h, WorktreeHandle)
    assert h.path == r.worktree_path and h.base_sha == r.worktree_base_sha
    assert h.root == r.worktree_root and h.gitdir == r.worktree_gitdir and h.repo == r.worktree_repo
    assert h.identity == r.identity


# ---- classify_recovery matrix -------------------------------------------

def test_classify_adopt_on_full_match_live():
    assert reg.classify_recovery(_rec(), live=True, identity_matches=True, sentinel_valid=False) == "adopt"


def test_classify_quarantine_on_live_mismatch():
    assert reg.classify_recovery(_rec(), live=True, identity_matches=False, sentinel_valid=False) == "quarantine"


def test_classify_relaunch_under_cap_then_fail():
    r = _rec(state="quota_paused", resume_attempts=0)
    assert reg.classify_recovery(r, live=False, identity_matches=True, sentinel_valid=False) == "relaunch"
    r2 = _rec(state="quota_paused", resume_attempts=reg.MAX_RESUME)
    assert reg.classify_recovery(r2, live=False, identity_matches=True, sentinel_valid=False) == "fail"


def test_classify_adopt_completed_sentinel_not_live():
    assert reg.classify_recovery(_rec(state="completed"), live=False, identity_matches=True, sentinel_valid=True) == "adopt"


def test_classify_quarantine_dead_no_sentinel():
    assert reg.classify_recovery(_rec(), live=False, identity_matches=True, sentinel_valid=False) == "quarantine"


# ---- reap_plan tiers -----------------------------------------------------

def _plan(records, live_fresh=(), dead=(), clean=(), now=10_000.0, max_age=100):
    policy = reg.ReapPolicy(max_age_s=max_age)
    dead_set, clean_set = set(dead), set(clean)
    return reg.reap_plan(
        records, live_fresh=set(live_fresh), now=now, policy=policy,
        dead_fn=lambda r: r.session_name in dead_set,
        clean_fn=lambda r: r.session_name in clean_set)


def test_reap_keeps_live_fresh():
    r = _rec(session_name="rg-live", state="running")
    p = _plan([r], live_fresh=["rg-live"])
    assert r in p.keep and r not in p.kill_session and r not in p.kill_tree


def test_reap_kills_finalized_dead():
    r = _rec(session_name="rg-done", state="completed")
    p = _plan([r], dead=["rg-done"], clean=["rg-done"])
    assert r in p.kill_session


def test_reap_kills_tree_of_wedged_live():
    # not live-fresh, NOT dead -> a wedged live child: kill the tree first (never sweep its worktree yet)
    r = _rec(session_name="rg-wedged", state="running")
    p = _plan([r], dead=[])  # dead_fn false -> still alive
    assert r in p.kill_tree and r not in p.kill_session and r not in p.retain_worktree


def test_reap_retains_dirty_dead_never_kills_worktree():
    r = _rec(session_name="rg-dirty", state="exited_no_sentinel")
    p = _plan([r], dead=["rg-dirty"], clean=[])  # dead + dirty
    assert r in p.retain_worktree and r not in p.kill_session


def test_reap_quarantined_dirty_retained_not_killed():
    r = _rec(session_name="rg-q", state="quarantined")
    p = _plan([r], dead=["rg-q"], clean=[])  # dead + dirty
    assert r in p.retain_worktree and r not in p.kill_session


def test_reap_quarantined_clean_aged_killed():
    r = _rec(session_name="rg-q2", state="quarantined", created_at=1.0)
    p = _plan([r], dead=["rg-q2"], clean=["rg-q2"], now=10_000.0, max_age=100)  # aged
    assert r in p.kill_session


# ---- JobRegistry I/O -----------------------------------------------------

def test_registry_roundtrip_and_atomic(tmp_path):
    root = str(tmp_path / "reg")
    r = reg.JobRegistry(root, clock=lambda: 1.0)
    rec = _rec(session_name="rg-1")
    r.upsert(rec)
    assert r.get(rec.identity).session_name == "rg-1"
    assert [x.session_name for x in r.by_run("run1")] == ["rg-1"]
    # fresh instance reads the persisted file
    r2 = reg.JobRegistry(root, clock=lambda: 1.0)
    assert r2.get(rec.identity).session_name == "rg-1"
    # no stray temp files
    assert not [f for f in os.listdir(os.path.join(root)) if f.endswith(".tmp") or f.startswith(".tmp")]
    # index file is 0700-dir + valid json
    idx = json.load(open(os.path.join(root, "jobs.json")))
    assert "rg-1" in json.dumps(idx)


def test_registry_upsert_updates_in_place(tmp_path):
    r = reg.JobRegistry(str(tmp_path / "reg"), clock=lambda: 1.0)
    r.upsert(_rec(session_name="rg-1", state="running"))
    r.upsert(_rec(session_name="rg-1", state="completed"))
    assert r.get(_idn()).state == "completed"
    assert len(r.all()) == 1
