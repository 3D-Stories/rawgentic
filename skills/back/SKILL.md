---
name: back
description: 'Record that the owner is watching again, clearing any away or sleeping declaration. Use when the owner says they are back, returned, awake, up, at the keyboard, here now, or asks to be marked attended again. Run it whenever an owner message arrives during a declared absence, because a message from the owner always means attended again. Do NOT use to declare an absence — those are /rawgentic:away and /rawgentic:sleeping.'
argument-hint: optional — a short reason or note (defaults to "owner returned")
---

# Declare ATTENDED (the owner is back)

Clear the workspace supervision state. This is the ONLY thing that lifts the unattended
guards: an away or sleeping declaration keeps refusing unattended installs even after its
stated return time passes, because a clock passing a timestamp is not evidence anybody
came back. An explicit return is.

## Steps

1. **Resolve the workspace root** — walk up from the current directory for the directory
   containing `.rawgentic_workspace.json`.

2. **Read the current revision.** `mark-attended` requires it, so the write can be
   refused if the state moved in the meantime:
   ```bash
   python3 hooks/supervision_lib.py effective --workspace <root>
   ```
   Take `revision` from that JSON.

3. **Mark attended:**
   ```bash
   python3 hooks/supervision_admin.py mark-attended \
     --workspace <root> --session-id "$CLAUDE_CODE_SESSION_ID" \
     --reason "<short reason>" --expected-revision <revision from step 2>
   ```
   Exit 0 prints the new record as JSON plus a confirmation on stderr.

   **A revision mismatch (exit 1) is not a failure to route around.** It means the
   supervision state changed between the read and the write — another session declared
   something. Re-read step 2 and decide again with the newer state; do NOT retry with a
   forced value, because that is exactly the clobber the fence exists to stop.

4. **Report** that the guards are lifted, and note that the consult-egress grant was
   cleared with them — a grant given for an absence must not outlive it.

## Lead with what happened while they were gone

If any decision was taken during the absence, the report the owner reads next puts those
FIRST, before ordinary status. They authorized nothing while they were away; they are
entitled to see what was chosen on their behalf before anything else. Each such decision
should already carry its undo in the decision log
(`python3 hooks/decision_log.py append ... --overturnable ...`).

The record's `revision` and `declared_at` from the absence are preserved through
evaluation precisely so this report can still name the window afterwards.
