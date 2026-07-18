"""#428 — hooks.bakeoff_policy: the competitive-rounds / build-bake-off caller policy that plugs
into phase_executor.run_competitive. Stubbed judge + stubbed dispatch (no live GLM / provider call);
one wall-clock test uses the REAL QuotaCoordinator + real routing pools with sleeping stub authors to
prove the parallel-bake-off AC exercises the actual ceiling (Step-4 finding M3)."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
HOOKS = REPO / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))
import bakeoff_policy as bp  # noqa: E402
import complexity_gate as cg  # noqa: E402

bp._ensure_pe_importable()  # put phase_executor/src on sys.path for this test module
# phase_executor resolves at runtime via _ensure_pe_importable; pylint (astroid) can't see it from
# tests/hooks/ (unlike tests/phase_executor/), so the static no-name-in-module here is a false
# positive — the tests below exercise these imports. Scoped disable, not a blanket one.
# pylint: disable=no-name-in-module
import phase_executor as pe  # noqa: E402
import phase_executor.contract as _contract  # noqa: E402
# pylint: enable=no-name-in-module

TABLE = REPO / "phase_executor" / "src" / "phase_executor" / "routing" / "rawgentic.routing-table.json"


# ---- lightweight Observation stub (unit tests that never enter run_competitive) ---------------
class _Obs:
    def __init__(self, *, status="ok", payload="draft text", engine="claude"):
        self.parse_status = status
        self.parsed_payload = payload
        self.engine = engine

    def to_dict(self):
        return {"parse_status": self.parse_status, "parsed_payload": self.parsed_payload,
                "engine": self.engine}


def _real_obs(model, text, *, engine="claude", status=_contract.OK):
    """A genuine contract.Observation so run_competitive's dataclasses.replace works."""
    return _contract.Observation(
        run_id="r", attempt_id="a", correlation_id=None, seat="design", engine=engine,
        transport="native", requested_model=model, actual_model=model, prompt_hash="h",
        context_hashes=[], usage=None, timing_ms=1, queued_ms=0,
        process={"exit_code": 0, "timed_out": False}, parse_status=status,
        parsed_payload=text, raw_capture_path=None, fallback_reason=None, routing_config_digest="d")


# ---- rubric -----------------------------------------------------------------------------------
def test_load_rubric_present():
    for phase in ("design", "build"):
        text = bp.load_rubric(phase)
        assert "Hard gates" in text and "Completeness" in text and "Quality" in text


def test_load_rubric_missing_fails_closed():
    with pytest.raises(bp.RubricUnavailable):
        bp.load_rubric("does-not-exist")


def test_vendored_rubric_structure_guard():
    # L2: a STRUCTURE guard (cross-repo freshness is not detectable here). Each vendored rubric must
    # carry the provenance header (source commit + source-sha256) and the bench-14 section skeleton.
    for phase in ("design", "build"):
        text = (bp.RUBRIC_DIR / f"{phase}.md").read_text(encoding="utf-8")
        assert "VENDORED" in text and "source-sha256:" in text
        assert "3adcf233c63b0a4a4b85f8e537f48c5215da8d8e" in text  # pinned source commit
        assert "## Hard gates" in text
        assert "## Completeness" in text and "## Quality" in text


# ---- anonymize + shuffle ----------------------------------------------------------------------
def test_anonymize_deterministic_and_maps_back():
    results = [_Obs(payload="AAA"), _Obs(payload="BBB"), _Obs(payload="CCC")]
    drafts1, order1 = bp.anonymize_and_shuffle(results, seed=7)
    drafts2, order2 = bp.anonymize_and_shuffle(results, seed=7)
    assert order1 == order2  # deterministic per seed
    assert sorted(order1) == [0, 1, 2]
    assert [d["label"] for d in drafts1] == [1, 2, 3]
    # each draft's text is exactly the original candidate it maps back to
    for k, d in enumerate(drafts1):
        assert d["text"] == results[order1[k]].parsed_payload


def test_anonymize_excludes_failed_candidates():
    results = [_Obs(payload="ok0"), _Obs(status="harness_error", payload=None), _Obs(payload="ok2")]
    drafts, order = bp.anonymize_and_shuffle(results, seed=1)
    assert sorted(order) == [0, 2]  # the None-payload failed candidate is excluded
    assert len(drafts) == 2


