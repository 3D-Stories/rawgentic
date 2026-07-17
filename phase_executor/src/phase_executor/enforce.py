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

import json
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import capture, contract, routing


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


@dataclass(frozen=True)
class PostCheck:
    """Post-call verification result. ``verified`` = requested==actual proven; ``ok`` = no
    enforcement breach. An honest availability/parse/identity failure is ``ok=True, verified=False``
    (not a breach); a requested!=actual mismatch or a missing identity on an ``ok`` call is
    ``ok=False`` and NON-retryable."""

    ok: bool
    verified: bool
    reason: str
    retryable: bool = False


def verify_post(obs) -> PostCheck:
    """Post-dispatch verification. On ``parse_status == "ok"`` the provider-reported ``actual_model``
    MUST canonicalize-match ``requested_model``; a mismatch (``requested_actual_mismatch``) or a
    missing identity (``identity_missing``) is a NON-retryable enforcement breach — the model
    responded with the wrong/absent id, so retrying re-bills the same wrong route. A non-ok status
    is an honest failure (already fell back in ``run_seat`` for availability): recorded, not a breach.
    Accepts an ``Observation`` or its dict form."""
    d = obs.to_dict() if isinstance(obs, contract.Observation) else obs
    status = d.get("parse_status")
    if status != contract.OK:
        return PostCheck(ok=True, verified=False, reason=str(status), retryable=False)
    actual = d.get("actual_model")
    if not actual:
        return PostCheck(ok=False, verified=False, reason="identity_missing", retryable=False)
    if not contract.models_match(d.get("requested_model"), actual):
        return PostCheck(ok=False, verified=False, reason="requested_actual_mismatch", retryable=False)
    return PostCheck(ok=True, verified=True, reason="ok", retryable=False)


_RECEIPT_REQUIRED = ("kind", "nonce", "seat", "correlation_id", "attempt_id",
                     "target_identity", "config_digest", "verdict")
_OBS_ENVELOPE_REQUIRED = ("kind", "receipt_nonce", "observation")
_EPOCH_REQUIRED = ("kind", "seq", "from", "to")
_VERDICTS = ("pass", "fail")


def _validate_record(obj, lineno: int) -> None:
    """Fail-closed audit-record shape check: unknown kind, a missing required field, a bad verdict,
    or a malformed epoch transition all raise ValueError — a corrupt/tampered audit is never clean."""
    if not isinstance(obj, dict):
        raise ValueError(f"audit line {lineno}: not a JSON object")
    kind = obj.get("kind")
    req = {"receipt": _RECEIPT_REQUIRED, "observation": _OBS_ENVELOPE_REQUIRED, "epoch": _EPOCH_REQUIRED}.get(kind)
    if req is None:
        raise ValueError(f"audit line {lineno}: unknown kind {kind!r}")
    missing = [k for k in req if k not in obj]
    if missing:
        raise ValueError(f"audit line {lineno}: {kind} missing fields {missing}")
    if kind == "receipt" and obj["verdict"] not in _VERDICTS:
        raise ValueError(f"audit line {lineno}: bad verdict {obj['verdict']!r}")
    if kind == "epoch":
        if not isinstance(obj["seq"], int) or isinstance(obj["seq"], bool):
            raise ValueError(f"audit line {lineno}: epoch seq not an int")
        if not isinstance(obj["from"], str) or not isinstance(obj["to"], str):
            raise ValueError(f"audit line {lineno}: epoch from/to not strings")


class RoutingAuditLog:
    """Per-run append-only JSONL audit log. Thread-safe (competitive candidates run in threads): a
    lock guards every append (write + flush + fsync). Line variants: ``receipt`` (pre-launch),
    ``observation`` (the binding envelope carrying ``receipt_nonce`` + the raw Observation), and
    ``epoch`` (a config-reload transition, ordered by ``seq``). The trusted ``capture_root`` and the
    untrusted ``run_id`` are SEPARATE params — only ``run_id`` is sanitized/contained."""

    def __init__(self, capture_root, run_id, *, filename: str = "routing-audit.jsonl"):
        root = Path(capture_root).resolve()
        safe = capture.sanitize_component(run_id)  # rejects empty / all-dot; neutralizes traversal chars
        target_dir = root / safe
        resolved = target_dir.resolve()
        if resolved != root and root not in resolved.parents:  # belt-and-suspenders containment (E1 pattern)
            raise ValueError(f"audit path escapes capture root: {target_dir}")
        target_dir.mkdir(parents=True, exist_ok=True)
        self.path = target_dir / filename
        self._lock = threading.Lock()
        self._seq = 0

    def _write_locked(self, obj) -> None:
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

    def append_receipt(self, receipt) -> None:
        with self._lock:
            self._write_locked(receipt.to_dict())

    def append_observation(self, obs, *, receipt) -> None:
        d = obs.to_dict() if isinstance(obs, contract.Observation) else dict(obs)
        contract.validate_observation(d)  # fail-loud: only real executor envelopes enter the log
        if not d.get("dispatched_lane"):
            raise ValueError("append_observation: observation missing dispatched_lane (cannot bind receipt<->observation)")
        with self._lock:
            self._write_locked({"kind": "observation", "receipt_nonce": receipt.nonce, "observation": d})

    def append_epoch(self, old_digest: str, new_digest: str) -> None:
        with self._lock:
            self._seq += 1
            self._write_locked({"kind": "epoch", "seq": self._seq, "from": old_digest, "to": new_digest})

    def records(self) -> list:
        """Parse + fail-closed-validate every line. A malformed line raises (never silently dropped)."""
        out = []
        if not self.path.exists():
            return out
        for i, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"audit line {i}: malformed JSON ({e})") from e
            _validate_record(obj, i)
            out.append(obj)
        return out


def audited_digests(records, initial_digest: str) -> frozenset:
    """The digests that were ever a real audited epoch: ``{initial_digest}`` plus the ``to`` of every
    epoch line, AFTER validating the epoch lines form ONE ordered contiguous chain rooted at
    ``initial_digest`` (seqs == 1..n, each ``from`` == the previous ``to``). A gap, repeat, reorder,
    or fork RAISES (fail-closed)."""
    epochs = sorted((r for r in records if r.get("kind") == "epoch"), key=lambda r: r["seq"])
    seqs = [e["seq"] for e in epochs]
    if seqs != list(range(1, len(epochs) + 1)):
        raise ValueError(f"epoch seqs not contiguous 1..n: {seqs}")
    digests = {initial_digest}
    prev = initial_digest
    for e in epochs:
        if e["from"] != prev:
            raise ValueError(f"epoch chain break at seq {e['seq']}: from {e['from']!r} != prev {prev!r}")
        digests.add(e["to"])
        prev = e["to"]
    return frozenset(digests)
