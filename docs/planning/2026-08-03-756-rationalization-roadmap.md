# Rawgentic rationalization roadmap — get unbroken, then get simple

**Date:** 2026-08-03 · **Authors:** owner + Fable 5, with gpt-5.6-sol as consultant (xhigh, repo-verified)
**Supersedes:** epic #756's queue order and the 2026-08-01 "keep the executor" ruling.
**Decisions:** D174–D179 in `claude_docs/decisions/rawgentic.jsonl` (workspace root). Each carries its undo.

```stats
48 | issues rationalized
28 | issues closed | accent
~33k | code lines to delete
6 | owner rulings (D174–D179)
34→15 | WF2 refusal gates
```

```callout
ok | Updated 2026-08-05 — M0 and M1 are SHIPPED; M2 is the active milestone
M0 (the retreat, PRs #867/#868/#870/#872) and M1 (#856/#761/#822, epic #875) are merged. The
2026-08-05 backlog rationalization closed 47 issues, folded 3 duplicates, filed 3 decision
tickets, and reordered everything below M2 by impact — full evidence in the companion doc
`2026-08-05-backlog-rationalization-post-m1.md` (§11 has the summary). The original 2026-08-03
text below M0/M1 is kept as history; M2-and-beyond reflects the new ordering.
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
M2 — THE PANE-HANDOFF CHAIN | epic #906 · active | crit
  QW | Quick wins: #916 stale-page redeploy · #928 wire the 9 evals.json into the shipped skill-creator harness | note
  1 | Launcher robustness: #731 + #835 — #800 already shipped (PR #912) | crit
  2 | Meter rework (D177): #797 55/75 + rolling summary · #729 residual (ack-tracking) · #734 blind-start | note
  3 | #726 in-flight-work gate + durable-path check + abandoned work named to the successor | note
  4 | Epic-run rework (D176): #927 fresh-session default · #769 boundary sweep · close-or-fold owed: #845 #846 #848 #849 #850 #851 | note
  5 | Trusted goal reader: #864 + #772 + #878 | note
  6 | #806 goal cap from the constant + exact-text display; no auto-arm | note
  SP | M1 spillover: #923 WF2-lite lane · #899 word budget | note
M2.5 — MINIMUM TELEMETRY TRUTH | gates away mode | warn
  #888 | One transactional persistence contract (absorbs #588 + #355) | note
  #363 | Per-run usage attribution (absorbs #660) | note
  #356 | Dispatch entries that survive session seams | note
M3 — TRUST THE NEW MACHINERY | runner · review loop · config | warn
  RH | Runner hardening (siblings, not merged): #876 trusted --brief · #894 death/OOM evidence · #365 contaminated returns · #893 engagement evidence | note
  RL | Review loop: #889 clean-round token refund · #892 re-litigation flag · #891 retire fail-open riskLevel default · #895 blindness guard | note
  CFG | #884 + #883 config pair · roadmap tail: #860 consult-on-exhaustion · #808 WF3 close · #750 registry helper · #759 deferral registry | note
M4 — AWAY MODE, BOUNDED | epic #871 · gated on M2 + M2.5 | warn
  #871 | A narrow first slice: explicit duration, cancel, recovery limits | note
  #586 | Resume launcher that survives /clear | note
M5 — IDENTITY & CONCURRENCY + WF HYGIENE | grouped pairs | note
  P1 | #345 + #346 one keying design, two migrations · #593 + #594 concurrent same-project sessions | note
  P2 | #372 WF14-vs-guard · #364 doc-only resume state · #370 + #379 ambiguity-breaker pair | note
  P3 | #395 authored-blind checklist · #658 call-site inventory · #890 consumer-project hook invocation | note
M6 — TAIL | small, independent | note
  #361 | Step-16 assembly cross-checks | note
  #380 | wal-context internal deadline | note
M7 — SKILLS & TOOLING | convenience tier | note
  T1 | #400 usage auditor · #390 workspace-doctor · #391 session-index v2 | note
  T2 | #534 retire epic-run-analysis (unblocked, #508 shipped) · #536 deploy-verify · #537 security-vet · #399 group inference · #350 CodeGuard | note
LATER — with reopen triggers, not vibes | watch list §6 refreshed | note
  #792 | Quota preflight — still statusline-based; no usage API exists (verified 2026-08-05) | note
  WF | #358 + #359 (#360) WF15/16 codex design workflows | note
  DT | Decision tickets from fired triggers: #931 native sandboxing · #932 LLM-judged hooks · #933 sandbox-runtime | note
  PK | #738 parked (trigger: project reactivates) · #568 Hermes substrate (trigger: a consumer beyond ask/notify-owner) | note
```

