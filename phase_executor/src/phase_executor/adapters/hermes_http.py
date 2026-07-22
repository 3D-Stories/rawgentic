"""Hermes HTTP offload adapter — rawgentic #568 Phase-2.

A read-only "offload" seat backed by the EXISTING Hermes gateway's OpenAI-compatible HTTP API
server (`gateway/platforms/api_server.py`, darwin 10.0.17.204). Submits a run, polls to a
deadline, and returns ONE normalized Observation — fitting the synchronous adapter boundary
(pure ``parse_*`` + a thin ``run``) exactly like the other adapters. See
docs/planning/2026-07-22-568-phase2-hermes-offload-design.md.

Design invariants (the load-bearing ones):
- ACTIVATION GATE (F7): before any ``POST /v1/runs`` the adapter probes the gateway's backend
  disposition; if it is NOT confirmably sandboxed it REFUSES to dispatch (typed availability
  failure). rawgentic's ``tool_grants:["read"]`` is caller-side selection, NOT a gateway
  sandbox — an unsandboxed gateway would run injected instructions as the darwin host user.
  Fail-CLOSED: an absent/unknown backend field is treated as unsandboxed (refuse).
- Identity (F2): the seat routes under model id ``hermes-agent`` and attests
  ``actual_model="hermes-agent"`` from the gateway's own ``/health`` platform field — the
  innermost gpt-5.x is Hermes's internal choice, not the seat's routed identity. ``verify_post``
  then VERIFIES requested==actual with no contract change.
- Usage (F3): absent gateway usage → ``ParsedResult.usage = None`` → the shared
  ``resolve_parse_status`` returns ``USAGE_UNAVAILABLE`` (a real status), never a fabricated
  zero and never a new closed-schema Observation field.
- Failure taxonomy (F4): mapped to EXISTING statuses — transport/gateway/health/gate failures
  are AVAILABILITY failures (fall back / honest); a terminal ``failed`` run is a produced
  envelope with a ``parse_error`` (breach, not availability); ``submission_unknown`` is
  ``empty_transport`` (availability) and is NEVER auto-retried (no idempotency key).
- Secrets (F9): the key is read by NAME from the process env (loaded from the 0600
  ``~/.config/rawgentic/hermes.env`` at the dispatch boundary); it is placed ONLY in the
  Authorization header, never in argv/logs/Observations/capture; ``redact`` strips it from any
  diagnostic string. Bounded response read (F14) caps memory/capture growth.

Transport is INJECTABLE (``run(..., transport=...)``) so CI never needs a live gateway; the
default shells to the stdlib HTTP client. ``run`` accepts ``env`` for the same reason (tests
inject; the real dispatch boundary loads the 0600 file into ``os.environ``).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Callable, Optional, Tuple

from .. import contract
from ..capture import create_capture
from .base import AdapterRequest, ParsedResult, ProcOutcome, build_observation

ENGINE = "hermes"
PLATFORM_IDENTITY = "hermes-agent"          # the routed model id the seat contracts on
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
DEFAULT_MAX_RESP_BYTES = 256 * 1024         # F14 bounded read
_POLL_MIN_S = 1.0
_POLL_MAX_S = 8.0

# env-file the dispatch boundary loads (F9); the adapter reads the values from os.environ.
ENV_FILE = os.path.expanduser("~/.config/rawgentic/hermes.env")
URL_ENV = "HERMES_API_URL"
KEY_ENV = "HERMES_API_SERVER_KEY"


class HermesUnreachable(Exception):
    """The gateway could not be reached / returned a non-2xx at an availability-critical step."""


class ActivationRefused(Exception):
    """The gateway backend is not confirmably sandboxed — dispatch refused (F7)."""


# --------------------------------------------------------------------------- #
# small pure helpers
# --------------------------------------------------------------------------- #
def redact(text: str) -> str:
    """Strip a bearer token from any diagnostic string."""
    return re.sub(r"(?i)(authorization\s*:\s*bearer\s+)\S+", r"\1***", text or "")


def _as_dict(body) -> dict:
    if isinstance(body, dict):
        return body
    try:
        d = json.loads(body)
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def is_terminal(status: str) -> bool:
    """OPEN vocabulary: only the recognized terminal set completes; anything else is non-terminal
    (an unrecognized status past the deadline is failed-closed by the poll loop, not here)."""
    return status in TERMINAL_STATUSES


def backend_is_sandboxed(caps: dict) -> bool:
    """F7 activation gate, FAIL-CLOSED. True only when the gateway plainly reports a
    non-``local`` terminal backend; an absent/unknown/``local`` backend → False (refuse)."""
    if not isinstance(caps, dict):
        return False
    term = caps.get("terminal")
    backend = term.get("backend") if isinstance(term, dict) else caps.get("terminal_backend")
    if not isinstance(backend, str) or not backend:
        return False
    return backend.strip().lower() not in ("local", "unsandboxed", "")


def parse_submit(http_status, body) -> Tuple[Optional[str], Optional[str]]:
    """POST /v1/runs → (run_id, error). 202 body is ``{"run_id","status":"started"}``."""
    d = _as_dict(body)
    if d.get("error"):
        return None, _error_str(d)
    rid = d.get("run_id") or d.get("id")
    if str(http_status) == "202" and rid:
        return str(rid), None
    if rid:
        return str(rid), None
    return None, f"submit returned no run_id (HTTP {http_status})"


def parse_run_object(body) -> dict:
    """GET /v1/runs/{id} → normalized {status, output, usage, error, run_id}. Pure."""
    d = _as_dict(body)
    if d.get("error") and not d.get("status"):
        return {"status": "failed", "output": "", "usage": None,
                "error": _error_str(d), "run_id": d.get("run_id")}
    return {
        "status": d.get("status") or "",
        "output": d.get("output") or "",
        "usage": d.get("usage"),
        "error": d.get("error"),
        "run_id": d.get("run_id") or d.get("id"),
    }


def _error_str(d: dict) -> str:
    err = d.get("error")
    if isinstance(err, dict):
        return err.get("code") or err.get("message") or json.dumps(err)
    return str(err)


def run_to_parsed(run_obj: dict, *, requested_model: str) -> ParsedResult:
    """Terminal run object → ParsedResult. Identity is the platform id (F2); absent usage stays
    None → USAGE_UNAVAILABLE (F3); a ``failed`` run is a produced envelope with a parse_error."""
    status = run_obj.get("status")
    if status == "failed" or run_obj.get("error"):
        return ParsedResult(actual_model=PLATFORM_IDENTITY,
                            parse_error=f"hermes run {status or 'error'}: {run_obj.get('error')}")
    if status == "cancelled":
        return ParsedResult(actual_model=PLATFORM_IDENTITY, parse_error="hermes run cancelled")
    text = run_obj.get("output") or ""
    usage = None
    u = run_obj.get("usage")
    if isinstance(u, dict):
        inp, out = u.get("input_tokens"), u.get("output_tokens")
        if inp is not None and out is not None:
            usage = {"input": int(inp), "output": int(out),
                     "cached": int(u.get("cached_tokens", 0) or 0)}
    return ParsedResult(text=text, actual_model=PLATFORM_IDENTITY, usage=usage, payload=text)


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #
def _default_transport(method, url, *, headers=None, body=None, timeout=None):
    """Real stdlib HTTP. Returns (status_code:int, body_text:str). Raises HermesUnreachable on a
    transport error (never a silent empty result). The bearer token lives only in headers."""
    data = body.encode() if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(DEFAULT_MAX_RESP_BYTES + 1)
            if len(raw) > DEFAULT_MAX_RESP_BYTES:
                raise HermesUnreachable("response body exceeded max bytes")
            return resp.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:  # a 4xx/5xx with a body is a real HTTP response
        raw = e.read(DEFAULT_MAX_RESP_BYTES) if hasattr(e, "read") else b""
        return e.code, raw.decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise HermesUnreachable(redact(str(e))) from None


def _resolve_endpoint(env: dict) -> Tuple[str, str]:
    url = env.get(URL_ENV)
    key = env.get(KEY_ENV)
    if not url or not key:
        raise HermesUnreachable(f"missing {URL_ENV}/{KEY_ENV} (config)")
    return url.rstrip("/"), key


# --------------------------------------------------------------------------- #
# run() — engine calls this with fixed kwargs; tests inject transport + env
# --------------------------------------------------------------------------- #
def run(req: AdapterRequest, *, run_id: str, attempt_id: str, capture_root,
        routing_config_digest: str, queued_ms: int = 0, fallback_reason: Optional[str] = None,
        transport: Optional[Callable] = None, env: Optional[dict] = None,
        sleep=time.sleep, clock=time.monotonic) -> contract.Observation:
    if req.resume_session_id is not None:
        raise contract.CompositionError("hermes launch: resume_session_id is not supported")
    transport = transport or _default_transport
    env = env if env is not None else dict(os.environ)
    cap = create_capture(capture_root, run_id, req.seat, attempt_id)
    cap.write_input(req.prompt)
    started = clock()
    proc = ProcOutcome(returncode=0, stdout="", stderr="", timed_out=False)
    parsed = ParsedResult(empty_transport=True)  # default = availability failure

    try:
        base, key = _resolve_endpoint(env)
        auth = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        # --- preflight: health + ACTIVATION GATE (F7) ---
        hstatus, hbody = transport("GET", f"{base}/health", headers=auth, timeout=req.timeout)
        if str(hstatus) != "200":
            raise HermesUnreachable(f"/health HTTP {hstatus}")
        cstatus, cbody = transport("GET", f"{base}/v1/capabilities", headers=auth, timeout=req.timeout)
        caps = _as_dict(cbody) if str(cstatus) == "200" else {}
        if not backend_is_sandboxed(caps):
            raise ActivationRefused("gateway terminal backend is not confirmably sandboxed "
                                    "(unsandboxed 'local' or unknown) — offload dispatch refused")

        # --- submit ---
        submit_body = json.dumps({"input": req.prompt, "model": req.requested_model})
        sstatus, sbody = transport("POST", f"{base}/v1/runs", headers=auth,
                                   body=submit_body, timeout=req.timeout)
        rid, serr = parse_submit(sstatus, sbody)
        if rid is None:
            # a definite error body → produced envelope (breach); a bare timeout would have
            # raised HermesUnreachable above (availability). Treat a no-run_id 2xx/4xx as failure.
            parsed = ParsedResult(actual_model=PLATFORM_IDENTITY,
                                  parse_error=f"submission failed: {serr}")
            raise _Done()
        cap.write_transport(f"run_id={rid}")  # F: persist run_id as soon as it exists

        # --- poll to deadline ---
        deadline = started + req.timeout
        delay = _POLL_MIN_S
        last = None
        while True:
            pstatus, pbody = transport("GET", f"{base}/v1/runs/{rid}", headers=auth,
                                       timeout=min(req.timeout, 20))
            if str(pstatus) == "404":
                parsed = ParsedResult(actual_model=PLATFORM_IDENTITY,
                                      parse_error=f"run {rid} not found")
                break
            last = parse_run_object(pbody)
            if is_terminal(last.get("status")):
                parsed = run_to_parsed(last, requested_model=req.requested_model)
                break
            if clock() >= deadline:
                # deadline: ONE best-effort stop, then fail-closed availability.
                try:
                    transport("POST", f"{base}/v1/runs/{rid}/stop", headers=auth, timeout=5)
                except HermesUnreachable:
                    pass
                proc = ProcOutcome(returncode=None, stdout="", stderr="deadline", timed_out=True)
                parsed = ParsedResult(empty_transport=True)
                break
            sleep(min(delay, max(0.0, deadline - clock())))
            delay = min(_POLL_MAX_S, delay * 2)
    except ActivationRefused as e:
        proc = ProcOutcome(returncode=None, stdout="", stderr=redact(str(e)),
                          timed_out=False, launch_error=redact(str(e)))
        parsed = ParsedResult(empty_transport=True)
    except HermesUnreachable as e:
        proc = ProcOutcome(returncode=None, stdout="", stderr=redact(str(e)),
                          timed_out=False, launch_error=redact(str(e)))
        parsed = ParsedResult(empty_transport=True)
    except _Done:
        pass

    timing_ms = int((clock() - started) * 1000)
    cap.write_output(parsed.text)
    obs = build_observation(
        req=req, engine=ENGINE, run_id=run_id, attempt_id=attempt_id, parsed=parsed, proc=proc,
        timing_ms=timing_ms, queued_ms=queued_ms, raw_capture_path=str(cap.path),
        routing_config_digest=routing_config_digest, fallback_reason=fallback_reason,
    )
    cap.write_observation(obs.to_dict())
    cap.finalize()
    return obs


class _Done(Exception):
    """Internal short-circuit: a produced-envelope failure already set ``parsed``."""
