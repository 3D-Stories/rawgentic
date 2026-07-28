# Test suite review — is 5,702 tests excessive?

**Date:** 2026-07-28 · **Repo:** `3D-Stories/rawgentic` · **Status:** report only, no test or source file changed
**Trigger:** the owner observed "over 5,000" CI tests and asked whether they should be trimmed.

## Verdict: keep 5,687 · change 15 · delete 0

The count is not the cost. The whole suite runs in 2m39s, and 27% of that is spent by fifteen
tests. Trimming the other 5,687 buys back milliseconds and costs guards that catch real
regressions.

Where a claim is inferred rather than measured, it says so.

---

## 1. Baseline — diff any future "no regressions" claim against this

```
pytest tests/ -q --durations=15
5681 passed, 21 skipped, 5 warnings in 159.03s (0:02:39)     exit code 0
```

| Metric | Value | Source |
|---|---|---|
| Tests collected | **5,702** | `pytest tests/ -q --collect-only`, verified on `origin/main` @ `ae26701` |
| Test files | 143 `test_*.py` | same |
| Passed / skipped / failed | **5,681 / 21 / 0** | full run, exit 0 |
| Wall clock | **159.03s** | full run on `origin/main` |
| Average per test | **0.028s** | 159.03 / 5,702 |
| Parametrized cases | 1,168 | collection lines containing a bracket |
| Distinct test functions | 4,534 | collection lines without one |
| Test code | ~60,150 lines / 151 `.py` | `tests/` |
| Source code | 26,821 lines / 36 files | `hooks/*.py` |
| Test-to-source ratio | **2.24x** | 60,150 / 26,821 |

**Measurement note.** Both numbers are verified on `origin/main` @ `ae26701`: the count
(5,702 across 143 files) and a full run of **5,681 passed / 21 skipped / 0 failed in 159.03s,
exit 0**, executed in a clean worktree containing only this review's two documents. An earlier
run on the sibling branch `fix/682-bind-first-resume` gave 159.52s — the two agree within half
a second, so the per-test and concentration figures below hold on main.

### CI cost — the test step is ~87% of the job

Run `30395284615`, job `test`:

| Step | Duration |
|---|---|
| Set up job, checkout, setup-python, jq | 3s |
| Install security scanners (gitleaks, semgrep, osv-scanner) | 14s |
| Install test deps | 3s |
| **Run tests** | **135s** |
| Post steps | 0s |

Last ten CI runs: all `success`, 130-172s.

---

## 2. Why there are 5,702 — a growth artifact, not bloat

Line counts from `git ls-tree` at the last commit before each date on `main`:

| Date | Source (`hooks/*.py`) | Tests (`tests/**/*.py`) | Ratio | Skills |
|---|---|---|---|---|
| 2026-04-01 | 1,054 | 4,679 | 4.44x | 16 |
| 2026-05-01 | 896 | 4,757 | **5.31x** | 15 |
| 2026-06-01 | 1,621 | 5,812 | 3.59x | 15 |
| 2026-07-01 | 5,844 | 12,641 | 2.16x | 18 |
| 2026-07-28 | 26,821 | 60,150 | **2.24x** | 22 |

Two conclusions, and they carry the recommendation:

1. **The project quadrupled in July.** Source went 5,844 to 26,821 lines in 27 days (4.6x).
   Tests went 12,641 to 60,150 (4.8x). They grew together.
2. **The ratio fell by more than half since May** (5.31x to 2.24x) then held flat. A suite that
   was genuinely bloating would show this climbing. There is proportionally *less* test code per
   line of source now than three months ago.

Not a parametrize explosion either: only 1,168 of 5,702 collected items come from parametrized
cases; the largest single case count is 48. The other 4,534 are distinct functions.

---

## 3. CHANGE — 15 tests, 43.78s, 27.4% of the run

This is the entire optimization surface. The other 5,687 tests share the remaining 115.7s, about
20 milliseconds each.

