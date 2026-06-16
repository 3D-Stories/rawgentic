# Iteration-4 — slim skill vs baseline (all 7 scenarios)

## What changed
Rewrote the skill from **608 → 173 lines**. Dropped: the 3-judge parallel critique,
the ambiguity circuit-breaker *step*, loop-back iterations + volume thresholds, the
opt-in adversarial sub-step, per-run conditional memorization, and the heavy
completion-gate. Kept: config-based repo targeting, dedup, template conformance,
codebase grounding (verify components exist), conventional titles, user review, and
the judges' real value distilled into a one-paragraph `<quality-bar>` ("don't
hallucinate, don't fabricate, bound an over-broad ask"). No subagents.

## Quality — slim is ≥ baseline on every scenario
| Scenario | slim | baseline |
|---|---|---|
| feature-quality | 100% | 87.5% (missed conventional title) |
| dedup-hit | 100% | 100% |
| config-decoy | 100% | 100% |
| bug-report | 100% | 85.7% (missed conventional title) |
| false-premise | 100% | 100% |
| vague-perf | 100% | 100% |
| over-broad | 100% | 100% |
| **overall** | **100%** | **96.2%** |

The slim skill **recovers the conventional-title edge** (the one thing that
discriminated the original from baseline) while matching the original's 100% on
everything else. So no quality was lost in the slimming.

## Cost — most of the original's overhead is gone
Medians (robust to the one outlier below):

| Version | Pass | Time (median) | Tokens (median) |
|---|---|---|---|
| Original (608 ln) | 100% | 268s | 68.2k |
| **Slim (173 ln)** | **100%** | **145s** | **48.0k** |
| Baseline (no skill) | 96% | 107s | 42.3k |

- Slim vs **original**: ~46% less wall-clock, ~30% fewer tokens — same quality.
- Slim vs **baseline**: +~35% time, +~13% tokens — buys a measurable quality edge
  (conventional titles + guaranteed dedup/scope discipline) that baseline doesn't
  reliably give.

## The one outlier (honest note)
`over-broad` slim took **828s / 59.6k tokens** because the model chose to actually
**split into 1 umbrella + 5 child issues** rather than file one epic (the original
filed a single epic in 315s). Both pass the assertion; the split is arguably the
*more* correct outcome, just more expensive. It inflates the slim *mean* time to
230s ± 265s — hence medians are the honest summary. Excluding it, slim averages
~130s / 47k.

## Verdict
The slim version is the right artifact: it keeps the only quality edge the skill
ever had over a bare model, costs a third less than the original, and sits close to
baseline. The heavy WF1 critique pipeline was pure overhead for issue creation with
a current model — this confirms it can be removed without regression.

## Caveats (unchanged)
Single run per cell; synthetic two-file project; one reviewer. The remaining
slim-vs-baseline gap is narrow and rests on a project convention (conventional
titles) — if rawgentic ever drops that convention, the skill's measurable edge goes
with it and the case for even the slim version weakens.
