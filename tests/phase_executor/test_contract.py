"""Task 2: contract.py — Observation producer conforms to the schema, canonical model-id
comparison (finding f2/f4), and fail-loud validation."""
import jsonschema
import pytest

from phase_executor import contract
from phase_executor.contract import Observation, canonicalize_model_id, models_match


def _obs_ok(**over):
    base = dict(
        run_id="r1", attempt_id="a1", seat="review", engine="claude", transport="native",
        requested_model="claude-opus-4-8", actual_model="claude-opus-4-8",
        prompt_hash="sha256:abc", usage={"input": 10, "output": 70, "cached": 5},
        timing_ms=100, queued_ms=0, process={"exit_code": 0, "timed_out": False},
        parse_status="ok", parsed_payload={"text": "OK"}, raw_capture_path="/runs/r1/a1",
        fallback_reason=None, routing_config_digest="sha256:deed",
    )
    base.update(over)
    return Observation(**base)


def test_ok_observation_roundtrips_and_validates():
    d = _obs_ok().to_dict()
    contract.validate_observation(d)  # no raise
    assert d["parse_status"] == "ok"
    assert "judge_degraded" not in d  # bool-only optional omitted when unset


def test_judge_degraded_emitted_when_set():
    d = _obs_ok(judge_degraded=True).to_dict()
    assert d["judge_degraded"] is True
    contract.validate_observation(d)


def test_correlation_id_present_as_null_when_absent():
    d = _obs_ok().to_dict()
    assert d["correlation_id"] is None
    contract.validate_observation(d)


def test_timeout_observation_validates_with_null_evidence():
    d = _obs_ok(
        parse_status="timeout", actual_model=None, usage=None,
        process={"exit_code": None, "timed_out": True}, parsed_payload=None,
    ).to_dict()
    contract.validate_observation(d)


def test_ok_with_null_actual_model_fails_validation():
    d = _obs_ok(actual_model=None).to_dict()
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(d)


@pytest.mark.parametrize("a,b", [
    ("claude-opus-4-8", "claude-opus-4-8[1m]"),
    ("claude-opus-4-8", "us.anthropic.claude-opus-4-8"),
    ("claude-haiku-4-5", "claude-haiku-4-5-20251001"),
    ("GLM-5.2", "glm-5.2"),
])
def test_canonicalize_treats_variants_as_equal(a, b):
    assert canonicalize_model_id(a) == canonicalize_model_id(b)
    assert models_match(a, b)


@pytest.mark.parametrize("a,b", [
    ("claude-opus-4-8", "claude-sonnet-5"),
    ("gpt-5.6-sol", "gpt-5.6-terra"),
])
def test_canonicalize_keeps_distinct_models_distinct(a, b):
    assert canonicalize_model_id(a) != canonicalize_model_id(b)
    assert not models_match(a, b)


def test_models_match_false_on_empty():
    assert not models_match(None, None)
    assert not models_match("", "")
    assert not models_match("claude-opus-4-8", None)


def test_routing_table_validator_accepts_shipped_default():
    import json
    import pathlib
    p = pathlib.Path(contract.__file__).resolve().parent / "routing" / "rawgentic.routing-table.json"
    contract.validate_routing_table(json.loads(p.read_text()))


def test_dispatched_lane_omitted_when_absent():
    """#425 B: backward-compat — absent when unset (kukakuka v1 parity, judge_degraded pattern)."""
    d = _obs_ok().to_dict()
    assert "dispatched_lane" not in d
    contract.validate_observation(d)


def test_dispatched_lane_emitted_and_validates_when_set():
    """#425 B: the executor stamps the actual dispatched lane; emitted + schema-valid."""
    lane = {"provider": "anthropic", "transport": "native", "auth_mode": "subscription_oauth",
            "pool": "claude", "credential_ref": None}
    d = _obs_ok(dispatched_lane=lane).to_dict()
    assert d["dispatched_lane"] == lane
    contract.validate_observation(d)
