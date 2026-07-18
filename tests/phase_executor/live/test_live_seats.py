"""Live ACs (marked `live`, skipped unless RUN_LIVE=1 — need real CLIs/SDK auth, never CI):

AC3: one seat call per adapter, requested == actual asserted.
AC4: a 3-candidate cross-pool parallel bake-off completes within 1.3x the slowest candidate.

These are the real end-to-end proof the adapters + engine work against the actual providers.
"""
import os
import shutil
import time

import pytest

from phase_executor import contract
from phase_executor.adapters import claude_cli, codex_cli, zhipuai_sdk
from phase_executor.adapters.base import AdapterRequest
from phase_executor.engine import Candidate, run_competitive
from phase_executor.quota import QuotaCoordinator
from phase_executor.routing import RoutingSnapshot

pytestmark = pytest.mark.live

PROMPT = "Reply with exactly: OK"
DIGEST = "sha256:live"

_HAVE_CLAUDE = shutil.which("claude") is not None
_HAVE_CODEX = shutil.which("codex") is not None
_HAVE_UV = shutil.which("uv") is not None
_HAVE_GLM = bool(os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("ZHIPU_API_KEY") or os.environ.get("GLM_API_KEY"))


@pytest.mark.skipif(not _HAVE_CLAUDE, reason="claude CLI not on PATH")
def test_ac3_claude_seat(tmp_path):
    req = AdapterRequest(seat="review", requested_model="claude-haiku-4-5", prompt=PROMPT, timeout=180)
    obs = claude_cli.run(req, run_id="ac3", attempt_id="claude", capture_root=tmp_path, routing_config_digest=DIGEST)
    assert obs.parse_status == "ok", obs.to_dict()
    assert contract.models_match(req.requested_model, obs.actual_model), (req.requested_model, obs.actual_model)
    assert obs.usage["input"] >= 0 and obs.usage["output"] >= 0


@pytest.mark.skipif(not _HAVE_CODEX, reason="codex CLI not on PATH")
def test_ac3_codex_seat(tmp_path):
    req = AdapterRequest(seat="review", requested_model="gpt-5.6-sol", prompt=PROMPT, transport="native", effort="low", timeout=180)
    obs = codex_cli.run(req, run_id="ac3", attempt_id="codex", capture_root=tmp_path, routing_config_digest=DIGEST, cwd=str(tmp_path))
    assert obs.parse_status == "ok", obs.to_dict()
    assert contract.models_match(req.requested_model, obs.actual_model), (req.requested_model, obs.actual_model)
    assert obs.usage["input"] >= 0 and obs.usage["output"] >= 0


@pytest.mark.skipif(not (_HAVE_UV and _HAVE_GLM), reason="uv and/or ZHIPUAI_API_KEY unavailable")
def test_ac3_zhipuai_seat(tmp_path):
    req = AdapterRequest(seat="review", requested_model="glm-4.5-flash", prompt=PROMPT, timeout=180)
    obs = zhipuai_sdk.run(req, run_id="ac3", attempt_id="zhipu", capture_root=tmp_path, routing_config_digest=DIGEST)
    assert obs.parse_status == "ok", obs.to_dict()
    assert contract.models_match(req.requested_model, obs.actual_model), (req.requested_model, obs.actual_model)
    assert obs.usage["input"] >= 0 and obs.usage["output"] >= 0


def _snapshot():
    lane = lambda pool, prov: {"provider": prov, "transport": "native", "auth_mode": "subscription_oauth", "credential_ref": None, "pool": pool}
    table = {
        "schema_version": "1",
        "pools": {"claude": {"concurrency": 2}, "codex": {"concurrency": 4}, "zhipu": {"concurrency": 2}},
        "seats": {"x": {"primary": {"model": "claude-haiku-4-5", "lane": lane("claude", "anthropic")}, "chain": []}},
    }
    return RoutingSnapshot.from_table(table)


def _judge_first(results, rubric):
    ok = [i for i, r in enumerate(results) if r.parse_status == "ok"]
    return {"winner_index": ok[0] if ok else 0, "scores": [1.0] * len(results)}


