# Iteration-3 Analyst Notes — the decisive test

## Question
Iteration-2 showed the skill adds ~no value on simple issues. The open question:
does it earn its cost on the HARD cases it was actually built for — false premises,
vague requests, over-broad scope? This iteration tests exactly those, with a
**low-information, uncooperative user** so the agent-as-user can't rescue a run by
handing over the missing information.

## Result: no delta, even on the hard cases
| Scenario | with-skill | baseline | discriminated? |
|---|---|---|---|
| false-premise (`ConnectionThrottler` doesn't exist) | 100% | 100% | **no** |
| vague ("app feels slow sometimes") | 100% | 100% | **no** |
| over-broad ("overhaul everything") | 100% | 100% | **no** |

The bare model, on its own:
- **false-premise** — ran `grep`/`find`, discovered there is no `ConnectionThrottler`
  / `src/throttle.js`, documented the misnomer, and filed against the real
  `handleConnection` in `src/server.js`. Same outcome as the skill.
- **vague** — filed an *investigation* issue with NO invented latency numbers,
  explicitly deferring targets until data exists. Same as the skill.
- **over-broad** — scoped it as one epic with explicit in/out-of-scope boundaries
  and a split-into-deliverables plan. Same as the skill.

These are precisely the behaviors the WF1 critique + circuit breaker were meant to
add. A current-generation model already does them unprompted.

## Cost (unchanged conclusion, now starker)
| | with-skill | baseline | delta |
|---|---|---|---|
| Time | 286.4s ± 52.7s | 105.7s ± 11.3s | **+180.6s (~2.7×)** |
| Tokens | 70,400 ± 1,887 | 42,490 ± 741 | **+27,911 (~66%)** |

## Bottom line
Across iteration-2 (easy) and iteration-3 (hard), the skill produced **no
measurable quality delta on any of 7 scenarios** while costing ~2.3–2.7× the time
and ~55–66% more tokens. The one thing it did differently — conventional
`feat(scope):` titles — is a convention the project owner questioned the value of.

The WF1 machinery (3-judge critique, ambiguity circuit breaker, codebase
verification, conditional memorization) was designed against a weaker model that
needed scaffolding to avoid hallucinating components, fabricating acceptance
criteria, or rubber-stamping over-broad asks. The current model doesn't need that
scaffolding for issue creation.

## Caveats (where this test is NOT the last word)
- **Single run per cell** (session-budget limits), not 3 — variance unmeasured.
- **One reviewer, synthetic fixture, two-stub-file project.** A real, large codebase
  with genuine architectural traps might still surface skill value.
- **Fleet consistency** (every issue emitted in an identical machine-readable shape
  so downstream WF2 can rely on a fixed contract) is a real argument the per-issue
  quality metric cannot capture — but it's an argument for a *lightweight template
  enforcer*, not a 2.7×-cost multi-judge workflow.
- The skill's value may live in genuinely **novel/ambiguous domain decisions** a
  generalist model gets subtly wrong — not exercised here.

## Recommendation
For issue creation with a current model: the heavy WF1 critique pipeline is not
paying for itself. Options, cheapest first:
1. **Slim to a template+dedup+config-targeting helper** — keep the parts that give
   structure/consistency, drop the 3-judge critique + loop-back. ~Baseline cost.
2. **Gate the heavy path** — only invoke critique when the requester flags the issue
   as high-stakes/architectural; default to the light path.
3. **Retire for issue creation**, keep the WF1 contract only where WF2 truly depends
   on it.