| # | Test | Sec | Why slow | Action |
|---|---|---|---|---|
| 1 | `tests/hooks/test_executor_routing.py::test_cli_dispatch_single_workspace_read` (line 3301) | **15.43** | Wraps a real `_do_dispatch()` to assert one integer: dispatch reads the workspace exactly once | **Fix first.** The assertion is `len(opens) == 1` and needs none of the real dispatch work |
| 2 | `tests/hooks/test_security_scan.py::TestRealToolSmoke::test_scan_repo_does_not_crash` | 6.66 | Invokes real scanners | **Keep.** Being un-mocked is the entire value |
| 3 | `tests/hooks/test_notes_size_handler.py::TestMemorypalaceIngestion::test_no_server_call_under_threshold` | 3.07 | Server/timeout path | Fake clock or shorter timeout constant |
| 4 | `tests/phase_executor/test_supervisor_launch.py::test_await_timeout_writes_supervisor_timeout_obs` | 2.23 | Sleeps out a real timeout | Fake clock |
| 5 | `tests/phase_executor/test_supervisor_launch.py::test_launch_threads_correlation_id_into_spec_and_timeout_obs` | 2.23 | Sleeps out a real timeout | Fake clock |
| 6 | `tests/hooks/test_adversarial_review_codex.py::test_run_timeout_is_timeout` | 2.01 | Sleeps out a real timeout | Fake clock |
| 7 | `tests/hooks/test_session_mining_lib.py::TestProposeDispositionCLI::test_declined_never_reproposed` | 1.86 | CLI subprocess | Consider in-process invocation |
| 8 | `tests/hooks/test_wal_guard.py::TestHugeCommandDeny::test_headless_huge_deny_still_audits` | 1.74 | Builds a very large payload | Shrink to the smallest size still over the threshold |
| 9 | `tests/hooks/test_session_mining_lib.py::TestStep8aFindings::test_evidence_set_change_triggers_evidence_updated` | 1.46 | CLI subprocess | Consider in-process invocation |
| 10 | `tests/hooks/test_session_mining_lib.py::TestDetectCLI::test_rerun_without_change_appends_nothing` | 1.39 | CLI subprocess | Consider in-process invocation |
| 11 | `tests/hooks/test_driver_bench.py::test_matrix_72_cells_deterministic` | 1.33 | Iterates 72 cells | **Keep.** Cost is proportional to real coverage |
| 12 | `tests/phase_executor/test_quota.py::test_cross_process_ceiling` | 1.13 | Real cross-process concurrency | **Keep.** The concurrency IS the assertion |
| 13 | `tests/phase_executor/test_packaging.py::test_wheel_builds_installs_and_imports_without_conftest` | 1.10 | Builds and installs a wheel | **Keep.** Inherently slow, high value |
| 14 | `tests/hooks/test_plan_lib.py::TestPublicFileLock::test_file_lock_serialises_two_writers` | 1.07 | Real file-lock contention | **Keep.** The contention IS the assertion |
| 15 | `tests/phase_executor/test_herdr_ac1_protocol.py::test_sentinel_self_times_out_without_a_release` | 1.07 | Sleeps out a real timeout | Fake clock |

**Realistic gain from fixing #1 plus the five sleep-bound tests (#3, #4, #5, #6, #15): about 26s
off 159.5s, roughly 16%.** Six of the fifteen are deliberately slow for good reasons and should be
left alone.

---

## 4. DELETE — 0 tests

Nothing met the bar. What was checked and why each candidate survived:

| Candidate class | Finding | Verdict |
|---|---|---|
| Permanently skipped / dead tests | 21 skipped at runtime; reasons are all environment-conditional: `tmux not installed`, `codex CLI not on PATH`, `claude CLI not on PATH`, `uv not installed`, `root ignores file permissions` | **Keep.** Correct `skipif` guards that run on a provisioned box, not dead code |
| Parametrize explosion | 1,168 of 5,702 (20%); largest case count 48 | **Keep.** No runaway matrix |
| Redundant duplicate tests | 51 test names appear more than once, spanning 105 tests | **Keep pending review** — section 5 |
| Trivially cheap filler | No assertion-free or tautological tests found | No action |

