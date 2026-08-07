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

# Mirrors session-start's path-safety guard (`.*|-*|*..*|*[!A-Za-z0-9._-]*`):
# internal dots are legal project names in this workspace, so rejecting them
# here would silently give a project like `api.v2` no decision store at all.
# Leading dot/dash and any `..` stay forbidden — this value becomes a path
# component.
PROJECT_NAME_RE = re.compile(r"^(?![.-])(?!.*\.\.)[A-Za-z0-9._-]+$")

# The fields every record carries. `run` may be empty for a standalone decision.
FIELDS = ("id", "ts", "session", "project", "run", "title", "body", "overturnable")


def _fsync_dir(directory: Path) -> None:
    """fsync a directory so a newly created entry survives a crash. Best effort:
    some filesystems refuse O_RDONLY fsync on directories."""
    try:
        dfd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


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
    data = line.encode("utf-8")
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        # If a previous writer died mid-record the file ends without a newline.
        # Appending straight onto it would JOIN this record to the broken one and
        # destroy both — the new record would report success and be unreadable.
        # Close the damaged line first; the reader then skips exactly one record.
        if path.stat().st_size:
            with open(path, "rb") as probe:
                probe.seek(-1, os.SEEK_END)
                if probe.read(1) != b"\n":
                    os.write(fd, b"\n")
        # os.write may write fewer bytes than asked (ENOSPC boundaries, large
        # records). Ignoring the count would persist a truncated record while
        # telling the caller the decision was saved — the precise failure this
        # module exists to prevent. Loop, and raise if it stops making progress.
        written = 0
        while written < len(data):
            n = os.write(fd, data[written:])
            if n <= 0:
                raise OSError(
                    f"short write to {path}: {written}/{len(data)} bytes")
            written += n
        os.fsync(fd)
        _fsync_dir(path.parent)
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


#: How many runs the rolling summary names before it stops enumerating (#797). The block must be
#: bounded by CONSTRUCTION, not by how big the store happens to be — an unbounded breakdown would
#: reintroduce exactly the growth the summary exists to remove.
_SUMMARY_MAX_RUNS = 5


def summarize_elided(elided: list[dict]) -> str | None:
    """One compact block describing the records `--last` cut away (#797). PURE.

    The decision store is never trimmed, and the session-start injection bounds itself by taking
    only the newest N — so before this, everything older was not summarized but silently DROPPED
    from a successor's view. This restores the "rolling compacted summary + the last N verbatim"
    shape the issue asks for.

    Returns None when nothing was elided, which is the common case and MUST stay byte-identical
    to the previous behaviour.

    Deliberately prints only counts, ids, dates and run names — **never a record body**. A body
    can carry quoted material, and relocating it into the summary would defeat the point, which
    is to shrink the read rather than move it.

    Degrades field by field instead of raising: this renders on a fail-OPEN injection path
    (`hooks/session-start`), so one malformed record must not take the whole block down.
    """
    if not elided:
        return None

    def _text(rec, key):
        value = rec.get(key) if isinstance(rec, dict) else None
        return value if isinstance(value, str) and value.strip() else None

    ids = [i for i in (_text(r, "id") for r in elided) if i]
    stamps = sorted(s for s in (_text(r, "ts") for r in elided) if s)

    runs: dict[str, int] = {}
    for rec in elided:
        name = _text(rec, "run")
        if name:
            runs[name] = runs.get(name, 0) + 1

    parts = [f"[rolling summary] {len(elided)} earlier decision(s) elided"]
    if ids:
        parts.append(f"{ids[0]}–{ids[-1]}" if ids[0] != ids[-1] else ids[0])
    if stamps:
        parts.append(f"{stamps[0][:10]} to {stamps[-1][:10]}")
    line = ", ".join(parts) + "."

    if runs:
        top = sorted(runs.items(), key=lambda kv: (-kv[1], kv[0]))[:_SUMMARY_MAX_RUNS]
        named = ", ".join(f"{name} ({count})" for name, count in top)
        if len(runs) > len(top):
            named += f", +{len(runs) - len(top)} more run(s)"
        line += f" Busiest runs: {named}."
    return line + " Nothing is hidden — read the full store with `decision_log.py read`."


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
    rp.add_argument("--summarize-elided", action="store_true",
                    help="prepend a rolling summary of the records --last cut away (#797). "
                         "Opt-in: without it the output is unchanged.")

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
        if getattr(args, "summarize_elided", False):
            # Recompute the UNSLICED set to learn what `--last` actually removed. Reading twice is
            # deliberate: `read_records` owns the parse, the run filter and the slice, and
            # reproducing that ordering here is exactly the drift this avoids.
            everything = read_records(args.state_dir, args.project, run=args.run)
            cut = len(everything) - len(records)
            if cut > 0:
                block = summarize_elided(everything[:cut])
                if block:
                    print(block)
        for rec in records:
            print(json.dumps(rec, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError) as exc:
        # Fail LOUD: a decision that was not recorded must not look recorded.
        warn(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
