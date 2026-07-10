#!/usr/bin/env python3
"""FTS5 full-text index over Claude Code session JSONL history (#375).

Pure core + thin CLI (design: docs/planning/2026-07-10-375-fts5-session-index-design.md).
Subcommands: index (incremental batch), search (FTS5 + filters), status.
Explicit invocation only — no hook-event registration, no daemon, no LLM.

Format assumptions (Claude Code-internal, may change without notice — the
extractor is total and fails soft, AC3): one JSON object per line; message
lines have type in {user, assistant}, top-level sessionId/timestamp/uuid, and
message.content as either a string (user) or a list of typed blocks
(assistant). Only `text` blocks are indexed — thinking/tool payloads are
deliberately excluded (v1 scope; bump PARSER_VERSION + `index --rebuild` to
change extraction).

The DB is a derived, rebuildable store (never committed; lives under the
workspace's claude_docs/.session-index/). `index --rebuild` runs as one
transaction, so the WAL transiently grows to roughly the full index size —
keep disk headroom of about twice the final DB size for rebuilds.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
PARSER_VERSION = 1
CHECKPOINT_EVERY_FILES = 50
DRIFT_RATIO = 0.5
DRIFT_MIN_LINES = 100
DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
WORKSPACE_MARKER = ".rawgentic_workspace.json"
DB_RELPATH = Path("claude_docs") / ".session-index" / "sessions.db"

# Sentinels distinguishing "not a message line" from "message line that failed
# extraction" (format-drift guard needs the split).
IGNORED = object()
REJECTED = object()


@dataclass(frozen=True)
class ExtractedMsg:
    role: str
    text: str
    session_id: str
    ts: str
    ts_us: int
    uuid: str | None


@dataclass(frozen=True)
class ChangePlan:
    unchanged: list
    dirty: list
    vanished: list


# ------------------------------------------------------------------ pure core

def parse_ts_us(ts) -> int | None:
    """Normalize an ISO timestamp (corpus shape: 2026-06-18T03:48:27.784Z)
    to UTC microseconds; None when unparseable."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1_000_000)


def _block_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "\n\n".join(parts)
    return ""


def extract_message(obj):
    """One parsed JSONL line -> ExtractedMsg | IGNORED | REJECTED. Total.

    IGNORED = expected non-indexable shapes (non-message types; textless
    messages — tool_use/tool_result/thinking-only make up ~77% of real message
    lines). REJECTED = unexpected shape or missing/bad provenance — the
    format-drift signal.
    """
    if not isinstance(obj, dict):
        return REJECTED
    typ = obj.get("type")
    if typ not in ("user", "assistant"):
        return IGNORED
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, (str, list)):
        return REJECTED  # shape drift
    session_id = obj.get("sessionId")
    ts = obj.get("timestamp")
    ts_us = parse_ts_us(ts)
    if not session_id or ts_us is None:
        return REJECTED  # provenance drift
    text = _block_text(content)
    if not text:
        return IGNORED  # expected textless message
    uuid = obj.get("uuid")
    # JSON can carry lone-surrogate escapes (\udXXX) that SQLite cannot
    # UTF-8-encode — sanitize so one weird message never aborts a run (AC3).
    text = text.encode("utf-8", "replace").decode("utf-8")
    return ExtractedMsg(role=typ, text=text, session_id=session_id, ts=ts,
                        ts_us=ts_us, uuid=uuid if isinstance(uuid, str) else None)


def literal_quote(query: str) -> str:
    """Convert user text to a safe FTS5 phrase: wrap in double quotes and
    double any embedded double-quote characters."""
    return '"' + query.replace('"', '""') + '"'


def resolve_workspace_root(start: Path) -> Path | None:
    for p in [start, *start.parents]:
        if (p / WORKSPACE_MARKER).is_file():
            return p
    return None


