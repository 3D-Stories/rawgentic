"""Step-entry state record — a tiny observational "now" pointer (#480).

Written once at each workflow-step entry (WF1/WF2/WF3/WF5/epic-run expected,
but `workflow` is a free string — NOT enum-enforced, this is observational
telemetry, not a schema to defend). Consumed by the owner's statusline and
`hooks/wal-context` to show "what step is running now"; both read it via the
`read` subcommand so the staleness math lives in exactly one tested place
instead of being duplicated as jq date arithmetic at each consumer.

FAIL-OPEN, EVERYWHERE, ALWAYS: this is never a gate. Every runtime failure —
an unwritable state dir, an OSError on write/read, a non-int --issue, an
unsanitizable/empty --project, an unresolvable state dir — prints a one-line
stderr note (write only; read stays silent on stdout by contract) and returns
0. Nothing here ever blocks a caller or raises past main(). Contrast
`hooks/wal-guard`, which is fail-CLOSED (missing jq denies every command) —
that hook is a security boundary; this one is pure telemetry, the opposite
end of the fail-mode spectrum (repo convention, CLAUDE.md section 3). The ONE
exception is argparse's own error handling: a malformed invocation (missing
a required flag) is a caller bug, not a runtime condition, and argparse's
built-in `exit(2)` is acceptable there.

Pure core + thin CLI (`hooks/registry_prune.py` is the exemplar). Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from atomic_write_lib import atomic_write_text

SCHEMA_VERSION = 1
WORKSPACE_MARKER = ".rawgentic_workspace.json"
DEFAULT_MAX_AGE_MIN = 240

# Local reimplementation of the phase_executor/capture.py `sanitize_component`
# idiom (that module deliberately doesn't import `hooks/` — see its own
# docstring — so this is a conceptual port, not a shared import): neutralize
# every char outside the safe set (in particular `/` and `\\`, so no
# sanitized name can ever contain a path separator) rather than reject
# outright, then still reject anything that collapses to all-dots/empty.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_project(name) -> "str | None":
    """Return a filesystem-safe single path component for `name`, or None if
    it can't be made safe. None means: empty/whitespace-only input, or a
    result that collapses to all dots (".", "..", "...") after traversal/
    control chars are neutralized to "_" — mirrors capture.sanitize_component's
    rejection rule. Never raises."""
    s = str(name).strip()
    if not s:
        return None
    s = _UNSAFE.sub("_", s)
    if not s.strip("."):
        return None
    return s


def find_state_dir(cwd: str) -> "str | None":
    """Resolve the DEFAULT state dir (used when --state-dir is not given):
    walk up from `cwd` until a directory containing WORKSPACE_MARKER is
    found -> return `<that>/claude_docs/wal` (may not exist yet; the writer
    creates it). If no workspace file is found all the way to the filesystem
    root, fall back to `<cwd>/claude_docs/wal` ONLY if it already exists;
    otherwise return None (caller fails open: no write, stderr note)."""
    d = os.path.abspath(cwd)
    while True:
        if os.path.isfile(os.path.join(d, WORKSPACE_MARKER)):
            return os.path.join(d, "claude_docs", "wal")
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    fallback = os.path.join(os.path.abspath(cwd), "claude_docs", "wal")
    return fallback if os.path.isdir(fallback) else None


def build_record(project, workflow, step, step_title, session_id, issue,
                 now: datetime) -> dict:
    """Pure record builder. `now` must be tz-aware; `entered_at` renders as
    UTC ISO-8601 with second precision (e.g. "2026-07-18T11:22:33Z")."""
    return {
        "schema_version": SCHEMA_VERSION,
        "project": project,
        "workflow": workflow,
        "step": step,
        "step_title": step_title,
        "issue": issue,
        "session_id": session_id,
        "entered_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _parse_entered_at(value) -> "datetime | None":
    """Parse a record's `entered_at` into a tz-aware UTC datetime, or None if
    it isn't a parseable string (mirrors registry_prune.py's `_parse_started`
    idiom: naive timestamps are treated as UTC, never raises)."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _state_path(state_dir: str, project: str) -> str:
    return os.path.join(state_dir, f"{project}.state.json")


