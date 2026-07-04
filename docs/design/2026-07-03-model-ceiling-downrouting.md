# Design: modelRouting.implementation as a CEILING — per-task down-routing (#132)

Date: 2026-07-03 · Issue #132 · Complexity: standard_feature · lean gate (1 design pass)

## Problem

`modelRouting.implementation` is applied as a blanket assignment: WF2 Step 8's delegation dispatches EVERY plan task with `model: <implementation>`. With `implementation: opus`, well-specified 2-file mechanical/TDD tasks that Sonnet handles fine are built on Opus — over-spend (owner-flagged). Signals to do better already exist: Step 2 complexity classification (simple_change/standard_feature/complex_feature) + Step 5 per-task `riskLevel: high|standard` (`plan_lib.RISK_CRITERIA`).

## Approach

Treat `modelRouting.implementation` as a **ceiling, not a floor**. Add ONE pure function to `hooks/model_routing_lib.py`; Step 8 calls it per task and dispatches with the returned model.

### `select_impl_model(ceiling: str, risk_level: str, complexity: str) -> tuple[str, str]`

Pure, fail-open, never raises. Returns `(model, reason)` for per-task audit logging (AC3).

Model rank (cheap→capable): `sonnet=1, opus=2, fable=3` — **haiku is deliberately NOT rankable for implementation** (standing project rule: never Haiku for coding). Ranks used only for the ceiling clamp.

Logic:
1. `ceiling == "inherit"` → `("inherit", "no routing configured — session model")`. No per-task logic when routing is off; Step 8 behaves exactly as today.
2. `ceiling not in {sonnet,opus,fable}` (unknown/haiku) → `("inherit", "ceiling <c> not a valid impl model — session model")` (fail-open; haiku ceiling for coding is a misconfig, degrade to session model rather than force Haiku).
3. Desired tier:
   - `risk_level == "high"` OR `complexity == "complex_feature"` → `desired = ceiling` (hardest work gets the configured max).
   - else → `desired = "sonnet"` (down-route target for standard/simple, well-specified).
4. Clamp to ceiling: `actual = desired if rank[desired] <= rank[ceiling] else ceiling`. (If ceiling is sonnet, a high-risk task stays sonnet — a ceiling is a hard cap, never exceeded.)
5. Reasons: `"high-risk/complex → ceiling <m>"`, `"standard/simple → down-routed to sonnet"`, `"clamped to ceiling <m>"`.

Floor note: the down-route target is `sonnet` (never below), satisfying "no Haiku for coding" without a separate clamp — sonnet is already the lowest desired tier.

### WF2 Step 8 (`skills/implement-feature/SKILL.md`) — delegation block

Currently: "When routing resolved the `implementation` role to a non-`inherit` model, execute each plan task via a subagent (`model: <implementation>`)". Change:
- Before each task's dispatch, call `select_impl_model(<resolved implementation ceiling>, task.riskLevel, <Step-2 complexity>)`; dispatch that task's subagent with the returned model.
- **Log per task** (session notes): `task <id>: model <m> (<reason>)` — makes over/under-routing auditable (AC3).
- **Escalate on struggle:** the existing clean-state restore + retry-once path now retries at the **ceiling** model (not the down-routed one) before falling through to normal Step 8 failure handling (AC2). One escalation, then normal handling.
- When ceiling is `inherit`, the block is inert exactly as today (function returns inherit → dispatch with no `model:`).

## Files
- `hooks/model_routing_lib.py`: `select_impl_model` (+ rank map).
- `tests/hooks/test_model_routing.py`: selection matrix.
- `skills/implement-feature/SKILL.md`: Step 8 delegation prose (ceiling + per-task select + escalation) + a `<model-routing-resolve>` note that implementation is a ceiling.
- `docs/config-reference.md`: document ceiling semantics (no schema change — same `modelRouting.implementation` key, clarified meaning). README if it describes routing.
- Version bump patch → 2.47.1 (behavior refinement, no new config).

## Test matrix (TDD)
`select_impl_model`:
- ceiling=opus, high risk → (opus, high-risk); ceiling=opus, standard+simple_change → (sonnet, down-routed); ceiling=opus, standard+complex_feature → (opus, complex); ceiling=opus, standard+standard_feature → (sonnet, down-routed)
- ceiling=sonnet, high risk → (sonnet, clamped); ceiling=sonnet, standard → (sonnet)
- ceiling=fable, high → (fable); ceiling=fable, standard → (sonnet)
- ceiling=inherit → (inherit, …); ceiling=haiku → (inherit, misconfig); ceiling="bogus" → (inherit)

## Supersession note (never-Haiku directive, mid-implementation)

After this doc was drafted, the owner directed: **rawgentic must never route work to Haiku.** That changed two rows of the matrix below and the fallback semantics as SHIPPED (commit 8530f7c):
- `select_impl_model` step 2 (haiku / unknown ceiling) → **`("sonnet", …)`**, NOT `("inherit", …)` — never punt coding to a session model that might be Haiku.
- `resolve()` bumps ANY role configured to `haiku` → `sonnet` (warned), all roles.
- Step 8 dispatch never uses `model: haiku`; under `inherit` with a Haiku session model, dispatch `sonnet`.

The matrix rows for `ceiling=haiku`/`bogus` therefore resolve to `sonnet` (not `inherit`) in the shipped code and tests. The rest of the design stands.

## Out of scope
`review`/`analysis` role down-routing (a cheaper reviewer weakens a gate — separate discussion, explicitly deferred per issue). Other dispatch sites (WF3/WF8) — follow-up once WF2 proven. No config schema change.

## AC coverage
AC1 per-task model from (riskLevel, complexity), ceiling=max → select_impl_model + Step 8 call. AC2 escalation → retry-at-ceiling prose. AC3 logging → per-task session-note line. AC4 docs ceiling semantics, no schema change → config-reference. AC5 selection-matrix tests.
