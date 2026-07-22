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
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import contract, routing
from .adapters import ADAPTERS
from .adapters.base import AdapterRequest
from .quota import QuotaCoordinator

# provider (lane) -> adapter engine family
PROVIDER_ENGINE = {"anthropic": "claude", "openai": "codex", "zhipuai": "zhipuai"}
# Statuses that warrant a chain fallback: the transport failed to deliver a usable response.
# Single-sourced in contract so engine (fallback) and enforce (breach classification) agree.
AVAILABILITY_FAILURES = contract.AVAILABILITY_FAILURES


class InfeasibleBakeoff(RuntimeError):
    """A candidate set cannot run within pool limits with the required parallelism."""


# D9 / cross_model_author contract: the rule is relational (reviewer must not share the author's
# engine) and is correctly INERT when author_provider is None — a seat call with no author is not a
# review, so there is nothing to conflict with. A REVIEW-seat caller MUST pass author_provider so
# the rule fires (same-engine targets are skipped chain-aware); that "reviews always supply the
# author" policy is enforced at the review-wiring layer (E3) and by its tests, not here — requiring
# it for every seat would wrongly break intake/design/build/ship, which have no author.


def _new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def _engine_for(lane: dict) -> str:
    return PROVIDER_ENGINE.get(lane["provider"], lane["provider"])


def _manifest_for(snapshot: routing.RoutingSnapshot, seat_name: str) -> Optional[dict]:
    """The seat's capability manifest, or None (manifest-less test/legacy table, or a
    bake-off candidate naming a seat absent from the table — byte-identical compat)."""
    try:
        return snapshot.seat(seat_name).get("manifest")
    except routing.RoutingError:
        return None


def _reject_mutating_manifest(manifest: Optional[dict], seat_name: str) -> None:
    """#558 AC2 (r6/A-F1): a MUTATING manifest never dispatches on the sync/competitive
    paths — no worktree exists here, so profile derivation would fail and silently
    keeping an uncapped no-profile dispatch is exactly the gap AC2 closes. Mutating
    dispatch is exclusively the supervised path's job."""
    if manifest is not None and {"edit", "bash"} & set(manifest.get("tool_grants") or ()):
        raise contract.CompositionError(
            f"seat {seat_name!r}: a MUTATING manifest cannot dispatch on the "
            "sync/competitive path (no worktree, no capped composition) — supervised "
            "dispatch only (#558 AC2)")


def _sync_profile(manifest: Optional[dict], engine: str):
    """Per-target cap profile for the sync/competitive paths (#558 SR-F2), derived only
    for a NON-mutating manifest (callers reject mutating first). None manifest → None
    (the AdapterRequest default keeps pre-#558 behavior byte-identical)."""
    if manifest is None:
        return None
    return contract.profile_from_manifest(manifest, engine=engine)


def _effective_timeout(manifest: Optional[dict], caller_timeout: float) -> float:
    """min(caller, bounds.timeout_s): the declared bound tightens, never loosens
    (pass-4 S-F6 — one rule on both the sync and supervised paths)."""
    bounds = (manifest or {}).get("bounds") or {}
    ts = bounds.get("timeout_s")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0:
        return min(caller_timeout, float(ts))
    return caller_timeout


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
    """Run a seat to an Observation, with chain-aware fallback on availability failures.

    For a REVIEW seat, pass ``author_provider`` (the authored artifact's engine) so the D9
    cross_model_author rule skips same-engine targets; omitting it leaves the rule inert (correct
    for non-review seats). See the module-level D9 note."""
    run_id = run_id or _new_run_id()
    targets = routing.eligible_targets(seat, snapshot, author_provider=author_provider)
    if not targets:
        raise routing.ChainExhausted(f"seat {seat!r}: no eligible target")
    # #558 AC2 (SR-F2): the manifest cap reaches every ordinary dispatch — mutating
    # manifests are rejected fail-loud BEFORE any candidate dispatch
    manifest = _manifest_for(snapshot, seat)
    _reject_mutating_manifest(manifest, seat)
    eff_timeout = _effective_timeout(manifest, timeout)
    last: Optional[contract.Observation] = None
    primary_model = targets[0]["model"]
    import time  # noqa: PLC0415
    for i, target in enumerate(targets):
        lane = target["lane"]
        engine = _engine_for(lane)
        # #465 AC3: resolve the requested effort against THIS target's model + engine ONCE,
        # pass the native value to the adapter, and stamp the resolution object onto the
        # returned Observation (the dispatched_lane precedent below). recorded == sent.
        eff = contract.resolve_effort(target["model"], effort, engine=engine)
        profile = _sync_profile(manifest, engine)
        req_kw = {} if profile is None else {"profile": profile}
        req = AdapterRequest(
            seat=seat, requested_model=target["model"], prompt=prompt, transport=lane["transport"],
            context=tuple(context), correlation_id=correlation_id, effort=eff.native,
            timeout=eff_timeout, credential_ref=lane.get("credential_ref"), **req_kw,
        )
        attempt_id = f"{i}-{uuid.uuid4().hex[:8]}"
        fallback_reason = None if i == 0 else f"fallback from {primary_model}: {last.parse_status if last else 'unknown'}"
        acct = lane.get("credential_ref") or "default"
        t0 = time.monotonic()
        with quota.acquire(lane["pool"], account=acct, timeout=timeout):
            queued_ms = int((time.monotonic() - t0) * 1000)
            obs = dispatch(engine, req, run_id=run_id, attempt_id=attempt_id, capture_root=capture_root,
                           digest=snapshot.config_digest, queued_ms=queued_ms, fallback_reason=fallback_reason)
        # #425 B: stamp the lane we ACTUALLY dispatched on (this target, not the request) so the
        # run-end audit can bind receipt<->observation independently of the receipt object.
        obs = replace(obs, dispatched_lane=dict(lane), effort=eff.to_dict())
        if obs.parse_status not in AVAILABILITY_FAILURES:
            return obs  # success or a non-availability failure (model responded) -> do not fall back
        last = obs
    return last  # chain exhausted on availability failures: return the last attempt (honest, non-ok)


