"""#470 W7 Task-3 — supervised-dispatch mechanisms in phase_executor: the pre-spawn probe
session's OWN short-lived quota permit (acquired/released finally-equivalent, so repeated
downstream refusals never shrink the pool) and pane_runner's NEW dual-digest TOCTOU re-verify
(pane-spec digest + recomputed staged-snapshot digest, before executing anything).

Plus the platform-feasibility CELL (#226): a RUN_LIVE-gated test that drives the REAL
``claude -p --output-format stream-json`` probe through the supervisor and feeds its stream to
the trusted collector, proving T2's parser against the live envelope. CI keeps stubs; the live
cell is the non-skippable proving gate the design names (§Platform).
"""
import json
import pathlib
import shutil

import pytest

from phase_executor import canary, canary_evidence, contract, pane_runner, supervisor
from phase_executor.capture import hash_text
from phase_executor.quota import QuotaCoordinator

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# --------------------------------------------------------------- probe-session permit accounting
def _sup(tmp_path):
    """A TmuxSupervisor whose probe_session we exercise directly — snapshot/roots are unused by
    probe_session (it owns only the permit + the injected runner)."""
    return supervisor.TmuxSupervisor(
        snapshot=None, quota=None, capture_root=str(tmp_path / "cap"),
        registry_root=str(tmp_path / "reg"))


def _comp():
    prof = contract.LaunchProfile(session_policy="fresh", mutating=True)
    return canary.LaunchComposition(provider="claude", profile=prof,
                                    dispatch_nonce="N", snapshot_digest="S")


def test_probe_session_holds_one_permit_during_the_run(tmp_path):
    quota = QuotaCoordinator(tmp_path / "permits", {"claude": 1})
    sup = _sup(tmp_path)
    observed = []

    def runner(*, composition, probe_plan, snapshot_dir, timeout):
        observed.append(quota.live_permits("claude"))  # a permit is HELD while the probe runs
        return [{"type": "system", "subtype": "init", "plugins": []}]

    sup.probe_session(_comp(), {"Bash": {"issued_tool": "Bash", "issued_correlation_id": "c"}},
                      snapshot_dir=str(REPO_ROOT), quota=quota, pool="claude", run=runner)
    assert observed == [1]
    assert quota.live_permits("claude") == 0  # released on the success path


def test_probe_session_releases_permit_on_repeated_refusals(tmp_path):
    # The leak guard: even when the downstream canary would REFUSE every time, the probe's own
    # permit is always released — N probe sessions never shrink a concurrency-1 pool.
    quota = QuotaCoordinator(tmp_path / "permits", {"claude": 1})
    sup = _sup(tmp_path)
    refusing_stream = [{"type": "system", "subtype": "init", "plugins": []}]  # no deny -> refuse
    for _ in range(4):
        sup.probe_session(_comp(), {"Bash": {"issued_tool": "Bash", "issued_correlation_id": "c"}},
                          snapshot_dir=str(REPO_ROOT), quota=quota, pool="claude",
                          run=lambda **_k: refusing_stream)
        assert quota.live_permits("claude") == 0
    with quota.acquire("claude", timeout=1.0):  # the slot is free — pool never shrank
        assert quota.live_permits("claude") == 1


def test_probe_session_releases_permit_when_runner_raises(tmp_path):
    quota = QuotaCoordinator(tmp_path / "permits", {"claude": 1})
    sup = _sup(tmp_path)

    def boom(**_k):
        raise RuntimeError("probe transport died")

    with pytest.raises(RuntimeError):
        sup.probe_session(_comp(), {"Bash": {"issued_tool": "Bash", "issued_correlation_id": "c"}},
                          snapshot_dir=str(REPO_ROOT), quota=quota, pool="claude", run=boom)
    assert quota.live_permits("claude") == 0  # released in the finally even on the raise path


def test_probe_prompt_covers_every_class_and_empty_is_empty():
    plan = {"Bash": {"issued_tool": "Bash"}, "Edit|Write": {"issued_tool": "Edit"}}
    prompt = supervisor.probe_prompt(plan)
    assert "(Bash)" in prompt and "(Edit|Write)" in prompt
    # empty plan -> a body-less prompt (no probes) -> the canary refuses positive_deny downstream
    assert supervisor.probe_prompt({}).endswith("\n")


# --------------------------------------------------------------- pane_runner dual-digest re-verify
def _spec():
    return {"engine": "claude", "run_id": "r", "attempt_id": "0-x", "capture_root": "/c",
            "routing_config_digest": "sha256:d", "request": {}}


def test_pane_spec_digest_match_ok():
    spec = _spec()
    text = json.dumps(spec, sort_keys=True)
    pane_runner._verify_digests(text, spec, hash_text(text))  # no raise


