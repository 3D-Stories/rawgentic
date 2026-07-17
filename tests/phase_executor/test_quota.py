"""Task 4: quota.py — pool ceiling (single-process + cross-process), finally-release,
stale reclaim, unbounded pool."""
import os
import subprocess
import sys
import pathlib
import time

import pytest

from phase_executor.quota import QuotaCoordinator, QuotaTimeout

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "phase_executor" / "src"
WORKER = pathlib.Path(__file__).resolve().parent / "_quota_worker.py"


def test_single_process_ceiling_and_release(tmp_path):
    qc = QuotaCoordinator(tmp_path, {"claude": 2})
    with qc.acquire("claude"):
        with qc.acquire("claude"):
            assert qc.live_permits("claude") == 2
            with pytest.raises(QuotaTimeout):
                with qc.acquire("claude", timeout=0.3, poll=0.02):
                    pass
        # one released -> a slot frees
        assert qc.live_permits("claude") == 1
        with qc.acquire("claude", timeout=1.0):
            assert qc.live_permits("claude") == 2
    assert qc.live_permits("claude") == 0


def test_permit_released_on_exception(tmp_path):
    qc = QuotaCoordinator(tmp_path, {"p": 1})
    with pytest.raises(ValueError):
        with qc.acquire("p"):
            raise ValueError("boom")
    assert qc.live_permits("p") == 0  # released in finally despite the raise


def test_unbounded_pool_is_noop(tmp_path):
    qc = QuotaCoordinator(tmp_path, {})  # no limit configured for "x"
    with qc.acquire("x"):
        with qc.acquire("x"):
            assert qc.live_permits("x") == 0  # unbounded => no tokens written


def test_stale_dead_pid_token_reaped(tmp_path):
    qc = QuotaCoordinator(tmp_path, {"claude": 1})
    pool_dir = tmp_path / "claude" / "default"
    pool_dir.mkdir(parents=True)
    # a permit held by a definitely-dead pid
    (pool_dir / "permit-999999-stale").write_text("999999\n0\n", encoding="utf-8")
    assert qc.live_permits("claude") == 0  # reaped
    with qc.acquire("claude", timeout=1.0):  # can acquire despite the stale token
        assert qc.live_permits("claude") == 1


def test_stale_by_age_reaped_when_pid_unknown(tmp_path):
    qc = QuotaCoordinator(tmp_path, {"claude": 1}, stale_after=0.0)
    pool_dir = tmp_path / "claude" / "default"
    pool_dir.mkdir(parents=True)
    tok = pool_dir / "permit-bad-old"  # unparseable pid -> age applies
    tok.write_text("not-a-pid\n0\n", encoding="utf-8")
    time.sleep(0.01)
    assert qc.live_permits("claude") == 0  # reaped by age (holder unknown)


def test_live_pid_not_age_reaped(tmp_path):
    """A legitimately long-running call (age > stale_after) with a LIVE holder keeps its permit —
    else another process could grab the slot and exceed the ceiling (diff-review finding #12)."""
    qc = QuotaCoordinator(tmp_path, {"claude": 1}, stale_after=0.0)  # aggressive age threshold
    pool_dir = tmp_path / "claude" / "default"
    pool_dir.mkdir(parents=True)
    tok = pool_dir / f"permit-{os.getpid()}-old"  # our own (alive) pid, ancient mtime
    tok.write_text(f"{os.getpid()}\n0\n", encoding="utf-8")
    time.sleep(0.01)
    assert qc.live_permits("claude") == 1  # NOT reaped despite age>stale_after (holder alive)


def _max_overlap(intervals):
    events = []
    for s, e in intervals:
        events.append((s, 1))
        events.append((e, -1))
    events.sort()
    cur = mx = 0
    for _, d in events:
        cur += d
        mx = max(mx, cur)
    return mx


def test_cross_process_ceiling(tmp_path):
    """OWNER f1: two+ OS processes sharing the permit root never jointly exceed the pool limit."""
    root = tmp_path / "permits"
    out = tmp_path / "intervals.txt"
    out.write_text("", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(SRC))
    procs = [
        subprocess.Popen([sys.executable, str(WORKER), str(root), "claude", "2", "0.5", str(out)], env=env)
        for _ in range(4)
    ]
    for p in procs:
        assert p.wait(timeout=60) == 0
    lines = [ln.split() for ln in out.read_text().splitlines() if ln.strip()]
    intervals = [(float(a), float(b)) for a, b in lines]
    assert len(intervals) == 4
    assert _max_overlap(intervals) <= 2, f"ceiling violated: max concurrent = {_max_overlap(intervals)}"
