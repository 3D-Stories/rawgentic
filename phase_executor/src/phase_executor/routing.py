"""Declarative routing table: load + validate + immutable snapshot + canonical digest/epoch
+ chain-aware eligibility.

The engine is policy-free: invariants (never-Haiku, the cross-model author rule) are
project-supplied ``forbidden_combinations`` rows in the table, evaluated here as data.

Digest / epoch: the config digest is a sha256 over the CANONICAL JSON encoding of the table
(sorted keys, no whitespace) so Python and a Rust producer (kukakuka) compute the same value —
a golden vector is pinned in the tests. A reload that yields a NEW digest is an audited epoch
event (``on_epoch`` callback), not a mismatch failure; in-flight runs keep their pinned snapshot.
``credential_ref`` names a config dir / env key, never a secret value, so it is safe in the digest.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import contract


class ChainExhausted(RuntimeError):
    """No eligible target remained in a seat's primary+chain (handled hard failure)."""


class RoutingError(RuntimeError):
    """Malformed routing table (unknown seat, lane pool not declared, etc.)."""


def load_routing_table(path: os.PathLike | str) -> dict:
    table = json.loads(Path(path).read_text(encoding="utf-8"))
    contract.validate_routing_table(table)  # fail-closed on schema violation
    _assert_referential_integrity(table)
    return table


def _assert_referential_integrity(table: dict) -> None:
    """Fail-closed cross-field semantic passes the JSON schema cannot express (#464 §C.2 + §D).

    Schema validation (``contract.validate_routing_table``) runs FIRST in ``load_routing_table``,
    so a table reaching here on the production path already has a well-shaped manifest/policy. The
    ``.get`` guards below are belt-and-suspenders for programmatic tables that call this pass
    directly and bypass the schema. Every failure raises ``RoutingError`` naming the offending
    seat/entry (fail-loud, no snapshot => no launch)."""
    pools = set(table.get("pools", {}))
    for seat_name, seat in table.get("seats", {}).items():
        targets = [seat["primary"], *seat.get("chain", [])]
        for target in targets:
            pool = target["lane"]["pool"]
            if pool not in pools:
                raise RoutingError(f"seat {seat_name!r} target {target['model']!r} names undeclared pool {pool!r}")
        # (a) CONFINEMENT COVERAGE: every provider a seat can dispatch on (primary + chain lanes)
        # must be declared in its manifest.confinement map — no lane may run unconfined.
        manifest = seat.get("manifest")
        if manifest is None:
            raise RoutingError(f"seat {seat_name!r} missing manifest")
        confined = set(manifest.get("confinement", {}))
        lane_providers = {t["lane"]["provider"] for t in targets}
        uncovered = lane_providers - confined
        if uncovered:
            raise RoutingError(
                f"seat {seat_name!r} confinement does not cover lane provider(s) {sorted(uncovered)} "
                f"(declared: {sorted(confined)})"
            )
        # (c) NAME<->ROLE BINDING LINT: a canonically-named seat must declare the matching role.
        # check_pre keys its per-role requirements on `role` (renamed-seat portability), so a
        # seat NAMED 'build'/'review' with a missing/mismatched role would silently escape those
        # requirements — a table-authoring hole this closes at load.
        if seat_name in ("build", "review") and seat.get("role") != seat_name:
            raise RoutingError(
                f"seat named {seat_name!r} must declare role {seat_name!r} (got {seat.get('role')!r})"
            )
    # (b) ENFORCED-ROLES BOUND: a table may not declare a role enforced that the engine has no
    # evaluator for (contract.ENFORCEABLE_ROLES is the ceiling). Policy is optional (programmatic
    # legacy tables omit it); the shipped table always declares it.
    policy = table.get("policy")
    if policy is not None:
        for role in policy.get("enforced_roles", ()):
            if role not in contract.ENFORCEABLE_ROLES:
                raise RoutingError(
                    f"policy.enforced_roles entry {role!r} is not in ENFORCEABLE_ROLES "
                    f"{sorted(contract.ENFORCEABLE_ROLES)} (nothing evaluates it)"
                )


