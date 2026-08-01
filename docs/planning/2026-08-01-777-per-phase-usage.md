# Design note — per-phase token attribution in run-records (#777)

**Issue:** #777 · **Epic:** #756 · **Date:** 2026-08-01 · **Lane:** small-standard
**Revision:** 2 (rev 1 failed the Step-4 gate: 7 High + 1 Medium, volume threshold met)
**Baseline:** 6520 passed, 21 skipped, exit 0 @ `9b37a11d`

## What rev 1 got wrong

Every finding was verified against live artifacts before being accepted.

| # | Finding (sev) | Verified | Fix in rev 2 |
|---|---|---|---|
| F1 | The reconciliation target is not a run total — `parse_session_jsonl` sums the WHOLE session, and one session spans several issues, so residue silently absorbs unrelated tokens while still reporting `complete` (High) | `claude_docs/wal/history/` holds `rawgentic-issue-792.history.jsonl` AND `rawgentic-issue-777.history.jsonl` for this same session | **Run-interval scoping** (§Interval) |
| F2 | `timing.status: complete` does not mean complete step coverage — `compute_timing` needs only an early first marker and any terminal-or-later one, so a Step-1→Step-11.5 jump computes `complete` and all traffic between lands on Step 1 (High) | `step_state.py:245` | **Coverage computed independently** (§Status) |
| F3 | `observation.json` mtime is COMPLETION time, not dispatch time — adapters write it after the subprocess returns, so a Step-4 dispatch finishing after Step 5 begins is booked to Step 5; supervisor timeout handling can rewrite it later (High) | `claude_cli.py:167`, `codex_cli.py:174`, `supervisor.py:1046` | **mtime DROPPED as a join key** (§Executor) |
| F4 | Two DISJOINT usage pools conflated — orchestrator-session usage and executor provider usage are separate, so one identity cannot prove both; and a scalar `dispatch_tokens` cannot express input vs output vs cache (High) | `claude_cli.py:85` records `input`/`cached`/`cache_write` | **Two ledgers, two reconciliations** (§Ledgers) |
| F5 | `run_id` is optional and **absent from the #812 record I just wrote**, so the executor capture tree cannot be located without guessing (High) | `work_summary.py:456`; the live record has no `run_id` | **Executor ledger requires `run_id`**, else explicit absent (§Executor) |
| F6 | The correlation grammar is not a contract — canonical prose is `<issue>-<step>-<slug>`, not rev 1's `<issue>-s<step>-<slug>`; steps `1b`/`8a`/`11.5` exist; `correlation_id` is documented opaque and optional (Medium) | `shared/blocks/model-routing-resolve.md:14`, `observation.schema.json:43` | **Full-match grammar + issue/run agreement** (§Executor) |
| F7 | `complete` ignores SOURCE completeness — an Observation with `usage: null` contributes nothing without being "unattributed", malformed transcript rows are silently skipped, and the GLM judge returns no usage at all (High) | live `.../review/0-14cf9d0a/observation.json`; `adversarial_review_lib.py:1618` | **Coverage counters, status derived from them** (§Status) |
| F8 | **The reconciliation test is tautological** — the code emits residue so the identity always closes, so any dropped/duplicated/cross-run token passes (High) | rev 1's own text | **Bijection tests, not identity tests** (§Tests) |

F8 is the one that mattered most: rev 1 proposed a test that could not fail.

## The gap is still half what the issue implies

