# #682 / UAT H7 — live session handover evidence (2026-07-28)

A real authorised handover of a real epic child, run against the live herdr server after the owner
reinstalled the plugin to **3.104.2** (which carries #679 and #682). Owner authorisation: D-3.

## What was NOT used, and why that is the finding

`launcher_lib.py handoff` was **refused legitimately** and I did not work around it.
`fresh_session_available` requires the caller to assert `--launcher-armed` AND
`--fresh-launch-supported`; I had no durable launcher armed, and the code comment is explicit that
absence must not read as support. Passing them would have been a false assertion to get past a
guardrail.

**So the child-boundary `handoff` subcommand is unusable by an interactive session** — it is built for
a durable launcher. That matters directly for #687's AC8 ("reuse the existing handoff plumbing"),
because #687's trigger fires in interactive sessions. `mid-child-handoff` is the interactive route
(#665) but needs a full `position` (branch, test_baseline, …) that does not exist *between* children.
**A real hole in the middle of AC8, to be resolved in #687's design step.**

Instead: herdr primitives, driven by hand, but with the **canonical** artifacts — the resume prompt
from `fresh_session_handoff` (so #682's bind-first prompt is exactly what the successor received) and
the goal read **verbatim** from the predecessor's own transcript via `last_unmet_goal_condition`
(AC6 — never retyped, never summarised).

## Sequence and results

| Step | Evidence | Result |
|---|---|---|
| driver-state created | `claude_docs/.driver-state/epic-684-watcher-fires.json` | `validate_driver_state` ok, `validate_campaign_start` ok |
| disposition | `fresh_session_handoff(mode=fresh-session)` | `ready`, next_issue **687**, generation 1 |
| prompt shape | `resume_prompt_binds_first(prompt, project="rawgentic")` | **True** — opens with `/rawgentic:switch rawgentic` |
| successor pane | `herdr pane split w1:pCX --direction down` | `w1:pD2` |
| successor session | `herdr agent start epic684succ --kind claude --pane w1:pD2` | `1eba31af-8776-497d-b97d-0bdb2ad812fb`, `interactive_ready: true` |
| **goal_armed** | one `goal_status` row with `met:false` in the successor's transcript | **CONFIRMED** — the same evidence the #665 ladder uses |
| **project_switched** | registry row `{"session_id":"1eba31af-…","project":"rawgentic","project_path":"./projects/rawgentic"}` | **CONFIRMED** |

## THE MEASUREMENT #682 DEFERRED — now taken

**The bind landed 25.5 s after the prompt was sent.** Budget is
`SWITCH_POLL_ATTEMPTS (40) × SWITCH_POLL_DELAY_S (3.0)` = 120 s.

That discharges #682's `verification_deferred` entry with a real number instead of an assumption. The
withdrawn claim was that 120 s is "generous"; what is now measured is narrower and honest: **with the
bind as the first action, one real bind on this host took 25.5 s — 21% of the budget.** It is NOT a
proof that 120 s always suffices (one sample, one host, a warm workspace file), but it is the first
evidence anyone has, and it is consistent with the fix's rationale: the budget is spent on one cheap
operation instead of arbitrary verification work.

Note what this does NOT show: the pre-#682 failure mode (a successor verifying before binding) was not
re-run, because the whole point of the fix is that the prompt no longer asks for that ordering.

## Verdict to record

**H7 — PARTIAL PASS.** A real authorised handoff on a real epic child, with goal transfer and bind both
confirmed, and the bind deadline measured. Partial because it did NOT exercise `perform_handoff`'s
six-step ladder or its teardown ordering — the launcher-armed gate made that impossible without a false
assertion. Record it with that reason attached; do not record it as a clean pass.

**L2 / H3** (run 1: INCONCLUSIVE, "successor spawns, gets the goal, binds the project") — all three
legs now confirmed live. **R3** — the reinstall to 3.104.2 happened and `sort -V` on the plugin cache
confirms it; the "fresh session loads the shipped version" leg belongs to the successor.
