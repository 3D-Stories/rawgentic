# WF3 per-step detail

Reading contract: read the matching section before executing that step.
The spine (SKILL.md) carries the one-line-per-step overview and the always-run
protocols; this file carries the full per-step instructions plus the
step-semantic blocks they depend on (`<trivial-work-check>`, `<learning-config>`).

---

<trivial-work-check>
The low-end mirror of `<complexity-override>`. Some bug fixes are **trivial** — a typo,
a one-line off-by-one, a comment, a config/constant tweak — where even WF3's 14 steps
are more ceremony than the fix warrants. This surfaces that BEFORE the workflow invests
in root-cause analysis and review.

**Trigger (evaluated in Step 2, after complexity classification):** set
`trivial_work = true` only when the fix is ~1 file (occasionally 2), roughly ≤ 10 changed
lines, mechanical, with no new logic and low reversal cost. (Reproduce-first still applies
*in spirit* — a one-line regression test is cheap and worth it — but the full step
machinery is not.)

This is a **suggestion, never a hard gate** — the orchestrator must NOT bail on its own,
and continuing the full workflow is always a valid choice.

**When `trivial_work == true` (interactive):** STOP and present, concisely:
```
Step 2 → TRIVIAL bug detected (<N files, ~M lines, <one-line why>).
The full WF3 (14 steps) is likely overkill. Proceed how?
  (a) Do it directly — reproduce test + minimal fix + branch + PR  [recommended]
  (b) Continue the full WF3 workflow
```
Wait for the choice.
- **(a) Do it directly:** LEAVE the workflow. Still write the failing reproduction test
  first (it is cheap and reproduce-first is the heart of WF3), apply the minimal fix, run
  the suite, bump the version + update docs per the project's pre-PR checklist, open a PR
  — but SKIP the reflect gate (Step 4), the code-review step, and the run-record ceremony.
  If you do emit a run-record, set `complexity: "trivial"`.
- **(b) Continue:** proceed to Step 3 (Root Cause Analysis) as normal.

**[Headless: AUTO-RESOLVE — continue the full workflow; log `### WF3 Step 2 —
trivial-work suggestion (auto-continued in headless)`.]**
</trivial-work-check>

<learning-config>
If this workflow discovers new project capabilities during execution (e.g., a new test framework, a previously unknown service), update `.rawgentic.json` before completing:
- Append to arrays (e.g., add new test framework to testing.frameworks[])
- Set fields that are currently null or missing
- Do NOT overwrite existing non-null values without asking the user
- Always read full file, modify in memory, write full file back
</learning-config>

---

## Step 1: Receive Bug Report Reference

### Instructions

1. **Execute `<config-loading>`** to load the project configuration and build the `capabilities` object. Then execute `<environment-setup>`** to populate PROJECT_ROOT. Log resolved config values in session notes. If config loading fails, STOP and tell the user which step failed.
2. Parse the argument as a GitHub issue number or URL.
3. Fetch the issue: `gh issue view <number> --repo capabilities.repo`
4. Confirm the issue is open and labeled as bug (or has bug report template format).
5. **Detect issue format:** Check the issue's labels for `security`. If the `security` label is present, the issue likely uses STRIDE format (from WF9) instead of the standard bug report template. Adapt field mapping:
   - STRIDE "Description" / "Affected Code" → treat as "Steps to Reproduce" (the vulnerable code path)
   - STRIDE "Risk" / "Impact" → treat as "Expected vs Actual" (expected: blocked/mitigated, actual: exploitable)
   - STRIDE "Recommended Remediation" → treat as acceptance criteria for the fix
   - If the issue has the `security` label but no recognizable STRIDE fields, fall back to standard parsing and ask the user to clarify.
