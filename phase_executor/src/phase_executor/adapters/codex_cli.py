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


def build_command(model: str, cwd: str, *, effort: Optional[str] = "high") -> list:
    """Read-only profile — byte-identical to the pre-#465 command when effort is set.
    None effort omits the flag (provider default; #465 — the None policy lives in
    model_capabilities.ENGINE_NONE_EFFORT, applied by run(), NOT re-defaulted here)."""
    cmd = ["codex", "exec", "--json", "-m", model]
    if effort:
        cmd += ["-c", f"model_reasoning_effort={effort}"]
    cmd += [
        "--ephemeral", "--color", "never", "-c", "project_doc_max_bytes=0",
        "-s", "read-only", "-C", cwd, "--skip-git-repo-check", "-",
    ]
    return cmd


# #465 B.4 — the three spike-#452 sandbox overrides (live Landlock-verified): the DEFAULT
# workspace-write boundary is /tmp-WIDE; these pin it to the worktree ONLY. approval_policy
# pinned per spike §4/§6 (belt-and-suspenders vs a user config.toml on-request; the W5
# canary #468 asserts the BEHAVIOR — composition validation alone never unlocks dispatch).
def build_mutating_command(model: str, worktree: str, *, effort: Optional[str] = "high",
                           containment_root: str) -> list:
    import os as _os  # noqa: PLC0415
    canon_wt = _os.path.realpath(worktree)
    canon_root = _os.path.realpath(containment_root)
    if canon_wt == canon_root or not canon_wt.startswith(canon_root + _os.sep):
        raise contract.CompositionError(
            f"codex mutating launch: worktree {worktree!r} fails containment under "
            f"{containment_root!r} (canonicalized: {canon_wt!r} vs root {canon_root!r})")
    cmd = ["codex", "exec", "--json", "-m", model]
    if effort:
        cmd += ["-c", f"model_reasoning_effort={effort}"]
    cmd += [
        "--ephemeral", "--color", "never", "-c", "project_doc_max_bytes=0",
        "-s", "workspace-write",
        "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
        "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        "-c", f'sandbox_workspace_write.writable_roots=["{canon_wt}"]',
        "-c", "approval_policy=never",
        "-C", canon_wt, "--skip-git-repo-check", "-",
    ]
    validate_mutating_composition(cmd, canon_wt)
    return cmd


def validate_mutating_composition(cmd: list, canonical_worktree: str) -> None:
    """The compose-time refusal predicate (spike #452 report :153) — separately importable
    so the W5 canary (#468) re-asserts the same invariant at runtime. Fail-closed: any
    missing override, or writable_roots not naming EXACTLY the canonical worktree, refuses
    before spawn."""
    joined = " ".join(cmd)
    required = (
        "-s workspace-write",
        "sandbox_workspace_write.exclude_slash_tmp=true",
        "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        f'sandbox_workspace_write.writable_roots=["{canonical_worktree}"]',
    )
    for lit in required:
        if lit not in joined:
            raise contract.CompositionError(
                f"codex mutating launch REFUSED: composition missing {lit!r} — a naive "
                f"workspace-write boundary is /tmp-wide (spike #452)")



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
    # #465 S1: the None policy is registry-sourced — ONE constant feeds both the engine's
    # resolver (adapter_default) and this fallback, so they agree by construction; wire is
    # byte-identical on run_seat AND run_competitive.
    from ..model_capabilities import ENGINE_NONE_EFFORT  # noqa: PLC0415
    effective_effort = req.effort or ENGINE_NONE_EFFORT.get("codex")
    profile = req.profile
    if profile is not None and profile.mutating != bool({"edit", "bash"} & set(profile.effective_grants)):
        raise contract.CompositionError(
            f"codex launch: profile.mutating={profile.mutating} inconsistent with "
            f"effective_grants={profile.effective_grants!r} — refusing to compose")
    if profile is not None and profile.mutating:
        if not req.containment_root:
            raise contract.CompositionError(
                "codex mutating launch REFUSED: AdapterRequest.containment_root is required "
                "(executor-approved root; a mutating launch without it is the /tmp-wide hazard)")
        cmd = build_mutating_command(req.requested_model, profile.worktree,
                                     effort=effective_effort, containment_root=req.containment_root)
    else:
        cmd = build_command(req.requested_model, work, effort=effective_effort)
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
