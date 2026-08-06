"""Revision-bound action claims, execute-once (#943/#947 Part B §6).

The ONE mechanism in this design that authorizes an outward side effect (a merge, a
consult send, anything an unsupervised session must not repeat or race). WRITE, locked
— mirrors `supervision_admin.py`'s pattern (fail-loud refusals, atomic replace).

Two files, one fixed lock order. `.supervision.json` (Part A, under `workspace_root`)
carries the revision fence; `claude_docs/.supervision-claims/<campaign_id>.json` (under
`project_root`, same convention as `claude_docs/.driver-state/<campaign_id>.json`)
carries the claims themselves. Any function that must read BOTH takes the supervision-
file lock FIRST, then the claims-file lock — the same order `mark_attended` uses when it
runs `cancel_claims` under its own hold of the supervision lock — so a revision bump and
a claim-state transition can never interleave (design §6, round-3 finding 5).

Claim identity is `(campaign_id, blocker_id, action_kind, action_target, action_digest)`
— deliberately NOT `bound_revision`. The revision is an authorization FENCE (was this
claim made under a supervision state still in force?), checked at `begin_execution` and
`reconcile_claim` time, never part of what the action IS (round 3 finding 6): a claim
already `executing`/`executed` under an OLDER revision must still block a fresh claim
for the identical real-world action, or the same side effect could run twice.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
from datetime import datetime, timezone

from atomic_write_lib import atomic_write_text

import plan_lib
import supervision_lib as sl

SCHEMA_VERSION = 1
CLAIM_STATES = ("pending", "executing", "executed", "cancelled")

_ACTION_TARGET_RE_PR = re.compile(r"^pr:[a-z0-9_.-]+/[a-z0-9_.-]+#[0-9]+$")
_ACTION_TARGET_RE_ISSUE = re.compile(r"^issue:[0-9]+$")


class ClaimError(Exception):
    """A claim operation was refused; nothing was written."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def claims_path(project_root: str, campaign_id: str) -> str:
    """Path-safety: reuses Part A's EXISTING `validate_campaign_id` for the basename —
    campaign ids are used as filesystem basenames elsewhere in this project already, so
    an unvalidated value here would be a path-traversal write primitive."""
    if not sl.validate_campaign_id(campaign_id):
        raise ClaimError(f"invalid campaign_id: {campaign_id!r}")
    return os.path.join(project_root, "claude_docs", ".supervision-claims",
                        f"{campaign_id}.json")


def _claims_dir(project_root: str) -> str:
    return os.path.join(project_root, "claude_docs", ".supervision-claims")


def normalize_action_target(action_target) -> str:
    """Lowercase, strip a trailing slash, and require one of the two documented shapes
    (`pr:<owner>/<repo>#<n>` or `issue:<n>`) — so two callers naming the "same" PR
    provably produce the same string (design §6)."""
    if not isinstance(action_target, str) or not action_target.strip():
        raise ClaimError("action_target must be a non-empty string")
    normalized = action_target.strip().lower().rstrip("/")
    if not (_ACTION_TARGET_RE_PR.match(normalized) or _ACTION_TARGET_RE_ISSUE.match(normalized)):
        raise ClaimError(
            "action_target must match 'pr:<owner>/<repo>#<n>' or 'issue:<n>', "
            f"got {action_target!r}")
    return normalized


