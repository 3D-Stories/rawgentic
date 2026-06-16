# Headless-mode protocol

Loaded on demand by this skill's SKILL.md `<headless-mode>` pointer when
`additionalContext` contains "HEADLESS MODE active". Not read on a normal interactive run.

<headless-interaction>
When the workflow hits a user interaction point, check whether headless mode is active
(additionalContext contains "HEADLESS MODE active"). If NOT in headless mode, behave
as normal (STOP and wait for terminal input). If in headless mode, follow this protocol:

**Interaction types and headless behavior:**

AUTO-RESOLVE interactions (no user input needed in headless mode):
- Step 1: Accept bug confirmation for WF1-created issues
- Step 2: Trivial-work suggestion — continue the full workflow (no interactive user for a "do it directly" hand-off)
- Step 6: Always stash dirty directory (post brief issue comment with stash ref)
- Step 6: Always resume existing branch

QUESTION interactions (post comment, suspend, exit):
- Step 1: Confirm bug for manually-created issues
- Step 1: Missing reproduction steps (not security/STRIDE)
- Step 2: Cannot reproduce — ask for more details
- Step 2: Complex bug upgrade to WF2
- Step 3: Multiple root causes — ask for guidance
- Step 4: Ambiguity circuit breaker findings
- Step 7: Reproduction test passes immediately — ask user to verify
- Step 12: Manual deploy confirmation
- Step 14: Merge approval (if project requires explicit approval)

ERROR interactions (post error comment, exit WITHOUT ai-waiting label):
- Step 1: Issue closed or not found
- Step 4: Loop-back budget exhausted
- Step 11: CI timeout (after 2x wait)
- Step 9: Design flaw + budget exhausted

**Protocol details are identical to WF2's `<headless-interaction>` block.**
See implement-feature SKILL.md for the full QUESTION protocol (post → label →
suspend file → WAL entry → checkpoint → exit), ERROR protocol, and label
management details.
</headless-interaction>

<headless-checkpoint>
Before exiting in headless mode, write a rich checkpoint to session notes.
Must contain enough context for a FRESH session to reconstruct workflow state.

**Always include:** Current step, branch name, last commit SHA, loop-back budget,
pending question + question_id, bug classification, RCA findings.

**Include if available:** Root cause analysis, fix approach, reproduction test
file + test name, implementation progress.

**Format:**
```
### WF3 Headless Checkpoint — Step N (SUSPENDED)
- Branch: fix/42-foo
- Last commit: abc123
- Loop-back budget: 0/2 used
- Bug classification: [simple/moderate/complex]
- RCA: [1-line root cause summary]
- Pending question: [question_id] — [brief description]
```
</headless-checkpoint>

<headless-resume>
On every fresh session start, BEFORE the normal resumption protocol:

0. **Load project configuration** per `<config-loading>` to populate `capabilities.repo`.
1. Check if `claude_docs/headless_suspend.json` exists.
2. If missing → no pending question. Proceed with normal resumption protocol.
3. If present → read suspend state, fetch user's reply from GitHub:
   ```bash
   gh api repos/${capabilities.repo}/issues/ISSUE/comments \
     --jq '[.[] | select(.created_at > "SUSPEND_TIMESTAMP")] | map(select(.user.login != "github-actions[bot]")) | last.body // empty'
   ```
4. If no reply → user hasn't responded. Exit cleanly.
5. If reply unparseable → increment clarification_round (update ONLY clarification_round
   in suspend file, NOT suspended_at), post clarification comment, re-add ai-waiting, exit.
6. If reply valid → delete suspend file, remove ai-waiting + ai-error labels, inject
   choice, continue with normal resumption protocol.
</headless-resume>
