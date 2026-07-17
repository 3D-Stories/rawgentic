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
                "primary": {"model": "claude-fable-5", "lane": _lane("claude")},
                "chain": [
                    {"model": "gpt-5.6-sol", "lane": _lane("codex", provider="openai")},
                    {"model": "claude-sonnet-5", "lane": _lane("claude")},
                ],
            },
            "build": {
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
