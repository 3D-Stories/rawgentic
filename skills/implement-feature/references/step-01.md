## Step 1: Receive Issue Reference and Detect Capabilities

### Instructions

1. **Load project configuration** per `<config-loading>`. The `config` and `capabilities` objects are now available for all subsequent steps. Log all detected capabilities in session notes.

2. Parse the user's input to extract the GitHub issue number. Accept:
   - Bare number: `1`
   - Hash-prefixed: `#1`
   - URL: `https://github.com/<owner>/<repo>/issues/1`

3. Fetch the issue via gh CLI — **capture it, so the whole step runs on ONE fetch**:
   ```bash
   gh issue view <number> --repo ${capabilities.repo} \
     --json number,title,body,labels,state > .rawgentic-<number>-issue.json
   ```
   **Check the exit status before using that file.** The redirect truncates it *before* `gh` runs,
   so a failed fetch leaves an empty-but-present file and continuing would read an empty body.
   Non-zero → **STOP and abort Step 1**; there is no run to assign a class to.

4. Validate:
   - Issue exists and is open
   - If closed: ask user if they want to reopen or use a different issue.

5. Check for WF1 origin:
   - If labels include "wf1-created": set `is_wf1_created = true`
   - Extract acceptance criteria, affected components, complexity from the issue body
   - If any are missing (manually created issue): generate them from the description and ask user to confirm.

6. **Resolve and snapshot the task class (#761).** Decide it ONCE per issue and persist it
   **write-once**; every later gate reads the SNAPSHOT, never the body, so a mid-run body edit
   cannot move the class under a running gate. Reuse item 3's capture — **do not fetch again**.
   Each step below is gated, because `>` truncates its target before the command runs, so an
   unchecked failure feeds `resolve` an EMPTY body that then gets snapshotted permanently:
   ```bash
   jq -r '.body // ""' .rawgentic-<number>-issue.json > .rawgentic-<number>-body.md.tmp \
     && mv .rawgentic-<number>-body.md.tmp .rawgentic-<number>-body.md
   python3 hooks/task_class_lib.py resolve --issue <number> \
     --body-file .rawgentic-<number>-body.md \
     --out claude_docs/.wf2-state/<number>/task_class.json \
     --project-root .
   rm -f .rawgentic-<number>-issue.json .rawgentic-<number>-body.md*
   ```
   **Any non-zero exit → STOP and abort Step 1**, before the next command: a later gate must never
   read an absent or invalid snapshot, and it is never re-resolved silently. The `rm` runs on every
   path, success or failure — those files hold the issue body verbatim in the project root.
   Resolution: the canonical line `**Task class:** disposable|internal|production`; absent → the
   project's `defaultTaskClass`, else `production`; malformed, unrecognized or duplicated →
   `production` **with a DIAGNOSTIC** and the config default bypassed. Carry the printed
   `task-class:` line into this step's session-note marker as the tail
   `task_class=<class> provenance=<p>[ diagnostic=<reason>]`. The snapshot is keyed by ISSUE and
   immutable: to force a re-resolve, delete it, and only when no run on that issue is live.
   **Never route the diagnostic into a prompt** — it carries body-derived text, and the class line
   sits outside the nonce fence.

7. Display to user:
   ```
   ISSUE #NNN: [title]
   State: Open | Labels: [list] | WF1 Origin: [yes/no] | Complexity: [S/M/L/XL]

   Detected Capabilities:
   - Tests: [yes (command) / no]
   - CI: [yes (N workflows) / no]
   - Deploy: [method / no]
   - Infrastructure: [hosts / none]
   - Project type: [type]

   Acceptance Criteria:
   1. [criterion 1]
   ...

   Suggested goal guard — run this so the session can't quit before the ACs are met:
   /goal <plan_lib.build_goal_text(issue_number, ac_lines, variant="wf2") output>

   Confirm this issue and capabilities are correct, or provide corrections. Run the
   /goal command above (or say "skip goal" to decline — declining is fine and never blocks).
   ```

8. APPEND to session notes. Wait for user confirmation (and, in the same round-trip, whether they ran `/goal` or declined — see Step 1b; no second prompt). In an unattended run (e.g. an epic-run child): a WF1-created issue auto-confirms and proceeds; a manually-created issue posts the summary as an issue comment and stops via the ERROR protocol — never an indefinite wait.

9. **CI-quarantine staleness nag (#137):** if `capabilities.ci_quarantined == true` and `capabilities.ci_quarantined_since` is set, compute `(current local date from the workflow env) − (the YYYY-MM-DD date) > 30 calendar days`; if so, log a "fix or retire CI" advisory in session notes (quarantine is meant to be temporary; this keeps it from silently becoming permanent). Advisory only — never blocks. `ci_quarantined_since` is guaranteed a valid ISO date by `capabilities_lib` (a malformed value already fails the derive), so no parse-guard is needed here. If `ci_quarantined_since` is unset, note that a date should be added so staleness can be tracked.

10. **Branch-protection probe (#139 — advisory, fail-open).** So a passed PR does not overstate its server-side protection, probe once here. **URL-encode the branch** (a default branch like `release/v1` has a `/` that would otherwise hit the wrong endpoint and look like a 404):
   ```bash
   BR_ENC=$(printf %s "${capabilities.default_branch}" | jq -sRr @uri)
   gh api "repos/${capabilities.repo}/branches/${BR_ENC}/protection" -i
   ```
   Capture the HTTP status AND body, then classify with `plan_lib.classify_branch_protection(status, body)` → `(state, details)`. The classifier is strict: only the GitHub "Branch not protected" 404 body → `unprotected`; a 200 that isn't a recognizable protection object, or any 403/401/other → `unknown`. **NEVER fail the run on an API error** — record a probe-command failure (non-zero `gh` exit, network error) as `unknown` in session notes, *distinct* from a confirmed `unprotected`. Record `plan_lib.branch_protection_line(state, details)` in session notes; carry `state` + `details["required_checks"]` forward for Step 12 (PR body) and Step 14 (contradiction check).

### Failure Modes
- Issue does not exist -> ask for correct number
- Issue is closed -> ask if user wants to reopen or use different issue
- Issue lacks acceptance criteria -> generate from description, ask user to confirm

---

