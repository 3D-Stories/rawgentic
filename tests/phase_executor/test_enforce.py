"""#425 (E2) routing enforcement: check_pre / verify_post / RoutingAuditLog / reconcile_run.

Fixture-tested against real E1 golden envelopes + a golden routing-audit.jsonl. Enforcement is a
security boundary — every check accumulates violations and fails closed.
"""
import json
import pathlib

import pytest

from phase_executor import contract, routing
from phase_executor import enforce
from phase_executor.enforce import PreReceipt, check_pre, target_identity

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def _lane(pool, provider="anthropic", transport="native", auth="subscription_oauth", cred=None):
    return {"provider": provider, "transport": transport, "auth_mode": auth, "credential_ref": cred, "pool": pool}


def _snapshot():
    table = {
        "schema_version": "1",
        "pools": {"claude": {"concurrency": 2}, "codex": {"concurrency": 4}, "zhipu": {"concurrency": 2}},
        # #464 Task 3 (§D): table-declared enforced-roles set — check_pre reads it from the snapshot.
        # build+review are recognized here so their per-role requirements fire (not unrecognized_role).
        "policy": {"enforced_roles": ["review", "build"]},
        "seats": {
            "review": {
                "role": "review",
                "primary": {"model": "claude-fable-5", "lane": _lane("claude")},
                "chain": [
                    {"model": "gpt-5.6-sol", "lane": _lane("codex", provider="openai")},
                    {"model": "claude-sonnet-5", "lane": _lane("claude")},
                ],
            },
            "build": {
                "role": "build",
                "primary": {"model": "claude-sonnet-5", "lane": _lane("claude")},
                "chain": [{"model": "gpt-5.6-terra", "lane": _lane("codex", provider="openai")}],
            },
        },
        "forbidden_combinations": [
            {"model_pattern": "haiku", "reason": "never Haiku"},
            {"rule": "cross_model_author", "reason": "reviewer != author engine"},
        ],
    }
    return routing.RoutingSnapshot.from_table(table)


def _target(model, lane):
    return {"model": model, "lane": lane}


def _build_target():
    """The build seat's declared primary target (claude-sonnet-5 via the claude lane)."""
    return _target("claude-sonnet-5", _lane("claude"))


def _attestation(*, seat="build", target=None, correlation_id="c", outcome="single",
                 policy_digest="sha256:pol"):
    """A GateAttestation minted bound to the given launch (input_digest recomputed from the args)."""
    target = _build_target() if target is None else target
    return enforce.GateAttestation(
        gate_outcome=outcome,
        policy_digest=policy_digest,
        input_digest=enforce.launch_input_digest(seat, target, correlation_id),
    )


# ---- target_identity ----

def test_target_identity_tuple():
    t = _target("claude-opus-4-8[1m]", _lane("claude", cred="ACCT_A"))
    tid = target_identity(t)
    # canonicalized model + full lane identity
    assert tid == ("claude-opus-4-8", "anthropic", "native", "subscription_oauth", "claude", "ACCT_A", None)


# ---- check_pre ----

def test_check_pre_pass_mints_receipt():
    snap = _snapshot()
    t = _target("claude-fable-5", _lane("claude"))
    r = check_pre("review", t, snap, correlation_id="wf2-step4", attempt_id="0", author_provider="openai")
    assert isinstance(r, PreReceipt)
    assert r.verdict == "pass" and not r.violations
    assert r.nonce and r.seat == "review" and r.correlation_id == "wf2-step4"
    assert r.config_digest == snap.config_digest
    assert r.target_identity == target_identity(t)


def test_check_pre_off_chain_allowed_model_wrong_provider():
    snap = _snapshot()
    # fable-5 is a declared review model, but through the WRONG provider/pool -> off_chain
    t = _target("claude-fable-5", _lane("codex", provider="openai"))
    r = check_pre("review", t, snap, correlation_id="c", attempt_id="0", author_provider="openai")
    assert r.verdict == "fail"
    assert any(v.startswith("off_chain") for v in r.violations)


def test_check_pre_forbidden_never_haiku():
    snap = _snapshot()
    t = _target("claude-haiku-4-5", _lane("claude"))
    r = check_pre("build", t, snap, correlation_id="c", attempt_id="0")
    # off_chain AND forbidden both accumulate (haiku isn't in build's chain either)
    assert r.verdict == "fail"
    assert any(v.startswith("forbidden") for v in r.violations)


def test_check_pre_review_requires_author_provider():
    snap = _snapshot()
    t = _target("claude-fable-5", _lane("claude"))
    r = check_pre("review", t, snap, correlation_id="c", attempt_id="0", author_provider=None)
    assert r.verdict == "fail"
    assert "author_provider_missing" in r.violations


def test_check_pre_build_valid_attestation_passes():
    """#464 §E (rewrites the pre-#429 unconditional deny): a build seat with a valid, launch-bound,
    single-outcome GateAttestation PASSES; the receipt carries role + gate_outcome + gate_input_digest,
    and gate_digest is repurposed to the attestation's policy_digest."""
    snap = _snapshot()
    t = _build_target()
    att = _attestation(seat="build", target=t, correlation_id="c", outcome="single",
                       policy_digest="sha256:pol")
    r = check_pre("build", t, snap, correlation_id="c", attempt_id="0", attestation=att)
    assert r.verdict == "pass" and not r.violations
    assert r.role == "build"
    assert r.gate_outcome == "single"
    assert r.gate_input_digest == enforce.launch_input_digest("build", t, "c")
    assert r.gate_digest == "sha256:pol"  # policy_digest repurposes the gate_digest receipt slot


def test_check_pre_build_missing_attestation_gate_missing():
    """#464 §E (rewrites the pre-#429 deny): a build seat with NO attestation fails CLOSED with
    gate_missing — the build path may only proceed on authenticated gate evidence."""
    snap = _snapshot()
    t = _build_target()
    r = check_pre("build", t, snap, correlation_id="c", attempt_id="0")
    assert r.verdict == "fail" and "gate_missing" in r.violations
    assert r.role == "build" and r.gate_outcome is None and r.gate_input_digest is None


def test_check_pre_unknown_seat_raises():
    snap = _snapshot()
    with pytest.raises(routing.RoutingError):
        check_pre("ghost", _target("x", _lane("claude")), snap, correlation_id="c", attempt_id="0")


# ---- verify_post ----

def _obs_dict(**over):
    d = {"schema_version": "1", "run_id": "r", "attempt_id": "a", "seat": "review", "engine": "claude",
         "transport": "native", "requested_model": "claude-opus-4-8", "actual_model": "claude-opus-4-8",
         "prompt_hash": "sha256:x", "context_hashes": [], "usage": {"input": 1, "output": 1},
         "timing_ms": 1, "queued_ms": 0, "process": {"exit_code": 0, "timed_out": False},
         "parse_status": "ok", "parsed_payload": None, "raw_capture_path": None, "fallback_reason": None,
         "routing_config_digest": "sha256:d"}
    d.update(over)
    return d


def test_verify_post_ok_match():
    pc = enforce.verify_post(_obs_dict())
    assert pc.ok and pc.verified and pc.retryable is False


def test_verify_post_mismatch_non_retryable():
    pc = enforce.verify_post(_obs_dict(actual_model="claude-sonnet-5"))
    assert not pc.ok and not pc.verified
    assert pc.reason == "requested_actual_mismatch" and pc.retryable is False


def test_verify_post_identity_missing_on_ok():
    pc = enforce.verify_post(_obs_dict(actual_model=None))
    assert not pc.ok and not pc.verified and pc.reason == "identity_missing"


def test_verify_post_canonicalizes_provider_prefix_and_bracket():
    pc = enforce.verify_post(_obs_dict(requested_model="claude-opus-4-8",
                                       actual_model="us.anthropic.claude-opus-4-8[1m]"))
    assert pc.ok and pc.verified  # same family through canonicalization


def test_verify_post_non_ok_is_not_enforcement_breach():
    pc = enforce.verify_post(_obs_dict(parse_status="launch_error", actual_model=None, usage=None))
    assert pc.ok and not pc.verified and pc.reason == "launch_error"


def test_verify_post_accepts_observation_object():
    obs = contract.Observation(
        run_id="r", attempt_id="a", seat="review", engine="claude", transport="native",
        requested_model="glm-5.2", actual_model="glm-5.2", prompt_hash="sha256:x",
        usage={"input": 1, "output": 1}, timing_ms=1, queued_ms=0,
        process={"exit_code": 0, "timed_out": False}, parse_status="ok", parsed_payload=None,
        raw_capture_path=None, fallback_reason=None, routing_config_digest="sha256:d")
    pc = enforce.verify_post(obs)
    assert pc.ok and pc.verified


def test_verify_post_kukakuka_fixture_verified():
    d = json.loads((FIXTURES / "kukakuka-observation.json").read_text())
    pc = enforce.verify_post(d)  # glm-5.2 == glm-5.2 (ccr transport, provider-attested)
    assert pc.ok and pc.verified


# ---- RoutingAuditLog + audited_digests ----

def _receipt(nonce="n1", verdict="pass", **over):
    base = dict(nonce=nonce, seat="review", correlation_id="c1", attempt_id="0",
                target_identity=("claude-opus-4-8", "anthropic", "native", "subscription_oauth", "claude", None, None),
                config_digest="sha256:d", gate_digest=None, author_provider="openai",
                verdict=verdict, violations=())
    base.update(over)
    return PreReceipt(**base)


