"""Claude Code CLI adapter.

Invocation: ``claude --print --model <m> --output-format json`` (prompt on stdin). The JSON
envelope carries ``modelUsage`` (a dict keyed by model id — the actual-model evidence),
``usage`` (token counts), ``total_cost_usd``, and ``result`` (the text).

Identity (finding f2, canonical-first): canonicalize the requested id AND every ``modelUsage``
key; the actual model is the RAW key whose canonical id equals the requested canonical id.
Exactly one match => that key; zero or more-than-one => no confirmed identity (engine records
identity_failure). Other keys (e.g. a haiku subagent) are auxiliary and never confused with it.
"""
from __future__ import annotations

import json
import time
from typing import Optional, Union

from .. import contract
from ..capture import create_capture
from .base import AdapterRequest, ParsedResult, ProcOutcome, build_observation, run_subprocess

ENGINE = "claude"


# #465: effective_grants -> claude -p tool names (wire == record; the closure lives in
# profile_from_manifest). Grants are capability SELECTION, not a sandbox.
_GRANT_TOOLS = {"read": ("Read", "Grep", "Glob"), "edit": ("Edit", "Write"),
                "bash": ("Bash",), "net": ("WebFetch", "WebSearch")}


def build_command(model: str, *, effort: Optional[str] = None,
                  profile: "Optional[contract.LaunchProfile]" = None,
                  resume_session_id: Optional[str] = None) -> list:
    """Compose the claude -p argv. No profile = today's exact command (byte-identical).

    #465 three-way session branch AT COMPOSE TIME (profiles are publicly constructible, so
    derivation-time validation alone is bypassable): None/fresh -> pin
    --no-session-persistence; validated resume -> omit it (spike #455: the pin made resume
    unreachable); ANY other value -> CompositionError. Pre-spawn invariant: mutating must
    equal edit-or-bash in effective_grants (an injected inconsistent profile refuses)."""
    cmd = ["claude", "--print", "--model", model, "--output-format", "json"]
    if resume_session_id is not None and (profile is None or profile.session_policy != "resume"):
        # #467 W4: resuming under a fresh-pinned launch is unreachable (spike #455) — refuse
        # at compose time rather than ship a --resume the pin silently defeats.
        raise contract.CompositionError(
            "claude launch: resume_session_id requires profile.session_policy == 'resume'")
    if profile is None or profile.session_policy == "fresh":
        cmd.append("--no-session-persistence")
    elif profile.session_policy == "resume":
        if resume_session_id is not None:
            cmd += ["--resume", resume_session_id]  # #467 W4 quota_paused relaunch (spike #455)
    else:
        raise contract.CompositionError(
            f"claude launch: session_policy {profile.session_policy!r} is not a validated "
            f"value (fresh|resume) — refusing to compose")
    if profile is not None:
        if profile.mutating != bool({"edit", "bash"} & set(profile.effective_grants)):
            raise contract.CompositionError(
                f"claude launch: profile.mutating={profile.mutating} inconsistent with "
                f"effective_grants={profile.effective_grants!r} — refusing to compose")
        if profile.effective_grants:
            tools = []
            for g in profile.effective_grants:
                mapped = _GRANT_TOOLS.get(g)
                if mapped is None:  # DF-2: a grant with no tool mapping must not silently vanish
                    raise contract.CompositionError(
                        f"claude launch: unknown grant {g!r} has no --allowedTools mapping")
                tools.extend(mapped)
            cmd += ["--allowedTools", ",".join(tools)]
        if profile.max_budget_usd is not None:
            cmd += ["--max-budget-usd", str(profile.max_budget_usd)]
    if effort:
        cmd += ["--effort", effort]
    return cmd