```legend
crit | broken or flaky today — fix first
warn | protects size or reliability
ok | pure deletion win
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

1. **WF2's implementation path points at a machine that has never run.** Every implementation
   dispatch routes through the executor build seat (`steps.md:997`) — a seat with zero real-run
   history, that only codex can drive, and codex is weekly-quota-capped.
2. **All three mandatory review gates fail as written.** The dispatch command the prose gives
   omits a flag the code requires (`--author-provider`, #863) — exit 4, every time.
3. **The workflow prose outgrew what any model can follow.** ~75k tokens carrying ~302 hard
   obligations. Measured research puts the compliance cliff near **80 simultaneous rules**
   (IFScale, arXiv 2507.11538). The #840 run proved it: a session running WF2 bypassed three of
   WF2's own controls without noticing (#855).

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
- Measure the post-retreat corpus, then pin **CI byte ceilings** (total + per-file, glob-exact so
  a new unbudgeted file can't evade it) at actual + modest headroom (#856).
- `steps.md` → step-local files, loaded one step at a time (SKILL.md stays a short index —
  Anthropic's own guidance: <500 lines, progressive disclosure).
- Targeted characterization pins only (mandatory review sites, reviewer≠author, artifact delivery,
  loop-back debit, deferral honesty, no executor vocabulary) — not every sentence.
- **WF2-lite lane** (#761 rescoped): `disposable | internal | production` task class; disposable
  work gets a short lane with its own definition of done.
- #822 folded into the existing version-pin test (names the stale surface, checks changelog tail).
- The **issue throttle** (D179) lands in the workspace manual.

### M2 — THE PANE-HANDOFF CHAIN: now load-bearing, so make it boring
*(Amended 2026-08-05: #800 shipped in PR #912 so item 1 shrinks; item 4 gains #927; quick wins
#916 + #928 and M1 spillover #923 + #899 ride this milestone's tail. Epic #906 is the tracking
surface. The 2026-08-03 text below is otherwise unchanged.)*

Priority order (epic-run depends on this chain per D176):
1. **Launcher robustness** (#731+#800+#835): error text on every `failed_step` + capture-before-
   cleanup + name preflight; path-equivalent `project_switched` compare; goal-send Enter-nudge
   recovery (the #700 pattern, extended to the goal).
2. **Meter rework** (D177 spec): never-latch re-resolution, mid-turn directive delivery, 55/75,
   the rolling log summary (#797's third AC — kept, not dropped), and a code shrink while keeping
   the two-threshold pane-handoff behavior. **Honest mechanism note:** the T2 "force" is delivered
   through the prompt-insert channel — authoritative user input that Claude Code queues to the
   next tool boundary, which is exactly "let the current task finish, then act". It cannot
   physically seize control, so it is **acknowledgement-tracked**: the meter records the insert,
   watches for the handoff's own evidence (successor registry line), and re-fires if none appears
   — never fire-and-forget.
3. **#726** — refuse handoff while background work is in flight (wait-or-decide, overridable),
   **plus** the issue's two teeth: a mechanical durable-path check (a successor cannot be handed a
   predecessor-session-scoped `/tmp` path), and abandoned in-flight work is NAMED in the successor
   prompt so it re-dispatches instead of waiting forever.
4. **Epic-run rework** (D176): children continue via pane-handoff; claim/lease/receipt machinery
   retired; lightweight stale-body check at child startup; **the #769 child-boundary sweep**
   (completed child's findings swept across every remaining child's scope/deps, recorded — this is
   NOT the same as head-revalidation and is kept, per review finding 6); visible both-projects
   confirmation (the #763 residual, one line).
5. **Trusted goal reader** (#864+#772): one origin-bound reader (LIVE/CLEARED/NEVER_ARMED/
   AMBIGUOUS) behind the CLI and both destructive paths.
6. **#806** rescoped: goal cap read from the constant + exact-text display; no auto-arm.

### M2.5 — MINIMUM TELEMETRY TRUTH *(added 2026-08-05, consult-driven)*
Before any unattended expansion, records must be trustworthy: **#888** — one transactional
persistence contract (absorbs #588's dropped-on-compaction evidence and #355's blind-append
duplicates): run-records that land exactly once, with the persist-before-merge ordering codified ·
**#363** — per-run usage attribution instead of session-cumulative snapshots (absorbs #660) ·
**#356** — dispatch entries that survive session seams. Rationale (gpt-5.6-sol, adopted): away
mode before durable records produces unattended runs that cannot be reconstructed.

### M3 — TRUST THE NEW MACHINERY *(reordered 2026-08-05; absorbs the old "small tail")*
The retreat's replacement machinery earns trust here. Kept as **linked siblings, not merged** —
different trust boundaries need independent tests (consult finding, adopted):
- **Runner hardening:** #876 (trusted `--brief` for review-artifact) · #894 (exit/signal + OOM
  evidence on a no-END death) · #365 (contaminated/fabricated-citation returns get named handling)
  · #893 (Step 8a engagement-evidence rule for runner passes).
- **Review-loop economics:** #889 (refund an unused reopen token on a clean round) · #892 (fuzzy
  re-litigation flag, never auto-dissolve) · #891 (retire the fail-open absence-based riskLevel
  default) · #895 (blindness guard: reserved artifact prefix + pre-draft search exclusion).
- **Config + old tail:** #884 + #883 (resolver distinguishes project-absent from field-absent;
  setup offers diffReviewMode) · #860 (consult-on-exhaustion into the gates) · #808 (WF3's own
  budget-exhausted close) · #750 (registry append helper) · #759-lite (owner deferral registry).

### M4 — AWAY MODE, AS A BOUNDED SLICE *(added 2026-08-05)*
**#871** (the epic — replace the retired headless stopgaps with a proper "nobody is watching"
mode; first slice with explicit duration, cancellation, artifact and recovery limits) + **#586**
(durable resume that survives /clear). Gated on M2 reliability + M2.5 records. #654's unattended
remainder (keystroke-free restart) lands here. Decision ticket #931 (native Bash sandboxing)
feeds this epic's design.

### M5 — PROJECT IDENTITY & CONCURRENCY + WF HYGIENE *(2026-08-05)*
#345 + #346 (one project-scoped keying design; two migration tickets — .wf2-state collisions and
WF14 store routing) · #593 + #594 (concurrent same-project sessions: canonical notes home +
auto-worktree on second bind — grouped siblings) · #372 (WF14 report path vs wal-bind-guard) ·
#364 (doc-only-commits resume state) · #370 + #379 (ambiguity-breaker pair: determinable-finding
exemptions + visible inspection) · #395 (authored-blind pre-push checklist) · #658 (call-site
inventory for class-of-defect fixes) · #890 (consumer-project hook invocation — measure first).

### M6 — TAIL *(2026-08-05)*
#361 (Step-16 assembly cross-checks against session-note ground truth) · #380 (wal-context
internal execution deadline).

### M7 — SKILLS & TOOLING *(2026-08-05)*
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
- **Anthropic (primary sources):** SKILL.md <500 lines, progressive disclosure, scripts over prose
  ("the context window is a public good"); *coding is a poor multi-agent fit* — inline endorsed;
  subagents are for side tasks that would flood the main context (reviews fit exactly).
- **Measured instruction decay:** best frontier models ~68% compliance at 500 rules; perfect
  compliance collapses by ~80 rules (IFScale; Prompt Design at Scale). WF2 carried ~302.
- **Review economics (Cloudflare, cubic, Greptile):** risk-tiered depth, deterministic pre-filters
  (not LLM severity judges), confidence fields in structured output, truncation retry, generated-
  noise stripping. Greptile: prompting against nits failed; embedding-filters worked — don't build
  an LLM severity judge.
- **Hooks over prose:** PreToolUse deny survives even permission-skip mode; "if violating it once
  is an incident, make it a hook; otherwise it's a skill line"; hook-fed state machine prior art
  (Nick Tune) is the template for #856's guard direction; practitioners report cutting standing
  instructions ~1/3 after moving enforcement into hooks.
- **codex exec:** `--output-schema` + `-o` gives machine-checkable review artifacts; repo-aware
  `codex exec review --base|--commit|--uncommitted` exists in the installed 0.146.0.
- **Subagent silent-death rates 14–30%** (claude-code#47936): the artifact gate + proof-token
  pattern is the cheap mitigation until the SDK surfaces termination status.

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
- **M0 runs in plain supervised sessions, NOT through WF2 (D180)** — WF2 is broken as written, and
  a session executes the installed cache's prose while editing the repo's, a self-reference trap.
  Hand-applied discipline is mandatory: TDD for behavior changes; full-suite + both pylint gates
  against the recorded baseline (7031 passed / 24 skipped at `98547d41`); secrets scan; codex
  available for consults; an adversarial cross-model review of every PR diff (through the M0a
  runner once it exists — the retreat dogfoods its own replacement); proper PR mechanics.
  **Rawgentic returns at M1**, which doubles as the test that the retreat worked.
- **Comparison telemetry (D181):** plain-session runs write per-PR run-records into the same
  append-only store (`docs/measurements/run_records.jsonl`) as `workflow: "plain-session"`,
  `architecture: "inline"` — wall-clock, usage, review findings, tests, LOC — so
  cost / quality / time compares directly against the 32 executor/legacy records since
  2026-07-28. One store, one query.
- Every milestone item ships as PRs from branches off fresh `origin/main`; owner merges (no
  standing auto-merge grant exists after this session).
- **Doc publishing goes through the `/design-doc-publish` skill** (its `publish_doc.py` owns
  render + Vercel deploy + verification in one command; `--type` picks the template). Sessions do
  not hand-invoke the raw render launcher. M0b's prose rewrite points WF2/WF3's doc-publish steps
  at the skill's command the same way (owner decision 2026-08-03, this session — this document is
  itself published through it).
- M0d's cutover: finish/abandon in-flight work → merge → `claude plugin remove/install` → fresh
  session. Old sessions may still hold cached executor-era skills; don't reinstall mid-session.
- Issue mechanics on owner approval of this roadmap: closing comments citing D174–D179 on the 28
  closures; 3 new combined-work issues + the epic-run rework issue + M0 umbrella issue; epic #756
  gets a final honest summary comment and closes in favor of this roadmap's milestone tracking.

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

- The four-PR decomposition held exactly as written — no scope moved between PRs, and each
  merge left main releasable (WF2 was runnable again the day M0b merged, as promised).
- The runner dogfood worked: M0b/M0c/M0d reviews all went through `review_runner.py`,
  one dispatch each, no retries. Its own path containment refused two /tmp dispatches
  live in M0c — the M0a hardening catching the M0c orchestrator.
- D183 (merge-as-created) held the whole run without a single CI red on merge.

### Discoveries that were NOT planned for

1. **The adversarial reviewer caught a self-inflicted 606-line README duplication.** M0d's
   docs-contraction commit pasted a whole re-rendered README span (Quick-Start-steps →
   Testing → a second marketplace section) instead of just the retirement note. The next
   session's editor MISREAD the doubled sections as pre-existing ("patterns found 2x") and
   worked around them. Only the cross-model review named it. Lesson: a 618-insertion diff
   for a "10-line note" commit should have failed a size sanity check at commit time.
2. **The handoff log drifted from the code.** Entry 1 said `assert_review_coverage` DIES in
   M0b; the actual M0b implementation kept it (correctly — Step 8a/9 prose still route
   coverage through it). The M0d diagram deltas were written from the current prose,
   re-verified per station, not from the notes. Lesson: handoff entries are plans, not
   records — re-verify before acting (the workspace rule existed; it earned its keep).
3. **gitleaks range-scanning bites retirement prose.** A phrase in the retirement
   notes — a gate-related keyword followed by a slash-joined protocol name — matched
   the generic-api-key rule — and because the
   pre-PR scan covers `origin/main..HEAD` (every commit), rewording the current tree could
   not clear it, and the `.gitleaksignore` comment that quoted the phrase then flagged
   itself. Three fingerprint pins with justification. Lesson: retirement notes are
   adversarial inputs to secret scanners; scan early, not at PR time.
4. **`review-artifact` has no trusted-brief channel.** The M0d diff (3.1MB) exceeded any
   model context, so the review ran on a composed `git diff -D` artifact — and the brief had
   to ride inside the untrusted artifact because only `review-code` takes `--brief`. The
   reviewer itself flagged that as an injection-shaped hole. Candidate runner follow-up
   (not filed — D179, owner decides).
5. **The smoke test passed for the wrong reason.** Hooks import siblings bare
   (`from atomic_write_lib import …`), and the new import-smoke subprocess only passed
   because pytest's environment leaked a suitable path. Confirmed by running it under
   `env -i`: ModuleNotFoundError. Fixed to be self-sufficient and to run under a clean env.

### Out-of-plan work that had to happen

- **D184 (owner ruling, mid-M0d):** `RAWGENTIC_HEADLESS` survives as a bare
  unattended-session signal in exactly three reads; epic **#871** (away mode) was filed from
  the owner's five ACs. The retirement tripwire deliberately excludes the bare token and now
  pins the exact surviving read set in both directions.
- **Keep-decisions the roadmap didn't enumerate:** `model_routing_lib.py` (dead-ish, not on
  the deletion list — M1/backlog) and the plan_lib ceremony helpers + unit tests (the M0b
  allowlist said "M0d-pending", the roadmap's deletion list never named them; kept under the
  owner's "don't delete anything good" directive — backlog decides).
- **telemetryAlerts** was removed in M0c though the kickoff's trap-list named only 2i/2k —
  roadmap §4 M0c named it explicitly; the kickoff list was traps, not scope.

### Gotchas for M1

- The plain-session cost accounting is **session-cumulative snapshots** (#363), not per-PR
  deltas — compare at effort level (M0 ≈ $390 for 5 merged PRs) against the executor
  cohort's per-issue records (32 records since 2026-07-28: median $191.23, mean $241.10 per
  issue), not line-by-line.
- The post-merge **plugin reinstall is still owner-owned** (CLAUDE.md §7): sessions keep
  loading the cached 3.122.0 until it happens; the switch-bind staleness nudge about model
  routing is expected until then.
- `docs/reviews/` is gitignored by design — the M0 review verdicts live in the PR bodies;
  the raw result JSONs exist only on this host.

## 11. M1 shipped + the 2026-08-05 backlog rationalization (written 2026-08-05)

**M1 shipped whole** (epic #875, closed): #856 (CI byte ceilings + `steps.md` split into
step-local files, PR #874's successor), #761 (task-class field through drafting and review
gates — the lite-lane half split to #923 by owner decision after pass 4 of its design gate
returned 8 new High findings), #822 (version surfaces + changelog tail folded into the pin
test). M1 doubled as the test that the retreat worked: WF2 ran real issues again (#879, #880
run-records are in the store).

**The backlog rationalization** (owner-approved this session, consult: gpt-5.6-sol via the
runner's own `consult` verb — the M0a machinery reviewing its own backlog):

- **47 issues closed** with citing comments: the roadmap §3 closures that were never executed
  (D174/D178 leftovers), work M0a had already delivered (#855, #793, #357), subjects the retreat
  deleted (the HD2 herdr-executor epic #621 + children, bake-off #484, seats #549, build-seat
  issues #666/#671, driver-bench #624/#681, #371, #661), five superseded tracking epics
  (#590/#595/#596/#597/#598) plus #599, #626, #685, #722 — and **#756 itself**, closed with its
  final summary as §8 directed. #670 verified fixed in claude-skills#16 by probe before closing.
- **3 duplicates folded:** #660→#363, #588+#355→#888.
- **3 decision tickets filed** (owner-approved under the D179 throttle): #931 native sandboxing,
  #932 LLM-judged hooks, #933 sandbox-runtime — the three §6 triggers that fired.
- **Open issues: 116 → 72.** Epic #906 (M2) and this roadmap are the only tracking surfaces.
- **Ordering changes** (consult-driven, adopted): #928 to the front (Anthropic's skill-evals
  harness shipped and our nine `evals.json` files already match its schema); a minimum
  telemetry-truth layer (M2.5) gates away mode; away mode runs as a bounded slice (M4), not a
  wholesale deferral; sibling pairs stay linked-but-separate rather than merged.

Full evidence, per-issue: `2026-08-05-backlog-rationalization-post-m1.md`
(hosted: https://rawgentic-analysis-backlog-post-m1.vercel.app).
