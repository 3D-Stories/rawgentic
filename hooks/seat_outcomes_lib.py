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
# NOTE: all anchored patterns use \Z (NOT $) — `$` matches before a terminal newline, so
# "safe\n" would wrongly pass and commit a control character (Step-11 finding).
_IDENT_RE = re.compile(r"\A[A-Za-z0-9._:/-]+\Z")
_WIN_DRIVE_RE = re.compile(r"\A[A-Za-z]:")
_SHA_RE = re.compile(r"\A[0-9a-f]{40,64}\Z")
_UTC_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_FALLBACK_RE = re.compile(r"\Afallback from (?P<from>[^:]+): (?P<status>.+)\Z")

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
def _is_path_shaped(s: str, *, allow_single_slash: bool) -> bool:
    """True if a grammar-passing string looks like a filesystem path (→ must be redacted).

    ``allow_single_slash`` is True ONLY for model-id fields (`provider/model`); every other
    identity field (run_id/attempt_id/correlation_id/seat/engine/lane) rejects ANY slash — a
    one-slash relative path like ``etc/passwd`` or ``secrets/token`` is NOT legitimate there
    (Step-11 finding: the model exception was wrongly applied to all fields)."""
    if s.startswith("/") or s.startswith("\\"):
        return True
    if _WIN_DRIVE_RE.match(s):
        return True
    if s in (".", ".."):  # bare relative components
        return True
    segs = s.split("/")
    if ".." in segs or "." in segs:  # any relative-path segment (leading or interior)
        return True
    if any(seg == "" for seg in segs[1:]):  # interior empty segment
        return True
    if not allow_single_slash:
        return "/" in s  # non-model fields: any slash is path-shaped
    return len(segs) > 2  # model fields: one internal slash is legit provider/model


def _clean_ident(value, cap: int, *, allow_single_slash: bool = False):
    """Grammar + path-shape gate for a free identity field. Returns (clean|None, redacted).
    ``allow_single_slash`` (model fields only) permits exactly one internal `/`."""
    if value is None:
        return None, False
    if not isinstance(value, str) or len(value) > cap or not _IDENT_RE.match(value):
        return None, True
    if _is_path_shaped(value, allow_single_slash=allow_single_slash):
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

    def ident(key, cap, allow_slash=False):
        v, red = _clean_ident(obs.get(key), cap, allow_single_slash=allow_slash)
        if red:
            counter[0] += 1
        return v

    actual = obs.get("actual_model")
    # H1 fix: canonicalize the CLEANED actual, then re-gate the canonical result through the
    # model-field grammar — canonicalizing raw actual would commit an unredacted path/secret
    # in `model` even when `actual_model` itself was redacted to null.
    clean_actual, red_actual = _clean_ident(actual, 120, allow_single_slash=True)
    if red_actual:
        counter[0] += 1
    canon = canonicalize_model_id(clean_actual) if clean_actual is not None else ""
    model = None
    if canon:
        model_clean, red_m = _clean_ident(canon, 120, allow_single_slash=True)
        if red_m:
            counter[0] += 1
        model = model_clean
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
        "requested_model": ident("requested_model", 120, allow_slash=True),
        "actual_model": clean_actual,
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


def _valid_utc(ra) -> bool:
    """Strict ISO-8601-UTC AND a real calendar instant (shape-match alone lets 2026-99-99 pass)."""
    if not isinstance(ra, str) or not _UTC_RE.match(ra):
        return False
    from datetime import datetime  # noqa: PLC0415
    try:
        datetime.strptime(ra, "%Y-%m-%dT%H:%M:%SZ")
        return True
    except ValueError:
        return False


def _ident_ok(v, cap, *, allow_slash=False, nullable=True):
    if v is None:
        return nullable
    return (isinstance(v, str) and 0 < len(v) <= cap and bool(_IDENT_RE.match(v))
            and not _is_path_shaped(v, allow_single_slash=allow_slash))


