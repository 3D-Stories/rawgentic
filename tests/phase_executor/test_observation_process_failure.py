"""#733 — contract.observation_process_failure: the pure process-success predicate.

Success is an explicit ALLOWLIST (parse_status in {ok, usage_unavailable}), deny-by-default.
Precedence within a failing observation: timeout evidence, then any non-zero integer
exit_code (signalled when negative, nonzero_exit when positive), then the non-allowlisted
status string. Malformed process/exit_code fields read as no-signal from THAT field; a
malformed (non-string) parse_status FAILS with "malformed_status" — the allowlist governs
status, so malformed evidence can never manufacture a success. Never raises.
"""
import pytest

from phase_executor import contract


def _obs_dict(status="ok", exit_code=0, timed_out=False, payload="x", actual="claude-sonnet-5",
              process="__default__"):
    proc = {"exit_code": exit_code, "timed_out": timed_out} if process == "__default__" else process
    return {"parse_status": status, "process": proc, "parsed_payload": payload,
            "actual_model": actual, "requested_model": "claude-sonnet-5"}


@pytest.mark.parametrize("status,exit_code,timed_out,expected", [
    # allowlist: the only two success states
    ("ok", 0, False, None),
    ("ok", None, False, None),
    ("usage_unavailable", 0, False, None),
    # timeout evidence wins first
    ("timeout", 0, False, "timeout"),
    ("ok", 0, True, "timeout"),                 # contradictory envelope: timed_out beats claimed ok
    ("nonzero_exit", -9, True, "timeout"),      # precedence: timeout > signalled
    # any non-zero integer exit fails, sign picks the label
    ("nonzero_exit", -9, False, "signalled"),
    ("nonzero_exit", 2, False, "nonzero_exit"),
    ("ok", 2, False, "nonzero_exit"),           # contradictory envelope: non-zero exit beats claimed ok
    ("ok", -9, False, "signalled"),
    ("usage_unavailable", 137, False, "nonzero_exit"),
    # deny-by-default: every other status fails as itself
    ("parse_error", 0, False, "parse_error"),
    ("no_response", None, False, "no_response"),
    ("launch_error", None, False, "launch_error"),
    ("identity_failure", 0, False, "identity_failure"),
    ("harness_error", None, False, "harness_error"),
])
def test_status_by_exit_matrix(status, exit_code, timed_out, expected):
    assert contract.observation_process_failure(
        _obs_dict(status=status, exit_code=exit_code, timed_out=timed_out)) == expected


@pytest.mark.parametrize("status", [None, ["timeout"], {"s": "ok"}, 7])
def test_malformed_parse_status_fails_closed(status):
    # deny-by-default: a non-string status is not an allowlisted success value (Step-6 REOPENS)
    assert contract.observation_process_failure(_obs_dict(status=status)) == "malformed_status"


@pytest.mark.parametrize("exit_code", ["-9", "2", 1.5])
def test_malformed_exit_code_is_no_signal_from_that_field(exit_code):
    # ok status + garbage exit evidence -> the allowlisted status governs (never raises)
    assert contract.observation_process_failure(_obs_dict(exit_code=exit_code)) is None
    # non-allowlisted status still fails as itself (the garbage exit just can't relabel it)
    assert contract.observation_process_failure(
        _obs_dict(status="parse_error", exit_code=exit_code)) == "parse_error"


def test_bool_exit_code_is_not_an_int_signal():
    # bool is an int subclass — explicitly excluded; True must not read as exit 1
    assert contract.observation_process_failure(_obs_dict(exit_code=True)) is None


@pytest.mark.parametrize("process", [None, "boom", ["x"], 3])
def test_malformed_process_is_no_signal(process):
    assert contract.observation_process_failure(_obs_dict(process=process)) is None
    assert contract.observation_process_failure(
        _obs_dict(status="timeout", process=process)) == "timeout"


def test_absent_fields_never_raise():
    assert contract.observation_process_failure({}) == "malformed_status"
    assert contract.observation_process_failure({"parse_status": "ok"}) is None


def test_accepts_observation_object_form():
    obs = contract.Observation(
        run_id="r", attempt_id="0-x", correlation_id="c", seat="review", engine="claude",
        transport="native", requested_model="m", actual_model="m", prompt_hash="sha256:x",
        context_hashes=[], usage=None, timing_ms=1, queued_ms=0,
        process={"exit_code": -9, "timed_out": True}, parse_status="timeout",
        parsed_payload="partial text", raw_capture_path="/cap", fallback_reason=None,
        routing_config_digest="sha256:d")
    assert contract.observation_process_failure(obs) == "timeout"


def test_mapping_proxy_process_is_read_as_evidence():
    # 8a R1-M2: a non-dict Mapping carrying failure evidence must not be ignored
    from types import MappingProxyType
    obs = MappingProxyType({"parse_status": "ok",
                            "process": MappingProxyType({"exit_code": -9, "timed_out": False}),
                            "parsed_payload": "x"})
    assert contract.observation_process_failure(obs) == "signalled"


def test_observation_with_malformed_process_never_raises():
    # 8a R1-M2: Observation(process=None) would raise inside to_dict(); the predicate must not
    obs = contract.Observation(
        run_id="r", attempt_id="0-x", correlation_id="c", seat="review", engine="claude",
        transport="native", requested_model="m", actual_model="m", prompt_hash="sha256:x",
        context_hashes=[], usage=None, timing_ms=1, queued_ms=0,
        process=None, parse_status="timeout",
        parsed_payload=None, raw_capture_path=None, fallback_reason=None,
        routing_config_digest="sha256:d")
    assert contract.observation_process_failure(obs) == "timeout"


# -- Step-11 R2-H1 (#733): usage_unavailable requires a payload on the READER side too --
# The producer fix (adapters/base.py: output checked before usage) covers only newly produced
# observations; legacy/in-flight producers (pre-reorder caches) can still emit
# usage_unavailable with nothing produced. The predicate must not bless a no-output success.

def test_733_s11_usage_unavailable_without_payload_is_no_response():
    assert contract.observation_process_failure(
        _obs_dict(status="usage_unavailable", payload=None)) == "no_response"
    # bare dict form (no parsed_payload key at all) is the same no-output shape
    assert contract.observation_process_failure(
        {"parse_status": "usage_unavailable"}) == "no_response"


@pytest.mark.parametrize("payload", ["", 0, False, {}, []])
def test_733_s11_usage_unavailable_with_falsy_payload_is_success(payload):
    # empty containers / "" / 0 / False ARE payloads (the AC4 partial rule's own definition)
    assert contract.observation_process_failure(
        _obs_dict(status="usage_unavailable", payload=payload)) is None


def test_733_s11_ok_without_payload_stays_success():
    # parse_status "ok" asserts the adapter parsed real output — payload-independence unchanged
    assert contract.observation_process_failure(_obs_dict(status="ok", payload=None)) is None


def test_733_s11_usage_unavailable_observation_form_requires_payload():
    obs = contract.Observation(
        run_id="r", attempt_id="0-x", correlation_id="c", seat="review", engine="claude",
        transport="native", requested_model="m", actual_model="m", prompt_hash="sha256:x",
        context_hashes=[], usage=None, timing_ms=1, queued_ms=0,
        process={"exit_code": 0, "timed_out": False}, parse_status="usage_unavailable",
        parsed_payload=None, raw_capture_path=None, fallback_reason=None,
        routing_config_digest="sha256:d")
    assert contract.observation_process_failure(obs) == "no_response"
