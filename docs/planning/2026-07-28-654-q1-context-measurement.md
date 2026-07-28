# #654 Q1 — measuring context pressure: the reading path, live-verified

Epic #667. Answers **Q1 only** of the #654 spike: *a documented, live-verified reading path covering
BOTH sources — the statusline bridge (authoritative) and the transcript fallback (headless).*
Q2 (can a goal be set without a human), Q3 (restart mechanisms), Q4 (threshold arithmetic) and
Q5 (recommendation + decomposition) are NOT answered here and #654 stays open for them.

Every number below was read from this host on 2026-07-28, not inferred.

## Verdict

**Both sources work, and the authoritative one needs no arithmetic at all.** Claude Code hands the
statusline a pre-calculated `used_percentage`. The transcript fallback reproduces it to within the
drift of the seconds between two readings. Context detection can therefore be mechanical rather
than model judgment.

## Source 1 — the statusline payload (authoritative, interactive only)

**Captured live**, by inserting one `printf '%s' "$input" > …` line into
`~/.claude/rawgentic-statusline.sh` after its existing `input=$(cat)`, waiting for a render, then
restoring the script from a backup and confirming the restore by sha256
(`e383b4a7456c5f8a14b4d52b02bcbeff87349385ee7c90a52d520fd0ab1da521`, probe line gone, script still
exits 0). This is the only way to see what Claude Code actually sends; reading the schema would not
have been evidence.

Top-level keys delivered:

```
context_window  cost  cwd  effort  exceeds_200k_tokens  fast_mode  model  output_style
prompt_id  rate_limits  session_id  session_name  thinking  transcript_path  version  workspace
```

And `context_window` itself, verbatim:

```json
{
  "total_input_tokens": 654219,
  "total_output_tokens": 1667,
  "context_window_size": 1000000,
  "current_usage": {
    "input_tokens": 2,
    "output_tokens": 1667,
    "cache_creation_input_tokens": 1257,
    "cache_read_input_tokens": 652960
  },
  "used_percentage": 65,
  "remaining_percentage": 35
}
```

**The load-bearing correction, and it simplifies the whole feature.** `total_input_tokens` is NOT a
session cumulative — it is the in-context total for the current request. Read from the capture:
`2 + 1257 + 652960 = 654219`, exactly the reported `total_input_tokens`. So a consumer needs ONE
field, `used_percentage`, or at most one division. #654's body correctly warns that
`hooks/usage_capture.py` sums usage across the whole session and is off by ~477x for this purpose —
that warning applies to the transcript rows, not to this field.

`session_id` is present in the same payload, so a reading is attributable to a session without any
extra lookup. `exceeds_200k_tokens` is also delivered, which is a free signal for the 200k-model
threshold question in Q4.

**What this source cannot do:** headless and cron runs render no statusline, so nothing invokes the
command and no reading is produced. That is the entire reason source 2 is required, not optional.

## Source 2 — the transcript fallback (works everywhere, including headless)

Sum the LAST `message.usage` row in the session transcript:
`input_tokens + cache_read_input_tokens + cache_creation_input_tokens`.

**Reading A — the same live session, seconds after the statusline capture:**

- transcript: `~/.claude/projects/-home-rocky00717-rawgentic/28e64d8b-….jsonl`, 562 usage-bearing rows
- last row: `input_tokens 2`, `cache_creation_input_tokens 2531`, `cache_read_input_tokens 649882`
- **in-context total 652,415 tokens = 65.2% of the 1M window**

**The cross-check.** Statusline said `used_percentage: 65` with `total_input_tokens: 654219`; the
transcript gave 652,415, i.e. 65.2%. The two agree to within **1,804 tokens — 0.18% of the window** —
which is the cache-read delta between two readings taken seconds apart, not a methodology error.

**Reading B — a session with NO statusline**, which AC1 requires separately:

- transcript `agent-a408d0c5b13b7c1fa.jsonl` (a subagent run: no UI, no statusline command ever
  invoked; confirmed non-interactive — no `queue-operation` or `queued_command` rows), 14 usage rows
- last row: `input_tokens 2`, `cache_read_input_tokens 154085`, `cache_creation_input_tokens 19580`
- **in-context total 173,667 tokens = 17.4% of a 1M window, or 86.8% of a 200k window**

That second framing is worth keeping for Q4: the same transcript is comfortable on a 1M model and
already past the reported ~77% auto-compaction point on a 200k one, so any threshold has to be
expressed against `context_window_size`, never as an absolute token count.

## Honest limits of this answer

- **AC1 asks for a reading that "matches `/context` for the same session". I did not run `/context`** —
  it is an interactive slash command and this run is unattended. Substituted a cross-check between
  the two independent mechanical sources above, which is what a consumer will actually rely on. If
  an operator wants the `/context` comparison, the statusline number to compare against is
  `used_percentage`.
- The no-statusline reading comes from a **subagent** transcript, not a cron-spawned top-level
  session. It establishes what AC1 needs — that the fallback works where no statusline exists, on
  the same row shape — but it is not a proof about cron specifically. A cron-spawned session writes
  the same `message.usage` rows; that last step is inference, and the cheap confirmation is to take
  one reading inside the first real cron-spawned run.
- `used_percentage` is an integer. It is precise enough for tier thresholds and too coarse for
  anything finer; use `total_input_tokens / context_window_size` when a fraction is wanted.
- Nothing here was implemented. #654's Scope puts all implementation out of bounds, and the
  statusline is a user-level file outside any git repository, so the ~3-line bridge cannot ship as a
  rawgentic PR with tests. That placement question belongs to Q5.

## What Q1 hands to the next question

1. The authoritative reading is `context_window.used_percentage`, already calculated, delivered with
   `session_id`, on every statusline render.
2. The headless reading is the last `message.usage` row summed over three fields, and it agrees with
   the authoritative one to 0.18% of the window.
3. A consumer therefore needs a **persist step**, not a compute step: the statusline is the only
   thing that sees source 1, and it currently discards it. Where that persisted state lives, and
   what reads it, is Q5's recommendation — not settled here.
