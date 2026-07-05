# implement-feature (WF2) — Assessment, 2026-06-16

Method: 6 independent dimension reviewers → adversarial verification of **every** finding
(39 agents). 28 findings survived verification (9 high / 6 med / 13 low); 5 dropped as
inaccurate/net-negative. Source data: workflow run `wf_355d24a2-173`.

Baseline: `skills/implement-feature/SKILL.md` = **1463 lines**, single monolith, no `references/`.

## Headline
The skill is **unusually correct** — the correctness reviewer verified every `hooks/*.py`
function, subcommand, flag, and exit-code reference against the actual code and found **no
miswired call, no missing function, no bad exit code**. PRs #87–89 kept prose in sync with
the extracted CLIs. The real problems are: (1) **structure/size** — the "much smaller base"
goal is achievable and safe but undone; (2) **two latent safety/correctness gaps**; (3) the
**Step 4 breaker control-flow** is the most fragile mechanism in the file.

---

## TIER 1 — The "much smaller base" refactor (highest leverage)

Concrete target: **base SKILL.md ~500–550 lines + 6 `references/` files** (from 1463).
Every move is verbatim cut → leave a 1-line imperative pointer at the decision point →
keep in base anything that cites a real past failure or names a fail-closed `hooks/*.py`
call. Verify after with a grep that every `references/*.md` is pointed-to and no
`[Headless:]` tag / `assert_*` / `consume_loopback` / `write_review_state` call was lost.

