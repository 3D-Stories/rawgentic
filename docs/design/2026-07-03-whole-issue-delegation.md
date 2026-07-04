# Design: whole-issue delegated build mode (#133)

Date: 2026-07-03 · Issue #133 · Complexity: standard_feature (workflow mode) · lean gate

## Problem

WF2 Step 8 runs in the orchestrator's main loop. Even with per-task delegation (#132), the orchestrator dispatches each task-agent serially and re-runs the suite + diffs after every task — so an orchestrator working a multi-issue backlog accumulates the full per-task ceremony in its own context and bloats fast. Field feedback: this was a primary reason the operator diverged from WF2 after one issue.

## Approach

Add an **opt-in whole-issue delegated build mode**: after Step 6, the orchestrator hands ONE build-subagent the complete contract (design + plan + TDD requirement + conventions + test baseline). The subagent implements ALL tasks on the branch, commits per plan, and returns a structured **receipt**. The orchestrator's context then holds the receipt, not the build.

**Trust boundary (the core invariant):** the builder never self-certifies. Every gate stays with the orchestrator, re-run against the real tree — a receipt claim is a hypothesis until the orchestrator confirms it. Delegation relocates the *typing*, never the *gating*.

### Enablement (mirror adversarialReview/peerConsult)

Per-project `.rawgentic_workspace.json` field `wholeIssueDelegation: {enabled: bool, workflows: [str]}`, read via the existing `adversarial_review_lib.is_enabled_for(..., key="wholeIssueDelegation")`. **Verified (not assumed):** `is_enabled_for` (adversarial_review_lib.py:243) takes an arbitrary `key` and forwards it to `load_adversarial_review_config(..., key=key)`, which does `_coerce_config(proj.get(key))` — a generic per-project field read, not hardcoded to `adversarialReview`/`peerConsult`. So `key="wholeIssueDelegation"` reads the `{enabled, workflows}` shape with **no lib change**. A test asserts this end-to-end (see Files). Default absent → disabled → Step 8 runs exactly as today (per-task or inline). Also honored: an explicit invocation opt-out even when configured on.

### Receipt contract (`hooks/plan_lib.py`)

`validate_build_receipt(receipt: dict, plan_tasks: list, repo_root: str, branch_base_sha: str) -> tuple[bool, list[str], dict]` — pure-ish (git read-only), returns `(ok, errors, normalized)`. Receipt shape:
```json
{
  "task_shas": {"<task_id>": "<commit_sha>", ...},   // every plan task → a DISTINCT real commit on the branch
  "baseline": {"before": {"passed": N, "failed": N}, "after": {"passed": N, "failed": N, "exit_code": 0}},
  "files_per_task": {"<task_id>": ["<path>", ...]},   // MUST equal that commit's own changed-file set
  "promotions": [{"task_id": "<id>", "reason": "<why>"}]  // mid-flight risk promotions the builder flagged
}
```
Note `exit_code` lives at `baseline.after.exit_code` (was ambiguous in an earlier draft — folded Codex finding 5).

