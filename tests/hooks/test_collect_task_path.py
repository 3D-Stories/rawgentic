"""#767/#762 — per-task executor build collection + audited landing, end-to-end against real git.

AC1's executable evidence (#767): (a) the reported failure mode is REAL — with only the linked
worktree's admin gitdir writable (issue #767's fix-candidate-1 grant), the contained side
cannot even `git add`; (b) the DOCUMENTED path works — collect_work_product (the same entry
point the Step-8 prose names) promotes the child's uncommitted work onto a temp collect ref,
and the documented guarded tri-state landing fast-forwards the checked-out feature branch,
byte-preserving binary content, with the rev-4 negative cases refused.

#762 adds the AUDITED landing (D1/R3-A/R3-B/R4-B/R4-C/R5-B): identity-required-or---no-audit,
authorization bound to the collect-time work_product record (nonce + temp_ref + new_sha +
inner base_sha + expected_feature_ref), the landed_work_product audit record with
full-immutable-identity dedup and append-time conflict refusal, the R4-B state machine
(append before CAS-delete; record-presence heal), and the D2 SCOPED dirty check (unrelated
dirt lands; colliding paths refuse, named).
"""
import json
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


# --- (b) the documented path: collect onto a temp ref, land via audited tri-state ff -----------

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


def _seed_audit(capture_root, *, nonce="rn1", run_id="runI"):
    audit = enforce.RoutingAuditLog(capture_root, run_id)
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
FEAT = "refs/heads/feat-x"


def _collect(record, mgr, audit, *, paths, target=TEMP_REF, feature_ref=FEAT):
    intent_dir = Path(audit.path).parent / "work-product-intents"
    return er.collect_work_product(
        run_id="runI", session_name="sessI", target_ref=target,
        expected_target_sha="0" * 40, kind="code", registry=_Reg(record), manager=mgr,
        audit=audit, intent_dir=str(intent_dir), correlation_id="cI",
        promote_paths=paths, expected_feature_ref=feature_ref)


def _land(repo, audit, *, expected_ref, pre_sha, new_sha, temp_ref=TEMP_REF, **kw):
    """The audited PRODUCTION landing (`land_work_product` / the `land-work-product` CLI verb)
    — these tests exercise it. Returns (ok, why) mapped from the production result."""
    res = er.land_work_product(repo=str(repo), expected_ref=expected_ref, pre_sha=pre_sha,
                               new_sha=new_sha, temp_ref=temp_ref, run_id="runI",
                               audit=audit, **kw)
    if res["ok"]:
        return True, res["status"]
    return False, res["error"]["code"]


def _landings(audit):
    return [r for r in audit.records() if r.get("kind") == "landed_work_product"]


def _setup(repo, tmp_path, *, branch="feat-x"):
    """One collected work product on TEMP_REF, feature branch checked out, shared audit."""
    _git(repo, "checkout", "-qb", branch)
    mgr, h, record, base = _mk_worktree(repo, tmp_path)
    Path(h.path, "a.txt").write_text("CHANGED\n")
    Path(h.path, "blob.bin").write_bytes(BIN)
    audit = _seed_audit(tmp_path / "runs")
    res = _collect(record, mgr, audit, paths=["a.txt", "blob.bin"])
    assert res["ok"], res
    return audit, base, res["new_sha"]