# ---- competitive ------------------------------------------------------------

@dataclass(frozen=True)
class Candidate:
    """One bake-off candidate. ``pool`` selects the quota pool (REQUIRED — an unset/unknown pool
    would run unbounded and silently defeat the ceiling, so run_competitive validates it against
    the routing table); ``provider`` selects the adapter."""
    seat: str
    model: str
    prompt: str
    provider: str
    pool: str
    transport: str = "native"
    auth_mode: str = "subscription_oauth"
    credential_ref: Optional[str] = None
    context: Sequence[str] = ()

    @property
    def engine(self) -> str:
        return PROVIDER_ENGINE.get(self.provider, self.provider)

    def lane(self) -> dict:
        """The full lane this candidate dispatches on (#425 B: stamped as dispatched_lane)."""
        return {"provider": self.provider, "transport": self.transport, "auth_mode": self.auth_mode,
                "pool": self.pool, "credential_ref": self.credential_ref}

    def as_target(self) -> dict:
        """A routing target view for forbidden_combinations evaluation AND enforcement identity.
        Single-sources the lane via ``lane()`` (#425 Step-8a: the forbidden-eval view, the
        receipt/target_identity source, and the stamped dispatched_lane must be the SAME lane —
        otherwise a competitive candidate's stamped 6-field lane cannot be reconciled against a
        3-field as_target identity)."""
        return {"model": self.model, "lane": self.lane()}


