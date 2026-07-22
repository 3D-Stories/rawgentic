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
- Failure taxonomy (F4): mapped to EXISTING statuses — transport/gateway/health/gate failures,
  a transient submit (429/5xx), a 404/transient poll, AND a terminal ``failed``/``cancelled`` run
  all resolve to AVAILABILITY failures so ``run_seat`` falls back to the analysis lane (a failed
  offload should degrade, not breach). Only a definite 4xx submit (a client/contract error that
  would fail identically on retry) is a produced-envelope breach. A submit timeout with no run_id
  raises ``HermesUnreachable`` (availability) and is NEVER auto-retried (no idempotency key). Any
  response-normalization exception is caught and mapped to an availability failure — never an
  uncaught crash.
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
from pathlib import Path
from typing import Callable, Optional, Tuple

from .. import contract
from ..capture import create_capture
from .base import AdapterRequest, ParsedResult, ProcOutcome, build_observation

ENGINE = "hermes"
PLATFORM_IDENTITY = "hermes-agent"          # the routed model id the seat contracts on
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
# Step-11 fix (Codex4/Opus-arch F2): the activation gate is an ALLOWLIST of confirmed-sandboxed
# gateway backends, never a denylist — an unknown/misspelled backend fails closed (refuse).
VERIFIED_SANDBOX_BACKENDS = frozenset({"docker", "podman", "gvisor"})
DEFAULT_MAX_RESP_BYTES = 256 * 1024         # F14 bounded read
_POLL_MIN_S = 1.0
_POLL_MAX_S = 8.0
_PREFLIGHT_TIMEOUT_S = 30.0                 # Step-11 fix (Opus-arch F4): preflight/submit are bounded
                                            # SHORT; only the poll loop gets the full seat budget.

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
    try:  # RecursionError (a deeply-nested hostile body) is caught too — Step-11 Opus-arch F3b
        d = json.loads(body)
    except (ValueError, TypeError, RecursionError):
        return {}
    return d if isinstance(d, dict) else {}


def _is_transient_http(status) -> bool:
    """429 or any 5xx → a transient gateway condition that should FALL BACK (availability),
    not a breach (Step-11 Opus-arch F1 / Codex). A definite 4xx (client/contract error) is not
    transient — it would fail identically on retry."""
    s = str(status)
    return s == "429" or (s.isdigit() and 500 <= int(s) <= 599)


def _load_env_file(path: Optional[str] = None) -> dict:
    """Parse the 0600 dispatch-boundary env file (Step-11 Codex1: the design's loader, now
    wired). ``path`` resolves to the module-global ENV_FILE at CALL time (so it stays overridable).
    Best-effort — a missing file yields {} and preflight fail-closes with a config error."""
    path = path or ENV_FILE
    out: dict = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def is_terminal(status: str) -> bool:
    """OPEN vocabulary: only the recognized terminal set completes; anything else is non-terminal
    (an unrecognized status past the deadline is failed-closed by the poll loop, not here)."""
    return status in TERMINAL_STATUSES


def backend_is_sandboxed(caps: dict) -> bool:
    """F7 activation gate, FAIL-CLOSED via an ALLOWLIST (Step-11 Codex4/Opus-arch F2). True ONLY
    when the gateway reports a terminal backend in VERIFIED_SANDBOX_BACKENDS; every other value —
    absent, unknown, misspelled, ``local``, ``host``, ``native``, ``docker-privileged`` — is
    refused. A denylist would open the gate on any unrecognized (possibly unsafe) backend."""
    if not isinstance(caps, dict):
        return False
    term = caps.get("terminal")
    backend = term.get("backend") if isinstance(term, dict) else caps.get("terminal_backend")
    if not isinstance(backend, str) or not backend:
        return False
    return backend.strip().lower() in VERIFIED_SANDBOX_BACKENDS


def parse_submit(http_status, body) -> Tuple[Optional[str], Optional[str]]:
    """POST /v1/runs → (run_id, error). 202 body is ``{"run_id","status":"started"}``. A run_id in
    any non-error body is accepted (the gateway ships 202); the transient-vs-breach decision for a
    NO-run_id response is the caller's, keyed on the HTTP status (Step-11 Opus-mech F4 — the old
    202-vs-non-202 branch was dead)."""
    d = _as_dict(body)
    if d.get("error"):
        return None, _error_str(d)
    rid = d.get("run_id") or d.get("id")
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


def _coerce_usage(u) -> Optional[dict]:
    """Gateway usage → {input,output,cached} or None. Guards every coercion (Step-11 F3): any
    non-int/negative/malformed value yields None (→ USAGE_UNAVAILABLE), never a raised exception
    and never a fabricated zero."""
    if not isinstance(u, dict):
        return None
    def _nn_int(v):
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            return None
        return v
    inp, out = _nn_int(u.get("input_tokens")), _nn_int(u.get("output_tokens"))
    if inp is None or out is None:
        return None
    cached = _nn_int(u.get("cached_tokens")) or 0
    return {"input": inp, "output": out, "cached": cached}


