"""Trusted two-phase canary evidence collector (#470 W7, epic #475) — the ONLY sanctioned
producer of ``canary.CanaryEvidence``. It runs entirely in the trusted orchestrator-side process,
strictly BEFORE the mutating task pane exists (design §2a), so the evidence the guardrail canary
evaluates is never self-reported by the thing being guarded.

Two phases (design §2a):
- ``build_local_evidence`` (phase 1): from a staged, digest-frozen snapshot dir + the launch
  composition, compute the LOCAL signals — ``registration_digest`` (reusing
  ``canary.compute_registration_digest``), ``plugin_version`` (staged ``.claude-plugin/plugin.json``),
  ``hooks_registration`` (staged ``hooks/hooks.json``), and ``final_argv`` (from the bound pane
  spec). Carries the composition's ``provider``/``profile``/``dispatch_nonce``/``snapshot_digest``
  so the completed evidence satisfies ``require_canary``'s binding.
- ``complete_evidence`` (phase 2): from an INJECTED pre-spawn probe-session stream, fill
  ``init_plugins`` (the stream-json init event) and ``probes`` (a ``ProbeOutcome`` per issued
  matcher class, correlated by the issuance plan). No live process is spawned here — the reader is
  injected (callable/file handle/iterable); a RUN_LIVE probe belongs to the supervisor (Task 3).

FAIL CLOSED EVERYWHERE: every external read is getattr/get-with-default. A missing snapshot, an
unparseable hooks.json/plugin.json, a malformed or truncated stream, a garbage composition — none
raise out of the collector; they yield evidence whose absent field makes the OWNING canary check
REFUSE (``hooks_evidence_missing`` / ``plugin_version_mismatch`` / ``init_evidence_invalid`` /
``positive_deny_unproven`` / ``evidence_binding_mismatch``). A guard that is genuinely ABSENT
surfaces via ``positive_deny_absent``/``positive_deny_unproven`` — never a fabricated pass. There
is NO caller-supplied evidence path and NO CLI surface: this is a pure library module.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Optional

from . import canary


# --------------------------------------------------------------- phase 1: local reads
def _safe_registration_digest(root: Optional[Path]) -> Optional[str]:
    if root is None:
        return None
    try:
        return canary.compute_registration_digest(root)
    except Exception:  # noqa: BLE001 — fail-closed: an unreadable snapshot -> hooks_evidence_missing
        return None


def _safe_plugin_version(root: Optional[Path]) -> Optional[str]:
    if root is None:
        return None
    try:
        obj = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        version = obj.get("version") if isinstance(obj, dict) else None
        return version if isinstance(version, str) else None
    except Exception:  # noqa: BLE001 — fail-closed: absent/garbage -> plugin_version_mismatch
        return None


def _safe_hooks_registration(root: Optional[Path]) -> Optional[dict]:
    if root is None:
        return None
    try:
        obj = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:  # noqa: BLE001 — fail-closed: None -> positive_deny canary_check_error
        return None


def build_local_evidence(*, snapshot_dir, composition, final_argv) -> canary.CanaryEvidence:
    """Phase 1. Build the partially-populated evidence from the staged snapshot + the composition.
    ``composition`` is read getattr-with-default so a malformed one never raises — it simply yields
    absent binding fields that ``require_canary`` later refuses. ``final_argv`` is passed through
    unchanged (the ``bare_absent`` check validates its shape)."""
    provider = getattr(composition, "provider", None)
    profile_obj = getattr(composition, "profile", None)
    mutating = bool(getattr(profile_obj, "mutating", False))
    profile_label = "mutating" if mutating else "read_only"

    root = Path(snapshot_dir) if snapshot_dir is not None else None
    registration_digest = _safe_registration_digest(root)

    return canary.CanaryEvidence(
        provider=provider,
        profile=profile_label,
        dispatch_nonce=getattr(composition, "dispatch_nonce", None),
        snapshot_digest=getattr(composition, "snapshot_digest", None),
        registration_digest=registration_digest,
        registration_readable=registration_digest is not None,
        plugin_version=_safe_plugin_version(root),
        hooks_registration=_safe_hooks_registration(root),
        final_argv=final_argv,
    )


# ------------------------------------------------------------- phase 2: probe stream
def _events(stream):
    """Yield parsed event dicts from an injected reader. Accepts a callable (called with no args),
    a file handle / iterable of JSON-text lines, or an iterable of already-parsed dicts. Malformed
    lines are skipped (fail-closed: unreadable evidence must not raise, it must be absent)."""
    if callable(stream):
        stream = stream()
    for item in stream or ():
        if isinstance(item, dict):
            yield item
            continue
        if not isinstance(item, str):
            continue
        line = item.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            yield parsed


def _text_of(content) -> str:
    """Flatten a tool_result ``content`` (a string, or a list of ``{type:text,text:...}`` blocks)
    into a single string. Anything unexpected flattens to "" — an empty reason carries no guard
    marker, so the owning check refuses (fail-closed)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                txt = block.get("text")
                if isinstance(txt, str):
                    parts.append(txt)
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def _index_stream(events):
    """Single pass over the stream → (init_plugins, {tool_use_id: name}, {tool_use_id: (is_error,
    reason_text)}). Every read is get-with-default."""
    init_plugins = None
    tool_names: dict = {}
    tool_results: dict = {}
    for ev in events:
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            plugins = ev.get("plugins")
            if isinstance(plugins, list):
                init_plugins = plugins
        message = ev.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                bid = block.get("id")
                if isinstance(bid, str):
                    tool_names[bid] = block.get("name")
            elif btype == "tool_result":
                tid = block.get("tool_use_id")
                if isinstance(tid, str):
                    tool_results[tid] = (bool(block.get("is_error", False)),
                                         _text_of(block.get("content")))
    return init_plugins, tool_names, tool_results


