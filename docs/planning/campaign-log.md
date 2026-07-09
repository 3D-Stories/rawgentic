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
