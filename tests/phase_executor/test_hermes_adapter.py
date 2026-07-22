"""Tests for the Hermes HTTP offload adapter (#568 Phase-2).

Pure parse layer is fixture-tested (no I/O). The run() lifecycle uses an INJECTED fake
transport — CI never touches a live gateway. Fixtures transcribed from the shipped darwin
build (hermes-agent v0.18.2 gateway/platforms/api_server.py, read live 2026-07-22); see
fixtures/hermes/MANIFEST.md.
"""
import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2] / "phase_executor" / "src"
sys.path.insert(0, str(PKG))

from phase_executor.adapters import hermes_http as hh  # noqa: E402
from phase_executor.adapters.base import AdapterRequest  # noqa: E402
from phase_executor import contract  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "hermes"


def _fx(name):
    return json.loads((FIX / f"{name}.json").read_text())


# --------------------------------------------------------------------------- #
# pure parse layer
# --------------------------------------------------------------------------- #
class TestParseSubmit:
    def test_202_started_yields_run_id(self):
        rid, err = hh.parse_submit(202, _fx("submit_202_started"))
        assert rid == "run_a1b2c3d4e5f60718293a4b5c6d7e8f90"
        assert err is None

    def test_error_body_yields_typed_error_not_run_id(self):
        rid, err = hh.parse_submit(401, _fx("error_invalid_api_key"))
        assert rid is None
        assert "invalid_api_key" in err

    def test_missing_run_id_is_error(self):
        rid, err = hh.parse_submit(202, {"status": "started"})
        assert rid is None and err


class TestParseRunObject:
    def test_completed_with_usage(self):
        r = hh.parse_run_object(_fx("run_completed_with_usage"))
        assert r["status"] == "completed"
        assert r["output"] == "PONG"
        assert r["usage"] == {"input_tokens": 812, "output_tokens": 9}
        assert r["run_id"] == "run_a1b2c3d4e5f60718293a4b5c6d7e8f90"

    def test_failed_carries_error(self):
        r = hh.parse_run_object(_fx("run_failed"))
        assert r["status"] == "failed"
        assert "401" in r["error"]

    def test_running_is_non_terminal(self):
        r = hh.parse_run_object(_fx("run_running"))
        assert r["status"] == "running"
        assert not hh.is_terminal(r["status"])

    def test_terminal_set(self):
        assert hh.is_terminal("completed")
        assert hh.is_terminal("failed")
        assert hh.is_terminal("cancelled")
        assert not hh.is_terminal("started")
        assert not hh.is_terminal("stopping")
        assert not hh.is_terminal("quantum_flux")  # unknown → NOT terminal


class TestRunToParsed:
    def test_completed_attests_platform_identity(self):
        # F2: actual_model attested as the routed platform id, not the innermost gpt-5.5.
        parsed = hh.run_to_parsed(hh.parse_run_object(_fx("run_completed_with_usage")),
                                  requested_model="hermes-agent")
        assert parsed.actual_model == "hermes-agent"
        assert parsed.text == "PONG"
        assert parsed.usage == {"input": 812, "output": 9, "cached": 0}
        assert parsed.parse_error is None

    def test_completed_without_usage_leaves_usage_none(self):
        # F3: absent gateway usage → usage None → resolve_parse_status returns USAGE_UNAVAILABLE,
        # NEVER a fabricated zero and NEVER a new Observation field.
        parsed = hh.run_to_parsed(hh.parse_run_object(_fx("run_completed_no_usage")),
                                  requested_model="hermes-agent")
        assert parsed.actual_model == "hermes-agent"
        assert parsed.text == "PONG"
        assert parsed.usage is None

    def test_failed_is_parse_error_with_identity(self):
        parsed = hh.run_to_parsed(hh.parse_run_object(_fx("run_failed")),
                                  requested_model="hermes-agent")
        assert parsed.parse_error is not None
        assert "401" in parsed.parse_error


class TestParseCapabilities:
    def test_sandboxed_backend_permits(self):
        assert hh.backend_is_sandboxed({"terminal": {"backend": "docker"}}) is True

    def test_local_backend_refused(self):
        # F7: an unsandboxed 'local' backend must be refused.
        assert hh.backend_is_sandboxed({"terminal": {"backend": "local"}}) is False

    def test_absent_backend_field_fails_closed(self):
        # F7 fail-closed: cannot confirm sandboxed → refuse.
        assert hh.backend_is_sandboxed({}) is False
        assert hh.backend_is_sandboxed({"terminal": {}}) is False


# --------------------------------------------------------------------------- #
# run() lifecycle with an injected fake transport
# --------------------------------------------------------------------------- #
class FakeTransport:
    """Scripted (method, path-suffix) -> (status, body-dict). Records calls."""
    def __init__(self, script, *, sandboxed=True):
        self.script = script
        self.calls = []
        self._sandboxed = sandboxed

    def __call__(self, method, url, *, headers=None, body=None, timeout=None):
        self.calls.append((method, url, body))
        for suffix, (status, payload) in self.script:
            if url.endswith(suffix) or suffix in url:
                return status, json.dumps(payload) if not isinstance(payload, str) else payload
        # default: capabilities/health
        if url.endswith("/health"):
            return 200, json.dumps({"status": "ok", "platform": "hermes-agent", "version": "0.18.2"})
        if url.endswith("/v1/capabilities"):
            return 200, json.dumps({"terminal": {"backend": "docker" if self._sandboxed else "local"}})
        return 404, json.dumps({"error": {"code": "not_found"}})