Per-step and per-phase **wall time already ships** (#506/#589): `step_state.compute_timing` →
`work_summary._auto_embed_timing:182`, with `timing_coverage_warning:231` already flagging gaps.
AC1's time component is DONE. This work is **token attribution only**.

## §Interval — what "this run" means (F1)

Tokens are attributed only inside a **run interval**, derived from the issue-scoped step-state
history via the existing public parser `step_state.read_history(state_dir, project, issue)` — the
ONE place that parses it (`find_state_dir` resolves to `claude_docs/wal`, files at
`wal/history/rawgentic-issue-<N>.history.jsonl`). Reusing it rather than re-parsing keeps the
one-helper-one-home rule and avoids the drift #589's own Step-9 review already caught once.

Each history event carries `session_id` as well as `issue` and `entered_at`, so the interval is
scoped by **both** issue and session — which is what makes cross-issue contamination detectable
rather than merely assumed:

- `interval_start` = `entered_at` of the FIRST marker for this issue
- `interval_end` = `entered_at` of the LAST marker for this issue (open-ended → now at assembly)

A transcript message outside the interval is **excluded-source evidence**, counted in
`excluded_rows`, and is **never** placed in residue. Residue means "inside the interval, attributable
to no step" — a much narrower and honest claim.

If the interval cannot be established, `phase_usage.status = absent`. No guessing.

## §Ledgers — two pools, two reconciliations (F4)

Orchestrator-session usage and executor provider usage are **disjoint**: executor dispatches run as
separate provider calls recorded in Observations, and their tokens never appear in the orchestrator's
own transcript. One identity cannot prove both, so there are two:

```json
"phase_usage": {
  "status": "complete|partial|absent",
  "interval": {"start": "...", "end": "..."},
  "steps": [
    {"step": "4", "title": "...",
     "orchestrator": {"input_tokens": 0, "output_tokens": 0},
     "executor": {"input_tokens": 0, "output_tokens": 0, "cached": 0, "cache_write": 0}}
  ],
  "phases": { "design": { "orchestrator": {...}, "executor": {...} } },
  "residue": {"orchestrator": {"input_tokens": 0, "output_tokens": 0}},
  "coverage": {
    "transcript_rows_parsed": 0, "transcript_rows_skipped": 0, "excluded_rows": 0,
    "attempts_seen": 0, "attempts_with_usage": 0,
    "attributed_strong": 0, "attributed_none": 0
  }
}
```

- **Orchestrator identity:** `sum(steps[].orchestrator) + residue == recount over the interval`
  — a **run-scoped recount**, not the whole-session `usage` object.
- **Executor identity:** `sum(steps[].executor) == sum(usage over audited attempts carrying usage)`.

`usage` itself is untouched. Executor token categories mirror the Observation's own
(`input`/`output`/`cached`/`cache_write`) rather than being flattened to a scalar.

## §Executor — the only exact join (F3, F5, F6)

**mtime is dropped entirely.** It records completion, not dispatch, and is rewritable — attributing
by it would confidently mis-book a boundary-crossing dispatch, which is precisely the wrong-number
class this epic exists to kill.

Attribution is by `correlation_id` **only**, under a full-match grammar:

```
^(?P<issue>\d+)-s?(?P<step>1b|8a|11\.5|\d{1,2})-(?P<slug>[A-Za-z0-9._-]+)$
```

and it counts only when **all** hold: the issue prefix equals this record's issue; the parsed step
is a member of this run's timing steps; and the record carries a `run_id` naming the audit directory.
Anything else → `attributed_none`, and status degrades. Never a guess.

**`run_id` is required for the executor ledger.** It is optional today and absent from the live #812
record, so when it is missing the executor ledger is `absent(no_run_id)` — explicitly, not silently
zero. Making WF2/WF3 assembly emit `run_id` is a named follow-up; this design does not pretend it is
already there.

## §Status — coverage-derived, never inherited (F2, F7)

`timing.status` is **not** reused. `phase_usage.status` is computed from the coverage counters:

- **`complete`** requires ALL of: a valid interval; a monotonic marker sequence with no duplicate or
  reset step ids; `transcript_rows_skipped == 0`; `attempts_seen == attempts_with_usage`;
  `attributed_none == 0`; and **both** reconciliation identities satisfied.
- **`partial`** — any of the above unmet, with the counters showing which.
- **`absent`** — no interval or no timing steps.

The counters are **rendered**, not just stored, so "the guard reported complete" is auditable rather
than trusted. A non-zero residue does not silently pass: it degrades to `partial`.

Known uncovered source, stated: the GLM bake-off judge returns text/error with no usage
(`adversarial_review_lib.py:1618`), so those calls are invisible to both ledgers. Recorded as a
limitation, not papered over.

## Files

| File | Change |
|---|---|
| `hooks/phase_usage.py` (NEW) | pure core (`run_interval`, `bucket_rows`, `attribute_attempts`, `compute_coverage`, `compute_phase_usage`) + thin CLI |
| `hooks/work_summary.py` | `_auto_embed_phase_usage` mirroring `_auto_embed_timing`; validation; render |
| `docs/run-records.md` | schema row + section |

## Failure modes

| Failure | Behavior |
|---|---|
| no interval derivable | `status: absent`, empty ledgers, key still present |
| transcript unreadable | `absent`; the existing `capture_status` path is unaffected |
| row outside interval | `excluded_rows` — never residue |
| malformed transcript row | `transcript_rows_skipped` — degrades to `partial` |
| missing `run_id` | executor ledger `absent(no_run_id)`; orchestrator ledger still computed |
| Observation with `usage: null` | `attempts_seen` incremented, `attempts_with_usage` not — degrades |
| correlation id unparseable / issue mismatch / step not in run | `attributed_none` — degrades, never guessed |
| duplicate or reset step markers | treated as separate runs; degrades to `partial` |

Fail-open throughout — reporting telemetry, never a gate.

## Platform / external dependencies

platform_apis: none

## Security implications

- Reads only local files the run already reads: its own transcript, its own step-state history, its
  own `.rawgentic/runs/<run_id>` tree.
- The transcript is parsed for `timestamp` and `message.usage` only — **no message content is read
  or persisted**.
- No new credential path, no network, no new identifiers beyond `run_id`, which is already a schema field.

## Tests — bijection, not identity (F8)

The central discipline: fixtures enumerate source items with known identities, and the assertion is
that **every source item is consumed exactly once** into a named step, residue, or an explicit
excluded/unattributed counter. An identity that residue can always close proves nothing.

1. **Bijection, orchestrator:** N messages with distinct ids across 3 windows → each appears in
   exactly one step or residue; counts sum to N.
2. **Bijection, executor:** M attempts with distinct correlation ids → each in exactly one step or
   `attributed_none`; sums to M.
3. **Cross-issue contamination (F1 red):** a session containing another issue's messages →
   those land in `excluded_rows`, NOT residue, and status is not `complete`.
4. **Sparse-but-`complete` timing (F2 red):** a Step-1 → Step-11.5 history that `compute_timing`
   calls `complete` → `phase_usage.status` is **`partial`**, not `complete`.
5. **Boundary-crossing dispatch (F3 red):** an attempt whose capture mtime falls in a later window
   is still attributed by correlation id, and mtime is never consulted.
6. **Missing `run_id` (F5 red):** executor ledger `absent(no_run_id)`; orchestrator ledger intact.
7. **Grammar (F6):** `1b`, `8a`, `11.5`, `812-s9-review-r1`, `792-s4-design-p2` all parse; an issue
   mismatch, a step absent from the run, and a malformed suffix all → `attributed_none`.
8. **`usage: null` Observation (F7 red):** `attempts_seen` > `attempts_with_usage` → `partial`.
9. **De-duplication (#812) applied before bucketing:** a 3-content-block message counts once, in one step.
10. `_auto_embed_phase_usage` never overwrites an orchestrator-supplied `phase_usage`.
11. A record carrying `phase_usage` validates; a malformed one is rejected.
