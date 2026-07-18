"""#466 W3 Task 1 — pure planning layer (no I/O)."""
from __future__ import annotations

import os

import pytest

from phase_executor import worktree as wt
from phase_executor import contract


def _ident(run="run1", seat="build", attempt="0-abcd1234"):
    return wt.WorktreeIdentity(run_id=run, seat=seat, attempt=attempt)


# ---- resolve_root ---------------------------------------------------------

def test_resolve_root_accepts_outside_tmp_abs():
    assert wt.resolve_root("/var/lib/rawgentic/worktrees") == "/var/lib/rawgentic/worktrees"


def test_resolve_root_rejects_relative():
    with pytest.raises(ValueError):
        wt.resolve_root("worktrees")


def test_resolve_root_rejects_empty_and_nonstr():
    with pytest.raises(ValueError):
        wt.resolve_root("")
    with pytest.raises(ValueError):
        wt.resolve_root(None)  # type: ignore[arg-type]


def test_resolve_root_rejects_fs_root():
    with pytest.raises(ValueError):
        wt.resolve_root("/")


def test_resolve_root_rejects_tmp_and_containment():
    with pytest.raises(ValueError):
        wt.resolve_root("/tmp")
    with pytest.raises(ValueError):
        wt.resolve_root("/tmp/x/y")


def test_resolve_root_rejects_tmpdir_env(monkeypatch, tmp_path):
    # a root under $TMPDIR is rejected too (spike #452 writable-roots hazard)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    with pytest.raises(ValueError):
        wt.resolve_root(str(tmp_path / "wt"))


def test_resolve_root_forbid_tmp_false_allows_tmp(tmp_path):
    # the outside-/tmp rule is an environment policy; a hermetic in-/tmp test disables
    # ONLY that check (canonical containment still applies at create time).
    got = wt.resolve_root(str(tmp_path / "wt"), forbid_tmp=False)
    assert got == os.path.realpath(str(tmp_path / "wt"))


# ---- component_for / planned_path -----------------------------------------

def test_component_for_sanitizes_and_hashes():
    c = wt.component_for("weird/seat name")
    assert "/" not in c and " " not in c
    assert c.endswith(tuple("0123456789abcdef")) and len(c.rsplit("-", 1)[1]) == 8


def test_component_for_collision_resistant():
    # two raws that sanitize to the same component keep distinct hashes
    a = wt.component_for("a/b")
    b = wt.component_for("a_b")
    assert a != b


def test_planned_path_deterministic():
    idn = _ident()
    p1 = wt.planned_path("/root", idn)
    p2 = wt.planned_path("/root", idn)
    assert p1 == p2
    assert p1.startswith("/root/")
    assert wt.component_for("run1") in p1


def test_planned_path_contained_boundary():
    # a benign identity yields a path canonical_contained_worktree accepts under root
    root = "/var/lib/rg/wt"
    idn = _ident()
    p = wt.planned_path(root, idn)
    assert contract.canonical_contained_worktree(p, root).startswith(os.path.realpath(root) + os.sep)


# ---- decide_disposition ---------------------------------------------------

def _insp(dirty=False, tree_differs=False):
    return wt.WorktreeInspection(dirty=dirty, changed=(), untracked=(), tree_differs=tree_differs)


def test_disposition_clean_only_when_ok_and_clean_and_same_tree():
    assert wt.decide_disposition(_insp(), contract.OK) == "clean"


def test_disposition_retain_on_failed_obs_even_clean_tree():
    assert wt.decide_disposition(_insp(), contract.TIMEOUT) == "retain"
    assert wt.decide_disposition(_insp(), contract.NONZERO_EXIT) == "retain"


def test_disposition_retain_when_dirty():
    assert wt.decide_disposition(_insp(dirty=True), contract.OK) == "retain"


def test_disposition_retain_when_tree_differs_even_if_not_dirty():
    # CF-2: gitignored child work is invisible to porcelain-dirty but shows in tree_differs
    assert wt.decide_disposition(_insp(dirty=False, tree_differs=True), contract.OK) == "retain"


# ---- select_evictions -----------------------------------------------------

def _rec(path, ident, dirty=False, created=0.0, retained=None):
    return wt.RetentionRecord(
        path=path, identity=ident, reason="r", dirty=dirty,
        created_at=created, retained_at=(created if retained is None else retained),
        base_sha="0" * 40,
    )


