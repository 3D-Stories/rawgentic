#!/usr/bin/env python3
"""WF2 tiered code review utilities for rawgentic implement-feature skill (#73).

Provides testable helpers for:
- Env-var loading with frozen Final constants + clamping
- Parsing markdown task plans with format contract (fail-closed on missing riskLevel)
- Risk-ratio calibration (8 criteria, N<3 edge, >=80% decompose)
- Mid-flight task promotion heuristics
- Loop-back budget persistence per source
- Review log append + coverage assertions
- Deferrals tracking + mechanical resolution gates
- Retroactive scan of prior commits for matching trigger reasons

Used by skills/implement-feature/SKILL.md via `python3 -c` invocations.
"""
import os
import re
import sys
from dataclasses import dataclass
from typing import Final, Literal


class PlanFormatError(ValueError):
    """Raised when the plan markdown does not conform to the WF2 contract."""


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    risk_level: Literal["high", "standard"]
    reason: str | None  # parenthesized reason for high-risk; None for standard


# --- Env-var loading with clamping and freeze-at-import ---

_WARN_DEFAULT = 30
_HALT_DEFAULT = 50
_WARN_MIN, _WARN_MAX = 5, 95
_HALT_MIN, _HALT_MAX = 10, 95
_CONFIDENCE_DEFAULT = 0.80