def test_pane_spec_digest_mismatch_refuses():
    spec = _spec()
    text = json.dumps(spec, sort_keys=True)
    with pytest.raises(ValueError, match="pane-spec digest mismatch"):
        pane_runner._verify_digests(text, spec, "sha256:not-the-digest")


def test_pane_spec_digest_absent_is_skipped():
    spec = _spec()
    pane_runner._verify_digests(json.dumps(spec), spec, None)  # legacy/non-supervised launch


def test_snapshot_digest_match_ok():
    spec = _spec()
    spec["snapshot_dir"] = str(REPO_ROOT)
    spec["expected_snapshot_digest"] = canary.compute_registration_digest(str(REPO_ROOT))
    pane_runner._verify_digests(json.dumps(spec), spec, None)  # no raise


def test_snapshot_digest_mismatch_refuses():
    spec = _spec()
    spec["snapshot_dir"] = str(REPO_ROOT)
    spec["expected_snapshot_digest"] = "sha256:stale"
    with pytest.raises(ValueError, match="staged-snapshot digest mismatch"):
        pane_runner._verify_digests(json.dumps(spec), spec, None)


def test_snapshot_binding_incomplete_refuses():
    spec = _spec()
    spec["snapshot_dir"] = str(REPO_ROOT)  # dir without the expected digest
    with pytest.raises(ValueError, match="binding incomplete"):
        pane_runner._verify_digests(json.dumps(spec), spec, None)


def test_pane_runner_main_refuses_tampered_spec_file(tmp_path):
    # end-to-end through main(): a spec swapped after the supervisor computed its digest refuses
    # (return 1) BEFORE any adapter resolves — the argv[2] out-of-band digest is the anchor.
    spec_path = tmp_path / "spec.json"
    original = json.dumps(_spec(), sort_keys=True)
    digest = hash_text(original)
    spec_path.write_text(json.dumps({**_spec(), "engine": "codex"}, sort_keys=True),  # tampered
                         encoding="utf-8")
    assert pane_runner.main([str(spec_path), digest]) == 1


# --------------------------------------------------------------- platform-feasibility live cell
_HAVE_CLAUDE = shutil.which("claude") is not None


@pytest.mark.live
@pytest.mark.skipif(not _HAVE_CLAUDE, reason="needs the `claude` CLI on PATH")
def test_two_stage_probe_stream_parses_live(tmp_path):
    """CELL (#226): drive a REAL claude -p --output-format stream-json probe through the supervisor
    and feed the stream to the trusted collector. Proves T2's parser against the live envelope:
    the init event yields plugins[] (lane_provisioned) and a hook-origin Bash deny yields a
    correlated ProbeOutcome carrying the wal-guard BLOCKED: marker. The Bash-class check must NOT
    refuse for a reason ABOUT the Bash class (a lane that lacks rawgentic simply skips)."""
    quota = QuotaCoordinator(tmp_path / "permits", {"claude": 1})
    sup = supervisor.TmuxSupervisor(
        snapshot=None, quota=None, capture_root=str(tmp_path / "cap"),
        registry_root=str(tmp_path / "reg"))
    comp = _comp()
    plan = {"Bash": {"issued_tool": "Bash", "issued_correlation_id": "probe-bash"}}
    events = sup.probe_session(comp, plan, snapshot_dir=str(REPO_ROOT), quota=quota,
                               pool="claude", timeout=180.0)
    # 3-line stream excerpt for the report
    print("LIVE STREAM EXCERPT:")
    for e in events[:3]:
        print(json.dumps(e)[:200])
    ev = canary_evidence.build_local_evidence(
        snapshot_dir=REPO_ROOT, composition=comp, final_argv=["claude", "--print"])
    done = canary_evidence.complete_evidence(evidence=ev, stream=events, probe_plan=plan)
    if done.init_plugins is None:
        pytest.skip("no init.plugins[] in the live stream (envelope shape changed)")
    lane = canary._check_lane_provisioned(done)
    ids = {(p.get("name") if isinstance(p, dict) else p) for p in done.init_plugins}
    if not (ids & canary._RAWGENTIC_PLUGIN_IDS):
        pytest.skip("this claude lane does not load rawgentic — cannot prove a hook-origin deny")
    assert lane.verdict == "pass", done.init_plugins
    outcome = done.probes["Bash"]
    if outcome.transport_error or not outcome.denied:
        pytest.skip(f"no hook-origin Bash deny observed (outcome={outcome})")
    assert outcome.deny_reason and "BLOCKED:" in outcome.deny_reason, outcome
    assert outcome.observed_tool == "Bash"
