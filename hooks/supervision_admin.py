"""Supervision declaration writer — locked, validated, fail-LOUD (#943 Part A).

The WRITE half of the supervision core. The read half is `supervision_lib.py`, which
imports the standard library ONLY because it rides a hook that runs on every tool call;
this module is free to import `plan_lib` and `atomic_write_lib`, and that asymmetry is
the whole reason the two are separate files.

Fail mode is the OPPOSITE of the read path's, deliberately. A read that quietly degrades
is right: it must never wedge a per-tool-call hook. A WRITE that quietly fails is wrong:
it would leave a session believing the owner is recorded as away when nothing landed —
exactly the false belief the state file exists to prevent. So every refusal raises, the
CLI turns it into rc 1 with a message, and a refused declaration writes nothing at all.

Concurrency: the lock is held across the WHOLE read-validate-increment-write cycle (the
`launcher_lib._locked_state_update` pattern), so two sessions cannot both read revision N
and both write N+1. `expected_revision` adds the optimistic-concurrency fence on top: a
write computed against a stale read aborts instead of clobbering a fresher declaration,
which is what stops a late owner reply from clearing an absence declared after it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

from atomic_write_lib import atomic_write_text

import plan_lib
import supervision_lib as sl

SCHEMA_VERSION = 1


class DeclarationRefused(Exception):
    """The declaration was rejected; nothing was written."""


class RevisionMismatch(DeclarationRefused):
    """The state moved under the caller; nothing was written."""


def _now(now):
    return now or datetime.now(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_workspace(workspace_root):
    if not workspace_root or not os.path.isdir(workspace_root):
        raise DeclarationRefused(
            f"workspace root is not a directory: {workspace_root!r}")


def _current(workspace_root):
    """(record, revision) for the state on disk, under the caller's lock.

    A corrupt file is recoverable — it is replaced, loudly — because while the state is
    invalid `installs_forbidden` is True, so refusing to write would leave the workspace
    stuck refusing installs with no way back. An unresolvable ROOT is a different thing
    and was already refused by `_require_workspace`.
    """
    loaded = sl.read_state(workspace_root)
    if loaded.load_status == "valid":
        return loaded.record, int(loaded.record.get("revision", 0))
    if loaded.load_status == "invalid":
        print("supervision: replacing an unreadable supervision state file",
              file=sys.stderr)
    return {}, 0


def _check_fence(expected_revision, found):
    if expected_revision is None:
        return
    if int(expected_revision) != found:
        raise RevisionMismatch(
            f"expected revision {expected_revision}, found {found} — the supervision "
            "state moved; re-read it and decide again rather than overwriting")


def _persist(workspace_root, record):
    path = sl.supervision_path(workspace_root)
    # fsync: the file's entire purpose is to outlive the session that wrote it,
    # including a host that dies mid-run.
    atomic_write_text(path, json.dumps(record, indent=2, sort_keys=True) + "\n",
                      prefix=".supervision.", mkdir=True, fsync=True)
    return record


def declare(workspace_root, *, state, until, session_id, campaign_ids,
            consult_providers, consult_granted, expected_revision=None, now=None):
    """Declare a supervision state. Raises `DeclarationRefused` on any refusal."""
    _require_workspace(workspace_root)
    stamp = _now(now)

    ok, err = sl.validate_declaration(state, until, stamp)
    if not ok:
        raise DeclarationRefused(err)

    campaign_ids = list(campaign_ids or [])
    bad = [c for c in campaign_ids if not sl.validate_campaign_id(c)]
    if bad:
        raise DeclarationRefused(
            f"unsafe campaign id(s) {bad}: must match [A-Za-z0-9._-]+ and carry no "
            "path separators")

    ok, err = sl.validate_providers(consult_providers)
    if not ok:
        raise DeclarationRefused(err)

    path = sl.supervision_path(workspace_root)
    with plan_lib.file_lock(path):
        _, found = _current(workspace_root)
        _check_fence(expected_revision, found)
        record = {
            "schema_version": SCHEMA_VERSION,
            "revision": found + 1,
            "state": state,
            "until": until,
            "declared_at": _iso(stamp),
            "declared_by_session": session_id,
            "governed_campaign_ids": campaign_ids,
            "consult_grant": {
                "providers": list(consult_providers or []),
                "granted": bool(consult_granted),
            },
        }
        return _persist(workspace_root, record)


def mark_attended(workspace_root, *, session_id, reason, expected_revision, now=None):
    """Record that the owner is watching again — the mechanism behind "a message from
    the owner always means attended again".

    `expected_revision` is REQUIRED here rather than optional, because this call is
    typically triggered by an ASYNCHRONOUS event (a reply that arrived minutes later),
    and an unfenced write would let a stale reply clear an absence declared after it.
    """
    _require_workspace(workspace_root)
    stamp = _now(now)
    path = sl.supervision_path(workspace_root)
    with plan_lib.file_lock(path):
        current, found = _current(workspace_root)
        _check_fence(expected_revision, found)
        record = {
            "schema_version": SCHEMA_VERSION,
            "revision": found + 1,
            "state": "attended",
            "until": None,
            "declared_at": _iso(stamp),
            "declared_by_session": session_id,
            "governed_campaign_ids": list(current.get("governed_campaign_ids") or []),
            # The grant dies with the absence it was given for.
            "consult_grant": {"providers": [], "granted": False},
            "attended_reason": reason,
        }
        return _persist(workspace_root, record)


# --------------------------------------------------------------------------- CLI


def _emit(record, human):
    # stdout is machine-consumable, stderr is the operator-visible line — the
    # task_class_lib.py convention.
    print(json.dumps(record, sort_keys=True))
    print(f"supervision: {human}", file=sys.stderr)
    return 0


def _cmd_declare(args) -> int:
    record = declare(
        args.workspace,
        state=args.state,
        until=args.until,
        session_id=args.session_id,
        campaign_ids=args.campaign,
        consult_providers=args.provider,
        consult_granted=args.granted,
        expected_revision=args.expected_revision,
    )
    until = record["until"] or "no stated return time"
    return _emit(record, f"declared {record['state']} ({until}) "
                         f"at revision {record['revision']}")


def _cmd_mark_attended(args) -> int:
    record = mark_attended(
        args.workspace,
        session_id=args.session_id,
        reason=args.reason,
        expected_revision=args.expected_revision,
    )
    return _emit(record, f"attended again at revision {record['revision']} "
                         f"({args.reason})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="supervision_admin",
        description="Write the declared supervision state (#943).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dec = sub.add_parser("declare", help="declare attended/away/sleeping")
    p_dec.add_argument("--workspace", required=True)
    p_dec.add_argument("--state", required=True, choices=list(sl.STATES))
    p_dec.add_argument("--until", default=None,
                       help="ISO-8601 UTC return/wake time; required for sleeping")
    p_dec.add_argument("--session-id", required=True, dest="session_id")
    p_dec.add_argument("--campaign", action="append", default=[],
                       help="campaign this declaration governs (repeatable; "
                            "none given = governs every campaign)")
    p_dec.add_argument("--provider", action="append", default=[],
                       help=f"consult provider to permit (repeatable; "
                            f"one of {list(sl.PROVIDERS)})")
    p_dec.add_argument("--granted", action="store_true",
                       help="grant consult egress to the named providers")
    p_dec.add_argument("--expected-revision", type=int, default=None,
                       dest="expected_revision",
                       help="optimistic-concurrency fence; abort if the on-disk "
                            "revision differs")
    p_dec.set_defaults(fn=_cmd_declare)

    p_att = sub.add_parser("mark-attended", help="record that the owner is back")
    p_att.add_argument("--workspace", required=True)
    p_att.add_argument("--session-id", required=True, dest="session_id")
    p_att.add_argument("--reason", required=True)
    p_att.add_argument("--expected-revision", type=int, required=True,
                       dest="expected_revision")
    p_att.set_defaults(fn=_cmd_mark_attended)

    args = parser.parse_args(argv)   # argparse usage errors exit 2
    try:
        return args.fn(args)
    except DeclarationRefused as exc:
        # The ONE failure surface: loud on stderr, rc 1, nothing written.
        print(f"supervision: refused — {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"supervision: write failed — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
