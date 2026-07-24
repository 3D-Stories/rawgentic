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


def test_park_and_reset_detects_no_reflog_match_despite_successful_push(repo, mgr, tmp_path, monkeypatch):
    # If 'git stash push' claims success (rc=0) but our own stash_name never turns up
    # in the refs/stash reflog search right after -- must raise, never silently claim
    # parked=True for a stash entry we can't actually prove exists.
    h = _handle(repo, mgr, tmp_path)
    open(os.path.join(h.path, "a.txt"), "w").write("changed\n")
    real_git = mgr._git  # noqa: SLF001

    def blind_reflog_search(*args, env=None):
        if "log" in args and "-g" in args and "--grep" in args:
            return (0, "", "")  # claims success, no matching reflog entry
        return real_git(*args, env=env)

    monkeypatch.setattr(mgr, "_git", blind_reflog_search)
    with pytest.raises(wt.WorktreeError, match="no matching refs/stash reflog entry"):
        mgr.park_and_reset(h, run_id="run1", design_version="v2", task_id="t1")
    # the real `stash push` genuinely succeeded (the diff is NOT lost) -- only our own
    # reflog identification of it was blinded here; it's still recoverable via the
    # real stash list even though we raised rather than report an (unconfirmed) OID.
    rc, out, _err = real_git(
        "--git-dir", h.gitdir, "--work-tree", h.path, "stash", "list")
    assert "rawgentic-parked:run1:v2:t1" in out


def test_park_and_reset_stash_oid_immune_to_foreign_worktree_race(repo, mgr, tmp_path, monkeypatch):
    # Step-11 regression: refs/stash is a repository-GLOBAL ref shared by every linked
    # worktree of one canonical repo (empirically confirmed: two `git worktree add`
    # checkouts of the same repo share one refs/stash). The OLD implementation captured
    # the "parked" OID by comparing refs/stash before/after `stash push` -- a worktree B
    # pushing its own stash in that window would advance refs/stash to B's commit, and A
    # would misattribute B's OID as its own. The fix resolves A's own commit by searching
    # the reflog for A's own (unique) stash_name rather than reading the ref's tip, so a
    # foreign worktree's concurrent push landing in between cannot alter what A records.
    h_a = _handle(repo, mgr, tmp_path, attempt="0-aaaa1111")
    h_b = _handle(repo, mgr, tmp_path, attempt="0-bbbb2222")
    open(os.path.join(h_a.path, "a.txt"), "w").write("A's change\n")
    open(os.path.join(h_b.path, "a.txt"), "w").write("B's change\n")
    real_git = mgr._git  # noqa: SLF001

    def foreign_push_after_a_pushes(*args, env=None):
        result = real_git(*args, env=env)
        if "stash" in args and "push" in args and h_a.path in args:
            # Worktree B concurrently parks BETWEEN A's own `stash push` and A's
            # reflog read -- exactly the race window the OLD before/after-TIP-read
            # design sampled and could misattribute.
            real_git("--git-dir", h_b.gitdir, "--work-tree", h_b.path,
                      "stash", "push", "-u", "-m", "b-concurrent-park")
        return result

    monkeypatch.setattr(mgr, "_git", foreign_push_after_a_pushes)
    rec_a = mgr.park_and_reset(h_a, run_id="run1", design_version="v2", task_id="t1")
    assert rec_a.parked is True
    # refs/stash's shared TIP is now B's push (it raced in after A's) -- proving the
    # OLD before/after-tip-read design would have misattributed it to A.
    rc, shared_tip, _err = real_git(
        "--git-dir", h_a.gitdir, "--work-tree", h_a.path, "rev-parse", "refs/stash")
    assert shared_tip.strip() != rec_a.stash_oid, (
        "test setup did not actually race the shared ref -- rewrite the race window")
    # A's recorded OID resolves to A's OWN content, not B's, regardless of the race.
    rc, show, _err = real_git(
        "--git-dir", h_a.gitdir, "--work-tree", h_a.path, "show", f"{rec_a.stash_oid}:a.txt")
    assert show == "A's change\n"
    # A's own worktree still cleanly reset to base despite the interleaved foreign push.
    assert open(os.path.join(h_a.path, "a.txt")).read() == "hello\n"


def test_park_and_reset_immune_to_shared_stash_name_across_worktrees(repo, mgr, tmp_path, monkeypatch):
    # Codex Step-11 finding ON THE MESSAGE-SEARCH HARDENING ITSELF: `stash_name` alone
    # (run_id + design_version + task_id) is NOT guaranteed unique per WORKTREE -- a
    # competitive/bake-off dispatch can run several attempts of the SAME task under the
    # SAME run concurrently, each in its OWN worktree, sharing an otherwise-identical
    # stash_name. Prove worktree A still records ITS OWN correct OID even when worktree
    # B -- a DIFFERENT worktree sharing the IDENTICAL run_id/design_version/task_id --
    # races in and fully parks between A's push and A's reflog read.
    h_a = _handle(repo, mgr, tmp_path, attempt="0-aaaa1111")
    h_b = _handle(repo, mgr, tmp_path, attempt="1-bbbb2222")
    open(os.path.join(h_a.path, "a.txt"), "w").write("A's change\n")
    open(os.path.join(h_b.path, "a.txt"), "w").write("B's change\n")
    real_git = mgr._git  # noqa: SLF001

    def foreign_same_task_park_after_a_pushes(*args, env=None):
        result = real_git(*args, env=env)
        if "stash" in args and "push" in args and h_a.path in args:
            # Worktree B -- a DIFFERENT worktree, the SAME run_id/design_version/task_id
            # -- fully parks BETWEEN A's own `stash push` and A's reflog read.
            mgr.park_and_reset(h_b, run_id="run1", design_version="v2", task_id="t1")
        return result

    monkeypatch.setattr(mgr, "_git", foreign_same_task_park_after_a_pushes)
    rec_a = mgr.park_and_reset(h_a, run_id="run1", design_version="v2", task_id="t1")
    assert rec_a.parked is True
    # A's recorded OID resolves to A's OWN content, not B's, despite an identical
    # audit-facing stash_name.
    rc, show, _err = real_git(
        "--git-dir", h_a.gitdir, "--work-tree", h_a.path, "show", f"{rec_a.stash_oid}:a.txt")
    assert show == "A's change\n"
    # the two pushed reflog messages genuinely differ (attempt-suffixed) despite
    # sharing the identical stash_name prefix -- confirms disambiguation, not luck.
    rc, log_out, _err = real_git(
        "--git-dir", h_a.gitdir, "--work-tree", h_a.path,
        "log", "-g", "--format=%gs", "refs/stash")
    messages = [ln for ln in log_out.splitlines() if "rawgentic-parked:run1:v2:t1" in ln]
    assert len(messages) == 2
    assert messages[0] != messages[1]
    assert open(os.path.join(h_a.path, "a.txt")).read() == "hello\n"


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
