"""Test-only pane adapter (injected via RAWGENTIC_PANE_ADAPTER) for supervisor
integration tests over a REAL tmux private socket. Behavior via RAWGENTIC_STUB_MODE:

- ok             — write a schema-valid observation, finalize, exit
- sleep          — never write anything; sleep (timeout / kill paths)
- ok_then_sleep  — write a valid observation, then sleep (timeout-race: child obs wins)
- exit_nonzero   — raise (pane_runner exits 1, no sentinel)
- malformed      — write a NON-schema observation.json, finalize
- provider_sleep — spawn a start_new_session 'provider' that sleeps, then sleep
                   (two-group kill / verify-dead paths)
- resume_ok      — like ok, plus a transport.stdout.txt carrying a claude-shaped
                   {"session_id": $RAWGENTIC_STUB_SESSION_ID} (resume-identity assert)
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from phase_executor import contract
from phase_executor.capture import create_capture, hash_text


def _valid_obs(req, run_id: str, attempt_id: str) -> dict:
    obs = contract.Observation(
        run_id=run_id, attempt_id=attempt_id, correlation_id=None, seat=req.seat,
        engine="claude", transport="native", requested_model=req.requested_model,
        actual_model=req.requested_model, prompt_hash=hash_text(req.prompt),
        context_hashes=[], usage={"input": 1, "output": 1, "cached": 0}, timing_ms=1,
        queued_ms=0, process={"exit_code": 0, "timed_out": False},
        parse_status=contract.OK, parsed_payload="stub", raw_capture_path=None,
        fallback_reason=None, routing_config_digest="sha256:stub")
    d = obs.to_dict()
    contract.validate_observation(d)
    return d


def run(req, *, run_id, attempt_id, capture_root, routing_config_digest,
        queued_ms=0, fallback_reason=None, **kw):
    mode = os.environ.get("RAWGENTIC_STUB_MODE", "ok")
    if mode == "exit_nonzero":
        raise RuntimeError("stub: provider failed")
    if mode == "sleep":
        time.sleep(300)
    if mode == "provider_sleep":
        subprocess.Popen([sys.executable, "-c", "import time;time.sleep(300)"],
                         start_new_session=True)
        time.sleep(300)
    cap = create_capture(capture_root, run_id, req.seat, attempt_id)
    cap.write_input(req.prompt)
    if mode == "resume_ok":
        import json as _json
        cap.write_transport(_json.dumps(
            {"session_id": os.environ.get("RAWGENTIC_STUB_SESSION_ID", "sess-1"),
             "result": "stub"}))
    if mode == "malformed":
        cap.write_observation({"not": "an observation"})
    else:
        cap.write_observation(_valid_obs(req, run_id, attempt_id))
    cap.finalize()
    if mode == "ok_then_sleep":
        time.sleep(300)
    return None