def plan_changes(stored: dict, on_disk: dict) -> ChangePlan:
    """stored/on_disk: {path: (mtime_ns, size)} -> sorted change plan."""
    unchanged, dirty = [], []
    for path in sorted(on_disk):
        if stored.get(path) == on_disk[path]:
            unchanged.append(path)
        else:
            dirty.append(path)
    vanished = sorted(set(stored) - set(on_disk))
    return ChangePlan(unchanged=unchanged, dirty=dirty, vanished=vanished)


def format_drift_warning(indexed: int, rejected: int) -> bool:
    seen = indexed + rejected
    return seen >= DRIFT_MIN_LINES and rejected / seen > DRIFT_RATIO


def day_bounds_us(since: str | None, until: str | None):
    lo = parse_ts_us(f"{since}T00:00:00+00:00") if since else None
    hi = parse_ts_us(f"{until}T23:59:59.999999+00:00") if until else None
    return lo, hi


# ------------------------------------------------------------------- DB layer

_SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE files(
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  project TEXT NOT NULL,
  mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL,
  indexed_at TEXT NOT NULL,
  message_count INTEGER NOT NULL,
  malformed_count INTEGER NOT NULL,
  ignored_count INTEGER NOT NULL,
  rejected_count INTEGER NOT NULL
);
CREATE TABLE messages(
  id INTEGER PRIMARY KEY,
  file_id INTEGER NOT NULL REFERENCES files(id),
  line_no INTEGER NOT NULL,
  session_id TEXT NOT NULL, ts TEXT NOT NULL,
  ts_us INTEGER NOT NULL,
  project TEXT NOT NULL, role TEXT NOT NULL,
  uuid TEXT, text TEXT NOT NULL,
  UNIQUE(file_id, line_no)
);
CREATE INDEX idx_messages_file ON messages(file_id);
CREATE INDEX idx_messages_project_ts ON messages(project, ts_us);
CREATE VIRTUAL TABLE messages_fts USING fts5(
  text, content='messages', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, text) VALUES('delete', old.id, old.text);
END;
CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, text) VALUES('delete', old.id, old.text);
  INSERT INTO messages_fts(rowid, text) VALUES (new.id, new.text);
END;
"""


def _refuse_symlink(db_path: Path) -> str | None:
    if db_path.is_symlink():
        return f"refusing symlinked DB destination: {db_path}"
    return None


def _create_schema(con: sqlite3.Connection) -> None:
    con.executescript(_SCHEMA)
    now = datetime.now(timezone.utc).isoformat()
    con.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", [
        ("schema_version", str(SCHEMA_VERSION)),
        ("parser_version", str(PARSER_VERSION)),
        ("last_run", now),
    ])


def _open_writer(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(db_path.parent, 0o700)
    existed = db_path.exists()
    con = sqlite3.connect(db_path)
    mode = con.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if mode != "wal":
        con.close()
        raise SystemExit2(f"journal_mode is {mode!r}, not 'wal' — filesystem "
                          "may not support WAL; refusing to index")
    con.execute("PRAGMA busy_timeout=5000")
    os.chmod(db_path, 0o600)  # unconditional — re-tighten pre-existing DBs
    for sidecar in (f"{db_path}-wal", f"{db_path}-shm"):
        if os.path.exists(sidecar):
            os.chmod(sidecar, 0o600)
    _ = existed
    return con


class SystemExit2(Exception):
    """Usage/environment error -> exit 2."""


def _meta(con) -> dict:
    return dict(con.execute("SELECT key, value FROM meta"))


def _scan_corpus(projects_dir: Path) -> dict:
    # rglob: the real corpus nests (project/session-dir/subagents/*.jsonl)
    on_disk = {}
    for f in projects_dir.rglob("*.jsonl"):
        try:
            st = f.stat()
        except OSError:
            continue
        on_disk[str(f)] = (st.st_mtime_ns, st.st_size)
    return on_disk


def _project_of(path: str, projects_dir: Path) -> str:
    """Project = first path component under the corpus root (nesting-safe)."""
    try:
        return Path(path).relative_to(projects_dir).parts[0]
    except (ValueError, IndexError):
        return Path(path).parent.name


def _parse_file(path: str):
    """Pure read pass: parse one JSONL file -> (counts, rows). No DB writes."""
    counts = {"message": 0, "malformed": 0, "ignored": 0, "rejected": 0}
    rows = []
    with Path(path).open(encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                counts["malformed"] += 1
                continue
            m = extract_message(obj)
            if m is IGNORED:
                counts["ignored"] += 1
            elif m is REJECTED:
                counts["rejected"] += 1
            else:
                counts["message"] += 1
                rows.append((line_no, m))
    return counts, rows


def _write_file_rows(con, path: str, stat_pair, counts, rows, project) -> None:
    """Replace one file's rows in one transaction (write pass only)."""
    now = datetime.now(timezone.utc).isoformat()
    with con:  # one transaction per file
        old = con.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
        if old:
            con.execute("DELETE FROM messages WHERE file_id=?", (old[0],))
            con.execute("DELETE FROM files WHERE id=?", (old[0],))
        cur = con.execute(
            "INSERT INTO files(path, project, mtime_ns, size, indexed_at, "
            "message_count, malformed_count, ignored_count, rejected_count) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (path, project, stat_pair[0], stat_pair[1], now, counts["message"],
             counts["malformed"], counts["ignored"], counts["rejected"]))
        file_id = cur.lastrowid
        con.executemany(
            "INSERT INTO messages(file_id, line_no, session_id, ts, ts_us, "
            "project, role, uuid, text) VALUES (?,?,?,?,?,?,?,?,?)",
            [(file_id, ln, m.session_id, m.ts, m.ts_us, project, m.role,
              m.uuid, m.text) for ln, m in rows])


