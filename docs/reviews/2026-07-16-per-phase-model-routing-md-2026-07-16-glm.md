# Adversarial Review — 2026-07-16-per-phase-model-routing.md

- Date: 2026-07-16
- Artifact type: plan
- Reviewer: GLM (model glm-5.2, reasoning effort high)
- Findings: 5 (Critical 0, High 1, Medium 3, Low 1)

## Summary

This artifact proposes model routing for WF2/WF3 based on bench #14, recommending opus as driver, fable for review, sonnet for analysis/ship/standard-impl, with one config value change plus a review fallback chain and several child issues. The core risk is a sequencing gap where the config change lands before its safety mechanism, plus an internally contradictory reliability table and an underspecified quota-detection mechanism.

## Findings

### 1. [High] completeness · high confidence — §7 Epic + child issues

> Smallest child; unblocks measurement of the rest.

Child 1 flips review to fable ('one value change') and is explicitly marked the first child that 'unblocks measurement of the rest.' Child 2 builds the fallback chain, and §4 promises 'fallback opus-4-8 on quota exhaustion.' Because the plan says children are independent single-PRs ('children 1–6 are each single-PR; only the last references Closes on the epic') with child 1 going first, there is a window in which review=fable is live in production while the fallback mechanism does not yet exist. Concrete failure: any run during that window that exhausts fable quota has no opus fallback, so the review gate fails or errors — the exact scenario §4 promises is handled.

**Recommendation:** In §7, add an explicit ordering/dependency constraint: child 2 (fallback chain) MUST land in the same release window or before child 1's config flip, or fold the fallback logic into child 1. State: 'child 1 depends on child 2 — do not merge review=fable until the run-scoped fable→opus circuit breaker is deployed.'

### 2. [Medium] ambiguity · high confidence — §5.2 Review fallback chain

> first *recognized quota/capacity rejection* from a fable dispatch trips `fable_exhausted`

The fallback circuit breaker hinges on detecting a 'recognized quota/capacity rejection' and distinguishing it from a 'failed/timed-out review that is NOT a quota rejection.' The artifact never specifies HOW a quota rejection is recognized — no error code, status, message pattern, or provider-specific signal is given. Without this, an implementer cannot build the trip/no-trip logic, and the acceptance criterion 'never-below-opus' is unverifiable because the trigger condition is undefined.

**Recommendation:** In §5.2, enumerate the concrete signals that count as a quota/capacity rejection (e.g., HTTP 429 with rate/quota headers, fable-specific capacity status strings) and state that all other errors (timeout, 5xx, parse failure) fall into the non-quota retry path.
**Ambiguity:** The recognition contract is entirely unspecified; 'recognized' is doing all the work with no definition.

### 3. [Medium] feasibility · medium confidence — §5.2 Review fallback chain (Step 8a note)

> the clamp is prose + dispatch discipline, not new infrastructure.

The ≤3 concurrent Claude subagent ceiling for Step 8a fan-out is said to be enforced by 'prose + dispatch discipline.' There is no code-level enforcement cited, so a concurrent over-subscription to fable/opus could exceed the ceiling purely from prose not being followed, which also amplifies the very quota-exhaustion risk the fallback chain exists to contain.

**Recommendation:** Either cite an existing programmatic concurrency clamp (code anchor) that enforces the ≤3 ceiling, or add a child issue to enforce it in the dispatch path rather than relying on skill prose.

### 4. [Medium] internal-consistency · high confidence — §2.3 Reliability floors table

> 6/6 (plan 4/5 on P-G1)

In the §2.3 reliability-floor table, the 'gates (design+plan)' column for fable-5 reports '6/6 (plan 4/5 on P-G1)'. This is self-contradictory: if plan gates pass only 4/5, the combined design+plan gate count cannot be 6/6. Since the reliability floor / gate-cleanliness argument is the load-bearing reason the driver seat is chosen, an internally inconsistent gate count in this table undermines trust in the safety-floor evidence.

**Recommendation:** Correct the fable row in §2.3 to a non-contradictory gate count, e.g. 'design 6/6, plan 4/5 (P-G1 miss)' or recompute the combined fraction, and clarify whether the 6/6 figure refers only to design gates.
**Ambiguity:** The notation is ambiguous about whether 6/6 refers to design-only or combined gates, but either reading conflicts with the parenthetical.

### 5. [Low] consistency · high confidence — §2.2 gap table header

> n=6 cells/model/phase

§2.2's header states 'n=6 cells/model/phase' for the gap/sd calculations, but §2.3 and its footnote establish that fable's plan row has only 5 valid cells ('80 (n=5¹)'). Any pooled-sd figure touching fable-plan is therefore computed at n=5, contradicting the blanket n=6 claim. While no fable-plan gap row is shown in §2.2, the global n=6 statement is inaccurate as written.

**Recommendation:** Amend the §2.2 header to 'n=6 cells (fable plan n=5; one null cell)' so the stated sample size matches the footnoted exception.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._