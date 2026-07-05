"""usage_capture — populate run-record token/cost from Claude Code session logs (#189).

The Tier-2 run-record has an OPTIONAL ``usage`` object (input/output tokens, cost,
model_mix). #155/#172 added the field and its validator but nothing ever populated
it, so it was null in all 24 records — which made #162's yield-per-token gate
incomputable. This module fills that gap by parsing the Claude Code session
transcript directly (the same source ``npx ccusage`` reads), so capture is
stdlib-only, deterministic, and fixture-testable — no network, no npx.

Each ``type:"assistant"`` line of a session JSONL carries ``message.usage``
(``input_tokens``, ``cache_creation_input_tokens``, ``cache_read_input_tokens``,
``output_tokens``) and ``message.model``. We sum per model into ``model_mix`` (the
PRIMARY metric) and total across models, excluding the ``<synthetic>`` pseudo-model
(injected/system turns — not billable inference). ``cost_estimate_usd`` is a
SECONDARY rate-card estimate (most runs are subscription-billed, so cost is a
cross-check, not the number to trend on).

Non-vacuity is the whole point (see #189 AC5): a session whose usage sums to zero
tokens raises ``NoUsageData`` and ``capture_usage`` returns ``None`` — capture can
NEVER emit ``capture_status="captured"`` with null/zero tokens. The guard is on the
token SUM, not the block count: a real aborted/degenerate turn carries a usage dict
whose fields are all zero, and blessing that as "captured 0/0" would be exactly the
#155 meaningless-telemetry failure. The paired validator change in work_summary.py
(same PR) enforces the same invariant at the schema level (rejects captured+null/zero).

Best-effort, never crash: capture reads the CURRENT session's log, which may be
mid-write, so parsing tolerates malformed and non-UTF-8 bytes and ``capture_usage``
returns ``None`` on any failure rather than propagating to the Step 16 summary.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Final, Optional

# Session-id format: Claude Code uses UUIDs; accept the conservative superset of
# hex + hyphen only. This is ALSO the path-traversal guard — anything with a
# slash, "..", space, or punctuation is rejected before it ever touches the
# filesystem (find_session_file globs on it).
_SESSION_ID_RE: Final[re.Pattern] = re.compile(r"[A-Za-z0-9-]+")  # used with fullmatch

# The pseudo-model for injected/system turns — not real inference, excluded from
# both totals and model_mix.
SYNTHETIC_MODEL: Final[str] = "<synthetic>"

# Rate card — USD per 1,000,000 tokens, per category. These are rate-card
# ESTIMATES as of 2026-07 for a cross-check figure, NOT authoritative billing;
# update when Anthropic pricing changes. An unknown model contributes 0 to cost
# (honest best-effort) while its tokens are still counted in model_mix/totals.
RATE_CARD: Final[dict[str, dict[str, float]]] = {
    "claude-opus-4-8":   {"input": 15.0, "cache_write": 18.75, "cache_read": 1.50, "output": 75.0},
    "claude-opus-4-7":   {"input": 15.0, "cache_write": 18.75, "cache_read": 1.50, "output": 75.0},
    "claude-sonnet-5":   {"input": 3.0,  "cache_write": 3.75,  "cache_read": 0.30, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "cache_write": 1.0, "cache_read": 0.08, "output": 4.0},
    "claude-fable-5":    {"input": 3.0,  "cache_write": 3.75,  "cache_read": 0.30, "output": 15.0},
}


class NoUsageData(Exception):
    """A session file yielded zero assistant-usage blocks. Raised (rather than
    returning a silent all-zero/all-null usage) so capture can never masquerade
    an empty parse as a real 'captured' measurement — the #155 failure mode."""


def _validate_session_id(session_id: str) -> None:
    """Reject anything that isn't a bare id token — this is the traversal guard.
    ``fullmatch`` (not ``match``) so a trailing newline can't slip through the ``$``."""
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        raise ValueError(f"invalid session id: {session_id!r}")


def default_projects_dir() -> Path:
    """Where Claude Code stores per-session transcripts on this host."""
    return Path.home() / ".claude" / "projects"


def find_session_file(session_id: str, projects_dir) -> Optional[Path]:
    """Locate ``<session_id>.jsonl`` under ``projects_dir`` (searched recursively,
    since Claude Code namespaces by project subdir). Returns None if not found.
    Raises ValueError on a malformed session id (traversal guard), and refuses any
    resolved path that escapes ``projects_dir`` (defense in depth)."""
    _validate_session_id(session_id)
    base = Path(projects_dir).resolve()
    candidates = [base / f"{session_id}.jsonl", *base.glob(f"**/{session_id}.jsonl")]
    prefix = str(base) + os.sep
    for cand in candidates:
        rc = cand.resolve()
        if rc.is_file() and str(rc).startswith(prefix):
            return rc
    return None


def _rate(model: str, category: str) -> float:
    card = RATE_CARD.get(model)
    return card[category] if card else 0.0