def test_anonymize_fewer_than_two_valid_raises():
    results = [_Obs(payload="only-ok"), _Obs(status="timeout", payload=None)]
    with pytest.raises(bp.JudgeError):
        bp.anonymize_and_shuffle(results, seed=1)


# ---- judge (index remap is the highest-risk logic — M1) ---------------------------------------
def _find_nonidentity_seed(results):
    """A seed whose shuffle is NOT the identity permutation (so a buggy or inverted remap fails)."""
    for seed in range(50):
        _, order = bp.anonymize_and_shuffle(results, seed=seed)
        if order != list(range(len(results))):
            return seed, order
    raise AssertionError("no non-identity shuffle found")


def test_judge_maps_shuffled_winner_to_original_index():
    results = [_Obs(payload="cand0"), _Obs(payload="cand1"), _Obs(payload="cand2")]
    seed, order = _find_nonidentity_seed(results)
    # pick the SECOND draft as winner (non-first, per M1) -> expect original index order[1]
    verdict = json.dumps({"winner_draft": 2, "scores": {"2": {"x": 90}}, "confidence": 0.8})
    judge = bp.make_glm_judge("RUBRIC", seed=seed, complete_fn=lambda prompt: (verdict, ""))
    out = judge(results)
    assert out["winner_index"] == order[1]
    assert out["scores"]["_confidence"] == 0.8  # confidence folded in (L1)
    # also assert a FIRST-draft winner maps to order[0] under the same seed — catches a
    # direction-inversion that a single non-first case could miss for some permutations.
    first_verdict = json.dumps({"winner_draft": 1, "scores": {}})
    judge_first = bp.make_glm_judge("R", seed=seed, complete_fn=lambda prompt: (first_verdict, ""))
    assert judge_first(results)["winner_index"] == order[0]


def test_judge_build_evidence_lands_on_correct_draft():
    # build_evidence is keyed by ORIGINAL candidate index and must reach the matching shuffled draft.
    results = [_Obs(payload="cand0"), _Obs(payload="cand1"), _Obs(payload="cand2")]
    seed, order = _find_nonidentity_seed(results)
    seen = {}

    def capture(prompt):
        seen["prompt"] = prompt
        return json.dumps({"winner_draft": 1, "scores": {}}), ""

    # evidence for original candidate `order[1]` should appear beside the 2nd draft's text (cand{order[1]})
    evidence = {order[1]: "EVIDENCE-FOR-SECOND-DRAFT"}
    judge = bp.make_glm_judge("R", seed=seed, complete_fn=capture, build_evidence=evidence)
    judge(results)
    prompt = seen["prompt"]
    # the evidence block must sit in the Draft-2 section, i.e. after "Draft 2" and its text
    draft2_marker = "--- Draft 2 ---"
    assert "EVIDENCE-FOR-SECOND-DRAFT" in prompt
    assert prompt.index("EVIDENCE-FOR-SECOND-DRAFT") > prompt.index(draft2_marker)


def test_judge_out_of_range_winner_raises():
    results = [_Obs(payload="a"), _Obs(payload="b")]
    verdict = json.dumps({"winner_draft": 99})
    judge = bp.make_glm_judge("R", seed=0, complete_fn=lambda p: (verdict, ""), retries=0)
    with pytest.raises(bp.JudgeError):
        judge(results)


def test_judge_unparseable_retries_then_fails():
    results = [_Obs(payload="a"), _Obs(payload="b")]
    calls = {"n": 0}

    def bad(prompt):
        calls["n"] += 1
        return "not-json", ""

    judge = bp.make_glm_judge("R", seed=0, complete_fn=bad, retries=1)
    with pytest.raises(bp.JudgeError):
        judge(results)
    assert calls["n"] == 2  # retries+1 attempts


