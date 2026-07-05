# Quality-bar rubric (in-repo self-review)

The in-repo replacement for the external reflexion `reflect`/`critique` skills
(#205). A quality gate that says "apply the quality-bar rubric" means: adopt the
stance below, evaluate the work product against the step's stated dimensions, and
emit findings in the shape below. No external plugin is invoked — this rubric is
the gate.

## Stance (non-negotiable)

You are a skeptical gatekeeper, not a cheerleader. Your job is to find fault
before it ships, not to affirm effort. Default to "this is not ready" and make the
work *earn* a pass. Specifically:

- **Cite evidence, not vibes.** Every finding names the concrete thing — a file,
  a missing test, an unhandled input, an AC with no covering check. "Looks fine"
  is not a review.
- **Do not rubber-stamp.** If you produced zero findings, that is a red flag about
  *your* review, not a gold star for the work — re-read the hardest part and the
  boundaries before declaring it clean.
- **Resist the biases that make a lenient judge:** length/effort ("they worked
  hard"), completion ("it's done, so it's good"), and confident tone ("it sounds
  right"). None of those are evidence.
- **Stay in scope.** Judge the work against *this issue's* acceptance criteria and
  the project's conventions — not a wishlist. Flag genuine gaps; don't gold-plate.

## Depth triage (match effort to blast radius)

- **Quick pass** — single-file edits, docs, a one-line fix: run the step's
  dimensions as a fast checklist, emit any findings, done.
- **Standard pass** — multi-file change, a new feature, a real behavior change:
  work every dimension deliberately; check the edge cases and the failure paths,
  not just the happy path.
- **Deep pass** — security-sensitive, data-lossy, or core-architecture changes:
  standard pass plus an explicit "how does this break?" round — name the most
  likely failure and confirm the design defends it.

## Finding shape

Emit each finding as:

```
Finding #N:
- Severity: Critical | High | Medium | Low
- Category: architecture | completeness | security | testability | scope_fidelity | migration_safety | performance
- Description: [the concrete defect — what, and where]
- Recommendation: [the specific action that resolves it]
- Ambiguity flag: clear | ambiguous
- Ambiguity reason: [why, if ambiguous]
```

The gate that invokes this rubric owns what happens next (volume thresholds, the
ambiguity circuit breaker, loop-back budgets) — those live in the step, not here.
This file supplies only the stance, the depth triage, and the finding shape.
