# Headless-mode protocol

Loaded on demand by this skill's SKILL.md `<headless-mode>` pointer when
`additionalContext` contains "HEADLESS MODE active". Not read on a normal interactive run.

<headless-interaction>
When the workflow hits a user interaction point, check whether headless mode is active
(additionalContext contains "HEADLESS MODE active"). If NOT in headless mode, behave
as normal (STOP and wait for terminal input). If in headless mode, follow this protocol:

**Interaction types and headless behavior:**

AUTO-RESOLVE interactions (no user input needed in headless mode):
- Step 1: Accept auto-generated ACs for WF1-created issues
- Step 1: Accept capabilities for WF1-created issues
- Step 1b: Goal guard — WF1-created: emit built goal text to checkpoint for the driver to set; unlabeled: skip and log (skipped)
- Step 2: Live environment probe — skip SSH probes entirely; local exploration only (file reads, grep, git). A headless run makes no outbound SSH.
- Step 5: Remove excess tasks on scope creep (document in session notes)
- Step 5: Risk ratio `warn` band — log to session notes, continue
- Step 2: Trivial-work suggestion — continue the full workflow (no interactive user for a "do it directly" hand-off)
- Step 7: Always stash dirty directory
- Step 7: Always resume existing branch
- Step 8: Rewrite failing RED test (up to 2 attempts)
- Step 13: Wait up to 2x CI_MAX_WAIT before erroring
- Step 14: Merge and deploy — skip the entire step; PR creation is the terminal deliverable (no merge, no deploy, no SSH). Proceed to Step 16. Step 15 (post-deploy) is likewise skipped.

QUESTION interactions (post comment, suspend, exit):
- Step 1: Confirm capabilities/ACs for manually-created issues
- Step 2: Component discrepancy
- Step 3: Design approach trade-offs
- Step 3: Scope larger than estimated
- Step 4: Ambiguity circuit breaker findings
- Step 5: Risk ratio `halt` band (>50%) or `decompose` (≥80%) — risk-ratio breakdown with options
- Step 8a: Ambiguity circuit breaker on per-task review findings
- Step 8a: Reviewer dispatch failure after retry (REVIEW_DISPATCH_FAILED)
- Step 11: Unresolved deferred-High findings at exit gate
  (Step 14's manual-deploy confirmation is NOT a headless QUESTION — Step 14 is
  skipped entirely in headless mode; see the Step 14 AUTO-RESOLVE entry above.)

ERROR interactions (post error comment, exit WITHOUT ai-waiting label):
- Step 4: Design loop-back budget exhausted
- Step 4: Global loop-back budget exhausted
- Step 5: Plan format contract violation (missing riskLevel; fail-closed)
- Step 8: Design flaw + budget exhausted
- Step 8a: Design flaw + review_design loop-back budget exhausted
- Step 11: Design flaw in review + budget exhausted

**QUESTION protocol (post → label → suspend → exit):**