def cmd_index(args) -> int:
    db_path = Path(args.db)
    err = _refuse_symlink(db_path)
    if err:
        raise SystemExit2(err)
    projects_dir = Path(args.projects_dir)
    if not projects_dir.is_dir():
        raise SystemExit2(f"projects dir not found or not a directory: "
                          f"{projects_dir} — refusing to index (a wrong path "
                          "would prune the whole store)")

    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = db_path.parent / (db_path.name + ".lock")
    lock = open(lock_path, "w", encoding="utf-8")
    os.chmod(lock_path, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another index run appears to be in progress "
                  f"(lock: {lock_path})", file=sys.stderr)
            return 3
        fresh = not db_path.exists()
        con = _open_writer(db_path)
        try:
            if fresh:
                with con:
                    _create_schema(con)
            elif args.rebuild:
                with con:  # one txn: readers see old or new, never partial
                    con.executescript(
                        "DROP TRIGGER IF EXISTS messages_ai;"
                        "DROP TRIGGER IF EXISTS messages_ad;"
                        "DROP TRIGGER IF EXISTS messages_au;"
                        "DROP TABLE IF EXISTS messages_fts;"
                        "DROP TABLE IF EXISTS messages;"
                        "DROP TABLE IF EXISTS files;"
                        "DROP TABLE IF EXISTS meta;")
                    _create_schema(con)
            else:
                meta = _meta(con)
                if (meta.get("schema_version") != str(SCHEMA_VERSION)
                        or meta.get("parser_version") != str(PARSER_VERSION)):
                    print(f"index version mismatch (stored schema="
                          f"{meta.get('schema_version')} parser="
                          f"{meta.get('parser_version')}, code "
                          f"{SCHEMA_VERSION}/{PARSER_VERSION}) — run "
                          "`index --rebuild`", file=sys.stderr)
                    return 2

            stored = {r[0]: (r[1], r[2]) for r in con.execute(
                "SELECT path, mtime_ns, size FROM files")}
            on_disk = _scan_corpus(projects_dir)
            if stored and not on_disk:
                # A readable-but-empty scan of a previously populated corpus
                # is an environment problem (unreadable dirs, wrong mount) far
                # more often than "every session legitimately vanished".
                print(f"refusing mass-vanish: index holds {len(stored)} files "
                      f"but the corpus scan of {projects_dir} found none — "
                      "check the path/permissions, or force with "
                      "`index --rebuild`", file=sys.stderr)
                return 2
            plan = plan_changes(stored, on_disk)

            for path in plan.vanished:
                with con:
                    fid = con.execute("SELECT id FROM files WHERE path=?",
                                      (path,)).fetchone()[0]
                    con.execute("DELETE FROM messages WHERE file_id=?", (fid,))
                    con.execute("DELETE FROM files WHERE id=?", (fid,))

            totals = {"message": 0, "malformed": 0, "ignored": 0, "rejected": 0}
            indexed_files = 0
            for path in plan.dirty:
                written = False
                for _attempt in range(3):
                    try:
                        st = Path(path).stat()
                        pair = (st.st_mtime_ns, st.st_size)
                        counts, rows = _parse_file(path)  # pure read pass
                        st2 = Path(path).stat()
                    except OSError as e:
                        print(f"warning: cannot read {path} ({e}), skipping",
                              file=sys.stderr)
                        break
                    if (st2.st_mtime_ns, st2.st_size) != pair:
                        continue  # changed mid-read (live session) — retry
                    _write_file_rows(con, path, pair, counts, rows,
                                     _project_of(path, projects_dir))
                    for k in totals:
                        totals[k] += counts[k]
                    indexed_files += 1
                    written = True
                    break
                else:
                    print(f"warning: {path} kept changing mid-read; keeping "
                          "prior indexed version (stale — see `status`)",
                          file=sys.stderr)
                if written and indexed_files % CHECKPOINT_EVERY_FILES == 0:
                    con.execute("PRAGMA wal_checkpoint(PASSIVE)")

            with con:
                con.execute("UPDATE meta SET value=? WHERE key='last_run'",
                            (datetime.now(timezone.utc).isoformat(),))
            con.execute("PRAGMA wal_checkpoint(PASSIVE)")
        finally:
            con.close()

        if format_drift_warning(totals["message"], totals["rejected"]):
            print(f"warning: possible format drift — {totals['rejected']} of "
                  f"{totals['message'] + totals['rejected']} message lines "
                  "failed extraction; check `status` and the format "
                  "assumptions in this module's docstring", file=sys.stderr)
        print(f"{indexed_files} files indexed, {len(plan.unchanged)} "
              f"unchanged, {len(plan.vanished)} vanished; "
              f"{totals['message']} messages added; "
              f"malformed {totals['malformed']} ignored {totals['ignored']} "
              f"rejected {totals['rejected']}")
        return 0
    finally:
        lock.close()


