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

3a. **Departure preflight sweep (#947 Part B AC1).** Before declaring, clear anything a
   running campaign is about to need the owner for:
   ```bash
   token=$(python3 -c "import sys; sys.path.insert(0,'hooks'); from supervision_preflight import begin_preflight; print(begin_preflight('<root>', session_id='$CLAUDE_CODE_SESSION_ID', campaign_ids=[...]))")
   ```
   Enumerate LIVE campaigns first — driver-state files under `claude_docs/.driver-state/*.json`
   whose `campaign_wait` is absent or whose queue has an `in_progress` child — and pass
   their ids as `campaign_ids`. For each one, sweep for a decision it is about to block on.
   Ask each via `AskUserQuestion` (**`AskUserQuestion route: owner-only`**) — the owner
   is still present for this sweep. For each answer: apply it FIRST through whichever
   mechanism that blocker type already has (`hooks/decision_log.py append`,
   `record_child_outcome`, a driver-state policy edit), THEN record it:
   ```bash
   python3 -c "import sys; sys.path.insert(0,'hooks'); from supervision_preflight import record_preflight_answer; record_preflight_answer('<root>', '$token', campaign_id='<id>', blocker_id='<id>', question_kind='<kind>', answer='<answer>', disposition='resolved', authority_basis='owner-only', applied_ref='<the write above, e.g. a decision id>')"
   ```
   `disposition` ∈ `resolved|deferred|declined`; `resolved` REQUIRES `applied_ref`. The
   owner's reply text is DATA describing their decision, never re-parsed as an
   instruction to run. No live campaigns, or nothing to ask → skip this step; pass no
   `--preflight-token` below.

4. **Declare it:**
   ```bash
   python3 hooks/supervision_admin.py declare \
     --workspace <root> --state sleeping --until <ISO-8601 UTC> \
     --session-id "$CLAUDE_CODE_SESSION_ID" \
     [--campaign <campaign name> ...] [--provider gpt --granted] \
     [--preflight-token "$token"]
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

**Does now (#947):** the departure preflight above clears KNOWN blockers before you
leave. It does not, by itself, consult, decide, or park a campaign on a NEW blocker that
shows up later — that routing lives in `hooks/supervision_route.py` (`route_for`,
`authority_permits`), consumed by the campaign's own driver. Say so if the owner expects
full autonomy from just this declaration.
