"""Task 1: JSON Schemas — meta-validity, the conditional ok-requires-evidence rule,
AC2 (kukakuka-shaped Observation), and routing-table validity incl. the shipped default."""
import copy
import json
import pathlib

import jsonschema
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "phase_executor" / "src" / "phase_executor" / "schemas"
ROUTING_DIR = REPO_ROOT / "phase_executor" / "src" / "phase_executor" / "routing"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def _load(path):
    return json.loads(path.read_text())


OBS_SCHEMA = _load(SCHEMA_DIR / "observation.schema.json")
RT_SCHEMA = _load(SCHEMA_DIR / "routing-table.schema.json")


def _obs_ok():
    return {
        "schema_version": "1",
        "run_id": "r1",
        "attempt_id": "a1",
        "seat": "review",
        "engine": "claude",
        "transport": "native",
        "requested_model": "claude-opus-4-8",
        "actual_model": "claude-opus-4-8",
        "prompt_hash": "sha256:abc",
        "context_hashes": [],
        "usage": {"input": 10, "output": 70, "cached": 5},
        "timing_ms": 100,
        "queued_ms": 0,
        "process": {"exit_code": 0, "timed_out": False},
        "parse_status": "ok",
        "parsed_payload": {"text": "OK"},
        "raw_capture_path": "/runs/r1/a1",
        "fallback_reason": None,
        "routing_config_digest": "sha256:deed",
    }


def test_schemas_are_meta_valid():
    jsonschema.Draft202012Validator.check_schema(OBS_SCHEMA)
    jsonschema.Draft202012Validator.check_schema(RT_SCHEMA)


def test_ok_observation_validates():
    jsonschema.validate(_obs_ok(), OBS_SCHEMA)


def test_ok_requires_nonnull_actual_model():
    bad = _obs_ok()
    bad["actual_model"] = None
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, OBS_SCHEMA)


def test_ok_requires_usage_with_input_output():
    bad = _obs_ok()
    bad["usage"] = None
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, OBS_SCHEMA)
    bad2 = _obs_ok()
    bad2["usage"] = {"cached": 1}  # missing input/output
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad2, OBS_SCHEMA)


def test_nonok_observation_may_have_null_evidence():
    """A pre-envelope timeout is recordable: null actual_model + null usage on a non-ok status."""
    obs = _obs_ok()
    obs["parse_status"] = "timeout"
    obs["actual_model"] = None
    obs["usage"] = None
    obs["process"] = {"exit_code": None, "timed_out": True}
    obs["parsed_payload"] = None
    jsonschema.validate(obs, OBS_SCHEMA)


def test_unknown_parse_status_rejected():
    bad = _obs_ok()
    bad["parse_status"] = "made_up"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, OBS_SCHEMA)


def test_extra_top_level_field_rejected():
    bad = _obs_ok()
    bad["surprise"] = 1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, OBS_SCHEMA)


def test_ac2_kukakuka_shaped_observation_validates():
    """AC2: a kukakuka-shaped Observation (CCR transport, proxied actual_model,
    turn-nonce correlation_id) validates against the committed schema."""
    obs = _load(FIXTURES / "kukakuka-observation.json")
    jsonschema.validate(obs, OBS_SCHEMA)
    assert obs["transport"] == "ccr"
    assert obs["correlation_id"].startswith("turn-nonce-")
    assert obs["actual_model"] == obs["requested_model"]  # proxied but innermost id reported


def test_shipped_routing_table_validates():
    table = _load(ROUTING_DIR / "rawgentic.routing-table.json")
    jsonschema.validate(table, RT_SCHEMA)


def test_routing_table_lane_missing_pool_rejected():
    table = _load(ROUTING_DIR / "rawgentic.routing-table.json")
    bad = copy.deepcopy(table)
    del bad["seats"]["review"]["primary"]["lane"]["pool"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, RT_SCHEMA)


def test_routing_table_requires_pools_and_seats():
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"schema_version": "1", "seats": {}}, RT_SCHEMA)
