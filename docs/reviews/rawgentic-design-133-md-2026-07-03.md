# Adversarial Review — .rawgentic-design-133.md

- Date: 2026-07-03
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 5 (Critical 0, High 2, Medium 3, Low 0)

## Summary

The artifact proposes an opt-in whole-issue delegation mode with receipt validation and orchestrator-owned gates. The main risks are that several validation claims are not strong enough to support the stated trust boundary, and a fallback command can destroy unrelated workspace state.

## Findings

### 1. [High] correctness · high confidence — WF2 Step 8 — delegated-build sub-mode, step 3

> 3. **On return, VALIDATE** via `validate_build_receipt`. Reject → **restore** (`git reset --hard branch_base_sha && git clean -fd`) and **fall back to the normal per-task Step 8** (delegation can never block Step 8). Log the fallback loudly.

The fallback restore deletes all untracked files in the checkout, not just files produced by the delegated builder. If a receipt is rejected while the operator has pre-existing untracked work, `git clean -fd` removes that work, creating a concrete data-loss failure mode.

**Recommendation:** Change `WF2 Step 8 — delegated-build sub-mode`, step 3 to require a clean worktree before dispatch, or run the delegated builder in an isolated worktree and delete only that worktree on reject. Do not prescribe `git clean -fd` against the operator's main checkout.

### 2. [High] security · high confidence — Receipt contract, validation rule 1

> 1. Every plan task id present in `task_shas`; each sha exists on the branch (`git cat-file -e`) and is a descendant of `branch_base_sha` (`git merge-base --is-ancestor`). Missing/unknown/foreign sha → reject.

The validation only proves each task id names some descendant commit; it does not prove the commit corresponds to that task, is unique, contains the task's claimed files, or brackets the task's changes. A delegated builder can map a high-risk task to a benign commit and put the risky change elsewhere, causing Step 8a's per-task review on `receipt.task_shas` to review the wrong object.

**Recommendation:** In `Receipt contract`, change `task_shas` to a validated per-task commit range or commit id plus expected files, require distinct task commits in plan order, and verify each task's diff matches `files_per_task[task_id]` before Step 8a coverage can pass.

### 3. [Medium] feasibility · high confidence — Enablement

> read via the existing `adversarial_review_lib.is_enabled_for(..., key="wholeIssueDelegation")` (already supports arbitrary keys — no lib change).

The claim that the existing helper already supports arbitrary keys is load-bearing for the 'no lib change' design, but it is unverifiable from the provided artifact. If false, the opt-in flag will not be read and the feature will silently remain disabled or require unplanned library work.

**Recommendation:** In `Enablement`, either include the required behavior contract for `is_enabled_for` in this artifact or add an explicit implementation task and test proving `wholeIssueDelegation` is read through that helper.

### 4. [Medium] internal-consistency · high confidence — Receipt contract, validation rule 3

> 3. `files_per_task` union ⊆ the branch's actual changed files (`git diff --name-only base..HEAD`); a task claiming a file the diff doesn't show, or the diff showing a file no task claims → reject (undeclared change).

The formal set relation says only `files_per_task` must be a subset of the actual diff, but the prose also requires rejecting actual diff files that no task claims. An implementer following the subset condition would accept undeclared changed files, defeating the stated staging-discipline check.

**Recommendation:** Change validation rule 3 to state set equality explicitly: `set(union(files_per_task.values())) == set(git diff --name-only branch_base_sha..HEAD)`, then keep both rejection examples as consequences of that equality.

### 5. [Medium] internal-consistency · high confidence — Receipt contract schema and validation rule 2

>   "baseline": {"before": {"passed": N, "failed": N}, "after": {"passed": N, "failed": N}, "exit_code": 0},

The receipt schema places `exit_code` at `baseline.exit_code`, while the validation rule later refers to `after.exit_code`. This can produce incompatible implementations where valid receipts are rejected or the exit code is checked in the wrong location.

**Recommendation:** In `Receipt contract`, make the schema and rule match. Either move the field to `"after": {"passed": N, "failed": N, "exit_code": 0}` or change validation rule 2 to check `baseline.exit_code == 0`.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._