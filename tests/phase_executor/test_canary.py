"""#468 W5 — fail-closed guardrail canary unit tests.

Covers each check's pass + every refuse path with its EXACT stable tag; evaluate_canary
determinism + never-raises; require_canary composition-binding (a Claude composition cannot run
codex_mutating), evidence replay-rejection, CanaryRefused carrying result+tags; the pass_summary
== 8-key-set == schema property set; the digest byte-redistribution collision; per-mutating-class
coverage; and the additive canary_result Observation round-trip.
"""
import json
import pathlib

import jsonschema
import pytest

from phase_executor import canary, contract
from phase_executor.adapters import codex_cli
from phase_executor.adapters.base import AdapterRequest, ParsedResult, ProcOutcome, build_observation

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
HOOKS = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))

_BASH = "Bash"
_EDIT = "Edit|Write|MultiEdit|NotebookEdit"


# --------------------------------------------------------------------------- builders
def _probe(tool, cid, marker, **over):
    base = dict(issued_tool=tool, issued_correlation_id=cid, observed_tool=tool,
                observed_correlation_id=cid, denied=True, executed=False,
                deny_reason=f"{marker} probe denied", transport_error=None)
    base.update(over)
    return canary.ProbeOutcome(**base)


def _claude_probes():
    return {
        _BASH: _probe("Bash", "cid-bash", "BLOCKED:"),
        _EDIT: _probe("Write", "cid-write", "SECURITY BLOCK:"),
    }


def _claude_evidence(**over):
    base = dict(
        provider="claude", profile="mutating", dispatch_nonce="N1", snapshot_digest="S1",
        registration_digest=canary.EXPECTED_REGISTRATION_DIGEST, registration_readable=True,
        plugin_version=canary.EXPECTED_PLUGIN_VERSION,
        init_plugins=["rawgentic@rawgentic"], hooks_registration=HOOKS, probes=_claude_probes(),
        final_argv=["claude", "-p", "--output-format", "stream-json"],
    )
    base.update(over)
    return canary.CanaryEvidence(**base)


def _codex_evidence(tmp_path, **over):
    root = tmp_path / "root"
    wt = root / "wt"
    wt.mkdir(parents=True)
    canon = contract.canonical_contained_worktree(str(wt), str(root))
    cmd = codex_cli.build_mutating_command("gpt-5.6-terra", str(wt), effort="low",
                                           containment_root=str(root))
    base = dict(provider="codex", profile="mutating", dispatch_nonce="N1", snapshot_digest="S1",
                codex_argv=cmd, codex_worktree=canon, codex_containment_root=str(root), final_argv=cmd)
    base.update(over)
    return canary.CanaryEvidence(**base)


def _claude_comp(mutating=True, nonce="N1", snap="S1"):
    prof = contract.LaunchProfile(session_policy="fresh", mutating=mutating)
    return canary.LaunchComposition(provider="claude", profile=prof, dispatch_nonce=nonce, snapshot_digest=snap)


# --------------------------------------------------------------------------- full pass
def test_claude_full_pass():
    res = canary.evaluate_canary("claude_mutating", _claude_evidence())
    assert res.verdict == "pass", res.violations
    assert res.violations == ()
    assert res.required_checks == ("hooks_digest", "plugin_version", "lane_provisioned",
                                   "positive_deny", "bare_absent")
    assert [c.check_id for c in res.checks if c.verdict == "pass"] == list(res.required_checks)


def test_codex_full_pass(tmp_path):
    res = canary.evaluate_canary("codex_mutating", _codex_evidence(tmp_path))
    assert res.verdict == "pass", res.violations
    assert res.required_checks == ("codex_containment", "bare_absent")


