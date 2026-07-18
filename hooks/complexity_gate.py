"""Deterministic complexity gate (#429, E6, epic #422; plan doc §3.2).

"Code routes, prose never does" (owner directive). A pure, fail-closed decision helper the executor
consults (wired in #428 competitive rounds / #430 driver-bench) to decide whether a phase runs a
competitive bake-off. Lives BESIDE `plan_lib` in its own module (single responsibility, like
`model_routing_lib` / `executor_routing_lib`); it is executor-consumed, not a WF2-prose helper, so it
is deliberately NOT part of plan_lib's skill-wired public surface.

`needs_bakeoff(task, issue, plan_est, cfg) -> GateDecision` — pure, no I/O; accepts dicts or objects.
Bakes off if ANY: task risk_level == high; issue complexity == complex; the plan's files hit a
security surface; estimated diff lines > threshold; estimated file_count > threshold. **Fail-closed:**
missing/invalid mandatory metadata forces a bake-off (a gate that cannot evaluate its inputs bakes off
rather than silently passing). Returns the decision + reason codes + the exact input snapshot + a
policy digest so the executor can recompute it at admission and refuse a gate edited between plan and
run.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Final

# --- security-surface globs ---------------------------------------------------------------------
# A NARROWER, repo-owned override list: a change touching any of these ALWAYS bakes off, because
# plan-time size estimates can undershoot on a small-but-sensitive diff (plan §3.2 named limit —
# this glob list is the backstop and is a maintained artifact). Distinct from (and narrower than)
# plan_lib.DEFAULT_HIGH_RISK_PATH_PATTERNS, which is the broader Step-5 risk-promotion allowlist.
SECURITY_SURFACE_PATTERNS: Final[tuple[str, ...]] = (
    r"auth", r"secret", r"payment", r"migration", r"crypto", r"\.github", r"ci",
)

# Anchor each pattern to path-segment boundaries (same idiom as plan_lib._anchor — kept local to
# avoid a cross-module private import): match `src/auth/x.py` / `AUTH/h.py` / `ci.yml`, NOT
# `author.py` / `special.py`. Boundary chars: start/end, slash, underscore, dot, hyphen; a trailing
# `s` is allowed for natural plurals. A pattern already starting with `\.` isn't left-double-anchored.
_BOUNDARY = r"(?:^|[/_.\-])"
_BOUNDARY_END = r"s?(?:$|[/_.\-])"


def _anchor(pattern: str) -> str:
    if pattern.startswith(r"\."):
        return pattern + _BOUNDARY_END
    return _BOUNDARY + pattern + _BOUNDARY_END


_SECURITY_SURFACE_RE = re.compile(
    "(" + "|".join(_anchor(p) for p in SECURITY_SURFACE_PATTERNS) + ")",
    re.IGNORECASE,
)

# --- config-default thresholds (a project's `cfg` may override) ---------------------------------
DEFAULT_BAKEOFF_DIFF_LINES: Final[int] = 400
DEFAULT_BAKEOFF_FILE_COUNT: Final[int] = 10

# issue.complexity vocab matches the run-record store (work_summary): trivial|standard|complex.
_VALID_ISSUE_COMPLEXITIES: Final[frozenset[str]] = frozenset({"trivial", "standard", "complex"})
_VALID_RISK_LEVELS: Final[frozenset[str]] = frozenset({"high", "standard"})

_GATE_MISSING: Final = object()  # sentinel distinguishing an ABSENT field from a present null


def hits_security_surface(files) -> bool:
    """True if any path in ``files`` touches a security surface. A non-list input is False here —
    the CALLER (`needs_bakeoff`) fail-closes on a missing/invalid ``files`` field; this helper only
    answers the match question."""
    if not isinstance(files, (list, tuple)):
        return False
    return any(isinstance(p, str) and _SECURITY_SURFACE_RE.search(p) for p in files)


@dataclass(frozen=True)
class GateDecision:
    """A deterministic complexity-gate verdict. ``decision`` True ⇒ bake off. ``reason_codes`` names
    every trigger that fired (incl. ``fail_closed:<field>`` for missing/invalid metadata).
    ``input_snapshot`` is the exact inputs the decision was computed from; ``policy_digest`` is a
    sha256 over that snapshot so the executor can recompute it at admission and refuse a gate edited
    between plan and run."""

    decision: bool
    reason_codes: tuple
    input_snapshot: dict
    policy_digest: str


def _field(obj, *names, default=_GATE_MISSING):
    """Read a field from a dict OR an object (tolerates snake/camel aliases)."""
    for name in names:
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
        elif hasattr(obj, name):
            return getattr(obj, name)
    return default


def _json_safe(value):
    """A JSON-serializable scalar: pass None/str/int/float/bool through, stringify anything else
    (Enum/bytes/set/custom object). Keeps the snapshot — and the digest computed from it — total, so
    a non-primitive raw metadata value fail-CLOSES via the value-validity check instead of crashing
    _policy_digest's json.dumps (Step-11 F1)."""
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)


