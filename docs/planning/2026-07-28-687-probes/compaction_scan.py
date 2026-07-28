#!/usr/bin/env python3
"""Probe 8 for #687 — where does Claude Code's auto-compaction actually fire?

Answers the part of #654's Q4 that is answerable from data already on disk: walk
every session transcript, compute the in-context total for each `message.usage`
row, and report both the sharp falls and — the load-bearing figure — the observed
CEILING per window size.

Why the ceiling and not the drops: a sharp fall is not necessarily an
auto-compaction. A `/clear`, a manual `/compact`, or a new cache prefix produces
an identical discontinuity, so the drop distribution proves nothing about onset.
But a session OBSERVED at 99.8% of its window proves compaction did not fire
below that, which is exactly what a threshold needs to sit under.

Run:  python3 docs/planning/2026-07-28-687-probes/compaction_scan.py
"""
import argparse
import glob
import json
import os

IN_CONTEXT_FIELDS = ("input_tokens", "cache_creation_input_tokens",
                     "cache_read_input_tokens")


def usage_total(usage) -> int:
    if not isinstance(usage, dict):
        return 0
    total = 0
    for field in IN_CONTEXT_FIELDS:
        value = usage.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            total += value
    return total


def scan_one(path, drop_ratio, floor):
    """(peak, [(pre, post), …]) for one transcript."""
    previous, peak, drops = None, 0, []
    with open(path, errors="replace") as handle:
        for line in handle:
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                continue
            message = obj.get("message") if isinstance(obj, dict) else None
            if not isinstance(message, dict):
                continue
            total = usage_total(message.get("usage"))
            if total <= 0:
                continue
            peak = max(peak, total)
            if previous and total < previous * drop_ratio and previous > floor:
                drops.append((previous, total))
            previous = total
    return peak, drops


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects",
                        default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--min-size", type=int, default=200_000)
    parser.add_argument("--drop-ratio", type=float, default=0.6)
    parser.add_argument("--drop-floor", type=int, default=50_000)
    parser.add_argument("--window", type=int, default=1_000_000)
    args = parser.parse_args()

    scanned, sessions = 0, []
    for path in glob.glob(os.path.join(args.projects, "*", "*.jsonl")):
        try:
            if os.path.getsize(path) < args.min_size:
                continue
            scanned += 1
            peak, drops = scan_one(path, args.drop_ratio, args.drop_floor)
        except OSError:
            continue
        if drops:
            sessions.append((peak, drops, os.path.basename(path)[:12]))

    all_drops = [d for _, drops, _ in sessions for d in drops]
    print(f"scanned {scanned} transcripts >= {args.min_size} bytes")
    print(f"{len(all_drops)} sharp drops across {len(sessions)} sessions")

    if all_drops:
        ceiling, _, where = max(
            ((pre, post, name) for peak, drops, name in sessions
             for pre, post in drops), key=lambda row: row[0])
        print(f"highest reading observed: {ceiling:,} tokens "
              f"({ceiling / args.window * 100:.1f}% of {args.window:,}) in {where}")
        print("\ntop 14 pre-drop readings:")
        for pre, post, name in sorted(
                ((pre, post, name) for peak, drops, name in sessions
                 for pre, post in drops), reverse=True)[:14]:
            print(f"  {name}  {pre:>9,} -> {post:>9,}   "
                  f"{pre / args.window * 100:5.1f}% of {args.window:,}")

    low = [s for s in sessions if s[0] < 250_000]
    print(f"\nsessions with a drop whose peak is under 250k: {len(low)}")
    if not low:
        print("  => the 200k window is UNMEASURABLE from this corpus; the "
              "200k onset stays unconfirmed (see the design's honest limits).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