# --- validator (strict, fail-closed) -------------------------------------------------------
def validate_seat_outcome(row) -> list:
    """Return a list of human-readable errors ([] == a valid committed row).

    COMPLETE closed-schema check (Step-11 finding H4): every documented key must be PRESENT,
    every string field re-passes its grammar + path-shape gate (so a hand-planted stored row
    carrying a path/secret in ANY field is rejected on read, not re-committed), nested objects
    are exact-key-and-type checked, numbers are finite, and recorded_at is a real UTC instant.
    """
    errs = []
    if not isinstance(row, dict):
        return ["row is not an object"]
    extra = set(row) - _ROW_KEYS - {"redacted_fields"}
    if extra:
        errs.append(f"unknown keys for schema_version 1: {sorted(extra)}")
    missing = _ROW_KEYS - set(row)
    if missing:
        errs.append(f"missing required keys: {sorted(missing)}")
    if row.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"schema_version {row.get('schema_version')!r} != {SCHEMA_VERSION!r}")
    # idempotency key: non-empty, grammar-safe, no path/slash
    for k in ("run_id", "attempt_id"):
        if not _ident_ok(row.get(k), 120, nullable=False):
            errs.append(f"{k} must be a non-empty grammar-safe non-path string")
    if not _ident_ok(row.get("correlation_id"), 128):
        errs.append("correlation_id fails grammar/path gate")
    for k in ("seat", "engine"):
        if not _ident_ok(row.get(k), 64):
            errs.append(f"{k} fails grammar/path gate")
    for k in ("requested_model", "actual_model", "model"):
        if not _ident_ok(row.get(k), 120, allow_slash=True):
            errs.append(f"{k} fails grammar/path gate")
    lane = row.get("lane")
    if lane is not None:
        if not isinstance(lane, dict) or set(lane) != {"provider", "transport", "auth_mode", "pool"}:
            errs.append("lane must be null or exactly {provider,transport,auth_mode,pool}")
        else:
            for k, v in lane.items():
                if not _ident_ok(v, 64):
                    errs.append(f"lane.{k} fails grammar/path gate")
    fb = row.get("fallback")
    if fb is not None:
        if not isinstance(fb, dict) or fb.get("kind") not in ("model_fallback", "other"):
            errs.append("fallback must be null or {kind: model_fallback|other, ...}")
        elif fb["kind"] == "other":
            if set(fb) != {"kind"}:
                errs.append("fallback kind 'other' takes no other keys")
        else:  # model_fallback: exact keys + grammar/enum on the values
            if set(fb) != {"kind", "from_model", "parse_status"}:
                errs.append("fallback model_fallback keys must be exactly "
                            "{kind, from_model, parse_status}")
            else:
                if not _ident_ok(fb.get("from_model"), 120, allow_slash=True):
                    errs.append("fallback.from_model fails grammar/path gate")
                st = fb.get("parse_status")
                if st is not None and st not in PARSE_STATUS_ENUM:
                    errs.append("fallback.parse_status not in enum")
    wp = row.get("work_product_ref")
    if wp is not None:
        if not isinstance(wp, dict):
            errs.append("work_product_ref must be null or an object")
        else:
            for k in ("content_tree_sha", "base_sha", "head_sha"):
                v = wp.get(k)
                if v is not None and not (isinstance(v, str) and _SHA_RE.match(v)):
                    errs.append(f"work_product_ref.{k} must be a hex sha or null")
            st = wp.get("promotion_status")
            if st is not None and st not in PROMOTION_STATUS_ENUM:
                errs.append("work_product_ref.promotion_status not in enum")
    usage = row.get("usage")
    if usage is not None:
        if not isinstance(usage, dict):
            errs.append("usage must be null or an object")
        else:
            for k in ("input", "output", "cached"):
                v = usage.get(k)
                if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
                    errs.append(f"usage.{k} must be a non-negative int or null")
            if _num_or_none(usage.get("cost_proxy")) is None and usage.get("cost_proxy") is not None:
                errs.append("usage.cost_proxy must be a finite non-negative number or null")
    bud = row.get("budget")
    if bud is not None:
        if not isinstance(bud, dict) or set(bud) - {"reserved_usd", "spent_usd"}:
            errs.append("budget must be null or exactly {reserved_usd, spent_usd}")
        else:
            for k in ("reserved_usd", "spent_usd"):
                if k in bud and bud[k] is not None and _num_or_none(bud[k]) is None:
                    errs.append(f"budget.{k} must be a finite non-negative number or null")
    for k in ("timing_ms", "queued_ms", "hook_denials"):
        v = row.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
            errs.append(f"{k} must be a non-negative int or null")
    ec = row.get("exit_code")
    if ec is not None and (isinstance(ec, bool) or not isinstance(ec, int)):
        errs.append("exit_code must be an int or null")
    to = row.get("timed_out")
    if to is not None and not isinstance(to, bool):
        errs.append("timed_out must be a bool or null")
    ps = row.get("parse_status")
    if ps is not None and ps not in PARSE_STATUS_ENUM:
        errs.append(f"parse_status {ps!r} not in enum")
    cv = row.get("canary_verdict")
    if cv is not None and cv not in CANARY_VERDICT_ENUM:
        errs.append(f"canary_verdict {cv!r} not in enum")
    ra = row.get("recorded_at")
    if ra is not None and not _valid_utc(ra):
        errs.append("recorded_at must be a real ISO-8601 UTC instant (YYYY-MM-DDTHH:MM:SSZ) or null")
    iss = row.get("issue")
    if iss is not None and (isinstance(iss, bool) or not isinstance(iss, int) or iss <= 0):
        errs.append("issue must be a positive int or null")
    mm = row.get("models_match")
    if mm is not None and not isinstance(mm, bool):
        errs.append("models_match must be bool or null")
    for k in ("experiment_id", "arm"):
        if row.get(k) is not None:
            errs.append(f"{k} must be null (AC-K4 stub)")
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


