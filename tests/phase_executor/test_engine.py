"""Task 10: engine.py — run_seat (chain-aware skip, availability fallback, ChainExhausted, quota)
and run_competitive (parallel wall-clock, judge/failure_strategy/sink, feasibility preflight).
Uses a stub dispatch (no live calls)."""
import time

import pytest

from phase_executor import contract, engine, routing
from phase_executor.quota import QuotaCoordinator
from phase_executor.engine import Candidate, InfeasibleBakeoff, run_competitive, run_seat


def _lane(pool, provider="anthropic", transport="native"):
    return {"provider": provider, "transport": transport, "auth_mode": "subscription_oauth", "credential_ref": None, "pool": pool}


def _snapshot():
    table = {
        "schema_version": "1",
        "pools": {"claude": {"concurrency": 2}, "codex": {"concurrency": 4}, "zhipu": {"concurrency": 2}},
        "seats": {
            "review": {
                "primary": {"model": "claude-fable-5", "lane": _lane("claude")},
                "chain": [
                    {"model": "gpt-5.6-sol", "lane": _lane("codex", provider="openai")},
                    {"model": "claude-sonnet-5", "lane": _lane("claude")},
                ],
            },
            "solo": {"primary": {"model": "claude-opus-4-8", "lane": _lane("claude")}, "chain": []},
        },
        "forbidden_combinations": [
            {"model_pattern": "haiku", "reason": "never Haiku"},
            {"rule": "cross_model_author", "reason": "reviewer != author"},
        ],
    }
    return routing.RoutingSnapshot.from_table(table)


def _obs(req, status=contract.OK, engine_name="claude"):
    actual = req.requested_model if status == contract.OK else (None if status == contract.IDENTITY_FAILURE else req.requested_model)
    usage = {"input": 5, "output": 7, "cached": 0} if status == contract.OK else None
    return contract.Observation(
        run_id="r", attempt_id="a", correlation_id=req.correlation_id, seat=req.seat, engine=engine_name,
        transport=req.transport, requested_model=req.requested_model, actual_model=actual,
        prompt_hash="sha256:x", context_hashes=[], usage=usage, timing_ms=1, queued_ms=0,
        process={"exit_code": 0 if status != contract.NONZERO_EXIT else 1, "timed_out": status == contract.TIMEOUT},
        parse_status=status, parsed_payload=req.prompt, raw_capture_path=None, fallback_reason=None,
        routing_config_digest="sha256:d",
    )


def _stub(status_by_engine=None, *, sleep=0.0, record=None, raise_engines=()):
    status_by_engine = status_by_engine or {}
    def dispatch(engine_name, req, *, run_id, attempt_id, capture_root, digest, queued_ms, fallback_reason):
        if sleep:
            time.sleep(sleep)
        if record is not None:
            record.append((engine_name, req.requested_model, fallback_reason))
        if engine_name in raise_engines:
            raise RuntimeError(f"boom-{engine_name}")
        st = status_by_engine.get(engine_name, contract.OK)
        return _obs(req, status=st, engine_name=engine_name)
    return dispatch


def test_run_seat_happy_primary(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2, "codex": 4})
    calls = []
    obs = run_seat("review", "hi", snapshot=_snapshot(), quota=qc, capture_root=tmp_path,
                   dispatch=_stub(record=calls))
    assert obs.parse_status == "ok"
    assert obs.requested_model == "claude-fable-5"
    assert calls == [("claude", "claude-fable-5", None)]  # primary only, no fallback


def test_run_seat_chain_aware_skip_by_author(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2, "codex": 4})
    calls = []
    # author is anthropic -> both claude targets skipped -> codex sol is first eligible
    obs = run_seat("review", "hi", snapshot=_snapshot(), quota=qc, capture_root=tmp_path,
                   author_provider="anthropic", dispatch=_stub(record=calls))
    assert obs.requested_model == "gpt-5.6-sol"
    assert calls == [("codex", "gpt-5.6-sol", None)]


def test_run_seat_falls_back_on_availability_failure(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2, "codex": 4})
    calls = []
    # claude (primary fable) nonzero_exit -> fall back to codex sol (ok)
    obs = run_seat("review", "hi", snapshot=_snapshot(), quota=qc, capture_root=tmp_path,
                   dispatch=_stub({"claude": contract.NONZERO_EXIT}, record=calls))
    assert obs.requested_model == "gpt-5.6-sol"
    assert obs.parse_status == "ok"
    assert calls[0][0] == "claude" and calls[1][0] == "codex"
    assert calls[1][2] and "fallback from claude-fable-5" in calls[1][2]


