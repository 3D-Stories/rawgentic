# Design: small-standard lane — a middle gear between trivial and full WF2 (#135)

> rev 2 — all 5 Codex findings folded (exact file-count rule, input-source honesty, retained/removed gate lists, evidence-as-heuristic table, semantic-replacement predicate).

Date: 2026-07-03 · Issue #135 · Complexity: standard_feature (workflow-spine change) · lean gate

## Problem

WF2 has two cost reducers with a gap between them:
- `<trivial-work-check>` — exits WF2 entirely for typo-grade changes (1 file, ≤10 lines).
- `<fast-path-detection>` — lightens **Step 4 only** (reflect vs 3-judge critique).

Everything above trivial pays the full remaining spine regardless of size: plan + drift gates (Steps 5/6/9), the design-stage machinery (peer consult, 3-judge panel, adversarial-on-design), and full run-record ceremony. Two field campaigns (SayStory 7-issue Tauri, 3dstories backend) hand-rolled a middle gear for small/standard features — a 3–5-file UI feature or a 2-file hardening guard genuinely needs a **code review** but not a **pre-implementation design panel**. Field evidence: across 6 SayStory features the Step-11 diff review caught every real bug; the Step-4 design panel mostly reaffirmed already-sound designs. Invest in review, not design ceremony, for small work.

## Approach

**The lane is a SEMANTIC REPLACEMENT of the Step-4-only fast path, generalized to the whole spine** [rev2, Codex F5]. Today `fast_path_eligible` (Step 2 item 8) only swaps critique→reflect at Step 4. This is NOT a rename or a pure extension — it changes the target set AND what the flag controls, so it gets a new canonical predicate:

- **`small_standard_lane_eligible`** replaces `fast_path_eligible`. `fast_path_eligible` is kept as a **deprecated alias** (`fast_path_eligible = small_standard_lane_eligible`) so any existing "Step-4 reflect vs critique" reader keeps working unchanged — for that one decision the lane still selects reflect, so old Step-4-only callers see identical behavior. New code reads the canonical name.
- The flag now controls the **whole lane** (Steps 3/4/5/6/9 collapse), not just Step 4.

### Lane eligibility (Step 2, replaces item 8; suggested-never-silent)

`small_standard_lane_eligible == true` when ALL:
- complexity ∈ {simple_change, standard_feature} (never complex_feature — always full spine), AND
- **changed implementation source files ≤ 7** — counting rule (same rule used by `lane_decision`) [rev2, Codex F1]: count non-test, non-doc **source** files the change will create/modify; **exclude** test files (`test_*`, `*_test.*`, `tests/`), docs (`*.md`, `docs/`), and generated/lockfiles; a rename counts as **1**. Test+doc files are deliberately excluded because a small feature legitimately touches several test/doc files without being "big." AND
- no architecture change, no migration, no new cross-service surface, no new dependency (the signals Step 2 already gathers), AND
- not `trivial_work` (that has its own exit at `<trivial-work-check>`).

This SUPERSEDES the old two-branch rule (simple_change unconditionally + standard_feature only if WF1-validated). Rationale: a non-WF1 standard_feature of bounded size is exactly the SayStory case that needed the lane; requiring WF1 origin excluded it. WF1 origin still *strengthens* confidence but is no longer required.

**Input-source honesty** [rev2, Codex F2]: `lane_decision` is a pure, unit-tested function, but at Step 2 (pre-implementation) `file_count` is an **estimate** from the component map — there is no diff yet. So lane eligibility is "mechanically decided **given** the Step-2 estimates," not fully mechanical end-to-end. Guard: **Step 9 cross-checks the actual changed-file count** (`git diff --name-only origin/<default>..HEAD`, applying the same counting rule); if the real count materially exceeds the lane threshold, record a `lane-widened` note in session notes + the run-record (the design panel was skipped on a change that turned out larger than estimated) — do NOT retroactively fail (the gates that DID run — Step 11, 11.5, 8a — are still valid and are the load-bearing ones). A deterministic pre-diff detector is the AC5 follow-up.

**Surfacing (mirror `<trivial-work-check>`):** when `lane_eligible` and not already forced, present:
```
Step 2 → SMALL-STANDARD detected (<N files, complexity>). Recommend the small-standard lane:
  keeps TDD + code review + security scan + CI; skips the design panel + drift gates.
  (a) Small-standard lane  [recommended]
  (b) Full WF2 (design panel + all gates)
```
[Headless: AUTO-RESOLVE → lane for eligible; full for complex_feature. Log the choice.]

### Keep / collapse table (the contract)