def parse_session_jsonl(path) -> dict:
    """Parse one session transcript into a strict 5-key ``usage`` dict.

    ``input_tokens`` totals ALL input categories (fresh + cache-create +
    cache-read) — the tokens the model actually processed; ``output_tokens`` sums
    outputs. ``model_mix`` carries per-model {input,output} totals. ``cost_estimate_usd``
    applies per-category rates. ``wall_clock_s`` is left None (the orchestrator owns
    wall-clock time). Malformed lines and lines without usage are skipped; a file
    with zero usable usage blocks raises NoUsageData (non-vacuity)."""
    mix: dict[str, dict[str, int]] = {}
    cost = 0.0
    # errors="replace": the current session's log may be read mid-write, so a
    # split multibyte char at EOF must degrade gracefully, never raise.
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue  # malformed line — skip, keep going
            if not isinstance(rec, dict) or rec.get("type") != "assistant":
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            model = msg.get("model")
            if not isinstance(usage, dict) or not isinstance(model, str):
                continue
            if model == SYNTHETIC_MODEL:
                continue  # not billable inference
            fresh = _int(usage.get("input_tokens"))
            cwrite = _int(usage.get("cache_creation_input_tokens"))
            cread = _int(usage.get("cache_read_input_tokens"))
            out = _int(usage.get("output_tokens"))
            in_total = fresh + cwrite + cread
            m = mix.setdefault(model, {"input_tokens": 0, "output_tokens": 0})
            m["input_tokens"] += in_total
            m["output_tokens"] += out
            cost += (fresh * _rate(model, "input")
                     + cwrite * _rate(model, "cache_write")
                     + cread * _rate(model, "cache_read")
                     + out * _rate(model, "output")) / 1_000_000
    total_in = sum(m["input_tokens"] for m in mix.values())
    total_out = sum(m["output_tokens"] for m in mix.values())
    # Non-vacuity: guard on input tokens being POSITIVE, not on block count. Every
    # real inference turn processes input (the prompt, plus cache after turn 1), so
    # total_in <= 0 means the parse found nothing real — it must NOT be blessed as a
    # measurement (the #155 failure mode, and its zero-token variant). Guarding on
    # input>0 (not just sum>0) also blocks a captured dict with input 0 / output N.
    if total_in <= 0:
        raise NoUsageData(f"no positive input tokens in {path}")
    return {
        "input_tokens": total_in,
        "output_tokens": total_out,
        "cost_estimate_usd": round(cost, 6),
        "wall_clock_s": None,
        "model_mix": mix,
    }


def _int(v) -> int:
    """Coerce a token count to a non-negative int. Accepts int OR float (a JSON
    number may be emitted as a float) — floats are truncated, not silently
    dropped to 0, which would under-count. Rejects bools, negatives, non-numbers."""
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)) and v >= 0:
        return int(v)
    return 0


def capture_usage(session_id: str, projects_dir=None) -> Optional[dict]:
    """Capture usage for one session id. Returns the usage dict tagged
    ``capture_status="captured"`` on success, or None when the session file is
    missing or has no usage — NEVER a captured dict with null tokens."""
    projects_dir = Path(projects_dir) if projects_dir else default_projects_dir()
    try:
        path = find_session_file(session_id, projects_dir)
    except ValueError:
        return None
    if path is None:
        return None
    try:
        usage = parse_session_jsonl(path)
    except (NoUsageData, OSError):
        return None
    usage["capture_status"] = "captured"
    return usage


def backfill_record(rec: dict, projects_dir=None) -> str:
    """Backfill one run-record's usage in place. Returns the action taken:
    ``skip-no-usage`` (no usage object) / ``skip-has-data`` (already captured or
    non-null) / ``recovered`` (re-captured from a session id the record carries) /
    ``unrecoverable`` (null usage with no correlator — the honest marker per AC2).

    Historical records carry no session id, so they mark unrecoverable. A record
    that DOES carry ``session_id`` (or ``usage.session_id``) can be re-captured —
    this is the recoverable path the known-value backfill test exercises."""
    usage = rec.get("usage")
    if not isinstance(usage, dict):
        return "skip-no-usage"
    if usage.get("capture_status") == "captured" or usage.get("input_tokens") is not None:
        return "skip-has-data"
    sid = rec.get("session_id") or usage.get("session_id")
    if sid:
        cap = capture_usage(sid, projects_dir=projects_dir)
        if cap is not None:
            # preserve an existing wall_clock_s (capture doesn't know wall time)
            if usage.get("wall_clock_s") is not None:
                cap["wall_clock_s"] = usage["wall_clock_s"]
            rec["usage"] = cap
            return "recovered"
    usage["capture_status"] = "unrecoverable"
    return "unrecoverable"


def backfill_store(records_path, projects_dir=None) -> dict:
    """Backfill every record in a JSONL store IN PLACE (hand-edit each line, per the
    append-only store's amend rule). Returns action counts. Non-record lines are
    preserved verbatim so a malformed line is never silently dropped."""
    path = Path(records_path)
    out_lines: list[str] = []
    stats = {"recovered": 0, "unrecoverable": 0, "skip-has-data": 0,
             "skip-no-usage": 0, "malformed": 0}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            out_lines.append(line)
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            stats["malformed"] += 1
            out_lines.append(line)  # preserve, don't drop
            continue
        if not isinstance(rec, dict):
            # valid JSON but not a record object (e.g. a bare list) — preserve
            # verbatim rather than crash the whole rewrite on rec.get(...)
            stats["malformed"] += 1
            out_lines.append(line)
            continue
        action = backfill_record(rec, projects_dir=projects_dir)
        stats[action] += 1
        out_lines.append(json.dumps(rec, separators=(",", ":")))
    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Capture run-record usage from session logs (#189)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    pc = sub.add_parser("capture", help="print the usage JSON for a session id")
    pc.add_argument("--session-id", required=True)
    pc.add_argument("--projects-dir", default=None)
    pb = sub.add_parser("backfill", help="backfill usage in a run-records JSONL store")
    pb.add_argument("--records", required=True)
    pb.add_argument("--projects-dir", default=None)
    args = parser.parse_args(argv)
    if args.cmd == "capture":
        usage = capture_usage(args.session_id, projects_dir=args.projects_dir)
        if usage is None:
            print(json.dumps({"capture_status": "unavailable"}))
            return 0
        print(json.dumps(usage))
        return 0
    if args.cmd == "backfill":
        stats = backfill_store(args.records, projects_dir=args.projects_dir)
        print(json.dumps(stats))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
