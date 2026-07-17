<!-- VENDORED — bench #14 rubric (frozen v2 fixture). Source: rawgentic-next docs/measurements/model-bench/fixtures/v2-blueprint/rubrics/design.md @ commit 3adcf233c63b0a4a4b85f8e537f48c5215da8d8e. source-sha256: 42702a0512fa68d66000ec13e0735d46ac8f3deacb8522d9e78ad8f248e349e9. Do NOT edit here — update at source and re-vendor. This is a STRUCTURE-guarded copy; cross-repo freshness is not auto-detectable. -->

# Fixture v2 measurement spec — design

## Purpose and inputs

Measure whether `approved-design.md` actually designs the brief and approved
intake. The central question is not “does it look like an architecture doc?”
but “does every requirement map to a constraint-satisfying, implementable
design with defensible choices?” Evaluate only supplied artifacts and pinned
environment versions; no live-web currency scoring.

## Score and pass rule

Hard gates first. Completeness and quality are each 5 areas × 0–5 × 2 = 50;
total 100; pass requires every gate and ≥75. Anchors: 0 absent/harmful, 1 major
deficiency, 3 usable with important gaps, 5 complete and defensible; 2/4 are
intermediate.

## Scoring calibration — anchors are hard caps

- Score each area by FIRST counting defects, THEN applying the caps below. Do
  not score from overall impression.
- **Countable demotion rule (every 0–5 area):** before scoring an area, list
  its counted defects — each missing, vague, untestable, untraced, or
  unhandled element that the area's 5-anchor implies — each with a citation
  into the artifact. 0 defects → score 4; 1–2 defects → at most 3;
  3–4 defects → at most 2; 5 or more → at most 1.
- **5 is exceptional and rare.** It requires zero counted defects AND unique
  thinking: a quoted excerpt showing insight beyond competent compliance — a
  non-obvious risk surfaced, a tension resolved in a way a typical strong
  artifact would miss, or a decision whose absence only an expert would
  notice. If the reason does not QUOTE the exceptional element, award 4.
- 3 is the expected score for a strong, professional, complete area. A
  flawless-but-unexceptional artifact totals about 80, not 100.
- If a draft worksheet totals 90 or more, re-examine every 5 and demote any
  whose quoted justification is generic praise.

## Hard gates

| ID | Gate | Lane |
|---|---|---|
| D-G1 | Every MUST-v1 ID has a valid trace to a concrete design element and verification method | Mechanical coverage + judge unanimity |
| D-G2 | No brief invariant or approved acceptance criterion is contradicted | Mechanical predicates where possible + judge unanimity |
| D-G3 | The brief's primary authorization or offline-sync tension is fully resolved across data, state, API, UX, failure, and tests | Judge unanimity |
| D-G4 | No major implementation decision is deferred: exact pinned stack, data model, state/transaction model, API, repo, UX, tests, and local workflow are specified | Mechanical presence + judge unanimity |
| D-G5 | At least three plausible forks are evaluated; selection and rejections are grounded in the brief, with swap points | Mechanical count + judge unanimity |

## Completeness — 50 points

| Area | 1 anchor | 3 anchor | 5 anchor |
|---|---|---|---|
| Traceability | Requirements mostly untraced | All listed; several weak links | Every ID links design, invariant/contract, UI, and verification |
| Architecture and data | Generic stack/CRUD | Main layers and entities defined | Exact pins, full model/index/lifecycle/audit, repo and integration contracts |
| Safety and consistency | Hard tension hand-waved | Core rules stated | Actor/trust matrix, state machines, atomicity/idempotency/convergence/time invariants complete |
| API, failure, and recovery | Happy-path endpoints | Main contracts/errors | Concrete command/query contracts cover auth, validation, conflict, retry, stale, recovery |
| UX, NFR, tests, run | Screens or tests missing | Usable coverage | Complete routes/flows/states/tokens/a11y plus local run, observability, layered tests |

## Quality — 50 points

| Area | 1 anchor | 3 anchor | 5 anchor |
|---|---|---|---|
| Constraint satisfaction | Violates brief/invariant | Mostly fits; edge tension weak | All constraints jointly satisfied with downstream-detectable invariants |
| Fork quality | Arbitrary or trend-driven | Reasonable choice | Brief-specific tradeoff analysis selects simplest sufficient design |
| Failure reasoning | Assumes happy path | Handles common failures | Duplicate, race/offline, stale, crash, rollback, privacy, and audit failures cohere |
| Simplicity and maintainability | Enterprise excess or fragile shortcut | Conventional design | Local-first, testable boundaries, clear scale path without speculative machinery |
| Handoff evidence | Narrative/generalities | Some tables/examples | Concrete matrices, schemas, transitions, APIs, repo tree, tokens, and decisions remove guesswork |

## Worksheet

```text
DESIGN WORKSHEET — run/candidate/fixture: __________________
GATES: D-G1 [ ] D-G2 [ ] D-G3 [ ] D-G4 [ ] D-G5 [ ]
Requirement coverage: __ / __ MUST IDs
Constraint violations: ______________________________________

COMPLETENESS: Trace __ Arch/data __ Safety __ API/failure __ UX/NFR/test __ = __/50
QUALITY: Constraints __ Fork __ Failure __ Simplicity __ Handoff __ = __/50
TOTAL __/100   GATE VERDICT PASS/FAIL   PHASE PASS/FAIL

Chosen fork and evidence: ___________________________________
Rejected alternatives grounded? _____________________________
Evidence-cited rationale per area: ___________________________
```

## Calibration notes

Schema/headings alone approximate zero. A polished design that fails one
load-bearing invariant fails the phase. Do not penalize pinned versions for
training-knowledge staleness.
