# WF14 aggregate — saystory M1+M2 overnight campaign (9 WF2 runs, 2026-07-10 → 07-11)

One roll-up of the nine per-issue WF14 assessments from the saystory M1–M5 overnight
run's first two milestones (epics #171 M1, #172 M2). Individual reports (committed
alongside this aggregate) are authoritative for per-run evidence; this document is
the cross-run view: score trends, systemic patterns, and what got routed where.
Rubric v2 throughout (M1 reports predate the fixed summary block; scores extracted
from their dimension sections). All adversarial/WF14 layers ran on gpt-5.6-sol per
the owner directive of 2026-07-10.

## Scoreboard

| Run | Lane | PRs | Fidelity | Gates | Clarity | Dispatch | Telemetry | Cost |
|---|---|---|---|---|---|---|---|---|
| #164 runbook reconcile (M1) | trivial-D1 | #177 | 5 | unscored | 5 | unscored | 3 | 3 |
| #165 dispatch extraction (M1) | full | #178 | 4 | 5 | 3 | 3 | 3 | 3 |
| #69 paste-spacing port (M1) | small-standard | #179 | 4 | 5 | 4 | 4 | 3 | 3 |
| #157 Parrot text (M1) | trivial-D1 | #180 | 5 | unscored | 5 | unscored | 3 | 3 |
| #166 P2 mac skeleton (M2) | full | #181 | 5 | 5 | 4 | 5 | 3 | 3 |
| #167 P3 platform adapter (M2) | full ×2PR | #182+#183 | 4 | 4 | 3 | 3 | 3 | 2 |
| #168 P4 model pipeline (M2) | full ×2PR | #186+#189 | 3 | 5 | 4 | 4 | 2 | 3 |
| #156 input-monitoring recovery (M2) | small-standard | #188 | 4 | 5 | 4 | 5 | 2 | 4 |
| #162 cleanup-stage feedback (M2) | full | #190 | 4 | 5 | 5 | 5 | 3 | 4 |

Campaign outcome: 9/9 issues merged (11 PRs), v0.2.39 → v0.2.48, zero regressions
against recorded baselines at every merge, both epics' code scope complete
(owner-gated remainders: M2 UAT round, #154, #163).

## The five cross-run patterns

**1. Gates score 5/5 whenever scoreable — and the catches were real, not ceremony.**
Every scoreable run names at least one pre-merge catch with the finding text quoted.
The campaign's three best: (a) #168 design pass-2 H4 — the double-llama test-bundle
link hazard, which produced the R6 symbol-localization mechanism; (b) #162 Step-11
adversarial — a Critical in the DESIGN'S OWN stage sequencing ("running" emitted
before the load it labels), caught after two 8a lenses had verified the faithful
implementation as clean: the strongest specimen yet that the adversarial layer
catches the spec where compliance lenses structurally cannot; (c) #156 design F0 —
the recovery notice would have rendered inside a hidden window; `notifyOs`
surfacing became the fix's actual user value.

**2. The mac-preflight rule flipped CI economics mid-campaign.** #167 took 4 mac-lane
fix loops and #168 PR-A took 6 (each loop 15–40 min) — all authored-blind Swift/ld
details a target-host run would have caught. After the owner's challenge (~07:30Z
2026-07-11), the rule "full build-macos-ffi.sh + swift test on the mac host before
ANY mac-lane push" went live: #168 PR-B, #156-adjacent runs, and #162 went
**3-for-3 mac-lane-green on the first CI attempt**. Cost scores rise accordingly
(#167 $2 → #162 $4). Promotion of this rule into WF2 prose is the campaign's top
process recommendation (routed: #391-class checklist thread; also named in the #168
report's finding 3).

**3. Telemetry is the chronically weakest dimension (2–3, never higher).** Three
independent causes, all routed: (a) usage capture unavailable — standing
known-limitation, #329/#330; (b) orchestrator schema stumbles when assembling
records by hand (#168: `type` vs `subagent_type`; #156: invented reviewer_kind
enum) — the validator caught both loudly, the read-the-schema-first lesson landed
by #162 (first-summarize valid); (c) gate-count semantics under the #340 rule need
per-pass bookkeeping discipline (one in-store correction, #168). No new telemetry
issues filed — every negative was either already tracked or assessor-caused.

**4. The adversarial disposition ledger (#393) stays the top plugin friction —
with new nuance.** Re-litigation cost: #167 PR-A 3/4 findings, #168 PR-A 3/4 and
PR-B 2/4 declined against settled evidence; but #162's two engine passes produced
ONLY fresh findings (including the campaign's best catch). The fix must inject
dispositions without dampening fresh-finding aggression — the #162 data point is
the design constraint. Recurrence ≥4 runs; friction memory saved
(`wf2-adversarial-needs-disposition-ledger-168`, mempalace).

**5. Cross-project WF14 mechanics still need the #372 re-bind dance.** Every report
write into the rawgentic tree from the saystory-bound session required the
documented registry re-bind workaround (3× this session). #372 remains open; the
cost is small but per-report.

## Environment ledger (not machinery, named for completeness)

Two same-day disk-full incidents (mac host at 276MiB free — owner freed 137G; WSL
root 100% — spent worktree targets + a 13G incremental cache reclaimed), one
usage-window stall recovered by the tick machinery (#69, M1), reviewer
session-limit deaths with re-dispatch (#167), `gh pr edit` projectCards failure
(PATCH workaround), stale mergeable races after fresh pushes. All carried in the
workspace env-gotcha notes.

## Routed (cumulative, deduplicated)

- Filed during M1: rawgentic#371 (worktree spawn layout), rawgentic#372 (WF14
  path-binding vs bind-guard). Linked during M2: rawgentic#393 (disposition
  ledger — recurrence evidence added, not refiled).
- Friction memories: M1 reviewer-dead-threshold; M2 disposition-ledger
  (`drawer_rawgentic_process-conventions_d151e29eadde94aa69115328`).
- Process recommendation for WF2 prose (not yet filed, #391-class thread): the
  mac-preflight/target-host proxy rule (pattern 2) and the Step-9-folds-into-
  Step-11 marker question (3 occurrences).
- Telemetry: nothing new filed — #329/#330/#333 cover the field.

## Owner-gated remainders (unchanged by this aggregate)

M2 exit UAT round (install, re-grant, hotkey→dictation→paste through the rust
core, #156 recovery UI, #162 stage pill), #154 (mic prompt, pre-milestone tccutil
repro is the owner's), #163 (Developer-ID cert, Apple-side).
