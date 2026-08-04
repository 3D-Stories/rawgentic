## Step 8: Implementation

### Instructions

Execute the implementation plan task by task.

**For each task in the plan:**

1. **If TDD mode** (`capabilities.has_tests == true`):
   - RED: Write failing test(s). Run the SCOPED test command for the area under change (per `<test-run-discipline>`, SKILL.md — never the full suite here) to confirm failure.
   - GREEN: Write minimum code to pass. Run the scoped suite to confirm all pass.
   - REFACTOR: Clean up. Re-run the scoped suite.
   - **Test-output projection (#314, see `### Delegated reads`):** consume the runner's
     own final summary (pass/fail counts + failing test ids + first assertion lines — a
     bounded tail), never `cat` a full run log into context. Verdicts come from exit
     codes; diagnosing a failure is a correctness decision and uses targeted reads of the
     named failing tests, not a summarizing agent. Projection validation applies (empty
     projection on a failing run ⇒ inline).

2. **If Implement-Verify mode** (`capabilities.has_tests == false`):
   - IMPLEMENT: Write the code, config, or infrastructure changes.
   - VERIFY: Run the verification command specified in the plan. Capture output as evidence.
   - If verification fails: debug and fix before proceeding.

3. **Commit:** Create a conventional commit:
   ```bash
   git add <specific_changed_files> && git commit -m "<type>(scope): <description> (#<issue_number>)"
   ```
   Stage ONLY the files modified in this task. Never `git add -A` or `git add .`.

4. **Push regularly:** Push to origin at natural checkpoints (after every 2-3 tasks or every 30 minutes):
   ```bash
   git push origin <branch_name>
   ```

**Early smoke-install (deploy-bearing projects only, #494).** On a project with
`capabilities.has_deploy`, after the first runnable commit of the plan, run the cheap live
smoke-install/boot check per `<early-smoke-install>` (SKILL.md) before continuing to the next
task — a crash-on-boot or environment/port clash found here is a 2-minute fix, not an
hours-later cutover surprise. When `has_deploy == false` the directive does not apply — skip
silently. In whole-issue delegation the branch typically only advances at collect time
(worktree runs — item 4b of that sub-mode) — run the smoke once right after the first
runnable commit lands on the branch, before the receipt gates.

<!-- model-routing: role=implementation -->
**Inline implementation (D174): the plan tasks are written in THIS session.** For each task:
record the pre-task state (HEAD + the `git status --porcelain` dirty baseline) → implement the
task inline, test-first per the loop above → commit, staging ONLY the task's files → assert the
branch advanced with non-empty content (`git diff --name-only <pre-task sha>..HEAD`) → run the
SCOPED suite for the task's area. No dispatch machinery, no seats, no per-task model routing
(`<model-routing-resolve>`).

**Optional worktree subagents for genuinely parallel tasks.** When Step 5 marked a
parallel-eligible group AND `capabilities.parallelism == "worktree"` (Step 2 item 10), the
group's tasks MAY dispatch as Agent-tool worktree subagents (isolation keeps each mutation off
the shared tree; the agents commit in their own worktrees). This is judgment, never obligation
— the default is inline and serial. For each collected subagent task:
1. **Collect** the agent's commit onto the feature branch (cherry-pick or fast-forward the
   reported SHA — the commit lives in the shared object store but NOT on the branch).
2. **Staging backstop:** assert the collected commit's file set (`git show --name-only <sha>`)
   ⊆ the task's declared `files`; STOP to reconcile if not.
