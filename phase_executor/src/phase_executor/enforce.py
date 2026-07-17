"""Routing enforcement (E2, #425) — verified, not trusted.

Executor-internal enforcement functions (NOT Claude Code harness hooks): a pre-dispatch check
that mints a receipt (``check_pre`` -> ``PreReceipt``), a post-call requested==actual verification
(``verify_post``), a per-run append-only audit log (``RoutingAuditLog``), and a run-end
reconciliation (``reconcile_run``) that binds every expected seat call to a PASSED receipt + a
VERIFIED observation. Pure Python over the E1 primitives (contract/routing/capture); imports no
``hooks/`` so the package stays extraction-ready for a Rust producer (kukakuka).

Chain of custody: ``check_pre`` mints a receipt (recorded BEFORE launch); the engine stamps the
Observation's ``dispatched_lane`` from the target it actually ran; ``reconcile_run`` binds the two
by ``(seat, correlation_id)`` + receipt ``nonce`` and refuses ship unless the receipt's
``target_identity``/``config_digest`` match the observation's own ``dispatched_lane``/digest.

Honest limit (glm's plan review): if a CLI silently substitutes a model WITHOUT reflecting it in
its own output, ``verify_post`` cannot see it — it compares against the provider-reported
``actual_model``. Mitigation, not a fix: pin CLI versions and fixture-test the parsers. And
``dispatched_lane`` is executor-recorded, not provider-attested — it binds executor-record
consistency (pre-checked target == dispatched target), not physical provider attestation.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from . import contract, routing


def target_identity(target: dict) -> tuple:
    """Canonical identity of a routing target: canonicalized model id + the full lane.

    Model canonicalization is ONE field (aliases/prefixes/date collapse); the lane fields are
    compared verbatim, so an allowed model smuggled through an undeclared
    provider/transport/auth_mode/pool/credential is caught. Returns a 6-tuple
    ``(canon_model, provider, transport, auth_mode, pool, credential_ref)``.
    """
    lane = target["lane"]
    return (
        contract.canonicalize_model_id(target["model"]),
        lane["provider"], lane["transport"], lane["auth_mode"], lane["pool"], lane.get("credential_ref"),
    )


@dataclass(frozen=True)
class PreReceipt:
    """A pre-dispatch receipt minted by ``check_pre``. The caller records it to the audit log
    BEFORE launching; a ``fail`` verdict means the call must NOT launch. Bound to its observation at
    reconcile by ``nonce`` + ``target_identity`` + ``config_digest``."""

    nonce: str
    seat: str
    correlation_id: str
    attempt_id: str
    target_identity: tuple
    config_digest: str
    gate_digest: Optional[str]
    author_provider: Optional[str]
    verdict: str  # "pass" | "fail"
    violations: tuple = ()

    def to_dict(self) -> dict:
        return {
            "kind": "receipt",
            "nonce": self.nonce,
            "seat": self.seat,
            "correlation_id": self.correlation_id,
            "attempt_id": self.attempt_id,
            "target_identity": list(self.target_identity),
            "config_digest": self.config_digest,
            "gate_digest": self.gate_digest,
            "author_provider": self.author_provider,
            "verdict": self.verdict,
            "violations": list(self.violations),
        }


def _declared_identities(seat_obj: dict) -> set:
    targets = [seat_obj["primary"], *seat_obj.get("chain", [])]
    return {target_identity(t) for t in targets}


def check_pre(seat, target, snapshot, *, correlation_id, attempt_id, gate_digest=None,
              gate_validator=None, author_provider=None, nonce=None) -> PreReceipt:
    """Pre-dispatch enforcement, evaluated against the EXACT snapshot that will serve the call.

    Accumulates ALL violations (never short-circuits — the caller sees every problem):
    - ``off_chain``: the target's full identity is not one of the seat's declared chain entries.
    - ``forbidden``: a ``forbidden_combinations`` row matches (never-Haiku; cross_model_author).
    - ``author_provider_missing``: a review seat with no ``author_provider`` (the cross_model_author
      rule would otherwise be silently inert — fail closed).
    - build gate (requirement DERIVED from ``seat == "build"``, never a caller bool):
      ``gate_validation_unavailable`` when no ``gate_validator`` is supplied (fail closed until #429
      provides one); ``gate_invalid`` when the validator rejects ``gate_digest``.

    Unknown seat -> ``routing.RoutingError`` (fail-loud). Returns a ``PreReceipt``;
    ``verdict == "pass"`` iff there are no violations.
    """
    seat_obj = snapshot.seat(seat)  # raises routing.RoutingError on an unknown seat
    tid = target_identity(target)
    violations = []
    if tid not in _declared_identities(seat_obj):
        violations.append(f"off_chain: {tid!r} not in declared chain of seat {seat!r}")
    reason = routing.target_forbidden_reason(target, snapshot, author_provider=author_provider)
    if reason is not None:
        violations.append(f"forbidden: {reason}")
    if seat == "review" and author_provider is None:
        violations.append("author_provider_missing")
    if seat == "build":
        if gate_validator is None:
            violations.append("gate_validation_unavailable")
        elif not gate_validator(gate_digest):
            violations.append("gate_invalid")
    return PreReceipt(
        nonce=nonce or uuid.uuid4().hex,
        seat=seat,
        correlation_id=correlation_id,
        attempt_id=attempt_id,
        target_identity=tid,
        config_digest=snapshot.config_digest,
        gate_digest=gate_digest,
        author_provider=author_provider,
        verdict="pass" if not violations else "fail",
        violations=tuple(violations),
    )