def compute_action_digest(action_params: dict) -> str:
    """Computed HERE from structured params — never accepted as a caller-supplied hash
    (round 3 finding 7), so two callers describing the same real action always agree."""
    if not isinstance(action_params, dict):
        raise ClaimError("action_params must be a dict")
    payload = json.dumps(action_params, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _identity(claim: dict) -> tuple:
    """Execute-once identity. Deliberately EXCLUDES `blocker_id` (Step 11 cross-model
    review, High finding 2): a blocker rename, or two blocker records describing the
    same real-world action, must still collide on one claim — `blocker_id` is metadata
    about WHY the claim was minted, never part of WHAT the action is."""
    return (claim["campaign_id"], claim["action_kind"],
            claim["action_target"], claim["action_digest"])


def _read_claims(path: str) -> dict:
    if not os.path.exists(path):
        return {"schema_version": SCHEMA_VERSION, "claims": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise ClaimError(f"cannot read claims file {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
        raise ClaimError(f"claims file {path} does not match the expected schema")
    return data


def _write_claims(path: str, data: dict) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n",
                      prefix=".supervision-claims.", mkdir=True, fsync=True)


#: Step 11 cross-model review, High finding 8: a CORRUPT supervision file must never
#: agree with any real `bound_revision` a claim could legitimately carry (revisions are
#: always >= 0 — see `supervision_admin._current`'s own "absent" vs "invalid" split,
#: which this mirrors). Returning the SAME 0 for "absent" and "invalid" let a caller
#: mint or execute a claim under a corrupt/unreadable authorization state, because 0 is
#: also the legitimate revision of a workspace that has genuinely never declared
#: anything. `-1` can never equal a real revision, so every comparison against it
#: denies, without changing any caller's raise/return contract.
_INVALID_SUPERVISION_REVISION_SENTINEL = -1


def _current_supervision_revision(workspace_root: str) -> int:
    loaded = sl.read_state(workspace_root)
    if loaded.load_status == "invalid":
        return _INVALID_SUPERVISION_REVISION_SENTINEL
    if loaded.load_status != "valid":
        return 0
    return int(loaded.record.get("revision", 0))


def claim_action(*, project_root: str, workspace_root: str, campaign_id: str,
                 blocker_id: str, action_kind: str, action_target: str,
                 action_params: dict, bound_revision: int, session_id: str) -> dict:
    """Mint (or return the existing) claim for this identity. Locked read-modify-write
    on the claims file; re-reads the CURRENT supervision revision inside the SAME call
    and refuses to mint a NEW claim if it no longer matches the caller's `bound_revision`
    intent (revalidation as part of one locked operation, not a separate unlocked step)."""
    if not isinstance(action_kind, str) or not action_kind.strip():
        raise ClaimError("action_kind must be a non-empty string")
    if not isinstance(blocker_id, str) or not blocker_id.strip():
        raise ClaimError("blocker_id must be a non-empty string")
    # A real revision is never negative (finding 8's own sentinel is -1, reserved to
    # mean "supervision state is unreadable" and never a legitimate value to bind to):
    # rejecting a negative bound_revision HERE, at the only minting point, closes the
    # loophole a caller could otherwise open by simply supplying -1 itself to match the
    # sentinel and mint under corrupt authorization state.
    if int(bound_revision) < 0:
        raise ClaimError(
            f"bound_revision must be >= 0, got {bound_revision!r} — a negative value "
            "can never be a real supervision revision")
    normalized_target = normalize_action_target(action_target)
    digest = compute_action_digest(action_params)
    path = claims_path(project_root, campaign_id)

    # Step 11 cross-model review, Medium finding 9: the supervision-file lock is taken
    # FIRST, then the claims-file lock — the SAME fixed order `begin_execution` and
    # `reconcile_claim` use, so the revision this mint binds to can never change between
    # being read and being written into the new claim.
    supervision_path = sl.supervision_path(workspace_root)
    with plan_lib.file_lock(supervision_path):
        current_revision = _current_supervision_revision(workspace_root)
        if int(bound_revision) != current_revision:
            raise ClaimError(
                f"bound_revision {bound_revision} no longer matches the current "
                f"supervision revision {current_revision} — re-read and decide again "
                "rather than minting a claim under a stale revision")

        with plan_lib.file_lock(path):
            data = _read_claims(path)
            wanted = {"campaign_id": campaign_id, "action_kind": action_kind,
                      "action_target": normalized_target, "action_digest": digest}
            for existing in data["claims"]:
                if _identity(existing) == _identity(wanted) \
                        and existing["state"] in ("pending", "executing", "executed"):
                    return dict(existing)

            claim = {
                "claim_id": f"c-{os.urandom(6).hex()}",
                "campaign_id": campaign_id, "blocker_id": blocker_id,
                "action_kind": action_kind, "action_target": normalized_target,
                "action_digest": digest, "action_params": action_params,
                "bound_revision": current_revision, "created_at": _now_iso(),
                "state": "pending", "session_id": session_id,
            }
            data["claims"].append(claim)
            _write_claims(path, data)
            return dict(claim)


def begin_execution(*, project_root: str, workspace_root: str, campaign_id: str,
                    claim_id: str) -> bool:
    """Atomic `pending -> executing`. Takes the supervision-file lock FIRST (a short
    critical section — just reading the current revision), then the claims-file lock;
    verifies state is still `pending` AND `bound_revision` still equals the revision
    just read under that first lock. Returns False on any mismatch; the caller MUST NOT
    act on False. This fixed order — never the reverse — is what makes it impossible for
    this read to observe a revision `mark_attended`/`cancel_claims` is mid-way through
    changing (round 3 finding 5)."""
    supervision_path = sl.supervision_path(workspace_root)
    with plan_lib.file_lock(supervision_path):
        current_revision = _current_supervision_revision(workspace_root)
        claims_file = claims_path(project_root, campaign_id)
        with plan_lib.file_lock(claims_file):
            data = _read_claims(claims_file)
            for claim in data["claims"]:
                if claim["claim_id"] != claim_id:
                    continue
                if claim["state"] != "pending" or claim["bound_revision"] != current_revision:
                    return False
                claim["state"] = "executing"
                _write_claims(claims_file, data)
                return True
    return False


def mark_executed(*, project_root: str, campaign_id: str, claim_id: str,
                  evidence: dict) -> dict:
    """Atomic `executing -> executed`. Refuses (raises) from any other state."""
    path = claims_path(project_root, campaign_id)
    with plan_lib.file_lock(path):
        data = _read_claims(path)
        for claim in data["claims"]:
            if claim["claim_id"] == claim_id:
                if claim["state"] != "executing":
                    raise ClaimError(
                        f"claim {claim_id} is {claim['state']!r}, not 'executing' — "
                        "refusing mark_executed")
                claim["state"] = "executed"
                claim["evidence"] = evidence
                _write_claims(path, data)
                return dict(claim)
        raise ClaimError(f"no such claim: {claim_id!r}")


def cancel_claims(*, project_root: str, campaign_id: "str | None" = None) -> list:
    """Cancel every claim still `pending` — NEVER `executing`/`executed`. Called by
    `/rawgentic:back`, under the same fixed lock order `begin_execution` uses. With
    `campaign_id=None`, sweeps every campaign's claims file under `project_root` (the
    owner's return clears pending claims workspace-wide, not one campaign at a time).
    Returns the list of claims actually cancelled."""
    if campaign_id is not None:
        paths = [claims_path(project_root, campaign_id)]
    else:
        paths = sorted(glob.glob(os.path.join(_claims_dir(project_root), "*.json")))

    cancelled = []
    for path in paths:
        if not os.path.exists(path):
            continue
        with plan_lib.file_lock(path):
            data = _read_claims(path)
            changed = False
            for claim in data["claims"]:
                if claim["state"] == "pending":
                    claim["state"] = "cancelled"
                    cancelled.append(dict(claim))
                    changed = True
            if changed:
                _write_claims(path, data)
    return cancelled


def reconcile_claim(*, project_root: str, workspace_root: str, campaign_id: str,
                    claim_id: str, evidence_probe) -> str:
    """Reconcile-before-retry (no idempotency key — GitHub's merge API takes none).
    `evidence_probe(claim) -> "resolved"|"retry"|"unknown"` is the caller's own check of
    whether the real-world side effect actually happened.

    - "resolved": the action DID happen -> atomic `executing -> executed`, same
      transition as `mark_executed`.
    - "retry": the action did NOT happen -> atomic `executing -> pending`, WITH
      `bound_revision` re-validated against the CURRENT supervision revision; if it no
      longer matches, the claim goes to `cancelled` instead (reconciliation never
      resurrects a claim whose authorization has since lapsed).
    - "unknown": the probe itself failed -> the claim STAYS `executing`, unchanged —
      automation stops here and a human looks.
    """
    supervision_path = sl.supervision_path(workspace_root)
    with plan_lib.file_lock(supervision_path):
        current_revision = _current_supervision_revision(workspace_root)
        claims_file = claims_path(project_root, campaign_id)
        with plan_lib.file_lock(claims_file):
            data = _read_claims(claims_file)
            for claim in data["claims"]:
                if claim["claim_id"] != claim_id:
                    continue
                if claim["state"] != "executing":
                    raise ClaimError(
                        f"claim {claim_id} is {claim['state']!r}, not 'executing' — "
                        "refusing reconcile_claim")
                outcome = evidence_probe(dict(claim))
                if outcome == "resolved":
                    claim["state"] = "executed"
                    _write_claims(claims_file, data)
                    return "resolved"
                if outcome == "retry":
                    if claim["bound_revision"] == current_revision:
                        claim["state"] = "pending"
                    else:
                        claim["state"] = "cancelled"
                    _write_claims(claims_file, data)
                    return "retry"
                if outcome == "unknown":
                    return "unknown"
                raise ClaimError(
                    f"evidence_probe returned {outcome!r}, must be one of "
                    "'resolved'|'retry'|'unknown'")
            raise ClaimError(f"no such claim: {claim_id!r}")
