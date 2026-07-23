#!/usr/bin/env python3
"""WF completion summary + per-run structured run-record (Tier-2 telemetry).

WF2 (implement-feature) Step 16 used to hand-type a free-text completion summary,
which means its shape drifted run-to-run and nothing about the run was captured
for later analysis. This lib does two jobs from one validated record:

1. **render_summary** — deterministically renders the SAME human "WF COMPLETE"
   block the skill used to type by hand, so Step 16 output is consistent.
2. **the run-record** — a structured JSON line (issue/type, changes, tests,
   per-gate findings *caught vs resolved*, Step 11.5 security-scan status,
   loop-backs, PR/CI/deploy outcome) appended to a store. Accumulated across
   ~100 runs this is the substrate the Tier-2 A/B harness aggregates to measure
   the agentic workflow's effectiveness (docs/measurements names it explicitly).

Design mirrors hooks/security_scan.py: the logic is PURE functions
(validate_record, normalize_record, render_summary) carrying all behavior and
exhaustively unit-tested; the I/O is thin/injected (the clock is passed into
normalize_record; the store path resolves from flag>env>default). The store is
env-configurable from v1 via RAWGENTIC_RUN_RECORD_STORE; the default is
<project-root>/docs/measurements/run_records.jsonl (committed, reproducible).

Failure philosophy: fail-closed for the STORE (a record that fails validation is
never persisted — the telemetry substrate stays pristine), but render the human
summary best-effort regardless, so a malformed-record bug never denies the user
their Step 16 output. main() exits 1 in that case so the skill surfaces the gap.
"""
import argparse
import copy
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

# #473 (W11): optional additive top-level run_id — the I3 <-> I2 join key linking a run-record
# to its seat-outcomes sidecar rows. Grammar-bounded (safe component, no spaces/paths) so it
# never carries free text into committed telemetry. Absent on legacy records (tolerated).
# \Z (not $) so a trailing newline never passes; all-dot components (".", "..") rejected to
# agree with the executor's capture.sanitize_component.
_RUN_ID_RE = re.compile(r"\A[A-Za-z0-9._-]{1,120}\Z")


def _run_id_ok(v) -> bool:
    return isinstance(v, str) and bool(_RUN_ID_RE.match(v)) and v.strip(".") != ""

# The store accumulates one JSON line per workflow run. Env-overridable from v1;
# default is the project's committed measurements dir (reproducible telemetry).
STORE_ENV = "RAWGENTIC_RUN_RECORD_STORE"
DEFAULT_STORE_RELPATH = ("docs", "measurements", "run_records.jsonl")

# Required top-level fields. follow_ups is optional (defaults to []).
REQUIRED_TOP = ("workflow", "workflow_version", "issue", "changes", "tests",
                "gates", "security_scan", "loop_backs", "outcome")

ISSUE_TYPES = {"feature", "bug", "chore", "other"}
COMPLEXITIES = {"trivial", "standard", "complex"}  # None also allowed
GATE_STATUSES = {"pass", "fail", "skipped", "fast_path"}
CI_STATUSES = {"passed", "failed", "not_configured", "skipped"}
DEPLOY_STATUSES = {"success", "manual", "failed", "not_applicable"}
# Canonicalizes reviewer identity on a gate entry per #116's controlled-
# vocabulary contract. Optional; free text is rejected by design.
REVIEWER_KINDS = {"inline", "reflexion", "builtin_code_review", "codex",
                   "hand_rolled_multi"}
# #116: canonical gate name per (workflow, step) — the SINGLE SOURCE OF TRUTH so the Tier-2
# `gates[].name` column stops drifting across orchestrator sessions (the same gate was
# recorded as "Design Critique" / "design critique (3-judge + codex)" / "Design Critique
# (3-panel + codex)"). Keyed BY WORKFLOW because WF2 and WF3 map the same step numbers to
# different gates (WF2 step 4 = design critique; WF3 step 4 = the lightweight reflect; WF3
# does code review at step 9 where WF2 does drift). The run-record schema doc
# (references/run-record.md) + the WF2/WF3 Step-16 assembly prompts must emit these names;
# `TestCanonicalGateRegistry` guards the code↔doc sync. Step 11.5 is intentionally absent —
# its result lives in the `security_scan` section, not a `gates[]` row.
CANONICAL_GATE_NAMES = {
    "implement-feature": {
        "4": "Design Critique",
        "6": "Plan Drift",
        "8a": "Per-task Review",
        "9": "Implementation Drift",
        "11": "Code Review",
        "15": "Post-Deploy",
    },
    "fix-bug": {
        "4": "Lightweight Reflect",
        "9": "Code Review",
    },
}
# #116: controlled vocabulary for `security_scan.skipped[]` — a scanner KIND, not a
# free-text reason (the live dogfood fragmented `sca` and `sca: osv-scanner (no lockfiles)`
# into two "kinds"). Enforced fail-closed only in strict (write-time) validation; the
# lenient default keeps historical free-text records readable (forward-only, no eviction).
SCANNER_KINDS = {"secrets", "sca", "sast", "iac"}
# `set`/`skipped`/`deferred` are recorded by the orchestrator; `fired` is
# MANUAL-ONLY (see the goal_guard validation block below) — no code path detects
# it automatically. `deferred` (#191): Step 1b deferred the per-issue /goal to an
# already-active epic-level campaign goal (RAWGENTIC_EPIC_GOAL set) rather than
# emitting one that would clobber it.
GOAL_GUARD_VALUES = {"set", "skipped", "fired", "deferred"}
# #474: the run's dispatch architecture. REQUIRED for records at workflow_version >= this
# threshold (the flip release); optional/lenient below so historical records stay readable.
ARCHITECTURE_VALUES = {"executor", "legacy"}
_ARCHITECTURE_REQUIRED_FROM = (3, 93, 0)
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _semver_tuple(version):
    """Strict ``X.Y.Z`` -> int 3-tuple, else None. NEVER lexical (#474 SR4-9: a lexical
    compare would classify 3.100.0 < 3.93.0). A malformed version returns None and the
    caller treats the record as NEW — failing toward the requirement, never around it."""
    if not _is_str(version):
        return None
    m = _SEMVER_RE.match(version.strip())
    return tuple(int(g) for g in m.groups()) if m else None


def _architecture_required(record) -> bool:
    v = _semver_tuple(record.get("workflow_version"))
    return v is None or v >= _ARCHITECTURE_REQUIRED_FROM


def architecture_dispatch_warnings(record) -> list:
    """#474 detective surface: ADVISORY warnings (never validation errors) when an
    executor-architecture run-record carries any non-``primary`` dispatch resolution —
    post-hoc detection of Agent-tool dispatch inside an executor run, independent of any
    hook. Advisory by design: DISPATCH lines carry no run-ID join key, so sequential
    legacy+executor runs of one issue can false-positive (run-keyed telemetry is #606)."""
    warns = []
    if record.get("architecture") != "executor":
        return warns
    dispatches = record.get("dispatches")
    if not isinstance(dispatches, list):
        return warns
    for i, item in enumerate(dispatches):
        if isinstance(item, dict) and item.get("resolution") in ("fallback", "generic"):
            warns.append(
                f"advisory (#474): dispatches[{i}] resolution={item['resolution']!r} inside an "
                f"executor-architecture run — possible Agent-tool dispatch in an executor run "
                f"(false-positive possible across sequential runs of one issue)")
    return warns
# `usage.capture_status` (#189) — how the token/cost numbers were obtained.
# `captured` = live-parsed from the session log (REQUIRES real non-null tokens
# summing > 0 — the schema-level backstop against the #155 null-forever state);
# `unrecoverable` = a historical row with no session-id correlator; `unavailable`
# = capture was attempted for this run but failed (file missing / no usage).
CAPTURE_STATUS_VALUES = {"captured", "unrecoverable", "unavailable"}
TIMING_STATUS_VALUES = {"complete", "partial", "absent"}  # #506

# `dispatches[]` (#329) — per-subagent dispatch telemetry. Controlled
# vocabularies, same present-is-strict philosophy as GOAL_GUARD_VALUES /
# CAPTURE_STATUS_VALUES: an absent field is legacy-valid, but a present entry is
# fail-closed on anything outside these sets (non-strings, case variants, null).
DISPATCH_ROLES = {"review", "implementation", "analysis", "other"}
DISPATCH_OUTCOMES = {"ok", "error", "retried", "dead"}
DISPATCH_RESOLUTIONS = {"primary", "fallback", "generic"}

# Human-summary header label per workflow; falls back to the upper-cased name.
_WF_LABELS = {"implement-feature": "WF2", "fix-bug": "WF3"}


