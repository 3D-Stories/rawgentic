---
name: epic-run
description: 'Use when setting up or driving a multi-issue epic auto-run in a rawgentic project — the user says "cycle through all issues in epic #N", "write me a goal for the epic", "auto-run these children", or asks to sequence WF2 across an epic''s task list. Covers drafting the /goal condition, deriving the queue, the merge-policy decision, and the per-child + wrap-up contract. Do NOT use for a single issue (use /rawgentic:implement-feature or /rawgentic:fix-bug directly) or to define/plan the epic itself (that is /rawgentic:create-issue).'
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

**Transport is PROBED, not asked (#927 — this step asks exactly TWO questions, above).**
There is no session-mode question any more. After the driver-state file exists, record the
campaign's transport by probing for the capability:

```bash
python3 hooks/launcher_lib.py transport resolve-creation \
  --driver-state claude_docs/.driver-state/<campaign>.json --project-root .
```

It writes `preferred_transport: "pane_chain"` when herdr answers, `"inline"` when it does
not, and records the probe's reason either way. **`pane_chain` is the DEFAULT** — each child
then runs in its OWN Claude process (fresh context, none of the prior child's turns) instead
of accumulating the whole epic in one session. Write-once: to change it later use
`transport set pane_chain|inline --reason "<why>"`, which is refused while a child is in
flight or a boundary holds a claim.

**Never assert the capability from `HERDR_ENV` or a flag.** The probe is a round trip, because
a recorded capability goes stale while the real one moves — herdr can be present at creation
and gone by boundary 4. That is why `--herdr-available` no longer exists on `select-mode`.

The transport preference is the campaign's own answer and the launcher always defers to it.
The contract lives in `docs/multi-issue-driver.md`.

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
- **A decision log**: forks and substitutions are recorded with
  `python3 hooks/decision_log.py append --project <name> --id D<n> --run epic-<N>
  --title "<what was decided>" --body "<why, and what it was decided against>"
  --overturnable "<how to undo it in one step>"`. That store lives at
  `claude_docs/decisions/<project>.jsonl`, is append-only, and is **never
  trimmed**; `session-start` injects its newest 15 records. Do NOT hand-append
  decisions to `claude_docs/session_notes/epic-<N>-autorun-log.md` — the notes
  trimmer destroyed six such logs before #847, and although that filename is now
  excluded from trimming, the durable store is the one that gets injected and the
  one `--overturnable` is enforced in.

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
- **The learnings sweep — the owner's standing order, and a GATE since #769.**
  **After every merged, deferred, or abandoned child — and whenever `origin/main` moves
  between children without a completion — sweep every remaining eligible child against the
  learnings for that boundary before selecting or handing off the next child.** Revalidation
  asks only whether the remaining bodies still describe reality at this head; this asks the
  wider question the owner actually gave (D181, epic #906, verbatim: *"in between each issue,
  make sure you revalidate future issues in the epic based on learnings"*). A completed child
  routinely invalidates a sibling's premise in a way no line-anchor check can see. The five
  parts, in order:
  1. **List** the completed child's findings, recorded decisions and filed issues.
  2. **Sweep** every remaining child's CURRENT body against them.
  3. **Comment or rescope** any child whose scope, approach or dependencies changed.
  4. **Log a decision entry** for anything you changed or deliberately did not.
  5. **Only then** start the next child.

  Record it, or the next `next-child`/`handoff` refuses with **rc 8**:
  ```bash
  HEAD=$(python3 hooks/launcher_lib.py sweep begin --project-root . \
         | python3 -c 'import json,sys; print(json.load(sys.stdin)["head"])')
  python3 hooks/launcher_lib.py sweep record --driver-state <f> --expected-head "$HEAD" \
    --after-issue <the child that just finished> --learnings '<what it taught>' \
    --assess '{"issue":<n>,"outcome":"unaffected","note":"<why it is unaffected>"}' \
    --project-root .
  ```
  One `--assess` per remaining eligible child, and coverage is checked as set EQUALITY — a
  missing child and a foreign one are equally refused. Outcomes are `unaffected`, `commented`
  or `rescoped`; the last two need a `ref` pointing at the artifact. **OMIT `--after-issue`
  when the head moved with no child completing** (an unplanned blocker fix between children);
  the literal text `null` is rejected so a typo cannot pass for that case. `--expected-head` is
  re-compared under the state lock, so assessments made against a head that has since moved are
  refused rather than stamped as current.
  **What the gate checks, and what it cannot:** coverage and record integrity ONLY — that a
  record exists for this head naming every remaining child with a reason. It does **not** verify
  that the judgment behind it was any good, and nothing here should be read as claiming
  otherwise. Two refusals with different remedies: `missing` is cleared by doing the sweep and
  running `sweep record`; `unreadable` means the state file needs repair first — copy it aside,
  RESET the field to `[]` (never DELETE the key: an absent key means the campaign predates
  this contract and disarms the gate permanently), check it parses, then record.
  A campaign whose driver-state carries no `boundary_sweeps` key at all predates this contract
  and is NOT gated — it adopts by recording its first sweep. Field precedent: the by-hand D181
  sweeps in `claude_docs/session_notes/epic-906-autorun-log.md`.
- **The child boundary is the DEFAULT (#927 — it fires unless the campaign records
  `preferred_transport: "inline"`).** After a child reaches ANY terminal outcome — `merged` OR a blocker's `deferred`/`abandoned` — the
  session ENDS rather than looping in-process (a blocked child's context must not bleed into
  an independent successor). Get the disposition through
  **`python3 hooks/launcher_lib.py handoff ...`**, never by calling
  `driver_lib.fresh_session_handoff` directly (#840): an armed campaign REFUSES a
  disposition computed without a freshly observed head, and a receipt-less one would bypass
  the gate entirely — the launcher command is what runs `observe_head`. If you do call the
  pure function in a probe, pass `observed_head=` from `launcher_lib.observe_head(<repo>)`.
  It takes `project=<the bound project NAME>` —
  the project is REQUIRED (#682): the resume prompt must OPEN with `/rawgentic:switch <project>`,
  because a bare bind enters the switch skill's list mode and waits for a human, and the
  launcher closes the successor's pane when `project_switched` exhausts at 120 s. Without it the
  disposition is `no_project` and NOTHING is persisted (deliberately, so a refusal cannot strand
  a bumped `generation`).
  **On `ready` the `handoff` command performs the whole boundary itself — do NOT call
  `driver_lib.open_handoff` after it** (corrected at Step-11 round 4, High 2; the instruction to
  do so described a transaction that does not exist). `handoff` observes the head, computes the
  disposition and, when it is `ready`, goes straight into `perform_handoff`: it splits the pane,
  launches the successor and — unless `--no-teardown` — retires the predecessor, then prints an
  `ok` report (rc 0 ok, 4 the ladder refused, 3 nothing ready, 5 head unobservable, 6 revalidate
  first, **10 declare your in-flight work first**).
  **`handoff` also takes an in-flight declaration (#726), and refuses with rc 10 without one.**
  Before the boundary, say what background work of YOURS is still running — harness background
  bash tasks, dispatched review jobs, `Monitor` watches — and pass either `--inflight-none` or one
  `--inflight '<kind>:<ident>:<state>:<detail>'` per item. A `running` item refuses and
  `--allow-inflight` cannot pass it: wait and re-declare it `completed`, or re-declare it
  `abandoned` and add `--allow-inflight`, which makes the command tell the successor not to wait
  for it. The same call also refuses a resume prompt that points the successor at a session-scoped
  path, and THAT has no override — copy the artifact in-repo and reference that. Both checks run
  before the generation is claimed, so a refusal costs nothing and is cleared by re-running. It returns no disposition object for you to persist, and there is no `disp` to pass on.
  **The exactly-one-successor fence IS here now (#845, closed inside #927).** `handoff` opens the
  generation and takes a claim BEFORE it probes, so two invocations for one boundary cannot both
  launch: the loser exits **rc 7**, does no campaign work, and does NOT start the next child in
  its own session. Treat rc 7 as "somebody else owns this boundary — stop", never as "nothing was
  due" (that is rc 3). The claim is released when the transition reaches a DEFINITE outcome, and
  deliberately held when a launch is indeterminate, because the lease is what protects a
  possibly-live successor until reconciliation runs. Then the durable launcher — which must
  POSITIVELY advertise no-`--resume` support (`fresh_session_available`'s `fresh_launch_supported`
  probe) — starts a FRESH `claude -p` **with NO `--resume`** for the successor. The successor
  rebuilds position from `.driver-state`, never from in-context memory.
  **It DOES claim now (#927).** The boundary reuses `mid-child-handoff`'s generation/claim/lease/ack
  machinery unchanged and differs only in its precondition: mid-child needs exactly one child
  `in_progress`, the boundary needs the opposite — the next child `queued` and nothing in flight.
  A boundary attempted while a child is in flight is refused with rc 3, because that is the
  `mid-child-handoff` case rather than this one. Every transition also records what it chose in an
  append-only log, and a degraded boundary prints ONE advisory line on **stderr** naming the
  reason (stdout stays machine-readable). On `complete` (every child merged) do Step 5; on
  `blocked` (unmerged children remain but none ready) leave the epic OPEN with an honest summary
  and end — never conflate `blocked` with `complete`. On **`revalidation_required`** (#840 — the
  remaining queue has not been revalidated against the current `origin/main`) **READ THE REASON
  BEFORE ACTING** (round-6 finding 4 — this used to say "run the skill and retry"
  unconditionally, which is a no-op for one of the two reasons):
  - **stale provenance** (a child never revalidated, or stamped against an older head) → run
    `/rawgentic:revalidate-children`, post any corrections, then retry the disposition.

  That is the ONLY reason the gate produces today. A `pending_disposition` used to be a second,
  owner-only reason; the owner gate was cut from #840 and is being rebuilt in #848, so a marker
  currently refuses nothing. When #848 lands, this list grows a second bullet whose remedy is an
  owner write-back that the revalidation skill can never clear.

  This is neither `complete` nor `blocked` and must never be reported as either.
- **Selecting the next child in the IN-SESSION loop goes through
  `python3 hooks/launcher_lib.py next-child --driver-state <f> --project-root <r>`** (#840),
  never by reading the state file and picking. That command observes `origin/main` itself and
  is the only in-session path the freshness gate can see: `single-session` mode does not cross
  a process boundary, so it never calls `handoff`, and before this the default mode advanced
  on a stale queue. rc 0 = ready (`next_issue` on stdout), 3 = nothing ready, 5 = the head
  could not be observed, 6 = revalidate first (worklist on stdout). **rc 2 = caller/data error —
  read stdout before deciding:** a `next_issue` key means selection SUCCEEDED and only a valid
  `project` is missing (pass `--project` and re-run), anything else means the state file is
  unusable and the run stops. `launcher_lib handoff`
  exits **6** for it and **5** when the head cannot be observed at all. **After EVERY merge, the
  remaining children need revalidating before the next one starts** — that is what the gate
  enforces, and it is the tax this epic's own 2026-08-02 audit measured at 14 rotted bodies out of
  23. **The terminal-backend verdict is part of
  this decision, not a later one (#611).** Resolve it first —
  `python3 hooks/launcher_lib.py select-mode --terminal-backend <backend> [--launcher-herdr]` —
  and pass it as `fresh_session_available`'s `launch_mode`. **`--herdr-available` is gone (#927):**
  the command derives the capability itself, because a caller-asserted capability is exactly the
  stale answer this issue removes. `--launcher-herdr` stays asserted — it is a claim about the
  LAUNCHER's support, which this process cannot observe. Deciding it
  only inside the launcher is too late: the driver would end the session believing the boundary
  was available, and "keep the current loop" is no longer possible once the loop has ended.
  **Fail-open:** if `driver_lib.fresh_session_available` is false (no launcher / no fresh-launch
  support / handoff path unwritable / the launch mode cannot cross the boundary), degrade to the
  single-session loop with the visible marker `### epic-run:
  fresh-session unavailable — single-session fallback (<reason>)`; the run never aborts for lack
  of the boundary. Each fresh session builds its OWN Step-3b task list from `.driver-state` (the
  harness Task tools are session-scoped — no list carries across the boundary). Full contract:
  `docs/multi-issue-driver.md`.
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
