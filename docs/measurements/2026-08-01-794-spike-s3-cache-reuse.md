# Spike S3 — do byte-identical seat briefs hit the prompt cache? (#794)

**Verdict: INCONCLUSIVE. The experiment was not controlled well enough to answer the question,
and it must be re-run before #799 or #795 proceed.**

What IS established: the mechanism can hit (one dispatch achieved `cache_write: 0`), and an
identical *brief* did not produce reliable reuse across fresh sessions. What is NOT established:
whether a properly-controlled setup would, and what causes the misses.

Run 2026-08-01, seat `analysis` (Claude lane, `claude-opus-5`), `session_policy: fresh`,
run id `wf2-794-cf9ff806`.

## Result

| dispatch | wall time | `cache_write` | `cache_read` |
|---|---|---|---|
| 1 (cold) | 01:45:11 | 49,106 | 17,300 |
| 2 | 01:45:18 | 46,825 | 21,393 |
| 3 | 01:45:57 | **0** | 66,406 |
| 4 | 01:54:28 | 50,941 | 17,300 |
| 5 | 01:54:38 | 45,013 | 21,416 |

## Why this does not answer the question

Three defects in the experiment, all found at Step-11 review rather than by me:

1. **The dispatches were not back-to-back.** D1–D3 ran within 46 s; **D4 came 8 m 31 s later**.
   An earlier draft of this document asserted "five dispatches back-to-back over ~40 s", which is
   simply false. Read with the real timing, the data is two bursts — `write, write, HIT` then
   `write, write, (never reached a third)` — which is *consistent* with a warmup hypothesis rather
   than refuting it. Neither reading is supported.

2. **The provider inputs were NOT identical, even though the brief was.** `prompt_hash` covers
   only `req.prompt`; Anthropic's cached prefix spans tools + system + messages. Total provider
   input varied across the calls (~66.4k vs ~68.2k on alternating dispatches), so "byte-identical
   dispatch" was never actually achieved at the layer that matters. The stable
   17,300 / ~21,4xx alternation in `cache_read` is that variation showing through.

3. **Every write was the 1-hour tier, not the 5-minute tier.** The raw transport shows
   `ephemeral_1h_input_tokens` carrying the whole write on every dispatch and
   `ephemeral_5m_input_tokens: 0` throughout. An earlier draft of this document, the adapter
   comment, and `phase_executor/README.md` all reasoned from a 5-minute TTL. That reasoning was
   wrong, and with a 1-hour TTL an 8.5-minute gap should not have expired anything — so TTL does
   not explain D4's miss either.

## Four successive wrong answers, recorded deliberately

n=1 read as "cache works". n=2 as "cache fails". n=3 as "works after a two-dispatch warmup" — a
verdict that reached a commit and would have unblocked #799 and #795 on one anomalous data point.
n=5 read as "does not work", which was also over-claimed. Only the review caught that the
experiment could not support any of them.

The root cause of the first three is mechanical: `adapters/claude_cli.py` mapped `cached` to
`cache_read_input_tokens` and **discarded `cache_creation_input_tokens`**, with the observation
schema `additionalProperties: false` so nothing downstream could add it. #794's own AC2 — a
measured before/after on `cache_creation_input_tokens` — was unmeasurable against shipped code.
**That telemetry is what this PR lands.** The verdict is explicitly not shipped with it.

## What a controlled re-run needs

- Fixed inter-dispatch interval, recorded, and varied deliberately (e.g. 10 s / 2 min / 10 min).
- At least 3 dispatches per burst and ≥3 bursts, so a warmup pattern is distinguishable from noise.
- The **full provider input** captured per dispatch, not just `prompt_hash` — the prefix that
  matters includes tools and system, which the current hash does not cover.
- Tier-split writes recorded (`ephemeral_5m` vs `ephemeral_1h`) now that they are known to differ.
- A `session_policy: "resume"` arm, since lever 2 does not depend on winning a byte-exact match.

## Consequence for the epic

The epic-#756 hard gate asks whether byte-identical briefs can hit cache. **This spike cannot
answer it**, so the gate is not passed and **#799 and #795 remain blocked pending the owner's
ruling** — an inconclusive gate is not a pass. The observed hit on D3 is a reason to re-run the
spike properly, not a reason to retire the executor path.
