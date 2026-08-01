# Design note — context-meter retune 55/75 + overshoot guard (#797)

**Issue:** #797 · **Epic:** #756 · **Date:** 2026-08-01 · **Lane:** small-standard
**Baseline:** 6520 passed, 21 skipped, exit 0 @ `9b37a11d`
**Scope:** Parts 1+2 of the issue. `Part of #797` — Part 3 (rolling summary) and AC4 follow.

## What the owner asked for, and the problem with doing only that

> "if 60-80 is the degredation point lets change to 55 and 75"

`DEFAULT_CHECK_IN_PCT = 35 → 55`, `DEFAULT_ACT_PCT = 50 → 75`. The gap becomes **20**, which clears
`MIN_TIER_GAP_PCT = 10` (`context_meter.py:86`) — the check the issue asked for, confirmed.

**But raising the act line alone would make the meter worse, and the issue says so.** Today it fired
the directive tier at **69%** against a 50% act line. Move the line to 75 with the same lag and it
fires around **~94%** — past the degradation band the retune exists to stay inside. The retune would
defeat its own purpose.

## Root cause of the lag (traced, not assumed)

The issue observed the overshoot; it did not explain it. It is **not** the cadence throttle:
`Stop` is explicitly **exempt** from it (`context_meter.py:35-36`, #713) and fires once per turn.

The real cause is structural: **the meter can only act at a turn boundary, and one large turn can
add ~19 points of a 1M window.** The 69%-against-50% observation is exactly one such turn. No
threshold value fixes that — the tier is always read *after* the jump that crossed it.

`#729` (directive tier unreachable mid-turn) is the child that would allow mid-turn firing. It sits
**after** #797 in the queue, so deferring to it — which AC2 permits — would ship the raise while its
own stated hazard is live.

## The fix: fire on the projection, not the crossing

Persist one extra sample and fire when the **next** turn is projected to cross:

```
delta      = max(0, pct_now - pct_last)            # per-turn fill growth
projected  = pct_now + min(delta, act_pct - check_in_pct)
fire ACT  when pct_now >= act_pct  OR  projected >= act_pct
```

- **Why clamp the lead to `act - check_in` (20):** an anomalous jump must not let the act tier fire
  below the check-in band. The clamp bounds how far ahead the projection can reach, so the tiers
  keep their ordering by construction rather than by luck.
- **No prior sample ⇒ no projection.** First observation behaves byte-identically to today, which
  matters because #734 is precisely "the meter is blind for the first minutes" — this change must
  not pretend to fix that.
- **State cost:** two keys (`last_pct`, `last_pct_turn`). The module's docstring says state "carries
  only cadence bookkeeping (whose loss costs one late check, not a lost warning)" — this preserves
  that: losing the sample costs one *un-projected* check, never a lost warning.
- **Monotonic-only:** `max(0, …)` means a shrinking context (compaction) never produces a negative
  delta that would suppress firing.

This addresses AC2 **in the same change**, as Part 1 asks, rather than deferring it.

## The tension I am not hiding

`context_meter.py:78-83` records the rationale for 35/50:

> "At 35% of a 1M window a session has ~650k tokens in hand to finish its phase and hand over
> properly; **at 70% it has 300k and is already choosing what to drop.**"

That argument is about **room to write a good handoff**. The owner's instruction is about **where
output quality degrades**. Both are true and they pull opposite ways: 75 sits inside the band the
existing comment calls already-compromised.

The owner's instruction is later and authoritative, so 55/75 ships. But the comment is **updated
rather than deleted**, so the trade-off stays visible to whoever reads it next — and AC4 (the
reload-vs-fill measurement) is exactly the evidence that would settle it. That measurement needs
#777, which is blocked on #815, so it is recorded as blocked, not quietly dropped.

## Files

| File | Change |
|---|---|
| `hooks/context_meter.py` | thresholds 35/50 → 55/75; the `:85` "gap 15" comment → 20; the `:78-83` rationale updated to record both constraints; `project_fill` pure helper; two state keys |
| `tests/hooks/test_context_meter.py` | the tests below |
| `docs/context-meter.md` | new defaults + env overrides + the projection rule |
| `README.md` | changelog entry |

## Failure modes

| Failure | Behavior |
|---|---|
| no prior sample | no projection — today's behavior exactly |
| state lost / corrupt | no projection for one check; warning never lost |
| context shrank (compaction) | `max(0, …)` ⇒ delta 0, no suppression |
| anomalous huge delta | lead clamped to `act - check_in`; tier ordering preserved |
| env override sets a gap < 10 | existing validator rejects and falls back to defaults — unchanged |

## Platform / external dependencies

platform_apis: none

## Security implications

- Pure arithmetic on a percentage already computed and already persisted in the meter's own state.
- No new file, path, network, or credential surface. The two added state keys are integers.
- The state file's existing atomic-write and O_EXCL marker discipline is untouched.

## Tests

1. 55/75 are the defaults; env overrides still apply; the gap validator still rejects a gap < 10.
2. **Projection fires early:** `pct 60`, previous `45` (delta 15) ⇒ projected 75 ⇒ ACT fires at 60,
   where today it would wait for 75 and land at ~90.
3. **No prior sample ⇒ no projection** — first check behaves exactly as today (guards #734's scope).
4. **Clamp:** a delta of 40 does not let ACT fire below the check-in band.
5. **Shrinking context** (pct 70 → 55) yields delta 0, not negative; no suppression.
6. Tier ordering holds for every (check_in, act) pair the validator admits.
7. State round-trips `last_pct`/`last_pct_turn`; a corrupt/absent value degrades to no-projection.
