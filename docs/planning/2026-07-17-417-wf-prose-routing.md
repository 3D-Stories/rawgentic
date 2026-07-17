# #417 — WF2/WF3 skill prose: fallback wiring + concurrency + driver-seat (epic #422) — lane design+plan

**Small-standard lane.** Plan §5.3. No unmet deps (documents behavior already shipped by #425/#426/#429).
Prose-only — no hook logic.

## Brief design (lane)

Document three routing-plan contracts in the `<model-routing-resolve>` block prose:
1. **Seat fallback chains + circuit breaker** — each seat's model is a config-declared chain, tried in
   order on availability failure with a chain-aware cross-model skip; a chain exhausting its eligible
   entries is a **handled hard failure, never a silent downgrade** (the canonical drift-guardable
   sentence).
2. **Concurrency ceiling** — ≤3 concurrent Claude subagents (standing cap); driver-active → effective
   ceiling 2. Prose rule, no programmatic clamp.
3. **Driver-seat guidance** — opus-4-8 recommended session model (strong-model-on-top reliability
   floor); GUIDANCE only, the harness owns the session model.

**Shared-block discipline (mistake #14):** `<model-routing-resolve>` is MANIFEST-synced into
`implement-feature` ONLY. So the WF2 change goes in **`shared/blocks/model-routing-resolve.md`** + a
`scripts/sync_shared_blocks.py` run (never the inline SKILL.md copy). WF3's (`fix-bug`) block is a
DELIBERATELY-divergent bespoke variant (MANIFEST comment: "no forced unification") → edited DIRECTLY.
`steps.md` is NOT edited: the dispatch contract is single-sourced in the block (#158), and steps.md's
dispatch annotations already reference `<model-routing-resolve>` — duplicating the rule there would
violate single-sourcing.

## Platform / external dependencies

platform_apis: none

## Plan (lane checklist)

### Task 1: shared block + sync + fix-bug + drift guard
- riskLevel: standard
- Edit `shared/blocks/model-routing-resolve.md` (the 3 contracts); run `sync_shared_blocks.py`
  (regenerates `implement-feature/SKILL.md`); `--check` green. Add the corresponding note to
  `skills/fix-bug/SKILL.md`'s bespoke WF3 block (direct). Add `tests/test_model_routing_resolve_prose.py`
  pinning the canonical fallback sentence in the shared source + confirming it shipped to the WF2
  corpus + WF3 carries its note. verify: shared-block-drift test + full suite.

version 3.48.1 → **3.49.0 (minor — `feat(wf2,wf3)`)**. No workflow-SPINE change (prose/contract doc,
no new/removed/reordered step or gate) → no diagram REV.