def _obs_with_lane(**over):
    d = _obs_dict(**over)
    d["dispatched_lane"] = {"provider": "anthropic", "transport": "native",
                            "auth_mode": "subscription_oauth", "pool": "claude", "credential_ref": None}
    return d


def test_audit_log_constructor_containment(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path, "run-1")  # ok
    assert tmp_path.resolve() in log.path.resolve().parents
    # an all-dot / empty run_id is rejected outright by sanitize_component
    with pytest.raises(ValueError):
        enforce.RoutingAuditLog(tmp_path, "..")
    # a name carrying traversal chars is NEUTRALIZED to a safe single component, never an escape
    log2 = enforce.RoutingAuditLog(tmp_path, "../escape")
    assert tmp_path.resolve() in log2.path.resolve().parents


def test_audit_log_append_and_records_roundtrip(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path, "run-1")
    r = _receipt()
    log.append_receipt(r)
    log.append_observation(_obs_with_lane(), receipt=r)
    log.append_epoch("sha256:d", "sha256:e")
    recs = log.records()
    kinds = [x["kind"] for x in recs]
    assert kinds == ["receipt", "observation", "epoch"]
    assert recs[1]["receipt_nonce"] == "n1"
    assert recs[1]["observation"]["dispatched_lane"]["pool"] == "claude"
    assert recs[2]["seq"] == 1


def test_append_observation_fail_closed_missing_dispatched_lane(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path, "run-1")
    with pytest.raises(ValueError):
        log.append_observation(_obs_dict(), receipt=_receipt())  # no dispatched_lane -> refuse


def test_append_observation_rejects_schema_invalid(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path, "run-1")
    import jsonschema
    bad = _obs_with_lane(parse_status="ok", actual_model=None)  # ok requires actual_model
    with pytest.raises(jsonschema.ValidationError):
        log.append_observation(bad, receipt=_receipt())


def test_records_fail_closed_unknown_kind(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path, "run-1")
    log.path.write_text('{"kind":"bogus","x":1}\n')
    with pytest.raises(ValueError):
        log.records()


def test_records_fail_closed_bad_verdict(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path, "run-1")
    log.path.write_text('{"kind":"receipt","nonce":"n","seat":"review","correlation_id":"c","attempt_id":"0","target_identity":[],"config_digest":"d","verdict":"maybe"}\n')
    with pytest.raises(ValueError):
        log.records()


def test_records_fail_closed_malformed_json(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path, "run-1")
    log.path.write_text('{not json}\n')
    with pytest.raises(ValueError):
        log.records()


def test_append_park_and_records_roundtrip(tmp_path):
    # #637 (epic #635 C4)
    log = enforce.RoutingAuditLog(tmp_path, "run-1")
    log.append_park(run_id="run1", task_id="t1", design_version="v2",
                    stash_name="rawgentic-parked:run1:v2:t1", worktree_path="/wt/path",
                    parked=True, stash_oid="abc123")
    recs = log.records()
    assert recs[0]["kind"] == "park"
    assert recs[0]["stash_name"] == "rawgentic-parked:run1:v2:t1"
    assert recs[0]["parked"] is True
    assert recs[0]["stash_oid"] == "abc123"


def test_append_park_recorded_even_when_nothing_parked(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path, "run-1")
    log.append_park(run_id="run1", task_id="t1", design_version="v2",
                    stash_name="rawgentic-parked:run1:v2:t1", worktree_path="/wt/path",
                    parked=False)
    recs = log.records()
    assert recs[0]["parked"] is False
    assert recs[0]["stash_oid"] is None


def test_records_fail_closed_park_bad_stash_oid_type(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path, "run-1")
    log.path.write_text(
        '{"kind":"park","run_id":"r1","task_id":"t1","design_version":"v2",'
        '"stash_name":"s","worktree_path":"/wt","parked":true,"stash_oid":123}\n')
    with pytest.raises(ValueError, match="stash_oid"):
        log.records()


def test_records_fail_closed_park_missing_field(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path, "run-1")
    log.path.write_text('{"kind":"park","run_id":"r1","task_id":"t1","design_version":"v2","stash_name":"s","parked":true}\n')
    with pytest.raises(ValueError, match="missing fields"):
        log.records()


def test_records_fail_closed_park_bad_parked_type(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path, "run-1")
    log.path.write_text(
        '{"kind":"park","run_id":"r1","task_id":"t1","design_version":"v2",'
        '"stash_name":"s","worktree_path":"/wt","parked":"yes"}\n')
    with pytest.raises(ValueError, match="not a bool"):
        log.records()


def test_records_reads_legacy_park_record_missing_stash_oid_key(tmp_path):
    # Step-11 review: the reader must stay backward-compatible with a park record
    # written before stash_oid existed (the key entirely absent, not merely null).
    log = enforce.RoutingAuditLog(tmp_path, "run-1")
    log.path.write_text(
        '{"kind":"park","run_id":"r1","task_id":"t1","design_version":"v2",'
        '"stash_name":"s","worktree_path":"/wt","parked":true}\n')
    recs = log.records()
    assert recs[0]["parked"] is True
    assert "stash_oid" not in recs[0]


def test_append_park_rejects_parked_true_with_no_stash_oid(tmp_path):
    # Step-11 review finding #2: the WRITER, not just the reader, must refuse an
    # inconsistent record -- parked=True with a missing/empty OID is a caller bug
    # upstream, not a valid audit line.
    log = enforce.RoutingAuditLog(tmp_path, "run-1")
    with pytest.raises(ValueError, match="parked=True requires a non-empty stash_oid"):
        log.append_park(run_id="run1", task_id="t1", design_version="v2",
                        stash_name="s", worktree_path="/wt", parked=True, stash_oid=None)
    assert log.records() == []


def test_append_park_rejects_parked_false_with_stash_oid(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path, "run-1")
    with pytest.raises(ValueError, match="parked=False must carry stash_oid=None"):
        log.append_park(run_id="run1", task_id="t1", design_version="v2",
                        stash_name="s", worktree_path="/wt", parked=False, stash_oid="abc123")
    assert log.records() == []


def test_audited_digests_valid_chain(tmp_path):
    recs = [{"kind": "epoch", "seq": 1, "from": "sha256:a", "to": "sha256:b"},
            {"kind": "epoch", "seq": 2, "from": "sha256:b", "to": "sha256:c"}]
    assert enforce.audited_digests(recs, "sha256:a") == frozenset({"sha256:a", "sha256:b", "sha256:c"})


def test_audited_digests_rejects_gap_repeat_reorder_fork():
    # gap: seq jumps 1 -> 3
    with pytest.raises(ValueError):
        enforce.audited_digests([{"kind": "epoch", "seq": 1, "from": "a", "to": "b"},
                                 {"kind": "epoch", "seq": 3, "from": "b", "to": "c"}], "a")
    # broken chain (fork/reorder): second from != prev to
    with pytest.raises(ValueError):
        enforce.audited_digests([{"kind": "epoch", "seq": 1, "from": "a", "to": "b"},
                                 {"kind": "epoch", "seq": 2, "from": "X", "to": "c"}], "a")
    # first from != initial
    with pytest.raises(ValueError):
        enforce.audited_digests([{"kind": "epoch", "seq": 1, "from": "b", "to": "c"}], "a")


def test_audit_log_concurrent_append_integration(tmp_path):
    import threading
    log = enforce.RoutingAuditLog(tmp_path, "run-cc")
    def worker(i):
        log.append_receipt(_receipt(nonce=f"n{i}"))
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    recs = log.records()
    assert len([x for x in recs if x["kind"] == "receipt"]) == 20  # no interleaved/lost lines


def test_reload_on_epoch_exception_propagates(tmp_path):
    """Contract (Med#7 negative-path): RoutingConfig.reload must NOT swallow an on_epoch failure,
    so a lost audit epoch surfaces rather than silently dropping."""
    import json as _json
    # #464 fixture migration: this table loads via RoutingConfig (validation path), so the seat
    # needs a schema-valid manifest; the canonical review seat declares its role + a policy section
    # for forward-compat with the Task-2 name<->role loader lint.
    table = {"schema_version": "1", "pools": {"claude": {"concurrency": 2}},
             "policy": {"enforced_roles": ["review", "build"]},
             "seats": {"review": {"role": "review",
                                  "primary": {"model": "claude-fable-5", "lane": _lane("claude")}, "chain": [],
                                  "manifest": {"session_policy": "fresh", "tool_grants": ["read"],
                                               "effort": "high", "confinement": {"anthropic": "hooks"},
                                               "bounds": {"timeout_s": 1800}}}},
             "forbidden_combinations": []}
    p = tmp_path / "rt.json"
    p.write_text(_json.dumps(table))

    def boom(old, new):
        raise OSError("append_epoch failed")

    cfg = routing.RoutingConfig(p, on_epoch=boom)
    table["pools"]["claude"]["concurrency"] = 3  # change digest
    p.write_text(_json.dumps(table))
    with pytest.raises(OSError):
        cfg.reload()


def test_candidate_as_target_single_sources_lane():
    """#425 Step-8a fix: Candidate.as_target()'s lane == the stamped lane(), so target_identity
    works on a competitive candidate and matches its stamped dispatched_lane (no 3-vs-6 asymmetry)."""
    from phase_executor.engine import Candidate
    c = Candidate(seat="build", model="claude-sonnet-5", prompt="p", provider="anthropic",
                  pool="claude", credential_ref="ACCT_X")
    assert c.as_target()["lane"] == c.lane()
    assert target_identity(c.as_target()) == \
        ("claude-sonnet-5", "anthropic", "native", "subscription_oauth", "claude", "ACCT_X", None)