# --------------------------------------------------------------- per-check refuse paths
@pytest.mark.parametrize("over,tag", [
    (dict(registration_readable=False), "hooks_evidence_missing"),
    (dict(registration_digest=None), "hooks_evidence_missing"),
    (dict(registration_digest="sha256:wrong"), "hooks_digest_mismatch"),
])
def test_hooks_digest_refuse(over, tag):
    res = canary.evaluate_canary("claude_mutating", _claude_evidence(**over))
    assert res.verdict == "refuse"
    assert tag in res.violations


@pytest.mark.parametrize("ver", [None, "3.75.0", "9.9.9"])
def test_plugin_version_refuse(ver):
    res = canary.evaluate_canary("claude_mutating", _claude_evidence(plugin_version=ver))
    assert "plugin_version_mismatch" in res.violations


@pytest.mark.parametrize("plugins,tag", [
    (None, "init_evidence_invalid"),
    ("not-a-list", "init_evidence_invalid"),
    ([], "lane_unprovisioned"),
    (["other@other"], "lane_unprovisioned"),
])
def test_lane_provisioned_refuse(plugins, tag):
    res = canary.evaluate_canary("claude_mutating", _claude_evidence(init_plugins=plugins))
    assert tag in res.violations


def test_lane_provisioned_accepts_dict_entry():
    res = canary.evaluate_canary("claude_mutating",
                                 _claude_evidence(init_plugins=[{"name": "rawgentic@rawgentic"}]))
    assert res.verdict == "pass", res.violations


@pytest.mark.parametrize("argv,tag", [
    (None, "argv_evidence_invalid"),
    ([], "argv_evidence_invalid"),
    (["claude", 5], "argv_evidence_invalid"),
    (["claude", "-p", "--bare"], "bare_detected"),
])
def test_bare_absent_refuse(argv, tag):
    res = canary.evaluate_canary("claude_mutating", _claude_evidence(final_argv=argv))
    assert tag in res.violations


def test_codex_containment_refuse_missing(tmp_path):
    res = canary.evaluate_canary("codex_mutating", _codex_evidence(tmp_path, codex_argv=None))
    assert "codex_containment" in res.violations


def test_codex_containment_refuse_bad_composition(tmp_path):
    # a read-only argv is not a contained mutating composition -> validate_mutating_composition raises
    res = canary.evaluate_canary("codex_mutating",
                                 _codex_evidence(tmp_path, codex_argv=["codex", "exec", "-s", "read-only"]))
    assert res.violations == ("codex_containment",)  # stable tag, no leaked detail


# ------------------------------------------------------------- positive_deny coverage
def test_mutating_classes_derived_from_hooks():
    assert canary.mutating_guard_classes(HOOKS) == {
        "Bash": "wal-guard",
        "Edit|Write|MultiEdit|NotebookEdit": "security-guard.py",
    }


def test_positive_deny_missing_class_refuses():
    probes = {_BASH: _probe("Bash", "c", "BLOCKED:")}  # Edit class absent
    res = canary.evaluate_canary("claude_mutating", _claude_evidence(probes=probes))
    assert f"positive_deny_unproven:{_EDIT}" in res.violations


def test_positive_deny_executed_is_absent():
    probes = _claude_probes()
    probes[_EDIT] = _probe("Write", "c", "SECURITY BLOCK:", denied=False, executed=True)
    res = canary.evaluate_canary("claude_mutating", _claude_evidence(probes=probes))
    assert f"positive_deny_absent:{_EDIT}" in res.violations


@pytest.mark.parametrize("over", [
    dict(observed_correlation_id="mismatch"),   # uncorrelated
    dict(observed_tool="Read"),                 # tool not the issued one
    dict(issued_tool="Read"),                   # issued tool not in the class
    dict(deny_reason="denied but no marker"),   # non-hook-origin
    dict(transport_error="timeout"),            # transport failure
])
def test_positive_deny_unproven_variants(over):
    probes = _claude_probes()
    probes[_BASH] = _probe("Bash", "c", "BLOCKED:", **over)
    res = canary.evaluate_canary("claude_mutating", _claude_evidence(probes=probes))
    assert f"positive_deny_unproven:{_BASH}" in res.violations