def test_collect_and_land_end_to_end(repo, tmp_path):
    audit, base, new_sha = _setup(repo, tmp_path)
    ok, why = _land(repo, audit, expected_ref=FEAT, pre_sha=base, new_sha=new_sha)
    assert ok, why
    # filesystem materialized (pass-3 F1's exact worry): bytes on disk, clean status
    assert (repo / "blob.bin").read_bytes() == BIN
    assert (repo / "a.txt").read_text() == "CHANGED\n"
    assert _git(repo, "status", "--porcelain")[1].strip() == ""
    assert _git(repo, "rev-parse", "refs/heads/feat-x")[1].strip() == new_sha
    assert _git(repo, "rev-parse", "--verify", TEMP_REF)[0] != 0  # temp ref cleaned
    # D1: the landing is AUDITED — one landed_work_product record, fully bound
    lands = _landings(audit)
    assert len(lands) == 1
    rec = lands[0]
    assert rec["receipt_nonce"] == "rn1" and rec["feature_ref"] == FEAT
    assert rec["pre_sha"] == base and rec["new_sha"] == new_sha
    assert rec["temp_ref"] == TEMP_REF and rec["landing_status"] == "landed"
    assert rec["run_id"] == "runI" and rec["landing_version"] == 1


def test_collect_refuses_undeclared_path(repo, tmp_path):
    _git(repo, "checkout", "-qb", "feat-x")
    mgr, h, record, _base = _mk_worktree(repo, tmp_path)
    Path(h.path, "a.txt").write_text("CHANGED\n")
    Path(h.path, "smuggle.txt").write_text("nope\n")
    audit = _seed_audit(tmp_path / "runs")
    res = _collect(record, mgr, audit, paths=["a.txt"])
    assert res["ok"] is False
    assert res["error"]["code"] == "promote_refused"


def test_collect_kind_code_requires_expected_feature_ref(repo, tmp_path):
    # R5-B: the destination is authorized at COLLECT time — a code collect without the
    # feature ref (or with a non-refs/heads one) is malformed.
    _git(repo, "checkout", "-qb", "feat-x")
    mgr, h, record, _base = _mk_worktree(repo, tmp_path)
    Path(h.path, "a.txt").write_text("CHANGED\n")
    audit = _seed_audit(tmp_path / "runs")
    res = _collect(record, mgr, audit, paths=["a.txt"], feature_ref=None)
    assert res["ok"] is False and res["error"]["code"] == "invalid_expected_feature_ref"
    res2 = _collect(record, mgr, audit, paths=["a.txt"], feature_ref="feat-x")
    assert res2["ok"] is False and res2["error"]["code"] == "invalid_expected_feature_ref"


def test_collect_records_carry_expected_feature_ref(repo, tmp_path):
    audit, _base, new_sha = _setup(repo, tmp_path)
    recs = [r for r in audit.records()
            if r.get("kind") in ("work_product", "expected_work_product")
            and r.get("new_sha") == new_sha]
    assert len(recs) == 2
    assert all(r.get("expected_feature_ref") == FEAT for r in recs)


def test_land_requires_identity_or_no_audit(repo, tmp_path):
    audit, base, new_sha = _setup(repo, tmp_path)
    del audit  # the point: no identity supplied at all
    res = er.land_work_product(repo=str(repo), expected_ref=FEAT, pre_sha=base,
                               new_sha=new_sha, temp_ref=TEMP_REF)
    assert res["ok"] is False
    assert res["error"]["code"] == "landing_identity_required"


def test_land_no_audit_optout_is_pure_git(repo, tmp_path):
    # rev-2 adoption 2: --no-audit is the visible, greppable opt-out — pure-git landing,
    # nothing appended anywhere.
    audit, base, new_sha = _setup(repo, tmp_path)
    res = er.land_work_product(repo=str(repo), expected_ref=FEAT, pre_sha=base,
                               new_sha=new_sha, temp_ref=TEMP_REF, no_audit=True)
    assert res["ok"] and res["status"] == "landed"
    assert _git(repo, "rev-parse", "refs/heads/feat-x")[1].strip() == new_sha
    assert _landings(audit) == []


def test_land_no_audit_conflicts_with_identity(repo, tmp_path):
    audit, base, new_sha = _setup(repo, tmp_path)
    res = er.land_work_product(repo=str(repo), expected_ref=FEAT, pre_sha=base,
                               new_sha=new_sha, temp_ref=TEMP_REF, no_audit=True,
                               run_id="runI", audit=audit)
    assert res["ok"] is False
    assert res["error"]["code"] == "landing_audit_mode_conflict"