def _policy_digest(snapshot: dict) -> str:
    # default=str is belt-and-suspenders: _json_safe already scrubs the stored values, but a total
    # serializer guarantees the digest can never raise regardless of what the snapshot holds.
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _int_or_none(value):
    """An int that is NOT a bool (bool is an int subclass — a True 'line count' is invalid metadata)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def reasons_from_snapshot(snap: dict) -> tuple:
    """Re-derive the reason codes (hence ``decision = bool(reasons)``) from a gate ``input_snapshot``
    ALONE — the single source of the trigger logic, shared by ``needs_bakeoff`` (which builds the
    snapshot) and the executor's admission check (#428, which recomputes the decision from the
    integrity-verified snapshot so a gate whose *decision* was edited between plan and run is caught:
    the ``policy_digest`` binds the snapshot, and the decision is a pure function of it). Reason order
    matches ``needs_bakeoff``'s original emission order so ``reason_codes`` is byte-identical."""
    reasons: list[str] = []
    for key in snap.get("threshold_invalid") or ():   # threshold fail-closes emit first (as before)
        reasons.append(f"fail_closed:{key}_invalid")
    thr = snap.get("thresholds") or {}
    diff_thr = thr.get("BAKEOFF_DIFF_LINES", DEFAULT_BAKEOFF_DIFF_LINES)
    file_thr = thr.get("BAKEOFF_FILE_COUNT", DEFAULT_BAKEOFF_FILE_COUNT)

    rl = snap.get("risk_level")
    if rl is None or rl not in _VALID_RISK_LEVELS:
        reasons.append(f"fail_closed:risk_level={'missing' if rl is None else rl}")
    elif rl == "high":
        reasons.append("risk_high")

    cx = snap.get("complexity")
    if cx is None or cx not in _VALID_ISSUE_COMPLEXITIES:
        reasons.append(f"fail_closed:complexity={'missing' if cx is None else cx}")
    elif cx == "complex":
        reasons.append("complexity_complex")

    hit = snap.get("security_surface_hit")
    if hit is None:
        reasons.append("fail_closed:files=missing")
    elif hit:
        reasons.append("security_surface")

    lines = snap.get("lines")
    if lines is None:
        reasons.append("fail_closed:lines=invalid")
    elif lines > diff_thr:
        reasons.append("diff_lines_over")

    fc = snap.get("file_count")
    if fc is None:
        reasons.append("fail_closed:file_count=invalid")
    elif fc > file_thr:
        reasons.append("file_count_over")
    return tuple(reasons)


def decision_from_snapshot(snap: dict) -> bool:
    """The bake-off decision re-derived from a snapshot alone (``bool`` of the reasons). The executor
    calls this at admission on the digest-verified snapshot — the authoritative decision, so a
    tampered ``GateDecision.decision`` cannot force or suppress a bake-off (#428 M7)."""
    return bool(reasons_from_snapshot(snap))


class GateTamperError(RuntimeError):
    """A ``GateDecision`` failed authentication: either its ``policy_digest`` does not recompute from
    ``input_snapshot`` (edited between plan and run), or an ``expected_context`` fact does not match
    the snapshot (a stale/reused decision minted for different plan inputs)."""


