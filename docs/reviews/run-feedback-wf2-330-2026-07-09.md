# WF14 run-feedback — WF2 #330 (emit dispatches[] from completion steps)

Assessed under **rubric v1**. Mode: **full**. Record: `--record` (persisted store line,
rode PR #351 per store-lag convention; schema-VALID). Run's loaded cache: **3.25.0**
(session crashed mid-run and resumed — id 34273e95 → 82e1cde5; cache updated to 3.26.0
mid-run, prose executed from context). Current main: 3.27.1. Full spine (8 impl files >
lane ceiling), PR #349 merged 4a629df, v3.27.0, suite 2503+1skip→2512+1skip.

## Evidence ledger

- `confirmed` — all mandatory markers present in the `## WF2 run: rawgentic #330`
  section (Steps 1,1b,2,3,4,5,6,7,8,8a,9,11,11.5,12,13,14,16; Step 10 SKIPPED-VISIBLE
  with the mempalace peer-writer-lock reason; Step 15 skipped, no deploy). Suite deltas
  from runner finals at every gate. 19 DISPATCH lines in-section (the run dogfooded the
  contract it shipped).
- `confirmed` — 3 loop-backs consumed (counters file `claude_docs/.wf2-state/330/`:
  design=2, spec_tighten=1, total=3/3), each tied to a named, quoted finding.
- `confirmed` — session-limit incident: 3 Step-11 reviewers + 1 memorize agent killed
  mid-work ("You've hit your session limit"); results treated as DEAD (never trusted),
  all re-dispatched post-reset; recorded as `outcome=dead` DISPATCH lines and in the
  record's `extra`.
- `confirmed` — over-emission: 19 assembled dispatch entries vs 18 real events (one
  review line double-logged by the orchestrator); surfaced honestly in `extra` per the
  never-dedup contract — the contract's emitter-error surfacing worked on its first run.

## Dimension scores

| Dimension | Score | Evidence |
|---|---|---|
| Step fidelity | 5/5 | Every mandatory marker present; every skip quotes its met condition; the crash/resume rebuilt state from the counters file + notes, no step lost. |
| Gate value | 5/5 | Named real catches with quoted findings: pass-1 self-review proved the design's run-header scoping anchor "matches NOTHING in the notes corpus (WF3 has no run headers at all)" — an unimplementable assembly contract stopped pre-code; pass-2 convergent reviewers killed the unimplementable start-vs-completion count; Step 11 caught the changelog order inversion @0.97. |
| Prose clarity | 3/5 | Interpretation needed: §4 item 7 prescribes dispatching the WF5 skill for adversarial-on-design, but the practical path is `adversarial_review_lib.py review --type design` — and its sidecar must live UNDER the project root, discovered by a failed run ("sidecar path escapes project root"); the CONSULT out-file rule ("may live anywhere") primes the wrong expectation. |
| Dispatch reliability | 3/5 | 4 dispatches died on the session limit (environment) — every death detected, never trusted, visibly re-dispatched; remaining 15 resolved first-try, models as routed. Substitutions declared per contract. |
| Telemetry honesty | 3/5 | Valid on first summarize (rc=0); `usage.capture_status: unavailable` is the honest value for a crashed-session id; Step-4 `findings: 17` aggregates 3 review passes — the multi-pass counting rule is #340's open question (`known-limitation`); the 19-vs-18 over-emission is recorded in `extra` (contract-honest). |
| Cost sanity | 3/5 | Full spine justified (over lane ceiling) and 3 loop-backs each bought a real fix; heaviest-least-value: the pass-3 ADVERSARIAL re-review — it re-litigated two already-refuted findings (union, platform_apis) and its two real catches (stale cross-references) were convergently caught by the same-model pass-3 reviewer anyway. |

## Telemetry audit

| field | recorded_value | session_evidence | verdict | impact | routing |
|---|---|---|---|---|---|
| tests | 9/2512/2513 | runner finals 2503→2512, +9 guards | match | — | — |
| gates[4] | 17/17 pass | 3 passes: 5+6, 1+4, 1+5 findings (self+adv) | known-limitation | multi-pass counting rule undefined | dup → #340 (filed, epic queue) |
| gates[6] | 3/3 pass codex | adversarial-on-plan 3 Medium | match | — | — |
| gates[8a] | 5/5 | R2's 5 applied hardenings | match | — | — |
| gates[11] | 7/7 | A1 1 + A2 3 + adversarial 3 (1 applied/2 refuted) | known-limitation | refuted-vs-resolved counting = #340 | dup → #340 |
| loop_backs | 3/3 | counters file design=2 spec_tighten=1 | match | — | — |
| outcome | PR 349 merged, ci passed | gh MERGED 4a629df, 4 checks pass | match | — | — |
| usage | capture_status unavailable | crashed session id changed | match (honest null) | per-run cost unknowable | standing weak spot |
| dispatches[] | 19 entries (4 dead) | 19 lines; 18 real events; over-emission in extra | match-with-documented-emitter-error | first live record of the field | — |

## Findings and classification

1. **PLUGIN FRICTION** — the artifact-only adversarial reviewer re-litigates findings
   refuted in earlier passes (it has no pass memory; the design must carry refutation
   rationale in its own text to defend itself). Quoted trigger: §4 item 7 "the next
   Step 4 pass dispatches a fresh adversarial review against the revised design." Cost:
   2 re-refutations × 2 passes this run.
2. **PLUGIN FRICTION** — sidecar-location asymmetry: consult out-file "may live
   anywhere" vs review `--findings-json` must be under project root; learned by failure.
3. **ENVIRONMENT** — session-limit kill ×4 mid-Step-11; crash/resume mid-Step-3. The
   then-current prose had no dead-return rule for reviewer sites; the orchestrator
   improvised the correct behavior — and #331 (next child, now merged) codified exactly
   it. Working-as-designed as of v3.27.1.
4. **WORKING AS DESIGNED** — over-emission surfaced, never silently deduped; the
   contract's honesty path executed on its first live record.

## Routing

- Defects filed: **none** (multi-pass counting → #340 dup; marker attribution → #341
  dup, reproduced again this run by the same concurrent-session contamination).
- Telemetry improvements: none beyond dups (dropped-with-reason: filed).
- Friction: ONE memory for this run (items 1+2), id printed in the summary block.

## Intentionally not claimed

- Per-run token cost (usage unavailable — crashed session).
- Whether pass-3 adversarial should be skippable after refutations (a design question
  for the owner, recorded as friction, not filed as a defect).
