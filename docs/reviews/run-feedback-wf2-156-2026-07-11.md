# WF14 run-feedback — WF2 #156 (saystory, small-standard lane, PR #188)

Assessed under rubric v2 (2026-07-10, #377). Plugin version loaded: 3.35.0 (cache).
Record: explicit (`claude_docs/.rawgentic-156-record.json`), persisted to the saystory
store (24th line) — schema-valid on SECOND summarize (reviewer_kind enum; see
telemetry). Adversarial layers used gpt-5.6-sol per owner directive.

## At a glance

**A clean, cheap, gate-productive small-lane run: the collapsed design note plus two
adversarial passes and one reviewer produced four adopted improvements on a 69-line
frontend fix, with zero CI loops on substance — the only churn was a predictable
cross-PR design-log merge conflict and two enum/schema stumbles by the orchestrator.**

- **Fidelity 4/5** — small-standard collapsed-steps lane; all executed steps
  marked (1, 2, 3-5 collapsed, 4-gate, 8-12, 14, 16); the collapse itself is the
  lane's documented shape.
- **Gates 5/5** — real catches, named below (notifyOs surfacing High; row-synthesis
  edge; once-per-session notification guard).
- **Clarity 4/5** — one interpreted contract: the engine rejects sidecar paths
  outside the project root (clear error, no prose warning; same as #168's run).
- **Dispatch 5/5** — one reviewer dispatch, opus as routed, non-vacuous, first try.
- **Telemetry 2/5** — record rejected on first summarize (`codex_adversarial` not in
  the reviewer_kind enum — the assessor invented a value instead of reading the
  documented set); validator loud and correct.
- **Cost 4/5** — lane choice fit the change (no full 3-agent design gate, no
  implementer dispatch — orchestrator-implemented inline); heaviest-least-value
  step: the second full workspace suite run after the merge-conflict resolution
  whose only rust delta was version numbers (defensible under the semantic-conflict
  rule, but it re-proved an unchanged surface).

**Best catch:** design-pass F0 (High, gpt-5.6-sol): "the only notification is
rendered inside the Settings section... hotkey failure can remain visibly silent" —
adopted as the `notifyOs` surfacing, which is the issue's actual user-facing value.
Runner-up: diff-pass row-synthesis (the protocol compat-shim omits the Input
Monitoring row while state is Unknown — the recovery row could vanish exactly when
needed).

**Worst friction:** reviewer_kind enum invention (orchestrator error — the enum is
documented in run-record.md; second occurrence of a schema stumble in one session
after #168's `subagent_type`). Pattern noted in the friction memory lineage, not
refiled.

**Routed:** 0 defects filed; no new friction memory (the #168 memory of this session
already carries the disposition-ledger lesson; the schema stumbles are orchestrator
errors, not plugin faults — the validator worked both times); clean-run line below.

## Evidence ledger (selected)

- Steps 1-2 markers (previous leg) + Steps 3-5 collapsed note + 4-gate dispositions
  + 8-12 markers + merge/audit — **confirmed** (session_notes.md markers, quoted in
  the run's section).
- Design adversarial r1: 4 findings, F0/F3 adopted, F1/F2 declined-with-evidence —
  **confirmed** (dispositions in claude_docs/wf2-156-design-note.md + marker).
- Step 11: opus reviewer 0 C/H/M + 2 Lows (1 adopted, 1 declined-deliberate); engine
  diff pass 3 findings (1 adopted, 2 declined with proofs) — **confirmed** (agent
  result + findings JSON).
- Gates on merge: typecheck 0, ws 407/0 twice (pre-PR and post-conflict-resolution),
  drift-guard 1/1, scan PASS — **confirmed** (runner outputs read).
- Merge held until #168 PR-B for version monotonicity; design-log append-append
  conflict resolved keep-both; stale DIRTY mergeable race observed then cleared —
  **confirmed**.

## Telemetry audit

| field | recorded | session evidence | verdict |
|---|---|---|---|
| workflow/issue/lane | implement-feature / 156 / small-standard | matches | match |
| tests | 0 added / 407 passing | typecheck-only frontend change; ws 407/0 runner output | match (no JS harness exists — known repo shape) |
| gates[] | 4/4, 0/0, 5/5 | dispositions + markers | match |
| loop_backs | 0/3 | no loop-back consumed | match |
| outcome | PR 188 merged, ci passed | merge SHA 5d24fab | match |
| security_scan | ran, iac skip | scan output | match |
| usage | unavailable | session-level only | known-limitation |
| dispatches[] | 1 reviewer | agent result | match; first summarize rejected reviewer_kind enum (orchestrator-invented value) |

Verdicts: match 7 · known-limitation 1 · mismatch 0.

## Findings and classes

1. **ORCHESTRATOR ERROR** — invented `codex_adversarial` reviewer_kind (documented
   enum: builtin_code_review/codex/hand_rolled_multi/inline/reflexion). Validator
   caught it. Second schema stumble this session — the read-the-schema-first lesson
   is the orchestrator's, not the plugin's.
2. **ENVIRONMENT/GH** — `gh pr edit` fails on the projectCards GraphQL deprecation
   (body edits must go through `gh api PATCH`); and mergeable/mergeState reads race
   fresh pushes (a stale DIRTY after the conflict-resolution push). Both are gh/API
   behaviors; the workspace env-gotcha notes now carry them.
3. **WORKING AS DESIGNED** — merge-order hold for version monotonicity and the
   keep-both design-log conflict resolution followed the documented conventions
   exactly; the small-standard lane's collapsed steps kept cost proportional.

## Summary block

```
WF RUN ASSESSMENT — WF2 @ v3.35.0 (issue #156, PR #188, lane small-standard) · rubric v2
Fidelity 4/5 · Gates 5/5 · Clarity 4/5 · Dispatch 5/5 · Telemetry 2/5 · Cost 4/5
Mode: full · Record: claude_docs/.rawgentic-156-record.json (persisted)
Best catch: design F0 — silent hotkey failure needs notifyOs surfacing (adopted)
Worst friction: orchestrator-invented reviewer_kind enum value (own goal; validator correct)
Telemetry verdicts: match 7 / mismatch 0 / missing-in-record 0 / missing-in-session 0 / unverifiable 0 / known-limitation 1
Defects filed: none | Telemetry filed: none | Not filed (cap): 0 | Friction memorized: none (covered by this session's #168 memory) | Clean run: yes (machinery)
Report: projects/rawgentic/docs/reviews/run-feedback-wf2-156-2026-07-11.md
Inferred (unconfirmed) claims: none
```