3. **Assert the branch advanced with content** (HEAD differs from the pre-task SHA AND the
   diff is non-empty — an empty commit lets every diff-scoped gate pass vacuously, #767), then
   run the SCOPED suite. Apply the vacuous-result gate from `<model-routing-resolve>` to the
   agent's report before trusting any claim in it.
4. **On failure or a vacuous return:** the shared checkout was never touched (the work lives
   in the agent's worktree) — restore nothing, and re-run that task INLINE. A subagent can
   never block Step 8: inline is always the fallback.

<!-- whole-issue-delegation: #133 -->
**Optional WHOLE-ISSUE delegated build sub-mode (`wholeIssueDelegation`, default-off).** Inline implementation above keeps the full per-task ceremony (implement, scoped-suite re-run, diff) in the orchestrator's own loop, so an orchestrator working a backlog bloats fast. When opted in, Step 8 instead hands ONE build-subagent the whole branch and validates a structured **receipt** — the *typing* is delegated, the *gating* is not. **Trust boundary:** the builder never self-certifies; every gate re-runs in the orchestrator against the real tree, and a receipt claim is a hypothesis until confirmed. The build-subagent is an Agent-tool WORKTREE subagent — it authors the per-task commits the receipt names (`task_shas`) in its own worktree, off the shared tree. Read `references/whole-issue-delegation.md` in full before using this mode — it holds the build-subagent brief template, the receipt schema, and the validation/fallback contract; the block below is only the spine.

1b. **Enablement gate.** Only when enabled for this skill:
   ```bash
   python3 hooks/adversarial_review_lib.py is-enabled \
     --workspace .rawgentic_workspace.json --project <name> --skill implement-feature \
     --key wholeIssueDelegation
   ```
   Exit `0` → enabled; non-zero → **skip silently** and run Step 8 exactly as above (per-task or inline). An explicit invocation opt-out also skips.
2. **Pre-flight clean-worktree gate (data-loss guard).** Require `git status --porcelain` empty. If the operator has ANY staged/unstaged/untracked work, do NOT enter this mode — log it and fall back to the normal per-task Step 8. The reject path must be able to discard the builder's output without touching pre-existing operator files.
3. **Record** `branch_base_sha` (`git rev-parse HEAD`) and the pre-build test baseline (`{before:{passed,failed}}`).
4. **Dispatch ONE build-subagent** — an Agent-tool worktree subagent — with the brief (design, plan w/ riskLevels, TDD requirement, conventions, baseline) + the required receipt schema (both in the reference file). **Never dispatch it on Haiku.**
4b. **Collect BEFORE validating (worktree runs).** When the build ran worktree-isolated, the receipt's commits exist in the shared object store but the feature branch has NOT advanced — and receipt Rule 4 diffs `base..HEAD` on THIS checkout, so validating first would reject every worktree build (empty diff vs declared files). Land the build on the branch first: fast-forward to the receipt's final sha (or cherry-pick `task_shas` in plan order), then assert the branch actually advanced past `branch_base_sha`. Only then validate. (Non-isolated fallback dispatches leave HEAD already advanced — this step is then a no-op assert.)
5. **Validate the receipt** against the real tree:
   ```bash
   python3 -c "import sys,json; sys.path.insert(0,'hooks'); from plan_lib import validate_build_receipt, parse_tasks; r=json.load(open('<receipt.json>')); tasks=parse_tasks(open('<plan.md>').read()); ok,errs,norm=validate_build_receipt(r,tasks,'.','<branch_base_sha>'); print(ok); print('\n'.join(errs)); print(json.dumps(norm))"
   ```
   **Reject (ok=False)** → **restore then fall back** (delegation can never block Step 8): `git reset --hard <branch_base_sha>` (tracked) then remove ONLY the untracked paths the receipt declared (`union(files_per_task)` filtered to still-untracked); on an unparseable/partial receipt, reset only and WARN that builder untracked files may remain — **never** blanket `git clean -fd` against the operator's checkout. Log the fallback loudly, then run the normal per-task Step 8.
6. **On a valid receipt, run the gates in the orchestrator against the real tree** (not the receipt's word for them):
   - **Step 8a** as the ONE accumulated wave (#492) covering every high-risk task's receipt sha (tagged in Step 5 **OR** in `norm["promoted_task_ids"]`); coverage recorded per covered task via the Step 8a session-note markers. **8a is NOT delegated** — the orchestrator owns it.
   - **Step 9** re-run the full suite from the orchestrator (the receipt baseline is a claim; the orchestrator's own run is the gate).
   - **Steps 11 / 11.5** unchanged (full diff review + scan).
7. **Marker** (session notes): `### WF2 Step 8 whole-issue-delegation (#<issue>): <APPLIED receipt-valid | FALLBACK per-task (<reason>) | SKIPPED not-enabled>`.

Interplay with `<small-standard-lane>`: whole-issue delegation is still allowed in the lane — the collapsed gates still run in the orchestrator; the receipt's Step-8a set is just usually empty.

**Parallel task execution:** Use Step 5's parallel-eligibility judgment (declared, pairwise-disjoint `files`) to know which groups *could* run concurrently, and the `capabilities.parallelism` probe from Step 2 item 10 (#136) to know whether the environment *can* isolate them. When `parallelism == "serial-only"`, state it once ("worktree isolation unavailable → serial execution") and run everything inline; when `parallelism == "worktree"`, a parallel-eligible group MAY dispatch as Agent-tool worktree subagents per the optional-subagent contract above. **The default is serial, inline, in plan order** — parallelism is judgment for genuinely-independent tasks, never obligation. **Staging backstop:** the "stage ONLY this task's files, never `git add -A`" rule above applies to every task; when a task additionally declares `files`, that rule becomes machine-checkable — assert the staged set is a subset of the declared `files` and STOP to reconcile if not. (A task in a parallel_group that declared no `files` is already non-eligible and runs sequentially under the same stage-only-this-task's-files rule.)

**Mid-flight risk promotion (P15):** After implementing each task and staging its diff, re-evaluate the task against the 8 risk criteria via two paths:

1. **Mechanical** — call `plan_lib.should_promote(task_id, file_paths, loc_delta)`. It returns `(True, reason)` if any file path matches the high-risk regex allowlist OR `loc_delta >= 200`.
2. **Agent-flagged** — if your implementation work surfaced subjective criteria (e.g., the new error path is non-trivial in a way the path-allowlist couldn't catch), emit a `PROMOTE: <task_id> <reason>` directive in session notes.

Either trigger ADDS the just-committed commit to the accumulated Step-8a set (#492) AND triggers a **retroactive scan** of all prior commits in this branch via `plan_lib.scan_prior_commits_for_trigger(repo, since_sha=<branch_base>, exclude_sha=<current_sha>)`. Any prior SHAs returned by the scan join the accumulated set; the single Step-8a wave reviews the whole set **before Step 9**. Log the promotion using `plan_lib.format_promotion_note(task_id, criterion, rationale, issue=<issue>)`.

Promotion at the last task still lands in the accumulated set — the wave (and any retroactive scan) completes before Step 9.

**Mid-flight feasibility check (#226 AC6).** If, while implementing (or iterating on a fix during
UAT), you introduce a **new** platform/framework/external API that the Step-3 `platform_apis:`
declaration did NOT cover — a change that bypasses the design gate — apply the lightweight
feasibility check inline **before committing** that task: prove the API works under this
project's real config (an exact-object-kind precedent or a quick spike, not the API's mere
existence), classify `fail-loud`/`fail-silent`, and add a `surface:` assertion/log if silent.
Record a `platform_apis:` block for it in session notes and fold it into the design doc so
Step 9 and the run-record stay honest. This is AC1/AC3/AC4 in miniature for the exact place the
original failure lived — a mid-UAT fix that never went back through Step 3/4.

**Debugging:** If stuck after 3 manual fix attempts, escalate to systematic debugging.

**Design flaw discovery:** If implementation reveals a fundamental design flaw:
- Check: `tdd_loopback_used == false` AND `global_loopback_total < GLOBAL_LOOPBACK_BUDGET`
- If allowed: **park the invalidated work first** — a design-level loop-back INVALIDATES the
  current uncommitted diff, so stash it recoverably before returning to Step 3:
  `git stash push -u -m "rawgentic-parked:<issue>:<design-version>:<task-id>"`, verified by a
  `refs/stash` OID change (never a bare `rc == 0`; an empty tree stashes nothing — that is
  fine, note it). Log the stash name + OID in session notes so the parked diff stays
  discoverable. Then consume the loop-back (`plan_lib.consume_loopback(<counters>, "tdd")`)
  and return to Step 3 with the flaw identified.
- If budget exhausted: STOP and escalate to user.

**Session checkpoint (APPEND, every 2-3 tasks).** After each batch of 2-3 tasks, APPEND a
**lightweight progress checkpoint** to session notes — this is separate from and lighter
than a full suspend/error checkpoint:

```
#### Progress — Tasks N-M complete
- Files: [list]
- Commits: [count]
- Key decisions: [if any]
```

APPEND it under the Step 8 section as you go (the Step 8 `— DONE` marker is APPENDed last);
never overwrite an earlier entry, so the audit trail stays cumulative.

---

### Step 8a sub-step: Per-task Review (P15)

**Fires when:** any plan task has `riskLevel: high` (tagged in Step 5 OR promoted mid-flight in Step 8). Since #492 the review runs as ONE review wave over the accumulated high-risk commits — never a wave per task-batch: high-risk commits accumulate as tasks complete, and after the LAST plan task's commit (including any mid-flight promotions and retroactive-scan hits), BEFORE Step 9, dispatch a single 2-reviewer wave over the whole set. Coverage stays per task — every high-risk commit is in the reviewed range and the log records one entry per task — so `plan_lib.assert_review_coverage` is unchanged. Blocking point: Critical/High findings are fixed BEFORE Step 9 (was: before the next task) — the deferred barrier is the #492 trade, bought back by the wave seeing cross-task interactions the per-task waves never saw.

1. **Capture each accumulated commit's diff** (concatenated, one section per high-risk sha):
   ```bash
   git show --no-color --format= <sha>   # per accumulated high-risk sha
   ```
   A section shows the change as committed, which later low-risk commits may have since
   modified — reviewers judge each hunk against the CURRENT tree (HEAD is checked out in
   the repo they read), and Step 11's full `origin/<default>..HEAD` diff reviews the final
   state of everything regardless.
<!-- model-routing: role=review -->
Run the wave per the `<model-routing-resolve>` contract: the cross-model pass dispatches
`python3 hooks/review_runner.py review-code --base <branch_base_sha> --brief <brief.md> --author-model <your model id> --reviewer <default per the contract> [--reopen-token <token.json>] --out <result.json> --project-root .`
from a read-only harness subagent, IN PARALLEL with your own INLINE self-review of the same
concatenated high-risk diff (two independent passes, never merged). **The diff is a REQUIRED
input: the runner composes it itself from `--base`, and the brief names the accumulated
high-risk shas — the artifact-delivery guarantee that `--requires-context` used to provide
(#826) is now structural: a route that cannot carry the bytes cannot be called.** Mint the
reopen token FIRST: `python3 hooks/plan_lib.py review-reopen --state-file claude_docs/.wf2-state/<issue>/loopback_counters.json --source review_design --out <token.json> --project-root .`
— the mint debits the loop-back budget; on exhaustion (exit 3) dispatch TOKENLESS and note the
round is diagnostic (a design-flaw finding then escalates instead of looping — item 6). Every
reviewer brief MUST restate the read-only execution clause (#510): Bash is for read-heavy inspection only — never execute the target project's entry-point scripts, deploy paths, or anything that mutates state or sends outward; the only sanctioned executions are the verification commands this brief names (from the project's `.rawgentic.json` testing config); an entry script invoked in an unexpected form may fall through to a live path — do not experiment with invocation forms; when a command's read-only-ness is uncertain, don't run it — report the uncertainty as part of the review.

2. **Run the 2 review passes in parallel** — Reviewer 1 is your INLINE self-review; Reviewer 2 is the runner-dispatched cross-model review (the command above). Per `<review-lens-routing>` (SKILL.md): Reviewer 1 carries the `mechanical` lens, Reviewer 2 the `security` lens (the strong pass — never the one dropped):
   - **Reviewer 1: Code-level (style + bug/logic)** — naming, imports, hardcoded credentials, off-by-one errors, null/undefined handling, race conditions, type errors. Scope: every accumulated high-risk section in the concatenated diff.
   - **Reviewer 2: Silent-failure hunt** — catch-block swallows, missing error returns, unchecked async paths, ignored exceptions, fallthrough cases, missing `else` branches that should reject. Scope: every accumulated high-risk section in the concatenated diff.

   Each reviewer's return MUST carry a per-sha acknowledgment line — `reviewed <sha>: <one-line verdict>` for every accumulated sha — inclusion in the wave's input never counts as review by itself (#492).

   While the two reviewers run, pipeline per `<review-pipelining>` (SKILL.md): draft the PR body or version/changelog edits (non-committing — the accumulated wave runs after the LAST task, so there is no next task's tests to draft, #492); triage (item 4) still waits for both returns.
3. **Filter findings using the `SEVERITY_BANDED_CONFIDENCE` thresholds** (values in `<constants>`; canonical in `plan_lib.SEVERITY_BANDED_CONFIDENCE`). Count dropped findings.
4. **Triage:**
   - **Critical:** must fix before Step 9 (block).
   - **High:** fix before Step 9 unless deferred-with-rationale. Persist the deferral via `plan_lib.append_deferral(<deferrals_path>, finding)` (the `finding` needs at least `finding_id`, `severity`, `originator_reviewer_slot`) — it **must be re-presented to Step 11** for resolution.
   - **Medium/Low:** advisory; log to review log only.
5. **Ambiguity circuit breaker:** if any finding is ambiguous or two findings conflict, STOP and ask user.
6. **Design flaw detection:** a successful pre-dispatch mint (`--source review_design`) authorizes ONE gate-wide fix round for a design-level flaw found by EITHER pass — inline or runner (the debit already happened at mint; never consume a second time). Consult the result's `diagnostic` field only when validating the runner receipt. On an authorized round: park the invalidated uncommitted work per Step 8's design-flaw-discovery stash recipe, then return to Step 3. On a refused mint (budget exhausted): NEITHER pass may open the round — the disposition step MUST refuse — STOP and escalate.
7. **Dispatch failure fallback:** if the runner dispatch fails (exit 3/4, a dead subagent), retry once; on a second failure record `REVIEW_DISPATCH_FAILED` for that slot in session notes and follow the ERROR protocol — the wave never proceeds with fewer than two live passes. **Dead-return detection:** a reviewer return that is vacuous (no findings AND no substantive content) is a DEAD dispatch, not a clean pass — relaunch that pass once; on a second death treat it as a dispatch failure (this item's REVIEW_DISPATCH_FAILED path).
8. **Record coverage** — ONE session-note marker per high-risk task the wave covered (item 10's marker shape, with the task's verdict `applied|deferred|REVIEW_DISPATCH_FAILED` in the detail), written ONLY when both passes acknowledged that task's sha (item 2); an unacknowledged sha is UNCOVERED and re-presents to the wave before Step 9 (same passes — this is what keeps coverage honest under #492). **Then APPEND the entry Step 9's coverage gate actually reads (#880 — the marker alone is NOT the log), one per covered task, via the `append_review_log` writer's CLI:** `python3 hooks/plan_lib.py append-review-log --log "claude_docs/.wf2-state/<issue>/review_log.jsonl" --task-id "<id>" --sha "<sha>" --reviewers "inline-mechanical,runner-security" --verdict "<verdict>" --findings "<crit>,<high>,<med>,<low>,<dropped>" --project-root .` — `<verdict>` is exactly one of `applied`, `deferred`, or `REVIEW_DISPATCH_FAILED`; entry shape `{task_id, sha, reviewers, verdict, findings:{crit,high,med,low,dropped}, ts}` (`ts` auto-added; exit 2 = validation refused, nothing written). Commit only the actual fix files; never stage bookkeeping.
10. **Log per-task marker in session notes:** `### WF2 Step 8a [task <id>, sha <abc>]: DONE (#<issue>: <summary>)`.
11. **Suspend protection (unattended runs):** when Step 8a suspends (any QUESTION/ERROR path), convert the PR to draft if one exists (`gh pr ready --undo`). On fork PRs or no-perm sessions, post a blocking review comment instead.

### Output
For each high-risk task: a session-note coverage marker carrying its verdict (applied|deferred|REVIEW_DISPATCH_FAILED), optional fix commits. The branch is not "ready" until every covered task's verdict is `applied` or a persisted deferral.

### Step 8a Failure Modes
- Flat 2-reviewer cost regardless of high-risk-task count (#492's single wave); the blocking signal is deferred to before Step 9 — the accepted trade, bought back by cross-task-interaction visibility. A very large accumulated set may warrant splitting the wave (orchestrator judgment; the per-sha acknowledgment in item 2 catches an under-inspected tail).
- A Step 8a-deferred High finding is never re-presented at Step 11: this is what `plan_lib.assert_no_unresolved_high_deferrals` defends against in Step 11's exit check.

---

### Step 8 Failure Modes (main task loop)

These apply to the main Step 8 implementation loop above (Step 8a, the per-task review sub-step, has its own failure modes listed under it).

- Verification fails and cannot be fixed -> flag blocker to user
- Design flaw discovered -> loop back to Step 3 if budget allows
- For TDD: test passes before implementation (test not testing right thing) -> rewrite test

---

