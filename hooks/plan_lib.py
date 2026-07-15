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
import fcntl
import hashlib
import json as _json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, Literal

from atomic_write_lib import atomic_write_text


class PlanFormatError(ValueError):
    """Raised when the plan markdown does not conform to the WF2 contract."""


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    risk_level: Literal["high", "standard"]
    reason: str | None  # parenthesized reason for high-risk; None for standard
    # Optional, purely additive (PR 3a / optimization C). A task may declare a
    # `parallel_group` and the `files` it touches so validate_parallel_groups can
    # prove same-group tasks are file-disjoint. Absent -> not parallel-eligible.
    # tuple (not list) to stay hashable under frozen=True.
    parallel_group: str | None = None
    files: tuple[str, ...] = ()
    # Optional (#138). Set to the parenthesized reason when a task declares
    # `- verification: deferred-to-target (<reason>)` — the dev env cannot
    # exercise this artifact, so its remaining behavior must be checked on the
    # target. None = not deferred. Additive; absence never changes any contract.
    deferral_reason: str | None = None


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

# #224: Step-2 path-cost estimate. Step 11 dispatches 3 review agents on the
# full spine and ≥1 in the small-standard lane — the axis is LANE, not
# complexity (steps.md §11; drift-guarded in tests/test_wf2_clarity.py).
STEP11_REVIEW_AGENT_COUNT_FULL: Final[int] = 3
STEP11_REVIEW_AGENT_COUNT_LANE: Final[int] = 1
WF2_EST_MINUTES_PER_AGENT: Final[int] = _clamp(
    _coerce_int_env("WF2_EST_MINUTES_PER_AGENT", 5), 1, 60)


def estimate_agents(high_risk_tasks: int, *, lane: bool,
                    adversarial: bool = False, peer_consult: bool = False,
                    diff_review: bool = False) -> dict:
    """Estimate dispatched agents + rough wall minutes for a WF2 path (#224).

    agents (work count): Step-4 self-review 1 (counted whether inline or
    dispatched) + PER_TASK_REVIEW_AGENT_COUNT × high_risk_tasks (Step 8a)
    + Step 11 (lane-keyed: 3 full / 1 lane) + opt-ins ×1 each. adversarial
    and peer_consult are forced off when lane=True (the lane drops all
    design-stage cross-model ceremony); diff_review counts on both paths.

    minutes (wall model): parallel-stage model, NOT a serial sum — stages =
    1 (Step-4; adversarial/peer run concurrent within it) + high_risk_tasks
    (Step-8a reviews serialize across tasks, parallel within) + 1 (Step-11;
    its agents + diff review run parallel); minutes = stages ×
    WF2_EST_MINUTES_PER_AGENT. The two paths' minutes therefore always
    match under this wall model — the lane saves agent-cost, not wall time.

    An ESTIMATE, not a contract: Step-2 analysis fan-out, loop-backs, and
    whole-issue delegation are not modeled.
    """
    if high_risk_tasks < 0:
        raise ValueError("high_risk_tasks must be >= 0")
    if lane:
        adversarial = peer_consult = False
    agents = (1 + PER_TASK_REVIEW_AGENT_COUNT * high_risk_tasks
              + (STEP11_REVIEW_AGENT_COUNT_LANE if lane
                 else STEP11_REVIEW_AGENT_COUNT_FULL)
              + int(adversarial) + int(peer_consult) + int(diff_review))
    stages = 1 + high_risk_tasks + 1
    return {"agents": agents, "minutes": stages * WF2_EST_MINUTES_PER_AGENT}

# Severity-banded confidence thresholds for filtering reviewer findings (Step 8a
# per-task review AND Step 11 pre-PR review). Critical/High get a lower bar
# because hiding them is more dangerous than flagging a false-positive. This dict
# is the SINGLE source of truth: SKILL.md's <constants> block mirrors it and a
# drift-guard test (tests/hooks/test_plan_lib_deferral_writer.py) asserts the two
# stay equal, so the "source of truth is hooks/plan_lib.py" claim cannot rot.
SEVERITY_BANDED_CONFIDENCE: Final[dict[str, float]] = {
    "Critical": 0.50,
    "High": 0.65,
    "Medium": 0.80,
    "Low": 0.90,
}


# --- parse_tasks: plan markdown -> [Task] ---

_TASK_HEADER_RE = re.compile(r"^###\s+Task\s+([0-9.]+)\s*:\s*(.+?)\s*$")
_RISKLEVEL_RE = re.compile(
    r"^\s*[-*]\s*riskLevel\s*:\s*(high|standard)(?:\s*\(([^)]+)\))?\s*$",
    re.IGNORECASE,
)
# Optional parallel-execution annotations (PR 3a). Both purely additive — their
# absence never changes the riskLevel fail-closed contract or the pre-P15 migration.
_PARALLEL_GROUP_RE = re.compile(r"^\s*[-*]\s*parallel_group\s*:\s*(\S+)\s*$", re.IGNORECASE)
_FILES_RE = re.compile(r"^\s*[-*]\s*files\s*:\s*(.+?)\s*$", re.IGNORECASE)
_ANY_HEADING_RE = re.compile(r"^#{1,6}\s+")
# Deferred-to-target verification (#138). Any `- verification: <value>` line;
# only a value beginning with the `deferred-to-target` keyword is our concern
# (free-text values are ordinary Implement-Verify commands and stay ignored).
# A deferral MUST carry a non-empty parenthesized reason, else it is malformed.
_VERIFICATION_RE = re.compile(r"^\s*[-*]\s*verification\s*:\s*(.+?)\s*$", re.IGNORECASE)
_DEFERRAL_KEYWORD_RE = re.compile(r"^deferred-to-target\b", re.IGNORECASE)
# Greedy `.*` (not `[^)]*`) so a reason containing inner parens — e.g.
# `deferred-to-target (needs #[cfg(target_os="windows")] build)` — is captured to
# the LAST `)`, not truncated at the first (#231 AC1). Empty `()` still yields ""
# which the caller rejects as malformed.
_DEFERRAL_VALUE_RE = re.compile(r"^deferred-to-target\s*\((.*)\)\s*$", re.IGNORECASE)


def _split_files(raw: str) -> tuple[str, ...]:
    """Split a `- files:` line value on commas/whitespace into a tuple."""
    parts = [p.strip() for p in re.split(r"[,\s]+", raw) if p.strip()]
    return tuple(parts)


def parse_tasks(plan_markdown: str) -> list[Task]:
    """Extract Task objects from a WF2 plan markdown.

    Contract:
    - Each task starts with `### Task <id>: <title>` heading.
    - Each task MUST include a `- riskLevel: high|standard` line within its body
      (before the next ### heading); high-risk tasks may include a parenthesized
      reason: `- riskLevel: high (security surface)`.
    - Tasks without a riskLevel line raise PlanFormatError (fail-closed).
    - Non-task `###` headings (anything not starting with `Task `) are ignored.

    Backward compatibility: if NO task in the plan has a riskLevel line, the
    plan is treated as pre-P15 (created before this feature shipped) and every
    task is defaulted to `riskLevel: standard` with a stderr warning. Partial
    annotation (some tasks tagged, some not) still fail-closes — that's a real
    bug, not a migration.
    """
    lines = plan_markdown.splitlines()
    raw_tasks: list[
        tuple[str, str, str | None, str | None, str | None, tuple[str, ...], str | None]
    ] = []
    i = 0
    while i < len(lines):
        m = _TASK_HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        task_id, title = m.group(1), m.group(2)
        # Scan body until next ### heading or EOF
        risk_level: str | None = None
        reason: str | None = None
        parallel_group: str | None = None
        files: tuple[str, ...] = ()
        deferral_reason: str | None = None
        body_start = i + 1
        j = body_start
        while j < len(lines):
            if _ANY_HEADING_RE.match(lines[j]) and lines[j].startswith("### "):
                break
            mm = _RISKLEVEL_RE.match(lines[j])
            if mm:
                risk_level = mm.group(1).lower()
                reason = mm.group(2).strip() if mm.group(2) else None
            pg = _PARALLEL_GROUP_RE.match(lines[j])
            if pg:
                parallel_group = pg.group(1)
            fm = _FILES_RE.match(lines[j])
            if fm:
                files = _split_files(fm.group(1))
            vm = _VERIFICATION_RE.match(lines[j])
            if vm and _DEFERRAL_KEYWORD_RE.match(vm.group(1).strip()):
                # A deferral is declared — the parenthesized reason is mandatory.
                dv = _DEFERRAL_VALUE_RE.match(vm.group(1).strip())
                reason_text = dv.group(1).strip() if dv else ""
                if not reason_text:
                    raise PlanFormatError(
                        f"Task {task_id} ({title!r}) declares "
                        f"`verification: deferred-to-target` without a (reason); "
                        f"the reason is required so the deferred surface is legible"
                    )
                deferral_reason = reason_text
            j += 1
        raw_tasks.append(
            (task_id, title, risk_level, reason, parallel_group, files, deferral_reason)
        )
        i = j

    tagged_count = sum(1 for t in raw_tasks if t[2] is not None)
    if raw_tasks and tagged_count == 0:
        # Pre-P15 plan: default every task to standard and warn once.
        print(
            "plan_lib: pre-P15 plan detected (no tasks have riskLevel). "
            "Defaulting all tasks to riskLevel: standard. "
            "Re-run /rawgentic:setup or update the plan to opt into P15 tiered review.",
            file=sys.stderr,
        )
        return [
            Task(id=tid, title=t, risk_level="standard", reason=None,
                 parallel_group=pg, files=fs, deferral_reason=dr)
            for tid, t, _, _, pg, fs, dr in raw_tasks
        ]

    out: list[Task] = []
    for tid, t, rl, r, pg, fs, dr in raw_tasks:
        if rl is None:
            raise PlanFormatError(
                f"Task {tid} ({t!r}) is missing required `riskLevel` line"
            )
        out.append(Task(id=tid, title=t, risk_level=rl, reason=r,
                        parallel_group=pg, files=fs, deferral_reason=dr))
    return out


def deferred_tasks(tasks: list[Task]) -> list[Task]:
    """Tasks whose verification is deferred to the target (#138) — i.e. those
    with a `deferral_reason`. Used by Step 9 (list + local proxy), Step 12 (PR
    section), and the completion gate."""
    return [t for t in tasks if t.deferral_reason is not None]