def run_to_parsed(run_obj: dict, *, requested_model: str) -> ParsedResult:
    """A COMPLETED run object → ParsedResult. Identity is the platform id (F2); absent/partial
    usage stays None → USAGE_UNAVAILABLE (F3). A non-completed terminal status (failed/cancelled)
    is NOT a produced-envelope breach — it returns ``empty_transport`` so ``run_seat`` falls back
    to the analysis lane (Step-11: failed offload should degrade, not breach); ``run`` captures the
    gateway error string separately."""
    if run_obj.get("status") != "completed":
        return ParsedResult(empty_transport=True)
    text = run_obj.get("output") or ""
    return ParsedResult(text=text, actual_model=PLATFORM_IDENTITY,
                        usage=_coerce_usage(run_obj.get("usage")), payload=text)


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
    if env is None:  # Step-11 Codex1: load the 0600 dispatch-boundary env file when unset
        env = dict(os.environ)
        if not (env.get(URL_ENV) and env.get(KEY_ENV)):
            for k, v in _load_env_file().items():
                env.setdefault(k, v)
    cap = create_capture(capture_root, run_id, req.seat, attempt_id)
    cap.write_input(req.prompt)
    started = clock()
    pre_to = min(req.timeout, _PREFLIGHT_TIMEOUT_S)  # short-bound preflight/submit (Opus-arch F4)
    proc = ProcOutcome(returncode=0, stdout="", stderr="", timed_out=False)
    parsed = ParsedResult(empty_transport=True)  # default = availability failure

    try:
        base, key = _resolve_endpoint(env)
        auth = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        # --- preflight: VALIDATED /health + ACTIVATION GATE (F7) ---
        hstatus, hbody = transport("GET", f"{base}/health", headers=auth, timeout=pre_to)
        hd = _as_dict(hbody)
        if str(hstatus) != "200" or hd.get("status") != "ok" or hd.get("platform") != PLATFORM_IDENTITY:
            # Step-11 Codex6: attest the platform identity from the body, not just the status —
            # a wrong/compatible endpoint must not be reported as a verified Hermes gateway.
            raise HermesUnreachable(
                f"/health did not attest a {PLATFORM_IDENTITY} gateway (HTTP {hstatus})")
        cstatus, cbody = transport("GET", f"{base}/v1/capabilities", headers=auth, timeout=pre_to)
        caps = _as_dict(cbody) if str(cstatus) == "200" else {}
        if not backend_is_sandboxed(caps):
            raise ActivationRefused("gateway terminal backend is not in the verified-sandbox "
                                    "allowlist (unsandboxed/unknown) — offload dispatch refused")

        # --- submit ---
        submit_body = json.dumps({"input": req.prompt, "model": req.requested_model})
        sstatus, sbody = transport("POST", f"{base}/v1/runs", headers=auth,
                                   body=submit_body, timeout=pre_to)
        rid, serr = parse_submit(sstatus, sbody)
        if rid is None:
            if _is_transient_http(sstatus):  # 429/5xx → availability → fall back (Opus-arch F1)
                raise HermesUnreachable(f"submit transient HTTP {sstatus}: {serr}")
            # a definite 4xx/contract error → produced-envelope breach (would fail identically on
            # retry, so do NOT fall back).
            parsed = ParsedResult(actual_model=PLATFORM_IDENTITY,
                                  parse_error=f"submission failed (HTTP {sstatus}): {serr}")
            raise _Done()
        cap.write_transport(f"run_id={rid}")  # persist run_id as soon as it exists

        # --- poll to deadline ---
        deadline = started + req.timeout
        delay = _POLL_MIN_S
        while True:
            pstatus, pbody = transport("GET", f"{base}/v1/runs/{rid}", headers=auth,
                                       timeout=min(req.timeout, 20))
            if str(pstatus) == "404" or _is_transient_http(pstatus):
                # run vanished / transient → availability → fall back (Opus-arch F1)
                raise HermesUnreachable(f"poll HTTP {pstatus} for run {rid}")
            last = parse_run_object(pbody)
            st = last.get("status")
            if is_terminal(st):
                parsed = run_to_parsed(last, requested_model=req.requested_model)
                if st != "completed":  # failed/cancelled → availability, capture the error string
                    err = redact(str(last.get("error") or st))
                    proc = ProcOutcome(returncode=None, stdout="", stderr=err, timed_out=False,
                                       launch_error=f"hermes run {st}")
                break
            if clock() >= deadline:
                try:  # deadline: ONE best-effort stop, then fail-closed availability.
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
    except (ValueError, TypeError, RecursionError) as e:
        # Step-11 Opus-arch F3: any response-normalization failure is a typed availability failure,
        # never an uncaught crash that skips the Observation/capture.
        proc = ProcOutcome(returncode=None, stdout="", stderr=redact(str(e)),
                          timed_out=False, launch_error=f"hermes response parse error: {redact(str(e))}")
        parsed = ParsedResult(empty_transport=True)

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
