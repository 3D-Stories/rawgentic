"""#470 W7 Task 2 — trusted two-phase canary evidence collector unit tests.

Phase 1 (local): registration_digest/plugin_version/hooks_registration/final_argv from a staged
snapshot dir + the composition's binding. Phase 2 (runtime): init_plugins + per-matcher-class
ProbeOutcome from an INJECTED probe-session stream. Both phases fail CLOSED — a malformed
snapshot or stream yields evidence that REFUSES at the owning canary check, never an exception
escaping the collector. The end-to-end proof is that a completed evidence satisfies
``require_canary``'s composition binding (provider/profile/nonce/digest echo the composition).
"""
import json
import pathlib

import pytest

from phase_executor import canary, canary_evidence, contract

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_BASH = "Bash"
_EDIT = "Edit|Write|MultiEdit|NotebookEdit"

_PLAN = {
    _BASH: {"issued_tool": "Bash", "issued_correlation_id": "cid-bash"},
    _EDIT: {"issued_tool": "Write", "issued_correlation_id": "cid-write"},
}


# --------------------------------------------------------------------------- builders
def _comp(provider="claude", mutating=True, nonce="N1", snap="S1"):
    prof = contract.LaunchProfile(session_policy="fresh", mutating=mutating)
    return canary.LaunchComposition(provider=provider, profile=prof,
                                    dispatch_nonce=nonce, snapshot_digest=snap)


def _init_event(plugins=("rawgentic@rawgentic",)):
    return {"type": "system", "subtype": "init", "session_id": "s", "plugins": list(plugins)}


def _tool_use(cid, name):
    return {"type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": cid, "name": name, "input": {}}]}}


def _tool_result(cid, marker, is_error=True):
    return {"type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": cid,
                                     "is_error": is_error,
                                     "content": [{"type": "text", "text": f"{marker} probe denied"}]}]}}


def _happy_stream():
    return [
        _init_event(),
        _tool_use("cid-bash", "Bash"), _tool_result("cid-bash", "BLOCKED:"),
        _tool_use("cid-write", "Write"), _tool_result("cid-write", "SECURITY BLOCK:"),
    ]


def _built(final_argv=("claude", "-p", "--output-format", "stream-json"), **comp_over):
    return canary_evidence.build_local_evidence(
        snapshot_dir=REPO_ROOT, composition=_comp(**comp_over), final_argv=list(final_argv))


def _completed(stream=None, plan=None, **comp_over):
    ev = _built(**comp_over)
    return canary_evidence.complete_evidence(
        evidence=ev, stream=_happy_stream() if stream is None else stream,
        probe_plan=_PLAN if plan is None else plan)


# ------------------------------------------------------------------ phase 1: local
def test_build_local_evidence_reads_staged_snapshot():
    ev = _built()
    assert ev.registration_digest == canary.EXPECTED_REGISTRATION_DIGEST
    assert ev.registration_readable is True
    assert ev.plugin_version == canary.EXPECTED_PLUGIN_VERSION
    assert canary.mutating_guard_classes(ev.hooks_registration)  # hooks.json parsed & usable
    assert ev.final_argv == ["claude", "-p", "--output-format", "stream-json"]


def test_build_local_evidence_echoes_composition_binding():
    ev = _built()
    assert ev.provider == "claude"
    assert ev.profile == "mutating"
    assert ev.dispatch_nonce == "N1"
    assert ev.snapshot_digest == "S1"


def test_build_local_evidence_local_checks_pass():
    ev = _built()
    assert canary._check_hooks_digest(ev).verdict == canary.PASS
    assert canary._check_plugin_version(ev).verdict == canary.PASS
    assert canary._check_bare_absent(ev).verdict == canary.PASS


def test_build_local_evidence_missing_snapshot_fails_closed(tmp_path):
    ev = canary_evidence.build_local_evidence(
        snapshot_dir=tmp_path / "nope", composition=_comp(), final_argv=["claude", "-p"])
    assert ev.registration_digest is None
    assert ev.registration_readable is False
    assert ev.plugin_version is None
    assert ev.hooks_registration is None
    res = canary.evaluate_canary("claude_mutating", ev)  # no exception escapes
    assert res.verdict == "refuse"
    assert "hooks_evidence_missing" in res.violations
    assert "canary_check_error:positive_deny" in res.violations


def test_build_local_evidence_malformed_hooks_json_fails_closed(tmp_path):
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "hooks.json").write_text("{not json", encoding="utf-8")
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "9.9.9"}', encoding="utf-8")
    ev = canary_evidence.build_local_evidence(
        snapshot_dir=tmp_path, composition=_comp(), final_argv=["claude", "-p"])
    assert ev.registration_digest is None       # digest compute caught, not raised
    assert ev.hooks_registration is None
    assert ev.plugin_version == "9.9.9"          # the readable plugin.json is still surfaced
    assert canary.evaluate_canary("claude_mutating", ev).verdict == "refuse"


