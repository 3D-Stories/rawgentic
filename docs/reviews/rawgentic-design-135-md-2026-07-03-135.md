# Adversarial Review — .rawgentic-design-135.md

- Date: 2026-07-03-135
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 5 (Critical 0, High 2, Medium 3, Low 0)

## Summary

The artifact proposes replacing the existing WF2 fast path with a broader small-standard lane that collapses design and drift ceremony while preserving review and security gates. The main risks are ambiguous eligibility, conflicting terminology around mandatory safety gates, and unverified field evidence used as a load-bearing justification.

## Findings

### 1. [High] ambiguity · high confidence — Lane eligibility

> `lane_eligible == true` when ALL:
> - complexity ∈ {simple_change, standard_feature} (never complex_feature — those always get the full spine), AND
> - ≤ ~7 files and no architecture change, no migration, no new cross-service surface, no new dependency (the same signals Step 2 already gathers), AND
> - not `trivial_work` (that has its own exit).

The lane boundary uses an approximate threshold, `≤ ~7 files`, while the rest of the design makes lane entry consequential and auto-resolved in headless mode. Around the boundary, implementers cannot know whether 7 files, 8 files, generated files, tests, docs, or renamed files qualify, which can route work into the reduced lane incorrectly.

**Recommendation:** Change `Lane eligibility` to define an exact threshold and counting rule, e.g. `changed implementation files <= 7; tests and docs excluded/included explicitly; generated files excluded; renames count as one changed file`, and use the same rule in `lane_decision`.
**Ambiguity:** The artifact does not define what `~7 files` means or how file_count is computed.

### 2. [High] internal-consistency · high confidence — AC coverage / Files

> AC1 lane entry mechanically checkable → `lane_decision` pure fn. AC2 keep/collapse table with rationale → the table above, ported into SKILL.md. AC3 suggested-never-silent + headless behavior → surfacing block. AC4 run-record still emitted with lane marker → Step 16 unchanged + `lane` field. AC5 follow-up impl issue → file one for any deeper automation (e.g. auto-detecting file_count) beyond the prose lane.

The artifact claims lane entry is mechanically checkable, but defers deeper automation such as detecting `file_count`, even though `file_count` is part of the proposed `lane_decision` input. That leaves the key eligibility data manually estimated while presenting the outcome as mechanically checkable, which will likely cause incorrect or inconsistent lane decisions.

**Recommendation:** Change `AC coverage` and `Files` so AC1 covers the full decision inputs: either implement deterministic detection for `file_count` and the other booleans now, or state that AC1 is only mechanically checkable after manual Step 2 classification and remove the stronger claim.

### 3. [Medium] completeness · medium confidence — Problem / Field-evidence note

> Field evidence: across 6 SayStory features the Step-11 diff review caught every real bug; the Step-4 design panel mostly reaffirmed already-sound designs. Invest in review, not design ceremony, for small work.

The field evidence is load-bearing for dropping the design panel and drift checks, but the artifact does not define what counted as a `real bug`, how many bugs there were, whether any design-stage issues were missed, or whether the campaigns are representative of the proposed eligibility space. A reasonable implementer cannot validate that the evidence supports the generalized lane.

**Recommendation:** Change `Field-evidence note` to include a compact evidence table with campaign, feature count, bug count, design-panel findings, Step-11 findings, and any missed/rework cases; otherwise downgrade the rationale from evidence-backed to heuristic.

### 4. [Medium] consistency · high confidence — Approach

> **The lane IS the fast-path, generalized from "Step 4 only" to the whole spine.** Today `fast_path_eligible` only swaps critique→reflect at Step 4. Redefine it: `fast_path_eligible == true` selects the **small-standard lane**, which trims ceremony across Steps 4/5/6/9 while keeping every safety gate.

This says the lane keeps every safety gate, but the same sentence says it trims Steps 5/6/9, and later the design explicitly skips Step 6 and part of Step 9. Because the artifact earlier identifies drift gates as part of the WF2 spine, implementers can reasonably disagree on whether drift checks are safety gates that must remain or ceremony that may be removed.

**Recommendation:** Change `Approach` to name the exact retained gates instead of saying `every safety gate`, e.g. `while keeping TDD, Step 8a for high-risk tasks, Step 11, Step 11.5, CI, and PR/merge gates; Step 6 and Step 9 Part A are intentionally removed in this lane`.
**Ambiguity:** The phrase `safety gate` is not defined, and the design uses both `keeps every safety gate` and `skips the design panel + drift gates`.

### 5. [Medium] internal-consistency · high confidence — Approach

> No new eligibility predicate is invented — the existing `fast_path_eligible` computation (Step 2 item 8) already encodes exactly the target set (simple_change, or standard_feature that is WF1-validated); we extend it to also admit a non-WF1 standard_feature that passes a size check, and route all of it through the lane.

The artifact says no new eligibility predicate is invented, but it changes the target set by adding non-WF1 standard features and later introduces `lane_eligible` and `lane_decision`. This is not just a rename; it changes semantics and creates migration risk for any existing logic that interprets `fast_path_eligible` as only Step 4 critique selection.

**Recommendation:** Change `Approach` to state that this is a semantic replacement, not an extension without a new predicate: define one canonical name such as `small_standard_lane_eligible`, deprecate `fast_path_eligible`, and specify how old Step 4-only callers are migrated.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._