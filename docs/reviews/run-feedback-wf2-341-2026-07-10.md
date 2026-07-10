# WF14 run-feedback — WF2 #341 (issue-keyed step markers)

Assessed 2026-07-10 · rubric v1 · record: `claude_docs/.epic-333-scratch/record-341.json`
(explicit `--record`, schema-valid on load — `validate_record` NONE) · session notes:
workspace `claude_docs/session_notes.md` lines 4089–4197 · run version 3.28.0 (shipped);
cache lag known (run loaded pre-#341 cache; keyed markers emitted manually per the repo
contract) · current main 3.30.0 — every claim below re-checked against main.

## Evidence ledger (load-bearing)

| Fact | Tag | Evidence |
|---|---|---|
| All 12 mandatory/conditional markers present, keyed | confirmed | notes:4089–4197, quoted below |
| 12 dispatches in notes = 12 in record | confirmed | `grep -c "^DISPATCH issue=341 "` = 12; record `dispatches` len 12 |
| 1 dead reviewer dispatch, visibly re-dispatched | confirmed | record dispatches[1] `outcome=dead`; 8a marker names re-dispatch |
| Suite 2518 → 2534 (+16) | confirmed | Step 2 marker (baseline 2518p/1s, :4105); Step 11 marker (2534p/1s, :4190) |
| Loop-backs 2/3 | confirmed | Step 3 marker "rev 3 after 2 loop-backs" (:4125) = record `loop_backs.used: 2` |
| PR #352 merged 27aab30, 4 CI checks green | confirmed | Step 13/14 markers (:4194–4195); `git log origin/main` |
| usage `capture_status: unavailable` | confirmed | prior session crashed (id changed); honest marker, not a gap |
| Step 4 gate counts 14/14 deduped | unverifiable | pre-#340 assembly; per-pass evidence not in marker detail — `known-limitation` per the #340 legacy rule |

## Dimension scores

| Dimension | Score | Evidence (one line) |
|---|---|---|
| Step fidelity | 5 | every mandatory marker present + keyed (`— DONE (#341: …)` shape, :4089–4197); skips quote conditions (Step 6 "small-standard lane", Step 15 "no deployment target", Step 10 SKIPPED-VISIBLE with routing) |
| Gate value | 5 | named real catches quoted: 8a R1 "Step 6 sibling + Step 4 discard un-keyed" + R2 "slot-table-unpinned Med@0.95" (:4141); Step 11 fix commit 09f8ec6 with adversarial 4 findings dispositioned (:4189) |
| Prose clarity | 4 | executable throughout; one interpreted area — keyed-marker emission under cache lag required manual emission per the repo contract (declared, not improvised) |
| Dispatch reliability | 4 | 12/12 resolved primary; 1 dead reviewer detected as DEAD (never trusted) and re-dispatched — visible substitution per contract, not first-try-clean |
| Telemetry honesty | 3 | record valid on first summarize (persisted: yes, :4197); `usage` unavailable (known-limitation, honest) + gate counts pre-#340 (known-limitation) |
| Cost sanity | 3 | lane used; lane-widened 9>7 noted honestly; heaviest-least-value = Step 4's 3-pass design gate (2 loop-backs on a marker-prose design); cap 3 binds (usage absent) |

## Telemetry audit

| field | recorded | session_evidence | verdict | impact | routing |
|---|---|---|---|---|---|
| tests | 16 added, 2534/2535 | Step 2 baseline 2518 + Step 11 2534p/1s | match | — | — |
| gates[4] | 14/14 pass | "3 passes, 2 loop-backs, pass-3 clean at band" (:4127); per-pass detail not persisted | known-limitation | pre-#340 counting; legacy rule applies | — (rule shipped in #340, PR #353) |
| gates[8a] | 7/7 pass | R1 2 + R2 5 = 7, "ALL applied" (:4141) | match | — | — |
| gates[11] | 9/9 pass | 3-agent counts not in marker; adversarial 4 named (:4189) | known-limitation | pre-#340 counting | — |
| loop_backs | 2/3 | "rev 3 after 2 loop-backs" (:4125) | match | — | — |
| outcome | PR #352 merged, ci passed | Step 13/14 markers (:4194–4195) | match | — | — |
| security_scan | ran, 0/0, skips iac+sca | Step 11.5 marker (:4191) | match | — | — |
| usage | unavailable | prior session crashed; capture honest | known-limitation | session-level attribution weak spot | — |
| reviewer_kind | inline/hand_rolled_multi | conforms to the #340 precedence retroactively | match | — | — |
| dispatches[] | 12 entries | 12 flush-left lines, order preserved, 1 dead honest | match | — | — |

No `mismatch`, no `missing-in-record`, no `missing-in-session`. Zero telemetry
improvements to file (the two known-limitations are already fixed by #340, merged).

## Findings

1. **WORKING AS DESIGNED** — Step 1b marker's slot is un-keyed (`(deferred): epic #333
   goal active`, :4090): emitted under pre-#341 cache; the #341 contract itself declares
   the legacy fallback for stale-cache emitters. No action.
2. **WORKING AS DESIGNED** — dead reviewer dispatch at 8a: detected, recorded
   `outcome=dead`, re-dispatched. The #331 dead-return rule working.

Best catch this run: 8a R2's slot-table-unpinned (Med@0.95) — the authoritative slot
table would have shipped guard-less.

## Routing

Clean run: nothing filed, nothing memorized. (Both known-limitations already resolved
by #340 on main; no new defect reproducible at 3.30.0; friction covered by the
checkpoint-1 memories.)

```
WF RUN ASSESSMENT — WF2 @ v3.28.0 (issue #341, PR #352, lane small-standard) · rubric v1
Fidelity 5/5 · Gates 5/5 · Clarity 4/5 · Dispatch 4/5 · Telemetry 3/5 · Cost 3/5
Mode: full · Record: claude_docs/.epic-333-scratch/record-341.json
Best catch: 8a R2 slot-table-unpinned (Med@0.95) — authoritative table would have shipped guard-less
```