6. Display to the user: title, steps to reproduce (or vulnerability path), expected vs actual behavior (or risk assessment), environment.
7. Ask user to confirm this is the correct bug to fix. **[Headless: AUTO-RESOLVE for WF1-created issues. QUESTION for manual issues — post summary for confirmation, suspend.]**
8. If the issue lacks reproduction steps or expected behavior (and is not a security finding with STRIDE fields), ask user to provide them before proceeding. **[Headless: QUESTION — post comment requesting reproduction steps, suspend.]**
9. **Memory search for bug history (Layer 3 — proactive recall).** If a mempalace MCP server is available (`mcp__mempalace__*` tools loaded), call `mempalace_search` with the symptom and any error messages from the issue. Past similar bugs often have documented root causes and fixes. Surface any matches explicitly before moving to Step 2. If no mempalace MCP server is configured, skip silently.

### Output Format

Present to user:

```
Bug Report: #<number>
Title: <title>
Status: <open/closed>
Format: [standard bug report | security finding (STRIDE)]

Steps to Reproduce / Vulnerability Path:
<from issue>

Expected: <from issue or "blocked/mitigated">
Actual: <from issue or "exploitable">
Environment: <from issue>

Suggested goal guard — run this so the session can't quit before the fix lands:
/goal <plan_lib.build_goal_text(issue_number, [], variant="wf3") output>

Confirm this is the bug to fix, or provide corrections. Run the /goal command above
(or say "skip goal" to decline — declining is fine and never blocks).
```

Wait for user confirmation before proceeding to Step 2 (and, in the same round-trip,
whether they ran `/goal` or declined — see Step 1b; no second prompt). **[Headless: AUTO-RESOLVE for WF1-created issues. QUESTION for manual issues — post summary, suspend.]**

### Failure Modes

- Issue not found → ask for correct number
- Issue is not a bug → suggest WF2 (`/implement-feature`) instead
- Missing reproduction steps (and not a security finding with STRIDE fields) → ask user to provide them before proceeding. **[Headless: QUESTION — post comment requesting details, suspend.]**

---

## Step 1b: AC-Derived Goal Guard (/goal)

### Instructions

This is an optional guard, not a gate — it never blocks the workflow.

1. **Why.** A skill cannot set a session goal itself; `/goal` is a session command
   (see code.claude.com/docs/en/goal.md), not something a skill body can invoke. So
   this step CONSTRUCTS the goal text and the user (or the headless driver) is the
   one who runs it, giving the session's Stop-hook a concrete condition so the
   workflow can't be silently abandoned before the fix lands.

2. **Build the text.** Call `plan_lib.build_goal_text(<issue_number>, [], variant="wf3")`
   — the pure, tested helper. WF3 has no numbered ACs, so `ac_lines` is ignored
   entirely; the text instead names: repro documented, regression test red→green,
   PR open with green CI (never "merged" — merge is owner-gated and happens after
   the workflow ends).

3. **Fold into Step 1's confirmation — no second prompt.** The built text is shown
   inside Step 1's existing confirmation block; the user's single confirmation
   covers both the bug-report check and the `/goal` invocation (run it, or decline).
   Declining is always a valid answer and never blocks progress (`goal_guard: skipped`).

4. **Record the marker** (read by the run-record `goal_guard` field):
   ```
   ### WF3 Step 1b — Goal guard (set|skipped): <first 80 chars of text | decline reason>
   ```

**[Headless: AUTO-RESOLVE — for WF1-created issues, emit the built goal text
verbatim into the headless checkpoint for the driver to set via
`claude -p "/goal …"` (session 1 cannot self-set it — the goal text needs the
fetched issue body, though the driver MAY pre-derive it at launch); for
unlabeled/manual issues, skip the guard and log the marker with (skipped).]**

### Failure Modes
- User neither runs `/goal` nor says "skip" -> treat silence as decline, log `(skipped)`, proceed

---

## Step 2: Analyze Bug Context and Classify

### Instructions

