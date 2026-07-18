"""#466 W3 Task 3 — finalize / _clean / _retain: redaction, move, retention index, eviction."""
from __future__ import annotations

import json
import os
import stat
import subprocess

import pytest

from phase_executor import worktree as wt
from phase_executor import contract


def _fake_pem(marker="MIIfake"):
    """A PEM-shaped fixture built at RUNTIME so the contiguous ``-----BEGIN … PRIVATE KEY-----``
    literal never appears in source (gitleaks / the pre-push hook would flag a real-looking key).
    The WRITTEN file still contains the full header, exercising the redaction content-scan regex."""
    begin = "-----BEGIN " + "PRIVATE KEY" + "-----"
    end = "-----END " + "PRIVATE KEY" + "-----"
    return f"{begin}\n{marker}\n{end}\n"


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


def _mgr(retention=None, clock=None):
    return wt.WorktreeManager(_run, forbid_tmp=False, clock=clock or (lambda: 1000.0), retention=retention)


def _ident(attempt="0-aaaa1111"):
    return wt.WorktreeIdentity(run_id="run1", seat="build", attempt=attempt)


def _base(repo):
    return _git(repo, "rev-parse", "HEAD")[1].strip()


def test_finalize_clean_leaves_zero_residue(repo, tmp_path):
    m = _mgr()
    h = m.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    rec = m.finalize(h, contract.OK)
    assert rec is None
    assert not os.path.exists(h.path)
    _rc, out, _e = _git(repo, "worktree", "list", "--porcelain")
    assert f"worktree {h.path}" not in out


def test_finalize_retain_on_failed_obs_even_clean_tree(repo, tmp_path):
    m = _mgr()
    h = m.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    rec = m.finalize(h, contract.TIMEOUT)
    assert rec is not None
    assert not os.path.exists(h.path)  # moved
    assert os.path.isdir(rec.path) and rec.path.endswith(wt._meta_name(h.identity))  # noqa: SLF001
    assert rec.reason == "failed-observation"
    _rc, out, _e = _git(repo, "worktree", "list", "--porcelain")
    assert f"worktree {h.path}" not in out


def test_retain_redacts_exact_populated_env(repo, tmp_path):
    m = _mgr()
    src = tmp_path / "src"; src.mkdir()
    (src / ".env").write_text("API_KEY=supersecretvalue\n")
    h = m.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    m.populate(h, str(src), [(".env", ".env")])
    rec = m.finalize(h, contract.NONZERO_EXIT)
    retained_env = os.path.join(rec.path, ".env")
    assert os.path.isfile(retained_env)  # name kept
    assert "supersecretvalue" not in open(retained_env).read()  # content gone
    assert open(retained_env).read().startswith("[redacted")
    assert rec.redaction_incomplete is False


def test_retain_redacts_gitignored_secret_via_walk(repo, tmp_path):
    """CF-2: a gitignored child secret escapes `status --untracked-files=all` (porcelain) but the
    retained-tree walk still catches and redacts it."""
    m = _mgr()
    h = m.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    # child writes a gitignored PEM key
    with open(os.path.join(h.path, ".gitignore"), "w") as f:
        f.write("leaked.pem\n")
    pem = _fake_pem("MIIabc")
    with open(os.path.join(h.path, "leaked.pem"), "w") as f:
        f.write(pem)
    insp = m.inspect(h)
    assert not any("leaked.pem" in u for u in insp.untracked)  # porcelain MISSED it (gitignored)
    rec = m.finalize(h, contract.TIMEOUT)  # failed obs -> retain
    retained_pem = os.path.join(rec.path, "leaked.pem")
    assert "PRIVATE KEY" not in open(retained_pem).read()  # walk caught + redacted it
    assert open(retained_pem).read().startswith("[redacted")


def test_retain_redacts_scanned_pem(repo, tmp_path):
    m = _mgr()
    h = m.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    with open(os.path.join(h.path, "server.pem"), "w") as f:
        f.write(_fake_pem("ZZZ"))
    rec = m.finalize(h, contract.TIMEOUT)
    assert open(os.path.join(rec.path, "server.pem")).read().startswith("[redacted")
    assert rec.redaction_incomplete is False


def test_retain_unreadable_file_surfaces_failure(repo, tmp_path):
    m = _mgr()
    h = m.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    secret = os.path.join(h.path, "creds.key")  # name matches *.key -> flagged for redaction
    with open(secret, "w") as f:
        f.write("KEYDATA\n")
    os.chmod(secret, 0o000)  # unreadable/unwritable -> O_NOFOLLOW open fails
    try:
        rec = m.finalize(h, contract.TIMEOUT)
        assert rec.redaction_incomplete is True
        assert any("creds.key" in fail["path"] for fail in rec.redaction_failures)
    finally:
        os.chmod(os.path.join(rec.path, "creds.key"), 0o600)  # let tmp cleanup remove it