def test_build_local_evidence_bad_composition_fails_closed():
    ev = canary_evidence.build_local_evidence(
        snapshot_dir=REPO_ROOT, composition=object(), final_argv=["claude", "-p"])
    assert ev.dispatch_nonce is None and ev.snapshot_digest is None
    # binding to a real composition then refuses (no nonce/digest to echo)
    with pytest.raises(canary.CanaryRefused) as ei:
        canary.require_canary(_comp(), ev)
    assert "canary_provider_mismatch" in str(ei.value) or "evidence_binding_mismatch" in str(ei.value)


def test_build_local_evidence_none_final_argv_refuses_at_check():
    ev = canary_evidence.build_local_evidence(
        snapshot_dir=REPO_ROOT, composition=_comp(), final_argv=None)
    assert canary._check_bare_absent(ev).violation == "argv_evidence_invalid"


# ------------------------------------------------------------------ phase 2: runtime
def test_complete_evidence_full_pass_end_to_end():
    completed = _completed()
    res = canary.evaluate_canary("claude_mutating", completed)
    assert res.verdict == "pass", res.violations


def test_complete_evidence_satisfies_require_canary_binding():
    comp = _comp()
    ev = canary_evidence.build_local_evidence(
        snapshot_dir=REPO_ROOT, composition=comp, final_argv=["claude", "-p"])
    completed = canary_evidence.complete_evidence(evidence=ev, stream=_happy_stream(), probe_plan=_PLAN)
    res = canary.require_canary(comp, completed)  # provider/profile/nonce/digest all echo
    assert res.verdict == "pass"
    assert res.policy_id == "claude_mutating"


def test_complete_evidence_preserves_phase1_binding():
    completed = _completed()
    assert completed.provider == "claude"
    assert completed.dispatch_nonce == "N1"
    assert completed.snapshot_digest == "S1"
    assert completed.registration_digest == canary.EXPECTED_REGISTRATION_DIGEST


def test_complete_evidence_reads_init_plugins():
    completed = _completed()
    assert completed.init_plugins == ["rawgentic@rawgentic"]


def test_complete_evidence_missing_init_refuses():
    stream = [ev for ev in _happy_stream() if ev.get("subtype") != "init"]
    completed = _completed(stream=stream)
    assert completed.init_plugins is None
    res = canary.evaluate_canary("claude_mutating", completed)
    assert "init_evidence_invalid" in res.violations


def test_complete_evidence_guard_absent_surfaces():
    # Bash probe RAN (is_error False) -> guard absent, not a deny.
    stream = [_init_event(), _tool_use("cid-bash", "Bash"),
              _tool_result("cid-bash", "ran", is_error=False),
              _tool_use("cid-write", "Write"), _tool_result("cid-write", "SECURITY BLOCK:")]
    res = canary.evaluate_canary("claude_mutating", _completed(stream=stream))
    assert f"positive_deny_absent:{_BASH}" in res.violations


def test_complete_evidence_missing_probe_result_unproven():
    # no tool_result for the Write class -> unproven (fail-closed), never a fake pass.
    stream = [_init_event(), _tool_use("cid-bash", "Bash"), _tool_result("cid-bash", "BLOCKED:")]
    res = canary.evaluate_canary("claude_mutating", _completed(stream=stream))
    assert f"positive_deny_unproven:{_EDIT}" in res.violations


def test_complete_evidence_non_hook_origin_deny_unproven():
    # deny with no guard marker in the reason -> not hook-origin -> unproven.
    stream = [_init_event(), _tool_use("cid-bash", "Bash"),
              _tool_result("cid-bash", "placeholder"),
              _tool_use("cid-write", "Write"), _tool_result("cid-write", "SECURITY BLOCK:")]
    # the marker "BLOCKED:" must be absent from the reason:
    stream[2]["message"]["content"][0]["content"][0]["text"] = "some OS error, permission denied"
    res = canary.evaluate_canary("claude_mutating", _completed(stream=stream))
    assert f"positive_deny_unproven:{_BASH}" in res.violations


def test_complete_evidence_malformed_stream_lines_skipped():
    good = _happy_stream()
    lines = ["not json at all", "{unterminated", ""] + [json.dumps(e) for e in good] + ["garbage}"]
    res = canary.evaluate_canary("claude_mutating", _completed(stream=lines))
    assert res.verdict == "pass", res.violations  # junk lines ignored, good ones parsed


def test_complete_evidence_accepts_callable_reader():
    completed = _completed(stream=lambda: iter(_happy_stream()))
    assert canary.evaluate_canary("claude_mutating", completed).verdict == "pass"


def test_complete_evidence_accepts_json_text_lines():
    lines = [json.dumps(e) for e in _happy_stream()]
    assert canary.evaluate_canary("claude_mutating", _completed(stream=lines)).verdict == "pass"


# ------------------------------------------------------------------ no CLI surface
def test_module_has_no_cli_surface():
    # pure library — no caller-supplied evidence path, no argv entry point.
    assert not hasattr(canary_evidence, "main")
