Track all design loop-backs across the workflow. There are **four** sources (the
canonical caps live in `plan_lib._LOOPBACK_SOURCE_MAX`):
- Step 4 -> Step 3: max 2 iterations (MAX_DESIGN_LOOPBACK_ITERATIONS, source `design`)
- Step 8 -> Step 3: max 1 iteration (MAX_TDD_DESIGN_LOOPBACK, source `tdd`)
- Step 8a -> Step 3: max 1 iteration (MAX_REVIEW_DESIGN_LOOPBACK_STEP_8A, source `review_design`)
- Step 11 -> Step 3: max 1 iteration (MAX_REVIEW_DESIGN_LOOPBACK, source `review`)

Global cap: GLOBAL_LOOPBACK_BUDGET = 3 — this binds BEFORE the per-source caps (which
sum to 5), so the workflow loops back at most 3 times total. `plan_lib.consume_loopback`
enforces both the per-source and the global cap; call it and act on its `(ok, state)`
return rather than pre-checking the in-context mirror.
If the global cap is reached, STOP and escalate to user with a full summary of all loop-back triggers. **[Headless: ERROR — post error comment with full loop-back summary, add rawgentic:ai-error label, exit.]**

Track loop-back state (mirror of the canonical counters file — one var per source):
design_loopback_count = 0
tdd_loopback_used = false
review_loopback_used = false
review_design_loopback_used = false
global_loopback_total = 0

**Source of truth:** once it exists, `claude_docs/.wf2-state/<issue>/loopback_counters.json` (written via `plan_lib.consume_loopback`) is canonical for all *successfully persisted* counts — it survives context compaction, fresh headless sessions, and worktrees. The in-context variables above are a convenience mirror: on resume, initialize them from the file when it is present, otherwise from the defaults above (a missing file means "no loop-backs consumed yet," not an error). Do not write the in-context values back over a more-advanced file. If a `consume_loopback` call increments the in-context counter but fails to persist, treat that as a blocker — reconcile or STOP rather than blindly trusting either side, since a stale file would silently restore spent budget.
