"""The synchronous execution engine: ``run_seat`` and ``run_competitive``.

- ``run_seat`` resolves a seat to its first eligible target (chain-aware skip via routing),
  acquires the target pool's quota (release in ``finally``), dispatches the adapter, and on an
  *availability* failure (launch/nonzero-exit/timeout) walks to the next eligible target with a
  ``fallback_reason`` — never a silent downgrade. Identity/usage/parse failures do NOT fall back
  (the model responded); the Observation is returned as-is.
- ``run_competitive`` runs candidates CONCURRENTLY across quota pools (the AC4 parallel bake-off),
  then applies a CALLER-supplied judge / failure_strategy / results sink. The engine implements
  the execution + failure semantics; the winner *policy* is the caller's (E5).

The engine is policy-free: never-Haiku and the cross-model author invariant are routing-table
``forbidden_combinations`` rows, applied in `routing`.
"""
from __future__ import annotations

import concurrent.futures as _cf
import os
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import contract, routing
from .adapters import ADAPTERS
from .adapters.base import AdapterRequest
from .quota import QuotaCoordinator

# provider (lane) -> adapter engine family
PROVIDER_ENGINE = {"anthropic": "claude", "openai": "codex", "zhipuai": "zhipuai"}
AVAILABILITY_FAILURES = frozenset({contract.LAUNCH_ERROR, contract.NONZERO_EXIT, contract.TIMEOUT})


class InfeasibleBakeoff(RuntimeError):
    """A candidate set cannot run within pool limits with the required parallelism."""


def _new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def _engine_for(lane: dict) -> str:
    return PROVIDER_ENGINE.get(lane["provider"], lane["provider"])


def _dispatch_real(engine: str, req: AdapterRequest, *, run_id: str, attempt_id: str,
                   capture_root, digest: str, queued_ms: int, fallback_reason: Optional[str]) -> contract.Observation:
    mod = ADAPTERS[engine]
    kwargs = dict(run_id=run_id, attempt_id=attempt_id, capture_root=capture_root,
                  routing_config_digest=digest, queued_ms=queued_ms, fallback_reason=fallback_reason)
    if engine == "codex":
        kwargs["cwd"] = os.getcwd()
    return mod.run(req, **kwargs)


def run_seat(
    seat: str,
    prompt: str,
    *,
    snapshot: routing.RoutingSnapshot,
    quota: QuotaCoordinator,
    capture_root,
    context: Sequence[str] = (),
    correlation_id: Optional[str] = None,
    author_provider: Optional[str] = None,
    run_id: Optional[str] = None,
    effort: Optional[str] = None,
    timeout: float = 300.0,
    dispatch: Callable[..., contract.Observation] = _dispatch_real,
) -> contract.Observation:
    """Run a seat to an Observation, with chain-aware fallback on availability failures."""
    run_id = run_id or _new_run_id()
    targets = routing.eligible_targets(seat, snapshot, author_provider=author_provider)
    if not targets:
        raise routing.ChainExhausted(f"seat {seat!r}: no eligible target")
    last: Optional[contract.Observation] = None
    primary_model = targets[0]["model"]
    import time  # noqa: PLC0415
    for i, target in enumerate(targets):
        lane = target["lane"]
        engine = _engine_for(lane)
        req = AdapterRequest(
            seat=seat, requested_model=target["model"], prompt=prompt, transport=lane["transport"],
            context=tuple(context), correlation_id=correlation_id, effort=effort, timeout=timeout,
            credential_ref=lane.get("credential_ref"),
        )
        attempt_id = f"{i}-{uuid.uuid4().hex[:8]}"
        fallback_reason = None if i == 0 else f"fallback from {primary_model}: {last.parse_status if last else 'unknown'}"
        acct = lane.get("credential_ref") or "default"
        t0 = time.monotonic()
        with quota.acquire(lane["pool"], account=acct, timeout=timeout):
            queued_ms = int((time.monotonic() - t0) * 1000)
            obs = dispatch(engine, req, run_id=run_id, attempt_id=attempt_id, capture_root=capture_root,
                           digest=snapshot.config_digest, queued_ms=queued_ms, fallback_reason=fallback_reason)
        if obs.parse_status not in AVAILABILITY_FAILURES:
            return obs  # success or a non-availability failure (model responded) -> do not fall back
        last = obs
    return last  # chain exhausted on availability failures: return the last attempt (honest, non-ok)


# ---- competitive ------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """One bake-off candidate. ``pool`` selects the quota pool; ``provider`` selects the adapter."""
    seat: str
    model: str
    prompt: str
    provider: str
    transport: str = "native"
    pool: str = ""
    credential_ref: Optional[str] = None
    context: Sequence[str] = ()

    @property
    def engine(self) -> str:
        return PROVIDER_ENGINE.get(self.provider, self.provider)