class WorkSummaryError(ValueError):
    """A run-record file could not be read or parsed. Surfaced as a usage error
    (the skill must supply a readable JSON record), never a silent empty run."""


def _is_int(x) -> bool:
    """True only for a real int. bool is a subclass of int in Python, so a naive
    isinstance(x, int) would accept `findings: true` and corrupt the substrate —
    reject bool explicitly."""
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x) -> bool:
    """True only for a real, finite int or float. bool is a subclass of int in
    Python, so a naive isinstance(x, (int, float)) would accept
    `cost_estimate_usd: true` and corrupt aggregation — reject bool explicitly.
    NaN/inf are rejected too (math.isfinite): a NaN cost or duration would
    silently corrupt any downstream sum/average in the Tier-2 substrate."""
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(x))


def _is_str(x) -> bool:
    return isinstance(x, str)


def _as_dict(x) -> dict:
    """Coerce to a dict for best-effort rendering. render_summary runs on
    UNVALIDATED records (main's rc=1 path), so a wrong-typed section (a string or
    list where an object is expected) must degrade to {} rather than raise."""
    return x if isinstance(x, dict) else {}


def _as_list(x) -> list:
    """Coerce to a list for best-effort rendering — a non-list (incl. a string,
    which is iterable but not the intended shape) degrades to [] rather than
    iterating its characters or raising."""
    return x if isinstance(x, list) else []


def _require_present(obj, section, keys, errs) -> None:
    """Append an error for any of `keys` ABSENT from obj. A *nullable* field means
    `null` is an allowed VALUE, not that the key may be omitted — tolerating
    absence would let a producer silently drop a field, making the Tier-2
    substrate unable to tell a deliberate null from a dropped one. (Non-nullable
    keys are already required implicitly: their value check fails on the None that
    `.get` returns for an absent key.)"""
    for k in keys:
        if k not in obj:
            errs.append(f"{section}.{k} is required (use null if not applicable)")


def canonical_gate_name(workflow, step):
    """Canonical gate name for a (workflow, step) pair (#116). Unknown workflow/step
    -> None (the caller keeps its own name; the registry covers the standard WF2/WF3
    gates). Keyed by workflow because WF2 and WF3 reuse step numbers for different gates."""
    return CANONICAL_GATE_NAMES.get(workflow, {}).get(str(step))


# --- #512: loop_backs cross-check against the persisted counters file ------

LOOPBACK_STATE_RELPATH = ("claude_docs", ".wf2-state")


def check_loopback_counters(record, counters) -> list:
    """Cross-check record loop_backs.used against the persisted counters state.

    `counters` is the parsed loopback_counters.json dict (source of truth,
    written by plan_lib.consume_loopback — it survives sessions; the record
    value is assembled from in-context memory, which is structurally wrong on
    any resumed/multi-session run, #512/WF2 #467). None => nothing to check.
    Fail-loud on divergence or an unreadable total: this validator exists
    precisely because a schema-valid record can be semantically wrong."""
    if counters is None:
        return []
    if not isinstance(counters, dict) or not isinstance(counters.get("total"), int):
        return ["loop_backs counters file is malformed (no integer 'total') — "
                "cannot cross-check; fix the counters file or drop the flag"]
    used = record.get("loop_backs", {}).get("used") if isinstance(
        record.get("loop_backs"), dict) else None
    total = counters["total"]
    if used != total:
        return [f"loop_backs.used ({used!r}) diverges from the persisted "
                f"counters file total ({total}) — the counters file is the "
                f"source of truth (#512); re-read it, never in-context memory"]
    return []


def load_loopback_counters(path, *, explicit) -> "dict | None":
    """Load the counters state for the cross-check (#512).

    explicit=True (--loopback-counters passed): a MISSING file means
    consume_loopback never ran => zero loop-backs => return a zero state so
    the check still validates; a present-but-malformed file returns a
    sentinel malformed dict (fail-loud downstream).
    explicit=False (cwd auto-discovery): a missing file returns None (skip —
    the cwd may not be the workspace root, so absence is ambiguous there)."""
    p = Path(path)
    if not p.exists():
        return {"total": 0} if explicit else None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"malformed": True}


# --- validate_record (pure) ------------------------------------------------

