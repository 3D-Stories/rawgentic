# WF2 state files & resumption protocol

Read this before any WF2 resume, and before reading or writing a
session-scoped state file or the local (git-excluded) review-state pointer. These are the
cross-cutting state/resume contracts the spine's steps depend on.

<state-files>
P15 (tiered review) introduces session-scoped state files under
`claude_docs/.wf2-state/<issue-number>/`:

- `review_log.jsonl` — append-only Step 8a review entries (`task_id`, `sha`,
  `reviewers`, `verdict`, nested `findings` `{crit, high, med, low, dropped}`, `ts` —
  the same shape Step 8a writes). `sha` and `verdict` are the only fields the Step 9
  coverage gate (`plan_lib.assert_review_coverage`) actually reads. Read by Step 9
  (coverage assertion) and Step 11 (already-reviewed SHA list).
- `deferrals.json` — finding-level deferrals re-presented at Step 11. Each
  entry: `finding_id`, `severity`, `status`, `defer_count`,
  `originator_reviewer_slot`, `concurrences`, `user_ack`. Written via
  `plan_lib.append_deferral` (create/re-defer) and `plan_lib.resolve_deferral`
  (apply a resolution) — do NOT hand-author this JSON; the resolution semantics
  live in `plan_lib._deferral_is_resolved`, so a mistyped field would silently
  drop a deferred High/Critical from the Step 11 exit gate.
- `loopback_counters.json` — per-source loop-back counters (`design`, `tdd`,
  `review_design`, `review`) plus `total`. Persisted across sessions via
  `plan_lib.consume_loopback`.

In addition, a small **local, git-excluded** status pointer lives at
`.rawgentic/review-state/<branch-sanitized>.json` (single object: `{schema_version,
branch, last_review_log_status, ts}`). Per-branch path so concurrent PRs do
not conflict. `plan_lib.write_review_state` auto-appends `.rawgentic/` to the repo's
`.git/info/exclude` so the pointer is **never committed into a feature PR** (#231 AC2) —
do NOT stage it. Read via `plan_lib.read_review_state(repo_root, branch)` which
also verifies `state.branch == current_branch` before trusting the file.
Step 12 and Step 14 read this file and refuse to ship if the last status is
not `"applied"`. The pointer is an on-disk file that survives across sessions in the
same checkout (it is no longer git-committed, so it does not travel via git history
across a fresh clone/worktree — a single WF2 run is one checkout, so this is moot);
the session-scoped files likewise do not.

The session-scoped directory is cleaned up on Step 14 merge success.
</state-files>

<resumption-protocol>
WF2 may span multiple Claude Code sessions. On resumption, detect the current step.

**Headless resume check (FIRST):** If in headless mode, execute the headless-resume protocol in `references/headless.md` before anything below. If a pending question was answered, inject the reply and resume at the step indicated in the session notes checkpoint. If no reply yet, exit cleanly.

**Otherwise, do NOT hand-apply the priority cascade.** The resume target is a strict ordered precedence (a merged PR resumes at post-deploy even if a stale design doc also exists), and applying that order by hand is how a resume silently lands on the wrong step. Gather the facts below — git/gh for the PR and branch, session notes for the design/issue/test status — then let `hooks/resume_lib.py detect-step` apply the canonical order. The ordering lives in one tested place so it can't drift from this prose:

```bash
set -euo pipefail
# Map your gathered facts to these three states (the value names ARE the rules):
#   --pr-state     none | open | ready-to-merge | merged
#     merged         = PR is merged
#     ready-to-merge = PR open AND (CI green OR project has no CI)   [-> Step 14]
#     open           = PR open AND CI not yet green                  [-> Step 13]
#     none           = no PR for this branch
#   --branch-state none | empty | changes | verified
#     verified = branch has commits AND tests pass/verified in notes [-> Step 11]
#     changes  = branch has commits, tests not yet verified          [-> Step 9]
#     empty    = branch exists with no commits                       [-> Step 8]
#     none     = no feature branch
#   --notes-state none | issue-validated | design-doc
#     design-doc      = a design document is recorded in notes       [-> Step 5]
#     issue-validated = issue validated in notes, no design yet       [-> Step 2]
#     none            = neither                                       [-> Step 1]
# MARKERS_COMPLETE = true|false  (true iff ALL step markers are present in notes)
# GATE_PRINTED     = true|false  (true iff the completion gate was already printed)
# HEADLESS = true|false  (true iff additionalContext has "HEADLESS MODE active").
#   In headless mode WF2 is PR-terminal: a ready-to-merge PR resumes at Step 16
#   (no merge/deploy) and a merged PR resumes at Step 16 (no post-deploy); `open`
#   still resumes at Step 13 so the bot can push CI fixes (a local op).
STEP=$(python3 hooks/resume_lib.py detect-step \
  --pr-state PR_STATE --branch-state BRANCH_STATE --notes-state NOTES_STATE \
  --markers-complete MARKERS_COMPLETE --completion-gate-printed GATE_PRINTED \
  --headless HEADLESS)
echo "Resuming at: $STEP"
```

Pass the marker booleans (and `--headless`) on every call (don't leave the completion-gate or headless rules to prose) — `detect-step` prints either a step number (1, 2, 5, 8, 9, 11, 13, 14, 15, 16) or `completion-gate` (all markers present but the gate was never printed — run the completion gate, then terminate). Resume at the printed step. An unrecognized `--*-state` or non-`true`/`false` flag value exits non-zero rather than defaulting to Step 1, so a mistyped fact fails loudly instead of restarting in-flight work.

Before context compacts, APPEND to session notes:
- Current step number and sub-step
- Quality gate findings not yet applied
- Feature branch name and last commit SHA
- Loop-back budget state (design_loopback_count, tdd/review used, global total)
- Any unresolved circuit breaker state
- Detected capabilities summary
- If in Step 8: current task index, implementation phase, and verification status
</resumption-protocol>