def _probe_for(spec, tool_names, tool_results) -> canary.ProbeOutcome:
    issued_tool = spec.get("issued_tool") if isinstance(spec, dict) else None
    issued_cid = spec.get("issued_correlation_id") if isinstance(spec, dict) else None
    observed_tool = tool_names.get(issued_cid) if isinstance(issued_cid, str) else None
    result = tool_results.get(issued_cid) if isinstance(issued_cid, str) else None
    if result is None:
        # No correlated result observed in the stream -> ambiguous -> unproven (fail-closed).
        return canary.ProbeOutcome(
            issued_tool=issued_tool, issued_correlation_id=issued_cid,
            observed_tool=observed_tool, observed_correlation_id=None,
            transport_error="probe_result_absent")
    is_error, reason = result
    # is_error True  -> a hook returned a deny (denied); executed stays False.
    # is_error False -> the probe tool actually RAN (no deny) -> the guard is ABSENT.
    return canary.ProbeOutcome(
        issued_tool=issued_tool, issued_correlation_id=issued_cid,
        observed_tool=observed_tool, observed_correlation_id=issued_cid,
        denied=is_error, executed=not is_error, deny_reason=reason)


def complete_evidence(*, evidence, stream, probe_plan) -> canary.CanaryEvidence:
    """Phase 2. Read the injected probe-session stream and return ``evidence`` with ``init_plugins``
    and ``probes`` filled — every phase-1 field (incl. the composition binding) preserved.
    ``probe_plan`` maps ``{matcher_class: {"issued_tool", "issued_correlation_id"}}`` (the issuance
    the trusted process scripted); the outcome is correlated to the stream by that issued id."""
    init_plugins, tool_names, tool_results = _index_stream(_events(stream))
    probes = None
    if isinstance(probe_plan, dict):
        probes = {cls: _probe_for(spec, tool_names, tool_results)
                  for cls, spec in probe_plan.items()}
    return dataclasses.replace(evidence, init_plugins=init_plugins, probes=probes)
