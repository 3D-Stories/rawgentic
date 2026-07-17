# Model routing — provenance & refresh rule

How rawgentic decides which model serves each WF2/WF3 seat, and — the point of this doc — the
**executable decision rule** that keeps those values tied to bench evidence with a human in the loop.
There is **no auto-tuner**: a model only moves when the rule below fires AND a human applies it, and
every decision (including "no change") re-stamps its provenance. The routing *values* live in
`phase_executor/src/phase_executor/routing/rawgentic.routing-table.json` (per-seat primary + fallback
chain); this doc is the *policy* for changing them.

The evidence basis is the six-model bench (bench #14). Sources, all committed: the plan doc
`docs/planning/2026-07-16-per-phase-model-routing.md` — **§2** (the gap test / noise method + the
six-model evidence table), **§1** (the seat table + floors), **§4** (the seat-table refresh discipline
/ config-rot rule) — plus `hooks/model_routing_lib.py` for the dispatch-role taxonomy
(review / analysis / implementation). Re-run the bench, then apply this rule per seat — never eyeball a
median.

## Role → phase map

Each dispatch role is scored against the phase(s) it actually serves:

| Role / seat | Scored against |
|---|---|
| **review** | the `review` phase |
| **analysis** | `intake` + `design` (mean of the two phases' gaps) |
| **implementation** (ceiling) | `build` |
| **driver / orchestrator seat** | `design` + `plan` (each evaluated independently, not averaged) |

## The two tests

**Gap test (effect-size heuristic — NOT a significance test).** A candidate model beats the incumbent
on a phase only when `candidate_median − incumbent_median > pooled_sd`, where `pooled_sd` is the
bench's **pooled per-cell standard deviation** over the **valid** cells (a null/void cell is dropped
and named, not zero-filled — the exact pooling is the bench's own noise method, plan §2). Require
**min n = 5** valid cells per side, else the comparison is **void**. This is a deliberate effect-size
heuristic (gap vs spread), not a statistical significance test — it is the smallest bar that keeps
noise from moving a seat.

**Floor test.** The candidate's **worst valid cell** must clear this **decision-rule** floor: **≥ 70**
for subagent seats (review / analysis / implementation) and **≥ 80** for the **driver** seat; the
driver additionally must **clear ≥ 5 of the 6 (`5/6`) `design` + `plan` gates** (a count of gates
passed — opus cleared 6/6, sonnet 5/6). Thresholds are owner-tunable and were derived from the
bench-#14 spread (opus floors 81/82; the collapse cells 46/28/38 are exactly what the floor excludes).

This decision-rule floor (the minimum a *candidate* must clear to be eligible to move) is distinct
from a seat's declared **runtime enforcement floor** in `rawgentic.routing-table.json` (e.g.
`review.floor = 80`, `build.floor = 76` — the per-seat serve-time value); the two are different
quantities and need not be equal.

## The move rule (canonical)

> **A seat's routed model changes ONLY when BOTH the gap test and the floor test pass for the
> candidate; a tie or a void holds the incumbent, and provenance is re-stamped on every decision —
> including a "no change".**

Ties and voids are conservative on purpose: the incumbent holds unless the evidence clears both bars.

## Provenance stamp

Every routing decision — an actual move OR an explicit "no change" — MUST record: the bench id/date,
the per-phase medians + pooled sd + valid-n used, which test(s) passed/failed, the decision, and the
deciding human. (This is the target the stamp captures; the current `rawgentic.routing-table.json`
`provenance` block is a thinner interim `{source, plan, note}` shape — the fuller stamp is the
policy this doc sets.) A "no change" decision is stamped too, so a seat's value is never silently
carried forward without a fresh evidence check each bench cycle (the config-rot guard, plan §4:
floor-based picks must be **re-derived** every bench, not persist by inertia).