1. Run the post-label-suspend sequence as **ONE atomic Bash block**. This is
   mandatory: `$QID`, `$COMMENT_BODY`, and `$COMMENT_URL` are generated/captured
   at runtime and shell variables do NOT persist across separate Bash tool
   calls — splitting these into multiple blocks would post with an empty body or
   write a suspend file with an empty question_id/comment_url. `set -euo
   pipefail` plus the explicit URL guard make the whole sequence fail closed.
   The CLI replaces inline `python3 -c` (a stable flag contract avoids the
   quoting/escaping bugs of reconstructing a snippet on every suspend).
   ```bash
   set -euo pipefail

   # Generate a question_id + the structured comment body.
   QID=$(python3 hooks/headless_interaction.py new-id)
   COMMENT_BODY=$(python3 hooks/headless_interaction.py format-comment \
     --step STEP_NUMBER \
     --title "QUESTION_TITLE" \
     --context "WHAT_THE_WORKFLOW_IS_DOING" \
     --question "THE_DECISION_NEEDED" \
     --option "(a) Option 1" --option "(b) Option 2" \
     --type "INTERACTION_TYPE" \
     --question-id "$QID")

   # Post the comment; capture the URL. Fail closed if gh returns no URL.
   COMMENT_URL=$(gh issue comment ISSUE_NUMBER --repo ${capabilities.repo} --body "$COMMENT_BODY")
   [[ -n "$COMMENT_URL" ]] || { echo "no comment URL returned by gh" >&2; exit 1; }

   # Add the waiting label (create it first if missing).
   gh label create "rawgentic:ai-waiting" --repo ${capabilities.repo} \
     --description "Rawgentic headless: waiting for user reply" --color "FBCA04" 2>/dev/null || true
   gh issue edit ISSUE_NUMBER --repo ${capabilities.repo} --add-label "rawgentic:ai-waiting"

   # Write the suspend state, reusing the SAME $QID and $COMMENT_URL. write-suspend
   # rejects empty --question-id/--comment-url, so a lost variable fails closed
   # rather than writing an unmatchable suspend file.
   python3 hooks/headless_interaction.py write-suspend \
     --path claude_docs/headless_suspend.json \
     --issue ISSUE_NUMBER \
     --step STEP_NUMBER \
     --question-id "$QID" \
     --comment-url "$COMMENT_URL"

   # Write the SUSPEND WAL entry.
   bash hooks/wal-suspend
   ```
   On a clarification re-ask, also pass `--clarification-round N` to
   `write-suspend` (defaults to `0`); `--session-id` defaults to `N/A`.

2. Write a rich checkpoint to session notes (see <headless-checkpoint>).

3. **EXIT the workflow cleanly.** Do NOT continue to the next step. The orchestrator
   will re-invoke the skill in a fresh session after the user replies.

**ERROR protocol (post error → exit WITHOUT label):**

1. Post an error comment to the issue describing what went wrong and what the
   user needs to do to unblock.
