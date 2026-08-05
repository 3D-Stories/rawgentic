---
name: sleeping
description: 'Record that the owner is unreachable until a stated wake time, so hooks and workflows know nobody can be asked anything. Use when the owner says they are going to bed, turning in, sleeping, asleep, off for the night, or asks to be marked unreachable until a given hour. A wake time is REQUIRED. Do NOT use when the owner is merely stepping out and can still be texted — that is /rawgentic:away — and do NOT use to announce they are back, which is /rawgentic:back.'
argument-hint: required — the wake time (an ISO-8601 UTC time, or a plain time to convert)
---

# Declare SLEEPING

Write the workspace supervision state so every hook and workflow can see that the owner
is unreachable until a stated time. SLEEPING means **cannot be asked** — as opposed to
`away`, which means absent but still reachable by phone.

## Steps

1. **Resolve the workspace root** — walk up from the current directory for the directory
   containing `.rawgentic_workspace.json`. The state file lives at
   `<root>/claude_docs/.supervision.json`.

2. **A wake time is mandatory.** Convert what the owner said ("7am", "in eight hours")
   into an ISO-8601 UTC timestamp. If the owner gave no time, ASK for one — do not invent
   a default. A declaration with no wake time is refused, and rightly: "unreachable
   forever" is not a state any run should be left in.
   A time that lands in the past is also refused, so re-check the timezone rather than
   guessing when the conversion looks wrong.

3. **Ask about consult egress SEPARATELY.** While the owner is unreachable, a run may
   want a cross-model consult to break a tie. That sends repo text off this host, so it
   needs its own explicit yes — never bundle it into "goodnight". Ask, then pass
   `--provider gpt --granted` (or `--provider glm`) only if granted. Omit both to
   withhold it.

4. **Declare it:**
   ```bash
   python3 hooks/supervision_admin.py declare \
     --workspace <root> --state sleeping --until <ISO-8601 UTC> \
     --session-id "$CLAUDE_CODE_SESSION_ID" \
     [--campaign <campaign name> ...] [--provider gpt --granted]
   ```
   Exit 0 prints the record as JSON plus a one-line confirmation on stderr. **Exit 1
   means nothing was written** — say so plainly rather than reporting the owner as
   asleep.

5. **Report** the wake time, whether consult egress was granted, and the revision.

## What this does and does not change

**Does:** unattended package installs are refused, and the context-pressure meter routes
to a handoff instead of asking a human. These two do NOT expire together, deliberately:
past the wake time the meter goes back to addressing a human, while the install refusal
STAYS in force, because the clock reaching morning is not evidence the owner woke up.
Only `/rawgentic:back` lifts the install refusal.

**Does not, yet:** it does not consult, decide, notify, or park a campaign on the owner's
behalf. That behaviour is #947. Until it ships, a run that hits a blocker while the owner
is asleep parks for a human rather than deciding — which is the safer half of the
protocol, and worth stating out loud if the owner expects the run to carry on alone.
