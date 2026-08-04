## Step 1b: AC-Derived Goal Guard (/goal)

### Instructions

This is an optional guard, not a gate — it never blocks the workflow.

1. **Why.** A skill cannot set a session goal itself; `/goal` is a session command
   (see code.claude.com/docs/en/goal.md), not something a skill body can invoke. So
   this step CONSTRUCTS the goal text and the user (or the epic driver) is the
   one who runs it, giving the session's Stop-hook a concrete condition so the
   workflow can't be silently abandoned before the ACs are met.

2. **Build the text.** Call `plan_lib.build_goal_text(<issue_number>, <the numbered
   AC lines gathered in Step 1>, variant="wf2")`. This is a pure, tested helper:
   the result is guaranteed ≤4000 chars (falling back to "all numbered acceptance
   criteria of issue #<N> as written" if the full AC list would overflow it), and
   it always appends the escape disjunct ("or a blocker is posted to the issue via the ERROR protocol")
   so a legitimately blocked run still clears the goal honestly instead of hanging
   forever. The wording is PR-terminal ("PR open with green CI"), never "merged":
   merge is owner-gated and happens after the workflow ends.

3. **Fold into Step 1's confirmation — no second prompt.** The built text is shown
   inside Step 1 item 6's display block; the user's single confirmation covers both
   the issue/capabilities check and the `/goal` invocation (run it, or decline).
   Declining is always a valid answer and never blocks progress (`goal_guard: skipped`).

4. **ALWAYS emit the constructed `/goal` prompt** (#191). Because `/goal` is a
   session command the skill cannot observe or set, the skill has no reliable way
   to know whether a prior goal is still active — so it must not *suppress*
   emission on the guess that one might be. Emit the built text every run and let
   the user/driver decide whether to run it (declining stays valid). Do NOT skip
   emitting just because a previous run in the same session may have set a goal.

5. **Epic-campaign exception (defer, don't clobber) (#191 AC2).** When this run is
   part of an epic campaign — signaled by the `RAWGENTIC_EPIC_GOAL` environment
   variable being set (to the driving epic's issue number; the driver sets it,
   #192) — an epic-level goal is already in force for the whole campaign. Emitting
   a per-issue `/goal` would clobber it, so **defer**: do NOT emit the per-issue
   prompt, and log the marker as `(deferred: epic #<N> goal active)` (never
   silently skip — the defer must be visible).

6. **Record the marker** (Step 16 reads this to populate the run-record
   `goal_guard` field — `set` when emitted, `deferred` under an epic campaign,
   `skipped` when the user declines / an unattended run with no goal channel):
   ```
   ### WF2 Step 1b — Goal guard (set|deferred|skipped): #<issue> — <first 80 chars of text | epic #N | decline reason>
   ```

7. `fired` (the Stop-hook actually blocked a quit) is recorded manually only — no
   structured signal reaches the orchestrator when that happens.

### Failure Modes
- User neither runs `/goal` nor says "skip" -> treat silence as decline, log `(skipped)`, proceed

---

