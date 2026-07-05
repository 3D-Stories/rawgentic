# Rawgentic modernization — campaign log

Rolling design-artifact log for the autonomous workflow-modernization campaign
(dogfood: rawgentic builds rawgentic). One section per implemented slot, updated
in that slot's PR (shared-doc mode). The hand-curated program dashboard lives
separately at `docs/planning/2026-07-04-workflow-modernization-review.html`; this
log is the per-slot artifact the WF2 Step-12 lifecycle renders with embedded
run-record telemetry.

Milestones: **M1** instrument+guard (done) · **M2** enable+restructure (done) ·
**M3** multi-issue autonomy + v3.0.0 (in progress) · **M4** headless.

---

## Slot 10 — #148 + #163: multi-issue driver (M3 start) · v2.64.0

**Issues.** #148 (build the multi-issue driver as a documented pattern + queue
state schema, from design #134) and #163 (dependency-DAG + epic anchor,
schema v2) — implemented together in one PR (#163 extends #148's queue).

**What shipped.**
- `docs/multi-issue-driver.md` — the documented driver **pattern**: the loop
  (WF2 fresh per issue; advance on merge / `pr_open` when headless; park on
  DEFER), policy (order / deploy / review-budget / never-Haiku), the DEFER
  taxonomy + deterministic branch-preservation rule, the rollback-anchor
  protocol, the dependency-DAG ordering, the epic anchor, and the resumption
  reconciliation table (intra-WF2 resume delegated to `resume_lib`). Explicitly
  does **not** weaken WF2 — each iteration is a full run terminating at Step 16.
- `hooks/driver_lib.py` — the narrow, unit-tested DAG surface: `parse_depends_on`
  (word-boundary, negation-aware, sentence-bounded), `topo_sort_issues` (Kahn,
  fail-closed on cycle), `next_ready_issue` (deps-satisfied advance rule +
  `deps_satisfied_by` knob), `validate_driver_state` (v1/v2 readability +
  serial-active invariant), `validate_campaign_start` (headless-requires-epic).
  The fuller state-transition validator stays deferred (design #134 follow-up #2).
- `docs/driver-state/` — the git-tracked schema (`queue.schema.json`) + v1/v2
  example campaign files (live per-campaign state is disk-persisted under the
  gitignored `claude_docs/.driver-state/`).

**Decisions (this slot).**
- #163 DAG fork (Codex-consulted → option C): #148 stays pure-doc; #163 ships the
  *narrow* DAG helper only. Its algorithmic ACs ("cycles halt fail-closed",
  "0 ordering violations", "v1 readable") can't be verified as prose.
- "Committed" queue state reconciled with the gitignored `claude_docs/`: the
  live state file is *durably persisted to disk* (the resumption substrate); the
  git-tracked contract (schema + examples) lives in `docs/driver-state/`.

**Reviews.** Per-task 8a (2 reviewers) hardened `parse_depends_on` + fail-closed
number guards. Step-11 Codex diff review (owner-directed) applied 4 findings
(sentence-boundary parse, serial-active invariant, `validate_campaign_start`,
persist-topo-order-in-doc); concurrent Claude review reworded the overstated
"prompt-injection-safe" claim to an honest best-effort filter. Security scan
clean.

**Status.** PR + CI + merge SHA filled by the next slot's pass (established
convention). Telemetry for this slot is embedded below.
