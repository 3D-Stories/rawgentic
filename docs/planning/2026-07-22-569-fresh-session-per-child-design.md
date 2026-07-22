# #569 — Fresh session per child in the epic auto-run

Design r2 · 2026-07-22 · session 3544db7b · issue #569 · plugin 3.88.0 → 3.89.0
WF2 full spine (architectural: process-boundary state handoff; a broken handoff strands a run).
r2 delta (folds 6 adversarial findings, gpt-5.6-sol, all verified): [1] fresh-session mode forces
the launcher to SKIP `--resume` (bare `-p`) — else AC1 fails; [2] handoff returns an explicit
disposition {ready|complete|blocked}, never a None sentinel (a blocked-but-incomplete epic must
stay OPEN); [3] ACKNOWLEDGED handoff (`handoff_pending` + generation id, claimed by the successor;
takeover-failure detected by the launcher's staleness re-fire + escalation) — pre-launch checks
alone don't detect a post-end launch failure; [4] EVERY terminal child outcome (merged/deferred/
abandoned) ends the session, not just merged; [5] TaskList is session-scoped — a fresh `-p` session
builds its OWN list from `.driver-state`, progress persisted in state not the list; [6] launcher
flock + handoff generation give the exactly-one-successor singleton.

## §1 Problem

`/rawgentic:epic-run` drives WF2 over an epic's children sequentially (`skills/epic-run/SKILL.md`
Step 4: "one child at a time, WF2 fresh per child"). But **"fresh WF2" ≠ "fresh session"**: the
whole run lives in ONE Claude Code process, driven by the `/goal` Stop-hook re-injecting into the
SAME process. Context accumulates across children — by the Nth child the window carries N children
of design→impl→review→CI→merge turns, trimmed only by compaction (which can summarize away detail
the current child needs). Cost/attribution is muddied (all children share one session).

This feature: give the driver a fresh **session** (process) boundary per child. When child N
completes (merged+closed, WF2 Step 16 done, epic box ticked), child N+1 starts in a NEW `claude`
process whose context contains NONE of child N's turns.

## §2 Verified facts

- The continuity substrate ALREADY EXISTS and is durable: `claude_docs/.driver-state/<campaign>.json`
  (status machine `queued→in_progress→{pr_open|merged|deferred|abandoned}`, `driver_lib.py:41`),
  `driver_lib.next_ready_issue(state, deps_satisfied_by="merged")` (`:232` — picks the next child
  from state alone), `topo_sort_issues` (`:170`), epic checkboxes (one-way state→epic), and the
  append-only `claude_docs/session_notes/epic-<N>-autorun-log.md`. A fresh session rebuilds the
  whole run position from these files — no in-context memory needed. (Confirmed by reading
  driver_lib.py + epic-run/SKILL.md.)
- **Prior art crosses the process boundary already, LIVE:** `epic475-resume.sh` (the
  `long-run-resume` system-crontab launcher) starts `claude` sessions that re-bind the project,
  `git fetch`, read the goal/decision-log, find the current position, and continue — this very
  #569 run is executing under it. The launcher is the proven, durable relaunch vehicle
  (`.claude/skills/long-run-resume/SKILL.md`: system-crontab is the DEFAULT; in-session CronCreate
  does NOT survive a quota pause).
- `epic-run` Step 2 ALREADY recommends arming that durable launcher at run start.
- Fresh context requires a FRESH `claude -p "<prompt>"` (NO `--resume`): `--resume <id>` reloads the
  prior session's transcript (full context), defeating AC1. A bare `-p` run driven by durable state
  starts with an empty window. (The launcher currently prefers `--resume`; #569's fresh-session mode
  uses the fresh `-p` variant the launcher already has as its fallback.)

## §3 Mechanism decision (AC5)