def validate_record(record, *, strict=False) -> list:
    """Return a list of human-readable validation errors ([] == valid).

    Hand-rolled (matching capabilities_lib's style) so the hook carries no
    runtime jsonschema dependency. Enforces required fields, types (rejecting
    bool-as-int), enum membership, and cross-field integrity (resolved<=findings,
    passing<=total, used<=budget, non-negative counts) — the integrity the
    downstream Tier-2 aggregation will rely on.

    `strict` (#116): additional WRITE-TIME controls that would evict historical
    free-text records if applied on read. Currently: `security_scan.skipped[]` must
    be a scanner KIND from `SCANNER_KINDS`. The lenient default is what `load_store`
    uses, so pre-#116 records stay readable (forward-only, no data loss); the summarize
    CLI validates with `strict=True` so every NEW record is clean."""
    if not isinstance(record, dict):
        return ["record must be a JSON object"]

    errs = []
    for key in REQUIRED_TOP:
        if key not in record:
            errs.append(f"missing required field: {key}")

    for key in ("workflow", "workflow_version"):
        if key in record and not (_is_str(record[key]) and record[key].strip()):
            errs.append(f"{key} must be a non-empty string")

    # #473: additive run_id (I3<->I2 join key). Optional; when present it must be a
    # grammar-bounded safe component (no spaces, no path shapes) so committed telemetry
    # carries no free text. Legacy records without it are tolerated.
    if "run_id" in record and not _run_id_ok(record["run_id"]):
        errs.append("run_id must be a grammar-safe non-all-dot component "
                    "([A-Za-z0-9._-], 1..120) when present")

    # #474: the run's dispatch architecture. Off-vocab rejected at ANY version; the field is
    # REQUIRED once workflow_version >= 3.93.0 (strict semver 3-tuple compare; a malformed
    # version counts as new — fails toward the requirement), lenient/optional below so
    # pre-flip records stay readable.
    if "architecture" in record:
        arch = record["architecture"]
        if not _is_str(arch) or arch not in ARCHITECTURE_VALUES:
            errs.append(f"architecture must be one of {sorted(ARCHITECTURE_VALUES)}")
    elif _architecture_required(record):
        errs.append("architecture is required for records at workflow_version >= 3.93.0 "
                    "(#474: executor|legacy)")

    if "issue" in record:
        issue = record["issue"]
        if not isinstance(issue, dict):
            errs.append("issue must be an object")
        else:
            _require_present(issue, "issue", ("number", "complexity"), errs)
            num = issue.get("number")
            if num is not None and not _is_int(num):
                errs.append("issue.number must be an integer or null")
            if issue.get("type") not in ISSUE_TYPES:
                errs.append(f"issue.type must be one of {sorted(ISSUE_TYPES)}")
            comp = issue.get("complexity")
            if comp is not None and comp not in COMPLEXITIES:
                errs.append(
                    f"issue.complexity must be one of {sorted(COMPLEXITIES)} or null")

    if "changes" in record:
        changes = record["changes"]
        if not isinstance(changes, dict):
            errs.append("changes must be an object")
        else:
            _require_present(changes, "changes", ("insertions", "deletions"), errs)
            for f in ("files_changed", "commits"):
                v = changes.get(f)
                if not _is_int(v) or v < 0:
                    errs.append(f"changes.{f} must be a non-negative integer")
            for f in ("insertions", "deletions"):
                v = changes.get(f)
                if v is not None and (not _is_int(v) or v < 0):
                    errs.append(f"changes.{f} must be a non-negative integer or null")

    if "tests" in record:
        tests = record["tests"]
        if not isinstance(tests, dict):
            errs.append("tests must be an object")
        else:
            _require_present(tests, "tests", ("passing", "total"), errs)
            added = tests.get("added")
            if not _is_int(added) or added < 0:
                errs.append("tests.added must be a non-negative integer")
            for f in ("passing", "total"):
                v = tests.get(f)
                if v is not None and (not _is_int(v) or v < 0):
                    errs.append(f"tests.{f} must be a non-negative integer or null")
            p, t = tests.get("passing"), tests.get("total")
            if _is_int(p) and _is_int(t) and p > t:
                errs.append("tests.passing cannot exceed tests.total")

    if "gates" in record:
        gates = record["gates"]
        if not isinstance(gates, list):
            errs.append("gates must be a list")
        else:
            for i, g in enumerate(gates):
                if not isinstance(g, dict):
                    errs.append(f"gates[{i}] must be an object")
                    continue
                if not _is_str(g.get("step")):
                    errs.append(f"gates[{i}].step must be a string")
                if not _is_str(g.get("name")):
                    errs.append(f"gates[{i}].name must be a string")
                fnd, rsv = g.get("findings"), g.get("resolved")
                if not _is_int(fnd) or fnd < 0:
                    errs.append(f"gates[{i}].findings must be a non-negative integer")
                if not _is_int(rsv) or rsv < 0:
                    errs.append(f"gates[{i}].resolved must be a non-negative integer")
                if g.get("status") not in GATE_STATUSES:
                    errs.append(
                        f"gates[{i}].status must be one of {sorted(GATE_STATUSES)}")
                if _is_int(fnd) and _is_int(rsv) and rsv > fnd:
                    errs.append(f"gates[{i}].resolved cannot exceed gates[{i}].findings")
                # #473: additive per-gate severity split (feeds review_findings_p90). Optional;
                # BOTH-or-neither (by KEY PRESENCE — an explicit null is NOT a valid value), each
                # a non-negative int, and their sum must not exceed findings. Legacy gates
                # without both keys are tolerated.
                has_c, has_h = "findings_critical" in g, "findings_high" in g
                if has_c != has_h:
                    errs.append(f"gates[{i}] findings_critical and findings_high are "
                                f"both-or-neither (by key presence)")
                elif has_c and has_h:
                    crit, high = g.get("findings_critical"), g.get("findings_high")
                    for f in ("findings_critical", "findings_high"):
                        v = g.get(f)
                        if not _is_int(v) or v < 0:
                            errs.append(f"gates[{i}].{f} must be a non-negative integer")
                    if _is_int(crit) and _is_int(high) and _is_int(fnd) and crit + high > fnd:
                        errs.append(f"gates[{i}] findings_critical+findings_high "
                                    f"cannot exceed findings")
                if "reviewer_kind" in g and (not _is_str(g["reviewer_kind"])
                                              or g["reviewer_kind"] not in REVIEWER_KINDS):
                    errs.append(f"gates[{i}].reviewer_kind must be one of "
                                f"{sorted(REVIEWER_KINDS)}")
            steps = [g.get("step") for g in gates if isinstance(g, dict)]
            dups = sorted({s for s in steps if _is_str(s) and steps.count(s) > 1})
            if dups:
                errs.append(f"gates have duplicate step id(s) {dups}; each gate "
                            f"must have a distinct step (Tier-2 keys on step)")

    if "security_scan" in record:
        sec = record["security_scan"]
        if not isinstance(sec, dict):
            errs.append("security_scan must be an object")
        else:
            if not isinstance(sec.get("ran"), bool):
                errs.append("security_scan.ran must be a boolean")
            for f in ("blocking_resolved", "advisory"):
                v = sec.get(f)
                if not _is_int(v) or v < 0:
                    errs.append(f"security_scan.{f} must be a non-negative integer")
            skipped = sec.get("skipped")
            if not isinstance(skipped, list) or not all(_is_str(s) for s in skipped):
                errs.append("security_scan.skipped must be a list of strings")
            elif strict:
                bad = [s for s in skipped if s not in SCANNER_KINDS]
                if bad:
                    errs.append(
                        f"security_scan.skipped entries must be scanner kinds "
                        f"{sorted(SCANNER_KINDS)} (got {bad}) — record the KIND, not a "
                        f"free-text reason (#116)")
            # A scan that did NOT run cannot have resolved findings or skipped
            # scanners. render shows only "not run" for ran=false, so accepting
            # nonzero data here would hide it AND be self-contradictory telemetry.
            if sec.get("ran") is False and (
                    sec.get("blocking_resolved") or sec.get("advisory")
                    or sec.get("skipped")):
                errs.append("security_scan: ran=false requires blocking_resolved=0, "
                            "advisory=0, and empty skipped")

    if "loop_backs" in record:
        lb = record["loop_backs"]
        if not isinstance(lb, dict):
            errs.append("loop_backs must be an object")
        else:
            used, budget = lb.get("used"), lb.get("budget")
            for name, v in (("used", used), ("budget", budget)):
                if not _is_int(v) or v < 0:
                    errs.append(f"loop_backs.{name} must be a non-negative integer")
            if _is_int(used) and _is_int(budget) and used > budget:
                errs.append("loop_backs.used cannot exceed loop_backs.budget")

    if "outcome" in record:
        out = record["outcome"]
        if not isinstance(out, dict):
            errs.append("outcome must be an object")
        else:
            _require_present(out, "outcome", ("pr_number", "pr_url", "merged"), errs)
            pn = out.get("pr_number")
            if pn is not None and not _is_int(pn):
                errs.append("outcome.pr_number must be an integer or null")
            pu = out.get("pr_url")
            if pu is not None and not _is_str(pu):
                errs.append("outcome.pr_url must be a string or null")
            mg = out.get("merged")
            if mg is not None and not isinstance(mg, bool):
                errs.append("outcome.merged must be a boolean or null")
            if out.get("ci") not in CI_STATUSES:
                errs.append(f"outcome.ci must be one of {sorted(CI_STATUSES)}")
            if out.get("deploy") not in DEPLOY_STATUSES:
                errs.append(f"outcome.deploy must be one of {sorted(DEPLOY_STATUSES)}")

    if "follow_ups" in record:
        fu = record["follow_ups"]
        if not isinstance(fu, list) or not all(_is_str(s) for s in fu):
            errs.append("follow_ups must be a list of strings")

    # `extra` carries ordered, workflow-specific labeled lines (e.g. WF3's Root
    # Cause / Fix) that ride along in the human render without bloating the
    # uniform core schema that Tier-2 aggregates across workflows. Optional.
    if "extra" in record:
        ex = record["extra"]
        if not isinstance(ex, list):
            errs.append("extra must be a list of {label, value} objects")
        else:
            for i, item in enumerate(ex):
                if not isinstance(item, dict):
                    errs.append(f"extra[{i}] must be an object")
                    continue
                if not (_is_str(item.get("label")) and item["label"].strip()):
                    errs.append(f"extra[{i}].label must be a non-empty string")
                if not _is_str(item.get("value")):
                    errs.append(f"extra[{i}].value must be a string")

    # `verification_deferred` (#138) — a STRUCTURED list, not a count. A bare
    # count could be satisfied while the required per-task evidence (reason /
    # local proxy / target check) is missing, so each item must carry all four
    # fields; the completion gate keys on task_id via
    # plan_lib.assert_deferrals_recorded. Optional: absent → old records valid.
    if "verification_deferred" in record:
        vd = record["verification_deferred"]
        if not isinstance(vd, list):
            errs.append("verification_deferred must be a list of "
                        "{task_id, reason, local_proxy, target_check} objects")
        else:
            seen_ids = []
            for i, item in enumerate(vd):
                if not isinstance(item, dict):
                    errs.append(f"verification_deferred[{i}] must be an object")
                    continue
                for f in ("task_id", "reason", "local_proxy", "target_check"):
                    if not (_is_str(item.get(f)) and item[f].strip()):
                        errs.append(f"verification_deferred[{i}].{f} must be a non-empty string")
                tid = item.get("task_id")
                if _is_str(tid):
                    seen_ids.append(tid)
            dups = sorted({t for t in seen_ids if seen_ids.count(t) > 1})
            if dups:
                errs.append(f"verification_deferred has duplicate task_id(s) {dups}")

    # `usage` (#155 Task 1) — OPTIONAL top-level telemetry: absent → old records
    # stay valid, but present is strict (deliberate-null-vs-dropped-field, same
    # philosophy as elsewhere in this validator). Rendering is _render_usage_line;
    # aggregation lands with #115 (fleet rollups).
    if "usage" in record:
        usage = record["usage"]
        if not isinstance(usage, dict):
            errs.append("usage must be an object")
        else:
            _require_present(usage, "usage",
                              ("input_tokens", "output_tokens", "cost_estimate_usd",
                               "wall_clock_s", "model_mix"), errs)
            for f in ("input_tokens", "output_tokens"):
                v = usage.get(f)
                if v is not None and (not _is_int(v) or v < 0):
                    errs.append(f"usage.{f} must be a non-negative integer or null")
            for f in ("cost_estimate_usd", "wall_clock_s"):
                v = usage.get(f)
                if v is not None and (not _is_num(v) or v < 0):
                    errs.append(f"usage.{f} must be a non-negative number or null")
            mix = usage.get("model_mix")
            if mix is not None and not isinstance(mix, dict):
                errs.append("usage.model_mix must be an object or null")
            elif isinstance(mix, dict):
                for model, counts in mix.items():
                    if not (_is_str(model) and model.strip()):
                        errs.append("usage.model_mix keys must be non-empty strings")
                        continue
                    if not isinstance(counts, dict):
                        errs.append(f"usage.model_mix['{model}'] must be an object")
                        continue
                    _require_present(counts, f"usage.model_mix['{model}']",
                                      ("input_tokens", "output_tokens"), errs)
                    for f in ("input_tokens", "output_tokens"):
                        v = counts.get(f)
                        if v is not None and (not _is_int(v) or v < 0):
                            errs.append(f"usage.model_mix['{model}'].{f} must be "
                                        f"a non-negative integer or null")
            # capture_status (#189): OPTIONAL, but present-is-strict — membership in
            # CAPTURE_STATUS_VALUES, fail-closed on anything else (non-strings, case
            # variants, null). When it claims "captured", the tokens MUST be real
            # (non-null and summing > 0) — this is what makes a captured claim with a
            # null/zero measurement (the #155 failure mode) impossible to persist.
            if "capture_status" in usage:
                cs = usage.get("capture_status")
                if not _is_str(cs) or cs not in CAPTURE_STATUS_VALUES:
                    errs.append("usage.capture_status must be one of "
                                f"{sorted(CAPTURE_STATUS_VALUES)}")
                elif cs == "captured":
                    it, ot = usage.get("input_tokens"), usage.get("output_tokens")
                    # captured claims a real measurement: input MUST be positive
                    # (every real turn processes prompt/cache input), output non-negative.
                    # input>0 (not just sum>0) also rejects a captured input=0/output=N dict.
                    if not _is_int(it) or not _is_int(ot) or it <= 0 or ot < 0:
                        errs.append("usage.capture_status 'captured' requires "
                                    "non-null input_tokens > 0 and output_tokens >= 0")

    # `timing` (#506) — OPTIONAL top-level per-step timing telemetry from the
    # step-state history: absent → old records stay valid (no schema bump);
    # present → strict. duration_s is non-negative-int-or-null (null = an
    # open-ended last event — an honest gap, never a fabricated duration).
    if "timing" in record:
        timing = record["timing"]
        if not isinstance(timing, dict):
            errs.append("timing must be an object")
        else:
            _require_present(timing, "timing",
                              ("status", "idle_gap_threshold_s", "steps",
                               "phases", "total_s"), errs)
            st = timing.get("status")
            if not _is_str(st) or st not in TIMING_STATUS_VALUES:
                errs.append(f"timing.status must be one of {sorted(TIMING_STATUS_VALUES)}")
            th = timing.get("idle_gap_threshold_s")
            if not _is_int(th) or th <= 0:
                errs.append("timing.idle_gap_threshold_s must be a positive integer")
            steps = timing.get("steps")
            if not isinstance(steps, list):
                errs.append("timing.steps must be a list")
            else:
                for i, entry in enumerate(steps):
                    if not isinstance(entry, dict):
                        errs.append(f"timing.steps[{i}] must be an object")
                        continue
                    _require_present(entry, f"timing.steps[{i}]",
                                      ("step", "title", "entered_at",
                                       "duration_s", "idle_gap"), errs)
                    d = entry.get("duration_s")
                    if d is not None and (not _is_int(d) or d < 0):
                        errs.append(f"timing.steps[{i}].duration_s must be a "
                                    "non-negative integer or null")
                    if not isinstance(entry.get("idle_gap"), bool):
                        errs.append(f"timing.steps[{i}].idle_gap must be a bool")
            phases = timing.get("phases")
            if not isinstance(phases, dict):
                errs.append("timing.phases must be an object")
            else:
                for name, val in phases.items():
                    if not (_is_str(name) and name.strip()) or not _is_num(val) or val < 0:
                        errs.append("timing.phases entries must map non-empty "
                                    "strings to non-negative numbers")
                        break
            tot = timing.get("total_s")
            if tot is not None and (not _is_num(tot) or tot < 0):
                errs.append("timing.total_s must be a non-negative number or null")
            if "skipped_lines" in timing:
                sk = timing.get("skipped_lines")
                if not _is_int(sk) or sk < 0:
                    errs.append("timing.skipped_lines must be a non-negative integer")

    # `goal_guard` (#156, AC6) — OPTIONAL top-level field, same validated-optional
    # pattern as `reviewer_kind`: absent → old records stay valid (forward-
    # compatible addition, no schema version bump); present → strict membership
    # in GOAL_GUARD_VALUES, fail-closed on anything else (including non-strings).
    # `fired` is MANUAL-ONLY: no automated signal currently reaches the
    # orchestrator when the Stop-hook's goal evaluator blocks a premature quit,
    # so this validator can accept the value but nothing yet sets it on its own.
    if "goal_guard" in record:
        gg = record["goal_guard"]
        if not _is_str(gg) or gg not in GOAL_GUARD_VALUES:
            errs.append(f"goal_guard must be one of {sorted(GOAL_GUARD_VALUES)}")

    # `dispatches` (#329) — OPTIONAL top-level list of per-subagent dispatch
    # records, same validated-optional pattern as `usage`/`goal_guard`: absent →
    # old records stay valid (forward-compatible, no schema bump); present is
    # strict — a list of dicts, each carrying all 6 keys with controlled-vocab
    # role/outcome/resolution (fail-closed on non-strings, case variants, null),
    # a non-empty subagent_type, and string-or-null model/effort.
    if "dispatches" in record:
        dispatches = record["dispatches"]
        if not isinstance(dispatches, list):
            errs.append("dispatches must be a list")
        else:
            for i, item in enumerate(dispatches):
                if not isinstance(item, dict):
                    errs.append(f"dispatches[{i}] must be an object")
                    continue
                _require_present(item, f"dispatches[{i}]",
                                  ("role", "subagent_type", "model", "effort",
                                   "outcome", "resolution"), errs)
                for field, vocab in (("role", DISPATCH_ROLES),
                                     ("outcome", DISPATCH_OUTCOMES),
                                     ("resolution", DISPATCH_RESOLUTIONS)):
                    v = item.get(field)
                    if not _is_str(v) or v not in vocab:
                        errs.append(f"dispatches[{i}].{field} must be one of "
                                    f"{sorted(vocab)}")
                st = item.get("subagent_type")
                if not (_is_str(st) and st.strip()):
                    errs.append(f"dispatches[{i}].subagent_type must be a "
                                "non-empty string")
                for field in ("model", "effort"):
                    v = item.get(field)
                    if v is not None and not _is_str(v):
                        errs.append(f"dispatches[{i}].{field} must be a string "
                                    "or null")
                # #420 routing telemetry — OPTIONAL per-dispatch fields, validated
                # only-if-present so pre-#420 6-key entries + old records stay rc=0
                # (populated once #417 wires the executor). Excludes prompt contents.
                for field in ("preferred_model", "actual_model", "fallback_reason"):
                    if field in item and item[field] is not None and not _is_str(item[field]):
                        errs.append(f"dispatches[{i}].{field} must be a string or null")
                for field in ("queued_ms", "concurrency"):
                    if field in item and item[field] is not None:
                        v = item[field]
                        # non-negative: a queue-wait / permit-count is never < 0 (matches every
                        # other count/duration field in this validator; Step-11 finding).
                        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                            errs.append(f"dispatches[{i}].{field} must be a non-negative int or null")
                if "selector" in item and item["selector"] is not None:
                    sel = item["selector"]
                    if not isinstance(sel, dict):
                        errs.append(f"dispatches[{i}].selector must be an object or null")
                    else:
                        for sk in ("risk_level", "complexity", "ceiling"):
                            if sk in sel and sel[sk] is not None and not _is_str(sel[sk]):
                                errs.append(f"dispatches[{i}].selector.{sk} must be a string or null")

    return errs