**Deleting here is asymmetric.** A large share of this suite is guard tests: version-surface
checks, skill-registration counts, changelog and diagram-drift guards. Per the workspace manual
each exists because that exact failure happened and was written down — the three-version-surface
miss, the multi-surface skill registration, the changelog omission, the diagram REV decision.
Removing a guard to save 20 milliseconds re-opens a known wound.

---

## 5. REVIEW — 105 tests across 51 duplicated names (human eye, low expected yield)

51 test-function names appear more than once. Highest counts:

| Name | Occurrences |
|---|---|
| `test_skill_dir_and_frontmatter_exist` | 3 |
| `test_doc_exists` | 3 |
| `test_absent_is_valid_legacy_record` | 3 |
| `test_writes_current_session_id` | 2 |
| `test_wiring_sentence_present` | 2 |
| `test_the_subcommand_exists` | 2 |
| `test_template_parses_and_carries_example` | 2 |
| `test_style_resolution_canonical_sentence_present` | 2 |

**This is a lead, not a finding.** Repeating a name across modules is normal and usually correct —
`test_doc_exists` in three skill-registration modules almost certainly asserts three different
docs. Worth one pass to confirm none are literal copy-paste against the same target. Do not
bulk-delete on name collision alone.

Regenerate:

```bash
grep -rhoE '^\s*def (test_[a-zA-Z0-9_]+)' tests/ | sed 's/.*def //' | sort | uniq -c | awk '$1>1'
```

---

## 6. The two levers

### Lever 1 — fix the 15.4s outlier (measured, high confidence)

`test_cli_dispatch_single_workspace_read` spends 15.43s asserting one integer: about a tenth of
the whole suite in one test. The assertion does not require the real dispatch to execute.

### Lever 2 — run in parallel (INFERRED, not measured)

The suite is overwhelmingly independent unit tests, the ideal shape for `pytest-xdist`.
**`pytest-xdist` is not currently installed.**

- The rough 4x estimate is inferred from workload shape, **not measured**.
- **Check this first:** the suite contains genuine concurrency tests — `test_cross_process_ceiling`,
  `test_file_lock_serialises_two_writers`, `TestConsumeLoopback::test_concurrent_consume_does_not_overspend`
  — plus wall-clock timing assertions in the fan-out tests. Some may need `--dist loadfile` or an
  xdist group marker.
- **How to confirm:** install it, run `pytest tests/ -n auto`, and require **5,681 passed /
  21 skipped / 0 failed**. Any deviation means a test depends on serial execution — fix or group
  it, never accept a lower count.

---

## 7. Do NOT do

- Do not delete tests to reduce the count. The count is not the cost.
- Do not remove guard, registration or drift tests. They encode recorded past failures.
- Do not touch the six deliberately-slow tests (#2, #11, #12, #13, #14 and the real-tool smoke).
- Do not judge "no regressions" against anything but the baseline in section 1.

---

## 8. Acceptance criteria for any cleanup PR

1. `pytest tests/ -q` reports **5,681 passed, 21 skipped, 0 failed, exit 0** — same or better.
2. Wall clock reported before and after, read from the runner's own final line.
3. No test deleted without a one-line justification naming the coverage lost.
4. The three version surfaces and the diagram REV decision handled per the repo checklist.

---

## 9. What I would most expect to be wrong

**The parallel-execution estimate.** It is the only unmeasured claim here. If several concurrency
or timing tests depend on serial execution, the real gain could be far below 4x and the change may
not be worth its risk. Measure before committing.

Secondary: the duplicate-name review may yield nothing. It is included because it is cheap to
check, not because there is evidence of redundancy.

---

## 10. Reproduce

```bash
cd projects/rawgentic
pytest tests/ -q --collect-only | tail -3     # count
pytest tests/ -q --durations=15               # baseline and slowest
gh run view <run-id> --json jobs              # CI step timings
```

Growth figures: `git ls-tree -r <commit> --name-only` over `hooks/*.py` and `tests/**/*.py`.

No test or source file was changed to produce this review.
