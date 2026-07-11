# WF14 run-feedback — WF2 #162 (saystory, standard lane, PR #190)

Assessed under rubric v2 (2026-07-10, #377). Plugin version loaded: 3.35.0 (cache).
Record: explicit (`claude_docs/.rawgentic-162-record.json`), persisted to the saystory
store (25th line) — **schema-valid on FIRST summarize** (the session's two earlier
schema stumbles didn't recur). Adversarial layers used gpt-5.6-sol per owner directive.

## At a glance

**The run where the layered-gate design proved itself: two independent 8a lenses
verified a faithful implementation of a design whose stage sequencing was itself
wrong, and the Step-11 adversarial diff caught that Critical (plus a real cache
race) before any push — zero CI fix loops, mac lane green first try, and every gate
artifact carried its evidence.**

- **Fidelity 4/5** — full-spine steps marked (1-5 incl. gated design, 8, 8a, 11,
  11.5, 12, 14, 16); Step 9 again folded into the 8a whole-diff task-mapping rather
  than marked separately (same pattern as #168 PR-B — see friction lineage).
- **Gates 5/5** — the assessment's centerpiece, named below.
- **Clarity 5/5** — zero improvisation this run: every engine/tool invocation ran
  as learned earlier in the session (sidecar path in-project, rc read unpiped,
  schema enums from the doc).
- **Dispatch 5/5** — 3 dispatches (1 implementer worktree, 2 reviewers), all opus
  as routed, non-vacuous, first try; implementer's flagged doubt (is hotkey_worker
  mac's live path?) retired by the orchestrator with a code citation
  (socket.rs:169) instead of being trusted or ignored.
- **Telemetry 3/5** — valid first summarize; usage still session-level
  (known-limitation); tests.added assembled from two suites (stated).
- **Cost 4/5** — proportional: one implementer + 2 reviewers + 2 engine passes for
  a 700-line cross-crate change; heaviest-least-value step: the second full
  workspace suite run between 8a-fixes and adversarial-fixes (defensible, but the
  intermediate run proved surfaces the final run re-proved 20 minutes later).

**Best catch:** Step-11 adversarial (gpt-5.6-sol), Critical — "On a cold cache miss,
`loading-model` is immediately followed by `running` before `cleanup()` begins.
Because `cleanup()` itself performs the model load, the overlay switches to
'Cleaning up…' for the entire slow load" — a flaw in the DESIGN'S OWN sequencing
(D-162-3 as written), faithfully implemented and passed by both 8a lenses. Fixed by
warming explicitly between the stages (the trait surface already existed). This is
the strongest same-session evidence yet that the adversarial layer catches what
compliance-oriented lenses structurally cannot: the spec itself.
Runner-up (High): `SingleSlotModelCache` two-key insert race (both platforms) —
red-proven reentrancy test now pins the RAM invariant.

**Worst friction:** none new — the recurring items (Step-9 marker absorption;
adversarial disposition ledger #393) both reappeared in known shapes; #393's
re-litigation cost this run was ZERO findings (both engine passes produced only
fresh findings), a favorable data point for the pending fix's priority calculus.

**Routed:** 0 defects filed; no new friction memory (both patterns already carried:
#393 + this session's #168 memory); orchestrator-error note below.

## Evidence ledger (selected)

- Design gate 1 pass, 5 findings dispositioned (4 adopted incl. the send-failure
  safety net that Step 11 later CORRECTED in mechanism — the sidecar window-hide,
  not a frontend re-render — an honest two-stage refinement, both recorded) —
  **confirmed** (design-note dispositions + markers).
- T1 red-first: taps disabled → 3 sequencing tests red; cache race: retain
  disabled → panic "two models were resident" — **confirmed** (runner outputs read
  in-session).
- Gates: ws 416/0 (+9), linux 70/0 (+4), typecheck 0, drift-guard 1/1, scan PASS,
  mac preflight 84/1/0 ×2 — **confirmed** (final runner outputs).
- PR #190 merged 50bf91b; mac lane green FIRST CI run (preflight rule now 3-for-3
  since adoption); audit clean (#162 closed, #154/#172 untouched) — **confirmed**.
- ORCHESTRATOR ERROR (owned): during the red-proof, `git checkout --` restored the
  committed file and silently wiped the UNCOMMITTED fix+test (reapplied from
  context). Lesson: red-prove by sed-out/sed-back, never a checkout restore over
  uncommitted work. No plugin prose invited it.

## Telemetry audit

| field | recorded | session evidence | verdict |
|---|---|---|---|
| workflow/issue/lane | implement-feature / 162 / full | matches | match |
| tests | 13 added / 486 passing | ws 416 + linux 70 (two suites, stated) | match (assembly method stated) |
| gates[] | 5/5, 2/2, 2/2 | dispositions + markers | match |
| loop_backs | 0/3 | none consumed | match |
| outcome | PR 190 merged, ci passed | merge SHA 50bf91b | match |
| security_scan | ran, iac skip | scan output | match |
| usage | unavailable | session-level | known-limitation |
| dispatches[] | 3 | agent results | match (first-summarize valid) |

Verdicts: match 7 · known-limitation 1 · mismatch 0.

## Findings and classes

1. **WORKING AS DESIGNED (highlight)** — the redundant-lens architecture: 8a
   verifies implementation-vs-spec, adversarial attacks the spec. #162 is the
   clean specimen: both 8a lenses CLEAN on code that faithfully implemented a
   wrong sequence; the adversarial pass alone caught it. Feed to #393's issue as
   supporting evidence that the disposition ledger must NOT dampen fresh-finding
   aggression.
2. **PLUGIN FRICTION (recurrent, low)** — Step 9's identity keeps dissolving into
   Step 11's whole-diff task-mapping on multi-task runs (3rd occurrence: #168
   PR-B, #162). Either the prose should bless the fold (Step 11 subsumes 9 when
   the same reviewer maps the whole diff) or demand the marker. Below filing
   threshold individually; noted for the #391-class checklist thread.
3. **ORCHESTRATOR ERROR** — the checkout-over-uncommitted-work slip (owned,
   recovered, lesson recorded).

## Summary block

```
WF RUN ASSESSMENT — WF2 @ v3.35.0 (issue #162, PR #190, lane full) · rubric v2
Fidelity 4/5 · Gates 5/5 · Clarity 5/5 · Dispatch 5/5 · Telemetry 3/5 · Cost 4/5
Mode: full · Record: claude_docs/.rawgentic-162-record.json (persisted, first-summarize valid)
Best catch: adversarial Critical — the design's own stage sequencing billed the load to "Cleaning up…"
Worst friction: none new (known lineages: Step-9 fold, #393 disposition ledger)
Telemetry verdicts: match 7 / mismatch 0 / missing-in-record 0 / missing-in-session 0 / unverifiable 0 / known-limitation 1
Defects filed: none | Telemetry filed: none | Not filed (cap): 0 | Friction memorized: none (covered) | Clean run: yes (machinery)
Report: projects/rawgentic/docs/reviews/run-feedback-wf2-162-2026-07-11.md
Inferred (unconfirmed) claims: none
```
