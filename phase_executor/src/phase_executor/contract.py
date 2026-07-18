"""The Observation contract — one producer of the normative JSON-Schema documents.

`contract.py` is ONE producer implementation; a Rust producer (kukakuka) emits the same
`observation.schema.json` documents. The schema is the normative artifact, not this module.

Design rules (plan 2026-07-16-per-phase-model-routing §3.1 + #424 design §9c):
- `actual_model` is the provider-reported id from the INNERMOST envelope. It is mandatory
  evidence when `parse_status == "ok"` and MAY be null only on a non-success status (so a
  pre-envelope timeout is still recordable). The schema enforces this conditional; we never
  fabricate an id or substitute the requested model.
- `canonicalize_model_id` normalizes ids for the requested==actual comparison (aliases,
  provider prefixes, context-window `[..]` tags, trailing dates) WITHOUT rewriting the raw
  evidence stored in `actual_model`.
"""
from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Optional

SCHEMA_VERSION = "1"

# The seat roles the engine actually has ``check_pre`` evaluators for. The loader semantic pass
# (``routing._assert_referential_integrity``) rejects any ``policy.enforced_roles`` entry outside
# this set: a table must not declare a role "enforced" that nothing evaluates
# (appears-enforced-but-isn't). So ``enforced_roles`` is a SUBSET of ENFORCEABLE_ROLES — projects
# may narrow, never widen, until the engine ships a new evaluator. Defined HERE (the shared leaf),
# not in enforce.py, because both routing.py and enforce.py need it and ``enforce`` imports
# ``routing`` — so ``routing`` must not import ``enforce`` (cycle). enforce.py re-exports it so the
# public API reads ``enforce.ENFORCEABLE_ROLES``.
ENFORCEABLE_ROLES: Final[frozenset[str]] = frozenset({"review", "build"})

# parse_status vocabulary (mirrors observation.schema.json enum).
OK = "ok"
NONZERO_EXIT = "nonzero_exit"
TIMEOUT = "timeout"
LAUNCH_ERROR = "launch_error"
PARSE_ERROR = "parse_error"
NO_RESPONSE = "no_response"          # transport produced nothing / parsed envelope carried no output
IDENTITY_FAILURE = "identity_failure"
USAGE_UNAVAILABLE = "usage_unavailable"
HARNESS_ERROR = "harness_error"
PARSE_STATUSES = frozenset(
    {OK, NONZERO_EXIT, TIMEOUT, LAUNCH_ERROR, PARSE_ERROR, NO_RESPONSE, IDENTITY_FAILURE, USAGE_UNAVAILABLE, HARNESS_ERROR}
)

# Statuses where the transport failed to deliver a usable envelope: a chain fallback is warranted
# and the run-end audit treats them as legitimate (non-breach) attempts. Every OTHER non-ok status
# (identity_failure, parse_error, usage_unavailable, harness_error) means an envelope WAS produced
# but is not a clean success — routing enforcement treats an absent/mismatched identity there as a
# breach (verified, not trusted). Single-sourced here so engine and enforce agree.
AVAILABILITY_FAILURES = frozenset({NONZERO_EXIT, TIMEOUT, LAUNCH_ERROR, NO_RESPONSE})

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"

_PROVIDER_PREFIXES = (
    "us.anthropic.", "eu.anthropic.", "apac.anthropic.", "anthropic.", "anthropic/",
    "openai/", "openai.", "zhipuai/", "zhipu/",
)
_BRACKET_RE = re.compile(r"\[[^\]]*\]")          # context-window / variant tag, e.g. [1m]
_TRAILING_DATE_RE = re.compile(r"-\d{8}$")        # dated revision, e.g. -20251001


@functools.lru_cache(maxsize=None)
def _load_schema(name: str) -> dict:
    return json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))


def observation_schema() -> dict:
    return _load_schema("observation.schema.json")


def routing_table_schema() -> dict:
    return _load_schema("routing-table.schema.json")


def canonicalize_model_id(model_id: Optional[str]) -> str:
    """Normalize a model id for requested==actual comparison.

    Strips a known provider prefix, a bracketed variant tag (``[1m]``), and a trailing
    ``-YYYYMMDD`` date; lowercases. Does NOT collapse distinct families/versions
    (``claude-opus-4-8`` != ``claude-sonnet-5``). Returns "" for a falsy/non-string input
    so an absent id never spuriously matches another absent id at the call site.
    """
    if not model_id or not isinstance(model_id, str):
        return ""
    s = model_id.strip().lower()
    for prefix in _PROVIDER_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = _BRACKET_RE.sub("", s)
    s = _TRAILING_DATE_RE.sub("", s)
    return s.strip("-. ")