def test_judge_retry_then_success():
    results = [_Obs(payload="a"), _Obs(payload="b")]
    calls = {"n": 0}

    def flaky(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return None, "transport blip"
        return json.dumps({"winner_draft": 1}), ""

    judge = bp.make_glm_judge("R", seed=0, complete_fn=flaky, retries=1)
    out = judge(results)
    assert calls["n"] == 2
    assert out["winner_index"] in (0, 1)


def test_judge_none_carries_results():
    results = [_Obs(payload="a"), _Obs(payload="b")]
    judge = bp.make_glm_judge("R", seed=0, complete_fn=lambda p: (None, "down"), retries=0)
    with pytest.raises(bp.JudgeError) as exc:
        judge(results)
    assert len(exc.value.results) == 2


# ---- failure strategy -------------------------------------------------------------------------
def test_headless_returns_incumbent_degraded():
    results = [_Obs(payload="a"), _Obs(payload="b")]
    strat = bp.hybrid_failure_strategy(headless=True, incumbent_index=1)
    out = strat(results, RuntimeError("judge down"))
    assert out == {"winner_index": 1, "degraded": True, "scores": None}


def test_headless_incumbent_not_ok_fails_closed():
    results = [_Obs(payload="a"), _Obs(status="harness_error", payload=None)]
    strat = bp.hybrid_failure_strategy(headless=True, incumbent_index=1)
    with pytest.raises(bp.JudgeError):
        strat(results, RuntimeError("judge down"))


def test_interactive_persists_trace_then_reraises():
    results = [_real_obs("m0", "a"), _real_obs("m1", "b")]
    captured = []
    strat = bp.hybrid_failure_strategy(headless=False, incumbent_index=0, sink=captured.append)
    with pytest.raises(bp.JudgeError):
        strat(results, RuntimeError("judge down"))
    assert len(captured) == 1
    assert captured[0]["judge_degraded"] is True
    assert captured[0]["winner_index"] is None


# ---- sink -------------------------------------------------------------------------------------
def test_sink_appends_jsonl(tmp_path):
    path = tmp_path / "bakeoff_results.jsonl"
    sink = bp.bakeoff_sink(path)
    sink({"run_id": "r1", "winner_index": 0})
    sink({"run_id": "r2", "winner_index": 1})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["run_id"] == "r1"
    assert json.loads(lines[1])["winner_index"] == 1


# ---- D9 ---------------------------------------------------------------------------------------
def test_reviewer_backend_gpt_winner_forces_glm():
    assert bp.reviewer_backend_for_winner(_Obs(engine="codex")) == "glm"


def test_reviewer_backend_non_gpt_uses_default():
    assert bp.reviewer_backend_for_winner(_Obs(engine="claude")) == "gpt"
    assert bp.reviewer_backend_for_winner(_Obs(engine="claude"), default="glm") == "glm"


# ---- lane sourcing ----------------------------------------------------------------------------
def test_lane_for_model():
    snap = pe.snapshot_from_file(TABLE)
    assert bp._lane_for_model(snap, "gpt-5.6-sol")["pool"] == "codex"
    assert bp._lane_for_model(snap, "claude-opus-4-8")["pool"] == "claude"
    assert bp._lane_for_model(snap, "no-such-model") is None


# ---- gate digest (M7) -------------------------------------------------------------------------
def _real_gate(*, bake):
    """A genuine GateDecision from needs_bakeoff (snapshot + digest + decision all consistent), so
    the executor's re-derived decision matches. bake=True -> risk_high triggers; bake=False -> clean."""
    task = {"risk_level": "high" if bake else "standard"}
    issue = {"complexity": "standard"}
    plan_est = {"files": ["src/app.py"], "lines": 1, "file_count": 1}
    return cg.needs_bakeoff(task, issue, plan_est, cfg={})


def test_build_bakeoff_tampered_digest_fails_closed():
    gd = cg.GateDecision(decision=True, reason_codes=(), input_snapshot={"a": 1},
                         policy_digest="sha256:deadbeef")
    snap = pe.snapshot_from_file(TABLE)
    quota = pe.QuotaCoordinator(REPO / ".tmp-none", {"claude": 2, "codex": 4, "zhipu": 2})
    with pytest.raises(bp.JudgeError, match="policy_digest mismatch"):
        bp.run_build_bakeoff("build this", gate_decision=gd, snapshot=snap, quota=quota,
                             capture_root=REPO / ".tmp-none", headless=True, seed=1)


def test_build_bakeoff_tampered_decision_field_is_ignored():
    # M7: flipping decision True->False on an otherwise-valid gate must NOT suppress the bake-off —
    # the executor re-derives the decision from the digest-verified snapshot, not the decision field.
    real = _real_gate(bake=True)
    tampered = cg.GateDecision(decision=False, reason_codes=(), input_snapshot=real.input_snapshot,
                               policy_digest=real.policy_digest)
    snap = pe.snapshot_from_file(TABLE)
    seat_calls = {"n": 0}

    def fake_seat(seat, prompt, *, snapshot, quota, capture_root):
        seat_calls["n"] += 1
        return "SINGLE-SEAT"

    verdict = json.dumps({"winner_draft": 1, "scores": {}, "confidence": 0.5})
    winner, losers, judge_obs, record = bp.run_build_bakeoff(
        "build this", gate_decision=tampered, snapshot=snap,
        quota=pe.QuotaCoordinator(REPO / ".tmp-none2", snap.pool_concurrency()),
        capture_root=REPO / ".tmp-none2", headless=True, seed=2,
        complete_fn=lambda p: (verdict, ""), dispatch=_sleeping_dispatch(0.0),
        default_seat_runner=fake_seat)
    assert seat_calls["n"] == 0           # NOT the single-seat path
    assert record["n_candidates"] == 3    # the bake-off ran despite decision=False


def test_build_bakeoff_gate_false_runs_single_seat_uniform_shape():
    gd = _real_gate(bake=False)
    snap = pe.snapshot_from_file(TABLE)
    called = {}

    def fake_seat(seat, prompt, *, snapshot, quota, capture_root):
        called.update(seat=seat, prompt=prompt)
        return "SINGLE-SEAT-RESULT"

    winner, losers, judge_obs, record = bp.run_build_bakeoff(
        "build this", gate_decision=gd, snapshot=snap, quota=None, capture_root=None,
        headless=True, seed=1, default_seat_runner=fake_seat)
    assert winner == "SINGLE-SEAT-RESULT" and losers == [] and judge_obs is None
    assert record["bakeoff_skipped"] is True and record["n_candidates"] == 1
    assert called == {"seat": "build", "prompt": "build this"}


# ---- design-seat manifest data invariants (#464 §B) -------------------------------------------
def _shipped_seats():
    return json.loads(TABLE.read_text())["seats"]


def _seat_models(spec):
    return {spec["primary"]["model"], *(e["model"] for e in spec.get("chain", []))}


def test_design_models_equals_design_seat_models():
    # §B breaker A6: SET EQUALITY, not subset. A one-directional subset would let a model ADDED to
    # the design row silently never become a bake-off candidate (bakeoff_policy scans DESIGN_MODELS,
    # so a table-only model would never dispatch). Read from the SHIPPED table.
    design = _shipped_seats()["design"]
    assert set(bp.DESIGN_MODELS) == _seat_models(design)


def test_design_row_manifest_read_only_for_models_shared_with_other_chains():
    """§B P3 (normative rule pin, DATA invariant): a competitive candidate's governing manifest is
    ALWAYS the REQUESTED design row's, NEVER the seat its lane happened to be discovered from — a
    duplicate model id found via e.g. the build chain must not resolve build's WRITE-granting
    manifest for a design dispatch. This pins the data that makes the rule meaningful: for every
    DESIGN_MODELS id that ALSO lives in another seat's chain (opus appears in build/analysis/ship
    chains; build is write-granting), the design row declares read-only tool_grants (["read"]), so a
    candidate that must resolve the design manifest never inherits write grants. Runtime resolution
    enforcement is W2 #465; W1 pins only the data invariant."""
    seats = _shipped_seats()
    other_chain_models = set().union(
        *({e["model"] for e in spec.get("chain", [])}
          for name, spec in seats.items() if name != "design"))
    shared = set(bp.DESIGN_MODELS) & other_chain_models
    assert shared, "rule is only meaningful when a DESIGN_MODEL also lives in another seat's chain"
    assert seats["design"]["manifest"]["tool_grants"] == ["read"]


# ---- integration + wall-clock AC (real quota, real pools, sleeping stub authors) --------------
def _sleeping_dispatch(delay):
    def dispatch(engine, req, *, run_id, attempt_id, capture_root, digest, queued_ms, fallback_reason):
        time.sleep(delay)
        return _real_obs(req.requested_model, f"draft from {req.requested_model}", engine=engine)
    return dispatch


def test_design_round_end_to_end_and_parallel(tmp_path):
    snap = pe.snapshot_from_file(TABLE)
    quota = pe.QuotaCoordinator(tmp_path / "permits", snap.pool_concurrency())
    sink_path = tmp_path / "bakeoff_results.jsonl"
    verdict = json.dumps({"winner_draft": 1, "scores": {}, "confidence": 0.7})
    delay = 0.5
    t0 = time.monotonic()
    winner, losers, judge_obs, record = bp.run_design_round(
        "design the widget", snapshot=snap, quota=quota, capture_root=tmp_path / "cap",
        headless=True, seed=3, sink_path=sink_path,
        complete_fn=lambda prompt: (verdict, ""), dispatch=_sleeping_dispatch(delay))
    elapsed = time.monotonic() - t0
    # sol (codex pool) + opus (claude pool) run concurrently -> ~1x delay, never the 2x serial sum.
    assert elapsed < delay * 1.6, f"design round not parallel: {elapsed:.2f}s for 2x {delay}s authors"
    assert record["n_candidates"] == 2
    assert record["winner_index"] in (0, 1)
    assert sink_path.exists() and len(sink_path.read_text().splitlines()) == 1


def test_build_bakeoff_saturates_claude_pool_but_stays_parallel(tmp_path):
    # M3: the real build set puts sonnet+opus BOTH on the claude pool (limit 2) + terra on codex.
    # All three must still run concurrently (2 claude slots + 1 codex), proving the ceiling boundary.
    snap = pe.snapshot_from_file(TABLE)
    quota = pe.QuotaCoordinator(tmp_path / "permits", snap.pool_concurrency())
    verdict = json.dumps({"winner_draft": 1, "scores": {}, "confidence": 0.6})
    delay = 0.5
    t0 = time.monotonic()
    winner, losers, judge_obs, record = bp.run_build_bakeoff(
        "build the widget", gate_decision=_real_gate(bake=True), snapshot=snap, quota=quota,
        capture_root=tmp_path / "cap", headless=True, seed=2, sink_path=tmp_path / "b.jsonl",
        complete_fn=lambda prompt: (verdict, ""), dispatch=_sleeping_dispatch(delay))
    elapsed = time.monotonic() - t0
    assert elapsed < delay * 1.8, f"build bake-off serialized on the claude pool: {elapsed:.2f}s"
    assert record["n_candidates"] == 3


def test_headless_judge_failure_degrades_to_incumbent_through_engine(tmp_path):
    # The core resilience path exercised END-TO-END through run_competitive: a failing glm judge in a
    # headless design round yields winner = incumbent (opus) with judge_degraded True in the record.
    snap = pe.snapshot_from_file(TABLE)
    quota = pe.QuotaCoordinator(tmp_path / "permits", snap.pool_concurrency())
    winner, losers, judge_obs, record = bp.run_design_round(
        "design it", snapshot=snap, quota=quota, capture_root=tmp_path / "cap", headless=True, seed=3,
        sink_path=tmp_path / "b.jsonl",
        complete_fn=lambda prompt: (None, "glm down"),          # judge fails on every attempt
        dispatch=_sleeping_dispatch(0.0))
    assert record["judge_degraded"] is True
    # incumbent = opus = DESIGN_MODELS index 1; the winner Observation is that candidate.
    assert bp.DESIGN_MODELS[record["winner_index"]] == bp.INCUMBENT_MODEL
    assert winner.requested_model == bp.INCUMBENT_MODEL


def test_candidates_for_unknown_model_raises():
    snap = pe.snapshot_from_file(TABLE)
    with pytest.raises(ValueError, match="no lane"):
        bp._candidates_for(snap, ("no-such-model",), "p", seat="design")


def test_reviewer_backend_engine_none_uses_default():
    assert bp.reviewer_backend_for_winner(_Obs(engine=None)) == "gpt"


# ---- live glm-5.2 judge (opt-in: RUN_LIVE=1 + a glm-capable python w/ ZHIPUAI_API_KEY) ---------
@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1",
                    reason="live GLM judge; set RUN_LIVE=1 in a zhipuai-capable env with the key sourced")
def test_live_glm_judge_scores_two_drafts():
    # A real glm-5.2 call over two anonymized drafts returns a valid winner_index + per-criterion
    # scores. Needs zhipuai installed in THIS interpreter + a GLM credential in env (see the design
    # doc's live-demonstration note). Never runs in CI.
    results = [
        _real_obs("A", "Design: single Postgres table, RLS by tenant, optimistic-lock version col."),
        _real_obs("B", "just use a global variable and hope for the best"),
    ]
    judge = bp.make_glm_judge(bp.load_rubric("design"), seed=1)  # real glm_complete
    out = judge(results)
    assert out["winner_index"] in (0, 1)
    assert isinstance(out["scores"], dict)