2. Do NOT add `rawgentic:ai-waiting` label (errors don't expect a reply).
3. Add `rawgentic:ai-error` label instead (create if missing, color "D93F0B").
4. Write session notes with the error state.
5. EXIT the workflow.

**Label management:**
- `rawgentic:ai-waiting` — set by skill on QUESTION suspend, removed by skill on resume
- `rawgentic:ai-error` — set by skill on terminal error
- `rawgentic:ai-in-progress` — set/removed by orchestrator (NOT by the skill)
</headless-interaction>

<headless-checkpoint>
Before exiting in headless mode (either QUESTION suspend or ERROR), write a rich
checkpoint to session notes. This checkpoint must contain enough context for a
FRESH session (no --resume, no conversation history) to reconstruct the workflow state.

**Always include in the checkpoint:**
- Current step number and sub-step
- Feature branch name and last commit SHA
- Loop-back budget state
- The question that was posted (for QUESTION suspends)
- The question_id (for reply correlation)

**Include if available (from prior session notes or current conversation):**
- Design approach selected and rationale (if past Step 3)
- Key critique findings and how they were resolved (if past Step 4)
- Implementation plan summary (if past Step 5)
- Implementation progress: which tasks are done, current task index (if in Step 8)

**Format in session notes:**
```
### WF2 Headless Checkpoint — Step N (SUSPENDED)
- Branch: feature/43-foo
- Last commit: abc123
- Loop-back budget: 1/3 used
- Pending question: [question_id] — [brief description]
- Design: [approach name] — [1-line rationale]
- Plan: [N tasks, M complete]
- Key decisions: [bullet list of non-obvious choices made]
```

This checkpoint is what enables the fresh-session resumption pattern. Without it,
a fresh session can only detect "Step 8 has code changes" but not "Step 8 task 5
of 7, using approach B because of critique finding #3."
</headless-checkpoint>

<headless-resume>
On every fresh session start, BEFORE the normal resumption protocol, check for a
pending headless interaction:

0. **Load project configuration** per `<config-loading>` to populate `capabilities.repo`.
   If config-loading fails, post error comment and exit.
   Each numbered step below runs as its own Bash call with your judgement in
   between, so shell variables do NOT persist across them. Read the values once
   from `read-suspend` (step 1) and substitute them as **literals** into the
   later commands (the `ISSUE`, `SUSPENDED_AT`, `OPTION_TOKENS` placeholders).
   The user's reply itself is never substituted — it stays inside a quoted shell
   variable within step 2's single block.

1. Read and validate the suspend state in one fail-closed step with `read-suspend`
   (it handles existence, JSON parse, AND field validation — a file that parses
   but is unusable must not be mistaken for "no pending question"). On success it
   prints the validated state JSON to stdout:
   ```bash
   python3 hooks/headless_interaction.py read-suspend \
     --path claude_docs/headless_suspend.json
   rc=$?; echo "read-suspend exit: $rc"; exit "$rc"
   ```
   (the block re-exits with read-suspend's code so the exit status — not just text — carries the result.)
   - exit `3` → no suspend file: no pending question. Proceed with the normal `<resumption-protocol>`.
   - exit `1` (or any non-zero other than 3) → the suspend file exists but is corrupt/unusable; a pending question was lost. STOP and escalate (post an error comment, add `rawgentic:ai-error`, exit). Do NOT silently restart as if nothing were pending.
   - exit `0` → read these fields from the printed JSON and carry them as literals into the steps below: `question_id`, `comment_url`, `issue`, `step`, `suspended_at`, `clarification_round` (always present — defaults to 0).
2. Fetch the user's reply AND attempt a deterministic literal parse in ONE block,
   keeping the reply in a quoted shell variable so an arbitrary comment (quotes,
   newlines, shell metacharacters) is never spliced into a command. Substitute
   the literal `issue` and `suspended_at` from step 1 for `ISSUE`/`SUSPENDED_AT`,
   and `OPTION_TOKENS` with the comma-separated letters/numbers you offered (e.g.
   `a,b`) so an out-of-range answer fails closed to clarification:
   ```bash
   set -uo pipefail   # NOT -e: we branch on the parse outcome below
   # Check the fetch itself: an auth/network/rate-limit failure returns non-zero
   # with empty output, which must NOT be mistaken for "the user hasn't replied".
   if ! REPLY=$(gh api repos/${capabilities.repo}/issues/ISSUE/comments \
     --jq '[.[] | select(.created_at > "SUSPENDED_AT")] | map(select(.user.login != "github-actions[bot]")) | last.body // empty'); then
     echo "FETCH_FAILED"
   elif [[ -z "$REPLY" ]]; then
     echo "NO_REPLY"
   else
     printf 'REPLY: %s\n' "$REPLY"
     printf '%s' "$REPLY" \
       | python3 hooks/headless_interaction.py parse-reply --options "OPTION_TOKENS" \
       && echo "CHOICE_OK" || echo "CHOICE_DEFERRED"
   fi
   ```
3. Act on the block's output:
   - `FETCH_FAILED` → could not read the issue comments (auth/network/rate-limit). This is a transient infrastructure failure, NOT a missing reply: do not post a no-response reminder. STOP and escalate (or retry) so the pending question is preserved.
   - `NO_REPLY` → the user hasn't responded yet. Post a reminder comment if `clarification_round < 2`, otherwise post an error. Exit.
   - `CHOICE_OK` → the token printed on the line just above it is an unambiguous, in-range option (e.g. `a`, `2`). Use it.
   - `CHOICE_DEFERRED` → no in-range literal choice. Interpret the `REPLY:` text
     yourself ("proceed"/"approved"/"go with the first one" → the obvious option).
     If you still cannot resolve it confidently → increment `clarification_round`,
     post a clarification comment, re-add `rawgentic:ai-waiting`, update the suspend
     file (update ONLY `clarification_round` — do NOT update `suspended_at`, as the
     next resume must still see comments posted after the original question), exit.
4. Once the choice is resolved:
   - Delete `claude_docs/headless_suspend.json`
   - Remove `rawgentic:ai-waiting` label (and `rawgentic:ai-error` if stale from prior run), substituting the literal `issue`:
     ```bash
     gh issue edit ISSUE --repo ${capabilities.repo} --remove-label "rawgentic:ai-waiting" --remove-label "rawgentic:ai-error" 2>/dev/null || true
     ```
   - Inject the user's choice into the workflow context and resume at the `step`
     read from the suspend state (the session notes checkpoint provides the rest
     of the context for that step).
</headless-resume>