def models_match(requested: Optional[str], actual: Optional[str]) -> bool:
    """True iff requested and actual canonicalize to the same non-empty id."""
    rc = canonicalize_model_id(requested)
    ac = canonicalize_model_id(actual)
    return bool(rc) and rc == ac


@dataclass(frozen=True)
class Observation:
    """A single model-seat invocation record. Serialize with ``to_dict``; the dict conforms
    to observation.schema.json (validate with ``validate_observation``)."""

    run_id: str
    attempt_id: str
    seat: str
    engine: str
    transport: str
    requested_model: str
    actual_model: Optional[str]
    prompt_hash: str
    usage: Optional[dict]
    timing_ms: int
    queued_ms: int
    process: dict
    parse_status: str
    parsed_payload: Any
    raw_capture_path: Optional[str]
    fallback_reason: Optional[str]
    routing_config_digest: str
    context_hashes: list = field(default_factory=list)
    correlation_id: Optional[str] = None
    judge_degraded: Optional[bool] = None
    dispatched_lane: Optional[dict] = None
    # #465 AC3: the effort resolution actually used for this dispatch — an object
    # {requested, native, resolution, capability_revision}; optional-additive (absent on
    # legacy records; consumers tolerate absence — the dispatched_lane precedent).
    effort: Optional[dict] = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        out = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "correlation_id": self.correlation_id,
            "seat": self.seat,
            "engine": self.engine,
            "transport": self.transport,
            "requested_model": self.requested_model,
            "actual_model": self.actual_model,
            "prompt_hash": self.prompt_hash,
            "context_hashes": list(self.context_hashes),
            "usage": self.usage,
            "timing_ms": self.timing_ms,
            "queued_ms": self.queued_ms,
            "process": dict(self.process),
            "parse_status": self.parse_status,
            "parsed_payload": self.parsed_payload,
            "raw_capture_path": self.raw_capture_path,
            "fallback_reason": self.fallback_reason,
            "routing_config_digest": self.routing_config_digest,
        }
        # judge_degraded is a bool-only optional (no null in schema): emit only when set.
        if self.judge_degraded is not None:
            out["judge_degraded"] = self.judge_degraded
        # dispatched_lane (#425 B): the lane the executor actually dispatched on, stamped by the
        # engine at dispatch — enables independent receipt<->observation binding at run-end audit.
        # Optional/additive (kukakuka v1 parity): emitted only when set.
        if self.effort is not None:
            out["effort"] = dict(self.effort)
        if self.dispatched_lane is not None:
            out["dispatched_lane"] = dict(self.dispatched_lane)
        return out



@dataclass(frozen=True)
class EffortResolution:
    """One effort resolution (#465): dataclass fields == the serialized JSON keys."""
    requested: Optional[str]
    native: Optional[str]
    resolution: str            # "identity" | "stepdown" | "adapter_default"
    capability_revision: int

    def to_dict(self) -> dict:
        return {"requested": self.requested, "native": self.native,
                "resolution": self.resolution, "capability_revision": self.capability_revision}


_UNSAFE_WORKTREE_CHARS = ('"', "'", "\\", "]", "[", ",", "\n", "\r", "\x00")


def canonical_contained_worktree(worktree, containment_root) -> str:
    """#465: THE shared mutating-worktree boundary check (both adapters call it — claude
    has no OS sandbox so cwd is its ONLY containment, codex adds Landlock). Fail-closed:
    the worktree must be an absolute path, carry no character that could break a config
    array, canonicalize to a path strictly UNDER the executor-approved containment_root,
    and never equal the root itself. Returns the canonical worktree or raises."""
    import os as _os  # noqa: PLC0415
    if not isinstance(worktree, str) or not worktree:
        raise CompositionError("mutating launch: worktree must be a non-empty path string")
    if not containment_root:
        raise CompositionError(
            "mutating launch: containment_root is required (executor-approved root; a "
            "mutating launch without it is the /tmp-wide hazard)")
    if any(c in worktree for c in _UNSAFE_WORKTREE_CHARS):  # RAW string, before realpath (NUL would crash realpath)
        raise CompositionError(
            f"mutating launch: worktree path {worktree!r} carries an unsafe character — refused")
    if not _os.path.isabs(worktree):
        raise CompositionError(f"mutating launch: worktree must be absolute (got {worktree!r})")
    canon_wt = _os.path.realpath(worktree)
    canon_root = _os.path.realpath(containment_root)
    if canon_root == _os.sep:
        raise CompositionError(
            "mutating launch: containment_root cannot be the filesystem root '/' — name a "
            "real approved directory (a '/' root would refuse every worktree)")
    if any(c in canon_wt for c in _UNSAFE_WORKTREE_CHARS):
        raise CompositionError(
            f"mutating launch: canonical worktree {canon_wt!r} carries an unsafe character — refused")
    if canon_wt == canon_root or not canon_wt.startswith(canon_root + _os.sep):
        raise CompositionError(
            f"mutating launch: worktree {worktree!r} fails containment under "
            f"{containment_root!r} (canonicalized {canon_wt!r} vs root {canon_root!r})")
    return canon_wt