def _coerce_int_env(name: str, default: int) -> int:
    """Parse an env var as int. Non-int / empty / malformed -> default.

    Strict acceptance: only optional leading '-' followed by ASCII digits.
    Floats (e.g. '30.5'), unicode digits, shell injection attempts, and
    English words all fall back to default. A warning is logged to stderr.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    stripped = raw.strip()
    # Strict: optional minus + ASCII digits only
    if stripped.startswith("-"):
        body = stripped[1:]
        sign = -1
    else:
        body = stripped
        sign = 1
    if not body or not body.isascii() or not body.isdigit():
        print(
            f"plan_lib: env {name}={raw!r} rejected (not an integer); using default {default}",
            file=sys.stderr,
        )
        return default
    return sign * int(body)


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _load_warn_halt() -> tuple[int, int]:
    warn = _coerce_int_env("WF2_HIGH_RISK_RATIO_WARN_PCT", _WARN_DEFAULT)
    halt = _coerce_int_env("WF2_HIGH_RISK_RATIO_HALT_PCT", _HALT_DEFAULT)
    warn = _clamp(warn, _WARN_MIN, _WARN_MAX)
    halt = _clamp(halt, _HALT_MIN, _HALT_MAX)
    # Enforce halt >= warn + 10
    if halt < warn + 10:
        halt = _clamp(warn + 10, _HALT_MIN, _HALT_MAX)
    return warn, halt


_warn, _halt = _load_warn_halt()
WF2_HIGH_RISK_RATIO_WARN_PCT: Final[int] = _warn
WF2_HIGH_RISK_RATIO_HALT_PCT: Final[int] = _halt
PER_TASK_REVIEW_CONFIDENCE_THRESHOLD: Final[float] = _CONFIDENCE_DEFAULT
PER_TASK_REVIEW_AGENT_COUNT: Final[int] = 2


# --- parse_tasks: plan markdown -> [Task] ---

_TASK_HEADER_RE = re.compile(r"^###\s+Task\s+([0-9.]+)\s*:\s*(.+?)\s*$")
_RISKLEVEL_RE = re.compile(
    r"^\s*[-*]\s*riskLevel\s*:\s*(high|standard)(?:\s*\(([^)]+)\))?\s*$",
    re.IGNORECASE,
)
_ANY_HEADING_RE = re.compile(r"^#{1,6}\s+")


def parse_tasks(plan_markdown: str) -> list[Task]:
    """Extract Task objects from a WF2 plan markdown.

    Contract:
    - Each task starts with `### Task <id>: <title>` heading.
    - Each task MUST include a `- riskLevel: high|standard` line within its body
      (before the next ### heading); high-risk tasks may include a parenthesized
      reason: `- riskLevel: high (security surface)`.
    - Tasks without a riskLevel line raise PlanFormatError (fail-closed).
    - Non-task `###` headings (anything not starting with `Task `) are ignored.
    """
    lines = plan_markdown.splitlines()
    tasks: list[Task] = []
    i = 0
    while i < len(lines):
        m = _TASK_HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        task_id, title = m.group(1), m.group(2)
        # Scan body until next ### heading or EOF
        risk_level = None
        reason = None
        body_start = i + 1
        j = body_start
        while j < len(lines):
            if _ANY_HEADING_RE.match(lines[j]) and lines[j].startswith("### "):
                break
            mm = _RISKLEVEL_RE.match(lines[j])
            if mm:
                risk_level = mm.group(1).lower()
                reason = mm.group(2).strip() if mm.group(2) else None
            j += 1
        if risk_level is None:
            raise PlanFormatError(
                f"Task {task_id} ({title!r}) is missing required `riskLevel` line"
            )
        tasks.append(Task(id=task_id, title=title, risk_level=risk_level, reason=reason))
        i = j
    return tasks


# --- Risk-ratio calibration ---

# The 8 risk criteria. Tag a task `riskLevel: high (<criterion>)` when any of:
#   1. Security surface — auth, secrets, sanitization, input validation, crypto,
#      access control
#   2. Module boundary — introduces/changes a service or module API that other
#      code will import
#   3. Non-trivial error/exception flow — state machines, retry, fallback
#      branches, discriminated outcomes
#   4. Infra/persistence — infrastructure, deployment, migrations, schema
#   5. Security middleware — rate limiting, circuit breakers, request validation
#   6. Deserialization of external data — JSON/YAML/TOML/binary formats from
#      untrusted sources
#   7. Subprocess construction — shells out to external commands with dynamic
#      args
#   8. Regex on untrusted input — ReDoS risk, lookahead in user-controlled input
RISK_CRITERIA: Final[tuple[str, ...]] = (
    "security surface",
    "module boundary",
    "non-trivial error flow",
    "infra/persistence",
    "security middleware",
    "deserialization of external data",
    "subprocess construction",
    "regex on untrusted input",
)

# Minimum task count below which calibration is meaningless.
_RATIO_SKIP_MIN = 3
# Threshold above which a 0% high-risk classification is implausible.
_IMPLAUSIBLE_ZERO_MIN = 5
# Threshold above which the plan should be decomposed rather than just halted.
_DECOMPOSE_PCT = 80


def compute_risk_ratio(tasks: list[Task]) -> tuple[float, int, int]:
    """Return (ratio, high_count, total_count). 0/0 returns (0.0, 0, 0)."""
    total = len(tasks)
    high = sum(1 for t in tasks if t.risk_level == "high")
    ratio = (high / total) if total > 0 else 0.0
    return ratio, high, total


def check_ratio_band(ratio: float, n_tasks: int) -> Literal[
    "skip", "implausible_zero", "pass", "warn", "halt", "decompose"
]:
    """Classify the high-risk ratio band.

    - skip: N<3 (calibration is meaningless on tiny plans)
    - implausible_zero: ratio == 0 AND N>=5 (zero high-risk on a meaningful
      plan is implausible — emit info note, not halt)
    - pass: ratio <= WARN_PCT/100
    - warn: WARN_PCT/100 < ratio <= HALT_PCT/100
    - halt: HALT_PCT/100 < ratio < DECOMPOSE/100
    - decompose: ratio >= DECOMPOSE/100 (plan likely needs subdivision)
    """
    if n_tasks < _RATIO_SKIP_MIN:
        return "skip"
    pct = ratio * 100
    if ratio == 0.0 and n_tasks >= _IMPLAUSIBLE_ZERO_MIN:
        return "implausible_zero"
    if pct >= _DECOMPOSE_PCT:
        return "decompose"
    if pct > WF2_HIGH_RISK_RATIO_HALT_PCT:
        return "halt"
    if pct > WF2_HIGH_RISK_RATIO_WARN_PCT:
        return "warn"
    return "pass"


# --- High-risk path allowlist (case-insensitive regex patterns) ---

# Defaults. Users can extend (not replace) via .rawgentic.json `highRiskPaths: []`.
DEFAULT_HIGH_RISK_PATH_PATTERNS: Final[tuple[str, ...]] = (
    r"auth",
    r"secret",
    r"\.env",
    r"migration",
    r"crypto",
    r"jwt",
    r"session",
    r"oauth",
    r"csrf",
    r"token",
    r"credential",
    r"passport",
    r"middleware",
    r"lib/server/auth",
    r"security-",
    r"hooks/security",
)

# Compile once. Case-insensitive substring match anywhere in the path.
_HIGH_RISK_PATH_RE = re.compile(
    "(" + "|".join(DEFAULT_HIGH_RISK_PATH_PATTERNS) + ")",
    re.IGNORECASE,
)

# LOC threshold above which a task is considered large enough to merit promotion.
_LOC_PROMOTE_THRESHOLD = 200


def _path_matches_high_risk(path: str, extra_patterns: tuple[str, ...] = ()) -> str | None:
    """Return the first matching pattern (or None). Case-insensitive."""
    m = _HIGH_RISK_PATH_RE.search(path)
    if m:
        return m.group(0)
    for pat in extra_patterns:
        if re.search(pat, path, re.IGNORECASE):
            return pat
    return None


def should_promote(
    task_id: str,
    file_paths: list[str],
    loc_delta: int,
    extra_high_risk_patterns: tuple[str, ...] = (),
) -> tuple[bool, str | None]:
    """Mechanical heuristic for mid-flight task promotion (standard -> high).

    Triggers (any of):
    - Any file path matches the high-risk allowlist regex
    - loc_delta >= _LOC_PROMOTE_THRESHOLD (200)

    Returns (promote_flag, reason_string). reason is None when no promotion.
    """
    for fp in file_paths:
        match = _path_matches_high_risk(fp, extra_high_risk_patterns)
        if match is not None:
            return True, f"path matches security-relevant pattern {match!r} ({fp})"
    if loc_delta >= _LOC_PROMOTE_THRESHOLD:
        return True, f"LOC delta {loc_delta} >= threshold {_LOC_PROMOTE_THRESHOLD} (size suggests non-trivial surface)"
    return False, None


def format_promotion_note(task_id: str, criterion: str, rationale: str) -> str:
    """Format a session-notes line documenting a mid-flight promotion."""
    return (
        f"### WF2 Step 8 — Promoted {task_id}: standard -> high "
        f"(criterion: {criterion}; rationale: {rationale})"
    )


# --- review log (jsonl) ---

import json as _json  # noqa: E402  (intentional; kept distinct from top-level imports)
from datetime import datetime, timezone  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_review_log(log_path: str, entry: dict) -> None:
    """Append a JSON entry to the review log (one entry per line).

    Auto-adds `ts` (ISO 8601 UTC) if not present.
    """
    enriched = dict(entry)
    enriched.setdefault("ts", _now_iso())
    line = _json.dumps(enriched, separators=(",", ":")) + "\n"
    # Append-only; create if missing
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)


def _read_review_log(log_path: str) -> list[dict]:
    if not os.path.exists(log_path):
        return []
    out = []
    with open(log_path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(_json.loads(raw))
            except _json.JSONDecodeError:
                continue
    return out


def assert_review_coverage(
    log_path: str,
    plan_tasks: list[Task],
    task_to_sha: dict[str, str | None],
) -> tuple[bool, list[str]]:
    """Verify every high-risk task has a matching applied review log entry.

    Returns (ok, missing) where `missing` is a list of human-readable
    descriptions of the gaps. REVIEW_DISPATCH_FAILED entries do NOT count
    as coverage.
    """
    entries = _read_review_log(log_path)
    applied_shas = {
        e.get("sha")
        for e in entries
        if e.get("verdict") in ("applied", "deferred")
    }
    missing = []
    for task in plan_tasks:
        if task.risk_level != "high":
            continue
        sha = task_to_sha.get(task.id)
        if sha is None:
            missing.append(f"task {task.id} ({task.title!r}) has no recorded commit SHA")
            continue
        if sha not in applied_shas:
            missing.append(
                f"task {task.id} (sha {sha}) has no applied/deferred review log entry"
            )
    return (len(missing) == 0, missing)


def get_deferred_findings(deferrals_path: str) -> list[dict]:
    """Read the deferrals file. Missing file returns []."""
    if not os.path.exists(deferrals_path):
        return []
    with open(deferrals_path, "r", encoding="utf-8") as f:
        data = _json.load(f)
    return data if isinstance(data, list) else []


_HIGH_SEVERITIES = ("High", "Critical")


def _deferral_is_resolved(deferral: dict) -> bool:
    """A deferred-High|Critical is resolved if any of:
    - status == 'applied'
    - has independent concurrence (from a different reviewer slot than originator)
    - defer_count >= 2 AND user_ack is True
    """
    if deferral.get("status") == "applied":
        return True
    originator = deferral.get("originator_reviewer_slot")
    concurrences = deferral.get("concurrences") or []
    independent = [c for c in concurrences if c != originator]
    if independent:
        # Independent concurrence: resolved IF defer_count < 2 OR user_ack True.
        # Without user_ack on chains of 2+, we still demand explicit ack.
        if deferral.get("defer_count", 1) >= 2 and not deferral.get("user_ack", False):
            return False
        return True
    if deferral.get("defer_count", 1) >= 2 and deferral.get("user_ack", False):
        return True
    return False


def assert_no_unresolved_high_deferrals(deferrals_path: str) -> tuple[bool, list[dict]]:
    """Return (ok, unresolved) over Critical/High deferrals.

    Used by Step 11 exit check.
    """
    deferrals = get_deferred_findings(deferrals_path)
    unresolved = [
        d for d in deferrals
        if d.get("severity") in _HIGH_SEVERITIES and not _deferral_is_resolved(d)
    ]
    return (len(unresolved) == 0, unresolved)


# --- loop-back budget persistence ---

_LOOPBACK_SOURCES: Final[tuple[str, ...]] = ("design", "tdd", "review", "review_design")
_LOOPBACK_SOURCE_MAX = {
    "design": 2,
    "tdd": 1,
    "review": 1,
    "review_design": 1,
}
GLOBAL_LOOPBACK_BUDGET: Final[int] = 3


def _read_loopback_state(path: str) -> dict:
    if not os.path.exists(path):
        return {src: 0 for src in _LOOPBACK_SOURCES} | {"total": 0}
    with open(path, "r", encoding="utf-8") as f:
        state = _json.load(f)
    # Backfill missing keys
    for src in _LOOPBACK_SOURCES:
        state.setdefault(src, 0)
    state.setdefault("total", sum(state.get(s, 0) for s in _LOOPBACK_SOURCES))
    return state


def _write_loopback_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(state, f, separators=(",", ":"), sort_keys=True)


def _git_run(repo: str, args: list[str]) -> str:
    """Run a git command in `repo` and return stdout. Raises on non-zero exit."""
    import subprocess
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _parse_numstat(numstat_output: str) -> tuple[list[str], int]:
    """Parse `git show --numstat --format=` output.

    Format per file: `<additions>\\t<deletions>\\t<path>` (tab-separated).
    Binary files use `-\\t-\\t<path>` (count as 0 LOC).

    Returns (file_paths, total_loc_delta).
    """
    paths: list[str] = []
    total = 0
    for raw_line in numstat_output.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_s, del_s, path = parts[0], parts[1], "\t".join(parts[2:])
        try:
            additions = int(add_s) if add_s != "-" else 0
            deletions = int(del_s) if del_s != "-" else 0
        except ValueError:
            continue
        paths.append(path)
        total += additions + deletions
    return paths, total


def scan_prior_commits_for_trigger(
    repo: str,
    since_sha: str | None = None,
    exclude_sha: str | None = None,
    extra_high_risk_patterns: tuple[str, ...] = (),
) -> list[str]:
    """Scan prior commits in the current branch for any matching the
    high-risk path allowlist or LOC threshold.

    Returns a list of commit SHAs that WOULD have triggered should_promote().
    Used by retroactive scan after a mid-flight promotion fires.

    - since_sha: if provided, only scan commits AFTER this SHA (exclusive)
    - exclude_sha: if provided, exclude this SHA from results (typically
      the commit that triggered the current promotion)
    """
    # Build the rev range
    if since_sha:
        range_arg = f"{since_sha}..HEAD"
    else:
        range_arg = "HEAD"
    log_out = _git_run(repo, ["rev-list", range_arg])
    shas = [s.strip() for s in log_out.splitlines() if s.strip()]
    flagged: list[str] = []
    for sha in shas:
        if exclude_sha and sha == exclude_sha:
            continue
        try:
            stat = _git_run(repo, ["show", "--numstat", "--format=", sha])
        except Exception:
            continue
        paths, loc_delta = _parse_numstat(stat)
        promote, _ = should_promote(sha, paths, loc_delta, extra_high_risk_patterns)
        if promote:
            flagged.append(sha)
    return flagged


def consume_loopback(path: str, source: str) -> tuple[bool, dict]:
    """Attempt to consume one loop-back from the named source.

    Returns (ok, state). Fails if:
    - Source exceeds its per-source cap
    - Global total would exceed GLOBAL_LOOPBACK_BUDGET

    Raises ValueError on unknown source.
    """
    if source not in _LOOPBACK_SOURCES:
        raise ValueError(f"unknown loopback source: {source!r}")
    state = _read_loopback_state(path)
    if state[source] >= _LOOPBACK_SOURCE_MAX[source]:
        return False, state
    if state["total"] >= GLOBAL_LOOPBACK_BUDGET:
        return False, state
    state[source] += 1
    state["total"] = sum(state[s] for s in _LOOPBACK_SOURCES)
    _write_loopback_state(path, state)
    return True, state