| Step | Full WF2 | Small-standard lane | Why |
|---|---|---|---|
| 3 Design | inline 1-2 approaches + doc | **brief design note** (file list + failure modes + security), no multi-approach brainstorm | small work has one obvious approach |
| 4 Design critique | 3-judge panel + peer consult + adversarial-on-design | **`/reflexion:reflect` only** (already the fast-path) — NO panel, NO peer consult, NO adversarial-on-design | field: panel reaffirms sound small designs |
| 5 Plan | full task decomposition + drift-ready fields | **checklist plan**: ordered tasks, each with `riskLevel` + a verification line; parallel_group/files optional | keeps TDD + risk tagging; drops ceremony |
| 6 Plan drift | reflect + optional adversarial-on-plan | **SKIP** (folded — the checklist is small enough to eyeball; Step 9 still verifies AC coverage) | a 3-task checklist has no drift surface |
| 8 / 8a | TDD; 8a per high-risk task | **UNCHANGED** — TDD kept; **8a still fires for any `riskLevel: high` task** | security surface never loses per-task review |
| 9 Impl drift | reflect (Part A) + evidence (Part B) | **evidence-only**: run the suite, record the delta, verify each AC has a covering test; skip the alignment reflect | the reflect adds little on a checklist plan; evidence is the real gate |
| 11 Code review | 3-agent (complex) | **≥1 reviewer** (existing "minimum 1-agent for simple/standard") + the opt-in diff adversarial sub-step (#131) still applies | **NON-NEGOTIABLE — this is where the value is** |
| 11.5 Security scan | full | **UNCHANGED** | tool gate never skipped |
| 12/13/14 PR/CI/merge | full | **UNCHANGED** | |
| 16 run-record | full | **UNCHANGED shape**, `complexity` reflects lane; add `lane: "small-standard"` marker | lane runs stay measurable vs full |

**Exact retained vs removed gates** [rev2, Codex F4 — no vague "every safety gate"]:
- **RETAINED (unchanged):** TDD red-green (Step 8), Step 8a per-task review for any `riskLevel: high` task, Step 11 code review (≥1 reviewer) + the #131 opt-in diff adversarial sub-step, Step 11.5 security scan, CI (Step 13), PR + merge (Steps 12/14), run-record (Step 16).
- **COLLAPSED:** Step 3 (brief note, no multi-approach brainstorm), Step 4 (`/reflexion:reflect` only — no 3-judge panel, no peer consult, no adversarial-on-design), Step 5 (checklist plan, keeps riskLevel + verification), Step 9 (Part B evidence only — Part A alignment reflect removed).
- **REMOVED entirely:** Step 6 (plan drift).

The skill's own history is the reason the RETAINED set is non-negotiable: Step 11 caught 2 Criticals on a run judged "too simple to review." The lane is cheaper on **design ceremony**, never on **review or security**.

### `<mandatory-steps>` reconciliation

Steps 4, 5, 9 stay "mandatory" but gain a lane column: in the lane they run in their collapsed form (reflect / checklist / evidence-only) — they are not *skipped*, so the mandatory-step invariant holds. Only Step 6 is skipped in the lane, and it is already a conditional step ("run unless time-critical") — add "or in the small-standard lane" to its skip condition.

## Files
- `skills/implement-feature/SKILL.md`: rewrite `<fast-path-detection>` → `<small-standard-lane>` (eligibility + surfacing + keep/collapse table); update Step 2 item 8, Step 4 gate selection, Step 5 (checklist variant), Step 6 skip condition, Step 9 (evidence-only variant), `<mandatory-steps>` lane note, Step 16 lane marker.
- `hooks/plan_lib.py`: `lane_decision(complexity, impl_file_count, has_arch_change, has_migration, has_new_dep, is_trivial) -> tuple[str, str]` returning `("full"|"lane"|"trivial", reason)` — pure, unit-testable (mirrors `should_run_diff_review`). `impl_file_count` follows the counting rule above (tests/docs/generated excluded). Decision order: `is_trivial` → `("trivial", …)` (handled by the trivial-work exit, not the lane); `complexity == "complex_feature"` OR any of arch/migration/new-dep → `("full", …)`; `impl_file_count > 7` → `("full", …)`; else → `("lane", …)`. Plus `LANE_MAX_IMPL_FILES = 7` constant + a `count_impl_files(paths) -> int` helper applying the exclusion rule (reused by the Step 9 cross-check on real `git diff --name-only`).
- `docs/config-reference.md` or `docs/` WF2 doc: document the three tiers (trivial / small-standard lane / full).
- `tests/`: `lane_decision` matrix + SKILL.md drift guards (keep/collapse table present, hard-floor steps still mandatory-tagged, run-record lane marker).
- Version bump minor → 2.48.0 (new workflow lane).

## AC coverage (issue #135)
AC1 lane entry mechanically decided → `lane_decision` pure fn (mechanical GIVEN Step-2 inputs; file_count is a Step-2 estimate, cross-checked at Step 9 — see input-source honesty). AC2 keep/collapse table with rationale → the exact retained/collapsed/removed lists above, ported into SKILL.md. AC3 suggested-never-silent + headless behavior → surfacing block. AC4 run-record still emitted with lane marker → Step 16 unchanged + `lane` field. AC5 follow-up impl issue → file one for a deterministic pre-diff file_count/arch-change detector (beyond the Step-2 estimate).

## Out of scope
Changing WF3 (fix-bug already exists for narrow bugs); weakening Step 11/11.5 in any lane; auto-measuring file_count/arch-change (the orchestrator estimates from Step 2 today — a mechanical detector is the AC5 follow-up); a fourth tier.

## Field-evidence note (observational, not a controlled study) [rev2, Codex F3]

Two autonomous campaigns hand-rolled this middle gear; the split below is what they converged on. This is **observational** (2 campaigns, self-reported, no control arm) — treated as a **heuristic**, not proof. The retained gates do not rest on it (they rest on the skill's own Step-11-caught-2-Criticals history); the evidence only motivates *dropping the design panel for small work*.

| Campaign | Small features | Real bugs caught by Step 11 (diff review) | Design-panel (Step 4) contribution | Missed/rework |
|---|---|---|---|---|
| SayStory (Tauri) | 6 | 5 — filter mismatch mis-stating a delete count; non-atomic write resetting lifetime stats on crash; dead "stalled" branch; pause-state desync; stale tray after download | mostly reaffirmed already-sound designs | none reported attributable to skipping the panel |
| 3dstories backend | (this campaign) | fail-open bugs caught at diff stage (motivated #131) | design/plan gates missed the code-level fail-opens | — |

Caveat: neither campaign ran the full panel AND the lane on the same feature, so "panel would have added nothing" is inferred, not measured. If a future run finds a design-stage defect the lane would have missed, revisit the collapse of Step 4.
