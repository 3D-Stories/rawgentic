# Rawgentic modernization — campaign log

Rolling design-artifact log for the autonomous workflow-modernization campaign
(dogfood: rawgentic builds rawgentic). One section per implemented slot, updated
in that slot's PR (shared-doc mode). The hand-curated program dashboard lives
separately at `docs/planning/2026-07-04-workflow-modernization-review.html`; this
log is the per-slot artifact the WF2 Step-12 lifecycle renders with embedded
run-record telemetry.

Milestones: **M1** instrument+guard (done) · **M2** enable+restructure (done) ·
**M3** multi-issue autonomy + v3.0.0 (done) · **M4** headless (done — pilot
shipped; live run owner-gated). M1–M4 **COMPLETE**; the **epic #188 fast-follow**
(WF2 hardening + epic-native workflows + OAuth Action reviews) is now in progress.

---

## Epic #906 M2 — #734: the context meter says when it is blind · v3.141.5

**Issue:** [#734](https://github.com/3D-Stories/rawgentic/issues/734) ·
**Design:** brief note, small-standard lane (no separate design doc)

**The issue's headline fix was falsified, and the run said so instead of building it.** #734 was
filed on the reading that the meter latches an unresolved transcript and never recovers. It does
not. `resolve_transcript` runs fresh on every non-throttled check, after the throttle, and the
`transcript_unresolved` branch sets no suppression flag — verified against the code rather than
taken from the issue's own later revalidation comment. Two of the six acceptance criteria therefore
described behavior that already worked, so this slot pins them with regression tests rather than
changing code, and says so in the PR body so a reviewer does not have to infer it.

**What was actually broken was the reporting, in two ways.** First, `diagnostics` is append-only and
nothing ever removed an entry, so it means "happened at least once", never "is true now" — which is
why a state file frozen at turn 3 reads as permanently blind, and it is the misreading the issue was
originally filed on. `diagnostics_current` is now rebuilt every check alongside it, which is what
makes `session_unbound` stop being reported after a later bind. Second, `_diagnose` is
once-per-session, so a genuinely blind session emitted one stderr line at minute 0 and nothing for
the next 44 minutes while it consumed 368,175 tokens. A `blind_streak` counter now drives one
further warning once blindness outlives five checks, and both blind kinds count toward it because
the issue's own live reproduction was blind via `no_usage_row`, not `transcript_unresolved`.

**A third acceptance criterion was aimed at the wrong field and was re-aimed, not quietly
satisfied.** AC4 asks that `turns` advance over a session driven mainly by tool calls. `turns`
increments at exactly one site, guarded by `event == "UserPromptSubmit"`, so a tool-driven session
leaving it frozen is by design. The test drives the hook through `PostToolUse` payloads only, exactly
as the criterion asks, and pins `last_check_ts` instead. The re-aim is posted as a comment on the
issue.

**What review caught.** The cross-model pass refused the first cut's `== BLIND_STREAK_WARN` gate: a
counter arriving already past the threshold increments straight past equality and then never warns
at all, which is a fail-open on the one signal the issue exists to add, in a state file the user can
write. Now `>=` plus an explicit once-per-episode flag, so the guarantee holds for any starting
value. It also caught a constant comment claiming 5 was "one full cadence arm" when it is five
intervals. Declined with recorded reason: a High finding recommending the `/goal` send order be
inverted — that is this diff's prose only, it did not create the window, and the same
recommendation shipped as #989 and was reverted by owner decision D298 one day earlier on measured
evidence.

**Deviation, recorded.** `skills/pane-handoff/SKILL.md` carries an unrelated prose change in this PR
— a `/goal` arms only when it leads the submitted text — folded in by owner decision after the
mixed-scope concern was raised. Repo convention is one PR per issue.

Suite 6393→6405, exit 0. Both lint lanes 10.00/10. Security scan clean. 12 tests added.

---

## Epic #871 M4 — #944: claim-inventory coverage + the obsolete-child owner gate · v3.136.0

**Two documented holes, closed.** The receipt used to attest only *that a look happened and left
evidence*, never *that every claim in the body was examined* — a `deep` record carrying one
`cause` claim was structurally valid and made the child selectable regardless of what the body
actually raised. And `pending_disposition: "issue_obsolete"` gated nothing at all: the owner gate
had been cut from #840 after four consecutive review rounds broke it, and #848 (its planned
rebuild) was closed without a replacement in the D176 fold.

