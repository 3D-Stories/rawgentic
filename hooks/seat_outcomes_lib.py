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


# --- harvest: consume-time validation + binding + non-destructive locked rewrite ----------
import fcntl  # noqa: E402
import os  # noqa: E402

AUDIT_MAX_BYTES = 10 * 1024 * 1024
AUDIT_MAX_ENTRIES = 500
LINE_MAX_BYTES = 64 * 1024
SIDECAR_SOFT_MAX_ROWS = 50_000


class DigestConflict(Exception):
    """A same-key row already in the store has a DIFFERENT content digest — abort before write."""


class HarvestBounds(Exception):
    """An input exceeded a hard bound (aborts before any write)."""


def _target_identity(model, lane):
    """Replicate enforce.target_identity for the binding check (avoids importing enforce here;
    the shape is the frozen (model, provider, transport, auth_mode, pool, credential_ref, extra)
    tuple — matched against the receipt's stored target_identity list)."""
    from phase_executor.enforce import target_identity  # noqa: PLC0415  # pylint: disable=no-name-in-module
    return list(target_identity({"model": model, "lane": lane}))


def _read_audit(audit_path: Path):
    """Line-tolerant read of routing-audit.jsonl → (receipts_by_nonce, nonce_counts, obs_list,
    counters). Duplicate-key JSON is rejected. Malformed/oversize lines are counted, not fatal."""
    counters = {"skipped_malformed": 0}
    receipts = {}
    nonce_counts = {}
    obs = []
    size = audit_path.stat().st_size
    if size > AUDIT_MAX_BYTES:
        raise HarvestBounds(f"audit file {size} bytes > {AUDIT_MAX_BYTES}")
    for raw in audit_path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        if len(raw.encode("utf-8")) > LINE_MAX_BYTES:
            counters["skipped_malformed"] += 1
            continue
        try:
            rec = json.loads(raw, object_pairs_hook=_no_dup_keys)
        except (json.JSONDecodeError, ValueError):
            counters["skipped_malformed"] += 1
            continue
        kind = rec.get("kind")
        if kind == "receipt":
            n = rec.get("nonce")
            nonce_counts[n] = nonce_counts.get(n, 0) + 1
            receipts[n] = rec
        elif kind == "observation":
            obs.append(rec)
            if len(obs) > AUDIT_MAX_ENTRIES:
                raise HarvestBounds(f"audit > {AUDIT_MAX_ENTRIES} observation entries")
    return receipts, nonce_counts, obs, counters


