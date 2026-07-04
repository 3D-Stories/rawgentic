# Adversarial Review — .rawgentic-158-split-plan.md

- Date: 2026-07-04
- Artifact type: plan
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 5 (Critical 0, High 2, Medium 3, Low 0)

## Summary

The artifact plans a mechanical split of a large WF2 SKILL.md while preserving pinned semantics and test behavior. The main risks are internal contradictions between verbatim-preservation guarantees and later rewrite/cut work, plus a heading exception that undermines the stated corpus-slice rule for Step 16.

## Findings

### 1. [High] consistency · high confidence — Commits item 3; Success metric

> 3. refactor(wf2): dead-weight pass (AC5, separate reviewable cuts) — incl. #160 follow-up:
>    Step 11.5 prose "WF9 calls the same lib / never drift between the two workflows" →
>    names /rawgentic:scan as the surviving second caller (WF9 deprecated)

This commit describes both cuts and a concrete prose rewrite, but the success metric says there will be zero pin rewording and the invariant says mandatory-step semantics move verbatim. If Step 11.5 is pinned or mandatory-step prose, this creates a direct acceptance conflict and likely rework.

**Recommendation:** Move the Step 11.5 wording change to a separate follow-up with its own acceptance criteria, or add an explicit exception under `Invariant (AC3)` and `Success metric` stating that this exact line is intentionally reworded and not covered by zero-rewording verification.
**Ambiguity:** The artifact does not state whether Step 11.5 is a pin, but it is explicitly step prose within the planned split and conflicts with the verbatim-preservation rule.

### 2. [High] internal-consistency · high confidence — Invariant (AC3); Commits

> Restructure, not rewrite: every gate, constant, and mandatory-step semantic moves VERBATIM.
> Mechanical verifier after the split: every non-empty stripped line of the ORIGINAL SKILL.md
> must appear in the new corpus

The plan later includes a dead-weight pass and explicit prose replacement, which cannot satisfy a verifier requiring every original non-empty stripped line to remain in the new corpus. Implemented as written, either the verifier fails after commit 3 or the AC5 dead-weight/rewrite work cannot be performed.

**Recommendation:** In `Commits`, separate AC5 into a follow-up plan outside this invariant, or change `Invariant (AC3)` to define exactly which later deletion/rewording commit is exempt from the line-preservation verifier and when the verifier must run.

### 3. [Medium] completeness · high confidence — File layout

> references/steps.md: verbatim Steps 1-15 + 8a + full Step 16 detail, PLUS the moved
>   blocks that are step semantics: <small-standard-lane>, <trivial-work-check>,
>   <learning-config>. [Headless: ...] annotations move WITH their step prose (deliberate
>   AC2 deviation: duplicating them into headless.md would either double the pinned
>   33-count or break AC3's verbatim rule; headless.md stays the protocol home — noted in PR).

The plan relies on a deliberate AC2 deviation but only says it will be noted in the PR; it does not say which acceptance criterion or test is updated to permit the deviation. Implementers would have to stop and determine whether AC2 is still expected to pass as originally written.

**Recommendation:** In `File layout`, add a precise AC2 exception statement naming the expected location of `[Headless: ...]` annotations and the exact pin/test expectation after the split, instead of only saying it is `noted in PR`.

### 4. [Medium] correctness · high confidence — Heading rule

> references/steps.md carries the verbatim `## Step N:` sections for ALL steps (1, 1b,
>   2-16, incl. 8a) — slices then resolve into steps.md.

This claim is false for Step 16 under the artifact's own ordering rule, because the spine also contains `## Step 16:` and SKILL.md concatenates first. A reader implementing tests from this statement may assume Step 16 detail is protected when `text.index` will hit the spine stub instead.

**Recommendation:** Change this sentence to exclude Step 16, for example: `references/steps.md carries verbatim sections for Steps 1, 1b, 2-15, and 8a; Step 16 has a special spine stub and requires separate coverage for full detail`.

### 5. [Medium] internal-consistency · high confidence — Heading rule

> NO `## Step N:`
>   headings in the spine, with ONE exception:
> - `## Step 16:` STAYS in the spine

The heading rule says corpus guards use `text.index("## Step N:")` and SKILL.md is concatenated first, so keeping `## Step 16:` in the spine makes Step 16 resolve to the stub, not the detailed section in references/steps.md. That contradicts the later statement that slices resolve into steps.md for all steps and leaves any Step 16 corpus guard under-pinned.

**Recommendation:** In `Heading rule`, either remove `## Step 16:` from the spine and satisfy direct tests with a non-heading anchor, or explicitly state that Step 16 corpus-slice guards are absent and add a test/pin proving that the Step 16 detail in `references/steps.md` remains covered.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._