def test_retain_does_not_follow_symlink(repo, tmp_path):
    m = _mgr()
    outside = tmp_path / "outside.secret"
    outside.write_text(_fake_pem("OUTSIDE"))
    h = m.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    os.symlink(str(outside), os.path.join(h.path, "link.pem"))  # name matches *.pem but is a symlink
    m.finalize(h, contract.TIMEOUT)
    # the symlink target OUTSIDE the tree must be untouched (never followed)
    assert "OUTSIDE" in outside.read_text()
    assert outside.read_text().startswith("-----BEGIN")


def test_retention_index_evicts_oldest_clean_and_persists(repo, tmp_path):
    root = str(tmp_path / "wtroot")
    m = _mgr(retention=wt.RetentionPolicy(max_retained_count=1))
    # seed the index with one OLD clean retained record whose dir exists
    old_dir = tmp_path / "wtroot" / ".retained" / "old"
    os.makedirs(old_dir, exist_ok=True)
    old_rec = wt.RetentionRecord(
        path=str(old_dir), identity=wt.WorktreeIdentity("r0", "s0", "a0"), reason="dirty-tree",
        dirty=False, created_at=1.0, retained_at=1.0, base_sha="0" * 40)
    m._write_index(root, {  # noqa: SLF001
        "records": [wt._record_to_dict(old_rec)],  # noqa: SLF001
        "live_identities": [], "pressure": False,
    })
    h = m.create(str(repo), _ident(), _base(repo), root=root)
    (open(os.path.join(h.path, "x.txt"), "w")).write("dirty\n")  # make it retainable
    rec = m.finalize(h, contract.TIMEOUT)
    idx = json.load(open(m._index_file(root)))  # noqa: SLF001
    kept_paths = {r["path"] for r in idx["records"]}
    assert rec.path in kept_paths
    assert str(old_dir) not in kept_paths  # oldest clean evicted
    assert not os.path.exists(old_dir)  # its dir removed
    assert idx["pressure"] is False


def test_gitignored_only_success_is_cleaned_not_retained(repo, tmp_path):
    """Deliberate contract (security review Finding #1): gitignored-only content on a SUCCESSFUL
    obs is force-removed (destroyed, not leaked) — it is not promotable work product. The
    gitignored-secret REDACTION path is exercised only on retain (see the gitignored-via-walk
    test), so no gitignored secret survives un-redacted into durable retention."""
    m = _mgr()
    # commit .gitignore INTO the base so it is tracked (an untracked .gitignore would itself be dirty)
    (repo / ".gitignore").write_text("local.env\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-qm", "add gitignore")
    h = m.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    with open(os.path.join(h.path, "local.env"), "w") as f:
        f.write("API_KEY=x\n")
    insp = m.inspect(h)
    assert insp.dirty is False and insp.tree_differs is False  # gitignored escapes both signals
    rec = m.finalize(h, contract.OK)  # successful obs
    assert rec is None  # cleaned
    assert not os.path.exists(h.path)  # worktree + its gitignored file destroyed (no leak, no retain)


def _dirty_rec(path, ident, created):
    os.makedirs(path, exist_ok=True)
    return wt.RetentionRecord(path=path, identity=ident, reason="dirty-tree", dirty=True,
                              created_at=created, retained_at=created, base_sha="0" * 40)


def test_pressure_blocks_next_create(repo, tmp_path):
    """Genuine pressure: over-limit with every slot dirty-not-yet-aged -> recompute confirms
    pressure -> create refuses (records must ACTUALLY produce pressure, not a stale flag)."""
    root = str(tmp_path / "wtroot")
    os.makedirs(os.path.join(root, ".meta"), exist_ok=True)
    m = _mgr(retention=wt.RetentionPolicy(max_retained_count=1, max_age_s=1000), clock=lambda: 100.0)
    recs = [_dirty_rec(os.path.join(root, ".retained", f"r{i}"), wt.WorktreeIdentity(f"r{i}", "s", "a"), 99.0)
            for i in range(2)]  # 2 dirty, age 1 << 1000 -> not evictable, over limit 1
    m._write_index(root, {"records": [wt._record_to_dict(r) for r in recs],  # noqa: SLF001
                          "live_identities": [], "pressure": False})
    with pytest.raises(wt.WorktreeError):
        m.create(str(repo), _ident(), _base(repo), root=root)


