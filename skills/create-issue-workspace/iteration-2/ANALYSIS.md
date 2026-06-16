# Iteration-2 Analyst Notes — rawgentic:create-issue

## Headline
Evals now **execute for real** (mock-gh liveness checks would fail any simulated
walkthrough — the exact failure iteration-1 could not detect) and **discriminate**
skill from baseline. With-skill = 100% pass on all 4 evals; baseline = 93% (varies
86–100% across evals).

## What actually discriminates (2 of 23 assertions)
| Assertion | with-skill | baseline |
|---|---|---|
| feature: Conventional title `feat(scope): …` | PASS | **FAIL** (`Add WebSocket support…`) |
| bug: Conventional title `fix(scope): …` | PASS | **FAIL** (`WebSocket connections dropped…`) |

Every other assertion (repo targeting, dedup-before-create, in/out scope, risk,
≥3 acceptance criteria, decoy-repo resolution, bug-template sections, `bug` label)
**passes in BOTH configs.**

## Why the gap is narrow (honest read)
A capable model already does most of WF1's job *when given the issue templates and
a structured config*:
- It reads `.github/ISSUE_TEMPLATE/*` and fills scope/risk/AC sections.
- It reasons that project `.rawgentic.json` outranks a root `CLAUDE.md` — even with
  a plausible, unlabelled decoy repo (the iteration-1 weakness is fixed, and the
  baseline still resolved it correctly).
- It runs a dedup search and surfaces the existing #42 instead of duplicating.

So on these scenarios the skill's *measurable* edge is concentrated in conventional
commit-style titles. Its other value — the 3-judge critique catching subtle spec
defects, the ambiguity circuit breaker, conditional memorization — is real but
**not captured by programmatic assertions** and needs qualitative transcript review.

## Cost of the skill (the tradeoff)
| | with-skill | baseline | delta |
|---|---|---|---|
| Time | 245.2s ± 83.7s | 105.4s ± 14.1s | **+139.8s (~2.3×)** |
| Tokens | 65,776 ± 6,674 | 42,532 ± 1,343 | **+23,244 (~55%)** |

The skill is materially more expensive. For the common case (a clear, single issue),
that cost buys conventional titles + process rigor. The skill's own text already
hedges ("for simpler features or bug reports, proceed with inline brainstorming") —
the data supports leaning harder on that lightweight path.

## Harness caveats / next-iteration ideas
- Runs are **1 per (eval,config)** (session-limit constraint), not 3. ±stddev shown
  is variation *across the 4 evals*, not run-to-run. For variance analysis, re-run
  3× when budget allows.
- The Step-3 critique tells the agent to spawn 3 parallel judge subagents. Inside a
  subagent that is both very expensive and the likely cause of the original
  session-limit stalls; the reruns used inline critique. Worth deciding whether the
  skill should fall back to inline judges when subagent-spawning is unavailable.
- To capture the skill's real differentiators, future assertions could target:
  spec-defect catches the baseline misses, circuit-breaker firing on a genuinely
  ambiguous request, or out-of-scope discipline on a deliberately over-broad ask.