def test_evictions_none_when_under_limit():
    pol = wt.RetentionPolicy(max_retained_count=5)
    recs = [_rec(f"/r/{i}", _ident(attempt=str(i)), created=float(i)) for i in range(3)]
    ev, pressure = wt.select_evictions(recs, pol, now=100.0, live_identities=set())
    assert ev == [] and pressure is False


def test_evictions_oldest_clean_first_never_live():
    pol = wt.RetentionPolicy(max_retained_count=2)
    ids = [_ident(attempt=str(i)) for i in range(4)]
    recs = [_rec(f"/r/{i}", ids[i], created=float(i)) for i in range(4)]  # all clean
    # id0 is live -> must not be evicted even though it is oldest
    ev, pressure = wt.select_evictions(recs, pol, now=100.0, live_identities={ids[0]})
    assert pressure is False
    evicted_ids = {r.identity for r in ev}
    assert ids[0] not in evicted_ids
    assert len(ev) == 2  # over by 2 (4 - 2)


def test_evictions_dirty_only_when_aged():
    pol = wt.RetentionPolicy(max_retained_count=1, max_age_s=100)
    ids = [_ident(attempt=str(i)) for i in range(3)]
    # all dirty; only the aged ones are eligible
    recs = [
        _rec("/r/young", ids[0], dirty=True, created=95.0),   # age 5 (< 100) -> protected
        _rec("/r/old1", ids[1], dirty=True, created=0.0),     # age 100 -> not > 100, still protected
        _rec("/r/old2", ids[2], dirty=True, created=-50.0),   # age 150 > 100 -> evictable
    ]
    ev, pressure = wt.select_evictions(recs, pol, now=100.0, live_identities=set())
    evp = {r.path for r in ev}
    assert "/r/old2" in evp
    assert "/r/young" not in evp and "/r/old1" not in evp


def test_evictions_pressure_when_all_protected():
    pol = wt.RetentionPolicy(max_retained_count=1)
    ids = [_ident(attempt=str(i)) for i in range(3)]
    recs = [_rec(f"/r/{i}", ids[i], dirty=True, created=float(i)) for i in range(3)]  # dirty, not aged
    ev, pressure = wt.select_evictions(recs, pol, now=0.0, live_identities=set())
    assert ev == [] and pressure is True


# ---- validate_allowlist ---------------------------------------------------

def test_allowlist_empty_copies_nothing():
    assert wt.validate_allowlist([], "/wt", "/src") == []


def test_allowlist_rejects_dotdot_escape(tmp_path):
    src = tmp_path / "src"; wtd = tmp_path / "wt"
    src.mkdir(); wtd.mkdir()
    with pytest.raises(ValueError):
        wt.validate_allowlist([("../secret", ".env")], str(wtd), str(src))
    with pytest.raises(ValueError):
        wt.validate_allowlist([(".env", "../escape")], str(wtd), str(src))


def test_allowlist_rejects_absolute(tmp_path):
    src = tmp_path / "src"; wtd = tmp_path / "wt"
    src.mkdir(); wtd.mkdir()
    with pytest.raises(ValueError):
        wt.validate_allowlist([("/etc/passwd", ".env")], str(wtd), str(src))


def test_parse_porcelain_v2_records():
    # ordinary changed (1), untracked (?), rename (2 + trailing origPath NUL), unmerged (u)
    ordinary = "1 .M N... 100644 100644 100644 h1 h2 a.txt"
    untracked = "? evil.txt"
    rename = "2 R. N... 100644 100644 100644 h1 h2 R100 new.txt"
    origpath = "old.txt"
    unmerged = "u UU N... 100644 100644 100644 100644 h1 h2 h3 conflicted.txt"
    out = "\x00".join([ordinary, untracked, rename, origpath, unmerged]) + "\x00"
    changed, untracked_list = wt._parse_porcelain_v2(out)
    assert "a.txt" in changed
    assert "new.txt" in changed
    assert "conflicted.txt" in changed  # u-record path at index 10, not 8 (Finding #2)
    assert "old.txt" not in changed  # rename origPath consumed, not mis-read as a record
    assert untracked_list == ["evil.txt"]


def test_allowlist_resolves_contained_pair(tmp_path):
    src = tmp_path / "src"; wtd = tmp_path / "wt"
    src.mkdir(); wtd.mkdir()
    (src / ".env").write_text("x")
    out = wt.validate_allowlist([(".env", ".env")], str(wtd), str(src))
    assert len(out) == 1
    s, d = out[0]
    assert s == os.path.realpath(str(src / ".env"))
    assert d == os.path.realpath(str(wtd / ".env"))
