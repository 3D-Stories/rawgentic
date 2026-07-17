"""Task 5: routing.py — load/validate, referential integrity, canonical digest (+golden vector),
chain-aware eligibility, forbidden_combinations, ChainExhausted."""
import copy
import json
import pathlib

import jsonschema
import pytest

from phase_executor import routing
from phase_executor.routing import (
    ChainExhausted, RoutingError, RoutingSnapshot, digest, eligible_targets,
    load_routing_table, select_target,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SHIPPED = REPO_ROOT / "phase_executor" / "src" / "phase_executor" / "routing" / "rawgentic.routing-table.json"


def _lane(pool, provider="anthropic", transport="native", auth="subscription_oauth"):
    return {"provider": provider, "transport": transport, "auth_mode": auth, "credential_ref": None, "pool": pool}


def _table(**over):
    t = {
        "schema_version": "1",
        "pools": {"claude": {"concurrency": 2}, "codex": {"concurrency": 4}},
        "seats": {
            "review": {
                "primary": {"model": "claude-fable-5", "lane": _lane("claude")},
                "chain": [
                    {"model": "gpt-5.6-sol", "lane": _lane("codex", provider="openai")},
                    {"model": "claude-sonnet-5", "lane": _lane("claude")},
                ],
            }
        },
        "forbidden_combinations": [
            {"model_pattern": "haiku", "reason": "never Haiku"},
            {"rule": "cross_model_author", "reason": "reviewer != author engine"},
        ],
    }
    t.update(over)
    return t


def test_load_shipped_table():
    table = load_routing_table(SHIPPED)
    assert "review" in table["seats"]


def test_referential_integrity_rejects_unknown_pool():
    bad = _table()
    bad["seats"]["review"]["primary"]["lane"]["pool"] = "ghost"
    p = pathlib.Path  # noqa
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".json")
    os.write(fd, json.dumps(bad).encode()); os.close(fd)
    with pytest.raises(RoutingError):
        load_routing_table(path)
    os.unlink(path)


def test_schema_invalid_table_fails_closed(tmp_path):
    bad = _table()
    del bad["pools"]  # required
    p = tmp_path / "bad.json"; p.write_text(json.dumps(bad))
    with pytest.raises(jsonschema.ValidationError):
        load_routing_table(p)


def test_digest_deterministic_and_key_order_invariant():
    t1 = _table()
    t2 = json.loads(json.dumps(t1))  # same content
    assert digest(t1) == digest(t2)
    t3 = copy.deepcopy(t1)
    t3["pools"]["claude"]["concurrency"] = 3
    assert digest(t3) != digest(t1)


def test_digest_golden_vector():
    """Cross-language parity: a fixed table hashes to a pinned value (Rust producer must match)."""
    fixed = {"schema_version": "1", "pools": {"p": {"concurrency": 1}},
             "seats": {"s": {"primary": {"model": "m", "lane": {"provider": "x", "transport": "native", "auth_mode": "a", "pool": "p"}}, "chain": []}}}
    import hashlib
    expect = "sha256:" + hashlib.sha256(routing.canonical_bytes(fixed)).hexdigest()
    assert digest(fixed) == expect
    # and canonical bytes are sorted/compact
    assert routing.canonical_bytes(fixed) == json.dumps(fixed, sort_keys=True, separators=(",", ":")).encode()


def test_eligibility_chain_aware_skip_by_author():
    snap = RoutingSnapshot.from_table(_table())
    # author is anthropic -> both claude targets (fable, sonnet) skipped, only openai sol remains
    elig = eligible_targets("review", snap, author_provider="anthropic")
    assert [t["model"] for t in elig] == ["gpt-5.6-sol"]
    assert select_target("review", snap, author_provider="anthropic")["model"] == "gpt-5.6-sol"


def test_eligibility_no_author_keeps_full_chain():
    snap = RoutingSnapshot.from_table(_table())
    elig = eligible_targets("review", snap)
    assert [t["model"] for t in elig] == ["claude-fable-5", "gpt-5.6-sol", "claude-sonnet-5"]


