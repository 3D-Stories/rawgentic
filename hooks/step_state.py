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

# atomic_write_lib imports LAZILY inside cmd_write (Step-11 review): a top-level
# sibling import would traceback (exit 1) on ImportError before main()'s fail-open
# boundary, and `read` has no writer dependency at all — a lone copy of this file
# must still serve reads.

SCHEMA_VERSION = 1
WORKSPACE_MARKER = ".rawgentic_workspace.json"
DEFAULT_MAX_AGE_MIN = 240
CLOCK_SKEW_MIN = 5  # future-dated entered_at beyond this is corrupt, not "fresh forever"

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
    otherwise return None (caller fails open: no write, stderr note).

    Divergence note (Step-11 review, the C21 resolver class): this DELIBERATELY
    does not read `claudeDocsPath` from the workspace file (wal-lib.sh's
    wal_resolve_claude_docs does) — today no project sets it, and the dormant
    migration also symlinks `<ws>/claude_docs`, so the paths converge. A future
    claudeDocsPath adopter must reconcile all three resolvers or the writer and
    reader silently diverge (feature degrades to the notes-grep, fail-safe)."""
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


def _history_path(state_dir: str, project: str, issue: int) -> str:
    """Per-run history file (#506): keyed project+issue (NOT session) so a
    multi-session run — pause/resume, a fresh continuation session — keeps
    accumulating ONE history; events carry session_id for interleave
    visibility."""
    return os.path.join(state_dir, "history",
                        f"{project}-issue-{issue}.history.jsonl")


def _append_history(state_dir: str, project: str, issue, record: dict) -> None:
    """Append the step-entry event to the per-run history (#506 AC1).

    Only when `issue` is an int — a null-issue write has no run to key.
    Append-only sibling of the overwrite pointer; single short line via
    O_APPEND (well under PIPE_BUF — atomic enough for telemetry). Same
    fail-open contract as the pointer: any OSError is a stderr note, never
    a gate. Raises nothing."""
    if not isinstance(issue, int) or isinstance(issue, bool):
        return  # bool is an int subclass — never key a file "issue-True"
    path = _history_path(state_dir, project, issue)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"step_state write: could not append history {path}: {exc} "
              "(fail-open)", file=sys.stderr)


# --- #506: timing computation -------------------------------------------------

DEFAULT_IDLE_THRESHOLD_S = 1800

# Phase buckets per workflow: (upper-exclusive step number, phase name), tried
# in order. Verified against the skills' real step headers (WF2
# implement-feature, WF3 fix-bug) at design time — WF3: 1/1b/2 receive+analyze,
# 3 RCA, 4 reflect gate (design); 5 fix plan (plan); 6 branch + 7 TDD fix
# (implement); 8 verification + 9 review (review); 10 PR, 11 CI, 12 merge,
# 13 post-deploy (pr_ci); 14 summary (wrap).
_PHASE_BUCKETS = {
    "wf2": ((5, "design"), (7, "plan"), (9, "implement"), (12, "review"),
            (15, "pr_ci"), (None, "wrap")),
    "wf3": ((5, "design"), (6, "plan"), (8, "implement"), (10, "review"),
            (14, "pr_ci"), (None, "wrap")),
}
# "complete" terminal = the PR-creation step (wf2 12 / wf3 10) — the last step
# EVERY path (headless included, which terminates at the PR) reaches before the
# completion step runs this CLI. Gating on the completion step's own number
# (16/14) made "complete" unreachable on a live-assembled record: that step's
# event only lands AFTER timing is embedded (#506 review F1).
_TERMINAL_STEP = {"wf2": 12.0, "wf3": 10.0}
_PHASE_NAMES = ("design", "plan", "implement", "review", "pr_ci", "wrap", "idle")


def _step_num(step) -> "float | None":
    """Leading-numeric parse: '8a' -> 8.0, '11.5' -> 11.5, garbage -> None.
    (Conceptual port of step_state_post.py's helper — that hook reads this
    module's CLI, not the reverse, so no shared import.)"""
    if not isinstance(step, str):
        return None
    m = re.match(r"(\d+(?:\.\d+)?)", step)
    return float(m.group(1)) if m else None


def _phase_of(workflow, step) -> str:
    num = _step_num(step)
    buckets = _PHASE_BUCKETS.get(workflow)
    if buckets is None or num is None:
        return "other"
    for upper, name in buckets:
        if upper is None or num < upper:
            return name
    return "other"


def compute_timing(events: list, idle_threshold_s: int = DEFAULT_IDLE_THRESHOLD_S) -> dict:
    """Pure #506 duration computation over parsed history events.

    Entry-interval model: duration(event_i) = entered_at(i+1) - entered_at(i);
    the last event is open-ended (duration null — NEVER fabricated, AC4).
    Events are sorted by entered_at first (multi-session interleave can land
    out of file order). An interval above `idle_threshold_s` keeps the
    threshold on its step and books the excess to phases.idle with
    `idle_gap: true` (AC3 — a stall is never silently attributed to the step
    it interrupted). The sort makes every interval >= 0 by construction, so
    no negative duration can be emitted. Status: absent (no events) / complete (first event
    step <= 2 AND a workflow-terminal event present) / partial (anything
    else)."""
    parsed = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        at = _parse_entered_at(ev.get("entered_at"))
        if at is None:
            continue
        parsed.append((at, ev))
    parsed.sort(key=lambda pair: pair[0])
    phases = {name: 0 for name in _PHASE_NAMES}
    steps_out = []
    total = 0
    for i, (at, ev) in enumerate(parsed):
        entry = {"step": ev.get("step"), "title": ev.get("step_title"),
                 "entered_at": ev.get("entered_at"), "duration_s": None,
                 "idle_gap": False}
        if i + 1 < len(parsed):
            # sorted order: dur >= 0 by construction (no clamp branch needed)
            dur = int((parsed[i + 1][0] - at).total_seconds())
            if dur > idle_threshold_s:
                entry["duration_s"] = idle_threshold_s
                entry["idle_gap"] = True
                phases["idle"] += dur - idle_threshold_s
            else:
                entry["duration_s"] = dur
            phase = _phase_of(ev.get("workflow"), ev.get("step"))
            if phase not in phases:
                phases[phase] = 0
            phases[phase] += entry["duration_s"]
            total += dur
        steps_out.append(entry)
    if not parsed:
        status = "absent"
    else:
        workflow = parsed[0][1].get("workflow")
        terminal = _TERMINAL_STEP.get(workflow)
        first_num = _step_num(parsed[0][1].get("step"))
        last_nums = [n for n in (_step_num(ev.get("step")) for _, ev in parsed)
                     if n is not None]
        has_terminal = (terminal is not None and last_nums
                        and max(last_nums) >= terminal)
        status = ("complete" if first_num is not None and first_num <= 2
                  and has_terminal else "partial")
    # drop a zero "other" bucket (only meaningful when an unknown workflow
    # or unparseable step actually accrued time)
    if phases.get("other") == 0:
        phases.pop("other", None)
    return {"status": status,
            "idle_gap_threshold_s": idle_threshold_s,
            "steps": steps_out,
            "phases": phases,
            "total_s": total if len(parsed) >= 2 else None}


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
        from atomic_write_lib import atomic_write_text  # noqa: PLC0415 — lazy: see module header
        atomic_write_text(path, json.dumps(record) + "\n",
                          prefix=".step_state.", mkdir=True)
    except ImportError as exc:
        print(f"step_state write: atomic_write_lib unavailable: {exc} "
              "(fail-open)", file=sys.stderr)
    except OSError as exc:
        print(f"step_state write: could not write {path}: {exc} "
              "(fail-open)", file=sys.stderr)
    _append_history(state_dir, project, issue, record)
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
    # Structural honesty (Step-11 review): a record missing its required fields,
    # carrying the wrong schema, or labeled for a DIFFERENT project (sanitize
    # collisions / mislabeled files) must read as "nothing to show" — never a
    # placeholder line that suppresses a consumer's valid fallback.
    if record.get("schema_version") != SCHEMA_VERSION:
        return 0
    if record.get("project") != project:
        return 0
    for field in ("workflow", "step", "step_title", "session_id"):
        val = record.get(field)
        if not isinstance(val, str) or not val:
            return 0
    if record.get("issue") is not None and not isinstance(record.get("issue"), int):
        return 0
    entered_at = _parse_entered_at(record.get("entered_at"))
    if entered_at is None:
        return 0
    if args.max_age_min < 0:
        return 0
    age = datetime.now(timezone.utc) - entered_at
    # Reject stale AND far-future timestamps (a corrupt future date must not be
    # immortal-fresh); a small skew allowance covers real clock drift.
    if age > timedelta(minutes=args.max_age_min) or age < timedelta(minutes=-CLOCK_SKEW_MIN):
        return 0

    print(text.rstrip("\n"))
    return 0


def cmd_timing(args) -> int:
    """Print the #506 timing object computed from the per-run history file.
    Always returns 0 (fail-open telemetry): no/unreadable history prints the
    honest absent object, never an error. Malformed history lines are skipped
    and counted in `skipped_lines`."""
    absent = {"status": "absent",
              "idle_gap_threshold_s": args.idle_threshold_s,
              "steps": [], "phases": {name: 0 for name in _PHASE_NAMES},
              "total_s": None, "skipped_lines": 0}
    project = sanitize_project(args.project)
    if project is None:
        print(json.dumps(absent))
        return 0
    try:
        issue = int(args.issue)
    except (TypeError, ValueError):
        print(json.dumps(absent))
        return 0
    state_dir = args.state_dir if args.state_dir else find_state_dir(os.getcwd())
    if not state_dir:
        print(json.dumps(absent))
        return 0
    events, skipped = [], 0
    try:
        with open(_history_path(state_dir, project, issue), encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    skipped += 1
                    continue
                if isinstance(ev, dict):
                    events.append(ev)
                else:
                    skipped += 1
    except OSError:
        print(json.dumps(absent))
        return 0
    timing = compute_timing(events, idle_threshold_s=args.idle_threshold_s)
    timing["skipped_lines"] = skipped
    print(json.dumps(timing))
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

    p_timing = sub.add_parser(
        "timing", help="print the #506 per-run timing object from the history")
    p_timing.add_argument("--project", required=True)
    p_timing.add_argument("--issue", required=True)
    p_timing.add_argument("--state-dir", default=None, dest="state_dir")
    p_timing.add_argument("--idle-threshold-s", type=int,
                          default=DEFAULT_IDLE_THRESHOLD_S, dest="idle_threshold_s")
    p_timing.set_defaults(fn=cmd_timing)

    args = parser.parse_args(argv)  # argparse errors: exit(2) — the one exception
    try:
        return args.fn(args)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"step_state: unexpected error: {exc} (fail-open)", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
