"""#767 — per-task executor build collection, end-to-end against real git.

AC1's executable evidence: (a) the reported failure mode is REAL — with only the linked
worktree's admin gitdir writable (issue #767's fix-candidate-1 grant), the contained side
cannot even `git add`; (b) the DOCUMENTED path works — collect_work_product (the same entry
point the Step-8 prose names) promotes the child's uncommitted work onto a temp collect ref,
and the documented guarded tri-state landing fast-forwards the checked-out feature branch,
byte-preserving binary content, with the rev-4 negative cases refused.
"""
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent.parent / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
import executor_routing_lib as er  # noqa: E402

er._ensure_pe_importable()
# pylint: disable=no-name-in-module
from phase_executor import contract, enforce  # noqa: E402
from phase_executor import worktree as wt  # noqa: E402
from phase_executor.registry import JobRecord  # noqa: E402
# pylint: enable=no-name-in-module


def _run(cmd, env=None):
    full = {**os.environ, **env} if env else None
    p = subprocess.run(cmd, capture_output=True, text=True, env=full, check=False)
    return p.returncode, p.stdout, p.stderr


def _git(repo, *args):
    return _run(["git", "-C", str(repo), *args])


def _restore_write(root):
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            try:
                os.chmod(p, os.stat(p).st_mode | stat.S_IWUSR)
            except OSError:
                pass
    os.chmod(root, os.stat(root).st_mode | stat.S_IWUSR)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "canon"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("hello\n")
    _git(r, "add", "a.txt")
    _git(r, "commit", "-qm", "init")
    yield r
    _restore_write(str(r / ".git"))


# --- (a) the #767 symptom: candidate-1's grant cannot even stage -------------------------------

