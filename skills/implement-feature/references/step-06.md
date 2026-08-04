## Step 6: Quality Gate — Plan Drift Check

### Instructions

**Skip condition:** Step 6 is skipped when time-critical **or when running the
small-standard lane** (`small_standard_lane_eligible` — the checklist plan is small enough to
eyeball, and Step 9 still verifies acceptance-criteria coverage). Otherwise run it.

Apply the quality-bar rubric (`references/quality-bar.md`) over these check dimensions:
- **Design-plan alignment:** Does every design component map to at least one task?
- **Verification completeness:** Does every implementation task have a corresponding verification step?
- **Acceptance criteria coverage:** Does the plan, if executed, satisfy all acceptance criteria?
- **Task ordering validity:** Are dependencies correctly ordered?
- **Commit checkpoint adequacy:** Are checkpoints at logical boundaries?

Apply ambiguity circuit breaker on findings. If clear: apply automatically.

**Adversarial review sub-step (opt-in, cross-model).** After the self-review above, optionally run a cross-model adversarial review of the **implementation plan**. Gate on project opt-in only (Step 6 has no fast-path branch):
```bash
python3 hooks/adversarial_review_lib.py is-enabled \
  --workspace .rawgentic_workspace.json --project <name> --skill implement-feature
```
The command exits `0` when enabled and non-zero otherwise; if non-zero, **skip silently**. When enabled, write the plan to a temp file under the project and invoke `/rawgentic:adversarial-review <plan-path> plan`. On a pass-N dispatch, apply the Step 4 item 7 disposition-ledger fold (#393) here too — fold, join backstop, gate-close persistence; the plan review shares the issue's ledger. It is report-only; merge its findings (tagged `source: adversarial`) with the self-review findings and apply the circuit breaker over the **merged** list (do not run two separate breakers). At this gate's close, persist each Critical/High finding's terminal disposition via `plan_lib.append_disposition` (Step 4's gate-close persistence sentence is canonical). If the merged list contains one or more Critical/High design-level flaws, consume **exactly one** existing `design` loop-back counter and return to Step 3 once with the unified constraints. **Codex failure is non-blocking** (additive review): on any non-success — including an unmet prerequisite — skip the adversarial layer, log loudly, and continue with the self-review result; never ERROR or block WF2. Log: `### WF2 Step 6 — Adversarial Review (#<issue>, invoked|skipped): <report path or skip reason>`.

### Output
Plan drift check result.

### Failure Modes
- Significant drift detected -> add missing tasks
- Scope creep detected -> remove excess tasks or flag for user decision.

---

