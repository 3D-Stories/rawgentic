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

import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import capture, contract, routing
from .contract import ENFORCEABLE_ROLES  # re-export: enforce.ENFORCEABLE_ROLES is the public-API home (#464 §D)


def target_identity(target: dict) -> tuple:
    """Canonical identity of a routing target: canonicalized model id + the full lane.

    Model canonicalization is ONE field (aliases/prefixes/date collapse); the lane fields are
    compared verbatim, so an allowed model smuggled through an undeclared
    provider/transport/auth_mode/pool/credential/participation_mode is caught. Returns a 7-tuple
    ``(canon_model, provider, transport, auth_mode, pool, credential_ref, participation_mode)``.
    ``participation_mode`` is included (Step-11) so two targets differing ONLY in that schema-optional
    lane field do not collapse to the same identity — relevant to kukakuka's participation modes.
    """
    lane = target["lane"]
    return (
        contract.canonicalize_model_id(target["model"]),
        lane["provider"], lane["transport"], lane["auth_mode"], lane["pool"],
        lane.get("credential_ref"), lane.get("participation_mode"),
    )


@dataclass(frozen=True)
class GateAttestation:
    """Authenticated #429 gate evidence, minted at the HOOKS boundary AFTER the GateDecision is
    authenticated (digest recompute), and bound to ONE launch via ``input_digest`` (#464 §E).

    An ``isinstance`` check against this type is the in-process trust boundary — a raw dict never
    satisfies it, so ``check_pre`` cannot be handed a hand-rolled attestation. Honest limit: this is
    NOT cryptographic (the digests are unkeyed) — the SAME trust class as the existing receipt/audit
    chain. It defends against authoring errors, stale reuse, and cross-launch replay, never a hostile
    in-process caller (HMAC/signed attestations are the named follow-up if cross-process trust is
    ever needed).

    Fields:
    - ``gate_outcome``: the #429 routing decision — ``"bakeoff"`` | ``"single"``.
    - ``policy_digest``: the gate's policy digest (recorded into the receipt's ``gate_digest`` slot).
    - ``input_digest``: ``launch_input_digest(seat, target, correlation_id)`` for the exact launch
      this attestation authorizes; ``check_pre`` recomputes it and rejects a mismatch.
    """

    gate_outcome: str
    policy_digest: str
    input_digest: str


def launch_input_digest(seat: str, target: dict, correlation_id: str) -> str:
    """The canonical digest binding a gate attestation to ONE launch: sha256 over canonical JSON of
    the seat, the target's full identity, and the correlation id. Uses ``routing.canonical_bytes``
    (sort_keys, compact separators, ensure_ascii) so the digest is cross-language reproducible. Hooks
    mint an attestation carrying this digest; ``check_pre`` recomputes it from its own args and
    refuses a mismatch (stale/replayed gate evidence cannot cross launches)."""
    payload = {
        "seat": seat,
        "target_identity": list(target_identity(target)),
        "correlation_id": correlation_id,
    }
    return "sha256:" + hashlib.sha256(routing.canonical_bytes(payload)).hexdigest()


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
    # #464 §E: the seat's role + attestation evidence, so the audit can PROVE a build launch was
    # gated. Defaulted (None) so old constructors and role-less legacy receipts are unchanged.
    role: Optional[str] = None
    gate_outcome: Optional[str] = None
    gate_input_digest: Optional[str] = None

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
            "role": self.role,
            "gate_outcome": self.gate_outcome,
            "gate_input_digest": self.gate_input_digest,
        }


def _declared_identities(seat_obj: dict) -> set:
    targets = [seat_obj["primary"], *seat_obj.get("chain", [])]
    return {target_identity(t) for t in targets}


