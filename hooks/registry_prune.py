"""Prune stale entries from a session registry (#7 — housekeeping).

`claude_docs/session_registry.jsonl` grows one entry per session forever
(session-start dedups by session_id but never prunes by age). This prunes entries
whose `started` timestamp is older than a configurable TTL (default 30 days,
env `RAWGENTIC_REGISTRY_TTL_DAYS`). Fail-safe: a line that can't be parsed or has no
datable `started` is KEPT — a prune tool must never silently drop data it can't age.

Pure core (`prune_registry`) + a CLI the `rawgentic:housekeeping` skill shells out to.
WAL cleanup is out of scope: session-start already rotates any WAL >5000 lines.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from atomic_write_lib import atomic_write_text

DEFAULT_TTL_DAYS = 30
TTL_ENV = "RAWGENTIC_REGISTRY_TTL_DAYS"


def ttl_days(env: dict | None = None) -> int:
    """Resolve the TTL in days from the environment. Fail-safe: a missing, non-int,
    or < 1 value falls back to the 30-day default (never prunes with a bogus TTL)."""
    raw = (env if env is not None else os.environ).get(TTL_ENV)
    if raw is None:
        return DEFAULT_TTL_DAYS
    try:
        v = int(str(raw).strip())
    except (ValueError, TypeError):
        return DEFAULT_TTL_DAYS
    return v if v >= 1 else DEFAULT_TTL_DAYS


def _parse_started(obj) -> datetime | None:
    """Return a tz-aware UTC datetime for the entry's `started`, or None if undatable."""
    if not isinstance(obj, dict):
        return None
    s = obj.get("started")
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _atomic_write(path: str, text: str) -> None:
    """Write `text` to `path` via a temp file + os.replace so a crash never leaves the
    registry truncated/half-written (mirrors hooks/notes-size-handler.py's discipline).
    ponytail: os.replace closes the truncate-corruption window. Residual ceiling (NOT
    closed here): the registry's appenders are bash `printf ... >>` in skills/switch +
    skills/new-project, which take no lock — so a session that BINDS in the window between
    this prune's read and its os.replace can be dropped (its line went to the old inode).
    Accepted for #7: opt-in manual tool, rarely run concurrently with a bind, and the lost
    datum is a low-value registry line ("no correctness impact"); fully closing it needs
    cooperative locking added to those two bash append sites — out of this issue's scope."""
    atomic_write_text(path, text, prefix=".registry_prune.")


def prune_registry(text: str, now: datetime, ttl: int = DEFAULT_TTL_DAYS) -> tuple[str, dict]:
    """Pure prune. Given the registry TEXT, a tz-aware `now`, and a TTL in days, return
    `(new_text, stats)` with `stats = {kept, removed, undatable}`. An entry is REMOVED only
    when its `started` parses AND is strictly older than `now - ttl`. Blank lines are
    dropped; malformed / undatable lines are KEPT (counted in `undatable`, also in `kept`)."""
    cutoff = now - timedelta(days=ttl)
    kept_lines: list[str] = []
    removed = 0
    undatable = 0
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            kept_lines.append(s)
            undatable += 1
            continue
        dt = _parse_started(obj)
        if dt is None:
            kept_lines.append(s)
            undatable += 1
            continue
        if dt < cutoff:
            removed += 1
            continue
        kept_lines.append(s)
    new_text = ("\n".join(kept_lines) + "\n") if kept_lines else ""
    return new_text, {"kept": len(kept_lines), "removed": removed, "undatable": undatable}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="registry_prune",
                                description="Prune stale session-registry entries (#7).")
    p.add_argument("--registry", required=True, help="path to session_registry.jsonl")
    p.add_argument("--ttl-days", type=int, default=None,
                   help=f"override the TTL (else ${TTL_ENV} or {DEFAULT_TTL_DAYS})")
    p.add_argument("--now", default=None, help="ISO timestamp override (testing)")
    p.add_argument("--dry-run", action="store_true", help="report only; do not rewrite")
    a = p.parse_args(argv)

    ttl = a.ttl_days if a.ttl_days is not None else ttl_days()
    if ttl < 1:
        print("--ttl-days must be >= 1", file=sys.stderr)
        return 2
    if a.now:
        try:
            now = datetime.fromisoformat(a.now.replace("Z", "+00:00"))
        except ValueError:
            print(f"--now is not an ISO timestamp: {a.now}", file=sys.stderr)
            return 2
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    else:
        now = datetime.now(timezone.utc)

    try:
        with open(a.registry, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        print(f"registry not found: {a.registry} (nothing to prune)")
        return 0
    except OSError as exc:
        print(f"cannot read registry {a.registry}: {exc}", file=sys.stderr)
        return 2

    new_text, stats = prune_registry(text, now, ttl)
    print(f"session-registry prune (ttl={ttl}d): kept {stats['kept']}, "
          f"removed {stats['removed']}, undatable-kept {stats['undatable']}")
    if a.dry_run:
        print("(dry-run — no changes written)")
    elif stats["removed"] > 0:
        _atomic_write(a.registry, new_text)   # temp + os.replace — never truncate-in-place
        print(f"rewrote {a.registry}")
    else:
        print("nothing to remove — registry unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