def _harness_observation(c: "Candidate", *, run_id: str, digest: str, reason: str) -> contract.Observation:
    """A non-ok Observation standing in for a candidate that could not run (raised, or forbidden)."""
    from .capture import hash_text  # noqa: PLC0415
    obs = contract.Observation(
        run_id=run_id, attempt_id="harness", correlation_id=None, seat=c.seat, engine=c.engine,
        transport=c.transport, requested_model=c.model, actual_model=None, prompt_hash=hash_text(c.prompt),
        context_hashes=[], usage=None, timing_ms=0, queued_ms=0,
        process={"exit_code": None, "timed_out": False}, parse_status=contract.HARNESS_ERROR,
        parsed_payload=None, raw_capture_path=None, fallback_reason=reason[:500], routing_config_digest=digest,
    )
    contract.validate_observation(obs.to_dict())
    return obs


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
    # #465 AC3 scope: the competitive path takes NO effort input (a Candidate has no effort
    # field), so there is nothing to resolve and no effort object is stamped — nothing was
    # requested, so nothing is misrecorded. The codex adapter still applies its
    # registry-sourced None default (ENGINE_NONE_EFFORT) so the wire stays byte-identical.
    # #558 AC2 (SR-F2): same per-target cap derivation as run_seat (run_competitive
    # already rejected mutating manifests before fan-out)
    manifest = _manifest_for(snapshot, c.seat)
    profile = _sync_profile(manifest, c.engine)
    req_kw = {} if profile is None else {"profile": profile}
    req = AdapterRequest(
        seat=c.seat, requested_model=c.model, prompt=c.prompt, transport=c.transport,
        context=tuple(c.context), credential_ref=c.credential_ref,
        timeout=_effective_timeout(manifest, 300.0), **req_kw,
    )
    attempt_id = uuid.uuid4().hex[:8]
    acct = c.credential_ref or "default"
    import time  # noqa: PLC0415
    t0 = time.monotonic()
    with quota.acquire(c.pool, account=acct, timeout=300.0):
        queued_ms = int((time.monotonic() - t0) * 1000)
        obs = dispatch(c.engine, req, run_id=run_id, attempt_id=attempt_id, capture_root=capture_root,
                       digest=snapshot.config_digest, queued_ms=queued_ms, fallback_reason=None)
    # #425 B: stamp the candidate's actual dispatch lane (parity with run_seat).
    return replace(obs, dispatched_lane=c.lane())


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
    author_provider: Optional[str] = None,
    require_parallel: bool = False,
    max_workers: Optional[int] = None,
    dispatch: Callable[..., contract.Observation] = _dispatch_real,
) -> tuple:
    """Run candidates concurrently, judge, and persist via the caller's sink.

    Returns ``(winner, losers, judge_obs, record)``. ``judge`` returns a dict with ``winner_index``
    and optionally ``judge_obs``/``degraded``. On a judge exception, ``failure_strategy`` (if given)
    must return the same shape; otherwise the exception propagates.

    Enforcement (parity with run_seat — the engine's invariants apply to bake-offs too):
    - Each candidate's ``pool`` MUST be a pool declared in the routing table, else ValueError
      (an unknown/unset pool would run unbounded and defeat the ceiling).
    - Each candidate is checked against the table's ``forbidden_combinations`` (never-Haiku, and
      the cross_model_author rule when ``author_provider`` is given); a forbidden candidate is NOT
      dispatched — it is recorded as a non-ok Observation. If NO candidate is eligible, raise
      ChainExhausted.

    Failure isolation (adopted f4): a candidate that raises inside dispatch is recorded as a
    ``harness_error`` Observation (never cancels the others or aborts the round); its permit is
    already released in ``_run_candidate``'s ``finally``. A sink exception never erases results."""
    run_id = run_id or _new_run_id()
    candidates = list(candidates)
    # #558 AC2 (r6/A-F1): mutating manifests reject fail-loud BEFORE candidate fan-out —
    # inside the pool a raise would soften into a harness_error Observation
    for c in candidates:
        _reject_mutating_manifest(_manifest_for(snapshot, c.seat), c.seat)
    # seat validation — fail closed on an unknown seat (H3 #559): an unknown seat resolves to
    # NO manifest (_manifest_for swallows RoutingError → None) and would dispatch UNCAPPED, the
    # cost-cap bypass. No no-seats carve-out (r3 C4/ADV-2): a table lacking a usable seats
    # mapping raises the same way for any candidate seat. A seat that EXISTS but declares no
    # manifest is the one legitimate None (run_seat parity) and is untouched here.
    for c in candidates:
        try:
            snapshot.seat(c.seat)
        except routing.RoutingError as exc:
            raise ValueError(
                f"candidate {c.model!r}: seat {c.seat!r} is not a declared routing seat "
                f"— an unknown seat would dispatch uncapped ({exc})") from exc
    # pool validation — fail closed on an unknown/unset pool (finding f: ceiling bypass)
    known_pools = set(snapshot.pool_concurrency())
    for c in candidates:
        if c.pool not in known_pools:
            raise ValueError(f"candidate {c.model!r}: pool {c.pool!r} is not a declared routing pool {sorted(known_pools)}")
    # forbidden_combinations — bake-offs enforce the same invariants as run_seat
    forbidden = {i: routing.target_forbidden_reason(c.as_target(), snapshot, author_provider=author_provider)
                 for i, c in enumerate(candidates)}
    runnable = [i for i in range(len(candidates)) if forbidden[i] is None]
    if not runnable:
        raise routing.ChainExhausted(
            f"run_competitive: no eligible candidate (all {len(candidates)} forbidden"
            + (f" for author_provider={author_provider!r}" if author_provider else "") + ")"
        )
    if require_parallel:
        assert_parallel_feasible([candidates[i] for i in runnable], snapshot)
    results: List[contract.Observation] = [None] * len(candidates)  # type: ignore
    for i, reason in forbidden.items():
        if reason is not None:
            results[i] = _harness_observation(candidates[i], run_id=run_id, digest=snapshot.config_digest,
                                              reason=f"forbidden: {reason}")
    n = max_workers or max(1, len(runnable))
    with _cf.ThreadPoolExecutor(max_workers=n) as ex:
        futs = {
            ex.submit(_run_candidate, candidates[i], snapshot=snapshot, quota=quota,
                      capture_root=capture_root, run_id=run_id, dispatch=dispatch): i
            for i in runnable
        }
        for fut in _cf.as_completed(futs):
            i = futs[fut]
            try:
                results[i] = fut.result()
            except Exception as exc:  # noqa: BLE001 — a raising candidate (QuotaTimeout, capture OSError) must not abort the round (f4)
                results[i] = _harness_observation(candidates[i], run_id=run_id,
                                                  digest=snapshot.config_digest, reason=f"dispatch raised: {exc}")

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