def assert_parallel_feasible(candidates: Sequence[Candidate], snapshot: routing.RoutingSnapshot) -> None:
    """Raise InfeasibleBakeoff if any pool has more candidates than its concurrency limit — such a
    set cannot run fully parallel, so it must not be advertised as a parallel bake-off (finding f5)."""
    limits = snapshot.pool_concurrency()
    per_pool: Dict[str, int] = {}
    for c in candidates:
        per_pool[c.pool] = per_pool.get(c.pool, 0) + 1
    for pool, n in per_pool.items():
        limit = limits.get(pool)
        if limit is not None and n > limit:
            raise InfeasibleBakeoff(f"pool {pool!r}: {n} candidates > concurrency {limit} (cannot run fully parallel)")


def _run_candidate(c: Candidate, *, snapshot, quota, capture_root, run_id, dispatch) -> contract.Observation:
    req = AdapterRequest(
        seat=c.seat, requested_model=c.model, prompt=c.prompt, transport=c.transport,
        context=tuple(c.context), credential_ref=c.credential_ref,
    )
    attempt_id = uuid.uuid4().hex[:8]
    acct = c.credential_ref or "default"
    import time  # noqa: PLC0415
    t0 = time.monotonic()
    with quota.acquire(c.pool, account=acct, timeout=300.0):
        queued_ms = int((time.monotonic() - t0) * 1000)
        return dispatch(c.engine, req, run_id=run_id, attempt_id=attempt_id, capture_root=capture_root,
                        digest=snapshot.config_digest, queued_ms=queued_ms, fallback_reason=None)


def run_competitive(
    candidates: Sequence[Candidate],
    *,
    judge: Callable[[List[contract.Observation], Any], dict],
    rubric: Any = None,
    failure_strategy: Optional[Callable[[List[contract.Observation], Exception], dict]] = None,
    sink: Optional[Callable[[dict], None]] = None,
    snapshot: routing.RoutingSnapshot,
    quota: QuotaCoordinator,
    capture_root,
    run_id: Optional[str] = None,
    require_parallel: bool = False,
    max_workers: Optional[int] = None,
    dispatch: Callable[..., contract.Observation] = _dispatch_real,
) -> tuple:
    """Run candidates concurrently, judge, and persist via the caller's sink.

    Returns ``(winner, losers, judge_obs, record)``. ``judge`` returns a dict with ``winner_index``
    and optionally ``judge_obs``/``degraded``. On a judge exception, ``failure_strategy`` (if given)
    must return the same shape; otherwise the exception propagates. Every candidate's permit is
    released in ``_run_candidate``'s ``finally`` even on failure; a candidate that raises surfaces
    its exception as a result error but never leaks a permit or cancels the others (E1 completes all,
    judges among what returned)."""
    run_id = run_id or _new_run_id()
    candidates = list(candidates)
    if require_parallel:
        assert_parallel_feasible(candidates, snapshot)
    n = max_workers or max(1, len(candidates))
    results: List[contract.Observation] = [None] * len(candidates)  # type: ignore
    with _cf.ThreadPoolExecutor(max_workers=n) as ex:
        futs = {
            ex.submit(_run_candidate, c, snapshot=snapshot, quota=quota, capture_root=capture_root,
                      run_id=run_id, dispatch=dispatch): i
            for i, c in enumerate(candidates)
        }
        for fut in _cf.as_completed(futs):
            i = futs[fut]
            results[i] = fut.result()  # _run_candidate returns a (possibly non-ok) Observation; a raise here is a real bug

    judge_obs = None
    degraded = False
    try:
        verdict = judge(results, rubric)
    except Exception as exc:  # noqa: BLE001 (judge is caller code; policy is caller-supplied)
        if failure_strategy is None:
            raise
        verdict = failure_strategy(results, exc)
        degraded = True
    winner_index = verdict["winner_index"]
    judge_obs = verdict.get("judge_obs")
    degraded = bool(verdict.get("degraded", degraded))
    winner = results[winner_index]
    losers = [r for i, r in enumerate(results) if i != winner_index]
    record = {
        "run_id": run_id,
        "winner_index": winner_index,
        "n_candidates": len(results),
        "judge_degraded": degraded,
        "candidates": [r.to_dict() for r in results],
        "scores": verdict.get("scores"),
    }
    if sink is not None:
        try:
            sink(record)
        except Exception:  # noqa: BLE001 — a sink failure must not erase candidate Observations or leak permits (already released)
            record["sink_error"] = True
    return winner, losers, judge_obs, record
