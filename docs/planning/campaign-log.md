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

## Slot 11 — #162: review switch — ABANDONED per AC4 data gate · v2.64.2

**Issue.** #162 (Step 4 reflect-only + Step 11 built-in /code-review + WF5 diff
pass) was **data-gated** by its own header ("no A/B evidence, no switch") and
AC4 ("matched hand-rolled yield over ≥10 runs at lower cost … otherwise abandon
this issue with the data cited").

**The data (23 run-records as of 2026-07-04).** The candidate arm
(`builtin_code_review`) has **0 gate-instances** — it never ran (≥10 required).
Token/cost telemetry (`usage.input_tokens`/`output_tokens`/`cost_estimate_usd`)
is **null in all 23 records**, so the success metric (findings-yield per token)
is incomputable for *any* arm. Incumbent arms: hand_rolled_multi 41 findings/11
gates · codex 16/4 · inline 19/13.

**Decision.** Abandon per AC4's explicit branch — a **deferral pending
telemetry, not a rejection** of built-in `/code-review` (Codex peer-consult
concurred). The roadmap's "the program is its own A/B" assumption was circular:
campaign runs could only generate candidate-arm data *after* the switch this
gate blocks. Reopen conditions: pilot built-in `/code-review` as an *additional*
Step 11 reviewer for ≥10 runs + backfill token telemetry. Full record:
`docs/measurements/2026-07-05-issue-162-data-gate-decision.md` (drift-guarded
by `tests/test_decision_records.py`, which recomputes the evidence basis from
`run_records.jsonl` records[:23]).

**What shipped.** Decision record + 3 drift-guard tests (one recomputes the
evidence from the store) · README/roadmap/dashboard annotations · v2.64.2.

**Owner directives (mid-slot).** #184 (version-aware setup prompt) inserted
into the campaign as **slot 12, before #161** — slots renumbered 12=#184,
13=#161, 14=#165; run doc, dashboard, roadmap updated.

**Reviews.** Step 11: opus reviewer (2 Low: citation + count fix, both applied)
+ Codex adversarial diff pass (2 Medium: drift-guard vacuity — test now parses
the store; applied). Security scan clean (iac/sca visible skips).

**Status.** PR + CI + merge SHA filled by the next slot's pass (established
convention). Telemetry embedded below. Issue closed as *not planned*, data
cited.

---

## Slot 10 — #148 + #163: multi-issue driver (M3 start) · v2.64.0 <br>*(status backfill: PR #185 squash-merged `d7ea584`; post-merge hardening review → PR #186 `5e15862`, 7 findings fixed, v2.64.1, suite 1863/0)*

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