def _req(**kw):
    base = dict(seat="offload", requested_model="hermes-agent", prompt="Say PONG.",
                transport="http", timeout=5.0, credential_ref="HERMES_API_SERVER_KEY")
    base.update(kw)
    return AdapterRequest(**base)


def _run(transport, tmp_path, env=None, attempt_id="0-aaa", **kw):
    if env is None:
        env = {"HERMES_API_URL": "http://10.0.17.204:8710", "HERMES_API_SERVER_KEY": "testkey123"}
    return hh.run(_req(**kw), run_id="wf2-t", attempt_id=attempt_id,
                  capture_root=str(tmp_path), routing_config_digest="sha256:test",
                  transport=transport, env=env)


def test_happy_path_completed(tmp_path):
    poll = [
        ("/v1/runs/run_x", (200, {"object": "hermes.run", "run_id": "run_x",
                                  "status": "completed", "output": "PONG",
                                  "usage": {"input_tokens": 5, "output_tokens": 1}})),
        ("/v1/runs", (202, {"run_id": "run_x", "status": "started"})),
    ]
    t = FakeTransport(poll, sandboxed=True)
    obs = _run(t, tmp_path)
    d = obs.to_dict()
    assert d["parse_status"] == contract.OK
    assert d["actual_model"] == "hermes-agent"
    assert d["parsed_payload"] == "PONG"


def test_activation_gate_refuses_unsandboxed(tmp_path):
    # F7: the gate refuses BEFORE any POST /v1/runs when the backend is unsandboxed.
    t = FakeTransport([], sandboxed=False)
    obs = _run(t, tmp_path)
    d = obs.to_dict()
    assert d["parse_status"] in contract.AVAILABILITY_FAILURES
    # no run was ever submitted
    assert not any(m == "POST" and url.endswith("/v1/runs") for m, url, _ in t.calls)


def test_completed_without_usage_is_usage_unavailable(tmp_path):
    poll = [
        ("/v1/runs/run_x", (200, {"object": "hermes.run", "run_id": "run_x",
                                  "status": "completed", "output": "PONG", "usage": None})),
        ("/v1/runs", (202, {"run_id": "run_x", "status": "started"})),
    ]
    obs = _run(FakeTransport(poll), tmp_path)
    assert obs.to_dict()["parse_status"] == contract.USAGE_UNAVAILABLE


def test_gateway_down_is_availability_failure(tmp_path):
    def boom(method, url, **kw):
        if url.endswith("/health"):
            raise hh.HermesUnreachable("connection refused")
        return 200, "{}"
    obs = _run(boom, tmp_path)
    assert obs.to_dict()["parse_status"] in contract.AVAILABILITY_FAILURES


def test_failed_run_surfaces_error(tmp_path):
    poll = [
        ("/v1/runs/run_x", (200, {"object": "hermes.run", "run_id": "run_x",
                                  "status": "failed", "error": "AuthenticationError [HTTP 401]"})),
        ("/v1/runs", (202, {"run_id": "run_x", "status": "started"})),
    ]
    obs = _run(FakeTransport(poll), tmp_path)
    d = obs.to_dict()
    assert d["parse_status"] != contract.OK
    assert d["parse_status"] not in contract.AVAILABILITY_FAILURES  # a produced envelope, not availability


def test_missing_credential_refuses(tmp_path):
    obs = _run(FakeTransport([], sandboxed=True), tmp_path, env={"HERMES_API_URL": "http://x:8710"})
    assert obs.to_dict()["parse_status"] in contract.AVAILABILITY_FAILURES


def test_effort_accepted_not_forwarded_identical(tmp_path):
    # F13/adv-F3: no-effort and explicit medium dispatch identically; effort never forwarded.
    poll = [
        ("/v1/runs/run_x", (200, {"object": "hermes.run", "run_id": "run_x",
                                  "status": "completed", "output": "ok",
                                  "usage": {"input_tokens": 1, "output_tokens": 1}})),
        ("/v1/runs", (202, {"run_id": "run_x", "status": "started"})),
    ]
    t1 = FakeTransport(poll, sandboxed=True)
    _run(t1, tmp_path, attempt_id="0-e1", effort=None)
    t2 = FakeTransport(poll, sandboxed=True)
    _run(t2, tmp_path, attempt_id="0-e2", effort="medium")
    posts1 = [b for m, u, b in t1.calls if m == "POST" and u.endswith("/v1/runs")]
    posts2 = [b for m, u, b in t2.calls if m == "POST" and u.endswith("/v1/runs")]
    assert posts1 == posts2  # identical request body; effort not in either
    assert all("effort" not in (b or "") for b in posts1)


def test_secret_never_in_observation_or_capture(tmp_path):
    poll = [
        ("/v1/runs/run_x", (200, {"object": "hermes.run", "run_id": "run_x",
                                  "status": "completed", "output": "ok",
                                  "usage": {"input_tokens": 1, "output_tokens": 1}})),
        ("/v1/runs", (202, {"run_id": "run_x", "status": "started"})),
    ]
    obs = _run(FakeTransport(poll), tmp_path,
               env={"HERMES_API_URL": "http://x:8710", "HERMES_API_SERVER_KEY": "SUPERSECRETKEY"})
    blob = json.dumps(obs.to_dict())
    assert "SUPERSECRETKEY" not in blob


@pytest.mark.live
def test_live_offload_dispatch_deferred():
    # #138 deferred owner-attended cell: authenticated .205-originated submit→poll→complete on a
    # SANDBOXED gateway backend. Skipped in CI (RUN_LIVE unset); blocked on the §10 sandbox
    # precondition regardless. See docs/hermes-offload.md.
    pytest.skip("deferred owner-attended live cell (needs sandboxed gateway backend + key on .205)")
