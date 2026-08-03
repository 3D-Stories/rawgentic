# #855 — Implementation plan

**Issue:** [#855](https://github.com/3D-Stories/rawgentic/issues/855) · Part of epic #756
**Design:** `docs/planning/2026-08-03-855-enforceable-review-loop-controls.md` (revision 4, 584 lines)
**Base:** `main` @ `d3374dde` · plugin 3.118.2 · suite baseline **6938 passed / 21 skipped**
**Step 4:** CLOSED budget-exhausted, passes=3, 20 findings adopted (D145–D148)
**Revision:** 2 — Step 6 plan drift check returned 11 findings (7 High, 4 Medium, 0 Critical); all
applied. §Step-6 dispositions below.

## Branch

`feat/855-enforceable-review-loop-controls`

`feat/`, not `feature/`: the repo manual (`projects/rawgentic/CLAUDE.md` §2) and every remote branch
use conventional-commit prefixes, and the repo file wins over `steps.md`'s generic form for this
repo. Commit type matches the prefix.

## Multi-PR decomposition

Five PRs. Each follows Steps 8–14 independently. **Ordering is a hard constraint from Step-4 pass 2
(self #12 / adv #5): the admission context stays OPTIONAL at the routing layer until the prose that
supplies it has landed, so no PR ever leaves existing review callers broken.**

| PR | scope | closes |
|---|---|---|
| **1a** | `phase_executor` foundation: admission record + read model, the issue-scoped journal, platform gate, downgrade compat | `Part of #855` |
| 1b | `phase_executor` lifecycle: waves, observations, retry/blocking, fencing, in-flight migration | `Part of #855` |
| 2 | `hooks/review_transition_lib.py`: the four verbs, WAL commit, filter, deferral lineage | `Part of #855` |
| 3 | routing: `resolve_review_context`, `GATE_REGISTRY` **and roster authority**, the any-seat pending block | `Part of #855` |
| 4 | prose + shared blocks + re-sync, completion gate, `work_summary` reconciliation; admission becomes **required** | `Closes #855` |

**PR 1 was split into 1a and 1b** on Step-6 finding #8 (see the risk-band note below), and **roster
enforcement moved out of `phase_executor` into PR 3** on finding #2: `GATE_REGISTRY` is hook-owned
and `phase_executor` must never import `hooks/`, so PR 1 had no trusted source for a gate's
authoritative roster. The ledger enforces slot *uniqueness and membership within a wave*; the
*authority* for what the roster should be lives with the registry, in PR 3, along with tests J/J2.

**This plan decomposes PR 1a and 1b only.** PRs 2–4 get their own Step-5 pass when their turn comes.

### Risk-band note — read before judging the ratio

Every task below is `high`. That is not dilution, it is the subject matter: PR 1a/1b are a
persisted, concurrency-critical state machine, and the canonical criteria (module boundary,
non-trivial error/exception flow, infra/persistence, deserialization of external data) genuinely
apply to each task. Step-6 finding #8 caught six tasks mis-tagged `standard` in revision 1 and the
honest re-tag is what produced this.

The consequence is that `check_ratio_band` returns **`decompose`** for these PRs and will keep
returning it however they are split, because the ratio is homogeneous rather than diluted. The
band's documented remedy — subdivide — **has been applied** (PR 1 → 1a + 1b, roster moved to PR 3),
which reduces the blast radius per PR even though it cannot move the ratio. Recorded so the band
result is a stated, deliberate outcome rather than a silently ignored gate.

## PR 1a — `phase_executor` foundation

Red-Green-Refactor throughout (`capabilities.has_tests == true`). Tests live under
`tests/phase_executor/`, collected by the normal `pytest tests/ -q` gate. The CI pylint lanes do not
cover `phase_executor/src/`, so lint it directly when editing.

Ordering follows Step-6 finding #3: schema/read model → journal → (PR 1b) lifecycle → observations
→ completion/retry → reopening/concurrency → fencing → migration. **No task tests behavior a later
task introduces**, and per finding #4 PR-1 tests assert only observable journal/ledger state and
injected callbacks — claims about spawning, release, debiting and intake belong to the PR that owns
those callers.

### Task 1: admission record — write and read halves together

Add an optional, versioned `review_admission` keyword to `ExpectedCallLedger.append_expected`,
persist it, and expose it on `LedgerState`'s `expected` entries. The write and read halves ship as
one task (finding #11: splitting them left a task whose own tests could not observe its effect). A
record written before this change has no such key and reads as `None`.

- RED: capture the **complete current serialized line** for a dispatch and assert **byte-for-byte
  equality** between the pre-change call and `review_admission=None` — including `run_id`, which the
  existing helper already writes (finding #10; "four shipped keys" was wrong and would have made the
  test red for the wrong reason). Then a round-trip test for the populated object, and a fixture of
  only pre-upgrade records parsing with `review_admission is None`.
- riskLevel: high (module boundary — a persisted schema other code reads)
- files: phase_executor/src/phase_executor/ledger.py, tests/phase_executor/test_ledger_review_admission.py
- verification: `pytest tests/phase_executor/test_ledger_review_admission.py -q`
- commit: `feat(phase_executor): carry review admission metadata on the expected-call record (#855)`

### Task 2: the issue-scoped admission journal

The design's transaction requires an issue-scoped journal whose lock is held through reservation
**and** the run-ledger append, in the fixed order issue-journal → run-ledger. Revision 1 had no task
for it at all (finding #1) while Task 4 depended on it. Durable append with `fsync`; recovery reads
a reservation left by a crash.

- RED: the **primitive** only — round-trip and issue-key stamping; `fsync` on append; a record
  appended inside a transaction whose body then raises still persists (the fail-closed direction of
  design test R); `precheck` sees the state held under the lock and a refused append writes
  nothing; two concurrent claimants of one slot yield exactly one winner (the indivisible
  check-then-append underlying design test T); a second appender cannot acquire the lock mid
  transaction, which is what makes the fixed issue-journal → run-ledger order mean anything;
  hardened parse (symlink, oversized, non-UTF-8, malformed JSON, malformed record).
- **Corrected during implementation:** revision 2 of this plan wrote the RED list as "two
  concurrent **reopening** attempts", but reopening is Task 10 in PR 1b. That is precisely the
  ordering defect Step-6 finding #3 named — a task testing behaviour a later task introduces — and
  it survived into the revision that claimed to fix it. Wave, generation and roster semantics are
  out of this task; it delivers the locking primitive they ride on.
- riskLevel: high (infra/persistence)
- files: phase_executor/src/phase_executor/admission_journal.py, tests/phase_executor/test_admission_journal.py
- verification: `pytest tests/phase_executor/test_admission_journal.py -q`
- commit: `feat(phase_executor): issue-scoped admission journal with durable reservations (#855)`

### Task 3: POSIX platform gate

A startup gate raising exit 5 `platform_unsupported` naming the missing primitive rather than
silently degrading to a non-atomic path. Required: `fcntl.flock`, `os.open(dir_fd=…)`, `O_NOFOLLOW`,
`os.fsync` (design §20, third entry).

- RED: monkeypatch each primitive away in turn — each raises `platform_unsupported` naming it; a
  positive test asserts the gate passes on this host.
- riskLevel: high (infra/persistence — the atomicity guarantees rest on these)
- files: phase_executor/src/phase_executor/platform_gate.py, tests/phase_executor/test_platform_gate.py
- verification: `pytest tests/phase_executor/test_platform_gate.py -q`
- commit: `feat(phase_executor): fail loud when a required POSIX primitive is unavailable (#855)`

### Task 4: downgrade compatibility

Finding #7 (ambiguous, resolved here): observation records live in the **same** run ledger JSONL as
`expected` records, distinguished by `kind`. Revision 1 tested only the new reader against old data;
this tests the reverse — a base-version reader encountering records written after the upgrade.

- RED: a base-version reader parses a ledger containing `review_admission` fields and unknown `kind`
  values without raising; if it cannot, the test instead pins the documented rollback constraint
  (upgraded runs must be completed or archived before downgrade) so the limit is recorded, not
  discovered.
- riskLevel: high (migration_safety)
- files: tests/phase_executor/test_ledger_downgrade.py, phase_executor/src/phase_executor/ledger.py
- verification: `pytest tests/phase_executor/test_ledger_downgrade.py -q`
- commit: `feat(phase_executor): pin base-reader compatibility for upgraded ledgers (#855)`

### Task 5: version surfaces and changelog for PR 1a

Revision 1 named the release work in prose but gave it no task, so executing the plan exactly would
have left it undone (finding #9). All four surfaces move together:
`.claude-plugin/plugin.json`, `plugins/rawgentic/.codex-plugin/plugin.json`,
`tests/hooks/test_adversarial_review_registration.py::test_plugin_version_bumped`, and
`phase_executor/src/phase_executor/canary.py` `EXPECTED_PLUGIN_VERSION`. `feat` → minor bump,
3.118.2 → 3.119.0. README changelog entry in the exact repo shape, with the tail tokens: **no
workflow-spine change in PR 1a → no diagram REV**, and `Suite <old>→<new>`.

- RED: `pytest tests/hooks/test_adversarial_review_registration.py tests/phase_executor/test_canary_digest_pin.py tests/phase_executor/test_canary_evidence.py -q` fails before the bump and passes after.
- riskLevel: high (module boundary — the canary digest refuses the whole package on a mismatch)
- files: .claude-plugin/plugin.json, plugins/rawgentic/.codex-plugin/plugin.json, tests/hooks/test_adversarial_review_registration.py, phase_executor/src/phase_executor/canary.py, README.md
- verification: the three test files above, then the whole suite
- commit: `chore(release): 3.119.0 (#855)`

## PR 1b — `phase_executor` wave lifecycle

### Task 6: fresh wave and parallel members

The fresh-wave and parallel-member branches only. Slot **uniqueness and membership within the wave**
is enforced here; the *authority* for a gate's roster is PR 3 (finding #2). An extra member beyond
the wave's declared roster is refused `roster_full`.

- RED: two reviewers on one wave admitted to distinct slots (design test I); a third refused
  `roster_full` (test J); a duplicate slot claim refused.
- riskLevel: high (non-trivial error/exception flow)
- files: phase_executor/src/phase_executor/admission_journal.py, tests/phase_executor/test_wave_members.py
- verification: `pytest tests/phase_executor/test_wave_members.py -q`
- commit: `feat(phase_executor): fresh review waves with unique member slots (#855)`

### Task 7: observation persistence

The five record kinds — `success`, `spawn_failure`, `timeout`, `dead_result`, `cancelled` —
persisted to the run ledger. Persistence only; classification is Task 8 (finding #11 split).

- RED: each kind round-trips and is readable by the fold; an append failure propagates loudly rather
  than returning.
- riskLevel: high (infra/persistence)
- files: phase_executor/src/phase_executor/ledger.py, tests/phase_executor/test_observations.py
- verification: `pytest tests/phase_executor/test_observations.py -q`
- commit: `feat(phase_executor): persist review dispatch observations (#855)`

### Task 8: envelope classification and the termination-path matrix

A `success` requires a structurally valid reviewer envelope; blank, malformed and vacuous output all
classify as `dead_result`. Finding #5: revision 1 tested one vacuous payload and promised three, and
covered no termination matrix.

- RED: a matrix over success, spawn failure, timeout, signal/non-zero result, cancellation, parse
  failure, blank output, malformed output and vacuous output — each yields **exactly one** fenced
  observation or a loud error (design test AF).
- riskLevel: high (deserialization of external data)
- files: phase_executor/src/phase_executor/ledger.py, tests/phase_executor/test_envelope_classification.py
- verification: `pytest tests/phase_executor/test_envelope_classification.py -q`
- commit: `feat(phase_executor): classify reviewer envelopes, one observation per termination path (#855)`

### Task 9: slot completion, retry allowance, blocked waves

A slot is *completed* only by `success`; the four failure kinds are terminal **attempts** leaving it
retryable; exhausting `RETRY_LIMIT` marks the wave `blocked`.

- RED: a slot with only `dead_result` is not complete (design test K2); a retry in the same
  generation is admitted (test N); exhausted retries mark the wave `blocked` (test P). Assertions
  are on journal state only — "no release" and "intake refuses" belong to PR 2 (finding #4).
- riskLevel: high (non-trivial error/exception flow)
- files: phase_executor/src/phase_executor/admission_journal.py, tests/phase_executor/test_slot_completion.py
- verification: `pytest tests/phase_executor/test_slot_completion.py -q`
- commit: `feat(phase_executor): distinguish completed slots from terminal attempts (#855)`

### Task 10: reopening, digest policy, store-assigned generations

The reopening branch: a changed digest is required, the generation is assigned by the store, and a
caller-supplied generation is refused.

- RED: reopening with an unchanged digest refused `digest_unchanged` (test E); an explicit
  caller `generation` refused (test H); a fresh gate gets 0 and a reopening N+1.
- riskLevel: high (non-trivial error/exception flow)
- files: phase_executor/src/phase_executor/admission_journal.py, tests/phase_executor/test_reopening.py
- verification: `pytest tests/phase_executor/test_reopening.py -q`
- commit: `feat(phase_executor): reopenings require a changed digest and a store-assigned generation (#855)`

### Task 11: fencing tokens and reservation deadlines

Each reservation carries a fencing token and a deadline. Finding #5: revision 1 tested only stale
rejection with no positive-path accept.

- RED: an observation bearing the **current** token is accepted; one bearing a stale token is
  rejected (test Q); an expired reservation is retryable and its retry's token differs.
- riskLevel: high (infra/persistence — recovery correctness)
- files: phase_executor/src/phase_executor/admission_journal.py, tests/phase_executor/test_fencing.py
- verification: `pytest tests/phase_executor/test_fencing.py -q`
- commit: `feat(phase_executor): fence review reservations with tokens and deadlines (#855)`

### Task 12: in-flight grace import

Finding #6: "first contact" alone cannot identify a pre-upgrade record's issue, gate or slot — it has
none of them. An explicit import API receives **trusted resolved context**, binds it to the legacy
correlation id and run, and persists a one-time import marker.

- RED: an outstanding pre-upgrade review imports into generation 0 with the counters untouched; a
  second import attempt is refused by the marker; conflicting issue/gate/slot claims are refused;
  replay is idempotent.
- riskLevel: high (infra/persistence — migration of live run state)
- files: phase_executor/src/phase_executor/admission_journal.py, tests/phase_executor/test_inflight_grace.py
- verification: `pytest tests/phase_executor/test_inflight_grace.py -q`
- commit: `feat(phase_executor): import outstanding pre-upgrade reviews into generation 0 (#855)`

### Task 13: version surfaces and changelog for PR 1b

Same four surfaces as Task 5; `feat` → 3.119.0 → 3.120.0. **No workflow-spine change → no diagram
REV.** `Suite <old>→<new>` from the measured run.

- RED: the three version tests fail before the bump, pass after.
- riskLevel: high (module boundary — canary digest)
- files: .claude-plugin/plugin.json, plugins/rawgentic/.codex-plugin/plugin.json, tests/hooks/test_adversarial_review_registration.py, phase_executor/src/phase_executor/canary.py, README.md
- verification: the three test files, then the whole suite
- commit: `chore(release): 3.120.0 (#855)`

## Parallel groups

None declared. Within each PR the tasks concentrate on
`phase_executor/src/phase_executor/admission_journal.py` and `ledger.py`, so no group's file sets are
pairwise disjoint and `validate_parallel_groups` would degrade any candidate group to serial.
Declaring a group that cannot be proven disjoint would be a false claim: **both PRs execute
sequentially.**

## Verification

Per task above. After the last task of each PR, the WHOLE suite:
`/home/rocky00717/.local/bin/pytest tests/ -q`, judged by exit code, delta stated against the
recorded baseline **6938 passed / 21 skipped**. Both pylint lanes verbatim from
`.github/workflows/lint.yml`, plus `pylint` directly over `phase_executor/src/` since CI does not
cover it. No deferred-to-target verification — everything here is exercisable locally.

## Migrations / config

No schema *migration*: the ledger change is additive (Task 1) and live-run state is handled by the
grace import (Task 12). **Rollback is not unconditional** (finding #7): Task 4 pins whether a
base-version reader tolerates the new field and the new `kind` values. If it does, `git revert` is
safe; if it does not, the recorded constraint is that upgraded runs must be completed or archived
before downgrading. Revision 1's flat "no persisted-state shape change, safely revertible" was
wrong — Task 1 adds a persisted field and Task 7 adds persisted records.

## Documentation

Version and changelog work is now Task 5 (PR 1a) and Task 13 (PR 1b) rather than prose. No other
user-facing docs change in these two PRs; PR 4 changes the workflow spine and will need a diagram REV.

## Step-6 dispositions

11 findings, 7 High / 4 Medium, 0 Critical. **All adopted; none declined.** Two were flagged
ambiguous and are resolved here rather than left open.

| # | finding | disposition |
|---|---|---|
| 1 (H) | no task creates the issue-scoped journal Task 4 depended on | **adopted** — new Task 2 |
| 2 (H) | PR 1 cannot know a gate's authoritative roster (`GATE_REGISTRY` is hook-owned, PR 3) | **adopted** — roster authority and tests J/J2 moved to PR 3; PR 1b keeps slot uniqueness only |
| 3 (H) | tasks tested behavior later tasks introduce | **adopted** — reordered to schema → journal → lifecycle → observations → completion → reopening → fencing → migration |
| 4 (H) | RED checks pass vacuously at their layer (no-spawn, no-release, no-debit) | **adopted** — PR-1 tests assert journal/ledger state only; caller-path claims move to the owning PR |
| 5 (H) | no termination matrix; one vacuous case for three promises; no positive-token test | **adopted** — Task 8 matrix, Task 11 positive accept |
| 6 (H) | grace import cannot identify a legacy record's issue/gate/slot | **adopted** — Task 12 explicit import API + one-time marker + conflict tests |
| 7 (M, ambiguous) | rollback claimed safe while persisted shape changes; observation file unstated | **resolved + adopted** — observations share the run ledger, keyed by `kind`; Task 4 pins base-reader behavior; the Migrations section is corrected |
| 8 (H) | six tasks mis-tagged `standard` | **adopted** — all re-tagged `high` with criteria; PR 1 split into 1a/1b; the resulting `decompose` band is stated, not ignored |
| 9 (M) | version/changelog work had no task | **adopted** — Tasks 5 and 13 |
| 10 (M, ambiguous) | "four shipped keys" ignores `run_id` | **resolved + adopted** — Task 1 asserts byte-for-byte equality of the complete serialized line |
| 11 (M) | Task 5/6 overloaded, Tasks 1/2 over-split | **adopted** — 1+2 merged into Task 1; lifecycle split across Tasks 6, 9, 10; observations split into Tasks 7 and 8 |
