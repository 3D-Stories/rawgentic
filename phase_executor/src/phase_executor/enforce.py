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
    - ``author_provider_missing``: a ``role: "review"`` seat with no ``author_provider`` (the
      cross_model_author rule would otherwise be silently inert — fail closed).
    - build gate (requirement DERIVED from the seat's ``role == "build"``, never a caller bool or a
      literal seat name): ``gate_validation_unavailable`` when no ``gate_validator`` is supplied
      (fail closed until #429 provides one); ``gate_invalid`` when the validator rejects ``gate_digest``.

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
    # Enforcement is keyed on the seat's declared ``role`` in the routing table, NOT its literal name
    # (Step-8a: a name-based check silently disabled itself on a renamed seat, and is not portable to
    # kukakuka's seat vocabulary). A seat with no ``role`` carries no review/build requirement.
    role = seat_obj.get("role")
    if role == "review" and author_provider is None:
        violations.append("author_provider_missing")
    if role == "build":
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
    """Post-dispatch verification, keyed on whether an envelope was produced (Step-8a: the earlier
    version treated EVERY non-ok status as benign, so ``identity_failure`` — the real-envelope
    wrong-model case — escaped the breach check).

    - An AVAILABILITY failure (``launch_error``/``nonzero_exit``/``timeout``/``no_response``) means no
      usable envelope: ``ok=True, verified=False`` — an honest failure (``run_seat`` already fell
      back), not a breach.
    - Otherwise an envelope WAS produced (``ok``, ``usage_unavailable``, ``identity_failure``,
      ``parse_error``, ``harness_error``): routing is VERIFIED iff the provider-attested
      ``actual_model`` canonicalize-matches ``requested_model``. A missing identity
      (``identity_missing``) or a mismatch (``requested_actual_mismatch``) is a NON-retryable breach
      (``ok=False``) — retrying re-bills the same wrong route. (This VERIFIES a ``usage_unavailable``
      call whose identity matches — identity is attested, only token counts are missing — instead of
      wrongly refusing it.)

    Accepts an ``Observation`` or its dict form."""
    d = obs.to_dict() if isinstance(obs, contract.Observation) else obs
    status = d.get("parse_status")
    if status in contract.AVAILABILITY_FAILURES:
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


@dataclass(frozen=True)
class ExpectedCall:
    """The stable identity of an expected seat call (WF2 step/task id; kukakuka turn nonce)."""

    seat: str
    correlation_id: str


@dataclass(frozen=True)
class Reconcile:
    """Run-end reconciliation verdict. ``ok`` iff every anomaly list is empty — reconcile refuses
    ship on ANY. Fail-closed."""

    ok: bool
    missing_receipt: tuple = ()
    failed_precheck: tuple = ()
    missing_obs: tuple = ()
    binding_mismatch: tuple = ()
    duplicate_nonce: tuple = ()
    duplicate: tuple = ()
    unverified: tuple = ()
    unaudited_digest: tuple = ()
    orphan: tuple = ()


def reconcile_run(expected, records, *, initial_digest, require_nonempty=False):
    """Run-end audit: bind every expected seat call to a PASSED receipt + a VERIFIED observation,
    with the observation's OWN ``dispatched_lane``/digest matching the receipt (independent binding).
    Refuses ship (``ok=False``) on any anomaly. ``known_digests`` is derived INTERNALLY via
    ``audited_digests`` (not a caller input) from a trusted ``initial_digest``. The ``expected``
    sequence must have unique ``(seat, correlation_id)`` tuples (else ValueError). Expects the
    fail-closed-validated output of ``RoutingAuditLog.records()``; a raw record missing a required
    receipt field fails loud (KeyError), never a silent pass.

    ``require_nonempty`` (Step-8a defense-in-depth): when True, an EMPTY ``expected`` refuses ship
    rather than passing vacuously — so a wiring bug that drops the expected-set cannot silently ship
    a run in which nothing was verified."""
    exp_keys = [(e.seat, e.correlation_id) for e in expected]
    if len(exp_keys) != len(set(exp_keys)):
        raise ValueError("reconcile_run: expected has duplicate (seat, correlation_id) tuples")
    if require_nonempty and not exp_keys:
        return Reconcile(ok=False, orphan=("no-expected-calls: require_nonempty set but expected is empty",))
    exp_set = set(exp_keys)
    known = audited_digests(records, initial_digest)  # raises on a broken epoch chain

    receipts = [r for r in records if r.get("kind") == "receipt"]
    observations = [r for r in records if r.get("kind") == "observation"]

    missing_receipt, failed_precheck, missing_obs, binding_mismatch = [], [], [], []
    duplicate_nonce, duplicate, unverified, unaudited_digest, orphan = [], [], [], [], []

    by_nonce = {}
    for r in receipts:
        n = r["nonce"]
        if n in by_nonce:
            duplicate_nonce.append(n)
        else:
            by_nonce[n] = r
        if r["config_digest"] not in known:
            unaudited_digest.append(n)
        if (r["seat"], r["correlation_id"]) not in exp_set:
            orphan.append(f"receipt:{n}")

    obs_by_nonce = {}
    for o in observations:
        n = o["receipt_nonce"]
        inner = o["observation"]
        try:
            contract.validate_observation(inner)
        except Exception:  # noqa: BLE001 — a non-conforming inner obs is an orphan/invalid envelope (fail closed)
            orphan.append(f"observation:{n}:invalid")
            continue
        rec = by_nonce.get(n)
        if rec is None:
            binding_mismatch.append(f"{n}:no-receipt")
            continue
        if inner.get("seat") != rec["seat"]:  # Step-8a Finding 2: obs seat must match its receipt's
            binding_mismatch.append(f"{n}:seat-drift")
            continue
        lane = inner.get("dispatched_lane")
        if not lane:
            binding_mismatch.append(f"{n}:no-dispatched_lane")
            continue
        obs_tid = target_identity({"model": inner["requested_model"], "lane": lane})
        if list(obs_tid) != list(rec["target_identity"]) or rec["config_digest"] != inner.get("routing_config_digest"):
            binding_mismatch.append(f"{n}:identity-or-digest")
            continue
        if (rec["seat"], rec["correlation_id"]) not in exp_set:
            orphan.append(f"observation:{n}:unexpected")
            continue
        if n in obs_by_nonce:
            duplicate.append(n)
        else:
            obs_by_nonce[n] = o

    for key in exp_keys:
        seat, cid = key
        rs = [r for r in receipts if r["seat"] == seat and r["correlation_id"] == cid]
        for r in rs:
            if r["verdict"] == "fail" and r["nonce"] in obs_by_nonce:
                failed_precheck.append(r["nonce"])
        passed = [r for r in rs if r["verdict"] == "pass"]
        if not passed:
            missing_receipt.append(key)
            continue
        saw_verified = saw_breach = saw_missing_obs = False
        for r in passed:
            o = obs_by_nonce.get(r["nonce"])
            if o is None:
                saw_missing_obs = True
                continue
            pc = verify_post(o["observation"])
            if pc.verified:
                saw_verified = True
            elif not pc.ok:
                saw_breach = True
            # pc.ok and not verified => availability failure => a legitimate fallback attempt
        # A wrong-model BREACH (requested!=actual, non-retryable) is NEVER forgiven — evaluated
        # BEFORE saw_verified, because a sibling attempt verifying must not launder a recorded,
        # billed breach on the same call (Step-8a Finding 1: only availability failures are
        # legitimate forgivable siblings, per the design).
        if saw_breach:
            unverified.append(key)
        elif saw_verified:
            continue
        elif saw_missing_obs:
            missing_obs.append(key)
        else:
            missing_receipt.append(key)  # passed receipts all availability-failed: the call was never served

    ok = not any([missing_receipt, failed_precheck, missing_obs, binding_mismatch,
                  duplicate_nonce, duplicate, unverified, unaudited_digest, orphan])
    return Reconcile(
        ok=ok, missing_receipt=tuple(missing_receipt), failed_precheck=tuple(failed_precheck),
        missing_obs=tuple(missing_obs), binding_mismatch=tuple(binding_mismatch),
        duplicate_nonce=tuple(duplicate_nonce), duplicate=tuple(duplicate),
        unverified=tuple(unverified), unaudited_digest=tuple(unaudited_digest), orphan=tuple(orphan),
    )
