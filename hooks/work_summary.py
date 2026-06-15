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

    return 2


if __name__ == "__main__":
    sys.exit(main())
