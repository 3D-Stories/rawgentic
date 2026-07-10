#!/usr/bin/env python3
"""WF17 session-mining detectors, candidates queue, and CLI (#376).

Pure core + thin CLI (design: docs/planning/2026-07-10-376-session-mining-design.md).
detect → append-only event-log queue → propose (recurrence ≥ 3 distinct
sessions, human gate) → disposition. Report-only by contract: this module
writes ONLY the candidates queue; deterministic detectors, no LLM, explicit
invocation only.

Fail modes: queue corruption anywhere but a torn final line fails CLOSED for
`propose`/`disposition` (a lost `declined` event would silently resurrect a
declined candidate — the queue holds human dispositions and is NOT
rebuildable); `detect` proceeds (append-only, never proposes). Detector
coverage is honest: v1 reads the #375 FTS5 index and session notes only — it
does NOT inspect raw tool_use/tool_result payloads and cannot conclude that
command sequences or tool errors are absent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

IDENTITY_VERSION = 1
RULES_VERSION = 1
SCHEMA_VERSION = 1
SCOPE = "workspace"
RECURRENCE_THRESHOLD = 3
SEARCH_LIMIT = 500
EVIDENCE_MAX_CHARS = 500
STRONG_MATCH = 0.6
BORDERLINE_MATCH = 0.3
QUEUE_RELPATH = Path("claude_docs") / ".mining" / "candidates.jsonl"
WORKSPACE_MARKER = ".rawgentic_workspace.json"

# Versioned detector rules (a change here requires an IDENTITY_VERSION bump
# only if it alters canonical patterns; adding phrases is additive).
# Apostrophe-free by rule: unicode61 splits "doesn't" and breaks phrases.
FRICTION_PHRASES = (
    "command not found",
    "permission denied",
    "try again",
    "still failing",
    "same error",
    "does not work",
)
ERROR_PHRASES = (
    "traceback most recent call",
    "no such file or directory",
    "connection refused",
    "exit code 1",
)

MACHINE_EVENTS = ("detected", "evidence_updated", "proposed")
HUMAN_EVENTS = ("accepted", "declined", "filed")

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_REDACT_RULES = (
    # no / or . (paths/modules are the evidence signal); UUIDs exempted
    ("hex-or-b64", re.compile(r"\b(?=[A-Za-z0-9+_-]*[0-9])"
                              r"(?=[A-Za-z0-9+_-]*[A-Za-z])"
                              r"[A-Za-z0-9+_-]{20,}\b")),
    ("key-value", re.compile(
        r"\b(\w*(?:TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL)\w*)=(\S+)",
        re.IGNORECASE)),
    ("bearer", re.compile(r"\bBearer\s+\S+", re.IGNORECASE)),
)


class QueueCorruption(Exception):
    """Unparseable NON-final queue line — propose/disposition must refuse."""


@dataclass(frozen=True)
class Signal:
    detector: str
    canonical_pattern: str
    session_id: str | None
    ts: str
    quote: str
    source: str  # "index" | "notes"


@dataclass(frozen=True)
class RecurrenceAssessment:
    distinct_sessions: int
    sessions: frozenset


@dataclass
class Candidate:
    candidate_key: str
    detector: str
    canonical_pattern: str
    title: str
    evidence: list
    distinct_sessions: int
    coverage: dict
    borderline_match: tuple | None = field(default=None)


# ------------------------------------------------------------------ pure core

def normalize_pattern(text: str) -> str:
    return " ".join(text.split()).strip().strip(".,;:!?").lower()


def normalize_command(cmd: str) -> str:
    return " ".join(cmd.split()[:2])


def candidate_key(detector: str, canonical_pattern: str,
                  scope: str = SCOPE) -> str:
    raw = f"{IDENTITY_VERSION}|{detector}|{scope}|{normalize_pattern(canonical_pattern)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _signal_from_row(detector: str, pattern: str, r: dict) -> Signal | None:
    sid = r.get("session_id")
    if not sid:
        return None
    return Signal(detector=detector, canonical_pattern=normalize_pattern(pattern),
                  session_id=sid, ts=r.get("ts", ""),
                  quote=r.get("quote") or r.get("snippet", ""),
                  source=r.get("_quote_source", "index"))


def detect_friction(phrase: str, rows: list) -> list:
    return [s for s in (_signal_from_row("friction", phrase, r) for r in rows)
            if s is not None]


def detect_error_proxies(phrase: str, rows: list) -> list:
    return [s for s in (_signal_from_row("error_proxy", phrase, r) for r in rows)
            if s is not None]


def detect_note_commands(notes_text: str) -> list:
    """Fenced ```bash blocks + backticked commands in session notes.
    Session id resolves from a UUID in the SAME markdown section; otherwise
    the signal is evidence-only (session_id=None — never counts toward the
    recurrence threshold)."""
    signals = []
    sections = re.split(r"^(?=#{2,3} )", notes_text, flags=re.MULTILINE)
    for section in sections:
        m = _UUID_RE.search(section)
        sid = m.group(0) if m else None
        cmds = []
        for block in re.findall(r"```(?:bash|sh)\n(.*?)```", section, re.DOTALL):
            cmds.extend(line.strip() for line in block.splitlines()
                        if line.strip() and not line.strip().startswith("#"))
        cmds.extend(c for c in re.findall(r"`([^`\n]+)`", section)
                    if " " in c and re.match(r"^[a-z][\w.-]*(\s|$)", c))
        for cmd in cmds:
            canon = normalize_command(cmd)
            if not canon:
                continue
            signals.append(Signal(
                detector="note_commands", canonical_pattern=canon,
                session_id=sid, ts="", quote=cmd[:EVIDENCE_MAX_CHARS],
                source="notes"))
    return signals


def recurrence(signals: list) -> dict:
    """Distinct-session counting — one vote per session; evidence-only
    signals (session_id None) never count."""
    buckets: dict = {}
    for s in signals:
        k = (s.detector, s.canonical_pattern)
        buckets.setdefault(k, set())
        if s.session_id:
            buckets[k].add(s.session_id)
    return {p: RecurrenceAssessment(distinct_sessions=len(v),
                                    sessions=frozenset(v))
            for p, v in buckets.items()}


def redact_evidence(text: str) -> str:
    """Best-effort NAMED-rule masking (v1 rules; not a guarantee over
    arbitrary secret shapes — the human gate additionally reviews quotes)."""
    out = text
    out = _REDACT_RULES[2][1].sub("[redacted:bearer]", out)
    out = _REDACT_RULES[1][1].sub(r"\1=[redacted:key-value]", out)
    out = _REDACT_RULES[0][1].sub(
        lambda m: m.group(0) if _UUID_RE.fullmatch(m.group(0))
        else "[redacted:blob]", out)
    return out


def parse_frontmatter(text: str) -> tuple:
    name, desc = "", ""
    if text.startswith("---"):
        for line in text.split("---", 2)[1].splitlines():
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("description:"):
                desc = line.split(":", 1)[1].strip()
    return name, desc


_STOPWORDS = frozenset(
    "a an the and or of to for in on with over via when use this that is are "
    "it its by as at from".split())


def _tokens(text: str) -> frozenset:
    return frozenset(t for t in re.findall(r"[a-z0-9]+", text.lower())
                     if t not in _STOPWORDS)


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def dedupe_candidates(candidates: list, reduced_queue: dict,
                      skill_descriptors: list) -> tuple:
    """-> (fresh, suppressed, borderline). Terminal queue states and strong
    skill matches suppress; borderline matches surface with skill+score."""
    fresh, suppressed, borderline = [], [], []
    for c in candidates:
        state = reduced_queue.get(c.candidate_key, {}).get("event")
        if state in HUMAN_EVENTS:
            suppressed.append((c, f"terminal:{state}"))
            continue
        cand_tokens = _tokens(f"{c.title} {c.canonical_pattern}")
        best = (0.0, None)
        for name, desc in skill_descriptors:
            score = jaccard(cand_tokens, _tokens(f"{name} {desc}"))
            if score > best[0]:
                best = (score, name)
        if best[0] >= STRONG_MATCH:
            suppressed.append((c, f"skill-match:{best[1]}:{best[0]:.2f}"))
        elif best[0] >= BORDERLINE_MATCH:
            c.borderline_match = (best[1], round(best[0], 2))
            borderline.append(c)
        else:
            fresh.append(c)
    return fresh, suppressed, borderline


# -------------------------------------------------------------------- queue

def queue_append(path, event: dict) -> None:
    """Plain O_APPEND single-line write (no lock — the reducer's
    human-over-machine rule makes duplicates benign). Torn-tail guard:
    truncate a torn final fragment (already-lost data from a crashed write)
    so it never becomes a fatal mid-file corruption. Truncate-then-append is
    not atomic; the window exists only in post-crash recovery, a dropped
    MACHINE event regenerates on the next detect, and human dispositions are
    interactive (never concurrent with crash recovery in practice)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        with path.open("r+b") as fh:
            fh.seek(-1, os.SEEK_END)
            if fh.read(1) != b"\n":
                data = path.read_bytes()
                cut = data.rfind(b"\n")
                tail = data[cut + 1:]
                try:
                    parsed = json.loads(tail.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    parsed = None
                if isinstance(parsed, dict):
                    fh.write(b"\n")  # complete event, just unterminated
                    # (hand-repair case) — repair, never wipe real data
                else:
                    fh.truncate(cut + 1 if cut >= 0 else 0)
    event = dict(event)
    if "evidence" in event:
        event["evidence"] = [
            {**e, "quote": redact_evidence(str(e.get("quote", "")))[:EVIDENCE_MAX_CHARS]}
            for e in event["evidence"]]
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, separators=(",", ":")) + "\n")


def reduce_queue(path) -> tuple:
    """-> (state, malformed_tail). Human events are absorbing against machine
    events; a later HUMAN event always applies. Raises QueueCorruption on any
    unparseable NON-final line; a torn final line returns malformed_tail=True."""
    path = Path(path)
    state: dict = {}
    if not path.exists():
        return state, False
    lines = path.read_text(encoding="utf-8").splitlines()
    malformed_tail = False
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            e = None
        if not isinstance(e, dict):
            # non-JSON or valid-JSON-non-object: same corruption semantics
            if i == len(lines) - 1:
                malformed_tail = True
                continue
            raise QueueCorruption(
                f"unparseable non-final queue line {i + 1} in {path}")
        key = e.get("candidate_key")
        if not key:
            continue
        prior = state.get(key)
        if (prior and prior.get("event") in HUMAN_EVENTS
                and e.get("event") not in HUMAN_EVENTS):
            continue  # only a human event may follow a human event
        state[key] = e
    return state, malformed_tail


# ---------------------------------------------------------------- WF1 draft

def build_wf1_draft(c: Candidate) -> str:
    """WF1 template-shaped draft PROMPT (WF1 has no pre-drafted-body entry
    path — it re-drafts from this, and its own dedup + approval still run)."""
    ev_lines = "\n".join(
        f"- session `{e['session_id']}`: \"{e['quote']}\" ({e['source']})"
        for e in c.evidence)
    slug = re.sub(r"[^a-z0-9]+", "-", c.title.lower()).strip("-")
    return f"""feat(mining-candidate): {c.title}

## Description
Recurring pattern mined from session history by WF17 session-mining
(detector: {c.detector}; canonical pattern: `{c.canonical_pattern}`;
recurrence: {c.distinct_sessions} distinct sessions). Proposed as a
skill/command candidate `{slug}`.

Verbatim evidence:
{ev_lines}

## Acceptance Criteria
1. The recurring friction above no longer requires manual repetition.
2. [WF1 to refine testable criteria with the user]

## Scope
In scope: the minimal skill/command covering the evidenced pattern.
Out of scope: anything not evidenced above.

## Affected Components
[WF1 verifies real components before filing]

## Risk
Low — additive tooling candidate; recurrence evidence attached
(coverage: {c.coverage}).
"""


# --------------------------------------------------------------- CLI helpers

def resolve_workspace_root(start: Path):
    for p in [start, *start.parents]:
        if (p / WORKSPACE_MARKER).is_file():
            return p
    return None


def _default_queue():
    root = resolve_workspace_root(Path.cwd())
    return str(root / QUEUE_RELPATH) if root else None


def _default_db():
    """Default #375 DB path — quote resolution must not silently degrade to
    marked-up snippets just because --db was omitted (live-run catch)."""
    root = resolve_workspace_root(Path.cwd())
    db = root / "claude_docs" / ".session-index" / "sessions.db" if root else None
    return str(db) if db and db.exists() else None


def _session_index_cli() -> Path:
    return Path(__file__).resolve().parent / "session_index.py"


def _search(phrase: str, db: str | None) -> tuple:
    cmd = [sys.executable, str(_session_index_cli()), "search", phrase,
           "--literal", "--limit", str(SEARCH_LIMIT), "--json"]
    if db:
        cmd += ["--db", db]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise SystemExit2(f"session_index search failed (rc={r.returncode}): "
                          f"{r.stderr.strip()[:300]}")
    try:
        rows = json.loads(r.stdout)["results"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise SystemExit2(f"unparseable session_index search output: {e}")
    coverage = {"returned_rows": len(rows), "requested_limit": SEARCH_LIMIT,
                "limit_hit": len(rows) >= SEARCH_LIMIT}
    return rows, coverage


def _resolve_quote(db_path: str, row: dict, phrase: str) -> str:
    """Verbatim quote from the #375 DB (read-only) — matched phrase ± window."""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            got = con.execute(
                "SELECT m.text FROM messages m JOIN files f ON f.id = m.file_id "
                "WHERE f.path = ? AND m.line_no = ?",
                (row["path"], row["line_no"])).fetchone()
        finally:
            con.close()
    except sqlite3.Error as e:
        # fail-loud per the design's platform_apis declaration — a silent
        # snippet fallback would break AC2's verbatim promise invisibly
        raise SystemExit2(f"quote resolution failed against {db_path}: {e}")
    if not got:
        row["_quote_source"] = "index-snippet"
        return row.get("snippet", "")
    text = got[0]
    idx = text.lower().find(phrase.lower())
    if idx < 0:
        row["_quote_source"] = "index-snippet"
        return text[:EVIDENCE_MAX_CHARS]
    start = max(0, idx - 200)
    return text[start:idx + len(phrase) + 200]


