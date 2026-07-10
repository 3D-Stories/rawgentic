# Epic #333 — WF14 aggregate review · 9 assessed WF2 runs · rubric v1

## At a glance

**Across the epic's ten shipped children (nine WF14-assessed runs incl. the #337 precursor), the workflow machinery held its floor everywhere — fidelity never dropped below 4/5, no gate was ever skipped silently, and zero dead dispatches were ever trusted — while the epic measurably fixed its own worst telemetry problems mid-flight: the dispatch audit trail went from unmeasurable (#329) to a 15/15 field-match (#344), and gate counting went from an eyeballed number (#337) to a rule every later record audits against.**

- **Step fidelity — 5,5,5,5,5,5,5,4,5** — the floor never cracked; the one 4 (#332) is an owned convention bend (deferred campaign-log slot), logged at the time and repaired one PR later
- **Gate value — 3,5,5,5,5,4,5,3,3** — every full-spine or contested run produced named, quoted, pre-merge catches; the 3s are honest "nothing to catch" docs/schema runs, not ceremony
- **Prose clarity — 3,3,4,4,5,4,4,5,5** — a visible upward trend: the early interpretation costs (probe body parse, sidecar containment, feasibility line shape) stopped recurring once learned, and two became filed/fixed prose
- **Dispatch reliability — 5,3,5,3,5,4,5,5,5** — 79 total dispatches across the epic; every sub-5 score is session-limit environment deaths, all detected, none trusted, all visibly re-dispatched
- **Telemetry honesty — 4,3,4,2,3,4,4,4,4** — the #340 2/5 (dispatches[] mismatch + duplicate store line) is the epic's only recorded mismatch, and it produced the #340/#355 fixes that made every later record audit clean
- **Cost sanity — 4,3,4,4,4,3,3,3,3** — capped at ≤4 epic-wide by the standing session-level usage attribution; lane election was correct on all six eligible children

**Best catch of the epic:** #344's Step 11 adversarial diff review — a syntactically valid `{"projects": 1}` config raising `TypeError` in violation of `design_artifact_style`'s never-raises contract — confirmed live, guarded at both call sites, regression-tested. Runner-up: #330's pass-1 design review proving the assembly design's scoping anchor "matches NOTHING in the notes corpus" before any code existed.

**Worst systemic friction (now filed):** the ambiguity circuit breaker's mechanical `ambiguity_flag` trigger vs determinable internal-consistency findings under autonomous campaigns — fired repeatedly on #344, resolved-and-logged each time, filed as **#370**.

**Routed across the epic:** issues #340, #341, #342, #343, #344 (all dogfood-found, ALL now shipped inside the same epic — the feedback loop closed on itself) · #355 (blind-append duplicate, open) · #356 (refuted with evidence, close recommended) · #370 (breaker semantics, new) · plus the #345/#346 telemetry improvements from checkpoint 1.

## Scorecard

| Run | Issue · PR | Version | Lane | Fid | Gate | Clar | Disp | Tel | Cost | Filed |
|---|---|---|---|---|---|---|---|---|---|---|
| pre-epic | #337 · #339 | 3.24.26 | full | 5 | 5 | 4 | 5 | 2 | 3 | #340 #341 #342 |
| CP1 | #329 · #347 | 3.26.0 | small-standard | 5 | 3 | 3 | 5 | 4 | 4 | none (dups) |
| CP1 | #330 · #349 | 3.27.0 | full | 5 | 5 | 3 | 3 | 3 | 3 | none (dups) |
| CP1 | #331 · #351 | 3.27.1 | small-standard | 5 | 5 | 4 | 5 | 4 | 4 | none |
| CP2 | #341 · #352 | 3.28.0 | small-standard | 5 | 5 | 4 | 4 | 3 | 3 | none |
| CP2 | #340 · #353 | 3.29.0 | small-standard | 5 | 5 | 4 | 3 | 2 | 4 | #355 #356 (concurrent session) |
| CP2 | #338 · #354 | 3.30.0 | small-standard | 5 | 5 | 5 | 5 | 3 | 4 | none |
| CP3 | #343 · #367 | 3.31.0 | small-standard | 5 | 4 | 4 | 4 | 4 | 3 | none |
| CP3 | #344 · #368 | 3.32.0 | full | 5 | 5 | 4 | 5 | 4 | 3 | #370 |
| CP3 | #332 · #369 | 3.32.1 | small-standard | 4 | 3 | 5 | 5 | 4 | 3 | none |
| CP4 | #342 · #373 | 3.32.2 | small-standard | 5 | 3 | 5 | 5 | 4 | 3 | none |

(11 rows: #337 was the dogfood precursor that spawned three of the epic's children; the epic's own ten children are #329–#332, #338, #340–#344.)

## The loop closed on itself

The epic's defining property: **its later children fixed what its earlier assessments found, and its later assessments then measured the fixes working.**

- #337's assessment found gate counting was "an eyeball" → **#340 shipped the counting rule** → every CP3/CP4 record's `gates[]` audits `match` against it.
- #337's assessment found markers mechanically un-attributable across concurrent runs → **#341 shipped issue-keyed slots** → CP3/CP4 attribution was mechanical despite four runs sharing one notes file.
- #337's report "rendered like a raw .md file" → **#343 shipped table rendering + the at-a-glance mandate** → this aggregate and all CP3/CP4 reports render their tables and open verdict-first.
- #343's structure half generalized → **#344 shipped the design language** → CP3/CP4 reports render with `--style report` (score chips live in the HTML of the very reports that assessed the feature).
- #337's F-3 rot list → **#342 fixed it** (with one already-fixed citation honestly delta'd).
- #329/#330 shipped `dispatches[]` → #340's assessment caught the first real `dispatches[]` mismatch (2 recorded vs 8 evidenced) → later records assemble from the canonical grep and audit 15/15.

## Systemic observations (cross-run, not per-run)

1. **The severity-banded confidence filter did its job both ways:** it dropped 10+ sub-band findings across the epic without losing anything real (band-dropped items that mattered — the changelog count, ragged-row parity — were still fixed as cheap advisories or recorded as follow-ups), and it never suppressed a Critical/High.
2. **Environment, not machinery, caused every dispatch death:** session-limit kills (#330: 4, #340: 1) and one 529 (#343). Detection worked every time; the #331-shipped dead-return rule was exercised live in the same epic that wrote it.
3. **The standing telemetry ceiling is usage attribution** — session-cumulative tokens cap Cost at 3–4 on every run. Per-run attribution is the single highest-leverage telemetry improvement left (known-limitation in every audit; already on the #333 follow-up surface).
4. **Orchestrator errors were owned, not laundered:** the concurrent-pytest collision (#344), the deferred campaign-log slot (#332), the unnamed transient failure (#342) — each classified ORCHESTRATOR ERROR in its own report with the rule it violated named.
5. **Cross-model gates earned their cost exactly where the design said they would:** on the two full-spine runs (#330, #344) the adversarial passes forced design loop-backs that fixed High-severity flaws pre-code; on lane runs the collapsed ceremony lost nothing (no lane run later surfaced a design defect the panel would have caught).

## Open items for the owner

- **#355** (persist_record blind-append duplicates) — real, reproduced live; open.
- **#356** — refuted with evidence (off-by-one store read); close recommended, owner decides.
- **#370** — breaker semantics under autonomous campaigns (new, from #344's run).
- **runFeedback key** still enabled on NO project entry — the #338 wiring is live but dormant everywhere; enabling it on rawgentic is an owner config decision.
- **Falsification experiment** (route `implementation` to sonnet, re-measure delegation) — documented as open by #332; unrun.

Provenance: individual reports `docs/reviews/run-feedback-wf2-{337,329,330,331,341,340,338,343,344,332,342}-*.{md,html}` · records `claude_docs/.epic-333-scratch/record-*.json` + `docs/measurements/run_records.jsonl` · assessed under rubric v1 by the same orchestrator that ran the runs (bias stated in every report).