# ------------------------------------------------------------------- search

def _open_reader(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit2(f"no index at {db_path} — run `session_index.py "
                          "index` first")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def cmd_search(args) -> int:
    con = _open_reader(Path(args.db))
    query = literal_quote(args.query) if args.literal else args.query
    sql = ("SELECT m.session_id, m.ts, m.project, m.role, "
           "snippet(messages_fts, 0, '[', ']', '…', 12) AS snip, "
           "f.path, m.line_no, bm25(messages_fts) AS score "
           "FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid "
           "JOIN files f ON f.id = m.file_id "
           "WHERE messages_fts MATCH ?")
    params = [query]
    if args.project:
        sql += " AND m.project = ?"
        params.append(args.project)
    lo, hi = day_bounds_us(args.since, args.until)
    if args.since and lo is None:
        print(f"invalid --since date: {args.since!r} (expected YYYY-MM-DD)",
              file=sys.stderr)
        return 2
    if args.until and hi is None:
        print(f"invalid --until date: {args.until!r} (expected YYYY-MM-DD)",
              file=sys.stderr)
        return 2
    if lo is not None:
        sql += " AND m.ts_us >= ?"
        params.append(lo)
    if hi is not None:
        sql += " AND m.ts_us <= ?"
        params.append(hi)
    sql += (" ORDER BY bm25(messages_fts), m.ts_us, m.session_id, f.path, "
            "m.line_no LIMIT ?")
    params.append(args.limit)
    try:
        rows = con.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        print(f"FTS5 query syntax error: {e} — try --literal for plain-text "
              "queries", file=sys.stderr)
        return 2
    results = [{"session_id": r[0], "ts": r[1], "project": r[2], "role": r[3],
                "snippet": r[4], "path": r[5], "line_no": r[6], "score": r[7]}
               for r in rows]
    if args.json:
        print(json.dumps({"query": args.query, "results": results}))
    else:
        for r in results:
            print(f"{r['ts']}  {r['project']}  {r['session_id']}  "
                  f"[{r['role']}] {r['snippet']}")
        if not results:
            print("no matches")
    return 0


def cmd_status(args) -> int:
    db_path = Path(args.db)
    con = _open_reader(db_path)
    meta = _meta(con)
    files, msgs, malformed, ignored, rejected = con.execute(
        "SELECT count(*), coalesce(sum(message_count),0), "
        "coalesce(sum(malformed_count),0), coalesce(sum(ignored_count),0), "
        "coalesce(sum(rejected_count),0) FROM files").fetchone()
    stored = {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT path, mtime_ns, size FROM files")}
    plan = plan_changes(stored, _scan_corpus(Path(args.projects_dir)))
    new = [p for p in plan.dirty if p not in stored]
    changed = [p for p in plan.dirty if p in stored]
    wal = db_path.with_name(db_path.name + "-wal")
    print(f"schema_version: {meta.get('schema_version')}  "
          f"parser_version: {meta.get('parser_version')}")
    print(f"last_run: {meta.get('last_run')}")
    print(f"files: {files}  messages: {msgs}")
    print(f"malformed: {malformed}  ignored: {ignored}  rejected: {rejected}")
    print(f"db_size: {db_path.stat().st_size}  "
          f"wal_size: {wal.stat().st_size if wal.exists() else 0}")
    print(f"staleness — new: {len(new)}  changed: {len(changed)}  "
          f"missing: {len(plan.vanished)}")
    return 0


# ---------------------------------------------------------------------- CLI

def _positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return n


def _default_db() -> str | None:
    root = resolve_workspace_root(Path.cwd())
    return str(root / DB_RELPATH) if root else None


def main(argv) -> int:
    ap = argparse.ArgumentParser(prog="session_index")
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = {"--db": dict(default=_default_db(),
                           help="index DB path (default: workspace "
                                "claude_docs/.session-index/sessions.db)"),
              "--projects-dir": dict(default=str(DEFAULT_PROJECTS_DIR))}

    p_index = sub.add_parser("index")
    p_index.add_argument("--db", **common["--db"])
    p_index.add_argument("--projects-dir", **common["--projects-dir"])
    p_index.add_argument("--rebuild", action="store_true")
    p_index.set_defaults(fn=cmd_index)

    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.add_argument("--db", **common["--db"])
    p_search.add_argument("--project")
    p_search.add_argument("--since", metavar="YYYY-MM-DD")
    p_search.add_argument("--until", metavar="YYYY-MM-DD")
    p_search.add_argument("--limit", type=_positive_int, default=20)
    p_search.add_argument("--literal", action="store_true")
    p_search.add_argument("--json", action="store_true")
    p_search.set_defaults(fn=cmd_search)

    p_status = sub.add_parser("status")
    p_status.add_argument("--db", **common["--db"])
    p_status.add_argument("--projects-dir", **common["--projects-dir"])
    p_status.set_defaults(fn=cmd_status)

    # Corpus project names are path-mangled and start with "-" (e.g.
    # -home-user-proj); merge "--project <val>" to "--project=<val>" so
    # argparse doesn't read the value as a flag.
    argv = list(argv)
    for i, a in enumerate(argv[:-1]):
        if a == "--project" and argv[i + 1].startswith("-"):
            argv[i:i + 2] = [f"--project={argv[i + 1]}"]
            break
    args = ap.parse_args(argv)
    if not args.db:
        print("cannot resolve workspace root (.rawgentic_workspace.json not "
              "found above cwd) — pass --db explicitly", file=sys.stderr)
        return 2
    try:
        return args.fn(args)
    except SystemExit2 as e:
        print(str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
