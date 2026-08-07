# Rawgentic rationalization roadmap — get unbroken, then get simple

**Date:** 2026-08-03 · **Authors:** owner + Fable 5, with gpt-5.6-sol as consultant (xhigh, repo-verified)
**Supersedes:** epic #756's queue order and the 2026-08-01 "keep the executor" ruling.
**Decisions:** D174–D179 in `claude_docs/decisions/rawgentic.jsonl` (workspace root). Each carries its undo.

```stats
68 | issues open today
0 | untracked by a milestone | accent
10 | closed since 2026-08-06
3 | corrections this pass
D187 | decision log tail
```

```callout
ok | Updated 2026-08-05 (second amendment, D185) — M2 PAUSED at 3/16; M4 re-scoped and runs NEXT
M0 (the retreat, PRs #867/#868/#870/#872) and M1 (#856/#761/#822, epic #875) are merged. The
2026-08-05 backlog rationalization closed 47 issues, folded 3 duplicates, filed 3 decision
tickets, and reordered everything below M2 by impact — full evidence in the companion doc
`2026-08-05-backlog-rationalization-post-m1.md` (§11 has the summary). The original 2026-08-03
text below M0/M1 is kept as history; M2-and-beyond reflects the new ordering.
LATER the same day (D185): with #916, #928 and #731 merged, M2 was PAUSED and M4 was re-scoped to
SESSION CONTINUITY & UNSUPERVISED MODES (away · sleeping) and moved up to run NEXT — #927, #769
and #726 fold into it, #888 moves up from M2.5, and new children #943 (supervision core) and #944
(revalidate hardening) were filed. Full plan: https://rawgentic-plan-871.vercel.app/ and
`2026-08-05-871-m4-session-continuity-away-mode.md`. M2's remaining ten children resume after.
Updated 2026-08-06 — M4 (epic #871) is COMPLETE. All 8 children merged and closed: #888, #943
(supervision core, split into Part A/#948 + Part B/#947), #927, #769, #726 (issue stays open by
owner decision — two ACs unsatisfiable, noted on the issue itself), #586, #944. Final version
3.137.0, full suite 6087/6087. Epic #871 closed 2026-08-06 with a summary comment listing every
child, PR, version and merge SHA. M2 (epic #906, PAUSED above) is the next milestone to resume —
that resume is a separate, later, owner-started run, not automatic.
Updated 2026-08-07 — REASSESSED against main at `714fdeb7`. All **68** open issues checked; every
one is tracked by a milestone, and none was left unabsorbed. **Three roadmap claims were wrong and
are corrected below** (§12): the receipt machinery D176 said would retire is still live, #792's
issue still names the deleted executor, and the supervision line kept shipping past M4 (#963, #976)
without appearing here at all. M2 (epic #906) remains PAUSED at 3/16 and is still the next
milestone to resume. Two defects found during builds are NOT filed, per the D179 throttle — they
are listed in §12.4 for your call.
```

## The roadmap