def test_land_refuses_detached_head(repo, tmp_path):
    audit, base, new_sha = _setup(repo, tmp_path)
    _git(repo, "checkout", "-q", "--detach", base)  # detached at the pre-task SHA
    ok, why = _land(repo, audit, expected_ref=FEAT, pre_sha=base, new_sha=new_sha)
    assert not ok and why == "landing_wrong_ref"


def test_land_refuses_other_branch_at_same_sha(repo, tmp_path):
    audit, base, new_sha = _setup(repo, tmp_path)
    _git(repo, "checkout", "-qb", "impostor")  # different branch, same SHA
    ok, why = _land(repo, audit, expected_ref=FEAT, pre_sha=base, new_sha=new_sha)
    assert not ok and why == "landing_wrong_ref"


def test_land_refuses_when_branch_advanced(repo, tmp_path):
    audit, base, new_sha = _setup(repo, tmp_path)
    (repo / "peer.txt").write_text("peer\n")
    _git(repo, "add", "peer.txt")
    _git(repo, "commit", "-qm", "peer advance")
    ok, why = _land(repo, audit, expected_ref=FEAT, pre_sha=base, new_sha=new_sha)
    assert not ok and why in ("landing_unexpected_sha", "landing_ff_refused")


def test_land_rerun_after_merge_is_already_landed(repo, tmp_path):
    # crash-after-merge recovery (#767 rev-4 F2 + R4-B): the temp ref is gone but the landing
    # record IS the heal — a second run succeeds idempotently, appending nothing new.
    audit, base, new_sha = _setup(repo, tmp_path)
    ok, why = _land(repo, audit, expected_ref=FEAT, pre_sha=base, new_sha=new_sha)
    assert ok, why
    ok2, why2 = _land(repo, audit, expected_ref=FEAT, pre_sha=base, new_sha=new_sha)
    assert ok2 and why2 == "already_landed"
    assert len(_landings(audit)) == 1  # dedup on the full immutable identity


def test_land_unauthorized_without_work_product(repo, tmp_path):
    # R3-A: the collect-time work_product record IS the authorization — an audit stream
    # without it refuses, whatever git says.
    audit, base, new_sha = _setup(repo, tmp_path)
    del audit
    foreign = _seed_audit(tmp_path / "runs2")  # receipt+obs but NO work_product record
    ok, why = _land(repo, foreign, expected_ref=FEAT, pre_sha=base, new_sha=new_sha)
    assert not ok and why == "landing_unauthorized"


def test_land_authorization_binds_pre_sha(repo, tmp_path):
    # R4-C: the authorizing work_product's inner base_sha must equal --pre-sha on BOTH
    # tri-state paths — a first audit append on an already-landed branch with a fabricated
    # pre_sha refuses instead of healing a lie.
    audit, base, new_sha = _setup(repo, tmp_path)
    res = er.land_work_product(repo=str(repo), expected_ref=FEAT, pre_sha=base,
                               new_sha=new_sha, temp_ref=TEMP_REF, no_audit=True)
    assert res["ok"]  # merge landed pure-git; no landing record exists
    fabricated = "f" * 40
    ok, why = _land(repo, audit, expected_ref=FEAT, pre_sha=fabricated, new_sha=new_sha)
    assert not ok and why == "landing_unauthorized"


def test_land_already_landed_without_record_or_temp_ref_refuses(repo, tmp_path):
    # R4-B: temp ref absent AND no landing record → cannot authorize the heal; refuse loudly.
    audit, base, new_sha = _setup(repo, tmp_path)
    res = er.land_work_product(repo=str(repo), expected_ref=FEAT, pre_sha=base,
                               new_sha=new_sha, temp_ref=TEMP_REF, no_audit=True)
    assert res["ok"]  # merged + temp ref CAS-deleted, no record anywhere
    ok, why = _land(repo, audit, expected_ref=FEAT, pre_sha=base, new_sha=new_sha)
    assert not ok and why == "landing_record_missing"


