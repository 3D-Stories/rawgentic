# Adversarial Review — .rawgentic-design-132.md

- Date: 2026-07-03-132
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 3 (Critical 0, High 1, Medium 2, Low 0)

## Summary

The artifact proposes treating `modelRouting.implementation` as a per-task ceiling with a new selector, Step 8 dispatch changes, logging, docs, and tests. The main risks are internal inconsistencies in fallback semantics and audit reasons, plus an underspecified dependency on Step 2 complexity being available at Step 8.

## Findings

### 1. [High] internal-consistency · high confidence — Approach / select_impl_model logic item 2

> `ceiling not in {sonnet,opus,fable}` (unknown/haiku) → `("inherit", "ceiling <c> not a valid impl model — session model")` (fail-open; haiku ceiling for coding is a misconfig, degrade to session model rather than force Haiku).

The design claims Haiku is never used for coding, but the invalid/haiku ceiling path delegates to the session model without specifying or checking that the session model is not Haiku. If the session model is Haiku, implementing this as written violates the stated project rule and silently routes coding work to Haiku.

**Recommendation:** Change `select_impl_model` logic item 2 to return `("sonnet", "ceiling <c> not a valid impl model — fallback to sonnet")` for `haiku` and unknown implementation ceilings, or explicitly define and enforce that `inherit` can never resolve to Haiku for implementation dispatch.

### 2. [Medium] completeness · medium confidence — WF2 Step 8 delegation block

> Before each task's dispatch, call `select_impl_model(<resolved implementation ceiling>, task.riskLevel, <Step-2 complexity>)`; dispatch that task's subagent with the returned model.

The Step 8 change depends on `<Step-2 complexity>` being available at each dispatch point, but the artifact does not specify where that value is stored, how Step 8 retrieves it, or what happens if it is absent or malformed. A reasonable implementer would have to stop and determine the data flow before modifying `skills/implement-feature/SKILL.md`.

**Recommendation:** In the WF2 Step 8 section, add the exact source of the Step 2 complexity value, its accepted values, and the fallback behavior for missing or unknown complexity, e.g. `read workflow_state.complexity from Step 2; if missing or unknown, treat as standard_feature and include that in the reason`.
**Ambiguity:** The artifact references Step 2 complexity but does not define the handoff contract into Step 8.

### 3. [Medium] internal-consistency · high confidence — Test matrix (TDD)

> ceiling=sonnet, high risk → (sonnet, clamped); ceiling=sonnet, standard → (sonnet)

The test matrix expects a high-risk task with `ceiling=sonnet` to produce a `clamped` reason, but the selector logic says high-risk or complex work sets `desired = ceiling`, so no clamp occurs and the documented reason would be `high-risk/complex → ceiling <m>`. This will either make the selection-matrix test fail or make the implementation contradict the algorithm.

**Recommendation:** Change the test matrix entry to `ceiling=sonnet, high risk → (sonnet, high-risk/complex → ceiling sonnet)`, or change the algorithm to compute high-risk desired as a fixed higher tier before clamping and document that behavior consistently.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._