def test_run_seat_falls_back_on_no_response(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2, "codex": 4})
    calls = []
    # claude primary returns no_response (empty transport/output) -> availability -> fall back
    obs = run_seat("review", "hi", snapshot=_snapshot(), quota=qc, capture_root=tmp_path,
                   dispatch=_stub({"claude": contract.NO_RESPONSE}, record=calls))
    assert obs.requested_model == "gpt-5.6-sol"
    assert obs.parse_status == "ok"
    assert calls[0][0] == "claude" and calls[1][0] == "codex"


def test_run_seat_does_not_fall_back_on_identity_failure(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2, "codex": 4})
    calls = []
    obs = run_seat("review", "hi", snapshot=_snapshot(), quota=qc, capture_root=tmp_path,
                   dispatch=_stub({"claude": contract.IDENTITY_FAILURE}, record=calls))
    assert obs.parse_status == "identity_failure"
    assert len(calls) == 1  # model responded (wrong id) -> no availability fallback


def test_run_seat_chain_exhausted(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2})
    with pytest.raises(routing.ChainExhausted):
        run_seat("solo", "hi", snapshot=_snapshot(), quota=qc, capture_root=tmp_path,
                 author_provider="anthropic", dispatch=_stub())


# ---- run_competitive --------------------------------------------------------

def _candidates_cross_pool():
    return [
        Candidate(seat="design", model="claude-opus-4-8", prompt="p", provider="anthropic", pool="claude"),
        Candidate(seat="design", model="gpt-5.6-sol", prompt="p", provider="openai", pool="codex"),
        Candidate(seat="design", model="glm-5.2", prompt="p", provider="zhipuai", pool="zhipu"),
    ]


def _judge_first(results, rubric):
    return {"winner_index": 0, "scores": [1.0, 0.5, 0.4]}


def test_run_competitive_parallel_wall_clock(tmp_path):
    """AC4 shape: 3 candidates across 3 pools run concurrently -> wall <= 1.3x slowest."""
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2, "codex": 4, "zhipu": 2})
    D = 0.3
    start = time.monotonic()
    winner, losers, judge_obs, record = run_competitive(
        _candidates_cross_pool(), judge=_judge_first, snapshot=_snapshot(), quota=qc,
        capture_root=tmp_path, require_parallel=True, dispatch=_stub(sleep=D),
    )
    wall = time.monotonic() - start
    assert wall <= 1.3 * D, f"not parallel: wall={wall:.3f} vs slowest={D}"
    assert record["n_candidates"] == 3
    assert winner.requested_model == "claude-opus-4-8"
    assert len(losers) == 2


def test_run_competitive_sink_receives_record(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2, "codex": 4, "zhipu": 2})
    got = []
    run_competitive(_candidates_cross_pool(), judge=_judge_first, sink=got.append,
                    snapshot=_snapshot(), quota=qc, capture_root=tmp_path, dispatch=_stub())
    assert len(got) == 1 and got[0]["winner_index"] == 0


def test_run_competitive_judge_failure_uses_strategy(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2, "codex": 4, "zhipu": 2})
    def bad_judge(results, rubric):
        raise RuntimeError("judge down")
    def strategy(results, exc):
        return {"winner_index": 0, "degraded": True}
    winner, losers, judge_obs, record = run_competitive(
        _candidates_cross_pool(), judge=bad_judge, failure_strategy=strategy,
        snapshot=_snapshot(), quota=qc, capture_root=tmp_path, dispatch=_stub())
    assert record["judge_degraded"] is True


def test_run_competitive_judge_failure_no_strategy_raises(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2, "codex": 4, "zhipu": 2})
    def bad_judge(results, rubric):
        raise RuntimeError("judge down")
    with pytest.raises(RuntimeError):
        run_competitive(_candidates_cross_pool(), judge=bad_judge, snapshot=_snapshot(),
                        quota=qc, capture_root=tmp_path, dispatch=_stub())