# --- normalize_record (pure) -----------------------------------------------

def normalize_record(record, *, now, schema_version=SCHEMA_VERSION) -> dict:
    """Return a copy stamped with schema_version + generated_at and with optional
    fields defaulted. `now` is passed in (not read from the clock here) so the
    function stays pure and deterministic in tests. Only ever called on a record
    that already passed validate_record, so it can assume a dict."""
    out = copy.deepcopy(record)
    out["schema_version"] = schema_version
    out["generated_at"] = now
    out.setdefault("follow_ups", [])
    out.setdefault("extra", [])
    return out


def worker_token_share(mix, worker_models) -> "float | None":
    """Derived worker-token-share (#315, CMA 'plan big, execute small' cookbook:
    https://github.com/anthropics/claude-cookbooks/blob/main/managed_agents/CMA_plan_big_execute_small.ipynb).

    A ``model_mix`` entry counts as a WORKER iff any configured ``modelRouting``
    value (short name, e.g. "sonnet"/"opus") is a case-insensitive substring of
    the model id; everything else is orchestrator. Returns worker input_tokens /
    total input_tokens, or None when underivable (malformed/empty mix, no
    worker_models config, zero/unparseable totals). Best-effort like
    _render_usage_line: never raises on unvalidated records. Known limitation:
    when the orchestrator's own family equals a routed value, its tokens count
    as worker — the rule is config-derived, not session-aware."""
    if not isinstance(mix, dict) or not mix or not worker_models:
        return None
    names = [w.lower() for w in worker_models if isinstance(w, str) and w]
    if not names:
        return None
    total = worker = 0
    for model, counts in mix.items():
        if not isinstance(model, str) or not isinstance(counts, dict):
            continue
        tokens = counts.get("input_tokens")
        if not _is_int(tokens) or tokens < 0:
            continue
        total += tokens
        if any(n in model.lower() for n in names):
            worker += tokens
    if total <= 0:
        return None
    return worker / total


