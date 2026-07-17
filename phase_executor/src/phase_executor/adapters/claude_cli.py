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


def build_command(model: str, *, effort: Optional[str] = None) -> list:
    cmd = ["claude", "--print", "--model", model, "--output-format", "json", "--no-session-persistence"]
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


def run(req: AdapterRequest, *, run_id: str, attempt_id: str, capture_root, routing_config_digest: str, queued_ms: int = 0, fallback_reason: Optional[str] = None) -> contract.Observation:
    """Live seat call. Writes a capture dir and returns an Observation."""
    cmd = build_command(req.requested_model, effort=req.effort)
    cap = create_capture(capture_root, run_id, req.seat, attempt_id)
    cap.write_input(req.prompt)
    started = time.monotonic()
    proc = run_subprocess(cmd, req.prompt, req.timeout)
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