def _no_dup_keys(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate JSON key {k!r}")
        seen[k] = v
    return seen


def _bind_ok(envelope, receipts, nonce_counts, run_id):
    """Return (True, inner) if the observation is bound to a valid pass-verdict receipt for
    THIS run, else (False, reason). Honest scope: detects orphan/tamper, NOT hostile forgery."""
    inner = envelope.get("observation")
    if not isinstance(inner, dict):
        return False, "no-inner"
    if inner.get("run_id") != run_id:
        return False, "run-id-mismatch"
    nonce = envelope.get("receipt_nonce")
    rec = receipts.get(nonce)
    if rec is None:
        return False, "no-receipt"
    if nonce_counts.get(nonce, 0) != 1:
        return False, "dup-nonce"
    if rec.get("verdict") != "pass":
        return False, "verdict-not-pass"
    if inner.get("attempt_id") != rec.get("attempt_id"):
        return False, "attempt-drift"
    if inner.get("seat") != rec.get("seat"):
        return False, "seat-drift"
    if inner.get("correlation_id") != rec.get("correlation_id"):
        return False, "correlation-drift"
    try:
        tid = _target_identity(inner.get("requested_model"), inner.get("dispatched_lane"))
    except Exception:  # pylint: disable=broad-exception-caught
        return False, "identity-error"
    if tid != list(rec.get("target_identity") or []):
        return False, "identity-mismatch"
    if rec.get("config_digest") != inner.get("routing_config_digest"):
        return False, "digest-mismatch"
    return True, inner


def resolve_store_path(project_root, store) -> Path:
    if store is not None:
        return Path(store)
    return Path(project_root).joinpath(*DEFAULT_STORE_RELPATH)


def _read_store(store_path: Path):
    """Stream the committed store → (valid_index {key: (digest,row)}, kept_lines, quarantine,
    counters). valid v1 rows index + keep; future-version pass through (kept); invalid interior
    rows → quarantine; a torn terminal fragment → quarantine. Nothing is deleted."""
    index = {}
    kept = []  # verbatim lines to re-commit (valid v1 + future-version)
    quarantine = []
    counters = {"passed_through": 0, "quarantined": 0}
    if not store_path.exists():
        return index, kept, quarantine, counters
    lines = store_path.read_text(encoding="utf-8").splitlines()
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            continue
        is_last = (i == len(lines) - 1)
        try:
            rec = json.loads(s, object_pairs_hook=_no_dup_keys)
        except (json.JSONDecodeError, ValueError):
            quarantine.append(raw)  # torn terminal or malformed interior → quarantine
            counters["quarantined"] += 1
            continue
        sv = rec.get("schema_version")
        if sv != SCHEMA_VERSION:
            kept.append(raw)  # forward-compat: pass through byte-verbatim
            counters["passed_through"] += 1
            continue
        if validate_seat_outcome(rec):
            quarantine.append(raw)  # invalid v1 row → quarantine, never re-committed
            counters["quarantined"] += 1
            continue
        key = (rec.get("run_id"), rec.get("attempt_id"))
        index[key] = (content_digest(rec), rec)
        kept.append(raw)
    return index, kept, quarantine, counters


def harvest(capture_root, run_id, store, *, issue=None, now, project_root=None):
    """Harvest one run's audit observations into the durable committed sidecar.

    Locked (flock), consume-time-validated, binding-checked, non-destructive. Returns a result
    dict with per-outcome counters. Raises DigestConflict / HarvestBounds (both leave the store
    untouched). ``now`` is the recorded_at stamp (ISO-8601 UTC; the CLI supplies it)."""
    store_path = resolve_store_path(project_root, store) if project_root else Path(store)
    audit_path = Path(capture_root) / run_id / "routing-audit.jsonl"
    res = {"rows_appended": 0, "skipped_malformed": 0, "skipped_invalid_observation": 0,
           "skipped_unbound": 0, "passed_through": 0, "quarantined": 0, "redacted_fields": 0,
           "note": None}
    if not audit_path.exists():
        res["note"] = "telemetry: no capture dir"
        return res

    # Lock FIRST (before reading the store) so read+evaluate+rewrite are one critical section.
    store_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = store_path.with_name(store_path.name + ".lock")
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        receipts, nonce_counts, obs_envelopes, ac = _read_audit(audit_path)
        res["skipped_malformed"] = ac["skipped_malformed"]

        derived = []
        for env in obs_envelopes:
            ok, inner = _bind_ok(env, receipts, nonce_counts, run_id)
            if not ok:
                res["skipped_unbound"] += 1
                continue
            try:
                from phase_executor.contract import validate_observation  # noqa: PLC0415  # pylint: disable=no-name-in-module
                validate_observation(inner)
            except Exception:  # pylint: disable=broad-exception-caught
                res["skipped_invalid_observation"] += 1
                continue
            row = derive_seat_outcome(inner, issue=issue, recorded_at=now)
            if validate_seat_outcome(row):
                res["skipped_invalid_observation"] += 1
                continue
            res["redacted_fields"] += row.pop("redacted_fields", 0)
            derived.append(row)

        index, kept, quarantine, sc = _read_store(store_path)
        res["passed_through"] = sc["passed_through"]
        res["quarantined"] = sc["quarantined"]

        new_lines = []
        for row in derived:
            key = (row["run_id"], row["attempt_id"])
            dig = content_digest(row)
            if key in index:
                existing_dig, existing_row = index[key]
                if existing_dig != dig:
                    raise DigestConflict(f"{key}: existing digest != new (store untouched)")
                # enrichment: null issue → validated positive
                if existing_row.get("issue") is None and row.get("issue"):
                    existing_row["issue"] = row["issue"]
                    kept = [ln for ln in kept
                            if (json.loads(ln).get("run_id"), json.loads(ln).get("attempt_id")) != key]
                    kept.append(json.dumps(existing_row, separators=(",", ":")))
                continue  # idempotent skip
            index[key] = (dig, row)
            new_lines.append(json.dumps({k: v for k, v in row.items()}, separators=(",", ":")))
            res["rows_appended"] += 1

        if quarantine:
            q_path = store_path.with_name(store_path.name + ".quarantine")
            with open(q_path, "a", encoding="utf-8") as f:
                for ln in quarantine:
                    f.write(ln + "\n")

        final = "\n".join(kept + new_lines)
        if final:
            final += "\n"
        _atomic_write(str(store_path), final)

        total_rows = len([1 for ln in (kept + new_lines) if ln.strip()])
        if total_rows > SIDECAR_SOFT_MAX_ROWS:
            res["note"] = f"telemetry: sidecar large ({total_rows} rows)"
        return res
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _atomic_write(path, text):
    """Route through the repo one-home atomic writer (mkstemp+os.replace)."""
    import atomic_write_lib  # noqa: PLC0415
    atomic_write_lib.atomic_write_text(path, text, prefix=".seat-outcomes-", fsync=True)


# --- baselines (AC-K2) ---------------------------------------------------------------------
import math  # noqa: E402
import statistics  # noqa: E402

DEFAULT_MIN_SAMPLES = 5
DEFAULT_WINDOW = 30


def _nearest_rank_p90(values):
    return sorted(values)[math.ceil(0.9 * len(values)) - 1]


def _percentile_metric(values, min_n):
    n = len(values)
    if n < min_n:
        return {"n": n, "missing": 0, "status": "insufficient_history"}
    return {"n": n, "missing": 0, "status": "ok",
            "p50": statistics.median(values), "p90": _nearest_rank_p90(values)}


def _row_cost(row):
    u = row.get("usage") or {}
    if _num_or_none(u.get("cost_proxy")) is not None:
        return u["cost_proxy"]
    b = row.get("budget") or {}
    return _num_or_none(b.get("spent_usd"))


def _window_key(row):
    return (row.get("recorded_at") or "", row.get("run_id") or "", row.get("attempt_id") or "")


def compute_baselines(rows, *, exclude_run_id=None, min_n=DEFAULT_MIN_SAMPLES, window=DEFAULT_WINDOW):
    """Rolling per-(seat, canonical model) baselines. Closed output schema (design §3.3):
    exactly four metrics per group; percentile metrics carry {n,missing,status[,p50,p90]},
    rate metrics {n,missing,status[,numerator,value]}. Null-model rows are counted only."""
    groups = {}
    unknown = 0
    for r in rows:
        if exclude_run_id is not None and r.get("run_id") == exclude_run_id:
            continue
        if r.get("model") is None:
            unknown += 1
            continue
        groups.setdefault((r["seat"], r["model"]), []).append(r)

    out = {"groups": {}, "unknown_model_rows": unknown, "review_findings": None,
           "bench_anchors": None, "notes": []}
    for (seat, model) in sorted(groups):
        grp = sorted(groups[(seat, model)], key=_window_key)[-window:]
        timings = [r["timing_ms"] for r in grp if _int_or_none(r.get("timing_ms")) is not None]
        costs = [c for c in (_row_cost(r) for r in grp) if c is not None]
        n_grp = len(grp)
        fb_num = sum(1 for r in grp if r.get("fallback") is not None)
        mm_rows = [r for r in grp if r.get("models_match") is not None]
        mm_num = sum(1 for r in mm_rows if r.get("models_match") is False)

        def _rate(n, num):
            if n < min_n:
                return {"n": n, "missing": 0, "status": "insufficient_history"}
            return {"n": n, "missing": 0, "status": "ok", "numerator": num, "value": num / n}

        out["groups"][f"{seat}|{model}"] = {"window": window, "metrics": {
            "timing_ms": {**_percentile_metric(timings, min_n),
                          "missing": n_grp - len(timings)},
            "cost": {**_percentile_metric(costs, min_n), "missing": n_grp - len(costs)},
            "fallback_rate": _rate(n_grp, fb_num),
            "mismatch_rate": _rate(len(mm_rows), mm_num),
        }}
    return out


def compute_review_baseline(i2_records, *, workflow, exclude_run_id=None,
                            min_n=DEFAULT_MIN_SAMPLES, window=DEFAULT_WINDOW):
    """Baseline of Σ(findings_critical+findings_high) over eligible I2 run-records: same
    workflow, non-null run_id, BOTH severity fields present, deduped by run_id (last wins),
    exclude_run_id, latest `window` distinct runs."""
    by_run = {}
    for rec in i2_records:
        if rec.get("workflow") != workflow:
            continue
        run = rec.get("run_id")
        if not isinstance(run, str) or not run or run == exclude_run_id:
            continue
        total = 0
        ok = False
        for g in rec.get("gates") or []:
            c, h = g.get("findings_critical"), g.get("findings_high")
            if isinstance(c, int) and not isinstance(c, bool) and \
               isinstance(h, int) and not isinstance(h, bool):
                total += c + h
                ok = True
        if ok:
            by_run[run] = total  # last occurrence wins
    values = list(by_run.values())[-window:]
    n = len(values)
    if n < min_n:
        return {"n": n, "missing": 0, "status": "insufficient_history"}
    return {"n": n, "missing": 0, "status": "ok",
            "p50": statistics.median(values), "p90": _nearest_rank_p90(values)}


# --- bench anchors (AC-K2 reference) -------------------------------------------------------
def _mean_scores(cells):
    """Per-dimension mean over cells whose scores are finite numbers."""
    sums, counts = {}, {}
    for c in cells:
        for dim, v in (c.get("scores") or {}).items():
            if _num_or_none(v) is None:
                continue
            sums[dim] = sums.get(dim, 0.0) + v
            counts[dim] = counts.get(dim, 0) + 1
    return {dim: sums[dim] / counts[dim] for dim in sums}


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def load_bench_anchors(bench_dir):
    """Stubbed (per-model recompute from cells) + live (newest-by-filename-timestamp, aborted →
    partial + last_completed) driver-bench anchors. Independent statuses; fail-open."""
    bench_dir = Path(bench_dir)
    out = {"stubbed": {"status": "unavailable"}, "live": {"status": "unavailable"}}

    stub = _load_json(bench_dir / "stubbed-baseline.json")
    if isinstance(stub, dict) and isinstance(stub.get("cells"), list):
        by_model = {}
        for c in stub["cells"]:
            by_model.setdefault(c.get("model"), []).append(c)
        out["stubbed"] = {"status": "ok",
                          "per_model": {m: _mean_scores(cs) for m, cs in by_model.items()},
                          "n_cells": len(stub["cells"])}

    live_files = sorted(bench_dir.glob("live-*.json")) if bench_dir.exists() else []
    if live_files:
        newest = live_files[-1]  # filename timestamp order (mtime never used)
        rep = _load_json(newest)
        if isinstance(rep, dict):
            if rep.get("aborted"):
                last = None
                for f in reversed(live_files):
                    r = _load_json(f)
                    if isinstance(r, dict) and not r.get("aborted"):
                        last = f.name
                        break
                out["live"] = {"status": "partial", "report": newest.name,
                               "abort_reason": rep.get("abort_reason"), "last_completed": last}
            else:
                out["live"] = {"status": "ok", "label": "campaign", "report": newest.name,
                               "dimension_means": _mean_scores(rep.get("cells") or [])}
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="seat_outcomes_lib")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("noop")  # baselines/alerts/run-end verbs land in T3/T4
    parser.parse_args(argv)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
