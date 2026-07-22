"""Fail-closed guardrail canary (#468, W5 of epic #475) — the load-bearing hook-layer
control on a ``defaultMode:auto`` host (spike #454 CRITICAL: an un-granted mutating tool is
auto-approved and runs with NO gate except the hook layer, so this canary proves the hook
layer actually fired before a mutating spawn).

Two-function split (the peer's key correction — closes the fail-open risk):
- ``evaluate_canary(policy_id, evidence)`` is PURE and NEVER raises; it accumulates every
  check's violation in deterministic policy order and returns a ``CanaryResult``. It exists for
  hermetic unit tests and diagnostics.
- ``require_canary(composition, evidence)`` is the PRODUCTION API; it derives the policy FROM
  the immutable final ``composition`` (never a caller-selected policy — adversarial H2), binds
  the evidence to that composition (nonce + snapshot digest — replay rejection), and RAISES
  ``CanaryRefused`` on any non-pass so a caller cannot silently ignore a refusal.

Fail-closed EVERYWHERE: a check that cannot evaluate its input REFUSES with a stable tag, never
a silent pass; an internal exception becomes ``canary_check_error:<id>``. Omission of evidence
never means success.

#468 ships the evaluator + the fail-closed checks (5 for the claude_mutating policy +
``codex_containment`` for codex_mutating) + the ``canary_result`` Observation field;
#470 (W7) wires ``require_canary``/``build_observation`` into the production dispatch choke-point
(stage-and-bind an immutable snapshot, populate the trusted evidence, call ``require_canary``
exactly once immediately before spawn).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import contract

# --- Pinned constants (re-pinned per release, drift-guarded by test_canary_digest_pin.py) ---
POLICY_REVISION = 1
EXPECTED_PLUGIN_VERSION = "3.88.0"
# Computed live over hooks/hooks.json + the scripts referenced in its command fields (the
# canonical length-framed encoding below). test_canary_digest_pin.py asserts pin == live.
EXPECTED_REGISTRATION_DIGEST = "sha256:7ec8cbff3426af999406db12dec82d327271db352a848721c650fc65451747ce"

# The mutating tool/matcher classes to positive-deny-probe are DERIVED from hooks.json's
# PreToolUse matchers (never invented) — each matcher whose command is an ENFORCING guard. The
# guard -> unique hook-origin deny marker map is the single source: a class's probe result must
# carry its guard's unique string, so an OS denial cannot masquerade as a hook success.
_GUARD_DENY_MARKERS = {
    "wal-guard": "BLOCKED:",                 # hooks/wal-guard deny() reason (spike #454; unique to wal-guard)
    "security-guard.py": "SECURITY BLOCK:",  # hooks/security_guard_lib.py:format_deny (unique to security-guard)
}
# PreToolUse guards that are NOT deny-enforcers for the un-granted-mutating-tool threat (spike
# #454): wal-bind-guard (cross-project bind) + wal-pre (WAL staging log). Coverage is the CURATED
# _GUARD_DENY_MARKERS set — NOT every PreToolUse matcher. `test_pretooluse_guard_set_is_classified`
# fails if hooks.json gains a PreToolUse guard absent from BOTH sets, forcing a conscious
# classify (add an enforcer to _GUARD_DENY_MARKERS, or here if genuinely non-enforcing) so a new
# enforcing guard can never be silently left un-probed.
_KNOWN_NONENFORCING_PRETOOL_GUARDS = frozenset({"wal-bind-guard", "wal-pre"})

# Explicit policy matrix keyed by provider/profile. An unknown provider/policy/profile refuses
# (canary_policy_unknown); every required check returns pass or refuse (inapplicability of a
# REQUIRED check is a refusal, never a pass).
POLICIES = {
    "claude_mutating": ["hooks_digest", "plugin_version", "lane_provisioned", "positive_deny", "bare_absent"],
    "codex_mutating": ["codex_containment", "codex_behavioral", "bare_absent"],
}

_PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"
_RAWGENTIC_PLUGIN_IDS = frozenset({"rawgentic@rawgentic", "rawgentic"})

PASS = "pass"
REFUSE = "refuse"
NOT_APPLICABLE = "not_applicable"


# --------------------------------------------------------------------------- data model
@dataclass(frozen=True)
class CheckResult:
    """One check outcome. ``verdict`` is pass|refuse|not_applicable; ``not_applicable`` only
    marks a check a policy does NOT require (such checks are absent from ``CanaryResult.checks``),
    so a required check is always pass or refuse."""
    check_id: str
    verdict: str
    violation: Optional[str] = None


@dataclass(frozen=True)
class ProbeOutcome:
    """One positive-deny probe result for a mutating class (#468). #470's trusted collector
    populates these from the real provider stream; the canary only EVALUATES them. Each probe
    payload is intrinsically non-mutating (an OS-non-writable target) so an OS denial can never
    masquerade as a hook success — hook-origin evidence (``deny_reason`` carrying the guard's
    unique marker) is required."""
    issued_tool: Optional[str] = None              # tool the canary issued a probe for (must be in the class)
    issued_correlation_id: Optional[str] = None    # nonce issued
    observed_tool: Optional[str] = None            # tool named in the deny/result envelope
    observed_correlation_id: Optional[str] = None  # correlation echoed back
    denied: bool = False                           # a hook returned permissionDecision:deny (is_error tool_result)
    executed: bool = False                         # the probe tool actually RAN (no deny) -> the guard is ABSENT
    deny_reason: Optional[str] = None              # verbatim deny reason; must contain the class's guard marker
    transport_error: Optional[str] = None          # timeout/transport/malformed/provider-rejection detail


@dataclass(frozen=True)
class CanaryEvidence:
    """The signals the canary evaluates. Its EVIDENCE comes from provider CLI output contracts
    (spike #454): #470's trusted collector populates the fields from the real dispatch. Every
    field is fail-closed by default (absent -> the owning check refuses). ``dispatch_nonce`` +
    ``snapshot_digest`` bind the evidence to ONE composition (replay rejection)."""
    provider: str
    profile: str = "mutating"
    dispatch_nonce: Optional[str] = None
    snapshot_digest: Optional[str] = None
    # hooks_digest
    registration_digest: Optional[str] = None      # digest computed over the staged snapshot
    registration_readable: bool = True              # False -> hooks_evidence_missing
    # plugin_version
    plugin_version: Optional[str] = None
    # lane_provisioned
    init_plugins: Optional[list] = None             # init.plugins[] from the claude -p stream-json init event
    # positive_deny
    hooks_registration: Optional[dict] = None       # parsed hooks.json from the staged (digest-pinned) snapshot
    probes: Optional[dict] = None                   # {matcher_class: ProbeOutcome}
    # codex_containment
    codex_argv: Optional[list] = None
    codex_worktree: Optional[str] = None
    codex_containment_root: Optional[str] = None  # approved root — the canary RE-VERIFIES containment (not just roots==wt)
    # codex_behavioral (#556 H3): the result of a pre-spawn behavioral probe that launched the EXACT
    # mutating composition in a THROWAWAY worktree — {"inside_written": bool, "outside_blocked": bool}
    # (fail-closed: absent / not-both-true -> refuse). Composition-validation alone does not prove the
    # sandbox actually confines a write; this does.
    codex_behavioral: Optional[dict] = None
    # bare_absent
    final_argv: Optional[list] = None


@dataclass(frozen=True)
class LaunchComposition:
    """The immutable final launch composition ``require_canary`` derives its policy from (#468).
    #470 constructs this from the real dispatch (provider + the staged ``LaunchProfile`` + the
    dispatch nonce + the staged-snapshot digest). The canary NEVER accepts a caller-selected
    policy — the policy is derived from ``provider`` + ``profile.mutating`` (adversarial H2)."""
    provider: str
    profile: "contract.LaunchProfile"
    dispatch_nonce: Optional[str] = None
    snapshot_digest: Optional[str] = None


@dataclass(frozen=True)
class CanaryResult:
    """Identifiers live ON the result (adversarial M2 — no global-state lookup). ``checks`` holds
    one ``CheckResult`` per ``required_checks`` id (pass|refuse only — a required check is never
    not_applicable). ``violations`` is accumulated in deterministic policy order."""
    policy_revision: int
    policy_id: str
    provider: str
    profile: str
    verdict: str                       # "pass" | "refuse"
    required_checks: tuple
    checks: tuple
    violations: tuple

    def __post_init__(self):
        for c in self.checks:
            if c.verdict not in (PASS, REFUSE):
                raise ValueError(
                    f"required check {c.check_id!r} has forbidden verdict {c.verdict!r} "
                    f"(a required check is pass or refuse, never not_applicable)")

    def pass_summary(self) -> dict:
        """The EXACTLY-8-key summary stamped onto a dispatched Observation. Built from THIS
        result alone (no global state). On a dispatched pass ``passed_checks == required_checks``
        and ``violations == []``."""
        return {
            "policy_revision": self.policy_revision,
            "policy_id": self.policy_id,
            "provider": self.provider,
            "profile": self.profile,
            "verdict": self.verdict,
            "required_checks": list(self.required_checks),
            "passed_checks": [c.check_id for c in self.checks if c.verdict == PASS],
            "violations": list(self.violations),
        }


class CanaryRefused(contract.CompositionError):
    """Raised by ``require_canary`` on any non-pass. Carries the structured result; ``__str__``
    leads with the policy id + revision + the stable violation tags so a handler cannot reduce
    the refusal to a generic error (adversarial M1)."""

    def __init__(self, result: "CanaryResult"):
        self.result = result
        super().__init__(
            f"canary refused [{result.policy_id} rev{result.policy_revision}]: "
            f"{','.join(result.violations)}")


# --------------------------------------------------------------- registration digest (H3)
def _add_record(records: dict, root_p: Path, rel_path: str) -> None:
    """Read ``rel_path`` (relative to ``root_p``) into ``records`` keyed by its normalized rel
    path. Fail-closed: a path escaping root, a symlink, or a duplicate rel path all raise."""
    norm = os.path.normpath(rel_path)
    if os.path.isabs(norm) or norm == ".." or norm.startswith(".." + os.sep):
        raise ValueError(f"registration digest: referenced path {rel_path!r} escapes root")
    if norm in records:
        raise ValueError(f"registration digest: duplicate path {norm!r}")
    target = root_p / norm
    if target.is_symlink():
        raise ValueError(f"registration digest: symlink rejected: {rel_path!r}")
    resolved = target.resolve()
    if resolved != root_p and root_p not in resolved.parents:
        raise ValueError(f"registration digest: path {rel_path!r} resolves outside root")
    records[norm] = target.read_bytes()


def compute_registration_digest(root) -> str:
    """Canonical LENGTH-FRAMED sha256 over hooks.json + every script referenced in its command
    fields (adversarial H3: bind the enforcing artifacts, not just the registration). Each record
    is ``u64(len(rel_path)) ++ rel_path ++ u64(len(content)) ++ content`` (8-byte big-endian
    lengths), records ordered by normalized rel path. Naive concatenation permits boundary
    collisions (bytes moved between two files leave the concatenated hash unchanged); the framing
    closes that. Fail-closed: duplicate paths, symlinks, and paths outside root all raise.

    ``root`` is the plugin registration root; hooks.json lives at ``root/hooks/hooks.json`` and
    each command's ``${CLAUDE_PLUGIN_ROOT}`` resolves to ``root``."""
    root_p = Path(root).resolve()
    records: dict = {}
    _add_record(records, root_p, "hooks/hooks.json")
    obj = json.loads(records["hooks/hooks.json"])
    for event_hooks in (obj.get("hooks") or {}).values():
        for entry in event_hooks:
            for hook in entry.get("hooks", []):
                cmd = str(hook.get("command", ""))
                if _PLUGIN_ROOT_VAR in cmd:
                    rel = cmd.replace(_PLUGIN_ROOT_VAR, "").lstrip("/")
                    # A bare ${CLAUDE_PLUGIN_ROOT} (or trailing whitespace) yields an empty/"."
                    # rel that would read_bytes() the root DIR — skip it (references no script),
                    # rather than crash uncontrolled.
                    if rel and os.path.normpath(rel) != ".":
                        _add_record(records, root_p, rel)
    hasher = hashlib.sha256()
    for rel_path in sorted(records):
        content = records[rel_path]
        rp = rel_path.encode("utf-8")
        hasher.update(len(rp).to_bytes(8, "big"))
        hasher.update(rp)
        hasher.update(len(content).to_bytes(8, "big"))
        hasher.update(content)
    return "sha256:" + hasher.hexdigest()


def pretooluse_guard_basenames(hooks_obj: dict) -> set:
    """Every PreToolUse guard basename in a hooks.json object (enforcing or not). The drift-guard
    asserts this set is fully classified across _GUARD_DENY_MARKERS ∪ _KNOWN_NONENFORCING_PRETOOL_GUARDS."""
    out = set()
    for entry in hooks_obj["hooks"]["PreToolUse"]:
        for hook in entry.get("hooks", []):
            out.add(str(hook.get("command", "")).rsplit("/", 1)[-1])
    return out


def mutating_guard_classes(hooks_obj: dict) -> dict:
    """Map each PreToolUse matcher CLASS -> its enforcing-deny guard basename, for the guards in
    the CURATED ``_GUARD_DENY_MARKERS`` set (the deny-enforcers for the un-granted-mutating-tool
    threat, spike #454). Coverage is bounded by that curated set — NOT by the full PreToolUse
    matcher set — so a NEW enforcing guard is NOT auto-covered; ``test_pretooluse_guard_set_is_classified``
    forces it to be consciously classified. Fail-closed: a non-conforming object raises (the caller
    wraps it to canary_check_error)."""
    pre = hooks_obj["hooks"]["PreToolUse"]
    out = {}
    for entry in pre:
        matcher = entry["matcher"]
        for hook in entry.get("hooks", []):
            base = str(hook.get("command", "")).rsplit("/", 1)[-1]
            if base in _GUARD_DENY_MARKERS:
                out[matcher] = base
    return out


# ------------------------------------------------------------------------- the 5 checks
def _check_hooks_digest(ev: CanaryEvidence) -> CheckResult:
    if not ev.registration_readable or ev.registration_digest is None:
        return CheckResult("hooks_digest", REFUSE, "hooks_evidence_missing")
    if ev.registration_digest != EXPECTED_REGISTRATION_DIGEST:
        return CheckResult("hooks_digest", REFUSE, "hooks_digest_mismatch")
    return CheckResult("hooks_digest", PASS)


def _check_plugin_version(ev: CanaryEvidence) -> CheckResult:
    # missing/unreadable version is not equal to the pin -> refuse (its OWN check, not folded
    # into the digest — the peer's separation).
    if ev.plugin_version != EXPECTED_PLUGIN_VERSION:
        return CheckResult("plugin_version", REFUSE, "plugin_version_mismatch")
    return CheckResult("plugin_version", PASS)


def _check_lane_provisioned(ev: CanaryEvidence) -> CheckResult:
    plugins = ev.init_plugins
    if plugins is None or not isinstance(plugins, list):
        return CheckResult("lane_provisioned", REFUSE, "init_evidence_invalid")
    ids = set()
    for p in plugins:
        if isinstance(p, str):
            ids.add(p)
        elif isinstance(p, dict):
            for key in ("name", "id", "plugin"):
                v = p.get(key)
                if isinstance(v, str):
                    ids.add(v)
    if not (ids & _RAWGENTIC_PLUGIN_IDS):
        return CheckResult("lane_provisioned", REFUSE, "lane_unprovisioned")
    return CheckResult("lane_provisioned", PASS)


def _check_positive_deny(ev: CanaryEvidence) -> CheckResult:
    # Per-mutating-class coverage: EVERY class derived from the (digest-pinned) hooks.json
    # PreToolUse matchers must produce a correlated, hook-origin denial or the check refuses.
    # A missing hooks_registration raises here -> evaluate_canary wraps it to canary_check_error.
    guard_by_class = mutating_guard_classes(ev.hooks_registration)
    if not guard_by_class:
        return CheckResult("positive_deny", REFUSE, "positive_deny_unproven:*")
    probes = ev.probes or {}
    for matcher in sorted(guard_by_class):
        marker = _GUARD_DENY_MARKERS[guard_by_class[matcher]]
        tools = set(matcher.split("|"))
        outcome = probes.get(matcher)
        if outcome is None:
            return CheckResult("positive_deny", REFUSE, f"positive_deny_unproven:{matcher}")
        if outcome.transport_error:
            return CheckResult("positive_deny", REFUSE, f"positive_deny_unproven:{matcher}")
        if outcome.executed and outcome.denied:  # contradictory / ambiguous
            return CheckResult("positive_deny", REFUSE, f"positive_deny_unproven:{matcher}")
        if outcome.executed:                      # the probe RAN with no deny -> guard absent
            return CheckResult("positive_deny", REFUSE, f"positive_deny_absent:{matcher}")
        if not outcome.denied:                    # neither ran nor denied -> ambiguous
            return CheckResult("positive_deny", REFUSE, f"positive_deny_unproven:{matcher}")
        if (outcome.issued_tool not in tools
                or outcome.observed_tool != outcome.issued_tool
                or not outcome.issued_correlation_id
                or outcome.observed_correlation_id != outcome.issued_correlation_id):
            return CheckResult("positive_deny", REFUSE, f"positive_deny_unproven:{matcher}")
        if not outcome.deny_reason or marker not in outcome.deny_reason:  # not hook-origin
            return CheckResult("positive_deny", REFUSE, f"positive_deny_unproven:{matcher}")
    return CheckResult("positive_deny", PASS)


def _check_codex_containment(ev: CanaryEvidence) -> CheckResult:
    argv = ev.codex_argv
    wt = ev.codex_worktree
    root = ev.codex_containment_root
    if not argv or not isinstance(argv, list) or not wt or not root:
        return CheckResult("codex_containment", REFUSE, "codex_containment")
    from .adapters import codex_cli  # noqa: PLC0415 (local: keep canary import-light; reuse the compose-time predicate)
    try:
        # RE-VERIFY the worktree is actually contained under the approved root (not just that
        # the argv's writable_roots == wt) — the canary's independent containment proof.
        contract.canonical_contained_worktree(wt, root)
        codex_cli.validate_mutating_composition(argv, wt)
    except contract.CompositionError:
        return CheckResult("codex_containment", REFUSE, "codex_containment")
    return CheckResult("codex_containment", PASS)


def _check_codex_behavioral(ev: CanaryEvidence) -> CheckResult:
    """#556 H3 — the BEHAVIORAL containment proof. Composition validation (codex_containment) proves
    the argv/roots are shaped right; this proves the sandbox ACTUALLY confined a real write: a
    pre-spawn probe launched the exact mutating composition in a throwaway worktree and reported
    whether an in-worktree write LANDED and an out-of-worktree (sibling) write was BLOCKED. Both must
    be true. Fail-closed: absent evidence, a non-dict, or either signal false/missing -> refuse (a
    negative control that did NOT block is the exact escape this check exists to catch)."""
    b = ev.codex_behavioral
    if not isinstance(b, dict) or b.get("inside_written") is not True or b.get("outside_blocked") is not True:
        return CheckResult("codex_behavioral", REFUSE, "codex_behavioral")
    return CheckResult("codex_behavioral", PASS)


def _check_bare_absent(ev: CanaryEvidence) -> CheckResult:
    argv = ev.final_argv
    if not argv or not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return CheckResult("bare_absent", REFUSE, "argv_evidence_invalid")
    if "--bare" in argv:  # exact token anywhere in the final direct-exec argv (F3a: no opt-out)
        return CheckResult("bare_absent", REFUSE, "bare_detected")
    return CheckResult("bare_absent", PASS)


_CHECKS = {
    "hooks_digest": _check_hooks_digest,
    "plugin_version": _check_plugin_version,
    "lane_provisioned": _check_lane_provisioned,
    "positive_deny": _check_positive_deny,
    "codex_containment": _check_codex_containment,
    "codex_behavioral": _check_codex_behavioral,
    "bare_absent": _check_bare_absent,
}


# ------------------------------------------------------------------------- evaluators
def evaluate_canary(policy_id, evidence) -> CanaryResult:
    """PURE evaluator — NEVER raises. Runs the policy's required checks in order, accumulating
    every violation deterministically. An unknown policy refuses (canary_policy_unknown); an
    internal check exception becomes canary_check_error:<id> (fail-closed)."""
    provider = getattr(evidence, "provider", None) or "unknown"
    profile = getattr(evidence, "profile", None) or "unknown"
    required = POLICIES.get(policy_id)
    if required is None:
        return CanaryResult(POLICY_REVISION, str(policy_id), provider, profile,
                            REFUSE, (), (), ("canary_policy_unknown",))
    checks = []
    violations = []
    for check_id in required:
        try:
            result = _CHECKS[check_id](evidence)
            if result.verdict not in (PASS, REFUSE):  # a required check never resolves not_applicable
                result = CheckResult(check_id, REFUSE, f"canary_check_error:{check_id}")
        except Exception:  # noqa: BLE001 — fail-closed: any check exception is a refusal, never a pass
            result = CheckResult(check_id, REFUSE, f"canary_check_error:{check_id}")
        checks.append(result)
        if result.verdict == REFUSE:
            # A refusing check ALWAYS contributes a legible violation — never dropped for a
            # null tag (that would let a refuse vanish from the trail). Fail-closed.
            violations.append(result.violation or f"unspecified_refuse:{check_id}")
    # Verdict is keyed off the CHECK VERDICTS, not the accumulated violation strings: a REFUSE
    # with an empty tag must still refuse (a latent aggregation fail-open otherwise). An EMPTY
    # checks list refuses too (all([]) is True → would fail open on an empty required-checks policy).
    verdict = PASS if (checks and all(c.verdict == PASS for c in checks)) else REFUSE
    if not checks:
        violations = list(violations) + ["canary_no_checks"]
    return CanaryResult(POLICY_REVISION, policy_id, provider, profile, verdict,
                        tuple(required), tuple(checks), tuple(violations))


def _policy_for(provider: Optional[str], mutating: bool) -> Optional[str]:
    if not mutating:
        return None
    key = f"{provider}_mutating"
    return key if key in POLICIES else None


def _meta_refuse(policy_id: str, provider: str, profile: str, tag: str) -> CanaryResult:
    return CanaryResult(POLICY_REVISION, policy_id, provider or "unknown", profile,
                        REFUSE, (), (), (tag,))


def require_canary(composition, evidence) -> CanaryResult:
    """PRODUCTION API. Derives the policy from the immutable final ``composition`` (never a
    caller arg — a Claude launch cannot be evaluated under codex_mutating), refuses a provider
    mismatch / an unresolvable composition / replayed evidence, and RAISES ``CanaryRefused`` on
    any non-pass. Returns the ``CanaryResult`` on pass. #470's dispatch choke-point calls this
    exactly once after the final composition is known, immediately before spawn; callers MUST
    NOT branch on the verdict themselves."""
    provider = getattr(composition, "provider", None)
    profile_obj = getattr(composition, "profile", None)
    mutating = bool(getattr(profile_obj, "mutating", False))
    profile_label = "mutating" if mutating else "read_only"
    policy_id = _policy_for(provider, mutating)
    if policy_id is None:
        raise CanaryRefused(_meta_refuse("unknown", provider or "unknown", profile_label, "canary_policy_unknown"))
    if getattr(evidence, "provider", None) != provider:
        raise CanaryRefused(_meta_refuse(policy_id, provider, profile_label, "canary_provider_mismatch"))
    # getattr-with-default on EVERY external read (incl. evidence) — malformed evidence must
    # produce a structured CanaryRefused, never a raw AttributeError (the module's contract).
    ev_digest = getattr(evidence, "snapshot_digest", None)
    ev_nonce = getattr(evidence, "dispatch_nonce", None)
    if (not ev_digest or not ev_nonce
            or ev_digest != getattr(composition, "snapshot_digest", None)
            or ev_nonce != getattr(composition, "dispatch_nonce", None)):
        raise CanaryRefused(_meta_refuse(policy_id, provider, profile_label, "evidence_binding_mismatch"))
    result = evaluate_canary(policy_id, evidence)
    if result.verdict != PASS:
        raise CanaryRefused(result)
    return result