def assert_deferrals_recorded(
    plan_deferred: list[Task],
    recorded_entries,
) -> tuple[bool, list[str]]:
    """Verify every plan-deferred task is recorded exactly once in the run-record.

    The completion-gate mechanism for "unrecorded deferral = failure" (#138):
    compares `deferred_tasks(plan)` against the run-record's `verification_deferred`
    list. Returns (ok, errors). Fails on a plan-deferred task missing from the
    record, a duplicate task_id in the record, or a foreign task_id (recorded but
    not planned-deferred). Fail-closed: a non-list record → error.
    """
    errors: list[str] = []
    if not isinstance(recorded_entries, list):
        return (False, ["verification_deferred must be a list"])
    recorded_ids: list[str] = []
    for idx, e in enumerate(recorded_entries):
        if not isinstance(e, dict) or not _is_nonempty_str(e.get("task_id")):
            errors.append(f"verification_deferred[{idx}] has no string task_id")
            continue
        # Every recorded entry must carry the full evidence set, else a bare
        # {task_id} would satisfy the gate without the required local proxy /
        # target check (the anti-abuse invariant would fail open).
        for f in ("reason", "local_proxy", "target_check"):
            if not _is_nonempty_str(e.get(f)):
                errors.append(
                    f"verification_deferred[{idx}] (task {e['task_id']}) is missing "
                    f"non-empty {f}")
        recorded_ids.append(e["task_id"])
    dupes = sorted({i for i in recorded_ids if recorded_ids.count(i) > 1})
    if dupes:
        errors.append(f"verification_deferred has duplicate task_id(s) {dupes}")
    planned_ids = {t.id for t in plan_deferred}
    recorded_set = set(recorded_ids)
    for tid in sorted(planned_ids - recorded_set):
        errors.append(f"deferred task {tid} is not recorded in verification_deferred")
    for tid in sorted(recorded_set - planned_ids):
        errors.append(f"verification_deferred names task {tid} that the plan did not defer")
    return (len(errors) == 0, errors)


_DEFERRED_PR_HEADING = "## Deferred verification"


def assert_pr_body_has_deferred_section(
    pr_body: str,
    plan_deferred: list[Task],
) -> tuple[bool, list[str]]:
    """When the plan has deferred tasks, the PR body MUST carry the canonical
    `## Deferred verification` section (#138). Returns (ok, errors). No deferrals
    → always ok (the section is omitted-when-empty by design)."""
    if not plan_deferred:
        return (True, [])
    body = pr_body if isinstance(pr_body, str) else ""
    if _DEFERRED_PR_HEADING not in body:
        return (False, [
            f"plan has {len(plan_deferred)} deferred task(s) but the PR body is "
            f"missing the '{_DEFERRED_PR_HEADING}' section"
        ])
    return (True, [])


def _is_nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


# --- Platform / external-dependency feasibility gate (#226) ---
# A design doc / RCA MUST declare `platform_apis:` — either `none`, or one block per
# material platform/framework/external API not already proven in-repo the same way.
# The declaration being ABSENT is itself a Step-4 blocker: an omitted note is the
# silent-gap class #226 exists to kill, so it must fail closed, not pass by default.

# `docs` is deliberately NOT an accepted kind (#226 review): docs prove an API
# *exists*, not that THIS project's config *permits* it — accepting `verified via
# docs` for a permission/capability-gated API is the exact silent gap #226 targets
# (the motivating Tauri case: docs say setSize exists; the capability file denies it).
# Cite the capabilities/manifest file, an exact existing call site, or a spike.
FEASIBILITY_EVIDENCE_KINDS: Final[tuple[str, ...]] = (
    "capabilities-file", "existing-call-site", "spike",
)


@dataclass(frozen=True)
class ApiFeasibility:
    api: str
    status: str | None            # "verified" | "assumed" | None (unrecognized/missing)
    kind: str | None              # evidence kind for a verified note
    citation: str | None          # evidence citation for a verified note
    failure: str | None           # "fail-loud" | "fail-silent" | None
    surface: str | None           # surfacing assertion/log; required when fail-silent


@dataclass(frozen=True)
class FeasibilityDecl:
    present: bool                  # a `platform_apis:` line exists
    none: bool                    # the declaration is exactly `none`
    apis: tuple[ApiFeasibility, ...] = ()
    ambiguous: bool = False       # >1 non-fenced `platform_apis:` line — fail closed


_PLATFORM_APIS_RE = re.compile(r"^\s*platform_apis\s*:\s*(.*)$", re.IGNORECASE)
_FEAS_API_RE = re.compile(r"^\s*-\s*api\s*:\s*(.+?)\s*$", re.IGNORECASE)
# Fields are dashless + indented under `- api:` (per the contract). No leading `-?`:
# a `- feasibility:` line is a NEW (malformed) list item, not a field of the prior
# block, and must NOT bleed its value into it (#226 review, finding 4).
_FEAS_FEASIBILITY_RE = re.compile(r"^\s+feasibility\s*:\s*(.+?)\s*$", re.IGNORECASE)
_FEAS_FAILURE_RE = re.compile(r"^\s+failure\s*:\s*(.+?)\s*$", re.IGNORECASE)
_FEAS_SURFACE_RE = re.compile(r"^\s+surface\s*:\s*(.+?)\s*$", re.IGNORECASE)
# `verified via <kind> <sep> <citation>` — sep tolerates em-dash (U+2014), en-dash
# (U+2013), minus (U+2212), hyphen, and colon (editor smart-punctuation produces all
# of these; the WF3 drift guard hit exactly this dash-variant bug).
_FEAS_VERIFIED_RE = re.compile(
    r"^verified\s+via\s+([a-z][a-z-]*)\s*(?:[—–−\-:]\s*(.*\S))?\s*$", re.IGNORECASE
)
_MD_HEADING_RE = re.compile(r"^\s*#{1,6}\s")


def _parse_feasibility_value(raw: str) -> tuple[str | None, str | None, str | None]:
    """Parse a `feasibility:` value → (status, kind, citation)."""
    val = raw.strip()
    low = val.lower()
    if low == "assumed":
        return ("assumed", None, None)
    if low.startswith("verified"):
        m = _FEAS_VERIFIED_RE.match(val)
        if m:
            citation = m.group(2).strip() if m.group(2) else None
            return ("verified", m.group(1).lower(), citation)
        return ("verified", None, None)  # malformed → assert flags bad kind/citation
    return (None, None, None)


def parse_feasibility_block(text: str) -> "FeasibilityDecl | None":
    """Extract the `platform_apis:` declaration from a design doc / RCA.

    Returns None when NO `platform_apis:` line is present, so the caller can tell
    "declaration absent" (an omission → Step-4 error) from "declared none". The
    grammar: exactly ONE non-fenced `platform_apis:` line is the declaration (>1 is
    ambiguous → `ambiguous=True`, fails closed); `none` on that line means no platform
    APIs; otherwise each following `- api:` opens a block whose dashless-indented
    `feasibility:`/`failure:`/`surface:` fields belong to it until the next `- api:`
    or the next markdown heading (or EOF).
    """
    lines = text.splitlines()
    # Mark fenced lines so a design doc that *quotes* the contract in a code block
    # (this very feature's design doc does) is not mis-parsed as a real declaration —
    # the prose declaration is what counts, not a quoted example. Both ``` and ~~~
    # fences are handled. (A 4-space-indented code block is a residual gap — rare in
    # these docs, and a false-reject if ever hit, not a fail-open.)
    fenced = [False] * len(lines)
    in_fence = False
    for i, ln in enumerate(lines):
        if re.match(r"^\s*(?:```|~~~)", ln):
            in_fence = not in_fence
            fenced[i] = True  # the fence marker line itself is skipped
            continue
        fenced[i] = in_fence

    # Collect ALL non-fenced declarations. Exactly one is required: >1 is ambiguous
    # and fails closed, so an early stray `platform_apis: none` (e.g. in a summary)
    # cannot shadow a later real declaration whose APIs are unproven (#226 review,
    # finding 1 — the first-`none`-wins fail-open).
    decl_indices = [
        i for i, ln in enumerate(lines)
        if not fenced[i] and _PLATFORM_APIS_RE.match(ln)
    ]
    if not decl_indices:
        return None
    if len(decl_indices) > 1:
        return FeasibilityDecl(present=True, none=False, apis=(), ambiguous=True)
    decl_idx = decl_indices[0]
    decl_rest = _PLATFORM_APIS_RE.match(lines[decl_idx]).group(1).strip()
    if decl_rest.lower() == "none":
        return FeasibilityDecl(present=True, none=True, apis=())

    apis: list[ApiFeasibility] = []
    cur: dict | None = None

    def _flush():
        nonlocal cur
        if cur is not None:
            apis.append(ApiFeasibility(**cur))
            cur = None

    for j in range(decl_idx + 1, len(lines)):
        ln = lines[j]
        if fenced[j]:
            continue
        if _MD_HEADING_RE.match(ln):
            break
        am = _FEAS_API_RE.match(ln)
        if am:
            _flush()
            cur = {"api": am.group(1).strip(), "status": None, "kind": None,
                   "citation": None, "failure": None, "surface": None}
            continue
        if cur is None:
            continue
        fm = _FEAS_FEASIBILITY_RE.match(ln)
        if fm:
            cur["status"], cur["kind"], cur["citation"] = _parse_feasibility_value(fm.group(1))
            continue
        flm = _FEAS_FAILURE_RE.match(ln)
        if flm:
            cur["failure"] = flm.group(1).strip().lower()
            continue
        sm = _FEAS_SURFACE_RE.match(ln)
        if sm:
            cur["surface"] = sm.group(1).strip()
            continue
    _flush()
    return FeasibilityDecl(present=True, none=False, apis=tuple(apis))