def verified_decision(gate_decision, expected_context=None) -> bool:
    """Single-sourced authentication for ALL gate consumers (#464 — extracted from
    ``bakeoff_policy._verified_decision``, which now wraps this).

    (1) Recompute the #429 ``policy_digest`` over ``input_snapshot`` and refuse a snapshot edited
    between plan and run (``GateTamperError``). (2) If ``expected_context`` is given, cross-check
    each of the caller's OWN plan facts against the snapshot so a stale decision minted for DIFFERENT
    plan inputs is rejected — the message names the offending key but NEVER the values (they may
    carry plan text). (3) Return the AUTHORITATIVE bake-off decision RE-DERIVED from the (now
    integrity-verified) snapshot — NOT ``gate_decision.decision``, which the digest does not bind.

    ``expected_context`` = the caller's own plan facts (a subset of the ``input_snapshot`` keys);
    ``None`` = digest-only (bakeoff_policy's internal carve-out — it mints the gate in-process one
    call earlier and holds no separate plan doc). Honest limit: this is an IN-PROCESS trust boundary
    with an unkeyed digest — it defends against authoring errors and stale reuse, not a hostile
    in-process caller who can fabricate a self-consistent decision."""
    snapshot = gate_decision.input_snapshot
    expected = _policy_digest(snapshot)  # single-source digest
    if expected != gate_decision.policy_digest:
        raise GateTamperError(
            f"#429 gate policy_digest mismatch (input_snapshot edited between plan and run): "
            f"expected {expected}, got {gate_decision.policy_digest}")
    if expected_context is not None:
        # {} is NOT the carve-out: only None means digest-only. An emptied programmatic context
        # must fail loud, never silently disable the stale-decision defense (#464 Step-8a).
        if not expected_context:
            raise GateTamperError(
                "#429 gate: empty expected_context — only None (digest-only carve-out) or a "
                "non-empty mapping is accepted")
        for key, value in expected_context.items():
            # Distinguish an ABSENT snapshot key from a present-but-mismatched one; a None-valued
            # expected fact must not collapse into .get()'s None default (#464 Step-8a).
            # Do NOT leak the values — they may carry plan text.
            if key not in snapshot:
                raise GateTamperError(
                    f"#429 gate context mismatch: input_snapshot[{key!r}] missing "
                    f"(stale/reused decision)")
            if snapshot[key] != value:
                raise GateTamperError(
                    f"#429 gate context mismatch: input_snapshot[{key!r}] != expected "
                    f"(stale/reused decision)")
    return decision_from_snapshot(snapshot)


def needs_bakeoff(task, issue, plan_est, cfg=None) -> GateDecision:
    """Deterministic bake-off gate (plan §3.2). See the module docstring. Pure; fail-closed.

    Builds the ``input_snapshot`` from the raw inputs, then derives ``reason_codes``/``decision`` from
    the snapshot via ``reasons_from_snapshot`` (single source, so the admission-time recompute cannot
    drift from the plan-time verdict)."""
    cfg = cfg or {}
    snap: dict = {}
    invalid_thresholds: list[str] = []

    def _threshold(key, alias, default):
        # Absent -> default (a legitimate "not configured"). PRESENT-but-unparseable -> fail CLOSED:
        # a bad threshold must not silently LOOSEN the gate to the default when the operator meant
        # something stricter (Step-11 F2). The invalid key is recorded in the snapshot (so the digest
        # binds it and the decision is re-derivable), then surfaced as fail_closed:{key}_invalid.
        raw = _field(cfg, key, alias)
        if raw is _GATE_MISSING:
            return default
        val = _int_or_none(raw)
        if val is None:
            invalid_thresholds.append(key)
            return default
        return val

    diff_lines_thr = _threshold("BAKEOFF_DIFF_LINES", "bakeoff_diff_lines", DEFAULT_BAKEOFF_DIFF_LINES)
    file_count_thr = _threshold("BAKEOFF_FILE_COUNT", "bakeoff_file_count", DEFAULT_BAKEOFF_FILE_COUNT)
    if invalid_thresholds:
        snap["threshold_invalid"] = invalid_thresholds

    rl = _field(task, "risk_level", "riskLevel")
    snap["risk_level"] = None if rl is _GATE_MISSING else _json_safe(rl)

    cx = _field(issue, "complexity")
    snap["complexity"] = None if cx is _GATE_MISSING else _json_safe(cx)

    files = _field(plan_est, "files")
    snap["security_surface_hit"] = None if not isinstance(files, (list, tuple)) else hits_security_surface(files)

    snap["lines"] = _int_or_none(_field(plan_est, "lines"))
    snap["file_count"] = _int_or_none(_field(plan_est, "file_count", "fileCount"))
    snap["thresholds"] = {"BAKEOFF_DIFF_LINES": diff_lines_thr, "BAKEOFF_FILE_COUNT": file_count_thr}

    reasons = reasons_from_snapshot(snap)
    return GateDecision(
        decision=bool(reasons), reason_codes=reasons,
        input_snapshot=snap, policy_digest=_policy_digest(snap),
    )
