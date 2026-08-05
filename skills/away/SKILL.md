---
name: away
description: 'Record that the owner is stepping out but is still reachable by phone, so hooks and workflows know nobody is watching this session. Use when the owner says they are going out, stepping away, leaving for a bit, off to do something, back in an hour, or asks to be marked away. Also use when they name a return time ("back at 22:30", "out for two hours"). Do NOT use for going to bed or being unreachable — that is /rawgentic:sleeping — and do NOT use to announce that the owner has returned, which is /rawgentic:back.'
argument-hint: optional — when you expect to be back (an ISO-8601 UTC time, or nothing)
---

# Declare AWAY

Write the workspace supervision state so every hook and workflow can see that the owner
is absent but reachable. AWAY means **absent, still reachable by phone** — as opposed to
`sleeping`, which means unreachable until a stated wake time.

## Steps

1. **Resolve the workspace root** — walk up from the current directory for the directory
   containing `.rawgentic_workspace.json`. That directory IS the root; the state file
   lives at `<root>/claude_docs/.supervision.json`.

2. **Convert the return time, if the owner gave one.** The argument may be informal
   ("in two hours", "half nine"). Turn it into an ISO-8601 UTC timestamp. A time already
   in the past is refused, so if the conversion lands behind now, ask rather than guess.
   No return time is fine — pass no `--until`.

3. **Declare it:**
   ```bash
   python3 hooks/supervision_admin.py declare \
     --workspace <root> --state away [--until <ISO-8601 UTC>] \
     --session-id "$CLAUDE_CODE_SESSION_ID" \
     [--campaign <campaign name> ...] [--provider gpt --granted]
   ```
   - `--campaign` narrows the declaration to named campaigns; **passing none means it
     governs every campaign**, which is the usual case for "I am going out".
   - `--provider ... --granted` records permission to send repo text to a cross-model
     consult while the owner is away. Ask for it explicitly rather than assuming it —
     it authorizes data leaving this host. Omit both to withhold it.
   - Exit 0 prints the new record as JSON and a one-line confirmation on stderr.
     **Exit 1 means nothing was written** — say so plainly and do not report the owner
     as away.

4. **Report** what was recorded: the state, the return time (or that none was given),
   whether consult egress was granted, and the revision number.

## What this does and does not change

**Does:** unattended package installs are refused, and the context-pressure meter routes
to a handoff rather than asking a human. These two do NOT expire together, and the
difference is deliberate: past the stated return time the meter goes back to addressing a
human (its only stake is which advice it prints), while the install refusal STAYS in
force, because installing packages is an outward act and a clock passing a timestamp is
not evidence anybody came back. Only `/rawgentic:back` lifts the install refusal.

**Does not, yet:** it does not text the owner about blockers, decide anything on their
behalf, or park a running campaign. That behaviour is #947; until it ships, an
unsupervised run parks for a human rather than deciding. Say so if the owner expects
autonomy — over-trusting this command is the failure mode worth preventing.
