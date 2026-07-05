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
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

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
# `set`/`skipped` are recorded by the orchestrator; `fired` is MANUAL-ONLY (see
# the goal_guard validation block below) — no code path detects it automatically.
GOAL_GUARD_VALUES = {"set", "skipped", "fired"}
# `usage.capture_status` (#189) — how the token/cost numbers were obtained.
# `captured` = live-parsed from the session log (REQUIRES real non-null tokens
# summing > 0 — the schema-level backstop against the #155 null-forever state);
# `unrecoverable` = a historical row with no session-id correlator; `unavailable`
# = capture was attempted for this run but failed (file missing / no usage).
CAPTURE_STATUS_VALUES = {"captured", "unrecoverable", "unavailable"}

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


# --- validate_record (pure) ------------------------------------------------

def validate_record(record) -> list:
    """Return a list of human-readable validation errors ([] == valid).

    Hand-rolled (matching capabilities_lib's style) so the hook carries no
    runtime jsonschema dependency. Enforces required fields, types (rejecting
    bool-as-int), enum membership, and cross-field integrity (resolved<=findings,
    passing<=total, used<=budget, non-negative counts) — the integrity the
    downstream Tier-2 aggregation will rely on."""
    if not isinstance(record, dict):
        return ["record must be a JSON object"]

    errs = []
    for key in REQUIRED_TOP:
        if key not in record:
            errs.append(f"missing required field: {key}")

    for key in ("workflow", "workflow_version"):
        if key in record and not (_is_str(record[key]) and record[key].strip()):
            errs.append(f"{key} must be a non-empty string")

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


def _render_usage_line(usage: dict) -> str:
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

    return line


# --- render_summary (pure, best-effort) ------------------------------------

def render_summary(record) -> str:
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
        lines.append(_render_usage_line(usage))
    deferred = record.get("verification_deferred")
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
    return {"n": n, "gates": gates, "loop_backs": loop_backs,
            "outcomes": outcomes, "effort": effort}


_GROUP_KEYS = {
    "workflow": lambda r: r.get("workflow"),
    "version": lambda r: r.get("workflow_version"),
    "type": lambda r: _as_dict(r.get("issue")).get("type"),
    "complexity": lambda r: _as_dict(r.get("issue")).get("complexity"),
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

    pa = sub.add_parser(
        "aggregate",
        help="roll a JSONL run-record store up into aggregate Tier-2 metrics")
    pa.add_argument("--store", default=None,
                    help=f"run-record store path (falls back to ${STORE_ENV})")
    pa.add_argument("--json", action="store_true",
                    help="emit the metric object as JSON instead of Markdown")
    pa.add_argument("--group-by", choices=sorted(_GROUP_KEYS), default=None,
                    help="partition every metric by this dimension "
                         "(version is the cross-skill A/B slice)")
    pa.add_argument("--since", default=None,
                    help="only records whose generated_at is on/after this ISO date")
    args = parser.parse_args(argv)

    if args.cmd == "summarize":
        try:
            raw = load_record_file(args.record_file)
        except WorkSummaryError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        errors = validate_record(raw)
        if errors:
            # Best-effort render so the user keeps Step 16 output, but never
            # persist an invalid record. Exit 1 so the skill surfaces the gap.
            print(json.dumps(raw, separators=(",", ":")) if args.json
                  else render_summary(raw))
            print("run-record validation failed (NOT persisted):", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1

        record = normalize_record(raw, now=_now())
        print(json.dumps(record, separators=(",", ":")) if args.json
              else render_summary(record))
        if not args.no_persist:
            store = resolve_store_path(args.store, os.environ, args.project_root)
            try:
                persist_record(record, store)
            except OSError as exc:
                print(f"failed to persist run-record to {store}: {exc}",
                      file=sys.stderr)
                return 1
        return 0

    if args.cmd == "aggregate":
        store = args.store or os.environ.get(STORE_ENV)
        if not store:
            print(f"aggregate requires --store or ${STORE_ENV}", file=sys.stderr)
            return 2
        if args.since is not None and not _looks_iso(args.since):
            print(f"--since must be an ISO-8601 date (YYYY-MM-DD); got: {args.since}",
                  file=sys.stderr)
            return 2
        try:
            records, excluded = load_store(store)
        except WorkSummaryError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        records = filter_since(records, args.since)
        agg = (aggregate_grouped(records, args.group_by) if args.group_by
               else aggregate_records(records))
        if args.json:
            print(json.dumps(
                {"group_by": args.group_by, "since": args.since,
                 "excluded": excluded, "excluded_count": len(excluded),
                 "aggregate": agg}, separators=(",", ":")))
        else:
            print(render_aggregate_markdown(
                agg, group_by=args.group_by, excluded=excluded, since=args.since))
        # AC8: corrupt/excluded lines are ALSO surfaced on stderr with a count,
        # so a fail-closed exclusion is never invisible.
        if excluded:
            print(f"{len(excluded)} line(s) excluded as corrupt/invalid:",
                  file=sys.stderr)
            for e in excluded:
                print(f"  - {e}", file=sys.stderr)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
