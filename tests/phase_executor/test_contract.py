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


# --- #469 W6 Task 2: work_product typed field (schema shape; derivation lives in test_work_product) ---

def _wp(**over):
    wp = {
        "kind": "code",
        "worktree_path": "/wt/run/seat/att",
        "base_sha": "a" * 40,
        "head_sha": "a" * 40,
        "content_tree_sha": "b" * 40,
        "changed_paths": ["src/x.py"],
        "documents": [],
        "tests": [{"command_digest": "sha256:t", "status": "passed", "exit_code": 0,
                   "report_ref": "runs/r/t.json"}],
        "promotion_status": "not_attempted",
    }
    wp.update(over)
    return wp


def test_work_product_omitted_when_absent():
    """Optional-additive: absent when unset (the canary_result/effort precedent)."""
    d = _obs_ok().to_dict()
    assert "work_product" not in d
    contract.validate_observation(d)


def test_work_product_emitted_and_validates_when_set():
    d = _obs_ok(work_product=_wp()).to_dict()
    assert d["work_product"] == _wp()
    contract.validate_observation(d)


def test_work_product_empty_arrays_valid_for_no_change_seat():
    """A failed/no-change seat has empty changed_paths/documents/tests (peer: allow empty)."""
    d = _obs_ok(work_product=_wp(changed_paths=[], documents=[], tests=[])).to_dict()
    contract.validate_observation(d)


def test_work_product_unknown_kind_rejected():
    d = _obs_ok(work_product=_wp(kind="wombat")).to_dict()
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(d)


def test_work_product_bad_test_status_rejected():
    d = _obs_ok(work_product=_wp(tests=[{"command_digest": "x", "status": "bogus",
                                         "exit_code": 0, "report_ref": "r"}])).to_dict()
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(d)


def test_work_product_missing_test_key_rejected():
    d = _obs_ok(work_product=_wp(tests=[{"command_digest": "x", "status": "passed",
                                         "exit_code": 0}])).to_dict()  # no report_ref
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(d)


def test_work_product_test_exit_code_null_ok_but_string_rejected():
    ok = _obs_ok(work_product=_wp(tests=[{"command_digest": "x", "status": "errored",
                                          "exit_code": None, "report_ref": "r"}])).to_dict()
    contract.validate_observation(ok)  # int-or-null: null accepted
    bad = _obs_ok(work_product=_wp(tests=[{"command_digest": "x", "status": "errored",
                                           "exit_code": "0", "report_ref": "r"}])).to_dict()
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(bad)


def test_work_product_bad_promotion_status_rejected():
    d = _obs_ok(work_product=_wp(promotion_status="maybe")).to_dict()
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(d)


def test_work_product_extra_key_rejected():
    d = _obs_ok(work_product={**_wp(), "sneaky": 1}).to_dict()
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(d)


def test_work_product_empty_digest_string_rejected():
    d = _obs_ok(work_product=_wp(base_sha="")).to_dict()
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(d)


# --- #469 W6 Task 3: new AC-I1 telemetry fields (typed optional; POPULATION deferred to #470) ---

_I1_FIELDS = ("session_policy", "worktree_id", "tmux_session", "budget", "hook_denials")


def test_i1_fields_omitted_when_absent():
    """All new I1 fields are optional-additive: absent when unset (the canary_result precedent)."""
    d = _obs_ok().to_dict()
    for f in _I1_FIELDS:
        assert f not in d
    contract.validate_observation(d)


@pytest.mark.parametrize("policy", ["fresh", "resume"])
def test_session_policy_set_and_validates(policy):
    d = _obs_ok(session_policy=policy).to_dict()
    assert d["session_policy"] == policy
    contract.validate_observation(d)


def test_session_policy_bad_value_rejected():
    d = _obs_ok(session_policy="paused").to_dict()
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(d)


def test_worktree_id_set_and_empty_rejected():
    d = _obs_ok(worktree_id="run1/build/0-aaaa").to_dict()
    assert d["worktree_id"] == "run1/build/0-aaaa"
    contract.validate_observation(d)
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(_obs_ok(worktree_id="").to_dict())


def test_tmux_session_set_and_empty_rejected():
    d = _obs_ok(tmux_session="pe-run1-build-0").to_dict()
    assert d["tmux_session"] == "pe-run1-build-0"
    contract.validate_observation(d)
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(_obs_ok(tmux_session="").to_dict())


def test_budget_set_and_validates():
    d = _obs_ok(budget={"reserved_usd": 5.0, "spent_usd": 1.25}).to_dict()
    assert d["budget"] == {"reserved_usd": 5.0, "spent_usd": 1.25}
    contract.validate_observation(d)
    contract.validate_observation(_obs_ok(budget={"reserved_usd": 0, "spent_usd": 0}).to_dict())