# ---- reconcile_run ----

_DEF_LANE = {"provider": "anthropic", "transport": "native", "auth_mode": "subscription_oauth",
             "pool": "claude", "credential_ref": None}
_DEF_TID = ("claude-fable-5", "anthropic", "native", "subscription_oauth", "claude", None, None)


def _receipt_rec(nonce, seat="review", cid="c1", verdict="pass", tid=_DEF_TID, digest="sha256:d",
                 recovered_from=None):
    r = {"kind": "receipt", "nonce": nonce, "seat": seat, "correlation_id": cid, "attempt_id": "0",
         "target_identity": list(tid), "config_digest": digest, "gate_digest": None,
         "author_provider": "openai", "verdict": verdict, "violations": []}
    if recovered_from is not None:  # #554 provenance: recovery attempt of an earlier expected call
        r["recovered_from"] = recovered_from
    return r


def _obs_rec(nonce, requested="claude-fable-5", actual="claude-fable-5", lane=None, status="ok",
             digest="sha256:d", cid="c1"):
    inner = _obs_dict(seat="review", correlation_id=cid, requested_model=requested, actual_model=actual,
                      parse_status=status, routing_config_digest=digest)
    if status != "ok":
        inner["actual_model"] = None
        inner["usage"] = None
        inner["process"] = {"exit_code": None, "timed_out": status == "timeout"}
    inner["dispatched_lane"] = lane or _DEF_LANE
    return {"kind": "observation", "receipt_nonce": nonce, "observation": inner}


def _EC(seat="review", cid="c1"):
    return enforce.ExpectedCall(seat, cid)


