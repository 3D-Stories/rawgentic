# #855 — Make the loop-back budget, confidence filter and High-deferral enforceable

**Issue:** [#855](https://github.com/3D-Stories/rawgentic/issues/855) · Part of epic #756
**Base:** `main` @ `d3374dde` · plugin 3.118.2 · suite baseline 6938 passed / 21 skipped
**Complexity:** `complex_feature` (full spine; lane ineligible — architecture change)
**Author:** WF2 orchestrator (claude-opus-5), run `wf2-855-8c186830`
**Revision:** 4 — the amended design at the Step-4 budget-exhausted close (#798).
Three review passes; loop-backs `design` 2/2, global 2/3. Decisions D145, D146, D147.
§19 records every finding and its disposition across all three passes.

---

## 1. Problem restatement — the issue understates it

Verified at `d3374dde`:

| control | definition | production call sites |
|---|---|---|
| loop-back budget | `plan_lib.GLOBAL_LOOPBACK_BUDGET`, `consume_loopback` (:2508) | **0** |
| confidence filter | `plan_lib.SEVERITY_BANDED_CONFIDENCE` (:163) | **0** |
| High-deferral | `plan_lib.append_deferral` (:1262), `resolve_deferral` (:1306) | **0** |

`plan_lib.py` exposes exactly two CLI subparsers — `assert-pr-body` (:2838), `close-design-gate`
(:2849); neither reaches any control. `executor_routing_lib.py` contains zero occurrences of
`loopback`/`deferral`/`disposition`. Every `skills/**` reference is prose telling an LLM to call
Python that has **no runner**.

**The run-id is a red herring** — nothing debits under *any* run-id. The work is *build the
transition and wire it*. The ACs are unchanged. And the governing corollary: **prose instructing
an LLM to call a function is exactly the mechanism that already failed.**

## 2. The harm

#840 burned ~8 hours on **thirteen review rounds**. The harm is unbounded *rounds*.

## 3. Enumerations — settled, not deferred

**Every `--seat build` call site — exactly three, all initial plan-task builds:**
`skills/implement-feature/references/steps.md:997`, `skills/fix-bug/SKILL.md:122`,
`skills/fix-bug/references/steps.md:327`.

**Fixes are applied INLINE, never by dispatch:** Step 8a is orchestrator-owned ("8a is NOT
delegated", `steps.md:1047`) and fixes Critical/High before Step 9; Step 11 fixes them before the
PR ("filtered findings **and fixes applied**"); Step 4 edits the design document directly.

**Every review producer that can drive a loop-back** (pass 3, self #3 — the inventory the earlier
revisions lacked):

| producer | where | seat / mechanism |
|---|---|---|
| Step 4 self-review | `steps.md:610` | executor `review` |
| Step 4 adversarial-on-design | `steps.md:683` | `adversarial_review_lib`, no seat |
| Step 4 incremental verifier (spec_tighten) | `steps.md:715` | executor `review` |
| Step 6 adversarial review | `steps.md:892` | `adversarial_review_lib`, consumes `design` |
| Step 8a wave (2 reviewers) | `steps.md` Step 8a item 2 | executor `review`; `review_design` path |
| Step 11 wave (2 reviewers) | `steps.md:1285`+ | executor `review` |
| WF3 Step 4 reflect (+ optional adversarial) | `fix-bug/references/steps.md:258, 268` | inline + `adversarial_review_lib` |
| WF3 Step 9 code review | `fix-bug/references/steps.md:428` | executor `review` |
| LEGACY agents | declared rollback | Agent tool, **no ledger** (§18) |

## 4. Approaches

**A — debit per dispatch** (rejected): makes the budget a raw call counter; would have stopped
thirteen legitimate clean re-reads.
**B — detective exit gate only** (retained as tier c, never primary): a runaway loop still runs
every round and merely cannot open a PR.
**C — harden the prose + a lint** (rejected): this is what exists. `steps.md:1291` already says
"do not edit the deferrals JSON by hand" and the function was never called at all.
**D — barrier on the `build` seat** (refuted, pass 1): §3 shows fix rounds never dispatch a build
seat, and its "relabelling a fix as an initial build is refused" clause would falsely refuse
legitimate plan-task builds after any loop-back.
**D′ — the same barrier on the `review` seat** (refuted, pass 2): **a barrier that serializes
waves never caps them** — a wave whose findings are absent, filtered, declined or dissolved debits
nothing, so thirteen rounds still run one at a time.

### E — Review admission: one free initial wave per gate, every reopening debits (SELECTED)

From the round-3 peer consult (§22):

> **A gate gets exactly one free initial review wave. A completed wave is terminal. Any later wave
> for that same issue and gate is a *reopening*, and a reopening debits — regardless of whether the
> previous findings were absent, filtered, declined, dissolved, deferred or adopted.**

- **A clean review stays free** — the *first* wave at each gate costs nothing, so the legitimate
  clean re-reads that killed approach A are not charged for existing.
- **A clean *re-read* is a loop-back**, because that is what it is. This closes D′'s hole.
- **Waves are therefore bounded by the shipped budgets** — no new ceiling, nothing for
  `work_summary.py:614` to disagree with.

State key `(issue_key, workflow, gate, generation)`; generation 0 free, every later generation
debits before dispatch.

## 5. What is actionable — the boundary is NOT a caller-selected seat

Pass 3 raised two Criticals against conditioning admission on `seat == review`: a caller can put a
review prompt on any read-only seat, and nothing mechanically coupled "a review returned findings"
to "intake happened". Both are answered by widening the boundary rather than trusting the label:

1. **Model output is non-actionable by default.** A gate's findings become actionable only through
   `complete-review`, which is the only writer of dispositions, deferrals and the terminal wave
   record.
2. **An un-intaken actionable wave blocks the next dispatch of ANY seat in that run** — not just
   the next review. Exit **4** `review_intake_pending`, naming the wave and the verb that clears
   it. This is the choke that exists given §3's finding that fixes are inline: there is no fix
   dispatch to gate, but there is always a *next dispatch*, and the run cannot proceed past it.
3. **It cannot falsely refuse a plan-task build.** The block is live only in the window between a
   review wave completing and its intake — a window the orchestrator closes immediately by running
   intake. Test L asserts a `build` dispatch succeeds when no wave is pending; test L2 asserts it
   is refused while one is.
4. **Non-seat producers** (the `adversarial_review_lib` paths at Step 4, Step 6, WF3 Step 4) have
   no ledger record to admit. They are **advisory-only inputs merged into the owning gate's wave**,
   exactly as the prose already treats them, and the owning gate's admission is what debits. Where
   such a producer can *itself* drive a loop-back (Step 6, `steps.md:892`), that gate is in the
   allowlist and opening it is a reopening like any other.

## 6. The admission state machine

Enforced at the one mandatory pre-spawn choke, `hooks/executor_routing_lib.py:3284`
(`led.append_expected(...)`) — append-before-dispatch, under `flock`, duplicates failing closed.

```
review dispatch:
  ctx = hooks.resolve_review_context()   # canonical issue, allowlisted gate, artifact digest, slot
  append_expected(..., review_admission=ctx)

append_expected, FIXED lock order (issue admission journal → run ledger):
  require run ledger has an `initial` record        -> else run_not_declared      (exit 4)
  require run is not closed                          -> else run_closed_intake_refused
  require no un-intaken actionable wave in this run  -> else review_intake_pending
  run_ledger.check_architecture_and_duplicate()      # existing checks, unchanged
  state = issue_journal.fold(issue_key, workflow, gate)
  roster = GATE_REGISTRY[(workflow, gate)].roster    # AUTHORITATIVE — never the caller's

  if no state for this gate:
      wave = create(generation=0, roster=roster, digest=ctx.digest)          # the free wave
  elif state.wave is open:
      require ctx.digest == state.digest and ctx.slot in roster and unclaimed
      wave = state.wave                                                      # parallel member
  elif technical retry of a failed slot:
      require same generation and attempts(slot) < RETRY_LIMIT
      wave = state.wave                                                      # attempt, not budget
  else:
      require ctx.digest != state.digest                                     # nothing changed
      atomically consume the mapped source AND the workflow's global allowance   # THE debit
      wave = create(generation=state.generation + 1, roster=roster, digest=ctx.digest)

  issue_journal.append(member_reserved(wave, slot, correlation_id, fencing_token, deadline))
  run_ledger.append_expected(seat, correlation_id, recovered_from, review_admission={...})
```

The `initial`/`closed` preconditions are stated here explicitly (pass 3, adv #1) because they are
load-bearing *before* any mutation or spawn — the code does refuse
(`executor_routing_lib.py:3245-3250`; `ledger.py` `_check`), but a design that leaves it implicit
cannot be implemented from.

**The gate registry owns the roster.** `GATE_REGISTRY` maps `(workflow, gate)` → immutable
`{roster_size, slot_ids, budget_source}`. **`roster_size` is removed from caller-supplied
context**; a caller's slot is an *assertion checked against* the registry, never configuration.
Pass 3 (self #5, adv #7) found that a caller-supplied roster of 1 would let one reviewer close the
two-reviewer Step 8a/11 gates and skip the second review. Tests G, J, J2 cover size 0/1/3 and
invalid slots.

**Slot-completed ≠ attempt-terminal** (pass 3, adv #2). A slot is *completed* only by a `success`
observation carrying a structurally valid reviewer envelope. `spawn_failure`, `timeout`,
`dead_result` and `cancelled` are terminal **attempts** that leave the slot retryable; exhausting
`RETRY_LIMIT` marks the wave `blocked`, and intake **refuses** a blocked wave. Without this
distinction a failed reviewer would satisfy intake.

**Exactly one debit point** (pass 3, self #4, adv #6): admission, before dispatch. Intake does
**not** re-validate budget, intake replay covers persistence only, and **finding dispositions never
debit** — every admitted reopening has already consumed exactly one debit.

## 7. Ledger and observation changes — declared, not assumed

`append_expected` today writes a fixed `{kind, seat, correlation_id, recovered_from}`
(`ledger.py:237-257`) and takes one caller precondition, `expected_architecture`, asserted inside
its locked `_check` (`:246-250`).

1. **`append_expected` gains an optional versioned `review_admission` object** — issue key,
   workflow, gate, generation (**store-assigned**; a caller-supplied generation is refused), digest,
   slot, attempt, fencing token. `LedgerState` exposes it; absent → `None`. Non-review seats and
   every pre-upgrade record stay valid.
2. **The precondition is asserted inside the locked `_check`**, as `expected_architecture` is.
   Checking it in `hooks/` around the call reintroduces the read-then-append window the comment at
   `executor_routing_lib.py:3282` exists to eliminate.
3. **Observation records** — `success`, `spawn_failure`, `timeout`, `dead_result`, `cancelled`.
   **Call-site inventory** (pass 3, adv #5), each named because the record is load-bearing for
   roster completion, retries, fencing and blocking: the dispatch result path and its
   process-failure branches at `executor_routing_lib.py:3291-3306` (`dispatch_timeout` /
   `dispatch_signalled` / `dispatch_<status>`, #733), the supervised and resume siblings, and the
   parse/verify step that produces `parse_status`. The **executor** appends the observation; an
   append failure is **fail-loud** (exit 5), never swallowed — a lost observation would silently
   wedge or silently free a wave. A spike asserts every termination path yields exactly one fenced
   observation or a loud error.
4. **`append_initial` is NOT touched.** Requiring a field there is impossible without stranding
   live runs: `begin-run` early-returns `already_declared` on matching architecture+digest
   (`executor_routing_lib.py:3726-3735`) and `append_initial` enforces "initial must be the first
   and only 'initial'" (`ledger.py:231-236`). The issue binding is established on the first
   post-upgrade review dispatch from immutable run state and is immutable thereafter.
5. **`phase_executor` still never imports `hooks/`.** It defines the record schema and transaction
   protocol; `hooks/` owns `GATE_REGISTRY`, issue-key resolution and all finding semantics.
6. **In-flight grace path** (pass 3, self #9): a pre-upgrade `expected` review record with an
   outstanding result is imported into generation 0 of its gate on first contact — one explicit
   import, no debit — so an upgrade mid-run cannot strand a review that already happened. Strict
   enforcement applies to waves opened after the upgrade. Tests M, M2.

## 8. Budget keying — workflow-scoped, because WF3's cap is 2

Counters stay issue-keyed (`claude_docs/.wf2-state/<issue>/loopback_counters.json`). **The debit
identity is `(workflow, run_id, generation, gate)`** — `workflow` included because the caps differ
and one issue can see activity in both (pass 3, adv #9). The projection records `workflow` on every
row, and a reader folds per workflow; a legacy row without the field folds to `wf2`, its only
possible origin before this change.

- Issue-scoped and **survives across runs**; a new run never resets it.
- **The admission journal is authoritative**; `loopback_counters.json` and `dispositions.jsonl`
  remain the projections `close-design-gate` (`plan_lib.py:2657`) and `work_summary.py:410`
  already read, unchanged in location and shape.
- **WF2's global cap is 3; WF3's is 2** (`fix-bug/references/steps.md:792`). `consume_loopback`
  hard-codes 3 (`plan_lib.py:2527`), so admission checks the workflow's effective cap **first** and
  refuses before calling it. The constant is untouched and neither existing reader is disturbed.

**Remedy on exhaustion (AC4)** — exit 4 `budget_exhausted`:

> Loop-back budget exhausted for issue #N (source `<s>`: x/y, <workflow> total: t/<cap>).
> Remedies, in order of preference:
>   1. Close or defer the remaining findings without reopening the gate.
>   2. Split the unfixed work into a separately scoped issue.
>   3. Stop and escalate to the owner — this cap is theirs to raise, not the run's.

A fourth remedy — *run a peer consult when the budget is spent* — is deliberately **out of scope
here** and filed as its own issue in epic #756 (owner decision, 2026-08-03), because it is a remedy
rather than an enforcement mechanism and can ship independently. Validated by this very run: two
review passes refuted two mechanisms and a peer consult produced the third.

**No runtime ceiling override exists.** Revision 1's `extend-budget --approved-by` was dropped:
`--approved-by` is a string the constrained agent supplies itself (§18), and it cannot work anyway
— `consume_loopback` compares against the module constant and `work_summary.py:614` rejects
`used > budget`. Raising the cap is an owner edit to the constant, auditable in git.

## 9. High deferrals

A surviving High may be deferred only with `{owner, reason, remedy, due}`; missing metadata → exit
**2**. A **Critical** deferral is refused → exit **6**.

**Repeat deferral is keyed by a host-assigned deferral lineage id, not by the content hash** (pass
3, adv #3). `compute_finding_key` hashes model-authored `description`, so a paraphrase would mint a
new key and buy a fresh "first" deferral. The lineage id is assigned on first deferral and carried
in an alias table mapping every observed `finding_key` to it; a later finding whose key resolves to
an existing lineage is the *same* deferral.

**A second deferral of one lineage blocks automated continuation** — exit **4**
`deferral_needs_owner`. Revision 2 gated this on `--user-ack`, which is the same self-approval
defect as `extend-budget`: the constrained agent supplies the flag. Only a human acting outside the
run can move it forward.

**Migration is specified, not assumed** (pass 3, self #7): the existing `deferrals.json` schema
keys on `finding_id`. `append_deferral`, `resolve_deferral` and `_deferral_is_resolved` gain the
lineage field with `finding_id` retained as an alias; fixtures cover existing one- and
two-deferral files; the state/resume prose moves with them.

Deferrals stay in `deferrals.json`, **not** the disposition ledger — the shipped boundary
(`plan_lib.py:1370-1379`: the ledger holds only terminal decisions).

## 10. The transition — one module, full CLI grammar

**`hooks/review_transition_lib.py`** owns the policy and all verbs, importing plan_lib's primitives
(`consume_loopback`, `SEVERITY_BANDED_CONFIDENCE`, `append_deferral`, `append_disposition`,
`file_lock`, `compute_finding_key`) rather than duplicating them.

```bash
review_transition_lib.py complete-review --issue N --run-id ID --gate G --generation K
    --findings-file F [--repo-root .]        # stdout: {status, dropped[], dispositions[], deferrals[]}
review_transition_lib.py diagnose-review --reason "…" [--issue N] --findings-file F [--repo-root .]
review_transition_lib.py import-diagnostic --from RECEIPT --issue N --run-id ID --gate G [--repo-root .]
review_transition_lib.py assert-review-intake --run-id ID --issue N [--repo-root .]
```

New dispatch options on `executor_routing_lib.py dispatch`: `--review-gate G`, `--review-slot S`,
`--review-digest D`, `--review-issue N`, `--review-workflow wf2|wf3`. Every `--*-file` resolves
under `--repo-root`. Exit mapping per §21. (Pass 3, self #8: the earlier revision named verbs
without a grammar.)

There is **no** `abandon-wave` verb — revision 2's became the escape hatch. Failure is handled by
observations and the bounded attempt allowance (§7.3, §12).

**`close-design-gate` is not a second transition.** It records the Step-4 budget-exhausted close
(#798) and writes dispositions for findings adopted at that close; `complete-review` intakes a
*wave*. They never both run for one wave; test AD guards it.

`complete-review`, **every validation before every mutation**:

1. Lineage — `initial` present, run not closed.
2. Wave — the admission record for `(issue, workflow, gate, generation)` exists and **every roster
   slot has a `success` observation**; incomplete → exit 4 `wave_roster_incomplete`; blocked → exit
   4 `wave_blocked`. A `diagnostic` record can never be intaken here.
3. Issue binding — bound at dispatch; mismatch → exit 4 `issue_mismatch`.
4. Validate findings, deferral metadata and disposition vocabulary. Missing/invalid `confidence` or
   `category` → exit **2**, **nothing written**. (No budget check here — §6, one debit point.)
5. Confidence policy — `SEVERITY_BANDED_CONFIDENCE`. A below-band finding is recorded as a
   **journal-only `dropped_low_confidence` observation** carrying `{finding_key, severity,
   confidence, band}` — deliberately *not* a disposition, since the shipped vocabulary has no such
   terminal value (pass 3, self #10). Tests just-below / at / above the band for every severity.
6. Dedupe by `compute_finding_key` against the issue's disposition history.
7. Commit (§11).

## 11. Transaction protocol — a real WAL, not per-file atomicity

Pass 3 (self #6, adv #4) established that per-file atomic replacement plus lock ordering cannot
make separate appends atomically visible. Replaced by an explicit write-ahead commit:

1. **Append and fsync ONE authoritative commit record** to the wave journal, containing the entire
   intake intent and a transaction id: the dispositions, deferrals, `dropped_low_confidence`
   observations and the terminal wave state it will produce.
2. **Materialize each projection** — `dispositions.jsonl`, `deferrals.json`, the counters
   projection — each stamped with the transaction id as an **applied marker**.
3. **Replay at read time**: any committed transaction whose projection lacks its applied marker is
   re-materialized idempotently; **readers ignore uncommitted transactions entirely**.
4. **Fail-closed asymmetry** between the journal reservation and `append_expected`: a crash between
   them consumes a technical attempt, never grants a wave.
5. Reservations carry **deadlines and fencing tokens**; recovery may fail an expired attempt and
   retry the slot, and late observations are rejected by token.
6. Lock order, fixed everywhere: issue admission journal → run ledger → counters → dispositions →
   deferrals. Whole-file writes use `atomic_write_lib.atomic_write_text`
   (`hooks/atomic_write_lib.py:27`); append-only JSONL keeps its plain append, matching
   `append_disposition`'s documented rationale (`plan_lib.py:1372-1374`).

Tests crash-inject before and after **every** append and replace, asserting the exact journal,
counter, disposition and deferral state each time, with a debit total of exactly one.

## 12. Failure handling — without an escape hatch

| outcome | recorded | slot | budget |
|---|---|---|---|
| valid reviewer envelope | `success` | **completed** | debit already taken at admission |
| pre-spawn refusal | none — nothing spawned, no wave opened | — | none |
| spawn failure / timeout / cancellation | `spawn_failure` / `timeout` / `cancelled` | retryable | none; one attempt |
| blank, malformed or vacuous return | `dead_result` | retryable | none; one attempt |
| attempts exhausted | wave `blocked` | — | none; intake refuses; run stops and reports |

A blocked gate is a reported stop, not a released barrier.

## 13. Structured-result contract

```json
{ "schema_version": 1, "run_id": "…", "workflow": "wf2", "issue": 855,
  "gate": "11", "generation": 1,
  "findings": [ {
    "severity": "High",                       // Critical | High | Medium | Low
    "category": "architecture",               // REQUIRED — append_disposition validates it
    "confidence": 0.72,                       // finite JSON number in [0,1]
    "location": "hooks/plan_lib.py",          // repo-relative; no line number
    "description": "…",
    "rationale": "precondition and impact, one line" } ],
  "requested_dispositions": { "<finding_key>": { "kind": "adopted" } },
  "requested_deferrals":    { "<finding_key>": { "owner": "…", "reason": "…",
                                                 "remedy": "…", "due": "YYYY-MM-DD" } } }
```

`category` and `requested_deferrals` exist because `_disposition_entry_error`
(`plan_lib.py:1395-1415`) requires top-level strings `id, finding_key, disposition, reason,
decided_by, date`, **integers `issue` and `pass`**, a string `gate`, and a `finding` object with
`severity`, **`category`**, `description`. The command supplies `issue`, `gate` and `pass`
(= `generation`); the model supplies the rest.

**Identity reuses `plan_lib.compute_finding_key` unchanged** (`:1351-1366`), always host-recomputed
so a model cannot perturb its own key. **Vocabulary is the shipped `adopted | declined |
dissolved`** (`:1409`) — and per §6, none of them debits.

*Residual weakness, named:* the shipped key is paraphrase-sensitive. That is why deferral
recurrence uses a lineage id (§9) rather than the key alone; for dispositions the exposure is
duplicate work, not a bypass.

## 14. Break-glass diagnostic mode (AC5, AC2)

`diagnose-review --reason "<why>"`: no declared run needed; same parse and confidence policy;
writes only to `claude_docs/.wf2-state/<issue>/diagnostic/<utc>-<token>.json`; **debits nothing,
writes no disposition**; every finding marked `diagnostic: true`; `--reason` mandatory.

**No reason-only release exists.** A diagnostic dispatch carrying `--issue` records a diagnostic
generation for that gate; the next actionable wave there is a **reopening** and debits like any
other. There is nothing to "release", so the revision-2 hole (close the quarantine with a string)
is gone. `import-diagnostic` debits normally. A diagnostic with **no** `--issue` is a genuine
emergency outside any issue's boundary and cannot be imported (§18).

## 15. Test strategy (AC6 is the headline)

Black-box via `subprocess.run([sys.executable, CLI, ...])` per `docs/testing.md:5-8`, plus unit
tests on the pure helpers and the ledger. Red before green.

| # | test | asserts |
|---|---|---|
| A | hand-minted run-id, never `begin-run` | exit 4 `run_not_declared`; counters byte-identical; **no reviewer spawned**, no admission record |
| B | declared run, fabricated admission record | exit 4 `review_not_in_ledger` |
| C | **first wave at a gate, clean findings** | admitted, **no debit** — the free-wave guarantee |
| D | **second wave, changed digest, clean findings** | admitted **and debited** — D′'s hole, closed |
| E | reopen with an unchanged digest | refused `digest_unchanged`; no debit |
| F | reopen to the cap | exit 4 `budget_exhausted`, remedy string present |
| G | `gate` not in `GATE_REGISTRY` | refused `gate_not_allowlisted` |
| H | caller supplies its own `generation` | refused — the store assigns it |
| I | two reviewers, same wave, concurrent | both admitted to distinct slots |
| J | third member of a two-slot roster | refused `roster_full` |
| J2 | caller claims roster size 1 for a two-slot gate | refused — registry wins; also size 0 and 3, and an invalid slot id |
| K | intake with one slot lacking `success` | exit 4 `wave_roster_incomplete` |
| K2 | intake of a wave whose slot only has `dead_result` | refused — a failed attempt is not a completed slot |
| L | `build` dispatch, no wave pending | **exit 0** — the §3 regression guard |
| L2 | any-seat dispatch while an actionable wave is un-intaken | exit 4 `review_intake_pending` |
| M | pre-upgrade ledger record | parses with `review_admission` None; dispatch unaffected |
| M2 | upgrade mid-run with an outstanding pre-upgrade review | imported to generation 0, no debit, run completes |
| N | spawn failure then retry in the same generation | admitted; no debit; attempt count 1 |
| O | vacuous/blank return | `dead_result`, never clean |
| P | attempts exhausted | wave `blocked`; intake refuses; no release |
| Q | late observation after fencing | rejected by token |
| R | crash between journal reservation and `append_expected` | fail-closed: attempt consumed, **no free wave** |
| S | crash before/after **every** append and replace | replay yields exact state; debit total exactly 1 |
| S2 | reader encountering an uncommitted transaction | ignores it entirely |
| T | two concurrent reopenings | exactly one admitted |
| U | finding missing `confidence` | exit 2; nothing written |
| V | finding missing `category` | exit 2 |
| W | second deferral of one lineage, incl. a paraphrased description | exit 4 `deferral_needs_owner` — paraphrase does not mint a fresh first deferral |
| W2 | existing one- and two-deferral `deferrals.json` fixtures | migrate to lineage ids without loss |
| X | Critical with a deferral requested | exit 6 |
| Y | WF3 reopen past 2 | refused at 2, not 3 |
| Y2 | one issue with interleaved WF2 and WF3 reopenings | independent totals of 3 and 2 |
| Z | diagnostic under a hand-minted run-id, then an actionable wave | diagnostic debits nothing; the wave debits |
| AA | `assert-review-intake` with a non-terminal wave | exit 1 |
| AB | `phase_executor/src` off `sys.path` | exit 5 `ledger_unavailable` naming the path; plus a positive-path import test |
| AC | traversal, leaf symlink, parent-component symlink, oversized/over-deep JSON, unknown keys | refused per §17 |
| AD | `complete-review` and `close-design-gate` on one gate | mutually exclusive; no double disposition |
| AE | review prompt dispatched on a non-review read-only seat | its output cannot become actionable without intake (§5) |
| AF | every executor termination path | exactly one fenced observation, or a loud error |
| AG | confidence just below / at / above the band, each severity | dropped vs kept exactly per band |

**C, D, L, L2 and J2 are the regression guards for this revision's central claims.** Test A is AC6
verbatim and asserts both halves — refused *and* not debited.

## 16. Prose changes (surgical — decision D143)

- `shared/blocks/loop-back-budget.md` — the bare `consume_loopback` instruction becomes the
  `complete-review` invocation; add the exit-gate sentence. Re-sync (→ implement-feature).
- `shared/blocks/review-severity.md` — deferral points at `complete-review`; a second deferral of a
  lineage stops the run. Re-sync (→ implement-feature **and** fix-bug).
- `skills/implement-feature/references/steps.md:708, 713, 1144, 1291, 1350` — each bare function
  reference becomes a real CLI invocation.
- Step 4, Step 6, Step 8a and Step 11 dispatch prose — pass gate, slot and digest (never roster).
- WF3 `fix-bug/references/steps.md:258, 268, 428` — the same for its two gates.
- `SKILL.md` `<completion-gate>` — add the `assert-review-intake` item (shape of item 13).
- `SKILL.md` `<constants>` — values unchanged; note the bands are now *applied* by intake.

Drift guards moving with these: `tests/test_wf2_clarity.py:518, 1516, 1527, 1534, 1603`,
`tests/test_shared_block_drift.py:42`, and the WF3 clarity guards.

## 17. Security

- **Closed schema**, `additionalProperties: false` at every level; unknown key → exit 2.
- **Limits:** ≤ 200 findings; `description` ≤ 4 KiB; `rationale` ≤ 1 KiB; `location` ≤ 512 B;
  file ≤ 1 MiB; depth ≤ 6.
- **Paths open relative to a trusted directory descriptor**, `O_NOFOLLOW` on the leaf and every
  parent resolved through that descriptor chain — not by pathname after a separate check.
  `close-design-gate`'s pattern is insufficient: separate `realpath`/leaf-`islink` before an
  ordinary open (`plan_lib.py:2613-2617, 2642-2658`) leaves a check/open race and does not refuse
  symlinked parents.
- **Every identifier in a pathname is sanitized** with `phase_executor.capture.sanitize_component`
  (already used for `run_id` at `executor_routing_lib.py:3239`).
- Nothing from a finding reaches a shell; `--reason` is written as JSON.

## 18. Known limits, stated plainly

- **No authenticated human boundary.** Any string the orchestrator supplies authorizes nothing;
  `--approved-by` and `--user-ack` were removed rather than kept as decoration.
- **Workflow enforcement at the routing path, not tamper-proofing.** An orchestrator that edits the
  journal directly or invokes a model outside rawgentic is outside the boundary. No OS-level
  prevention is claimed.
- **LEGACY has no ledger**, so the preventive tier cannot cover it; legacy keeps the detective tier
  only. A bounded, named gap.
- **A diagnostic with no `--issue`** is deliberately outside the boundary (§14).
- **A new issue is a new budget** — honest scoping; the journal records the run→issue binding.
- **Issue-journal lifecycle:** reopening a long-finished issue cannot safely reset capacity without
  a trusted epoch; today that is a deliberate manual operation.
- **`dead_result` is structural, not semantic** — a well-formed but useless review can pass it.
- **POSIX-only** (pass 3, adv #8): `flock`, `dir_fd` and `O_NOFOLLOW` are required. CI runs Linux
  and the fleet is Linux/macOS; the requirement is declared in §20 and enforced by a fail-loud
  platform gate rather than assumed.

## 19. Findings and dispositions across all three passes

Pass 1: 14 self-review + 10 adversarial. Pass 2: 13 + 12. Pass 3: 11 + 9. Every citation was
re-read at source before being acted on; nothing was taken on a reviewer's word. **Nothing was
declined.** The mechanism changed twice on evidence (D → D′ → E) and four sub-mechanisms were
removed rather than repaired: `extend-budget`, the `begin-run --issue` binding, `abandon-wave`, and
`--user-ack`.

Pass-3 dispositions (pass 1 and 2 are recorded in decisions D145/D146 and were applied in
revisions 2 and 3):

| finding | disposition |
|---|---|
| self #1, #2 (C) — actionable boundary is a caller-selected seat; intake uncoupled from output | **adopted** — §5: non-actionable by default; an un-intaken wave blocks the next dispatch of ANY seat; tests AE, L2 |
| self #3 (H) — review-producer inventory incomplete | **adopted** — §3 inventory table; §5.4 |
| self #4 (H), adv #6 (H) — debit trigger/timing contradictory | **adopted** — §6: admission is the sole debit point; intake never re-validates budget |
| self #5 (H), adv #7 (H) — caller supplies roster size | **adopted** — §6 `GATE_REGISTRY` owns it; tests J2 |
| self #6 (H), adv #4 (H) — no real multi-file commit protocol | **adopted** — §11 WAL with applied markers and read-time replay; tests S, S2 |
| self #7 (H), adv #3 (H) — deferral policy incompatible; paraphrase mints a new first deferral | **adopted** — §9 lineage id + migration; tests W, W2 |
| self #8 (H) — no CLI grammar | **adopted** — §10 synopsis and dispatch options |
| self #9 (H) — no grace path for in-flight pre-upgrade reviews | **adopted** — §7.6; test M2 |
| self #10 (M) — `dropped_low_confidence` has no durable shape | **adopted** — §10.5 journal-only observation; test AG |
| self #11 (M) — `sanitize_component` undeclared | **adopted** — §20 second entry |
| adv #1 (H) — initial/closed checks not stated pre-spawn | **adopted** — §6; test A extended |
| adv #2 (H) — a failed observation would satisfy intake | **adopted** — §6 slot-completed vs attempt-terminal; test K2 |
| adv #5 (H) — no observation call-site evidence | **adopted** — §7.3 inventory + spike; test AF |
| adv #8 (M) — POSIX APIs undeclared | **adopted** — §18, §20 |
| adv #9 (M) — debit identity omits workflow | **adopted** — §8; test Y2 |

## 20. Platform / external dependencies

platform_apis:
- api: `phase_executor.ledger.ExpectedCallLedger` — guarded lazy import from a hooks/ module, plus an extension of its `append_expected` write schema and new observation records
  feasibility: verified via existing-call-site — `hooks/executor_routing_lib.py:2629` performs the guarded lazy `import phase_executor.ledger` inside the subcommand; `:3242-3243` constructs `pe.ledger.ExpectedCallLedger(run_dir, args.run_id)` and calls `.read()`; `:3284` calls `.append_expected(seat, correlation_id, expected_architecture="executor")`. Read at d3374dde. Scope stated honestly: the READ path and the guarded-import pattern are proven by those call sites; the `review_admission` field, the store-supplied admission transaction and the observation records are NEW code in `phase_executor/src/phase_executor/ledger.py:237-257`, which today writes a fixed four-key record and exposes no observation-read API.
  failure: fail-loud
  surface: ImportError is caught and re-raised as exit 5 `ledger_unavailable` naming the resolved sys.path — test AB runs the transition with `phase_executor/src` absent AND asserts the normal configured import succeeds. Test M asserts a pre-upgrade record parses with `review_admission` None, so an older ledger cannot fail closed by surprise.
- api: `phase_executor.capture.sanitize_component`
  feasibility: verified via existing-call-site — imported in the same guarded lazy block at `hooks/executor_routing_lib.py:2630` and already used to build the run directory at `:3239` (`Path(paths["capture_root"]) / pe.capture.sanitize_component(args.run_id)`). Exactly the function and call form this design needs for every pathname identifier.
  failure: fail-loud
  surface: shares the guarded-import exit 5 `ledger_unavailable` path; a positive test asserts a hostile identifier is sanitized and a negative test asserts the traversal attempt is refused rather than written.
- api: POSIX filesystem primitives — `fcntl.flock`, `os.open(..., dir_fd=…)`, `O_NOFOLLOW`, `os.fsync`
  feasibility: verified via existing-call-site — `plan_lib.file_lock` already uses `flock` for the counters this design debits, and the shipped ledger takes `flock` per append; CI runs Ubuntu on Python 3.12 (`.github/workflows/ci.yml`) and every fleet host is Linux or macOS. No Windows lane exists in this project.
  failure: fail-loud
  surface: a startup platform gate raises exit 5 `platform_unsupported` naming the missing primitive rather than silently degrading to a non-atomic path; asserted by a test that monkeypatches the primitive away.

No new dependency, no new service. Everything else is stdlib plus in-repo modules.

## 21. Exit codes

| exit | meaning | cases |
|---|---|---|
| 0 | ok | wave admitted or intaken; diagnostic recorded |
| 1 | gate FAILS | `assert-review-intake` found a non-terminal actionable wave |
| 2 | malformed | `confidence_required`, `category_required`, `high_deferral_metadata_required`, `diagnostic_reason_required`, schema violation, size/depth cap, path escape |
| 3 | availability | state file locked or unreadable |
| 4 | enforcement | `run_not_declared`, `run_closed_intake_refused`, `review_intake_pending`, `issue_mismatch`, `review_not_in_ledger`, `wave_roster_incomplete`, `wave_blocked`, `roster_full`, `digest_unchanged`, `gate_not_allowlisted`, `budget_exhausted`, `deferral_needs_owner` |
| 5 | internal | `ledger_unavailable`, `platform_unsupported`, invariant violation, failed atomic update |
| 6 | refused | `critical_deferral_refused`, actionable use of diagnostic output |

## 22. Multi-PR assessment

Estimated ~1600–2000 changed lines. Step 5 makes the final call. Ordering is constrained so no PR
leaves existing review callers broken — the admission context stays *optional* at the routing layer
until the prose that supplies it has landed:

- **PR 1 (`Part of #855`)** — `phase_executor`: `review_admission`, the admission transaction
  protocol, observation records, store-assigned generations, roster and fencing semantics, the
  platform gate. Tests H, I, J, J2, M, M2, N, O, Q, R, T, AF. Inert for every existing caller.
- **PR 2 (`Part of #855`)** — `hooks/review_transition_lib.py`: all four verbs, the WAL commit,
  validation, the confidence filter, deferral lineage + migration, the workflow-scoped cap.
  Tests A–G, K, K2, P, S, S2, U–Y2, Z, AA–AD, AG.
- **PR 3 (`Part of #855`)** — wire routing: `resolve_review_context`, `GATE_REGISTRY`, the
  `append_expected` call site, the any-seat pending-intake block. Tests L, L2, AE.
- **PR 4 (`Closes #855`)** — prose and shared-block edits + re-sync, the completion gate, the
  `work_summary` reconciliation, and only now **make the admission context required**.

## 23. Provenance (cross-model, blind both ways)

Two consults, both backend `gpt` (Codex), both report-only and gitignored here:

- **Step 3 peer consult** — `docs/reviews/peer-rawgentic-peer-problem-855-2026-08-03.md`. My draft
  was on disk before the proposal was read, per the Step-3 blindness rule. Contributed the *shape*
  of preventive enforcement, the actionable/diagnostic distinction, debit-once-per-round, Critical
  deferral refusal, host-recomputed identity, a focused module over more `plan_lib` verbs.
- **Round-3 mechanism consult** — `docs/reviews/peer-855-round3-mechanism-2026-08-03.md`, run after
  two review passes had refuted candidates D and D′, against a problem statement carrying nine
  source-confirmed constraints. **Contributed the mechanism this design uses:** one free initial
  wave per `(issue, gate)` with every reopening debiting; store-assigned generations; the gate
  allowlist; full-roster reservation; digest as duplicate-read guard rather than replenishment;
  observation records with bounded attempts, deadlines and fencing; the fail-closed asymmetry
  between the two durable appends; and the judgment that a second High deferral must block rather
  than accept a self-supplied flag.

**Adopted then withdrawn on evidence:** the immutable `begin-run --issue` binding (§7.4) and
`extend-budget --approved-by` (§8). **Rejected:** changing which files `close-design-gate`
(`plan_lib.py:2657`) and `work_summary.py:410` read; `review-round` as a new dispatch verb (a field
on the existing dispatch is smaller); `adopt-run` (nothing to adopt).

**Retained over both consults:** the completion-gate assertion as a cheap second tier (§10) — a
preventive barrier never fires when a run simply ends with no further dispatch.
