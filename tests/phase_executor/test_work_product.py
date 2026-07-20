"""#469 W6 Task 2 — derive_work_product: executor-derived git evidence (OQ-4), the
content_tree_sha content commitment (adversarial H2), promotion reconcile-not-copy, and the
v2-only-field-on-v1 freeze proof. Real git in a tmp repo (the test_worktree_* harness)."""
from __future__ import annotations

import os
import re
import subprocess

import pytest

from phase_executor import contract
from phase_executor import worktree as wt

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


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


def _ident(attempt="0-wwww1111"):
    return wt.WorktreeIdentity(run_id="run1", seat="build", attempt=attempt)


def _base(repo):
    return _git(repo, "rev-parse", "HEAD")[1].strip()


def _wt(repo, mgr, tmp_path, attempt="0-wwww1111"):
    return mgr.create(str(repo), _ident(attempt), _base(repo), root=str(tmp_path / "wtroot"))


def _obs_with(work_product, *, parsed_payload):
    """Build a minimal ok Observation carrying a work_product + an arbitrary parsed_payload."""
    return contract.Observation(
        run_id="r", attempt_id="0-a", seat="build", engine="claude", transport="native",
        requested_model="claude-opus-4-8", actual_model="claude-opus-4-8", prompt_hash="sha256:x",
        context_hashes=[], usage={"input": 1, "output": 1}, timing_ms=1, queued_ms=0,
        process={"exit_code": 0, "timed_out": False}, parse_status=contract.OK,
        parsed_payload=parsed_payload, raw_capture_path=None, fallback_reason=None,
        routing_config_digest="sha256:d", work_product=work_product,
    )


def test_derive_work_product_git_evidence(repo, mgr, tmp_path):
    h = _wt(repo, mgr, tmp_path)
    (open(os.path.join(h.path, "a.txt"), "w")).write("CHANGED\n")   # dirty a tracked file
    (open(os.path.join(h.path, "new.txt"), "w")).write("added\n")    # untracked
    wp = contract.derive_work_product(mgr, h, kind="code")
    assert wp["base_sha"] == h.base_sha
    assert _SHA_RE.match(wp["head_sha"]) and wp["head_sha"] == _base(repo)  # HEAD == base (no commit)
    assert _SHA_RE.match(wp["content_tree_sha"])
    assert wp["changed_paths"] == ["a.txt", "new.txt"]  # sorted, includes dirty + untracked
    assert wp["worktree_path"] == h.path
    assert wp["promotion_status"] == "not_attempted"
    assert wp["documents"] == [] and wp["tests"] == []
    contract.validate_observation(_obs_with(wp, parsed_payload="OK").to_dict())


def test_content_tree_sha_binds_dirty_untracked(repo, mgr, tmp_path):
    """Adversarial H2: an untracked file's BYTES change while head_sha + changed_path NAMES stay
    identical — content_tree_sha MUST flip (names alone can't establish produced content)."""
    h = _wt(repo, mgr, tmp_path)
    (open(os.path.join(h.path, "new.txt"), "w")).write("v1\n")
    wp1 = contract.derive_work_product(mgr, h, kind="code")
    (open(os.path.join(h.path, "new.txt"), "w")).write("v2-different-bytes\n")  # same path, new bytes
    wp2 = contract.derive_work_product(mgr, h, kind="code")
    assert wp1["head_sha"] == wp2["head_sha"]              # HEAD unchanged
    assert wp1["changed_paths"] == wp2["changed_paths"]    # path NAMES unchanged
    assert wp1["content_tree_sha"] != wp2["content_tree_sha"]  # content commitment flips


def test_work_product_ignores_lying_self_report(repo, mgr, tmp_path):
    """OQ-4: a fabricated parsed_payload claiming false SHAs/paths never affects work_product —
    the git evidence is executor-derived (derive_work_product takes no self-report)."""
    h = _wt(repo, mgr, tmp_path)
    (open(os.path.join(h.path, "real.txt"), "w")).write("real\n")
    wp = contract.derive_work_product(mgr, h, kind="code")
    lying = {"changed_files": ["totally_fake.py", "lies.txt"], "commit": "f" * 40,
             "base_sha": "0" * 40, "content_tree_sha": "deadbeef"}
    obs = _obs_with(wp, parsed_payload=lying)
    d = obs.to_dict()
    contract.validate_observation(d)
    assert d["work_product"]["changed_paths"] == ["real.txt"]          # git truth, not the lie
    assert d["work_product"]["content_tree_sha"] == wp["content_tree_sha"]
    assert "totally_fake.py" not in d["work_product"]["changed_paths"]
    assert d["parsed_payload"] == lying  # the provider's claim is preserved, just never load-bearing


