# WF14 Run-Feedback — WF2 #344 (PR #368) · rubric v1

## At a glance

**The full spine earned its cost on this run: cross-model gates forced one design loop-back that fixed three real High-severity design flaws before implementation, per-task review caught a meaning-inverting decorator bug, and the Step 11 adversarial pass confirmed a live never-raises contract violation — but the breaker fired repeatedly on determinable doc-consistency flags (now filed as #370), and the orchestrator burned ~13 minutes on a self-inflicted concurrent-pytest collision.**

- **Step fidelity 5/5** — every mandatory + conditional marker present, issue-keyed; every skip quotes its condition
- **Gate value 5/5** — the strongest gate showing of the epic: 3 High design flaws (loop-back), a meaning-inversion (8a), a confirmed TypeError contract violation (Step 11 adversarial), all pre-merge with quoted finding text
- **Prose clarity 4/5** — two interpreted spots: Step 4's adversarial dispatch (skill-vs-lib path), sidecar containment discovered by runtime error
- **Dispatch reliability 5/5** — 15 dispatches, all first-try, zero vacuous, zero substitutions, models exactly as routed
- **Telemetry honesty 4/5** — record valid on first summarize; every audited field matches; usage stays the standing session-level caveat
- **Cost sanity 3/5** — full spine justified for a renderer touching every artifact; the waste was orchestrator-owned (concurrent suite runs), not machinery-owned

**Best catch:** Step 11 adversarial diff review — "a syntactically valid but malformed config such as `{"projects": 1}` raises `TypeError`, contradicting the never-raises contract" — reproduced live in the run, fixed with guards in BOTH `design_artifact_style` and `design_artifact_shared_doc` plus a regression test. Runner-up: Step 4 pass 1's "decorators over rendered HTML can reach attributes" forced the inline-stage decorator redesign.

**Worst friction:** the ambiguity circuit breaker's mechanical trigger (`ambiguity_flag == ambiguous`) fired on 10 cross-model flags across Steps 4/6 — every one a determinable internal-consistency item; an autonomous run must choose between hanging and resolving-with-a-log. Filed as **#370**.

**Routed:** 1 issue filed — #370 (breaker semantics under autonomous campaigns) · friction memory SKIPPED (mempalace unavailable) · not a clean run.

## Run facts

- Assessed run: WF2 implement-feature · issue #344 · PR #368 (merged `7bb8928`, all 4 CI green) · lane **full** (complex_feature, 8 impl files) · 2026-07-10
- Plugin cache the run loaded: **3.28.0** · current main at assessment: 3.32.1 (`f8434bf`)
- Mode: **full** · Record: `claude_docs/.epic-333-scratch/record-344.json` — validated at persist (summarize rc=0, first attempt); provenance vs session markers: confirmed
- Record: 28 files / 12 commits / +1574-642 · tests 2556→2611 (+55 net) · loop_backs **1**/3 · 15 dispatches · goal_guard deferred
- Mid-run interleave (owner-directed, outside WF2 machinery): PR #366 conflict-resolution + merge (re-versioned 3.31.1) between Steps 6 and 7 — base moved 912a629→352c124; the run re-verified its baseline on the new base before branching. Handled cleanly; noted for provenance.

> **Bias caveat (stated, not waived):** same-orchestrator assessment; scores lean on issue-keyed markers, runner tails, sidecar JSONs, and the persisted record.

## Evidence ledger (summary)

Markers in `claude_docs/session_notes.md` (#344-keyed throughout; the #341 slot contract again made attribution mechanical). Confirmed:

- Steps 1, 1b(deferred), 2 (+estimate), 3 (peer consult, blind both ways — draft on disk before out-file read), 4 ×2 passes (+2 invoked adversarial markers), 5 (+estimate refresh 8 agents), 6 (+invoked adversarial marker), 7, 8 (+whole-issue SKIPPED, 6 per-task dispatch logs), 8a ×2 (both high-risk tasks), 9, 10 SKIPPED, 11 (+adversarial `findings_present 4`), 11.5, 12 (+design artifact updated), 13, 14, 16 — all present [confirmed]
- Loop-back: `consume_loopback(design)` → counters file `{design:1, total:1}` persisted [confirmed: command output in notes]
- Red evidence per task: 7/7(templates)/13/6/7 failures quoted from implementer returns [confirmed]
- Suite: 2556 baseline → 2566 → 2581 → 2596 → 2602 → 2609 → 2611 final, exit 0, all solo runs; the two anomalous runs (3-failed@273s, 7-failed@487s) are logged as CONCURRENT-pytest collisions with process-level evidence and named flake-with-reason [confirmed]
- 8a review log: 2 entries (tasks 1, 2), verdict applied; `assert_review_coverage` → (True, []) [confirmed]
- Real-thing: `--style report` on a committed WF14 report (tpl class present, fenced scores correctly undecorated), `--style roadmap` campaign-log re-render, exemplar byte-reproduction as a committed test [confirmed]

## Dimension scores

1. **Step fidelity 5/5** — full-spine marker set complete; skips quoted (whole-issue delegation "not-enabled", Step 10 "mempalace disconnected", Step 15 "no deployment target").
2. **Gate value 5/5** — named catches with quoted text: Step 4 pass 1 High "Escaping user text does not make regex replacement over rendered HTML structurally safe" (→ inline-stage decorator redesign); High "A required surface change is explicitly excluded from the only stated delivery vehicle" (→ workspace-surface reframe); 8a T2 "the prohibition 'MUST NOT' is rendered as the positive requirement 'MUST'" (→ placeholder-bridging fix); Step 11 adversarial "malformed config `{"projects": 1}` raises TypeError" (→ confirmed live, guarded both functions).
3. **Prose clarity 4/5** — (a) Step 4 item 7 says dispatch `/rawgentic:adversarial-review <design-doc-path>`; the run invoked the underlying lib (`adversarial_review_lib.py review --type design`) directly — worked, but is an improvisation the prose doesn't sanction or forbid; (b) the findings-json sidecar's project-root containment surfaced as a runtime rejection ("sidecar path escapes project root") rather than prose — documented for the Step 11 diff path, absent for design/plan usage.
4. **Dispatch reliability 5/5** — 15/15 first-try ok, named agents, worktree-isolated, models as routed (opus ceiling for complex_feature tasks per `select_impl_model`, opus reviewers, sonnet analysis); zero dead returns.
5. **Telemetry honesty 4/5** — audit below: 8 match, 1 known-limitation, 0 mismatch. Gate counting followed the #340 unique-across-passes rule (Step 4: 21 unique across two passes; Step 11: 7 unique after cross-reviewer dup-merge) with the derivation recorded in notes at gate close.
6. **Cost sanity 3/5 (capped)** — full spine was the right call (renderer change affects every artifact; the design loop-back alone justified the panel). Heaviest-least-value: the two collided suite runs (~13 min) plus their diagnosis — orchestrator-owned (see F1). Cap: session-cumulative usage.

## Telemetry audit

| field | recorded | session evidence | verdict | impact | routing |
|---|---|---|---|---|---|
| tests | 57/2611/2612 | runner tails; +53 net +2 step-11 regression tests | match | — | — |
| gates[] step 4 | 21/21 inline | 3 self + 9 adv (pass 1) + 2 self + 7 adv (pass 2), all terminal (applied/dispositioned/refuted) | match | — | — |
| gates[] step 6 | 6/6 inline | 6 adversarial Medium dispositioned as plan amendments | match | — | — |
| gates[] step 8a | 3/3 hand_rolled_multi | review log: T1 1 Low applied, T2 2 Low applied | match | — | — |
| gates[] step 11 | 7/7 hand_rolled_multi | 9 raw − 2 cross-source dups = 7 unique; 5 applied + 1 refuted + 1 band-dropped (terminal per #340) | match | — | — |
| loop_backs | 1/3 | counters file quoted | match | — | — |
| outcome | PR 368 merged, ci passed | gh outputs | match | — | — |
| security_scan | ran 0/0 skipped iac+sca | scan JSON | match | — | — |
| reviewer_kind | inline/inline/hrm/inline/hrm | gate-defining mechanisms (#340 precedence; adversarial layers additive) | match | — | — |
| dispatches[] | 15 | 15 flush-left lines, canonical-regex validated at assembly | match | — | — |
| usage | 146.8M/399k/$58.98 captured | session-cumulative across 4 runs | known-limitation | per-run cost unattributable | standing |

## Findings and classification

- **F1 · ORCHESTRATOR ERROR (owned)** — during the 8a-fix verification the orchestrator backgrounded a `pytest | grep FAILED` run and then started foreground suite runs; the overlap produced two wobbly results (3-failed, 7-failed) and ~13 min of waste before the one-oracle-at-a-time rule (workspace CLAUDE.md) was honored and solo runs restored green. The prose did not invite it; the workspace manual explicitly forbids it. Own it.
- **F2 · PLUGIN FRICTION → FILED #370** — the ambiguity breaker's mechanical `ambiguity_flag` trigger vs determinable internal-consistency findings under an autonomous campaign (10 flags this run, all resolved-and-logged; the judgment call should be prose).
- **F3 · PLUGIN FRICTION (report-only, below cap-worthiness)** — Step 4 item 7's adversarial dispatch names only the skill invocation; the lib path the run actually used (and the sidecar containment it hit) is undocumented for design/plan artifacts. Merged into #370's surface? No — distinct; preserved here (routing: not-filed, minor, would be swept by any Step 4 prose touch).
- **F4 · WORKING AS DESIGNED** — mid-run owner interleave (PR #366) handled outside WF2 without corrupting the run: baseline re-verified on the new base, stash-preserved store line. No machinery change needed.

## Routing

- Defects/enhancements filed: **#370** (labels enhancement, framework, wf-feedback).
- Telemetry improvements: none — all fields match.
- Friction memory: **mempalace unavailable — friction memory SKIPPED**.
- Artifact publish: deferred to the end-of-run aggregate review (owner directive).

```
WF RUN ASSESSMENT — WF2 @ v3.32.0 (issue #344, PR #368, lane full) · rubric v1
Fidelity 5/5 · Gates 5/5 · Clarity 4/5 · Dispatch 5/5 · Telemetry 4/5 · Cost 3/5
Mode: full · Record: claude_docs/.epic-333-scratch/record-344.json
Best catch: Step 11 adversarial — {"projects": 1} TypeError violating the never-raises contract, confirmed live, guarded both call sites
Worst friction: mechanical breaker trigger on determinable consistency flags under an autonomous run — filed #370
Telemetry verdicts: 10 match / 0 mismatch / 0 missing-in-record / 0 missing-in-session / 0 unverifiable / 1 known-limitation
Defects filed: #370 | Telemetry filed: none | Not filed (cap): 1 (F3, minor) | Friction memorized: SKIPPED (mempalace unavailable) | Clean run: no
Report: docs/reviews/run-feedback-wf2-344-2026-07-10.md · Artifact: deferred to end-of-run aggregate (owner directive)
Inferred (unconfirmed) claims: none
```
