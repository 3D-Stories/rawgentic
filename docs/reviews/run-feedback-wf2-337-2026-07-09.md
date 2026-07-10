# WF14 Run-Feedback — WF2 #337 (PR #339) · rubric v1

## At a glance

**The workflow ran faithfully and its gates caught three real defects — but the telemetry it recorded about itself has two honest mismatches, and the design gate spent the entire loop-back budget before a line of code was written.**

- **Step fidelity 5/5** — every mandatory step ran; every skip quotes its condition
- **Gate value 5/5** — three named real catches; none of the gates were ceremony
- **Prose clarity 4/5** — two spots needed interpretation (sidecar path, stale line pointer)
- **Dispatch reliability 5/5** — 15 dispatches, zero vacuous, zero substitutions
- **Telemetry honesty 2/5** — record valid on first try, but 2 fields don't survive audit
- **Cost sanity 3/5** — full spine justified by ACs; design-gate pass 3 was the low-value heavyweight

**Best catch:** the design gate found that an "empty skeletal" diagram entry would crash the SPA tab on click — caught before it shipped, now a committed guard test.

**Worst friction:** the run-record schema has no rule for counting findings on a multi-pass gate, so the number recorded was an eyeball.

**Routed:** 3 issues filed — #340 (telemetry counting), #341 (issue-keyed markers), #342 (doc-rot batch) · 1 friction memory saved · not a clean run.

## Run facts

