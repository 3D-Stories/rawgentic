"""Task 5: routing config epoch — reload swaps snapshot only on a digest change, emits one epoch
event, and in-flight snapshots keep their pinned digest."""
import json

from phase_executor.routing import RoutingConfig


def _table(concurrency=2):
    # #464 fixture migration: loaded via RoutingConfig (validation path), so the review seat needs
    # a schema-valid manifest; the canonical seat declares its role + a policy section for
    # forward-compat with the Task-2 name<->role loader lint.
    return {
        "schema_version": "1",
        "policy": {"enforced_roles": ["review", "build"]},
        "pools": {"claude": {"concurrency": concurrency}},
        "seats": {"review": {"role": "review",
                  "primary": {"model": "claude-fable-5",
                  "lane": {"provider": "anthropic", "transport": "native", "auth_mode": "subscription_oauth", "pool": "claude"}},
                  "chain": [],
                  "manifest": {"session_policy": "fresh", "tool_grants": ["read"], "effort": "high",
                               "confinement": {"anthropic": "hooks"}, "bounds": {"timeout_s": 1800}}}},
    }


def test_reload_unchanged_is_not_an_epoch(tmp_path):
    p = tmp_path / "rt.json"
    p.write_text(json.dumps(_table()))
    events = []
    cfg = RoutingConfig(p, on_epoch=lambda old, new: events.append((old, new)))
    changed, snap = cfg.reload()
    assert changed is False
    assert events == []  # no digest change => no epoch


def test_reload_changed_emits_one_epoch(tmp_path):
    p = tmp_path / "rt.json"
    p.write_text(json.dumps(_table(2)))
    events = []
    cfg = RoutingConfig(p, on_epoch=lambda old, new: events.append((old, new)))
    old_digest = cfg.snapshot.config_digest
    p.write_text(json.dumps(_table(3)))  # change concurrency -> new digest
    changed, snap = cfg.reload()
    assert changed is True
    assert len(events) == 1
    assert events[0] == (old_digest, snap.config_digest)
    assert snap.config_digest != old_digest


def test_inflight_snapshot_keeps_old_digest(tmp_path):
    p = tmp_path / "rt.json"
    p.write_text(json.dumps(_table(2)))
    cfg = RoutingConfig(p)
    inflight = cfg.snapshot  # a caller pinned this
    old_digest = inflight.config_digest
    p.write_text(json.dumps(_table(3)))
    cfg.reload()
    assert inflight.config_digest == old_digest  # unchanged reference
    assert cfg.snapshot.config_digest != old_digest  # new runs get the new epoch