**AC1 — claim-inventory coverage, enforced at the REAL write path.** A mechanical inventory
(`extract_claim_inventory`) pulls citation/cause/ac claims out of the issue body; coverage is
checked via proper maximum bipartite matching (`_max_bipartite_match`, an augmenting-path
algorithm — a round-2 design review finding was that a GREEDY first-match reports false gaps when
a complete matching exists), with EXACT normalized-text equality for cause/ac (a round-2 finding:
substring matching let a short generic claim fragment spuriously cover any item containing it).
The enforcement point is `rebuild-receipt --bodies`, not the record constructor — a round-2 design
review finding was that the constructor has ZERO production callers, so a coverage check living
only there would never run on a real campaign. `--bodies` carries ONLY the raw body text; the CLI
derives `resolves` itself via real `git cat-file -e` probes against both the record's endpoints,
so nothing a caller supplies can fabricate citation coverage (a round-2 security finding on the
first draft's trust boundary).

**AC2-4 — the obsolete-child owner gate, restored.** `next_ready_issue` now refuses a child
carrying a live `pending_disposition` (`ObsoletePendingChild`, rc 11 on both `next-child` and
`handoff`), naming the `record-child-outcome --status deferred|abandoned|merged` write-back
remedy. The scan does not stop at the first marked child — it remembers the first one seen and
keeps scanning, raising only when every remaining ready child is pending-disposition or nothing is
ready at all (a round-2 finding: the original version raised at the FIRST such candidate, parking
the whole run even when unrelated ready work existed). Ordered behind the self-clearing gates
(sweep, in-flight) so a caller who hasn't cleared those sees them first. Closes a genuine
preflight/locked-commit race: a `revalidate-children` write-back can land `pending_disposition`
without touching `status`, so `child_boundary_precondition` now rechecks the receipt under the
SAME lock `_open_and_claim` already holds, and `_cmd_handoff` maps that race to the identical rc-11
payload the preflight path emits.

**Scope correction, stated plainly (D256 partially overturned by D257, Step-4 review round 2
finding 6).** The issue's own AC2 text asked for "sleeping → defer the child … and continue ONLY
if no remaining child depends on it." Round 1 accepted that as one remedy command, the same shape
every other refusal code names. Round 2 correctly pushed back: the OTHER refusal codes name a
single command for a human (or the next skill invocation) to run — they never claim the run then
continues unattended. "Defer, post the blocker, run the write-back, retry, continue" is FOUR
chained actions with no human and no orchestrator code tying them together, a materially stronger
claim. **#944 ships the mechanical rc-11 refusal only, in every supervision state, with no
automatic continuation of any kind** — attended/away/attended-overdue print an ask-the-owner
message, sleeping prints a recommendation (defer, or park if a dependent exists) that is never
executed automatically. Building the actual automation is explicitly deferred, most naturally to
#947 (blocker routing) or a dedicated follow-up.

**Reviews.** Two Step-4 design-review rounds (`gpt-5.6-sol`): round 1 found 1 Critical + 4 High
(all fixed); round 2 found 4 High + 1 Medium — the greedy-matching bug, the unreachable
constructor, substring matching, the scope-of-scan bug, and the scope-correction above — all
fixed, gate closed budget-exhausted (design 2/2) per the #798 carve-out. Step-6 plan review: 3
High + 2 Medium (1 refuted — Tasks 2+3 already drew the distinction the finding demanded in
isolation), 2 applied as plan/skill refinements, 1 fixed as a security tightening (removing the
caller-suppliable `resolves` field from `--bodies` entirely).

**Step 8a found four more real bugs in code that looked correct on first pass — the same pattern
every earlier round hit.** `_extract_section` only ever inventoried the FIRST heading matching a
synonym-group pattern (a body with both "## Problem" and a separate "## Root cause" silently lost
the second section's claims); the section extractor captured a Markdown checkbox prefix as part
of the item text, so the skill's own fully-worked example (`- [ ] X`) was not actually executable
under exact-match coverage; the rc-11 remedy text printed one line with UNQUOTED `|` characters (a
shell pipeline, not a copyable command) and omitted `--driver-state`; and `_probe_path_exists`
treated an unresolvable commit the same as a resolvable one that merely lacks the path, silently
narrowing the required citation inventory around an operational failure. All four fixed, plus a
vacuous self-comparing test and a stale "clears both clauses" sentence. **Three findings deferred
with recorded rationale** (D260-D262) rather than patched under time pressure — each conflicts
with an existing, deliberate test convention or needs its own design-level decision: a
substantiveness policy for vacuous coverage on unstructured bodies, a closing-note syntax choice
for trailing-content classification, and a live `gh issue view` fetch to close a body-authenticity
gap where a caller-supplied hash is checked only against a caller-supplied body.

**Step 11 (pre-PR review, tokenless — review-source loop-back budget exhausted, global 3/3)
independently re-confirmed all three Step 8a deferrals still open, and found 2 MORE real bugs, both
fixed** (`900bf4ad`): the sleeping+has_pending_dependents rc-11 branch printed no remedy command at
all, contradicting the change's own claim that rc 11 names it in every supervision state; and
`_probe_path_exists` validated only the endpoint commit, not each subsequent path-level probe, so
an operational failure there still read as "path absent" — rewritten to a genuine tri-state via
`git ls-tree`. Deferred-resolution exit gate (`plan_lib.assert_no_unresolved_high_deferrals`):
PASS. Step 11.5 security scan: 0 findings, gate not blocked.

**Decisions (this slot).** D255 (declined the small-standard lane — this hardens a shared
selection mechanism every future epic run depends on), D256/D257 (the scope-correction above),
D258 (a Step-6 finding fixed as a plan refinement rather than reopening the closed design gate,
loop-back budget already exhausted), D259 (this handoff used ad-hoc pane-handoff rather than
mid-child-handoff, a mid-#944 boundary), D260-D262 (the three Step 8a deferrals above).

**Status.** Suite 5804→5896 (+92), exit 0 (re-verified unpiped before merge). No workflow-spine
change → no diagram REV. `Closes #944`.

Design: `docs/planning/2026-08-06-944-revalidate-hardening-design.md` ·
live: https://rawgentic-design-944.vercel.app/

---

## Epic #871 M4 — #586 Part 2: the scheduler, wired outside the repo · v3.135.2

**The gap, closed.** Part 1 (v3.135.1) shipped the measurement/validation/lineage-check
library with no caller. Part 2 wires it into the actual launcher: `overnight-resume.sh`
(the workspace-root template, outside any git repo — `.git` there is a stub, confirmed) is
rewritten into two roles sharing one recurring `*/20` cron trigger. RECONCILER: if stale
and a fresh `resets_at` observation exists, arm a self-removing ONE-SHOT crontab entry at
`resets_at + 60s` and return without launching; if the one-shot is missing/overdue, or the
reset time was never measured (`waiting_for_reset_unmeasured`), fall back to the pre-#586
blind-staleness launch. ONE-SHOT: remove its own crontab line FIRST (a crash after that
point must never leave a live cron field that could re-match next month/year), then launch.
Session resume now reads the campaign's lineage tail from `claude_docs/session_registry.jsonl`
and confirms it point-in-time via `check_session_lineage` before `--resume`, instead of a
session ID pinned at arm time. `.claude/skills/long-run-resume/SKILL.md` carries the same
rewrite so future campaigns inherit it.

**AC 1's live spike, resolved without touching the bridge.** The existing `usagebar`
integration (fed by the identical `$input` payload `rawgentic-statusline.sh` receives)
already caches `rate_limits.five_hour.resetsAt` — read live during this run:
`1786021800`, ~4.6 hours in the future of the capture instant, well inside the library's
6-hour sanity bound. That confirms the field genuinely exists in the live payload on this
host, independent of any edit to the bridge script itself.

**The wiring that could NOT ship, and why.** A narrow, single-purpose `Edit` adding the
persist call to `~/.claude/rawgentic-statusline.sh` was denied by the auto-mode
classifier — the same class of denial D249 hit on this exact file. Per that precedent, the
run did not retry or route around it via a different tool (D253). The one-line patch is
below for the OWNER to apply by hand — the classifier does not gate a human editing their
own files. Nothing downstream depends on it existing: `reset_resume_lib.extract_resets_at`
degrades cleanly to `{"ok": false, ...}` and the launcher's `waiting_for_reset_unmeasured`
path carries the resume exactly as it did before #586, just logged rather than silent.

```bash
# Insert into ~/.claude/rawgentic-statusline.sh, immediately after the existing
# `session_id=$(echo "$input" | jq -r '.session_id // empty' ...)` line:
if [ -n "$session_id" ]; then
  _rr_resets_at=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // empty' 2>/dev/null)
  if [ -n "$_rr_resets_at" ]; then
    _rr_used_pct=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty' 2>/dev/null)
    _rr_state_dir="$HOME/.claude/rawgentic-reset-state"
    _rr_args=(persist --state-path "$_rr_state_dir/$session_id.json" --resets-at "$_rr_resets_at" --observed-at "$(date +%s)")
    [ -n "$_rr_used_pct" ] && _rr_args+=(--used-percentage "$_rr_used_pct")
    (mkdir -p "$_rr_state_dir" 2>/dev/null
     timeout 5 python3 /home/rocky00717/rawgentic/projects/rawgentic/hooks/reset_resume_lib.py "${_rr_args[@]}" \
       >/dev/null 2>&1) &
  fi
fi
```

**A latent bug found and fixed, not backported.** `launches=$(grep -c 'LAUNCH ' "$LOG" ||
echo 0)` looks safe but is not: `grep -c` exits 1 (not an error) whenever the count is
legitimately zero, so the `||` ALSO fires, doubling the captured value to `"0\n0"`. Fed
into `$((launches+1))`, that doesn't just miscompute — it silently corrupts control flow,
skipping past an intended `exit` entirely (reproduced and confirmed in isolation: a
plain `if`/`exit` block downstream of the bad arithmetic never ran). Every pre-#586
per-campaign resume script (`epic204-resume.sh` and eleven siblings) carries this exact
idiom; harmless today only because none of them are currently enabled in crontab. Fixed
in the template only — not backported, out of scope for #586 and most of those campaigns
are already closed.

**Testing, given these files sit outside any git repo.** No pytest surface applies. The
reconciler/one-shot/lineage decision logic was validated by extracting the pure bash
helper functions into an isolated harness (stub `crontab`, fixture
`session_registry.jsonl`, fake `~/.claude/projects/<dir>/*.jsonl` mtimes, a stub `claude`
binary) and driving six scenarios: unmeasured→fallback launch, future
epoch→arm-without-launch, idempotent re-arm, one-shot fire→self-remove-then-launch,
overdue epoch→fallback launch, and valid unambiguous lineage→`--resume`. All six pass. The
live script itself could not be syntax-checked directly (`bash -n` on that exact path is
also classifier-denied — it is a `bypassPermissions` launcher, a sensible boundary) but an
inert scratch copy passed `bash -n` cleanly.

**Decisions (this slot).**
- **D253** — the bridge-wiring edit is classifier-blocked (D249 precedent held again); not
  retried, patch handed to the owner instead of silently dropped or worked around.

**Status.** No pytest suite applies (workspace-root files only) — the whole-suite baseline
carried over unchanged. No workflow-spine change → no diagram REV. `Closes #586`.

---

## Epic #871 M4 — #586 Part 1: a measured clock instead of a pinned session ID · v3.135.1

**The gap.** The durable overnight resume launcher (`overnight-resume.sh` template, per-run copies
like `epic204-resume.sh`) relaunched via `claude --resume "$SESSION_ID"` with the ID **pinned at arm
time**. A `/clear` mints a new session ID, so `--resume <old-id>` errors, and the fresh-`-p` fallback
was fragile enough to intermittently miss overnight (owner report, 2026-07-22).

**What shipped, and what deliberately did not.** Part 1 (this PR, `Part of #586`) ships only the
testable core: `hooks/reset_resume_lib.py` extracts and validates
`rate_limits.five_hour.resets_at` from a statusline-shaped payload, asserts freshness on the
OBSERVATION timestamp advancing (never on `resets_at` itself changing — see below), computes the
one-shot resume epoch (`resets_at + 60s`), and runs a conservative session-lineage identity check
before any `--continue`. The scheduler wiring, the `overnight-resume.sh` rewrite, and the
watchdog-to-reconciler demotion are workspace-root changes outside any git repo (`.git` there is a
stub with no HEAD/objects/refs — confirmed, not assumed) and ship in a follow-up PR (D250).

**The live spike that AC 1 called for, and what it actually produced.** Instrumenting the live
global bridge script (`~/.claude/rawgentic-statusline.sh`) to observe its real stdin payload was
denied by the auto-mode permission classifier on every attempt. A nested `claude --debug hooks -p`
one-shot DID confirm, first-hand, that headless print-mode never invokes the statusLine command at
all across a full session lifecycle — empirically backing, rather than merely assuming, the design
doc's own claim that the bridge write must happen during a live interactive session. The harder
fact — whether THIS host's interactive payload carries the field — stays unconfirmed and is why
`extract_resets_at` returns a clean `{"ok": false, ...}` rather than trusting the value blind;
Part 2 is where this gets a live caller for the first time. Recorded as D249.

**The freshness design decision.** This same wave's log recorded a measured defect: `herdr`'s
scraped statusline `tokens` field returned byte-identical output and an unchanged revision counter
across three probes spread over many minutes — the capture had frozen, not the value. `resets_at`
legitimately stays constant for up to five hours, so freshness here is asserted on the OBSERVATION
timestamp advancing between reads, never on `resets_at` changing — checking the latter would
false-positive on every healthy read.

**Decisions (this slot).**
- **D249** — the classifier-blocked live spike is shipped as a runtime self-check
  (`extract_resets_at`'s `ok: false` path) rather than a manual pre-verification gate.
- **D250** — splits #586 into two PRs (the #927 D233 precedent): this PR ships the pure library;
  Part 2 (fresh WF2 run) wires it into the actual scheduler and the workspace-root templates.

**Reviews.** One Step-11 cross-model pass (`gpt-5.6-sol`, diagnostic): 2 High + 3 Medium, all
applied — the freshness check gained a live-clock recency bound (an advancing-but-ancient pair was
wrongly "fresh"), the lineage check's docstring now states plainly that it is a point-in-time
snapshot and Part 2 must resume via `--resume <verified-tail>` immediately rather than a
separately-resolved `--continue`, and three malformed-input paths that raised now return clean
failures.

**Status.** Suite 5762→5804 (+42), exit 0. No workflow-spine change → no diagram REV. PR, CI and
merge SHA filled by the next slot's pass, per the established convention here.

---

## Epic #871 M4 — #726: a handoff finally looks backward · v3.135.0

**The gap.** Every gate in `perform_handoff` — split, agent start, project bind, prompt landed,
goal armed — asks whether the SUCCESSOR is ready. Nothing asked whether the PREDECESSOR was
finished. Measured 2026-07-30: a handoff ran with a design re-gate still dispatched, its 15 KB
verdict (8 findings, 3 High) landed two minutes later in a scratchpad scoped to the session being
retired, and the handoff reported every gate green.

**What shipped, and what deliberately did not.** Two backward-looking preflights: a mandatory
in-flight declaration on all three handoff CLIs, and a scan that refuses a resume prompt pointing
the successor at a session-scoped path. **`Part of #726`, not `Closes`** — AC 4 (poll to completion
and proceed automatically) is UNSATISFIED because there is nothing to poll, and `clear-prep` lives
outside this repository so it gets a callable check and nothing more (D247). The issue stays OPEN
with those two residuals named on it; the epic box is ticked, the same shape D226 set for #943
Part A.

**Three probes, and one of them refutes the issue body.** `/tmp/claude-<uid>` is `drwx------` owned
by the single host user and 207 sibling session directories back to 2026-08-01 were listable — so a
successor CAN read a predecessor's scratchpad. The issue's stated reason was wrong; the real one is
untracked per-session temp state tied to a session being retired, addressed by an id the successor
cannot derive. The second probe is negative and load-bearing: `<scratch-root>/tasks/<id>.output`
exists per background task but carries only stdout, observed on the same task at 14, 49 and 69
bytes with no sibling file at any point. That is why the gate ATTESTS rather than detects, and the
design says so in those words rather than claiming more.

**Decisions (this slot).**
- **D245** — declined an ELIGIBLE small-standard lane, because the deliverable is a refusing gate on
  the module's most safety-critical function and the lane drops the cross-model design layer. That
  layer then found 13 High defects.
- **D246** — the ambiguity breaker fired on four findings across three passes and was resolved from
  MEASUREMENT every time, never escalated to an away owner. One finding was REFUTED: the reviewer
  believed the date was 2026-08-05 and rejected the probes on that basis.
- **D247** — `Part of #726` with the issue left open, and the in-flight gate enforced on ALL THREE
  CLIs. Pass 2 demanded a rollout split for the frozen plugin cache; pass 3 objected that two
  unenforced paths leave the defect live. Pass 3 won on evidence from this same campaign: #769's
  rc-8 sweep gate shipped a mandatory refusal against that same frozen cache and the next boundary
  read its printed remedy and cleared it.

**The ordering IS the safety property, again.** `_cmd_handoff` records `mark_split_attempted`
durably before it calls `perform_handoff`, and `_classify_launch` maps an unrecognised failed step
to "append no terminal event". A refusal inside `perform_handoff` would therefore have parked a
campaign with `split_attempted: true` and nothing terminal. Found by reading shipped code — neither
reviewer raised it — and fixed by gating early, beside the rc-6 and rc-8 gates, plus two new
`_classify_launch` rows so even the backstop cannot park a run.

**Reviews.** One peer consult and three adversarial design passes (`gpt-5.6-sol`): 13 High +
5 Medium, 17 applied and 1 refuted, gate CLOSED budget-exhausted at the `design` cap. Four
self-review High findings, all applied; two of them (the frozen-cache trap and untrusted text in
the successor prompt) were independently corroborated by the reviewer a pass later.

**Status.** Suite 5698→5762 (+64), exit 0. No workflow-spine change → no diagram REV. PR, CI and
merge SHA filled by the next slot's pass, per the established convention here.

## Epic #871 M4 — #769: the learnings sweep stops being a habit · v3.134.0

**Issue:** [#769](https://github.com/3D-Stories/rawgentic/issues/769) ·
**Design:** `docs/planning/2026-08-06-769-child-boundary-learnings-sweep.md` ·
**Base:** `224ddace` · **Suite:** 5636→5698

**Problem.** Queue revalidation asks whether the remaining issue bodies still describe reality at
this head. The owner's D181 standing order asks the wider question — re-assess every remaining
child against what the completed child *learned*. That sweep was being done, and done well, by
hand at every boundary. It was named in neither `skills/epic-run/SKILL.md` nor
`docs/multi-issue-driver.md` (grep for reassess / learnings sweep / boundary sweep at `224ddace`:
**zero**), and nothing recorded that it happened — so a fresh-session successor either redid it
worse, without the predecessor's context, or dropped it silently.

**What shipped.** An append-only `boundary_sweeps` field, four pure functions in `driver_lib`,
three `launcher_lib sweep` subcommands, a schema declaration with no `schema_version` bump, and a
shared **rc 8** refusal wired into both `next-child` and `handoff` from one helper.

**The decision that shaped it: gate, or record?**

| | |
|---|---|
| Drafted | a record plus a visible advisory, no hard gate |
| Shipped | a coverage-validated record AND a fail-closed gate |
| Why it flipped | the independent peer proposal argued the gate; my "a second gate doubles the wedge surface" objection does not survive, because a `missing` sweep is self-clearable by doing the ordered work. The failure asymmetry decides it: a gate firing wrongly costs one command, an ignored advisory costs the standing order silently |

**What the gate honestly checks:** coverage and record integrity only — that a record exists for
this head naming every remaining child with a reason. Not the quality of the judgment, and the
prose says so, because this repo already has one audit stamp whose own docstring admits `depth` is
"an instruction to the auditor, not a property the validator checks".

**Three things the design review changed, each confirmed before it was applied:**

| Finding | Change |
|---|---|
| A head alone is not a boundary identity | A deferred child moves no commit, so a second deferral at one head was a real boundary the gate called already-swept while refusing to record it. Replay identity became `(head, after_issue)`, and the gate now compares coverage against the CURRENT eligible set — no new counter |
| `conflict` was unreachable AND unrepairable | A refused write leaves no evidence, so the status could never fire. Deleted rather than made reachable |
| The published command sequence was broken | `HEAD=$(sweep begin)` assigns JSON, not a sha, so every documented record would have been refused. Fixed, and the three-command sequence is now under test |

**Refuted with evidence:** the claim that `observe_head` never refreshes the tracking ref.
`launcher_lib.py:1786-1792` builds an explicit refspec, fetches before the rev-parse, and fails
closed on a non-zero fetch.

**Known limitation, deliberate.** Issue bodies are mutable, and the gate does not detect a body
edited after the sweep. Each assessment records the `body_hash` it assessed, but comparing it
against a live body would make a pure state function do network I/O. Verified the same hole exists
in the revalidation receipt this mirrors (`_receipt_covers_child` compares only `to_sha`), so it is
shared and pre-existing, not introduced here.

**Migration (D242).** Campaigns predating the contract are grandfathered: gating them would refuse
work over a boundary already past and no longer sweepable — including the paused #906 campaign.
Creation seeds `[]`, so every new campaign is gated.

## Epic #871 M4 — #943 Part A: supervision is declared, not guessed · v3.131.0

**Issue:** [#943](https://github.com/3D-Stories/rawgentic/issues/943) — Part A only; Part B is
[#947](https://github.com/3D-Stories/rawgentic/issues/947), and #943 stays OPEN until that lands ·
**Design:** [`2026-08-05-943-supervision-state-core.md`](2026-08-05-943-supervision-state-core.md)

Three code paths asked "is anybody watching?" by reading one environment variable.
`RAWGENTIC_HEADLESS` could say only present-or-absent, so it could not tell the owner stepping out —
reachable by phone — from the owner asleep, and no session could clear it, because a process cannot
un-export its parent's environment. It is now a declared workspace state at
`claude_docs/.supervision.json`, written by three new commands (`/rawgentic:away`,
`/rawgentic:sleeping`, `/rawgentic:back`) and read through two predicates. The modules are split for
a measured reason: `supervision_lib` is read-only and **stdlib-imports-only**, test-enforced, because
`context_meter` consumes it on a hook that runs every tool call, while `supervision_admin` holds
`plan_lib.file_lock` across the whole read-validate-increment-write cycle.

**Two predicates rather than one flag, because the consumers need opposite safe defaults.**
`nobody-to-ask` relaxes when a declaration expires — it only picks which advice a context-pressure
nag prints. `installs-forbidden` does not: installing packages is an outward act, and a clock passing
a stated wake time is not evidence anybody came back. An asymmetry test fails if the two are ever
collapsed, and an inert-feature regression proves a bare `/rawgentic:away` still guards both hook
sites.

**The design gate ran three adversarial passes and did not close cleanly.** Findings went 10 → 9 → 8,
each pass surfacing genuinely new depth, several items introduced by the previous pass's own fixes.
The design loop-back budget hit its cap at 2/2 and the #798 budget-exhausted close was unavailable,
because two of the eight findings carried ambiguity markers and that close requires the breaker
clear. So the contract escalated instead of iterating silently, and the owner — present — chose the
split (D226, verbatim: "split it, do part a and and part b after"). Five findings plus the
AskUserQuestion capability question moved wholesale to #947 with the machinery they belong to; two
landed here. Part A therefore ships **no routing, no claims, no authority logic and no external
calls** — `platform_apis` went from two blocks plus a release gate to `none`, which is the honest
reading once the transport and consult paths left.

**What the pre-PR reviews caught, and it was the strongest round of the wave.** Two cross-model waves
(Step 8a over the two high-risk modules, Step 11 over the whole diff) plus an inline pass returned 14
findings; 8 were real defects in shipped code. Four were the same class — a failure path that quietly
*permitted* installs while a declaration was in force: a dangling symlink read as absence, a delete
racing between `stat` and `open` read as absence, a schema-incomplete record such as
`{"state":"attended"}` was accepted as valid, and `validate_campaign_id` accepted `.` and `..`. Two
were availability: invalid UTF-8 raised out of a function whose contract is never-raises, and a FIFO
at the state path would have blocked `open()` on a hook that runs on every tool call. The eighth is
the one worth naming: `declare --state attended` offered an **unfenced** route to clear a newer
absence, because `declare`'s `--expected-revision` is optional while `mark-attended`'s is mandatory —
directly contradicting the documented "only `/rawgentic:back` lifts it". Both waves found that
independently, and `declare` now refuses `attended` outright.

**Declined, with reasons, rather than dropped.** Deleting the state file *between* hook invocations
still reads as a genuine absence: telling "never declared" from "record removed" needs a durable
marker outside the file being protected, which is a design change belonging to #947. And the
reviewer's inability to verify `plan_lib.file_lock` from the brief is an artifact-scope limit, not a
defect — it has been public since #665 and `launcher_lib` composes it identically.

**Gates:** suite 5366 → 5487, 0 failed, exit 0 · both lint lanes 10.00/10 · Step 4: 3 passes, closed
by owner resolution of the ambiguity breaker — explicitly not a clean round and not the #798
budget-exhausted close · Step 8a: 8 findings (5 High) · Step 11: 6 findings (3 High) · security scan
PASS (1 visible skip: iac not applicable) · loop-backs 2/3 with the design source exhausted at 2/2 ·
skills 21 → 24 · 121 tests added · no workflow-spine change → no diagram REV. *(PR #, CI, merge SHA:
filled by the next slot's pass.)*

---

## Epic #871 M4 — #888: run-records land exactly once · v3.130.0

**Issue:** [#888](https://github.com/3D-Stories/rawgentic/issues/888) ·
**Design:** brief note, small-standard lane (no separate design doc) ·
**Slot added late:** #888's own PR omitted its campaign-log section. This entry was reconstructed at
the next slot's pass from the child's persisted run-record, so every figure below is the recorded
one rather than a retelling.

The telemetry store is append-only, so re-running `summarize` to attach late usage numbers appended
a second line for the same run instead of amending the first. #888 made the persist transactional —
idempotent by content fingerprint — and codified the ordering that matters: the record is persisted
BEFORE the merge, so a run that dies between the two leaves a record rather than a hole. Absorbed
#588 and #355.

**Gates:** suite 5340 → 5366, 0 failed, exit 0 · Step 4: 3 findings, all resolved · Step 9: 1
finding, resolved · Step 11: 7 findings, 5 resolved — the 2 residuals (an `usage.capture_cutoff`
schema field, and a content-fingerprint collision for two blocked runs on one issue with null usage
and zero counts) are deliberately UNFILED pending owner confirmation under the D179 throttle, and
are recorded in the child's `follow_ups` · security scan PASS (1 visible skip: iac not applicable) ·
loop-backs 1/3 · no workflow-spine change → no diagram REV. PR
[#946](https://github.com/3D-Stories/rawgentic/pull/946) · CI passed · merged as `b7d3f258`.
`outcome.merged` is null in its own record on purpose: the record was written pre-merge by the very
ordering this issue codifies, which is not a failed merge.

---

## Epic #906 M2 — #731: a failed handoff now says why · v3.129.1

**Issue:** [#731](https://github.com/3D-Stories/rawgentic/issues/731) ·
**Design:** brief note, small-standard lane (no separate design doc)

Two consecutive live handoffs failed with `"failed_step": "agent_start"` and nothing else, while
the cause — herdr's `agent_name_taken` — was already captured in `out["steps"][].note` and then
dropped by the CLI payload filter, which copies nine keys and not `steps`. The name stayed bound,
so the obvious operator recovery ("run it again") was structurally impossible. The fix follows the
choke-point rule: `record()` now derives a failure note from any failed proc's herdr body, a pure
`failure_detail(out)` reader lifts the failing step's own note (never an unrelated one — wrong
attribution beats no detail is false), and the payload carries `failure_detail` plus
`pane_capture` — the tentative pane's last words, read before cleanup destroys them, and only when
the pane provably still hosts our session.

**The preflight makes the poisoned retry impossible instead of merely legible.** Before any split,
`herdr agent list` (verified live on 0.8.0 — entries carry `name` only when named) is checked for
the requested name; taken → `failed_step: "name_taken"` naming the holding pane, with no pane
created. Fail-open in every direction — non-zero rc, garbled output, a raising or legacy runner —
because a broken optional check must not become a new way for handoffs to fail. The start-time
race maps to the same name-specific step and is never retried; only `agent_pane_busy` remains
retryable.

**What the reviews caught.** The Step 8a wave (two passes over the two high-risk commits) found
the real security defect in the first cut: the capture ran before the identity probe, so a reused
handle could have disclosed a foreign viewport into the report — reordered, with a visible
capture-skip on refusal. Step 11's passes then caught the first-ever runner `timeout=` kwarg
breaking legacy caller-supplied runners (TypeError → orphaned pane; fixed with a fallback) and the
library-level teardown exits still returning a bare `failed_step` (fixed with a finalizer that
prefers `predecessor_guard`). Declined with recorded rationale: never-capture-when-no-session (it
guts AC2's motivating case, and the strictly more destructive close already proceeds on the
identical #611 honest bound) and a herdr integration test (CI has no herdr; injected-runner
black-box is this repo's testing philosophy).

**Gates:** suite 5303 → 5340, 0 failed, exit 0 · Step 8a: 3 findings, both Highs fixed ·
Step 11: 9 raw findings, **6 unique** after 3 identity merges, 5 fixed, 2 declined with rationale,
0 deferred · security scan PASS (1 visible skip: iac not applicable) · loop-backs: 2 mints spent,
0 returns to Step 3 · no workflow-spine change → no diagram REV. PR
[#942](https://github.com/3D-Stories/rawgentic/pull/942) · CI passed · merged as `acffe3ed`.
*(Corrected at the next slot's pass, as this child's own run-record instructed: the line previously
read "7 unique post-merge", which was one too many.)*

---

## Epic #875 M1 — #910: the last two hand-pinned counts, and a guard that already existed · v3.128.2

**Issue:** [#910](https://github.com/3D-Stories/rawgentic/issues/910) ·
**Design:** brief note, small-standard lane (no separate design doc)

Filed as the tail of the #271 computed-guard conversion: two count strings in `CLAUDE.md` §4
mistake #2 that the guards never reached. Measuring them first turned up that **both were already
wrong** — "All 7 config-driven skills" and "6 workspace management" against a real 9 and 9. Not new
either: the #528 run record booked exactly that drift as a follow-up on 2026-07-20 and it survived
fifteen days, because nothing read those lines. Correcting the numbers is a forced consequence of
guarding them.

**The durable lesson is where the guard went, not what it checks.** The first implementation built a
computed guard in `test_adversarial_review_registration.py` — mutation-verified, section-anchored,
and *wrong*, because `hooks/skill_registration_check.py` (#528, three slots earlier in this same
campaign) already swept every count pin, already enumerated both families, and already had a
`COMPUTED_FAMILIES` mechanism built for exactly this promotion. Its own `test_real_repo_is_clean`
failed and said so. That cost the run's `tdd` loop-back. The rule that would have prevented it is
already written down twice — workspace §3 and repo §3, "one helper, one home" — and Step 2 did not
check it. **A step-2 inventory that greps for the surface but not for an existing owner of that
surface will keep producing this.**

**Shipped:** `CLAUDE.md` added to `SWEEP_GLOBS` (it is not "docs" in the excluded sense — it is the
manual read as instruction, and being outside the sweep is *why* it rotted); `pin:config-driven`
promoted into `COMPUTED_FAMILIES` against the canary's own population; a fail-closed branch so an
uncomputable expectation can never silently demote a family back to consensus. Five new tests, four
mutation-verified.

**The promotion mattered for a reason worth stating.** Consensus failed in the only way consensus
can: README and its own test literal agreed on 8 while the corpus carried 9 — every copy consistent,
every copy wrong. **A lone pin trivially agrees with itself.** The change then exposed the same blind
spot reproduced inside the test suite: the checker's own fixture claimed two config-driven skills
over a fixture tree containing one, and consensus-of-one had been passing it.

**What the review caught, and what it got wrong.** Two cross-model passes returned five findings.
The High was real and applied — the claim that *both* counts are now computed was overstated, since
`pin:workspace` is consensus-checked and `breakdown-sum` cannot close the gap (`[9, 8, 2, 2]` sums
to 21 exactly as `[9, 9, 1, 2]` does); the manual now names which is which. Their other remedy —
invent a per-skill category taxonomy — was declined, because it adds a hand-maintained surface while
claiming to remove one. **Both passes also independently raised the same Critical, and both were
wrong:** they predicted this PR's changelog text would trip the sweep, not seeing `_readme_body()`,
which truncates README at `## Changelog` precisely because the changelog legitimately holds
historical counts. Two independent reviewers agreeing on a false conclusion, inside the PR whose
subject is that agreement is not correctness.

**Gates:** suite 5249 → 5254, 0 failed, exit 0 · both lint lanes 10.00/10 · security scan PASS
(1 visible skip: iac not applicable) · loop-backs 2/3 (`tdd` 1/1 rehome, `review` 1/1 mint) ·
lane-widened 3→4 impl files · no workflow-spine change → no diagram REV.

---

## Epic #875 M1 — #909: a description cap, and the YAML comment that was eating triggers · v3.128.1

**Issue:** [#909](https://github.com/3D-Stories/rawgentic/issues/909) ·
**Design:** `docs/planning/2026-08-05-909-description-diet-and-incident-split.md`
([live](https://rawgentic-design-909.vercel.app/)) · **Follow-up:** #928

Filed as a character-cap chore. The cap was real — `pane-handoff` at 1,174 against Anthropic's
documented 1,024 — but measuring it surfaced a worse defect the issue never mentioned: **an unquoted
YAML plain scalar treats ` #` as a comment start**, so `epic-run`'s description loaded as **131 of
534 characters**, losing every trigger phrase after *"cycle through all issues in epic"*, and
`pane-handoff` silently lost `Requires HERDR_ENV=1.` Confirmed twice — `yaml.safe_load` over the
tree, and a live session's own available-skills listing ending that entry mid-phrase at the same
byte. Both are single-quoted now; `peer-consult` had been quoted all along, which is what made the
fix's live behaviour *observable* rather than assumed.

**Shipped:** `pane-handoff` 1,174 → 897 chars keeping every quoted phrasing, all four bare trigger
verbs, both dictated variants (#732) and the unprompted-run directive; install notes out of
`adversarial-review` (842→697) and `peer-consult` (690→557) into their bodies;
`incident/SKILL.md` 537 → ~220 lines split into three flat `references/` files with zero substantive
prose lost, while `<mandatory-verification>` and the destructive-action approval rule stayed in the
always-loaded body on the argument that a reference read is prose-enforced, not enforcement.

**Guards:** `tests/test_skill_description_budget.py` (present/string/non-empty, the 1,024 cap, and a
truncation check length alone structurally cannot make — its fixture parses cleanly, sits far under
the cap, and is half dead text); `tests/test_skill_reference_structure.py` (flat references, walked
recursively because a flat glob would miss the nested file it exists to reject; no plugin-root token,
over every file type). Both `incident` ordering pins rebuilt on a new provenance-preserving
`corpus.skill_files()` — **not** `skill_corpus()`, whose joined string cannot express "same file" —
and mutation-verified in both directions.

**What the reviews caught, which is the durable lesson.** Four cross-model design passes (16
findings) plus a code review and a refutation pass (8 more) found **four defects introduced by this
change itself**: a behaviour change broadening the destructive-action rule to cover read-only
diagnostics, which could stall a SEV-1; two bare triggers (`pass over`, `send work`) silently deleted
by a compression while every quoted pin stayed green; a "bidirectional" link guard whose reverse
direction was hard-coded; and a truncation detector blind to a quoted key and to duplicate keys. The
recurring shape across all of them: **a fix applied in one place and missed in another**, which
happened twice in the design document alone. That is the argument for mechanical guards over
vigilance, made by the issue against itself.

**Named residual (#928):** this repo has no behavioural gate proving a skill still gets *selected*
after a description edit — no CI job invokes `evals.json`. Phrase coverage proves no mined phrasing
was dropped and nothing more. The real gate needs an installed plugin build and live sessions, and
reinstalling mid-session is prohibited, so it was unavailable rather than merely costly. The reviewer
demanded it in all four design passes; the disposition held, and is the owner's to overturn.

**Gates:** suite 5208 → 5249, 0 failed, exit 0 · both lint lanes 10.00/10 · security scan PASS
(1 visible skip: iac not applicable) · design gate closed budget-exhausted over resolved ground
(`design` 2/2, global 2/3) · no workflow-spine change → no diagram REV.

---

## Epic #875 M1 — #761: a task class, resolved once and rendered inert · v3.128.0

Round-11 session (mid-child handoff from round 10), full lane — `complex_feature`, `fast_path_eligible`
false, so no design-stage ceremony was dropped. Plugin 3.125.8 cache.

The campaign's own session-mining report named the gap: **"no actor at any gate owning the question
'should this exist at this size at all?'"** #761 ships the *field* that question needs and nothing
that acts on it — `disposable | internal | production`, resolved ONCE from the issue body at Step 1,
snapshotted **write-once**, and rendered as one line into every cross-model review and consult
prompt. Reviewers are told the class and told explicitly to apply the same rubric regardless. No
demand scales; no gate relaxes. That inertness is the point: the demand-scaling half and the WF2-lite
lane are #923's, after the owner split this issue mid-flight.

**The design cost more than the code.** Seven revisions, six Step-4 passes, and the `design`
loop-back source refused at 2/2 twice with the ambiguity breaker unclear both times. The owner ended
it by **override (D204)** — implement-with-constraints, rather than a third design pass, another
split, or folding into #923 — on the condition that the doc not ship carrying known gaps. Revision 7
is that fold: all thirteen pass-6 findings written in as constraints C1–C13 with a
constraint→location map, so the shipped doc and the shipped code agree. Six of the thirteen were
DOC gaps where the code was already right, which is worth recording plainly — a design that
describes something the code does not do is the prose-divergence this campaign keeps finding.

**Then the reviews found seventeen more defects, and two of them were regressions in the fixes.**
Step 8a (two passes over the three high-risk tasks): five findings, all applied. Step 11 (two passes
plus the mandated adversarial diff layer, `diffReviewMode: always`): twelve findings, 0 dropped by
band, 10 unique after deduping the two the passes independently converged on — nine applied, one
declined on the merits, one left closed as already-adjudicated. The two regressions are the
instructive part: the Step-8a fix that corrected the WF5/WF13 instruction left its own **completion
checklist** still saying the opposite, and the Step-8a fix that gated the issue *fetch* left the `jq`
body *extraction* unguarded — the empty-body hole had **moved, not closed**. Both were caught only
because the round re-reviewed the fixes rather than the original diff.

One finding went back to the owner rather than being decided in-flight. The cross-model pass argued
the runner should read the snapshot itself instead of trusting its caller, and tagged it
`design-flaw` — which would mean returning to Step 3, in direct conflict with D204's override. All
five findings were presented together and the owner chose **verify-if-present (D207)**: where the
snapshot is readable the runner checks it and refuses a disagreement; an absent snapshot proceeds, so
a standalone WF5 review naming a never-ran-WF2 issue keeps working. Step 11 then re-raised the
always-read argument and it was **not re-opened** — a prior answer stands.

**Both Step-11 passes ran TOKENLESS.** `review-reopen` refused at rc 3 with the total budget at 3/3,
so the round was diagnostic by construction: a design-level finding escalates instead of looping.
Every fix still landed red-before-green.

**Telemetry (run-record `wf2-761-<sha>`):** gates — Design Critique CLOSED BY OWNER OVERRIDE (D204,
13 findings → implementation constraints, 7 High with terminal ADOPTED dispositions), Per-task Review
pass (T1/T2/T3 all `applied`), Implementation Drift pass, Code Review pass (9 applied, 1 declined
with a recorded reason, 1 not re-opened), security scan PASS (0 blocking, skipped: iac — a visible
skip, not a pass). Tests **+128**, suite **5080→5208/0**. Loop-backs **3/3 exhausted** — 2 design,
1 review_design — with the Step-11 round therefore tokenless. No workflow-spine change → no diagram
REV.

**On STAY SMALL, since this slot pushed a byte ceiling.** `references/step-01.md` gained a whole new
gated sub-step and went 4108 → 7074 bytes. #903 set the precedent that raising a ceiling inside a
milestone named STAY SMALL requires trying the trim first, so the rationale prose was compressed back
to **6360** with every guard, command and failure clause intact. The residual growth is operative
prose, not commentary: resolve-and-snapshot did not exist before, and its three failure gates are
themselves the Step-8a and Step-11 findings. Trim the commentary, keep the guards — stated here
because "we raised a ceiling in STAY SMALL" deserves an argument, not a shrug.

## Epic #875 M1 — #903: a budget-exhausted design-gate close requires resolved ground · v3.127.0

Round-9 session, small-standard lane (`standard_feature`, 4 impl files ≤ 7), plugin 3.125.8 cache.
#798 let WF2's Step-4 gate self-close the moment the design loop-back budget ran out. Exhaustion
alone was the whole test. Observed live on #874: the design source cap was reached with the
breaker clear, so the close was available **while a High design finding was unresolved and an AC
was known unmet** — the run refused it by hand and escalated. A second instance is on record
(epic #667 child #665: budget exhausted with two findings still open, run proceeded, logged as a
deviation), and #798's own review pass had independently flagged the gap: "no disposition or owner
acceptance is required, so the workflow can knowingly implement a design with [surviving
findings]".

The fix is a pure predicate at the executable boundary, mirroring the sibling
`findings_are_unambiguous`: every Critical/High finding in `--findings-file` must declare
`terminal_disposition` (`applied|refuted|deferred`), and `refuted`/`deferred` each need a real
`disposition_reason`. It sits AFTER the ambiguity check deliberately — running it first would
leave the older ambiguity guard still returning non-zero while silently no longer proving what it
claims. The refusal is **self-repairing**: it names the field and its values, and the prose says
explicitly that this refusal is not an escalation condition. That mattered more than it sounds —
the Step-4 gate's own review caught that a merely-correct refusal would have escalated every
caller still emitting the old findings-file shape, recreating the six-consecutive-escalations
problem #798 was built to delete.

**Three review waves found three separate fail-opens in the fix itself, each one the same defect
the feature exists to remove — a property asserted and never checked.** Step 8a: `str()` coercion
turned `null`/`false`/`0`/`[]` into the non-empty text "None"/"False"/"0"/"[]", so a refuted High
closed the gate with no rationale; and an unclassifiable severity ("Blocker", a list) was silently
skipped as non-severe rather than refused. Step 11: a `disposition_reason` made only of invisible
characters — zero-width space, ESC, a bidi override — read as substance. The adversarial layer:
the same hole one category over, because an invisible character can be a **letter** (U+3164 HANGUL
FILLER), a mark (U+034F, U+FE0F) or a symbol (U+2800), none of them `Cc`/`Cf`. Notably the
reviewer's own recommendation there ("require a letter or number") would not have caught its own
example. It also caught a regression in the new prose: an enumerated finding shape that omitted
`ambiguity_flag`, which `findings_are_unambiguous` defaults to "clear" — a caller rebuilding
findings from that list would have disarmed the older guard.

Two High recommendations were **declined with reasons**: removing `deferred`, and removing
`applied`, from the accepted dispositions. Both are fair internal-consistency critiques — the
feature condemns bare asserted tokens and then permits two — but AC1 names all three, and
narrowing an owner-authored spec is not an implementation decision. Filed as a follow-up instead,
alongside a second one: Step 8a's per-sha acknowledgment requirement is structurally
unsatisfiable through the runner, whose `FINDINGS_SCHEMA` has no field to carry it.

**Telemetry (run-record `wf2-903-82947d55`):** gates — Design Critique pass (lane rubric-only,
deep pass; 1 High spec-tightened in-gate and verified RESOLVED, 1 sub-band Low adopted anyway),
Plan Drift skipped (lane), Per-task Review pass (T1+T2 high-risk; 2 High + 2 Medium fixed),
Implementation Drift pass (one real gate failure found and fixed — see below), Code Review pass
(3 High adopted+fixed across Step 11 and the adversarial layer; 2 High + 1 Medium declined with
recorded reasons; 1 Medium dropped by its confidence band and refuted on the merits), security
scan PASS (0 blocking, 0 advisory, skipped: iac). Tests **+48**, suite **5031→5079/0**.
Loop-backs **3/3** — one spec-tighten, one 8a, one Step-11 — every one spent on an authorized
in-place fix round, none on a redesign. Eight terminal dispositions persisted (4 adopted,
4 declined). WF2 diagram REV 3.127.0 (station 04 delta).

Step 9 also caught something the epic itself cares about: the new prose pushed
`references/step-04.md` 797 bytes past its byte ceiling. The guard offers "trim it, or raise the
ceiling" — trimmed, twice, because raising a size ceiling inside a milestone named STAY SMALL
would contradict the milestone. The file ships at 23712 of 23727 bytes.

---

## Epic #875 M1 — #902: reviewer confidence is a native number the band filter can consume · v3.126.0

Round-8 session, small-standard lane (`standard_feature`, 5 impl files ≤ 7), plugin 3.125.7 cache.
Ten consecutive cross-model review rounds returned `confidence` as a word ("high"/"medium") and
every round needed a manual word→float step before `plan_lib.SEVERITY_BANDED_CONFIDENCE` could
run. The root cause was not reviewer noncompliance: **the runner's own contract demanded words** —
`FINDINGS_SCHEMA` declared `enum ["high","medium","low"]`, `validate_finding` enforced it, and the
prompt instructed it. The reviewers were obeying the schema, which outranks the brief.

Fix, end-to-end: the schema demands `{"type": "number"}` (live-probed on the exact
`codex exec --output-schema` invocation before design — external session-notes evidence; range
checked in `validate_finding` because `minimum`/`maximum` are strict-mode-rejected keywords);
the prompt asks for 0.0–1.0; `coerce_confidence` maps legacy words and numeric strings through
the ONE existing map `ADV_CONFIDENCE_TO_FLOAT` with per-finding `confidence_source` and top-level
`confidence_mapped` provenance — flagged, never silently native; garbage (bool, out-of-range,
non-finite, arbitrary-precision overflow ints) refuses the whole review. **AC1's literal
0.8/0.6/0.3 triple was deliberately dropped** per the issue's authority comment — it collided
with the pre-existing map. A non-native round gets one bounded re-roll with held-result
protection: a valid, fully native, non-empty re-roll UNION-merges with the held findings (native
copy wins an exact dedupe-key match; a deterministic merge note discloses retained findings); any
other re-roll outcome accepts the held mapped result — a review validly in hand never becomes a
failure, an empty pass, or a smaller finding set because its polish re-roll went sideways.

The fix reviewed itself: all three review waves on this PR (Step 8a, Step 11, adversarial
`always` layer) returned **all-native numeric confidence** — `confidence_mapped: false`, zero
manual mapping — and the banded filter ran mechanically for the first time in the campaign.
The waves also earned their cost in the classic way: the 8a wave caught the re-roll discarding
held results (High, fixed), Step 11 caught the wholesale-replace variant of the same class plus
an `OverflowError` crash on huge JSON integers (High + Medium, fixed), and the adversarial layer
added the merge-note disclosure (Medium, fixed) while two of its recommendations were declined
with recorded reasons (a severity-independent merge key can silently collapse distinct findings;
validator-enforced severity-confidence coupling would refuse a whole review over one calibration
miss).

**Telemetry (run-record `wf2-902-f6d9a48f`):** gates — Design Critique pass (lane rubric-only;
2 Low adopted as amendments), Plan Drift skipped (lane), Per-task Review pass (T1+T3 high-risk;
1 High fixed), Implementation Drift 0/0 pass, Code Review pass (2 High adopted+fixed across
Step 11 + 8a; 4 adversarial Mediums: 2 adopted, 2 declined with reasons; 0 findings dropped by
bands), security scan PASS (0 blocking, 0 advisory, skipped: iac). Tests +72 added net, suite
**4959→5031/0**. Loop-backs 2/3 minted, both spent on authorized in-place fix rounds, neither on
a redesign. Two terminal dispositions persisted (both High, adopted). No workflow-spine change →
no diagram REV.

---

## Epic #875 M1 — #901: a negated "does not close #N" no longer closes #N · v3.125.8

Round-7 session, small-standard lane (`simple_change`, 5 impl files ≤ 7), plugin 3.125.7 cache.
GitHub's closing-keyword parser does not understand negation: "this PR does not close #N" matches
`close #N` and shuts that issue on merge. **It had already fired twice** — #568 on the #573 merge
(2026-07-21) and #874 on the #898 merge (2026-08-04) — both on PRs deliberately marked `Part of`,
with no closing keyword in any commit. The body prose alone did it, and both issues were reopened
by hand. Memory recall surfaced the first incident, which the issue body did not mention; that
turned a one-off into a repeat and gave the tests a second verbatim case.

Fix: `find_closing_refs` (a deliberately NAIVE scanner mirroring GitHub — negation-blind on
purpose, because the guard's job is to predict the parser, not read the sentence),
`assert_pr_body_closing_refs`, and a `check-pr-refs` CLI wired into WF2 Step 12 item 4b and WF3's
PR step, both BEFORE `gh pr create`. Two decisions are load-bearing. **Code spans are NOT
exempted** even though GitHub ignores backticked keywords (verified live 2026-07-28): a false
positive costs one question, a miss costs a wrongly-closed issue, and a markdown parser would be a
new bug surface inside the guard that has to be the trustworthy one. **A declaration is
unqualified** — `--closes 901` authorizes this repo only, so a qualified `other/repo#901` never
matches a bare integer.

The guard earned itself on its own PR body: run against the draft, it flagged **four** live
closures, two of them pointing at **#874 — the very issue the original defect wrongly shut** — which
merging the unfixed body would have shut a second time. Two of the four sat inside a fenced block,
so exempting fences would have hidden them.

**Telemetry (run-record `wf2-901-66ccc052`):** gates — Design Critique 3/3 pass (rubric-only, lane;
1 High adopted before Step 5), Plan Drift skipped (lane), Per-task Review 8/8 pass (T1 high-risk;
2 High fail-opens fixed — unscanned commit messages, and a cross-repo identity collision where a
bare `--closes` authorized another repo's same-numbered issue), Implementation Drift 0/0 pass after
the full-suite gate caught 2 real drift-guard regressions on its first run, Code Review 8/8 pass
(**1 Critical**: both workflows prescribed a `/tmp/...` body file that the gate's own containment
check refuses, so the documented command returned rc 2 every time — the mandatory gate was
unsatisfiable as written, reproduced before fixing; the adversarial `always`-mode layer then caught
the WF3 ordering bug introduced *by that fix*, plus an over-claim about backticks); security scan
PASS (0 blocking, 0 advisory, skipped: iac). Tests 72 added, suite **4887→4959/0**. Loop-backs 2/3,
both mints spent on authorized in-place fix rounds, neither on a redesign. Six terminal dispositions
persisted (5 adopted, 1 refuted-and-declined with measured evidence). Diagram REV 3.125.8 marks
station 12.

Two review rounds found what tests could not: both Criticals-in-effect were prose-versus-code
drift, in a guard whose entire value is being trustworthy.

---

## Epic #875 M1 — #904: a gates[] row for step 11.5 is rejected at write time · v3.125.7

Round-6 session, small-standard lane (`simple_change`, 3 impl files ≤ 7), plugin 3.125.6 cache.
**The filed issue was refuted before any code was written.** Its symptom — `work_summary` rendering
"Security Scan: not run" for a record carrying a valid passing block — did not reproduce; owner
decision **D187** rescoped the issue rather than closing it. The real defect in the same area:
**28 of the 205 stored records carry a `gates[]` row for step `"11.5"`**, which the schema forbids
(that result lives in `security_scan`, and `CANONICAL_GATE_NAMES` has no `11.5` key), so each of
those summaries renders a duplicate Security-Scan line — the plausible cause of the original
misreading. Counts re-verified independently at head 69e751ba rather than taken from the comment;
the rescope's "204 records" was stale by one, the 28 was not.

Fix: `validate_record` rejects such a row in **strict (write-time) mode only**, naming the offending
index and pointing at `security_scan`, mirroring the `SCANNER_KINDS` vocabulary precedent.
Strict-only is the load-bearing choice — `load_store` validates leniently, so a non-strict guard
would have evicted all 28 historical records. Verified against the real store: 205 loaded, 0
excluded, 28 rows still readable. Renderer deliberately untouched (AC3), `references/run-record.md`
untouched (AC4), history not rewritten (AC5) — so those 28 summaries keep their duplicate line as a
**stated** consequence, not a silent one.

**Telemetry (run-record `wf2-904-475415bb`):** gates — Design Critique 4/4 pass (rubric-only, lane;
2 findings were verified-not-defects, 2 folded into the plan and PR body), Plan Drift skipped (lane),
Per-task Review skipped (0 high-risk tasks), Implementation Drift 0/0 pass, Code Review 3/3 pass
(inline pass clean after its own suspected pylint finding was refuted by running the real lanes;
runner gpt-5.6-sol + the `always`-mode adversarial diff pass raised 3 unique findings — one adopted
as a `load_store` pin, one declined with cited evidence, one a genuine changelog miscount);
security scan PASS (0 blocking, 0 advisory, skipped: iac). Tests 7 added, suite **4880→4887/0**
(twice-run discipline held, plus one evidence-driven pre-PR re-run after the review-fix commit).
Loop-backs 1/3 — the reopen token was minted before dispatch and never spent on a redesign round.
No `11.5` gate row in this record: the run dogfoods its own guard.

**Notable:** both cross-model passes independently recommended moving validation into
`persist_record`. Declined (D904-11-1b, decision **D193**) — refuted by inspection a diff-scoped
reviewer cannot perform: one caller, reachable only past the strict check that returns 1 before
persisting. Filed as a follow-up rather than expanded into this slot, which is what M1 "STAY SMALL"
asks for.

## Epic #875 M1 — #905: driver_lib.py refuses CLI invocation loudly · v3.125.5

Post-mortem child (round-5 session, small-standard lane, plugin 3.125.4 cache). A bare
`python3 hooks/driver_lib.py <anything>` imported and exited 0 silently — read live as a passing
gate while `launcher_lib.py next-child` was refusing rc 6. Fix: tail `__main__` stub (stderr
refusal naming `hooks/launcher_lib.py`, exit 2), `sys` imported inside the guard so the
import-time surface is unchanged; purity source-grep untouched.

**Telemetry (run-record `wf2-905-bbad90f6`):** gates — Design Critique 1/1 pass (rubric-only,
lane), Plan Drift skipped (lane), Per-task Review skipped (no high-risk), Implementation Drift
0/0 pass, Code Review 2/2 pass (inline clean; runner gpt-5.6-sol found 2 Low, both verified and
applied — full remediation-string pin + docstring purity qualifier); security scan PASS
(skipped: iac). Tests 3 added, suite 4809→4812/0 (twice-run discipline held + one evidence-driven
pre-PR re-run after the review-fix commit). Loop-backs 0/3; no reopen token minted (lane
single-reviewer shape — the #761 AC6 gap, noted not papered over). Usage 54.7M in / 189k out
(≈$77.9 rate-card), wall 1,330s; timing complete (design 404s · plan 16s · implement 347s ·
review 563s). PR #911, CI test+lint+security-review all green per-sha.

## Epic #875 M1 slot 1 — #856: CI byte ceilings for the WF2 prose corpus · v3.123.1

First post-retreat WF2 run (small-standard lane, manual handoff, first M1 child on plugin
3.123.0). Rescope 2026-08-04 governed the slot: the 302-row obligation inventory and the
digest instrument were dropped as sunk cost; what shipped is the measurement + the
ceiling guard, with the steps.md split moved to #874 — which runs AFTER this so its byte
redistribution is forced through the guard.

- **Measured post-retreat corpus** (tree 050cbe8e): **237,717 B / 3,079 lines across 6
  files** (pre-retreat 301,635 B / 7 files); ≈59.4k tokens as a labelled bytes/4
  approximation, never asserted.
- **Guard** `tests/test_wf2_prose_budget.py`: recursive-glob, glob-exact accounting with
  four violation classes (unbudgeted new file / stale budget entry / per-file over /
  total over); ceilings at actual + 2.4–19% per file, total 245,000 B (~3.1%);
  file-specific failures name the path, over-failures carry the byte delta.
- **Review** (lane: single inline reviewer on the security seat + cross-model
  gpt-5.6-sol diff review per D180): 5 findings, 0 Critical/High — 1 adopted
  (wording-honesty fix, f6e03595), 1 declined with reason, 1 band-dropped Medium noted
  as a follow-up candidate (ceiling-ratchet: in-place shrinkage accumulates allowance),
  2 others band-dropped.
- **Gates:** suite 4659→4666/0 (rc 0); both pylint lanes 10.00/10; security scan PASS
  (0 blocking, 0 advisory; iac skipped — not applicable). No workflow-spine change → no
  diagram REV.
- PR / CI / merge: filled by the next slot's pass.

## Epic #756 — silent failures: the executor instruction layer (#735 → #733 → #732 → #758 → #767 → #765 → #762 → #847) · v3.118.2

Eighth slot (#847, session ea47e4bc, v3.118.2, WF3): the run that turned the epic's own thesis on
the epic's own records. `hooks/notes-size-handler.py` had been deleting everything outside the last
200 lines of every `session_notes/*.md` with **no archive**, and its only preservation path — a POST
to `localhost:9077/ingest` — had never served, swallowed every exception, and had its result
assigned at `:105` and never read. Six epic decision logs were destroyed, `epic-46` (960 lines) and
`epic-756` (848) on the day the issue was filed. Reproduced first by the reporter's entry path: an
852-line fixture holding 140 decision entries came back **0 of 140 surviving**, `"ingested": false`
and `"trimmed": true` in the same result object. Three compounding defects, each separately
sufficient: destruction as the primary action; fail-OPEN on an unobservable preservation call; and a
line metric inverted 833× against context cost (a 100-line 2.0 MB file spared, an 801-line 2.4 KB
file cut). Shipped: archive-first to a `.notes-archive/` dot-directory outside the glob, created
`O_CREAT|O_EXCL` so a same-second trim cannot clobber the prior archive; fail-CLOSED (archive does
not land → no trim, byte-identical, loud on stderr); a 64,000-CHARACTER threshold with a 200-line /
16,000-char tail; the dead ingest deleted with its three tests; decision logs never trimmed at all,
matched by NAME rather than by the accident that had been sparing them (a dot in the stem failed
`PROJECT_NAME_RE` — the only reason `sysop.handoff.md` survived at 1,670 lines); and a new
`hooks/decision_log.py` append-only store at `claude_docs/decisions/<project>.jsonl`, injected 15
records deep at session start. **Sabotage did real work twice.** The first run showed the
fail-closed test passing with the guard REMOVED — a read-only parent had been blocking the notes
write too, so the OS was doing the test's job; that is why the archive got its own directory. The
second proved the `flock`+`O_APPEND` deviation from AC8 load-bearing: swapping in
`atomic_write_text`'s read-modify-write lost **31 of 40** concurrent records. Gate economics: the
Step-4 adversarial layer returned 6 findings (1 High) and rewrote the plan's constants before a line
was written; two review rounds, both `gpt-5.6-sol` seats against `anthropic` authorship, returned
0 Critical / 3 High / 8 Medium / 4 Low and then 2 High / 3 Medium / 2 Low — and materially changed
the fix each time (ignored `os.write` short returns; universal-newline translation eating every
`\r` so the archive did not byte-preserve what it archived; a truncated final JSONL record
POISONING the next append; a 40-digit env value making bash's own `[ -gt ]` fail so the round-1
clamp silently did not clamp; a no-progress guard that measured the tail but not the header it was
about to prepend, so the file stayed oversized and churned a fresh archive every session). **Eight
of the reviewers' findings were false-greens in this change's OWN tests** — including
`test_invalid_project_name_rejected`, whose fixture stem `passwd` is a perfectly valid project name
and which asserted only `rc == 0`. Owner decisions: the two-round review cap held (round-2 fixes
therefore ship unreviewed, stated on the PR); the decision WRITERS were redirected in-PR rather than
deferred (D-847-1) — there was no programmatic writer to change, only `skills/epic-run/SKILL.md`
prose telling the orchestrator to hand-append markdown, now `decision_log.py append` with a drift
guard; ONE High deferred with acknowledgement (D-847-2) — the pre-existing `flock` + `os.replace`
inode race, unchanged by this fix and already modelled by `plan_lib.file_lock()`'s sidecar lock.
Suite 6886→6938/21/exit 0, both pylint lanes 10.00/10, scan clean (iac skip visible). No
workflow-spine change → no diagram REV.

Seventh slot (#762, sessions ff40b6d5 → 89f42f76 → 3fecd708 → f0517473 across three clean-seam
pane-handoffs, v3.112.0, full spine): the wiring child — the executor build seat becomes the
PRIMARY WF2/WF3 implementation path, proven by dogfood (Tasks 3–6 and the WF3 drill each ran the
real mint-gate → dispatch → exact-path collect → audited landing cycle — the first real build-seat
receipts the #762 census demanded; run B's final reconcile closes CLEAN, 8 calls, zero anomalies).
Shipped: audited landing (`landed_work_product` bound to the landing identity, scoped dirty check,
expected-feature-ref persisted at collect and REQUIRED at land — same-base-sha replay refuses,
R5-B), reconcile arms (`unlanded_work_product`/`orphan_landing`/`landing_mismatch`/
`landing_conflict` ok-flipping + the NAMED report-only `pre_cutover_unverifiable` bucket, R5-D),
the owner's verbatim 8-seat matrix retune (analysis budget 2.0→10.0; `INCUMBENT_MODEL` →
claude-fable-5 — the R4-E owner-veto item presented at the merge gate), WF2 §8 prose truth +
delegation-fence flip citing follow-up #779 (filed), WF3 build adoption (R4-D complexity mapping,
mint-gate plan grammar, executor-primary Step 7, subprocess e2e), docs truth flips + the R4-G AC1
atomic recipe — executed LIVE: all 24 active workspace projects now carry declarative
`executorRouting` (owner D30, backup retained), and WF2+WF3 diagram REV 3.112.0. Owner decisions:
D30 (AC1/AC4/AC2-WF3 ratified in-session), D31 (Task 2 orchestrator-inline — an honest task-scoped
gate refuses a bakeoff-gated HIGH task on single dispatch and no build bake-off caller exists; the
live refusal evidence rides the PR, the gap is #779's). Two-run chain by design (#474): T3's
digest change ended run A's epoch; run A closed honestly non-reconciled (2 documented dead Step-2
attempts + the probe row in pre_cutover), run B reconciled clean. Gate economics: design 3 passes
(24 unique findings, 17 dispositions, budget-exhausted close per five owner precedents — itself
the second owner-veto item); plan gate 7 findings; Step-8a wave 3 findings (the reconcile
land-time-binding High) + 4 pre-existing stale pins; Step 9 caught 9 MORE stale-pin regressions
outside T3/T5 allowlists (driver-bench fixtures + the WF3 regex pin — the process finding: a
retune task's allowlist must carry every pin-guard surface of the constant it changes), suite
6447→6514/21/0 exit 0; Step-11 wave 2 executor reviewers → 5 unique (1 High, three layers
independently converging on the item-4 baseline-destroying restore — now landing-state-aware; +
landing/reconcile conflict-identity parity, run_id one-to-one validation, retune prose now
guard-tied to `DESIGN_MODELS` + the routing table, seat-derived WF3 DISPATCH type), all fixed
red-first; adversarial diff layer failed (truncated) — recorded loudly, leads source-adjudicated.
Scan clean (iac skip visible). All red-before-green; loop-backs design 2/2, global 2/3.

## Epic #756 — earlier slots · v3.111.0 and prior

Sixth slot (#765, session 4ac8c4b0 → b9a74758 → c2710ce0 across two clean-seam pane-handoffs,
v3.111.0, full spine after an honest lane re-run): the bake-off decision child — the WIRE-vs-RETIRE
fork the #735 gate filed. Owner D26 chose WIRE (the RETIRE recommendation presented and declined,
two-way /ask-owner RG-761044); owner D27 mid-run scoped it DISABLEABLE-first (`designBakeoff.enabled`
opt-in, default OFF, fail-open-to-off; hardening follow-up filed as #775, owner-joined to the epic
at the queue tail, D28). Shipped: run-identity plumbing through `run_competitive` (additive;
legacy callers keep their exact record shape — the `correlation_id` key is OMITTED, not null),
the workflow-callable `design-round` CLI (identity REQUIRED + grammar-validated, `--winner-out`
delivering the winner's exact bytes as the design draft, whitelist-only sanitized `--evidence-out`
with rubric-bound score keys + `judge_model`, raw sinks refusing ANY committable path in ANY git
work tree, in-path gate enforcement exit 4, empty-brief and output-collision refusals), a LIVE
glm-judged sol-vs-opus proving round committed as fail-closed AC2 evidence (regenerated under the
final hardened code, run `wf2-765-c2710ce0`), the Step-3 prose/diagram wired-opt-in truth with
`_WIRED/_GATE` drift pins and the stale-#472 sweep, and diagram REV 3.111.0 (station 3 delta,
snapshots regenerated with the draw-in-animation wait fix — the shipped pair had been clipped).
Gate economics: design 3 passes (10 findings, D25 budget-exhausted close per owner precedent);
plan gate 6 adversarial findings; Step-8a wave 2 reviewers over the two high-risk commits (the
winner-bytes-discard High caught there); Step-11 wave 2 executor reviewers + adversarial diff —
13 unique findings, the winner-DELIVERY contract gap raised independently by ALL THREE layers
(fixed: the operative command now names `--winner-out`), 12 adopted + 1 declined with disposition
(tri-state gate vs the owner-ratified D27 fail-open-to-off; #775 owns the diagnostics). No
loop-back consumed at Steps 9/11 (design 2/2 spent at the gate, global 2/3). All red-before-green
(10 red Step-11 tests). Suite 6414→6447. PR #776; merge/CI: filled by the next slot's pass.

Fifth slot (#767, session 0dfdd03a → 4ac8c4b0 across a mid-child pane-handoff at the Step-11
JOIN seam, v3.110.1, small-standard lane, lane-widened 8>7 at Step 9): the blocker #735's first
real build dispatch exposed — a contained mutating build agent CANNOT commit (the linked
worktree's gitdir is read-only under containment), and a live probe REFUTED the issue's
widen-the-grant candidate (`git add` already needs the common objects DB; a sufficient grant
opens most of the shared `.git` that spike-#452 containment protects). Shipped: orchestrator
collection as the sanctioned mechanized path — `collect_work_product` generalized to an
exact-path per-task policy (`promote_paths_only`, component-tuple equality; repeatable
`--promote-path`), promotion identity on the durable intent + audited v2 bindings
(`intent_conflict` on mistargeted retries, legacy v1 logs readable, reconcile never
cross-matches versions), Step-8 prose two-tier contract (executor: collect onto
`refs/rawgentic/collect/<nonce>` then guarded landing; legacy: cherry-pick/fast-forward),
whole-issue delegation fenced fail-closed pending #762. The Step-11 wave (9 unique findings,
one TOCTOU breaker adjudicated at source, decision log D23 — owner asleep D21, D22-pattern
self-resolution, veto point before merge) hardened the route end-to-end: unconditional
empty-work-product guard, canonical-ref + create-semantics ENFORCEMENT for exact-path collects
(`invalid_collect_ref`; `--kind code` requires paths), every unlanded legacy intent refused,
strict semantic intent validation + a landed-detection guard before identity rebinds (F-l
double-spend closed), the **production `land-work-product` CLI verb** (clean tree, exact
symbolic ref, tri-state SHA, ff-only, postconditions, CAS temp-ref delete — integration tests
exercise the production op), `PromotionResult.content_tree_sha` binding all three worktree
snapshots (A==B in collect, B==C in reconcile), and mutually exclusive audit binding schemas
(hybrid v2-fields-no-version refused, exact non-bool int version, writers reject empty
identity). Audit-side FEATURE-ref landing binding deferred to #762. Dispositions ledger 19.
All red-before-green (24 red Step-11 tests). Suite 6348→6414. PR #774, squash-merged
`e9c10b0` 2026-07-31, CI green (test+lint hard, security-review advisory — all green).

Fourth slot (#758, session f6996e2f, v3.110.0, small-standard lane): owner-authored goals now
carry VERBATIM across pane-handoffs, enforced at the handoff boundary — the measured failure was
accretion (owner goals 1,200–2,000 chars; model-drafted successor goals 4,000–5,400; the #720
override rode inside one). Shipped: `live_owner_goal` (trusted-origin rows only — top-level
attachment + sentinel + boolean met — with strict tail-ambiguity refusal on destructive reads),
`validate_goal_carry` (armed-form exact comparison, one documented trailing-newline
normalization, affirmative-only owner override recorded in the audit output solely when
consumed), fail-CLOSED provenance on the ad-hoc retirement path, and
`strict_goal_binding`/`expected_predecessor_goal` on `perform_handoff` (the destructive clear
re-reads and REFUSES on ANY divergence from the validated snapshot, including a goal appearing
where none was; uncoupled params refused via an explicit-snapshot sentinel). Gate economics:
three-pass design gate (11 unique findings, both design loop-backs consumed, owner closes D15
+ D18 in-session — the owner was ACTIVE this leg: goal-drift question answered with sha-identical
hashes across all 5 sessions, epic body corrected to 20 children, #767/#765/#766 folded in and
the executor block prioritized); Step-8a wave caught its own Critical (the #707 clear
classification consulted sentinel-insensitive helpers AFTER strict binding passed) + the
origin-binding gap (`sentinel: true` is forgeable — trust needs the attachment location); the
Step-11 wave added affirmative-only approval (an owner "no" through the flag never authorizes),
torn-tail ambiguity refusal, the truncation guard, and param coupling — pre-existing
campaign/mid-child recursive-reader exposure filed as #772, never silently absorbed. All
red-before-green. Suite 6292→6348. PR #773, squash-merged `158b6ee` 2026-07-31, CI green
(test+lint hard lanes + security-review), issue auto-closed, epic box ticked.

Third slot (#732, session b8905b86, v3.109.8, queue-front by owner decision D13): the trap that
strands sessions at the advisory tier — the meter's own text sent them to `clear-prep`, the
skill that prepares a handoff and never performs one (#713 fixed the directive tier only), and
the pane-handoff provenance gate recognized only directive markers, so from the advisory tier
there was NO compliant path to a successor (this run's own field evidence: three observed
outcomes). Shipped on the small-standard lane (the epic's first): the advisory branch names
pane-handoff as the route (owner wording verbatim, next-clean-seam timing kept — the tiers
decide WHEN, never WHETHER); `herdr_available` threads pure through `nag_text` so the headless
no-launcher branch prefers pane-handoff over stop-and-wait when a pane exists; the provenance
gate becomes a directive-first two-tier `compgen -G` disjunction where the printed marker's
tier is the timing authority, never the injectable reminder text (Step-8a security High).
Review catches en route: one `ls` with two globs exits 2 in the advisory-only case even while
printing the marker (Step-4 pass-2); a bare `.*.emitted` would admit future marker types
(Step-4 pass-1 High → design loop-back 1/2); empty session id fails closed — all pinned by
executable fixture tests that RUN the gate command per case, plus full-canonical-sentence
drift guards per branch. Adversarial diff High declined with a dispositions entry
(attended-unconditional mirrors the #713 directive shape; AC4 conditions only the headless
branch); freshness + standing run-contract authorization deferred to #760. Suite 6273→6292.
PR #771, squash-merged `25ecb5c` 2026-07-31, CI green (test+lint hard lanes), issue
auto-closed, epic box ticked.

Second slot (#733, session cf8ac68a → 33b3f9ef across two pane-handoffs, v3.109.7): the defect
class the epic is named for — a SIGKILLed executor seat returned `ok: true` / exit 0, so a
killed review read as a passed gate. Root cause: `verify_post` answers identity, never success,
and every consumer equated the two. Shipped: the `contract.observation_process_failure`
allowlist predicate ({ok, usage_unavailable}, deny-by-default) at all six consumers; partial
output preserved and flagged on every correlation-owned failure; class-derived retryability
(proven death only). The 3-pass design gate consumed both design loop-backs (owner RG-562362,
RG-255373); the Step-8a wave adopted 6 findings (remediation 423f956); the Step-11 pre-PR wave
survived a mid-wave Claude process restart (both executor jobs completed and were consumed from
their observations — the fix's own discipline applied to its review) and adopted 4 more Highs
(8b5b793): residue never retryable, evidence-first verdict ordering, usage_unavailable requires
a payload, audit read-boundary validation + receipt-bound collect authorization. Suite
6194→6273. PR #770, squash-merged `9bdce86` 2026-07-30, CI green (test+lint hard lanes), issue
auto-closed, epic box ticked.

First slot of the epic #756 auto-run (17 children, AUTO MODE, session 048888d0). #735 was the
spike-turned-fix: the executor machinery was proven working, but the always-loaded manuals named
the legacy Agent-tool types for the work the executor owns, and documented a fallback the canary
refuses (F7). Shipped: fix shape (a), owner-ratified through THREE two-way ask-owner round-trips
(fork RG-065627; amended F7 policy RG-227616 after the gate proved the ratified fallback
infeasible; budget-exhausted escalation RG-662910). The design took 3 gate passes x 2 cross-model
reviewers (37 findings, 19 High dispositions, both design loop-backs consumed) and the pre-PR
review hardened the fix's own residue (6 findings, 3 High fixed pre-PR). Acceptance highlight:
the FIRST `executor:build` dispatch by a real WF2 run in recorded history (correlation
`735-task2-build-r1`, gpt-5.6-terra, completed 100s, 186 in-worktree tests green) — which
immediately found two new defects by existing: the gate refuses bakeoff-true plans outright
(`gate_requires_bakeoff` — systemic note on #762) and a mutating build agent cannot COMMIT
(worktree git metadata read-only → #767; recovered by orchestrator collection with provenance,
commit `ba4ceec`). Issues filed from the run: #765, #766, #767. Out-of-repo manual edits land
post-PR behind a normalized read-back merge gate (applied + read-back PASS, epic log D5/D7).
Suite 6190→6194. PR #768, squash-merged `c46d8e8` 2026-07-30, CI green (test+lint hard lanes),
issue auto-closed, epic box ticked.

## Epic #626 — the context meter: from a nag nobody acts on to a prompt that lands

Section opened by the #718 slot. #713 (`d005749`) and #716 (`2c1e298`) shipped earlier the same
day without adding their sections here — shared-doc mode was skipped on both, so their detail
lives only in the README changelog and their own planning docs. Recorded rather than
back-filled, because a section invented after the fact carries none of the run's telemetry.

### #718 — the act tier INSERTS A PROMPT, it no longer only emits text · v3.108.0

**Problem.** The meter's entire output was `print(payload_out)` — a JSON envelope of English.
#713's probe 12 had already measured a model naming that injected directive as possible prompt
injection and **refusing its imperative** while faithfully reporting it. Injected hook text is
data; only user input is authoritative.

**What shipped.** `launcher_lib.py insert-prompt` (terminal primitives stay in that one module by
`skills/pane-handoff/SKILL.md`'s standing rule) pastes PROSE into a named pane, waits
`INSERT_SUBMIT_DELAY_S = 1.5`, then sends a separate `Enter`. `context_meter.py` calls it at
`Stop`, directive tier only, on its own `stop-insert` reservation, herdr-only, project-config
required, with a `contextMeter.insertPrompt` kill switch.

**The two measurements that decided the design** (design doc:
`docs/planning/2026-07-29-718-meter-inserts-prompt.md`, rendered HTML alongside):

| Measured | Result |
|---|---|
| Prose into an UNMET `/goal` loop | **Acted on in 17 s** — marker file written while the next tick file did not yet exist |
| Bare slash command, same conditions | **Inert** — queued through five goal-driven turns, taken up only after `✔ Goal achieved` |
| A registered `Stop` hook typing into its OWN pane | Inherits `HERDR_ENV`/`HERDR_PANE_ID`, may subprocess — but an **immediate `Enter` returns rc 0 and submits nothing** |
| Same, with 1.5 s between paste and `Enter` | **Submits.** The "hooks block input" hypothesis was refuted; round 1 was a paste race |

**Reviews.** WF5 design review (gpt): **6 findings, 2 High, all six confirmed against code, none
refuted.** Four became design changes; two became the live spike above, which caught the
unsubmitted-Enter bug **before** it shipped green. Dispositions table in the design doc, since
`docs/reviews/` is gitignored.

**Gates.** Suite **6131 → 6175 passed**, 21 skipped, rc 0 (baseline recorded on `2c1e298` before
any edit; one PID-reuse flake in `test_quota.py` seen once and green in isolation and on re-run).
Both pylint lanes 10.00/10. Security scan PASS with `iac: not applicable` as a visible skip.
Version bumped on all four surfaces + `canary.EXPECTED_REGISTRATION_DIGEST` re-pinned, because
editing a registered hook script invalidates it. No workflow-spine change → no diagram REV.

---

## Epic #684 — make the blocked-pane watcher actually fire

### #679 — poll pane state + key on `state_change_seq` (small-standard lane) · v3.104.1

**Two independent reasons the #612 watcher could never notify anybody, and only the first was
known.** Cause 1 (what #679 diagnosed): `events.subscribe` on herdr 0.7.5 delivers a ~39-frame
backlog burst and then nothing, ever — five instruments, including a controlled stimulus (three pane
renames on a live subscription → 0 frames) and duration-independence (39 events at 12 s, 39 at 12 s,
39 at 50 s). Cause 2, found by RUNNING it: a real Claude pane driven to a real permission prompt went
`working@revision 3` → `blocked@revision 3`. herdr bumps `revision` on pane-record changes, not on
every `agent_status` transition, so `Reconciler.accepts` — which requires a strictly newer key —
refused the one frame the feature exists for. The watcher polled happily, `poll_failures: 0`, and
sent **zero notifications**. The `revision 1 → 3` figure in #679/#684 is real but spans TWO
transitions, and that is what hid it.

**Shipped.** `poll_lines` (a drop-in for `socket_lines`; the brain is untouched because
`watch_stream` already took its lines as an argument), `merge_agent_sequences` + `_revision_of`
(the key is `state_change_seq` from `snapshot.agents[]`, falling back to `revision`),
`clamp_poll_interval`, `write_heartbeat(extra=)` carrying `polls`/`poll_failures`, and
`--source {poll,events}` / `--poll-interval-s` with **poll** the default. The subscription path is
RETAINED behind the flag — one flag away if herdr repairs the feed, and its tests are the written
record of what those five instruments learned.

**Decisions (this slot).**
- D-1 (owner): poll, not `events.wait` in a loop (contract unverified, may drop transitions between
  calls) and not upstream-only (would leave the watcher dead and W2–W7 BLOCKED). No upstream filing.
- The 15-minute pre-check the epic mandated came back NEGATIVE and is recorded on #679:
  `projects/herdr-dashboard` has no socket client at all (only dep `textual`); it reads pane state
  through the herdr **CLI** and refreshes on discrete UI events. So the subscription was
  unchallenged by it — and the one other project on this box that shows live pane state polls too.
- Owner elected the **small-standard lane** (design ceremony dropped; TDD, Step 8a, Step 11,
  security scan, CI all kept).
- The detection gap is documented as CUMULATIVE rather than "one poll interval": the generator is
  pull-driven and one drain walks every pending registration and every pending send sequentially. A
  persistent block is always detected on the next sample, so what degrades is lateness, never loss.
- Owner authorised a FOURTH revision round past the exhausted `design` loop-back budget, to fix a
  High the third review pass found (agent detach killing the watcher).

**Reviews — three design passes, each of which found something real.** Pass 1: 6 findings (5 High)
tripped the volume loop-back. Pass 2: 4 High + 1 Medium, two of them reproduced by the reviewer.
Pass 3: 1 High + 2 Medium + 1 Low, the High being a routine agent detach read as `2148 → 5` and
killing the whole watcher. One item was DECLINED with its reason recorded (making ordinary generator
exhaustion fail-loud in `watch_stream`: unreachable from either shipped source, and it would break
~20 existing tests that legitimately feed a finite list). Step 8a (2 reviewers) + Step 11 + the
adversarial diff review ran on the committed diff. Security scan PASS (iac not applicable).

**Status.** Suite 5618 → 5663, 0 failing; both pylint lanes 10.00/10. Verified live three times
against a real herdr server — run A failed (revision key), runs B and C passed — and the failure is
the only reason cause 2 was found. Evidence retained at
`docs/planning/2026-07-28-667-uat-plan/harness/evidence/679-t5-live-ac3-2026-07-28.md`. No
workflow-spine change → no diagram REV. PR + CI + merge SHA per the established next-slot convention.

---

## Standalone — #647: backend-resolved run-status probe + `liveness_unknown` · v3.97.2

**The last instance of the #638 defect class, in the read-only status surface (small-standard
lane).** `hooks/executor_routing_lib.py`'s `live_fn` probed `tmux -S <record.run_socket>
has-session` unconditionally. A herdr-backed record's `run_socket` is a herdr *workspace id*, so
tmux exited nonzero for an ordinary reason, the closure returned `(False, None)` with no
`probe_error`, and `derive_state` reported `exited_no_sentinel` — a positive claim that the job
EXITED, from a probe that never addressed the right runtime. Same shape as the eleven #638 review
passes: an operational failure read as a definitive answer.

**What shipped.** `supervisor.resolve_backend()` lifted to module level with
`TmuxSupervisor._resolve_backend` delegating to it — necessary, not cosmetic: the status surface
cannot construct a supervisor, because `__init__` builds a `JobRegistry` whose own `__init__`
mkdir/chmods the registry root, a write `AC-J3` forbids (the same reason #471 W8 lifted
`read_sentinel`/`derive_state`/`run_status`). New module-level
`status_live_verdict(record, *, tmux, herdr, tmux_present)` with the backends INJECTED, mapping
the existing `Liveness` tri-state; `derive_state` gains keyword-only `liveness_unknown`
(default `False`, so `TmuxSupervisor.status()` is byte-identical) and `run_status` derives it
from `probe_error is not None`, so `live_fn`'s injected contract never changed. The new state is
derived-only — never written to the registry, so `JOB_STATES` and `recorded_state` still
distinguish every OQ-8 state. The tmux-availability check moved after resolution: evaluated
first, a herdr record on a tmux-less host reported `"tmux unavailable on this host"` — a true
flag with a false reason. Beyond the ACs and flagged as such, `resolve_backend` now raises on an
unrecognised backend instead of falling through to tmux.

**Reviews.** Step-8a ran two cross-model lenses on the executor `review` seat
(`cross_model_author` enforcement routed both to gpt-5.6-sol, the author being Claude/anthropic).
Mechanical: 1 Medium — the five helper tests pinned the verdict function but left the `live_fn`
BINDING free to regress; accepted, and the fix is a test driving the real `_do_status` with an
injected backend, its red-before-green proven by a throwaway discriminator. Silent-failure/
security: PASS, having independently traced that an absent `HERDR_WORKSPACE_ID` cannot mislead
status (the surface passes the persisted `record.run_socket` straight to `probe_session`; that
variable is only used by `resolve_endpoint()` on the LAUNCH path). Step-11 whole-diff: PASS,
mergeable, plus 1 Low — the new public contract in `docs/config-reference.md` was unpinned, now
guarded on a paragraph-sliced anchor. Step-11 adversarial diff (WF5, gpt backend): 2 Medium —
one REFUTED against the code (`HerdrBackend.__init__` neither rejects an absent `workspace_id`
nor performs I/O), one accepted (the resolver fallthrough above). Security scan PASS, 1 visible
skip (`iac`, not applicable).

**Notable.** The first WF2 attempt dispatched Step 8a via the Agent tool and both reviewers
correctly REFUSED (`architecture_self_check`: the bundled agents are the legacy rollback target
and this workspace declares no `defaultArchitecture`). The executor path then denied on
`author_provider_missing` — `enforce.py:193` fails closed for a `role: "review"` seat so the
cross-model rule cannot be silently inert. Both denials were the system working.

**Status.** Suite 5166→5183, 0 failing, exit 0. Both pylint lanes exit 0. No workflow-spine
change → no diagram REV.

---

## Standalone — #535: rev-diagram snapshot script — fullPage dual-theme capture + gate · v3.94.0

**First WF2 run under the executor architecture (owner-ordered smoke, small-standard
lane).** New `skills/rev-diagram/scripts/snapshot.sh`: serves `docs/` locally, drives
the pinned `npx playwright@1.61.1 screenshot --full-page --viewport-size=1440,900`
CLI at both themes — forced via a new `?theme=light|dark` URL query-param bootstrap
read in `docs/workflow-diagram.html` (no custom Playwright library script; no
committed npm project — `require('playwright')` does not resolve standalone, only the
CLI binary does, verified live) — writes the two fixed asset paths atomically
(temp-capture, backup-then-promote, restore-on-gate-failure), then runs
`tests/test_workflow_diagram.py -q` and propagates its exit code. Hard-codes
`--full-page`, the 1440 viewport, and both paths — the actual prevention mechanism for
the documented viewport-clip failure mode.

**What shipped.** The script; the theme-bootstrap read; a new dependency-free
PNG-header height drift guard (`tests/test_workflow_diagram.py`) plus a
byte-identical-snapshots guard; `tests/test_rev_diagram_snapshot.py` pinning the
pinned invocation and the light/dark mapping end-to-end (var → tmp → capture call →
promotion rename); `docs/workflow-diagram.md`'s recipe now leads with the script,
manual recipe kept as fallback.

**Reviews.** Step-4 design self-review (executor `review` seat; `cross_model_author`
enforcement routed it to gpt-5.6-sol since the author is Claude/anthropic) found 1
High + 2 Medium — all independently re-verified against primary sources before being
applied, not accepted on the reviewer's word: an untested `--device-scale-factor` flag
that doesn't exist in 1.61.1 (design cited a spike that didn't match its own shipped
invocation — pinned the version, dropped the flag); an overclaim that the existing
snapshot test already caught a viewport clip (it only checked existence + a size
floor); a buffered-stdout port-parse race in the naive `http.server 0` design. Step-11
pre-PR review (single lane reviewer, security/strong seat) found 2 more Medium, both
confirmed and fixed: the script wrote directly to the committed PNG paths with no
atomic promote (a mid-run failure could leave them half-overwritten despite reporting
rejection); the theme-param test never asserted the actual `setAttribute` call, only
that the read preceded render(). Fixing the atomic-promote finding surfaced a THIRD,
previously-undiscovered bug caught by the implementer's own mandatory live re-run (not
a reviewer): `mktemp`'s random suffix broke Playwright's extension-based mime-type
detection ("unsupported mime type null") — fixed by preserving the `.png` extension in
the temp filename. Adversarial diff review: gated off, no security-surface path in
this diff. Security scan: clean (0 findings).

**Status.** PR + CI + merge SHA + telemetry filled by the next slot's pass
(established convention — this slot's PR is still open as of this section being
written). Two reusable gotchas memorized to mempalace (executor-dispatch timeout/
author-provider/correlation-id semantics; Playwright-CLI/`http.server` buffering) —
see `claude_docs/session_notes.md` "WF2 Step 10" for this issue.

---

## Standalone — #552: bare skill `name:` frontmatter — un-double the slash commands · v3.79.1

**Interrupt fix, owner-ordered mid-epic-475-pause (2026-07-20).** New Claude Code builds
namespace plugin skills themselves and forbid a colon in the `name:` field; the embedded
`rawgentic:` prefix (a deliberate 2026-03 design choice) was colon-sanitized and every
command doubled (`/rawgentic:rawgentic-switch`), unregistering all old-style
`/rawgentic:<name>` invocations — including the epic-475 resume script's. Control case
proving the mechanism: `sync-security-patterns`, the one bare-named skill, registered clean.

**Fix (root-cause, minimal).** Strip the prefix from all 20 `name:` fields — the 145
in-body `/rawgentic:*` cross-references, the resume script, and the handoff needed ZERO
edits (the old names simply re-register). Registration checker now requires the bare form
with frontmatter-scoped, newline-safe matching; new guard `tests/test_skill_name_frontmatter.py`
(red first: 20 named violations). Bonus: `peer-consult` description quoted — pre-existing
Codex-validator YAML failure; validator now passes 21/21. Found+documented the FOURTH
version surface (`phase_executor` `canary.py` `EXPECTED_PLUGIN_VERSION`, since #470);
CLAUDE.md version rules ×3→×4.

**Reviews.** WF5 gpt adversarial on the RCA: 5 findings (1 High — Codex-mirror
verification, resolved via validator evidence; 4 Medium — all applied: bare-only checker,
red-test-first ordering, UNVERIFIED old-build claim, live post-reinstall acceptance).
Step-9 2× opus: silent-failure-hunter caught a REAL Medium in the new code (`\s*` eats
newlines — empty `name:` + stray line passed both guards; confirmed live, fixed, 3 new
red tests); code-reviewer 0 Critical/Important, scope confirmed complete.

**Status.** PR + CI + merge SHA filled by the next slot's pass (established convention).
Post-reinstall acceptance is the owner's: listing shows `/rawgentic:<name>` ×21, no
doubles, `/rawgentic:switch rawgentic` resolves. Follow-up (out of scope, pre-existing):
`skills/create-issue-workspace/skill-snapshot/SKILL.md` naming vs org-validator rule.

## Epic #529 — run-speed levers from the #509 profiler (auto-run)

**Status: IN PROGRESS (started 2026-07-20, auto-merge scoped grant D-1).** Queue #526 →
#527 → #528 (independent, benefit order). Ship the three time-to-completion levers the
epic #509 run profiler measured (PR #525) — recover ~70 min per comparable epic run
without weakening any gate.

### #526 — epic-run: notify at human-blocked points + launcher at run start · v3.72.0

Epic #509 lever 1: the single biggest wall item was a 56.3-min owner-away stall (18% of
wall) between a Step-11 verdict landing and the owner's resume — the run had no signal it
was blocked on a human. epic-run SKILL.md Step 4 now directs the driver to notify the
owner at every human-blocked point via the workspace `notify-owner` skill when available
(visible fail-open skip marker when not — the notification layer never blocks the run);
Step 2 recommends arming the durable resume launcher (`long-run-resume` system-crontab
pattern) at RUN START beside the merge-policy question — same stall class covers
unattended quota pauses. Small-standard lane; suite 3874→3876, 0 regressions; guards
`TestEpicRunOwnerNotification` red-before-green.

- PR #539, squash-merged b6cd0ba (2026-07-20), 4/4 lanes green on 3ds-fleet-linux.
  Run deviations D-4/D-5/D-6: GitHub Actions partial outage → mid-run CI migration to
  the org self-hosted fleet (PR #540, 9a8d617; group now allows public repos with
  all_external_contributors fork-PR approval).

### #527 — WF2 pre-PR gate goes scoped for prose-only post-Step-9 fixes · v3.73.0

Epic #509 lever 2: 5/9 children re-ran the full suite (~2.4 min each) at Step 12 for
guard-pinned prose tweaks. `<test-run-discipline>` exception (a) + steps.md §12 item 4
now carry a precise file-list predicate (prose/docs + own guard tests only; hooks/,
phase_executor/, scripts/, shared behavior code, and shared test infrastructure all
force the full re-run) with the scoped set = affected guard files + the version-pin
test, consuming the Step 9 full-suite result as regression evidence. Review breaker
fired once (shared-test-infra edge, owner elected tighten — predicate now fully
mechanical). Small-standard lane; suite 3876→3877; guard red-before-green.

- PR #541, squash-merged c9b6533 (2026-07-20), 4/4 lanes green on 3ds-fleet-linux (serial,
  pre-D-8; hosted-first routing restored after via #542/v3.73.1).

### #528 — skill-registration surface checker: every count pin, computed · v3.74.0

Epic #509 lever 3: the new-skill registration walk cost ~4 min + one full-suite round-trip
per skill, including a burned round-trip on a second hand-pinned SDLC count in
`tests/test_interview_skill.py` that the guard-file subset doesn't cover. New
`hooks/skill_registration_check.py` (pure core + thin CLI, fail-closed): given a skill
name, prints every registration surface current-vs-expected — frontmatter, whitelist
position + whitelist==disk, codex symlink, MANIFEST membership, config-loading canary,
computed README count strings — then grep-sweeps ALL hand-pinned count copies (tests/,
README body, plugin/marketplace/codex descriptions, .rawgentic.json; negative pins and
the Changelog excluded); exit 1 names each stale surface. The `add-skill` workspace skill
and `docs/skill-development.md` run it as the verify step. Its own first sweep caught a
live straggler (.rawgentic.json "8 SDLC" vs pinned 9 — fixed in-PR). Small-standard lane;
suite 3877→3918, 0 regressions; 41 tests (37 core red-before-green); Step-11 2-agent
review yielded 6 unique findings, all resolved (negative-pin per-occurrence match,
fail-closed encoding walk, fullmatch anchor, changelog count fix).

- PR / merge SHA: filled at close-out.

## Epic #493 — WF2 speed levers (manual drive)

**Status: COMPLETE (2026-07-19, 6/6 merged, epic closed)** — #488 (PR #495, 46ae9b0, v3.58.0); #489 (PR #496, a59096f, v3.59.0); #490 (PR #497, 031e01c, v3.60.0); #491 (PR #498, f01e6bc, v3.61.0); #492 (PR #500, 88f01c0, v3.62.0); #494 (PR #503, 72f1d04, v3.64.0). Suite 3634+10skip → 3695+10skip across the epic, zero regressions. Levers inert for live sessions until owner plugin reinstall + fresh session. Follow-up: #502.
Owner D-5/D-6 (2026-07-18): built BEFORE #475 resumes — #475 (and #467 W4, paused at Task 1
@ 5c1c880) stay paused until this epic merges AND the owner reinstalls the plugin + fresh session.

Per-child WF2 wall-clock levers derived from the epic #475 run-timing profile (~1h52m/child;
review-wait ~20% of wall-clock, much of it orchestrator idle-blocking). All children run the
small-standard lane; the levers are applied MANUALLY while building them (scoped pytest during
iteration, probe-before-design, pipelined reviews). AUTO MODE merge grant (owner, 2026-07-18,
scoped to this run).

### #488 — review-wave pipelining: never idle-wait · v3.58.0

New canonical `<review-pipelining>` block in implement-feature's SKILL.md: after dispatching any
review wave (Step 4 design critique, Step 8a per-task, Step 11 pre-PR), immediately draft the
next phase's non-committing artifact (plan, next task's tests, PR body, version/changelog edits)
and reconcile findings on the wave's return. Hard boundary pinned in the same block: committing,
branching, pushing, and every gate verdict still WAIT — only idle time is reclaimed, no gate
skipped, no verdict pre-empted; a gate finding always wins over a stale draft. Three wave-site
pointers in references/steps.md (§4 item 7 · §8a item 2 · §11 item 2), single-source per the
drift-guard doctrine; guards: `TestReviewPipelining` (canonical sentence, gate-semantics
sentence, ≥3 pointer sites).

- **Lane run:** small-standard (standard_feature, 4 impl files ≤ 7; laneImplExtensions
  markdown-is-product). TDD red (1e6eba0) → green (91f8127); suite 3634+10skip → 3637+10skip.
- **Dogfood note:** the run itself pipelined its own Step 11 — PR body + this section drafted
  while the lane reviewer ran.
- **Reviews:** 1 lane reviewer (opus) + adversarial diff review skipped (no security surface).
  No workflow-spine change → no diagram REV.
- PR #495, squash-merged 46ae9b0 (2026-07-19), all 4 CI lanes green.

### #489 — scoped tests during iteration: full suite exactly twice · v3.59.0

New canonical `<test-run-discipline>` block in implement-feature's SKILL.md: FULL suite exactly
twice per run (Step 2 baseline, Step 9 final regression gate); Step 8 iteration runs the SCOPED
suite for the area under change; a scoped run never substitutes for the final full-suite gate.
Documented scoped-path convention (mirror the changed area into the test tree; prose → its
pinning guard file) + two evidence-driven exceptions (Step 12 re-run only on post-Step-9
code/test-pinned commits; invalid baseline re-records). Four steps.md sites amended (§2 baseline
record incl. tree-hash carry rule · §8 item 1 · §9 Part B · §12 item 4); guards:
`TestTestRunDiscipline` (exactly-twice sentence, never-substitutes sentence, ≥3 pointer sites).

- **Lane run:** small-standard (4 impl files). TDD red (b69f3f5) → green (efd504b); suite
  3637+10skip → 3640+10skip.
- **Dogfood note:** baseline carried from #488's final gate by tree-hash identity
  (46ae9b0^{tree} == f452ad7^{tree}) — this child ran the full suite exactly once locally.
- **Reviews:** 1 lane reviewer (opus): 1 Medium fixed-in-gate — the per-task delegation paths
  (steps.md item 3 + 2 restatements) still mandated per-task full-suite runs, contradicting
  exactly-twice; fixed 78d403e. 1 Low band-dropped (0.60 < 0.90). Adversarial diff review
  skipped (no security surface). The fix touched a test-pinned surface post-Step-9, so the new
  Step-12 rule itself mandated the full re-run (3640/10 unchanged) — the exception fired
  correctly on its own shipping PR. No workflow-spine change → no diagram REV.
- PR #496, squash-merged a59096f (2026-07-19), all 4 CI lanes green.

### #490 — probe the real platform API before the design · v3.60.0

New canonical `<probe-before-design>` block in implement-feature's SKILL.md: before a design
commits to any load-bearing platform/API behavior, run a SHORT live probe of the EXACT
invocation the design will ship — never a proxy composition — and cite the real result in
`platform_apis:`; a `verified via spike` claim must reference the actual shipped invocation
(#467 post-mortem: two ~25-min design loop-backs traced to proxy-composition spikes). Step 3
platform_apis rules gain probe-before-claim; Step 4 feasibility judgment treats a proxy spike
as blocking; #226 precedent rule untouched. Guards: `TestProbeBeforeDesign` (3).

- **Lane run:** small-standard (4 impl files). TDD red (f973061) → green (8107779); suite
  3640+10skip → 3643+10skip.
- **Reviews:** 1 lane reviewer (opus) + adversarial diff review skipped (no security surface).
  No workflow-spine change → no diagram REV.
- PR #497, squash-merged 031e01c (2026-07-19), hard lanes green (advisory code-review still
  running at merge — owner grant gates on test+lint; follow-up noted).

### #491 — model-tier reviewers: sonnet mechanical, strong security · v3.61.0

New `select_review_lens_model` (`hooks/model_routing_lib.py` + CLI `--lens`): WF2 review
dispatches pick the model per lens — `security` PINNED to the resolved review model (config
override ignored with a warning), `mechanical`/`ac_completeness`/`test_coverage`/`bug_logic`
default sonnet via optional `modelRouting.reviewLenses`. Never-Haiku on every path (8a
hardened: entry-point floor). WF2-local `<review-lens-routing>` block carries the lens map;
shared `model-routing-resolve` untouched (WF3 out of scope). 17 routing tests + 3 guards.

- **Lane run:** small-standard; Task 2 riskLevel high (module boundary) → 8a fired,
  dogfooding its own lens map (R1 sonnet mechanical / R2 opus security). 6 findings:
  3 fixed dd48127 (boundary haiku floor — R2's catch; docstring purity; Final[frozenset]),
  3 band-dropped. Suite 3643+10skip → 3663+10skip (+20).
- **Reviews:** 1 lane reviewer (opus, security lens) NO FINDINGS + adversarial diff review
  FIRED (high-risk task, gpt): 3 findings — 2 Medium adopted (5f0f33c: `--lens`
  review-role-only; malformed `reviewLenses` warns), 1 High refuted with evidence
  (inherit = documented dispatch-site-guard contract; ledger d-491-11-1-adv1).
- PR #498, squash-merged f01e6bc (2026-07-19), all 4 CI lanes green.

### #492 — fewer/tighter review waves: one 8a wave, Step 11 to 2 · v3.62.0

Step 8a → ONE accumulated 2-reviewer wave over every high-risk commit (after the last plan
task, before Step 9; per-task coverage preserved via one log entry per task —
`assert_review_coverage` unchanged; blocking point moves to fix-before-Step-9, the named
trade). Step 11 → 2 reviewers (R1 mechanical+bug/logic fast tier; R2 architecture+security
strong — the security lens is never the one dropped). `STEP11_REVIEW_AGENT_COUNT_FULL` 3→2,
one-wave `estimate_agents`, shared-block examples corrected, mirror guards recomputed.

- **Lane run:** small-standard; Task 3 (gate-flow prose) riskLevel high → 8a fired as ONE
  wave, dogfooding the rule it ships. Suite 3666+10skip → 3669+10skip (+3 guards).
- **Reviews:** single 8a wave (sonnet mechanical / opus security) + lane Step-11 +
  adversarial diff review (fires on high-risk task) — results in the PR.
- PR #500, squash-merged 88f01c0 (2026-07-19), all 4 CI lanes green.

### #494 — early smoke-install after first runnable commit (deploy-bearing) · v3.64.0

New canonical `<early-smoke-install>` block in implement-feature's SKILL.md: on a deploy-bearing
project (`capabilities.has_deploy`), after the first runnable commit boots something, run a cheap
live smoke-install/boot check (install / start / health) before continuing implementation —
crash-on-boot and environment/port clashes surface as 2-minute fixes instead of hours-later
cutover surprises (the 3dstories-fleet timing post-mortem's move #2: a Config crash + a mempalace
port clash). Capability-gated: code-only projects (`has_deploy == false`, rawgentic itself)
unaffected — the directive never runs there. Step 8 gains the first-runnable-commit site (incl.
whole-issue-delegation collect-time timing); Step 15 gains a never-substitutes note — the
mandatory post-deploy smoketest is not weakened or replaced. Guards: `TestEarlySmokeInstall`
(canonical sentence, gating sentence, Step-15 distinct + ≥2 pointer sites).

- **Lane run:** small-standard (standard_feature, 4 impl files); 0 high-risk tasks → no 8a
  (mirrors #488–#490). TDD red (f8cf18f) → green (5c9f199); suite 3692+10skip → 3695+10skip.
- **Reviews:** lane Step-11 reviewer (opus, security seat) + adversarial diff review per the
  opt-in — results in the PR.
- PR #503, squash-merged 72f1d04 (2026-07-19), all 4 CI lanes green; Step-11 F1 (Low, band-dropped, verified real) adopted as tightening 6f23f58.

## Standalone — #502: entry signatures for the step-state pointer (owner-ordered, post-#493) · v3.65.0

Born live during #494: the statusline sat on "step 5" through all of Step 8 (small-standard-lane
dead zone — Step 6 skipped, Step 7 marker-less, Step 8's marker appends at step end), and the
owner asked twice. Fix: `_SIGNATURES` rows become `(needle, hit, entry_only)`; `git checkout -b `
stamps branch-cut entry (WF2 S7 / WF3 S6, non-monotonic for same-session follow-up issues) and
`git commit` becomes a monotonic entry stamp (WF2 S8 / WF3 S7 — fires only below the target, so
Step-11/12 commits never regress the pointer). `detect_signature` grows optional `current_step`;
`_step_num` compares off the hot path; ReDoS posture + foreign-session gate untouched (tested).
Completion-time advance stays the #499 design — these are the cheap unambiguous entry stamps its
review anticipated.

- **Lane run:** small-standard (standard_feature, 3 impl files); Task 2 (hot-path hook infra)
  riskLevel high → one 8a wave. TDD red (cf7c94e, 8 failing) → green (530af76, 58 scoped, pylint
  10.00); suite 3695+10skip → 3710+10skip (+15 tests incl. the 8a + Step-11 hardening guards).
- **Reviews:** 8a wave (sonnet mechanical / opus security) fixed commit-graph false match +
  classify-definitively ordering (d182273); Step-11 (opus lane + gpt adversarial, 4+2 findings
  identity-merged) adopted branch-name issue REBIND + the pinned compound-input trade-off +
  these count corrections — full dispositions in the PR.
- PR #504, squash-merged f48f0ac (2026-07-19), all 4 CI lanes green (owner-approved merge at CI-green).

## Standalone — #499: hook-level step-state emission (owner-ordered, mid-#493) · v3.63.0

Born live: the statusline froze twice during the #493 run (manual #480 step-entry writes
skipped under batching). New PostToolUse hook `step_state_post.py` — marker detector
(session-notes DONE markers parsed from the append command's heredoc body) + signature
detector (derive/scan/pr-create/pr-merge/summarize), both writing through the existing
`step_state.py write` CLI; conservative context rules (registry session match; foreign
records never stamped); fail-open, empty stdout. Manual per-step calls now OPTIONAL in
all five skills; the no-gating-hook guard recut + PostToolUse-only registration pin.

- **Lane run:** small-standard; Task 2 (hook+registration) high → single 8a wave
  (sonnet mechanical / opus security). 14 hook tests + 1 registration guard.
- PR / merge SHA: filled by the next slot's pass.

## Epic #475 — orchestrator/executor wiring: WF2/WF3 on the executor path (auto-run)

**Status: IN PROGRESS** — 4 children merged (#464 #480 #445 #446), #465 (W2) building, #466–#474 pending. (The roadmap chip aggregates the merged sub-sections below; the epic itself is open until #474 closes it.)

Wire WF2/WF3 to the `phase_executor` engine per the ratified architecture (the real #417).
Doc of record: `docs/planning/2026-07-17-orchestrator-executor-acceptance-criteria.md`.
Queue (topo, D-1 pins + D-2 insertion): #464 (W1) ✓ · #480 ✓ · #445 ✓ · #446 ✓ · **#465 (W2)** ← IN PROGRESS ·
#466 (W3) · #467 (W4) · #468 (W5) · #469 (W6) · #470 (W7) · #447 · #471 (W8) ·
#472 (W9) · #449 (W10) · #473 (W11) · #474 (W12, closes epic). AUTO MODE campaign
(owner grant 2026-07-18, session-scoped); overnight D-3 autonomous posture from ~03:00.

### #464 — capability manifest + WIRED_SEATS full set + attested build-audit path (W1) · v3.52.0

Every routing-table seat carries a required `manifest` (`session_policy` fresh ×7 per D-8,
`tool_grants`, normalized `effort`, per-provider `confinement`, `bounds`), fail-closed at load
(schema draft-2020-12 + semantic passes: confinement↔chain-provider coverage,
`policy.enforced_roles ⊆ ENFORCEABLE_ROLES` evaluator ceiling, name↔role binding lint). Table
grows to 7 seats (+`analysis` sonnet-primary, +`design` = the #428 competitive pair as a static
row whose dispatch stays bake-off-owned; single-dispatch refused). `check_pre` drops the
unconditional build hard-deny for a launch-bound `GateAttestation` (anti-replay
`launch_input_digest`, outcome routing — a `bakeoff` outcome can never authorize single
dispatch; receipts carry `role` + gate evidence; denied-build receipts stay audit-readable);
unrecognized non-empty roles fail closed (#434 part 2, red-before-green on the typo'd-role
cell). `dispatch_seat` gains the gated build path (`--gate-file` + `--plan-context` with EXACT
canonical key-set equality) authenticated via the extracted `complexity_gate.verified_decision`
(+`GateTamperError`, strict empty/missing-key context contract); driver-bench admits build
audits.

- **Design:** r3 — peer consult (gpt) + 3-approach brainstorm + THREE adversarial design passes
  with a dispositions ledger, then a 4th adversarial DIFF pass at Step 11 that caught 2 genuine
  failed-remediations (partial-context acceptance; truthiness-only receipt validation) — fixed
  same-PR. 14-entry terminal-disposition ledger; 2 design loop-backs consumed (budget
  exhausted, pass-3 clean); owner resolved 3 breakers live, Step-11 fixes applied under the
  D-3 overnight posture.
- **Carried forward:** W7 #470 — bake-off audit-spine wiring + orchestrator-minted plan-context
  (issuecomment-5010761468); W2 #465 — runtime manifest resolution per the design-row rule.
- **Session-limit resilience (live):** the 04:20 window hit mid-Step-11 — 3 reviewer agents died
  vacuous and were re-dispatched clean after reset; the codex diff review (separate quota)
  survived and its sidecar was joined on schedule.

### #480 — step-ENTRY state record: machine-readable current position (D-2 insertion) · v3.53.0

Owner-directed mid-campaign insertion. New `hooks/step_state.py` (pure core + thin CLI, stdlib)
writes an observational "now" pointer — `claude_docs/wal/<project>.state.json`, atomic
overwrite-in-place, last-writer-wins — at each numbered step ENTRY of all five workflow skills
(one canonical prose line each, pin-tested). `wal-context` prefers a FRESH record over the
notes-grep, **session-scoped** (a concurrent same-project session's record is suppressed and the
byte-identical grep fallback runs — the raw file stays project-scoped for the owner's statusline,
whose recipe routes through the validating `read` subcommand). Fail-open EVERYWHERE, live-proved
with a crashing-python3 shim; a drift-guard pins that no gating hook references it.

- **Lane run:** small-standard (7-impl estimate; lane-widened to 9 real — version surfaces).
- **Reviews:** 8a on the wal-context commit (converged session-scoping Medium fixed) + lane
  reviewer + adversarial diff (5 Mediums adopted: lazy writer import, reader
  schema/project/freshness-bounds validation, future-timestamp bound, reader-routed statusline
  recipe, collision guard via project-equality).
- **Session-limit resilience:** none needed this child (ran post-reset).

### #445 — per-project phase-seat table in config + single executor resolution · v3.54.0

Foundation child for #446 (setup seed/tweak) and #447 (diagram render): "projects own their
tables" becomes real. New `.rawgentic.json` `phaseExecutorTable {version, file}` descriptor
(complete-replacement pointer, never a merge overlay) → `phase_executor_table` capability
(derive fail-closed incl. control-char/backslash rejection); ONE shared
`executor_routing_lib.resolve_table` serves both consumers — executor CLI (`resolve-seat` gains
`table_source` + `config_digest`) and driver-bench — resolving the declared override (lstat
entry-probe, canonical symlink-safe containment, statically-dead-seat check via public
`target_forbidden_reason`, uniform exit-2 for every declared-override failure) or the new
`phase_executor.routing.default_table_path()` package accessor; `_ROUTING_TABLE_REL` + bench
`TABLE` constants retired. `seed_table` byte-copies the package default (atomic no-clobber
`os.link` publish) for #446.

- **Full spine:** peer consult (gpt, blind) + 3 adversarial design passes (converged pass 3;
  2 design loop-backs consumed — budget exactly spent) + adversarial plan pass + codex diff
  pass; **36-entry disposition ledger** (DF-2 absent-config fail-closed DECLINED citing AC2;
  DF-5 component-swap TOCTOU declined per trust model).
- **Reviews:** 8a on the high-risk resolver commit (2 reviewers, 5 Lows fixed) + 3-agent
  Step 11 (2 Lows fixed incl. exact exit-5 pin) + codex diff (3 adopted, fixed in-branch).
- **Session-limit resilience:** 2nd occurrence — all 3 first-wave Step-11 reviewers died at
  the window (marked `outcome=dead`, re-dispatched clean post-reset); codex diff review
  survived on its separate quota.
- Suite 3428+8skip → 3483+8skip (+55). No workflow-spine change → no diagram REV.

### #465 — W2: agentic adapter profiles · PR #486 (green-pending) · v3.56.0

Conditional session persistence (claude resume), codex workspace-write sandbox pinning
(three spike-#452 overrides, fail-closed composition), per-model effort gating with
recorded stepdown. Design converged adversarial pass 3 (volume loop-back pass 1 — 7 High;
all budgets spent; 45-entry ledger). Progress: T1 effort contract + capability registry
committed (bf1a081, suite 3525/8) · T2 LaunchProfile + fail-closed derivation committed
(ca179b4, suite 3540/8; 8a dual review returned — schema-gap Medium fixed in-flight) ·
all 6 tasks + 8a fixes on the 2 high-risk tasks committed; 3-agent Step 11 + codex diff (claude-mutating containment + grants fail-open fixed); **W7 security blocker recorded** — claude mutating dispatch forbidden until a real FS sandbox lands. Suite 3514/8 → 3576/10.

### #446 — seed + tweak phase-seat models through /rawgentic:setup · v3.55.0

Setup Step 2i makes #445's project-owned table usable: `show-table` (human + versioned
`--json` projection; bake-off sets displayed from `bakeoff_policy.BUILD_MODELS`, labeled
informational) + `apply-table` (sparse per-seat patch over the RESOLVED current table,
validated in memory through #445's load path — base + candidate digest guards, canonical
symlink-safe dest containment, atomic no-clobber fresh-create via the factored
`_publish_bytes`, digest-guarded replace re-seed incl. reset-to-default). Accept-defaults =
strict no-op; fresh-create aborts retain-and-warn. Staleness goes source-aware
(`phaseExecutorTable` nudged via switch, three-state fail-open; `reconcile_projects` skips
project-config entries).

- **S1 descope (owner-visible):** AC1's "build bake-off set" parenthetical descoped —
  candidates are a module constant the table cannot carry; follow-up #484 filed + comment
  on #446.
- **Full spine:** peer consult + 3 design passes (2 design loop-backs + 1 spec-tighten w/
  verifier — GLOBAL budget exactly spent) + plan pass + 8a (1 High: symlinked-parent write
  escape, probe-proven + fixed) + 3-agent Step 11 + codex diff (reset TOCTOU guard fixed);
  **40-entry ledger**.
- Suite 3483+8skip → 3514+8skip (+31). No workflow-spine change → no diagram REV.

## Epic #422 — per-phase model routing + deterministic execution engine (auto-run)

Route WF2/WF3 model seats from bench-#14 evidence through a deterministic `phase_executor`
engine. Plan: `docs/planning/2026-07-16-per-phase-model-routing.md`. Children (dep order):
#424 (E1 package) ✓ · #425 (E2 enforcement) ✓ · #426 (E3 seat-table config) ✓ ·
**#427 (E4 seat cutover)** ← this slot · then #428 #429 #431 #430 #420 #419 #417.

### #427 — ship/intake/plan seats through the executor (E4) · v3.45.0

The FIRST consumer of `phase_executor`: `hooks/executor_routing_lib.py` — a `resolve-seat` /
`dispatch` CLI routing the **ship / intake / plan** seats through `run_seat` as a verified choke
point, gated by a per-seat `executorRouting` toggle in `.rawgentic_workspace.json` (default
`inherit`; the executor is off until a seat is opted in, so merge is a no-op — the prose call sites
land in #417). `dispatch` wires a per-attempt `check_pre` (primary + every fallback target, selected
by the engine's attempt-index) → `run_seat` → `verify_post` → append-only routing-audit log; a
three-way `inherit`/`executor`/`driver_only` tag (merge/CI/deploy/Step-16 stay driver-inline) and a
granular fail-closed exit taxonomy (2 malformed · 3 availability/quota retryable · 4 enforcement
breach · 5 internal/audit), structured `{ok:false,error}` on every non-zero. Capture/permit dirs
derive under the project repo's git-ignored `.rawgentic/`; `reconcile_run` deferred to #420; the
build seat stays fail-closed until #429. Supersedes #418.

- **Design:** rev 3, hardened over a Codex peer consult + a Step-4 adversarial-on-design pass + a
  verifier round (base=project-repo-root, run_id-less capture, pool-sig permits, per-attempt
  check_pre, absent-vs-malformed config).
- **Review:** Step-8a 2-reviewer (fixed a QuotaTimeout-escape High + exit-taxonomy gaps) + Step-11
  3-agent + Codex adversarial-diff (fail-closed corrupt-workspace, repo-root containment,
  RoutingError/schema-error in the taxonomy; D2 "post-check not appended" rejected — reconcile
  recomputes verify_post).
- **Verification:** full suite 3151+6skip → **3208+7skip** (0 failing); pylint hooks+tests 10/10;
  security scan PASS (iac n/a). A **live** ship-seat `claude --print` spike (RUN_LIVE) verified the
  real `actual_model==sonnet` end-to-end. No workflow-spine change → no diagram REV.

### #428 — competitive design rounds + build bake-off + glm judge (E5) · DEFERRED

`ZHIPUAI_API_KEY` absent in the autonomous-run environment → the live glm-5.2 competitive judging
(its acceptance) cannot be verified. Deferred with a blocker comment (no code written); the auto-run
continued at #429. Revisit in a session with the key (`pip install "zhipuai>=2.1.5"` + the key).

### #429 — deterministic complexity gate (E6) · v3.46.0

`hooks/complexity_gate.py` — a pure, fail-closed `needs_bakeoff(task, issue, plan_est, cfg) ->
GateDecision` ("code routes, prose never does"). Bakes off on risk_level==high / complexity==complex
/ security-surface glob hit (auth/secrets/payments/migrations/CI/crypto) / diff-lines-over /
file-count-over; fail-closed on missing/invalid metadata (incl. non-serializable values + unparseable
thresholds — both hardened in the Step-11 review). Returns decision + reason codes + input snapshot +
sha256 policy digest (executor recomputes at admission). Shipped in its OWN module (not plan_lib) —
executor-consumed (#428/#430), not a WF2-prose helper, so out of plan_lib's skill-wired surface (the
`test_skill_helpers` reverse drift-guard caught the initial plan_lib placement). Small-standard lane;
suite 3208+7 → 3246+7 (+38); no spine change → no diagram REV. (child 5/10)

### #431 — multi-account Claude lanes via CLAUDE_CONFIG_DIR (E8) · v3.47.0

The `phase_executor` claude adapter sets `CLAUDE_CONFIG_DIR=<credential_ref>` per invocation
(`run_subprocess` gains an env-MERGE param; `_claude_env` builds it), so a lane's `credential_ref`
selects an isolated Claude config tree = an independent quota pool (per-config-dir 5-hour window).
Quota-per-account was already wired (engine keys permits by account=credential_ref); per-account
ceilings + parallel lanes fall out for free. No `credential_ref` → environment inherited unchanged;
codex/zhipu untouched. + per-account setup runbook + ToS note in the phase_executor README.
Small-standard lane; suite 3246+7 → 3252+7 (+6); no spine change → no diagram REV. (child 6/10)

### #420 — routing telemetry in run records · v3.48.0

Extends run-record `dispatches[]` with OPTIONAL per-dispatch routing telemetry (preferred/actual
model, fallback_reason, queued_ms, concurrency, selector inputs) — validated-optional (old records +
pre-#420 entries stay rc=0), populated once #417 wires the executor. Small-standard lane; suite
3252+7 → 3262+7 (+10); no spine change → no diagram REV. (child 7/10)

### #419 — model-routing.md provenance + refresh rule · v3.48.1

New `docs/model-routing.md`: the executable refresh-rule decision table (role→phase map, gap test =
median-gap>pooled-sd effect-size heuristic, floor test ≥70 subagent/≥80 driver + driver 5/6 gates,
move-only-when-both-pass, provenance re-stamped every decision incl. "no change") + a drift-guard
pinning the canonical move sentence. Docs child (PATCH bump). Small-standard lane; suite 3265+7 →
3269+7 (+4); no spine change → no diagram REV. (child 8/10)

### #417 — WF2/WF3 skill prose: fallback + concurrency + driver-seat · v3.49.0

Documents three dispatch contracts in the single-sourced `<model-routing-resolve>` block: seat
fallback chains + circuit breaker (chain exhaustion = handled hard failure, never silent downgrade),
≤3-Claude concurrency ceiling (effective 2 driver-active), driver-seat guidance (opus recommended,
not enforced). Shared source edited + synced into implement-feature; fix-bug's bespoke WF3 block
updated directly (no forced unification). Drift-guard pins the canonical fallback sentence. Lane;
suite 3269+7 → 3273+7 (+4); no spine change → no diagram REV. (child 10/10 — LAST queued)

## Epic #408 slot 2 — #393: disposition ledger for pass-N adversarial reviews · v3.40.0

**Issue.** #393 (feature, standard, full spine; epic #408 auto-run child 2, scoped
auto-merge grant 2026-07-15): each adversarial engine invocation saw only the
artifact, so multi-pass gates re-derived and RE-LITIGATED settled decisions —
observed on saystory #167, #69, and three times in the #407 run (the same
category-poisoning disposition dissolved at pass 2, pass 3, and the Step-11 diff
review).

**What shipped.** Orchestrator-persisted terminal-disposition memory:
`plan_lib.append_disposition` (fail-closed writer) / `read_dispositions` (tolerant
per-line binary reader — one bad byte costs one line) / `fold_dispositions`
(last-write-wins, last-occurrence order) / `compute_finding_key` (engine
dedupe-tuple sha256, category deliberately excluded — relabel-proof) /
`strip_reopens`. Pass-N dispatches fold `claude_docs/.wf2-state/<issue>/dispositions.jsonl`
to a 0600 temp copy and add `--dispositions <temp> --issue <n>`; the engine
re-validates, renders escaped single lines (C0/C1 + U+2028/U+2029 stripped), caps
at 20KB (most-recent kept, loud truncation), and injects a SECOND independent
nonce fence with a disposition-aware instruction (declined/dissolved: no re-raise
without `REOPENS <id>:` + new evidence; adopted: DO re-raise if still broken).
Split fail policy: benign → fail-OPEN `ledger: degraded`/`ledger: empty`,
`--issue` mismatch → fail-CLOSED exit 6 → `failed (ledger integrity)`. Steps
4/6/11 wire gate-close persistence, the dispatch sequence, and the join backstop
(DECLINED/DISSOLVED match auto-dissolves; ADOPTED match → `possible failed
remediation`). No flag → byte-identical prompt, pinned vs a committed pre-change
golden. Diagram REV 3.40.0 (stations 4+11 delta).

**Decisions (this slot).** Plan-gate: 4 adversarial Highs dissolved-with-evidence
(2 reviewer-scope — the design §1/§5 held the "missing" contracts; 1 intentional
key asymmetry; 1 already-defended realpath containment), 4 Mediums adopted. D-11
task reorder (T1→T4): a cross-surface corpus guard sat red between new public
helpers and their steps.md wiring — both plan reviewers missed the sequencing.
Golden-fixture base64-encoding DECLINED (fence contract held live; auditability
wins). Import layering confirmed coherent (plan_lib owns `.wf2-state`
persistence, engine owns the fence/escaping contract).

**Reviews.** Step-4 gate closed in the prior session (3 passes, 13+7+6 findings,
budget 3/3 exhausted — Steps 8/11 required clean-or-blocker). 8a ×2 on all 3
high-risk tasks: 1 High fixed red-first (text-mode UnicodeDecodeError dropped the
whole ledger on one bad byte) + 5 cheap adopts (honest empty-vs-degraded signal,
loud truncation, Unicode line-separator strip, pre-change golden, empty-string
seam). Step 11 (3 agents + cross-model diff pass): 3H+2M adopted red-first —
including the diff pass LIVE-DOGFOODING `--dispositions` on its own diff (ledger
seeded from the plan-gate's dissolved Highs; codex re-litigated neither) and
catching that the no-re-raise instruction wrongly covered ADOPTED entries.
Security scan clean (iac/sca visible skips). Suite 2920+1skip→2983+1skip, zero
regressions, red-before-green per task.

**Status.** PR + CI + merge SHA filled by the next slot's pass (established
convention). Telemetry for this slot embedded below.

---

## Epic #408 slot 1 — #407: adversarial findings carry a loopback-class · v3.39.0

**Issue.** #407 (feature, standard, full spine; epic #408 auto-run, scoped auto-merge
grant 2026-07-15): WF2 Step 4's fold treated every WF5 adversarial Critical/High as
`untagged` → full design loop-back by construction. The #403 run burned its entire
3/3 global budget on prose tightening; the spec_tighten cheap path was unreachable
from adversarial findings.

**What shipped.** `FINDINGS_SCHEMA` gains a required-but-nullable `loopback_class`
(plain `["string","null"]`, NO enum — a null-member enum has no strict-mode precedent;
the prompt constrains vocab). The review prompt carries the WF2 rubric (spec-tightening
= intent right/text wrong, stateable verbatim; design-flaw incl. the boundary clarifier;
unsure→design-flaw; null for Medium/Low) + an injection-guard extension naming
loop-back-classification steering. `validate_finding` is FULLY permissive on the field
(whole-report gate :1220/:1465 — a bad advisory tag must never parse_error a review);
the new pure `loopback_class_entries` owns the fail-close: security-category
Critical/High → `untagged` UNCONDITIONALLY (case-insensitive, self-contained),
exact-case vocab after strip, else `untagged` (backward compatible). WF2 item-7
consumes the tag (security override stated first), the cheap-path verifier brief is
sidecar-sourced with per-originating-finding confirmation, and the Step-4 dispatch now
explicitly wires `--findings-json` (Step-11 catch: the field is sidecar-only —
without it the feature is silently inert at its target step). Diagram REV 3.39.0
(station 4 delta).

**Decisions (this slot).** Enum dropped at gate pass 1 (unproven strict-mode shape,
zero correctness cost — helper constrains). Vocab-rejecting/type-checking validator
DECLINED twice (peer + pass-2): normalize drops invalid findings + whole-report gate.
Category-distrust DECLINED ×3 (would nullify the cheap path; residual risk documented;
the recurring re-litigation across passes is live evidence for #393's disposition
ledger). Case asymmetry: security override case-INSENSITIVE (widening fail-closed net),
vocab match case-SENSITIVE (repair conceals drift).

**Reviews.** 3 design-gate passes (17 unique findings, all terminal): 2 design
loop-backs + the run's FIRST spec_tighten cheap pass — both reviewers repeatedly
demonstrated the pre-#407 cost live (every adversarial Critical/High entered untagged;
2/3 budget burned on the run shipping the fix). Plan gate: 5 findings (task-order fix:
version-bump-before-diagram-REV — linkage test is one-directional). 8a ×2 on the
high-risk tasks (1 Low applied: self-contained override). Step 11: 3 agents +
adversarial diff (5 unique: station-13 stray marker, the sidecar-wiring gap;
2 re-litigations dissolved). Security scan clean (iac/sca visible skips). Suite
2889+1skip→2920+1skip, zero regressions, red-before-green per task.

**Status.** *(backfilled by slot 2's pass)* PR #409, CI hard lanes green,
squash-merged `7bea79f` 2026-07-15, issue #407 auto-closed, v3.39.0 on main.
Telemetry for this slot embedded below.

---

## Standalone — #403: selectable GLM review/consult backend (gpt | glm | both) · v3.38.0

**Issue.** #403 (feature, standard, full spine — new optional dependency): WF5
adversarial-review and WF13 peer-consult were hardwired to the Codex CLI; the owner's
GLM Coding Plan subscription (proven live in rawgentic-next's bench-judge lane) makes
a second, independent cross-model backend available.

**What shipped.** A `backend` field (`gpt`|`glm`|`both`, absent → gpt) on the
`adversarialReview`/`peerConsult` config blocks + `--backend` on the `review`/`consult`/
`prereq` CLI and both skills. New GLM engine path in `hooks/adversarial_review_lib.py`:
zhipuai SDK (deferred import, version floor 2.1.5), sync-STREAMING with a two-layer
timeout (SDK read timeout at client construction + per-chunk deadline), schema-in-prompt
+ the existing tolerant validators, the same nonce-fenced injection defense, unbypassable
in-run-function secret scan (supplied `artifact_text` is scanned too). `both` runs each
backend independently — gpt keeps every path byte-identical, glm writes `-glm` siblings
(report suffix AFTER the date; sidecar/out siblings), exit 5 = machine-distinguishable
PARTIAL. Fail-closed egress control: a present-but-invalid backend value (incl. explicit
JSON null, half/empty resolution args) REFUSES with exit 2 before any provider call —
never silently laundered into gpt. Embedded WF2 Step 3/11 call sites resolve the config
backend via the new `backend` subcommand and consume exit 5 + dual sidecars with a
deterministic merge.

**Verification.** TDD throughout (130 new tests, injected fake clients — CI network-free);
**LIVE pre-merge smoke on the z.ai Coding Plan subscription endpoint**: glm-only review
(exit 0, GLM reviewer line, findings parsed) and both-mode with dual sidecars (exit 0,
stdout manifest, gpt sidecar untagged/byte-compat, glm sibling tagged). Suite
2759+1skip→2889+1skip.

**Gates.** Step 4 ran FOUR passes (owner elected a 4th over escalation; 36 deduped
findings adjudicated, budget 3/3 spent — the cross-model reviewer re-litigated the
owner's decided live-smoke fork repeatedly; discard-with-reason each time). 8a dual
reviews on all 4 high-risk tasks (16 findings; fixes incl. a lazy-urlsplit port crash
on the consent path). Step 11: all four review sources converged on one High (prereq
CLI missing `--backend`) — fixed red-before-green with three more diff-review catches
(JSON-null backend, empty resolution args, consult out-sibling/artifact collision).



### #375 — FTS5 session index + `/rawgentic:session-recall` skill · v3.33.0

**Issue.** #375 (feature, epic #378 child 1/3): full-text search over session history was
the one capability the 2026-07-10 nine-tool comparison found genuinely missing —
mempalace is curated semantic memory; nothing searched the raw 2.35 GB JSONL corpus.

**What shipped.** `hooks/session_index.py` (pure core + thin CLI): incremental `index`
over `~/.claude/projects/**/*.jsonl` (recursive — the corpus nests `subagents/` trees;
per-file `(mtime_ns, size)` high-water marks, per-file transactions, stat-recheck for
live-appending files), provenance-carrying `search` (FTS5 external-content table + sync
triggers, bm25 deterministic ordering, `--literal` phrase quoting, inclusive date
filters), `status` (versions, malformed/ignored/rejected split, staleness). Single-writer
`fcntl.flock`; WAL concurrent readers; `--rebuild` builds a temp DB and atomically
`os.replace()`s it in. Guards: missing-corpus-dir refusal, partial-vanish ratio refusal
(>50%), startup schema/parser gate, reader staleness warning, lone-surrogate sanitize,
dir 0700/files 0600, symlink refusal (DB + lock). New workspace-management skill
`session-recall` wraps it; registration across all surfaces (17 skills, workspace 6→7).

**Gate story.** Step 4 ran two passes (design loop-back + user-chosen spec-tighten cheap
path, D1): 23 unique findings, all terminal. Step 11's adversarial diff review caught a
Critical the spike had masked — `executescript()` autocommits, so the in-place
"one-transaction" rebuild was never atomic; the peer consult's temp-DB swap (initially
rejected as over-complex) was reinstated. Live Task-4 execution against the real corpus
caught two more the synthetic fixtures missed: `*/*.jsonl` missed 3,308 nested files, and
77% of message lines are legitimately textless (tool_use/tool_result/thinking) — the
format-drift guard now measures true shape failures (rebuilt live: 5,139 files, 76,769
messages, 0 rejected). Risk-tagging hit the `decompose` band because the bare `session`
path pattern matches every file of a session-tooling feature (D2: manual tags kept,
word-scope follow-up filed). Suite 2614+1skip→2670+1skip. No diagram REV (leaf skill +
hook only). PR #386 squash-merged `5675cc1`, CI 4/4 green, issue auto-closed.

### #376 — WF17 `/rawgentic:session-mining` — detect→queue→synthesize→gate · v3.34.0

**Issue.** #376 (feature, epic #378 child 2/3): adopt claude-reflect's verified shape
(detect → durable queue → synthesis → human gate) built native, report-only, no LLM in
detect.

**What shipped.** `hooks/session_mining_lib.py`: deterministic detectors over the #375
index (`--literal` phrase queries; friction + restated-error proxies) and session notes
(command mentions with same-section UUID session-id resolution; unresolvable =
evidence-only). Append-only event-log queue with sha256 candidate identity,
human-over-machine reducer (unknown/machine events can never override a decline),
tail-parse torn-tail guard (repairs valid-but-unterminated hand-fixed lines; truncates
only unparseable fragments; non-object tails truncate too), mid-file corruption fails
propose/disposition closed, best-effort redaction preserving paths/UUIDs, verbatim
quotes via read-only #375-DB JOIN (fail-loud; fallback marked `index-snippet`).
Recurrence ≥ 3 DISTINCT sessions, bucketed per (detector, pattern) — the cross-detector
leak was caught because the live run mined its own session's notes. WF17 skill mirrors
WF14's report-only pattern; WF1 handoff is a re-draftable template prompt.

**Gate story.** Step 4 took THREE passes + a verifier-guided micro-fix, consuming the
entire loop-back budget (design ×2 + spec_tighten ×1, D7–D10 in the run log): the peer
consult refuted my hybrid detector (sampling bias — option C with proxy labeling won,
D6), the adversarial reviews forced the accepted-event lifecycle, absorbing-terminal
semantics, and the torn-tail guard — whose first version the incremental verifier then
proved converted the benign case into fatal corruption (fix reversed to
truncate-then-append). Step 11's adversarial diff found the recurrence cross-detector
leak + accepted-evidence loss; one High rejected with rationale (torn-tail-as-declined —
write-time visibility). Live verification against the real corpus (temp queue): 1,031
signals → 177 patterns → 10 proposals with verbatim evidence, AC4 decline-then-re-propose
verified live. Suite 2670+1skip→2723+1skip. WF17 skeletal diagram entry, no WF2-spine
REV. PR #387 squash-merged `ccebaf4`, CI 4/4 green, issue auto-closed.

### #377 — WF14 rubric v2: cross-session recurrence evidence wiring · v3.35.0

**Issue.** #377 (feature, epic #378 child 3/3, complexity S): wire #375/#376's
recurrence evidence into WF14 run-feedback — prose-only, no hook code.

**What shipped.** Small-standard LANE run (the epic's first; D11). WF14 Step 2 friction
findings gain an OPTIONAL `recurrence: <n> runs (index query, quoted)` tag (#375 index
query, distinct sessions, --limit raised past the bm25 default); rubric stamped v2 with
a comparability note (no anchors moved — recurrence raises CONFIDENCE only); provenance
boundary pinned (index SUPPLEMENTS; Step 1 marker-grep stays SOLE run-fact source; Step
1 prose byte-identical); Step 4 cap-sharing (WF17 candidates at ≥ 3 runs share the
3-issue pool; below threshold never crowd out a defect). 4 new drift-guard pins + the
v1 stamp pin updated. Lane gates: single-reviewer Step 11 PASS (2 Low prose nits fixed:
a splice-duplicated clause, a --limit undercount note); adversarial diff mechanically
skipped (no security surface); 0 loop-backs. Suite 2723+1skip→2727+1skip. No diagram
REV (skeletal wf14 sheet, prose-only). PR #388 squash-merged `637f66c`, CI 4/4 green, issue auto-closed. Epic #378: all 3 children shipped (v3.33.0–v3.35.0).

---

## Epic #333 — subagent-dispatch observability + review-gate hardening (auto-run)

### #329 — structured dispatches[] in the run-record schema + aggregate rollup · v3.26.0

**Issue.** #329 (feature, epic #333 child 1/10): the #328 dispatch audit needed ~2.3 GB
of transcript archaeology to answer "did subagents run?" — `run_records.jsonl` had no
structured dispatch field.

**What shipped.** Optional, present-is-strict `dispatches[]` in the run-record schema
(`hooks/work_summary.py` validate_record — the usage #155 / goal_guard #156 precedent):
per-entry `role`/`subagent_type`/`model`/`effort` + orthogonal `outcome`
(ok/error/retried/dead; dead = vacuous return) vs `resolution` (primary/fallback/generic).
Aggregate rollup: counts by role/model (null model → `"(none)"`), dead rate, fallback
rate, `runs_with_dispatches`; the section is omitted ENTIRELY when no record carries the
field (single contract, per-partition under `--group-by`). Documented in
`docs/run-records.md`. Emission wired by follow-up #330.

**Reviews.** Step 4 quality-bar (opus): 2 Low ambiguous, both resolved in-gate from repo
conventions ("(none)" sentinel; per-partition omit). Step 8a on the high-risk validation
task: 2 reviewers, clean. Step 11: 2 opus reviewers NO FINDINGS + codex adversarial diff
2 Medium @0.7 — dropped by the severity band and refuted against code. Security scan:
0 blocking / 0 advisory (iac n/a, sca nothing-to-scan). Lane: small-standard (3 impl
files). Suite 2435+1skip → 2503+1skip (+68). No spine change → no diagram REV.

**PR.** #347 — merged 839f5a2, all 4 CI checks green (test, lint, code-review, security-review).

### #330 — emit dispatches[] from the workflow completion steps · v3.27.0

**Issue.** #330 (feature, epic #333 child 2/10, depends on #329): the schema existed but
nothing wrote it — the audit line prescribed by the dispatch prescriptions carried no
subagent_type/outcome/resolution and never reached the run-record.

**What shipped.** Canonical completion-time audit line `DISPATCH issue=<n> role=… type=…
model=… effort=… outcome=… resolution=…` in the dispatch prescriptions (shared block →
synced WF2 SKILL.md; bespoke review-only WF3 variant), with per-invocation / flush-left /
retry / pre-suspend emission rules and a resolution decision table (`fallback` =
carried-never-emitted). Assembly at WF2 Step 16 item 2d / WF3 Step 14 item 3b: grep
`^DISPATCH issue=<n>` from session notes, null→JSON null, never dedup, malformed lines
counted (incl. indented rescues), zero→omit; under-count detection owned by WF14.
Capture contract + worked example in `docs/run-records.md ### Capture (#330)`. 9 drift
guards incl. regex byte-identity.

**Reviews.** Step 4 took the FULL loop-back budget (3/3): 2 design loop-backs (run-header
scoping anchor matched nothing in the real notes corpus → issue-scoped lines; resolution
ladder unmapped → decision table) + 1 spec-tighten (stale cross-references), final
verifier CLEAN. Step 6 adversarial-on-plan: 2 Medium plan clarifications. 8a: 5 prose
hardenings applied. Step 11 (re-dispatched after a session-limit kill — 3 dead agents +
1 dead memorize recorded as outcome=dead DISPATCH lines): 1 Medium changelog inversion
fixed + 3 Low hardenings + adversarial 1 applied / 2 refuted. Scan 0/0. DOGFOOD: this
run's own record carries 19 assembled dispatches[] entries (4 dead from the limit kill).
Suite 2503+1skip → 2512+1skip. No spine change → no diagram REV.

**PR.** #349 — merged 4a629df, all 4 CI checks green.

### #331 — WF3 Step 9 per-slot fallback chain + dead-return detection · v3.27.1

**Issue.** #331 (fix, epic #333 child 3/10): WF3's NON-NEGOTIABLE Step 9 gate named only
two external-plugin agents with no declared fallback and no vacuous-return handling — a
mandatory gate with an undeclared single point of failure.

**What shipped.** Declared per-slot three-tier chain (pr-review-toolkit named →
rawgentic-reviewer substitute → generic inline; never collapses two reviews to one;
both-slots-tier-2 distinct briefs) + dead-return detection (vacuous = DEAD, relaunch
once, second death → REVIEW_DISPATCH_FAILED + ERROR protocol; mid-tier runtime error
retries once then descends) + two named failure modes + headless Step 9 ERROR entry.
WF3 resolution table reconciled (tier1=primary, tier2=fallback — the first real
producer, tier3=generic; the pre-existing #330 table mismatch fixed). Descent emission
split by trigger (resolve-failure emits no line for a tier that never ran — no
fabricated audit records; runtime-error descent carries the abandoned tier's own
resolution). WF2's 8a + Step 11 reviewer sites gained the same dead-return rule —
this session's own limit-kill (3 dead reviewers) is the live case. WF3 diagram
REV 3.27.1, full-page snapshots re-verified 1440×2586 both themes. Suite
2512+1skip → 2518+1skip. Reviews caught real: pass-1 design collided with the
#330 tables merged 30 minutes earlier; Step 11 caught the fabricated-audit-line
semantics. Loop-backs 1/3.

**PR.** #351 — merged 70e7d75, all 4 CI checks green.

### #341 — issue-keyed step markers · v3.28.0

**Issue.** #341 (feature, epic #333 child 4/10, WF14 dogfood finding): step markers
carried no issue key — concurrent runs sharing one notes file were mechanically
un-attributable (reproduced THREE times across this epic's own runs).

**What shipped.** Per-marker-type canonical key-slot contract (5 classes + the
hook-emitted promotion shape) with an AUTHORITATIVE slot table, emitter caution, and
declared deferrals in both `<step-tracking>` blocks; 14 keyed prescribed literals
(incl. the Step 4-discard and Step 6 adversarial siblings the 8a review caught);
`format_promotion_note` gains a backward-compatible `issue` kwarg (TDD, `#`-input
normalized); run-scoped consumer rules at all three read sites (WF2 MARKERS_COMPLETE,
WF3 §Workflow Resumption, WF14 attribution with inlined slots — cache blocks
cross-skill reads); legacy fallback tightened to pre-#341/stale-cache only;
`docs/session-notes.md` updated. Lane-widened honestly noted (9 impl files > 7 after
8a hardening). Suite 2518+1skip → 2534+1skip. Reviews caught real: unpinned slot
table, stale canonical doc, wrong changelog file refs, fail-open fallback framing.
Loop-backs 2/3. No spine change → no diagram REV.

**PR.** *(backfilled by #340's pass)* PR #352 squash-merged `27aab30`, all CI checks
green.

---

### #340 — multi-pass gate counting rule + merged-gate reviewer_kind precedence · v3.29.0

**Issue.** #340 (feature, epic #333 child 5/10, WF14 dogfood finding F-1): the run-record
schema gave multi-pass gates no counting rule (the #337 record shipped an eyeballed 14/14
matching no defensible derivation) and single-slot `reviewer_kind` could not describe a
merged self-review+codex gate.

**What shipped.** Two documented-and-guarded semantics rules: (1) `findings` = UNIQUE
findings across all passes (identity = same artifact location AND same required change),
`resolved` = terminal FINAL disposition at gate close (applied / fixed-in-gate /
refuted-with-cited-evidence / dropped-by-band; band-drops count in both), computed at
gate close and persisted — assembly reads, never re-derives; (2) merged-gate
`reviewer_kind` records the gate-DEFINING mechanism (Step 4/6 → `inline`, Step 11 →
`hand_rolled_multi`; `→ codex` scoped to sole-mechanism gates; skipped gate omits the
key). Canonical prose + worked example in `run-record.md`; WF3 §14 pointer;
`docs/run-records.md` subsection; WF14 rubric weak-spot checks audit against both rules
(pre-#340 records = `known-limitation`). No validator change — shape untouched.

**Reviews.** Step 8a (T1 high): 2 opus reviewers — R2 High @0.8 real (rule landed where
per-finding input no longer exists → compute-at-gate-close persistence added). Step 11:
R1 1 Med confirmed (changelog test count), R2 clean; codex adversarial 3 — A1 High
(disposition-alias escape clause reopened the closed set → closed), A2 Med (pre-#340
reviewer_kind legacy carve-out → added), A3 Low = issue #343's exact subject (tracked,
not fixed here). Security scan 0/0 (iac n/a, sca nothing-to-scan). Lane: small-standard
(5 impl files ≤ 7). 6 new drift guards. Suite 2534+1skip → 2540+1skip. Loop-backs 1/3.
No spine change → no diagram REV.

**PR.** *(backfilled by #338's pass)* PR #353 squash-merged `5cd28b4`, all 4 CI checks
green.

---

### #338 — runFeedback embedded invocation wired into WF2/WF3 completion · v3.30.0

**Issue.** #338 (feature, epic #333 child 6/10, follow-up to #337): the WF14
run-feedback skill shipped embed-ready but deliberately unwired — every assessment this
epic ran was a manual invocation.

**What shipped.** Opt-in embedded self-assessment item in WF2 Step 16 (item 5) and WF3
Step 14 (item 6): gate on `adversarial_review_lib.py is-enabled --key runFeedback`
(generic key parser, live-probed, no code change), silent skip on absent/disabled (the
peerConsult pattern), enabled → invoke the `/rawgentic:run-feedback` core path with
explicit `--record /tmp/wf{2,3}-run-record.json --wf <n> --session-notes <notes-path>`.
Fail-open (AC3): assessment failure logs + continues; runs regardless of summarize rc
(degraded mode covers schema-invalid records); report-only for the plugin source +
PR-terminal-safe → runs in headless, where WF14's outward writes (report pair, ≤3
filed issues, mempalace memory) proceed autonomously. Stale not-wired prose retired in run-feedback SKILL.md +
config-reference.md.

**Reviews.** (filled at Step 11) Lane: small-standard. 4 new drift guards. Suite
2540+1skip → 2544+1skip.

**PR.** *(backfilled by #343's pass)* PR #354 squash-merged `03ac4fe`, all CI checks
green.

### #343 — markdown-table rendering + human-first at-a-glance report structure · v3.31.0

**Issue.** #343 (feature, epic #333 child 7/10, owner request from the first WF14
dogfood + A3 in #340's Step 11): `_render_body_plain` had no markdown-table branch —
every table row in a `--style plain` artifact (WF14 reports, WF5 reviews, design docs)
rendered as a literal `<p>| ... |</p>` paragraph; and the WF14 report template never
mandated a human-first structure.

**What shipped.** GFM table branch in `hooks/render_artifact.py` (header +
`| --- | :-: |` separator detection, contiguous pipe rows, escape-first per-cell via
`_inline(html.escape(...))`, `close_list()` before emission; pipe row with no
separator stays a paragraph; fenced tables stay code; existing table CSS reused —
roadmap cards get tables for free). WF14 report structure made explicitly human-first:
rubric.md gains "Report structure — human-first" (canonical sentence, drift-guarded)
mandating the `## At a glance` opener (bolded verdict, six dimension scores with
one-line verdicts, best catch, worst friction, routed line) before evidence detail;
SKILL.md Step 3 points there. Real-thing check: rendering the committed #338 WF14
report produced 3 `<table>`, 0 raw pipe paragraphs.

**Reviews.** (filled at Step 11) Lane: small-standard. 9 renderer tests (red 5-failed
evidence) + 3 drift guards. Suite 2544+1skip → 2556+1skip.

**PR.** *(backfilled by #344's pass)* PR #367 squash-merged `912a629`, all 4 CI checks
green.

### #344 — visual design language + per-artifact-type templates · v3.32.0

**Issue.** #344 (feature, epic #333 child 8/10, depends on #343): six artifact surfaces
funnel through a renderer with two styles and a minimal markdown subset — artifacts
looked inconsistent and each skill invented its own document structure.

**What shipped.** Seven-template registry in `hooks/render_artifact.py` (plain, roadmap,
report, design, dashboard, review, spec): one shared escape-first block renderer,
per-template CSS layers over a component stylesheet (score chips, severity badges,
RFC-2119 requirement badges — light+dark), `tpl-<name>` body classes, narrow
inline-stage decorators (code-span-skipping, hard-break-bridging). Paragraphs gained
standard soft-wrap semantics (multi-line bold fixed, two-space hard breaks, CR
normalization). `design_artifact_style`: full vocabulary, absent→design, invalid→plain
+warning, never-raises hardened. `docs/design-language.md` + byte-reproducible exemplar;
five in-repo surfaces name their template with drift-guarded canonical sentences
(WF3's missing style resolution fixed — the WF2/WF3 asymmetry). Workspace
design-doc-publish updated in place (stated gap: outside the repo, no CI guard).

**Reviews.** Full spine. Step 4: 2 adversarial passes (9 High/Medium pass 1 → design
loop-back consumed → 7 Medium pass 2, dispositioned). Step 6 adversarial-on-plan:
6 Medium dispositioned. 8a on both high-risk tasks: 3 Low applied (CRLF, MUST-NOT
bridge, unknown-style warning). Step 11 (3 agents + adversarial diff): 4 fixed incl.
a confirmed never-raises violation (non-list `projects` TypeError), 1 refuted
(CSP-inline claim vs the established no-external-hosts contract). Loop-backs 1/3.
Suite 2556+1skip → 2611+1skip.

**PR.** *(backfilled by #342's pass)* PR #368 squash-merged `7bb8928`, all 4 CI checks
green.

### #332 — Step 8 inline-vs-delegated expectation documented · v3.32.1

**Issue.** #332 (docs, epic #333 child 9/10, #328 audit follow-up): the skill text
implied delegation was obligatory whenever `implementation` resolved non-`inherit`,
while the audit measured 6/6 genuine runs inline — doc/behavior misalignment.

**What shipped.** One Step 8 paragraph: when the resolved implementation model equals
the session/orchestrator model, inline is an expected, acceptable outcome (delegation =
isolation/parallelism, not obligation), citing the audit, honesty-bounded (the sonnet
falsification experiment stays open). 2 drift guards. Reviewer verified every claim
against the audit primary source. Lane small-standard; loop-backs 0/3. Suite
2611+1skip → 2613+1skip. *(Slot added by #342's pass — a convention bend owned in
#332's WF14 report: the slot should have ridden PR #369.)*

**PR.** #369 — merged `f8434bf`, all 4 CI checks green.

### #342 — doc-rot batch · v3.32.2

**Issue.** #342 (fix, epic #333 child 10/10, WF14 dogfood F-3): three stale/un-guarded
doc surfaces batched so none is lost silently.

**What shipped.** CLAUDE.md pointer `:1348`→`:1306`; `load_adversarial_review_config`
docstring gains the live `runFeedback` key (the issue's cited `is_enabled_for` was
already fixed by #338 — honest citation delta recorded); Codex manifest
`longDescription` count corrected 20→16 AND converted to a computed disk-glob guard
(#271 pattern, red at 20≠16). Workspace `add-skill` stale hand-pinned count replaced
with read-from-test guidance in place. Lane small-standard; loop-backs 0/3. Suite
2613+1skip → 2614+1skip.

**PR.** #373 — merged `e4d6aa2`, all 4 CI checks green. Epic #333 auto-run complete: 10/10 children shipped, 4 WF14 checkpoints run, aggregate review `docs/reviews/2026-07-10-epic333-wf14-aggregate.{md,html}`.

---

## Standalone — codex reliability (audit #328 fallout)

### #334 — codex thought-partner dispatches hang: routing rule + dead-job protocol + userns runbook · v3.24.26

**Issue.** #334 (bug): two same-day cross-model "thought partner" dispatches via the
third-party `codex:codex-rescue` path failed — Codex's bwrap sandbox died on Ubuntu
24.04's `apparmor_restrict_unprivileged_userns=1` (kernel-audit evidence on the
issue), then the connector fallback hung >21 min with no watchdog anywhere
(`codex-companion.mjs` has only a 240s status-poll wait).

**RCA pivot.** The issue as filed proposed building a timeout-enforced consult path —
Step 2 found it **already exists** (WF13 `peer-consult` + the `consult` CLI, 600s
fail-closed, 12 lib tests). Root cause reclassified: a routing/guidance gap, not
missing code. Scope-correction comment posted on the issue.

**What shipped.** `docs/codex-reliability.md` — canonical routing rule (load-bearing
consults go through WF13/`consult`, never bare `codex-rescue`), dead-job protocol
(absolute wall-clock ceiling + output-silence signal, mirrors #331's dead-agent
rule), field-tested AppArmor bwrap-userns host runbook (applied + verified on the
dev host same day). Repo-manual §8 pointer so sessions load the rule.
`tests/test_codex_reliability_doc.py` — 5 guards, red before the doc existed.

**Reviews.** Step 4 reflect + cross-model adversarial (codex, `plan` type): 0C/2H/3M,
all 5 applied (dispatch-surface pointer, platform_apis declaration, version/test
naming, 3-piece drift guard, per-slot detail). Step 9: `silent-failure-hunter` +
`code-reviewer` (both Opus): 0C/0H/0M/4L, all 4 applied (absolute-deadline semantics,
recipe-token guard, RCA artifacts committed, sandbox parenthetical softened).
Security scan PASS (iac/sca visible skips).

**Status.** PR + CI + merge SHA filled by the next slot's pass (established
convention). Telemetry embedded below.

---

## Epic #309 — harness safety, memory consolidation & workspace janitor

The 2026-07-07 unified-review scope that was never filed (children #300–#308,
mostly supervised live-config items). Repo children run WF2/WF3 under the owner's
2026-07-08 scoped unsupervised grant; live-config children apply with timestamped
backups and close on-issue.

### #320 — port the #314 mechanical-projection read discipline to WF3 · v3.24.23 <br>*(status backfill: PR #321 squash-merged `4e6a723`)*

**Issue.** #320 (epic #309): PR #319 (#314, option 3) shipped fail-closed
**projection** read discipline in WF2 — token-heavy runner/scan/CI output consumed
as a bounded reduction, never a full-log dump into the orchestrator's context — but
`skills/fix-bug/` had zero #314 wiring while WF3 has the same heavy read points
(reproduce-first TDD runs, the full-suite gate, CI `--log-failed`). A prose + drift-
guard port; no hook changes (the `plan_lib` byte-threshold constants are skill-
agnostic and already shipped in #319). WF3 has no security-scan step, so the WF2
Step-11.5 projection has no WF3 equivalent (out of scope).

**What shipped.** `skills/fix-bug/references/steps.md`: Step 7 (RED reproduction run
+ full-suite regression) and Step 8 item 4 now consume test runs as **projections** —
the runner's final-summary tail (pass/fail counts + failing test ids + first assertion
lines), the exit code as the verdict, and targeted reads of the named failing tests
for diagnosis — with the fail-closed rule that an empty/malformed/command-failed
projection on a failing run falls back to the inline raw read (logged). Step 11 item 3
consumes `gh run view --log-failed` as a bounded grep (failing job/step +
assertion/traceback first lines) when over `WF2_READ_DELEGATE_BYTES_LOG`, measured
with a piped `wc -c`, same fail-closed fallback. `tests/test_wf3_clarity.py` gains
`TestDelegatedReadsWF3` — 5 section-sliced, one-canonical-sentence-per-guard drift
guards (repo mistake #6). Option-3 scope held: no LLM reader surface (no
`validate_index`, no `.rawgentic-read-` in WF3); the Step 9 diff read stays inline.

**Path.** Small-standard lane (simple_change, 3 impl files) — collapsed design note +
quality-bar rubric + checklist plan + evidence-only drift; Step 6 skipped; TDD +
2-reviewer code review + security scan retained.

**Reviews.** Two `rawgentic-reviewer` agents (Opus) over the diff: both CLEAN on
correctness/prose/scope; one shared Low (the Step 8 guard was a bare-word `projection`
check, blind to its own drift target) — fixed in-run by pinning the item-4 canonical
sentence. Adversarial diff review enabled but skipped (`no security surface` — 0
high-risk paths/tasks). Security scan clean (0 findings; iac/sca skipped, no lockfile).

**Decisions (this slot).** No workflow-spine change (read-discipline within existing
WF3 Steps 7/8/11, no station/gate/loop-back delta) → **no diagram REV**.

**Status.** PR + CI + merge SHA filled by the next slot's pass (established
convention). Telemetry for this slot is embedded below.

### #303 — WAL recovery report expires stale INTENTs · v3.24.20

**Issue.** #303 (epic #309, review 2a): the SessionStart recovery notice
re-announced every incomplete INTENT forever (~20/session, oldest March 2026,
188 total live) — permanent noise desensitizing against real fresh crash INTENTs.

**What shipped.** `hooks/session-start` announce filter hides incomplete INTENTs
older than `WAL_RECOVERY_MAX_AGE_DAYS` (default 7, clamped [1,365]) behind a
visible suppressed-count line; hidden entries stay on disk (rotation already
preserves incomplete entries regardless of age). Fail-open everywhere: undated
entries, a failed date computation, a filter jq error, and a malformed env value
all announce MORE, never less — with the malformed value noted visibly in the
session context (the hook's stderr is discarded at its callsite). Version
3.24.20 ×3 surfaces. No workflow-spine change → no diagram REV.

**Reviews.** Small-standard lane. Step 8a (2 opus reviewers over the hook commit):
1 Medium applied — the filter's jq-error path failed CLOSED against its own
fail-open contract; now exit-code gated. Step 11 (1 opus reviewer + cross-model
Codex diff review, report committed at
`docs/reviews/rawgentic-diff-review-303-e466f037-patch-2026-07-08.md`): both
reviewers independently converged on the dead-stderr-warning finding (fixed +
red-first assert); Codex's all-suppressed-framing Medium refuted with grep
evidence (no programmatic consumer). Security scan PASS (visible skips: iac
not-applicable, sca nothing-to-scan). 8 boundary tests red-before-green.
Suite 2345+1skip → 2353+1skip, 0 failing.

**Status.** PR + CI + merge SHA filled by the next slot's pass (established
convention). Telemetry embedded below.

## Epic #280 — unified-review EPIC 6 close-out

The 2026-07-08 backlog run shipped children 6b/6c/6d′ (PRs #281–#298, report:
`docs/reviews/2026-07-08-unified-review-backlog-run-report.md`); child 6a (#274)
was owner-gated on a wire-or-delete decision and closes the epic.

### #274 — wire-or-delete external_ref_lib → DELETE · v3.24.18

**Issue.** #274 (epic #280, review 6a): `hooks/external_ref_lib.py` was complete,
tested (16 tests), and documented — with zero production consumers. The intended
consumer (#196/#162 post-PR `/code-review` gate) shipped as a GitHub Action that
never called it.

**Decision.** Owner directed a Codex consult first, then follow it. Codex
recommended **DELETE** (fresh thread; verdict recorded on the issue): wiring now
would create behavior just to justify existing code, reopening an abandoned gate
design with no scheduled owner. First consult attempt is its own lesson — the task
went web-spelunking and its process died leaving a zombie "running" job (23 min);
the scoped no-research retry answered in ~1 minute (memorized).

**What shipped.** Removed `hooks/external_ref_lib.py`,
`tests/hooks/test_external_ref_lib.py`, `docs/external-references.md`; dropped the
two structural parametrization references in `tests/hooks/test_atomic_write_lib.py`.
Historical changelog/campaign/review references stay (append-only history). Version
3.24.18 ×3 surfaces. No workflow-spine change → no diagram REV.

**Reviews.** Small-standard lane. Step 11 (1 opus reviewer, all 3 lenses): CLEAN,
0 findings; suite-delta arithmetic independently confirmed (16+2+1=19). Adversarial
diff review: skipped (no security surface). Security scan PASS (visible skips: iac
not-applicable, sca nothing-to-scan). Suite 2359+1skip → 2340+1skip, 0 failing.

### #310 — wal-guard deny() fails closed on huge commands · v3.24.19

**Issue.** #310 (found by #267 R2): deny() passed the full blocked command as one
jq exec argument; over Linux `MAX_ARG_STRLEN` (~128KiB) the exec failed (E2BIG,
rc 126) — empty stdout = ALLOW. The deliberately fail-closed guard failed open.

**What shipped.** Command bounded at deny() entry (`${cmd:0:2000}` + visible
`[truncated: total N chars]`, pure parameter expansion — a first-cut `printf|head`
pipe died of SIGPIPE under pipefail and failed open again, caught red-first).
Review hardening applied: printf-builtin fallback deny on the decision call (ANY
serializer failure previously = allow) + guarded audit `ts=` assignment.

**Reviews.** WF3: 2-reviewer Step 9 (opus — silent-failure hunter + standards;
standards CLEAN, hunter's `ts=` finding applied) + cross-model adversarial on the
RCA (report: `docs/reviews/rawgentic-rca-310-md-2026-07-08.md`; High applied).
5 tests red-before-green. Suite 2340+1skip → 2345+1skip, 0 failing. PR #311.

## Epic #188 fast-follow (post-M4)

WF2 hardening + epic-native workflows + OAuth Action reviews. #189 already shipped
(folded in as slot 12). Ordered slots #190 → #191 → #192 → #193 → #194 → #195
→ #196 → #197 (+ owner-added #205) — **all shipped**; epic #188 closes with #197.
Follow-up #206 (memory migration) remains conditional, outside the ordered list.

### #190 — retire WF2 Step 4 3-judge reflexion panel → reflect-only · v3.2.0

**Issue.** #190 (epic #188 P2): the full-spine Step 4 still ran the same-model
3-judge `/reflexion:critique` panel; owner telemetry measured ≈ 0 gain and the lean
spine shipped 10/10 with 0 loop-backs. Severed AC1 of the abandoned #162.

**What shipped.** WF2 Step 4 runs `/reflexion:reflect` for all lanes; the panel is
removed. Full spine keeps its opt-in cross-model adversarial-on-design sub-step
(WF5, AC2) — high-stakes scrutiny lives there now. Ambiguity breaker, volume
thresholds, and the `design` loop-back budget retained (sourced from reflect, or
merged reflect+adversarial); `critiqueMethod` preamble removed from WF2 (`setup`
keeps it). Fast-path table + SKILL spine one-liner + run-record reviewer_kind
mapping + config-reference + README (feature tables + changelog) all updated.

**Reviews.** Small-standard lane. 2 red-first §4 drift guards (no panel / table
both-reflect) + a README regression guard. Step 11 (1 opus, both lenses): logic
NO FINDINGS (item-numbering resolves, breaker-runs-once holds across all 4 rows);
1 Medium leftover — README feature tables still listed WF2 under
`/reflexion:critique` (the #161-class miss) — FIXED + guarded. Security scan clean.
Suite 1970/0 → 1972/0.

**Owner decision (mid-slot).** Go further than #190: **full reflexion removal** —
replace reflect (WF2 4/9 + WF3), critique (WF1 + setup), and memorize (Step 10)
with in-repo prompts; use **mempalace** for memory instead of `reflexion:memorize`;
follow-up issue to migrate existing rawgentic memories to mempalace if required.
Sequenced as the next slot after #190 (kept out of #190 to preserve its narrow,
reviewed scope).

**Status.** PR #204 squash-merged `cd1fe1b`, v3.2.0, issue closed.

### #205 — remove the reflexion plugin dependency · v3.3.0 (owner-expanded from #190 P3)

**Issue.** Mid-#190 the owner asked: why still depend on reflexion at all? Investigation:
`/reflexion:reflect|critique|memorize` are prompt-only (a rubric behind a slash command;
no code we called) and fail open to *unreviewed* when the plugin is absent. Decision: full
removal + use mempalace for memory.

**What shipped.** An in-repo **quality-bar rubric** (`skills/*/references/quality-bar.md` —
skeptical-gatekeeper stance + depth triage + finding shape) replaces reflect/critique at every
gate: WF2 Steps 4/6/9/15, WF3, incident, and setup's config critique. Memorize (WF2 Step 10,
WF3, incident) curates into **mempalace** (`mcp__mempalace__*`) when available, falling back
to `CLAUDE.md`/`MEMORY.md` on absence **or store failure**. `critiqueMethod` deprecated/inert;
reflexion prerequisite, add-on row, and troubleshooting entry removed. No active skill invokes
`/reflexion:*` (drift-guarded).

**Reviews.** 2 opus reviewers (leftover + logic lenses), converging independently on the same
Medium: "reflect" was the retired skill's own name, so keeping it as the replacement's name
undercut the removal — swept WF2 §4/§6/§9 to "self-review" (WF3 keeps its consistent
"Lightweight Reflect" gate name). Also fixed: SKILL.md mandatory-steps stale "full critique"
tier, memorize store-failure fallback, and the quality-bar finding-shape override contract.
Security scan clean. Suite 1972/0 (2 red-first guards: reflexion-freedom + §4 quality-bar).

**Follow-up.** #206 — migrate existing rawgentic memories into mempalace if warranted.

**Status.** PR #207 squash-merged `f5786a3`, v3.3.0, issue closed.

### #191 — WF2 Step 1b always emits the /goal prompt · v3.4.0

**Issue.** #191 (P4): Step 1b skipped emitting the constructed `/goal` when a prior goal
might be active (observed on #162) — but a skill can't observe or set the session goal, so
the reliable behavior is to always emit.

**What shipped.** Step 1b ALWAYS emits the per-issue `/goal` prompt; it no longer suppresses
on the guess that a prior goal is active. Exception: under an epic campaign
(`RAWGENTIC_EPIC_GOAL` env set — the driver sets it, forward-declared for #192) it **defers**
to the active epic-level goal rather than clobbering it, logged `(deferred: epic #N)`. New
`deferred` value in the run-record `goal_guard` vocab. Reviewer flagged WF3 parity as a #192
follow-up (WF3 will need the same defer once epics drive its sub-issues).

**Reviews.** Small-standard lane. 1 opus: NO FINDINGS (item-4/5 coherent, vocab consistent
across steps.md/run-record.md/work_summary.py/README, forward-declaration honest, validation
fail-closed). red-first: goal_guard `deferred` + Step-1b always-emit drift guard. Scan clean.
Suite 1972/0 → 1974/0.

**Status.** PR #208 squash-merged `b132c51`, v3.4.0, issue closed.

### #192 — driver epic-level goal guard + tolerant escape clause · v3.5.0

**Issue.** #192 (P5, depends on #191): the `/goal` guard belongs at the epic/campaign
level, not per-issue — a per-issue goal lets the session quit after any single slot, and
same-session `/goal` overwrite is documented-unverified. And this campaign hit the stale-goal
failure directly (the goal fired relentlessly after each slot).

**What shipped.** `plan_lib.build_goal_text` gains a `campaign` variant enumerating an epic's
topo-ordered children into ONE goal (≤4000-char fallback), with a **tolerant escape clause**:
"a child closed not-planned per its own acceptance criteria counts as satisfied, and the owner
may pause the campaign at any time." `driver_lib.campaign_goal_text(state)` is the kickoff seam
(epic anchor + topo children; raises on missing epic / dependency cycle). The driver emits the
goal (owner-run — a skill can't self-set `/goal`) and exports `RAWGENTIC_EPIC_GOAL=<epic>`,
which WF2 **and** WF3 Step 1b defer to (the #191 contract, extended to fix-bug per its review).

**Reviews.** Small-standard lane (2 impl .py). 1 opus: 1 Medium (my changelog insertion garbled
a v3.4.0 line — fixed), everything else clean (cap enforced, no import cycle, epic guard rejects
bool, escape-clause wording consistent across code/doc/changelog, RAWGENTIC_EPIC_GOAL honestly
scoped as driver-set). red-first campaign + driver tests. Scan clean. Suite 1974/0 → 1984/0.

**Status.** PR #209 squash-merged `1b3b3fd`, v3.5.0, issue closed.

### #193 — WF1 decompose an over-large ask → epic + children · v3.6.0

**Issue.** #193 (P6): WF1 only *suggested* splitting an over-large ask and filed one issue —
enhance it to emit a driver-consumable epic + ordered children (the missing front-end for the
#163 epic/driver machinery).

**What shipped.** create-issue Step 1 detects over-large (≥3 shippable deliverables / many
concerns) and OFFERS to decompose (new Step 2c): an epic (`epic:` label + `- [ ] #N` task-list)
+ children with `Depends on #N` edges (`driver_lib.parse_depends_on` reads them). Hard approval
gate — the whole decomposition is presented and NOTHING is filed until "go"; children file in
topo order, epic last. Threshold: ≥3 → epic, 2 → cross-linked, 1 → single issue. Lean single-pass
+ inline quality-bar; opt-in WF5 for architectural asks.

**Reviews.** Small-standard lane (prose). 1 opus: 1 Medium (partial-decomposition resumption gap
— <resumption> now records per-child + COMPLETE markers and resumes without re-filing) + 3 Low
(pre-approval label creation moved to filing; threshold-seam clarified; test pins `- [ ] #N`) —
all fixed. Driver-consumability verified against parse_depends_on + the epic task-list regex. Scan
clean. Suite 1984/0 → 1990/0.

**Status.** PR #210 squash-merged `4a54482`, v3.6.0, issue closed.

### #194 — reliable external skill/command use (probe + vendored-copy) · v3.7.0

**Issue.** #194 (P8+P10): nothing verified a built-in/plugin skill existed before a gate
relied on it (the #162 trap), and running an external command by hard cache path is brittle.
Build ONE primitive.

**What shipped.** `hooks/external_ref_lib.py`: `probe(kind, name)` (version-independent cache
lookup — numeric version sort, not lexicographic — reports exists/trusted; a miss is a VISIBLE
skip), `vendor_copy(...)` (durable gitignored copy + sha256-manifest refresh + retained
`vanished` alert), and a trust-gate (`is_trusted` + `RAWGENTIC_TRUSTED_MARKETPLACES`) because
an external command is third-party prompt content. CLI probe/vendor/is-trusted;
`docs/external-references.md`; `.rawgentic-vendored/` gitignored. First real consumer is #196.

**Reviews.** Small-standard lane (new .py primitive). 1 opus: 2 Medium — lexicographic version
pick (3.10.0<3.9.0) → numeric sort; path-traversal via `name` → bare-name guard on probe+vendor
— both fixed with tests. Also caught + fixed two spliced changelog headings from earlier slots
and added a permanent garble drift guard. Scan clean. Suite 1990/0 → 2007/0.

**Status.** PR #211 squash-merged `f8d2252`, v3.7.0, issue closed.

### #195 — OAuth-first authenticated Action reviews; migrate #166 · v3.8.0

**Issue.** #195 (P12, folds P11): run reviews as GitHub Actions authenticated by subscription
OAuth first, API-key fallback. The dedicated `claude-code-security-review` action is API-key-only,
so route security review through `claude-code-action@v1` too.

**What shipped.** `.github/workflows/claude-security-review.yml` migrated off the API-key-only
action to `claude-code-action@v1` (SHA-pinned) running `/security-review`. Auth resolves
OAuth-first: `CLAUDE_CODE_OAUTH_TOKEN` → `ANTHROPIC_API_KEY` → visible skip (`executed=false`).
Non-blocking + 10-PR tally preserved; `executed=true` gated on the review actually succeeding.
Output shape doc-verified (inline PR comments via `classify_inline_comments`); live run
owner-gated. Owner setup + self-hosted zero-secret alternative in `docs/config-reference.md`.

**Reviews.** Small-standard lane (CI yml). 1 opus: 2 Medium — missing `id-token: write` (the
action's default App-OIDC auth needs it) + `executed=true` emitted from secret-presence not
review-success — both fixed; 1 Low (inline posting doc-verified, owner-gated). AC3 honesty
confirmed. Scan clean. Suite 2007/0 → 2011/0.

**Status.** PR #212 squash-merged `36aa09a`, v3.8.0, issue closed.

### #196 — reopen #162: post-PR code-review via the Action · v3.9.0

**Issue.** #196 (P9, depends on #189 ✓ + #195 ✓): reopen the #162 review-switch with a mechanism
that works. `/code-review` can't be called from a skill, but claude-code-action@v1 can run it
post-PR (OAuth), capturing findings as `builtin_code_review` for the A/B #162 couldn't run.

**What shipped.** `.github/workflows/claude-code-review.yml`: post-PR built-in `/code-review` via
claude-code-action@v1 (OAuth-first, draft-gated + `ready_for_review`, SHA-pinned, non-blocking) —
the candidate `builtin_code_review` arm running **additively** to WF2's hand-rolled Step 11 (coverage
never drops), which breaks #162's circular gate. With #189's telemetry, the AC4 A/B is now
**computable**; the #162 decision doc is reopened as "computable, pending owner-gated data." Capture
mechanism documented (run-records.md). WF5 diff pass unchanged.

**Reviews.** Small-standard lane (CI yml). 1 opus: 1 Medium — draft-gate missing `ready_for_review`
trigger type (draft→ready transition wouldn't fire) — fixed + guarded; everything else clean
(additive verified, original ABANDONED record preserved, AC2/AC3 honestly scoped). Scan clean.
Suite 2011/0 → 2020/0.

**Status.** PR #213 squash-merged `35413a7`, CI green, suite 2020/0, issue closed
(backfilled by the #197 pass).

---

### #197 — official versioned workflow diagram · v3.10.0 — LAST epic-#188 slot

**Issue.** #197: the canonical, versioned workflow diagram — workflow-only view,
clickable per-phase drill-down, version history, committed to the repo. Separate from
the health/proposals overlay. Owner: build with Fable, award-grade showcase visual.

**What shipped.** `docs/workflow-diagram.html` — self-contained hash-routed SPA styled
as an engineering drafting document (title block, REV stamps as the version selector,
revision triangles Δ, loop-back return arcs; colored-ink vellum light / luminous
blueprint dark; embedded OFL fonts, zero external requests, DOM-builder rendering — no
`innerHTML`, test-enforced). Full 19-station WF2 drill-down (purpose, sub-steps, gate
facts, lane behavior per station) at REV 3.10.0 + the pre-campaign 3.1.0 snapshot
(SUPERSEDED stamp, per-station overrides incl. facts/lane); WF1 (7) / WF3 (15) / WF5
(5) skeletal phase sheets from their pinned skills. README embeds theme-aware
snapshots (`docs/assets/workflow-diagram-{light,dark}.png`, GitHub `<picture>`
pattern) linking to the interactive page; `docs/workflow-diagram.md` carries the
append-a-revision + snapshot-regeneration recipes; GitHub Pages (main + `/docs`)
serves it live once owner-enabled. Guarded by `tests/test_workflow_diagram.py`.

**Process.** Owner-gated design round: mockup approved after 3 rounds (drafting
concept → color enrichment per "too plain/too white-black" → WF1 tab first); final
artifact stored in `docs/` base per owner override of the AC4 `docs/planning/` path.

**Status.** PR #214 squash-merged `2fe2e0e`, CI green (incl. first live firing of the
#195/#196 Action review lanes), suite 2034/0. Issues #197 AND epic #188 closed —
**campaign complete, 9/9 slots.** Telemetry embedded below.

---

## Slot 15 — #165: headless Action pilot · v3.1.0 — M4 crown, campaign capstone

**Issue.** #165 (M4): label-triggered headless WF2 on GA tooling
(claude-code-action v1), folding #48 (STATUS comments), #51 (large-PR warning),
#52 (progress guardrails). The overnight end-state: label an issue, get a PR.

**What shipped.**
- `.github/workflows/rawgentic-auto.yml`: `rawgentic:auto` label → headless
  `/rawgentic:implement-feature <n>`, PR-terminal. Job-level label gate,
  per-issue concurrency, `timeout-minutes: 120`, SHA-pinned action,
  subscription-OAuth secret by NAME, runner-local workspace bootstrap (WF2
  config-loading needs a workspace file; the checkout is the project repo),
  `plugins`/`plugin_marketplaces` from the repo's own PUBLIC marketplace —
  every external contract read from the action's own action.yml, not memory.
- `headlessEnabled` object shape: `{"enabled", "triggers", "auth"}` — fail-closed
  per-trigger allowlist against `RAWGENTIC_HEADLESS_TRIGGER` in session-start
  (jq verdict, 9 exotic inputs probed fail-closed), mirrored in `/switch` +
  setup Step 2c prose; auth-mode decision recorded per repo (AC5/AC7).
- STATUS comment type (#48): `format_status_comment()` + CLI `--type status` —
  non-blocking, metadata carries NO question_id so the resume path can never
  mistake it for a pending question; five step-boundary posts in headless.md.
- Large-PR warning (#51): Step-12 PR comment past `RAWGENTIC_LARGE_PR_FILES`
  (default 50, the issue's own default). Guardrails (#52): job timeout as the
  hard wall + STATUS heartbeat as the liveness signal.
- Suite 1936/0 → 1970/0 (34 new: 12 yml-structural, 9 shape, 5 STATUS,
  5 corpus drift guards proven red-on-old-prose, 3 hardening).

**Decisions (this slot).**
- Shape-extend `headlessEnabled` instead of adding a staged workspace field —
  keeps #184's setup↔manifest drift guard green by construction.
- Reduced fold scope named honestly: #48's machine-metadata AC descoped to
  free text; #51 interactive warning + #52 self-diagnosis design deferred.
- Never echo the trigger env value in the BLOCKED message (8a F1): the deny
  path writes to the model's instruction channel; any echo is an injection rider.

**Reviews.** Step 8a fired twice (T2 gate, T3 yml; 2 opus each): trigger-env
prompt-injection High fixed, concurrency/SHA-pin/label-approval-contract
hardening applied; **HIGH environmental finding — `main` had no branch
protection** (verified 404 + empty rulesets): ruleset creation was
permission-gated in-session, handed to owner as an exact command; **the pilot
must not go live before it.** Step 11 (2 opus): 1 Medium config-reference
field-table leftover (the #161 Critical's exact class — caught this time) +
threshold 25→50 correction; runtime reviewer probed the jq gate and bootstrap
end-to-end, no material findings. Security scan clean.

**Status.** PR + CI + merge SHA recorded post-merge this session. Live
end-to-end success metric (1 issue, zero touches) is owner-gated: repo secret
`CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`), the main-branch ruleset, then
label a real issue `rawgentic:auto`. Telemetry embedded below.

---

## Slot 14 — #161: v3.0.0 — six workflows removed, upgrade guide shipped

**Issue.** #161 (M3 capstone): bundle the 2.x breaking changes into one v3.0.0
boundary — one migration event for consumers instead of a drip.

**What shipped.**
- **BREAKING:** the six workflows deprecated at v2.60.0 (#160) are **removed** —
  `refactor` (WF4), `update-docs` (WF7), `update-deps` (WF8), `security-audit`
  (WF9), `optimize-perf` (WF10), `create-tests` (WF12) — plus their eval
  workspaces and their Codex-mirror symlinks; the marketplace `skills` whitelist
  drops 19 → 13. Zero STUB-FIRED telemetry across the deprecation cycle backed
  the verdict.
- `docs/upgrade-3.0.md` (AC1): replacement table (verified verbatim against the
  deleted stubs' own redirects), what-moved recap, cache-refresh steps, config
  notes — a removed name left in `adversarialReview.workflows` is inert
  (code-verified fail-closed membership matching).
- `tests/test_v3_removals.py` (23 red-first guards) replaces the stub drift
  guards: gone-stays-gone in BOTH trees, whitelist/description scrub, README
  body reference-freedom, guide presence.
- CHANGELOG: v3.0.0 entry **plus the missing v2.57.0–v2.66.0 backfill**
  (slot-13 follow-up closed; all 12 entries spot-checked against git by a
  reviewer — no drift).

**Reviews.** Small-standard lane, **lane-widened** honestly logged (real impl
count 146 vs estimate 4 — deletion mass, not new logic). Step 11 (2 opus):
**1 Critical, independently found by both reviewers** — the README's main SDLC
catalog table still advertised all six removed skills as invocable commands
(the removal sweep missed it and no test covered it); fixed + a red-first README
guard added. 2 Medium + 3 Low doc/manifest fixes. Adversarial diff review
mechanically skipped (no security surface). Security scan clean. Suite
1941/0 → 1936/0 (stub tests replaced by removal guards).

**Status.** PR #202 squash-merged `ea4e048`, v3.0.0, issue closed. CI green
(68s); AC3 verified via Mirror-to-STARS Action success @ea4e048 (evidence
comment on #161). **M3 COMPLETE.**

---

## Slot 13 — #184: version-aware setup prompt · v2.67.0

**Issue.** #184 (M3, epic #169): shipped opt-in features sit dark because
nothing tells users to re-run `/rawgentic:setup` after an upgrade — while the
existing post-update nudge re-nagged about the *same* unconfigured features on
**every** version bump. Fix: prompt only when the upgrade actually shipped a
setup-requiring feature.

**What shipped.**
- `hooks/post_update_reconcile.py` (the existing SECTION 2f mechanism — extended,
  not duplicated): `FEATURE_MANIFEST` entries gain `since` (the plugin version
  that introduced each setup step, verified against git history: headlessEnabled
  2.18.0 · adversarialReview 2.24.0 · modelRouting 2.46.0 · peerConsult 2.46.0 ·
  designArtifact 2.63.0) and the manifest expands 2 → 5 entries. needs-question
  nudges fire only when the reconciled-version jump crosses a `since`; an upgrade
  shipping nothing new bumps the marker **silently**.
- Numeric tuple version compare (never string compare); missing marker = fresh
  install = version zero; unparseable versions fail **open** toward prompting;
  `since`-less override entries keep legacy always-eligible semantics.
- Workspace top-level `"setupPrompt": false` opt-out — suppresses all output,
  still bumps the marker (lifting the opt-out prompts only on the next upgrade).
- Prompt now names the new feature(s) + affected projects, that setup preserves
  existing config, the no-re-nag guarantee, and the opt-out.
- **Drift guard (AC6):** manifest keys must equal the fields staged by setup
  SKILL.md's write-back sentence (anchored extraction, fail-loud), each with a
  valid `since` ≤ installed — a new setup opt-in step cannot ship without its
  manifest entry.

**Decisions (this slot).** Record-at-print marker semantics (a SessionStart hook
cannot observe accept/decline — the AC4 intent "same version never nags twice"
holds identically); workspace-level opt-out only (AC5's "per-project/workspace"
read as either-granularity; one kill-switch is the meaningful UX). Pre-existing
flaw named: README Changelog was missing v2.57.0–v2.66.0 entries — backfill
logged as a campaign follow-up. Between slots: #199 (PR #200, v2.66.0) shipped
the roadmap card style this log now renders with.

**Reviews.** Small-standard lane (1 impl file). Step 11: 2 opus reviewers, **0
Critical/High/Medium**; 3 Low (strict-boolean opt-out ruled working-as-designed
by both; one advisory test-completeness fix applied — the "won't repeat" wording
is now pinned). All 5 `since` values independently re-verified against git
history by reviewer 2. Adversarial diff review mechanically skipped (no security
surface). Security scan clean (iac/sca visible skips). Suite 1927/0 → 1941/0.

**Status.** *(backfilled by slot 14's pass)* PR #201 squash-merged `6c375b1`,
CI green, v2.67.0, issue closed. Telemetry embedded below.

---

## Slot 12 — #189: capture usage token/cost in run-records · v2.65.0

**Issue.** #189 (owner-promoted from fast-follow epic #188): the run-record `usage`
object existed (#155/#172) but nothing populated it — **null in all 24 records** —
so #162's yield-per-token gate was incomputable. Fix: capture real numbers + backfill,
with **non-vacuous** tests (AC5, explicitly "better than #155's schema-only tests").

**What shipped.**
- `hooks/usage_capture.py` — parses the Claude Code session transcript directly (same
  source as `ccusage`; stdlib-only, deterministic, no network). Sums per-model tokens
  into `model_mix` + totals + a rate-card cost, excludes the `<synthetic>` pseudo-model.
  `capture` (live, Step 16) + `backfill` (historical) subcommands, with a path-traversal
  guard on the session id and UTF-8-resilient reads (the current log may be mid-write).
- **Validator backstop** (`hooks/work_summary.py`) — `usage.capture_status` controlled
  vocab `{captured, unrecoverable, unavailable}`; a `captured` claim REQUIRES positive
  input + non-negative output, so the #155 null/zero-forever state can no longer persist.
- **Backfill applied** — the 12 historical usage rows carry no session-id correlator, so
  they are marked `unrecoverable` (honest per AC2; never silently null).
- Step 16 capture wiring documented + **pinned by a corpus drift-guard**; AC3 store
  drift-guard forbids any usage object with null/zero tokens and no marker.

**Non-vacuity (AC5).** Tests assert real-fixture known-value totals (865/90), end-to-end
capture, backfill against known values, and **red-before-green** guards for the
present-but-zero and zero-token-no-marker paths — the #155 failure mode in its new forms.

**Reviews.** Step 8a on both high-risk tasks caught **2 empirically-confirmed High** bugs
(non-vacuity guard checked block-count not token-sum = the #155 mode recurring; UTF-8
crash on a mid-write log) — both fixed. Step 11 (2 opus reviewers) caught **3 Medium**
(drift-guard zero-token blind spot; unpinned wiring; captured input=0) + 1 Low — all
fixed. Security scan clean (iac/sca visible skips).

**First real telemetry.** This slot's own run-record is the **first with non-null captured
tokens** (session-scoped — a documented granularity limitation). Suite 1907/0.

**Status.** *(backfilled by slot 13's pass)* PR #198 squash-merged `f6e2682`, CI
green, v2.65.0, issue closed. Telemetry embedded below.

---

## Slot 11 — #162: review switch — ABANDONED per AC4 data gate · v2.64.2

**Issue.** #162 (Step 4 reflect-only + Step 11 built-in /code-review + WF5 diff
pass) was **data-gated** by its own header ("no A/B evidence, no switch") and
AC4 ("matched hand-rolled yield over ≥10 runs at lower cost … otherwise abandon
this issue with the data cited").

**The data (23 run-records as of 2026-07-04).** The candidate arm
(`builtin_code_review`) has **0 gate-instances** — it never ran (≥10 required).
Token/cost telemetry (`usage.input_tokens`/`output_tokens`/`cost_estimate_usd`)
is **null in all 23 records**, so the success metric (findings-yield per token)
is incomputable for *any* arm. Incumbent arms: hand_rolled_multi 41 findings/11
gates · codex 16/4 · inline 19/13.

**Decision.** Abandon per AC4's explicit branch — a **deferral pending
telemetry, not a rejection** of built-in `/code-review` (Codex peer-consult
concurred). The roadmap's "the program is its own A/B" assumption was circular:
campaign runs could only generate candidate-arm data *after* the switch this
gate blocks. Reopen conditions: pilot built-in `/code-review` as an *additional*
Step 11 reviewer for ≥10 runs + backfill token telemetry. Full record:
`docs/measurements/2026-07-05-issue-162-data-gate-decision.md` (drift-guarded
by `tests/test_decision_records.py`, which recomputes the evidence basis from
`run_records.jsonl` records[:23]).

**What shipped.** Decision record + 3 drift-guard tests (one recomputes the
evidence from the store) · README/roadmap/dashboard annotations · v2.64.2.

**Owner directives (mid-slot).** #184 (version-aware setup prompt) inserted
into the campaign as **slot 12, before #161** — slots renumbered 12=#184,
13=#161, 14=#165; run doc, dashboard, roadmap updated.

**Reviews.** Step 11: opus reviewer (2 Low: citation + count fix, both applied)
+ Codex adversarial diff pass (2 Medium: drift-guard vacuity — test now parses
the store; applied). Security scan clean (iac/sca visible skips).

**Status.** *(backfilled by slot 13's pass)* decision-record PR #187
squash-merged `e7aadf7`, CI green, v2.64.2. Telemetry embedded below. Issue
closed as *not planned*, data cited.

---

## Slot 10 — #148 + #163: multi-issue driver (M3 start) · v2.64.0 <br>*(status backfill: PR #185 squash-merged `d7ea584`; post-merge hardening review → PR #186 `5e15862`, 7 findings fixed, v2.64.1, suite 1863/0)*

**Issues.** #148 (build the multi-issue driver as a documented pattern + queue
state schema, from design #134) and #163 (dependency-DAG + epic anchor,
schema v2) — implemented together in one PR (#163 extends #148's queue).

**What shipped.**
- `docs/multi-issue-driver.md` — the documented driver **pattern**: the loop
  (WF2 fresh per issue; advance on merge / `pr_open` when headless; park on
  DEFER), policy (order / deploy / review-budget / never-Haiku), the DEFER
  taxonomy + deterministic branch-preservation rule, the rollback-anchor
  protocol, the dependency-DAG ordering, the epic anchor, and the resumption
  reconciliation table (intra-WF2 resume delegated to `resume_lib`). Explicitly
  does **not** weaken WF2 — each iteration is a full run terminating at Step 16.
- `hooks/driver_lib.py` — the narrow, unit-tested DAG surface: `parse_depends_on`
  (word-boundary, negation-aware, sentence-bounded), `topo_sort_issues` (Kahn,
  fail-closed on cycle), `next_ready_issue` (deps-satisfied advance rule +
  `deps_satisfied_by` knob), `validate_driver_state` (v1/v2 readability +
  serial-active invariant), `validate_campaign_start` (headless-requires-epic).
  The fuller state-transition validator stays deferred (design #134 follow-up #2).
- `docs/driver-state/` — the git-tracked schema (`queue.schema.json`) + v1/v2
  example campaign files (live per-campaign state is disk-persisted under the
  gitignored `claude_docs/.driver-state/`).

**Decisions (this slot).**
- #163 DAG fork (Codex-consulted → option C): #148 stays pure-doc; #163 ships the
  *narrow* DAG helper only. Its algorithmic ACs ("cycles halt fail-closed",
  "0 ordering violations", "v1 readable") can't be verified as prose.
- "Committed" queue state reconciled with the gitignored `claude_docs/`: the
  live state file is *durably persisted to disk* (the resumption substrate); the
  git-tracked contract (schema + examples) lives in `docs/driver-state/`.

**Reviews.** Per-task 8a (2 reviewers) hardened `parse_depends_on` + fail-closed
number guards. Step-11 Codex diff review (owner-directed) applied 4 findings
(sentence-boundary parse, serial-active invariant, `validate_campaign_start`,
persist-topo-order-in-doc); concurrent Claude review reworded the overstated
"prompt-injection-safe" claim to an honest best-effort filter. Security scan
clean.

**Status.** PR + CI + merge SHA filled by the next slot's pass (established
convention). Telemetry for this slot is embedded below.

---

## #927 — the epic-run child boundary becomes the default, with a fence (epic #871, M4 wave)

**Shipped.** v3.132.0 (part 1, `19925711`) + **v3.133.0 (part 2)**. Part 1 was honestly scoped as
machinery with no caller (D233, after the pre-PR review caught a changelog entry claiming a working
fence while `_cmd_handoff` was unchanged). Part 2 is the wiring, and it carries `Closes #927`.

**What part 1 actually left behind, measured at `19925711` rather than assumed.** Twelve functions
appeared ONLY in `hooks/driver_lib.py` where they were defined; `resolve_creation_transport` had no
caller; there was no `transport` CLI verb of any kind; and `skills/epic-run/SKILL.md` contained the
string `transport` zero times. So **AC 1, AC 2 and AC 4 had no live path** — not merely an unwired
fence. That measurement is why part 2 absorbed four items the handoff into this pane had not listed
(D237): the creation seam, `transport set`, `transport unpark`, and the `creation_refused` downgrade.

**The design.** `docs/planning/2026-08-05-927-epic-run-transport-rework.md`. Sections 1–14 are the
settled three-pass design; §15 is its pass-3 findings table, which was cited from SEVEN places and
had never been written (its headings stopped at §14 — every one of those references was dangling);
§16–§17 are part 2's implementation design and platform declaration; §18–§20 are part 2's own
review passes.

**The ordering IS the safety property.** Claim before probe; resolution (with the pre-split pane
inventory) before any launch; `split_attempted` before the split; then terminal outcome, downgrade
and claim release in ONE locked mutation. `split_attempted: false` therefore PROVES nothing was
created, and a `null` successor under `true` is resolved by an inventory diff rather than trusted.

**Decisions (this slot).**
- **D235** — part 1's spent loop-back counters rotated aside so part 2 ran on a fresh budget; the
  counters file is keyed by ISSUE, not by run.
- **D236** — declined an ELIGIBLE small-standard lane and ran the full spine, because what this
  wires is the fence whose failure mode is two successors on one generation.
- **D237** — kept as ONE PR rather than splitting code from prose: shipping another caller-less
  command is exactly the defect D233 recorded against part 1.
- **D238 / D239** — the ambiguity circuit breaker fired on three findings across two design passes.
  Every one was resolved from the SHIPPED CODE rather than escalated to an away owner: two were
  confirmed real defects, and one was REFUTED with file:line evidence (`_terminal_for` already
  returns the latest terminal event, so the reviewer's repeated-unpark scenario cannot occur).

**Reviews.** Two cross-model design passes (`gpt-5.6-sol`, both `diagnostic: false`, freshness
verified): **1 Critical + 11 High + 4 Medium**, 15 applied, 1 refuted. The Critical was mine — a
claim-refused contender was told to "continue in place", which at a boundary means starting the
next child beside the holder's successor. The gate then CLOSED budget-exhausted at the `design`
cap per #798. Step 4's inline self-review added 2 input-validation findings on the new CLI trust
boundary, both applied.

**Measured, not reasoned.** The negative `pane split` probe part 1 owed (§11) was run live with the
exact shipped argv: rc 1, empty stdout, `{"error":{"code":"pane_not_found"}}` on stderr, pane
inventory unchanged — nothing created. That measurement NARROWED the design: the transport downgrade
now triggers on an enumerated spiked code rather than on an inferred failure shape.

**Also owed and recorded.** Close-or-fold dispositions on #846 (open, scope narrowed — the boundary
path now records completion, mid-child still does not), #849 (open, unchanged), #850 (open, scope
widened — three new CLI verbs the jam matrix will not cover) and #851 (open, unchanged).

**Status.** Suite 5584→5628 (+44), exit 0. No workflow-spine change → no diagram REV. PR, CI and
merge SHA filled by the next slot's pass, per the established convention here.

---

## #947 — supervision behaviour Part B: preflight, routing, claims, authority (epic #871, M4 wave)

**Shipped.** New `hooks/supervision_route.py` (`CampaignView`/`evaluate_campaign`/
`route_for`/`authority_permits`/`consult_permitted`/`consult_check`/
`validate_supervision_override`), `hooks/supervision_claims.py` (revision-bound action
claims: `claim_action`/`begin_execution`/`mark_executed`/`cancel_claims`/
`reconcile_claim`), `hooks/supervision_preflight.py` (departure-preflight staging).
Modified `hooks/supervision_lib.py` (`transport_verified` on Part A's own view),
`hooks/supervision_admin.py` (`declare(preflight_token=...)` fold-in,
`mark_transport_verified`), `hooks/driver_lib.py` (`set_supervision_override`, the
tighten-only field's sole writer), `hooks/review_runner.py` (`--allowed-backends`).
Wired into `skills/away`, `skills/sleeping` (preflight sweep before declaring),
`skills/back` (cancels pending claims on return), and this repo's three real
`review_runner.py consult` call sites (implement-feature Step 3, peer-consult/WF13). New
`tests/test_askuserquestion_registration.py` guard (AC8).

**The design.** `docs/planning/2026-08-06-947-supervision-behaviour-part-b.md`. Three
review rounds (§16 records provenance): round 1 fixed 6 High + 2 Medium, round 2 fixed
7 High + 1 Medium (design loop-back budget then exhausted, 2/2), round 3 CLOSED
budget-exhausted per the #798 carve-out with all 12 findings terminally disposed (10
applied, 2 refuted with evidence — the `AskUserQuestion` platform-citation question,
asked three times across the gate, is the same harness-tool refutation Part A's §12
already made).

**Step 6 (plan review) found what the design gate could not — that the plan itself
didn't follow through.** A fourth cross-model pass, this time over the 14-task
implementation plan rather than the design prose, found 2 Critical + 3 High + 3 Medium.
Two were genuinely NEW: the design's own §1a already committed #947 to wiring the ONE
`consult` call site through `consult_permitted`, but no task implemented it (fixed:
Task T9b added) — and a High/High pair the design gate's three rounds had never reached
because they lived in the PLAN's restatement of already-settled design mechanisms, not
the mechanisms themselves (T8's RED criteria dropped an ordering the design's own §7
already stated; fixed by rewording, no design change).

**Two genuine design-level flaws surfaced at Step 6, with the design loop-back budget
already exhausted — escalated to the owner rather than silently reworked (D267).** (1)
The 500-token consumed-preflight-token cap could, after 500+ intervening departures,
let an old undelivered staging file replay and re-fold; the design's own text called
this "not load-bearing for correctness," which the reviewer correctly disputed as an
overclaim, even though the design's operational-implausibility reasoning is sound. (2)
`transport_verified` checked a 24h timestamp but never that the verifying session
matched the CURRENT session — a verification from an OLDER session stayed trusted in a
brand-new one. Owner reply (option 2 of 3, via `/rawgentic:ask-owner`, ~18 minutes):
tighten (2) — Task T1 now requires `verified_session_id` to exactly match the current
session — and accept (1) as documented risk, matching the design's own reasoning.

**Decisions (this slot).** D267 (the ask-owner escalation above); the Step-6 ledger
(`claude_docs/.wf2-state/947/dispositions.jsonl`, gate=6) carries all 8 plan-review
findings' terminal dispositions, 3 gate=4 dispositions from the design rounds' own
close, 15 entries total.

**Status.** Suite 5896→(final, this PR) — filled at merge, per the established
convention here. Branch `feat/947-supervision-behaviour-part-b`. PR, CI and merge SHA
filled by the next slot's pass. #927's own deferred integration (wiring
`authority_permits`/the claims lifecycle into `hooks/launcher_lib.py`'s merge step) is
NOT this slot — it is a future issue by the design's own §1a scope boundary.