**The durable system-crontab launcher is the process-boundary vehicle; the driver ends its session
after each merged child, and a fresh `claude -p` (no `--resume`) session picks up the next child
from durable state.** Chosen over the two alternatives:
- *Driver spawns `claude -p` as an in-session subprocess per child* — rejected: a subprocess parented
  by the current session dies with it (quota pause / teardown), and it nests processes; the launcher
  is external + already the durable survival layer (long-run-resume's hard lesson).
- *Keep single-session + rely on compaction* — rejected: that IS the status quo the issue fixes.

Flow (per child):
1. Session S_n runs child N's WF2 to Step 16 and reaches a **terminal** child outcome — `merged`
   (auto-merge mode: verify merge SHA + issue auto-close + tick the epic box) OR `deferred`/
   `abandoned` (blocker: ERROR comment posted, per AC3). It updates `.driver-state` (child N → its
   terminal status) + the autorun-log. **[4] The session ends on ANY terminal outcome, not only
   merged** — a blocked child must not leave its accumulated context to bleed into an independent
   ready successor.
2. S_n computes `disp = fresh_session_handoff(state, mode)` (§4). If `disp.outcome == "ready"` AND
   fresh-session mode is armed AND the boundary is crossable (§5), S_n writes `handoff_pending`
   (generation id + the successor issue) and **ENDS** — it does NOT start the successor. If
   `disp.outcome == "complete"` → S_n does Step 5 wrap-up (close epic). If `disp.outcome ==
   "blocked"` (unmerged children remain but none are ready — all deferred/abandoned/dep-blocked) →
   S_n leaves the epic OPEN with an honest summary and ENDS (no relaunch — nothing is runnable;
   **[2] never mistaken for "complete"**).
3. The armed durable launcher, seeing `session_mode == "fresh-session"`, starts a FRESH
   `claude -p "<prompt>"` **with NO `--resume`** (**[1]** — a `--resume` reloads S_n's transcript and
   fails AC1) → session S_{n+1}, empty context. Under its flock singleton (**[6]**) S_{n+1}
   atomically CLAIMS `handoff_pending` (matching the generation id; a duplicate/early fire finds it
   claimed and exits), then: `/rawgentic:switch`, `git fetch origin`, read `.driver-state` +
   autorun-log + epic, `next_ready_issue(state)` → child N+1, run its WF2 fresh (AC1: none of child
   N's turns present — verifiable by inspecting S_{n+1}'s transcript at child start).
4. Repeat until `fresh_session_handoff` reports `complete` → Step 5 wrap-up.

## §4 The fresh-session handoff + arming (new `driver_lib` surface)

New pure helper `driver_lib.fresh_session_handoff(state, *, mode) -> dict`:
- `mode != "fresh-session"` → `{"outcome": "single_session"}` (no boundary; the driver loops
  in-session exactly as today: **byte-identical default, zero behavior change unless opted in**).
- Else returns an explicit **[2] disposition** (never a bare None):
  - `{"outcome": "ready", "next_issue": n, "generation": <int>, "resume_prompt": <str>, "campaign": <str>}`
    when `next_ready_issue(state)` yields a runnable child. `generation` is a monotonic counter
    persisted in `.driver-state` (incremented per handoff — the **[6]** claim token). `resume_prompt`
    is the canonical idempotent, state-re-deriving prompt (per SR1: used for the interactive
    hand-back + the direct-spawn path; the crontab launcher's own prompt conforms to it).
  - `{"outcome": "complete"}` ONLY when EVERY child is `merged` (or closed) — the sole
    epic-close trigger.
  - `{"outcome": "blocked"}` when unmerged children remain but none is ready (all
    `deferred`/`abandoned`/dependency-blocked) — the epic stays OPEN, honest summary, no relaunch.
    **[2] `complete` and `blocked` are distinct; a blocked-incomplete epic is never closed.**
  `outcome` is computed from the status counts (`complete` = no non-merged/closed children;
  `ready` = a `next_ready_issue` exists; else `blocked`).

**Prompt vs launcher (self-review SR1).** Under the chosen crontab-launcher mechanism (§3) the
launcher fires its OWN static generic prompt, which ALREADY re-derives the position from durable
state ("find the current position … else the first child whose deps are all merged and not yet
merged/closed" — exactly the epic475-resume.sh prompt this run uses). So on the crontab path the
driver's job is only to **write the merged state + END**; the launcher + `next_ready_issue` drive
the next session — the handoff's `resume_prompt` is NOT injected there. `resume_prompt` is the
CANONICAL idempotent prompt for (a) the interactive hand-back (printed for the operator) and (b) a
direct `claude -p` spawn where no static launcher prompt exists; the crontab launcher's own prompt
is contract-required to CONFORM to it (re-bind, fetch, `next_ready_issue`, never re-do a merged
child, restate the grant). This removes the double-source-of-truth: state is authoritative; the
prompt is a conforming driver, not a second position record.

Arming (`mode`): fresh-session mode is opt-in per run, set at epic-run Step 2 alongside the
merge-policy decision, and recorded in `.driver-state` as `session_mode: "fresh-session" |
"single-session"` (absent → `single-session`, the safe default). The durable launcher must be armed
(long-run-resume) for fresh-session mode; Step 2 already recommends it.

## §5 Fail-open (AC6) — the load-bearing safety rule

**Pre-launch check.** If fresh-session mode is armed but the boundary cannot be crossed — no durable
launcher detected (no crontab entry / handoff-path not writable / launch mechanism absent) — the
driver DEGRADES to the current single-session loop (continue to child N+1 in the SAME session) and
emits ONE visible marker `### epic-run: fresh-session unavailable — single-session fallback
(<reason>)`. Worst case = exactly today's behavior. `driver_lib.fresh_session_available(state) ->
(bool, reason)` is pure over injected probes (launcher-armed flag + handoff-path writability), tested.

**[3] Post-end takeover-failure detection (the real gap the pre-launch check misses).** After S_n
ends, the launch can still fail (cron removed mid-run, CLI error, timeout) with no in-session
component watching. The acknowledged-handoff design closes this: S_n writes `handoff_pending =
{generation, next_issue, written_ts}` to `.driver-state`; the successor S_{n+1} ATOMICALLY CLAIMS
it (sets `handoff_claimed = generation` under the launcher flock) before running. Takeover failure
is then observable durable state: a `handoff_pending` whose `generation` is still unclaimed on a
later launcher fire ⇒ the prior takeover failed. The launcher's existing **staleness re-fire**
(every ~20 min on no-transcript-append/no-commit — long-run-resume) IS the retry: it re-launches,
which re-claims the pending handoff. Escalation: after `HANDOFF_RETRY_CAP` (e.g. 3) unclaimed
re-fires for the same generation, the launcher notifies the owner (notify-owner) and stops
re-firing that generation — a stranded run surfaces instead of silently dying. A quota pause is NOT
a failure (expected; the next post-reset fire claims the still-valid pending handoff). This is the
long-run-resume durable-layer reliability boundary, inherited — #569 adds the claim/generation +
escalation on top. `driver_lib` exposes `handoff_claim(state, generation) -> (ok, state)` (pure;
idempotent; a second claim of a claimed generation returns `ok=False`) so the singleton is tested.

## §6 Continuity invariants preserved across the boundary (AC2/AC3/AC4)

- **AC2:** queue, topo order, merge-policy, per-child running record, and the decision log are read
  from `.driver-state` + `epic-<N>-autorun-log.md` by the new session — never from in-context memory.
  `validate_driver_state` (`driver_lib.py:272`) gate-checks the state a fresh session loads.
- **AC3:** a blocked child still gets its ERROR blocker comment; its state → `deferred`/`abandoned`;
  the fresh session's `next_ready_issue` skips it and continues; the epic stays OPEN with an honest
  summary; the run never hangs (the boundary does not change blocker semantics — the next session
  just reads `deferred` and moves on).
- **AC4 [5 correction]:** the harness Task tools are **session-scoped** — a fresh `-p` process does
  NOT see a prior session's TaskList (unverified-and-likely-false to assume otherwise). So the
  authoritative run record is `.driver-state` (statuses) + the autorun-log, NOT the task list. Each
  fresh session BUILDS ITS OWN task list from `.driver-state` at Step 3b (marking already-`merged`
  children completed from state, the active child in_progress) — the "check TaskList first" rule
  still prevents a DUPLICATE *within* a session; across the boundary each session's list is a
  fresh render of the same durable state. No cross-session TaskList visibility is claimed or
  required. (Doc-note this in SKILL.md Step 3b; no reliance on carrying a list across the boundary.)
- **Driver still cannot weaken a WF2 gate:** each child runs WF2 FRESH to Step 16 in its own session;
  the driver never reaches into a WF2 step (unchanged contract).

## §7 platform_apis feasibility (#226)

platform_apis:
- api: `claude -p "<prompt>"` fresh headless session launch (no --resume) + system-crontab relaunch
  feasibility: verified via existing-call-site — `epic475-resume.sh:71-80` (the running launcher:
  `timeout "$SESSION_WALL" "$CLAUDE_BIN" --print --permission-mode bypassPermissions --model ... "$PROMPT"`,
  with the `--resume`-fails→fresh-`-p` fallback at :76-80); this #569 run executes under it live 2026-07-22.
  failure: fail-loud
  surface: the driver's §5 fail-open marker + the launcher log line per relaunch decision.

**[1] Launcher contract change (IN this PR as CONTRACT; the .sh wiring is a deferred follow-up).**
The evidenced launcher tries `--resume` FIRST and only reaches the fresh `-p` on resume-failure — so
today it would reload S_n's context and fail AC1. The fresh-session contract (documented in
`docs/multi-issue-driver.md` + the `long-run-resume` skill): **when `.driver-state.session_mode ==
"fresh-session"`, the launcher MUST skip the `--resume` attempt and invoke `claude -p` with NO
session id.** The actual edit to `epic475-resume.sh` (and the `long-run-resume` template) lives
OUTSIDE this repo — it is a deferred owner-attended follow-up (exactly the #568-Phase-1 precedent
where the launcher resume-glue shipped as a documented follow-up). This PR ships the contract + the
driver_lib support + the docs; the single-session default path is unaffected, so nothing regresses
before the launcher is updated. Until then, fresh-session mode's pre-launch check (§5) sees the
unmodified launcher and DEGRADES to single-session with the visible marker — safe.

## §8 Design self-note / risks

- Risk (issue): a broken handoff strands a run mid-epic. Mitigated by AC6 fail-open to single-session
  + AC2/AC3 continuity read from validated durable state + tests.
- The fresh-session prompt is the ONLY thing crossing the boundary besides the durable files — it
  must be idempotent + re-derive state (never carry a child-specific in-context assumption).
- Attention: `next_ready_issue` uses `deps_satisfied_by="merged"` — a fresh session must `git fetch`
  and confirm the predecessor actually merged on origin/main before treating a dep as satisfied
  (the state says merged; the new session verifies against main — the existing between-children
  merge-verification rule, SKILL.md:101-104, now spans the boundary).

## §9 Acceptance criteria (this PR)

1. Fresh-session mode: after a merged child, the driver writes the handoff + ends; a fresh `-p`
   session resumes the next child with none of the prior child's turns (documented + the handoff
   helper unit-tested; the live cross-session transcript check is the owner-attended cell).
2. Continuity from durable state only (helper reads state; `validate_driver_state` gate).
3. Blocker protocol preserved across the boundary (deferred/abandoned child skipped by next session).
4. Task list refreshed not duplicated (existing rule; doc note for the cross-session path).
5. Mechanism documented in `skills/epic-run/SKILL.md` + `docs/multi-issue-driver.md`; driver still
   structurally cannot weaken a WF2 gate.
6. Fail-open: unavailable launch mechanism → single-session fallback + visible marker; never aborts.
7. `session_mode` absent/`single-session` → byte-identical to today (opt-in; zero default change).
8. Version ×4 + no phase_executor change; README changelog; no WF2-spine change → no diagram REV
   (epic-run is the driver, not a WF2 station).

## §10 Task sketch (Step 5 refines)

T1 `driver_lib.fresh_session_handoff(state, *, mode)` → disposition {single_session|ready|complete|
   blocked} + `fresh_session_available(state, *, probes)` + `handoff_claim(state, generation)` (all
   pure, red-first) + tests (every disposition; complete≠blocked; claim idempotency) ·
T2 `.driver-state` fields `session_mode` + `handoff_pending{generation,next_issue,written_ts}` +
   `handoff_claimed` + `generation` counter; `validate_driver_state` tolerance (schema stays
   backward-compatible — absent fields = single-session) + tests ·
T3 epic-run/SKILL.md Step 2 (arm the launcher + mode choice) + Step 4 (terminal-outcome boundary,
   acknowledged handoff, per-session task list, fail-open) prose ·
T4 docs/multi-issue-driver.md (cross-session lifecycle + the [1] launcher-skip-resume contract +
   [3] takeover-failure/escalation + fail-open) ·
T5 versions ×4 + README changelog + design doc md+html.

Deferred (#138): (a) the live cross-session transcript check (AC1 "none of prior child's turns") —
owner-attended, needs a real ≥2-child epic run under the updated launcher; (b) the actual
`epic475-resume.sh` / `long-run-resume` template edit to skip `--resume` in fresh-session mode
(outside this repo — #568-Phase-1 launcher-glue precedent). Local proxy for both: the pure
handoff/claim/available helpers + their unit tests + the running epic475 launcher as the fresh-`-p`
existence proof. Target check: run a 2-child epic with `session_mode=fresh-session` after the
launcher edit; inspect S_2's transcript at child start.

## §11 Disposition log (Step-4 adversarial, gpt-5.6-sol — all ADOPTED into r2)

- F1 [H] launcher `--resume`-first defeats AC1 → ADOPTED §7: contract forces bare `-p` in
  fresh-session mode; .sh wiring = deferred follow-up (#568-P1 precedent).
- F2 [H] `next_ready_issue` None ambiguous → ADOPTED §4: explicit {ready|complete|blocked}; blocked
  never closes the epic.
- F3 [H] fail-open only pre-launch → ADOPTED §5: acknowledged handoff (generation + claim) +
  staleness re-fire retry + escalation after HANDOFF_RETRY_CAP.
- F4 [H] boundary only after merged → ADOPTED §3: every terminal outcome (merged/deferred/abandoned)
  ends the session.
- F5 [M] TaskList session-scoped → ADOPTED §6/AC4: each session builds its own list from
  `.driver-state`; no cross-session TaskList visibility claimed.
- F6 [M] no singleton proof → ADOPTED §3/§5: launcher flock + handoff generation claim.
