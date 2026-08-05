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
import sys
from collections import namedtuple
from dataclasses import dataclass
from datetime import datetime, timezone

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


def _record_is_sane(record) -> bool:
    if not isinstance(record, dict):
        return False
    if record.get("state") not in STATES:
        return False
    until = record.get("until")
    if until is not None and _parse_ts(until) is None:
        return False
    revision = record.get("revision", 0)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        return False
    grant = record.get("consult_grant", {})
    if grant is not None and not isinstance(grant, dict):
        return False
    return True


def read_state(workspace_root) -> Loaded:
    """Read the state file. NEVER raises, and never searches for the workspace root.

    The caller supplies the root because both consumers already have it:
    `context_meter` from `find_workspace(cwd)`, `scanner_bootstrap` from `--workspace`.
    Searching again here would pay for it twice.
    """
    if not workspace_root:
        # Genuinely no rawgentic workspace context — today's unset-env default.
        return Loaded({}, "absent")
    if not os.path.isdir(workspace_root):
        # Supplied but unresolvable: a caller/config failure, NOT an absence. Treating
        # it as absence would let a config bug unlock installs.
        _warn(f"workspace root is not a directory: {workspace_root!r}")
        return Loaded({}, "invalid")

    path = supervision_path(workspace_root)
    try:
        if os.path.getsize(path) > READ_CAP_BYTES:
            _warn(f"state file exceeds {READ_CAP_BYTES} bytes; treating as invalid")
            return Loaded({}, "invalid")
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read(READ_CAP_BYTES + 1)
    except FileNotFoundError:
        return Loaded({}, "absent")
    except OSError as exc:
        _warn(f"state file unreadable ({exc}); treating as invalid")
        return Loaded({}, "invalid")

    try:
        record = json.loads(raw)
    except ValueError as exc:
        _warn(f"state file is not valid JSON ({exc}); treating as invalid")
        return Loaded({}, "invalid")

    if not _record_is_sane(record):
        _warn("state file does not match the supervision schema; treating as invalid")
        return Loaded({}, "invalid")
    return Loaded(record, "valid")


def evaluate_workspace(loaded: Loaded, *, now: datetime) -> SupervisionView:
    """Workspace-global evaluation: "is the human watching, at all?".

    Deliberately NOT narrowed by `governed_campaign_ids`. The consumers are two hooks
    that belong to no campaign, and narrowing an install guard by campaign is
    meaningless — an earlier design overloaded one evaluator with both this question and
    the campaign-scoped one, which would have shipped a declaration that silently did
    nothing at exactly the sites it exists for. The campaign-scoped evaluator is #947.
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
                              now=datetime.now(timezone.utc))


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
        p.set_defaults(fn=fn)

    args = parser.parse_args(argv)   # argparse usage errors exit 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