def _usage_from(env: dict) -> Optional[dict]:
    u = env.get("usage")
    if not isinstance(u, dict):
        return None
    inp = u.get("input_tokens")
    out = u.get("output_tokens")
    if inp is None or out is None:
        return None
    usage = {"input": int(inp), "output": int(out), "cached": int(u.get("cache_read_input_tokens", 0) or 0)}
    cost = env.get("total_cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        usage["cost_proxy"] = float(cost)
    return usage


def parse_claude(raw: Union[str, dict], *, requested_model: str) -> ParsedResult:
    """Pure parser over a claude ``--output-format json`` envelope."""
    try:
        env = raw if isinstance(raw, dict) else json.loads(raw)
    except (ValueError, TypeError) as exc:
        return ParsedResult(parse_error=f"not JSON: {exc}")
    if not isinstance(env, dict):
        return ParsedResult(parse_error="envelope is not an object")
    model_usage = env.get("modelUsage")
    if not isinstance(model_usage, dict) or not model_usage:
        return ParsedResult(text=env.get("result", "") or "", parse_error="no modelUsage in envelope")
    rc = contract.canonicalize_model_id(requested_model)
    matches = [k for k in model_usage if contract.canonicalize_model_id(k) == rc]
    actual = matches[0] if len(matches) == 1 else None
    return ParsedResult(
        text=env.get("result", "") or "",
        actual_model=actual,
        usage=_usage_from(env),
        payload=env.get("result"),
    )


def _claude_env(credential_ref: Optional[str]) -> Optional[dict]:
    """#431: a lane's ``credential_ref`` names an isolated Claude config dir → set
    ``CLAUDE_CONFIG_DIR`` so this invocation uses that account's independent quota pool. A missing /
    empty ``credential_ref`` returns None (env inherited unchanged — the single-account default).
    Claude-only: codex has its own ``CODEX_HOME``, zhipu its own key."""
    if isinstance(credential_ref, str) and credential_ref:
        return {"CLAUDE_CONFIG_DIR": credential_ref}
    return None


def run(req: AdapterRequest, *, run_id: str, attempt_id: str, capture_root, routing_config_digest: str, queued_ms: int = 0, fallback_reason: Optional[str] = None) -> contract.Observation:
    """Live seat call. Writes a capture dir and returns an Observation."""
    cmd = build_command(req.requested_model, effort=req.effort, profile=req.profile,
                        resume_session_id=req.resume_session_id)
    # #465 P3-1: a mutating claude launch pins cwd to the canonicalized worktree — claude
    # has no OS sandbox here, so an ambient cwd must never receive Edit/Write/Bash effects.
    cwd = None
    if req.profile is not None and req.profile.mutating:
        # #465 Step-11 DF-1: claude has NO OS sandbox, so cwd is the ONLY containment — a
        # mutating claude launch MUST verify the worktree is contained under the
        # executor-approved root (same boundary the codex adapter enforces), else Edit/Write
        # could land in the canonical checkout. Shared helper, fail-closed.
        cwd = contract.canonical_contained_worktree(req.profile.worktree, req.containment_root)
    cap = create_capture(capture_root, run_id, req.seat, attempt_id)
    cap.write_input(req.prompt)
    started = time.monotonic()
    proc = run_subprocess(cmd, req.prompt, req.timeout, env=_claude_env(req.credential_ref), cwd=cwd)
    timing_ms = int((time.monotonic() - started) * 1000)
    cap.write_transport(proc.stdout)
    cap.write_stderr(proc.stderr)
    parsed = parse_claude(proc.stdout, requested_model=req.requested_model) if proc.stdout.strip() else ParsedResult(empty_transport=True)
    cap.write_output(parsed.text)
    obs = build_observation(
        req=req, engine=ENGINE, run_id=run_id, attempt_id=attempt_id, parsed=parsed, proc=proc,
        timing_ms=timing_ms, queued_ms=queued_ms, raw_capture_path=str(cap.path), routing_config_digest=routing_config_digest, fallback_reason=fallback_reason,
    )
    cap.write_observation(obs.to_dict())
    cap.finalize()
    return obs
