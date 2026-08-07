"""Supervision state — who is watching this session (#943 Part A).

The READ + PURE half of the supervision core. Writes live in `supervision_admin.py`.

**This module imports the standard library ONLY, and a test enforces it.** The split
exists for one measured reason: `hooks/context_meter.py` consumes this on the emit path
of a hook that runs on every tool call, and it currently imports stdlib only. The write
half needs `plan_lib.file_lock`, and `plan_lib` is large — importing it to answer "is
anyone watching" would tax every tool call. Keep the two halves apart.

Fail modes are deliberately different in the two directions, because the two consumers
authorize different things:

- READ is fail-OPEN for AVAILABILITY: a broken state file must never wedge a
  per-tool-call hook, so nothing here raises.
- READ is fail-SAFE for AUTHORITY: a broken state file must never *unlock* an outward
  action. Hence `load_status`, and hence `installs_forbidden` returning True on
  `invalid`.

The two predicates fail safe in OPPOSITE directions, which is why there are two of them
rather than one `is_watched` boolean:

- `nobody_to_ask` only changes the wording of advice, so an EXPIRED declaration relaxes
  it — past a stated wake time, assume the human is back.
- `installs_forbidden` authorizes a real, outward, not-trivially-undone action
  (installing packages), so an expired declaration keeps refusing. A clock passing a
  timestamp is not evidence anybody came back; only an explicit `/rawgentic:back` is.

Absence vs invalidity is the other load-bearing line. `ENOENT` under a valid root is the
only *file* failure treated as absence. A supplied-but-unresolvable root is INVALID, not
absent — otherwise a path-resolution or caller-configuration bug would ALLOW installs
while the real workspace held an active away declaration, inverting the fail-safe
property via a config error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from collections import namedtuple
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

#: Declared states. `attended-overdue` is EFFECTIVE-only — never written to the file.
STATES = ("attended", "away", "sleeping")
ATTENDED_OVERDUE = "attended-overdue"
ABSENCES = ("away", "sleeping")

#: Consult providers, mirroring `review_runner.py`'s `--backend` vocabulary. Validated at
#: declaration time rather than at send time, so a typo surfaces while the owner is here.
PROVIDERS = ("gpt", "glm")

#: The state file is a handful of scalars; anything larger is not our file.
READ_CAP_BYTES = 64 * 1024

#: Campaign ids are used as `claude_docs/.driver-state/<id>.json` basenames by Part B
#: (#947), so an unvalidated value would be a path-traversal write primitive.
_CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_CAMPAIGN_ID = 128

_REL_PATH = ("claude_docs", ".supervision.json")

#: The durable declaration marker (#963 AC2). It sits BESIDE the state file and answers
#: the one question the state file cannot answer once it is gone: "was a declaration
#: live?". Without it, deleting `.supervision.json` mid-absence read as `absent` ->
#: `attended` -> `authority_permits` True for every action kind (#947 Step 11 findings
#: 1/4/7). It is deliberately NOT an "ever declared" tombstone: `mark_attended` clears it
#: (`declared: false`), because deleting an ATTENDED record widens nothing and denying
#: there would be a pure fail-closed outage.
_MARKER_REL_PATH = ("claude_docs", ".supervision.declared.json")

#: (record, load_status) where load_status is "absent" | "valid" | "invalid".
Loaded = namedtuple("Loaded", "record load_status")


@dataclass(frozen=True)
class SupervisionView:
    """The evaluated answer to "is the human watching, at all?".

    `state` is EFFECTIVE (expiry applied); `declared` is what the file said, or
    "invalid". Both are needed: routing reads `state`, while the install guard reads
    `declared` so that expiry cannot unlock it. `revision`/`declared_at` ride along so a
    later owner-return report can name the absence window a decision was taken in, even
    after the state has been reset to attended.
    """

    state: str
    declared: str
    until: "str | None"
    expired: bool
    revision: int
    declared_at: "str | None"
    load_status: str
    consult_providers: tuple
    granted: bool
    transport_verified: bool
    transport_verified_at: "str | None"
    transport_verified_session_id: "str | None"


def _warn(msg: str) -> None:
    try:
        print(f"supervision: {msg}", file=sys.stderr)
    except Exception:  # pylint: disable=broad-except
        pass


def _parse_ts(value):
    """Parse an ISO-8601 UTC timestamp, or return None. Never raises."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def supervision_path(workspace_root: str) -> str:
    """The state file's path under a workspace root. Never taken from user input."""
    return os.path.join(workspace_root, *_REL_PATH)