class CompositionError(RuntimeError):
    """A launch composition/profile that must not spawn (#465 — fail-closed at compose time)."""


@dataclass(frozen=True)
class LaunchProfile:
    """One seat launch profile (#465 B.2). The dataclass default is fresh/read-only — the
    byte-identical compat path for every pre-profile caller. `effective_grants` is
    init=False: populated ONLY by `profile_from_manifest` (the bash=>net closure lives
    there); a hand-built inconsistent profile is caught by the adapters' pre-spawn assert
    (mutating == edit-or-bash in effective_grants)."""
    session_policy: str = "fresh"
    mutating: bool = False
    worktree: Optional[str] = None
    tool_grants: tuple = ()
    max_budget_usd: Optional[float] = None
    effective_grants: tuple = field(default=(), init=False)


_SESSION_POLICIES = frozenset({"fresh", "resume"})
_MUTATING_ENGINES = frozenset({"claude", "codex"})


def profile_from_manifest(manifest: dict, *, engine: str, worktree: Optional[str] = None) -> "LaunchProfile":
    """THE launch-profile derivation (#465 B.2). Fail-closed on every invariant:
    session_policy REQUIRED in {fresh, resume} (#464 made it non-defaultable); mutating
    (edit/bash granted) requires a worktree; mutating claude requires a positive
    max_budget_usd (the enforceable cost bound — codex's bound is the enforced timeout);
    mutating is restricted to {claude, codex} (a zhipuai manifest with edit/bash refuses).
    The bash=>net closure is recorded in effective_grants (grants are capability
    SELECTION, never a sandbox). SECURITY-LAYER ASYMMETRY (Step-11 R2-F1, W7 BLOCKER):
    the codex mutating path is OS-confined (Landlock workspace-write pinned to the
    worktree); the CLAUDE mutating path has NO OS sandbox here — only a cwd pin +
    path-containment refusal + a --max-budget-usd cap, so an absolute-path Edit/Write/Bash
    is NOT filesystem-confined. W7 MUST NOT wire a mutating-CLAUDE dispatch until claude
    gains a real FS sandbox (bwrap/landlock around run_subprocess) — a binding cross-child
    constraint, fail-closed today because W2 dispatches no mutating profile. TRUST BOUNDARY
    (8a-B): the manifest carries no provider field — `engine` is CALLER-supplied and, on
    the wired path, comes from `_engine_for(lane)` (config-controlled, engine.py:52); the
    derivation trusts that caller, a manifest cannot self-attest its provider."""
    if engine not in _EFFORT_ENGINES:
        raise ValueError(f"profile_from_manifest: unknown engine {engine!r} (valid: {sorted(_EFFORT_ENGINES)})")
    policy = manifest.get("session_policy")
    if policy not in _SESSION_POLICIES:
        raise ValueError(
            f"profile_from_manifest: session_policy must be one of {sorted(_SESSION_POLICIES)} "
            f"(got {policy!r}; #464 made it explicit, never defaulted)")
    grants = tuple(manifest.get("tool_grants") or ())
    # #465 Step-11 DF-2: grants are capability SELECTION — an empty or unknown grant set must
    # never silently produce an unrestricted command (claude with no --allowedTools gets ALL
    # tools). The schema already pins tool_grants to a non-empty enum array; this is the
    # fail-closed backstop for a hand-built/malformed manifest reaching the derivation.
    _valid = {"read", "edit", "bash", "net"}
    if not grants:
        raise ValueError("profile_from_manifest: tool_grants must be a non-empty subset of "
                         f"{sorted(_valid)} (a seat manifest always grants at least 'read')")
    unknown = set(grants) - _valid
    if unknown:
        raise ValueError(f"profile_from_manifest: unknown tool_grants {sorted(unknown)} "
                         f"(valid: {sorted(_valid)})")
    effective = tuple(dict.fromkeys(grants + (("net",) if "bash" in grants and "net" not in grants else ())))
    mutating = "edit" in grants or "bash" in grants
    bounds = manifest.get("bounds") or {}
    budget = bounds.get("max_budget_usd")
    if mutating:
        if engine not in _MUTATING_ENGINES:
            raise ValueError(
                f"profile_from_manifest: engine {engine!r} has no mutating profile "
                f"(mutating is restricted to {sorted(_MUTATING_ENGINES)}; zhipuai stays one-shot)")
        if not worktree:
            raise ValueError("profile_from_manifest: a mutating profile REQUIRES a worktree")
        import math  # noqa: PLC0415
        if engine == "claude" and not (isinstance(budget, (int, float)) and not isinstance(budget, bool)
                                       and math.isfinite(budget) and budget > 0):
            raise ValueError(
                "profile_from_manifest: a mutating CLAUDE profile REQUIRES a positive "
                "bounds.max_budget_usd (the enforceable cost bound)")
    profile = LaunchProfile(session_policy=policy, mutating=mutating, worktree=worktree,
                            tool_grants=grants,
                            max_budget_usd=float(budget) if isinstance(budget, (int, float)) and not isinstance(budget, bool) else None)
    object.__setattr__(profile, "effective_grants", effective)
    return profile


