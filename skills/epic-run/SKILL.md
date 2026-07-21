---
name: epic-run
description: Use when setting up or driving a multi-issue epic auto-run in a rawgentic project — the user says "cycle through all issues in epic #N", "write me a goal for the epic", "auto-run these children", or asks to sequence WF2 across an epic's task list. Covers drafting the /goal condition, deriving the queue, the merge-policy decision, and the per-child + wrap-up contract. Do NOT use for a single issue (use /rawgentic:implement-feature or /rawgentic:fix-bug directly) or to define/plan the epic itself (that is /rawgentic:create-issue).
argument-hint: <epic issue number>
---

# Epic Auto-Run

Set up and drive a sequential WF2 run over an epic's children: derive the queue, get
the merge-policy decision, draft the `/goal` Stop-hook condition, and run child-by-child
with honest blocker handling. The driver contract lives in
`docs/multi-issue-driver.md` + `hooks/driver_lib.py`; this skill is the interactive
front-end for it.

Step-entry state (#480; #499 epic-run carve-out): epic-run's markers are not `### WF<n>`-shaped and it has no signature table, so the PostToolUse hook cannot derive its position — at each numbered step ENTRY, run `python3 hooks/step_state.py write --project <project> --workflow epic-run --step <N> --step-title "<step name>" --issue <epic number> --session-id "$CLAUDE_CODE_SESSION_ID"` (the manual call stays REQUIRED here, unlike the marker-covered workflows, where it is OPTIONAL hook-emitted since #499). Fail-open (never gates; any failure is ignored and the step proceeds).

## Step 1: Derive the queue from the epic

- Read the epic issue. The queue is its task-list checkboxes, exactly the shape
  `- [ ] #N` (already-checked `- [x]` children are done — exclude them).
- Per-child dependencies come from **each child's own body** via the
  `parse_depends_on` phrasing: `depends on #N` / `blocked by #N` (immediate `#N` list
  only; negations like "no longer depends on" are ignored; a bare `#N` in prose is not
  a dependency). **Never parse the epic body for dependencies** — its checkboxes would
  be misread as deps.
- Topo-order the children (deps first; tie-break lowest issue number). A cycle is a
  blocker to surface, not to route around.
- Read each child for rescope markers ("🔄 Rescope", edited ACs) — the CURRENT body is
  the contract, not the original filing. Note children that build on siblings' outputs
  so the goal can pin the order.

## Step 2: The merge-policy decision (the user's call, every run)

Ask ONE question with the options:
1. **Auto-merge (scoped override)** — the run creates AND merges each PR; the
  authorization is one-time and scoped to this run, spent when it ends. Sequential
  merge-between is required (next child branches from the merged main).
2. **PR-only** — the run stops each child at PR creation; the user merges.

Never assume auto-merge from a past run — the grant does not carry over.

Alongside the merge-policy question, recommend arming the durable resume launcher (the
`long-run-resume` skill's system-crontab pattern) at RUN START — even attended runs hit
the same stall class (owner-away review verdicts, unattended quota pauses; measured
basis: epic #509 lever 1, one 56.3-min owner-away gap, ~56 min per comparable attended
run). Declining is fine and never blocks the run.

## Step 3: Draft the /goal condition

Hand the user a block they can paste into `/goal` (you cannot invoke /goal for them —
it is session-level). The condition must contain, explicitly:

- **The queue**: all open children in topo order, by number, and the epic number/repo.
- **Mode**: the Step-2 decision, with "scoped one-time override, spent when the run
  ends" language if auto-merge.
- **Per-child contract** (WF2 non-negotiables spelled out so the Stop hook can check
  them): branch from fresh origin/main; TDD red-before-green; Step-4 design gate,
  Step-11 review, Step-11.5 scan; full suite green vs baseline; version bump ×3
  surfaces (patch fix/chore/docs/ci, minor feat); README changelog + docs + diagram REV
  decision; PR; wait for CI (name the hard lanes: test, lint); merge (if auto);
  verify issue auto-close; persist the Step-16 run-record.
- **Child-specific notes**: rescoped children implement the rescope section;
  dependency-ordered children build on the merged predecessor; investigation children
  deliver a report + drift-guard (docs-patch PR if no code change).
- **Blocker protocol**: a blocked child gets an ERROR blocker comment on its issue,
  then the run CONTINUES to the next child; the epic stays OPEN with an honest summary.
  Never hang the run on an unsatisfiable condition.
- **DONE definition**: all children merged+closed, epic checkboxes checked, epic
  CLOSED with a summary comment.
- **A decision log**: forks and substitutions go to
  `claude_docs/session_notes/epic-<N>-autorun-log.md` (append-only).

## Step 3b: Put up the run task list (#517)

The operator gets an on-screen checklist of the whole run — filed from live
field evidence (epic #509, 2026-07-19: the owner had to interrupt mid-run to
put a list up by hand).

- Check `TaskList` first: if a relevant list for this epic already exists (a
  resumed run — including one created by a PRIOR session of the same run),
  refresh it instead of creating a second list (mark merged children
  completed, delete stale entries).
- Otherwise create one task per queued child via `TaskCreate` — subject
  `#<n> — <short title>`, an `activeForm` for the spinner, and a sequential
  `blockedBy` chain matching the topo order — plus a final close-epic task
  ("Close epic #<N> — summary + run-records") blocked by the last child.
- Fail-open: when the Task tools are unavailable (deferred and not loadable
  via ToolSearch), skip with the one-line session-note marker
  `### epic-run task list: skipped (tools unavailable)` — the task list is
  bookkeeping and never blocks the run.

## Step 4: Drive the run

- One child at a time, WF2 fresh per child, terminating at its Step 16 — the driver
  never reaches into a WF2 step.
- Keep the Step 3b task list honest as state changes: mark the active child
  `in_progress` (at most one), flip it `completed` only when its PR is merged
  AND the epic box is ticked, and leave a blocked child visible with a note
  (mirroring the ERROR-comment-and-continue protocol). An owner-added
  mid-run child gets a task inserted at its queue position.
- Between children (auto-merge mode): merge, verify the merge SHA on main and the issue
  auto-closed, `git fetch origin`, branch the next child from the new main. Use the
  `merge-watch` skill's lane doctrine for CI triage (hard vs advisory lanes; OAuth
  false-red signature).
- Tick the epic checkbox after each merged child (state flows one-way: run → epic;
  never un-tick a human's edit).
- Notify the owner at every point the run blocks on human input — a review verdict
  ready with findings needing a call, a mid-run policy question, a pause request
  honored — via the workspace `notify-owner` skill when available; when unavailable,
  log the visible fail-open skip marker `### epic-run notify: skipped (notify-owner
  unavailable)` and continue — the notification layer never blocks the run
  (measured basis: epic #509 lever 1).
- Mid-run environment changes (a CI outage, a denied permission) that force a policy
  deviation are the USER's call — ask once with options, log the decision (D-numbered)
  in the run log, apply it for the rest of the run.
- Keep a per-child running record: issue, PR, version shipped, suite delta, deviations.

## Step 5: Wrap up

- Verify every child CLOSED and every box checked; close the epic with a summary
  comment (children → PRs → versions table, deviations, follow-ups).
- Persist any run-records not yet committed (`chore(telemetry):` PR if the project
  keeps telemetry in-repo).
- Final report: what merged (with versions), what was blocked and why, decisions made
  under the run's authority, and the one claim most worth re-checking.
- Complete the close-epic task on the Step 3b list when the epic closes (and
  complete/annotate any child tasks the run could not finish, honestly).

## Common mistakes

- Treating a past run's auto-merge grant as still live — it is spent; ask again.
- Deriving deps from the epic body (checkboxes ≠ dependencies).
- Implementing a child's ORIGINAL ACs when the body was rescoped after filing.
- Skipping merge-verification between children — the next child then branches from a
  main that doesn't contain its dependency.
- Silently skipping a blocked child instead of the ERROR-comment-and-continue protocol.
- Letting the run end with the epic open but unannotated — the honest summary is part
  of DONE.