def test_derive_reconciles_matching_promotion(repo, mgr, tmp_path):
    h = _wt(repo, mgr, tmp_path)
    (open(os.path.join(h.path, "new.txt"), "w")).write("x\n")
    res = mgr.promote(h, target_ref="refs/heads/integration", expected_target_sha="0" * 40,
                      message="promote")
    assert res.promoted is True
    wp = contract.derive_work_product(mgr, h, kind="code", promotion=res)
    assert wp["promotion_status"] == "promoted"
    assert wp["base_sha"] == res.base_sha and wp["head_sha"] == res.head_sha
    assert set(wp["changed_paths"]) == set(res.changed_paths)


def test_derive_refuses_mismatched_promotion(repo, mgr, tmp_path):
    h = _wt(repo, mgr, tmp_path)
    (open(os.path.join(h.path, "new.txt"), "w")).write("x\n")
    real = mgr.promote(h, target_ref="refs/heads/integration", expected_target_sha="0" * 40,
                       message="promote")
    # a PromotionResult whose changed_paths lie about what was produced -> reconcile refuses
    liar = wt.PromotionResult(promoted=True, new_target_sha=real.new_target_sha,
                              base_sha=real.base_sha, head_sha=real.head_sha,
                              changed_paths=("phantom.py",), reason="")
    with pytest.raises(ValueError, match="reconcile"):
        contract.derive_work_product(mgr, h, kind="code", promotion=liar)
    # a wrong base_sha also refuses
    liar2 = wt.PromotionResult(promoted=False, base_sha="0" * 40, head_sha=real.head_sha,
                               changed_paths=real.changed_paths, reason="x")
    with pytest.raises(ValueError, match="reconcile"):
        contract.derive_work_product(mgr, h, kind="code", promotion=liar2)


def test_not_promoted_status_from_refused_promotion(repo, mgr, tmp_path):
    """A base-stale/CAS refusal (promoted=False) with reconciling evidence -> 'not_promoted'."""
    base = _base(repo)
    _git(repo, "branch", "integration", base)
    h = _wt(repo, mgr, tmp_path)
    (open(os.path.join(h.path, "wt.txt"), "w")).write("wt\n")
    # advance integration so promote refuses (base stale)
    _git(repo, "checkout", "-q", "integration")
    (repo / "peer.txt").write_text("peer\n")
    _git(repo, "add", "peer.txt")
    _git(repo, "commit", "-qm", "peer")
    peer = _git(repo, "rev-parse", "integration")[1].strip()
    res = mgr.promote(h, target_ref="refs/heads/integration", expected_target_sha=peer,
                      message="stale")
    assert res.promoted is False
    wp = contract.derive_work_product(mgr, h, kind="code", promotion=res)
    assert wp["promotion_status"] == "not_promoted"


def test_documents_must_be_executor_verified_changed_paths(repo, mgr, tmp_path):
    h = _wt(repo, mgr, tmp_path)
    (open(os.path.join(h.path, "doc.md"), "w")).write("# doc\n")
    # a document that IS a changed path -> ok
    wp = contract.derive_work_product(mgr, h, kind="docs", documents=["doc.md"])
    assert wp["documents"] == ["doc.md"]
    # a document NOT among changed paths -> loud refuse (unverified claim)
    with pytest.raises(ValueError, match="changed paths"):
        contract.derive_work_product(mgr, h, kind="docs", documents=["not_produced.md"])


def test_work_product_on_v1_document_rejected(repo, mgr, tmp_path):
    """Freeze proof (test 1b/4b): a work_product (a v2-only field) on a doc DECLARING '1' is
    rejected — dispatch validates it against frozen v1, which has no such property."""
    h = _wt(repo, mgr, tmp_path)
    (open(os.path.join(h.path, "x.txt"), "w")).write("x\n")
    wp = contract.derive_work_product(mgr, h, kind="code")
    obs = _obs_with(wp, parsed_payload="OK").to_dict()
    obs["schema_version"] = "1"  # declare v1 but carry a v2-only field
    import jsonschema
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(obs)
