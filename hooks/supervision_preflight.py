"""Departure-preflight staging, campaign-scoped answers (#947 Part B §3).

WRITE, locked staging — folds into `supervision_admin.declare` (T10). Lives under
`workspace_root` (the SAME root as `.supervision.json`), not `project_root`: `declare`
already has `workspace_root` and reads the staging file under the SAME lock as its own
write, so keeping both files on one root avoids plumbing a second root through Part A's
existing write path.

Answers stage un-bound to any supervision revision — binding happens only inside
`declare`'s own atomic write. This is the fix for the carried finding: writing several
campaign files before the declaration lands, then having the declaration itself fail,
must never leave `preflight_results` bound to a revision that was never written. Nothing
here computes a revision at all; `declare` stamps one in, later, under its own lock.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from atomic_write_lib import atomic_write_text

import plan_lib

SCHEMA_VERSION = 1
DISPOSITIONS = ("resolved", "deferred", "declined")


class PreflightError(Exception):
    """A preflight operation was refused, or the token could not be read."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _preflight_dir(workspace_root: str) -> str:
    return os.path.join(workspace_root, "claude_docs", ".supervision-preflight")


def preflight_path(workspace_root: str, token: str) -> str:
    return os.path.join(_preflight_dir(workspace_root), f"{token}.json")


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as exc:
        raise PreflightError(f"no such preflight token (file not found: {path})") from exc
    except (OSError, ValueError) as exc:
        raise PreflightError(f"cannot read preflight file {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("answers"), list):
        raise PreflightError(f"preflight file {path} does not match the expected schema")
    return data


def _write(path: str, data: dict) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n",
                      prefix=".supervision-preflight.", mkdir=True, fsync=True)


def begin_preflight(workspace_root: str, *, session_id: str, campaign_ids: list) -> str:
    """Create the staging file (locked, atomic write). Status is implicit in its
    existence — no separate status field, since `abandon_preflight` deletes it."""
    token = f"pf-{os.urandom(8).hex()}"
    path = preflight_path(workspace_root, token)
    with plan_lib.file_lock(path):
        _write(path, {
            "schema_version": SCHEMA_VERSION, "token": token,
            "session_id": session_id, "started_at": _now_iso(),
            "campaign_ids": list(campaign_ids), "answers": [],
        })
    return token


def read_preflight(workspace_root: str, token: str) -> dict:
    path = preflight_path(workspace_root, token)
    if not os.path.exists(path):
        raise PreflightError(f"no such preflight token: {token!r}")
    with plan_lib.file_lock(path):
        return _read(path)


def record_preflight_answer(workspace_root: str, token: str, *, campaign_id: str,
                            blocker_id: str, question_kind: str, answer,
                            disposition: str, authority_basis: str,
                            applied_ref=None) -> None:
    """Append one answer. Answer identity is (campaign_id, blocker_id, question_kind) —
    NOT (blocker_id, question_kind) — so two campaigns reusing the same blocker_id shape
    never collide (round-1 finding 1).

    Refuses BEFORE staging (raises, writes nothing) when:
    - `campaign_id` is not in this token's own `campaign_ids` list (finding 1), or
    - `disposition == "resolved"` with no `applied_ref` (round-2 finding 2) — a
      resolved-but-unapplied answer is a contradiction this module can catch cheaply,
      even though it cannot verify the referenced write is semantically correct.
    """
    if disposition not in DISPOSITIONS:
        raise PreflightError(
            f"disposition must be one of {DISPOSITIONS}, got {disposition!r}")
    if disposition == "resolved" and not applied_ref:
        raise PreflightError(
            "a 'resolved' answer requires applied_ref — evidence of the write that "
            "actually applied the decision")

    path = preflight_path(workspace_root, token)
    if not os.path.exists(path):
        raise PreflightError(f"no such preflight token: {token!r}")
    with plan_lib.file_lock(path):
        data = _read(path)
        if campaign_id not in data["campaign_ids"]:
            raise PreflightError(
                f"campaign_id {campaign_id!r} is not in this token's own campaign_ids "
                f"{data['campaign_ids']}")
        data["answers"].append({
            "campaign_id": campaign_id, "blocker_id": blocker_id,
            "question_kind": question_kind, "answer": answer,
            "disposition": disposition, "authority_basis": authority_basis,
            "answered_at": _now_iso(), "applied_ref": applied_ref,
        })
        _write(path, data)


def abandon_preflight(workspace_root: str, token: str) -> None:
    """Delete the staging file. Idempotent — abandoning an already-gone token is a
    no-op, never an error (a caller cleaning up after itself must not have to first
    check whether the file still exists)."""
    path = preflight_path(workspace_root, token)
    with plan_lib.file_lock(path):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