def assert_feasibility_declared(decl: "FeasibilityDecl | None") -> tuple[bool, list[str]]:
    """The mechanical Step-4 feasibility gate (#226). Fail-closed. Returns (ok, errors).

    - `decl is None` (declaration absent) → error: an omitted `platform_apis:`
      declaration must not pass, else the silent-gap failure class returns.
    - `decl.ambiguous` (>1 declaration) → error: exactly one is required.
    - `decl.none` → ok.
    - else per api block: `assumed` blocks; a verified note needs an allowed evidence
      kind AND a non-empty citation; a `fail-silent` API needs a `surface:` (AC4);
      a missing `failure:` classification blocks.
    """
    if decl is None:
        return (False, [
            "design must declare `platform_apis:` (either `none` or one block per "
            "material platform/external API) — an omitted declaration cannot pass "
            "Step 4 (#226)"
        ])
    if decl.ambiguous:
        return (False, [
            "multiple `platform_apis:` declarations found — a design must have exactly "
            "one, else an early stray `none` can shadow a real declaration (#226)"
        ])
    if decl.none:
        return (True, [])
    errors: list[str] = []
    if not decl.apis:
        errors.append(
            "`platform_apis:` declared but is neither `none` nor any `- api:` block"
        )
    for a in decl.apis:
        label = a.api or "<unnamed api>"
        if a.status == "assumed":
            errors.append(
                f"api {label!r}: feasibility is `assumed` — must be verified against "
                f"this project's real config before Step 4 (#226)"
            )
        elif a.status == "verified":
            if a.kind not in FEASIBILITY_EVIDENCE_KINDS:
                errors.append(
                    f"api {label!r}: evidence kind {a.kind!r} is not one of "
                    f"{FEASIBILITY_EVIDENCE_KINDS}"
                )
            if not _is_nonempty_str(a.citation):
                errors.append(
                    f"api {label!r}: `verified via {a.kind}` has an empty citation — "
                    f"cite the file path / doc section / call site / spike result"
                )
        else:
            errors.append(
                f"api {label!r}: unrecognized or missing feasibility status "
                f"(expected `verified via <kind> — <citation>` or `assumed`)"
            )
        if a.failure not in ("fail-loud", "fail-silent"):
            errors.append(
                f"api {label!r}: missing/invalid `failure:` classification "
                f"(must be fail-loud|fail-silent)"
            )
        elif a.failure == "fail-silent" and not _is_nonempty_str(a.surface):
            errors.append(
                f"api {label!r}: `failure: fail-silent` requires a `surface:` "
                f"assertion/log so build #1 reveals the failure (#226 AC4)"
            )
    return (len(errors) == 0, errors)


_GLOB_CHARS = frozenset("*?[]")


def _classify_decl(path: str) -> tuple[str, str]:
    """Classify a declared file path for disjointness proof.

    Returns ``(kind, value)``. ``kind`` is ``"ok"`` with ``value`` set to a
    canonical comparison key (normalized + case-folded), or one of
    ``"glob"``/``"directory"``/``"absolute"`` (value = the raw path) when the
    declaration cannot be statically proven disjoint. We case-fold so that on a
    case-insensitive filesystem ``A.py`` and ``a.py`` are treated as the same
    file — this can only ADD conflicts (lose the optimization), never miss a
    real collision. Symlink/hardlink aliasing is out of scope for a pure static
    check; the runtime touched-file gate (issue #85, PR 3b) is the real backstop.
    """
    if any(c in _GLOB_CHARS for c in path):
        return ("glob", path)
    if path.endswith("/"):
        return ("directory", path)
    if os.path.isabs(path):
        return ("absolute", path)
    norm = os.path.normpath(path)
    # Reject anything that escapes the repo or denotes the root: `..`, `../x`
    # (can re-enter and alias another declared file), and `.` (the root, an
    # ancestor of every path — the prefix-based overlap check would miss it).
    if norm == "." or norm == ".." or norm.startswith(".." + os.sep) or norm.startswith("../"):
        return ("non-repo-relative", path)
    return ("ok", norm.casefold())


