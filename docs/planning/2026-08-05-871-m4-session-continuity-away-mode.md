# #871 M4 — Session continuity & unsupervised modes: one cohesive plan

**Date:** 2026-08-05 · **Authors:** owner + Fable 5, gpt-5.6-sol as consultant · **Status:** for owner review
**Epic:** #871 (reshaped by this design) · **Supersedes:** the M2/M4 split of #927, #769, #726, #586

```stats
5 | mechanisms unified
2 | unsupervised modes: away · sleeping | accent
7 | ACs (5 owner + 2 suggested)
7 | wave children (2 new issues)
3 | RAWGENTIC_HEADLESS stopgap reads retired
```

```callout
ok | Owner decisions already made this session (2026-08-05)
Fold the overlapping M2 children (#927, #769, #726, #586) into this M4 wave — the other ten #906
children resume after. Blockers: AWAY asks by text and waits min(return time, 20 min); SLEEPING
decides immediately. Revalidate hardening = the claim-coverage gap + the obsolete-child owner
gate. M2.5 is sliced: #888 (exactly-once run-records) rides this wave; #363/#356 stay behind.
```

## 1. The problem, in plain words

rawgentic has five separately-built mechanisms that all exist to keep long work going past the
limits of one session, and they do not compose. The owner has had to untangle the same confusion
repeatedly — most recently: "you keep getting confused that single-session mode means don't run
pane-handoff."

Measured incidents, each a real run:

- **Epic #875 (2026-08-05):** driver-state said `single-session`, so after #761 merged the run
  went straight into the next child in the same exhausted session. No trigger fired; none was due.
  The owner had to intervene. (#927's filing evidence.)
- **Epic #906 (this run):** the owner wanted a pane-handoff chain (the epic is literally named
  "pane-handoff chain"), but the campaign was armed `single-session` — so every boundary handoff
  is done by hand with a hand-written prompt, outside the driver's own machinery, and the
  fresh-session gate machinery (receipts, dispositions) is driven manually.
