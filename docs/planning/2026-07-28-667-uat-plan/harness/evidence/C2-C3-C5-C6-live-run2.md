# C2/C3/C5/C6 — live drives of the MERGED hook (UAT run 2, 2026-07-28)

## C2 — once per TIER, not once per turn (T=1 makes every call due)
call 1: EMITTED: {"additionalContext": "[rawgentic context meter] This session is using 150,000 t
call 2: (silent)
call 3: (silent)
call 4: (silent)

## C5 — thresholds are RELATIVE: the same token count, two windows
window   200000 -> {"fraction": 0.79708, "provenance": "env", "tier": "directive", "transcript": "/tmp/tmp.TgcmP2OOAk/.claude/projects/-p/c0ffee12-0000-1111-2222-333344445555.jsonl", "used": 159416, "window": 200000}
window  1000000 -> {"fraction": 0.159416, "provenance": "env", "tier": "none", "transcript": "/tmp/tmp.TgcmP2OOAk/.claude/projects/-p/c0ffee12-0000-1111-2222-333344445555.jsonl", "used": 159416, "window": 1000000}

## C6 — fail-open, ABSENT transcript
context_meter: could not resolve a transcript for session c0ffee12-0000-1111-2222-333344445555 — the meter is inactive for this session
stdout=[] rc=0
## C6 — fail-open, CORRUPT transcript
context_meter: no parseable message.usage row in c0ffee12-0000-1111-2222-333344445555.jsonl — the meter is inactive for this session
stdout=[] rc=0

## C3 — cadence at the DEFAULT 5-turn arm: turns 1-4 silent, turn 5 checks
turn 1: EMITTED
turn 2: (silent)
turn 3: (silent)
turn 4: (silent)
turn 5: (silent)