@pytest.mark.skipif(not (_HAVE_CLAUDE and _HAVE_CODEX and _HAVE_UV and _HAVE_GLM),
                    reason="AC4 needs claude + codex + uv + ZHIPUAI_API_KEY")
def test_ac4_parallel_bakeoff(tmp_path):
    candidates = [
        Candidate(seat="design", model="claude-haiku-4-5", prompt=PROMPT, provider="anthropic", pool="claude"),
        Candidate(seat="design", model="gpt-5.6-sol", prompt=PROMPT, provider="openai", pool="codex"),
        Candidate(seat="design", model="glm-4.5-flash", prompt=PROMPT, provider="zhipuai", pool="zhipu"),
    ]
    qc = QuotaCoordinator(tmp_path / "q", _snapshot().pool_concurrency())
    start = time.monotonic()
    winner, losers, judge_obs, record = run_competitive(
        candidates, judge=_judge_first, snapshot=_snapshot(), quota=qc, capture_root=tmp_path,
        require_parallel=True,
    )
    wall_ms = int((time.monotonic() - start) * 1000)
    all_obs = [winner, *losers]
    slowest_ms = max(o.timing_ms for o in all_obs)
    ok_count = sum(1 for o in all_obs if o.parse_status == "ok")
    assert ok_count >= 2, [o.to_dict() for o in all_obs]  # at least the two premium lanes answered
    assert wall_ms <= 1.3 * slowest_ms, f"not parallel: wall={wall_ms}ms slowest={slowest_ms}ms"
    print(f"\nAC4 bake-off: wall={wall_ms}ms slowest_candidate={slowest_ms}ms ratio={wall_ms/slowest_ms:.2f} ok={ok_count}/3")


# --- #426: one REAL review-seat dispatch under the shipped config (fable primary) ---
import json as _json
import pathlib as _pl
from phase_executor.contract import models_match
from phase_executor.engine import run_seat
from phase_executor.routing import RoutingSnapshot

_SHIPPED = _pl.Path(__file__).resolve().parents[3] / "phase_executor" / "src" / "phase_executor" / "routing" / "rawgentic.routing-table.json"


@pytest.mark.skipif(not _HAVE_CLAUDE, reason="needs the claude CLI")
def test_live_review_seat_dispatch_on_fable_426(tmp_path):
    """#426 AC: a REAL review-seat dispatch under the exact shipped config records the resolved
    model and a successful fable response; requested == actual."""
    snap = RoutingSnapshot.from_table(_json.loads(_SHIPPED.read_text()))
    qc = QuotaCoordinator(tmp_path / "q", snap.pool_concurrency())
    obs = run_seat("review", PROMPT, snapshot=snap, quota=qc, capture_root=tmp_path,
                   author_provider="openai", timeout=120.0)
    assert obs.requested_model == "claude-fable-5", "review primary is fable"
    assert obs.parse_status == "ok", f"expected ok, got {obs.parse_status}"
    assert models_match(obs.requested_model, obs.actual_model), f"requested {obs.requested_model} != actual {obs.actual_model}"


@pytest.mark.live
def test_claude_grants_budget_live_semantics(tmp_path):
    """#465 deferred-to-target (executes in the W9 proving run #472): mapped-tool acceptance
    + budget flag behavior under the REAL claude CLI — flag EXISTENCE was probe-confirmed;
    this cell proves the semantics (launch succeeds with --allowedTools + --max-budget-usd,
    envelope parses)."""
    from phase_executor import contract
    from phase_executor.adapters import claude_cli
    from phase_executor.adapters.base import AdapterRequest
    m = {"session_policy": "fresh", "tool_grants": ["read"], "effort": "low",
         "confinement": {"anthropic": "hooks"}, "bounds": {"timeout_s": 120, "max_budget_usd": 0.5}}
    profile = contract.profile_from_manifest(m, engine="claude")
    req = AdapterRequest(seat="ship", requested_model="claude-sonnet-5",
                         prompt="Reply with the single word: ok", timeout=120.0, profile=profile)
    obs = claude_cli.run(req, run_id="live465", attempt_id="0-a", capture_root=tmp_path,
                         routing_config_digest="sha256:live")
    assert obs.parse_status == contract.OK and obs.actual_model
