# Spike S3 — do byte-identical seat briefs hit the prompt cache? (#794)

**Verdict: YES, after a warmup of roughly two dispatches.** `cache_creation_input_tokens`
falls to **zero** on the third byte-identical dispatch. The executor path is **not** in
question; #799 and #795 are unblocked.

Run 2026-08-01, seat `analysis` (Claude lane, `claude-opus-5`), `session_policy: fresh`,
run id `wf2-794-cf9ff806`.

## Method

One brief with a unique nonce so dispatch 1 is a guaranteed cold miss, then the **same file**
dispatched three times back-to-back. All three carried the identical
`prompt_hash sha256:602bc8ce527da…`, so the bytes are proven identical rather than assumed.

## Result

| dispatch | uncached input | `cache_write` | `cache_read` |
|---|---|---|---|
| 1 (cold) | 2 | 49,106 | 17,300 |
| 2 | 2 | 46,825 | 21,393 |
| 3 | 2 | **0** | **66,406** |

`cache_write` 49,106 → 0 across identical dispatches: a **100% reduction** in warmup cost once
the entry is live. The ~17–21k reads present from dispatch 1 are the CLI's own system prompt,
already org-cached — not the seat brief. That is what made the early readings ambiguous.

## The finding that had to come first

**The measurement was impossible before this change.** `adapters/claude_cli.py` mapped `cached`
to `cache_read_input_tokens` and **discarded `cache_creation_input_tokens` entirely**, and the
observation schema was `additionalProperties: false`, so the field could not even be added
downstream. #794's own AC2 — "a measured before/after on `cache_creation_input_tokens`" — was
literally unmeasurable against the shipped code.

This is not a small point. Reading only the conflated field, the first two dispatches look like
a cache **failure** (large writes persisting), and the first single-turn probes look like a
cache **success** (large reads, 2 uncached input). Both readings were wrong. Only the split
plus a third data point shows the real shape: a two-dispatch warmup, then a clean hit.

**You cannot optimise a cost you do not record** — so the telemetry is the first lever, and the
other levers are now measurable rather than argued.

## What this does NOT establish

- **Cross-run reuse over time.** All three dispatches ran within ~30 s. Anthropic's 5-minute
  cache TTL means a seat dispatched less often than every 5 minutes re-pays the write. Real WF2
  phases are minutes apart, so the steady state above is the optimistic bound, not the typical one.
- **Why dispatch 2 still wrote.** Propagation delay is the plausible explanation, but it was not
  isolated. A dispatch-2 write is a real cost that a naive "identical bytes ⇒ free" model misses.
- **That the seat prompt prefix is stable in normal runs.** These briefs were fixed by
  construction. Lever 1 (freeze the prefix, volatile content last) still has to be done and
  measured; this spike only proves the mechanism pays off once the prefix IS stable.