def _render_usage_line(usage: dict, worker_models=None) -> str:
    """Render the best-effort '- Usage: ...' line (#155 Task 3). `usage` is
    already coerced by _as_dict and confirmed truthy by the caller. Every inner
    value is isinstance-guarded (never trusted) since render_summary runs on
    UNVALIDATED records: a wrong-typed field degrades to '?' or is dropped
    rather than raising or leaking a dict/list repr."""
    it = usage.get("input_tokens")
    ot = usage.get("output_tokens")
    it_str = str(it) if _is_int(it) else "?"
    ot_str = str(ot) if _is_int(ot) else "?"
    line = f"- Usage: {it_str} in / {ot_str} out tokens"

    cost = usage.get("cost_estimate_usd")
    if _is_num(cost):
        line += f", ~${cost}"

    wall = usage.get("wall_clock_s")
    if _is_num(wall):
        line += f", {wall}s wall"

    mix = usage.get("model_mix")
    if isinstance(mix, dict) and mix:
        parts = []
        for model, counts in mix.items():
            if not isinstance(model, str) or not isinstance(counts, dict):
                continue  # malformed entry: skip silently, best-effort
            min_ = counts.get("input_tokens")
            mout = counts.get("output_tokens")
            min_str = str(min_) if _is_int(min_) else "?"
            mout_str = str(mout) if _is_int(mout) else "?"
            parts.append(f"{model}: {min_str}/{mout_str}")
        if parts:
            line += f" ({', '.join(parts)})"

    share = worker_token_share(usage.get("model_mix"), worker_models)
    if share is not None:
        line += f", worker-share {round(share * 100)}%"

    return line


# --- render_summary (pure, best-effort) ------------------------------------

def render_summary(record, worker_models=None) -> str:
    """Render the human "WF COMPLETE" block. Best-effort and total: never raises
    on a partial/invalid/non-dict record, so the user always gets Step 16 output
    even when the record failed validation."""
    r = _as_dict(record)
    wf = r.get("workflow") or "workflow"
    label = _WF_LABELS.get(wf, str(wf).upper())
    issue = _as_dict(r.get("issue"))
    tests = _as_dict(r.get("tests"))
    gates = _as_list(r.get("gates"))
    sec = _as_dict(r.get("security_scan"))
    lb = _as_dict(r.get("loop_backs"))
    out = _as_dict(r.get("outcome"))
    follow = _as_list(r.get("follow_ups"))
    extra = _as_list(r.get("extra"))

    header = f"{label} COMPLETE"
    lines = [header, "=" * len(header), ""]

    pr_num, pr_url = out.get("pr_number"), out.get("pr_url")
    if pr_url and pr_num is not None:
        pr_str = f"{pr_url} (PR #{pr_num})"
    elif pr_url:
        pr_str = pr_url
    elif pr_num is not None:
        pr_str = f"PR #{pr_num}"
    else:
        pr_str = "(no PR)"
    lines.append(f"GitHub PR: {pr_str}")

    inum, itype = issue.get("number"), issue.get("type", "?")
    lines.append(
        f"GitHub Issue: #{inum} ({itype})" if inum is not None
        else f"GitHub Issue: (none, {itype})")
    for item in extra:
        if isinstance(item, dict):
            label, value = item.get("label"), item.get("value")
            # best-effort: skip a malformed pair rather than leak "None:" or a
            # dict/list repr into the human summary (render runs unvalidated).
            if isinstance(label, str) and isinstance(value, str):
                lines.append(f"{label}: {value}")
    lines.append("")

    lines.append("Quality Gates:")
    for g in gates:
        if not isinstance(g, dict):
            continue
        lines.append(
            f"- Step {g.get('step', '?')} ({g.get('name', '?')}): "
            f"{g.get('findings', '?')} findings, {g.get('resolved', '?')} resolved "
            f"[{g.get('status', '?')}]")
    if sec.get("ran"):
        skipped = [str(s) for s in _as_list(sec.get("skipped"))]
        lines.append(
            f"- Step 11.5 (Security Scan): {sec.get('blocking_resolved', '?')} "
            f"blocking resolved / {sec.get('advisory', '?')} advisory / "
            f"skipped: {', '.join(skipped) if skipped else 'none'}")
    else:
        # A workflow with no tool-based security scan (e.g. WF3) — don't
        # reference a Step 11.5 that never happened.
        lines.append("- Security Scan: not run")
    lines.append("")

    lines.append("Verification:")
    passing, total = tests.get("passing"), tests.get("total")
    if passing is not None and total is not None:
        lines.append(f"- Tests: {tests.get('added', 0)} added, {passing}/{total} passing")
    else:
        lines.append(f"- Tests: {tests.get('added', 0)} added")
    usage = _as_dict(r.get("usage"))
    if usage:
        lines.append(_render_usage_line(usage, worker_models))
    timing = _as_dict(r.get("timing"))
    if timing:
        phases = _as_dict(timing.get("phases"))
        phase_bits = ", ".join(f"{k} {int(v)}s" for k, v in phases.items() if v)
        lines.append(f"- Timing: {timing.get('status', '?')}"
                     + (f" — {phase_bits}" if phase_bits else ""))
    deferred = r.get("verification_deferred")
    if isinstance(deferred, list) and deferred:
        lines.append("- Verification deferred (must be checked on target):")
        for d in deferred:
            if isinstance(d, dict):
                lines.append(f"  - {d.get('task_id', '?')} — {d.get('reason', '')}")
    lines.append(f"- CI: {out.get('ci', 'n/a')}")
    lines.append(f"- Deploy: {out.get('deploy', 'n/a')}")
    lines.append("")

    lines.append(f"Loop-backs used: {lb.get('used', '?')} / {lb.get('budget', '?')}")

    if follow:
        lines.append("")
        lines.append("Follow-up items:")
        lines.extend(f"- {item}" for item in follow)

    return "\n".join(lines)


# --- I/O (thin) ------------------------------------------------------------

def resolve_store_path(store_arg, env, project_root) -> str:
    """Store path precedence: --store flag > RAWGENTIC_RUN_RECORD_STORE env >
    default <project-root>/docs/measurements/run_records.jsonl."""
    if store_arg:
        return store_arg
    env_val = (env or {}).get(STORE_ENV)
    if env_val:
        return env_val
    return str(Path(project_root, *DEFAULT_STORE_RELPATH))


def load_record_file(path) -> dict:
    """Read + JSON-parse the run-record file. Fail-closed: an unreadable or
    non-JSON file raises WorkSummaryError rather than producing an empty run."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, ValueError) as exc:   # ValueError: embedded NUL in path
        raise WorkSummaryError(f"cannot read record file {path}: {exc}")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise WorkSummaryError(f"record file {path} is not valid JSON: {exc}")


def persist_record(record, store_path) -> None:
    """Append one compact JSON line (one record per line) to the JSONL store,
    creating the parent directory if needed."""
    p = Path(store_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- aggregate: fail-closed JSONL reader (#94) -----------------------------

def _looks_iso(s) -> bool:
    """Cheap shape check for an ISO-8601 date/timestamp that sorts lexically
    (the writer stamps '%Y-%m-%dT%H:%M:%SZ'): a leading YYYY-MM-DD with digit
    groups and '-' separators. Used to fail-close on a record whose
    generated_at the aggregator dates/filters on but validate_record never
    checks (it is writer-stamped, not workflow-supplied)."""
    if not _is_str(s) or len(s) < 10:
        return False
    d = s[:10]
    return (d[4] == "-" and d[7] == "-" and d[:4].isdigit()
            and d[5:7].isdigit() and d[8:10].isdigit())


def load_store(path) -> tuple:
    """Read a JSONL run-record store -> (records, excluded).

    Fail-closed reader (mirrors the fail-closed writer): each non-blank line is
    JSON-parsed and run through validate_record; a parse-error, non-object,
    schema-invalid, or missing/non-ISO `generated_at` line is EXCLUDED and
    appended to `excluded` as a "line N: <reason>" string — never silently
    averaged in nor silently dropped. Blank lines are ignored (not errors).

    An unreadable/missing file (or a NUL in the path) raises WorkSummaryError
    (usage error). An empty file returns ([], [])."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except (OSError, ValueError) as exc:   # ValueError: embedded NUL in path
        raise WorkSummaryError(f"cannot read store {path}: {exc}")
    records, excluded = [], []
    for i, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            excluded.append(f"line {i}: not valid JSON ({exc})")
            continue
        errs = validate_record(obj)
        if errs:
            excluded.append(f"line {i}: schema-invalid ({errs[0]})")
            continue
        if not _looks_iso(obj.get("generated_at")):
            excluded.append(f"line {i}: missing or non-ISO generated_at")
            continue
        records.append(obj)
    return records, excluded