# --- CLI ---------------------------------------------------------------------

def cmd_write(args) -> int:
    """Write (overwrite-in-place) the state record for --project. Every
    failure path below fails open: stderr note, return 0, never a crash."""
    project = sanitize_project(args.project)
    if project is None:
        print(f"step_state write: --project {args.project!r} is empty or "
              "unsanitizable — no write (fail-open)", file=sys.stderr)
        return 0

    issue = None
    if args.issue is not None:
        try:
            issue = int(args.issue)
        except (TypeError, ValueError):
            print(f"step_state write: --issue {args.issue!r} is not an int "
                  "— recording issue=null (fail-open)", file=sys.stderr)

    state_dir = args.state_dir if args.state_dir else find_state_dir(os.getcwd())
    if not state_dir:
        print(f"step_state write: could not resolve a state dir (no "
              f"{WORKSPACE_MARKER} found above cwd, and no existing "
              "claude_docs/wal fallback) — no write (fail-open)", file=sys.stderr)
        return 0

    record = build_record(project, args.workflow, args.step, args.step_title,
                          args.session_id, issue, datetime.now(timezone.utc))
    path = _state_path(state_dir, project)
    try:
        atomic_write_text(path, json.dumps(record) + "\n",
                          prefix=".step_state.", mkdir=True)
    except OSError as exc:
        print(f"step_state write: could not write {path}: {exc} "
              "(fail-open)", file=sys.stderr)
    return 0


def cmd_read(args) -> int:
    """Print the state record's JSON to stdout iff the file exists, parses,
    and its entered_at is within --max-age-min minutes of now; otherwise
    print nothing. Always returns 0 — absence, staleness, and corruption are
    all the same "nothing to show" outcome for a consumer, not an error."""
    project = sanitize_project(args.project)
    if project is None:
        return 0

    state_dir = args.state_dir if args.state_dir else find_state_dir(os.getcwd())
    if not state_dir:
        return 0

    path = _state_path(state_dir, project)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        record = json.loads(text)
    except (OSError, ValueError):
        return 0

    if not isinstance(record, dict):
        return 0
    entered_at = _parse_entered_at(record.get("entered_at"))
    if entered_at is None:
        return 0
    if datetime.now(timezone.utc) - entered_at > timedelta(minutes=args.max_age_min):
        return 0

    print(text.rstrip("\n"))
    return 0


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="step_state",
        description="Step-entry state record — observational now-pointer (#480).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_write = sub.add_parser("write", help="record a step-entry now-pointer")
    p_write.add_argument("--project", required=True)
    p_write.add_argument("--workflow", required=True,
                         help="free string, e.g. wf1/wf2/wf3/wf5/epic-run (not enforced)")
    p_write.add_argument("--step", required=True,
                         help='free string, e.g. "8a" or "11.5"')
    p_write.add_argument("--step-title", required=True, dest="step_title")
    p_write.add_argument("--session-id", required=True, dest="session_id")
    p_write.add_argument("--issue", default=None,
                         help="int; a non-int value is recorded as null (fail-open)")
    p_write.add_argument("--state-dir", default=None, dest="state_dir")
    p_write.set_defaults(fn=cmd_write)

    p_read = sub.add_parser("read", help="print the now-pointer if it is fresh")
    p_read.add_argument("--project", required=True)
    p_read.add_argument("--state-dir", default=None, dest="state_dir")
    p_read.add_argument("--max-age-min", type=int, default=DEFAULT_MAX_AGE_MIN,
                        dest="max_age_min")
    p_read.set_defaults(fn=cmd_read)

    args = parser.parse_args(argv)  # argparse errors: exit(2) — the one exception
    try:
        return args.fn(args)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"step_state: unexpected error: {exc} (fail-open)", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
