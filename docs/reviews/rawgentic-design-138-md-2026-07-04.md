# Adversarial Review — .rawgentic-design-138.md

- Date: 2026-07-04
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 4 (Critical 0, High 2, Medium 2, Low 0)

## Summary

The artifact defines a deferred verification state for tasks whose remaining behavior cannot be exercised locally. The main risk is that the design asserts gate honesty but leaves key evidence as unstructured text and a count, so the stated anti-abuse invariant is not actually enforceable as written.

## Findings

### 1. [High] completeness · high confidence — Step 16 run-record

> Add optional top-level `verification_deferred` (non-negative int; count of deferred tasks), validated only when present, distinct from tests pass/fail.

The run-record only stores a count, but the completion gate requires recorded reason, proxy, PR section, and manual target check. A count cannot identify which deferred tasks were recorded or whether their proxy/manual-check evidence exists, so an implementation can satisfy the numeric field while omitting the evidence the design says is required. The concrete failure is a falsely satisfied completion gate for an incompletely recorded deferral.

**Recommendation:** Change Step 16 run-record to store a structured list such as `verification_deferred: [{task_id, reason, local_proxy, target_check}]`, and require validation that every plan-deferred task appears exactly once.

### 2. [High] feasibility · high confidence — Step 5 Completion gate

> An **unrecorded** deferral (a task the plan marked deferred but Step 9/12/16 didn't surface) → gate failure.

The design does not specify a mechanism for detecting that a plan-marked deferred task was not surfaced in Step 9, Step 12, or Step 16. Step 9 and Step 12 are SKILL.md prose outputs, while Step 16 is only an optional count, so the gate cannot reliably compare planned deferred tasks against recorded evidence. The concrete failure is that unrecorded deferrals can pass undetected unless a human manually audits all sections.

**Recommendation:** In the completion-gate section, define an explicit validation algorithm: read `deferred_tasks(plan)`, compare task identifiers against the structured run-record entries and PR section entries, and fail on missing, duplicate, or mismatched entries.

### 3. [Medium] ambiguity · high confidence — Step 1 Plan contract

> Other `- verification: <free text>` values are ignored (they are ordinary Implement-Verify commands, not our concern).

The plan parser is instructed to ignore free-text verification values, but the same line prefix is also used to identify deferred verification. The artifact does not specify how ordinary Implement-Verify commands are preserved or consumed if this parser ignores them, creating ambiguity about whether adding deferral parsing changes existing verification command handling.

**Recommendation:** In Plan contract, specify the existing field or parser path that retains ordinary `- verification: <free text>` commands, or state explicitly that this parser only extracts deferral metadata and leaves the original verification text unchanged.
**Ambiguity:** The artifact may rely on existing parser behavior, but that behavior is not provided in the design text.

### 4. [Medium] consistency · high confidence — Files / Step 12

> Step 12 (Deferred Verification PR section)

The artifact uses two different section names for the same PR body section: `Deferred verification` and `Deferred Verification`. Drift guards and completion-gate checks that match headings literally could miss the section or enforce the wrong heading.

**Recommendation:** Normalize the Step 12 heading everywhere to one exact string, for example `Deferred verification`, and require drift guards to check that exact heading.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._