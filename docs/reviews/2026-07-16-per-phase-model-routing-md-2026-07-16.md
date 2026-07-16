# Adversarial Review — 2026-07-16-per-phase-model-routing.md

- Date: 2026-07-16
- Artifact type: plan
- Reviewer: Codex (model gpt-5.6-sol, reasoning effort high)
- Findings: 6 (Critical 0, High 3, Medium 3, Low 0)

## Summary

The plan proposes routing WF2/WF3 work among Opus, Fable, and Sonnet based on benchmark results. Its main risks are activating routing before fallback support, relying on unverified provider behavior, and leaving acknowledged workflow dependencies broken.

## Findings

### 1. [High] completeness · high confidence — §4 routing table, ship row; §5.3; §7 Child 4

> | ship (Steps 12-16) | driver inline today | ship-shaped tasks → **sonnet-5** via standard-task down-route; final merge/verify stays driver | sonnet 88 real edge AND cheapest — double win | reuse implementation fork; no new machinery |

The current ship seat is explicitly inline, while the proposed selector only affects delegated implementation tasks. Merely classifying ship tasks as standard does not create a delegation boundary, so the tasks remain on the Opus driver and the stated ship-to-Sonnet routing is not implemented.

**Recommendation:** Change §5.3 and Child 4 to require explicit delegation of each eligible ship task before calling `select_impl_model`, define which Steps 12–16 remain driver-only, and test both delegation-disabled and delegation-enabled paths by asserting the actual executing model.

### 2. [High] completeness · high confidence — §8 Owner attention required, item 2

> 2. **`~/.codex/config.toml` is broken for every Codex-backed flow** (WF5 gpt backend,
>    WF13, codex-rescue): a leftover CCR "managed profile" points Codex at
>    `http://127.0.0.1:3456` (dead — you said not to restart CCR) with
>    `model=openai/gpt-4o-mini`. This run worked around it with a scratchpad
>    `CODEX_HOME` (auth copied, native provider, gpt-5.6-sol) — **not durable**. Fix =
>    delete the two CCR-managed blocks from `~/.codex/config.toml` (backup exists in
>    scratchpad). One-line decision; I did not edit your global config unattended.

The plan acknowledges that every Codex-backed gate is currently broken but treats the repair only as an owner decision, with no prerequisite child, verification, or durable configuration step. Implementing children 1–7 therefore leaves WF5/WF13 unavailable despite the plan relying on them as cross-model gates.

**Recommendation:** Add a blocking prerequisite issue before routing activation: obtain owner approval, repair the durable Codex configuration, run smoke tests for WF5 and WF13 under that configuration, and make successful results an epic acceptance criterion. If this repair is outside scope, explicitly declare WF5/WF13 unavailable and remove claims that they provide active gates.

### 3. [High] consistency · high confidence — §7, children 1–2

> 1. **Retune rawgentic `modelRouting` (review→fable) + provenance field + tolerance test**
>    — config change, `resolve()` unknown-key tolerance test, never-Haiku regression
>    test. Smallest child; unblocks measurement of the rest. (feat, S)
> 2. **Review fallback chain: run-scoped fable→opus circuit breaker** — quota-rejection
>    recognition, run-scoped state, single provenance log line, tests for
>    trip/no-trip/never-below-opus. (feat, M)

Child 1 activates Fable before Child 2 supplies the required fallback, and the children are explicitly independent PRs. If merged in the stated sequence, any Fable quota rejection during the interim leaves review dispatch without the promised Opus fallback and can fail WF2/WF3 runs.

**Recommendation:** Change Section 7 so the circuit breaker lands before activation: make Child 1 add only provenance/schema tests, make Child 2 implement and verify fallback, and add a final child that changes `modelRouting.review` to `fable` only after Child 2 passes. Alternatively, combine activation and fallback into one atomic PR.

### 4. [Medium] ambiguity · high confidence — §5.2 Review fallback chain

> - **Run-scoped circuit breaker:** first *recognized quota/capacity rejection* from a
>   fable dispatch trips `fable_exhausted` for the rest of the run; subsequent review
>   dispatches go straight to opus. No retry-storms, no per-call probing.
> - A failed/timed-out review that is NOT a quota rejection is retried per existing error
>   handling — model substitution is only for quota. [C: gpt peer, decision 4]

The first rule includes capacity rejections as breaker triggers, while the next says substitution is only for quota. It also specifies Opus only for subsequent dispatches, not whether the rejected dispatch itself is immediately replayed on Opus. Implementers cannot derive deterministic behavior for capacity errors or the first failed review.

**Recommendation:** Replace these bullets in §5.2 with an explicit transition contract: enumerate the exact error classes/statuses that trip the breaker, state whether capacity errors qualify, require the rejected dispatch to retry once immediately on Opus after atomically setting `fable_exhausted`, and define behavior for concurrent in-flight dispatches.
**Ambiguity:** Capacity rejection is simultaneously included in and apparently excluded from model substitution, and first-call replay behavior is unstated.

### 5. [Medium] ambiguity · high confidence — §6.2 Refresh rule

> 2. **Refresh rule** (documented in `docs/model-routing.md`, new): when a new bench
>    report lands under `docs/measurements/`, a role value may move ONLY when the new
>    report shows (a) median gap > pooled per-cell sd for that role's phase(s), AND
>    (b) the candidate's worst-cell floor is acceptable for the seat (driver seats
>    additionally require gate-clean). Otherwise values hold. Re-stamp provenance on
>    every decision, including "no change".

The refresh gate is not reproducible: it does not define how multiple phases map to one role, how their gaps are aggregated, what numeric floor is “acceptable,” what “gate-clean” means, or how null cells and small samples are handled. It also calls a gap-over-standard-deviation heuristic statistically real without defining a statistical test or uncertainty policy.

**Recommendation:** Expand §6.2 and Child 5 with an executable decision table defining role-to-phase mappings, the exact pooled-SD formula, minimum sample count, null handling, phase aggregation, numeric floor thresholds, gate-clean thresholds, tie behavior, and whether the rule is an effect-size heuristic or a specified statistical test.
**Ambiguity:** Several undefined thresholds and aggregation rules can produce different routing decisions from the same benchmark.

### 6. [Medium] feasibility · high confidence — §5.1 Config

> - `review: opus → fable` is the only value change. `resolve()` already accepts it;
>   fable is not in `_BELOW_OPUS`, so no soft-floor warning fires
>   [C: model_routing_lib.py:24-26,113-116].

Acceptance by the local resolver does not prove that a real WF2/WF3 dispatch under the workspace's provider, credentials, account entitlements, model-ID mapping, and quota configuration can invoke Fable. The artifact provides no real-config integration result, so the external-platform dependency is unverifiable from the provided text.

**Recommendation:** Add a pre-activation acceptance criterion to Child 1: execute one WF2 or WF3 review dispatch using the exact workspace configuration, record the resolved provider/model ID and successful response, and verify captured real provider quota/capacity errors against the fallback classifier.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._