- **A `single-session` campaign has NO sanctioned boundary handoff at all** (#927's table): the
  `handoff` command is gated on `fresh-session` mode, `mid-child-handoff` refuses at a boundary
  (`no_active_child`), and ad-hoc `pane-handoff`'s own skill says "not for mid-campaign epic runs."
- **The stop-hook goal loop nags an honestly-paused run** (this session, 3 consecutive stop-hook
  prods during an owner-ordered pause) and, symmetrically, **stalls an unsupervised run** on
  questions nobody is present to answer (D36, thewanderinginn epic #7: the stop hook fired eleven
  times demanding the epic finish while the contract said the session must end).
- **Overnight resumes silently fail** (#586, owner report 2026-07-22): the cron launcher pins a
  session ID at arm time; `/clear` mints a new one; `--resume <old-id>` errors. And the launcher
  polls blind on a `*/20` staleness heuristic instead of knowing when the 5-hour window actually
  resets.
- **Plugin refresh (D183) and the learnings sweep (D181) live in handoff-doc prose**, not in any
  skill or command — each new successor must be told by hand, and this run's successor was.

## 2. What exists today (component inventory)

| Mechanism | Home | What it does | Trigger |
|---|---|---|---|
| epic-run driver | `skills/epic-run` + `driver_lib`/`launcher_lib` | sequence WF2 over children; `session_mode: single-session \| fresh-session` (default single) | operator |
| fresh-session boundary | `launcher_lib handoff` | pane split + successor launch + predecessor retirement, receipt-gated | `fresh-session` mode only |
| mid-child-handoff | `launcher_lib mid-child-handoff` | context-pressure handoff DURING a child; generation/claim fence | context meter |
| ad-hoc pane-handoff | `skills/pane-handoff` | one-shot successor with bind/prompt/goal verification | user ask, or meter tiers |
| context meter | `hooks/context_meter.py` | 55/75 thresholds → advisory/directive handoff; reads `RAWGENTIC_HEADLESS` as "unattended" stopgap | statusline bridge |
| revalidate-children | skill + `rebuild-receipt` | re-check queued children's claims after every merge; receipt gates next-child | driver gate |
| plugin-refresh | workspace skill `refresh.sh` | reinstall plugin so successor loads the merged build (D183) | owner instruction, prose |
| long-run-resume | workspace skill + `overnight-resume.sh` | system-cron relaunch on staleness; pins session ID (#586) | cron `*/20` |
| goal / stop hook | harness `/goal` | re-prompts the session until the DONE condition is met | every turn end |
| goal-read | skill + `launcher_lib read-goal-condition` | reads the ARMED goal's condition verbatim from a verified source (transcript `goal_status` rows) — what pane-handoff's byte-identical goal carry and every status report stand on | pane-handoff, status reports, teardown validation |

Three `RAWGENTIC_HEADLESS` stopgap reads (context_meter, scanner_bootstrap, setup 2e) survive from
the deleted headless machinery, by owner decision D184 — #871 exists to replace them.

## 3. The unified model

Two orthogonal axes, named plainly:

**Axis 1 — supervision state** (who is watching): `attended` (default) · `away` (owner absent but
reachable by phone) · `sleeping` (owner unreachable until a stated wake time). Declared, never
inferred; one workspace-level state file; an owner message always means attended again.

**Axis 2 — session continuation** (how work outlives one context window / one usage window):

- **child boundary** → pane-handoff chain (fresh successor pane per child) — the DEFAULT whenever
  herdr is present (owner AC 1);
- **mid-child context pressure** → the meter's two tiers → mid-child-handoff (unchanged);
- **usage-window pause** → the durable launcher resumes at the MEASURED reset time (owner AC 3).

Every mechanism in §2 slots into exactly one cell of that model. The confusion existed because
"single-session" conflated axis-2 policy with axis-1 absence, and because the default fought the
owner's standing intent.

### 3.1 Supervision state — the away/sleep core (new work)

- One state file (workspace-level, e.g. `claude_docs/.supervision.json`) — normative shape
  (review finding 8): `{schema_version, revision, state: attended|away|sleeping,
  until: <ts|null>, declared_at, declared_by_session, governed_campaign_ids: [...],
  consult_grant: {providers: [...], granted: bool}}` — written by a small skill
  (`/rawgentic:away`, `/rawgentic:sleeping <wake time>`, `/rawgentic:back`), readable by every
  hook. A campaign may carry its own override field, which wins for that campaign. **Expiry is
  explicit:** past `until`, the effective state is `attended-overdue` — hooks treat it as
  attended for new blocker routing (no more sleeping-mode decisions on an expired declaration)
  while the owner-return report still leads with everything decided during the declared window.
  `waiting_for_owner` / `waiting_for_reset` live in CAMPAIGN state (driver-state fields), not
  here — supervision says who is watching; the campaign says what it is waiting for. Replaces
  all three `RAWGENTIC_HEADLESS` reads (#871 AC 5). `RAWGENTIC_HEADLESS` dies after cutover.
- **Departure protocol** (#871 AC 2): on declaring away/sleeping, the session sweeps the active
  run for decisions that could block unsupervised progress and asks them via `AskUserQuestion`
  BEFORE the owner leaves.
- **Blocker routing** (owner decision this session): AWAY → two-way `ask-owner` text, bounded wait
  of `min(declared return, now + 20 min)`, then decide + log; SLEEPING → decide + log immediately;
  one-way `notify-owner` only for run-fatal events. Both: decision log entry with an undo; the
  next report leads with decisions made without the owner.
  **Messaging capability contract (review finding 2):** the transports are the existing workspace
  skills — `ask-owner` (two-way BlueBubbles) and `notify-owner` (one-way) — and the supervision
  core names their exact invocations. The 20-minute timer starts only after CONFIRMED submission;
  a send failure or an uncorrelatable reply is logged visibly and the item enters
  `waiting_for_owner` — a delivery failure must never masquerade as "the owner chose not to
  answer" and trigger an autonomous decision on a false premise.
  **Owner-only exemption (review finding 6):** `pending_disposition: issue_obsolete` — and any
  other item the design marks owner-only — is EXEMPT from AWAY's decide-after-timeout rule: on
  timeout it is deferred when dependency-safe, else the campaign enters `waiting_for_owner`.
  Only an owner write-back clears it.
- **Self-unblocking** (#871 AC 3): while unsupervised, use codex/glm consults (peer-consult /
  adversarial-review runner) to break ties before deciding. **Consult egress grant (review
  finding 7):** consults transmit artifact/repo text off-box, so unsupervised consulting is
  conditional on an explicit pre-departure grant (part of the departure preflight; recorded in
  the supervision state as `consult_grant`) naming the allowed providers. Without the grant, or
  on any runner/auth failure: no external send — log the unavailable consult and follow the
  local-decision or `waiting_for_owner` rule instead.
- **Goal interplay (review finding 1 — three outcomes, not two):** the goal/stop-hook contract
  distinguishes `complete` from `paused_waiting_for_owner` and `paused_waiting_for_reset`. ONLY
  `complete` may write the resume launcher's done marker or authorize teardown; either paused
  outcome releases the current session while the armed launcher and campaign state persist. The
  goal-condition template for unsupervised runs carries this clause; the campaign state fields
  give the evaluator something checkable (pattern proven by this run's pause, where the binary
  contract produced three stop-hook nags on an honest wait).

### 3.2 Epic-run rework (owner ACs 1 + 2; absorbs #927, #769, #845-fold)

- **Step 2 asks exactly two questions** (AC 2): (a) merge policy (auto-merge scoped grant vs
  PR-only), (b) arm the 5-hour resume launcher (recommended yes). The session-mode question DIES.
- **Boundary mode is derived, not asked** (AC 1; wording unified with §5 per review finding 5):
  at campaign creation, record `preferred_transport: pane_chain` only after a herdr capability
  PROBE succeeds (`HERDR_ENV` alone is a hint, never proof — the probe is `select-mode`-style:
  herdr reachable, launcher present, fresh-launch supported); otherwise `inline`. Before EVERY
  boundary and resume, repeat the probe and record that transition's `effective_transport` — an
  `inline` transition under a `pane_chain` preference is an explicit, visible, temporary
  degradation, and the next boundary re-probes. `session_mode` is migrated once
  (`fresh-session` → `pane_chain`, `single-session` → `inline`) and runtime code stops reading
  it. The launcher keeps deferring to the recorded preference (#927 AC 3 preserved; #611 stays
  fixed) — what changes is that the RECORD is a preference plus per-transition reality, never a
  stale permanent answer.
- **In-flight mode change** (#927 AC 2): sanctioned command flips a campaign's mode at the next
  boundary; refused mid-child.
- **Exactly-one-successor fence at the boundary** (the #845 close-or-fold obligation lands here):
  the fresh-session boundary gets the same claim/fence discipline mid-child-handoff already has.
- **The child-boundary contract becomes explicit in the skill**: merge-verify → record-child-outcome
  → revalidate-children → learnings sweep (#769's 5-part procedure, mechanized state so a
  successor knows whether sweep N ran) → plugin-refresh when the project is rawgentic (D183
  codified; `refresh.sh` gains the marketplace-update step measured this run) → pane-handoff with
  a launcher-GENERATED successor prompt (driver-state position, task-list-rebuild instruction —
  the #819 prose rules — baked into the template, no hand-authored prompts).

### 3.3 Resume that survives the 5-hour window (owner AC 3; absorbs #586)

Confirmed mechanics (triangulated: official statusline schema via context7, claude-code-guide
agent on docs, prior art `kristofferR/smart_resume`; CLI `--help` verified live on this host):

- The statusline JSON payload carries `rate_limits.five_hour.{used_percentage, resets_at}` —
  `resets_at` is a Unix epoch. The existing statusline bridge appends it to a small per-session
  state file (the #654 pattern, ~3 lines). **Spike first (review finding 3):** the docs prove the
  schema, not that THIS host's configured bridge (`~/.claude/rawgentic-statusline.sh`) receives
  the field and can write the state file — #586's first AC is a live capture through the real
  bridge. When the field is absent or the write fails, the launcher logs it visibly and the
  campaign enters `waiting_for_reset_unmeasured` — the `*/20` watchdog then carries the resume
  (the old behavior), stated rather than silently substituted. The one-shot scheduler is named at
  implementation (system crontab one-shot entry that self-removes, or `at` if present) and spiked
  alongside.
- `claude -p --continue` resumes the MOST RECENT conversation in the cwd — no pinned session ID,
  which is the #586 fix. Idempotent fresh `-p` prompt stays as the fallback.
- The launcher becomes: **one-shot resume armed at `resets_at + 1 minute`** (measured, not
  guessed; the fixed 60 s allows for reset lag — owner decision 2026-08-05) + the existing `*/20`
  staleness watchdog as belt-and-braces + done-marker/flock/launch-cap unchanged. Headless `claude -p` emits no reset time on 429 — the statusline bridge is the one
  channel, so the bridge write must happen while the session is still healthy.
- **Pane-aware resume (RESOLVED at consult, §5):** the launcher re-resolves the effective
  transport at resume time — herdr usable and the campaign prefers `pane_chain` ⇒ resume INTO a
  herdr pane; otherwise resume headless, marked as an explicit degraded transition, and the next
  safe boundary re-enters the pane chain. Receipt gates are transport-independent and stay
  authoritative on both paths. Before any `--continue`, the campaign-identity check (§5 item 2)
  must pass; otherwise the generated fresh `-p` prompt is used.

### 3.4 Revalidate-children hardening (owner AC 4, as scoped this session)

- **Coverage gap:** a mechanical claim inventory over the issue body, bound claim-by-claim to the
  receipt, so a `deep` stamp that skipped claims is refused instead of trusted (closes the
  documented "depth is your obligation" hole).
- **Obsolete-child owner gate** (#848 lineage, rebuilt): a `pending_disposition: issue_obsolete`
  child cannot be handed out until an owner write-back clears it. Supervision-aware: attended/away
  → ask-owner; sleeping → the child is deferred with the ERROR-comment protocol and the run
  continues.
- Explicitly NOT in scope (owner declined): merging the whole boundary into one script, and
  cheaper passes.

### 3.5 Records-first minimum (owner decision: M2.5 sliced)

**#888** (run-records that land exactly once, persist-before-merge ordering) ships in this wave,
before the first real unsupervised run under the new mode. #363 and #356 stay in M2.5.

## 4. The M4 wave — issue breakdown (proposed)

Reshaped epic #871 task list, in execution order (sequencing settled at consult, §5):

```phases
M4 — SESSION CONTINUITY & UNSUPERVISED MODES | epic #871 · next up | crit
  #888 | Records first: run-records that land exactly once (moves up from M2.5) | note
  NEW | Supervision-state core: away/sleep/attended declaration, departure sweep, blocker routing, replaces RAWGENTIC_HEADLESS ×3 | crit
  #927 | Epic-run rework: two-question setup, transport derived from herdr, mode-change command, #845 fence | crit
  #769 | Child-boundary learnings sweep, mechanized state, folded into the boundary contract | note
  #726 | In-flight-work gate + durable-path check (scope as filed) | note
  #586 | Resume rewrite: measured resets_at one-shot + --continue with identity check + pane-aware resume | crit
  NEW | Revalidate hardening: claim-inventory coverage binding + obsolete-child owner gate | note
M2 — REMAINING TEN | #906 resumes after the wave | note
  Q | #835, #797, #729, #734, #864, #772, #878, #806, #923, #899 — order re-derived at resume | note
```

```legend
crit | the surfaces that have burned real runs
note | scheduled in the wave
```

Two NEW issues total; #931 (sandbox trial) stays attached to the epic as the decision ticket
feeding the away-mode slice. The remaining ten #906 children resume as M2 after this wave.

## 5. Consult outcomes (gpt-5.6-sol peer proposal, 2026-08-05 — adopted / rejected)

Report: `docs/reviews/peer-2026-08-05-871-m4-session-continuity-away-mode-2026-08-05.md`.

**Adopted into this design:**

1. **`preferred_transport` + `effective_transport` replace `session_mode`** (answers old open Q3):
   the campaign records its preference at creation (herdr present ⇒ `pane_chain`); every boundary
   and resume RE-RESOLVES capabilities and records the effective transport for that transition.
   Losing herdr is an explicit per-transition degradation with a visible marker — never a silent
   permanent mode change. Old `session_mode` values map: `fresh-session` → `pane_chain`,
   `single-session` → `inline`.
2. **Campaign-identity check before `--continue`** (hardens §3.3; mechanics pinned per review
   finding 4): the newest conversation in the cwd can be UNRELATED work — concurrent sessions
   share this workspace root. The check is concrete: the campaign records its session LINEAGE
   (each session's id, appended at bind/handoff) in driver-state; the launcher enumerates
   `~/.claude/projects/<cwd-slug>/*.jsonl` by mtime and requires the NEWEST transcript's session
   id to be the campaign lineage's tail. `/clear` mints a new id, which the resumed session's
   bind appends — so lineage stays current. Because `--continue`'s own selection cannot be
   queried before launch, the rule is conservative: any mismatch, tie within the staleness
   window, or unreadable enumeration ⇒ launch the generated fresh `-p` prompt from the durable
   checkpoint (position, receipts, pending decisions, successor instructions) instead.
   `--continue` is the optimization, never the authority, and the fallback is tested under
   concurrent sessions.
3. **Away wait = `min(declared return time, now + 20 min)`** (answers old open Q4).
4. **Terminal-for-now states for the goal loop:** `waiting_for_owner` and `waiting_for_reset`
   recorded in supervision/campaign state, referenced by the goal-condition template, so the Stop
   hook reads an honest wait instead of nagging (this session measured 3 stop-hook prods during an
   owner pause) or stalling an unsupervised run (D36).
5. **Reset-observation sanity checks:** persist `resets_at` with its observation time; reject
   stale/implausible epochs (freshness, range, monotonicity); launch cap and flock stay as final
   guards against a resume storm.
6. **Predecessor survives until successor acknowledgement** at the plugin-refresh seam, and the
   previous cached plugin version is retained as rollback — a failed refresh can no longer strand
   a campaign with no usable successor.
7. **Autonomous authority is bounded SEPARATELY from supervision state:** sleeping never widens
   permissions; merge authority comes only from the epic-run Step-2 grant; destructive/outward
   actions outside the granted set notify and enter `waiting_for_owner` regardless of mode.
8. **Departure preflight enumerates affected runs:** the away/sleeping declaration lists every
   live campaign it will govern, and asks the blocker-sweep questions per run before the owner
   leaves (#871 AC 2, sharpened).
9. **Test harness without real 5-hour waits** (answers old open Q6): injected clock, synthetic
   statusline payloads, fake scheduler, fake herdr adapter; crash-point matrix over
   supervision × transport availability × boundary checkpoint × duplicate launcher.
10. **Obsolete-child deferral must respect dependency order** (§3.4): a sleeping run defers an
    obsolete-marked child only when no remaining child depends on it; otherwise the run parks with
    an honest terminal-for-now state.

**Rejected, with reasons (D179 honesty):**

- **The full event-log + outbox coordinator.** Driver-state, receipts and the handoff ladder
  already carry this state; a parallel event-sourced store contradicts the roadmap's
  "no new enforcement state machines" ruling (§7) and would be a second source of truth to drift.
  The adopted pieces above are grafted onto the EXISTING driver-state schema (new fields, not a
  new store).
- **Owner-authenticated supervision events.** Single-owner host; the declaration skill writing a
  workspace state file is sufficient. Session IDs stay observations, as the peer said.

**Sequencing (old open Q5, settled by the peer's slice logic + owner's M2.5 answer):**
#888 records-first → supervision core → epic-run/boundary rework → resume rewrite → revalidate
hardening. #726 rides with the boundary rework wave position it already holds.

## 6. Review provenance

gpt-5.6-sol adversarially reviewed this plan (WF5, 2026-08-05): 8 findings — 7 High, 1 Medium,
0 Critical — all confirmed by the orchestrator and ALL FIXED IN THIS DOCUMENT before
implementation (D179 disposition: fix): three goal outcomes instead of a binary (finding 1),
the messaging capability contract with confirmed-submission timers (2), the statusline spike +
`waiting_for_reset_unmeasured` fallback (3), concrete session-lineage identity mechanics (4),
the §3.2/§5 transport contract unified around a capability probe (5), the owner-only exemption
from AWAY's decide-after-timeout (6), the pre-departure consult egress grant (7), and the
normative supervision schema with expiry semantics (8). Report:
`docs/reviews/2026-08-05-871-m4-session-continuity-away-mode-md-2026-08-05.md` (local, gitignored
by design). Earlier peer consult: same reviewer identity, §5.

## 7. Supervision state — placement

Workspace-level (away-ness is a property of the human, not a project), with the peer's two
refinements: the declaration enumerates the campaigns it governs, and a campaign may carry an
explicit override field.
