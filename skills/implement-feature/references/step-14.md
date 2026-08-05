## Step 14: Merge PR and Deploy (Adaptive)

### Instructions

**State cleanup:** on merge success, clean up `claude_docs/.wf2-state/<issue>/`. (Merge permission itself is owner-gated — see the workflow's merge rules; nothing here weakens them.)

**Quarantine × protection contradiction check (#139):** before attempting the merge, call `plan_lib.quarantine_protection_contradiction(capabilities.ci_quarantined, <protection state from Step 1 item 9>, <required_checks>)`. A non-None message means CI is quarantined (WF2 non-gating) but branch protection REQUIRES a status check — the squash-merge below would hit a server-side wall. Surface the message to the user and STOP rather than merging into the wall.

**Merge-grant path — persist the run-record BEFORE the merge (#888).** Step 16 runs *after* the
merge, so a run merging its own PR orphans its record if anything interrupts the gap (saystory
epic #204: 1 of 6 persisted; #879 needed the #882 backfill). **The run-record is
therefore persisted, committed and CI-re-verified before the merge is attempted, in this order:**

1. Assemble it per `references/run-record.md`; populate `usage` FIRST via
   `python3 hooks/usage_capture.py capture --session-id "$CLAUDE_CODE_SESSION_ID"`.
2. `python3 hooks/work_summary.py summarize --record-file <f> --project-root .` (rc 0 = persisted).
3. Commit the appended `docs/measurements/run_records.jsonl` line as a
   `chore(telemetry):` commit, staged BY NAME.
4. Re-verify CI **on the new head, per-sha** — a rollup queried by PR number can still report the
   previous head's green.
5. Prove THIS run's line is in the commit: `git show --numstat HEAD --
   docs/measurements/run_records.jsonl` must show one added line. **No added line means do not
   merge yet.** (`find --issue` is issue-scoped — an earlier run's record returns rc 0 while this
   one's is missing.)
6. Only now merge (below).

Its shape is `outcome.merged: null` plus a `follow_ups` entry naming the ordering — `null` here
means "not yet merged when the record was written", never "the merge failed". **`usage` is
pre-merge too**: it omits what the merge, CI re-verify and wrap-up still spend, so say so in that
same note rather than let a consumer read it as full-run cost. Step 16 then renders from it
rather than summarizing twice (§16 item 3a).

1. **Merge PR (squash merge):**
   ```bash
   gh pr merge <pr_number> --repo ${capabilities.repo} --squash --delete-branch
   ```

2. **Pull main:**
   ```bash
   git checkout ${capabilities.default_branch} && git pull origin ${capabilities.default_branch}
   ```

2b. **Record the terminal status back to any campaign queue (#695) — IMMEDIATELY after the merge is confirmed:**
   ```bash
   python3 hooks/launcher_lib.py record-child-outcome --issue <issue> --status merged --project-root .
   ```
   This is the write that #695 exists for. Nothing used to do it, so a child shipped outside the
   epic driver left `claude_docs/.driver-state/<campaign>.json` reading `queued` after its PR
   merged — and a fresh-session resume, correctly deriving position from durable state, would
   re-run a merged child.

   It runs **here**, right after the merge, rather than only at Step 16: Step 16 is not atomic
   with the merge, so a crash between them reproduces the defect exactly. Step 16 repeats it as
   idempotent reconciliation.

   **Fail-open by design.** No campaign naming this issue → exit 0, writes nothing, and says so
   on both streams; that is the normal case for a single-session run. A non-zero exit means a
   caller/data error (off-vocabulary status, a terminal regression, a corrupt state file) — do
   NOT ignore it, and do not hand-edit the state file to work around it.

3. **Deploy (adaptive based on capabilities.deploy_method):**

   **If `deploy_method == "script"`:**
   Run the deploy script from `config.deploy`.

   **If `deploy_method == "ssh"`:**
   SSH to infrastructure hosts from `config.infrastructure.hosts[]` and execute the deployment commands appropriate for the change (docker compose up, service restart, config reload, etc.). Generate commands based on the implementation plan — do NOT use hardcoded commands.

   **If `deploy_method == "compose"`:**
   Run `docker compose up -d` with the relevant compose file.

   **If `deploy_method == null` or `"manual"`:**
   Present deployment instructions to the user:
   ```
   MANUAL DEPLOYMENT REQUIRED
   ==========================
   The following changes need to be deployed:
   [list of changes and where they need to be applied]

   Suggested commands:
   [generated from implementation plan]

   Please deploy and confirm when complete.
   ```
   Wait for user confirmation before proceeding to Step 15.

### Output
Deployed (or manual deployment instructions provided and confirmed).

### Failure Modes
- Merge conflicts: rebase and re-push
- Deploy fails: check logs, rollback if needed
- Manual deploy: user must confirm completion

---