def test_infeasible_bakeoff_rejected(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2})
    three_claude = [Candidate(seat="build", model=f"m{i}", prompt="p", provider="anthropic", pool="claude") for i in range(3)]
    with pytest.raises(InfeasibleBakeoff):
        run_competitive(three_claude, judge=_judge_first, snapshot=_snapshot(), quota=qc,
                        capture_root=tmp_path, require_parallel=True, dispatch=_stub())


# ---- run_competitive hardening (Step 11 findings) ---------------------------

def test_candidate_exception_isolated_not_aborting_round(tmp_path):
    """f4: one candidate raising -> harness_error Observation, others still judged."""
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2, "codex": 4, "zhipu": 2})
    winner, losers, judge_obs, record = run_competitive(
        _candidates_cross_pool(), judge=_judge_first, snapshot=_snapshot(), quota=qc,
        capture_root=tmp_path, dispatch=_stub(raise_engines={"codex"}))  # codex candidate raises
    assert record["n_candidates"] == 3
    all_obs = [winner, *losers]
    statuses = sorted(o.parse_status for o in all_obs)
    assert "harness_error" in statuses  # the raising codex candidate recorded, not crashed
    assert statuses.count("ok") == 2    # claude + zhipu still succeeded and were judged


def test_forbidden_haiku_candidate_not_dispatched(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2, "codex": 4, "zhipu": 2})
    calls = []
    cands = _candidates_cross_pool() + [
        Candidate(seat="design", model="claude-haiku-4-5", prompt="p", provider="anthropic", pool="claude"),
    ]
    winner, losers, judge_obs, record = run_competitive(
        cands, judge=_judge_first, snapshot=_snapshot(), quota=qc, capture_root=tmp_path,
        dispatch=_stub(record=calls))
    assert record["n_candidates"] == 4
    assert not any("haiku" in m for _, m, _ in calls)  # haiku never dispatched
    haiku_obs = [o for o in [winner, *losers] if "haiku" in o.requested_model]
    assert haiku_obs and haiku_obs[0].parse_status == "harness_error"


def test_cross_model_author_skips_same_provider(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2, "codex": 4, "zhipu": 2})
    calls = []
    # author is anthropic -> the anthropic (claude) candidate is forbidden, not dispatched
    run_competitive(_candidates_cross_pool(), judge=_judge_first, author_provider="anthropic",
                    snapshot=_snapshot(), quota=qc, capture_root=tmp_path, dispatch=_stub(record=calls))
    assert not any(e == "claude" for e, _, _ in calls)  # claude candidate skipped
    assert any(e == "codex" for e, _, _ in calls) and any(e == "zhipuai" for e, _, _ in calls)


def test_unknown_pool_fails_closed(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2})
    bad = [Candidate(seat="x", model="m", prompt="p", provider="anthropic", pool="ghost")]
    with pytest.raises(ValueError):
        run_competitive(bad, judge=_judge_first, snapshot=_snapshot(), quota=qc,
                        capture_root=tmp_path, dispatch=_stub())


def test_all_forbidden_chain_exhausted(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2})
    haikus = [Candidate(seat="x", model="claude-haiku-4-5", prompt="p", provider="anthropic", pool="claude")]
    with pytest.raises(routing.ChainExhausted):
        run_competitive(haikus, judge=_judge_first, snapshot=_snapshot(), quota=qc,
                        capture_root=tmp_path, dispatch=_stub())


# --- #425 B: engine stamps dispatched_lane from the ACTUAL dispatched target ---

def test_run_seat_stamps_dispatched_lane_primary(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2, "codex": 4})
    obs = run_seat("review", "hi", snapshot=_snapshot(), quota=qc, capture_root=tmp_path, dispatch=_stub())
    assert obs.requested_model == "claude-fable-5"
    assert obs.dispatched_lane == _lane("claude")  # primary lane, stamped


def test_run_seat_dispatched_lane_reflects_fallback_target(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2, "codex": 4})
    # claude fails availability -> falls back to sol (codex/openai); lane must be the FALLBACK's
    obs = run_seat("review", "hi", snapshot=_snapshot(), quota=qc, capture_root=tmp_path,
                   dispatch=_stub({"claude": contract.LAUNCH_ERROR}))
    assert obs.requested_model == "gpt-5.6-sol"
    assert obs.dispatched_lane == _lane("codex", provider="openai")


