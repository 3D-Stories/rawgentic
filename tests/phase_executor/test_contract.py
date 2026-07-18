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


# --- #465 T1: effort gate + stepdown + Observation.effort ---

class TestResolveEffort:
    def test_identity_all_levels_claude(self):
        from phase_executor import contract
        for lvl in ("low", "medium", "high", "xhigh", "max"):
            r = contract.resolve_effort("claude-opus-4-8", lvl, engine="claude")
            assert (r.requested, r.native, r.resolution) == (lvl, lvl, "identity")

    def test_gpt55_max_steps_down_to_xhigh(self):
        from phase_executor import contract
        r = contract.resolve_effort("gpt-5.5", "max", engine="codex")
        assert (r.native, r.resolution) == ("xhigh", "stepdown")
        assert r.requested == "max" and isinstance(r.capability_revision, int)

    def test_gpt56_sol_max_identity(self):
        from phase_executor import contract
        r = contract.resolve_effort("gpt-5.6-sol", "max", engine="codex")
        assert (r.native, r.resolution) == ("max", "identity")

    def test_codex_none_is_adapter_default_high(self):
        from phase_executor import contract
        r = contract.resolve_effort("gpt-5.6-terra", None, engine="codex")
        assert (r.requested, r.native, r.resolution) == (None, "high", "adapter_default")

    def test_claude_none_identity_null(self):
        from phase_executor import contract
        r = contract.resolve_effort("claude-fable-5", None, engine="claude")
        assert (r.requested, r.native, r.resolution) == (None, None, "identity")

    def test_unknown_model_requested_refuses(self):
        from phase_executor import contract
        import pytest as _pt
        with _pt.raises(ValueError, match="no capability row"):
            contract.resolve_effort("wombat-9", "high", engine="codex")

    def test_unknown_model_none_passes(self):
        from phase_executor import contract
        r = contract.resolve_effort("wombat-9", None, engine="claude")
        assert (r.native, r.resolution) == (None, "identity")

    def test_bad_engine_refuses(self):
        from phase_executor import contract
        import pytest as _pt
        with _pt.raises(ValueError, match="engine"):
            contract.resolve_effort("claude-opus-4-8", "high", engine="zhipu")

    def test_registry_covers_shipped_table(self):
        from phase_executor import contract, model_capabilities, routing
        table = routing.load_routing_table(routing.default_table_path())
        models = set()
        for seat in table["seats"].values():
            for t in (seat["primary"], *seat.get("chain", [])):
                models.add(contract.canonicalize_model_id(t["model"]))
        missing = models - set(model_capabilities.SUPPORTED_EFFORT)
        assert not missing, f"registry lacks rows for shipped models: {sorted(missing)}"

    def test_observation_effort_round_trips_schema(self, tmp_path):
        import json as _json
        from phase_executor import contract
        obs = contract.Observation(
            run_id="r", attempt_id="0-a", correlation_id=None, seat="ship", engine="codex",
            transport="native", requested_model="gpt-5.6-terra", actual_model="gpt-5.6-terra",
            prompt_hash="sha256:x", context_hashes=[], usage={"input": 1, "output": 1, "cached": 0},
            timing_ms=1, queued_ms=0, process={"exit_code": 0, "timed_out": False},
            parse_status=contract.OK, parsed_payload="t", raw_capture_path=None,
            fallback_reason=None, routing_config_digest="sha256:d",
            effort={"requested": None, "native": "high", "resolution": "adapter_default",
                    "capability_revision": 1},
        )
        d = obs.to_dict()
        contract.validate_observation(d)  # schema accepts the new optional object
        assert d["effort"]["native"] == "high"

    def test_observation_without_effort_still_validates(self):
        from phase_executor import contract
        obs = contract.Observation(
            run_id="r", attempt_id="0-a", correlation_id=None, seat="ship", engine="claude",
            transport="native", requested_model="claude-sonnet-5", actual_model="claude-sonnet-5",
            prompt_hash="sha256:x", context_hashes=[], usage={"input": 1, "output": 1, "cached": 0},
            timing_ms=1, queued_ms=0, process={"exit_code": 0, "timed_out": False},
            parse_status=contract.OK, parsed_payload="t", raw_capture_path=None,
            fallback_reason=None, routing_config_digest="sha256:d",
        )
        d = obs.to_dict()
        assert "effort" not in d  # emit-only-when-set
        contract.validate_observation(d)
