# Adversarial Review — 2026-07-03-model-routing-and-peer-consult-design.md

- Date: 2026-07-03
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 5 (Critical 0, High 1, Medium 3, Low 1)

## Summary

The artifact specifies two workflow features: configurable subagent model routing and an optional Codex peer-consult step. The main risks are in fail-open delegation semantics and underspecified concurrency/output handling where the design claims safety but omits state isolation details.

## Findings

### 1. [High] completeness · high confidence — Feature 1: modelRouting / Step 8 implementation delegation

> **Fail-open:** a task-agent that dies or returns vacuous output → the main loop retries that task once inline, logs the fallback, and continues. Delegation can never block Step 8.

The fallback path does not specify how to handle partial workspace changes left by a failed or vacuous task-agent. If the subagent dies after editing files, the inline retry may run on a dirty, partially-applied task state, causing duplicate edits, broken tests, or a misleading continuation despite the claim that delegation cannot block Step 8.

**Recommendation:** In `Step 8 implementation delegation`, require a clean-state boundary per task: record pre-task git status, reject fallback unless only expected files changed, and either commit/patch-capture successful task output or explicitly restore the task-agent's partial changes before inline retry.

### 2. [Medium] ambiguity · high confidence — Feature 2: peerConsult / Behavior (WF2 Step 3 sub-step)

> Claude drafts its own design concurrently and does not read Codex's output until both proposals exist.

The design requires blindness during concurrent design but does not define the storage and synchronization mechanism that prevents Claude from reading Codex output early while still detecting completion and later synthesizing both proposals. Implementers would have to invent whether this is a background process, temporary file, transcript capture, or separate orchestration step, which can break the blind-both-ways property.

**Recommendation:** In `Feature 2: peerConsult / Behavior`, specify the exact handoff: where Codex output is written, who starts and waits for the process, when Claude is allowed to read that artifact, and how failures/timeouts are represented without exposing partial content.
**Ambiguity:** The artifact states the desired isolation property but omits the operational mechanism needed to enforce it.

### 3. [Medium] feasibility · high confidence — Testing

> Full suite green against the post-#123 baseline (1384 passed, 5 warnings).

The design claims a completed test result before implementation planning has occurred, while later saying implementation will proceed from this spec. As written, the success criterion is unverifiable from the provided text and internally premature; an implementer cannot know whether this is an intended future acceptance criterion or an already-achieved result.

**Recommendation:** In `Testing`, change this bullet to a future acceptance criterion, e.g. `Full suite must be green against the post-#123 baseline; current expected baseline is 1384 tests and 5 warnings`, or remove the concrete pass count until implementation verification exists.

### 4. [Medium] internal-consistency · high confidence — Docs + release

> Implementation proceeds from this spec via an implementation plan; a tracking issue is filed at planning time.

This states implementation has not yet proceeded, which conflicts with the earlier concrete test-result claim. The mismatch makes the artifact's lifecycle state unclear and can cause planning to treat unimplemented behavior as already verified.

**Recommendation:** Align `Testing` and `Docs + release`: either mark all test bullets as planned verification items, or update `Docs + release` to state that implementation and test execution have already occurred.

### 5. [Low] completeness · medium confidence — Feature 1: modelRouting / Resolution library

> Soft opus floor: a `review` role resolved below `opus` (`sonnet`/`haiku`) resolves as configured but emits a stderr warning ("below recommended opus floor").

The floor check enumerates `sonnet` and `haiku` as below `opus` but the allowed values also include `inherit` and `fable`; the artifact does not define their ordering relative to `opus`. This leaves warning behavior ambiguous for `review: inherit` under a weaker session model or for `review: fable`.

**Recommendation:** In `Resolution library`, explicitly define the review-floor ordering for every allowed value, including `inherit` and `fable`, or state that the warning only applies to explicit `sonnet` and `haiku` values.
**Ambiguity:** The artifact defines a threshold but not the complete model ordering needed to apply it.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._