def test_forbidden_model_pattern_skips_haiku():
    t = _table()
    t["seats"]["review"]["chain"].append({"model": "claude-haiku-4-5", "lane": _lane("claude")})
    snap = RoutingSnapshot.from_table(t)
    assert all("haiku" not in x["model"] for x in eligible_targets("review", snap))


def test_chain_exhausted_raises():
    # single-target seat whose only target is forbidden for this author
    t = _table()
    t["seats"] = {"solo": {"primary": {"model": "claude-opus-4-8", "lane": _lane("claude")}, "chain": []}}
    snap = RoutingSnapshot.from_table(t)
    with pytest.raises(ChainExhausted):
        select_target("solo", snap, author_provider="anthropic")


def test_snapshot_immune_to_source_mutation():
    """A caller mutating the original table after snapshotting must not change routing under the
    already-computed digest (diff-review finding #11)."""
    t = _table()
    snap = RoutingSnapshot.from_table(t)
    d0 = snap.config_digest
    t["pools"]["claude"]["concurrency"] = 99
    t["seats"]["review"]["primary"]["model"] = "hacked"
    assert snap.config_digest == d0
    assert snap.pool_concurrency()["claude"] == 2
    assert snap.seat("review")["primary"]["model"] == "claude-fable-5"


def test_shipped_table_digest_stable_and_pools():
    snap = RoutingSnapshot.from_table(load_routing_table(SHIPPED))
    assert snap.config_digest.startswith("sha256:")
    assert snap.pool_concurrency()["claude"] == 2


# --- #426: full seat table (intake/plan/build/review/ship) + fallback chains + provenance ---

_EXPECTED_SEATS_426 = {
    "intake": ("claude-opus-4-8", ["claude-fable-5", "claude-sonnet-5"]),
    "plan": ("claude-opus-4-8", ["claude-fable-5", "gpt-5.6-terra"]),
    "build": ("claude-sonnet-5", ["claude-opus-4-8", "gpt-5.6-terra"]),
    "review": ("claude-fable-5", ["gpt-5.6-sol", "claude-sonnet-5"]),
    "ship": ("claude-sonnet-5", ["claude-opus-4-8", "claude-fable-5"]),
}


def test_shipped_table_full_seat_set_426():
    table = load_routing_table(SHIPPED)
    assert set(table["seats"]) == set(_EXPECTED_SEATS_426)
    for seat, (primary, chain) in _EXPECTED_SEATS_426.items():
        s = table["seats"][seat]
        assert s["primary"]["model"] == primary, seat
        assert [c["model"] for c in s["chain"]] == chain, seat


def test_shipped_table_every_seat_has_fallback_chain_426():
    table = load_routing_table(SHIPPED)
    for seat, s in table["seats"].items():
        assert len(s["chain"]) >= 1, f"seat {seat} has no fallback chain"


def test_shipped_table_enforcement_roles_426():
    table = load_routing_table(SHIPPED)
    assert table["seats"]["review"].get("role") == "review"
    assert table["seats"]["build"].get("role") == "build"
    for seat in ("intake", "plan", "ship"):
        assert "role" not in table["seats"][seat]  # non-review/build seats carry no enforcement role


def test_shipped_table_never_haiku_426():
    table = load_routing_table(SHIPPED)
    models = [table["seats"][s]["primary"]["model"] for s in table["seats"]]
    models += [c["model"] for s in table["seats"].values() for c in s.get("chain", [])]
    assert not any("haiku" in m.lower() for m in models), "never routes a seat to Haiku"
    assert any(r.get("model_pattern") == "haiku" for r in table["forbidden_combinations"])


def test_shipped_table_provenance_bench14_426():
    table = load_routing_table(SHIPPED)
    assert "provenance" in table, "seat table must carry a provenance stamp"
    assert "14" in json.dumps(table["provenance"]), "provenance names bench #14"
