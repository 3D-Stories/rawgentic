"""Codex CLI adapter (owner decision: the CLI IS the supported subscription client).

Invocation: ``codex exec --json -m <m> -c model_reasoning_effort=<e> --ephemeral --color never
-c project_doc_max_bytes=0 -s read-only -C <cwd> --skip-git-repo-check -`` (prompt on stdin).
``--json`` prints JSONL events on stdout: ``item.completed`` (the agent_message text) and
``turn.completed`` (``usage`` with input/output/cached — a real split).

Telemetry note (verified live 2026-07-16, codex-cli 0.144.1 — release-gate finding f5): the
``--json`` stream carries usage but NOT a model id, and ``--json`` suppresses the plain stderr
``model:`` header. For a ``native`` transport a pinned ``-m`` cannot be silently substituted (no
proxy hop), so actual_model == requested is established by the invocation contract and recorded.
For any proxied transport the adapter cannot audit the innermost id from ``--json`` and returns
no actual_model (engine records identity_failure — fail closed).
"""
from __future__ import annotations

import json
import time
from typing import Optional

from .. import contract
from ..capture import create_capture
from .base import AdapterRequest, ParsedResult, build_observation, run_subprocess

ENGINE = "codex"


def build_command(model: str, cwd: str, *, effort: str = "high") -> list:
    return [
        "codex", "exec", "--json", "-m", model,
        "-c", f"model_reasoning_effort={effort}",
        "--ephemeral", "--color", "never", "-c", "project_doc_max_bytes=0",
        "-s", "read-only", "-C", cwd, "--skip-git-repo-check", "-",
    ]


def parse_codex(stdout_jsonl: str, *, requested_model: str, transport: str = "native") -> ParsedResult:
    """Pure parser over codex ``--json`` JSONL events."""
    text: Optional[str] = None
    usage: Optional[dict] = None
    model_from_events: Optional[str] = None
    saw_event = False
    for line in stdout_jsonl.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        saw_event = True
        etype = ev.get("type")
        if etype == "item.completed":
            item = ev.get("item") or {}
            if item.get("type") == "agent_message":
                text = item.get("text", text)
        elif etype == "turn.completed":
            u = ev.get("usage") or {}
            if "input_tokens" in u and "output_tokens" in u:
                usage = {
                    "input": int(u["input_tokens"]),
                    "output": int(u["output_tokens"]),
                    "cached": int(u.get("cached_input_tokens", 0) or 0),
                }
        # future-proof: honor a model id if a codex version ever emits one
        if not model_from_events:
            for key in ("model", "model_id"):
                if isinstance(ev.get(key), str):
                    model_from_events = ev[key]
    if not saw_event:
        return ParsedResult(empty_transport=True)  # no events -> transport gave nothing (availability)
    if model_from_events:
        actual = model_from_events
    elif transport == "native":
        actual = requested_model  # pinned -m, direct connection, no proxy substitution
    else:
        actual = None  # proxied transport: cannot audit -> engine records identity_failure
    return ParsedResult(text=text or "", actual_model=actual, usage=usage, payload=text)


def run(req: AdapterRequest, *, run_id: str, attempt_id: str, capture_root, routing_config_digest: str, queued_ms: int = 0, fallback_reason: Optional[str] = None, cwd: Optional[str] = None) -> contract.Observation:
    import os  # noqa: PLC0415
    work = cwd or os.getcwd()
    cmd = build_command(req.requested_model, work, effort=req.effort or "high")
    cap = create_capture(capture_root, run_id, req.seat, attempt_id)
    cap.write_input(req.prompt)
    started = time.monotonic()
    proc = run_subprocess(cmd, req.prompt, req.timeout)
    timing_ms = int((time.monotonic() - started) * 1000)
    cap.write_transport(proc.stdout)
    cap.write_stderr(proc.stderr)
    parsed = parse_codex(proc.stdout, requested_model=req.requested_model, transport=req.transport)
    cap.write_output(parsed.text)
    obs = build_observation(
        req=req, engine=ENGINE, run_id=run_id, attempt_id=attempt_id, parsed=parsed, proc=proc,
        timing_ms=timing_ms, queued_ms=queued_ms, raw_capture_path=str(cap.path), routing_config_digest=routing_config_digest, fallback_reason=fallback_reason,
    )
    cap.write_observation(obs.to_dict())
    cap.finalize()
    return obs