| Extract → reference file | Lines | Saves | Why it's reference, not core |
|---|---|---|---|
| `references/headless.md` (`<headless-interaction/checkpoint/resume>`) | 157–366 | ~204 | Only applies when "HEADLESS MODE active"; interactive runs read it for nothing. Keep inline `[Headless:]` one-liner tags. |
| `references/tiered-review.md` (P15: state-files, 8 criteria, bands, Step 8a procedure, log/deferral schemas) | 44–68, 777–805, 945–979 | ~90–110 | Dormant unless a task is high-risk. **Keep the gate CALLS inline** (`assert_review_coverage`, `assert_no_unresolved_high_deferrals`, review-state refusal). |
| `references/run-record.md` (Step 16 JSON schema + field rules) | 1378–1409 | ~40 | Write-once contract validated by `work_summary.py` (the tool IS the source of truth). |
| `references/adversarial-review.md` (WF5 join-barrier contract) | 724–737, 844–849 | ~15–18 | Opt-in feature most runs skip; **also de-duplicates** the near-identical Step 6 copy. |
| `references/resumption.md` (cross-session/worktree/compaction detail) | 59–67, 387, 424–432 | ~50–60 | Keep the `resume_lib detect-step` invocation + state legend inline (that's the every-run contract). |

Net: ~1463 → ~900 with headless+run-record+adversarial alone; ~500–550 once P15 +
resumption land. **No load-bearing gate, cited failure, or fail-closed contract leaves the base.**

---

## TIER 2 — Two real safety/correctness gaps + one API gap (small code changes)

1. **`deferrals.json` has a reader and a validator but NO writer** (high).
   `plan_lib` has `get_deferred_findings` + `assert_no_unresolved_high_deferrals` +
   `_deferral_is_resolved`, but no `append_deferral`. So Step 8a hand-authors the JSON,
   and its resolution semantics (status=='applied' OR concurrence from a *different*
   reviewer slot; `defer_count>=2` also needs `user_ack`) live only in code. A mistyped
   field → a deferred **Critical/High** silently fails to re-present at Step 11.
   **Fix:** add `plan_lib.append_deferral(path, finding)` + CLI verb + unit test against
   `_deferral_is_resolved` (mirror `append_review_log`); point Step 8a item 4 / Step 11 at
   it; keep the `<state-files>` field list, annotated "written by append_deferral — do not
   hand-author."

2. **`SEVERITY_BANDED_CONFIDENCE` source-of-truth claim is false** (high).
   Lines 33 & 41 say the banded values (Crit 0.50 / High 0.65 / Med 0.80 / Low 0.90) are
   "documented in / source of truth is `hooks/plan_lib.py`." They are **not** — plan_lib
   only has a flat `_CONFIDENCE_DEFAULT = 0.80`. The four numbers live ONLY in prose,
   triplicated (constants 34–38, Step 8a 956, Step 11 1084), with no test guard.
   **Fix (durable):** add the banded dict + a filter fn to `plan_lib`, route Step 8a/11
   through it, collapse the two inline copies to a cross-ref. (Or, minimal: delete the
   false claim and collapse to one cross-referenced copy.)

3. **Step 11 cites a non-existent public "companion reader" for the review log** (med).
   Only `_read_review_log` (private) exists. **Fix:** add public `read_review_log(path)`
   alias and name it at line 1056.

---

## TIER 3 — Executability clarity (cheap, big orientation payoff)

- **Add a happy-path spine** right after `<role>`: the ordered always-run sequence
  `1,2,3,4,5,(6),7,8,(8a),9,(10),11,11.5,12,(13),14,(15),16`. And **add Step 11.5 + Step 16
  to `<mandatory-steps>`** — both are in the `<completion-gate>` (items 10–11) but missing
  from the mandatory table = genuine desync; the most-skippable steps are least visible.
- **Collapse the Step 4 breaker run-count into ONE decision table** (4 runtime states:
  adversarial disabled / enabled+returned / enabled+non-success / loop-back fired →
  breaker over reflexion-only | merged | reflexion-only | skip). Co-locate and preserve the
  line-735 "otherwise the breaker runs zero times" clause and the line-720 skip.
  **Do NOT push to a hook** — the inputs are agent-observed runtime states the CLI can't see;
  a hook would be a second source of truth that could silently mask the breaker.
- **`<loop-back-budget>` is missing the 4th source `review_design`** (live, consumed by
  Step 8a line 962, cap 1). Add the row + a mirror counter; note the global cap (3) binds
  before per-source caps (sum 5). Better: stop re-listing per-source caps in prose; point to
  `<constants>` / `_LOOPBACK_SOURCE_MAX`.

---

## TIER 4 — Style / drift cleanups (small)

- **Trim `<mandatory-steps>` anti-rationalization filler** (94–101) to one why-grounded
  sentence anchored on the Step 11 fact; drop the scripted self-acknowledge ritual.
  **KEEP the "found 2 Critical security issues … when the orchestrator attempted to skip it"
  clause verbatim** — it's the gold-standard failure-grounded MUST and the template for the rest.
- **Reconcile the review-log entry shape**: `<state-files>` line 50 (`verdicts`,
  `findings_count`, `dropped_count`) vs the Step 8a example (`verdict` singular, nested
  `findings`). `assert_review_coverage` reads `.sha` + `.verdict` (singular) — reconcile to that.
- **Description**: soften "automated deployment" (it's capability-gated, not guaranteed) and
  add a one-line feature-vs-bug routing cue to disambiguate from `fix-bug`. A formal
  description-optimizer run is **not** worth it yet — do the manual tighten, reserve the
  optimizer for observed misfires.
- **"shell vars don't persist, carry as literals"** caveat repeated ~5× → state once.

---

## Dropped by verification (credibility signal — the adversarial pass had teeth)
- "Run-record schema not fully enforced" — FALSE: `work_summary.REQUIRED_TOP` fails closed
  on every top-level block; sub-keys enforced explicitly or via type/enum.
- "fix-bug overlap has no routing" — missed the live WF3→WF2 escalation contract; narrowing
  the description would contradict it.
- "retroactive scan ungated" — `assert_review_coverage` filters high-risk by task; reassurance
  was wrong but no action needed.
- headless-checkpoint vs resumption "duplication" — only ~3 fields overlap; "dedup" would ADD
  fields (net-negative).
- "5 blocks = 390 lines before Step 1" — overstated (~208–268); rationale wrong.

---

## OUTCOME (overnight, 2026-06-16) — all merged to main, full suite 1178 green

| PR | Tier | Shipped | Version |
|----|------|---------|---------|
| [#105](https://github.com/3D-Stories/rawgentic/pull/105) | 2 (safety) | `append_deferral`/`resolve_deferral` (close deferral write gap) + public `read_review_log` + real `SEVERITY_BANDED_CONFIDENCE` dict & drift guard | 2.39.0 |
| [#106](https://github.com/3D-Stories/rawgentic/pull/106) | 3 (clarity) | `<happy-path>` spine; Step 11.5 + 16 added to `<mandatory-steps>`; Step 4 breaker decision table; `review_design` 4th loop-back source | 2.39.1 |
| [#107](https://github.com/3D-Stories/rawgentic/pull/107) | 1 (partial) | run-record schema → `references/run-record.md` (first `references/` use) | 2.39.2 |
| [#108](https://github.com/3D-Stories/rawgentic/pull/108) | 4 (style) | reframed `<mandatory-steps>` filler; reconciled review-log shape; tightened description | 2.39.3 |

**Deferred — needs an owner decision (NOT done):** the full Tier 1 base slim-down
(headless / P15 mechanics / resumption → references/). Blocker discovered during
implementation: those blocks are intentionally **duplicated across all 11 workflow
skills** and their presence is pinned by `tests/hooks/test_bmad_detection.py`, so
centralizing them is a plugin-wide architecture change (+ a drift-suite rewrite), not a
safe single-skill edit. SKILL.md net: 1463 → 1484 (clarity additions slightly outweighed
the one safe extraction; the real reduction is gated on the plugin-wide decision).

**Also not done (deliberate):** dedup of the "shell vars don't persist" caveat — kept as
point-of-use reminders.
