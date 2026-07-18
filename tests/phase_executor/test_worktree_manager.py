"""#466 W3 Task 2 — WorktreeManager create / inspect / populate (real git in tmp)."""
from __future__ import annotations

import json
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
    # forbid_tmp=False: hermetic in-/tmp test; canonical containment still enforced at create.
    return wt.WorktreeManager(_run, forbid_tmp=False, clock=lambda: 1000.0)


def _ident(attempt="0-aaaa1111"):
    return wt.WorktreeIdentity(run_id="run1", seat="build", attempt=attempt)


def _base(repo):
    return _git(repo, "rev-parse", "HEAD")[1].strip()


def test_create_records_trusted_gitdir_and_repo(repo, mgr, tmp_path):
    root = tmp_path / "wtroot"
    h = mgr.create(str(repo), _ident(), _base(repo), root=str(root))
    assert os.path.isdir(h.path)
    # trusted gitdir is under the CANONICAL repo's .git/worktrees, NOT the worktree
    assert h.gitdir == os.path.realpath(os.path.join(str(repo), ".git", "worktrees", os.path.basename(h.gitdir)))
    assert h.gitdir.startswith(os.path.realpath(str(repo)) + os.sep)
    assert h.repo == os.path.realpath(str(repo))
    meta = json.load(open(mgr._meta_file(h)))  # noqa: SLF001
    assert meta["gitdir"] == h.gitdir and meta["repo"] == h.repo and meta["populated"] == []


def test_create_rejects_reused_leaf(repo, mgr, tmp_path):
    root = tmp_path / "wtroot"
    mgr.create(str(repo), _ident(), _base(repo), root=str(root))
    with pytest.raises(wt.WorktreeError):
        mgr.create(str(repo), _ident(), _base(repo), root=str(root))


def test_create_rejects_bad_base(repo, mgr, tmp_path):
    with pytest.raises(wt.WorktreeError):
        mgr.create(str(repo), _ident(), "deadbeef" * 5, root=str(tmp_path / "wtroot"))


def test_create_root_dirs_are_0700(repo, mgr, tmp_path):
    root = tmp_path / "wtroot"
    mgr.create(str(repo), _ident(), _base(repo), root=str(root))
    import stat
    for sub in ("", ".meta", ".retained"):
        d = root / sub if sub else root
        assert stat.S_IMODE(os.lstat(d).st_mode) == 0o700


def test_create_compensates_on_post_add_failure(repo, tmp_path):
    """CF-4: a failure AFTER `worktree add` (here: forced during gitdir discovery) force-removes
    the created worktree — no orphan checkout, and the identity can be retried."""
    calls = {"n": 0}

    def flaky_run(cmd, env=None):
        # fail the git-common-dir discovery exactly once (it runs right after `worktree add`)
        if "rev-parse" in cmd and "--git-common-dir" in cmd and calls["n"] == 0:
            calls["n"] += 1
            return 1, "", "injected discovery failure"
        return _run(cmd, env=env)

    m = wt.WorktreeManager(flaky_run, forbid_tmp=False, clock=lambda: 1.0)
    root = tmp_path / "wtroot"
    with pytest.raises(wt.WorktreeError):
        m.create(str(repo), _ident(), _base(repo), root=str(root))
    # the worktree leaf must be gone (compensated), and a clean retry must now succeed
    planned = wt.planned_path(wt.resolve_root(str(root), forbid_tmp=False), _ident())
    assert not os.path.exists(planned)
    good = wt.WorktreeManager(_run, forbid_tmp=False, clock=lambda: 2.0)
    h = good.create(str(repo), _ident(), _base(repo), root=str(root))
    assert os.path.isdir(h.path)


def test_inspect_clean_when_untouched(repo, mgr, tmp_path):
    h = mgr.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    insp = mgr.inspect(h)
    assert insp.dirty is False and insp.tree_differs is False


def test_inspect_sees_dirty_via_trusted_gitdir_despite_child_rewrite(repo, mgr, tmp_path):
    """A-H5 attack cell: the child repoints the worktree's `.git` FILE at a decoy, then dirties a
    tracked file. Inspect uses the TRUSTED admin gitdir, so it still sees the real dirty state."""
    h = mgr.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    # child dirties a tracked file + adds an untracked one
    (open(os.path.join(h.path, "a.txt"), "w")).write("TAMPERED\n")
    (open(os.path.join(h.path, "evil.txt"), "w")).write("x\n")
    # child rewrites its own .git control file to point at a decoy repo
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    _git(decoy, "init", "-q")
    with open(os.path.join(h.path, ".git"), "w") as fh:
        fh.write(f"gitdir: {decoy}/.git\n")
    insp = mgr.inspect(h)
    assert insp.dirty is True
    assert "a.txt" in insp.changed
    assert "evil.txt" in insp.untracked
    assert insp.tree_differs is True


def test_populate_write_ahead_records_pending_then_complete(repo, mgr, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / ".env").write_text("API_KEY=secret\n")
    h = mgr.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    mgr.populate(h, str(src), [(".env", ".env")])
    assert open(os.path.join(h.path, ".env")).read() == "API_KEY=secret\n"
    meta = json.load(open(mgr._meta_file(h)))  # noqa: SLF001
    dsts = {e["dst"]: e["state"] for e in meta["populated"]}
    assert dsts[os.path.realpath(os.path.join(h.path, ".env"))] == "complete"


def test_populate_rejects_escape(repo, mgr, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    h = mgr.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    with pytest.raises(ValueError):
        mgr.populate(h, str(src), [("../../etc/passwd", ".env")])
