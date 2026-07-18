"""#466 W3 Task 4 — promote: candidate-tree + CF-1 base-staleness guard + CAS."""
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
    return wt.WorktreeManager(_run, forbid_tmp=False, clock=lambda: 1.0)


def _ident(attempt="0-pppp1111"):
    return wt.WorktreeIdentity(run_id="run1", seat="build", attempt=attempt)


def _base(repo):
    return _git(repo, "rev-parse", "HEAD")[1].strip()


def _cat_ref_tree(repo, ref):
    """Return the set of file paths in the tree ``ref`` points at."""
    rc, out, _e = _git(repo, "ls-tree", "-r", "--name-only", ref)
    return set(p for p in out.split("\n") if p.strip())


def test_promote_fresh_ref_captures_dirty_and_untracked(repo, mgr, tmp_path):
    base = _base(repo)
    h = mgr.create(str(repo), _ident(), base, root=str(tmp_path / "wtroot"))
    (open(os.path.join(h.path, "a.txt"), "w")).write("CHANGED\n")   # modify tracked
    (open(os.path.join(h.path, "new.txt"), "w")).write("added\n")    # untracked
    res = mgr.promote(h, target_ref="refs/heads/integration",
                      expected_target_sha="0" * 40, message="promote 1")
    assert res.promoted is True
    assert res.new_target_sha
    # the new ref's tree contains the work product; a.txt is the CHANGED version
    files = _cat_ref_tree(repo, "refs/heads/integration")
    assert {"a.txt", "new.txt"} <= files
    rc, blob, _e = _git(repo, "show", "refs/heads/integration:a.txt")
    assert blob == "CHANGED\n"
    # commit parents on base
    rc, parent, _e = _git(repo, "rev-parse", "refs/heads/integration^")
    assert parent.strip() == base


def test_promote_existing_ref_when_base_equals_expected(repo, mgr, tmp_path):
    base = _base(repo)
    _git(repo, "branch", "integration", base)
    h = mgr.create(str(repo), _ident(), base, root=str(tmp_path / "wtroot"))
    (open(os.path.join(h.path, "new.txt"), "w")).write("x\n")
    res = mgr.promote(h, target_ref="refs/heads/integration",
                      expected_target_sha=base, message="advance")
    assert res.promoted is True
    assert _git(repo, "rev-parse", "refs/heads/integration")[1].strip() == res.new_target_sha


def test_promote_refuses_stale_base_and_keeps_peer_commit(repo, mgr, tmp_path):
    """CF-1: the worktree was cut at base B, but a peer advanced the target to Y=B+fileB. Promoting
    with expected=Y (the current tip) but base=B would silently REVERT fileB. Must refuse."""
    base = _base(repo)
    _git(repo, "branch", "integration", base)
    h = mgr.create(str(repo), _ident(), base, root=str(tmp_path / "wtroot"))
    (open(os.path.join(h.path, "wt_work.txt"), "w")).write("wt\n")
    # a peer advances the integration BRANCH to Y=base+fileB (check it out so the commit lands there)
    _git(repo, "checkout", "-q", "integration")
    (repo / "fileB.txt").write_text("peer\n")
    _git(repo, "add", "fileB.txt")
    _git(repo, "commit", "-qm", "peer advance")
    peer_tip = _git(repo, "rev-parse", "integration")[1].strip()
    assert peer_tip != base  # integration actually moved
    res = mgr.promote(h, target_ref="refs/heads/integration",
                      expected_target_sha=peer_tip, message="stale promote")
    assert res.promoted is False
    assert res.reason == "base stale — rebase"
    # integration still at the peer commit; fileB.txt NOT dropped
    assert _git(repo, "rev-parse", "integration")[1].strip() == peer_tip
    assert "fileB.txt" in _cat_ref_tree(repo, "integration")


def test_promote_refuses_on_cas_mismatch(repo, mgr, tmp_path):
    """The target moved after the caller sampled expected -> update-ref CAS refuses (target advanced)."""
    base = _base(repo)
    _git(repo, "branch", "integration", base)
    h = mgr.create(str(repo), _ident(), base, root=str(tmp_path / "wtroot"))
    (open(os.path.join(h.path, "new.txt"), "w")).write("x\n")
    # caller sampled expected=base, but the ref is moved to Y before update-ref runs
    (repo / "z.txt").write_text("z\n")
    _git(repo, "add", "z.txt")
    _git(repo, "commit", "-qm", "race")
    # NOTE: base still equals the value the worktree was cut at, so the staleness guard passes;
    # the CAS then catches the moved ref.
    _git(repo, "branch", "-f", "integration", base)  # reset so base==tip for the guard...
    moved = _git(repo, "rev-parse", "HEAD")[1].strip()
    _git(repo, "branch", "-f", "integration", moved)  # ...then move it out from under us
    res = mgr.promote(h, target_ref="refs/heads/integration",
                      expected_target_sha=base, message="cas")
    assert res.promoted is False
    assert res.reason == "target advanced or ref state changed"


def test_promote_path_policy_refuses_outside_paths(repo, mgr, tmp_path):
    base = _base(repo)
    h = mgr.create(str(repo), _ident(), base, root=str(tmp_path / "wtroot"))
    (open(os.path.join(h.path, "secrets.txt"), "w")).write("nope\n")
    with pytest.raises(wt.WorktreeError):
        mgr.promote(h, target_ref="refs/heads/integration", expected_target_sha="0" * 40,
                    message="policed", path_policy=lambda p: p.endswith(".md"))


def test_promote_fails_loud_on_unreadable_path(repo, mgr, tmp_path):
    """Fix (CAS-review F1): a child chmod-000 on a tracked file would make `add -A --ignore-errors`
    silently REVERT it to base in the promoted tree. promote uses strict mode and fails loud."""
    base = _base(repo)
    h = mgr.create(str(repo), _ident(), base, root=str(tmp_path / "wtroot"))
    tracked = os.path.join(h.path, "a.txt")
    (open(tracked, "w")).write("CHILD_EDIT\n")  # modify tracked, then make it unreadable
    os.chmod(tracked, 0o000)
    try:
        with pytest.raises(wt.WorktreeError):
            mgr.promote(h, target_ref="refs/heads/integration", expected_target_sha="0" * 40,
                        message="should refuse")
    finally:
        os.chmod(tracked, 0o600)


def test_child_dispatch_surface_never_references_promote():
    """Structural (A-H3): the child-facing adapters must never import/call `promote` — promotion is
    orchestrator-only, outside the child's boundary."""
    import pathlib
    adapters = pathlib.Path(__file__).resolve().parents[2] / "phase_executor" / "src" / "phase_executor" / "adapters"
    for py in adapters.glob("*.py"):
        assert "promote" not in py.read_text(), f"{py.name} references promote (child boundary leak)"
