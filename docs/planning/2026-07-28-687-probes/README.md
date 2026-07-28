# #687 probe harness — committed so the design's citations are auditable

The #687 design cites three live probes. The Step-4 verifier correctly refused two of them as
**not auditable**: they lived in a `/tmp` scratch dir that no reviewer could open, and a citation a
reader cannot check is not evidence. So the harness lives here, and each probe is re-runnable.

Nothing here is wired into the plugin or the test suite. These are one-shot measurement tools.

## Probe 8 — where does auto-compaction actually fire?

`compaction_scan.py` walks every transcript over 200 KB in `~/.claude/projects/`, computes the
in-context total for each `message.usage` row (`input_tokens + cache_creation_input_tokens +
cache_read_input_tokens`), and reports sharp falls plus the observed ceiling.

```bash
python3 docs/planning/2026-07-28-687-probes/compaction_scan.py
```

**Result on this host, 2026-07-28** (the design's probe 8): 266 transcripts scanned, 86 sharp drops
across 18 sessions whose peak was ≥ 900k, **highest reading anywhere 999,803 tokens = 100.0% of a 1M
window**, top cluster 994,859–999,803. **Zero** sessions with a drop had a peak under 250k, so the
200k window is unmeasurable from this corpus.

Two honest limits, restated here because the number is tempting to over-read:

1. A sharp fall is **not necessarily** auto-compaction — a `/clear`, a manual `/compact`, or a new
   cache prefix looks identical. So the drop *distribution* proves nothing about onset; only the
   **ceiling** does, and the ceiling is the load-bearing figure.
2. Exact counts drift as transcripts accumulate. Re-running will not reproduce 266/86/18 verbatim; it
   should reproduce the shape — a ceiling near the window size, and no low-window sessions.

## Probe 9 — what does a hook payload actually carry, and what output does each event accept?

`ptu-hook.sh` (PostToolUse) and `ups-hook.sh` (UserPromptSubmit) dump their stdin payload to a file;
`ptu-hook.sh` also emits a canary `additionalContext` so delivery can be observed end to end.

```bash
D=$(mktemp -d)
cp docs/planning/2026-07-28-687-probes/*.sh "$D"/
cat > "$D/settings.json" <<EOF
{"hooks":{
  "PostToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"$D/ptu-hook.sh"}]}],
  "UserPromptSubmit":[{"hooks":[{"type":"command","command":"$D/ups-hook.sh"}]}]}}
EOF
# the hooks write their dumps next to themselves; adjust the paths inside them to "$D" first
cd "$D" && claude -p "Use Bash to run: echo probe. Then reply DONE." \
  --settings ./settings.json --allowedTools Bash --model sonnet
```

**Results on this host, 2026-07-28:**

- `UserPromptSubmit` payload keys: `cwd, hook_event_name, permission_mode, prompt, prompt_id,
  session_id, transcript_path`.
- `PostToolUse` payload keys: those plus `tool_name, tool_input, tool_response, tool_use_id,
  duration_ms, effort`.
- **`transcript_path` is present on BOTH**, and its basename is exactly `<session_id>.jsonl` — which
  is what makes the design's basename-binding hardening rule satisfiable.
- **`PostToolUse` DOES deliver `additionalContext`, but only nested** under
  `{"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "…"}}`. The
  canary token came back quoted verbatim in the model's reply, which named it as arriving in a
  `PostToolUse:Bash` hook system-reminder. The top-level form that `hooks/wal-context:43` uses on
  `UserPromptSubmit` is **not** the right shape for this event.
- **Limit:** only a TOP-LEVEL session was captured. **No subagent payload was observed**, so the
  field name a subagent invocation carries is unverified — which is why `context_meter._is_subagent`
  checks several plausible keys and is inert when none is present.

An incidental but useful observation: the probing model flagged the injected canary as a possible
prompt-injection attempt and refused to act on it, while still reporting it. Injected hook context is
treated as data, not instruction — worth knowing for anything that injects text into a session.
