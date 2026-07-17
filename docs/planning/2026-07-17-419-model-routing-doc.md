# #419 — model-routing.md provenance + refresh-rule doc (epic #422) — lane design+plan

**Small-standard lane** (1 new doc + 1 drift-guard test). Plan §6. No unmet deps. Key-independent.
Pure docs — a reference/policy doc (like `config-reference.md`/`testing.md`), NOT a design/architecture
doc, so no HTML/Artifact render (the design doc is the merged `2026-07-16-per-phase-model-routing.md`).

## Brief design (lane)

New `docs/model-routing.md`: the executable refresh-rule decision table that ties routing values to
bench evidence with a human in the loop (no auto-tuner). Contents (plan §6, made executable):
- **Role→phase map:** review→review; analysis→intake+design (mean of gaps); implementation ceiling→build;
  driver seat→design+plan (each independently).
- **Gap test:** candidate median − incumbent median > pooled sd (mean of the two models' population sd
  over VALID cells; nulls dropped + named; min n=5 per side or void). Explicitly an effect-size
  HEURISTIC, not a significance test.
- **Floor test:** worst valid cell ≥70 (subagent seats) / ≥80 (driver seat); driver additionally every
  design+plan gate ≥5/6. Thresholds owner-tunable; derived from bench #14 spread.
- **Move rule:** change only when BOTH tests pass; ties/void hold the incumbent; re-stamp provenance
  every decision including "no change".
- **One canonical drift-guardable sentence** stating the gap>sd AND floor rule — pinned by a test
  (repo convention: anchor to ONE sentence in ONE file).

## Platform / external dependencies

platform_apis: none

## Plan (lane checklist — TDD)

### Task 1: model-routing.md + canonical-sentence drift guard
- riskLevel: standard
- RED: a test in tests/ asserting the canonical move-rule sentence (exact substring) is present in
  docs/model-routing.md — fails before the doc exists. GREEN: write docs/model-routing.md with the
  role→phase map, gap test, floor test, move rule, provenance stamp, and the canonical sentence.
  verify: the drift-guard test + full suite.
- files: docs/model-routing.md, tests/test_model_routing_doc.py

version 3.48.0 → **3.48.1 (patch — this is a `docs:` child** per repo §2 "patch for fix/chore/docs/ci";
the prior epic children were `feat` → minor). Branch `docs/419-…`. No spine change → no diagram REV.
