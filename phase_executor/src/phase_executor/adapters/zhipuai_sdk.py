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


def _invoke_worker(payload: str, timeout: float) -> ProcOutcome:
    """Try the locked env first (reproducible); fall back to the unbounded --with form."""
    last: Optional[ProcOutcome] = None
    for locked in (True, False):
        try:
            r = subprocess.run(_uv_command(locked), input=payload, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            last = ProcOutcome(returncode=None, stdout="", stderr=str(exc), timed_out=isinstance(exc, subprocess.TimeoutExpired), launch_error=str(exc))
            continue
        outcome = ProcOutcome(returncode=r.returncode, stdout=r.stdout, stderr=r.stderr, timed_out=False)
        if r.returncode == 0 and r.stdout.strip():
            return outcome
        last = outcome
    return last  # both attempts failed; return the last outcome for the Observation


def run(req: AdapterRequest, *, run_id: str, attempt_id: str, capture_root, routing_config_digest: str) -> contract.Observation:
    payload = json.dumps({"model": req.requested_model, "prompt": req.prompt, "max_tokens": 1024})
    cap = create_capture(capture_root, run_id, req.seat, attempt_id)
    cap.write_input(req.prompt)
    started = time.monotonic()
    proc = _invoke_worker(payload, req.timeout)
    timing_ms = int((time.monotonic() - started) * 1000)
    cap.write_transport(proc.stdout)
    cap.write_stderr(proc.stderr)
    parsed = parse_zhipuai(proc.stdout, requested_model=req.requested_model) if proc.stdout.strip() else ParsedResult(parse_error="empty worker stdout")
    cap.write_output(parsed.text)
    obs = build_observation(
        req=req, engine=ENGINE, run_id=run_id, attempt_id=attempt_id, parsed=parsed, proc=proc,
        timing_ms=timing_ms, queued_ms=0, raw_capture_path=str(cap.path), routing_config_digest=routing_config_digest,
    )
    cap.write_observation(obs.to_dict())
    cap.finalize()
    return obs