def test_positive_deny_check_error_on_malformed_hooks():
    res = canary.evaluate_canary("claude_mutating", _claude_evidence(hooks_registration={"bad": "shape"}))
    assert "canary_check_error:positive_deny" in res.violations


# --------------------------------------------------------- evaluate_canary invariants
def test_unknown_policy_refuses():
    res = canary.evaluate_canary("nope", _claude_evidence())
    assert res.verdict == "refuse"
    assert res.violations == ("canary_policy_unknown",)
    assert res.required_checks == ()


def test_violations_deterministic_policy_order():
    ev = _claude_evidence(registration_digest="sha256:wrong", init_plugins=[],
                          probes={_BASH: _probe("Bash", "c", "BLOCKED:")})  # Edit class missing
    res = canary.evaluate_canary("claude_mutating", ev)
    # hooks_digest < plugin_version(pass) < lane_provisioned < positive_deny < bare_absent(pass)
    assert res.violations == ("hooks_digest_mismatch", "lane_unprovisioned",
                              f"positive_deny_unproven:{_EDIT}")
    assert canary.evaluate_canary("claude_mutating", ev).violations == res.violations  # deterministic


def test_evaluate_never_raises_on_garbage():
    for junk in (object(), canary.CanaryEvidence(provider="claude")):
        res = canary.evaluate_canary("claude_mutating", junk)
        assert res.verdict == "refuse"  # no exception escaped


# ------------------------------------------------------- require_canary composition binding
def test_require_canary_pass_returns_result():
    res = canary.require_canary(_claude_comp(), _claude_evidence())
    assert res.verdict == "pass"
    assert res.policy_id == "claude_mutating"


def test_claude_composition_cannot_run_codex_policy(tmp_path):
    # Claude composition + codex-style evidence: the policy is DERIVED from the composition
    # (claude_mutating), never codex_mutating — so the codex containment check is unreachable and
    # the claude checks fire on absent evidence.
    codex_ev = _codex_evidence(tmp_path, provider="claude")  # relabel provider to pass the provider-match
    with pytest.raises(canary.CanaryRefused) as ei:
        canary.require_canary(_claude_comp(), codex_ev)
    result = ei.value.result
    assert result.policy_id == "claude_mutating"
    assert "codex_containment" not in result.violations
    assert result.required_checks == ("hooks_digest", "plugin_version", "lane_provisioned",
                                       "positive_deny", "bare_absent")


def test_require_canary_provider_mismatch():
    with pytest.raises(canary.CanaryRefused) as ei:
        canary.require_canary(_claude_comp(), _claude_evidence(provider="codex"))
    assert ei.value.result.violations == ("canary_provider_mismatch",)


@pytest.mark.parametrize("mutating", [False])
def test_require_canary_policy_unknown_non_mutating(mutating):
    with pytest.raises(canary.CanaryRefused) as ei:
        canary.require_canary(_claude_comp(mutating=mutating), _claude_evidence())
    assert ei.value.result.violations == ("canary_policy_unknown",)


def test_require_canary_policy_unknown_bad_provider():
    prof = contract.LaunchProfile(session_policy="fresh", mutating=True)
    comp = canary.LaunchComposition(provider="zhipuai", profile=prof, dispatch_nonce="N1", snapshot_digest="S1")
    with pytest.raises(canary.CanaryRefused) as ei:
        canary.require_canary(comp, canary.CanaryEvidence(provider="zhipuai", dispatch_nonce="N1", snapshot_digest="S1"))
    assert ei.value.result.violations == ("canary_policy_unknown",)


