# Spike S3 — do byte-identical seat briefs hit the prompt cache? (#794)

**Verdict: NO, not reliably.** Four of five byte-identical dispatches paid a full
~45–51k-token prefix write. One dispatch hit fully and the next reverted. **This trips the
epic-#756 hard gate: #799 and #795 must not start until the owner rules on it.**

Run 2026-08-01, seat `analysis` (Claude lane, `claude-opus-5`), `session_policy: fresh`,
run id `wf2-794-cf9ff806`.

## Method

One brief with a unique nonce so dispatch 1 was a guaranteed cold miss, then the **same file**
dispatched five times back-to-back over ~40 s. All five carried an identical
`prompt_hash sha256:602bc8ce527d…`, the same `actual_model`, the same lane, and all returned
`ok` with `parse_status: ok` — so the bytes are proven identical rather than assumed, and no
dispatch took a different code path.

## Result

| dispatch | uncached input | `cache_write` | `cache_read` |
|---|---|---|---|
| 1 (cold) | 2 | 49,106 | 17,300 |
| 2 | 2 | 46,825 | 21,393 |
| 3 | 2 | **0** | **66,406** |
| 4 | 2 | 50,941 | 17,300 |
| 5 | 2 | 45,013 | 21,416 |

**Dispatch 3 was an anomaly, not a steady state.** Dispatches 4 and 5 replicate 1 and 2 almost
exactly — note `cache_read` is **17,300 on both D1 and D4** and ~21,4xx on both D2 and D5. That
alternating pair is the CLI's own system prompt being read from the org cache; it is stable and
**independent of the seat brief**. The brief's own ~45–51k prefix is re-written nearly every time.

## Why this was misread three times before n=5

This spike produced three successive wrong answers, and the sequence is the actual finding:

1. **Two single-turn probes** showed large reads and `input: 2` → read as "cache works". Wrong:
   the reads were the system prompt, and the conflated field hid the writes entirely.
2. **n=2 with the split** showed writes persisting → read as "cache fails". Premature.
3. **n=3** showed a zero write → read as "works, with a two-dispatch warmup". Wrong: it was one
   anomalous dispatch, and the changelog nearly shipped saying so.
4. **n=5** shows the real shape: mostly miss, occasional hit.

Two lessons, both worth more than the number:

- **The conflated field made every early reading unfalsifiable.** `adapters/claude_cli.py`
  mapped `cached` to `cache_read_input_tokens` and **discarded `cache_creation_input_tokens`**,
  and the observation schema was `additionalProperties: false` so nothing downstream could add
  it. #794's own AC2 — "a measured before/after on `cache_creation_input_tokens`" — was
  unmeasurable against the shipped code. That is why the telemetry is the first lever.
- **n=3 was not enough to unblock two downstream issues.** The stakes (whether the executor path
  survives) demanded replication, and replication reversed the verdict.

## What this means for #794's levers

- **Lever 1 (stabilize the prefix) is NOT confirmed by this spike.** Byte-identical *briefs* are
  not sufficient. Something outside the brief still varies per dispatch — Claude Code injects
  per-session bytes early in the prompt (the scratchpad path carries the session id; git status
  is volatile), which is the mechanism #794 already suspected. The brief is the tail, not the
  prefix, so making the tail identical cannot fix a prefix mismatch.
- **Lever 2 (per-run seat session reuse)** is now the more promising route, precisely because it
  does not depend on winning a byte-exact prefix match against injected per-session content.
- **Lever 3 (move Claude-lane analysis inline)** is unaffected by this result and remains open.

## Open question for the owner (the hard gate)

The epic contract says: if byte-identical briefs cannot hit cache, the executor path itself is in
question. This spike says they cannot, *reliably*. But it also shows the failure is plausibly
**fixable** (per-session injected bytes ahead of the brief) rather than fundamental — D3 proves
the mechanism can work. The decision is whether to invest in lever 2 or step back to the
orchestrator-with-subagents approach, and that is the owner's call, not this run's.