def _safe_open_read(path: Path):
    """Open a file read-only with O_NOFOLLOW and assert it is a regular file on the OPENED fd
    (TOCTOU-safe — a symlink swapped in for `path` is refused, not followed). Returns the fd."""
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    st = os.fstat(fd)
    import stat as _stat  # noqa: PLC0415
    if not _stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise HarvestBounds(f"{path}: not a regular file")
    return fd


def _safe_read_text(path: Path, *, max_bytes: int = None) -> str:
    """Open ONCE (O_NOFOLLOW), fstat regular-file + size, and read from the SAME fd — so the
    size check and the read cannot straddle a path swap (TOCTOU-safe; Step-11 finding)."""
    fd = _safe_open_read(path)
    try:
        if max_bytes is not None and os.fstat(fd).st_size > max_bytes:
            raise HarvestBounds(f"{path}: {os.fstat(fd).st_size} bytes > {max_bytes}")
        with os.fdopen(fd, "r", encoding="utf-8") as f:
            return f.read()
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _read_audit(audit_path: Path):
    """Line-tolerant read of routing-audit.jsonl → (receipts_by_nonce, receipt_nonce_counts,
    obs_nonce_counts, obs_list, counters). Duplicate-key JSON is rejected. Malformed/oversize/
    non-object lines are counted, not fatal. Read via an O_NOFOLLOW-hardened fd."""
    counters = {"skipped_malformed": 0}
    receipts = {}
    nonce_counts = {}       # receipt nonces
    obs_nonce_counts = {}   # observation-envelope receipt_nonce references
    obs = []
    for raw in _safe_read_text(audit_path, max_bytes=AUDIT_MAX_BYTES).splitlines():
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
        if not isinstance(rec, dict):  # valid JSON that is not an object → counted, not fatal
            counters["skipped_malformed"] += 1
            continue
        kind = rec.get("kind")
        if kind == "receipt":
            n = rec.get("nonce")
            if not isinstance(n, str) or not n:  # a receipt without a real nonce is unusable
                counters["skipped_malformed"] += 1
                continue
            nonce_counts[n] = nonce_counts.get(n, 0) + 1
            receipts[n] = rec
        elif kind == "observation":
            en = rec.get("receipt_nonce")
            if isinstance(en, str) and en:
                obs_nonce_counts[en] = obs_nonce_counts.get(en, 0) + 1
            obs.append(rec)
            if len(obs) > AUDIT_MAX_ENTRIES:
                raise HarvestBounds(f"audit > {AUDIT_MAX_ENTRIES} observation entries")
    return receipts, nonce_counts, obs_nonce_counts, obs, counters


