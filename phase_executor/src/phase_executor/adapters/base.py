"""Shared adapter plumbing: request/parsed-result types, a process-group-safe subprocess
runner, parse-status resolution, and Observation assembly.

Each adapter is a PURE parser (``parse_*`` — fixture-tested, no I/O) plus a thin ``run`` that
does the live subprocess/SDK call, writes the capture, and assembles the Observation. The model
flag is owned by the adapter; the prompt goes on stdin, never as an argv (no argv injection).
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .. import contract
from ..capture import hash_context, hash_text


@dataclass(frozen=True)
class AdapterRequest:
    """One seat invocation request."""
    seat: str
    requested_model: str
    prompt: str
    transport: str = "native"
    context: Sequence[str] = field(default_factory=tuple)
    correlation_id: Optional[str] = None
    effort: Optional[str] = None
    timeout: float = 300.0
    credential_ref: Optional[str] = None
    # #465: launch profile + executor-approved containment root. Defaults keep every
    # pre-profile caller byte-identical (fresh/read-only; no containment needed read-only).
    profile: "contract.LaunchProfile" = field(default_factory=lambda: contract.LaunchProfile())
    containment_root: Optional[str] = None
    # #467 W4: a quota_paused relaunch resumes the persisted provider session (spike #455).
    # claude composes `--resume <id>` (requires profile.session_policy == "resume");
    # codex/zhipuai refuse fail-loud. Default None keeps every existing caller byte-identical.
    resume_session_id: Optional[str] = None
    # #640: the project this dispatch was invoked for — the claude adapter threads it into
    # RAWGENTIC_DISPATCH_PROJECT so wal-bind-guard can bind an otherwise-unregistered
    # dispatched subprocess. Default None keeps every existing caller byte-identical.
    project: Optional[str] = None


@dataclass
class ParsedResult:
    """Pure-parser output: the evidence extracted from a raw provider envelope."""
    text: str = ""
    actual_model: Optional[str] = None
    usage: Optional[dict] = None            # {input, output, cached, cost_proxy?}
    payload: Any = None                     # structured parsed payload if any
    parse_error: Optional[str] = None       # set when a NON-EMPTY envelope could not be parsed
    empty_transport: bool = False           # transport produced nothing (no bytes / no events) -> availability failure


def _has_output(parsed: "ParsedResult") -> bool:
    if parsed.text:
        return True
    return parsed.payload not in (None, "")


@dataclass
class ProcOutcome:
    returncode: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool
    launch_error: Optional[str] = None


def run_subprocess(cmd: Sequence[str], stdin: str, timeout: float, *, env: Optional[dict] = None, cwd: Optional[str] = None) -> ProcOutcome:
    """Run ``cmd`` with ``stdin`` on stdin in its OWN process group; on timeout kill the whole
    group (no orphaned children), wait, and report ``timed_out``. Launch errors are captured,
    never raised, so the caller can still record an Observation.

    ``env`` (#431) is a dict of env-var ADDITIONS, MERGED onto the current ``os.environ`` (the child
    keeps PATH/HOME/etc. and gains the additions) — e.g. the claude adapter's ``CLAUDE_CONFIG_DIR``
    for a multi-account lane. ``env=None`` (default) inherits the parent environment unchanged
    (byte-identical to the pre-#431 behavior)."""
    proc_env = {**os.environ, **env} if env else None
    try:
        proc = subprocess.Popen(
            list(cmd),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True, env=proc_env, cwd=cwd,
        )
    except OSError as exc:
        return ProcOutcome(returncode=None, stdout="", stderr=str(exc), timed_out=False, launch_error=str(exc))
    try:
        out, err = proc.communicate(input=stdin, timeout=timeout)
        return ProcOutcome(returncode=proc.returncode, stdout=out, stderr=err, timed_out=False)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            proc.kill()
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return ProcOutcome(returncode=proc.returncode, stdout=out or "", stderr=err or "", timed_out=True)


def resolve_parse_status(parsed: ParsedResult, requested_model: str, *, timed_out: bool,
                         exit_code: Optional[int], launch_error: Optional[str]) -> str:
    """Final parse_status from process outcome + extracted evidence. Order matters:
    process failures first, then evidence, then identity, then usage."""
    if launch_error:
        return contract.LAUNCH_ERROR
    if timed_out:
        return contract.TIMEOUT
    if exit_code not in (0, None):
        return contract.NONZERO_EXIT
    if parsed.empty_transport:
        return contract.NO_RESPONSE  # transport gave nothing -> availability failure (falls back)
    if parsed.parse_error:
        return contract.PARSE_ERROR
    if not parsed.actual_model or not contract.models_match(requested_model, parsed.actual_model):
        return contract.IDENTITY_FAILURE
    if not parsed.usage or "input" not in parsed.usage or "output" not in parsed.usage:
        return contract.USAGE_UNAVAILABLE
    if not _has_output(parsed):
        return contract.NO_RESPONSE  # valid identity+usage but empty output -> not a usable success
    return contract.OK


def build_observation(
    *, req: AdapterRequest, engine: str, run_id: str, attempt_id: str,
    parsed: ParsedResult, proc: ProcOutcome, timing_ms: int, queued_ms: int,
    raw_capture_path: Optional[str], routing_config_digest: str,
    fallback_reason: Optional[str] = None,
    canary_result: Optional[dict] = None,  # a CanaryResult.pass_summary() dict, stamped when set
) -> contract.Observation:
    status = resolve_parse_status(
        parsed, req.requested_model,
        timed_out=proc.timed_out, exit_code=proc.returncode, launch_error=proc.launch_error,
    )
    obs = contract.Observation(
        run_id=run_id,
        attempt_id=attempt_id,
        correlation_id=req.correlation_id,
        seat=req.seat,
        engine=engine,
        transport=req.transport,
        requested_model=req.requested_model,
        actual_model=parsed.actual_model,
        prompt_hash=hash_text(req.prompt),
        context_hashes=hash_context(req.context),
        usage=parsed.usage,
        timing_ms=timing_ms,
        queued_ms=queued_ms,
        process={"exit_code": proc.returncode, "timed_out": proc.timed_out},
        parse_status=status,
        parsed_payload=parsed.payload if parsed.payload is not None else (parsed.text or None),
        raw_capture_path=raw_capture_path,
        fallback_reason=fallback_reason,
        routing_config_digest=routing_config_digest,
        # #468 W5: stamp the canary PASS summary when the dispatch was canary-gated (#470 wires
        # the caller; every existing caller passes None -> byte-identical legacy Observation).
        canary_result=canary_result.pass_summary() if canary_result is not None else None,
    )
    # Fail-loud on the write path: the schema is the normative artifact (contract.py), so an
    # Observation that resolve_parse_status and the schema disagree about must never be emitted.
    contract.validate_observation(obs.to_dict())
    return obs
