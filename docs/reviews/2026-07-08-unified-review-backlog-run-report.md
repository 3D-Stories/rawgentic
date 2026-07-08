# Unified-review backlog run — final report (2026-07-07 → 2026-07-08, unsupervised)

**Scope executed:** EPIC 3 (5 children), EPIC 4 (4), EPIC 5 (4), 6b + 6c — **15 children + the PR0 deliverable, all merged.** Goal source: docs/reviews/2026-07-07-harness-and-code-review.md Part 3.

## Merged (16 PRs, v3.24.2 → v3.24.17, plus telemetry)

| PR | child | issue | version | main sha | suite after |
|---|---|---|---|---|---|
| #260 | PR0 review deliverable | — | 3.24.2 | c50aa5e | 2278 |
| #281 | 3a | #261 | 3.24.3 | af6c023 | 2283 |
| #282 | 3b | #262 | 3.24.4 | 1be0ed5 | 2293 |
| #283 | 3c | #263 | 3.24.5 | 952f863 | 2295 |
| #284 | 3d | #265 | 3.24.6 | 4724eba | 2304 |
| #285 | 3e | #264 | 3.24.7 | 2c95fbc | 2336 |
| #286 | 4a | #266 | 3.24.8 | e414f99 | 2345 |
| #287 | 4b | #267 | 3.24.9 | 2f716ed | 2347 |
| #288 | 4c | #268 | 3.24.10 | 98a2238 | 2350 |
| #289 | 4d | #269 | 3.24.11 | 732ee3f | 2358 |
| #290 | 5a | #270 | 3.24.12 | bc070d2 | 2363 |
| #291 | 5b | #271 | 3.24.13 | 85bb101 | 2363 |
| #292 | 5c (WF3) | #272 | 3.24.14 | ee2fd4b | 2363+1skip |
| #293 | 5d | #273 | 3.24.15 | cd0e043 | 2363+1skip |
| #294 | 6b | #275 | 3.24.16 | 023c98e | 2357+1skip |
| #295 | 6c | #276 | 3.24.17 | c905ec8 | 2359+1skip |
| #296 | telemetry (15 records) | — | — | faed5df | — |

**Suite delta vs run baseline:** 2278/0 → **2359 passed + 1 deliberate visible skip, 0 failing** (+81 net: new guards/regression tests added, 6 dead unit cases removed with their dead subject, 1 silent-pass converted to a visible skip). Every child: red-before-green shown, full-suite gate by exit code, both pylint lanes, security scan (iac/sca always visible skips), ≥1 opus review, CI all-4-lanes green before merge, merge verified on main, run-record rc=0.

## Epics closed
- **#277 (EPIC 3)** — closed with summary (prior session).
- **#278 (EPIC 4)** — closed with summary: WAL stdin 4 jq→1; wal-guard 13 greps→2; wal-bind-guard 6 jq→5; session-start spawns bounded + dead archive path removed.
- **#279 (EPIC 5)** — closed with summary: principles.md + consolidation.md quarantined w/ authoritative STATUS table; count guards compute from the tree; vacuous guards made loud; both operating manuals' self-rot fixed (workspace half direct-edited, .bak at /home/rocky00717/rawgentic/CLAUDE.md.2026-07-08.bak).
- **#280 (EPIC 6)** — STAYS OPEN: 6b+6c merged (status comment posted); **6a #274 [decision] not implemented per scope** (wire-or-delete external_ref_lib is an owner call).

## Review catches worth reading (every child had ≥1 real catch)
- **#266**: Codex High — multi-document stdin let the trailing document's assignment group win via eval (verified live, fixed). Chasing it exposed + fixed a pre-existing deny() duplicate-JSON bug, and a reviewer caught my own fix flipping the fail-closed guard open (also fixed, end-to-end pinned).
- **#267**: pre-filter treated grep rc 2 (future malformed pattern edit) as no-match — would have fail-opened all 12 rules at once. Fixed; parity fuzzed 600 iterations, 0 breaks.
- **#268**: reviewer refuted the first draft LIVE — it trusted the registry's unvalidated project_path; a stale/hand-edited entry flipped deny→allow. Redesigned to workspace-truth (kept the perf win).
- **#269**: Codex — newline-in-cwd field-shift in the consolidated transport (shlex fix); a reviewer then proved my regression pin was VACUOUS (passed on both parsers) — replaced with a mutation-verified discriminator.
- **#271**: the computed membership guard exposed that "9/15 evals" had the right count with WRONG membership (sync-security-patterns HAS evals) — and the review doc's own "10/15" correction was wrong too.
- **#272/#273/#275/#276**: root-skip convention omission; one more bare count found by the accuracy pass; stale doc bullet; detect/repair proof promoted from manual verification to a committed sandbox test.

## Incidents / process notes (honest ledger)
- **Session limit (~4:00–4:30am MT)** killed the first #271 reviewer pair mid-run. The requested overnight watchdog (25-min session cron) worked as designed: queued ticks fired after reset, both reviewers relaunched, zero work lost.
- **#273 merge**: first attempt hit a network error; PR state verified still OPEN before retry (no double-merge). The run-record was persisted one retry BEFORE the merge landed — accurate in hindsight, but the ordering was wrong; corrected for all later children (merge-verify before summarize).
- **Opus classifier outage** denied one agent dispatch once; retried clean.
- mempalace MCP disconnected mid-run — Step 10 memorize skipped (best-effort per convention) from #272 onward.

## Reserved for owner (untouched, per scope)
- **EPICs 1–2**: never filed as issues (live-harness scope) — only their review-doc entries exist.
- **6a #274** [decision], **6d**, **6e** (not filed — live-infra).
- Follow-up recorded during the run: wal-guard `deny()` uses `jq --arg`, which hits argv limits at ~300KB commands leaving no deny JSON (= allow) — pre-existing, verified not introduced by this run's changes; candidate small fix.
- Plugin cache still runs v3.24.0 — reinstall (exit sessions first, §7 recipe) to pick up v3.24.17.
