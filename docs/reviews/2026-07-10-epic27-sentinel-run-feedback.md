# WF14 batch run-feedback — epic 3D-Stories/sentinel#27 (9 WF2 children)

**Date:** 2026-07-10 · **Rubric:** v1 (2026-07-09, #337) · **Assessor:** WF14 batch (9 sonnet
analysis subagents, ≤3 concurrent; every load-bearing claim re-verified by the main loop
against files/`gh`) · **Plugin versions under assessment:** v3.25.0 (#18–#21), v3.27.1
(#22–#26) · **Current main at assessment:** v3.30.0 (03ac4fe) — every filed defect
re-verified present there. **Mode:** full (all 9 run-records present in the sentinel store,
`projects/sentinel/docs/measurements/run_records.jsonl`; store-lag n/a — records read
directly, not via `latest`).

Subject: the WF2 machinery — never the shipped features. All nine children merged + closed
(PRs sentinel#28–#36); deliverable quality appears only where it reveals machinery behavior.

## Per-run scorecard (Fidelity / Gates / Clarity / Dispatch / Telemetry / Cost — rubric v1)

| run | PR / merge | ver | lane | F | G | C | D | T | $ | one-line verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| #18 cron+stamp | #28 28f5f80 | 3.25.0 | small-std | 3 | 4 | 4 | 4 | 4 | 3 | ran clean; evidence trail unreliable at Steps 5–7 (concurrent-run interleave, confirmed by branch mismatch) |
| #19 voice unification | #29 eef078b | 3.25.0 | full | 3 | 4 | 4 | uns | 3 | 3 | held; unlogged Step 7 marker + pre-mandate dispatch/usage gaps |
| #20 PBS verification | #30 73b2bea | 3.25.0 | full | 3 | 5 | 3 | uns | 3 | 3 | real catches (nonexistent image tag; single-VM-loss rule gap); 6/6-vs-7/7 test-count drift unreconciled |
| #21 status-by-text | #31 d0107d9 | 3.25.0 | small-std | 3 | 3 | 5 | uns | 3 | 3 | live deploy + real false-all-clear catches; telemetry gaps all pre-tracked (#340/#341, since shipped) |
| #22 guarded cleanup | #32 28895ee | 3.27.1 | full | 3 | 4 | 5 | 2 | 2 | 4 | machinery worked, record of it didn't: DISPATCH 1-of-4, dispatches[] omitted against its own rule |
| #23 hardening pack | #33 8c251b8 | 3.27.1 | small-std | 3 | 5 | 3 | 2 | 2 | 4 | gates 5/5 on a security path; Step-16 record contradicts notes twice (goal_guard, dispatches[]) |
| #24 richer digest | #34 950f4f8 | 3.27.1 | small-std | 2 | 5 | 3 | 1 | 2 | 4 | two masked-failure bugs caught pre-merge; steps 7/8/9 markers + all DISPATCH lines missing |
| #25 shutdown/start | #35 8a4c311 | 3.27.1 | full | 4 | 5 | 3 | 5 | 4 | 4 | cross-session resume worked — via a judgment call papering over the BRANCH_STATES gap (#364) |
| #26 log/error hybrid | #36 45061a4 | 3.27.1 | full | 5 | 5 | 4 | 5 | 4 | 4 | cleanest run of the epic; 12/12 markers, 5/5 dispatch lines, both deferrals on both surfaces |

uns = unscored (v3.25.0 predates the #330 DISPATCH mandate — version gap, not defect).

## What the gates actually bought (all 9 runs)

Gate value is the epic's strongest machinery result — every run produced named,
verified-real catches; none read as ceremony:

- **#24**: `rows(): if not b: return []` turned a FAILED Prometheus subquery into
  `reboot-required: none` (false all-clear), and an unreachable Alertmanager coerced to
  "up (0 peers)" — both fixed pre-merge.
- **#26**: unanchored `REBOOT-REQUIRED` grep newly reachable via raw journal text —
  a spurious-3am-auto-reboot hazard, reproduced by both 8a reviewers; plus the
  JERR-before-vacuum undercount (Codex) and journalctl-failure silent zero.
- **#23**: `$?`-clobber + double-text bugs in the reboot-return handler, found at Step 11
  after a green suite.
- **#20**: pinned image tag didn't exist on ghcr (deploy-DOA), caught at review.
- **#21**: monitoring script rendered `ALERTS: none` when Alertmanager was UNREACHABLE.
- **#25**: Codex High — detached runner could exit silently post-ack on lock-open failure.
- Cross-model disagreement worked as intended: same-model band-filtering dropped an
  0.85-confidence finding at 8a that Codex re-raised as High at Step 11 (#25 lock-open) —
  it was real.

## Cross-run synthesis — recurring friction, ranked by frequency × cost

1. **Step-16 record assembly is unvalidated manual work** (4/9 runs shipped wrong fields:
   `goal_guard` set-vs-deferred ×2, `dispatches[]` omitted despite well-formed lines ×2,
   `complexity` contradiction ×1, commit-count ×1, a gates row with no session marker ×1 —
   every one passed `validate_record`, which checks shape only). → **filed #361**.
2. **Marker emission tracks local reinforcement, not orchestrator quality**: Step 7 marker
   missing in 7/9 runs; Steps 8/9 dropped where lane runs compressed; §7 has no local
   marker instruction while §11.5 (templated inline) was emitted every run. Markers are
   load-bearing for resume + this very assessment. → **filed #362**.
3. **Usage telemetry effectively absent for epics**: 7/9 runs `capture_status:
   "unavailable"` (long multi-child sessions); the two captures that exist include one
   hand-subtracted delta persisted as `"captured"`; no per-run attribution primitive in
   `usage_capture.py`. → **filed #363**.
4. **Mid-run mandate adoption has no reconcile step**: the 3.25.0→3.27.1 cache bump landed
   mid-epic; DISPATCH-line compliance went 1-of-4 (#22), 3-of-6 (#23), 0 (#24), then 7/7
   and 5/5 (#25/#26) — a clean adoption curve with no tooling nudge at the boundary.
   Enforcement half addressed by #361's cross-checks; adoption-nudge noted for
   `post_update_reconcile.py`'s owner (not separately filed — cap/noise discipline;
   revisit if it recurs on the next version boundary).
5. **Cross-session resume relies on judgment where vocabulary runs out**: doc-only
   checkpoint branches literally classify to `changes` → Step 9. Correct call was made
   this epic (#25), documented inline — but unsanctioned. → **filed #364**.
6. **Contaminated reviewer returns have no named handling** (fabricated SHA on #23's 8a;
   survived only via orchestrator re-verification). → **filed #365**.
7. **Owner-gate / classifier pattern (ENVIRONMENT, working as designed)**: every child hit
   auto-mode classifier denials on host-privileged actions (installs, docker cutover, ufw,
   PBS token, SSH exec, one binary download). All 9 runs handled it correctly: no
   workaround, exact one-liners posted on the issue, honest `deploy: manual`. This is the
   safety gate doing its job; the consistent per-issue one-liner comment pattern is worth
   keeping as the de-facto convention.

## Not filed, with reasons

- **AC-rescope path improvised** (#23 nillerkgames Windows premise): detection is
  sanctioned (Step 2 live-probe fired correctly); only the resolution mechanics are
  unscripted, and the escalation conventions covered it. Below the bar next to #361–#365.
- **Owner-pause vs goal-hook deadlock** (#25): the Stop-hook goal guard kept blocking after
  an explicit owner "pause"; resolved via interim honest summaries + AskUserQuestion. The
  goal/Stop-hook mechanism is Claude Code harness machinery, not rawgentic code —
  ENVIRONMENT. Noted for the epic-run skill's docs as a future first-class pause note.
- **Feasibility-gate failure trail** (#26): the mechanical gate correctly fail-closed twice
  (format, then `assumed`) before passing — the failures were visible only in tool output,
  never in session notes, so the batch assessor could not see them (confirmed first-hand by
  the orchestrator). Minor trail gap; rides #362's marker-discipline theme.
- **Friction memory**: mempalace unavailable — friction memory SKIPPED (MCP server
  disconnected this session; the report carries the friction inventory instead).

## Telemetry audit rollup (fields with ≥1 negative verdict across 9 runs)

| field | match | mismatch | missing-in-record | missing-in-session | unverifiable | known-limitation |
|---|---|---|---|---|---|---|
| goal_guard | 7 | 2 (#23,#24) | — | — | — | — |
| dispatches[] | 2 (#25,#26) | 2 (#22,#23 omitted-with-lines) | — | 1 (#24 zero lines emitted) | — | 4 (v3.25.0 version gap) |
| usage | — | — | — | — | 1 (#25 wall_clock) | 8 (7 null + 1 hand-delta) |
| tests | 7 | 2 (#20 6/6-vs-7/7; #21 commits 5-vs-4 in changes) | — | — | — | — |
| gates[] counts | 8 | — | — | 1 (#24 step-9 row, no marker) | 1 (#21 step-11 9/9 not itemizable) | — |
| issue.complexity | 8 | 1 (#22) | — | — | — | — |
| reviewer_kind | 6 | — | — | — | 3 (vocab has no solo-reviewer member) | — |

Verdicts feeding the telemetry lane are folded into #361 (cross-checks) and #363
(usage); reviewer_kind solo-vocab rides closed #340's follow-up space — not refiled.

## Recommendations (ranked) → routing

1. Step-16 assembly cross-checks against session-note ground truth — **#361** (top: converts
   4/9 silent record corruption into rc=1).
2. Per-step marker reinforcement (Steps 7/8/9 + templated 11.5) — **#362**.
3. usage_capture per-run attribution + `derived` status + unavailable-reason — **#363**.
4. resume_lib doc-only branch-state — **#364** (one-line fix, prevents a wrong-step resume).
5. Contaminated-reviewer-return handling — **#365**.

## Per-run assessment blocks (rubric v1 fixed format, abbreviated)

```
WF2 @3.25.0 #18/PR28 lane small-std · F3 G4 C4 D4 T4 $3 · best catch: post-deploy self-check mandate (Low) · worst friction: concurrent-run interleave at Steps 5-7
WF2 @3.25.0 #19/PR29 lane full      · F3 G4 C4 D- T3 $3 · best catch: null-labels mid-batch TypeError (High) · worst friction: Step 7 marker unlogged
WF2 @3.25.0 #20/PR30 lane full      · F3 G5 C3 D- T3 $3 · best catch: nonexistent pinned image tag (deploy-DOA) · worst friction: 6/6-vs-7/7 count drift
WF2 @3.25.0 #21/PR31 lane small-std · F3 G3 C5 D- T3 $3 · best catch: ALERTS:none on unreachable AM (false-healthy) · worst friction: step-11 9/9 not itemizable
WF2 @3.27.1 #22/PR32 lane full      · F3 G4 C5 D2 T2 $4 · best catch: runner-rc0-on-failure (High) · worst friction: DISPATCH 1-of-4 + dispatches[] omitted
WF2 @3.27.1 #23/PR33 lane small-std · F3 G5 C3 D2 T2 $4 · best catch: $?-clobber via contaminated-but-real return · worst friction: goal_guard mis-assembly
WF2 @3.27.1 #24/PR34 lane small-std · F2 G5 C3 D1 T2 $4 · best catch: failed-subquery false all-clear (High x2) · worst friction: steps 7/8/9 markers absent
WF2 @3.27.1 #25/PR35 lane full      · F4 G5 C3 D5 T4 $4 · best catch: lock-open silent post-ack exit (Codex High) · worst friction: BRANCH_STATES judgment call
WF2 @3.27.1 #26/PR36 lane full      · F5 G5 C4 D5 T4 $4 · best catch: unanchored REBOOT-REQUIRED grep (3am-reboot hazard) · worst friction: hand-delta usage
```

**Aggregate:** Defects filed: #361 #362 #363 #364 #365 · Telemetry filed: #361/#363 (shared) ·
Not filed (reasoned): 3 + friction-memory skip · Clean runs: #26 (near), rest routed ·
Inferred (unconfirmed) claims: #24 usage-null cause (session-position at /clear boundary);
#25 pause-mechanics half of F4 (harness transcript not in evidence set).
