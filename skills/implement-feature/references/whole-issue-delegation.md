# WF2 Step 8 — whole-issue delegated build mode (#133)

This is the detail behind the thin `wholeIssueDelegation` block in `SKILL.md`
Step 8. Read it in full before using the mode. The mode is **opt-in and
default-off**; when it is not enabled Step 8 behaves exactly as it does today.

## Why this exists

Per-task delegation (#132) still runs the whole per-task ceremony — dispatch,
scoped-suite re-run, diff, commit inspection — inside the orchestrator's own loop. An
orchestrator working a multi-issue backlog accumulates all of that in its own
context and bloats fast. Whole-issue delegation hands ONE build-subagent the
entire branch so the orchestrator's context holds a **receipt**, not the build.

## The trust boundary (non-negotiable)

The builder **never self-certifies**. Delegation relocates the *typing*, never
the *gating*. Every gate stays with the orchestrator and is re-run against the
real tree: a receipt claim is a hypothesis until the orchestrator confirms it.
Concretely, the orchestrator still owns Step 8a (per-task review), Step 9 (full
suite re-run), Step 11 (diff review), and Step 11.5 (security scan) — none of
them are delegated, and none of them trust the receipt's word.

## Build-subagent brief template

Dispatch exactly one subagent, sized by `select_impl_model` to the plan's
**highest-risk** task (one agent builds everything, so size it to the hardest
task; never `model: haiku`). Give it:

```
You are the whole-issue build agent for issue #<N> on branch <branch>.
Implement EVERY task in the plan below, test-first (TDD: RED → GREEN →
REFACTOR), committing each task as its own conventional commit
`<type>(scope): <desc> (#<N>)`. Stage ONLY that task's files per commit —
never `git add -A`/`git add .`. Build tasks in plan order; each builds on the
previous commit.

Contract:
- Design doc: <path/inline>
- Plan (tasks with id, title, riskLevel, files): <path/inline>
- Conventions: <project conventions>
- Test command: <capabilities.test_commands>
- Pre-build test baseline: {before: {passed: N, failed: N}}
- Base commit (do not amend/rebase past it): <branch_base_sha>

If mid-build you touch a path riskier than the plan tagged (a security surface,
a migration, auth), FLAG it in `promotions` — do not silently absorb it.

Return ONLY the receipt JSON (schema below). Your final message IS the receipt;
do not add prose. Do NOT open a PR, merge, or push to any remote — the
orchestrator owns everything after the build.
```

## Receipt schema

```json
{
  "task_shas": {"<task_id>": "<commit_sha>"},
  "baseline": {
    "before": {"passed": 0, "failed": 0},
    "after":  {"passed": 0, "failed": 0, "exit_code": 0}
  },
  "files_per_task": {"<task_id>": ["<path>"]},
  "promotions": [{"task_id": "<id>", "reason": "<why>"}]
}
```

- `task_shas` — every plan task id → the **distinct** commit that implements it.
- `files_per_task[id]` — MUST equal that commit's own changed-file set (this is
  what binds the sha to the task; see validation rule 2).
- `baseline.after.exit_code` — the final suite run's exit code (0 = green).
- `promotions` — mid-flight risk promotions the builder flagged; the
  orchestrator dispatches Step 8a for each.

## Collect before validation (worktree-isolated builds, #164)

The build-subagent (`rawgentic:rawgentic-implementer`) runs `isolation:
worktree`: its commits land in the shared object store, NOT on the feature
branch. Validation Rule 4 diffs `base..HEAD` on the ORCHESTRATOR's checkout,
so an un-collected worktree build always fails Rule 4 (empty diff) and would
be discarded on every run. Therefore, before invoking
`validate_build_receipt`: fast-forward the feature branch to the receipt's
final sha (or cherry-pick `task_shas` in plan order) and assert the branch
advanced past `branch_base_sha`. A build whose commits never reach the branch
is NOT done — and skipping the collect would also let the later diff-scoped
gates (8a/11/secret scan) run over an empty diff.

## Validation (`plan_lib.validate_build_receipt`)

`validate_build_receipt(receipt, plan_tasks, repo_root, branch_base_sha)` returns
`(ok, errors, normalized)`. It is **fail-closed**: any structural problem or
rule violation → `ok=False`. git is read-only. Rules:

1. **Existence + lineage + distinctness** — every plan task id present; each sha
   exists (`git cat-file -e`), is a strict descendant of `branch_base_sha`
   (`git merge-base --is-ancestor` AND `sha != base`), and all task SHAs are
   distinct. Missing / unknown / foreign / duplicate / equals-base → reject.
2. **sha↔task binding (the trust boundary in code)** — each task's OWN commit
   file-set (`git show --name-only <sha>`) MUST equal `set(files_per_task[id])`.
   A descendant sha alone does not prove the commit *is* that task's work; this
   stops a builder parking a risky change in a different commit than the one a
   high-risk task names, which would make Step 8a review the wrong object.
3. **Baseline non-regression** — `after.failed <= before.failed` AND
   `after.exit_code == 0`. (The orchestrator's own Step 9 re-run is the real
   gate; this only rejects an obviously-bad receipt early.)
4. **Staging discipline = set EQUALITY** —
   `set(union(files_per_task.values())) == set(git diff --name-only base..HEAD)`.
   Both directions: a task claiming a file not in the diff, OR the diff showing a
   file no task claims → reject. Subset is not enough.

`normalized["promoted_task_ids"]` is surfaced even on rejection, but only when
derivable — `receipt` is a dict and `receipt["promotions"]` is a list of
objects with a string `task_id`. A non-dict/malformed receipt yields an empty
list, so do not assume Step 8a promotion scheduling happens on *every* rejected
receipt. Note also that `task_shas`/`files_per_task` keys outside the plan's
task ids reject the whole receipt (they cannot launder an unplanned file past
the staging-discipline check).

## Fallback contract (reject path)

Delegation can **never** block Step 8. On `ok=False`:

1. **Restore tracked state:** `git reset --hard <branch_base_sha>`.
2. **Remove only the builder's untracked files:** the paths in
   `union(files_per_task.values())` that are still untracked. Do **NOT** run a
   blanket `git clean -fd` against the operator's checkout — the step-2
   clean-worktree pre-gate means the only untracked files present are the
   builder's, but targeted removal keeps a partial/unparseable receipt safe.
3. **If the receipt is unparseable** (can't read `files_per_task`): `git reset
   --hard <branch_base_sha>` only, and WARN that builder untracked files may
   remain — never blind-delete.
4. **Log the fallback loudly** and run the normal per-task Step 8 from a clean
   base.

## Gates after a valid receipt

- **Step 8a** — the ONE accumulated wave (#492) covering every high-risk task's
  `receipt["task_shas"][id]` (tagged in Step 5 OR in
  `normalized["promoted_task_ids"]`) — one review-log entry per covered task. Coverage via
  `plan_lib.assert_review_coverage(<log>, plan_tasks, receipt["task_shas"])`.
- **Step 9** — re-run the full suite from the orchestrator.
- **Steps 11 / 11.5** — unchanged (full diff review + security scan).

## Enablement

Per-project `.rawgentic_workspace.json` entry, sibling to `adversarialReview`:

```json
"wholeIssueDelegation": { "enabled": true, "workflows": ["implement-feature"] }
```

Read via `adversarial_review_lib.is_enabled_for(workspace, project, skill,
key="wholeIssueDelegation")` — the same loader `adversarialReview`/`peerConsult`
use, no lib change. Default absent → disabled → Step 8 unchanged.