Validation (fail-closed — any failure → receipt REJECTED → fall back):
1. **Existence + lineage + distinctness.** Every plan task id present in `task_shas`; each sha exists (`git cat-file -e <sha>^{commit}`), is a strict descendant of `branch_base_sha` (`git merge-base --is-ancestor base sha` exit 0 AND `sha != base`), and all task SHAs are **distinct**. Missing/unknown/foreign/duplicate/equals-base sha → reject.
2. **sha↔task binding (folded Codex finding 2 — the trust boundary).** A descendant sha alone does not prove the commit *is* that task's work. So for each task, the commit's OWN changed-file set (`git show --name-only --format= <sha>`) MUST equal `set(files_per_task[task_id])`. This binds each reviewed sha to its claimed files, so Step 8a reviews the right object and the builder cannot park a risky change in a different commit than the one a high-risk task names. Mismatch → reject.
3. **Baseline non-regression.** `baseline.after.failed <= baseline.before.failed` (no NEW failures) AND `baseline.after.exit_code == 0`. A regression → reject. (The orchestrator's own Step 9 re-run is the real gate; this only rejects an obviously-bad receipt early.)
4. **Staging discipline = set EQUALITY (folded Codex finding 4).** `set(union(files_per_task.values())) == set(git diff --name-only branch_base_sha..HEAD)`. A task claiming a file the diff doesn't show, OR the diff showing a file no task claims → reject (undeclared change). Subset is NOT enough — equality both directions.
5. `promotions` normalized; high-risk-promoted task ids surfaced (`normalized["promoted_task_ids"]`) so the orchestrator dispatches Step 8a for them.

### WF2 Step 8 — delegated-build sub-mode (prose; detail in `references/whole-issue-delegation.md` to keep SKILL.md lean)

When enabled:
0. **Pre-flight clean-worktree gate (folded Codex finding 1 — data-loss guard).** Before dispatching, require a clean worktree: `git status --porcelain` empty. If the operator has ANY staged/unstaged/untracked work, do NOT enter delegation — log it and fall back to the normal per-task Step 8. Rationale: the reject path must be able to discard the builder's output without touching the operator's pre-existing files.
1. **Record** `branch_base_sha` and the pre-build test baseline.
2. **Dispatch ONE build-subagent** with the brief (design, plan w/ riskLevels, TDD requirement, conventions, baseline) + the required receipt schema. Model: the `implementation` ceiling via `select_impl_model` for the *highest-risk* task in the plan (the whole build is one agent, so size it to the hardest task; never Haiku).
3. **On return, VALIDATE** via `validate_build_receipt`. Reject → **restore** then **fall back to the normal per-task Step 8** (delegation can never block Step 8). Restore = `git reset --hard branch_base_sha` (tracked) followed by removing ONLY the untracked paths the receipt declared (`union(files_per_task.values())` filtered to those actually untracked). **Never** blanket `git clean -fd` against the operator's checkout — with the step-0 clean-worktree gate the only untracked files present are the builder's, but targeted removal is the belt-and-suspenders that makes an unparseable/partial receipt safe (on unparseable receipt: `git reset --hard base` only, and WARN that builder untracked files may remain rather than blind-deleting). Log the fallback loudly.
4. **On valid receipt, run the gates in the orchestrator against the real tree:**
   - Step 8a: for every high-risk task (tagged in Step 5 OR promoted in the receipt), dispatch the per-task review on that task's receipt sha — coverage asserted via the existing `assert_review_coverage(<log>, plan_tasks, receipt.task_shas)`. **8a is NOT delegated** — the orchestrator owns it.
   - Step 9: re-run the full suite from the orchestrator (Part B evidence); the receipt's baseline is a claim, the orchestrator's own run is the gate.
   - Steps 11 / 11.5: unchanged (full diff review + scan).
5. Interplay with #135 lane: in the small-standard lane, whole-issue delegation is still allowed (the collapsed gates still run in the orchestrator); the receipt's Step-8a set is just usually empty.

## Files
- `hooks/plan_lib.py`: `validate_build_receipt` + a `BuildReceiptError`-free tuple return (fail-closed like assert_review_coverage). Reuse `_git_run` helpers already present.
- `skills/implement-feature/SKILL.md`: Step 8 delegated-build sub-mode block (thin) + pointer to the reference.
- `skills/implement-feature/references/whole-issue-delegation.md`: the build-subagent brief template + the receipt schema + the validation/fallback contract (keeps SKILL.md under budget).
- `docs/config-reference.md`: document `wholeIssueDelegation`.
- `tests/hooks/test_plan_lib.py`: receipt validation matrix against a real temp git repo (missing sha, foreign/non-descendant sha, sha==base, duplicate sha, per-commit file-set mismatch [finding 2], baseline regression, after.exit_code!=0, undeclared file [union⊄diff], unclaimed diff file [diff⊄union], valid receipt, promotions surfaced) + drift guards for the SKILL sub-mode + reference existence.
- `tests/hooks/test_adversarial_review_io.py` (or test_plan_lib): `is_enabled_for(..., key="wholeIssueDelegation")` reads a real workspace fixture end-to-end (finding 3 — proves the no-lib-change claim).
- Version bump minor → 2.49.0.

## AC coverage (issue #133)
AC1 opt-in flag, default unchanged → `wholeIssueDelegation` config + is_enabled_for. AC2 build brief + receipt schema documented → references/ file. AC3 all Step 8a/9/11/11.5 gates in the orchestrator against receipt+tree → the gate loop; receipt is hypothesis-until-confirmed. AC4 failure path restore+fallback+log → step 3. AC5 receipt validation tests (missing sha/regression/undeclared file → reject) → the matrix.

## Codex adversarial review — findings folded (2026-07-03, 0 Critical / 2 High / 3 Medium)
1. [High] `git clean -fd` reject-path data loss → step-0 clean-worktree gate + targeted (not blanket) untracked removal.
2. [High] descendant-sha doesn't prove commit-is-task's → validation rule 2 binds each sha to its commit's own file-set.
3. [Medium] `is_enabled_for` arbitrary-key claim unverified → verified in source (adversarial_review_lib.py:243), test added.
4. [Medium] subset vs equality → rule 4 is now set EQUALITY both directions.
5. [Medium] `exit_code` schema/rule location mismatch → normalized to `baseline.after.exit_code`.
No Critical/blocker → no design loop-back (lean review policy); findings incorporated in place.

## Out of scope
Parallel multi-issue builds (needs worktrees — #136/#85); changing any gate's semantics; delegating Step 8a/11 (they stay orchestrator-side by design — that IS the trust boundary).

## Note
This composes with #132 (per-task ceiling) — the single build-subagent is sized by `select_impl_model` to the plan's hardest task, and never Haiku.
