"""Checked-in per-model capability registry (#465 W2, spike #456 evidence).

Offline + deterministic: runtime NEVER probes a provider. Every canonical model id the
shipped routing table dispatches has a row (a drift-guard test pins the superset); an
UNKNOWN model with a REQUESTED effort refuses at resolve time (fail closed — fix the
registry, never guess a capability). Bump CAPABILITY_REVISION on any edit (provenance —
recorded on every Observation.effort object).
"""
from __future__ import annotations

CAPABILITY_REVISION: int = 1

EFFORT_LADDER: tuple = ("low", "medium", "high", "xhigh", "max")

_ALL = EFFORT_LADDER
# Spike #456 (live probes): the 5 names are identity across providers; SUPPORT is
# per-model — gpt-5.5 rejects `max`; max confirmed on gpt-5.6-sol/terra/luna; the claude
# CLI accepts all 5 for its models; glm-5.2 accepts all 5.
SUPPORTED_EFFORT: dict = {
    "claude-fable-5": _ALL,
    "claude-opus-4-8": _ALL,
    "claude-sonnet-5": _ALL,
    "gpt-5.6-sol": _ALL,
    "gpt-5.6-terra": _ALL,
    "gpt-5.6-luna": _ALL,
    "gpt-5.5": ("low", "medium", "high", "xhigh"),
    "glm-5.2": _ALL,
    # #568 Phase-2: the Hermes gateway has a FIXED internal model + no effort control; the seat
    # pins medium so routing validation is satisfied, and the adapter accepts-but-does-not-forward
    # it. Additive new-engine row — CAPABILITY_REVISION is deliberately NOT bumped (no existing
    # model's capability changed; test_engine pins the produced revision at 1).
    "hermes-agent": ("medium",),
}

# Per-ENGINE None policy (#465 S1/P2-S1): what a None-effort request becomes on the wire.
# codex historically forced "high" (adapters/codex_cli.py) — the value now lives HERE, the
# ONE source both the resolver's adapter_default branch and the adapter's fallback read,
# so they agree by construction. Engines absent from this map send no effort flag on None
# (provider default), recorded as identity/None.
ENGINE_NONE_EFFORT: dict = {"codex": "high"}
