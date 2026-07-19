"""ZhipuAI (GLM) adapter.

The SDK is invoked in an isolated subprocess (``workers/zhipuai_call.py``) via uv, so importing
and fixture-testing this package never imports zhipuai or requires uv. The non-streaming response
exposes ``.model`` (actual-model evidence) and ``.usage`` (prompt/completion/cached tokens) —
verified live 2026-07-16.

Reproducibility (finding f6): the worker is run from the package's LOCKED env
(``uv run --locked --extra glm``) when available; the unbounded ``uv run --with`` form (owner's
validated invocation) is the documented fallback.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Optional, Union

from .. import contract
from ..capture import create_capture
from .base import AdapterRequest, ParsedResult, ProcOutcome, build_observation

ENGINE = "zhipuai"

_WORKER = Path(__file__).resolve().parent.parent / "workers" / "zhipuai_call.py"
_PKG_PROJECT = Path(__file__).resolve().parents[3]  # phase_executor/ project dir (has pyproject.toml)


def parse_zhipuai(resp: Union[str, dict], *, requested_model: str) -> ParsedResult:
    """Pure parser over a zhipuai non-streaming response dict (or its JSON text)."""
    try:
        data = resp if isinstance(resp, dict) else json.loads(resp)
    except (ValueError, TypeError) as exc:
        return ParsedResult(parse_error=f"not JSON: {exc}")
    if not isinstance(data, dict):
        return ParsedResult(parse_error="response is not an object")
    if data.get("error"):
        return ParsedResult(parse_error=str(data["error"]))
    model = data.get("model")
    u = data.get("usage") or {}
    inp = u.get("prompt_tokens")
    out = u.get("completion_tokens")
    usage = None
    if inp is not None and out is not None:
        cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)) or 0
        usage = {"input": int(inp), "output": int(out), "cached": int(cached)}
    text = ""
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        pass
    return ParsedResult(text=text, actual_model=model, usage=usage, payload=text)


def _uv_command(locked: bool) -> list:
    if locked:
        return ["uv", "run", "--locked", "--project", str(_PKG_PROJECT), "--extra", "glm", "python", str(_WORKER)]
    return ["uv", "run", "--with", "zhipuai>=2.1.5", "--with", "sniffio", "python", str(_WORKER)]


def _attempt(locked: bool, payload: str, timeout: float) -> ProcOutcome:
    try:
        r = subprocess.run(_uv_command(locked), input=payload, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ProcOutcome(returncode=None, stdout="", stderr=str(exc),
                           timed_out=isinstance(exc, subprocess.TimeoutExpired), launch_error=str(exc))
    return ProcOutcome(returncode=r.returncode, stdout=r.stdout, stderr=r.stderr, timed_out=False)


def _invoke_worker(payload: str, timeout: float) -> ProcOutcome:
    """Run the worker from the LOCKED env (reproducible). Fall back to the unbounded --with form
    ONLY when the locked attempt failed to RUN the worker (empty stdout = uv/lock setup failure) —
    never when the worker actually ran, so a provider/auth/quota error is not retried into a second
    (billable) provider call (finding f4-diff)."""
    locked = _attempt(True, payload, timeout)
    if locked.stdout.strip():
        return locked  # the worker ran (success or an error JSON) -> do not re-issue the request
    if locked.timed_out:
        return locked  # a timeout may mean the request reached the provider -> do not double-issue
    return _attempt(False, payload, timeout)  # locked setup failed before the worker ran -> retry unlocked


def run(req: AdapterRequest, *, run_id: str, attempt_id: str, capture_root, routing_config_digest: str, queued_ms: int = 0, fallback_reason: Optional[str] = None) -> contract.Observation:
    if req.resume_session_id is not None:
        # #467 W4: session resume is a claude-only wiring (spike #455) — refuse fail-loud.
        raise contract.CompositionError("zhipuai launch: resume_session_id is not supported")
    payload = json.dumps({"model": req.requested_model, "prompt": req.prompt, "max_tokens": 1024})
    cap = create_capture(capture_root, run_id, req.seat, attempt_id)
    cap.write_input(req.prompt)
    started = time.monotonic()
    proc = _invoke_worker(payload, req.timeout)
    timing_ms = int((time.monotonic() - started) * 1000)
    cap.write_transport(proc.stdout)
    cap.write_stderr(proc.stderr)
    parsed = parse_zhipuai(proc.stdout, requested_model=req.requested_model) if proc.stdout.strip() else ParsedResult(empty_transport=True)
    cap.write_output(parsed.text)
    obs = build_observation(
        req=req, engine=ENGINE, run_id=run_id, attempt_id=attempt_id, parsed=parsed, proc=proc,
        timing_ms=timing_ms, queued_ms=queued_ms, raw_capture_path=str(cap.path), routing_config_digest=routing_config_digest, fallback_reason=fallback_reason,
    )
    cap.write_observation(obs.to_dict())
    cap.finalize()
    return obs