- Assessed run: WF2 implement-feature · issue #337 · PR #339 · lane **full** · 2026-07-09
- Plugin cache the run loaded: **3.24.26** (confirmed: the skill invocation's base dir) · current main: 3.25.0 (`70b906f`)
- Mode: **full** · Record: `latest` — tail line of `docs/measurements/run_records.jsonl`; `validate_record(strict=True)` returned no errors; provenance sanity-check (workflow/issue/PR vs session evidence): confirmed

> **Bias caveat (stated, not waived):** the assessor is the same orchestrator that ran the assessed WF2. Scores lean on mechanical evidence (markers with line numbers, runner output, counters file) rather than recollection; the two telemetry mismatches below are self-reported orchestrator errors.

## Evidence ledger (summary)

All 16 mandatory/conditional step markers for the #337 run were located in `claude_docs/session_notes.md` (lines ~3390–3520), each disambiguated by content — **necessary because the shared notes file interleaves THREE concurrent runs** (this #337 run; a sentinel #13 run with PR #14; a saystory-area run with PRs #142/#143) and most `### WF2 Step N` markers carry no issue key (Finding F-2).

Confirmed facts (each with its evidence):

- All mandatory steps ran (1, 2, 3, 4, 5, 7, 8, 9, 11, 11.5, 12, 16) — markers `### WF2 Step N: … — DONE` with #337-keyed detail
- Skips justified — 8a: "0 high-risk tasks"; 14: "no user merge request"; 15: "no deployment occurred"
- Adversarial diff review 4-state marker present — `skipped (no security surface) — should_run_diff_review(True, 20 paths, False)`
- Suite baseline→end 2418+1skip → 2435+1skip, 0 failing — runner final lines quoted at Steps 2/9/11
- Loop-backs 3/3 (design 2 + spec_tighten 1) — `claude_docs/.wf2-state/337/loopback_counters.json`
- CI 4/4 lanes green on PR #339 — `gh pr checks` output at Step 13
- Goal guard set — Stop-hook activation + Step 1b marker

Unverifiable per-run: cost attribution (usage is whole-session — known limitation).

## Dimension detail

**Fidelity 5/5.** Every mandatory marker present; every skip quotes its met condition.

**Gates 5/5.** Named real catches: (1) design-gate pass 1 caught the wf14-empty-skeletal SPA-crash class (reviewer traced the renderer JS; became the committed DATA-shape guard); (2) the whole-suite gate caught a 4th `"6 SDLC workflow skills"` pin (`tests/test_interview_skill.py:65`) that BOTH Step-2 analysis agents and the add-skill checklist missed; (3) Step-11 reviewer 3 caught the clipped README snapshots (1440×1200 viewport vs required full-page 1440×2981).

**Clarity 4/5.** Executable throughout; two interpreted points: (a) `adversarial_review_lib.py review --findings-json` requires an under-project-root sidecar — lib-enforced, but WF2 Step-4 item-7 prose never states it (one failed dispatch, exit 2 "sidecar path escapes project root"); (b) repo CLAUDE.md mistake #2 cites `test_headless.py:1348`, live location `:1306` (stale pointer, cost a reviewer detour).

**Dispatch 5/5.** All agent dispatches resolved first-try, non-vacuous, models as routed (analysis=sonnet ×3, review=opus ×7, codex ×5, all exit 0). The one retry was the sidecar-path command error, not an agent failure.

**Telemetry 2/5.** Record valid on first summarize (rc=0), but the audit finds two genuine mismatches (gate-count derivability; reviewer_kind mapping).

**Cost 3/5.** Proportional overall given issue-L + AC5 (user elected the full spine over the mechanically-eligible lane — AC5 requires peer consult + adversarial design gate, which the lane drops). Heaviest-least-value step: **design-gate pass 3**, a full fresh adversarial review whose only High was a contradiction pass-2's own edits introduced; the targeted delta-verifier gave the same assurance far cheaper. The 3-pass design gate consumed the ENTIRE global loop-back budget (3/3) before implementation began.

## Telemetry audit — field by field

Verdicts use the canonical set: match | mismatch | missing-in-record | missing-in-session | unverifiable | known-limitation.

**match (12 fields).** `workflow`, `workflow_version` (3.25.0 = shipped version per schema; the cache that RAN was 3.24.26 — the schema field means shipped), `issue`, `changes` (23f/+1004/−22/8c vs captured shortstat), `tests` (17 added, 2435/2436 vs runner final line), `gates[6]` (6 dispositioned: 5 applied + 1 refuted-with-evidence), `gates[8a]`, `gates[9]`, `gates[11]`, `gates[15]`, `security_scan` (ran, skips [iac, sca] visible), `loop_backs` (3/3 vs counters file), `outcome.ci`/`pr`, `goal_guard` (set — note: the Stop-hook actually FIRED once mid-run, blocking a premature stop at the Step 7→8 seam; `fired` would be the truer value but is manual-only per the schema's own named limitation).

**mismatch (2).**

- `gates[4].findings/resolved` recorded 14/14 — session evidence: pass-1 8 merged, pass-2 6 (1 dup), pass-3 8 raw ≈ 19–22 raw; 14 is not derivable under any stated counting rule. Impact: trend data understates gate volume. → Finding F-1, filed as #340.
- `gates[4].reviewer_kind` recorded `codex` — the documented mapping says Step-4 self-review → `inline`; codex is the adversarial layer; one slot cannot carry both. → merged into #340.

**missing-in-record (1).** `dispatches[]` absent while session evidence shows ≥15 dispatches — the field hasn't landed (#329/#330 open); linked, NOT refiled.

**known-limitation (2).** `usage` (whole-session tokens, values internally plausible — standing weak spot); `outcome.merged: false` (snapshot-at-persist; the PR merged after the record was written — store-lag-known semantics).

Improvements not filed, with reasons: `outcome.merged` staleness — working-as-designed snapshot semantics with an existing `chore(telemetry)` backfill lane; `goal_guard: fired` under-capture — already a named schema limitation (run-record.md, "MANUAL-ONLY … aspirational").

## Findings (classified)

- **F-1 · orchestrator-error (plugin-friction rider) · telemetry lane → FILED #340.** gates[4] recorded 14/14 by eyeball; no counting rule exists for multi-pass gates, nor a `reviewer_kind` convention for merged self-review+adversarial gates. The error is the orchestrator's; the schema's silence invited it (inviting text: run-record.md `"findings": N, "resolved": N`).
- **F-2 · plugin-friction (defect-adjacent for WF14/resume) → FILED #341.** Most `### WF2 Step N` markers carry no issue key; three concurrent runs interleaved in the shared session-notes file today. WF14's Step-1 gather and the resume protocol's markers-complete grep can mis-attribute markers across runs.
- **F-3 · plugin-friction (doc-rot batch) → FILED #342.** Stale `test_headless.py:1348` pointer (live `:1306`); `load_adversarial_review_config` docstring omits the live `runFeedback` key; codex `longDescription` count un-guarded. All verified on main `70b906f`.
- **F-4 · plugin-friction → friction memory.** WF2 Step-4 item-7 prose omits the findings-json under-project-root constraint the lib enforces (one failed dispatch this run).
- **F-5 · working-as-designed.** Global loop-back cap (3) binding before per-source caps let the design gate exhaust the whole budget pre-implementation — a pinned, documented trade-off. Surprising, correct, no action (already memorized at Step 10).

## Fixed summary block

```
WF RUN ASSESSMENT — WF2 @ v3.24.26 (issue #337, PR #339, lane full) · rubric v1
Fidelity 5/5 · Gates 5/5 · Clarity 4/5 · Dispatch 5/5 · Telemetry 2/5 · Cost 3/5
Mode: full · Record: latest (validated)
Best catch: wf14 empty-skeletal SPA-crash class (design gate pass 1 → committed DATA-shape guard)
Worst friction: run-record gates[] has no multi-pass counting rule — references/run-record.md ("findings": N)
Telemetry verdicts: match 12 · mismatch 2 · missing-in-record 1 · missing-in-session 0 · unverifiable 0 · known-limitation 2
Defects filed: #342 | Telemetry filed: #340, #341 | Not filed (cap): 0 | Friction memorized: drawer_rawgentic_process-conventions_fb032078851501f4f034404d (saved, id-verified) | Clean run: no
Report: docs/reviews/run-feedback-wf2-337-2026-07-09.md
Inferred (unconfirmed) claims: per-run cost share of the $50.15 session figure; goal-guard fired-count (manual-only)
```
