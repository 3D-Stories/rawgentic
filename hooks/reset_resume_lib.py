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
# How old the LATEST observation may be and still count as fresh. Review finding (High,
# confidence 0.91): checking only that observed_at advanced between two reads says nothing
# about whether that pair is itself hours old — an ancient-but-internally-advancing pair
# must still be reported stale relative to the live clock.
DEFAULT_MAX_OBSERVATION_AGE_SECONDS = 5 * 60


def _is_numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def extract_resets_at(payload, now_epoch, *, max_future_seconds=DEFAULT_MAX_FUTURE_SECONDS):
    """Extract + validate rate_limits.five_hour.{resets_at,used_percentage} from a
    statusline-shaped payload dict. Never raises on a malformed payload."""
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "malformed_payload"}
    rate_limits = payload.get("rate_limits", {})
    if not isinstance(rate_limits, dict):
        return {"ok": False, "reason": "malformed_payload"}
    five_hour = rate_limits.get("five_hour", {})
    if not isinstance(five_hour, dict):
        return {"ok": False, "reason": "malformed_payload"}
    if "resets_at" not in five_hour:
        return {"ok": False, "reason": "field_absent"}

    raw = five_hour["resets_at"]
    if not _is_numeric(raw):
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


def assess_freshness(
    prior, current, *, now_epoch,
    min_advance_seconds=DEFAULT_MIN_ADVANCE_SECONDS,
    max_observation_age_seconds=DEFAULT_MAX_OBSERVATION_AGE_SECONDS,
):
    """`prior`/`current`: {"resets_at": int, "observed_at": number}. `now_epoch` is REQUIRED
    (no default) so a caller cannot silently skip the recency bound by omission.

    Fresh iff BOTH: (1) observed_at advanced between prior and current — proves a NEW capture
    actually ran (resets_at staying constant for hours is expected, never treated as
    staleness); and (2) current's observed_at is recent relative to now_epoch — proves the
    advancing pair is not itself an old, un-refreshed capture (review finding, High)."""
    advanced = current["observed_at"] - prior["observed_at"] >= min_advance_seconds
    if not advanced:
        return {"fresh": False, "reason": "observed_at_frozen"}
    age = now_epoch - current["observed_at"]
    if age < 0 or age > max_observation_age_seconds:
        return {"fresh": False, "reason": "observed_at_stale_relative_to_now"}
    return {"fresh": True, "reason": "observed_at_advanced"}


def compute_one_shot_epoch(resets_at, lag_seconds=DEFAULT_ONE_SHOT_LAG_SECONDS):
    return resets_at + lag_seconds


def check_session_lineage(lineage_tail, session_mtimes, *, tie_window_seconds=1):
    """POINT-IN-TIME snapshot check, not a standing guarantee. `session_mtimes`:
    {session_id: mtime_epoch}. Safe only when a unique newest transcript exists and it IS the
    lineage tail — any mismatch, tie, or empty listing means launch the generated fresh -p
    prompt instead.

    Review finding (High, confidence 0.96): a `safe: True` result describes the transcript
    listing AT THE MOMENT IT WAS TAKEN. A new session created between this check and the
    actual resume subprocess launch is a real race `--continue` cannot see. Part 2 (the
    scheduler that actually launches the resume) MUST close this gap by resuming via
    `--resume <verified-lineage-tail-id>` immediately after this check succeeds, never a
    separately-resolved `--continue` — this function only tells you whether it was safe to
    do so as of this snapshot, and the caller must act on that snapshot without delay."""
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


def _is_valid_observation(obj):
    """Review finding (Medium, confidence 0.98): a schema-violating-but-valid-JSON file was
    returned as-is, so a downstream assess_freshness raised KeyError/TypeError instead of a
    clean failure. Require a dict with numeric resets_at/observed_at (bool excluded)."""
    if not isinstance(obj, dict):
        return False
    return _is_numeric(obj.get("resets_at")) and _is_numeric(obj.get("observed_at"))


def read_observation(state_path):
    p = Path(state_path)
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return obj if _is_valid_observation(obj) else None


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
        # Review finding (Medium, confidence 0.99): stderr broke the documented "machine JSON
        # always on stdout" contract — a caller parsing stdout got nothing on failure.
        print(json.dumps({"ok": False, "reason": "no_observation"}))
        return 2
    print(json.dumps(result))
    return 0


def _cmd_assess_freshness(args):
    try:
        prior = json.loads(Path(args.prior_file).read_text())
        current = json.loads(Path(args.current_file).read_text())
    except (OSError, json.JSONDecodeError):
        print(json.dumps({"fresh": False, "reason": "malformed_observation"}))
        return 1
    if not (_is_valid_observation(prior) and _is_valid_observation(current)):
        print(json.dumps({"fresh": False, "reason": "malformed_observation"}))
        return 1
    now_epoch = args.now if args.now is not None else int(datetime.now(timezone.utc).timestamp())
    result = assess_freshness(
        prior, current, now_epoch=now_epoch,
        min_advance_seconds=args.min_advance_seconds,
        max_observation_age_seconds=args.max_observation_age_seconds,
    )
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
    p_fresh.add_argument("--now", type=int, default=None, help="ISO/epoch override (testing)")
    p_fresh.add_argument("--min-advance-seconds", type=float, default=DEFAULT_MIN_ADVANCE_SECONDS)
    p_fresh.add_argument(
        "--max-observation-age-seconds", type=float, default=DEFAULT_MAX_OBSERVATION_AGE_SECONDS,
    )
    p_fresh.set_defaults(func=_cmd_assess_freshness)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