def load_stores(store_specs, *, tolerate_missing=True) -> tuple:
    """Pool multiple run-record stores into one record list (#115 — fleet view).

    `store_specs` is a list of `(store_path, origin)` pairs; each loaded record is
    origin-tagged with `_source = origin` at load time (read-side only, never persisted,
    so no schema change) — this is what a `--group-by source` fleet slice keys on.

    Returns `(records, excluded, missing)`:
    - `records`  — pooled, origin-tagged records from every readable store.
    - `excluded` — per-line fail-closed exclusions (from each store's `load_store`),
      each prefixed with its origin so a bad line is traceable to its store.
    - `missing`  — stores that could not be read at all. When `tolerate_missing` (the
      fleet default), an unreadable store is appended here (skip-with-visible-count),
      NOT fatal — the store-level analog of the per-line fail-closed reader. When
      `tolerate_missing=False` (a single explicitly-named `--store`), the first
      unreadable store re-raises `WorkSummaryError` (single-store parity, exit 2)."""
    records, excluded, missing = [], [], []
    for path, origin in store_specs:
        try:
            recs, exc = load_store(path)
        except WorkSummaryError as e:
            if tolerate_missing:
                missing.append(f"{origin} ({path}): {e}")
                continue
            raise
        for r in recs:
            r["_source"] = origin
        records.extend(recs)
        excluded.extend(f"[{origin}] {x}" for x in exc)
    return records, excluded, missing