1. **Reproduce path tracing:** Starting from the reported symptoms (error messages, unexpected behavior), trace the code path to the bug location. For simple traces (1-3 files, clear call chain), Grep and Read are sufficient and faster. Use Serena MCP (`find_symbol`, `find_referencing_symbols`) for complex call chains involving multiple services or deep symbol resolution where grep alone would miss indirect references.
2. **Blast radius assessment:** Identify all files and functions in the call chain from entry point to bug location.
3. **Test inventory:** Find existing tests covering the affected code paths.
4. **Complexity classification:**
   - `simple_bug`: 1-3 files, clear root cause, no migration needed
   - `moderate_bug`: 4-10 files, root cause requires investigation, may need migration
   - `complex_bug`: 10+ files, cross-service, unclear root cause → **prompt upgrade to WF2**
5. **Related issues check:** `gh issue list --repo capabilities.repo --search "<keywords>" --limit 10`
6. **Trivial-work check (may surface to user):** Apply `<trivial-work-check>`. If the fix
   is `trivial_work == true`, present the "do it directly vs. continue the full workflow"
   suggestion and WAIT for the user's choice before proceeding to Step 3 (headless:
   auto-continue).

### Output

Bug analysis (internal working artifact):

- Affected files and call chain
- Existing test coverage and gaps
- Complexity classification
- Related issues
- Suspected root cause

### Failure Modes

- Cannot reproduce from description → ask user for more details. **[Headless: QUESTION — post comment with reproduction attempt details, suspend.]**
- Bug is in a dependency, not our code → document and suggest upstream report
- Classified as `complex_bug` → prompt upgrade to WF2 (user can override)
- Classified as `trivial_work` → suggest doing it directly (user can continue WF3); see `<trivial-work-check>`

---

## Step 3: Root Cause Analysis

### Instructions

1. **Hypothesis generation:** Based on the code trace from Step 2, generate 1-3 hypotheses for the root cause.
2. **Evidence collection:** For each hypothesis, gather evidence from code, logs, and test behavior.
3. **Root cause determination:** Select the hypothesis with strongest evidence.
4. **Fix approach:** Design the minimal fix that addresses the root cause (not symptoms).
5. **Regression risk assessment:** Identify code paths that could break from the fix.

### Output

RCA document (internal working artifact):

- Root cause with evidence
- Fix approach (minimal change)
- Files to modify
- Regression risks

### Failure Modes

- Multiple equally likely root causes → present to user for guidance
- Root cause is in a design flaw (not a code bug) → suggest WF2 for redesign
- Fix would be a band-aid → flag that proper fix may need WF2

---

## Step 4: Quality Gate — Lightweight Reflect

### Instructions

Apply the quality-bar rubric (`references/quality-bar.md`) with focus on root cause correctness. Single-pass self-review checking:

1. Does the identified root cause actually explain ALL symptoms in the bug report?
2. Is the fix in the right layer (not a band-aid when the real issue is upstream)?
3. Are there unintended side effects of the proposed fix?
4. Does the fix handle edge cases mentioned in the bug report?
5. Is the fix backward-compatible (especially for API/DB changes)?

**Critique level:** Lightweight reflect ONLY. RATIONALE: Bug fixes have lower reversal cost than new features. A full 3-judge critique adds 2-3 minutes of latency for diminishing returns on small-scope changes.