@pytest.mark.parametrize("over", [
    dict(dispatch_nonce="N2"),          # replayed under a new nonce
    dict(snapshot_digest="S2"),         # config/env changed -> new snapshot
    dict(dispatch_nonce=None),          # missing nonce (fail-closed)
    dict(snapshot_digest=None),         # missing snapshot digest (fail-closed)
])
def test_require_canary_evidence_binding_mismatch(over):
    with pytest.raises(canary.CanaryRefused) as ei:
        canary.require_canary(_claude_comp(), _claude_evidence(**over))
    assert ei.value.result.violations == ("evidence_binding_mismatch",)


def test_require_canary_refuses_on_check_failure():
    with pytest.raises(canary.CanaryRefused) as ei:
        canary.require_canary(_claude_comp(), _claude_evidence(registration_digest="sha256:wrong"))
    assert "hooks_digest_mismatch" in ei.value.result.violations


def test_canary_refused_carries_result_and_tags():
    with pytest.raises(canary.CanaryRefused) as ei:
        canary.require_canary(_claude_comp(), _claude_evidence(registration_digest="sha256:wrong"))
    exc = ei.value
    assert isinstance(exc.result, canary.CanaryResult)
    assert isinstance(exc, contract.CompositionError)  # the established fail-closed refusal idiom
    s = str(exc)
    assert "hooks_digest_mismatch" in s
    assert f"rev{canary.POLICY_REVISION}" in s
    assert "claude_mutating" in s


# ----------------------------------------------------------- CanaryResult data model
def test_pass_summary_passed_equals_required_on_pass():
    res = canary.evaluate_canary("claude_mutating", _claude_evidence())
    ps = res.pass_summary()
    assert ps["passed_checks"] == list(res.required_checks)
    assert ps["violations"] == []
    assert ps["verdict"] == "pass"


def test_not_applicable_forbidden_for_required_check():
    with pytest.raises(ValueError):
        canary.CanaryResult(1, "claude_mutating", "claude", "mutating", "refuse",
                            ("hooks_digest",), (canary.CheckResult("hooks_digest", "not_applicable"),), ())


def test_pass_summary_keyset_equals_schema_property_set():
    res = canary.evaluate_canary("claude_mutating", _claude_evidence())
    schema_props = contract.observation_schema()["properties"]["canary_result"]["properties"]
    assert set(res.pass_summary().keys()) == set(schema_props.keys())


# --------------------------------------------------------- registration digest framing
def _write_two_script_root(root, a_bytes, b_bytes):
    (root / "hooks").mkdir(parents=True)
    manifest = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/a"},
        {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/b"},
    ]}]}}
    (root / "hooks" / "hooks.json").write_bytes(json.dumps(manifest).encode())
    (root / "hooks" / "a").write_bytes(a_bytes)
    (root / "hooks" / "b").write_bytes(b_bytes)


def test_digest_byte_redistribution_changes_framed_digest(tmp_path):
    r1 = tmp_path / "r1"
    r2 = tmp_path / "r2"
    # identical concatenation (a+b == "XXXXYYYYYY") but redistributed across the file boundary:
    _write_two_script_root(r1, b"XXXX", b"YYYYYY")
    _write_two_script_root(r2, b"XXXXY", b"YYYYY")
    d1 = canary.compute_registration_digest(r1)
    d2 = canary.compute_registration_digest(r2)
    assert d1 != d2  # the length framing defeats the boundary collision a naive concat would allow


def test_digest_rejects_path_outside_root(tmp_path):
    root = tmp_path / "root"
    (root / "hooks").mkdir(parents=True)
    manifest = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/../escape"},
    ]}]}}
    (root / "hooks" / "hooks.json").write_bytes(json.dumps(manifest).encode())
    with pytest.raises(ValueError):
        canary.compute_registration_digest(root)


def test_digest_pin_matches_live():
    assert canary.EXPECTED_REGISTRATION_DIGEST == canary.compute_registration_digest(REPO_ROOT)


# ----------------------------------------------- Observation canary_result round-trip
def _passing_result():
    return canary.evaluate_canary("claude_mutating", _claude_evidence())