_EFFORT_ENGINES = frozenset({"claude", "codex", "zhipuai"})


def resolve_effort(model: str, requested: Optional[str], *, engine: str) -> "EffortResolution":
    """Per-model effort gate + nearest-lower stepdown (#465 B.1, spike #456 rule).

    Fail-closed: an unknown engine, an unknown effort name, or an UNKNOWN MODEL with a
    REQUESTED effort refuses (ValueError — dispatch-configuration error; never guess a
    capability, never let a silent clamp happen). None-effort applies the per-ENGINE
    policy from model_capabilities.ENGINE_NONE_EFFORT (codex -> "high", recorded as
    adapter_default); other engines pass None through (provider default, identity).
    """
    from . import model_capabilities as mc  # local: keeps contract import-light
    if engine not in _EFFORT_ENGINES:
        raise ValueError(f"resolve_effort: unknown engine {engine!r} (valid: {sorted(_EFFORT_ENGINES)})")
    rev = mc.CAPABILITY_REVISION
    if requested is None:
        none_native = mc.ENGINE_NONE_EFFORT.get(engine)
        if none_native is not None:
            return EffortResolution(None, none_native, "adapter_default", rev)
        return EffortResolution(None, None, "identity", rev)
    if requested not in mc.EFFORT_LADDER:
        raise ValueError(f"resolve_effort: unknown effort {requested!r} (ladder: {mc.EFFORT_LADDER})")
    supported = mc.SUPPORTED_EFFORT.get(canonicalize_model_id(model))
    if supported is None:
        raise ValueError(
            f"resolve_effort: model {model!r} has no capability row in model_capabilities."
            f"SUPPORTED_EFFORT — cannot gate a requested effort (fix the registry)")
    if requested in supported:
        return EffortResolution(requested, requested, "identity", rev)
    idx = mc.EFFORT_LADDER.index(requested)
    for lvl in reversed(mc.EFFORT_LADDER[:idx]):
        if lvl in supported:
            return EffortResolution(requested, lvl, "stepdown", rev)
    raise ValueError(
        f"resolve_effort: model {model!r} supports no level at or below {requested!r} "
        f"(supported: {supported}) — cannot step down")


def validate_observation(obs: dict) -> None:
    """Raise jsonschema.ValidationError if ``obs`` does not conform. Fail-loud."""
    import jsonschema  # noqa: PLC0415 (deferred: keep import cost off the hot path / off consumers that only build)

    jsonschema.validate(obs, observation_schema())


def validate_routing_table(table: dict) -> None:
    """Raise jsonschema.ValidationError if ``table`` does not conform. Fail-loud."""
    import jsonschema  # noqa: PLC0415

    jsonschema.validate(table, routing_table_schema())
