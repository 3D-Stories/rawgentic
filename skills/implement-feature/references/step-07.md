## Step 7: Create Feature Branch

### Instructions

1. Ensure working directory is clean:
   ```bash
   git status --porcelain
   ```
   If dirty: stash, create branch, ask user about stash.

2. Create the feature branch from a **freshly-fetched** default branch — never `git pull` into the current checkout first. `git pull origin <default>` merges the default INTO whatever branch is checked out; if the session still sits on a prior issue's feature branch (a multi-issue campaign, or a prior run that ended at its PR without merging), that mutates the sibling branch AND bases the new branch on the mixture, silently carrying the sibling's unmerged commits into this PR. Fetch, then branch off `origin/<default>` regardless of the starting checkout:
   ```bash
   git fetch origin ${capabilities.default_branch}
   git checkout -b <branch_name> origin/${capabilities.default_branch}
   ```
   **Base assertion — STOP on mismatch.** Confirm the new branch's base is exactly `origin/<default>` HEAD (nothing foreign rode along):
   ```bash
   [ "$(git merge-base HEAD origin/${capabilities.default_branch})" = "$(git rev-parse origin/${capabilities.default_branch})" ] && echo BASE_OK || echo BASE_MISMATCH
   ```
   `BASE_MISMATCH` → STOP and reconcile before writing any code (do not build on a wrong base). Because nothing is pulled into the current checkout, no pre-existing branch is mutated as a side effect.

3. **MANDATORY step-state bootstrap — before anything else on the branch.**
   ```bash
   python3 hooks/step_state.py write --project <project> --workflow wf2 --step 7 \
     --step-title "Create Branch" --issue <issue number> \
     --session-id "$CLAUDE_CODE_SESSION_ID"
   ```
   The hook stamps later steps ONLY once the pointer names this session, and only a
   `— DONE` marker or this call creates it. Skip it and the run records **no timing at
   all** (#976 did). Fail-open — run it anyway. Why:
   `TestSignatureStampingNeedsABootstrap`.

4. Push empty branch to origin:
   ```bash
   git push -u origin <branch_name>
   ```

5. Link branch to issue:
   ```bash
   gh issue comment <issue_number> --repo ${capabilities.repo} --body "Implementation started on branch \`<branch_name>\`"
   ```

### Output
Feature branch created and pushed, issue commented.

### Failure Modes
- Branch already exists: ask user to resume or start fresh.
- Push fails: continue locally, push later

---