def stores_from_workspace(workspace_path) -> tuple:
    """Resolve each ACTIVE project's default run-record store from a
    `.rawgentic_workspace.json` (#115 — `--workspace` fleet mode). Returns
    `(specs, skipped)`: `specs` is a list of `(store_path, project_name)` pairs
    (origin = project name); `skipped` is a list of human-readable reasons for any
    **active** project that could NOT be resolved to a store path (a malformed/absent
    `path`) — surfaced so a silently-dropped active project can't hide (mirrors the
    per-store fail-closed policy). Inactive projects are ignored (not skipped-with-reason).
    A relative project path resolves against the workspace file's directory. Fail-closed:
    an unreadable/malformed workspace raises `WorkSummaryError`."""
    try:
        data = json.loads(Path(workspace_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise WorkSummaryError(f"cannot read workspace {workspace_path}: {e}")
    root = Path(workspace_path).resolve().parent
    out, skipped = [], []
    for proj in _as_list(data.get("projects")):
        if not isinstance(proj, dict) or not proj.get("active"):
            continue
        ppath = proj.get("path")
        name = proj.get("name") if _is_str(proj.get("name")) else None
        if not _is_str(ppath) or not ppath:
            skipped.append(f"{name or '<unnamed>'}: active project has no usable 'path'")
            continue
        base = Path(ppath)
        if not base.is_absolute():
            base = root / base
        out.append((str(base.joinpath(*DEFAULT_STORE_RELPATH)), name or ppath))
    return out, skipped


def filter_since(records, since):
    """Keep records whose `generated_at` >= `since` (lexical ISO compare —
    correct for the writer's Zulu format vs a bare YYYY-MM-DD, where the full
    timestamp sorts after the bare date). Records reaching here already carry a
    valid generated_at (load_store fail-closes otherwise). `since` falsy -> all."""
    if not since:
        return list(records)
    return [r for r in records if str(r.get("generated_at", "")) >= since]


# --- aggregate: pure metrics (#94) -----------------------------------------

def _mean(values):
    """Mean of the non-None numbers, or None if there are none (0-denominator
    -> null, consistent with _rate)."""
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _rate(numerator, denominator):
    """numerator/denominator, or None when denominator is 0 (never divide by
    zero; an empty denominator is 'not measurable', rendered as n/a)."""
    return (numerator / denominator) if denominator else None


def aggregate_records(records) -> dict:
    """Roll a list of (already-validated) run-records up into the aggregate
    metric object: gate effectiveness, loop-backs, outcome rates, effort means.

    Gate identity is keyed on `step` (the stable id the writer already enforces
    unique within a record), NOT step+name: real stores carry the same gate
    under drifting names (e.g. '4: Design Critique' vs '4: design critique
    (3-judge + codex)'), so keying on step+name would fragment the metric. The
    distinct names are carried as a `names` label list. (Documented deviation
    from issue #94 AC2; see docs/run-records.md.)"""
    n = len(records)

    # gate effectiveness, keyed on step
    gate_acc = {}
    for r in records:
        for g in _as_list(r.get("gates")):
            if not isinstance(g, dict):
                continue
            step = g.get("step")
            if not _is_str(step):
                continue
            a = gate_acc.setdefault(step, {"names": [], "runs_present": 0,
                                           "runs_with_findings": 0,
                                           "total_findings": 0,
                                           "total_resolved": 0})
            name = g.get("name")
            if _is_str(name) and name not in a["names"]:
                a["names"].append(name)
            a["runs_present"] += 1
            fnd = g.get("findings") if _is_int(g.get("findings")) else 0
            rsv = g.get("resolved") if _is_int(g.get("resolved")) else 0
            a["total_findings"] += fnd
            a["total_resolved"] += rsv
            if fnd > 0:
                a["runs_with_findings"] += 1
    gates = {}
    for step, a in gate_acc.items():
        gates[step] = {
            "names": a["names"],
            "runs_present": a["runs_present"],
            "hit_rate": _rate(a["runs_with_findings"], a["runs_present"]),
            "total_findings": a["total_findings"],
            "total_resolved": a["total_resolved"],
            "resolution_rate": _rate(a["total_resolved"], a["total_findings"]),
            "mean_findings_per_run": _rate(a["total_findings"], a["runs_present"]),
        }

    # loop-backs
    used_vals, cap_denom, cap_hits = [], 0, 0
    for r in records:
        lb = r.get("loop_backs")
        if not isinstance(lb, dict):
            continue
        if _is_int(lb.get("used")):
            used_vals.append(lb["used"])
        if _is_int(lb.get("budget")) and lb["budget"] > 0:
            cap_denom += 1
            if _is_int(lb.get("used")) and lb["used"] == lb["budget"]:
                cap_hits += 1
    loop_backs = {"mean_used": _mean(used_vals),
                  "pct_hit_cap": _rate(cap_hits, cap_denom),
                  "cap_runs_considered": cap_denom}

    # outcomes
    ci_denom = ci_pass = merge_denom = merge_yes = 0
    dep_denom = dep_ok = sec_denom = sec_blocked = 0
    skip_freq = {}
    for r in records:
        out = _as_dict(r.get("outcome"))
        if out.get("ci") in ("passed", "failed"):
            ci_denom += 1
            if out["ci"] == "passed":
                ci_pass += 1
        if isinstance(out.get("merged"), bool):
            merge_denom += 1
            if out["merged"]:
                merge_yes += 1
        if out.get("deploy") in ("success", "manual", "failed"):
            dep_denom += 1
            if out["deploy"] == "success":
                dep_ok += 1
        sec = _as_dict(r.get("security_scan"))
        if sec.get("ran") is True:
            sec_denom += 1
            if _is_int(sec.get("blocking_resolved")) and sec["blocking_resolved"] > 0:
                sec_blocked += 1
            for kind in _as_list(sec.get("skipped")):
                if _is_str(kind):
                    skip_freq[kind] = skip_freq.get(kind, 0) + 1
    outcomes = {
        "ci_pass_rate": _rate(ci_pass, ci_denom), "ci_runs_considered": ci_denom,
        "merge_rate": _rate(merge_yes, merge_denom),
        "merge_runs_considered": merge_denom,
        "deploy_success_rate": _rate(dep_ok, dep_denom),
        "deploy_runs_considered": dep_denom,
        "security_blocked_rate": _rate(sec_blocked, sec_denom),
        "security_runs_considered": sec_denom,
        "scanner_skip_freq": skip_freq,
    }

    # effort proxies (means; null insertions/deletions excluded from their mean)
    def _col(section, field):
        return [r[section][field] for r in records
                if isinstance(r.get(section), dict) and _is_int(r[section].get(field))]
    effort = {
        "mean_files_changed": _mean(_col("changes", "files_changed")),
        "mean_insertions": _mean(_col("changes", "insertions")),
        "mean_deletions": _mean(_col("changes", "deletions")),
        "mean_commits": _mean(_col("changes", "commits")),
        "mean_tests_added": _mean(_col("tests", "added")),
    }
    result = {"n": n, "gates": gates, "loop_backs": loop_backs,
              "outcomes": outcomes, "effort": effort}

    # dispatches (#329) — omitted entirely when no record carries the key at
    # all (present-is-present: a record with dispatches: [] still counts).
    runs_with_dispatches = 0
    total = by_role = by_model = None
    dead = fallback = 0
    for r in records:
        entries = r.get("dispatches")
        if not isinstance(entries, list):
            continue
        if by_role is None:
            total, by_role, by_model = 0, {}, {}
        runs_with_dispatches += 1
        for e in entries:
            if not isinstance(e, dict):
                continue
            total += 1
            role = e.get("role")
            if _is_str(role):
                by_role[role] = by_role.get(role, 0) + 1
            model = e.get("model") if _is_str(e.get("model")) else "(none)"
            by_model[model] = by_model.get(model, 0) + 1
            if e.get("outcome") == "dead":
                dead += 1
            if e.get("resolution") == "fallback":
                fallback += 1
    if by_role is not None:
        result["dispatches"] = {
            "runs_with_dispatches": runs_with_dispatches,
            "total": total,
            "by_role": by_role,
            "by_model": by_model,
            "dead_rate": _rate(dead, total),
            "fallback_rate": _rate(fallback, total),
        }
    return result


_GROUP_KEYS = {
    "workflow": lambda r: r.get("workflow"),
    "version": lambda r: r.get("workflow_version"),
    "type": lambda r: _as_dict(r.get("issue")).get("type"),
    "complexity": lambda r: _as_dict(r.get("issue")).get("complexity"),
    # #115: origin store/project, tagged at load by load_stores (read-side, no schema
    # change). Only meaningful for a pooled fleet aggregate; single-store runs bucket
    # all records under one source.
    "source": lambda r: r.get("_source"),
}


def aggregate_grouped(records, group_by) -> dict:
    """Partition records by `group_by` (one of _GROUP_KEYS) and aggregate each
    partition. A missing/null group value buckets under '(none)'."""
    keyfn = _GROUP_KEYS[group_by]
    groups = {}
    for r in records:
        k = keyfn(r)
        groups.setdefault(k if _is_str(k) else "(none)", []).append(r)
    return {k: aggregate_records(v) for k, v in groups.items()}


# --- aggregate: Markdown render (#94) --------------------------------------

def _fmt_pct(x):
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _fmt_num(x):
    if x is None:
        return "n/a"
    return f"{x:.1f}" if isinstance(x, float) else str(x)


def _render_one(a) -> list:
    lines = [f"- Records: {a['n']}", "", "### Gate effectiveness (keyed on step)"]
    if a["gates"]:
        lines.append("| Step | Name(s) | Runs | Hit rate | Findings | Resolved | "
                     "Resolution | Mean/run |")
        lines.append("|------|---------|------|----------|----------|----------|"
                     "------------|----------|")
        for step in sorted(a["gates"], key=lambda s: (len(s), s)):
            g = a["gates"][step]
            names = ", ".join(g["names"]) or "?"
            lines.append(
                f"| {step} | {names} | {g['runs_present']} | "
                f"{_fmt_pct(g['hit_rate'])} | {g['total_findings']} | "
                f"{g['total_resolved']} | {_fmt_pct(g['resolution_rate'])} | "
                f"{_fmt_num(g['mean_findings_per_run'])} |")
    else:
        lines.append("(no gates recorded)")
    lb = a["loop_backs"]
    lines += ["", "### Loop-backs",
              f"- Mean used: {_fmt_num(lb['mean_used'])}",
              f"- Hit cap: {_fmt_pct(lb['pct_hit_cap'])} "
              f"(of {lb['cap_runs_considered']} runs with budget>0)"]
    o = a["outcomes"]
    skips = o["scanner_skip_freq"]
    skipstr = ", ".join(f"{k}={v}" for k, v in sorted(skips.items())) if skips else "none"
    lines += ["", "### Outcomes",
              f"- CI pass rate: {_fmt_pct(o['ci_pass_rate'])} (of {o['ci_runs_considered']})",
              f"- Merge rate: {_fmt_pct(o['merge_rate'])} (of {o['merge_runs_considered']})",
              f"- Deploy success rate: {_fmt_pct(o['deploy_success_rate'])} "
              f"(of {o['deploy_runs_considered']})",
              f"- Security blocked-something rate: {_fmt_pct(o['security_blocked_rate'])} "
              f"(of {o['security_runs_considered']})",
              f"- Scanner skips: {skipstr}"]
    e = a["effort"]
    lines += ["", "### Effort (means)",
              f"- Files changed: {_fmt_num(e['mean_files_changed'])}",
              f"- Insertions: {_fmt_num(e['mean_insertions'])}",
              f"- Deletions: {_fmt_num(e['mean_deletions'])}",
              f"- Commits: {_fmt_num(e['mean_commits'])}",
              f"- Tests added: {_fmt_num(e['mean_tests_added'])}"]
    if "dispatches" in a:
        d = a["dispatches"]
        rolestr = ", ".join(f"{k}={v}" for k, v in sorted(d["by_role"].items())) or "none"
        modelstr = ", ".join(f"{k}={v}" for k, v in sorted(d["by_model"].items())) or "none"
        lines += ["", "### Dispatches",
                  f"- Runs with dispatches: {d['runs_with_dispatches']}",
                  f"- Total: {d['total']}",
                  f"- By role: {rolestr}",
                  f"- By model: {modelstr}",
                  f"- Dead rate: {_fmt_pct(d['dead_rate'])}",
                  f"- Fallback rate: {_fmt_pct(d['fallback_rate'])}"]
    return lines


def render_aggregate_markdown(agg, *, group_by=None, excluded=None, since=None) -> str:
    """Render the aggregate metric object as a Markdown report. The excluded
    count is ALWAYS shown so a fail-closed exclusion can never read as a clean run."""
    excluded = excluded or []
    lines = ["# Run-record aggregate", ""]
    if since:
        lines += [f"_Filtered to records on/after {since}._", ""]
    if group_by:
        lines += [f"Grouped by **{group_by}** — {len(agg)} group(s).", ""]
        for key in sorted(agg):
            lines += [f"## {group_by} = {key}", ""] + _render_one(agg[key]) + [""]
    else:
        lines += _render_one(agg)
    lines.append("")
    if excluded:
        lines.append(f"## Excluded lines ({len(excluded)})")
        lines += [f"- {e}" for e in excluded]
    else:
        lines.append("Excluded lines: 0")
    return "\n".join(lines)


# --- CLI -------------------------------------------------------------------

def _resolve_worker_models(project_root) -> "list | None":
    """Best-effort worker-model resolution for #315 — fail-open like
    model_routing_lib: walk up from project_root (≤5 levels) for
    .rawgentic_workspace.json, find the entry whose path basename matches the
    project root's basename, and return its unique modelRouting values.
    Any error/absence → None (the worker-share line is simply omitted)."""
    try:
        root = os.path.abspath(project_root)
        name = os.path.basename(root)
        d = root
        for _ in range(5):
            ws = os.path.join(d, ".rawgentic_workspace.json")
            if os.path.isfile(ws):
                with open(ws, encoding="utf-8") as fh:
                    data = json.load(fh)
                for proj in data.get("projects", []):
                    if not isinstance(proj, dict):
                        continue
                    ppath = str(proj.get("path", ""))
                    if proj.get("name") == name or os.path.basename(ppath.rstrip("/")) == name:
                        routing = proj.get("modelRouting")
                        if isinstance(routing, dict):
                            vals = sorted({str(v) for v in routing.values() if v})
                            return vals or None
                        return None
                return None
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    except Exception:
        pass
    return None


def main(argv=None) -> int:
    """CLI entry point.

    Subcommand:
      summarize  validate the run-record at --record-file, render the human
                 completion summary (or the normalized record with --json), and —
                 if valid — append the record to the store.

    Exit codes:
      0  valid record: summary rendered and (unless --no-persist) persisted
      1  invalid record: summary still rendered, errors on stderr, NOT persisted
      2  usage error / unreadable record file
    """
    parser = argparse.ArgumentParser(prog="work_summary")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser(
        "summarize",
        help="render the WF completion summary + emit/persist the run-record")
    p.add_argument("--record-file", required=True,
                   help="path to the JSON run-record assembled by the workflow")
    p.add_argument("--project-root", required=True,
                   help="active project root (for the default store path)")
    p.add_argument("--store", default=None,
                   help=f"run-record store path (overrides ${STORE_ENV} and "
                        f"the default <project-root>/{'/'.join(DEFAULT_STORE_RELPATH)})")
    p.add_argument("--json", action="store_true",
                   help="emit the normalized record as JSON instead of human text")
    p.add_argument("--no-persist", action="store_true",
                   help="render only; do not append the record to the store")
    p.add_argument("--loopback-counters", default=None,
                   help="path to the run's loopback_counters.json (#512); the "
                        "record's loop_backs.used is cross-checked against its "
                        "'total' (missing file = zero loop-backs). When omitted, "
                        "auto-discovers claude_docs/.wf2-state/<issue>/"
                        "loopback_counters.json relative to the cwd and checks "
                        "only if that file exists")

    pf = sub.add_parser(
        "find",
        help="print the LAST store record for --issue (#392 WF14 batch lookup)")
    pf.add_argument("--issue", required=True, type=int)
    pf.add_argument("--store", default=None,
                    help="store path; defaults via $%s then "
                         "<project-root>/%s" % (STORE_ENV, "/".join(DEFAULT_STORE_RELPATH)))
    pf.add_argument("--project-root", default=".",
                    help="used only for the default store path")

    pa = sub.add_parser(
        "aggregate",
        help="roll a JSONL run-record store up into aggregate Tier-2 metrics")
    pa.add_argument("--store", action="append", default=None,
                    help=f"run-record store path; repeatable to pool multiple stores "
                         f"(#115). Falls back to ${STORE_ENV} when neither --store nor "
                         f"--workspace is given")
    pa.add_argument("--workspace", default=None,
                    help="pool the default store of every ACTIVE project in a "
                         "`.rawgentic_workspace.json` (#115 fleet view)")
    pa.add_argument("--json", action="store_true",
                    help="emit the metric object as JSON instead of Markdown")
    pa.add_argument("--group-by", choices=sorted(_GROUP_KEYS), default=None,
                    help="partition every metric by this dimension "
                         "(version is the cross-skill A/B slice; source is the "
                         "per-project fleet slice)")
    pa.add_argument("--since", default=None,
                    help="only records whose generated_at is on/after this ISO date")
    args = parser.parse_args(argv)

    if args.cmd == "summarize":
        try:
            raw = load_record_file(args.record_file)
        except WorkSummaryError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        workers = _resolve_worker_models(args.project_root)

        errors = validate_record(raw, strict=True)  # #116: new writes must use the controlled vocab
        # #474 detective surface: advisory only — printed, never gates persistence.
        for w in architecture_dispatch_warnings(raw):
            print(w, file=sys.stderr)
        # #512: cross-check loop_backs against the persisted counters state —
        # a schema-valid record hand-populated from in-context memory can be
        # semantically wrong; the counters file is the source of truth.
        if args.loopback_counters is not None:
            counters = load_loopback_counters(args.loopback_counters, explicit=True)
        else:
            issue_no = raw.get("issue", {}).get("number") if isinstance(
                raw.get("issue"), dict) else None
            auto = Path(*LOOPBACK_STATE_RELPATH, str(issue_no),
                        "loopback_counters.json") if issue_no is not None else None
            counters = (load_loopback_counters(auto, explicit=False)
                        if auto is not None else None)
        errors.extend(check_loopback_counters(raw, counters))
        if errors:
            # Best-effort render so the user keeps Step 16 output, but never
            # persist an invalid record. Exit 1 so the skill surfaces the gap.
            print(json.dumps(raw, separators=(",", ":")) if args.json
                  else render_summary(raw, worker_models=workers))
            print("run-record validation failed (NOT persisted):", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1

        record = normalize_record(raw, now=_now())
        # #315: derived field — present-optional, never required by the validator
        usage = record.get("usage")
        if isinstance(usage, dict):
            share = worker_token_share(usage.get("model_mix"), workers)
            if share is not None:
                usage["worker_token_share"] = round(share, 4)
        print(json.dumps(record, separators=(",", ":")) if args.json
              else render_summary(record, worker_models=workers))
        if not args.no_persist:
            store = resolve_store_path(args.store, os.environ, args.project_root)
            try:
                persist_record(record, store)
            except OSError as exc:
                print(f"failed to persist run-record to {store}: {exc}",
                      file=sys.stderr)
                return 1
        return 0

    if args.cmd == "find":
        # #392: WF14 batch mode's per-issue record lookup. Last matching
        # record wins (a re-run's newer record supersedes); a miss is rc 1 —
        # loud, so the caller renders its per-issue degraded section instead
        # of silently skipping. Malformed/invalid lines are excluded by
        # load_store's fail-closed reader.
        store = resolve_store_path(args.store, os.environ, args.project_root)
        try:
            records, _excluded = load_store(store)
        except WorkSummaryError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        match = None
        for rec in records:
            issue = rec.get("issue")
            if isinstance(issue, dict) and issue.get("number") == args.issue:
                match = rec
        if match is None:
            print(f"find: no record for issue #{args.issue} in {store}",
                  file=sys.stderr)
            return 1
        print(json.dumps(match, separators=(",", ":")))
        return 0

    if args.cmd == "aggregate":
        if args.since is not None and not _looks_iso(args.since):
            print(f"--since must be an ISO-8601 date (YYYY-MM-DD); got: {args.since}",
                  file=sys.stderr)
            return 2
        # Resolve the store spec list (#115): --workspace pools active projects;
        # --store is repeatable; else fall back to the env store. A SINGLE explicitly
        # named store keeps single-store parity (missing -> exit 2); a fleet of >1
        # stores (or --workspace) tolerates a missing store with a visible warning.
        if args.workspace and args.store:
            print("use --workspace OR --store, not both", file=sys.stderr)
            return 2
        ws_skipped = []
        if args.workspace:
            try:
                store_specs, ws_skipped = stores_from_workspace(args.workspace)
            except WorkSummaryError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            if not store_specs and not ws_skipped:
                print(f"no active projects with stores in {args.workspace}",
                      file=sys.stderr)
                return 2
            tolerate = True
        elif args.store:
            store_specs = [(s, s) for s in args.store]
            tolerate = len(store_specs) > 1
        else:
            env_store = os.environ.get(STORE_ENV)
            if not env_store:
                print(f"aggregate requires --store, --workspace, or ${STORE_ENV}",
                      file=sys.stderr)
                return 2
            store_specs = [(env_store, env_store)]
            tolerate = False
        try:
            records, excluded, missing = load_stores(store_specs, tolerate_missing=tolerate)
        except WorkSummaryError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        # #115 (review F1): active projects that couldn't resolve to a store path are
        # surfaced alongside unreadable stores — a dropped active project never hides.
        missing = ws_skipped + missing
        records = filter_since(records, args.since)
        agg = (aggregate_grouped(records, args.group_by) if args.group_by
               else aggregate_records(records))
        if args.json:
            print(json.dumps(
                {"group_by": args.group_by, "since": args.since,
                 "excluded": excluded, "excluded_count": len(excluded),
                 "missing_stores": missing, "missing_store_count": len(missing),
                 "aggregate": agg}, separators=(",", ":")))
        else:
            print(render_aggregate_markdown(
                agg, group_by=args.group_by, excluded=excluded, since=args.since))
            if missing:
                print(f"\n> {len(missing)} store(s) skipped (unreadable): "
                      f"{', '.join(m.split(' (')[0] for m in missing)}")
        # AC8: corrupt/excluded lines are ALSO surfaced on stderr with a count,
        # so a fail-closed exclusion is never invisible.
        if excluded:
            print(f"{len(excluded)} line(s) excluded as corrupt/invalid:",
                  file=sys.stderr)
            for e in excluded:
                print(f"  - {e}", file=sys.stderr)
        # #115: a store skipped entirely (fleet fail-closed) is surfaced too.
        if missing:
            print(f"{len(missing)} store(s) skipped (unreadable):", file=sys.stderr)
            for m in missing:
                print(f"  - {m}", file=sys.stderr)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
