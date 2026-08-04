## Step 15: Quality Gate — Post-Deploy Verification (Conditional)

### Instructions

**If `capabilities.has_deploy == false` AND no deployment was performed:** Skip with note "No deployment target — verification deferred to manual testing."

The `<early-smoke-install>` early boot check (Step 8, deploy-bearing projects) is additional
to this step and never substitutes for it — this post-deploy verification runs in full
regardless of what the early smoke showed.

**If deployment was performed:**

Apply the quality-bar rubric (`references/quality-bar.md`) over check dimensions adapted to what was deployed:

- **Health check verification:** For each affected service, verify it responds correctly. Generate health check commands from the implementation context (not hardcoded URLs).
- **Acceptance criteria spot-check:** For each criterion, verify evidence of correct behavior using the verification commands from the plan.
- **Regression check:** Did any existing functionality break?

Apply ambiguity circuit breaker.

### Output
Post-deploy verification result (or skip confirmation).

### Failure Modes
- Health checks fail -> inspect logs, restart services
- Acceptance criteria not verifiable -> flag as test gap, verify manually if possible

---