def test_pressure_self_heals_when_records_age_out(repo, tmp_path):
    """Fix (CAS-review F2): a latched pressure does NOT block forever — create recomputes from the
    on-disk records, evicts the now-aged dirty ones, and proceeds."""
    root = str(tmp_path / "wtroot")
    os.makedirs(os.path.join(root, ".meta"), exist_ok=True)
    m = _mgr(retention=wt.RetentionPolicy(max_retained_count=1, max_age_s=10), clock=lambda: 10_000.0)
    aged = [_dirty_rec(os.path.join(root, ".retained", f"old{i}"), wt.WorktreeIdentity(f"o{i}", "s", "a"), 1.0)
            for i in range(2)]  # created at t=1, now=10000 -> age 9999 >> 10 -> evictable
    m._write_index(root, {"records": [wt._record_to_dict(r) for r in aged],  # noqa: SLF001
                          "live_identities": [], "pressure": True})  # latched True
    h = m.create(str(repo), _ident(), _base(repo), root=root)  # must NOT raise
    assert os.path.isdir(h.path)
    idx = json.load(open(m._index_file(root)))  # noqa: SLF001
    assert idx["pressure"] is False  # cleared


def test_retain_strengthened_content_detection(repo, tmp_path):
    """Fix (redaction-review F1): JSON-quoted + keyword-suffixed secrets are now caught."""
    m = _mgr()
    h = m.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    with open(os.path.join(h.path, "conf.json"), "w") as f:  # name not a secret-glob
        f.write('{"database_password": "hunter2"}\n')
    with open(os.path.join(h.path, "app.py"), "w") as f:
        f.write("SECRET_KEY = 'aabbccddeeff'\n")
    rec = m.finalize(h, contract.TIMEOUT)
    assert "hunter2" not in open(os.path.join(rec.path, "conf.json")).read()
    assert "aabbccddeeff" not in open(os.path.join(rec.path, "app.py")).read()


def test_retain_does_not_over_redact_benign_files(repo, tmp_path):
    """Step-11 regression F2: benign files an engineer needs to debug must survive retention intact
    (broad name-globs like *.ini / *token* were dropped; the regex no longer matches token_count)."""
    m = _mgr()
    h = m.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    with open(os.path.join(h.path, "tox.ini"), "w") as f:  # name dropped from secret globs
        f.write("[tox]\nenvlist = py312\n")
    with open(os.path.join(h.path, "counts.py"), "w") as f:  # content no longer over-matches
        f.write("token_count = 5\naccess_key_id_length = 3\n")
    rec = m.finalize(h, contract.TIMEOUT)  # retained
    assert "[tox]" in open(os.path.join(rec.path, "tox.ini")).read()  # untouched
    assert "token_count = 5" in open(os.path.join(rec.path, "counts.py")).read()  # untouched


def test_retain_large_unmatched_file_flags_truncated(repo, tmp_path):
    """Fix (redaction-review F1): a file larger than the scan cap that isn't otherwise redacted is
    surfaced as scan-truncated -> redaction_incomplete, never a silent pass."""
    m = _mgr()
    h = m.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    with open(os.path.join(h.path, "big.bin"), "w") as f:
        f.write("x" * (wt.WorktreeManager._SCAN_CAP + 10))  # noqa: SLF001
    rec = m.finalize(h, contract.TIMEOUT)
    assert rec.redaction_incomplete is True
    assert any(fl["kind"] == "scan-truncated" for fl in rec.redaction_failures)


def test_retain_fifo_does_not_hang_teardown(repo, tmp_path):
    """A child-planted FIFO named like a secret must NOT block `_retain` forever (O_NONBLOCK +
    S_ISREG guard); it is surfaced as a non-regular skip, not silently scanned."""
    import signal

    m = _mgr()
    h = m.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    os.mkfifo(os.path.join(h.path, "secret.key"))  # name matches *.key
    def _boom(sig, frm):  # noqa: ANN001
        raise TimeoutError("finalize hung on the FIFO")
    old = signal.signal(signal.SIGALRM, _boom)
    signal.alarm(15)
    try:
        rec = m.finalize(h, contract.TIMEOUT)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    assert any("secret.key" in fl["path"] for fl in rec.redaction_failures)


def test_retain_hardlink_does_not_corrupt_outside_file(repo, tmp_path):
    """Fix (redaction-review F2): a child-planted hardlink to an OUTSIDE file must NOT be
    ftruncated (that corrupts the shared inode). We break our link + write a fresh marker; the
    outside file keeps its content."""
    m = _mgr()
    outside = tmp_path / "outside.pem"
    outside.write_text(_fake_pem("KEEPME"))
    h = m.create(str(repo), _ident(), _base(repo), root=str(tmp_path / "wtroot"))
    os.link(str(outside), os.path.join(h.path, "linked.pem"))  # HARDLINK (name matches *.pem)
    rec = m.finalize(h, contract.TIMEOUT)
    # outside file untouched (still the PEM); in-tree link replaced with the marker
    assert "KEEPME" in outside.read_text()
    assert outside.read_text().startswith("-----BEGIN")
    assert open(os.path.join(rec.path, "linked.pem")).read().startswith("[redacted")