def _read_notes(workspace_root: Path) -> str:
    parts = []
    skipped = 0
    docs = workspace_root / "claude_docs"
    for f in sorted(docs.glob("session_notes*")):
        if f.is_file():
            try:
                parts.append(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                skipped += 1
                continue
    notes_dir = docs / "session_notes"
    if notes_dir.is_dir():
        for f in sorted(notes_dir.glob("*.md")):
            try:
                parts.append(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                skipped += 1
                continue
    if skipped:
        print(f"warning: {skipped} session-notes file(s) unreadable — "
              "note-command detection is partial", file=sys.stderr)
    return "\n".join(parts)


def _skill_descriptors() -> list:
    descs = []
    plugin_skills = Path(__file__).resolve().parent.parent / "skills"
    dirs = list(plugin_skills.glob("*/SKILL.md"))
    root = resolve_workspace_root(Path.cwd())
    if root:
        dirs += list((root / ".claude" / "skills").glob("*/SKILL.md"))
    for f in dirs:
        try:
            name, desc = parse_frontmatter(f.read_text(encoding="utf-8",
                                                       errors="replace"))
        except OSError:
            continue
        if name or desc:
            descs.append((name or f.parent.name, desc))
    return descs


class SystemExit2(Exception):
    """Usage/environment error -> exit 2."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------- commands

def cmd_detect(args) -> int:
    queue = Path(args.queue)
    run_id = args.run_id or _now()
    signals: list = []
    coverages: dict = {}
    for phrase in FRICTION_PHRASES:
        rows, cov = _search(phrase, args.db)
        for r in rows:
            r["quote"] = (_resolve_quote(args.db, r, phrase) if args.db
                          else r.get("snippet", ""))
        signals.extend(detect_friction(phrase, rows))
        coverages[normalize_pattern(phrase)] = cov
    for phrase in ERROR_PHRASES:
        rows, cov = _search(phrase, args.db)
        for r in rows:
            r["quote"] = (_resolve_quote(args.db, r, phrase) if args.db
                          else r.get("snippet", ""))
        signals.extend(detect_error_proxies(phrase, rows))
        coverages[normalize_pattern(phrase)] = cov
    root = (Path(args.workspace_root) if args.workspace_root
            else resolve_workspace_root(Path.cwd()))
    if root:
        signals.extend(detect_note_commands(_read_notes(Path(root))))

    rec = recurrence(signals)
    try:
        state, _ = reduce_queue(queue)
    except QueueCorruption:
        state = {}  # detect proceeds; skips the material-change optimization

    appended = 0
    by_pattern: dict = {}
    for s in signals:
        by_pattern.setdefault((s.detector, s.canonical_pattern), []).append(s)
    for (detector, pattern), sigs in sorted(by_pattern.items()):
        key = candidate_key(detector, pattern)
        assess = rec.get((detector, pattern),
                         RecurrenceAssessment(0, frozenset()))
        evidence = [{"session_id": s.session_id, "quote": s.quote,
                     "source": s.source} for s in sigs[:5]]
        prior = state.get(key)
        event_name = "detected" if prior is None else "evidence_updated"
        if prior and prior.get("event") in HUMAN_EVENTS:
            continue  # never re-touch human-stated keys
        norm_ev = sorted((e["session_id"] or "", redact_evidence(
            str(e["quote"]))[:EVIDENCE_MAX_CHARS]) for e in evidence)
        prior_ev = sorted((e.get("session_id") or "", str(e.get("quote", "")))
                          for e in (prior or {}).get("evidence", []))
        if (prior
                and prior.get("distinct_sessions") == assess.distinct_sessions
                and norm_ev == prior_ev):
            continue  # nothing materially changed (count AND evidence set)
        queue_append(queue, {
            "schema_version": SCHEMA_VERSION, "ts": _now(),
            "run_id": run_id, "event": event_name,
            "candidate_key": key, "detector": detector,
            "canonical_pattern": pattern, "title": pattern,
            "evidence": evidence,
            "distinct_sessions": assess.distinct_sessions,
            "coverage": coverages.get(pattern,
                                      {"returned_rows": len(sigs),
                                       "requested_limit": SEARCH_LIMIT,
                                       "limit_hit": False})})
        appended += 1
    print(f"{len(signals)} signals across {len(by_pattern)} patterns; "
          f"{appended} queue events appended")
    return 0


def cmd_propose(args) -> int:
    queue = Path(args.queue)
    run_id = args.run_id or _now()
    try:
        state, torn = reduce_queue(queue)
    except QueueCorruption as e:
        print(f"queue corruption: {e} — refusing to propose (the queue holds "
              "human dispositions and is not rebuildable; repair by hand)",
              file=sys.stderr)
        return 2
    if torn:
        print("warning: torn final queue line skipped", file=sys.stderr)

    candidates = []
    pending_wf1 = []
    for key, e in sorted(state.items()):
        if e.get("event") == "accepted":
            pending_wf1.append(e)
            continue
        if e.get("event") in HUMAN_EVENTS:
            continue
        if e.get("distinct_sessions", 0) < RECURRENCE_THRESHOLD:
            continue
        candidates.append(Candidate(
            candidate_key=key, detector=e.get("detector", ""),
            canonical_pattern=e.get("canonical_pattern", ""),
            title=e.get("title", ""), evidence=e.get("evidence", []),
            distinct_sessions=e.get("distinct_sessions", 0),
            coverage=e.get("coverage", {})))
    fresh, suppressed, borderline = dedupe_candidates(
        candidates, state, _skill_descriptors())

    out = {"proposed": [], "borderline": [], "suppressed": len(suppressed),
           "pending_wf1_action": [
               {"candidate_key": e["candidate_key"],
                "title": e.get("title", "")} for e in pending_wf1]}
    for c in fresh + borderline:
        entry = {"candidate_key": c.candidate_key, "detector": c.detector,
                 "canonical_pattern": c.canonical_pattern, "title": c.title,
                 "recurrence": c.distinct_sessions, "coverage": c.coverage,
                 "evidence": c.evidence}
        if c.borderline_match:
            entry["borderline_match"] = list(c.borderline_match)
            out["borderline"].append(entry)
        else:
            out["proposed"].append(entry)
        prior = state.get(c.candidate_key, {})
        if (prior.get("event") == "proposed"
                and prior.get("distinct_sessions") == c.distinct_sessions):
            continue  # already proposed at this recurrence — no churn
        queue_append(queue, {
            "schema_version": SCHEMA_VERSION, "ts": _now(),
            "run_id": run_id, "event": "proposed",
            "candidate_key": c.candidate_key, "detector": c.detector,
            "canonical_pattern": c.canonical_pattern, "title": c.title,
            "evidence": c.evidence,
            "distinct_sessions": c.distinct_sessions,
            "coverage": c.coverage})
    if args.json:
        print(json.dumps(out))
    else:
        for e in out["proposed"]:
            print(f"PROPOSE {e['candidate_key'][:12]} [{e['detector']}] "
                  f"{e['title']} — recurrence {e['recurrence']} "
                  f"(coverage {e['coverage']})")
            for ev_ in e["evidence"]:
                print(f"    {ev_['session_id']}: {ev_['quote'][:120]!r} "
                      f"({ev_['source']})")
        for e in out["borderline"]:
            print(f"BORDERLINE {e['candidate_key'][:12]} {e['title']} — "
                  f"matches {e['borderline_match']}")
        for e in out["pending_wf1_action"]:
            print(f"PENDING-WF1 {e['candidate_key'][:12]} {e['title']}")
        if not (out["proposed"] or out["borderline"]
                or out["pending_wf1_action"]):
            print("no candidates at threshold")
    return 0


def cmd_disposition(args) -> int:
    queue = Path(args.queue)
    try:
        state, _ = reduce_queue(queue)
    except QueueCorruption as e:
        print(f"queue corruption: {e} — refusing disposition", file=sys.stderr)
        return 2
    if args.candidate_key not in state:
        print(f"unknown candidate_key {args.candidate_key}", file=sys.stderr)
        return 2
    prior = state[args.candidate_key]
    queue_append(queue, {
        "schema_version": SCHEMA_VERSION, "ts": _now(),
        "run_id": _now(), "event": args.state,
        "candidate_key": args.candidate_key,
        "detector": prior.get("detector", ""),
        "canonical_pattern": prior.get("canonical_pattern", ""),
        "title": prior.get("title", ""),
        "evidence": prior.get("evidence", []),
        "distinct_sessions": prior.get("distinct_sessions", 0),
        "coverage": prior.get("coverage", {}),
        "note": args.note or ""})
    print(f"{args.state}: {args.candidate_key[:12]}")
    return 0


def main(argv) -> int:
    ap = argparse.ArgumentParser(prog="session_mining")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_detect = sub.add_parser("detect")
    p_detect.add_argument("--queue", default=_default_queue())
    p_detect.add_argument("--db", default=_default_db(),
                          help="session-index DB (default: the workspace "
                               "claude_docs/.session-index/sessions.db when "
                               "present)")
    p_detect.add_argument("--run-id", default=None)
    p_detect.add_argument("--workspace-root", default=None,
                          help="override workspace root for notes reading "
                               "(tests); default: resolve from cwd")
    p_detect.set_defaults(fn=cmd_detect)

    p_prop = sub.add_parser("propose")
    p_prop.add_argument("--queue", default=_default_queue())
    p_prop.add_argument("--json", action="store_true")
    p_prop.add_argument("--run-id", default=None)
    p_prop.set_defaults(fn=cmd_propose)

    p_disp = sub.add_parser("disposition")
    p_disp.add_argument("candidate_key")
    p_disp.add_argument("state", choices=list(HUMAN_EVENTS))
    p_disp.add_argument("--queue", default=_default_queue())
    p_disp.add_argument("--note", default=None)
    p_disp.set_defaults(fn=cmd_disposition)

    args = ap.parse_args(argv)
    if not args.queue:
        print("cannot resolve workspace root (.rawgentic_workspace.json not "
              "found above cwd) — pass --queue explicitly", file=sys.stderr)
        return 2
    try:
        return args.fn(args)
    except SystemExit2 as e:
        print(str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