**Adversarial review sub-step (opt-in, DEFAULT-OFF, cross-model).** WF3 is deliberately lightweight; an external cross-model review is therefore **off by default** and must be explicitly opted in per project. After the lightweight reflect above, check:
```bash
python3 hooks/adversarial_review_lib.py is-enabled \
  --workspace .rawgentic_workspace.json --project <name> --skill fix-bug
```
- The command exits `0` when enabled (`fix-bug` listed in the project's `adversarialReview.workflows`) and non-zero otherwise. If **disabled** (the default), **skip silently**. The fast path is preserved exactly; this adds zero overhead to a normal bug fix.
- If **enabled** (the user knowingly accepts the latency tradeoff — an external review adds ~1-3 min on top of the 2-3 min reflect, the very cost this step was designed to avoid), write the RCA + fix approach to a temp file under the project and invoke `/rawgentic:adversarial-review <rca-path> plan`. It is report-only; merge its findings (tagged `source: adversarial`) with the reflect findings and apply the circuit breaker over the **merged** list. If a Critical/High indicates the root cause itself is wrong, loop back to Step 3 **once** (max 1 per loop-back budget, same as the reflect loop-back — it does NOT add a second budget). **Codex failure is non-blocking** (additive review): on ANY non-success — including headless unmet-prerequisite — skip the adversarial layer, log loudly (headless: STATUS comment), and continue with the reflect result. Do NOT trigger the ERROR protocol and do NOT block WF3 (only the standalone `/rawgentic:adversarial-review` skill ERRORs on an unmet prerequisite). Log: `### WF3 Step 4 — Adversarial Review (invoked|skipped): <report path or skip reason>`.

Note: the `is-enabled` check reads `.rawgentic_workspace.json`; if that file is missing or corrupt the engine returns disabled (fail-safe), so WF3 continues unchanged.

### Output

Amended RCA (findings applied) OR blocked state (circuit breaker triggered).

### Failure Modes

- Reflect finds the root cause is wrong → loop back to Step 3 (max 1 time per loop-back budget)
- Fix has significant side effects → suggest WF2 for broader approach

---

## Step 5: Create Fix Plan

### Instructions

1. Break the fix into ordered TDD tasks:
   - Task 1: Write failing reproduction test
   - Task 2: Implement the fix (minimal change)
   - Task 3: Add regression/edge case tests
   - Task 4: Update documentation if behavior changes
2. Document the fix branch name: `fix/<issue-number>-<short-desc>`
3. Estimate: most bugs should have 3-6 tasks.

### Output

Fix plan with ordered tasks, file paths, and test expectations.

### Failure Modes

- Plan reveals fix is larger than expected → suggest upgrading to WF2

---

## Step 6: Create Fix Branch

### Instructions

1. Ensure the default branch is up to date:
   ```bash
   git fetch origin capabilities.default_branch
   ```
2. Create branch from the default branch:
   ```bash
   git checkout -b fix/<issue-number>-<short-desc> origin/capabilities.default_branch
   ```
3. Verify branch created successfully.
4. **Pre-flight dependency check:** If the project's `config.techStack` includes npm/yarn/pnpm-based technologies (node, react, vue, angular, etc.) or a `package.json` exists in the project root, verify `node_modules` exists. If missing, run the appropriate install command (`npm install`, `yarn install`, or `pnpm install`) before proceeding to Step 7. Similarly, for Python projects with a `requirements.txt` or `pyproject.toml`, verify the virtual environment is active or dependencies are installed. This prevents test failures due to missing dependencies rather than actual bugs.

### Output

Active fix branch with dependencies installed.

### Failure Modes

- Working directory is dirty → stash changes first (`git stash`), create branch, then ask user if stash should be applied. **[Headless: AUTO-RESOLVE — always stash, post brief issue comment with stash ref.]**
- Branch name already exists → ask user if they want to resume (checkout existing branch) or start fresh (delete and recreate). **[Headless: AUTO-RESOLVE — always resume existing branch.]**
- Push fails (network) → continue locally, push will be retried by P4 remote sync

---

## Step 7: TDD Bug Fix (Reproduce-First Pattern)

### Instructions

Execute the plan from Step 5 using strict reproduce-first TDD:

1. **RED — Reproduction test:** Write a test that captures the exact bug behavior. Run it — it MUST fail in a way that demonstrates the bug exists. In mocked environments, the specific status code or error message may differ from production — the key proof is that the broken behavior (missing validation, unguarded code path, incorrect logic) is demonstrated, not that the exact production symptom is reproduced. If the test passes, the bug may already be fixed or the test doesn't capture the right behavior. Investigate before proceeding.
2. **GREEN — Minimal fix:** Make the reproduction test pass with the smallest possible code change. Resist the urge to refactor surrounding code.
3. **REFACTOR (minimal):** Only refactor if the fix introduced obvious code smells. Bug fix PRs should be focused, not cleanup opportunities.
4. **Regression tests:** Add 2-3 edge case tests around the fix boundary.
5. **Full suite:** Run test commands from `capabilities.test_commands` to confirm no regressions. Iterate over all configured test frameworks.
6. **Commit frequently:** Follow P3 (every 5 min active work) and P12 (conventional commits): `fix(scope): brief description`

### Test Commands

Test commands are derived from `capabilities.test_commands` (loaded from `config.testing.frameworks[].command`). If `capabilities.has_docker`, run tests via the compose files from `config.infrastructure.docker.composeFiles[]`. If tests are configured to run on remote hosts, use `config.infrastructure.hosts[]` to determine connection details.

Do not hardcode test runners or compose file names — always derive from config.

### Output

Fixed code with passing tests on fix branch.

### Failure Modes

- Reproduction test passes immediately → bug may not be reproducible in current code. Ask user to verify. **[Headless: QUESTION — post comment explaining bug may already be fixed, suspend.]**
- Fix breaks other tests → investigate shared state or wrong approach
- Fix requires changes beyond plan scope → flag and decide: expand plan or split into multiple fixes

---

## Step 8: Lightweight Verification

### Instructions

Quick self-check (no sub-agent needed):

1. Verify all acceptance criteria from the bug report (or all risk mitigations from the security finding) are addressed.
2. Verify the reproduction test genuinely captures the original bug.
3. Verify no unrelated changes crept in: `git diff --stat` should show only planned files.
4. Verify all tests pass.

### Output

Verification pass/fail.

### Failure Modes

- Unrelated changes detected → `git checkout -- <file>` to revert strays
- Missing acceptance criteria → add tests/code for missed items

---

## Step 9: Code Review + Conditional Memorize

### Instructions

**Part A: Code Review**

<!-- model-routing: role=review -->
Dispatch these 2 review agents with `model: <review>` unless routing resolved `inherit`; when the resolved `review` effort is non-`none`, apply the dual-path effort rule from `<model-routing-resolve>` (pass it only where the dispatch layer supports effort; always log it).

Launch a focused 2-agent code review in parallel using Agent tool calls (subagent_type per the PR review toolkit):

1. `pr-review-toolkit:silent-failure-hunter` — silent failure detection (critical for bug fixes — ensure the fix doesn't suppress errors)
2. `pr-review-toolkit:code-reviewer` — project standards compliance + general review

For bug fixes, focus reviewers on: (a) is the fix correct and complete, (b) are there any new silent failures, (c) is the code simple and focused. Type design and code simplification are deferred — bug fixes should be minimal and targeted.

Apply findings automatically. Circuit breaker on ambiguity.

**Part B: Conditional Memorize**

If the bug fix reveals a pattern worth remembering (new pitfall, gotcha, or recurring issue), curate it into memory: if a mempalace MCP server is available (`mcp__mempalace__*` tools loaded), store it via `mempalace_kg_add` (a fact/decision) or `mempalace_add_drawer` (a note), scoped to this project; otherwise append it to the project `CLAUDE.md` / `MEMORY.md`. Skip if the fix is routine.

Memorize triggers:

- New database gotcha discovered
- Race condition pattern identified
- Security vulnerability pattern
- Environment-specific behavior surprise
- Recurring bug class (third instance of similar bug)

### Output

Review-clean code + optional project knowledge updates.

### Failure Modes

- Review finds fundamental flaw → loop back to Step 3 (max 1 time per loop-back budget)
- Review agents hit rate limit → log partial results, resume after reset

---

## Step 10: Create Pull Request

### Instructions

1. Stage all changes: `git add <specific files>` (never `git add -A`)

1b. **HTML design artifact — create-or-update BEFORE the PR (opt-in, #174).**
   Config-gated — skip silently unless the project opts in
   (`is_enabled_for(..., 'fix-bug', key='designArtifact')`; exit 0 = enabled).
   **Target doc — shared vs per-issue:** read the `designArtifact.sharedDoc` config via
   `design_artifact_shared_doc('.rawgentic_workspace.json', '<name>')`. A returned path →
   **shared-doc mode** (update THAT single rolling doc — the multi-issue / campaign model,
   one program doc updated per slot like this repo's modernization dashboard — refresh
   this issue's section, no per-issue file). None → **per-issue** default
   `docs/planning/<issue>-<slug>.{md,html}`. Either way create or update the `.md`+`.html`
   and commit BOTH inside THIS fix PR (one PR per issue). Render with the shared helper — never hand-roll HTML — embedding
   this run's **telemetry** read from the run-record structure (the Step 14
   run-record; gates, tests + suite delta, security-scan, lane, `usage`), never
   hand-retyped:
   ```bash
   python3 hooks/render_artifact.py --md docs/planning/<issue>-<slug>.md \
     --out docs/planning/<issue>-<slug>.html --title "#<issue> <title>" \
     --telemetry /tmp/wf3-run-record.json
   git add docs/planning/<issue>-<slug>.md docs/planning/<issue>-<slug>.html
   ```
   Fields not knowable pre-PR (PR #, CI, merge SHA) fill on the next slot's pass.
   Log `### WF3 Step 10 — design artifact (updated|skipped)`.

2. Create final commit with conventional format:
   ```bash
   git commit -m "fix(scope): description (closes #<issue>)"
   ```
3. Push branch:
   ```bash
   git push -u origin fix/<issue-number>-<short-desc>
   ```
4. Create PR:

   ```bash
   gh pr create --repo capabilities.repo \
     --title "fix(scope): description" \
     --body "$(cat <<'EOF'
   ## Summary
   - Fixes #<issue-number>
   - Root cause: [brief RCA]
   - Fix: [brief description of fix]

   ## Test plan
   - [ ] Reproduction test passes (was failing before fix)
   - [ ] Regression tests added
   - [ ] Full test suite passes
   - [ ] CI passes

   Generated with [Claude Code](https://claude.com/claude-code) using WF3
   EOF
   )" \
     --label "bug"
   ```

### Output

PR URL.

### Failure Modes

- Tests fail (Gate 1 blocks PR creation) → fix and retry
- Push fails → retry after 5 seconds; if persistent, save PR body for manual creation
- gh auth failure → verify PAT with `gh auth status`
- Branch has conflicts with default branch → rebase (`git pull --rebase origin capabilities.default_branch`), resolve conflicts, re-push

---

## Step 11: CI Verification

### Instructions

**If `capabilities.ci_quarantined == true` (#137):** CI is human-declared untrustworthy — observe the run but treat it as a **visible non-gate**: record `CI quarantined (<capabilities.ci_quarantine_reason>): run <status>, not gating` in session notes + the PR body, never block, never claim green, proceed to Step 12 regardless of conclusion. **Trust guard:** first confirm the quarantine comes from the trusted base config — `capabilities_lib.ci_quarantine_change(base_config, head_config)` (base from `git show origin/<default>:<config-path>`) must return None; if the branch introduced/altered the quarantine, CI GATES normally for this run (a PR cannot disable its own CI gate) and the change is surfaced for approval. Quarantine is read from config only, never entered/lifted by the workflow.

1. Wait for CI pipeline to complete:
   ```bash
   gh run list --repo capabilities.repo --branch fix/<branch-name> --limit 3
   ```
2. If CI passes → proceed to Step 12.
3. If CI fails → analyze failure with `gh run view <id> --log-failed`, fix, push, and re-check (max 2 retries).

**Note:** `gh pr checks` does NOT work with fine-grained PATs. Use `gh run list` / `gh run view` instead.

### Output

CI pass/fail status.

### Failure Modes

- CI flaky failure → retry once
- Genuine test failure → fix and push
- CI timeout → wait and check again; if persistent, ask user for explicit approval before proceeding with local test results only. **[Headless: AUTO-RESOLVE — wait up to 2x timeout. If still not done, ERROR — post error comment with CI run URL, add rawgentic:ai-error label, exit.]**

---

## Step 12: Merge and Deploy

### Instructions

**Pre-merge check:** If the project's CLAUDE.md or development rules require explicit user approval for merge or deploy operations, ask the user before proceeding. Do not auto-merge in projects with approval gates.

1. Squash-merge PR:
   ```bash
   gh pr merge <number> --squash --delete-branch --repo capabilities.repo
   ```
2. Deploy to dev: If `capabilities.has_deploy`, use the deploy method and commands from `config.deploy`. Otherwise, ask the user for deployment instructions.
3. If the fix includes a database migration and `capabilities.has_database`, run it using the database CLI from `config.database.cli` against the database specified in `config.database`. If the database runs in a container, derive the container name and credentials from `config.database` and `config.infrastructure.docker`.
4. Verify deployment health.

### Output

Merged PR + deployed dev environment.

### Failure Modes

- Merge conflicts → rebase on default branch, resolve, push
- Deploy fails → check logs, rollback if needed via `git revert` on the default branch

---

## Step 13: Post-Deploy Verification

### Instructions

1. **Symptom verification:** Check that the original bug symptoms no longer occur in the dev environment.
2. **E2E verification (if applicable):** If `capabilities.has_tests` and config includes E2E test commands, run the relevant E2E specs using the test command from `config.testing.frameworks[]` (filtered for E2E type). If tests run on a remote host, use the appropriate host from `config.infrastructure.hosts[]`.
3. **Health check:** Verify all services are healthy after deployment.
4. **Quick reflect:** Does the deployed fix match what was intended?
5. **Same-class bug scan:** If the root cause was a missing/incorrect parameter at a call site, grep ALL callers of the affected function to check for the same class of bug at other call sites. Document findings in session notes.

### Output

Deployment verified OR rollback needed.

### Failure Modes

- Bug still reproduces in dev → investigate env-specific differences
- New issues introduced → rollback via `git revert` on the default branch

---

## Step 14: Completion Summary

### Instructions

The completion summary is no longer hand-typed. Assemble a structured
**run-record** and drive the summary through `hooks/work_summary.py` — the same
Tier-2 telemetry substrate WF2 Step 16 uses (see `docs/run-records.md`), so WF3's
completion output is consistent and every run is measurable, not just a sentence
read once.

1. Update `claude_docs/session_notes.md` with fix summary.
2. Close GitHub issue with closing comment:
   ```bash
   gh issue close <number> --repo capabilities.repo \
     --comment "Fixed in PR #<pr-number>. Root cause: <brief>. Fix: <brief>."
   ```
3. **Assemble the run-record** and write it to `/tmp/wf3-run-record.json` (use the
   Write tool, or a `cat > … <<'JSON'` heredoc). Every key below must be
   **present**; "nullable" means `null` is an allowed value, NOT that the key may
   be omitted (a dropped field is a telemetry gap). Counts are non-negative
   integers and `resolved` may not exceed `findings`:

   ```json
   {
     "workflow": "fix-bug",
     "workflow_version": "<.claude-plugin/plugin.json version>",
     "issue": {"number": <bug issue #>, "type": "bug",
               "complexity": "trivial|standard|complex|null"},
     "changes": {"files_changed": N, "insertions": N|null, "deletions": N|null,
                 "commits": N},
     "tests": {"added": N, "passing": N|null, "total": N|null},
     "gates": [
       {"step": "4", "name": "Lightweight Reflect", "findings": N, "resolved": N, "status": "pass|fail|skipped|fast_path"},
       {"step": "9", "name": "Code Review",         "findings": N, "resolved": N, "status": "..."}
     ],
     "security_scan": {"ran": false, "blocking_resolved": 0, "advisory": 0, "skipped": []},
     "loop_backs": {"used": N, "budget": 2},
     "outcome": {"pr_number": N|null, "pr_url": "<url>"|null, "merged": true|false|null,
                 "ci": "passed|failed|not_configured|skipped",
                 "deploy": "success|manual|failed|not_applicable"},
     "extra": [
       {"label": "Root Cause", "value": "<one-line root cause>"},
       {"label": "Fix",        "value": "<one-line fix>"}
     ],
     "follow_ups": ["<any item requiring future attention>", ...]
   }
   ```
   WF3 has **no** tool-based security scan (that is WF2 Step 11.5), so
   `security_scan.ran` is `false` (with zero counts and empty `skipped`) and the
   render shows "Security Scan: not run". `extra` carries the Root Cause / Fix
   lines WF3 has always shown. WF3's loop-back budget is **2**. Each gate's
   `step` must be distinct; conditional memorization happens *within* Step 9
   (Code Review), so record any memorized insights in `follow_ups` rather than as
   a second step-9 gate.

4. **Render + persist** (carry `activeProject.path` in as a literal — shell vars
   do not persist across Bash tool calls):
   ```bash
   python3 hooks/work_summary.py summarize \
     --record-file /tmp/wf3-run-record.json \
     --project-root <activeProject.path>
   rc=$?
   ```
   The tool's stdout **is** the "WF3 COMPLETE" summary — present it to the user
   as-is (do not re-type it). It also appends the record to
   `<activeProject.path>/docs/measurements/run_records.jsonl` (override with
   `--store` or `$RAWGENTIC_RUN_RECORD_STORE`).

5. **Handle the exit code:**
   - `rc == 0`: record valid and persisted. Done.
   - `rc == 1`: the summary still rendered (the user keeps it) but the record
     FAILED validation and was **not** persisted — a telemetry gap. The stderr
     lists the bad fields; fix `/tmp/wf3-run-record.json` and re-run. If it
     genuinely can't be fixed, record the gap in session notes.
   - `rc == 2`: usage error / unreadable record file — fix the invocation.

Log a marker in `claude_docs/session_notes.md`:
`### WF3 Step 14: Completion summary + run-record — DONE (persisted: yes/no)`

### Failure Modes

- None — this is an informational step. If previous steps had partial failures, this step reports the partial completion status.


---

## Workflow Resumption

If this skill is invoked mid-conversation, detect the current state:

0. All step markers present but completion-gate not printed? → Run completion-gate, then terminate.
1. PR merged? → Step 13 (post-deploy verification)
2. PR exists and CI passed? → Step 12 (merge)
3. PR exists? → Step 11 (CI check)
4. Fix branch has passing tests? → Step 9 (code review)
5. Fix branch has code changes? → Step 8 (verification)
6. Fix branch exists (empty)? → Step 7 (TDD)
7. RCA in session notes? → Step 5 (plan)
8. None → Step 1 (start from scratch)

Announce the detected state before resuming: "Detected prior progress. Resuming at Step N."

---

## Conditional Memorization (P9)

After completing the bug fix, check if the fix revealed patterns worth memorizing:

- New database gotcha or query pitfall
- Race condition or timing-related bug class
- Security vulnerability pattern
- Environment-specific behavior (dev vs prod differences)
- Third or more instance of a similar bug category

If insights are found, they are curated into memory (mempalace if available, else `CLAUDE.md` / `MEMORY.md`) in Step 9. This is conditional — skip for routine, one-off fixes.
