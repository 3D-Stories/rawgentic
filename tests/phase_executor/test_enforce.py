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


# ---- target_identity ----

def test_target_identity_tuple():
    t = _target("claude-opus-4-8[1m]", _lane("claude", cred="ACCT_A"))
    tid = target_identity(t)
    # canonicalized model + full lane identity
    assert tid == ("claude-opus-4-8", "anthropic", "native", "subscription_oauth", "claude", "ACCT_A")


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
    r = check_pre("build", t, snap, correlation_id="c", attempt_id="0",
                  gate_digest="g", gate_validator=lambda d: True)
    # off_chain AND forbidden both accumulate (haiku isn't in build's chain either)
    assert r.verdict == "fail"
    assert any(v.startswith("forbidden") for v in r.violations)


def test_check_pre_review_requires_author_provider():
    snap = _snapshot()
    t = _target("claude-fable-5", _lane("claude"))
    r = check_pre("review", t, snap, correlation_id="c", attempt_id="0", author_provider=None)
    assert r.verdict == "fail"
    assert "author_provider_missing" in r.violations


def test_check_pre_build_gate_validation_unavailable():
    snap = _snapshot()
    t = _target("claude-sonnet-5", _lane("claude"))
    r = check_pre("build", t, snap, correlation_id="c", attempt_id="0", gate_digest="anything")
    assert r.verdict == "fail"
    assert "gate_validation_unavailable" in r.violations  # no validator -> fail closed


def test_check_pre_build_gate_invalid():
    snap = _snapshot()
    t = _target("claude-sonnet-5", _lane("claude"))
    r = check_pre("build", t, snap, correlation_id="c", attempt_id="0",
                  gate_digest="bad", gate_validator=lambda d: False)
    assert r.verdict == "fail"
    assert "gate_invalid" in r.violations


def test_check_pre_build_pass_with_validator():
    snap = _snapshot()
    t = _target("claude-sonnet-5", _lane("claude"))
    r = check_pre("build", t, snap, correlation_id="c", attempt_id="0",
                  gate_digest="good", gate_validator=lambda d: d == "good")
    assert r.verdict == "pass" and not r.violations


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
                target_identity=("claude-opus-4-8", "anthropic", "native", "subscription_oauth", "claude", None),
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
    table = {"schema_version": "1", "pools": {"claude": {"concurrency": 2}},
             "seats": {"review": {"primary": {"model": "claude-fable-5", "lane": _lane("claude")}, "chain": []}},
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
        ("claude-sonnet-5", "anthropic", "native", "subscription_oauth", "claude", "ACCT_X")


# ---- reconcile_run ----

_DEF_LANE = {"provider": "anthropic", "transport": "native", "auth_mode": "subscription_oauth",
             "pool": "claude", "credential_ref": None}
_DEF_TID = ("claude-fable-5", "anthropic", "native", "subscription_oauth", "claude", None)


def _receipt_rec(nonce, seat="review", cid="c1", verdict="pass", tid=_DEF_TID, digest="sha256:d"):
    return {"kind": "receipt", "nonce": nonce, "seat": seat, "correlation_id": cid, "attempt_id": "0",
            "target_identity": list(tid), "config_digest": digest, "gate_digest": None,
            "author_provider": "openai", "verdict": verdict, "violations": []}


def _obs_rec(nonce, requested="claude-fable-5", actual="claude-fable-5", lane=None, status="ok", digest="sha256:d"):
    inner = _obs_dict(seat="review", requested_model=requested, actual_model=actual,
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


def test_reconcile_require_nonempty_refuses_empty():
    """Step-8a Finding 3: require_nonempty makes an empty expected-set refuse ship, not pass vacuously."""
    assert enforce.reconcile_run([], [], initial_digest="sha256:d").ok  # default: empty is ok
    res = enforce.reconcile_run([], [], initial_digest="sha256:d", require_nonempty=True)
    assert not res.ok and res.orphan