def check_pre(seat: str, target: dict, snapshot, *, correlation_id, attempt_id, gate_digest=None,
              author_provider=None, nonce=None, attestation: "GateAttestation | None" = None) -> PreReceipt:
    """Pre-dispatch enforcement, evaluated against the EXACT snapshot that will serve the call.

    Accumulates ALL violations (never short-circuits — the caller sees every problem):
    - ``off_chain``: the target's full identity is not one of the seat's declared chain entries.
    - ``forbidden``: a ``forbidden_combinations`` row matches (never-Haiku; cross_model_author).
    - ``unrecognized_role`` (#464 §D): the seat's declared ``role`` is non-empty but NOT in the
      table's ``policy.enforced_roles`` set — fail closed. A missing/empty role carries no
      requirement; a policy-less programmatic table has an EMPTY enforced set, so ANY non-empty role
      fails (the safe direction). Keying on the TABLE-declared set (not a code constant) keeps the
      engine policy-free and rides #445's per-project extraction.
    - ``author_provider_missing``: a ``role: "review"`` seat with no ``author_provider`` (the
      cross_model_author rule would otherwise be silently inert — fail closed).
    - build gate (requirement DERIVED from the seat's ``role == "build"``, #464 §E): the build path
      proceeds ONLY on an authenticated, launch-bound ``GateAttestation``. ``gate_missing`` (none
      supplied); ``gate_invalid: <detail>`` (not a ``GateAttestation``; an unknown ``gate_outcome``;
      or an ``input_digest`` != ``launch_input_digest(seat, target, correlation_id)`` — stale/replayed
      evidence); ``gate_requires_bakeoff`` (a valid attestation whose outcome is ``"bakeoff"`` — the
      single-dispatch path may only proceed on a ``"single"`` outcome; the gate's routing decision is
      not bypassable). This module verifies the attestation's SHAPE + launch BINDING + outcome, and
      NEVER the gate's AUTHENTICITY (extraction-clean, no hooks import): authenticating the #429
      GateDecision (digest recompute) is the hooks boundary's job.

    Unknown seat -> ``routing.RoutingError`` (fail-loud). Returns a ``PreReceipt``;
    ``verdict == "pass"`` iff there are no violations. When an attestation is present, its
    ``gate_outcome`` / ``input_digest`` and (as ``gate_digest``) ``policy_digest`` are recorded on the
    receipt so the audit can prove a build launch was gated.
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
    # kukakuka's seat vocabulary). A seat with no ``role`` (or an empty-string role) carries no
    # review/build requirement.
    role = seat_obj.get("role")
    enforced = frozenset(snapshot.table.get("policy", {}).get("enforced_roles", ()))
    if role and role not in enforced:
        # #464 §D: a non-empty role the TABLE does not enforce fails closed (an empty enforced set —
        # a policy-less programmatic table — fails ALL non-empty roles: the safe direction).
        violations.append(f"unrecognized_role: {role!r} not in {sorted(enforced)}")
    if role == "review" and author_provider is None:
        violations.append("author_provider_missing")
    if role == "build":
        # #464 §E: the build path proceeds ONLY on an authenticated, launch-bound GateAttestation.
        # This module verifies SHAPE + BINDING + outcome; the hooks boundary authenticates the #429
        # GateDecision (digest recompute) before minting the attestation — enforce never imports
        # hooks, so it cannot and does not verify gate AUTHENTICITY (honest, extraction-clean limit).
        if attestation is None:
            violations.append("gate_missing")
        elif not isinstance(attestation, GateAttestation):
            violations.append("gate_invalid: not a GateAttestation")
        elif attestation.gate_outcome not in ("bakeoff", "single"):
            violations.append(f"gate_invalid: unknown outcome {attestation.gate_outcome!r}")
        elif attestation.input_digest != launch_input_digest(seat, target, correlation_id):
            violations.append("gate_invalid: input digest mismatch (stale/replayed gate evidence)")
        elif attestation.gate_outcome != "single":
            violations.append("gate_requires_bakeoff")
    # #464 §E: only a real GateAttestation feeds the receipt (a junk/absent attestation records
    # nothing); its policy_digest repurposes the pre-existing gate_digest slot (today always None in
    # production). An explicit gate_digest arg is still honoured when no attestation is present.
    valid_attestation = attestation if isinstance(attestation, GateAttestation) else None
    return PreReceipt(
        nonce=nonce or uuid.uuid4().hex,
        seat=seat,
        correlation_id=correlation_id,
        attempt_id=attempt_id,
        target_identity=tid,
        config_digest=snapshot.config_digest,
        gate_digest=valid_attestation.policy_digest if valid_attestation is not None else gate_digest,
        author_provider=author_provider,
        verdict="pass" if not violations else "fail",
        violations=tuple(violations),
        role=role or None,
        gate_outcome=valid_attestation.gate_outcome if valid_attestation is not None else None,
        gate_input_digest=valid_attestation.input_digest if valid_attestation is not None else None,
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

    (A ``harness_error`` — a candidate that never dispatched — carries no identity, so it reads as
    ``identity_missing``; such an obs never reaches ``reconcile_run`` anyway, since it has no
    ``dispatched_lane`` and ``append_observation`` rejects it.)

    Accepts an ``Observation`` or its dict form."""
    d = obs.to_dict() if isinstance(obs, contract.Observation) else obs
    status = d.get("parse_status")
    actual = d.get("actual_model")
    if actual:
        # An identity was reported -> verify it REGARDLESS of status. A wrong id on ANY status,
        # including a contradictory availability status that nonetheless carries an id, is a breach
        # (Step-11: the availability branch must not short-circuit past a populated wrong actual_model).
        if contract.models_match(d.get("requested_model"), actual):
            return PostCheck(ok=True, verified=True, reason="ok", retryable=False)
        return PostCheck(ok=False, verified=False, reason="requested_actual_mismatch", retryable=False)
    # No identity reported:
    if status in contract.AVAILABILITY_FAILURES:
        return PostCheck(ok=True, verified=False, reason=str(status), retryable=False)  # honest availability failure
    return PostCheck(ok=False, verified=False, reason="identity_missing", retryable=False)  # produced envelope w/o identity = breach


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
    if kind == "receipt" and obj.get("role") == "build" and obj.get("verdict") == "pass":
        # #464 §E: an APPROVED build receipt must PROVE it was gated — truthy gate_outcome +
        # gate_input_digest (fail-closed; empty strings prove nothing). A verdict='fail' build
        # receipt legitimately carries null gate fields (gate_missing / gate_invalid denials are
        # recorded BEFORE the verdict check) and must stay readable — else one denial poisons the
        # whole run's audit log. Receipts with NO 'role' key (all historical logs) skip this
        # branch and validate exactly as today; _RECEIPT_REQUIRED is unchanged.
        if not obj.get("gate_outcome") or not obj.get("gate_input_digest"):
            raise ValueError(f"audit line {lineno}: build receipt missing gate evidence")
        # Step-11 diff review (reopens step4p2-P2): the reader guard exists for corrupt/tampered
        # logs — a PASS build receipt claiming any outcome but "single" is corruption (check_pre
        # fails a bakeoff outcome), and gate digests must be sha256-canonical, not merely truthy.
        if obj["gate_outcome"] != "single":
            raise ValueError(f"audit line {lineno}: pass build receipt with non-single "
                             f"gate_outcome {obj['gate_outcome']!r}")
        for field in ("gate_input_digest", "gate_digest"):
            val = obj.get(field)
            if not isinstance(val, str) or not val.startswith("sha256:"):
                raise ValueError(f"audit line {lineno}: build receipt {field} not a canonical "
                                 f"sha256 digest")
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
    ``initial_digest`` (seqs == 1..n IN FILE ORDER, each ``from`` == the previous ``to``). A gap,
    repeat, reorder, or fork RAISES (fail-closed)."""
    # Do NOT sort — the epoch lines are read in FILE (append) order, and seq must already be 1..n in
    # that order. Sorting first would NORMALIZE a physically-reordered/tampered log and hide the
    # reorder (Step-11: the docstring promised reorder raises; sorting silently defeated that).
    epochs = [r for r in records if r.get("kind") == "epoch"]
    seqs = [e["seq"] for e in epochs]
    if seqs != list(range(1, len(epochs) + 1)):
        raise ValueError(f"epoch seqs not in contiguous file order 1..n (gap/repeat/reorder): {seqs}")
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


def reconcile_run(expected, records, *, initial_digest: str, require_nonempty: bool = True) -> "Reconcile":
    """Run-end audit: bind every expected seat call to a PASSED receipt + a VERIFIED observation,
    with the observation's OWN ``dispatched_lane``/digest matching the receipt (independent binding).
    Refuses ship (``ok=False``) on any anomaly. ``known_digests`` is derived INTERNALLY via
    ``audited_digests`` (not a caller input) from a trusted ``initial_digest``. The ``expected``
    sequence must have unique ``(seat, correlation_id)`` tuples (else ValueError). Expects the
    fail-closed-validated output of ``RoutingAuditLog.records()``; a raw record missing a required
    receipt field fails loud (KeyError), never a silent pass.

    ``require_nonempty`` (Step-11 fail-closed DEFAULT True): an EMPTY ``expected`` refuses ship
    rather than passing vacuously — so a wiring bug that drops the expected-set cannot silently ship
    a run in which nothing was verified. Pass ``require_nonempty=False`` only for a legitimately
    zero-routed-call run."""
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
        if inner.get("correlation_id") != rec["correlation_id"]:  # Step-11 Crit: obs must be for the receipt's OWN call
            binding_mismatch.append(f"{n}:correlation-drift")
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
        # A BREACH (wrong/absent model) and a MISSING observation on an approved-launch attempt are
        # BOTH evaluated BEFORE saw_verified — a sibling attempt that verified must not launder a
        # recorded billed breach (Step-8a) NOR an approved dispatch that produced no observation at
        # all (Step-11 bug/logic: a lost/uninstrumented attempt is fail-closed, not benign). Only an
        # availability-failed sibling is a legitimate forgivable fallback.
        if saw_breach:
            unverified.append(key)
        elif saw_missing_obs:
            missing_obs.append(key)
        elif saw_verified:
            continue
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