def test_land_feature_ref_replay_refused(repo, tmp_path):
    # R5-B: replaying a valid work product onto a DIFFERENT branch at the same base refuses —
    # the destination is authorized by the collect-time record, not the landing caller.
    audit, base, new_sha = _setup(repo, tmp_path)
    _git(repo, "checkout", "-qb", "feat-y")  # same base, different branch
    ok, why = _land(repo, audit, expected_ref="refs/heads/feat-y", pre_sha=base,
                    new_sha=new_sha)
    assert not ok and why == "landing_feature_ref_mismatch"


def test_land_temp_ref_mismatch(repo, tmp_path):
    # R3-A: on the fresh path the temp ref must resolve to exactly --new-sha.
    audit, base, new_sha = _setup(repo, tmp_path)
    _git(repo, "update-ref", "-d", TEMP_REF)
    ok, why = _land(repo, audit, expected_ref=FEAT, pre_sha=base, new_sha=new_sha)
    assert not ok and why == "landing_temp_ref_mismatch"


def test_land_unrelated_dirt_lands(repo, tmp_path):
    # D2 (the probe finding): the REAL checkout is never clean — unrelated tracked
    # modifications and untracked files must not block the landing, and must survive it.
    audit, base, new_sha = _setup(repo, tmp_path)
    (repo / "unrelated.txt").write_text("untracked dirt\n")
    (repo / "tracked2.txt").write_text("v1\n")
    _git(repo, "add", "tracked2.txt")
    _git(repo, "commit", "-qm", "tracked2")
    head2 = _git(repo, "rev-parse", "HEAD")[1].strip()
    del head2  # branch advanced — recollect against the new base instead
    # simplest honest construction: dirty files that are NOT in the changed set, branch
    # still at the recorded pre-task SHA
    _git(repo, "reset", "-q", "--hard", base)
    (repo / "unrelated.txt").write_text("untracked dirt\n")
    ok, why = _land(repo, audit, expected_ref=FEAT, pre_sha=base, new_sha=new_sha)
    assert ok, why
    assert (repo / "unrelated.txt").read_text() == "untracked dirt\n"  # dirt preserved
    assert (repo / "a.txt").read_text() == "CHANGED\n"


def test_land_colliding_tracked_dirt_refuses(repo, tmp_path):
    # D2: a locally-modified TRACKED file inside the changed set refuses, naming the path.
    audit, base, new_sha = _setup(repo, tmp_path)
    (repo / "a.txt").write_text("local edit\n")  # a.txt IS in the changed set
    res = er.land_work_product(repo=str(repo), expected_ref=FEAT, pre_sha=base,
                               new_sha=new_sha, temp_ref=TEMP_REF, run_id="runI", audit=audit)
    assert res["ok"] is False
    assert res["error"]["code"] == "landing_dirty_paths"
    assert "a.txt" in res["error"]["message"]


def test_land_colliding_untracked_dirt_refuses(repo, tmp_path):
    # D2: an UNTRACKED file at a path the landing would materialize refuses, naming the path.
    audit, base, new_sha = _setup(repo, tmp_path)
    (repo / "blob.bin").write_bytes(b"squatter")  # blob.bin IS in the changed set (untracked here)
    res = er.land_work_product(repo=str(repo), expected_ref=FEAT, pre_sha=base,
                               new_sha=new_sha, temp_ref=TEMP_REF, run_id="runI", audit=audit)
    assert res["ok"] is False
    assert res["error"]["code"] == "landing_dirty_paths"
    assert "blob.bin" in res["error"]["message"]


class _FlakyAudit:
    """Wraps a real audit; the FIRST append_landed_work_product raises (fault injection)."""

    def __init__(self, inner):
        self._inner = inner
        self.failures_left = 1

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def append_landed_work_product(self, **kw):
        if self.failures_left > 0:
            self.failures_left -= 1
            raise OSError("injected append failure")
        return self._inner.append_landed_work_product(**kw)