def declared_marker_path(workspace_root: str) -> str:
    """The declaration marker's path — a sibling of the state file (#963)."""
    return os.path.join(workspace_root, *_MARKER_REL_PATH)


#: Every key the writer emits. A record missing any of them is INVALID rather than
#: partially-honoured: a schema-incomplete file must fail SAFE (invalid forbids installs),
#: never read as a permissive `attended` (pre-PR review finding). `until` may be null but
#: the KEY must be present, so "no return time" is distinguishable from "field lost".
_REQUIRED_KEYS = (
    "schema_version", "revision", "state", "until",
    "declared_at", "declared_by_session",
)


def _record_is_sane(record) -> bool:
    if not isinstance(record, dict):
        return False
    if any(key not in record for key in _REQUIRED_KEYS):
        return False
    if record.get("state") not in STATES:
        return False
    until = record.get("until")
    if until is not None and _parse_ts(until) is None:
        return False
    for key in ("schema_version", "revision"):
        value = record.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return False
    for key in ("declared_at", "declared_by_session"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            return False
    grant = record.get("consult_grant", {})
    if grant is not None and not isinstance(grant, dict):
        return False
    return True


def _read_json_capped(path: str, what: str):
    """`(data, status)` with status "absent" | "present" | "invalid". NEVER raises.

    Shared by the state file and the declaration marker (#963) — both are small JSON
    files read on a per-tool-call path, and every failure mode below was a real review
    finding on the state read. Only `ENOENT` at `stat` time is an absence; everything
    else is invalid, so a corrupt or racing file can never read as "nobody declared".
    """
    # A DANGLING symlink is not an absence — something was put there deliberately and now
    # does not resolve. `os.stat` follows the link and would report FileNotFoundError,
    # which would read as "nobody declared anything" and permit installs (pre-PR review
    # finding). `lstat` sees the link itself.
    try:
        if os.path.islink(path) and not os.path.exists(path):
            _warn(f"{what} is a dangling symlink; treating as invalid")
            return None, "invalid"
    except OSError:
        return None, "invalid"

    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None, "absent"
    except OSError as exc:
        _warn(f"{what} unreadable ({exc}); treating as invalid")
        return None, "invalid"

    # Refuse non-regular files BEFORE opening. A FIFO here would block `open` and hang a
    # hook that runs on every tool call; a device or directory would misbehave in its own
    # way. `context_meter._read_capped` refuses non-regular files for the same reason.
    if not stat.S_ISREG(st.st_mode):
        _warn(f"{what} is not a regular file; treating as invalid")
        return None, "invalid"
    if st.st_size > READ_CAP_BYTES:
        _warn(f"{what} exceeds {READ_CAP_BYTES} bytes; treating as invalid")
        return None, "invalid"

    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read(READ_CAP_BYTES + 1)
    except FileNotFoundError:
        # `stat` succeeded a moment ago, so the file EXISTED and then vanished. That is a
        # race, not an absence: reporting absent here would let a delete-during-read
        # permit installs while a declaration was in force (pre-PR review finding).
        _warn(f"{what} vanished while being read; treating as invalid")
        return None, "invalid"
    # ValueError catches UnicodeDecodeError, which is NOT an OSError: invalid UTF-8 in
    # the file would otherwise raise straight out of a hook that runs on every tool call
    # and break the never-raises contract (pre-PR review finding, confirmed live).
    except (OSError, ValueError) as exc:
        _warn(f"{what} unreadable ({exc}); treating as invalid")
        return None, "invalid"

    # The size check above and this read are not atomic, so re-check what actually
    # arrived: a file that grew past the cap in between must not slip through just
    # because it still happens to parse.
    if len(raw) > READ_CAP_BYTES:
        _warn(f"{what} exceeds {READ_CAP_BYTES} bytes; treating as invalid")
        return None, "invalid"

    try:
        return json.loads(raw), "present"
    except ValueError as exc:
        _warn(f"{what} is not valid JSON ({exc}); treating as invalid")
        return None, "invalid"


def _marker_is_sane(marker) -> bool:
    if not isinstance(marker, dict):
        return False
    if not isinstance(marker.get("schema_version"), int) or isinstance(
            marker.get("schema_version"), bool):
        return False
    if not isinstance(marker.get("declared"), bool):
        return False
    revision = marker.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        return False
    return True


def read_marker(workspace_root: str):
    """`(marker, status)` with status "absent" | "valid" | "invalid" (#963). Never raises."""
    marker, status = _read_json_capped(
        declared_marker_path(workspace_root), "declaration marker")
    if status != "present":
        return {}, status
    if not _marker_is_sane(marker):
        _warn("declaration marker does not match its schema; treating as invalid")
        return {}, "invalid"
    return marker, "valid"


def _apply_declaration_marker(workspace_root: str, loaded: Loaded) -> Loaded:
    """Reconcile the state record against the durable declaration marker (#963 AC2).

    The marker answers what the state file cannot once it is gone or torn: was a
    declaration LIVE? Every inconsistency maps to the existing `invalid` status rather
    than to a fourth value, because `authority_permits`, `installs_forbidden`,
    `consult_permitted` and the claims revision fence ALREADY deny there — one branch
    here closes the hole at every consumer instead of N consumer cases that each risk
    failing open.
    """
    marker, status = read_marker(workspace_root)
    if status == "absent":
        # No marker: a workspace that never declared, or one predating #963. Today's
        # semantics exactly — this is the back-compat path.
        return loaded
    if status == "invalid":
        # A security boundary that cannot evaluate fails CLOSED (repo convention).
        return Loaded({}, "invalid")
    if not marker.get("declared"):
        # Positively cleared by `mark_attended`. Deleting an ATTENDED record widens
        # nothing, so the state file governs.
        return loaded

    # A declaration is live; the state record must corroborate it.
    if loaded.load_status != "valid":
        _warn("a declaration is live but the state file is missing or invalid "
              "(declared-then-deleted); treating as invalid")
        return Loaded({}, "invalid")
    if loaded.record.get("state") == "attended":
        # `declare` writes the marker first, so an attended record under a live marker
        # means the state replacement never landed.
        _warn("a declaration is live but the state file still reads attended "
              "(interrupted declare); treating as invalid")
        return Loaded({}, "invalid")
    if loaded.record.get("revision", 0) < marker.get("revision", 0):
        _warn("the state file is older than the declaration marker (torn write); "
              "treating as invalid")
        return Loaded({}, "invalid")
    return loaded


def read_state(workspace_root) -> Loaded:
    """Read the state file, reconciled against the declaration marker. NEVER raises.

    Never searches for the workspace root: the caller supplies it because both consumers
    already have it (`context_meter` from `find_workspace(cwd)`, `scanner_bootstrap` from
    `--workspace`). Searching again here would pay for it twice.
    """
    if not workspace_root:
        # Genuinely no rawgentic workspace context — today's unset-env default. No
        # workspace means no marker to consult either.
        return Loaded({}, "absent")
    if not isinstance(workspace_root, str):
        # `os.path.isdir([])` raises TypeError, which would escape the never-raises
        # contract on a per-tool-call hook (pre-PR review finding).
        _warn(f"workspace root is not a path string: {type(workspace_root).__name__}")
        return Loaded({}, "invalid")
    if not os.path.isdir(workspace_root):
        # Supplied but unresolvable: a caller/config failure, NOT an absence. Treating
        # it as absence would let a config bug unlock installs.
        _warn(f"workspace root is not a directory: {workspace_root!r}")
        return Loaded({}, "invalid")

    record, status = _read_json_capped(supervision_path(workspace_root), "state file")
    if status == "present" and not _record_is_sane(record):
        _warn("state file does not match the supervision schema; treating as invalid")
        status = "invalid"
    loaded = Loaded(record, "valid") if status == "present" else Loaded({}, status)
    return _apply_declaration_marker(workspace_root, loaded)


#: transport_verification.verified_at is trusted for at most this long (#947 Part B §5).
_TRANSPORT_VERIFY_WINDOW_HOURS = 24


def _compute_transport_verified(record: dict, *, now: datetime, session_id):
    """(verified, verified_at_raw, verified_session_id_raw) for #947 Part B §4a.

    `verified` is True only when ALL hold: the record carries a well-formed
    `transport_verification` object, `verified_at` parses, is not future-dated, is
    within `_TRANSPORT_VERIFY_WINDOW_HOURS` of `now`, AND `verified_session_id` exactly
    equals the CALLER-supplied `session_id` (Step-6 finding 5 / owner decision D267 —
    timestamp freshness alone let a verification from an OLDER session stay trusted in
    a brand-new one; absence of a caller session_id must never read as a match).
    The two raw values are returned regardless of `verified`, mirroring `declared_at`'s
    own convention: observability survives even when the effective answer is False.
    """
    tv = record.get("transport_verification")
    if not isinstance(tv, dict):
        return False, None, None
    verified_at_raw = tv.get("verified_at")
    verified_session_id_raw = tv.get("verified_session_id")
    if not isinstance(verified_session_id_raw, str) or not verified_session_id_raw:
        verified_session_id_raw = None
    if not isinstance(verified_at_raw, str):
        return False, None, verified_session_id_raw
    verified_at = _parse_ts(verified_at_raw)
    if verified_at is None:
        return False, None, verified_session_id_raw
    if verified_at > now:
        return False, verified_at_raw, verified_session_id_raw
    if now - verified_at > timedelta(hours=_TRANSPORT_VERIFY_WINDOW_HOURS):
        return False, verified_at_raw, verified_session_id_raw
    if not session_id or verified_session_id_raw != session_id:
        return False, verified_at_raw, verified_session_id_raw
    return True, verified_at_raw, verified_session_id_raw


def evaluate_workspace(loaded: Loaded, *, now: datetime, session_id=None) -> SupervisionView:
    """Workspace-global evaluation: "is the human watching, at all?".

    Deliberately NOT narrowed by `governed_campaign_ids`. The consumers are two hooks
    that belong to no campaign, and narrowing an install guard by campaign is
    meaningless — an earlier design overloaded one evaluator with both this question and
    the campaign-scoped one, which would have shipped a declaration that silently did
    nothing at exactly the sites it exists for. The campaign-scoped evaluator is #947.

    `session_id` (#947 Part B, additive, default `None`) is the CURRENT session's id,
    used only to compute `transport_verified` — every pre-#947 caller omits it and gets
    byte-identical behavior for every other field, with `transport_verified` correctly
    False (no current session to match against).
    """
    if loaded.load_status != "valid":
        return SupervisionView(
            state="attended",
            declared="attended" if loaded.load_status == "absent" else "invalid",
            until=None,
            expired=False,
            revision=0,
            declared_at=None,
            load_status=loaded.load_status,
            consult_providers=(),
            granted=False,
            transport_verified=False,
            transport_verified_at=None,
            transport_verified_session_id=None,
        )

    record = loaded.record
    declared = record["state"]
    until = record.get("until")
    grant = record.get("consult_grant") or {}
    providers = grant.get("providers")
    providers = tuple(p for p in providers if isinstance(p, str)) \
        if isinstance(providers, list) else ()

    expired = False
    state = declared
    if declared in ABSENCES and until is not None:
        deadline = _parse_ts(until)
        # AT `until` the declaration is still live; only PAST it is overdue.
        if deadline is not None and now > deadline:
            expired = True
            state = ATTENDED_OVERDUE

    tv_verified, tv_at, tv_session = _compute_transport_verified(
        record, now=now, session_id=session_id)

    return SupervisionView(
        state=state,
        declared=declared,
        until=until,
        expired=expired,
        revision=record.get("revision", 0),
        declared_at=record.get("declared_at"),
        load_status="valid",
        consult_providers=providers,
        granted=bool(grant.get("granted")),
        transport_verified=tv_verified,
        transport_verified_at=tv_at,
        transport_verified_session_id=tv_session,
    )


def nobody_to_ask(view: SupervisionView) -> bool:
    """True when there is no human to put a question to.

    Reads the EFFECTIVE state, so an expired declaration relaxes this: past a stated
    wake time, assume the owner is back. Safe, because the only consequence is which
    advice a context-pressure nag prints.
    """
    return view.state in ABSENCES


def installs_forbidden(view: SupervisionView) -> bool:
    """True when an unattended package install must not happen.

    Reads the DECLARED state, so expiry does NOT unlock it — only an explicit
    `/rawgentic:back` does. Also True whenever the state file is present-but-invalid or
    the workspace root was unresolvable: a file we cannot parse is not permission.
    """
    return view.load_status == "invalid" or view.declared in ABSENCES


def validate_declaration(state, until, now: datetime):
    """(ok, error) for a proposed declaration. Pure; used before any write."""
    if state not in STATES:
        return False, f"state must be one of {list(STATES)}, got {state!r}"
    if state == "attended":
        if until is not None:
            return False, "attended must not carry an `until`"
        return True, ""
    if state == "sleeping" and until is None:
        return False, "sleeping requires an `until` (a wake time)"
    if until is not None:
        deadline = _parse_ts(until)
        if deadline is None:
            return False, f"`until` is not an ISO-8601 timestamp: {until!r}"
        if deadline <= now:
            return False, (
                f"`until` is in the past ({until!r}); that would declare an "
                "already-expired absence")
    return True, ""


def validate_campaign_id(value) -> bool:
    """Campaign ids become state-file basenames in #947 — keep them path-safe."""
    if not isinstance(value, str):
        return False
    if not value or len(value) > _MAX_CAMPAIGN_ID:
        return False
    # `.` and `..` match the charset but ARE path navigation. The charset alone let them
    # through (pre-PR review finding, confirmed live: `validate_campaign_id("..")` was
    # True), so reject any all-dots value explicitly.
    if set(value) == {"."}:
        return False
    return bool(_CAMPAIGN_ID_RE.match(value))


def validate_providers(values):
    """(ok, error) for a consult-egress provider list."""
    if values is None:
        return True, ""
    if not isinstance(values, (list, tuple)):
        return False, "providers must be a list"
    unknown = [v for v in values if v not in PROVIDERS]
    if unknown:
        return False, (
            f"unknown consult provider(s) {unknown}; known: {list(PROVIDERS)}")
    return True, ""


# --------------------------------------------------------------------------- CLI


def _load_view(args) -> SupervisionView:
    return evaluate_workspace(read_state(args.workspace),
                              now=datetime.now(timezone.utc),
                              session_id=getattr(args, "session_id", None))


def _cmd_installs_forbidden(args) -> int:
    # Exit 0 means FORBIDDEN, so a shell `if` reads naturally at the call site. A bare
    # state word would be ambiguous here: `attended-overdue` reads as attended but must
    # still forbid.
    return 0 if installs_forbidden(_load_view(args)) else 1


def _cmd_nobody_to_ask(args) -> int:
    return 0 if nobody_to_ask(_load_view(args)) else 1


def _cmd_effective(args) -> int:
    view = _load_view(args)
    print(json.dumps({
        "state": view.state,
        "declared": view.declared,
        "until": view.until,
        "expired": view.expired,
        "revision": view.revision,
        "declared_at": view.declared_at,
        "load_status": view.load_status,
        "consult_providers": list(view.consult_providers),
        "granted": view.granted,
        "transport_verified": view.transport_verified,
        "transport_verified_at": view.transport_verified_at,
        "transport_verified_session_id": view.transport_verified_session_id,
        "nobody_to_ask": nobody_to_ask(view),
        "installs_forbidden": installs_forbidden(view),
    }, sort_keys=True))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="supervision_lib",
        description="Read the declared supervision state (#943).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name, fn, helptext in (
            ("installs-forbidden", _cmd_installs_forbidden,
             "exit 0 = unattended installs FORBIDDEN, 1 = allowed"),
            ("nobody-to-ask", _cmd_nobody_to_ask,
             "exit 0 = nobody is watching, 1 = a human is present"),
            ("effective", _cmd_effective, "print the evaluated view as JSON"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--workspace", required=True,
                       help="absolute path of the directory holding "
                            ".rawgentic_workspace.json")
        if name == "effective":
            p.add_argument("--session-id", default=None,
                           help="current session id, used only to evaluate "
                                "transport_verified (#947 Part B); omit for the "
                                "pre-#947 behavior (transport_verified always False)")
        p.set_defaults(fn=fn)

    args = parser.parse_args(argv)   # argparse usage errors exit 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