def _no_dup_keys(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate JSON key {k!r}")
        seen[k] = v
    return seen


def _bind_ok(envelope, receipts, nonce_counts, obs_nonce_counts, run_id):
    """Return (True, inner) if the observation is bound to a valid pass-verdict receipt for
    THIS run, else (False, reason).

    Honest scope (Step-11 narrowing): this proves the observation is linked to exactly one
    valid pass-verdict receipt whose seat/correlation/attempt/identity/digest agree — i.e. it
    rejects orphan, drift, failed-verdict, duplicate-nonce, and missing-nonce entries. It does
    NOT reconcile against the run's expected-call ledger (that is `enforce.reconcile_run`'s job,
    out of harvest scope) and it is NOT a hostile-forgery boundary (receipts share the same
    mutable gitignored file; no MAC)."""
    inner = envelope.get("observation")
    if not isinstance(inner, dict):
        return False, "no-inner"
    if inner.get("run_id") != run_id:
        return False, "run-id-mismatch"
    nonce = envelope.get("receipt_nonce")
    if not isinstance(nonce, str) or not nonce:  # a missing/empty nonce can never bind
        return False, "no-nonce"
    rec = receipts.get(nonce)
    if rec is None:
        return False, "no-receipt"
    if nonce_counts.get(nonce, 0) != 1:
        return False, "dup-receipt-nonce"
    if obs_nonce_counts.get(nonce, 0) != 1:  # two observations for one receipt = ambiguous
        return False, "dup-observation-nonce"
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
    counters). Nothing is deleted: valid v1 rows are indexed + kept; a malformed/non-object/
    invalid-v1 line, a DUPLICATE (run_id, attempt_id), OR an unknown/future schema_version is
    moved to quarantine (NOT re-committed) — Step-11: recommitting an unvalidated unknown-version
    row would bypass the redaction contract, so it is quarantined for diagnosis instead."""
    index = {}
    kept = []  # verbatim lines to re-commit (valid v1 only)
    quarantine = []
    counters = {"passed_through": 0, "quarantined": 0}
    if not store_path.exists():
        return index, kept, quarantine, counters
    for raw in _safe_read_text(store_path).splitlines():
        s = raw.strip()
        if not s:
            continue
        try:
            rec = json.loads(s, object_pairs_hook=_no_dup_keys)
        except (json.JSONDecodeError, ValueError):
            quarantine.append(raw)  # torn terminal or malformed interior → quarantine
            counters["quarantined"] += 1
            continue
        if not isinstance(rec, dict) or rec.get("schema_version") != SCHEMA_VERSION \
                or validate_seat_outcome(rec):
            quarantine.append(raw)  # non-object / unknown-version / invalid v1 → quarantine
            counters["quarantined"] += 1
            continue
        key = (rec.get("run_id"), rec.get("attempt_id"))
        if key in index:
            quarantine.append(raw)  # a duplicate committed key is an anomaly → quarantine the dupe
            counters["quarantined"] += 1
            continue
        index[key] = (content_digest(rec), raw, rec)
        kept.append(raw)
    return index, kept, quarantine, counters


def harvest(capture_root, run_id, store, *, issue=None, now, project_root=None):
    """Harvest one run's audit observations into the durable committed sidecar.

    Locked (flock), consume-time-validated, binding-checked, non-destructive. Returns a result
    dict with per-outcome counters. Raises DigestConflict / HarvestBounds (both leave the store
    untouched). ``now`` is the recorded_at stamp (ISO-8601 UTC; the CLI supplies it)."""
    store_path = resolve_store_path(project_root, store) if project_root else Path(store)
    from phase_executor.capture import sanitize_component  # noqa: PLC0415  # pylint: disable=no-name-in-module
    # H6 fix: the run_id names a capture SUBDIR — sanitize it the SAME way the writer
    # (RoutingAuditLog) does, so "../victim" can't escape the capture root at harvest.
    audit_path = Path(capture_root) / sanitize_component(run_id) / "routing-audit.jsonl"
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

        receipts, nonce_counts, obs_nonce_counts, obs_envelopes, ac = _read_audit(audit_path)
        res["skipped_malformed"] = ac["skipped_malformed"]

        derived = []
        for env in obs_envelopes:
            ok, inner = _bind_ok(env, receipts, nonce_counts, obs_nonce_counts, run_id)
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
        enriched = {}  # key -> new verbatim line, replacing the kept original (issue enrichment)
        for row in derived:
            key = (row["run_id"], row["attempt_id"])
            dig = content_digest(row)
            if key in index:
                existing_dig, _existing_raw, existing_row = index[key]
                if existing_dig != dig:
                    raise DigestConflict(f"{key}: existing digest != new (store untouched)")
                # enrichment: null issue → validated positive (digest excludes issue, so this
                # is not a conflict). A differing NON-null issue keeps the existing value.
                if existing_row.get("issue") is None and row.get("issue"):
                    merged = dict(existing_row)
                    merged["issue"] = row["issue"]
                    enriched[key] = json.dumps(merged, separators=(",", ":"))
                continue  # idempotent skip
            index[key] = (dig, None, row)
            new_lines.append(json.dumps({k: v for k, v in row.items()}, separators=(",", ":")))
            res["rows_appended"] += 1

        if enriched:  # swap the kept originals for their enriched versions, in place
            kept = [enriched.get((json.loads(ln).get("run_id"), json.loads(ln).get("attempt_id")), ln)
                    for ln in kept]

        if quarantine:
            q_path = store_path.with_name(store_path.name + ".quarantine")
            q_fd = os.open(str(q_path), os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(q_fd, "a", encoding="utf-8") as f:
                    for ln in quarantine:
                        f.write(ln + "\n")
            except BaseException:
                try:
                    os.close(q_fd)
                except OSError:
                    pass
                raise

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
            if run in by_run:
                del by_run[run]  # re-insert so the LATEST occurrence sets the dict order (F15)
            by_run[run] = total
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


# --- config (AC-K5 / #446): telemetryAlerts --------------------------------------------------
# Rule classes: COUNT rules take false | non-negative int; TOGGLE rules take bool. No quota rule
# (deferred — no producer signal; design §3.4). Defaults below ARE the documented defaults (a
# drift test pins config-reference == these).
_COUNT_RULES = ("fallback_fired", "dispatch_failures")
_TOGGLE_RULES = ("model_mismatch", "parse_failure", "seat_wall_time_p90", "seat_cost_p90",
                 "review_findings_p90")
DEFAULT_THRESHOLDS = {
    "fallback_fired": 0, "dispatch_failures": 0,
    "model_mismatch": True, "parse_failure": True,
    "seat_wall_time_p90": True, "seat_cost_p90": True, "review_findings_p90": True,
}
_CONFIG_KEYS = frozenset({"version", "enabled", "windowSize", "minSamples", "thresholds"})


def validate_telemetry_alerts(block) -> list:
    """STRICT validation (setup Step 2j uses this before staging). [] == valid. Unknown keys
    rejected, per-rule value contract, bounds. Runtime load_thresholds calls this and fails
    OPEN (defaults + advisory) instead of raising."""
    errs = []
    if block is None:
        return errs
    if not isinstance(block, dict):
        return ["telemetryAlerts must be an object"]
    extra = set(block) - _CONFIG_KEYS
    if extra:
        errs.append(f"unknown telemetryAlerts keys: {sorted(extra)}")
    if block.get("version") != 1:
        errs.append(f"version {block.get('version')!r} != 1")
    if "enabled" in block and not isinstance(block["enabled"], bool):
        errs.append("enabled must be a bool")
    ws = block.get("windowSize", DEFAULT_WINDOW)
    if isinstance(ws, bool) or not isinstance(ws, int) or not 1 <= ws <= 1000:
        errs.append("windowSize must be an int in 1..1000")
        ws = DEFAULT_WINDOW
    ms = block.get("minSamples", DEFAULT_MIN_SAMPLES)
    if isinstance(ms, bool) or not isinstance(ms, int) or not 1 <= ms <= ws:
        errs.append("minSamples must be an int in 1..windowSize")
    th = block.get("thresholds", {})
    if not isinstance(th, dict):
        errs.append("thresholds must be an object")
        th = {}
    for k, v in th.items():
        if k in _COUNT_RULES:
            if v is False:
                continue
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                errs.append(f"count rule {k} must be false or a non-negative int")
        elif k in _TOGGLE_RULES:
            if not isinstance(v, bool):
                errs.append(f"toggle rule {k} must be a bool")
        else:
            errs.append(f"unknown rule {k!r}")
    return errs


def load_thresholds_from_block(block):
    """Runtime loader — FAIL-OPEN. Returns an effective config dict
    {enabled, windowSize, minSamples, thresholds:{rule: value}}. `enabled` is parsed FIRST and
    independently, so a valid enabled:false beside a malformed sibling still disables. A
    malformed block otherwise degrades to defaults (caller emits ONE advisory)."""
    enabled = True
    if isinstance(block, dict) and isinstance(block.get("enabled"), bool):
        enabled = block["enabled"]
    eff = {"enabled": enabled, "windowSize": DEFAULT_WINDOW, "minSamples": DEFAULT_MIN_SAMPLES,
           "thresholds": dict(DEFAULT_THRESHOLDS)}
    if not isinstance(block, dict) or validate_telemetry_alerts(block):
        if block is not None:  # any PRESENT-but-malformed block (incl. falsey [] / 0) → advisory
            eff["_advisory"] = "telemetryAlerts malformed — using defaults"
        return eff  # fail-open to defaults (enabled already honored above)
    if isinstance(block.get("windowSize"), int) and not isinstance(block["windowSize"], bool):
        eff["windowSize"] = block["windowSize"]
    if isinstance(block.get("minSamples"), int) and not isinstance(block["minSamples"], bool):
        eff["minSamples"] = block["minSamples"]
    for k, v in (block.get("thresholds") or {}).items():
        eff["thresholds"][k] = v
    return eff


# --- alerts (AC-K3) — advisory, never a gate -----------------------------------------------
def _result(rule, status, reason, **fields):
    base = {"rule": rule, "status": status, "reason": reason, "advisory": True,
            "seat": None, "model": None, "observed": None, "threshold": None,
            "baseline_n": None, "message": None}
    base.update(fields)
    return base


_MSG_ALLOWED = re.compile(r"[^A-Za-z0-9 ._:/=+()%-]")


def _msg(text):
    """Fixed-template message. WHITELIST — keep only letters, digits, space, and
    `. _ : / = + ( ) % -`; everything else (tabs, NUL/Unicode/bidi controls, backticks, @,
    brackets, braces, `<`, `>`, `*`, `#`, `!`, `|`) is DROPPED, then length-capped. This
    forecloses markdown/mention/HTML/control injection through any identifier a template
    interpolates. Templates use the word "over" (never `>`) so comparisons survive the filter."""
    return _MSG_ALLOWED.sub("", str(text))[:200]


def evaluate_alerts(record, run_rows, baselines, thresholds) -> list:
    """Pure evaluation → closed-schema results. Only status=='fired' renders (§3.4). When the
    whole subsystem is disabled, return one 'disabled' result per rule."""
    th = thresholds if isinstance(thresholds, dict) and "thresholds" in thresholds else \
        {"enabled": True, "thresholds": dict(DEFAULT_THRESHOLDS)}
    if not th.get("enabled", True):
        return [_result(r, "disabled", "disabled") for r in DEFAULT_THRESHOLDS]
    rules = th["thresholds"]
    out = []

    # --- unconditional / record-level ---
    def cfg(rule):
        return rules.get(rule, DEFAULT_THRESHOLDS[rule])

    fb_cfg = cfg("fallback_fired")
    if fb_cfg is False:
        out.append(_result("fallback_fired", "disabled", "disabled"))
    else:
        nfb = sum(1 for r in run_rows if r.get("fallback") is not None)
        out.append(_result("fallback_fired", "fired" if nfb > fb_cfg else "not_evaluated",
                           "ok" if nfb > fb_cfg else "below_threshold",
                           observed=nfb, threshold=fb_cfg,
                           message=_msg(f"fallback fired {nfb}x (over {fb_cfg})")))

    df_cfg = cfg("dispatch_failures")
    if df_cfg is False:
        out.append(_result("dispatch_failures", "disabled", "disabled"))
    else:
        ndf = sum(1 for d in (record.get("dispatches") or [])
                  if d.get("outcome") in ("error", "dead"))
        out.append(_result("dispatch_failures", "fired" if ndf > df_cfg else "not_evaluated",
                           "ok" if ndf > df_cfg else "below_threshold",
                           observed=ndf, threshold=df_cfg,
                           message=_msg(f"dispatch failures {ndf} (over {df_cfg}) - broad dispatch failure")))

    if not cfg("model_mismatch"):
        out.append(_result("model_mismatch", "disabled", "disabled"))
    else:
        mm = sum(1 for r in run_rows if r.get("models_match") is False)
        out.append(_result("model_mismatch", "fired" if mm else "not_evaluated",
                           "ok" if mm else "below_threshold", observed=mm,
                           message=_msg(f"requested!=actual model on {mm} dispatch(es)") if mm else None))

    if not cfg("parse_failure"):
        out.append(_result("parse_failure", "disabled", "disabled"))
    else:
        pf = sum(1 for r in run_rows if r.get("parse_status") not in (None, "ok"))
        out.append(_result("parse_failure", "fired" if pf else "not_evaluated",
                           "ok" if pf else "below_threshold", observed=pf,
                           message=_msg(f"non-ok parse_status on {pf} dispatch(es)") if pf else None))

    # --- statistical / row-level (one result per (rule, seat, model)) ---
    groups = baselines.get("groups", {})

    def _stat(rule, value_fn):
        if not cfg(rule):
            out.append(_result(rule, "disabled", "disabled"))
            return
        metric = "timing_ms" if rule == "seat_wall_time_p90" else "cost"
        worst = {}  # (seat,model) -> (observed, p90)
        any_current_group_has_baseline = False
        current_keys = {f"{r['seat']}|{r['model']}" for r in run_rows if r.get("model") is not None}
        for key in current_keys:  # baseline availability is PER current-run group, not global (F16)
            m = groups.get(key, {}).get("metrics", {}).get(metric)
            if m and m.get("status") == "ok":
                any_current_group_has_baseline = True
        for r in run_rows:
            if r.get("model") is None:
                continue
            key = f"{r['seat']}|{r['model']}"
            m = groups.get(key, {}).get("metrics", {}).get(metric)
            if not m or m.get("status") != "ok":
                continue
            v = value_fn(r)
            if v is not None and v > m["p90"]:
                cur = worst.get(key)
                if cur is None or v > cur[0]:
                    worst[key] = (v, m["p90"])
        if not any_current_group_has_baseline:
            out.append(_result(rule, "not_evaluated", "no_baseline"))
            return
        if not worst:
            out.append(_result(rule, "not_evaluated", "below_threshold"))
            return
        for key, (obs, p90) in worst.items():
            seat, model = key.split("|", 1)
            out.append(_result(rule, "fired", "ok", seat=seat, model=model, observed=obs,
                               threshold=p90, baseline_n=groups[key]["metrics"].get(
                                   "timing_ms" if rule == "seat_wall_time_p90" else "cost", {}).get("n"),
                               message=_msg(f"{seat}/{model} {rule} {obs} over p90 {p90}")))

    _stat("seat_wall_time_p90", lambda r: _int_or_none(r.get("timing_ms")))
    _stat("seat_cost_p90", _row_cost)

    # review_findings_p90
    if not cfg("review_findings_p90"):
        out.append(_result("review_findings_p90", "disabled", "disabled"))
    else:
        rb = baselines.get("review_findings")
        # a gate carries severity input only when BOTH counts are present ints; a legacy record
        # with review findings but no severity split is MISSING input, not a real zero (F5/F23).
        gates = record.get("gates") or []
        have_severity = any(
            isinstance(g.get("findings_critical"), int) and not isinstance(g.get("findings_critical"), bool)
            and isinstance(g.get("findings_high"), int) and not isinstance(g.get("findings_high"), bool)
            for g in gates)
        if not rb or rb.get("status") != "ok":
            out.append(_result("review_findings_p90", "not_evaluated", "no_baseline"))
        elif not have_severity:
            out.append(_result("review_findings_p90", "not_evaluated", "missing_input"))
        else:
            total = 0
            for g in gates:
                c, h = g.get("findings_critical"), g.get("findings_high")
                if isinstance(c, int) and not isinstance(c, bool):
                    total += c
                if isinstance(h, int) and not isinstance(h, bool):
                    total += h
            fired = total > rb["p90"]
            out.append(_result("review_findings_p90", "fired" if fired else "not_evaluated",
                               "ok" if fired else "below_threshold", observed=total,
                               threshold=rb["p90"], baseline_n=rb["n"],
                               message=_msg(f"review Critical+High {total} over p90 {rb['p90']}")))
    return out


def extra_rows_for(results) -> list:
    """The Step-16 fold payload — fired results only, capped 20 + a truncation row."""
    fired = [r for r in results if r["status"] == "fired"]
    rows = [{"label": f"telemetry-alert:{r['rule']}", "value": r["message"] or r["rule"]}
            for r in fired[:20]]
    if len(fired) > 20:
        rows.append({"label": "telemetry-alert:truncated",
                     "value": f"{len(fired) - 20} more fired alerts omitted"})
    return rows


def render_advisory_block(results) -> str:
    fired = [r for r in results if r["status"] == "fired"]
    lines = ["## Telemetry alerts (advisory)"]
    if not fired:
        lines.append("no alerts")
    else:
        lines.extend(f"- {r['rule']}: {r['message']}" for r in fired[:20])
    return "\n".join(lines)


# --- CLI -----------------------------------------------------------------------------------
def _now_utc():
    from datetime import datetime, timezone  # noqa: PLC0415
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cmd_run_end(args) -> int:
    import work_summary  # noqa: PLC0415
    rec_path = Path(args.record_file)
    try:
        record = json.loads(rec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"run-end: cannot read --record-file: {e}", file=sys.stderr)
        return 2
    if work_summary.validate_record(record, strict=True):
        print("run-end: --record-file failed validate_record", file=sys.stderr)
        return 2
    rec_run = record.get("run_id")
    if isinstance(rec_run, str) and rec_run and rec_run != args.run_id:
        print(f"run-end: record run_id {rec_run!r} != --run-id {args.run_id!r}", file=sys.stderr)
        return 2
    # Issue attribution requires the record to POSITIVELY belong to this run: only trust its
    # issue when its run_id is present AND equals --run-id (F4/F26). A legacy record without a
    # run_id cannot be cross-attributed — warn and commit issue:null.
    issue = None
    if isinstance(rec_run, str) and rec_run == args.run_id:
        iss = (record.get("issue") or {}).get("number") if isinstance(record.get("issue"), dict) else None
        if isinstance(iss, int) and not isinstance(iss, bool) and iss > 0:
            issue = iss
    elif not rec_run:
        print("run-end: --record-file has no run_id — committing issue:null (set run_id at Step 16)",
              file=sys.stderr)
    now = _now_utc()
    cap = args.capture_root or str(Path(args.project_root) / ".rawgentic" / "runs")
    store = resolve_store_path(args.project_root, args.store)
    # Load config FIRST — its windowSize/minSamples must size the baselines (F14: previously
    # loaded after, so operator config never took effect). Surface a present-but-malformed block.
    th = load_thresholds_from_block(_load_config_block(args.project_root))
    if th.get("_advisory"):
        print(f"run-end: {th['_advisory']}", file=sys.stderr)
    win, mn = th["windowSize"], th["minSamples"]
    try:
        res = harvest(cap, args.run_id, store, issue=issue, now=now)
    except (DigestConflict, HarvestBounds) as e:
        print(f"run-end: harvest aborted (store untouched): {e}", file=sys.stderr)
        return 1
    rows = _read_store_rows(store)
    baselines = compute_baselines(rows, exclude_run_id=args.run_id, min_n=mn, window=win)
    baselines["review_findings"] = compute_review_baseline(
        _load_i2_records(args.project_root), workflow=record.get("workflow"),
        exclude_run_id=args.run_id, min_n=mn, window=win)
    results = evaluate_alerts(record, [r for r in rows if r.get("run_id") == args.run_id],
                             baselines, th)
    out = {**res, "evaluations": results, "alerts": [r for r in results if r["status"] == "fired"],
           "extra_rows": extra_rows_for(results), "advisory_block": render_advisory_block(results)}
    if args.json:
        print(json.dumps(out))
    else:
        print(out["advisory_block"])
        # surface the harvest counters + any note in the human path too (not just --json)
        counts = " ".join(f"{k}={res[k]}" for k in
                          ("rows_appended", "skipped_malformed", "skipped_invalid_observation",
                           "skipped_unbound", "quarantined", "passed_through", "redacted_fields"))
        print(f"telemetry harvest: {counts}")
        if res.get("note"):
            print(res["note"])
    return 0


def _read_store_rows(store_path):
    rows = []
    p = Path(store_path)
    if not p.exists():
        return rows
    for line in _safe_read_text(p).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("schema_version") == SCHEMA_VERSION \
                and not validate_seat_outcome(rec):
            rows.append(rec)
    return rows


def _load_i2_records(project_root):
    import work_summary  # noqa: PLC0415
    store = Path(project_root).joinpath("docs", "measurements", "run_records.jsonl")
    if not store.exists():
        return []
    try:
        records, _ = work_summary.load_store(str(store))
        return records
    except Exception:  # pylint: disable=broad-exception-caught
        return []


def _load_config_block(project_root):
    cfg = Path(project_root) / ".rawgentic.json"
    if not cfg.exists():
        return None
    try:
        parsed = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):  # a non-object .rawgentic.json can't carry the key
        return None
    return parsed.get("telemetryAlerts")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="seat_outcomes_lib")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ph = sub.add_parser("harvest")
    ph.add_argument("--run-id", required=True)
    ph.add_argument("--project-root", required=True)
    ph.add_argument("--record-file", default=None)
    ph.add_argument("--store", default=None)
    ph.add_argument("--capture-root", default=None)

    pb = sub.add_parser("baselines")
    pb.add_argument("--project-root", required=True)
    pb.add_argument("--store", default=None)
    pb.add_argument("--json", action="store_true")

    pr = sub.add_parser("run-end")
    pr.add_argument("--run-id", required=True)
    pr.add_argument("--record-file", required=True)
    pr.add_argument("--project-root", required=True)
    pr.add_argument("--store", default=None)
    pr.add_argument("--capture-root", default=None)
    pr.add_argument("--json", action="store_true")

    pv = sub.add_parser("validate-config")
    pv.add_argument("--json", required=True, help="the telemetryAlerts block as JSON")

    args = parser.parse_args(argv)

    if args.cmd == "validate-config":
        try:
            block = json.loads(args.json)
        except json.JSONDecodeError as e:
            print(f"validate-config: bad JSON: {e}", file=sys.stderr)
            return 2
        errs = validate_telemetry_alerts(block)
        if errs:
            for e in errs:
                print(e, file=sys.stderr)
            return 2
        print("ok")
        return 0

    if args.cmd == "run-end":
        return _cmd_run_end(args)

    if args.cmd == "harvest":
        # Standalone harvest attributes an issue ONLY from a record that VALIDATES and whose
        # run_id positively matches --run-id (Step-11: an arbitrary parseable record must not
        # misattribute committed rows). Otherwise issue stays null (recovery-tool posture).
        issue = None
        if args.record_file:
            import work_summary  # noqa: PLC0415
            try:
                rec = json.loads(Path(args.record_file).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                rec = None
            if isinstance(rec, dict) and not work_summary.validate_record(rec, strict=True) \
                    and rec.get("run_id") == args.run_id:
                iss = (rec.get("issue") or {}).get("number") if isinstance(rec.get("issue"), dict) else None
                if isinstance(iss, int) and not isinstance(iss, bool) and iss > 0:
                    issue = iss
            else:
                print("harvest: --record-file did not validate or run_id mismatch — issue:null",
                      file=sys.stderr)
        cap = args.capture_root or str(Path(args.project_root) / ".rawgentic" / "runs")
        store = resolve_store_path(args.project_root, args.store)
        try:
            res = harvest(cap, args.run_id, store, issue=issue, now=_now_utc())
        except (DigestConflict, HarvestBounds) as e:
            print(f"harvest aborted (store untouched): {e}", file=sys.stderr)
            return 1
        print(json.dumps(res))
        return 0

    if args.cmd == "baselines":
        store = resolve_store_path(args.project_root, args.store)
        th = load_thresholds_from_block(_load_config_block(args.project_root))
        b = compute_baselines(_read_store_rows(store), min_n=th["minSamples"],
                              window=th["windowSize"])
        if args.json:
            print(json.dumps(b))
        else:  # human summary of the baselines verb — NOT the alert block (F29)
            print(f"seat-outcome baselines: {len(b['groups'])} group(s), "
                  f"{b['unknown_model_rows']} unknown-model row(s)")
            for key, g in sorted(b["groups"].items()):
                t = g["metrics"]["timing_ms"]
                st = t.get("status")
                detail = (f"p50={t['p50']} p90={t['p90']}" if st == "ok"
                          else f"{st} (n={t['n']})")
                print(f"  {key}: timing {detail}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
