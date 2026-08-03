Severity is not a mood. It is the ONLY trigger for loop-backs (`steps.md` Step 8a/11
triage and the `Loopback-class` rule), so an uncalibrated label spends real budget. Before
this block these four words had **no definition anywhere in this repo** (a grep of `skills/`
and `shared/` returned nothing), while Critical/High alone decided whether the workflow
looped. Every reviewer invented their own scale.

**The rubric. Judge IMPACT and PRECONDITIONS separately, then band.**

- **Critical** — a plausible catastrophic, system-wide, or security/integrity failure, with
  preconditions that occur in NORMAL operation, and no downstream control that catches it.
  All three. Data loss, a gate that silently passes what it exists to refuse, a credential
  leak. Blocks: must fix before Step 9.
- **High** — a major correctness or workflow-integrity failure, but bounded: it needs an
  unusual precondition, OR a downstream control detects it, OR it is recoverable once seen.
  **Deferrable with rationale** via `plan_lib.append_deferral` — re-presented at Step 11.
  A High is NOT an automatic blocker, and treating it as one is how a PR reaches round 13.
- **Medium** — real but contained: a wrong error message, a missing guard on a path with a
  working sibling, prose contradicting code without changing behaviour. Advisory.
- **Low** — cosmetic, stylistic, or speculative. Advisory.

**Worked calibration, from #840 round 13.** `observe_head` fetched without a refspec, so a
narrow `remote.origin.fetch` let a STALE sha pass as freshly observed — defeating the gate's
own freshness clause. Impact is catastrophic (the gate stops gating). But it requires a
non-default git configuration and had no observed production incidence, so it is **High, not
Critical**. Promote to Critical only if the configuration is common or the stale decision
triggers something irreversible with nothing else in the way.

**Severity is not confidence.** Report BOTH. `plan_lib.SEVERITY_BANDED_CONFIDENCE` drops a
finding whose confidence is below its band (Critical 0.50, High 0.65, Medium 0.80, Low 0.90)
— a filter that **cannot run if the brief never asks for a confidence score**, which is
exactly what happened across #840 rounds 4-13. Every review brief MUST request
`severity`, `confidence` (0.0-1.0), and a one-line precondition/impact rationale. A Critical
requires explicit disposition regardless of confidence; a low-confidence severe claim
triggers targeted verification, never an automatic fix.

**Brief hygiene — measured, not stylistic.** #840 round 13 ran three adversarial briefs
(19.5-19.8 KB, carrying an accumulated 13-round failure history) against three neutral ones
(3.6 KB) on the same commit, seat and lane. All six FAILed on the same real defects, so the
adversarial framing bought nothing — but the NEUTRAL arm found a Critical the adversarial
arm missed, because the bloated brief had told reviewers that clause was "stable for eight
rounds" and all three duly looked elsewhere. Therefore:

- **Never tell a reviewer what verdict to reach** ("do not approve this") and never state
  which areas are settled. Both suppress findings.
- **Never accumulate round history in a brief.** Carry the diff, the scope, the deferred
  list, and unresolved claims — never past conclusions. History grew these briefs 6.5 KB →
  19.8 KB across rounds for a strictly worse review.
- **Keep a review brief under ~8 KB.** If it will not fit, the change under review is too
  large to review in one pass — split it instead of enlarging the brief.
