#!/usr/bin/env python3
"""Append-only decision store — the durable home for run/epic decisions (#847).

Usage:
    decision_log.py append --project <p> --id D139 --title <t> --body <b>
                           --overturnable <how-to-undo> [--run <r>] [--session <s>]
                           [--state-dir claude_docs]
    decision_log.py read --project <p> [--last N] [--run <r>] [--state-dir claude_docs]

Records live at `<state-dir>/decisions/<project>.jsonl`, a SIBLING of
`session_notes/`. That location is the whole point: `session-start` trims
`session_notes/*.md`, and before #847 that trimmer destroyed six epic decision
logs because a decision log's OLDEST entries are its valuable ones and the
trimmer keeps only the tail. A `.jsonl` file in a sibling directory is outside
that glob by construction, so no rule has to be remembered for it to be safe.

`overturnable` is mandatory: this workspace's convention is that every decision
records how to undo it, and a decision that cannot be reversed in one step is
not a decision anyone can safely overturn later.

FAIL MODE: **fail-LOUD** on append (exit 1). A decision that was not recorded
must never look recorded — the caller has to know. Reads are tolerant: a
corrupt line is skipped and reported on stderr, and never blocks the rest.

Why not `atomic_write_lib.atomic_write_text` for the append path: that helper
replaces the whole file (atomic_write_lib.py:48). Two concurrent appends would
each read the same prefix and the second replace would silently drop the
first record — the exact class of loss this module exists to end. Appends here
are `flock` + `O_APPEND` + one single write() call.
"""

import argparse
import fcntl
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# The fields every record carries. `run` may be empty for a standalone decision.
FIELDS = ("id", "ts", "session", "project", "run", "title", "body", "overturnable")


def store_path(state_dir, project: str) -> Path:
    """Resolve the JSONL store for `project`. Raises ValueError on a bad name."""
    if not PROJECT_NAME_RE.match(project):
        raise ValueError(f"invalid project name: {project!r}")
    return Path(state_dir) / "decisions" / f"{project}.jsonl"


def build_record(
    *, project: str, decision_id: str, title: str, body: str,
    overturnable: str, run: str = "", session: str = "", ts: str | None = None,
) -> dict:
    """Build one well-formed record. Pure — no I/O, so it is directly testable."""
    if not decision_id:
        raise ValueError("decision id is required")
    if not title:
        raise ValueError("title is required")
    if not overturnable:
        raise ValueError(
            "overturnable is required — every decision records how to undo it"
        )
    return {
        "id": decision_id,
        "ts": ts or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session": session,
        "project": project,
        "run": run,
        "title": title,
        "body": body,
        "overturnable": overturnable,
    }


def append_record(state_dir, project: str, record: dict) -> Path:
    """Append one record as a single line. Concurrency-safe, append-only.

    `flock` serialises writers; `O_APPEND` plus one `write()` of a
    newline-terminated line means a record is never interleaved or partially
    replaced. The file is only ever extended — no path in this module truncates
    or rewrites it.
    """
    path = store_path(state_dir, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line.encode("utf-8"))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    return path


def read_records(
    state_dir, project: str, last: int | None = None, run: str | None = None,
    warn=None,
) -> list[dict]:
    """Read records oldest-first. A corrupt line is skipped, not fatal.

    `last` slices AFTER filtering by `run`, so `--last 15 --run epic-46` means
    "the newest 15 decisions of that run", not "whichever of the newest 15
    happen to belong to it".
    """
    path = store_path(state_dir, project)
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError as exc:
                if warn:
                    warn(f"{path}:{lineno}: skipping unparseable record ({exc.msg})")
                continue
            if not isinstance(rec, dict):
                if warn:
                    warn(f"{path}:{lineno}: skipping non-object record")
                continue
            out.append(rec)
    if run is not None:
        out = [r for r in out if r.get("run") == run]
    if last is not None:
        if last <= 0:
            return []
        out = out[-last:]
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Append-only decision store")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ap = sub.add_parser("append", help="append one decision")
    ap.add_argument("--project", required=True)
    ap.add_argument("--id", dest="decision_id", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--body", default="")
    ap.add_argument("--overturnable", required=True,
                    help="how to undo this decision, in one step")
    ap.add_argument("--run", default="")
    ap.add_argument("--session", default="")
    ap.add_argument("--state-dir", default="claude_docs")

    rp = sub.add_parser("read", help="read decisions, oldest first")
    rp.add_argument("--project", required=True)
    rp.add_argument("--last", type=int, default=None)
    rp.add_argument("--run", default=None)
    rp.add_argument("--state-dir", default="claude_docs")

    args = parser.parse_args(argv)

    def warn(msg):
        print(f"decision_log: {msg}", file=sys.stderr)

    try:
        if args.cmd == "append":
            rec = build_record(
                project=args.project, decision_id=args.decision_id,
                title=args.title, body=args.body,
                overturnable=args.overturnable, run=args.run,
                session=args.session,
            )
            path = append_record(args.state_dir, args.project, rec)
            print(json.dumps({"appended": True, "id": rec["id"], "path": str(path)}))
            return 0

        records = read_records(
            args.state_dir, args.project, last=args.last, run=args.run, warn=warn,
        )
        for rec in records:
            print(json.dumps(rec, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError) as exc:
        # Fail LOUD: a decision that was not recorded must not look recorded.
        warn(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
