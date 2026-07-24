"""#637 (epic #635 C4) — WorktreeManager.park_and_reset: park recoverable, reset clean,
main-tree untouched, idempotent on an already-clean worktree, reset-failure-after-
successful-park stays recoverable. Real git in a tmp repo, matching the existing
test_worktree_manager.py fixture pattern.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from phase_executor import worktree as wt


def _run(cmd, env=None):
    full = {**os.environ, **env} if env else None
    p = subprocess.run(cmd, capture_output=True, text=True, env=full, check=False)
    return p.returncode, p.stdout, p.stderr


def _git(repo, *args):
    return _run(["git", "-C", str(repo), *args])


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "canon"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("hello\n")
    _git(r, "add", "a.txt")
    _git(r, "commit", "-qm", "init")
    return r


@pytest.fixture()
def mgr():
    return wt.WorktreeManager(_run, forbid_tmp=False, clock=lambda: 1000.0)


def _ident(attempt="0-aaaa1111"):
    return wt.WorktreeIdentity(run_id="run1", seat="build", attempt=attempt)


def _base(repo):
    return _git(repo, "rev-parse", "HEAD")[1].strip()


def _handle(repo, mgr, tmp_path, attempt="0-aaaa1111"):
    root = tmp_path / "wtroot"
    return mgr.create(str(repo), _ident(attempt), _base(repo), root=str(root))


def test_park_and_reset_parks_dirty_diff_recoverably(repo, mgr, tmp_path):
    h = _handle(repo, mgr, tmp_path)
    (open(os.path.join(h.path, "a.txt"), "w")).write("changed\n")
    rec = mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
    assert rec.parked is True
    assert rec.stash_name == "rawgentic-parked:run1:v2:t1"
    # worktree reset clean to base
    assert open(os.path.join(h.path, "a.txt")).read() == "hello\n"
    # the parked diff is genuinely recoverable via the named stash
    rc, out, _err = mgr._git(  # noqa: SLF001
        "--git-dir", h.gitdir, "--work-tree", h.path, "stash", "list")
    assert rec.stash_name in out


def test_park_and_reset_parks_untracked_files(repo, mgr, tmp_path):
    h = _handle(repo, mgr, tmp_path)
    open(os.path.join(h.path, "new.txt"), "w").write("new file\n")
    rec = mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
    assert rec.parked is True
    assert not os.path.exists(os.path.join(h.path, "new.txt"))
    rc, out, _err = mgr._git(  # noqa: SLF001
        "--git-dir", h.gitdir, "--work-tree", h.path, "stash", "list")
    assert rec.stash_name in out


def test_park_and_reset_idempotent_on_clean_worktree(repo, mgr, tmp_path):
    h = _handle(repo, mgr, tmp_path)
    rec = mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
    assert rec.parked is False
    assert rec.stash_name == "rawgentic-parked:run1:v2:t1"
    rc, out, _err = mgr._git(  # noqa: SLF001
        "--git-dir", h.gitdir, "--work-tree", h.path, "stash", "list")
    assert out.strip() == ""  # nothing was stashed -- no phantom entry


def test_park_and_reset_never_touches_main_checkout(repo, mgr, tmp_path):
    h = _handle(repo, mgr, tmp_path)
    open(os.path.join(h.path, "a.txt"), "w").write("changed in worktree\n")
    (repo / "b.txt").write_text("dirty in main\n")
    mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
    # main checkout's own uncommitted state is untouched
    assert (repo / "b.txt").read_text() == "dirty in main\n"
    rc, out, _err = _git(repo, "status", "--porcelain")
    assert "b.txt" in out


def test_park_and_reset_resets_to_recorded_base_sha(repo, mgr, tmp_path):
    h = _handle(repo, mgr, tmp_path)
    # advance main past base_sha after the worktree was created
    (repo / "c.txt").write_text("later commit\n")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-qm", "later")
    open(os.path.join(h.path, "a.txt"), "w").write("dirty\n")
    rec = mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
    assert rec.parked is True
    rc, out, _err = mgr._git(  # noqa: SLF001
        "--git-dir", h.gitdir, "--work-tree", h.path, "rev-parse", "HEAD")
    assert out.strip() == h.base_sha  # NOT main's later commit


def test_park_and_reset_reset_failure_after_park_stays_recoverable(repo, mgr, tmp_path, monkeypatch):
    h = _handle(repo, mgr, tmp_path)
    open(os.path.join(h.path, "a.txt"), "w").write("changed\n")
    real_git = mgr._git  # noqa: SLF001

    def failing_git(*args, env=None):
        if "reset" in args:
            return (1, "", "stub: reset failed")
        return real_git(*args, env=env)

    monkeypatch.setattr(mgr, "_git", failing_git)
    with pytest.raises(wt.WorktreeError, match="reset"):
        mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
    # the park already happened and is still recoverable, regardless of the reset failure
    rc, out, _err = real_git(
        "--git-dir", h.gitdir, "--work-tree", h.path, "stash", "list")
    assert "rawgentic-parked:run1:v2:t1" in out


def test_park_and_reset_park_failure_never_resets(repo, mgr, tmp_path, monkeypatch):
    h = _handle(repo, mgr, tmp_path)
    open(os.path.join(h.path, "a.txt"), "w").write("changed\n")
    real_git = mgr._git  # noqa: SLF001

    def failing_git(*args, env=None):
        if "stash" in args:
            return (1, "", "stub: stash failed")
        return real_git(*args, env=env)

    monkeypatch.setattr(mgr, "_git", failing_git)
    with pytest.raises(wt.WorktreeError, match="park"):
        mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
    # the dirty file must still be there -- reset was never attempted after a park failure
    assert open(os.path.join(h.path, "a.txt")).read() == "changed\n"


# --- Step-8a review findings (#637 mechanical + security reviewer waves) ---


def test_park_and_reset_captures_stash_oid(repo, mgr, tmp_path):
    h = _handle(repo, mgr, tmp_path)
    open(os.path.join(h.path, "a.txt"), "w").write("changed\n")
    rec = mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
    assert rec.stash_oid is not None
    rc, out, _err = mgr._git(  # noqa: SLF001
        "--git-dir", h.gitdir, "--work-tree", h.path, "rev-parse", "refs/stash")
    assert out.strip() == rec.stash_oid


def test_park_and_reset_clean_worktree_has_no_stash_oid(repo, mgr, tmp_path):
    h = _handle(repo, mgr, tmp_path)
    rec = mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
    assert rec.parked is False
    assert rec.stash_oid is None


def test_park_and_reset_repeated_identifiers_produce_distinct_stash_oid(repo, mgr, tmp_path):
    # Same run_id/design_version/task_id across two sequential loop-backs -> identical
    # stash_name, but the OID (not the name) is the collision-proof recovery identity.
    h = _handle(repo, mgr, tmp_path)
    open(os.path.join(h.path, "a.txt"), "w").write("first change\n")
    rec1 = mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
    open(os.path.join(h.path, "a.txt"), "w").write("second change\n")
    rec2 = mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
    assert rec1.stash_name == rec2.stash_name
    assert rec1.stash_oid != rec2.stash_oid
    rc, out, _err = mgr._git(  # noqa: SLF001
        "--git-dir", h.gitdir, "--work-tree", h.path, "stash", "list")
    assert out.count("rawgentic-parked:run1:v2:t1") == 2


def test_park_and_reset_detects_no_new_stash_despite_rc_zero(repo, mgr, tmp_path, monkeypatch):
    # Simulates the Step-8a mechanical-reviewer race: stash push returns rc=0 (e.g. a
    # concurrent park already stashed the same changes, or a "no local changes" no-op)
    # but refs/stash never actually changed -- must raise, never silently claim parked=True.
    h = _handle(repo, mgr, tmp_path)
    open(os.path.join(h.path, "a.txt"), "w").write("changed\n")
    real_git = mgr._git  # noqa: SLF001

    def noop_stash_push(*args, env=None):
        if "stash" in args and "push" in args:
            return (0, "No local changes to save\n", "")  # claims success, stashes nothing
        return real_git(*args, env=env)

    monkeypatch.setattr(mgr, "_git", noop_stash_push)
    with pytest.raises(wt.WorktreeError, match="no new refs/stash entry"):
        mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
    # reset must never have been attempted -- the dirty file is still there
    assert open(os.path.join(h.path, "a.txt")).read() == "changed\n"


def test_park_and_reset_reset_failure_carries_park_record(repo, mgr, tmp_path, monkeypatch):
    h = _handle(repo, mgr, tmp_path)
    open(os.path.join(h.path, "a.txt"), "w").write("changed\n")
    real_git = mgr._git  # noqa: SLF001

    def failing_reset(*args, env=None):
        if "reset" in args:
            return (1, "", "stub: reset failed")
        return real_git(*args, env=env)

    monkeypatch.setattr(mgr, "_git", failing_reset)
    with pytest.raises(wt.ParkThenResetError) as exc_info:
        mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
    rec = exc_info.value.park_record
    assert rec.parked is True
    assert rec.stash_oid is not None
    rc, out, _err = real_git(
        "--git-dir", h.gitdir, "--work-tree", h.path, "rev-parse", "refs/stash")
    assert out.strip() == rec.stash_oid  # the audit-recoverable OID is genuinely correct


def test_park_and_reset_clean_failure_carries_park_record(repo, mgr, tmp_path, monkeypatch):
    h = _handle(repo, mgr, tmp_path)
    open(os.path.join(h.path, "a.txt"), "w").write("changed\n")
    real_git = mgr._git  # noqa: SLF001

    def failing_clean(*args, env=None):
        if "clean" in args:
            return (1, "", "stub: clean failed")
        return real_git(*args, env=env)

    monkeypatch.setattr(mgr, "_git", failing_clean)
    with pytest.raises(wt.ParkThenResetError) as exc_info:
        mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
    rec = exc_info.value.park_record
    assert rec.parked is True
    assert rec.stash_oid is not None


def test_park_and_reset_absent_worktree_path_raises_cleanly(repo, mgr, tmp_path):
    h = _handle(repo, mgr, tmp_path)
    import shutil as _shutil
    _shutil.rmtree(h.path)  # simulate an already-torn-down worktree
    with pytest.raises(wt.WorktreeError):
        mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
