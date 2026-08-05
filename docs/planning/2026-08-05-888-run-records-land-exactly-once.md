# #888 — Run-records that land exactly once (brief design note)

**Date:** 2026-08-05 · **Lane:** small-standard (5 impl files ≤ 7, `standard_feature`)
**Epic:** #871 (M4 wave, records-first) · **Absorbs:** #588 (dropped) + #355 (duplicated)

## Problem

A run-record must land in `docs/measurements/run_records.jsonl` **exactly once**. Today it can
land zero times or twice, and both were measured live:

- **Zero (#588).** WF2 Step 16 persists the record *after* Step 14 merges, so a run that merges
  its own PR orphans its record — a crash, compaction, or context exhaustion between the two
  loses it. Measured: saystory epic #204, **1 of 6 children** persisted a record. #879 needed a
  separate backfill PR (#882).
- **Twice (#355).** `persist_record` (`hooks/work_summary.py:1069`) opens the store `"a"` and
  writes unconditionally — no idempotency guard. A re-run of Step 16 (the documented
  crash-recovery path) silently double-records. Observed live on the WF2 #340 run: a duplicate
  line the orchestrator had to notice in `git diff` and hand-discard.

The #880 run dodged the drop by improvising the right order; this issue makes that order the
contract and closes the duplicate hole structurally.

## Fix — one transactional persistence contract

**1. Cannot be duplicated — `persist_record` gains an idempotency guard (code).**

Identity is a **run fingerprint**:

- the record's `run_id` when present (#473's grammar-safe I3↔I2 join key), else
- SHA-256 over the record's canonical JSON with the **writer-stamped and auto-computed** keys
  removed: `generated_at`, `schema_version`, `timing`.

Removing those three is the load-bearing part, and `timing` is there for a reason the first draft
of this design missed (Step-4 self-review finding 1, High): `_auto_embed_timing` computes `timing`
from the per-run step-state history at persist time whenever the record file omits it. A recovery
re-run happens later, against a history that has grown, so its `timing` differs — a fingerprint
that included it would differ too, and the guard would fail to fire in **exactly** the
crash-recovery case it exists for. `generated_at` is the same class of problem, one clock tick
wide. `usage` is *not* excluded: it is read from the record file, so it is stable across re-runs of
the same file.

Before appending, the store's existing lines are fingerprinted; a match means the run is already
recorded, so nothing is appended and the caller is told. The CLI prints a loud stderr notice
naming the workflow, issue and store — never the record body — and still exits **0**: the desired
end state (this run's record is in the store) holds, and a recovery re-run must not look like a
failure.

A genuinely different record for the same issue (a later re-run with different findings) has a
different fingerprint and appends normally. That is correct: the guard deduplicates *runs*, not
issues.

**Fail-open, deliberately.** If the store cannot be read at dedupe time, the guard warns and
appends anyway. Losing a record is the worse failure — that is this issue's own priority ranking
(#588's measured 1-in-6 drop against #355's single hand-discarded duplicate), and it matches the
repo's fail-mode convention: data you cannot classify is kept, never dropped.

**2. Cannot be dropped — Step 14 codifies persist-before-merge (prose).**

When a merge grant is live, Step 14's new sub-step is the #880 sequence, in order: assemble the
record → `usage_capture.py capture` → `work_summary.py summarize` (persists) → commit the
persisted JSONL line into the PR as a `chore(telemetry)` commit → **re-verify CI on the new head,
per-sha** → merge. `work_summary.py find --issue <n>` (rc 0 = landed, rc 1 = missing) is the
mechanical proof the record exists before the merge is attempted. The record's shape on this path
is documented: `outcome.merged: null` (the merge had not happened when the record was written)
plus a `follow_ups` entry naming the ordering.

**3. Never summarized twice — Step 16 gains the render-only path (prose).**

Step 16 notes that on the merge-grant path the record is already persisted, so that run renders
from it with `--no-persist` instead of calling `summarize` a second time. The guard in (1) is the
backstop, not the plan.

**4. Clarity pin.** `tests/test_wf2_clarity.py` pins the canonical Step-14 sentence, anchored to
one sentence in one file per the repo's drift-guard convention.

## Files

| File | Change |
|---|---|
| `hooks/work_summary.py` | `record_fingerprint()` + idempotency guard in `persist_record`; CLI notice |
| `skills/implement-feature/references/step-14.md` | merge-grant sub-step (AC i) |
| `skills/implement-feature/references/step-16.md` | render-only path note (AC iii) |
| `tests/hooks/test_work_summary.py` | fingerprint + guard + CLI-notice tests; **repair `test_each_line_is_independent_json`** |
| `tests/test_wf2_clarity.py` | canonical-sentence pin (AC ii) |

Plus the three version surfaces and the README changelog entry.

## A test the guard would silently hollow out

`tests/hooks/test_work_summary.py::TestPersistRecord::test_each_line_is_independent_json`
persists `_valid_record()` **twice** and then asserts every line parses as JSON. Under the new
guard the second call no-ops, so the test sees one line, still passes, and quietly stops testing
what its name claims (Step-4 self-review finding 2, Medium). It is repaired in the same commit:
the two records are made distinguishable so the multi-line property is genuinely exercised, and
the dedupe behaviour gets its own explicit tests rather than riding on a test that was written
for a different contract.

## Failure modes considered

- **Store read cost.** The guard reads the whole store per persist. The store is ~90 lines; O(n)
  on a file that grows one line per run is not worth an index.
- **Corrupt legacy line.** Unparseable lines are skipped, never fatal — they cannot match a valid
  fingerprint.
- **Concurrent writers.** Two sessions persisting different runs at the same instant can both
  read a store that lacks the other's line. Neither is a duplicate of the other, so the outcome is
  correct. Two sessions persisting the *same* run concurrently is not a case that occurs (one run,
  one Step 16), and the append itself stays a single `write` call.

## platform_apis

None new. `hashlib` and `json` are stdlib; `json` is already imported in the module, `hashlib` is
added. No network, no subprocess, no new dependency. No egress and no secret handling — the
fingerprint is computed over a record that already passed `validate_record`.