@pytest.mark.parametrize("bad", [
    {"reserved_usd": -1, "spent_usd": 0},
    {"reserved_usd": 0, "spent_usd": -0.01},
    {"reserved_usd": 1.0},                       # missing spent_usd
    {"reserved_usd": 1.0, "spent_usd": 0.0, "x": 1},  # extra key
    {"reserved_usd": "1", "spent_usd": 0},       # non-number
])
def test_budget_malformed_rejected(bad):
    d = _obs_ok(budget=bad).to_dict()
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(d)


def test_hook_denials_set_and_validates():
    for n in (0, 3):
        d = _obs_ok(hook_denials=n).to_dict()
        assert d["hook_denials"] == n
        contract.validate_observation(d)


@pytest.mark.parametrize("bad", [-1, 1.5, "2", True])
def test_hook_denials_malformed_rejected(bad):
    d = _obs_ok(hook_denials=bad).to_dict()
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(d)


def test_all_i1_fields_and_work_product_roundtrip_together():
    d = _obs_ok(session_policy="resume", worktree_id="w", tmux_session="t",
                budget={"reserved_usd": 2, "spent_usd": 1}, hook_denials=0,
                work_product=_wp()).to_dict()
    for f in _I1_FIELDS + ("work_product",):
        assert f in d
    contract.validate_observation(d)


@pytest.mark.parametrize("field_kwargs", [
    {"session_policy": "fresh"},
    {"worktree_id": "w"},
    {"tmux_session": "t"},
    {"budget": {"reserved_usd": 1, "spent_usd": 0}},
    {"hook_denials": 1},
])
def test_i1_field_on_v1_document_rejected(field_kwargs):
    """Freeze proof: each v2-only I1 field on a doc DECLARING "1" is rejected (dispatch -> frozen
    v1, which has no such property)."""
    d = _obs_ok(**field_kwargs).to_dict()
    d["schema_version"] = "1"
    with pytest.raises(jsonschema.ValidationError):
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


# --- #465 T2: LaunchProfile + profile_from_manifest ---

def _manifest(grants=("read",), policy="fresh", bounds=None):
    return {"session_policy": policy, "tool_grants": list(grants),
            "effort": "high", "confinement": {"anthropic": "hooks"},
            "bounds": bounds or {"timeout_s": 900}}


class TestLaunchProfile:
    def test_default_profile_is_fresh_readonly(self):
        from phase_executor import contract
        p = contract.LaunchProfile()
        assert p.session_policy == "fresh" and p.mutating is False
        assert p.effective_grants == () and p.worktree is None

    def test_effective_grants_not_constructor_injectable(self):
        from phase_executor import contract
        import pytest as _pt
        with _pt.raises(TypeError):
            contract.LaunchProfile(effective_grants=("bash",))

    def test_readonly_derivation(self):
        from phase_executor import contract
        p = contract.profile_from_manifest(_manifest(), engine="claude")
        assert p.mutating is False and p.effective_grants == ("read",)

    def test_bash_closure_implies_net(self):
        from phase_executor import contract
        p = contract.profile_from_manifest(
            _manifest(grants=("read", "edit", "bash"), bounds={"timeout_s": 60, "max_budget_usd": 5.0}),
            engine="claude", worktree="/tmp/wt")
        assert p.mutating is True
        assert set(p.effective_grants) == {"read", "edit", "bash", "net"}
        assert p.tool_grants == ("read", "edit", "bash")  # declared set unchanged

    @pytest.mark.parametrize("policy", ["", "Fresh", "resumable", None])
    def test_bad_session_policy_refuses(self, policy):
        from phase_executor import contract
        m = _manifest()
        if policy is None:
            del m["session_policy"]
        else:
            m["session_policy"] = policy
        with pytest.raises(ValueError, match="session_policy"):
            contract.profile_from_manifest(m, engine="claude")

    def test_resume_policy_accepted(self):
        from phase_executor import contract
        p = contract.profile_from_manifest(_manifest(policy="resume"), engine="claude")
        assert p.session_policy == "resume"

    def test_mutating_requires_worktree(self):
        from phase_executor import contract
        with pytest.raises(ValueError, match="worktree"):
            contract.profile_from_manifest(
                _manifest(grants=("edit",), bounds={"timeout_s": 60, "max_budget_usd": 5.0}),
                engine="claude")

    def test_mutating_claude_requires_budget(self):
        from phase_executor import contract
        with pytest.raises(ValueError, match="max_budget_usd"):
            contract.profile_from_manifest(_manifest(grants=("edit",)), engine="claude", worktree="/tmp/wt")

    def test_mutating_codex_no_budget_needed(self):
        from phase_executor import contract
        p = contract.profile_from_manifest(_manifest(grants=("edit",)), engine="codex", worktree="/tmp/wt")
        assert p.mutating is True and p.max_budget_usd is None

    def test_mutating_zhipuai_refuses(self):
        from phase_executor import contract
        with pytest.raises(ValueError, match="zhipuai"):
            contract.profile_from_manifest(_manifest(grants=("edit",)), engine="zhipuai", worktree="/tmp/wt")

    def test_bad_engine_refuses(self):
        from phase_executor import contract
        with pytest.raises(ValueError, match="engine"):
            contract.profile_from_manifest(_manifest(), engine="zhipu")

    def test_adapter_request_gains_defaulted_fields(self):
        from phase_executor.adapters.base import AdapterRequest
        req = AdapterRequest(seat="ship", requested_model="claude-sonnet-5", prompt="hi")
        assert req.profile.session_policy == "fresh" and req.profile.mutating is False
        assert req.containment_root is None


