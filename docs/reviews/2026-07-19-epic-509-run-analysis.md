# Epic #509 run analysis

**Run:** 2026-07-19, session be2d9fd9 (+ resumed continuation), interactive AUTO MODE with scoped auto-merge.
**Outcome:** 9/9 children merged (PRs #514–#523, v3.66.1→3.71.0) + telemetry PR #524; suite 3782→3857 (+75), 0 regressions, 0 loop-backs, 0 blockers. Epic CLOSED.
**Method note:** phase splits below are **reconstructed from session-transcript timestamps** (anchor commands: `git checkout -b`, review dispatch, `gh pr create`, `gh pr merge`, `work_summary.py summarize`) — the last hand-reconstruction this epic should ever need: #506's `timing` telemetry shipped **inside** this run, so this run's own records still carry `timing: absent` honestly. Totals cross-checked against PR createdAt/mergedAt. Every number below is **estimated (transcript-anchored)** unless marked confirmed.

## At a glance

- **Wall clock:** 18:17–23:28 UTC ≈ **311 min**, of which **56.3 min stalled** (one owner-away gap 20:54→21:50, mid-#506 review — confirmed, the only >10-min gap in the whole run) and ~15 min owner pause/interactions around #513. **Active ≈ 240 min for 9 children ≈ 26.5 min/child.**
- **Average active phase split per child:** design+plan **1.5** · implement **10.1** · review **9.9** · PR+CI+merge **3.1** · wrap **0.7** (min).
- **Biggest lever:** the single owner-away stall (18% of total wall) — attended runs need a review-verdict push notification or the armed resume launcher.
- **Session cost (cumulative capture):** $140.01 total; per-child deltas $7.35–$23.02.

## Per-child phase table (minutes, transcript-anchored)

| Child | design+plan | implement | review | PR+CI+merge | wrap | notes |
|---|---|---|---|---|---|---|
| #510 | ~1.5ᵉ | 7.9 | 10.2 | 7.0 | 1.3 | first child; PR+CI includes advisory-lane wait experiment |
| #511 | ~1.5ᵉ | 8.1 | 5.8 | 2.5 | 0.7 | |
| #512 | 1.6 | 9.6 | 8.5 | 2.4 | 0.7 | |
| #513 | 1.7 | 8.7 | 12.4 | 2.5 | 0.7 | review incl. shared-helper fix wave |
| #517 | ~1.5ᵉ | 7.1 | 4.9 | 2.8 | 0.6 | owner-added mid-run |
| #506 | 2.0 | 13.5 | **15.0** (+56.3 stalled) | 2.9 | 0.8 | stall = owner away, bucketed separately |
| #392 | 1.3 | 10.4 | 9.0 | 2.7 | 0.7 | |
| #507 | 0.6 | 6.6 | 10.9 | 2.5 | 0.5 | review incl. HIGH fix + wiring-pin updates |
| #508 | 2.0 | 18.6 | 12.4 | 2.8 | 0.7 | new-skill registration inflates implement |

ᵉ fetch anchor not captured for 3 children (different command form); value estimated from the measured six (range 0.6–2.0). Boundaries: design+plan = issue fetch→branch cut; implement = branch→review dispatch (includes baseline full suite + TDD); review = dispatch→PR create (includes reviewer wave, fix reconcile, 11.5 scan, pre-PR full suite); PR+CI+merge = PR create→merge; wrap = merge→record persisted.

## Average phase split (active time)

```
design+plan  █▌            1.5 min   6%
implement    ██████████   10.1 min  40%
review       █████████▉    9.9 min  39%
PR+CI+merge  ███           3.1 min  12%
wrap         ▊             0.7 min   3%
```

## Cost / tokens per child

Capture caveat (finding C1 below): `usage` in each run-record is **session-cumulative** — per-child figures here are successive-capture **deltas** (confirmed arithmetic over the capture files; #510's delta includes epic setup, #513's includes the pause/tasklist/launcher work).

| Child | Δcost (USD) | Δoutput tokens |
|---|---|---|
| #510 | 18.97* | 142,108* |
| #511 | 9.80 | 53,496 |
| #512 | 7.35 | 48,616 |
| #513 | 23.02* | 71,661* |
| #517 | 9.46 | 26,715 |
| #506 | 21.97 | 84,168 |
| #392 | 15.32 | 41,196 |
| #507 | 13.19 | 31,760 |
| #508 | 20.93 | 54,889 |

\* inflated by non-child work in the same capture window. Total session $140.01 (rate-card estimate; subscription-billed).

## Gate findings per step (from run-records — confirmed)

| Child | Step 4 | Step 11 | 11.5 | Loop-backs |
|---|---|---|---|---|
| #510 | 1/1 | 2/2 (1 Med applied, 1 Low band-dropped) | PASS | 0 |
| #511 | 1/1 | 3/2 (3 Low: 2 dropped, 1 noted) | PASS | 0 |
| #512 | 1/1 | 2/2 (1 Low applied, 1 dropped) | PASS | 0 |
| #513 | 1/1 | 3/3 (1 Med + 1 Low applied, 1 reworded) | PASS | 0 |
| #517 | 1/1 | 0/0 (clean) | PASS | 0 |
| #506 | 2/2 | 3/2 (1 Med self-verified+applied, 1 Low applied, 1 noted) | PASS | 0 |
| #392 | 1/1 | 2/1 (1 Low applied, 1 noted) | PASS | 0 |
| #507 | 1/1 | 3/3 (**1 HIGH** + 1 Med applied) | PASS | 0 |
| #508 | 1/1 | 2/2 (1 Med + 1 Low applied) | PASS | 0 |

Review yield: 21 findings across 9 waves; 12 applied pre-PR, incl. one HIGH (#507 `--record` overload would have skipped the new approval gate) and two design-grade Mediums (#506 unreachable "complete" status; #508 WF16/#359 numbering collision). Every wave cost ~5–8.5 min of reviewer wall — and paid for itself at least four times.

## Optimization levers (grounded)

1. **Kill the attended-run stall (single biggest wall item).** The one owner-away gap was 56.3 min = 18% of total wall — more than any phase's average. Change: push a notification at review-verdict-ready (`notify-owner` skill) and/or arm the resume launcher (`epic509-resume.sh` pattern) at run start even for attended runs. Saving: up to ~56 min per comparable evening run. Risk: none (additive).
2. **Trim the review-phase serial tail (39% of active).** Reviewer agent wall averaged ~6 min/wave (confirmed from dispatch durations 267–509 s), but the review PHASE averaged 9.9 min: the difference is the serialized reconcile → 11.5 scan → pre-PR full-suite re-run tail. 5/9 children re-ran the full suite (~2.4 min) because a review fix landed post-Step-9. Change: scope the pre-PR re-run when the review fix touched only guard-pinned prose/tests (steps.md §12 exception wording already allows consuming Step-9 evidence — tighten the "touched a test-pinned surface" trigger to the affected guard subset + registration file). Saving: ~2–2.5 min on every review-fix child (~11 min/epic). Risk: weaker final gate — needs the exception spelled out, not improvised.
3. **Registration children carry a fixed ~5-min surcharge.** #508's implement was 18.6 min vs the 8.9-min median — the delta is the multi-surface registration walk (whitelist, symlink, sync, canary, count strings ×6, second hand-pin discovered only by the full suite). Change: fold the `test_interview_skill.py` hand-pin (and any future ones) into the add-skill checklist, and script the count-string walk (`scripts/` helper that prints every surface needing a bump). Saving: ~3–4 min per new-skill child + one full-suite round-trip. Risk: none (tooling).

Non-levers, for the record: CI itself is tight (test lane ~2.1 min, hard lanes green 9/9); design+plan at 1.5 min shows the lane collapse working as intended; wrap at 0.7 min is already minimal.

## Divergences and bugs (all 9 children, inline)

Cross-child synthesis (each finding: severity × children affected × status):

| # | Finding | Sev | Children | Status |
|---|---|---|---|---|
| C1 | **`usage` capture is session-cumulative, not per-run** — every record's input/output/cost includes all prior session work; two same-session children overlap 100%. Confirmed: successive records' token counts are monotone-increasing supersets (capture files diffed). `capture_status: "captured"` validates clean while the number answers "session so far", not "this run". Corrupts exactly the cost-per-child analysis #508's skill automates (this report had to use deltas). | Med | all 9 | candidate — relates to #363 |
| C2 | **designArtifact sharedDoc unusable under concurrent sessions** — `docs/planning/campaign-log.md` was foreign-dirty the whole run; all 9 children recorded `design artifact (skipped)` with reason. The sharedDoc convention has no answer for a dirty shared file (git can't split it). | Med (friction) | all 9 | candidate |
| C3 | **Step 1b deferral condition doesn't cover interactive epic goals** — prose defers only on `RAWGENTIC_EPIC_GOAL` (headless driver); this interactive run had a session `/goal` and deferral was applied by judgment. Right outcome, outside the letter. | Low | all 9 | candidate |
| C4 | **epic-run step-state writes stopped at Step 4** — the skill mandates a manual write at each numbered step ENTRY (1–5); steps 1–4 were written, Step 5 (wrap) never. Fail-open, display-only impact. | Low | run-level | candidate |
| C5 | Below-band review findings applied via self-verification (4×: #506 F1 0.72, #392 L1 0.8, #512 F1 0.85, #507 Med 0.5) — each independently verified before applying, but the banded-confidence prose has no explicit "orchestrator self-verify converts a below-band finding" path; today it's judgment. | Low | 4 children | note |
| C6 | Advisory `code-review` lane pending at 7/9 merges — doctrine-compliant (advisory never gates), but those merges shipped without the lane's review ever landing. Observation for lane-timing, not a defect. | Note | 7 of 9 | note |
| C7 | `wall_clock_s` hand-set from orchestrator arithmetic (prose-sanctioned) — coarse (#506's 5436s included the 56-min stall). #506's `timing` key supersedes this going forward. | Note | all 9 | superseded by #506 |

Prose-divergence sweep against the loaded cache (3.66.0 for most of the run): mandatory steps 1–5, 7–9, 11, 11.5, 12, 16 have DONE markers for all 9 children (confirmed, session notes); Step 6 skipped per lane (sanctioned); Step 8a never fired (no high-risk tasks — path-allowlist confirmed per child); no hook errors observed; no schema rejections (9/9 `work_summary.py summarize` rc=0, confirmed).

**Filing discipline:** C1–C4 are candidates presented for owner approval — nothing filed by this analysis. C1 dup-check must reconcile with #363 (usage attribution) before any filing.

## Not checked

- Subagent-side transcripts (reviewer agents' internal time breakdown) — only their total durations were captured.
- The 3 missing fetch anchors (different command shape) — design+plan for #510/#511/#517 is estimated, not measured.
- WF14 batch machinery assessment — deliberately not run here (this stopgap is WF14-decoupled by owner decision; the new WF19 skill wires it for future epics).

## Sources

Confirmed: `docs/measurements/run_records.jsonl` (9 records, PR #524), session transcripts `be2d9fd9…jsonl` + `4bed8604…jsonl` (89 anchor events), capture files (cost deltas), PR metadata. Estimated: phase boundaries as defined above.