def validate_parallel_groups(tasks: list[Task]) -> tuple[bool, list[str]]:
    """Prove that same-`parallel_group` tasks are statically file-disjoint.

    Returns ``(all_eligible, conflicts)``. ``all_eligible`` is True iff every
    parallel_group with >=2 members has every member declaring concrete,
    repo-relative, non-glob, non-directory ``files`` AND no two members'
    declared paths are equal or nested (one an ancestor directory of the
    other). Ungrouped tasks (parallel_group is None) and singleton groups are
    never flagged.

    A conflict (overlap, missing files, glob, directory, or absolute path)
    means the group is NOT parallel-eligible. The caller MUST run such a group
    sequentially — so an un-provable group degrades to serial execution, never
    to a concurrent collision. This is a STATIC pre-dispatch heuristic only;
    the runtime touched-file gate (issue #85, PR 3b) is the real backstop once
    isolated parallel execution exists.
    """
    groups: dict[str, list[Task]] = {}
    for t in tasks:
        if t.parallel_group is not None:
            groups.setdefault(t.parallel_group, []).append(t)

    conflicts: list[str] = []
    for gid, members in groups.items():
        if len(members) < 2:
            continue  # nothing to parallelize / no overlap possible

        # Phase 1 — every member must declare only provable concrete files.
        # If ANY member's declaration is unprovable, the whole group is not
        # eligible and we skip the overlap proof (avoids under-reporting).
        group_valid = True
        norm_sets: dict[str, set[str]] = {}
        for t in members:
            if not t.files:
                conflicts.append(
                    f"parallel_group {gid!r}: task {t.id} declares no files "
                    f"-> cannot prove disjointness, not parallel-eligible"
                )
                group_valid = False
                continue
            normed: set[str] = set()
            for f in t.files:
                kind, value = _classify_decl(f)
                if kind != "ok":
                    label = {"absolute": "absolute path",
                             "non-repo-relative": "non-repo-relative path"}.get(kind, kind)
                    conflicts.append(
                        f"parallel_group {gid!r}: task {t.id} declares {label} {f!r} "
                        f"-> cannot prove disjointness, not parallel-eligible"
                    )
                    group_valid = False
                else:
                    normed.add(value)
            norm_sets[t.id] = normed
        if not group_valid:
            continue

        # Phase 2 — pairwise overlap among fully-valid members. Two paths
        # collide if equal OR one is an ancestor directory of the other.
        ids = list(norm_sets.keys())
        for a in range(len(ids)):
            for b in range(a + 1, len(ids)):
                for fa in sorted(norm_sets[ids[a]]):
                    for fb in sorted(norm_sets[ids[b]]):
                        if fa == fb:
                            conflicts.append(
                                f"parallel_group {gid!r}: tasks {ids[a]} and {ids[b]} both "
                                f"touch {fa} -> overlap, not parallel-eligible"
                            )
                        elif fb.startswith(fa + "/") or fa.startswith(fb + "/"):
                            conflicts.append(
                                f"parallel_group {gid!r}: tasks {ids[a]} and {ids[b]} declare "
                                f"nested paths {fa} / {fb} -> cannot prove disjointness, "
                                f"not parallel-eligible"
                            )

    return (len(conflicts) == 0, conflicts)


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
# The canonical phrasings below MUST appear contiguously (case-insensitive)
# in both skills/implement-feature/SKILL.md and docs/principles.md.
# tests/test_skill_helpers.py::test_risk_criteria_canonical_strings_appear_in_docs
# enforces this drift guard.
RISK_CRITERIA: Final[tuple[str, ...]] = (
    "security surface",
    "module boundary",
    "non-trivial error/exception flow",
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

# Anchor each pattern to path-segment boundaries to reduce false positives.
# A pattern "auth" should match `src/auth/login.ts` and `AUTH/handler.py` but
# NOT `src/author.ts` or `lib/authority/x.ts`. Boundary chars: start/end,
# slash, underscore, dot, hyphen. Trailing `s` is allowed for natural plurals
# (`secret` matches `secrets.yaml`, `migration` matches `migrations/`).
# `\.env` already starts with a dot so we don't double-anchor on its left side.
_BOUNDARY = r"(?:^|[/_.\-])"
_BOUNDARY_END = r"s?(?:$|[/_.\-])"


def _anchor(pattern: str) -> str:
    # `\.env` already has a leading `\.` which IS a boundary char; don't
    # double-anchor or we'd require two dots. Other patterns get full anchoring.
    if pattern.startswith(r"\."):
        return pattern + _BOUNDARY_END
    return _BOUNDARY + pattern + _BOUNDARY_END


_HIGH_RISK_PATH_RE = re.compile(
    "(" + "|".join(_anchor(p) for p in DEFAULT_HIGH_RISK_PATH_PATTERNS) + ")",
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


def any_high_risk_path(paths: list[str], extra_patterns: tuple[str, ...] = ()) -> str | None:
    """Return the FIRST path in `paths` that matches the high-risk allowlist.

    Public plural face of the private `_path_matches_high_risk` matcher (same
    precedent as `read_review_log` wrapping `_read_review_log`). Used by WF2
    Step 11's adversarial-diff-review dispatch gate (#131). Returns the PATH
    (not the matched pattern), or None if no path matches. Paths are expected
    POSIX-separated (git output form); backslash-separated paths will not match.
    """
    if isinstance(extra_patterns, str):
        raise TypeError("extra_patterns must be a tuple of patterns, not str")
    for p in paths:
        if _path_matches_high_risk(p, extra_patterns) is not None:
            return p
    return None


def should_run_diff_review(
    enabled: bool,
    changed_paths: list[str],
    has_high_risk_task: bool,
    extra_patterns: tuple[str, ...] = (),
) -> tuple[bool, str]:
    """WF2 Step 11's adversarial-diff-review dispatch gate (#131).

    Pure (no I/O) so the 9-cell decision matrix is unit-testable. Paths are
    expected POSIX-separated (git output form); backslash-separated paths
    will not match. Evaluated in order:
    - not enabled              -> (False, "disabled")
    - not changed_paths        -> (False, "empty diff")
    - a high-risk path matches -> (True, f"high-risk path: {matched_path}")
    - has_high_risk_task       -> (True, "high-risk task in plan")
    - else                     -> (False, "no security surface")
    """
    if isinstance(extra_patterns, str):
        raise TypeError("extra_patterns must be a tuple of patterns, not str")
    if changed_paths is None or isinstance(changed_paths, str):
        raise TypeError(
            "changed_paths must be a list of paths (got %s)" % type(changed_paths).__name__
        )
    changed_paths = [p for p in changed_paths if p]
    if not enabled:
        return False, "disabled"
    if not changed_paths:
        return False, "empty diff"
    matched = any_high_risk_path(changed_paths, extra_patterns)
    if matched:
        return True, f"high-risk path: {matched}"
    if has_high_risk_task:
        return True, "high-risk task in plan"
    return False, "no security surface"


def should_promote(
    _task_id: str,
    file_paths: list[str],
    loc_delta: int,
    extra_high_risk_patterns: tuple[str, ...] = (),
) -> tuple[bool, str | None]:
    """Mechanical heuristic for mid-flight task promotion (standard -> high).

    `_task_id` is reserved for future logging/telemetry; the heuristic itself
    decides solely on `file_paths` and `loc_delta`.

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


def format_promotion_note(
    task_id: str,
    criterion: str,
    rationale: str,
    step: str = "8",
    *,
    issue: "int | str | None" = None,
) -> str:
    """Format a session-notes line documenting a mid-flight promotion.

    `step` defaults to "8" (the WF2 step where promotion fires today). If
    P15 ever sprouts a Step 8b promotion or a different workflow reuses
    the helper, callers can override.

    `issue` (#341) attributes the marker to a GitHub issue number for
    issue-keyed step markers: when set (int or str), the detail portion
    (immediately after "Promoted {task_id}: ") is prefixed with
    "#<issue>: ". Defaults to None for backward compat — omitting it
    reproduces today's exact output byte-for-byte.
    """
    detail = f"standard -> high (criterion: {criterion}; rationale: {rationale})"
    if issue is not None:
        # accept both 341 and "#341" — emit exactly one '#' (a "##341" token
        # would never match the canonical #<n> key shape, #341 review catch)
        detail = f"#{str(issue).lstrip('#')}: {detail}"
    return f"### WF2 Step {step} — Promoted {task_id}: {detail}"


# --- Small-standard lane decision (#135) ---

LANE_MAX_IMPL_FILES: Final[int] = 7
# #225: secondary lane signal — at most this many bounded defects, each within
# LANE_MAX_IMPL_FILES, aggregate <= MAX_LANE_DEFECTS * LANE_MAX_IMPL_FILES.
MAX_LANE_DEFECTS: Final[int] = 3


# Well-known project docs are NEVER product, even in markdown-is-product mode (#143):
# a repo's README/CHANGELOG/etc. are documentation regardless of where they live, so the
# `laneImplExtensions` opt-in must not sweep them into the impl count. Matched case-insensitively.
_ALWAYS_DOC_BASENAMES: Final[frozenset[str]] = frozenset({
    "readme.md", "changelog.md", "contributing.md", "security.md",
    "code_of_conduct.md", "codeofconduct.md",
})


def _normalize_impl_extensions(raw) -> tuple[str, ...]:
    """Normalize an iterable of extensions to lowercase leading-dot form, order-preserving
    dedupe. Non-str / blank items are skipped; a non-iterable (or str) yields ()."""
    if raw is None or isinstance(raw, str):
        return ()
    try:
        items = list(raw)
    except TypeError:
        return ()
    out: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        ext = item.strip().lower()
        if not ext.startswith("."):
            ext = "." + ext
        if ext not in out:
            out.append(ext)
    return tuple(out)


def _is_excluded_impl_file(path: str, impl_extensions: tuple[str, ...] = ()) -> bool:
    """True if `path` should NOT count toward LANE_MAX_IMPL_FILES.

    Excludes test files, docs, and lockfiles/generated build output. Paths
    are expected POSIX-separated (git diff output form), matching the same
    convention as any_high_risk_path/should_run_diff_review.

    `impl_extensions` (#143 — markdown-is-product opt-in, ALREADY normalized to
    lowercase leading-dot form): extensions a project declares as *implementation*
    even though they'd normally be excluded as docs (e.g. `.md` for a prompt/skill
    repo whose product IS `skills/*/SKILL.md`). Never product regardless of the
    opt-in: a `docs/` dir, well-known project docs (README/CHANGELOG/…), test files,
    and generated/lock artifacts.
    """
    segments = path.split("/")
    basename = segments[-1]
    low = basename.lower()
    if "tests" in segments or basename.startswith("test_") or "_test." in basename:
        return True
    if "docs" in segments:
        return True  # a docs/ dir is always docs, even when .md is declared impl
    if low.endswith(".md"):
        if low in _ALWAYS_DOC_BASENAMES:
            return True  # README/CHANGELOG/… are docs even in markdown-is-product mode
        if not any(low.endswith(e) for e in impl_extensions):
            return True  # markdown excluded unless the project declares it product
    if (
        basename in ("package-lock.json", "poetry.lock")
        or low.endswith(".lock")
        or ".min." in basename
    ):
        return True
    return False


def count_impl_files(paths, *, impl_extensions=None) -> int:
    """Count implementation source files for the small-standard lane (#135).

    Excludes test files, docs, and lockfiles/generated artifacts — see
    `_is_excluded_impl_file`. `None` -> 0 (no files is a valid pre-diff
    state). A bare str raises TypeError (same fail-closed precedent as
    `should_run_diff_review`) so a single path isn't iterated char-wise.

    `impl_extensions` (#143): a project's declared markdown-is-product extensions.
    Normalized here defensively (so a bare `"md"` or `".MD"` works even for a direct
    caller that skipped `lane_impl_extensions`). `None`/empty keeps the default
    behavior (exclude `.md`/`docs/`), so ordinary app repos are unaffected.
    """
    if paths is None:
        return 0
    if isinstance(paths, str):
        raise TypeError("paths must be an iterable of path strings, not str")
    exts = _normalize_impl_extensions(impl_extensions)
    return sum(1 for p in paths if not _is_excluded_impl_file(p, exts))


def lane_impl_extensions(config) -> tuple[str, ...]:
    """Normalize a project's `laneImplExtensions` config into a tuple of extensions
    (#143 — markdown-is-product opt-in).

    Reads `config["laneImplExtensions"]` (a list like `["md"]` or `[".md"]`),
    normalizes each to a lowercase leading-dot extension, dedupes preserving order.
    Fail-closed: a missing key, a non-dict config, or a non-list value all yield
    `()` (the default = current behavior, so app repos never regress).
    """
    if not isinstance(config, dict):
        return ()
    raw = config.get("laneImplExtensions")
    if not isinstance(raw, list):
        return ()
    return _normalize_impl_extensions(raw)


_LANE_ELIGIBLE_COMPLEXITIES: Final[tuple[str, ...]] = ("simple_change", "standard_feature")


def lane_decision(
    complexity: str,
    impl_file_count: int,
    has_arch_change: bool,
    has_migration: bool,
    has_new_dep: bool,
    is_trivial: bool,
    *,
    defect_file_counts: list[int] | None = None,
    operator_override: bool = False,
) -> tuple[str, str]:
    """Decide the WF2 execution tier for the small-standard lane (#135).

    Pure (no I/O); never raises except the impl_file_count type guard below.
    Returns (tier, reason) with tier in {"trivial", "full", "lane"}.
    Evaluated in order:
    - is_trivial                              -> ("trivial", ...)
    - complexity == "complex_feature"         -> ("full", ...)
    - arch change / migration / new dep       -> ("full", ...) (first true wins)
    - complexity not lane-eligible (defensive)-> ("full", ...)
    - operator_override (#225)               -> ("lane", ...) — AFTER the hard
      guards above, which it can never bypass; reason names the override
    - impl_file_count <= LANE_MAX_IMPL_FILES  -> ("lane", ...)
    - secondary signal (#225): count > cap but the change is 2..MAX_LANE_DEFECTS
      bounded defects, each 1..LANE_MAX_IMPL_FILES files, and the total is
      <= MAX_LANE_DEFECTS * LANE_MAX_IMPL_FILES -> ("lane", ...) with the
      per-defect counts enumerated verbatim; malformed/over-bound entries fall
      through to full (fail-closed)
    - else                                    -> ("full", ...)
    """
    if is_trivial:
        return "trivial", "trivial — handled by trivial-work exit"
    if complexity == "complex_feature":
        return "full", "complex_feature — full spine"
    if has_arch_change or has_migration or has_new_dep:
        if has_arch_change:
            reason = "architecture change"
        elif has_migration:
            reason = "migration"
        else:
            reason = "new dependency"
        return "full", f"{reason} — full spine"
    if complexity not in _LANE_ELIGIBLE_COMPLEXITIES:
        return "full", f"unknown complexity {complexity!r} — full spine"
    # Type guard protects the comparison below: bool is an int subclass, and
    # a truthy string/None must not silently pass `>` rather than raise.
    if isinstance(impl_file_count, bool) or not isinstance(impl_file_count, int):
        raise TypeError(
            f"impl_file_count must be an int (got {type(impl_file_count).__name__})"
        )
    if operator_override:
        return "lane", (
            f"operator override — lane elected ({impl_file_count} impl files; "
            f"hard guards passed)"
        )
    if impl_file_count > LANE_MAX_IMPL_FILES:
        counts = defect_file_counts
        if (
            isinstance(counts, list)
            and 2 <= len(counts) <= MAX_LANE_DEFECTS
            and all(
                isinstance(c, int) and not isinstance(c, bool)
                and 1 <= c <= LANE_MAX_IMPL_FILES
                for c in counts
            )
            and impl_file_count <= MAX_LANE_DEFECTS * LANE_MAX_IMPL_FILES
        ):
            return "lane", (
                f"bounded multi-defect: {len(counts)} defects "
                f"({'+'.join(str(c) for c in counts)}), each ≤ {LANE_MAX_IMPL_FILES}, "
                f"total {impl_file_count} ≤ {MAX_LANE_DEFECTS * LANE_MAX_IMPL_FILES} "
                f"— lane (secondary signal)"
            )
        return "full", f"{impl_file_count} impl files > {LANE_MAX_IMPL_FILES} — full spine"
    return "lane", f"small-standard: {complexity}, {impl_file_count} impl files ≤ {LANE_MAX_IMPL_FILES}"


# --- review log (jsonl) ---


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
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(_json.loads(raw))
            except _json.JSONDecodeError:
                # Surface corruption to operator; do not raise — the
                # assert_review_coverage gate will fail cleanly.
                print(
                    f"plan_lib: skipping malformed review log line "
                    f"{lineno} in {log_path}: {raw[:80]!r}",
                    file=sys.stderr,
                )
                continue
    return out


def read_review_log(log_path: str) -> list[dict]:
    """Public reader for the review log.

    Step 11's pre-flight builds `reviewed_shas` (and re-reads verdicts) from the
    log; this thin public alias of the internal reader means callers never reach
    into a private symbol or re-implement JSONL parsing inline (the fragile
    pattern the CLI extraction in #87-89 set out to remove).
    """
    return _read_review_log(log_path)


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


def _write_deferrals(deferrals_path: str, deferrals: list[dict]) -> None:
    os.makedirs(os.path.dirname(deferrals_path) or ".", exist_ok=True)
    with open(deferrals_path, "w", encoding="utf-8") as f:
        _json.dump(deferrals, f, indent=2)


def append_deferral(deferrals_path: str, finding: dict) -> dict:
    """Create (or re-defer) a finding in the deferrals file (a JSON array).

    `finding` MUST include 'finding_id' and 'severity'. severity is required
    because the Step 11 exit gate (assert_no_unresolved_high_deferrals) keys on
    it — a missing severity would let a deferred High/Critical fall out of the
    re-presentation silently, the exact failure this writer exists to prevent.
    Safe defaults make the entry well-formed for `_deferral_is_resolved`:
    status='deferred', defer_count=1, concurrences=[], user_ack=False. If a
    deferral with the same finding_id already exists this is a RE-deferral: its
    defer_count is incremented (so a chain of >=2 correctly demands user_ack)
    rather than appending a duplicate row. Returns the written entry.
    """
    fid = finding.get("finding_id")
    if not fid:
        raise ValueError("append_deferral: finding must include 'finding_id'")
    if not finding.get("severity"):
        raise ValueError(
            "append_deferral: finding must include 'severity' "
            "(the Step 11 exit gate keys on it)"
        )
    deferrals = get_deferred_findings(deferrals_path)
    existing = next((d for d in deferrals if d.get("finding_id") == fid), None)
    if existing is not None:
        existing["defer_count"] = int(existing.get("defer_count", 1)) + 1
        entry = existing
    else:
        entry = {
            "finding_id": fid,
            "severity": finding["severity"],
            "status": finding.get("status", "deferred"),
            "defer_count": int(finding.get("defer_count", 1)),
            "originator_reviewer_slot": finding.get("originator_reviewer_slot"),
            "concurrences": list(finding.get("concurrences", [])),
            "user_ack": bool(finding.get("user_ack", False)),
        }
        # carry through any extra descriptive fields without clobbering the above
        for k, v in finding.items():
            entry.setdefault(k, v)
        deferrals.append(entry)
    _write_deferrals(deferrals_path, deferrals)
    return entry


def resolve_deferral(
    deferrals_path: str,
    finding_id: str,
    *,
    status: str | None = None,
    add_concurrence: str | None = None,
    user_ack: bool | None = None,
) -> dict:
    """Apply a resolution to an existing deferral and persist it.

    Mirrors append_deferral for the OTHER write direction so Step 11 never
    hand-authors the resolution fields whose semantics live in
    `_deferral_is_resolved`: set status='applied', and/or record an independent
    concurrence (a slot != originator_reviewer_slot), and/or set user_ack for a
    defer_count>=2 chain. Raises ValueError if finding_id is not present.
    """
    deferrals = get_deferred_findings(deferrals_path)
    target = next((d for d in deferrals if d.get("finding_id") == finding_id), None)
    if target is None:
        raise ValueError(
            f"resolve_deferral: no deferral with finding_id {finding_id!r}"
        )
    if status is not None:
        target["status"] = status
    if add_concurrence is not None:
        cons = target.setdefault("concurrences", [])
        if add_concurrence not in cons:
            cons.append(add_concurrence)
    if user_ack is not None:
        target["user_ack"] = bool(user_ack)
    _write_deferrals(deferrals_path, deferrals)
    return target


# --- disposition ledger (jsonl, #393) ---
#
# `claude_docs/.wf2-state/<issue>/dispositions.jsonl` — TERMINAL gate decisions
# (adopted | declined | dissolved) fed forward as reviewer context on pass-N
# adversarial dispatches. Normative record schema: design doc
# docs/planning/2026-07-15-393-disposition-ledger.md §1.

_DISPOSITION_VALUES: Final[tuple[str, ...]] = ("adopted", "declined", "dissolved")
_REOPENS_RE = re.compile(r"^REOPENS (d-[^\s:-]+-\d+-\d+-[A-Za-z0-9]{4}): (.+)$", re.DOTALL)


def compute_finding_key(finding: dict) -> str:
    """sha256 identity over EXACTLY the engine dedupe tuple.

    "sha256:" + hex sha256 of the UTF-8 bytes of
    json.dumps([severity, location or "", description],
    separators=(",",":"), ensure_ascii=True). `category` is deliberately
    EXCLUDED: the mechanical key must be relabel-proof (the same finding
    re-raised under a different category still matches the join backstop);
    the prompt's 4-field "substantive match" wording is model-facing
    guidance only.
    """
    payload = _json.dumps(
        [finding["severity"], finding.get("location") or "", finding["description"]],
        separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def append_disposition(ledger_path: str, entry: dict) -> None:
    """Append a TERMINAL disposition entry to the issue's ledger (plain line append).

    Auto-adds `ts` (ISO 8601 UTC) alongside the caller's `date`. Plain
    open(..., "a") — append-only JSONL needs no atomic rewrite, and the serial
    per-issue orchestrator is the one writer. Boundary vs deferrals.json: a
    DEFERRAL is an unresolved High in a resolution pipeline (re-presented at
    Step 11); the ledger holds only TERMINAL decisions
    (adopted | declined | dissolved) — a deferral that later resolves gets a
    ledger entry at THAT gate close.
    """
    enriched = dict(entry)
    enriched.setdefault("ts", _now_iso())
    line = _json.dumps(enriched, separators=(",", ":")) + "\n"
    os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(line)


def _disposition_entry_error(entry: dict) -> str | None:
    """Return a reason string if the entry is invalid, else None."""
    if entry.get("schema_version") != 1:
        return f"schema_version {entry.get('schema_version')!r} != 1"
    required_str = ("id", "finding_key", "disposition", "reason", "decided_by", "date")
    for field in required_str:
        if not isinstance(entry.get(field), str) or not entry[field]:
            return f"missing/mistyped field {field!r}"
    for field in ("issue", "pass"):
        if not isinstance(entry.get(field), int) or isinstance(entry.get(field), bool):
            return f"missing/mistyped field {field!r}"
    if not isinstance(entry.get("gate"), str):
        return "missing/mistyped field 'gate'"
    if entry["disposition"] not in _DISPOSITION_VALUES:
        return f"disposition {entry['disposition']!r} not terminal (adopted|declined|dissolved)"
    finding = entry.get("finding")
    if not isinstance(finding, dict):
        return "missing/mistyped field 'finding'"
    for field in ("severity", "category", "description"):
        if not isinstance(finding.get(field), str) or not finding[field]:
            return f"missing/mistyped finding field {field!r}"
    if entry["finding_key"] != compute_finding_key(finding):
        return "finding_key does not recompute from stored finding fields"
    return None


def read_dispositions(ledger_path: str) -> tuple[list[dict], int]:
    """Tolerant ledger reader. Returns (valid_entries, skipped_count).

    Missing file -> ([], 0). A line is CORRUPT — skipped with a stderr warning
    and counted — when it fails JSON parse OR entry validation
    (schema_version == 1, required fields with the stated types, finding_key
    recomputes from the stored finding fields). Bounded, visible loss: the
    caller's degraded marker carries the skipped count. Boundary vs
    deferrals.json: this ledger holds TERMINAL decisions only — `deferred` is
    NOT a disposition here (deferrals have their own file and gate).
    """
    if not os.path.exists(ledger_path):
        return [], 0
    entries: list[dict] = []
    skipped = 0
    with open(ledger_path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = _json.loads(raw)
            except _json.JSONDecodeError:
                skipped += 1
                print(
                    f"plan_lib: skipping corrupt dispositions line {lineno} "
                    f"in {ledger_path}: not JSON: {raw[:80]!r}",
                    file=sys.stderr,
                )
                continue
            reason = _disposition_entry_error(entry) if isinstance(entry, dict) \
                else "entry is not an object"
            if reason is not None:
                skipped += 1
                print(
                    f"plan_lib: skipping corrupt dispositions line {lineno} "
                    f"in {ledger_path}: {reason}",
                    file=sys.stderr,
                )
                continue
            entries.append(entry)
    return entries, skipped


def fold_dispositions(entries: list[dict]) -> list[dict]:
    """Last-write-wins fold by finding_key in file order.

    Later entries for the same identity supersede earlier ones (append-only
    history; the read_review_log precedent). Returns the surviving entries in
    first-seen key order.
    """
    folded: dict[str, dict] = {}
    for entry in entries:
        folded[entry["finding_key"]] = entry
    return list(folded.values())


def strip_reopens(description: str) -> tuple[str | None, str]:
    """Parse an optional leading 'REOPENS <id>:' prefix from a finding description.

    Returns (disposition_id, stripped_text) when the prefix is well-formed —
    id matches the d-<gate>-<pass>-<seq>-<tok> shape AND non-empty delta text
    follows the colon — else (None, original_text). The join backstop computes
    the comparison key over the STRIPPED text (hashing the prefixed text would
    make the matched-entry validation unreachable).
    """
    m = _REOPENS_RE.match(description)
    if m is None:
        return None, description
    delta = m.group(2).strip()
    if not delta:
        return None, description
    return m.group(1), delta


# --- loop-back budget persistence ---

_LOOPBACK_SOURCES: Final[tuple[str, ...]] = (
    "design", "tdd", "review", "review_design", "spec_tighten",
)
_LOOPBACK_SOURCE_MAX = {
    "design": 2,
    "tdd": 1,
    "review": 1,
    "review_design": 1,
    # #223: cheap in-gate spec-tightening pass (amend + 1 verifier, no
    # Step-3 return). Shares the global budget: spec passes can starve a
    # later design loop-back — accepted; worst case is today's escalate.
    "spec_tighten": 2,
}
GLOBAL_LOOPBACK_BUDGET: Final[int] = 3

_SPEC_TIGHTENING_TAG: Final[str] = "spec-tightening"


def classify_loopback_source(flaw_classes: list[str]) -> str:
    """Fold per-finding Loopback-class tags into the loop-back source (#223).

    Contract: the caller passes EXACTLY ONE entry per Critical/High finding;
    a finding lacking the field contributes the literal "untagged". Returns
    "spec_tighten" only when the list is non-empty and every entry normalizes
    (case/whitespace) to "spec-tightening"; anything else — any "design-flaw",
    unknown, "untagged", or an empty list — folds to "design" (fail-closed).
    """
    if not flaw_classes:
        return "design"
    if all(c.strip().lower() == _SPEC_TIGHTENING_TAG for c in flaw_classes):
        return "spec_tighten"
    return "design"


def _read_loopback_state(path: str) -> dict:
    if not os.path.exists(path):
        return {src: 0 for src in _LOOPBACK_SOURCES} | {"total": 0}
    with open(path, "r", encoding="utf-8") as f:
        state = _json.load(f)
    # Backfill missing per-source keys (treat absent as 0)
    for src in _LOOPBACK_SOURCES:
        v = state.get(src, 0)
        if not isinstance(v, int) or v < 0:
            v = 0  # corruption / version skew — reset
        state[src] = v
    # ALWAYS recompute total from per-source values (do not trust on-disk total)
    # to defeat the corruption case where total and per-source disagree.
    state["total"] = sum(state[s] for s in _LOOPBACK_SOURCES)
    return state


def _write_loopback_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        _json.dump(state, f, separators=(",", ":"), sort_keys=True)


def _git_run(repo: str, args: list[str]) -> str:
    """Run a git command in `repo` and return stdout. Raises on non-zero exit."""
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
        except subprocess.CalledProcessError as exc:
            print(
                f"plan_lib: scan_prior_commits_for_trigger skipping {sha}: "
                f"git show exited {exc.returncode}",
                file=sys.stderr,
            )
            continue
        paths, loc_delta = _parse_numstat(stat)
        promote, _ = should_promote(sha, paths, loc_delta, extra_high_risk_patterns)
        if promote:
            flagged.append(sha)
    return flagged


# --- Whole-issue delegated build receipt validation (#133) ---


def _commit_exists(repo: str, sha: str) -> bool:
    """True iff `sha` resolves to a commit object in `repo`."""
    try:
        _git_run(repo, ["cat-file", "-e", f"{sha}^{{commit}}"])
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False


def _same_commit(repo: str, a: str, b: str) -> bool:
    """True iff `a` and `b` resolve to the same commit object."""
    try:
        ra = _git_run(repo, ["rev-parse", f"{a}^{{commit}}"]).strip()
        rb = _git_run(repo, ["rev-parse", f"{b}^{{commit}}"]).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False
    return bool(ra) and ra == rb


def _sha_is_descendant(repo: str, base: str, sha: str) -> bool:
    """True iff `base` is an ancestor of `sha` (sha is at or after base).

    `git merge-base --is-ancestor` exits 0 when base is an ancestor, 1 when it
    is not, and 128 on a bad object; only 0 counts as a descendant so a bad
    object fails closed.
    """
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", base, sha],
            cwd=repo, capture_output=True, text=True,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def _commit_files(repo: str, sha: str) -> set[str] | None:
    """The set of paths a single commit changed (its own diff vs its parent).

    Returns None on git failure so the caller fails closed.
    """
    try:
        out = _git_run(repo, ["show", "--name-only", "--format=", sha])
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return {p for p in (line.strip() for line in out.splitlines()) if p}


def _diff_files(repo: str, base: str) -> set[str] | None:
    """The set of paths changed across `base..HEAD`. None on git failure."""
    try:
        out = _git_run(repo, ["diff", "--name-only", f"{base}..HEAD"])
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return {p for p in (line.strip() for line in out.splitlines()) if p}


# --- #314: delegated-read index validation -------------------------------
#
# A cheap analysis-role reader subagent reads a token-heavy artifact in its
# own context and returns an INDEX (coordinates + capped one-liners + verbatim
# evidence), never a decision. The index is a hypothesis until validated here.
# Fail-closed: any structural problem, verdict-shaped structure, coverage
# miss, or fabricated quote rejects and the caller reads the artifact inline.
# AC3 honesty: the schema blocks STRUCTURED verdict-smuggling (closed keys, no
# severity/verdict/patch field, patch-shaped text rejected); a terse prose
# verdict still fits in 120 chars — that residual channel is neutralized by
# the orchestrator's raw-bytes re-read contract in steps.md, not here.

INDEX_SURFACES: Final[tuple[str, ...]] = ("step11-diff", "step2-map")

_INDEX_KEYS = {"surface", "source_ref", "entries", "coverage", "evidence",
               "truncated"}
_ENTRY_KEYS = {"locator", "component", "risk_tag", "one_line"}
_EVIDENCE_KEYS = {"file", "line", "text"}
_ONE_LINE_MAX = 120


def _load_read_delegate_bytes() -> tuple[int, int]:
    diff = _coerce_int_env("WF2_READ_DELEGATE_BYTES_DIFF", 65536)
    log = _coerce_int_env("WF2_READ_DELEGATE_BYTES_LOG", 32768)
    return (_clamp(diff, 4096, 10485760), _clamp(log, 4096, 10485760))


_rd_diff, _rd_log = _load_read_delegate_bytes()
WF2_READ_DELEGATE_BYTES_DIFF: Final[int] = _rd_diff
WF2_READ_DELEGATE_BYTES_LOG: Final[int] = _rd_log


def _patch_shaped(text: str) -> bool:
    """A patch-content line — never legitimate index prose. Design contract is
    ^[+-] (any sign-led line, so '+import os' is caught); the one deliberate
    carve-out is a sign immediately followed by a digit ('+10% faster'),
    which is prose, not a diff line."""
    if text.startswith(("+++", "---", "@@")):
        return True
    return bool(re.match(r"^[+-](?!\d)", text))


def validate_index(
    index,
    expected_units: list[str],
    artifact_text: "str | None" = None,
) -> tuple[bool, list[str]]:
    """Validate a delegated-read index (#314). Returns (ok, errors).

    expected_units is the unit list the dispatcher FED the reader
    (git diff --name-only output for step11-diff; component ids for
    step2-map). coverage.indexed must equal it exactly — for step11-diff
    that is a completeness proof; for step2-map a drop-guard only
    (discovered entries legitimately exceed the fed list and are NOT
    coverage units). Every evidence quote must be a verbatim substring of
    artifact_text — an EXISTENCE check, not attribution (file/line binding
    is neutralized by the orchestrator's raw-bytes re-read contract); when
    evidence is present but artifact_text is None the index REJECTS
    (fail-closed on the unverifiable case). Pure; never raises on a
    malformed index — malformed rejects. expected_units is dispatcher-fed
    and must be a list of str; anything else rejects.
    """
    errors: list[str] = []
    if not isinstance(index, dict):
        return (False, ["index is not a dict"])
    if not isinstance(expected_units, list) or any(
            not isinstance(u, str) for u in expected_units):
        return (False, ["expected_units must be a list of str"])

    extra = set(index) - _INDEX_KEYS
    missing = _INDEX_KEYS - set(index)
    if extra:
        errors.append(f"unknown top-level keys: {sorted(extra)}")
    if missing:
        errors.append(f"missing required keys: {sorted(missing)}")
        return (False, errors)

    surface = index["surface"]
    if surface not in INDEX_SURFACES:
        errors.append(f"unknown surface: {surface!r}")

    if (not isinstance(index["source_ref"], str)
            or not index["source_ref"].strip()):
        errors.append("source_ref must be a non-blank string")

    if index["truncated"] is not False:
        errors.append("truncated index rejected — a partial read is never "
                      "accepted for judgment surfaces; fall back to inline")

    entries = index["entries"]
    if not isinstance(entries, list) or not entries:
        errors.append("entries must be a non-empty list (a vacuous return is "
                      "a dead reader, not a clean pass)")
        entries = []
    expected_set = {u for u in expected_units if isinstance(u, str)}
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            errors.append(f"entries[{i}] is not an object")
            continue
        if set(e) - _ENTRY_KEYS:
            errors.append(f"entries[{i}] unknown keys: {sorted(set(e) - _ENTRY_KEYS)}")
        loc = e.get("locator")
        line = e.get("one_line")
        if not isinstance(loc, str) or not loc:
            errors.append(f"entries[{i}].locator must be a non-empty string")
            loc = ""
        if not isinstance(line, str) or not line:
            errors.append(f"entries[{i}].one_line must be a non-empty string")
            line = ""
        if len(line) > _ONE_LINE_MAX:
            errors.append(f"entries[{i}].one_line exceeds {_ONE_LINE_MAX} chars")
        if line and _patch_shaped(line):
            errors.append(f"entries[{i}].one_line is patch-shaped")
        # risk_tag / component: same discipline as one_line — a free channel
        # here would escape exactly the cap the AC3 argument leans on (8a).
        for opt_key in ("risk_tag", "component"):
            if opt_key not in e:
                continue
            val = e[opt_key]
            if not isinstance(val, str) or not val:
                errors.append(f"entries[{i}].{opt_key} must be a non-empty string")
            elif len(val) > _ONE_LINE_MAX:
                errors.append(f"entries[{i}].{opt_key} exceeds {_ONE_LINE_MAX} chars")
            elif _patch_shaped(val):
                errors.append(f"entries[{i}].{opt_key} is patch-shaped")
        if surface == "step11-diff" and loc:
            base = loc.split(":", 1)[0]
            if base not in expected_set:
                errors.append(f"entries[{i}].locator {base!r} not in the fed "
                              "unit list")

    cov = index["coverage"]
    if (not isinstance(cov, dict) or set(cov) != {"expected", "indexed"}
            or not isinstance(cov.get("expected"), list)
            or not isinstance(cov.get("indexed"), list)):
        errors.append("coverage must be {expected: [...], indexed: [...]}")
    elif (any(not isinstance(u, str) for u in cov["expected"])
            or any(not isinstance(u, str) for u in cov["indexed"])):
        # 8a HIGH (both reviewers, reproduced): nested JSON here made set()
        # raise TypeError — a reject, never a raise, is the whole contract.
        errors.append("coverage lists must contain only strings")
    else:
        if set(cov["expected"]) != expected_set:
            errors.append("coverage.expected does not match the fed unit list")
        if set(cov["indexed"]) != expected_set:
            errors.append("coverage.indexed != fed unit list (dropped or "
                          "invented units)")

    evidence = index["evidence"]
    if not isinstance(evidence, list):
        errors.append("evidence must be a list")
        evidence = []
    if evidence and artifact_text is None:
        # 8a Medium: an unverifiable quote must not pass silently.
        errors.append("evidence present but no artifact_text to verify "
                      "against — fail-closed on the unverifiable case")
    for i, ev in enumerate(evidence):
        if (not isinstance(ev, dict) or set(ev) != _EVIDENCE_KEYS
                or not isinstance(ev.get("file"), str)
                or not isinstance(ev.get("line"), int)
                or isinstance(ev.get("line"), bool)
                or not isinstance(ev.get("text"), str) or not ev.get("text")):
            errors.append(f"evidence[{i}] malformed (need file:str, line:int, "
                          "text:str)")
            continue
        if _patch_shaped(ev["text"]):
            errors.append(f"evidence[{i}].text is patch-shaped")
        if artifact_text is not None and ev["text"] not in artifact_text:
            errors.append(f"evidence[{i}].text not found verbatim in the "
                          "artifact — fabricated quote")

    return (not errors, errors)


def validate_build_receipt(
    receipt: dict,
    plan_tasks: list[Task],
    repo_root: str,
    branch_base_sha: str,
) -> tuple[bool, list[str], dict]:
    """Validate a whole-issue build subagent's RECEIPT against the real git tree.

    The receipt is a hypothesis; this is where the orchestrator confirms it
    before trusting a delegated build (#133). Fail-closed: any structural
    problem, missing/foreign/duplicate/base-equal sha, sha↔task file-set
    mismatch, baseline regression, or staging-discipline violation → rejected.
    git is read-only (cat-file, rev-parse, merge-base, show, diff); any git
    failure rejects rather than raises.

    Returns (ok, errors, normalized). `normalized["promoted_task_ids"]` is
    surfaced even on rejection, but ONLY when it is derivable — i.e. `receipt`
    is a dict and `receipt["promotions"]` is a list of objects with a string
    `task_id`. A non-dict receipt yields an empty list (nothing to derive), so
    the orchestrator must not assume Step 8a promotion scheduling happens on
    every rejected receipt.

    Receipt shape:
        {"task_shas": {task_id: sha}, "files_per_task": {task_id: [path]},
         "baseline": {"before": {"passed", "failed"},
                      "after": {"passed", "failed", "exit_code"}},
         "promotions": [{"task_id", "reason"}]}
    """
    normalized: dict = {"promoted_task_ids": [], "task_shas": {}}
    if not isinstance(receipt, dict):
        return (False, ["receipt is not a dict"], normalized)

    # Promotions surfaced first, best-effort, so they survive a later reject.
    promos = receipt.get("promotions", [])
    if isinstance(promos, list):
        normalized["promoted_task_ids"] = [
            p["task_id"]
            for p in promos
            if isinstance(p, dict) and isinstance(p.get("task_id"), str)
        ]

    errors: list[str] = []

    task_shas = receipt.get("task_shas")
    if not isinstance(task_shas, dict):
        return (False, ["receipt.task_shas is missing or not a dict"], normalized)
    files_per_task = receipt.get("files_per_task")
    if not isinstance(files_per_task, dict):
        return (False, ["receipt.files_per_task is missing or not a dict"], normalized)
    baseline = receipt.get("baseline")
    if not isinstance(baseline, dict):
        return (False, ["receipt.baseline is missing or not a dict"], normalized)

    # Foreign-key guard: task_shas/files_per_task may only name real plan tasks.
    # Otherwise a fake files_per_task entry could launder an unplanned changed
    # file past Rule 4 (the entry makes the file "declared" while the sha/binding
    # loop — which iterates plan_tasks only — never checks it).
    plan_ids = {task.id for task in plan_tasks}
    foreign = sorted((set(task_shas) | set(files_per_task)) - plan_ids)
    if foreign:
        errors.append(f"receipt names keys outside the plan task set: {foreign}")

    # Rule 3: baseline non-regression.
    before = baseline.get("before") if isinstance(baseline.get("before"), dict) else {}
    after = baseline.get("after") if isinstance(baseline.get("after"), dict) else {}
    try:
        b_failed = int(before.get("failed"))
        a_failed = int(after.get("failed"))
        a_exit = int(after.get("exit_code"))
    except (TypeError, ValueError):
        errors.append("baseline before.failed / after.failed / after.exit_code missing or non-int")
    else:
        if a_failed > b_failed:
            errors.append(
                f"baseline regression: after.failed={a_failed} > before.failed={b_failed}"
            )
        if a_exit != 0:
            errors.append(f"baseline.after.exit_code={a_exit} (expected 0)")

    # Rule 1 + 2: existence, lineage, distinctness, and sha↔task file binding.
    seen_shas: dict[str, str] = {}
    for task in plan_tasks:
        tid = task.id
        sha = task_shas.get(tid)
        if not isinstance(sha, str) or not sha:
            errors.append(f"task {tid} has no sha in task_shas")
            continue
        normalized["task_shas"][tid] = sha
        if not _commit_exists(repo_root, sha):
            errors.append(f"task {tid} sha {sha} does not exist on the branch")
            continue
        if _same_commit(repo_root, sha, branch_base_sha):
            errors.append(f"task {tid} sha equals branch_base_sha (no commit for the task)")
            continue
        if not _sha_is_descendant(repo_root, branch_base_sha, sha):
            errors.append(f"task {tid} sha {sha} is not a descendant of the base")
            continue
        if sha in seen_shas:
            errors.append(
                f"task {tid} sha {sha} duplicates task {seen_shas[sha]} — task commits must be distinct"
            )
        else:
            seen_shas[sha] = tid
        # Rule 2: bind the sha to its claimed files (the trust boundary).
        claimed = files_per_task.get(tid)
        if not isinstance(claimed, list):
            errors.append(f"task {tid} has no files_per_task entry")
            continue
        commit_files = _commit_files(repo_root, sha)
        if commit_files is None:
            errors.append(f"task {tid}: cannot read the changed-file set for commit {sha}")
            continue
        claimed_set = {c for c in claimed if isinstance(c, str)}
        if claimed_set != commit_files:
            errors.append(
                f"task {tid}: files_per_task {sorted(claimed_set)} != commit {sha} "
                f"changed files {sorted(commit_files)}"
            )

    # Rule 4: staging discipline is set EQUALITY, not subset (both directions).
    diff_files = _diff_files(repo_root, branch_base_sha)
    if diff_files is None:
        errors.append("cannot compute git diff base..HEAD")
    else:
        union: set[str] = set()
        for tid in plan_ids:  # plan tasks only — foreign keys already errored above
            v = files_per_task.get(tid)
            if isinstance(v, list):
                union |= {x for x in v if isinstance(x, str)}
        undeclared = diff_files - union
        overclaimed = union - diff_files
        if undeclared:
            errors.append(f"branch changed files claimed by no task: {sorted(undeclared)}")
        if overclaimed:
            errors.append(f"task-claimed files not in the branch diff: {sorted(overclaimed)}")

    return (len(errors) == 0, errors, normalized)


# --- Branch-protection probe classification (#139) ---


_PROTECTION_KEYS = frozenset({
    "required_status_checks", "required_pull_request_reviews", "enforce_admins",
    "restrictions", "url", "required_signatures", "required_linear_history",
    "allow_force_pushes", "allow_deletions", "required_conversation_resolution",
    "lock_branch", "block_creations",
})


def classify_branch_protection(status_code: int, body) -> tuple[str, dict]:
    """Classify a `gh api .../branches/<b>/protection` result (#139).

    Returns (state, details) where state is 'protected' | 'unprotected' |
    'unknown'. The probe is ADVISORY and fail-open, but it must NOT misread an
    ambiguous result as a definitive one (that would overstate OR understate
    protection):
    - 404 is 'unprotected' ONLY when the body is the GitHub "Branch not
      protected" shape; any other 404 (wrong repo/branch, inaccessible) is
      'unknown', so an absent probe never reads as a confirmed no-protection.
    - 200 is 'protected' ONLY when the body is a recognizable protection object;
      a 200 with a non-dict, unrecognized, or malformed `required_status_checks`
      body is 'unknown' (a corrupt parse must not silently pass as valid data,
      or the contradiction check would be skipped).
    - 403/401/other -> 'unknown'.
    `details` always carries `required_checks: list[str]`.
    """
    if status_code == 200:
        if not isinstance(body, dict) or not (_PROTECTION_KEYS & set(body)):
            return ("unknown", {"required_checks": []})
        checks: list[str] = []
        if "required_status_checks" in body:
            rsc = body["required_status_checks"]
            if not isinstance(rsc, dict):
                return ("unknown", {"required_checks": []})
            if isinstance(rsc.get("checks"), list):
                checks = [c.get("context") for c in rsc["checks"]
                          if isinstance(c, dict) and isinstance(c.get("context"), str)]
            elif isinstance(rsc.get("contexts"), list):
                checks = [c for c in rsc["contexts"] if isinstance(c, str)]
            elif "checks" in rsc or "contexts" in rsc:
                return ("unknown", {"required_checks": []})  # present but wrong type
        reviews = body.get("required_pull_request_reviews")
        return ("protected", {
            "required_checks": checks,
            "required_reviews": isinstance(reviews, dict),
        })
    if status_code == 404 and isinstance(body, dict) \
            and "not protected" in str(body.get("message", "")).lower():
        return ("unprotected", {"required_checks": []})
    # 403/401 (forbidden/not visible), a non-"not protected" 404, or any other
    # status -> fail-open unknown.
    return ("unknown", {"required_checks": []})


def branch_protection_line(state: str, details: dict) -> str:
    """One-line summary for session notes + the Step 12 PR body (#139).

    States plainly which layer enforces the shipping gates so a passed PR does
    not overstate its server-side protection.
    """
    checks = (details or {}).get("required_checks") or []
    if state == "protected":
        parts = []
        if checks:
            parts.append("required checks: " + ", ".join(checks))
        if (details or {}).get("required_reviews"):
            parts.append("reviews required")
        detail = f" ({'; '.join(parts)})" if parts else ""
        return f"Branch protection: enabled{detail} — GitHub enforces these server-side."
    if state == "unprotected":
        return ("Branch protection: none — review/CI gates enforced by WF2 only, "
                "not by GitHub (a direct push or unreviewed merge is not blocked server-side).")
    return ("Branch protection: unknown (protection API not visible — e.g. token "
            "lacks scope) — treat as WF2-only enforcement.")


def quarantine_protection_contradiction(
    ci_quarantined: bool,
    protection_state: str,
    required_checks: list,
) -> str | None:
    """Flag the quarantined-CI × required-status-check contradiction (#139).

    If CI is quarantined (WF2 won't gate on it) but branch protection REQUIRES
    status checks, a merge attempt will hit a server-side wall. Surface it before
    Step 14 rather than merging into the wall. Returns a message or None.
    """
    if ci_quarantined and protection_state == "protected" and required_checks:
        return (
            "CONTRADICTION: CI is quarantined (WF2 treats it as non-gating) but "
            f"branch protection requires status checks {list(required_checks)} — "
            "a merge will be blocked server-side. Lift the quarantine, fix CI, or "
            "adjust protection before attempting Step 14.")
    return None


# --- Per-branch review-state pointer (.rawgentic/review-state/, git-excluded) ---
#
# The pointer lives at <repo_root>/.rawgentic/review-state/<branch>.json but is
# LOCAL, git-excluded bookkeeping — it MUST NOT be committed into a feature PR
# (#231 AC2). An app repo that doesn't track `.rawgentic/` would otherwise get
# rawgentic bookkeeping in its PR. write_review_state auto-appends `.rawgentic/`
# to the repo's `.git/info/exclude` (local, per-clone, shared across worktrees via
# the common git dir — never committed), and the WF2 prose never stages it. A
# single WF2 run reads/writes it in one checkout, so losing git cross-worktree
# visibility (vs the old committed pointer) has no practical effect.

_REVIEW_STATE_DIR = ".rawgentic/review-state"
_VALID_STATUSES = ("applied", "suspended", "dispatch_failed")


def _sanitize_branch(branch: str) -> str:
    """Convert a git branch name to a path-safe filename component.

    Replaces /, \\, :, ?, *, <, >, |, " with -.  Preserves dashes, dots, and
    alphanumerics. Mirrors the documented convention in
    .rawgentic/review-state/README.md.
    """
    return re.sub(r"[/\\:?*<>|\"]", "-", branch)


def _ensure_rawgentic_git_excluded(repo_root: str) -> bool:
    """Append `.rawgentic/` to the repo's LOCAL git exclude (`.git/info/exclude`)
    so the review-state pointer (and any other `.rawgentic/` bookkeeping) can never
    be accidentally staged into an app repo's feature PR (#231 AC2).

    Local-only (never committed, unlike a tracked `.gitignore`) and shared across
    linked worktrees via the common git dir — exactly the manual `.git/info/exclude`
    workaround, made automatic. Best-effort: returns True if the pattern is present
    or was added, False on a no-op (not a git repo) or any failure — it must never
    break a review-state write.
    """
    pattern = ".rawgentic/"
    try:
        cp = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "--git-path", "info/exclude"],
            capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        return False
    if cp.returncode != 0:
        return False
    exclude_path = (cp.stdout or "").strip()
    if not exclude_path:
        return False
    if not os.path.isabs(exclude_path):
        exclude_path = os.path.join(repo_root, exclude_path)
    try:
        existing = ""
        if os.path.exists(exclude_path):
            with open(exclude_path, "r", encoding="utf-8", errors="replace") as fh:
                existing = fh.read()
        present = {ln.strip() for ln in existing.splitlines()}
        if pattern in present or ".rawgentic" in present:
            return True
        os.makedirs(os.path.dirname(exclude_path), exist_ok=True)
        with open(exclude_path, "a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write(pattern + "\n")
        return True
    except OSError:
        return False


def review_state_path(repo_root: str, branch: str) -> str:
    """Return the path to the per-branch review-state JSON file."""
    return os.path.join(repo_root, _REVIEW_STATE_DIR, _sanitize_branch(branch) + ".json")


def read_review_state(repo_root: str, branch: str) -> dict | None:
    """Read the per-branch review-state pointer. Returns None if missing.

    Performs a branch-match safety check: if the file's `branch` field does
    NOT equal the requested branch (file from a different branch with the
    same sanitized name, or someone edited the file by hand), returns None
    and logs a stderr warning. Callers should treat None as "no trusted
    state" and behave conservatively (e.g., Step 12 refusing to ship).
    """
    path = review_state_path(repo_root, branch)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = _json.load(f)
    except (_json.JSONDecodeError, OSError) as exc:
        print(
            f"plan_lib: review-state at {path} unreadable: {exc}",
            file=sys.stderr,
        )
        return None
    if state.get("branch") != branch:
        print(
            f"plan_lib: review-state at {path} has branch={state.get('branch')!r} "
            f"but requested {branch!r}; ignoring file",
            file=sys.stderr,
        )
        return None
    return state


def write_review_state(
    repo_root: str,
    branch: str,
    last_review_log_status: str,
) -> str:
    """Write the per-branch review-state pointer atomically. Returns the path.

    The pointer is LOCAL, git-excluded bookkeeping (#231 AC2) — this call
    best-effort-appends `.rawgentic/` to the repo's `.git/info/exclude` so it can
    never be staged into the feature PR. The WF2 prose never commits it.

    Raises ValueError on invalid status.
    """
    if last_review_log_status not in _VALID_STATUSES:
        raise ValueError(
            f"invalid last_review_log_status {last_review_log_status!r}; "
            f"must be one of {_VALID_STATUSES}"
        )
    _ensure_rawgentic_git_excluded(repo_root)  # keep the pointer out of the PR
    path = review_state_path(repo_root, branch)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "schema_version": 1,
        "branch": branch,
        "last_review_log_status": last_review_log_status,
        "ts": _now_iso(),
    }
    # Atomic write via the shared helper (#264) — gains a randomized temp name
    # and unlink-on-exception over the old fixed-name variant.
    atomic_write_text(path, _json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


@contextmanager
def _file_lock(path: str):
    """Context manager: hold an exclusive flock on `path` (created if absent).

    WF2 is single-writer-per-branch by design (one orchestrator session
    drives a feature branch at a time), so this lock is a defense-in-depth
    measure against accidental concurrent invocations rather than a
    primary correctness mechanism.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # Open in append mode so the file is created if missing without
    # truncating existing content; the lock is on the file descriptor.
    fd = os.open(path + ".lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# --- Goal-guard text assembly (#156) ---

_GOAL_ESCAPE_DISJUNCT: Final[str] = (
    " — or a blocker is posted to the issue via the ERROR protocol"
)
# Campaign (epic-level) escape clause (#192): tolerant of the real outcomes a
# multi-issue campaign actually produces, so the ONE campaign goal clears honestly
# instead of firing relentlessly against a stale condition (as it did this campaign).
_GOAL_CAMPAIGN_ESCAPE: Final[str] = (
    " — a child closed not-planned per its own acceptance criteria counts as "
    "satisfied, and the owner may pause the campaign at any time"
)
_GOAL_CAP: Final[int] = 4000
_AC_STRIP_RE = re.compile(r"^\s*(?:\d+[.):]\s*|[-*•]\s*)")


def _strip_ac_numbering(line: str) -> str:
    """Strip leading list numbering/bullets from one AC line.

    Handles "1. Foo", "2) Bar", "- Baz" (and "*"/"•" bullets). Anything not
    matching that shape is returned trimmed, unchanged otherwise.
    """
    return _AC_STRIP_RE.sub("", line).strip()


def build_goal_text(
    issue_number: int,
    ac_lines: list[str],
    variant: str = "wf2",
    headless: bool = False,
    child_issues: list[int] | None = None,
) -> str:
    """Build the goal-guard clear-condition text posted at workflow start.

    The ESCAPE DISJUNCT (AC2) is always appended so the goal can clear even
    when the happy path is blocked: the workflow is done either when the
    primary condition is met, or when a blocker has been posted to the
    issue via the ERROR protocol.

    `headless` currently produces identical wording in both modes — both
    wf2 and wf3 always say "PR open with green CI", never "merged" — because
    the goal must clear at workflow *termination*; merge is owner-gated and
    happens post-terminal. The param is kept so wording could diverge later
    (e.g. a headless run that also confirms merge) without changing the
    call signature.

    ac_lines are compressed by stripping leading numbering/bullets and
    joining with "; ". If the resulting wf2 text would exceed 4000 chars
    (or ac_lines is empty/all-blank), the AC list is replaced with the
    fixed phrase "all numbered acceptance criteria of issue #<N> as
    written" instead — that fallback is guaranteed to stay under the cap.

    wf3 (bug-fix/repro variant) has no numbered ACs, so ac_lines is
    ignored entirely.

    Pure function: deterministic, no I/O. Raises ValueError for any
    variant other than "wf2"/"wf3" — this text is orchestrator-authored
    input, so a typo'd variant should fail loudly rather than silently
    fall back to one of the two templates.
    """
    if variant not in ("wf2", "wf3", "campaign"):
        raise ValueError(f"unknown goal-text variant: {variant!r}")

    if variant == "campaign":
        # ONE goal over the epic's ordered child issues (#192). `child_issues`
        # is campaign-only; `ac_lines` is ignored. The tolerant campaign escape
        # clause replaces the per-issue ERROR-protocol disjunct.
        if child_issues:
            nums = ", ".join(f"#{n}" for n in child_issues)
            full = (
                f"Epic #{issue_number} campaign done: all ordered child issues "
                f"({nums}) merged with green CI{_GOAL_CAMPAIGN_ESCAPE}"
            )
            if len(full) <= _GOAL_CAP:
                return full
        # empty/None children, or a child list that would overflow the cap.
        return (
            f"Epic #{issue_number} campaign done: all ordered child issues of "
            f"epic #{issue_number} merged with green CI{_GOAL_CAMPAIGN_ESCAPE}"
        )

    if variant == "wf3":
        return (
            f"Bug #{issue_number} fixed: repro documented, regression test "
            f"red→green, PR open with green CI{_GOAL_ESCAPE_DISJUNCT}"
        )

    compressed = "; ".join(
        stripped
        for line in ac_lines
        if (stripped := _strip_ac_numbering(line))
    )
    if compressed:
        full = (
            f"Issue #{issue_number} done: ACs met ({compressed}), "
            f"PR open with green CI, run-record persisted"
            f"{_GOAL_ESCAPE_DISJUNCT}"
        )
        if len(full) <= _GOAL_CAP:
            return full

    fallback_acs = f"all numbered acceptance criteria of issue #{issue_number} as written"
    return (
        f"Issue #{issue_number} done: ACs met ({fallback_acs}), "
        f"PR open with green CI, run-record persisted{_GOAL_ESCAPE_DISJUNCT}"
    )


def consume_loopback(path: str, source: str) -> tuple[bool, dict]:
    """Attempt to consume one loop-back from the named source.

    Atomically (under a file lock): reads the current per-source counters,
    checks per-source and global caps, and writes back the incremented
    state. Two concurrent invocations cannot both see the same pre-state
    and over-spend the budget.

    Returns (ok, state). Fails if:
    - Source exceeds its per-source cap
    - Global total would exceed GLOBAL_LOOPBACK_BUDGET

    Raises ValueError on unknown source.
    """
    if source not in _LOOPBACK_SOURCES:
        raise ValueError(f"unknown loopback source: {source!r}")
    with _file_lock(path):
        state = _read_loopback_state(path)
        if state[source] >= _LOOPBACK_SOURCE_MAX[source]:
            return False, state
        if state["total"] >= GLOBAL_LOOPBACK_BUDGET:
            return False, state
        state[source] += 1
        state["total"] = sum(state[s] for s in _LOOPBACK_SOURCES)
        _write_loopback_state(path, state)
    return True, state
