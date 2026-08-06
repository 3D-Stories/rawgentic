#!/usr/bin/env python3
"""Measured resets_at capture + freshness + session-lineage identity check (#586, Part 1).

Part of the M4 wave's resume rewrite (docs/planning/2026-08-05-871-m4-session-continuity-away-mode.md
§3.3): the durable overnight launcher must stop pinning a session ID and instead arm a one-shot
resume at the MEASURED `rate_limits.five_hour.resets_at` from the statusline bridge, then resume
via `claude -p --continue` behind a session-lineage identity check.

This module ships the pure, testable core only (extraction/validation/freshness/lineage-check).
The scheduler wiring, the `overnight-resume.sh` template rewrite, and the watchdog-to-reconciler
demotion are workspace-root changes outside any git repo and ship in a follow-up PR (D250).

Freshness contract (measured lesson, epic-871-m4-wave-log.md "herdr tokens STALE on this host"):
a `resets_at` value that stays IDENTICAL across two reads is the EXPECTED steady state for a
5-hour window — it must never be treated as staleness. What actually failed on this host was the
underlying capture never refreshing at all. So freshness is asserted on `observed_at` advancing
between reads, never on `resets_at` changing.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_write_lib import atomic_write_text  # noqa: E402

DEFAULT_ONE_SHOT_LAG_SECONDS = 60
DEFAULT_MAX_FUTURE_SECONDS = 6 * 3600
DEFAULT_MIN_ADVANCE_SECONDS = 1


def extract_resets_at(payload, now_epoch, *, max_future_seconds=DEFAULT_MAX_FUTURE_SECONDS):
    """Extract + validate rate_limits.five_hour.{resets_at,used_percentage} from a
    statusline-shaped payload dict. Never raises on a malformed payload."""
    five_hour = (payload or {}).get("rate_limits", {}).get("five_hour", {})
    if not isinstance(five_hour, dict) or "resets_at" not in five_hour:
        return {"ok": False, "reason": "field_absent"}

    raw = five_hour["resets_at"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return {"ok": False, "reason": "not_numeric"}
    resets_at = int(raw)

    if resets_at <= now_epoch:
        return {"ok": False, "reason": "not_in_future"}
    if resets_at - now_epoch > max_future_seconds:
        return {"ok": False, "reason": "too_far_future"}

    used_percentage = five_hour.get("used_percentage")
    if used_percentage is not None and not isinstance(used_percentage, bool):
        try:
            used_percentage = float(used_percentage)
        except (TypeError, ValueError):
            used_percentage = None
    else:
        used_percentage = None

    return {"ok": True, "resets_at": resets_at, "used_percentage": used_percentage}


def assess_freshness(prior, current, *, min_advance_seconds=DEFAULT_MIN_ADVANCE_SECONDS):
    """`prior`/`current`: {"resets_at": int, "observed_at": number}. Fresh iff the
    observation timestamp itself advanced — resets_at staying constant is expected."""
    advanced = current["observed_at"] - prior["observed_at"] >= min_advance_seconds
    if advanced:
        return {"fresh": True, "reason": "observed_at_advanced"}
    return {"fresh": False, "reason": "observed_at_frozen"}


def compute_one_shot_epoch(resets_at, lag_seconds=DEFAULT_ONE_SHOT_LAG_SECONDS):
    return resets_at + lag_seconds


def check_session_lineage(lineage_tail, session_mtimes, *, tie_window_seconds=1):
    """Conservative --continue safety check. `session_mtimes`: {session_id: mtime_epoch}.
    Safe only when a unique newest transcript exists and it IS the lineage tail — any
    mismatch, tie, or empty listing means launch the generated fresh -p prompt instead."""
    if not session_mtimes:
        return {"safe": False, "reason": "no_transcripts"}

    ordered = sorted(session_mtimes.items(), key=lambda kv: kv[1], reverse=True)
    newest_id, newest_mtime = ordered[0]

    if len(ordered) > 1:
        _, second_mtime = ordered[1]
        if newest_mtime - second_mtime <= tie_window_seconds:
            return {"safe": False, "reason": "tie_within_staleness_window"}

    if newest_id != lineage_tail:
        return {"safe": False, "reason": "newest_transcript_not_lineage_tail"}

    return {"safe": True, "reason": "newest_transcript_matches_lineage_tail"}


def persist_observation(state_path, resets_at, observed_at, used_percentage=None):
    text = json.dumps({
        "resets_at": resets_at,
        "observed_at": observed_at,
        "used_percentage": used_percentage,
    })
    atomic_write_text(state_path, text, mkdir=True, prefix=".reset-resume-", suffix=".tmp")


def read_observation(state_path):
    p = Path(state_path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _cmd_extract(args):
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print(json.dumps({"ok": False, "reason": "unparseable_stdin"}))
        return 1
    now_epoch = args.now if args.now is not None else int(datetime.now(timezone.utc).timestamp())
    result = extract_resets_at(payload, now_epoch, max_future_seconds=args.max_future_seconds)
    print(json.dumps(result))
    return 0 if result["ok"] else 1


def _cmd_one_shot_epoch(args):
    print(json.dumps({"one_shot_epoch": compute_one_shot_epoch(args.resets_at, args.lag_seconds)}))
    return 0


def _cmd_persist(args):
    persist_observation(args.state_path, args.resets_at, args.observed_at, args.used_percentage)
    return 0


def _cmd_read(args):
    result = read_observation(args.state_path)
    if result is None:
        print(json.dumps({"ok": False, "reason": "no_observation"}), file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


def _cmd_assess_freshness(args):
    prior = json.loads(Path(args.prior_file).read_text())
    current = json.loads(Path(args.current_file).read_text())
    result = assess_freshness(prior, current, min_advance_seconds=args.min_advance_seconds)
    print(json.dumps(result))
    return 0 if result["fresh"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser("extract", help="Extract resets_at from a statusline payload on stdin")
    p_extract.add_argument("--now", type=int, default=None)
    p_extract.add_argument("--max-future-seconds", type=int, default=DEFAULT_MAX_FUTURE_SECONDS)
    p_extract.set_defaults(func=_cmd_extract)

    p_oneshot = sub.add_parser("one-shot-epoch", help="Compute resets_at + lag")
    p_oneshot.add_argument("--resets-at", type=int, required=True)
    p_oneshot.add_argument("--lag-seconds", type=int, default=DEFAULT_ONE_SHOT_LAG_SECONDS)
    p_oneshot.set_defaults(func=_cmd_one_shot_epoch)

    p_persist = sub.add_parser("persist", help="Persist a reset observation to a state file")
    p_persist.add_argument("--state-path", required=True)
    p_persist.add_argument("--resets-at", type=int, required=True)
    p_persist.add_argument("--observed-at", type=float, required=True)
    p_persist.add_argument("--used-percentage", type=float, default=None)
    p_persist.set_defaults(func=_cmd_persist)

    p_read = sub.add_parser("read", help="Read a persisted reset observation")
    p_read.add_argument("--state-path", required=True)
    p_read.set_defaults(func=_cmd_read)

    p_fresh = sub.add_parser("assess-freshness", help="Compare two observation JSON files")
    p_fresh.add_argument("--prior-file", required=True)
    p_fresh.add_argument("--current-file", required=True)
    p_fresh.add_argument("--min-advance-seconds", type=float, default=DEFAULT_MIN_ADVANCE_SECONDS)
    p_fresh.set_defaults(func=_cmd_assess_freshness)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