def test_run_competitive_stamps_dispatched_lane(tmp_path):
    qc = QuotaCoordinator(tmp_path / "q", {"claude": 2, "codex": 4})
    cands = [
        Candidate(seat="build", model="claude-sonnet-5", prompt="p", provider="anthropic", pool="claude"),
        Candidate(seat="build", model="gpt-5.6-terra", prompt="p", provider="openai", pool="codex"),
    ]
    winner, losers, _jo, rec = run_competitive(
        cands, judge=lambda results, rubric: {"winner_index": 0},
        snapshot=_snapshot(), quota=qc, capture_root=tmp_path, dispatch=_stub())
    lanes = {c["requested_model"]: c.get("dispatched_lane") for c in rec["candidates"]}
    assert lanes["claude-sonnet-5"] == {"provider": "anthropic", "transport": "native",
                                        "auth_mode": "subscription_oauth", "pool": "claude", "credential_ref": None}
    assert lanes["gpt-5.6-terra"]["provider"] == "openai" and lanes["gpt-5.6-terra"]["pool"] == "codex"


# --- #465 T5: run_seat resolves effort + stamps resolution on the Observation ---

def _codex_primary_snapshot():
    table = {
        "schema_version": "1",
        "pools": {"claude": {"concurrency": 2}, "codex": {"concurrency": 4}},
        "seats": {
            "build": {"primary": {"model": "gpt-5.6-terra", "lane": _lane("codex", provider="openai")},
                      "chain": []},
        },
        "forbidden_combinations": [{"model_pattern": "haiku", "reason": "never Haiku"}],
    }
    return routing.RoutingSnapshot.from_table(table)


def test_run_seat_resolves_effort_passes_native_and_stamps(tmp_path):
    calls = []
    qc = QuotaCoordinator(tmp_path / "p", {"claude": 2, "codex": 4, "zhipu": 2})
    obs = run_seat("solo", "hi", snapshot=_snapshot(), quota=qc, capture_root=tmp_path,
                   effort="max", dispatch=_stub(record=calls))
    # native passed to the adapter (solo = claude-opus-4-8, supports max -> identity)
    assert calls[0][1] == "claude-opus-4-8"
    assert obs.effort == {"requested": "max", "native": "max", "resolution": "identity",
                          "capability_revision": 1}


def test_run_seat_none_effort_claude_identity_null(tmp_path):
    qc = QuotaCoordinator(tmp_path / "p", {"claude": 2, "codex": 4, "zhipu": 2})
    obs = run_seat("solo", "hi", snapshot=_snapshot(), quota=qc, capture_root=tmp_path,
                   dispatch=_stub())
    assert obs.effort == {"requested": None, "native": None, "resolution": "identity",
                          "capability_revision": 1}


def test_run_seat_none_effort_codex_adapter_default_high(tmp_path):
    seen = {}
    qc = QuotaCoordinator(tmp_path / "p", {"claude": 2, "codex": 4})
    def dispatch(engine_name, req, **kw):
        seen["effort"] = req.effort
        return _obs(req, engine_name=engine_name)
    obs = run_seat("build", "hi", snapshot=_codex_primary_snapshot(), quota=qc,
                   capture_root=tmp_path, dispatch=dispatch)
    assert seen["effort"] == "high"  # native passed to the adapter
    assert obs.effort == {"requested": None, "native": "high", "resolution": "adapter_default",
                          "capability_revision": 1}


def test_run_seat_stepdown_recorded(tmp_path):
    # gpt-5.5 rejects max -> stepdown to xhigh, recorded on the Observation
    table = {
        "schema_version": "1", "pools": {"codex": {"concurrency": 4}},
        "seats": {"build": {"primary": {"model": "gpt-5.5", "lane": _lane("codex", provider="openai")},
                            "chain": []}},
        "forbidden_combinations": [{"model_pattern": "haiku", "reason": "x"}],
    }
    snap = routing.RoutingSnapshot.from_table(table)
    seen = {}
    qc = QuotaCoordinator(tmp_path / "p", {"codex": 4})
    def dispatch(engine_name, req, **kw):
        seen["effort"] = req.effort
        return _obs(req, engine_name=engine_name)
    obs = run_seat("build", "hi", snapshot=snap, quota=qc, capture_root=tmp_path,
                   effort="max", dispatch=dispatch)
    assert seen["effort"] == "xhigh"
    assert obs.effort["resolution"] == "stepdown" and obs.effort["native"] == "xhigh"