def test_candidate1_grant_cannot_stage(repo, tmp_path):
    """Fix candidate 1 (grant only .git/worktrees/<leaf>/) fails before commit: `git add`
    writes the COMMON object database. Pins the platform truth the design decision rests on."""
    _git(repo, "worktree", "add", "-q", "-b", "task-b", str(tmp_path / "wt1"))
    gitdir = repo / ".git"
    try:
        for dirpath, dirnames, filenames in os.walk(gitdir):
            for name in dirnames + filenames:
                p = os.path.join(dirpath, name)
                os.chmod(p, os.stat(p).st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        os.chmod(gitdir, os.stat(gitdir).st_mode & ~stat.S_IWUSR)
        _restore_write(str(gitdir / "worktrees"))  # candidate 1's grant: the admin dir only
        (tmp_path / "wt1" / "a.txt").write_text("edited\n")
        rc, _out, err = _git(tmp_path / "wt1", "add", "a.txt")
        assert rc != 0
        assert "insufficient permission" in err or "failed to insert" in err or "unable to" in err
    finally:
        _restore_write(str(gitdir))


# --- (b) the documented path: collect onto a temp ref, land via guarded tri-state ff -----------

def _mk_worktree(repo, tmp_path, *, nonce="rn1"):
    mgr = wt.WorktreeManager(_run, forbid_tmp=False, clock=lambda: 1.0)
    base = _git(repo, "rev-parse", "HEAD")[1].strip()
    ident = wt.WorktreeIdentity(run_id="runI", seat="build", attempt="0-itg1")
    h = mgr.create(str(repo), ident, base, root=str(tmp_path / "wtroot"))
    record = JobRecord(
        identity=ident, session_name="sessI", run_socket="s", pane_pid=1, pane_pgid=1,
        provider_pgid=None, pane_start_time="0", worktree_path=h.path,
        worktree_base_sha=h.base_sha, worktree_root=h.root, worktree_gitdir=h.gitdir,
        worktree_repo=h.repo, capture_dir=str(tmp_path / "cd"), attempt_id="0-itg1",
        permit_ref="unbounded", command_digest="sha256:x", provider_session_id=None,
        provider_exit_code=0, resume_attempts=0, state="completed", created_at=0.0,
        quarantine_reason=None, receipt_nonce=nonce)
    return mgr, h, record, base


class _Reg:
    def __init__(self, rec):
        self._rec = rec

    def by_run(self, run_id):
        return [self._rec]


def _seed_audit(tmp_path, *, nonce="rn1", run_id="runI"):
    audit = enforce.RoutingAuditLog(tmp_path / "runs", run_id)
    audit._write_locked({  # pylint: disable=protected-access
        "kind": "receipt", "nonce": nonce, "seat": "build", "correlation_id": "cI",
        "attempt_id": "0-itg1",
        "target_identity": ["codex-model", "openai", "cli", "api_key", "codex", None, None],
        "config_digest": "sha256:d", "gate_digest": "sha256:g", "author_provider": None,
        "verdict": "pass", "violations": [], "role": "build", "gate_outcome": "single",
        "gate_input_digest": "sha256:gi", "recovered_from": None})
    obs = contract.Observation(
        run_id=run_id, attempt_id="0-itg1", correlation_id="cI", seat="build", engine="codex",
        transport="cli", requested_model="codex-model", actual_model="codex-model",
        prompt_hash="sha256:p", context_hashes=[], usage={"input": 1, "output": 1}, timing_ms=1,
        queued_ms=0, process={"exit_code": 0, "timed_out": False}, parse_status="ok",
        parsed_payload=None, raw_capture_path=None, fallback_reason=None,
        routing_config_digest="sha256:d").to_dict()
    obs["dispatched_lane"] = {"provider": "openai", "transport": "cli", "auth_mode": "api_key",
                              "pool": "codex", "credential_ref": None}
    audit._write_locked({"kind": "observation", "receipt_nonce": nonce,  # pylint: disable=protected-access
                         "observation": obs})
    return audit


BIN = b"\x00\x01\x02BINARY\x00\xff\xfe"
TEMP_REF = "refs/rawgentic/collect/rn1"


def _collect(repo, tmp_path, record, mgr, *, paths, target=TEMP_REF):
    audit = _seed_audit(tmp_path)
    return er.collect_work_product(
        run_id="runI", session_name="sessI", target_ref=target,
        expected_target_sha="0" * 40, kind="code", registry=_Reg(record), manager=mgr,
        audit=audit, intent_dir=str(tmp_path / "intents"), correlation_id="cI",
        promote_paths=paths)


def _land(repo, *, expected_ref, pre_sha, new_sha, temp_ref=TEMP_REF):
    """Step-11 lane R1-F2: the landing is a PRODUCTION operation (`land_work_product` /
    the `land-work-product` CLI verb), not a test-owned facsimile — these tests exercise it.
    Returns (ok, why) mapped from the production result for the assertions below."""
    res = er.land_work_product(repo=str(repo), expected_ref=expected_ref, pre_sha=pre_sha,
                               new_sha=new_sha, temp_ref=temp_ref)
    if res["ok"]:
        return True, res["status"]
    return False, res["error"]["code"]


def test_collect_and_land_end_to_end(repo, tmp_path):
    _git(repo, "checkout", "-qb", "feat-x")
    mgr, h, record, base = _mk_worktree(repo, tmp_path)
    Path(h.path, "a.txt").write_text("CHANGED\n")
    Path(h.path, "blob.bin").write_bytes(BIN)
    res = _collect(repo, tmp_path, record, mgr, paths=["a.txt", "blob.bin"])
    assert res["ok"], res
    new_sha = res["new_sha"]
    ok, why = _land(repo, expected_ref="refs/heads/feat-x", pre_sha=base, new_sha=new_sha)
    assert ok, why
    # filesystem materialized (pass-3 F1's exact worry): bytes on disk, clean status
    assert (repo / "blob.bin").read_bytes() == BIN
    assert (repo / "a.txt").read_text() == "CHANGED\n"
    assert _git(repo, "status", "--porcelain")[1].strip() == ""
    assert _git(repo, "rev-parse", "refs/heads/feat-x")[1].strip() == new_sha
    assert _git(repo, "rev-parse", "--verify", TEMP_REF)[0] != 0  # temp ref cleaned


def test_collect_refuses_undeclared_path(repo, tmp_path):
    _git(repo, "checkout", "-qb", "feat-x")
    mgr, h, record, _base = _mk_worktree(repo, tmp_path)
    Path(h.path, "a.txt").write_text("CHANGED\n")
    Path(h.path, "smuggle.txt").write_text("nope\n")
    res = _collect(repo, tmp_path, record, mgr, paths=["a.txt"])
    assert res["ok"] is False
    assert res["error"]["code"] == "promote_refused"


def test_land_refuses_detached_head(repo, tmp_path):
    _git(repo, "checkout", "-qb", "feat-x")
    mgr, h, record, base = _mk_worktree(repo, tmp_path)
    Path(h.path, "a.txt").write_text("CHANGED\n")
    res = _collect(repo, tmp_path, record, mgr, paths=["a.txt"])
    assert res["ok"], res
    _git(repo, "checkout", "-q", "--detach", base)  # detached at the pre-task SHA
    ok, why = _land(repo, expected_ref="refs/heads/feat-x", pre_sha=base, new_sha=res["new_sha"])
    assert not ok and why == "landing_wrong_ref"


def test_land_refuses_other_branch_at_same_sha(repo, tmp_path):
    _git(repo, "checkout", "-qb", "feat-x")
    mgr, h, record, base = _mk_worktree(repo, tmp_path)
    Path(h.path, "a.txt").write_text("CHANGED\n")
    res = _collect(repo, tmp_path, record, mgr, paths=["a.txt"])
    assert res["ok"], res
    _git(repo, "checkout", "-qb", "impostor")  # different branch, same SHA
    ok, why = _land(repo, expected_ref="refs/heads/feat-x", pre_sha=base, new_sha=res["new_sha"])
    assert not ok and why == "landing_wrong_ref"


def test_land_refuses_when_branch_advanced(repo, tmp_path):
    _git(repo, "checkout", "-qb", "feat-x")
    mgr, h, record, base = _mk_worktree(repo, tmp_path)
    Path(h.path, "a.txt").write_text("CHANGED\n")
    res = _collect(repo, tmp_path, record, mgr, paths=["a.txt"])
    assert res["ok"], res
    (repo / "peer.txt").write_text("peer\n")
    _git(repo, "add", "peer.txt")
    _git(repo, "commit", "-qm", "peer advance")
    ok, why = _land(repo, expected_ref="refs/heads/feat-x", pre_sha=base, new_sha=res["new_sha"])
    assert not ok and why in ("landing_unexpected_sha", "landing_ff_refused")


def test_land_rerun_after_merge_is_already_landed(repo, tmp_path):
    # crash-after-merge recovery (rev-4 F2): a second landing run is a cleanup no-op, not a refusal
    _git(repo, "checkout", "-qb", "feat-x")
    mgr, h, record, base = _mk_worktree(repo, tmp_path)
    Path(h.path, "a.txt").write_text("CHANGED\n")
    res = _collect(repo, tmp_path, record, mgr, paths=["a.txt"])
    assert res["ok"], res
    ok, why = _land(repo, expected_ref="refs/heads/feat-x", pre_sha=base, new_sha=res["new_sha"])
    assert ok
    ok2, why2 = _land(repo, expected_ref="refs/heads/feat-x", pre_sha=base, new_sha=res["new_sha"])
    assert ok2 and why2 == "already_landed"


def test_land_refuses_dirty_tree(repo, tmp_path):
    # rev-4: the landing materializes the checkout — a dirty operator tree must refuse first.
    _git(repo, "checkout", "-qb", "feat-x")
    mgr, h, record, base = _mk_worktree(repo, tmp_path)
    Path(h.path, "a.txt").write_text("CHANGED\n")
    res = _collect(repo, tmp_path, record, mgr, paths=["a.txt"])
    assert res["ok"], res
    (repo / "dirty.txt").write_text("uncommitted\n")
    ok, why = _land(repo, expected_ref="refs/heads/feat-x", pre_sha=base, new_sha=res["new_sha"])
    assert not ok and why == "landing_dirty_tree"


def test_land_cli_end_to_end(repo, tmp_path):
    # quality bar: the CLI surface exercised via subprocess exactly as an orchestrator calls it.
    import json as _json  # noqa: PLC0415
    _git(repo, "checkout", "-qb", "feat-x")
    mgr, h, record, base = _mk_worktree(repo, tmp_path)
    Path(h.path, "a.txt").write_text("CHANGED\n")
    res = _collect(repo, tmp_path, record, mgr, paths=["a.txt"])
    assert res["ok"], res
    rc, out, err = _run([sys.executable, str(HOOKS / "executor_routing_lib.py"),
                         "land-work-product", "--repo", str(repo),
                         "--expected-ref", "refs/heads/feat-x", "--pre-sha", base,
                         "--new-sha", res["new_sha"], "--temp-ref", TEMP_REF])
    assert rc == 0, err
    payload = _json.loads(out)
    assert payload["ok"] and payload["status"] == "landed"
    assert _git(repo, "rev-parse", "refs/heads/feat-x")[1].strip() == res["new_sha"]
    assert _git(repo, "rev-parse", "--verify", TEMP_REF)[0] != 0  # temp ref cleaned
