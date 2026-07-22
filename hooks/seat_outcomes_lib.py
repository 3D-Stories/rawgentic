#!/usr/bin/env python3
"""#473 (W11, epic #475) — the I3 seat-outcomes sidecar (baselines + advisory alerts).

The executor writes per-dispatch Observations to an EPHEMERAL, git-excluded
``.rawgentic/runs/<run-id>/routing-audit.jsonl``. This module harvests those into a
DURABLE, committed ``docs/measurements/seat-outcomes.jsonl`` (the AC-I3 sidecar) at run
end, computes rolling per-seat x per-model baselines (AC-K2), and evaluates advisory alerts
(AC-K3 — never a gate). Thresholds are per-project config (AC-K5 / #446).

Design: docs/planning/2026-07-22-473-telemetry-baselines-alerts-design.md (r7).

Structure: pure core + thin ``main(argv)`` CLI (the ``registry_prune``/``work_summary``
exemplar). Fail-CLOSED on every retention/validation decision (committed telemetry): a value
that cannot be proven safe is redacted to null, an invalid row is skipped loudly, a same-key
digest conflict aborts before any write. Fail-OPEN only on WHETHER telemetry runs, never on
WHAT it commits — a telemetry failure must never block Step 16 (the caller wraps run-end
loud-log-and-continue).

Lives in hooks/ (NOT phase_executor/src) — the I3 aggregation is host machinery; the
``test_i3_aggregation_docs`` guards forbid a seat-outcomes writer under the package tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


# --- phase_executor bootstrap (the executor_routing_lib.py:1546 precedent) ------------------
def _ensure_pe_importable() -> None:
    """Put ``phase_executor/src`` on sys.path so the plain repo checkout imports the package."""
    here = Path(__file__).resolve().parent
    src = str(here.parent / "phase_executor" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


_ensure_pe_importable()
from phase_executor.contract import (  # noqa: E402  # pylint: disable=no-name-in-module
    canonicalize_model_id, models_match)

# --- constants -----------------------------------------------------------------------------
SCHEMA_VERSION = "1"
DEFAULT_STORE_RELPATH = ("docs", "measurements", "seat-outcomes.jsonl")

# Enums mirrored EXACTLY from phase_executor/src/phase_executor/schemas/observation.schema.json
# (a drift test pins these against the live schema). An off-vocab value redacts to null.
PARSE_STATUS_ENUM = frozenset({
    "ok", "nonzero_exit", "timeout", "launch_error", "parse_error", "no_response",
    "identity_failure", "usage_unavailable", "harness_error"})
PROMOTION_STATUS_ENUM = frozenset({"not_attempted", "promoted", "not_promoted", "failed"})
CANARY_VERDICT_ENUM = frozenset({"pass", "refuse"})

# Identifier grammar: no spaces / @ / backticks / brackets / quotes. Path-shape rejection
# (below) runs AFTER this, so a grammar-passing path-shaped value is still redacted.
_IDENT_RE = re.compile(r"^[A-Za-z0-9._:/-]+$")
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_FALLBACK_RE = re.compile(r"^fallback from (?P<from>[^:]+): (?P<status>.+)$")

# The row's committed key set (unknown keys rejected for schema_version 1).
_ROW_KEYS = frozenset({
    "schema_version", "run_id", "attempt_id", "correlation_id", "issue", "seat", "engine",
    "parse_status", "requested_model", "actual_model", "model", "models_match", "lane",
    "fallback", "usage", "timing_ms", "queued_ms", "exit_code", "timed_out", "canary_verdict",
    "work_product_ref", "hook_denials", "budget", "experiment_id", "arm", "recorded_at"})

# Fields NEVER copied from an Observation into a committed row (value-sweep test enforces).
_DENYLIST = frozenset({
    "worktree_path", "tmux_session", "raw_capture_path", "worktree_id", "credential_ref",
    "prompt_hash", "context_hashes", "parsed_payload", "fallback_reason"})


# --- redaction primitives ------------------------------------------------------------------
def _is_path_shaped(s: str) -> bool:
    """True if a grammar-passing string looks like a filesystem path (→ must be redacted)."""
    if s.startswith("/") or s.startswith("\\"):
        return True
    if _WIN_DRIVE_RE.match(s):
        return True
    if s.startswith("//"):  # UNC / posix double-slash
        return True
    segs = s.split("/")
    if ".." in segs or "." in segs:  # any relative-path segment (leading or interior)
        return True
    if any(seg == "" for seg in segs[1:]):  # interior empty segment
        return True
    if len(segs) > 2:  # 2+ separators = path-shaped (one internal slash is legit provider/model)
        return True
    return False


def _clean_ident(value, cap: int):
    """Grammar + path-shape gate for a free identity field. Returns (clean|None, redacted)."""
    if value is None:
        return None, False
    if not isinstance(value, str) or len(value) > cap or not _IDENT_RE.match(value):
        return None, True
    if _is_path_shaped(value):
        return None, True
    return value, False


def _enum(value, allowed):
    """Enum gate: return the value iff it is a member, else (None, redacted)."""
    if value is None:
        return None, False
    if isinstance(value, str) and value in allowed:
        return value, False
    return None, True


def _int_or_none(value):
    """A real (non-bool) int ≥0, else None."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _num_or_none(value):
    """A finite non-negative number (int/float, not bool), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")) or value < 0:  # NaN/Inf/neg
        return None
    return value


def _clean_usage(usage):
    if not isinstance(usage, dict):
        return None
    return {
        "input": _int_or_none(usage.get("input")),
        "output": _int_or_none(usage.get("output")),
        "cached": _int_or_none(usage.get("cached")),
        "cost_proxy": _num_or_none(usage.get("cost_proxy")),
    }


def _clean_budget(budget):
    if not isinstance(budget, dict):
        return None
    return {"reserved_usd": _num_or_none(budget.get("reserved_usd")),
            "spent_usd": _num_or_none(budget.get("spent_usd"))}


def _clean_lane(lane, counter):
    if not isinstance(lane, dict):
        return None
    out = {}
    for k in ("provider", "transport", "auth_mode", "pool"):
        v, red = _clean_ident(lane.get(k), 64)
        out[k] = v
        if red:
            counter[0] += 1
    return out


def _clean_fallback(reason, counter):
    """Structured fallback — the raw reason string is NEVER committed."""
    if reason is None:
        return None
    if not isinstance(reason, str):
        counter[0] += 1
        return {"kind": "other"}
    m = _FALLBACK_RE.match(reason)
    if not m:
        return {"kind": "other"}
    fm, red_fm = _clean_ident(m.group("from"), 120)
    st, _ = _enum(m.group("status"), PARSE_STATUS_ENUM)
    if red_fm:
        counter[0] += 1
    return {"kind": "model_fallback", "from_model": fm, "parse_status": st}


def _clean_work_product(wp, counter):
    if not isinstance(wp, dict):
        return None
    out = {}
    for k in ("content_tree_sha", "base_sha", "head_sha"):
        v = wp.get(k)
        out[k] = v if isinstance(v, str) and _SHA_RE.match(v) else None
        if v is not None and out[k] is None:
            counter[0] += 1
    st, red = _enum(wp.get("promotion_status"), PROMOTION_STATUS_ENUM)
    out["promotion_status"] = st
    if red:
        counter[0] += 1
    return out


# --- row derivation ------------------------------------------------------------------------
def derive_seat_outcome(obs: dict, *, issue, recorded_at: str = None) -> dict:
    """Derive one committed seat-outcome row from an executor Observation dict.

    ``issue`` is sourced ONLY from a validated run-record (caller passes an int > 0 or None);
    it is NEVER parsed from correlation text. ``recorded_at`` defaults to a caller-supplied
    ISO-8601-UTC stamp (the CLI stamps it; excluded from the content digest).
    """
    counter = [0]  # mutable redaction tally (path/grammar/enum failures)

    def ident(key, cap):
        v, red = _clean_ident(obs.get(key), cap)
        if red:
            counter[0] += 1
        return v

    actual = obs.get("actual_model")
    canon = canonicalize_model_id(actual) if actual is not None else ""
    model = canon or None
    proc = obs.get("process") or {}
    parse_status, red_ps = _enum(obs.get("parse_status"), PARSE_STATUS_ENUM)
    if red_ps:
        counter[0] += 1
    canary = obs.get("canary_result")
    canary_verdict, red_cv = (None, False)
    if isinstance(canary, dict):
        canary_verdict, red_cv = _enum(canary.get("verdict"), CANARY_VERDICT_ENUM)
    if red_cv:
        counter[0] += 1

    row = {
        "schema_version": SCHEMA_VERSION,
        "run_id": ident("run_id", 120),
        "attempt_id": ident("attempt_id", 120),
        "correlation_id": ident("correlation_id", 128),
        "issue": issue if isinstance(issue, int) and not isinstance(issue, bool) and issue > 0 else None,
        "seat": ident("seat", 64),
        "engine": ident("engine", 64),
        "parse_status": parse_status,
        "requested_model": ident("requested_model", 120),
        "actual_model": ident("actual_model", 120),
        "model": model,
        "models_match": (models_match(obs.get("requested_model"), actual)
                         if actual is not None else None),
        "lane": _clean_lane(obs.get("dispatched_lane"), counter),
        "fallback": _clean_fallback(obs.get("fallback_reason"), counter),
        "usage": _clean_usage(obs.get("usage")),
        "timing_ms": _int_or_none(obs.get("timing_ms")),
        "queued_ms": _int_or_none(obs.get("queued_ms")),
        "exit_code": (proc.get("exit_code")
                      if isinstance(proc.get("exit_code"), int)
                      and not isinstance(proc.get("exit_code"), bool) else None),
        "timed_out": proc.get("timed_out") if isinstance(proc.get("timed_out"), bool) else None,
        "canary_verdict": canary_verdict,
        "work_product_ref": _clean_work_product(obs.get("work_product"), counter),
        "hook_denials": _int_or_none(obs.get("hook_denials")),
        "budget": _clean_budget(obs.get("budget")),
        "experiment_id": None,
        "arm": None,
        "recorded_at": recorded_at,
        "redacted_fields": counter[0],
    }
    return row


# --- content digest ------------------------------------------------------------------------
def content_digest(row: dict) -> str:
    """SHA-256 over the row EXCLUDING recorded_at, issue, and redacted_fields (the last two
    are attribution/telemetry-only and may legitimately differ between a record-less recovery
    harvest and run-end). Canonical serialization: sort_keys, tight separators, UTF-8."""
    payload = {k: v for k, v in row.items()
               if k not in ("recorded_at", "issue", "redacted_fields")}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --- validator (strict, fail-closed) -------------------------------------------------------
def validate_seat_outcome(row) -> list:
    """Return a list of human-readable errors ([] == a valid committed row)."""
    errs = []
    if not isinstance(row, dict):
        return ["row is not an object"]
    extra = set(row) - _ROW_KEYS - {"redacted_fields"}
    if extra:
        errs.append(f"unknown keys for schema_version 1: {sorted(extra)}")
    if row.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"schema_version {row.get('schema_version')!r} != {SCHEMA_VERSION!r}")
    for k in ("run_id", "attempt_id"):
        if not isinstance(row.get(k), str) or not row.get(k):
            errs.append(f"{k} must be a non-empty string (idempotency key)")
    for k in ("timing_ms", "queued_ms"):
        v = row.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
            errs.append(f"{k} must be a non-negative int or null")
    ps = row.get("parse_status")
    if ps is not None and ps not in PARSE_STATUS_ENUM:
        errs.append(f"parse_status {ps!r} not in enum")
    cv = row.get("canary_verdict")
    if cv is not None and cv not in CANARY_VERDICT_ENUM:
        errs.append(f"canary_verdict {cv!r} not in enum")
    ra = row.get("recorded_at")
    if ra is not None and (not isinstance(ra, str) or not _UTC_RE.match(ra)):
        errs.append("recorded_at must be strict ISO-8601 UTC (YYYY-MM-DDTHH:MM:SSZ) or null")
    iss = row.get("issue")
    if iss is not None and (isinstance(iss, bool) or not isinstance(iss, int) or iss <= 0):
        errs.append("issue must be a positive int or null")
    mm = row.get("models_match")
    if mm is not None and not isinstance(mm, bool):
        errs.append("models_match must be bool or null")
    return errs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="seat_outcomes_lib")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("noop")  # placeholder until T2-T4 verbs land
    parser.parse_args(argv)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
