# C4 — a mid-phase break is REFUSED; a step-boundary break FIRES (UAT run 2, 2026-07-28)

130,000 of 200,000 = 65% -> the ADVISORY band, which is the tier that waits for a seam.
A real workspace + registry + step-state pointer, so the production resolution path is used.

## Turn 1 — arms the seam search against the CURRENT pointer (mid Step 8)
  (silent — correct: the search has only just armed)

## Turn 2 — pointer UNCHANGED (still mid Step 8): the break must be REFUSED
  (silent — mid-phase break refused)

## Turn 3 — pointer MOVED to Step 9: a step boundary, so the advisory must FIRE
  EMITTED:
    [rawgentic context meter] This session is using 130,000 tokens of an assumed 200,000-token context window (65%). Window source: default. That window is the conservative DEFAULT, not a measurement — if this session's model has a larger window, set `contextMeter.windowSize` in the project's .rawgentic.json (or RAWGENTIC_CONTEXT_WINDOW) so this reading is right. Start looking for a safe seam to break at. Do NOT stop mid-phase. A step boundary was just recorded (a workflow step boundary was just recorded). If your tree is clean and no review wave is outstanding, this is the moment to break — confirm both yourself; this signal cannot. Run the `clear-prep` skill: it writes the mempalace checkpoint, the durable handoff file, the resume prompt and the /goal text. Its handoff carries `next actions, in order` — the successor rebuilds its task list from those via /tasklist.

## And nothing from the project-controlled pointer is echoed into the model's context:
   the message says 'a workflow step boundary was just recorded' and carries no step number,
   no workflow name — the Step-11 CRITICAL injection channel is closed, not narrowed.