def canonical_bytes(table: dict) -> bytes:
    """Canonical JSON encoding for cross-language-reproducible digests."""
    return json.dumps(table, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def digest(table: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(table)).hexdigest()


@dataclass(frozen=True)
class RoutingSnapshot:
    """An immutable routing epoch: the table plus its digest."""
    table: dict
    config_digest: str

    @staticmethod
    def from_table(table: dict) -> "RoutingSnapshot":
        # Deep-copy on ingest so a caller mutating the original dict cannot change routing behavior
        # under an already-computed (audited) digest, and an in-flight snapshot stays fixed.
        import copy  # noqa: PLC0415
        frozen = copy.deepcopy(table)
        return RoutingSnapshot(table=frozen, config_digest=digest(frozen))

    def pool_concurrency(self) -> Dict[str, int]:
        return {name: spec["concurrency"] for name, spec in self.table["pools"].items()}

    def seat(self, name: str) -> dict:
        try:
            return self.table["seats"][name]
        except KeyError:
            raise RoutingError(f"unknown seat: {name!r}") from None


def snapshot_from_file(path: os.PathLike | str) -> RoutingSnapshot:  # noqa: F821
    return RoutingSnapshot.from_table(load_routing_table(path))


class RoutingConfig:
    """Holds the current immutable snapshot; ``reload`` swaps it atomically and emits an epoch
    event only when the digest changes. In-flight callers hold their own snapshot reference."""

    def __init__(self, path: os.PathLike | str, *, on_epoch: Optional[Callable[[str, str], None]] = None):  # noqa: F821
        self._path = Path(path)
        self._on_epoch = on_epoch
        self._lock = threading.Lock()
        self._snapshot = snapshot_from_file(self._path)

    @property
    def snapshot(self) -> RoutingSnapshot:
        return self._snapshot

    def reload(self) -> tuple[bool, RoutingSnapshot]:
        new = snapshot_from_file(self._path)
        with self._lock:
            old = self._snapshot
            if new.config_digest == old.config_digest:
                return False, old  # no change => not an epoch, not a mismatch
            self._snapshot = new
        if self._on_epoch is not None:
            self._on_epoch(old.config_digest, new.config_digest)
        return True, new


# ---- eligibility ---------------------------------------------------------------

def _row_matches(target: dict, row: dict, author_provider: Optional[str]) -> bool:
    """True if a forbidden_combinations row forbids this target. A row with only match-fields
    (engine/transport/auth_mode/model_pattern) forbids when EVERY present field matches; the
    ``cross_model_author`` rule forbids a target whose provider equals the author's."""
    lane = target["lane"]
    if row.get("rule") == "cross_model_author":
        return author_provider is not None and lane["provider"] == author_provider
    matched_any = False
    if "engine" in row:
        if lane["provider"] != row["engine"]:
            return False
        matched_any = True
    if "transport" in row:
        if lane["transport"] != row["transport"]:
            return False
        matched_any = True
    if "auth_mode" in row:
        if lane["auth_mode"] != row["auth_mode"]:
            return False
        matched_any = True
    if "model_pattern" in row:
        if row["model_pattern"].lower() not in target["model"].lower():
            return False
        matched_any = True
    return matched_any


def target_forbidden_reason(target: dict, snapshot: RoutingSnapshot, *, author_provider: Optional[str] = None) -> Optional[str]:
    for row in snapshot.table.get("forbidden_combinations", []):
        if _row_matches(target, row, author_provider):
            return row["reason"]
    return None


def eligible_targets(seat_name: str, snapshot: RoutingSnapshot, *, author_provider: Optional[str] = None) -> List[dict]:
    """Primary + chain in order, with every forbidden target skipped (chain-aware skip —
    never blind next-entry)."""
    seat = snapshot.seat(seat_name)
    ordered = [seat["primary"], *seat.get("chain", [])]
    return [t for t in ordered if target_forbidden_reason(t, snapshot, author_provider=author_provider) is None]


def select_target(seat_name: str, snapshot: RoutingSnapshot, *, author_provider: Optional[str] = None) -> dict:
    """First eligible target for the seat, or raise ChainExhausted (never a silent downgrade)."""
    elig = eligible_targets(seat_name, snapshot, author_provider=author_provider)
    if not elig:
        raise ChainExhausted(
            f"seat {seat_name!r}: no eligible target (all primary+chain entries forbidden"
            + (f" for author_provider={author_provider!r}" if author_provider else "") + ")"
        )
    return elig[0]
