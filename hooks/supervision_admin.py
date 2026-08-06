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
import re
import sys
import time
from datetime import datetime, timedelta, timezone

from atomic_write_lib import atomic_write_text

import plan_lib
import supervision_lib as sl
import supervision_preflight as spf

SCHEMA_VERSION = 1

#: #947 Part B §3 finding 8 — the consumed-token ledger lives INSIDE .supervision.json
#: itself (an array field, carried forward like every other additive field), capped
#: defensively at the most recent entries. Departures are per-session events, not
#: per-tool-call, so this bound is generous relative to any operationally plausible
#: retry window (owner decision D267: accepted as documented risk, not tightened).
_MAX_CONSUMED_PREFLIGHT_TOKENS = 500

#: #947 Part B §5 — a transport-verify evidence file must be this fresh to be trusted.
_TRANSPORT_VERIFY_EVIDENCE_WINDOW_MINUTES = 10

#: The evidence file's own `token` field is used as a filesystem path component
#: (`<hermes_state_dir>/asks/<token>.json`) but is READ FROM A CALLER-SUPPLIED FILE, not
#: minted here — same charset as `hermes_bridge.mint_token()`'s real output ("RG-482913")
#: but validated defensively rather than trusted, so a crafted evidence file cannot
#: path-traverse to an arbitrary "ask record" outside asks/ (found in this task's own
#: self-review; mirrors `supervision_lib._CAMPAIGN_ID_RE`'s own path-safety convention).
_TRANSPORT_VERIFY_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]+$")


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

    **A recovery JUMPS the revision counter to wall-clock seconds.** Restarting it at 0
    would make the next write revision 1 again, and a delayed event still carrying
    `expected_revision=1` from the PREVIOUS lineage would then satisfy the fence and clear
    a newer absence — the exact stale-event hole the fence exists to close (pre-PR review
    finding). An unreadable record cannot tell us its own revision, so the counter is
    advanced past any plausible prior value instead of reset.
    """
    loaded = sl.read_state(workspace_root)
    if loaded.load_status == "valid":
        return loaded.record, int(loaded.record.get("revision", 0))
    if loaded.load_status == "invalid":
        print("supervision: replacing an unreadable supervision state file; the revision "
              "counter jumps forward so no stale fence from the previous lineage can "
              "match", file=sys.stderr)
        return {}, int(time.time())
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


def _carry_forward_additive_fields(record: dict, current: dict) -> None:
    """Copy every additive top-level field FORWARD from `current` into `record`, in
    place. Step 8a cross-model review, Critical finding 1: `declare` and `mark_attended`
    each build a brand-new record dict, so any additive field not explicitly re-listed
    silently vanishes on the very next write -- this defeated
    `mark_transport_verified`'s whole purpose (verify while attended, then declare away
    -- if `declare` drops the verification, `route_for` never sees it) and would have
    reset the consumed-preflight-token ledger across a `mark_attended` call, reopening
    the exact round-2 finding 3 replay hole one level up. Callers apply their OWN
    field-specific logic (e.g. `declare`'s preflight fold-in) AFTER this call, so this
    only sets a field already absent from `record`."""
    for field in ("transport_verification", "preflight_results", "consumed_preflight_tokens"):
        if field in current and field not in record:
            record[field] = current[field]


def declare(workspace_root, *, state, until, session_id, campaign_ids,
            consult_providers, consult_granted, expected_revision=None, now=None,
            preflight_token=None):
    """Declare an ABSENCE (`away` or `sleeping`). Raises `DeclarationRefused` on refusal.

    Deliberately CANNOT declare `attended`. That transition belongs to `mark_attended`,
    which makes `expected_revision` mandatory — routing it through here, where the fence is
    optional, was a way to clear a newer absence unfenced and re-enable unattended installs,
    contradicting the documented "only /rawgentic:back lifts it" (pre-PR review finding,
    raised independently by both review waves).

    `preflight_token` (#947 Part B §3, additive, default `None`): folds a departure
    preflight's staged answers into this declaration. Idempotent replay via a
    token-consumed check, NOT "rollback is don't-delete" — if `preflight_token` is
    already in the CURRENT record's `consumed_preflight_tokens`, this is a retry of an
    already-successful declaration: `declare` returns the CURRENT persisted record
    UNCHANGED, writing nothing (no new revision, no re-fold, no re-ask), regardless of
    how many OTHER declarations happened in between. Only when the token is unconsumed
    does `declare` proceed to read the staging file, stamp every answer with THIS
    write's own new revision, fold them into `preflight_results`, and append the token
    to `consumed_preflight_tokens` (capped at the most recent 500) — all in the SAME
    atomic write that lands the declaration itself. Staging-file deletion afterward is
    pure garbage collection: best-effort, and never affects the declaration's own
    correctness either way.
    """
    _require_workspace(workspace_root)
    stamp = _now(now)

    if state == "attended":
        raise DeclarationRefused(
            "declare cannot set `attended` — use mark_attended (or /rawgentic:back), "
            "which requires --expected-revision so a stale event cannot clear a newer "
            "absence")

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
        current, found = _current(workspace_root)
        consumed = list(current.get("consumed_preflight_tokens") or [])
        if preflight_token is not None and preflight_token in consumed:
            return dict(current)

        _check_fence(expected_revision, found)
        new_revision = found + 1
        record = {
            "schema_version": SCHEMA_VERSION,
            "revision": new_revision,
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
        # Additive top-level fields (finding 8): carry the CURRENT record's own values
        # forward on every write, same convention as every other additive field.
        preflight_results = list(current.get("preflight_results") or [])
        if preflight_token is not None:
            try:
                staged = spf.read_preflight(workspace_root, preflight_token)
            except spf.PreflightError as exc:
                raise DeclarationRefused(
                    f"preflight_token {preflight_token!r} could not be read: {exc}") from exc
            for answer in staged["answers"]:
                preflight_results.append(dict(answer, supervision_revision=new_revision))
            consumed = (consumed + [preflight_token])[-_MAX_CONSUMED_PREFLIGHT_TOKENS:]
        if preflight_results:
            record["preflight_results"] = preflight_results
        if consumed:
            record["consumed_preflight_tokens"] = consumed
        _carry_forward_additive_fields(record, current)

        persisted = _persist(workspace_root, record)

    if preflight_token is not None:
        try:
            spf.abandon_preflight(workspace_root, preflight_token)
        except OSError:
            pass  # pure garbage collection — the declaration is already correct either way
    return persisted


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
        _carry_forward_additive_fields(record, current)
        return _persist(workspace_root, record)


def _read_json_file(path, *, what):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        raise DeclarationRefused(f"cannot read {what} at {path!r}: {exc}") from exc


def mark_transport_verified(workspace_root, *, evidence_path, session_id,
                            hermes_state_dir, expected_revision=None, now=None):
    """Record that the transport (Hermes/BlueBubbles) round-trip is verified for THIS
    session (#947 Part B §5, AC3). Requires REAL evidence of a delivered round-trip,
    reusing `hermes_bridge`'s own single-use token as the freshness/binding mechanism
    rather than inventing a parallel nonce protocol. Refuses (raises
    `DeclarationRefused`, writes nothing) unless ALL of:

    1. the evidence file's `run_id` matches `supervision-transport-verify-<session_id>`
       for THIS session;
    2. `dateCreated` is within a bounded recent window
       (`_TRANSPORT_VERIFY_EVIDENCE_WINDOW_MINUTES`) of `now` — closes replay of an
       old, otherwise-valid delivered file;
    3. the CURRENT supervision state is `attended` — this can never be self-granted by
       an unsupervised session;
    4. `hermes_bridge`'s OWN ask-record (`<hermes_state_dir>/asks/<token>.json`) shows
       `status: "answered"` with a matching `answered_guid` — proving this evidence is
       the actual product of a real, closed Hermes delivery, not a same-shaped file
       placed by hand.
    """
    _require_workspace(workspace_root)
    stamp = _now(now)

    evidence = _read_json_file(evidence_path, what="transport-verify evidence")
    expected_run_id = f"supervision-transport-verify-{session_id}"
    if evidence.get("run_id") != expected_run_id:
        raise DeclarationRefused(
            f"evidence run_id {evidence.get('run_id')!r} does not match the expected "
            f"{expected_run_id!r} for this session")

    date_created_ms = evidence.get("dateCreated")
    if not isinstance(date_created_ms, (int, float)):
        raise DeclarationRefused("evidence carries no numeric dateCreated")
    age = stamp - datetime.fromtimestamp(date_created_ms / 1000.0, tz=timezone.utc)
    if age < timedelta(0) or age > timedelta(minutes=_TRANSPORT_VERIFY_EVIDENCE_WINDOW_MINUTES):
        raise DeclarationRefused(
            f"evidence is {age} old — must be within "
            f"{_TRANSPORT_VERIFY_EVIDENCE_WINDOW_MINUTES} minutes and not future-dated")

    # The EFFECTIVE state (Part A's own evaluate_workspace), not a raw dict lookup —
    # an absent or invalid file reads as "attended" (Part A's established fail-open-
    # for-availability convention), so a never-yet-declared workspace is correctly
    # treated as attended rather than spuriously refused.
    view = sl.evaluate_workspace(sl.read_state(workspace_root), now=stamp)
    if view.state != "attended":
        raise DeclarationRefused(
            "mark_transport_verified requires the CURRENT state to be 'attended' — "
            "it cannot be self-granted by an unsupervised session")

    token = evidence.get("token")
    if not isinstance(token, str) or not _TRANSPORT_VERIFY_TOKEN_RE.match(token):
        raise DeclarationRefused(
            f"evidence token is not a safe path component: {token!r}")
    ask_path = os.path.join(hermes_state_dir, "asks", f"{token}.json")
    ask_record = _read_json_file(ask_path, what="hermes ask-record")
    # Step 8a cross-model review, High finding 7: checking status/answered_guid alone
    # proves SOME real delivery happened, but not that it was for THIS purpose -- an
    # evidence file can self-report the expected run_id (always attacker-settable) while
    # pointing (via token+guid, both readable local files) at a real, unrelated,
    # already-answered ask. The ask record's OWN run_id must independently agree.
    if ask_record.get("run_id") != expected_run_id:
        raise DeclarationRefused(
            f"hermes ask-record for token {token!r} has run_id "
            f"{ask_record.get('run_id')!r}, not the expected {expected_run_id!r} — "
            "this is not the product of a real delivery for THIS purpose")
    if ask_record.get("status") != "answered":
        raise DeclarationRefused(
            f"hermes ask-record for token {token!r} is not 'answered' "
            f"({ask_record.get('status')!r})")
    if ask_record.get("answered_guid") != evidence.get("guid"):
        raise DeclarationRefused(
            "hermes ask-record's answered_guid does not match the evidence file's "
            "guid — this evidence is not the product of the real delivery")

    path = sl.supervision_path(workspace_root)
    with plan_lib.file_lock(path):
        # Re-check "attended" HERE, under the lock, immediately before writing — the
        # check above is a fast fail only. Without this, another session's
        # declare(away/sleeping) landing in the TOCTOU window between that check and
        # this write would let a transport_verification get stamped onto a record
        # that is no longer attended (found this in this task's own self-review).
        if sl.evaluate_workspace(sl.read_state(workspace_root), now=stamp).state != "attended":
            raise DeclarationRefused(
                "mark_transport_verified requires the CURRENT state to be 'attended' — "
                "the state changed since this call started")
        current, found = _current(workspace_root)
        _check_fence(expected_revision, found)
        record = dict(current)
        record["schema_version"] = SCHEMA_VERSION
        record["revision"] = found + 1
        record["transport_verification"] = {
            "verified_at": _iso(stamp),
            "verified_session_id": session_id,
            "evidence_token": token,
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
        preflight_token=args.preflight_token,
    )
    until = record["until"] or "no stated return time"
    return _emit(record, f"declared {record['state']} ({until}) "
                         f"at revision {record['revision']}")


def _cmd_mark_transport_verified(args) -> int:
    record = mark_transport_verified(
        args.workspace,
        evidence_path=args.evidence,
        session_id=args.session_id,
        hermes_state_dir=args.hermes_state_dir,
        expected_revision=args.expected_revision,
    )
    return _emit(record, f"transport verified for session {args.session_id} "
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
    # `attended` is deliberately NOT offered here — mark-attended owns that
    # transition because its revision fence is mandatory.
    p_dec.add_argument("--state", required=True,
                       choices=[s for s in sl.STATES if s != "attended"])
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
    p_dec.add_argument("--preflight-token", default=None, dest="preflight_token",
                       help="fold a departure preflight's staged answers into this "
                            "declaration (#947 Part B §3)")
    p_dec.set_defaults(fn=_cmd_declare)

    p_att = sub.add_parser("mark-attended", help="record that the owner is back")
    p_att.add_argument("--workspace", required=True)
    p_att.add_argument("--session-id", required=True, dest="session_id")
    p_att.add_argument("--reason", required=True)
    p_att.add_argument("--expected-revision", type=int, required=True,
                       dest="expected_revision")
    p_att.set_defaults(fn=_cmd_mark_attended)

    p_tv = sub.add_parser("mark-transport-verified",
                          help="record a verified Hermes/BlueBubbles round-trip (#947 Part B §5)")
    p_tv.add_argument("--workspace", required=True)
    p_tv.add_argument("--evidence", required=True,
                      help="path to the delivered evidence file (hermes_bridge.py poll's "
                           "printed path on a matched disposition)")
    p_tv.add_argument("--session-id", required=True, dest="session_id")
    p_tv.add_argument("--hermes-state-dir", required=True, dest="hermes_state_dir")
    p_tv.add_argument("--expected-revision", type=int, default=None,
                      dest="expected_revision")
    p_tv.set_defaults(fn=_cmd_mark_transport_verified)

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