def test_land_append_failure_retains_temp_ref_then_retry_heals(repo, tmp_path):
    # R4-B: append failure AFTER the merge → exit 5, temp ref RETAINED, merge stands;
    # the retry heals (already_landed path appends the record, then CAS-deletes).
    audit, base, new_sha = _setup(repo, tmp_path)
    flaky = _FlakyAudit(audit)
    res = er.land_work_product(repo=str(repo), expected_ref=FEAT, pre_sha=base,
                               new_sha=new_sha, temp_ref=TEMP_REF, run_id="runI", audit=flaky)
    assert res["ok"] is False and res["error"]["code"] == "landing_append_failed"
    assert res["exit"] == 5
    assert _git(repo, "rev-parse", "HEAD")[1].strip() == new_sha  # merge stands
    assert _git(repo, "rev-parse", "--verify", TEMP_REF)[0] == 0  # temp ref retained
    ok, why = _land(repo, flaky, expected_ref=FEAT, pre_sha=base, new_sha=new_sha)
    assert ok and why == "already_landed"
    assert len(_landings(audit)) == 1
    assert _git(repo, "rev-parse", "--verify", TEMP_REF)[0] != 0  # cleaned on the heal


def test_land_identity_conflict_refuses_at_append(repo, tmp_path):
    # R3-B: a record agreeing on (receipt_nonce, new_sha) but differing on ANY other
    # immutable field is an IMMEDIATE append-time integrity error — exit 5, temp ref retained.
    audit, base, new_sha = _setup(repo, tmp_path)
    audit._write_locked({  # pylint: disable=protected-access
        "kind": "landed_work_product", "landing_version": 1, "receipt_nonce": "rn1",
        "feature_ref": "refs/heads/OTHER", "pre_sha": base, "new_sha": new_sha,
        "temp_ref": TEMP_REF, "landing_status": "landed", "run_id": "runI", "ts": 1})
    res = er.land_work_product(repo=str(repo), expected_ref=FEAT, pre_sha=base,
                               new_sha=new_sha, temp_ref=TEMP_REF, run_id="runI", audit=audit)
    assert res["ok"] is False and res["error"]["code"] == "landing_identity_conflict"
    assert res["exit"] == 5
    assert _git(repo, "rev-parse", "--verify", TEMP_REF)[0] == 0  # retained for forensics


def test_land_same_nonce_different_new_sha_refuses_pre_merge(repo, tmp_path):
    # #762 Step-11 r2-2: landing and reconcile must agree on the conflict identity — a
    # landed_work_product for the SAME receipt nonce at a DIFFERENT new_sha is a conflicting
    # identity (reconcile already calls it landing_conflict), refused BEFORE any git mutation,
    # not silently ignored until post-merge reconciliation.
    audit, base, new_sha = _setup(repo, tmp_path)
    audit._write_locked({  # pylint: disable=protected-access
        "kind": "landed_work_product", "landing_version": 1, "receipt_nonce": "rn1",
        "feature_ref": FEAT, "pre_sha": base, "new_sha": "f" * 40,
        "temp_ref": TEMP_REF, "landing_status": "landed", "run_id": "runI", "ts": 1})
    res = er.land_work_product(repo=str(repo), expected_ref=FEAT, pre_sha=base,
                               new_sha=new_sha, temp_ref=TEMP_REF, run_id="runI", audit=audit)
    assert res["ok"] is False and res["error"]["code"] == "landing_identity_conflict"
    # pre-mutation: the branch did NOT advance and the temp ref is retained
    assert _git(repo, "rev-parse", "refs/heads/feat-x")[1].strip() == base
    assert _git(repo, "rev-parse", "--verify", TEMP_REF)[0] == 0