def _ok_obs(canary_result=None):
    req = AdapterRequest(seat="ship", requested_model="claude-opus-4-8", prompt="hi")
    parsed = ParsedResult(text="OK", actual_model="claude-opus-4-8",
                          usage={"input": 1, "output": 1}, payload="OK")
    proc = ProcOutcome(returncode=0, stdout="", stderr="", timed_out=False)
    return build_observation(req=req, engine="claude", run_id="r", attempt_id="a", parsed=parsed,
                             proc=proc, timing_ms=1, queued_ms=0, raw_capture_path="/x",
                             routing_config_digest="sha256:d", canary_result=canary_result)


def test_observation_canary_result_present_roundtrips():
    res = _passing_result()
    d = _ok_obs(canary_result=res).to_dict()  # build_observation validates internally
    assert d["canary_result"] == res.pass_summary()
    contract.validate_observation(d)


def test_observation_canary_result_absent_when_unset():
    d = _ok_obs().to_dict()
    assert "canary_result" not in d
    contract.validate_observation(d)


def test_schema_rejects_extra_canary_key():
    d = _ok_obs().to_dict()
    d["canary_result"] = {**_passing_result().pass_summary(), "sneaky": 1}
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(d)


def test_schema_rejects_non_pass_verdict():
    d = _ok_obs().to_dict()
    d["canary_result"] = {**_passing_result().pass_summary(), "verdict": "refuse"}
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(d)


def test_schema_rejects_nonempty_violations():
    d = _ok_obs().to_dict()
    d["canary_result"] = {**_passing_result().pass_summary(), "violations": ["boom"]}
    with pytest.raises(jsonschema.ValidationError):
        contract.validate_observation(d)


def test_refuse_with_null_violation_is_not_dropped(monkeypatch):
    """Aggregation fail-open regression (Step-11 security lead): a check returning REFUSE with
    a null violation string must NOT be dropped from the verdict. Verdict is keyed off the check
    verdicts, not the accumulated violation list, and every refuse contributes a legible tag."""
    monkeypatch.setitem(canary._CHECKS, "codex_containment",
                        lambda ev: canary.CheckResult("codex_containment", canary.PASS))
    monkeypatch.setitem(canary._CHECKS, "bare_absent",
                        lambda ev: canary.CheckResult("bare_absent", canary.REFUSE, None))
    ev = canary.CanaryEvidence(provider="codex", profile="mutating")
    r = canary.evaluate_canary("codex_mutating", ev)
    assert r.verdict == canary.REFUSE, "a REFUSE check with a null violation was silently dropped (fail-open)"
    assert r.violations, "a refusing check must contribute a legible violation"
    assert any("bare_absent" in v for v in r.violations)


def test_require_canary_malformed_evidence_refuses_not_crashes():
    """Mechanical-review Medium: malformed evidence (a bare object missing the binding fields)
    whose provider matches must yield a structured CanaryRefused, never a raw AttributeError."""
    import types
    comp = _claude_comp()
    garbage = types.SimpleNamespace(provider="claude")  # no snapshot_digest / dispatch_nonce
    with pytest.raises(canary.CanaryRefused) as ei:
        canary.require_canary(comp, garbage)
    assert "evidence_binding_mismatch" in str(ei.value)


def test_require_canary_codex_full_pass(tmp_path):
    """Mechanical-review Low: the production entry point derives codex_mutating from a codex
    composition and passes end-to-end (composition-derivation + binding proven for codex too)."""
    prof = contract.LaunchProfile(session_policy="fresh", mutating=True)
    comp = canary.LaunchComposition(provider="codex", profile=prof, dispatch_nonce="N1", snapshot_digest="S1")
    result = canary.require_canary(comp, _codex_evidence(tmp_path))
    assert result.verdict == canary.PASS
    assert result.policy_id == "codex_mutating"
