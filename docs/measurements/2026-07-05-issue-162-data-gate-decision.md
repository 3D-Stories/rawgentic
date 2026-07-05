# Issue #162 data-gate decision — ABANDONED per AC4 (2026-07-05)

**Issue:** #162 `feat(wf2): Step 4 reflect-only + Step 11 built-in /code-review + WF5 diff pass (data-gated)`
**Decision:** **Abandon**, taking AC4's explicit branch: *"otherwise abandon this issue with the data cited."*
**Framing:** This is **not a rejection** of the built-in `/code-review` reviewer. It is a deferral: the gate's
required evidence does not exist yet, and per the issue's own header ("no A/B evidence, no switch") the
switch cannot be made without it. Cross-model peer consult (Codex, 2026-07-05) concurred and requested
this framing be explicit.

## The gate, as written

AC4: *"if built-in+Codex matched hand-rolled yield over ≥10 runs at lower cost, delete the hand-rolled
path; otherwise abandon this issue with the data cited."*
Success metric: *"Findings-yield per token by reviewer_kind before vs after; Critical-miss rate stays 0."*

## The data (docs/measurements/run_records.jsonl, 23 records as of 2026-07-04)

| Fact | Measured | Gate requirement |
|---|---|---|
| `builtin_code_review` reviewer_kind | **0 gate-instances** in 23 records — the candidate arm has never run | ≥10 runs |
| Token/cost telemetry (`usage.input_tokens` / `output_tokens` / `cost_estimate_usd`) | **null in all 23 records** (11 carry a `usage` block, 10 of those with only `wall_clock_s` populated) | needed to compute yield-per-token for *any* arm |
| `hand_rolled_multi` | 41 findings / 11 gate-instances | incumbent baseline exists |
| `codex` | 16 findings / 4 gate-instances | WF5 arm exists |
| `inline` | 19 findings / 13 gate-instances | reflect arm exists |

The gate fails twice over: the candidate arm's sample size is 0 (vs ≥10 required), and the cost axis of
the success metric is incomputable because no run-record ever captured token usage.

## Design flaw in the gate (named, not laundered)

The modernization roadmap (docs/planning/2026-07-04-workflow-modernization-roadmap.md, the
execution-order table's slot-11 `#162 review switch` row) assumed *"data gate satisfied by the program's own ≥10 reviewer_kind runs — the program is its
own A/B."* That assumption was **circular**: the campaign's runs could only generate `builtin_code_review`
data *after* switching Step 11 to the built-in reviewer — which is precisely the change this gate blocks.
As designed, the gate could only ever fail. The abandon branch firing is therefore the gate working as
written, but the experiment it wanted was never actually runnable.

## Reopen conditions (what would satisfy the gate honestly)

1. **Generate the candidate arm without trading away coverage:** run built-in `/code-review` as an
   *additional* Step 11 reviewer alongside the hand-rolled path for ≥10 runs, recording
   `reviewer_kind: builtin_code_review` gate entries (findings, resolved, severity mix). Coverage never
   drops during the pilot, so Critical-miss risk stays bounded.
2. **Close the token-telemetry gap:** populate `usage` token/cost fields in run-records (the ccusage
   backfill follow-up), so yield-per-token is computable for both arms.
3. When both exist, re-file with the same AC4 comparison — the decision rule itself was sound.

## Outcome

- WF2 Step 4 (reflexion critique / reflect split) and Step 11 (hand-rolled multi-agent review) are
  **unchanged**. WF5 Codex diff-pass triggering is **unchanged** (AC3 holds trivially).
- Issue #162 closed as *not planned*, with this record cited.
- Drift guard: `tests/test_decision_records.py` pins this record and its load-bearing numbers.

---

## REOPENED at #196 (2026-07-05) — the reopen conditions are now met

The two reopen conditions above are satisfied, so the AC4 comparison is now
**computable** (it was not, when #162 was abandoned):

1. **Candidate arm now generated (without trading away coverage).** The post-PR
   code-review lane `.github/workflows/claude-code-review.yml` (#196) runs the
   built-in `/code-review` through `anthropics/claude-code-action@v1` as an
   **additional** reviewer alongside WF2's hand-rolled Step 11 panel — coverage
   never drops, so Critical-miss risk stays bounded. Its findings are recorded as
   `reviewer_kind: builtin_code_review` gate entries.
2. **Token-telemetry gap closed.** #189 added the `usage` block (input/output
   tokens + cost) to run-records, so yield-per-token is computable for both the
   `builtin_code_review` and `hand_rolled_multi` arms.

**Circularity resolved.** The original gate was circular (the campaign could only
produce `builtin_code_review` data *after* switching Step 11 to it — the very
change the gate blocked). #196 breaks the loop by running the built-in reviewer as
a **parallel CI lane**, not a Step 11 replacement — so the candidate arm
accumulates data with zero coverage risk and no switch required first.

**Decision status: computable, pending data (honestly deferred).** The mechanism
is in place; the AC4 rule itself was sound and is unchanged: *if built-in matches
hand-rolled findings-yield at lower cost over ≥10 runs, delete the hand-rolled
path; otherwise keep it.* The ≥10-run sample requires the **owner-gated** live
Action (the `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` repo secret, #195) —
until those runs accumulate, no switch is made and the hand-rolled Step 11 panel
remains the incumbent. This is a deferral of the *decision*, not of the
*mechanism*: the experiment #162 wanted is now actually runnable.

**Capture (AC2).** Recording an Action review as a run-record gate entry with
token/cost is documented at `docs/run-records.md#builtin-code-review-capture`.