def test_land_path_unsafe_run_id_refuses(repo, tmp_path):
    # #762 Step-11 r2-5: RoutingAuditLog sanitizes run_id ('a/b' and 'a_b' address the same
    # audit directory), so a path-unsafe run_id could consume another run stream's
    # authorization — refused at entry, before any audit read or git mutation.
    audit, base, new_sha = _setup(repo, tmp_path)
    res = er.land_work_product(repo=str(repo), expected_ref=FEAT, pre_sha=base,
                               new_sha=new_sha, temp_ref=TEMP_REF, run_id="run/../I",
                               audit=audit)
    assert res["ok"] is False and res["error"]["code"] == "landing_invalid_input"
    assert res["exit"] == 2
    assert _git(repo, "rev-parse", "refs/heads/feat-x")[1].strip() == base


def _write_workspace(tmp_path, project="canon"):
    ws = tmp_path / ".rawgentic_workspace.json"
    ws.write_text(json.dumps({"version": 1, "projects": [
        {"name": project, "path": f"./{project}", "active": True, "configured": True}]}))
    return ws


def test_land_repo_mismatch_against_workspace(repo, tmp_path):
    # R3-A: in audited mode the repository derives from workspace+project; a --repo that
    # canonically differs refuses.
    audit, base, new_sha = _setup(repo, tmp_path)
    del audit
    ws = _write_workspace(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    res = er.land_work_product(repo=str(other), expected_ref=FEAT, pre_sha=base,
                               new_sha=new_sha, temp_ref=TEMP_REF, run_id="runI",
                               workspace=str(ws), project="canon")
    assert res["ok"] is False
    assert res["error"]["code"] == "landing_repo_mismatch"


def test_land_cli_end_to_end_audited(repo, tmp_path):
    # quality bar: the CLI surface exercised via subprocess exactly as an orchestrator calls
    # it — identity args required, audit written under the resolved repo root.
    _git(repo, "checkout", "-qb", "feat-x")
    mgr, h, record, base = _mk_worktree(repo, tmp_path)
    Path(h.path, "a.txt").write_text("CHANGED\n")
    audit = _seed_audit(repo / ".rawgentic" / "runs")  # where the CLI will look
    res = _collect(record, mgr, audit, paths=["a.txt"])
    assert res["ok"], res
    ws = _write_workspace(tmp_path)
    rc, out, err = _run([sys.executable, str(HOOKS / "executor_routing_lib.py"),
                         "land-work-product", "--repo", str(repo),
                         "--expected-ref", FEAT, "--pre-sha", base,
                         "--new-sha", res["new_sha"], "--temp-ref", TEMP_REF,
                         "--run-id", "runI", "--workspace", str(ws), "--project", "canon"])
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["ok"] and payload["status"] == "landed"
    assert _git(repo, "rev-parse", "refs/heads/feat-x")[1].strip() == res["new_sha"]
    assert _git(repo, "rev-parse", "--verify", TEMP_REF)[0] != 0  # temp ref cleaned
    assert len(_landings(audit)) == 1


def test_land_cli_requires_identity_or_no_audit(repo, tmp_path):
    audit, base, new_sha = _setup(repo, tmp_path)
    del audit
    rc, out, _err = _run([sys.executable, str(HOOKS / "executor_routing_lib.py"),
                          "land-work-product", "--repo", str(repo),
                          "--expected-ref", FEAT, "--pre-sha", base,
                          "--new-sha", new_sha, "--temp-ref", TEMP_REF])
    assert rc == 2
    assert json.loads(out)["error"]["code"] == "landing_identity_required"
    # the greppable opt-out works
    rc2, out2, _e2 = _run([sys.executable, str(HOOKS / "executor_routing_lib.py"),
                           "land-work-product", "--repo", str(repo),
                           "--expected-ref", FEAT, "--pre-sha", base,
                           "--new-sha", new_sha, "--temp-ref", TEMP_REF, "--no-audit"])
    assert rc2 == 0
    assert json.loads(out2)["status"] == "landed"
