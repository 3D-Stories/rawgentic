# WF14 run-feedback — WF2 #340 (gate counting + reviewer_kind)

Assessed under **rubric v1** (2026-07-09, #337). Assessor session d3d189d8 (bound saystory; report path-bound to the rawgentic root per WF14 contract). Run under assessment: WF2 #340 → PR #353 (merged 5cd28b4), epic #333 auto-run child 5/10, small-standard lane, plugin **3.29.0** (record `workflow_version`); defects verified on main @ **3.30.0**.

**Mode: full** · Record: `latest` = `docs/measurements/run_records.jsonl` line 86 — provenance sanity-check PASSED (workflow/issue/date match session-note markers; the assessing session is a different session, but the shared workspace `claude_docs/session_notes.md` carries the run's full keyed marker trail, lines 4199–4351).

Meta note: #340 was itself filed by the previous WF14 run (assessment of #337). This report assesses the run that implemented it — the first record assembled under the #340 counting rules.

## Evidence ledger (all quotes verbatim from workspace `claude_docs/session_notes.md`)

| # | Fact | Tag | Evidence |
|---|---|---|---|
| E1 | All mandatory steps ran | confirmed | `### WF2 Step N … — DONE (#340: …)` markers at lines 4206, 4225, 4253, 4255, 4257, 4259, 4305, 4318, 4333, 4339, 4342, 4346, 4348, 4349, 4351 |
| E2 | Step 6 skip justified | confirmed | line 4258 `### WF2 Step 6: Plan Drift — SKIPPED (#340: small-standard lane)` |
| E3 | Step 4 adversarial skip justified | confirmed | line 4254 `— Adversarial Review (#340, skipped): lane run — adversarial-on-design not applicable` |
| E4 | Design gate caught real defects | confirmed | line 4247: F1 High "WF3 §14 gates DUPLICATED inline", F2 High "existing mapping contradicts the new precedence rule", F3 Med "dispatches[]-visibility premise WRONG: 0 codex entries" |
| E5 | 8a caught a real High | confirmed | line 4276: "R2 1H@0.8 [rule landed where per-finding input no longer exists — fixed: compute-at-gate-close + persist]" |
| E6 | Step 11 caught a High + a factual Med | confirmed | line 4338: "A1 High [disposition-alias 'unless it cites evidence' reopened closed set — CLOSED]"; "R1 opus 1 Med confirmed [changelog said 8 new tests, truth 6 — fixed]" |
| E7 | One implementation dispatch died and was retried | confirmed | lines 4268/4277: `DISPATCH issue=340 … outcome=dead resolution=primary` then `outcome=retried`; line 4275 "impl task 1 (opus, retry after limit-kill)" |
| E8 | 8 dispatches evidenced | confirmed | 8 keyed `DISPATCH issue=340` lines (4250, 4251, 4268, 4277, 4278, 4279, 4340, 4341); Step 16 marker (4351) "8 dispatches assembled" |
| E9 | Record persisted 2 dispatches | confirmed | `run_records.jsonl` line 86: `dispatches` = 2 × `role=review, outcome=ok` |
| E10 | Duplicate record line appended by second summarize, hand-discarded | confirmed | line 4349: "discarded uncommitted DUPLICATE … jsonl line [second summarize appended dup @05:02:40 — committed 03:55:10 line canonical]" |
| E11 | Store has exactly 1 committed #340 line | confirmed | JSON audit: 87 lines, `issue.number==340` only at line 86 |
| E12 | `persist_record` has no idempotency guard | confirmed | `hooks/work_summary.py:703-709` @ main 3.30.0 — unconditional `open(p, "a")` + write |
| E13 | Run spanned two sessions (first crashed) | confirmed | record `extra` "run spanned two sessions (82e1cde5 crashed…)"; Step 16 marker "usage captured this-session-only" |
| E14 | Marker interleaving with concurrent runs | confirmed | `DISPATCH issue=340` line 4268 sits inside the sentinel #25 section — the exact contamination #341 (CLOSED, shipped) addresses; keyed tokens made attribution mechanical here |
| E15 | Loop-back accounting | confirmed | record `loop_backs 1/3`; markers 4247 "consume_loopback design #1 (total 1/3)", 4253 "rev 2 after 1 loop-back" — one loop-back, reported at both its consumption and its gate |

## Dimension scores

1. **Fidelity 5/5** — every mandatory marker present (E1); both skips quote their met condition (E2, E3); Step 15 skip consistent with `deploy: not_applicable`.
2. **Gate value 5/5** — ≥3 named real catches with finding text quoted (E4, E5, E6). Best: design pass-1 F3 killed a wrong premise (the design assumed codex runs appear in `dispatches[]`; evidence showed 0 across all epic records) before any code was written.
3. **Clarity 4/5** — no command failed as written; one improvisation: multi-session record assembly has no prescribed procedure — the orchestrator invented the honest-but-ad-hoc "usage captured this-session-only" note (E13). No skill sentence covers a crashed-session reassembly, so the honesty was orchestrator discipline, not machinery.
4. **Dispatch 3/5** — one implementer died on a session limit-kill and was visibly retried per the documented fallback, with honest `dead`/`retried` DISPATCH lines (E7). Not higher: a dispatch died; not lower: nothing vacuous was trusted.
5. **Telemetry 2/5** — one persisted field contradicts session evidence: `dispatches[]` = 2 vs 8 evidenced, and the dropped entries are precisely the `dead`/`retried` pair (E8, E9) → **mismatch**. A second summarize also appended a duplicate line requiring manual discard (E10, E12). Everything else audits clean (table below), and the record self-declares its usage limitation — hence 2, not 1.
6. **Cost 4/5** — lane skips demonstrably applied (E2; no 8a on tasks 2–3 per lane contract, line 4297). Heaviest-least-value step: 8a R1 on task 1 — "NO FINDINGS 6/6 checks" (line 4276) while R2 caught the High. Usage $28.28 / 58.4M input tokens is heavy for a 20-file telemetry change but spanned a crash-restart; attribution is session-level (known-limitation) so no cost defect claimable.

## Telemetry audit (record line 86 vs session evidence)

| field | recorded_value | session_evidence | verdict | impact | routing |
|---|---|---|---|---|---|
| workflow / workflow_version | implement-feature / 3.29.0 | WF2 markers; cache 3.29.0 loaded | match | — | — |
| issue | 340 / feature / standard | markers E1; lane markers | match | — | — |
| changes | 20 files, 7 commits, +882/−14 | commits listed line 4305 + fix commits 4f02142, T3 | unverifiable (diff not re-derived) | low | none — not re-derived; PR merged, counts plausible |
| tests | added 6, passing 2540, total 2541 | Step 11 "suite 2540p/1s post-fix"; R1 Med corrected 8→6 (E6) | match | — | note: record reflects the post-review corrected count — the gate fixed the record's input |
| gates[] step 4 | findings 9 resolved 9, inline | pass1 F1–F8 (8) + pass2 1 new Low = 9 unique, all terminal (4247-4248) | match | — | first record under the #340 unique-across-passes rule — rule works |
| gates[] step 8a | 5/5 hand_rolled_multi | 4276 detail lists "1H + 1M aliases + 2L" = 4 unique; recorded 5 | unverifiable | low | pass-detail line may omit one finding; not claimable as mismatch from the marker alone |
| gates[] step 11 | 4/3 hand_rolled_multi | R1 1 Med + A1 High + A2 Med + A3 Low = 4; resolved 3, A3 = tracked deferral to #343 (E6, 4338) | match | — | honest unresolved-deferral accounting |
| loop_backs | 1/3 | E15 | match | — | — |
| outcome | PR #353 merged, ci passed | 4346-4349 | match | — | — |
| security_scan | ran, 0/0, skipped iac+sca | 4342 | match | — | — |
| usage | 58.4M in / 221k out / $28.28, captured | E13 — this-session-only, prior session crashed | known-limitation | med | standing weak spot (session-level attribution); record self-declares |
| reviewer_kind | 4=inline, 8a/11=hand_rolled_multi | dispatch lines: opus reviewers at 8a+11; design gate inline-orchestrated | match | — | first record under #340 precedence rule — rule works |
| dispatches[] | 2 entries, both review/ok | 8 keyed lines incl. dead+retried implementer (E7, E8) | **mismatch** | **high** — false-clean dispatch history hides a died-and-retried implementer | **filed #356** |
| (store behavior) | 1 committed line | duplicate appended by 2nd summarize, hand-discarded (E10) | mismatch (store lane) | med — silent double-record on any Step-16 re-run | **filed #355** |

Verdict counts: match 8 · mismatch 2 · unverifiable 2 · known-limitation 1 · missing-in-record 0 · missing-in-session 0.

## Findings and classification

| Finding | Class | Routing |
|---|---|---|
| `dispatches[]` persisted 2/8; dead/retried implementer pair dropped; no truncation flag while marker claims 8 | PLUGIN DEFECT (telemetry lane) | filed **#356** (bug, framework, wf-feedback) |
| `persist_record` blind append — re-summarize duplicates record lines; recovery was manual eyeballing | PLUGIN DEFECT (telemetry lane) | filed **#355** (bug, framework, wf-feedback) |
| Marker interleaving across 3 concurrent runs in shared notes (E14) | WORKING AS DESIGNED post-#341 (keyed tokens attributed mechanically) | linked, not refiled — #341 CLOSED |
| No prescribed multi-session record-assembly procedure (crashed-session seam) | PLUGIN FRICTION (borderline) | **not memorized** — the concrete failure it produced is exactly #356's subject; a separate friction memory would duplicate the issue. Dropped with this reason |
| 8a R1 zero-findings pass (cost) | WORKING AS DESIGNED (multi-reviewer redundancy is the design; R2 caught the High the same pass) | none |
| Usage session-level attribution | known-limitation (standing weak spot) | none — rubric standing item |

Cap accounting: 2 of 3 filed; 0 findings beyond cap.

## Claims intentionally not made

- `changes` counts not re-derived from the merged diff (unverifiable, low impact).
- Step-8a gate count (5 vs 4 in the marker detail) left `unverifiable` — the marker may compress a finding; refiling on marker-prose alone would be noise.
- No per-run cost claim beyond the recorded session-level figure.

## Routing summary

- **#355** — fix(telemetry): persist_record blind-appends — re-summarize duplicates run-record lines, recovery is manual
- **#356** — fix(telemetry): dispatches[] loses cross-session entries — #340 record persisted 2 of 8, dead-dispatch evidence dropped
- Friction memory: skipped with reason (see table) — mempalace was available (verified writable this session); the skip is a dedup decision, not an availability failure.
- Both issues cross-linked to the #329/#330 lineage in their bodies; epic #333 conventions followed.