def test_manifest_schema_accepts_max_budget_usd():
    """#465 8a-A1: the field profile_from_manifest reads must be schema-SPECIFIABLE
    (bounds was additionalProperties:false with only timeout_s — the budget path was
    dead against any schema-valid manifest); positivity enforced at the schema layer."""
    import copy, json as _json, pathlib
    from phase_executor import contract, routing
    table = _json.loads(routing.default_table_path().read_text(encoding="utf-8"))
    t = copy.deepcopy(table)
    t["seats"]["build"]["manifest"]["bounds"]["max_budget_usd"] = 25.0
    contract.validate_routing_table(t)  # accepts
    t["seats"]["build"]["manifest"]["bounds"]["max_budget_usd"] = 0
    import pytest as _pt, jsonschema
    with _pt.raises(jsonschema.ValidationError):
        contract.validate_routing_table(t)  # zero refused at schema layer


def test_mutating_claude_manifest_round_trips_schema_to_profile():
    """#465 8a-B1: the positive mutating-claude derivation proven on a manifest that
    ACTUALLY round-trips validate_routing_table (guards against the schema edit being
    forgotten while raw-dict tests stay green)."""
    import copy, json as _json
    from phase_executor import contract, routing
    table = _json.loads(routing.default_table_path().read_text(encoding="utf-8"))
    t = copy.deepcopy(table)
    t["seats"]["build"]["manifest"]["bounds"]["max_budget_usd"] = 25.0
    contract.validate_routing_table(t)
    p = contract.profile_from_manifest(t["seats"]["build"]["manifest"],
                                       engine="claude", worktree="/tmp/wt")
    assert p.mutating is True and p.max_budget_usd == 25.0


def test_infinite_budget_refused():
    """#465 8a-B2: inf satisfies `> 0` but is no enforceable ceiling."""
    import pytest as _pt
    from phase_executor import contract
    m = {"session_policy": "fresh", "tool_grants": ["edit"], "effort": "high",
         "confinement": {"anthropic": "hooks"},
         "bounds": {"timeout_s": 60, "max_budget_usd": float("inf")}}
    with _pt.raises(ValueError, match="max_budget_usd"):
        contract.profile_from_manifest(m, engine="claude", worktree="/tmp/wt")


def test_profile_from_manifest_rejects_empty_grants():
    import pytest as _pt
    from phase_executor import contract
    m = {"session_policy": "fresh", "tool_grants": [], "effort": "high",
         "confinement": {}, "bounds": {"timeout_s": 60}}
    with _pt.raises(ValueError, match="non-empty"):
        contract.profile_from_manifest(m, engine="claude")


def test_profile_from_manifest_rejects_unknown_grant():
    import pytest as _pt
    from phase_executor import contract
    m = {"session_policy": "fresh", "tool_grants": ["read", "sudo"], "effort": "high",
         "confinement": {}, "bounds": {"timeout_s": 60}}
    with _pt.raises(ValueError, match="unknown tool_grants"):
        contract.profile_from_manifest(m, engine="claude")


def test_canonical_contained_worktree_shared_boundary(tmp_path):
    import pytest as _pt, os as _os
    from phase_executor import contract
    root = tmp_path / "root"; wt = root / "wt"; wt.mkdir(parents=True)
    assert contract.canonical_contained_worktree(str(wt), str(root)) == _os.path.realpath(str(wt))
    with _pt.raises(contract.CompositionError, match="containment_root is required"):
        contract.canonical_contained_worktree(str(wt), None)
    with _pt.raises(contract.CompositionError, match="containment"):
        contract.canonical_contained_worktree(str(root), str(root))  # == root
    with _pt.raises(contract.CompositionError, match="absolute"):
        contract.canonical_contained_worktree("rel/wt", str(root))


def test_worktree_nul_byte_refused_via_composition_error(tmp_path):
    # #465 Step-11 R2-F3: a NUL in the worktree must refuse via CompositionError (the raw-
    # string unsafe-char check runs BEFORE realpath, which would otherwise raise ValueError).
    import pytest as _pt
    from phase_executor import contract
    with _pt.raises(contract.CompositionError, match="unsafe"):
        contract.canonical_contained_worktree(str(tmp_path) + "/a\x00b", str(tmp_path))


def test_containment_root_filesystem_root_rejected():
    # #465 Step-11 R2-F4: containment_root == '/' would refuse every worktree — reject it
    # with a clear message instead of a confusing "fails containment under '/'".
    import pytest as _pt
    from phase_executor import contract
    with _pt.raises(contract.CompositionError, match="filesystem root"):
        contract.canonical_contained_worktree("/tmp/anything/wt", "/")