def test_reconcile_happy_e2e_ok():
    recs = [_receipt_rec("n1"), _obs_rec("n1")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert res.ok, res


# ---- #559 AC1: work_product binding (design §2.6) ----

def _valid_wp(promotion_status="promoted"):
    return {"kind": "docs", "worktree_path": "/wt", "base_sha": "b", "head_sha": "h",
            "content_tree_sha": "t", "changed_paths": ["docs/planning/appendix/x.md"],
            "documents": [], "tests": [], "promotion_status": promotion_status}


def _wp_rec(nonce, tree="sha256:tree1", new="sha256:new1"):
    return {"kind": "work_product", "receipt_nonce": nonce,
            "candidate_tree_sha": tree, "new_sha": new, "work_product": _valid_wp("promoted")}


def _obs_rec_wp(nonce, promotion_status, cid="c1"):
    # build via the dataclass so the observation carries the CURRENT schema_version that permits
    # the embedded work_product (the _obs_dict helper pins an older schema_version without it).
    o = contract.Observation(
        run_id="r", attempt_id="0-x", correlation_id=cid, seat="review", engine="claude",
        transport="native", requested_model="claude-fable-5", actual_model="claude-fable-5",
        prompt_hash="sha256:x", context_hashes=[], usage={"input": 1, "output": 1}, timing_ms=1,
        queued_ms=0, process={"exit_code": 0, "timed_out": False}, parse_status="ok",
        parsed_payload=None, raw_capture_path=None, fallback_reason=None,
        routing_config_digest="sha256:d", work_product=_valid_wp(promotion_status)).to_dict()
    o["dispatched_lane"] = _DEF_LANE
    return {"kind": "observation", "receipt_nonce": nonce, "observation": o}


def test_reconcile_work_product_bound_ok():
    recs = [_receipt_rec("n1"), _obs_rec("n1"), _wp_rec("n1")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert res.ok, res
    assert res.orphan_work_product == () == res.duplicate_work_product == res.missing_work_product


def test_reconcile_work_product_orphan_unknown_nonce():
    recs = [_receipt_rec("n1"), _obs_rec("n1"), _wp_rec("ghost")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and any("ghost" in x for x in res.orphan_work_product)


def test_reconcile_work_product_duplicate_per_receipt():
    recs = [_receipt_rec("n1"), _obs_rec("n1"), _wp_rec("n1"), _wp_rec("n1", new="sha256:new2")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and "n1" in res.duplicate_work_product


def test_reconcile_promoted_observation_without_record_is_missing():
    # an observation CLAIMING a promotion (embedded work_product.promotion_status=promoted) with NO
    # matching work_product record → missing_work_product (a promoted-but-unrecorded product is an
    # anomaly, not a silent pass).
    recs = [_receipt_rec("n1"), _obs_rec_wp("n1", "promoted")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and any("n1" in x for x in res.missing_work_product)


def _expected_wp_rec(nonce, tree="sha256:tree1", new="sha256:new1"):
    return {"kind": "expected_work_product", "receipt_nonce": nonce,
            "candidate_tree_sha": tree, "new_sha": new}


def test_reconcile_expected_work_product_without_record_is_missing():
    # #570 L2: a durable expected_work_product marker (the collect path writes it when a promotion
    # LANDED) with NO matching work_product record → missing_work_product. This is the real "no
    # missing" half — it keys off what the production collect path actually writes, unlike the old
    # check keyed on Observation.work_product (which no production path ever sets).
    recs = [_receipt_rec("n1"), _obs_rec("n1"), _expected_wp_rec("n1")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and any("n1" in x for x in res.missing_work_product)


def test_reconcile_expected_work_product_with_record_ok():
    recs = [_receipt_rec("n1"), _obs_rec("n1"), _expected_wp_rec("n1"), _wp_rec("n1")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert res.ok, res
    assert res.missing_work_product == ()


def test_reconcile_expected_work_product_same_nonce_wrong_hashes_is_missing():
    # #570 Step-11 finding 1: a work_product sharing the receipt_nonce but with DIFFERENT
    # candidate_tree_sha/new_sha must NOT satisfy the expectation (else the binding-field guard is
    # vacuous). The exact-tuple match flags it missing.
    recs = [_receipt_rec("n1"), _obs_rec("n1"),
            _expected_wp_rec("n1", tree="sha256:tree1", new="sha256:new1"),
            _wp_rec("n1", tree="sha256:STALE", new="sha256:STALE")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and any("n1" in x for x in res.missing_work_product)


def test_reconcile_promoted_observation_with_record_ok():
    # the same promoted observation WITH its work_product record → bound, no anomaly
    recs = [_receipt_rec("n1"), _obs_rec_wp("n1", "promoted"), _wp_rec("n1")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert res.ok, res


def test_reconcile_not_promoted_observation_needs_no_record():
    recs = [_receipt_rec("n1"), _obs_rec_wp("n1", "not_attempted")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert res.ok, res


def test_reconcile_missing_receipt():
    res = enforce.reconcile_run([_EC()], [], initial_digest="sha256:d")
    assert not res.ok and ("review", "c1") in res.missing_receipt


def test_reconcile_failed_precheck():
    recs = [_receipt_rec("n1", verdict="fail"), _obs_rec("n1")]  # launched despite fail
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and "n1" in res.failed_precheck


def test_reconcile_missing_obs():
    recs = [_receipt_rec("n1")]  # passed receipt, no observation
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and ("review", "c1") in res.missing_obs


# ---- #557 AC3: terminal failures reconcile as RECORDED failures, not missing pairs ----

def test_verify_post_no_response_synthetic_is_honest_availability():
    # #557: the exited_no_sentinel synthetic obs (no actual_model, parse_status
    # no_response) classifies as an honest availability failure — never a breach.
    pc = enforce.verify_post(_obs_dict(parse_status="no_response", actual_model=None,
                                       usage=None,
                                       process={"exit_code": None, "timed_out": False}))
    assert pc.ok and not pc.verified and pc.reason == "no_response"


def test_reconcile_exited_no_sentinel_is_recorded_failure_not_missing_pair():
    # #557 AC3: a bound no_response obs moves the key OUT of missing_obs — the failure is
    # attributable (availability-classed); with no verified sibling the run still refuses.
    recs = [_receipt_rec("n1"), _obs_rec("n1", status="no_response")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok
    assert ("review", "c1") not in res.missing_obs
    assert not res.binding_mismatch
    assert ("review", "c1") in res.missing_receipt  # all attempts availability-failed: never served


def test_reconcile_timed_out_is_recorded_failure_not_missing_pair():
    # #557 AC3 parity: timed_out reconciles exactly like exited_no_sentinel
    recs = [_receipt_rec("n1"), _obs_rec("n1", status="timeout")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok
    assert ("review", "c1") not in res.missing_obs
    assert not res.binding_mismatch
    assert ("review", "c1") in res.missing_receipt


def test_reconcile_sibling_success_forgives_clean_availability_failure():
    # #557 AC3: a CLEAN availability failure (no_response, no attested model) IS forgiven
    # by a verified sibling — the designed chain-fallback parity with timed_out — but the
    # failed attempt stays BOUND in the audit, never orphaned, mismatched, or dropped.
    recs = [_receipt_rec("n1"), _obs_rec("n1", status="no_response"),
            _receipt_rec("n2"), _obs_rec("n2")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert res.ok, res
    assert not res.binding_mismatch and not res.orphan and not res.duplicate


def test_reconcile_pause_recover_is_one_satisfied_call():
    # #554 AC1: a quota_paused original (availability obs) + a recovery attempt (own correlation
    # c1#resume1, recovered_from=c1, verified obs) reconciles OK as ONE satisfied expected call —
    # not two keys, not an unsatisfied availability-only key. The recovery is grouped under the
    # original expected key (review, c1) via recovered_from.
    recs = [
        _receipt_rec("n1", cid="c1"), _obs_rec("n1", status="no_response", cid="c1"),
        _receipt_rec("n2", cid="c1#resume1", recovered_from="c1"), _obs_rec("n2", cid="c1#resume1"),
    ]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert res.ok, res
    assert not res.orphan and not res.missing_receipt and not res.binding_mismatch


def test_reconcile_pause_without_recovery_still_fails():
    # #554 AC3 (no laundering): a quota_paused original with NO recovery is an availability-only
    # key → never served → refuse.
    recs = [_receipt_rec("n1", cid="c1"), _obs_rec("n1", status="no_response", cid="c1")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and ("review", "c1") in res.missing_receipt


def test_reconcile_recovery_receipt_not_orphaned():
    # #554: a recovery receipt whose OWN (seat, correlation_id) is not an expected key must not be
    # flagged orphan — it is attributed to the original via recovered_from.
    recs = [
        _receipt_rec("n1", cid="c1"), _obs_rec("n1", status="no_response", cid="c1"),
        _receipt_rec("n2", cid="c1#resume1", recovered_from="c1"), _obs_rec("n2", cid="c1#resume1"),
    ]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.orphan, res.orphan


def test_reconcile_recovery_with_no_verified_obs_still_fails():
    # #554 AC3 corollary: a recovery attempt that ALSO only produced an availability obs (still
    # never verified) does not satisfy — no laundering by a mere resume that also failed.
    recs = [
        _receipt_rec("n1", cid="c1"), _obs_rec("n1", status="no_response", cid="c1"),
        _receipt_rec("n2", cid="c1#resume1", recovered_from="c1"),
        _obs_rec("n2", status="timeout", cid="c1#resume1"),
    ]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and ("review", "c1") in res.missing_receipt


def test_reconcile_recovery_to_nonexistent_key_is_orphan():
    # #554 8a review (attack e): a recovery whose recovered_from names a NON-EXISTENT expected
    # key must FAIL CLOSED (orphan), never silently drop.
    recs = [_receipt_rec("n2", cid="ghost#resume1", recovered_from="ghost"),
            _obs_rec("n2", cid="ghost#resume1")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok
    assert any("n2" in o for o in res.orphan)


def test_reconcile_receipt_recovered_from_non_string_rejected():
    # #554 8a review (F2): a non-string recovered_from is rejected as a structured anomaly by the
    # record validator, not an ungraceful TypeError crash in reconcile.
    bad = _receipt_rec("n1", cid="c1")
    bad["recovered_from"] = ["c1"]  # unhashable — would crash the effective-key build
    with pytest.raises(ValueError):
        enforce._validate_record(bad, 1)


def test_reconcile_recovery_breach_not_laundered():
    # #554 AC3/AC4: if the recovery attempt is a BREACH (wrong model), the original key is unverified
    # — a recovery cannot launder a breach any more than a sibling can.
    recs = [
        _receipt_rec("n1", cid="c1"), _obs_rec("n1", status="no_response", cid="c1"),
        _receipt_rec("n2", cid="c1#resume1", recovered_from="c1"),
        _obs_rec("n2", requested="claude-fable-5", actual="claude-sonnet-5", cid="c1#resume1"),
    ]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and ("review", "c1") in res.unverified


def test_reconcile_suspicious_parse_error_not_laundered_by_sibling():
    # #557 AC3 "cannot be laundered by a sibling success": a SUSPICIOUS death (the
    # supervisor's parse_error synthetic — a child left an unparseable envelope) reads as
    # identity_missing in verify_post, so a verified sibling on the SAME expected key does
    # NOT forgive it — the run refuses.
    recs = [_receipt_rec("n1"), _obs_rec("n1", status="parse_error"),
            _receipt_rec("n2"), _obs_rec("n2")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok
    assert ("review", "c1") in res.unverified  # breach recorded, not laundered


def test_reconcile_binding_mismatch_dispatched_other_target():
    # pre-checked fable-5/claude (receipt tid), but dispatched sonnet via codex (obs lane) -> mismatch
    other = {"provider": "openai", "transport": "native", "auth_mode": "subscription_oauth",
             "pool": "codex", "credential_ref": None}
    recs = [_receipt_rec("n1"), _obs_rec("n1", requested="gpt-5.6-sol", actual="gpt-5.6-sol", lane=other)]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and res.binding_mismatch


def test_reconcile_duplicate_nonce():
    recs = [_receipt_rec("n1"), _receipt_rec("n1"), _obs_rec("n1")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and "n1" in res.duplicate_nonce


def test_reconcile_duplicate_observation():
    recs = [_receipt_rec("n1"), _obs_rec("n1"), _obs_rec("n1")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and "n1" in res.duplicate


def test_reconcile_unverified_mismatch():
    # obs bound + present but requested!=actual -> unverified (a real breach, not availability)
    recs = [_receipt_rec("n1"), _obs_rec("n1", requested="claude-fable-5", actual="claude-sonnet-5")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and ("review", "c1") in res.unverified


def test_reconcile_unaudited_digest():
    recs = [_receipt_rec("n1", digest="sha256:ROGUE"), _obs_rec("n1", digest="sha256:ROGUE")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and "n1" in res.unaudited_digest


def test_reconcile_orphan_unexpected_key():
    recs = [_receipt_rec("n1", cid="ghost"), _obs_rec("n1")]
    res = enforce.reconcile_run([_EC(cid="c1")], recs, initial_digest="sha256:d")
    assert not res.ok and res.orphan


def test_reconcile_expected_duplicate_raises():
    with pytest.raises(ValueError):
        enforce.reconcile_run([_EC(), _EC()], [], initial_digest="sha256:d")


def test_reconcile_multi_attempt_fallback_ok():
    # attempt 0 availability-failed (launch_error), attempt 1 verified fallback -> call OK
    recs = [
        _receipt_rec("n0"), _obs_rec("n0", status="launch_error"),
        _receipt_rec("n1"), _obs_rec("n1", status="ok"),
    ]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert res.ok, res


def test_reconcile_golden_fixture_ok():
    """AC1: a hand-authored golden routing-audit.jsonl (receipt + bound observation + epoch)
    reconciles ok when read from disk and passed through reconcile_run."""
    recs = [json.loads(line) for line in
            (FIXTURES / "routing-audit.jsonl").read_text().splitlines() if line.strip()]
    res = enforce.reconcile_run([enforce.ExpectedCall("review", "wf2-step11")], recs,
                                initial_digest="sha256:epoch-a")
    assert res.ok, res


def test_enforcement_api_exported_at_package_top_level():
    """Task 7: the enforcement API is importable from the package root + in __all__."""
    import phase_executor as pe
    for name in ["check_pre", "verify_post", "RoutingAuditLog", "reconcile_run", "PreReceipt",
                 "PostCheck", "ExpectedCall", "Reconcile", "target_identity", "audited_digests"]:
        assert hasattr(pe, name), f"missing top-level export: {name}"
        assert name in pe.__all__, f"missing from __all__: {name}"


def test_enforceable_roles_exported_at_package_top_level_464():
    """#464 Task 2 (design §D): ENFORCEABLE_ROLES — the evaluator-registry bound — is public API,
    names exactly the roles check_pre evaluates, and is a single object shared across the modules
    (defined in contract, the shared leaf; re-exported through enforce, the public-API home)."""
    import phase_executor as pe
    assert hasattr(pe, "ENFORCEABLE_ROLES")
    assert "ENFORCEABLE_ROLES" in pe.__all__
    assert pe.ENFORCEABLE_ROLES == frozenset({"review", "build"})
    assert enforce.ENFORCEABLE_ROLES is pe.ENFORCEABLE_ROLES
    assert contract.ENFORCEABLE_ROLES is pe.ENFORCEABLE_ROLES


def test_reconcile_breach_not_forgiven_by_verified_sibling():
    """Step-8a Finding 1: a wrong-model breach (requested!=actual, billed) on one attempt must NOT
    be laundered by a sibling attempt that verified — only availability failures are forgivable."""
    recs = [
        _receipt_rec("n0"), _obs_rec("n0", requested="claude-fable-5", actual="claude-sonnet-5"),
        _receipt_rec("n1"), _obs_rec("n1"),
    ]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and ("review", "c1") in res.unverified


def test_reconcile_seat_drift_binding_mismatch():
    """Step-8a Finding 2: an observation whose inner seat differs from its bound receipt's seat is
    a binding_mismatch (defense-in-depth against seat drift)."""
    obs = _obs_rec("n1")
    obs["observation"]["seat"] = "build"  # receipt is a review receipt
    recs = [_receipt_rec("n1"), obs]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and any("seat-drift" in x for x in res.binding_mismatch)


# ---- Step-8a consolidated fixes: verify_post envelope semantics + seat role + require_nonempty ----

def test_verify_post_identity_failure_is_breach():
    """Step-8a: identity_failure (a produced envelope with absent/wrong model) is a BREACH, not a
    benign non-ok — the earlier 'any non-ok is benign' let a real wrong-model attempt escape."""
    pc = enforce.verify_post(_obs_dict(parse_status="identity_failure", actual_model=None, usage=None))
    assert not pc.ok and not pc.verified and pc.reason == "identity_missing"


def test_verify_post_usage_unavailable_matching_identity_is_verified():
    """Step-8a: usage_unavailable with a MATCHING actual_model is verified (identity attested; only
    token counts missing) — not wrongly refused (over-fail-closed)."""
    pc = enforce.verify_post(_obs_dict(parse_status="usage_unavailable",
                                       requested_model="claude-opus-4-8", actual_model="claude-opus-4-8", usage=None))
    assert pc.ok and pc.verified


def test_reconcile_identity_failure_not_forgiven_by_verified_sibling():
    """Step-8a Finding: a real-envelope wrong-model attempt (identity_failure) must NOT be laundered
    by a verified sibling — the case the first fix missed."""
    recs = [
        _receipt_rec("n0"), _obs_rec("n0", status="identity_failure"),
        _receipt_rec("n1"), _obs_rec("n1"),
    ]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and ("review", "c1") in res.unverified


def _role_snapshot(seat_name, role):
    table = {"schema_version": "1", "pools": {"claude": {"concurrency": 2}},
             # #464 Task 3 (§D): recognize review+build so a role-carrying seat's per-role requirement
             # fires without also tripping unrecognized_role (the fixture is about role-keying, not roster).
             "policy": {"enforced_roles": ["review", "build"]},
             "seats": {seat_name: {"role": role, "primary": {"model": "claude-fable-5", "lane": _lane("claude")}, "chain": []}} if role
                      else {seat_name: {"primary": {"model": "claude-fable-5", "lane": _lane("claude")}, "chain": []}},
             "forbidden_combinations": []}
    return routing.RoutingSnapshot.from_table(table)


def test_check_pre_role_enforces_on_renamed_seat():
    """Step-8a: enforcement keys on the seat ROLE, so a review-role seat under ANY name still
    requires author_provider (a name-based check silently skipped a renamed seat)."""
    snap = _role_snapshot("code_review", "review")
    t = _target("claude-fable-5", _lane("claude"))
    r = check_pre("code_review", t, snap, correlation_id="c", attempt_id="0", author_provider=None)
    assert "author_provider_missing" in r.violations


def test_check_pre_no_role_no_requirement():
    """A seat with no role carries no review/build requirement (explicit config, not a name guess)."""
    snap = _role_snapshot("misc", None)
    t = _target("claude-fable-5", _lane("claude"))
    r = check_pre("misc", t, snap, correlation_id="c", attempt_id="0", author_provider=None)
    assert r.verdict == "pass" and not r.violations


def test_reconcile_require_nonempty_default_refuses_empty():
    """Step-11: require_nonempty DEFAULTS True — an empty expected-set refuses ship. Pass
    require_nonempty=False only for a legitimately zero-routed-call run."""
    assert not enforce.reconcile_run([], [], initial_digest="sha256:d").ok  # default fail-closed
    assert enforce.reconcile_run([], [], initial_digest="sha256:d", require_nonempty=False).ok


def test_reconcile_correlation_drift_binding_mismatch():
    """Step-11 Crit: an observation whose inner correlation_id differs from its bound receipt's is a
    binding_mismatch — an obs from another call cannot attach to a receipt by nonce alone."""
    obs = _obs_rec("n1")
    obs["observation"]["correlation_id"] = "other-call"
    recs = [_receipt_rec("n1"), obs]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and any("correlation-drift" in x for x in res.binding_mismatch)


def test_audited_digests_rejects_physical_reorder():
    """Step-11 High: a physically-reordered (tampered) epoch log — seq 2 line before seq 1 — RAISES
    (no sorting to normalize it away)."""
    with pytest.raises(ValueError):
        enforce.audited_digests([{"kind": "epoch", "seq": 2, "from": "b", "to": "c"},
                                 {"kind": "epoch", "seq": 1, "from": "a", "to": "b"}], "a")


def test_reconcile_missing_obs_not_forgiven_by_verified_sibling():
    """Step-11 bug/logic: a passed receipt with NO observation (a lost/uninstrumented approved
    dispatch) refuses ship even when a sibling attempt verified."""
    recs = [_receipt_rec("n0"),  # passed receipt, no observation for it
            _receipt_rec("n1"), _obs_rec("n1")]  # verified sibling
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and ("review", "c1") in res.missing_obs


def test_target_identity_includes_participation_mode():
    """Step-11: two targets differing ONLY in the schema-optional participation_mode lane field are
    DISTINCT identities (they must not collapse and let an undeclared mode pass off_chain)."""
    base = _lane("claude")
    a = target_identity(_target("claude-fable-5", dict(base)))
    b = target_identity(_target("claude-fable-5", dict(base, participation_mode="council")))
    assert a != b and len(a) == 7


# ---- #464 Task 3: unrecognized-role fail-closed (§D) + attested build gate (§E) ----

def test_check_pre_unrecognized_role_typo_fails_closed_464():
    """#464 §D / #434 part 2: a typo'd role ('biuld') NOT in the table's enforced_roles fails CLOSED
    with unrecognized_role. This silently PASSES on main (nothing keyed on an unknown role)."""
    table = {"schema_version": "1", "pools": {"claude": {"concurrency": 2}},
             "policy": {"enforced_roles": ["review", "build"]},
             "seats": {"builder": {"role": "biuld",
                                   "primary": {"model": "claude-fable-5", "lane": _lane("claude")},
                                   "chain": []}},
             "forbidden_combinations": []}
    snap = routing.RoutingSnapshot.from_table(table)
    t = _target("claude-fable-5", _lane("claude"))
    r = check_pre("builder", t, snap, correlation_id="c", attempt_id="0")
    assert r.verdict == "fail"
    assert any(v.startswith("unrecognized_role") for v in r.violations)
    assert "biuld" in " ".join(r.violations)  # the offending role is named


def test_check_pre_empty_string_role_treated_as_absent_464():
    """#464 §D (breaker S4): an empty-string role is treated as ABSENT — no unrecognized_role, no
    per-role requirement — and normalizes to None on the receipt."""
    table = {"schema_version": "1", "pools": {"claude": {"concurrency": 2}},
             "policy": {"enforced_roles": ["review", "build"]},
             "seats": {"misc": {"role": "",
                                "primary": {"model": "claude-fable-5", "lane": _lane("claude")},
                                "chain": []}},
             "forbidden_combinations": []}
    snap = routing.RoutingSnapshot.from_table(table)
    t = _target("claude-fable-5", _lane("claude"))
    r = check_pre("misc", t, snap, correlation_id="c", attempt_id="0", author_provider=None)
    assert r.verdict == "pass" and not r.violations
    assert r.role is None  # "" normalized to None on the receipt


def test_check_pre_build_raw_dict_attestation_rejected_464():
    """#464 §E: a raw dict is NOT a GateAttestation (the isinstance check is the in-process trust
    boundary) — fail closed with gate_invalid, no AttributeError populating the receipt."""
    snap = _snapshot()
    t = _build_target()
    r = check_pre("build", t, snap, correlation_id="c", attempt_id="0",
                  attestation={"gate_outcome": "single", "policy_digest": "sha256:pol",
                               "input_digest": enforce.launch_input_digest("build", t, "c")})
    assert r.verdict == "fail"
    assert any("not a GateAttestation" in v for v in r.violations)
    assert r.gate_outcome is None and r.gate_input_digest is None  # nothing extracted from a junk shape


def test_check_pre_build_unknown_outcome_invalid_464():
    """#464 §E: an attestation with an out-of-vocabulary gate_outcome is gate_invalid (checked before
    the digest binding)."""
    snap = _snapshot()
    t = _build_target()
    att = enforce.GateAttestation(gate_outcome="sideways", policy_digest="sha256:pol",
                                  input_digest=enforce.launch_input_digest("build", t, "c"))
    r = check_pre("build", t, snap, correlation_id="c", attempt_id="0", attestation=att)
    assert r.verdict == "fail"
    assert any("unknown outcome" in v for v in r.violations)


def test_check_pre_build_input_digest_mismatch_replay_464():
    """#464 §E (anti-replay): a valid attestation whose input_digest was minted for a DIFFERENT
    launch (correlation_id) fails closed — stale/replayed gate evidence cannot cross launches."""
    snap = _snapshot()
    t = _build_target()
    att = enforce.GateAttestation(gate_outcome="single", policy_digest="sha256:pol",
                                  input_digest=enforce.launch_input_digest("build", t, "DIFFERENT-cid"))
    r = check_pre("build", t, snap, correlation_id="c", attempt_id="0", attestation=att)
    assert r.verdict == "fail"
    assert any("input digest mismatch" in v for v in r.violations)


def test_check_pre_build_bakeoff_outcome_requires_bakeoff_464():
    """#464 §E (pass-2 P1): a valid, launch-bound attestation whose outcome is 'bakeoff' may NOT
    proceed on the single-dispatch path — gate_requires_bakeoff. The gate's routing decision is not
    bypassable by re-presenting its own evidence to single dispatch."""
    snap = _snapshot()
    t = _build_target()
    att = _attestation(seat="build", target=t, correlation_id="c", outcome="bakeoff")
    r = check_pre("build", t, snap, correlation_id="c", attempt_id="0", attestation=att)
    assert r.verdict == "fail" and "gate_requires_bakeoff" in r.violations


def test_launch_input_digest_deterministic_and_sensitive_464():
    """#464 §E: launch_input_digest is deterministic (same inputs -> same digest) and sensitive to
    each of seat, target, and correlation_id (any change -> a different digest)."""
    t1 = _target("claude-sonnet-5", _lane("claude"))
    d1 = enforce.launch_input_digest("build", t1, "c")
    assert d1.startswith("sha256:")
    assert d1 == enforce.launch_input_digest("build", t1, "c")  # deterministic
    assert enforce.launch_input_digest("review", t1, "c") != d1  # seat-sensitive
    assert enforce.launch_input_digest("build", t1, "OTHER") != d1  # correlation-sensitive
    t2 = _target("claude-fable-5", _lane("claude"))
    assert enforce.launch_input_digest("build", t2, "c") != d1  # target-sensitive


def test_validate_record_build_receipt_requires_gate_evidence_464():
    """#464 §E (pass-2 P2): a NEW build receipt (role == 'build') must carry non-null gate_outcome +
    gate_input_digest (fail-closed) — a build launch cannot validate ungated; receipts with NO role
    key (all historical logs) validate exactly as today."""
    # build receipt with null gate fields -> refused
    rec = _receipt_rec("n1", seat="build")
    rec["role"] = "build"
    rec["gate_outcome"] = None
    rec["gate_input_digest"] = None
    with pytest.raises(ValueError):
        enforce._validate_record(rec, 1)
    # build receipt with canonical gate evidence -> validates (Step-11 tighten: gate_digest must
    # also be sha256-canonical; _receipt_rec's default None would now be rejected)
    rec["gate_outcome"] = "single"
    rec["gate_input_digest"] = "sha256:x"
    rec["gate_digest"] = "sha256:pol"
    enforce._validate_record(rec, 1)  # no raise
    # a historical receipt with NO 'role' key -> validates exactly as today (_RECEIPT_REQUIRED unchanged)
    enforce._validate_record(_receipt_rec("n2"), 1)  # no raise


def test_validate_record_denied_build_receipt_still_readable_464(tmp_path):
    """#464 Step-8a review (both reviewers converged): a DENIED build launch legitimately mints a
    verdict='fail' receipt with NULL gate fields (gate_missing / gate_invalid paths) and the caller
    records it BEFORE checking the verdict — so the audit log must stay readable. Only a
    verdict='pass' build receipt must prove gating; a fail receipt with null gate evidence
    validates, else one denial poisons the whole run's audit (records() raises on line 1)."""
    snap = _snapshot()
    denied = enforce.check_pre("build", snap.table["seats"]["build"]["primary"], snap,
                               correlation_id="c-denied", attempt_id="0")  # no attestation
    assert denied.verdict == "fail" and "gate_missing" in denied.violations
    assert denied.gate_outcome is None and denied.gate_input_digest is None
    log = enforce.RoutingAuditLog(tmp_path, "run-denied")
    log.append_receipt(denied)
    recs = log.records()  # must NOT raise — the denial is a legitimate audit line
    assert recs[0]["verdict"] == "fail" and recs[0]["role"] == "build"
    # tightening (empty-string evasion): a PASS build receipt with empty-string gate fields is refused
    rec = _receipt_rec("n3", seat="build")
    rec["role"] = "build"
    rec["gate_outcome"] = ""
    rec["gate_input_digest"] = ""
    with pytest.raises(ValueError):
        enforce._validate_record(rec, 1)


def test_gate_attestation_and_launch_digest_exported_464():
    """#464 §E: GateAttestation + launch_input_digest are public API (enforce home, re-exported at
    the package top level and in __all__)."""
    import phase_executor as pe
    assert hasattr(pe, "GateAttestation") and "GateAttestation" in pe.__all__
    assert hasattr(pe, "launch_input_digest") and "launch_input_digest" in pe.__all__
    assert pe.GateAttestation is enforce.GateAttestation
    assert pe.launch_input_digest is enforce.launch_input_digest


def test_validate_record_pass_build_receipt_requires_single_outcome_464():
    """Step-11 diff review (REOPENS 464-step4p2-P2): the audit READER must reject a corrupt log
    claiming a PASS build receipt with a non-'single' outcome — a 'bakeoff' outcome can never
    legitimately authorize single dispatch (check_pre fails it), so a pass+bakeoff line is
    corruption/tampering. Digests must also be sha256-canonical, not arbitrary truthy strings."""
    rec = _receipt_rec("n4", seat="build")
    rec["role"] = "build"
    rec["gate_outcome"] = "bakeoff"
    rec["gate_input_digest"] = "sha256:abc"
    with pytest.raises(ValueError, match="gate"):
        enforce._validate_record(rec, 1)
    rec["gate_outcome"] = "frobnicate"
    with pytest.raises(ValueError, match="gate"):
        enforce._validate_record(rec, 1)
    rec["gate_outcome"] = "single"
    rec["gate_input_digest"] = "not-a-digest"
    with pytest.raises(ValueError, match="gate"):
        enforce._validate_record(rec, 1)
    rec["gate_input_digest"] = "sha256:abc"
    rec["gate_digest"] = "junk"
    with pytest.raises(ValueError, match="gate"):
        enforce._validate_record(rec, 1)
    rec["gate_digest"] = "sha256:def"
    enforce._validate_record(rec, 1)  # canonical single receipt validates


# ---- #733: a verified-identity process failure must not satisfy reconciliation ----

def _timeout_obs_rec_with_identity(nonce, cid="c1"):
    """A timeout observation that still ATTESTS the matching identity (the #733 escape shape) —
    unlike _obs_rec, which nulls actual_model on every non-ok status."""
    inner = _obs_dict(seat="review", correlation_id=cid, requested_model="claude-fable-5",
                      actual_model="claude-fable-5", parse_status="timeout",
                      routing_config_digest="sha256:d")
    inner["usage"] = None
    inner["process"] = {"exit_code": -9, "timed_out": True}
    inner["dispatched_lane"] = _DEF_LANE
    return {"kind": "observation", "receipt_nonce": nonce, "observation": inner}


def test_733_reconcile_verified_identity_timeout_is_not_served():
    # T10: an expected call whose only passed receipt binds a killed (timeout) observation is
    # NOT served — a killed attempt must not reconcile as a served call, identity or not.
    recs = [_receipt_rec("n1"), _timeout_obs_rec_with_identity("n1")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok, res
    assert ("review", "c1") in tuple(res.missing_receipt) + tuple(res.unverified), res


def test_733_reconcile_breach_still_wins_over_verified_sibling():
    # T10 precedence guard: a billed breach on one attempt is never laundered by a clean sibling
    breach = _obs_rec("n1", actual="claude-wrong-9")
    clean = _obs_rec("n2")
    recs = [_receipt_rec("n1"), breach, _receipt_rec("n2"), clean]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and ("review", "c1") in tuple(res.unverified)


def test_733_reconcile_availability_sibling_then_success_still_serves():
    # unchanged behavior: a no-identity availability failure + a verified sibling = served
    recs = [_receipt_rec("n1"), _obs_rec("n1", status="timeout"), _receipt_rec("n2"), _obs_rec("n2")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert res.ok, res


# -- Step-11 R2-H2 (#733): the audit READ boundary validates inner observations --
# append_observation is fail-loud (schema-validated), but records() only shape-checked the
# envelope keys — an inner observation written by anything other than append_observation
# (tampered file, foreign tool) flowed unvalidated into consumers like collect_work_product,
# where the predicate's documented no-signal forgiveness assumes schema-valid inputs.

def test_733_s11_records_validates_inner_observation(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path, "run-v")
    log._write_locked({"kind": "observation", "receipt_nonce": "n1",  # pylint: disable=protected-access
                       "observation": {"parse_status": "ok"}})
    with pytest.raises(ValueError, match="inner observation"):
        log.records()


def test_733_s11_records_accepts_append_written_observation(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path, "run-v2")
    obs = _obs_rec("n1")["observation"]
    log._write_locked({"kind": "observation", "receipt_nonce": "n1",  # pylint: disable=protected-access
                       "observation": obs})
    recs = log.records()
    assert len(recs) == 1 and recs[0]["observation"]["parse_status"] == obs["parse_status"]


# ---- #767 (rev-4 F3): versioned work-product binding — v2 requires identity fields, v1 legacy reads ----

def _wp_rec_v2(nonce, tree="sha256:tree1", new="sha256:new1", target="refs/heads/feat",
               digest="sha256:" + "ab" * 32):
    r = _wp_rec(nonce, tree=tree, new=new)
    r.update({"binding_version": 2, "target_ref": target, "paths_digest": digest})
    return r


def _expected_wp_rec_v2(nonce, tree="sha256:tree1", new="sha256:new1", target="refs/heads/feat",
                        digest="sha256:" + "ab" * 32):
    r = _expected_wp_rec(nonce, tree=tree, new=new)
    r.update({"binding_version": 2, "target_ref": target, "paths_digest": digest})
    return r


def _audit_roundtrip(tmp_path, rec):
    log = enforce.RoutingAuditLog(tmp_path / "runs", "runv")
    log._write_locked(rec)  # pylint: disable=protected-access
    return log.records()


def test_validate_work_product_v1_legacy_still_reads(tmp_path):
    # historical logs (no binding_version) must stay readable — fail-closed only on NEW shapes
    recs = _audit_roundtrip(tmp_path, _wp_rec("n1"))
    assert recs and recs[0]["kind"] == "work_product"


def test_validate_work_product_v2_missing_identity_fields_refuses(tmp_path):
    bad = _wp_rec("n1")
    bad["binding_version"] = 2  # v2 without target_ref/paths_digest must refuse
    import pytest as _pytest
    with _pytest.raises(ValueError):
        _audit_roundtrip(tmp_path, bad)


def test_validate_expected_work_product_v2_missing_identity_fields_refuses(tmp_path):
    bad = _expected_wp_rec("n1")
    bad["binding_version"] = 2
    import pytest as _pytest
    with _pytest.raises(ValueError):
        _audit_roundtrip(tmp_path, bad)


def test_validate_work_product_unknown_binding_version_refuses(tmp_path):
    bad = _wp_rec_v2("n1")
    bad["binding_version"] = 3
    import pytest as _pytest
    with _pytest.raises(ValueError):
        _audit_roundtrip(tmp_path, bad)


def test_validate_work_product_v2_complete_reads(tmp_path):
    recs = _audit_roundtrip(tmp_path, _wp_rec_v2("n1"))
    assert recs and recs[0]["binding_version"] == 2


def test_reconcile_v2_expectation_never_matches_v1_record():
    # a v2 expected_work_product must NOT be satisfied by a legacy v1 work_product sharing the
    # 3-tuple — cross-version matching would let an unidentified binding satisfy a v2 expectation
    recs = [_receipt_rec("n1"), _obs_rec("n1"), _expected_wp_rec_v2("n1"), _wp_rec("n1")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and any("n1" in x for x in res.missing_work_product)


def test_reconcile_v2_pair_matches_on_extended_tuple():
    recs = [_receipt_rec("n1"), _obs_rec("n1"), _expected_wp_rec_v2("n1"), _wp_rec_v2("n1")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert res.ok, res
    assert res.missing_work_product == ()


def test_reconcile_v2_pair_target_mismatch_is_missing():
    recs = [_receipt_rec("n1"), _obs_rec("n1"), _expected_wp_rec_v2("n1"),
            _wp_rec_v2("n1", target="refs/heads/OTHER")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok and any("n1" in x for x in res.missing_work_product)


# ---- #767 Step-8a fixes: v2 writer/reader strictness ----

def test_append_work_product_rejects_partial_binding(tmp_path):
    import pytest as _pytest
    log = enforce.RoutingAuditLog(tmp_path / "runs", "runv")
    with _pytest.raises(ValueError):
        log.append_work_product(receipt_nonce="n1", candidate_tree_sha="t", new_sha="s",
                                work_product=_valid_wp(), target_ref="refs/heads/x")
    with _pytest.raises(ValueError):
        log.append_expected_work_product(receipt_nonce="n1", candidate_tree_sha="t", new_sha="s",
                                         paths_digest="sha256:" + "0" * 64)


def test_validate_v2_rejects_nonstring_identity(tmp_path):
    import pytest as _pytest
    bad = _wp_rec_v2("n1")
    bad["target_ref"] = ["refs/heads/x"]  # truthy non-string would crash _binding_key's set
    with _pytest.raises(ValueError):
        _audit_roundtrip(tmp_path, bad)


def test_validate_v2_rejects_malformed_digest_and_ref(tmp_path):
    import pytest as _pytest
    bad = _wp_rec_v2("n1", digest="not-a-digest")
    with _pytest.raises(ValueError):
        _audit_roundtrip(tmp_path, bad)
    bad2 = _wp_rec_v2("n2", target="heads/x")  # not refs/-rooted
    with _pytest.raises(ValueError):
        _audit_roundtrip(tmp_path, bad2)


def test_validate_v2_accepts_appendix_default_digest(tmp_path):
    ok = _wp_rec_v2("n1", digest="appendix-default")
    recs = _audit_roundtrip(tmp_path, ok)
    assert recs and recs[0]["paths_digest"] == "appendix-default"


# ---- #767 Step-11 lane R1-F5: mutually exclusive binding schemas ----

def test_validate_refuses_v2_fields_without_version(tmp_path):
    # A hybrid record carrying v2 identity fields but NO binding_version must refuse — it
    # otherwise reads as "legacy" while reconcile's _binding_key still matches on the identity
    # fields, so two hybrids match despite the "legacy set OR complete v2" fail-closed contract.
    bad = _wp_rec_v2("n1")
    del bad["binding_version"]
    with pytest.raises(ValueError):
        _audit_roundtrip(tmp_path / "wp", bad)
    bad2 = _expected_wp_rec_v2("n1")
    del bad2["binding_version"]
    with pytest.raises(ValueError):
        _audit_roundtrip(tmp_path / "ewp", bad2)


def test_validate_refuses_non_exact_int_binding_version(tmp_path):
    # binding_version must be the exact non-boolean integer 2 — float 2.0 (== 2 in Python),
    # the string "2", and True all refuse.
    for i, v in enumerate((2.0, "2", True)):
        bad = _wp_rec_v2("n1")
        bad["binding_version"] = v
        with pytest.raises(ValueError):
            _audit_roundtrip(tmp_path / f"case{i}", bad)


def test_append_work_product_rejects_empty_identity(tmp_path):
    # R1-F5 writer half: empty strings pass the both-or-neither XOR but skipped the truthiness
    # v2 tagging — a SILENT version downgrade. Supplied identity must be non-empty strings.
    log = enforce.RoutingAuditLog(tmp_path / "runs", "runv")
    with pytest.raises(ValueError):
        log.append_work_product(receipt_nonce="n1", candidate_tree_sha="t", new_sha="s",
                                work_product=_valid_wp(), target_ref="", paths_digest="")
    with pytest.raises(ValueError):
        log.append_expected_work_product(receipt_nonce="n1", candidate_tree_sha="t", new_sha="s",
                                         target_ref="", paths_digest="")


# ---- #762 D1/R3-B/R5-B: the landed_work_product audit kind + expected_feature_ref field ----

_SHA_A = "a" * 40
_SHA_B = "b" * 40


def _landing_rec(nonce="n1", *, feature_ref="refs/heads/feat", pre=_SHA_A, new=_SHA_B,
                 temp="refs/rawgentic/collect/n1", status="landed", run_id="runL", ts=1000):
    return {"kind": "landed_work_product", "landing_version": 1, "receipt_nonce": nonce,
            "feature_ref": feature_ref, "pre_sha": pre, "new_sha": new, "temp_ref": temp,
            "landing_status": status, "run_id": run_id, "ts": ts}


def test_append_landed_work_product_roundtrip(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path / "runs", "runL")
    log.append_landed_work_product(
        receipt_nonce="n1", feature_ref="refs/heads/feat", pre_sha=_SHA_A, new_sha=_SHA_B,
        temp_ref="refs/rawgentic/collect/n1", landing_status="landed", run_id="runL", ts=1000)
    recs = log.records()
    assert len(recs) == 1
    r = recs[0]
    assert r["kind"] == "landed_work_product" and r["landing_version"] == 1
    assert r["feature_ref"] == "refs/heads/feat" and r["landing_status"] == "landed"
    assert r["run_id"] == "runL" and r["ts"] == 1000


def test_landed_missing_required_field_refuses(tmp_path):
    for field in ("landing_version", "receipt_nonce", "feature_ref", "pre_sha", "new_sha",
                  "temp_ref", "landing_status", "run_id", "ts"):
        bad = _landing_rec()
        del bad[field]
        with pytest.raises(ValueError):
            _audit_roundtrip(tmp_path / field, bad)


def test_landed_version_exact_int_one(tmp_path):
    # exact non-boolean int 1: True, 2, "1", 1.0 all refuse
    for i, v in enumerate((True, 2, "1", 1.0)):
        bad = _landing_rec()
        bad["landing_version"] = v
        with pytest.raises(ValueError):
            _audit_roundtrip(tmp_path / f"v{i}", bad)


def test_landed_ref_rooting_refused(tmp_path):
    with pytest.raises(ValueError):
        _audit_roundtrip(tmp_path / "fr", _landing_rec(feature_ref="feat"))
    with pytest.raises(ValueError):
        _audit_roundtrip(tmp_path / "tr", _landing_rec(temp="refs/heads/n1"))


def test_landed_sha_shape_refused(tmp_path):
    with pytest.raises(ValueError):
        _audit_roundtrip(tmp_path / "s1", _landing_rec(pre="abc"))
    with pytest.raises(ValueError):
        _audit_roundtrip(tmp_path / "s2", _landing_rec(new="Z" * 40))


def test_landed_status_enum_refused(tmp_path):
    with pytest.raises(ValueError):
        _audit_roundtrip(tmp_path / "st", _landing_rec(status="merged"))


def test_landed_ts_int_refused(tmp_path):
    with pytest.raises(ValueError):
        _audit_roundtrip(tmp_path / "t1", _landing_rec(ts="1000"))
    with pytest.raises(ValueError):
        _audit_roundtrip(tmp_path / "t2", _landing_rec(ts=True))


def test_landed_schema_exclusivity_refuses_binding_fields(tmp_path):
    # D1: mutually exclusive with the work_product v1/v2 schemas — any binding-vocabulary
    # field on a landing record is refused.
    for i, (k, v) in enumerate((("binding_version", 2), ("target_ref", "refs/heads/x"),
                                ("paths_digest", "sha256:" + "0" * 64))):
        bad = _landing_rec()
        bad[k] = v
        with pytest.raises(ValueError):
            _audit_roundtrip(tmp_path / f"x{i}", bad)


def test_landed_poison_diagnostic_names_line(tmp_path):
    # R3-J: a malformed landing record makes records() raise with a loud line-numbered diagnostic
    log = enforce.RoutingAuditLog(tmp_path / "runs", "runL")
    log._write_locked(_landing_rec())  # pylint: disable=protected-access
    log._write_locked(_landing_rec(status="bogus"))  # pylint: disable=protected-access
    with pytest.raises(ValueError, match="audit line 2"):
        log.records()


def test_append_landed_work_product_writer_validates(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path / "runs", "runL")
    kw = dict(receipt_nonce="n1", feature_ref="refs/heads/feat", pre_sha=_SHA_A, new_sha=_SHA_B,
              temp_ref="refs/rawgentic/collect/n1", landing_status="landed", run_id="runL",
              ts=1000)
    for bad in (dict(kw, feature_ref="feat"), dict(kw, temp_ref="refs/heads/n1"),
                dict(kw, pre_sha="xyz"), dict(kw, landing_status="merged"),
                dict(kw, receipt_nonce=""), dict(kw, run_id=""), dict(kw, ts="1000")):
        with pytest.raises(ValueError):
            log.append_landed_work_product(**bad)
    assert log.records() == []  # nothing bad was ever written


def test_validate_v2_expected_feature_ref_roundtrip(tmp_path):
    # R5-B: a v2 record may carry expected_feature_ref (refs/heads/-rooted non-empty string)
    ok = _wp_rec_v2("n1")
    ok["expected_feature_ref"] = "refs/heads/feat"
    recs = _audit_roundtrip(tmp_path, ok)
    assert recs and recs[0]["expected_feature_ref"] == "refs/heads/feat"


def test_validate_expected_feature_ref_without_version_refuses(tmp_path):
    # hybrid rule extension: the field is v2 vocabulary — present without binding_version refuses
    bad = _wp_rec("n1")
    bad["expected_feature_ref"] = "refs/heads/feat"
    with pytest.raises(ValueError):
        _audit_roundtrip(tmp_path, bad)


def test_validate_expected_feature_ref_shape_refused(tmp_path):
    for i, v in enumerate(("", "feat", ["refs/heads/feat"])):
        bad = _wp_rec_v2("n1")
        bad["expected_feature_ref"] = v
        with pytest.raises(ValueError):
            _audit_roundtrip(tmp_path / f"e{i}", bad)


def test_append_work_product_expected_feature_ref_requires_v2(tmp_path):
    # writer hygiene: expected_feature_ref rides the v2 binding only — without target_ref it
    # would be a version-downgraded hybrid at read time, so the writer refuses it up front.
    log = enforce.RoutingAuditLog(tmp_path / "runs", "runv")
    with pytest.raises(ValueError):
        log.append_work_product(receipt_nonce="n1", candidate_tree_sha="t", new_sha="s",
                                work_product=_valid_wp(),
                                expected_feature_ref="refs/heads/feat")
    with pytest.raises(ValueError):
        log.append_expected_work_product(receipt_nonce="n1", candidate_tree_sha="t", new_sha="s",
                                         expected_feature_ref="refs/heads/feat")
    with pytest.raises(ValueError):
        log.append_work_product(receipt_nonce="n1", candidate_tree_sha="t", new_sha="s",
                                work_product=_valid_wp(), target_ref="refs/heads/x",
                                paths_digest="sha256:" + "0" * 64,
                                expected_feature_ref="not-rooted")


def test_append_work_product_expected_feature_ref_roundtrip(tmp_path):
    log = enforce.RoutingAuditLog(tmp_path / "runs", "runv")
    log.append_expected_work_product(receipt_nonce="n1", candidate_tree_sha="t", new_sha="s",
                                     target_ref="refs/heads/x",
                                     paths_digest="sha256:" + "0" * 64,
                                     expected_feature_ref="refs/heads/feat")
    log.append_work_product(receipt_nonce="n1", candidate_tree_sha="t", new_sha="s",
                            work_product=_valid_wp(), target_ref="refs/heads/x",
                            paths_digest="sha256:" + "0" * 64,
                            expected_feature_ref="refs/heads/feat")
    recs = log.records()
    assert [r["expected_feature_ref"] for r in recs] == ["refs/heads/feat", "refs/heads/feat"]


# ---- #762 Task 2: landing reconcile arms (D1 reader + R3-B A4 + R5-D + rev-2 adoption 4) ----

def _landing_for(nonce, *, new="sha256:new1", feature_ref="refs/heads/feat", run_id="runL",
                 pre=_SHA_A, temp=None, status="landed", ts=1000):
    return {"kind": "landed_work_product", "landing_version": 1, "receipt_nonce": nonce,
            "feature_ref": feature_ref, "pre_sha": pre,
            "new_sha": new, "temp_ref": temp or f"refs/rawgentic/collect/{nonce}",
            "landing_status": status, "run_id": run_id, "ts": ts}


def _code_wp_rec(nonce, *, new="sha256:new1", target=None, feature="refs/heads/feat"):
    r = _wp_rec(nonce, new=new)
    r["work_product"] = dict(r["work_product"], kind="code")
    r.update({"binding_version": 2,
              "target_ref": target or f"refs/rawgentic/collect/{nonce}",
              "paths_digest": "sha256:" + "ab" * 32,
              "expected_feature_ref": feature})
    return r


def _base_pair(nonce="n1"):
    return [_receipt_rec(nonce), _obs_rec(nonce)]


def test_reconcile_landed_pair_clean():
    recs = _base_pair() + [_code_wp_rec("n1"), _landing_for("n1")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert res.ok, res
    assert res.unlanded_work_product == () and res.orphan_landing == ()
    assert res.landing_mismatch == () and res.landing_conflict == ()
    assert res.pre_cutover_unverifiable == ()


def test_reconcile_unlanded_work_product_flips_ok():
    # a code work_product POSITIONED AFTER the run's first landing with no landing of its
    # own = delivered-but-lost work (D1); flips ok.
    recs = (_base_pair() + [_code_wp_rec("n1"), _landing_for("n1")]
            + [_receipt_rec("n2", cid="c2"), _obs_rec("n2", cid="c2"),
               _code_wp_rec("n2", new="sha256:new2")])
    res = enforce.reconcile_run([_EC(), _EC(cid="c2")], recs, initial_digest="sha256:d")
    assert not res.ok
    assert any("n2" in x for x in res.unlanded_work_product)


def test_reconcile_pre_cutover_is_reported_never_ok_flip():
    # R5-D: a code work_product positioned BEFORE the run's first landing record classifies
    # pre_cutover_unverifiable — a NAMED report bucket that does not flip ok.
    recs = (_base_pair() + [_code_wp_rec("n1", new="sha256:new0")]
            + [_receipt_rec("n2", cid="c2"), _obs_rec("n2", cid="c2"),
               _code_wp_rec("n2", new="sha256:new2"), _landing_for("n2", new="sha256:new2")])
    res = enforce.reconcile_run([_EC(), _EC(cid="c2")], recs, initial_digest="sha256:d")
    assert res.ok, res
    assert any("n1" in x for x in res.pre_cutover_unverifiable)
    assert res.unlanded_work_product == ()


def test_reconcile_no_landings_at_all_is_all_pre_cutover():
    # zero landing records in the run = the pre-#762 world; every unmatched code wp is
    # pre_cutover (reported), never a hard unlanded failure — historical runs stay readable.
    recs = _base_pair() + [_code_wp_rec("n1")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert res.ok, res
    assert any("n1" in x for x in res.pre_cutover_unverifiable)


def test_reconcile_orphan_landing_flips_ok():
    recs = _base_pair() + [_code_wp_rec("n1"), _landing_for("n1"),
                           _landing_for("nX", new="sha256:other")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok
    assert any("nX" in x for x in res.orphan_landing)


def test_reconcile_landing_mismatch_new_sha_flips_ok():
    recs = _base_pair() + [_code_wp_rec("n1", new="sha256:new1"),
                           _landing_for("n1", new="sha256:FORGED")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok
    assert any("n1" in x for x in res.landing_mismatch)


def test_reconcile_landing_mismatch_noncanonical_temp_ref(tmp_path):
    # R3-B A4: temp_ref must be the canonical nonce ref — a schema-valid but mis-bound
    # record fails reconcile and never suppresses the unlanded flag.
    recs = _base_pair() + [_code_wp_rec("n1"),
                           _landing_for("n1", temp="refs/rawgentic/collect/OTHER")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok
    assert any("n1" in x for x in res.landing_mismatch)
    assert any("n1" in x for x in res.unlanded_work_product)  # the mis-bound landing heals nothing


def test_reconcile_landing_mismatch_target_ref_disagrees():
    # R3-B A4: the paired code work_product's target_ref must equal the landing's temp_ref
    recs = _base_pair() + [_code_wp_rec("n1", target="refs/rawgentic/collect/n1"),
                           _landing_for("n1", temp="refs/rawgentic/collect/n1x")]
    # the landing's temp ref is canonical for nonce "n1x", not "n1" — mis-bound either way
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok
    assert any("n1" in x for x in res.landing_mismatch)


def test_reconcile_landing_run_id_mismatch_flags():
    recs = _base_pair() + [_code_wp_rec("n1"), _landing_for("n1", run_id="runFOREIGN")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d", run_id="runL")
    assert not res.ok
    assert any("n1" in x for x in res.landing_mismatch)


def test_reconcile_landing_run_id_unchecked_when_unknown():
    # reconcile_run without a run_id (legacy callers/tests) skips the run-identity arm
    recs = _base_pair() + [_code_wp_rec("n1"), _landing_for("n1", run_id="runFOREIGN")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert res.ok, res


def test_reconcile_landing_conflict_differing_content_flips_ok():
    # rev-2 adoption 4 + R3-B: same nonce, two landings with DIFFERING immutable identity
    recs = _base_pair() + [_code_wp_rec("n1"), _landing_for("n1"),
                           _landing_for("n1", feature_ref="refs/heads/OTHER")]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert not res.ok
    assert any("n1" in x for x in res.landing_conflict)


def test_reconcile_landing_byte_identical_duplicates_collapse():
    # byte-identical duplicates (retry metadata aside) collapse — no conflict
    recs = _base_pair() + [_code_wp_rec("n1"), _landing_for("n1"),
                           _landing_for("n1", status="already_landed", ts=2000)]
    res = enforce.reconcile_run([_EC()], recs, initial_digest="sha256:d")
    assert res.ok, res
    assert res.landing_conflict == ()