```phases
M0 — UNBREAK: the retreat | SHIPPED 2026-08-04 · PRs #867 #868 #870 #872 + #869 | ok
  M0a | Review runner landed: codex+GLM, pinned reviewer identity, reopen-token choke point (#855), error classes | ok
  M0b | Behavioral cutover: WF2/WF3 inline; runner at Steps 4/8a/11; WF5/WF13 cut over — WF2 runnable again same day | ok
  M0c | Config contraction, with shims | ok
  M0d | ~33k lines deleted; tripwire + smoke guards; cutover done | ok
M1 — STAY SMALL | SHIPPED 2026-08-05 · epic #875 | ok
  #856 | CI byte ceilings + steps.md split into step-local files (#874) | ok
  #761 | Task-class field through drafting + review gates (lane split to #923) | ok
  #822 | Version-surface + changelog check folded into the pin test | ok
M2 — THE PANE-HANDOFF CHAIN | epic #906 · PAUSED at 3/16 (D185) — resumes after M4 | warn
  QW | Quick wins SHIPPED: #916 redeploy (no PR, D182) · #928 evals harness (PR #941, v3.129.0) | ok
  1 | Launcher robustness: #731 SHIPPED (PR #942, v3.129.1) · #835 remains — #800 already shipped (PR #912) | note
  2 | Meter rework (D177): #797 55/75 + rolling summary · #729 residual (ack-tracking) · #734 blind-start | note
  3 | #726 → MOVED to M4 (D185) | ok
  4 | Epic-run rework (D176): #927 + #769 SHIPPED in M4; #845 + #848 settled — but #846/#849/#850/#851 are LIVE WORK, not retired (§12.1 C1: rebuild_receipt/pending_disposition/jam_matrix still exist) | warn
  5 | Trusted goal reader: #864 + #772 + #878 | note
  6 | #806 goal cap from the constant + exact-text display; no auto-arm | note
  SP | M1 spillover: #923 WF2-lite lane · #899 word budget | note
M2.5 — MINIMUM TELEMETRY TRUTH | epic #935 · after the M4 wave | warn
  #888 | → MOVED up into the M4 wave (D185, records-first minimum) | ok
  #363 | Per-run usage attribution — the #976 record proves it: wall_clock_s null, usage session-cumulative | note
  #356 | Dispatch entries that survive session seams | note
  #361 | PROPOSED move from M6 (§12.2): Step-16 has zero session-note cross-checks, and #976 shipped a record with timing absent | warn
M3 — TRUST THE NEW MACHINERY | epic #936 · runner · review loop · config | warn
  RH | Runner hardening (siblings, not merged): #876 trusted --brief · #894 death/OOM evidence · #365 contaminated returns · #893 engagement evidence | note
  RL | Review loop: #889 clean-round token refund · #892 re-litigation flag · #891 retire fail-open riskLevel default · #895 blindness guard | note
  CFG | #884 + #883 config pair · roadmap tail: #860 consult-on-exhaustion · #808 WF3 close · #750 registry helper · #759 deferral registry | note
M4 — SESSION CONTINUITY & UNSUPERVISED MODES | epic #871 · SHIPPED 2026-08-06 · v3.137.0 | ok
  #888 | Records first: run-records that land exactly once (moved up from M2.5) — SHIPPED PR #946 | ok
  #943 | Supervision core: Part A SHIPPED PR #948; Part B split to #947, SHIPPED PR #961 — issue closed | ok
  #927 | Epic-run rework — SHIPPED PRs #950/#951 | ok
  #769 | Child-boundary learnings sweep, mechanized state — SHIPPED PR #953 | ok
  #726 | In-flight-work gate + durable-path check — SHIPPED PR #954; issue stays open, two ACs unsatisfiable (noted on the issue) | ok
  #586 | Resume rewrite — SHIPPED PRs #955–#958 | ok
  #944 | Revalidate hardening — SHIPPED PR #959 | ok
M4+ — SUPERVISION, CONTINUED | shipped AFTER M4 closed · was missing from this roadmap (§12.1 C3) | ok
  #963 | Supervised-merge broker: the first live caller of the #871 authority core — SHIPPED PR #973, v3.138.0 | ok
  #976 | PreToolUse enforcement of the broker + the broker's own campaign-binding bug — SHIPPED PR #978, v3.139.0 | ok
  FX | Follow-ups the same day: #981 step-state bootstrap (v3.139.1) · #982 WAL rotation lock (v3.139.2) | ok
M5 — IDENTITY & CONCURRENCY + WF HYGIENE | epic #937 · grouped pairs | note
  P1 | #345 + #346 one keying design, two migrations · #593 + #594 concurrent same-project sessions | note
  P2 | #372 WF14-vs-guard · #364 doc-only resume state · #370 + #379 ambiguity-breaker pair | note
  P3 | #395 authored-blind checklist · #658 call-site inventory · #890 consumer-project hook invocation | note
M6 — TAIL | epic #938 · small, independent | note
  #361 | Step-16 assembly cross-checks — PROPOSED move up to M2.5 (§12.2); no longer a tail item | warn
  #380 | wal-context internal deadline — confirmed absent from hooks/wal-context, not started | note
M7 — SKILLS & TOOLING | epic #939 · convenience tier | note
  T1 | #400 usage auditor · #390 workspace-doctor · #391 session-index v2 | note
  T2 | #534 retire epic-run-analysis (unblocked, #508 shipped) · #536 deploy-verify · #537 security-vet · #399 group inference · #350 CodeGuard | note
LATER — with reopen triggers, not vibes | watch list §6 refreshed | note
  #792 | Quota preflight — RE-TITLE OR CLOSE (§12.1 C2): the issue still says "executor" and "Claude-lane dispatches", both deleted in M0d | crit
  WF | #358 + #359 (#360) WF15/16 codex design workflows | note
  DT | #931 native sandboxing · #933 sandbox-runtime — still bare decision tickets | note
  #932 | NO LONGER just a decision ticket: design SHIPPED 2026-08-06 (PR #980) — promote to an M3 build candidate | warn
  PK | #738 parked (trigger: project reactivates) · #568 Hermes substrate (trigger: a consumer beyond ask/notify-owner) | note
```

```legend
crit | broken or flaky today — fix first
warn | protects size or reliability
ok | shipped / done (in M0's rows: deletion wins)
note | scheduled, not started
```

**Where the 48 issues land:**

```composition
closed | 28 | ok
combined into 3 work items | 8 | warn
rescoped | 7 | warn
kept as-is | 5 | note
```

---

## 1. Where we are, in plain words

rawgentic is broken on main today. Three things broke it:

1. **WF2's implementation path points at a machine that has never run.** Every implementation dispatch routes through the executor build seat (`steps.md:997`) — a seat with zero real-run history, that only codex can drive, and codex is weekly-quota-capped.
2. **All three mandatory review gates fail as written.** The dispatch command the prose gives omits a flag the code requires (`--author-provider`, #863) — exit 4, every time.
3. **The workflow prose outgrew what any model can follow.** ~75k tokens carrying ~302 hard obligations. Measured research puts the compliance cliff near **80 simultaneous rules** (IFScale, arXiv 2507.11538). The #840 run proved it: a session running WF2 bypassed three of WF2's own controls without noticing (#855).

The last 4 days (~45 PRs, ~30k lines) were mostly machinery to harden machinery, and each big
machinery PR spawned a new defect wave (#840's two PRs → six new issues; #829's review → three;
#825's review → two). **We do not revert** — about a third of those merges are genuinely good
silent-failure fixes (killed dispatches now report failure, the rate card is right, chain-burn is
stopped, severity is defined, logs archive before trimming). We stop feeding the loop and delete
the dead weight forward.

## 2. The rulings this roadmap builds on (all owner-approved today)

| ID | Ruling |
|---|---|
| **D174** | **Full retreat from the executor.** Analysis + implementation run inline. Cross-model reviews call the codex CLI **through a subagent** (parallel with self-review). phase_executor and every coupled hook are stripped out. Evidence: 276 recorded dispatches — codex review lane 97% ok, Claude lanes ≤66%, build seat never used in a real run. Version-bump surfaces drop 4 → 3. |
| **D175** | **Guardrail dial.** The ceremony layer dies (feasibility declarations, ratio/halt bands, parallel-group proofs, P15 SHA ledger, review-state refusals; completion gate 13→~8). The earned-by-incident guards stay (riskLevel tags, loop-back budget + High-deferral + severity/confidence via the one review runner, ambiguity breaker, secrets scanning, CI test+lint, wal-bind-guard, PR-only). ~34 distinct WF2 gates → ~15. |
| **D176** | **Epic-run rework.** Between-children continuation = **pane-handoff** (fresh successor pane per child). The claim/lease/generation-fence/receipt machinery retires. A lightweight stale-issue-body check stays at child startup (the #840 intent survives; the receipt subsystem does not). |
| **D177** | **Context meter: shrink, don't delete.** It must keep doing exactly two things (owner spec, verbatim): *"1) After the first threshold is passed, look for a clean break to do a pane handoff. 2) At the second threshold, let the session finish its current task and then force a pane-handoff."* Fix the latch bug, make the directive deliverable mid-turn, thresholds 55/75. |
| **D178** | **Kills:** #760, #763, #764 closed; #749 closed **and headless mode removed entirely** (the rawgentic-auto trigger is unused — all `[Headless:]` directives, headless.md files, the session-start headless gate, and `RAWGENTIC_HEADLESS` paths die). |
| **D179** | **One review runner + issue throttle.** A ~200–300-line runner (extracted from the proven codex path) serves WF2 gates, WF5 and WF13 — **GLM backend kept**. And a process rule: review findings never auto-become GitHub issues — fixed, explicitly declined, or PR-noted; a new issue needs owner confirmation. |

## 3. What happens to all 48 open issues

**Closed — 28** (each gets a closing comment naming the decision):

| Why | Issues |
|---|---|
| Retired by the retreat (D174) | #766 #775 #778 #779 #793* #794 #795† #799 #825 #832 #833 #834 #838 #839 #857 #861 #863 |
| Epic-run rework (D176) | #845 #846 #848 #849 #850 #851 |
| Owner kill call (D178) | #749 #760 #763 #764 |
| Producer boundary retired | #815 |

*\*#793's substance (noise-strip, truncation-retry) ships as runner acceptance criteria — see M0a. Residuals from #766/#832/#833/#834/#857 likewise become named runner ACs.*
*†#795 is closed with its ask (per-seat panes + live token ticker) **dropped by ruling**, not residualized — the runner emits start/end/error lines, which is less than #795 asked for. Said plainly so nothing is laundered.*

**Combined — 8 issues → 3 work items:** meter rework (#729+#734+#797, including #797's rolling log summary) · launcher robustness (#731+#800+#835) · trusted goal reader (#864+#772).

**Rescoped — 7:** #855 (runner reopen-token + `plan_lib review-reopen`; PR 1a's admission_journal is removed with the package — sunk cost, stated plainly) · #856 (M0 prose strip + M1 ceilings/split; the 302-row inventory and digest instrument are dropped) · #761 (WF2-lite lane only) · #806 (cap-and-display, no auto-arm) · #822 (extend the existing version-pin test; no new CLI) · #792 (LATER: small fail-open quota preflight) · #769 (NOT superseded by #840 — a findings-sweep across remaining issues is different from head-revalidation; it folds into the D176 epic-run rework as the child-boundary sweep step).

**Kept as-is — 5:** #726, #750, #759 (lite), #808, #860.

## 4. Milestone detail

### M0 — UNBREAK: the retreat (4 PRs, consult-sequenced so every merge is green and non-cutover until M0b)

**M0a — the replacement lands unused.** One small review runner (extracted from
`adversarial_review_lib` + the proven codex adapter; backends **gpt + glm**):
- interface: `review-code --base <ref> --brief <f> --author-model <id>` / `review-artifact --artifact <f> --type design --author-model <id>` / `consult --artifact <f>` (WF13 + #860 use the same runner — D179 means ALL of WF2/WF5/WF13, so the consult verb ships here, not in M3)
- owns ONLY: input validation, invocation, structured output, transport failure. Policy lives elsewhere.
- **reviewer identity is pinned, not inherited**: `--reviewer <model>` resolves to an explicit `-m`/backend selection (gpt|glm); author==reviewer or unresolvable identity REFUSES; the result echoes the transport-reported model, never an empty string (fixes the extraction's config-inherit hole at `adversarial_review_lib.py:1352`)
- **lineage or diagnostic — the #855 choke point**: an actionable run requires `--reopen-token <f>` minted by `plan_lib review-reopen` (which debits the existing atomic loop-back budget); without a token the runner still works but stamps the result `diagnostic: true`, and the workflow's disposition step mechanically refuses to open a fix round on a diagnostic result. Transport retries never debit.
- mandatory artifact parameter — a route that can't carry the bytes can't be called (#832 residual)
- bounded reads that **refuse** oversize instead of truncate-and-continue (#834); composition/capture time recorded in a `timing` field so the deadline means what it says (#834's second half)
- `codex exec --sandbox read-only --output-schema … -o <file>`; non-empty schema-valid output or terminal failure — a dead process/empty result/killed process is a FAILURE, never a pass and never "still running" (#766)
- **error classes, not a blanket retry** (#857 residual, in full): transport blip → one bounded retry; org-wide 429 spend limit → terminal, never retried; per-account/model 429 → retry once on the other backend is permitted; anything else → recorded-but-unclassified, terminal. The extraction must NOT keep the current blanket retry of every nonzero exit (`adversarial_review_lib.py:1346`). `quota_detect.py` dies with the package — this classification replaces it.
- result carries `{status, diagnostic, reviewer_model, input_sha256, base_sha, head_sha, timing, findings, summary, error_class}`; the orchestrator rejects results whose HEAD/artifact moved before disposition (freshness binding — closes the parallel-review race)
- noise-strip list (fixed generated-file set, visible; **no generic docs-only skip** — markdown IS executable behavior in this plugin); truncation → one bounded retry (#793)
- one start/end/error line for visibility
- dispatched from a **subagent** with the 3-line artifact gate (file exists, non-empty, shape-grep) so self-review and cross-model review run in parallel and vacuous results are caught mechanically

**M0b — behavioral cutover.** WF2/WF3 prose: inline analysis + implementation (delete the
primary/legacy split, `begin-run`/`mint-gate`, model-routing calls); Steps 4/8a/11 dispatch the
runner subagent; **WF5 adversarial-review and WF13 peer-consult cut over to the same runner in
this PR** (D179 is delivered by M0, not promised for later); the D175 ceremony cuts and the D178
`[Headless:]` directive removal ride the same edit; `model-routing-resolve` block rewritten as the
subagent contract. Everything the cutover invalidates moves WITH it so the PR is green alone:
the **run-record schema relaxation** (`architecture` optional-legacy — `work_summary.py:103/:463`
would otherwise fail every Step 16 on a record that can no longer truthfully say `executor|legacy`)
and the **prose-pin test rewrites** (`test_model_routing_resolve_prose.py` currently asserts the
executor command and `begin-run`). A negative guard asserts active prose names no executor entry
point. **WF2 is runnable again the day this merges.**

**M0c — config-surface contraction, with shims.** Setup skill + config reference + post-update
nudges + workspace configs (~20 `executorRouting` entries, `executorTerminalBackend`,
`phaseExecutorTable`, `telemetryAlerts`), `modelRouting` surfaces, headless config surfaces;
`agents/rawgentic-implementer` deleted, `agents/rawgentic-reviewer` replaced by the runner
subagent. **`capabilities_lib` keeps emitting its deprecated derived keys until M0d** — the
still-present executor code indexes them directly (`executor_routing_lib.py:446/:511`), so
removing the outputs one PR early is a KeyError factory; the ~70 validation lines leave in M0d
with their consumers.

**M0d — deletion + cutover.** `phase_executor/` (~10k src lines), `tests/phase_executor/` (~16k),
`executor_routing_lib` (3,984), `seat_outcomes_lib` (1,347), `complexity_gate` (296 — after
removing `plan_lib.py:29`'s module-load import and the `--gate-file` branch), `bakeoff_policy`
(685), `driver_bench_lib` (618), `diagram_seat_data`, **the old review engine in
`adversarial_review_lib` (2,628 lines → the extracted runner + kept GLM backend)**, the
`capabilities_lib` shims from M0c, the wal-bind-guard dispatch-claim exception, headless machinery
(session-start gate, headless.md ×2, `RAWGENTIC_HEADLESS` paths), herdr-pin becomes
self-authoritative, ~18 surviving test files triaged, canary version surface removed (4→3). Plus
two new guards: a **retirement tripwire** (static test — no retired imports/commands/config keys
outside explicitly archival dirs) and an import/CLI smoke test for surviving hooks. Diagram REV.
**Cutover is operational:** quiesce running work, merge, reinstall the plugin, fresh session;
`.rawgentic/runs/` history is read-only archival evidence, never deleted.

*Also closed by M0d: #449 (driver-bench executor cells — outside the 48 but its subject is deleted).*

### M1 — STAY SMALL: prose ceilings + the lite lane
- Measure the post-retreat corpus, then pin **CI byte ceilings** (total + per-file, glob-exact so a new unbudgeted file can't evade it) at actual + modest headroom (#856).
- `steps.md` → step-local files, loaded one step at a time (SKILL.md stays a short index — Anthropic's own guidance: <500 lines, progressive disclosure).
- Targeted characterization pins only (mandatory review sites, reviewer≠author, artifact delivery, loop-back debit, deferral honesty, no executor vocabulary) — not every sentence.
- **WF2-lite lane** (#761 rescoped): `disposable | internal | production` task class; disposable work gets a short lane with its own definition of done.
- #822 folded into the existing version-pin test (names the stale surface, checks changelog tail).
- The **issue throttle** (D179) lands in the workspace manual.

### M2 — THE PANE-HANDOFF CHAIN: now load-bearing, so make it boring
*(Amended 2026-08-05: #800 shipped in PR #912 so item 1 shrinks; item 4 gains #927; quick wins
#916 + #928 and M1 spillover #923 + #899 ride this milestone's tail. Epic #906 is the tracking
surface. The 2026-08-03 text below is otherwise unchanged.)*

Priority order (epic-run depends on this chain per D176):
1. **Launcher robustness** (#731+#800+#835): error text on every `failed_step` + capture-before- cleanup + name preflight; path-equivalent `project_switched` compare; goal-send Enter-nudge recovery (the #700 pattern, extended to the goal).
2. **Meter rework** (D177 spec): never-latch re-resolution, mid-turn directive delivery, 55/75, the rolling log summary (#797's third AC — kept, not dropped), and a code shrink while keeping the two-threshold pane-handoff behavior. **Honest mechanism note:** the T2 "force" is delivered through the prompt-insert channel — authoritative user input that Claude Code queues to the next tool boundary, which is exactly "let the current task finish, then act". It cannot physically seize control, so it is **acknowledgement-tracked**: the meter records the insert, watches for the handoff's own evidence (successor registry line), and re-fires if none appears — never fire-and-forget.
3. **#726** — refuse handoff while background work is in flight (wait-or-decide, overridable), **plus** the issue's two teeth: a mechanical durable-path check (a successor cannot be handed a predecessor-session-scoped `/tmp` path), and abandoned in-flight work is NAMED in the successor prompt so it re-dispatches instead of waiting forever.
4. **Epic-run rework** (D176): children continue via pane-handoff; claim/lease/receipt machinery retired; lightweight stale-body check at child startup; **the #769 child-boundary sweep** (completed child's findings swept across every remaining child's scope/deps, recorded — this is NOT the same as head-revalidation and is kept, per review finding 6); visible both-projects confirmation (the #763 residual, one line).
5. **Trusted goal reader** (#864+#772): one origin-bound reader (LIVE/CLEARED/NEVER_ARMED/ AMBIGUOUS) behind the CLI and both destructive paths.
6. **#806** rescoped: goal cap read from the constant + exact-text display; no auto-arm.

### M2.5 — MINIMUM TELEMETRY TRUTH *(added 2026-08-05, consult-driven — epic #935)*
Before any unattended expansion, records must be trustworthy: **#888** — one transactional
persistence contract (absorbs #588's dropped-on-compaction evidence and #355's blind-append
duplicates): run-records that land exactly once, with the persist-before-merge ordering codified ·
**#363** — per-run usage attribution instead of session-cumulative snapshots (absorbs #660) ·
**#356** — dispatch entries that survive session seams. Rationale (gpt-5.6-sol, adopted): away
mode before durable records produces unattended runs that cannot be reconstructed.

### M3 — TRUST THE NEW MACHINERY *(reordered 2026-08-05; absorbs the old "small tail" — epic #936)*
The retreat's replacement machinery earns trust here. Kept as **linked siblings, not merged** —
different trust boundaries need independent tests (consult finding, adopted):
- **Runner hardening:** #876 (trusted `--brief` for review-artifact) · #894 (exit/signal + OOM evidence on a no-END death) · #365 (contaminated/fabricated-citation returns get named handling) · #893 (Step 8a engagement-evidence rule for runner passes).
- **Review-loop economics:** #889 (refund an unused reopen token on a clean round) · #892 (fuzzy re-litigation flag, never auto-dissolve) · #891 (retire the fail-open absence-based riskLevel default) · #895 (blindness guard: reserved artifact prefix + pre-draft search exclusion).
- **Config + old tail:** #884 + #883 (resolver distinguishes project-absent from field-absent; setup offers diffReviewMode) · #860 (consult-on-exhaustion into the gates) · #808 (WF3's own budget-exhausted close) · #750 (registry append helper) · #759-lite (owner deferral registry).

### M4 — SESSION CONTINUITY & UNSUPERVISED MODES *(re-scoped and moved up 2026-08-05, D185)*
Re-planned mid-#906 after the "single-session means no pane-handoff" confusion recurred: one
cohesive design unifying epic-run, pane-handoff, plugin-refresh, long-run-resume, revalidation
and two declared unsupervised modes — **AWAY** (asks by text, waits min(return, 20 min), then
decides) and **SLEEPING** (consults cross-model, then decides immediately). Full plan (owner +
Fable 5, gpt-5.6-sol consult): `2026-08-05-871-m4-session-continuity-away-mode.md`, hosted at
https://rawgentic-plan-871.vercel.app/. Queue on epic **#871**: #888 (records first) → **#943**
supervision core → #927 → #769 → #726 → #586 → **#944** revalidate hardening. No longer gated on
M2/M2.5 completion — #888 is the sliced records-first minimum; #654's unattended remainder
(keystroke-free restart) lands in #586; decision ticket #931 (native Bash sandboxing) feeds the
away-mode slice. The former M4 gate rationale is preserved by the #888 slice, stated rather than
dropped.

### M5 — PROJECT IDENTITY & CONCURRENCY + WF HYGIENE *(2026-08-05 — epic #937)*
#345 + #346 (one project-scoped keying design; two migration tickets — .wf2-state collisions and
WF14 store routing) · #593 + #594 (concurrent same-project sessions: canonical notes home +
auto-worktree on second bind — grouped siblings) · #372 (WF14 report path vs wal-bind-guard) ·
#364 (doc-only-commits resume state) · #370 + #379 (ambiguity-breaker pair: determinable-finding
exemptions + visible inspection) · #395 (authored-blind pre-push checklist) · #658 (call-site
inventory for class-of-defect fixes) · #890 (consumer-project hook invocation — measure first).

### M6 — TAIL *(2026-08-05 — epic #938)*
#361 (Step-16 assembly cross-checks against session-note ground truth) · #380 (wal-context
internal execution deadline).

### M7 — SKILLS & TOOLING *(2026-08-05 — epic #939)*
#400 (WF17 invoked-vs-should-have-fired auditor — conceptual pair of #928, which ships in M2) ·
#390 (workspace-doctor) · #391 (session-index v2) · #534 (retire epic-run-analysis — unblocked,
#508 shipped) · #536 (deploy-verify skill) · #537 (security-vet skill) · #399 (runner-group
inference) · #350 (CodeGuard rules).

### LATER / watch *(refreshed 2026-08-05)*
#792 (quota preflight — still statusline-based; **no programmatic usage API exists**, verified) ·
#358 + #359 under #360 (WF15/16 codex design workflows — the runner's consult verb makes them
cheaper now) · #738 (parked; trigger: a listed project reactivates) · #568 (Hermes substrate,
rescoped; trigger: a consumer beyond ask-owner/notify-owner) · the three fired-trigger decision
tickets **#931/#932/#933** (native sandboxing → #871, LLM-judged hooks → prose-gate replacement,
sandbox-runtime → codex containment) · PreCompact auto-dump backstop if handoffs ever miss · the
§6 watch list (three items fired 2026-08-05, seven standing).

## 5. Research this plan stands on (so we stop guessing)
- **Anthropic (primary sources):** SKILL.md <500 lines, progressive disclosure, scripts over prose ("the context window is a public good"); *coding is a poor multi-agent fit* — inline endorsed; subagents are for side tasks that would flood the main context (reviews fit exactly).
- **Measured instruction decay:** best frontier models ~68% compliance at 500 rules; perfect compliance collapses by ~80 rules (IFScale; Prompt Design at Scale). WF2 carried ~302.
- **Review economics (Cloudflare, cubic, Greptile):** risk-tiered depth, deterministic pre-filters (not LLM severity judges), confidence fields in structured output, truncation retry, generated- noise stripping. Greptile: prompting against nits failed; embedding-filters worked — don't build an LLM severity judge.
- **Hooks over prose:** PreToolUse deny survives even permission-skip mode; "if violating it once is an incident, make it a hook; otherwise it's a skill line"; hook-fed state machine prior art (Nick Tune) is the template for #856's guard direction; practitioners report cutting standing instructions ~1/3 after moving enforcement into hooks.
- **codex exec:** `--output-schema` + `-o` gives machine-checkable review artifacts; repo-aware `codex exec review --base|--commit|--uncommitted` exists in the installed 0.146.0.
- **Subagent silent-death rates 14–30%** (claude-code#47936): the artifact gate + proof-token pattern is the cheap mitigation until the SDK surfaces termination status.

## 6. Future watch list (not now; revisit on trigger) — *statuses verified 2026-08-05*
1. Claude Code native Bash sandboxing — **FIRED: shipped** (`/sandbox`, Seatbelt/bubblewrap) → decision ticket **#931**
2. anthropic-experimental/sandbox-runtime — **FIRED: shipped** (npm, wraps arbitrary CLIs) → decision ticket **#933**
3. Async-subagent reliability fix (#47936) — still open upstream; artifact-gate machinery stays
4. Agent teams — shipped experimental (v2.1.178+, env-flag-gated); trigger unchanged: only if parallel implementation returns
5. Background agents / agent-view — shipped (`claude agents`, v2.1.139+); trigger unchanged: if epic-run outgrows panes
6. prompt/agent-type hooks — **FIRED: shipped** (type:prompt / type:agent, official hooks docs) → decision ticket **#932**
7. Codex CLI hooks — partial (UserPromptSubmit exists; PostToolUse "soon")
8. codex --output-schema — mature (incl. `exec resume --output-schema` since 0.132.0); adopted
9. Native usage API — still absent (only `/usage` + OTel export); #792 stays statusline-based
10. Plugin marketplace — shipped; trigger unchanged: only if rawgentic is ever shared
11. *(added 2026-08-05)* Anthropic skill-evals harness — **shipped** (skill-creator 2.0: evals.json, benchmark, trigger tuning); adopted via #928 in M2

## 7. What we deliberately do NOT do
- No wholesale revert of the last 4 days — good fixes stay; dead weight deletes forward.
- No new enforcement state machines. One runner + `plan_lib review-reopen` own review-loop control.
- No per-phase token attribution (#777/#815 closed-parked) until a producer boundary exists again.
- No generic docs-only review skip — markdown is executable behavior here.
- protectionLevel stays `sandbox`; no new pattern guards; secrets scanning stays.
- Historical receipts, measurements and planning docs are archival — M0 does not rewrite history.

## 8. Execution notes
- **M0 runs in plain supervised sessions, NOT through WF2 (D180)** — WF2 is broken as written, and a session executes the installed cache's prose while editing the repo's, a self-reference trap. Hand-applied discipline is mandatory: TDD for behavior changes; full-suite + both pylint gates against the recorded baseline (7031 passed / 24 skipped at `98547d41`); secrets scan; codex available for consults; an adversarial cross-model review of every PR diff (through the M0a runner once it exists — the retreat dogfoods its own replacement); proper PR mechanics. **Rawgentic returns at M1**, which doubles as the test that the retreat worked.
- **Comparison telemetry (D181):** plain-session runs write per-PR run-records into the same append-only store (`docs/measurements/run_records.jsonl`) as `workflow: "plain-session"`, `architecture: "inline"` — wall-clock, usage, review findings, tests, LOC — so cost / quality / time compares directly against the 32 executor/legacy records since 2026-07-28. One store, one query.
- Every milestone item ships as PRs from branches off fresh `origin/main`; owner merges (no standing auto-merge grant exists after this session).
- **Doc publishing goes through the `/design-doc-publish` skill** (its `publish_doc.py` owns render + Vercel deploy + verification in one command; `--type` picks the template). Sessions do not hand-invoke the raw render launcher. M0b's prose rewrite points WF2/WF3's doc-publish steps at the skill's command the same way (owner decision 2026-08-03, this session — this document is itself published through it).
- M0d's cutover: finish/abandon in-flight work → merge → `claude plugin remove/install` → fresh session. Old sessions may still hold cached executor-era skills; don't reinstall mid-session.
- Issue mechanics on owner approval of this roadmap: closing comments citing D174–D179 on the 28 closures; 3 new combined-work issues + the epic-run rework issue + M0 umbrella issue; epic #756 gets a final honest summary comment and closes in favor of this roadmap's milestone tracking.

## 9. Review provenance
gpt-5.6-sol consulted on the draft (7 sections, repo-verified) and then adversarially reviewed the
finished plan (9 findings: 6 High, 3 Medium — all applied above; the two reports are in this
session's records and the local `docs/reviews/` sink). Notable applied corrections: M0b now carries
the run-record relaxation and prose-pin rewrites it invalidates; M0c keeps capability shims until
M0d; the runner gained the reopen-token choke point (#855) and pinned reviewer identity; the
consult verb + WF5/WF13 cutover moved into M0; #769 was un-closed and folded into the epic-run
rework; #795/#797 residual claims were made honest.

## 10. M0 retrospective (written 2026-08-04, after the M0d merge — owner-requested)

M0 shipped whole: four PRs (**#867** runner → **#868** cutover → **#870** config → **#872**
deletion) plus the telemetry-persist PR **#869**, all merged, all with green test+lint CI and
a cross-model adversarial verdict recorded. Wall clock **19:42Z → 02:39Z (~6h57m)** across
four sessions; total session cost **≈ $390** (session-cumulative: $94.26 + $96.17 + $128.78 +
$71.11 at m0d PR-open). Suite: **7031/24 → 4659/0** — 2,385 executor-era tests left with
their subjects; 33 guard/rewrite tests arrived across the four PRs. The five run-records are
in `docs/measurements/run_records.jsonl` (`workflow: "plain-session"`).

### What went to plan

- The four-PR decomposition held exactly as written — no scope moved between PRs, and each merge left main releasable (WF2 was runnable again the day M0b merged, as promised).
- The runner dogfood worked: M0b/M0c/M0d reviews all went through `review_runner.py`, one dispatch each, no retries. Its own path containment refused two /tmp dispatches live in M0c — the M0a hardening catching the M0c orchestrator.
- D183 (merge-as-created) held the whole run without a single CI red on merge.

### Discoveries that were NOT planned for

1. **The adversarial reviewer caught a self-inflicted 606-line README duplication.** M0d's docs-contraction commit pasted a whole re-rendered README span (Quick-Start-steps → Testing → a second marketplace section) instead of just the retirement note. The next session's editor MISREAD the doubled sections as pre-existing ("patterns found 2x") and worked around them. Only the cross-model review named it. Lesson: a 618-insertion diff for a "10-line note" commit should have failed a size sanity check at commit time.
2. **The handoff log drifted from the code.** Entry 1 said `assert_review_coverage` DIES in M0b; the actual M0b implementation kept it (correctly — Step 8a/9 prose still route coverage through it). The M0d diagram deltas were written from the current prose, re-verified per station, not from the notes. Lesson: handoff entries are plans, not records — re-verify before acting (the workspace rule existed; it earned its keep).
3. **gitleaks range-scanning bites retirement prose.** A phrase in the retirement notes — a gate-related keyword followed by a slash-joined protocol name — matched the generic-api-key rule — and because the pre-PR scan covers `origin/main..HEAD` (every commit), rewording the current tree could not clear it, and the `.gitleaksignore` comment that quoted the phrase then flagged itself. Three fingerprint pins with justification. Lesson: retirement notes are adversarial inputs to secret scanners; scan early, not at PR time.
4. **`review-artifact` has no trusted-brief channel.** The M0d diff (3.1MB) exceeded any model context, so the review ran on a composed `git diff -D` artifact — and the brief had to ride inside the untrusted artifact because only `review-code` takes `--brief`. The reviewer itself flagged that as an injection-shaped hole. Candidate runner follow-up (not filed — D179, owner decides).
5. **The smoke test passed for the wrong reason.** Hooks import siblings bare (`from atomic_write_lib import …`), and the new import-smoke subprocess only passed because pytest's environment leaked a suitable path. Confirmed by running it under `env -i`: ModuleNotFoundError. Fixed to be self-sufficient and to run under a clean env.

### Out-of-plan work that had to happen

- **D184 (owner ruling, mid-M0d):** `RAWGENTIC_HEADLESS` survives as a bare unattended-session signal in exactly three reads; epic **#871** (away mode) was filed from the owner's five ACs. The retirement tripwire deliberately excludes the bare token and now pins the exact surviving read set in both directions.
- **Keep-decisions the roadmap didn't enumerate:** `model_routing_lib.py` (dead-ish, not on the deletion list — M1/backlog) and the plan_lib ceremony helpers + unit tests (the M0b allowlist said "M0d-pending", the roadmap's deletion list never named them; kept under the owner's "don't delete anything good" directive — backlog decides).
- **telemetryAlerts** was removed in M0c though the kickoff's trap-list named only 2i/2k — roadmap §4 M0c named it explicitly; the kickoff list was traps, not scope.

### Gotchas for M1

- The plain-session cost accounting is **session-cumulative snapshots** (#363), not per-PR deltas — compare at effort level (M0 ≈ $390 for 5 merged PRs) against the executor cohort's per-issue records (32 records since 2026-07-28: median $191.23, mean $241.10 per issue), not line-by-line.
- The post-merge **plugin reinstall is still owner-owned** (CLAUDE.md §7): sessions keep loading the cached 3.122.0 until it happens; the switch-bind staleness nudge about model routing is expected until then.
- `docs/reviews/` is gitignored by design — the M0 review verdicts live in the PR bodies; the raw result JSONs exist only on this host.

## 11. M1 shipped + the 2026-08-05 backlog rationalization (written 2026-08-05)

**M1 shipped whole** (epic #875, closed): #856 (CI byte ceilings + `steps.md` split into
step-local files, PR #874's successor), #761 (task-class field through drafting and review
gates — the lite-lane half split to #923 by owner decision after pass 4 of its design gate
returned 8 new High findings), #822 (version surfaces + changelog tail folded into the pin
test). M1 doubled as the test that the retreat worked: WF2 ran real issues again (#879, #880
run-records are in the store).

**The backlog rationalization** (owner-approved this session, consult: gpt-5.6-sol via the
runner's own `consult` verb — the M0a machinery reviewing its own backlog):

- **46 issues closed** with citing comments: the roadmap §3 closures that were never executed (D174/D178 leftovers), work M0a had already delivered (#855, #793, #357), subjects the retreat deleted (the HD2 herdr-executor epic #621 + children, bake-off #484, seats #549, build-seat issues #666/#671, driver-bench #624/#681, #371, #661), five superseded tracking epics (#590/#595/#596/#597/#598) plus #599, #626, #685, #722 — and **#756 itself**, closed with its final summary as §8 directed.
- **#670 corrected on review:** first closed as fixed-in-successor, then the cross-model diff review prompted a sharper retest — inline links and `---` rules ARE fixed in the claude-skills engine, but the damaging defect (wrapped list items splitting the `<li>`; numbered lists too) **persists**. Record corrected, issue reopened and **transferred to claude-skills**, where its code lives. This roadmap's own bullets were unwrapped to render correctly until that fix lands.
- **3 duplicates folded:** #660→#363, #588+#355→#888.
- **3 decision tickets filed** (owner-approved under the D179 throttle): #931 native sandboxing, #932 LLM-judged hooks, #933 sandbox-runtime — the three §6 triggers that fired.
- **Open issues: 116 → 72.** Epic #906 (M2) and this roadmap are the only tracking surfaces.
- **Milestone epics minted** (owner-approved, after PR #934 merged): **#935** (M2.5), **#936** (M3), **#937** (M5), **#938** (M6), **#939** (M7) — checkbox task lists referencing the existing issues, same shape as #906, nothing duplicated. With #906 (M2) and #871 (M4), every milestone now has a GitHub tracking surface; the LATER shelf deliberately has none.
- **Ordering changes** (consult-driven, adopted): #928 to the front (Anthropic's skill-evals harness shipped and our nine `evals.json` files already match its schema); a minimum telemetry-truth layer (M2.5) gates away mode; away mode runs as a bounded slice (M4), not a wholesale deferral; sibling pairs stay linked-but-separate rather than merged.

Full evidence, per-issue: `2026-08-05-backlog-rationalization-post-m1.md`
(hosted: https://rawgentic-analysis-backlog-post-m1.vercel.app).

## 12. The 2026-08-07 reassessment (written 2026-08-07, against main `714fdeb7`)

Every open issue was re-checked against the tree, not against this document's memory of it.
**68 open, all tracked by a milestone, none orphaned.** The distribution is unchanged in shape:
M1 1 · M2 16 · M2.5 3 · M3 15 · M5 12 · M6 3 · M7 9 · LATER 9.

That is the good news. The rest of this section is what the check got wrong.

### 12.1 Three corrections — claims this roadmap made that are not true

**C1 — "the claim/lease/receipt machinery retires" (D176, M2 item 4, M4) did not happen.**
`generation_fence` and `claim_lease` are gone, but `rebuild_receipt`, `pending_disposition` and
`jam_matrix` are all still live in `hooks/driver_lib.py` and `hooks/launcher_lib.py` — 71 and 54
`receipt` references respectively. **Consequence, and it is the useful half:** #846, #849, #850 and
#851 are **still valid**, not dead. This document has read them as retired-by-D176 since 2026-08-03;
they describe live code. Either D176's retirement gets finished, or those four get built. What is
not defensible is leaving them classified as already-handled.

**C2 — #792's issue never got the rescope this roadmap gave it.** §3 rescoped it to "a small
fail-open quota preflight" on 2026-08-03. The issue is still titled *"feat(**executor**): 5-hour-window
dispatch guard — refuse **Claude-lane dispatches** at 90% utilization"*, and the executor was deleted
in M0d. Anyone reading the issue reads a subject that no longer exists. The rescope lives only here.

**C3 — the supervision line kept shipping past M4, and this roadmap does not mention it.** M4 closed
2026-08-06 at v3.137.0. Since then **#963** (the supervised-merge broker — the first live caller of
the #871 authority core) and **#976** (PreToolUse enforcement of it) both shipped and closed. Neither
appears anywhere above. A reader of this document would conclude supervision work stopped at M4.

### 12.2 Verified still-valid, with today's evidence

| Issue | Checked | Verdict |
|---|---|---|
| #797 | no 55/75 threshold constants in `hooks/context_meter.py` | **valid** — not started |
| #380 | no `timeout`/`deadline` in `hooks/wal-context` | **valid** — not started |
| #361 | `hooks/work_summary.py` has **zero** references to `session_notes` | **valid**, and newly urgent — see below |
| #363 | the #976 run-record carries `wall_clock_s: null` and `input_tokens: 30301664` (session-cumulative) | **valid**, with fresh proof |
| #899 | `skills/implement-feature/SKILL.md` is **6442 words** against the 5,000 guideline | **valid** — 29% over |

**#361 deserves promotion.** It asks for Step-16 cross-checks against session-note ground truth. The
#976 run shipped a run-record whose `timing` was `absent` because no session-note step markers were
written — and nothing in Step 16 noticed except an advisory line. That is precisely the defect #361
describes, observed in production, one day after M2.5 was written to make records trustworthy. It is
currently in **M6 (TAIL)**. On this evidence it belongs in **M2.5**, next to #363.

### 12.3 Two issues that moved

- **#534** (retire `epic-run-analysis`) is **unblocked and actionable now**: `skills/epic-post-mortem`
  exists, so #508 shipped, and the superseded workspace skill is still installed at
  `.claude/skills/epic-run-analysis`. It is a deletion — the cheapest item on the board.
- **#932** (LLM-judged hooks) is **no longer a bare decision ticket**. Its design landed 2026-08-06 as
  `docs/planning/2026-08-06-932-mechanical-step-gates.md` (PR #980, commit `a81ee211`). That design
  independently reached the same conclusion as the #976 work — `step_state.py` is a fail-open
  now-pointer and can never be a gate, so the missing primitive is an append-only completion ledger
  gated on an evidence artifact. It should be reclassified from LATER to a buildable M3 candidate.

### 12.4 Found during builds, deliberately NOT filed (D179 throttle — your call)

1. **~3034 orphaned WAL `INTENT` entries**, 19% of all 16037 operations. Cause fixed 2026-08-07 in
   PR #982 (rotation rewrote the live file with no lock, destroying concurrent sessions' appends).
   The **existing** orphans are untouched and stay on disk by design, so the session-start notice
   keeps announcing them. A cleanup pass is a separate decision.
2. **Per-step timing is only as good as the bootstrap.** PR #981 made the branch-cut
   `step_state.py write` mandatory so a run can record timing at all. It does not make the WF2
   `— DONE` markers happen; those are still prose, and #976 skipped them. #361 is the durable fix.

### 12.5 What this changes about what runs next

Nothing about M2's position — epic #906 is still PAUSED at 3/16 and still the next milestone to
resume, on your start. The recommendations this pass produces are:

1. **Move #361 from M6 to M2.5.** Records-trustworthiness now has a production failure behind it.
2. **Re-title #792 or close it.** Its stated subject was deleted nine days ago.
3. **Reclassify #846/#849/#850/#851** — either finish D176's retirement or accept them as live work.
   They are currently mis-filed as handled.
4. **Take #534 whenever a session wants a five-minute win.** It is a pure deletion, unblocked.
