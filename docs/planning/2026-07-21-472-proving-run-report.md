# #472 proving run — report (W9, epic #475)

**Verdict:** PARTIAL — the wired executor path (merged W1–W8) **held for every read-only seat it
dispatched** (9/9 observations `requested == actual`, receipts + schema-v2 Observations durable),
and **is NOT end-to-end-ready for supervised mutating builds**: five WF2 design-gate passes plus
code verification converged on six missing-engine-capability defects, now decomposed into hardening
epic **#560** (children #554–#559). #472 ships this report (AC4), the five safe self-contained
fixes (red-first, below), and the epic filing. AC2 (live codex cell) and AC3 (live account-switch /
quota cycle) are **deferred** to the epic children that build their prerequisites — no capture is
claimed that did not happen.

**Author:** WF2 orchestrator (sessions 1bce2405 design · 641a6f27 implementation) ·
**Date:** 2026-07-21 · **Run evidence:** `.rawgentic/runs/wf2-472-1bce2405/routing-audit.jsonl`
(local run telemetry, not committed — integrity pin: **sha256
`f092b7ef0377170ad5ab57fc0ef999845880985dcaf84c68193ee06e961e69f5`**, 19 records = 10 receipts +
9 observations; the per-seat table in §1 is the full summary of those records)

## 1. What the wired path PROVED (confirmed, from the routing audit)

The run flipped `executorRouting` (analysis/review/build → executor) and executed the first
sustained real use of the wired dispatch path. Read directly from
`routing-audit.jsonl` (10 receipt lines + 9 observation lines):

| Seat | Dispatches | requested_model | actual_model | Identity |
|---|---|---|---|---|
| analysis (Step 2 fan-out) | 3 | claude-sonnet-5 | claude-sonnet-5 | 3/3 match |
| review (Step 4 self-review passes) | 6 | gpt-5.6-sol | gpt-5.6-sol | 6/6 match |

- **Routing held:** `requested == actual` on **all 9** observations; all 9 are schema-version-2
  Observations with receipts joined in the same audit log.
- **Cross-model enforcement held — including its refusal path.** The audit's one non-pass receipt
  (verdict `fail`, violation `author_provider_missing`, target claude-fable-5 on the review seat)
  is the D9 cross-model rule REFUSING a review dispatch that omitted `--author-provider`; the
  corrected dispatch routed to gpt-5.6-sol and passed. The enforcement receipt layer denied
  exactly what it was built to deny, before any spawn. Receipts: 9 pass / 1 fail (fail-closed).
- **The Observation contract behaved as specified** on every read-only dispatch: prompt hashes,
  usage, timing, `dispatched_lane`, correlation ids present and schema-valid.

(The r6 design's prose said "5 review dispatches"; the audit records **6** — the audit is the
source of truth and the count here is read from it.)

## 2. The finding: six capability gaps block the full end-to-end proving run

Each verified in code during the design passes (file:line on main @ 6e1e3f2). This inventory IS
the proving-run result — the remaining Critical/High were missing-engine-capability defects, not
design-prose defects, which is why the owner decomposed (D-7). Epic **#560**, dependency-ordered:

| # | Gap (evidence) | Blocks | Epic child |
|---|---|---|---|
| 1 | `exited_no_sentinel` yields no Observation; failure states not reconcilable (`supervisor.py:682-687`) | reconcile joins | **#557** (H4, foundational) |
| 2 | `reconcile_run` has no pause/recover model — a quota_paused→recovered call can't satisfy one logical expected call (`enforce.py:513-537`) | AC3 | **#554** (H1, dep #557) |
| 3 | Dispatch chokepoint is ledger-blind — no expected-call ledger, no post-`run_closed` rejection (`executor_routing_lib.py` dispatch path) | audit completeness | **#555** (H2) |
| 4 | Codex canary is composition-validation only — no behavioral sandboxed write probe with positive/negative controls (`canary.py:341-355`; spike #452 §5) | **AC2** | **#556** (H3) |
| 5 | No genuine quota detection/classification, no per-call token/turn/$ caps (`await_job` has no stderr-keyed `quota_paused` finalization) | **AC3** | **#558** (H5) |
| 6 | No account-switch recovery harness (account-identity capture, audited resume, cross-account verdict) | AC3 live answer | **#559** (H6, closes #560) |

## 3. The five safe fixes this PR lands (red-first, all landed)

All five were undisputed across the five review passes; each had a failing test before the fix:

| Fix | Root cause (on main) | Red → green | Commit |
|---|---|---|---|
| **F7** receipt_nonce durability | `JobRecord.receipt_nonce` declared (`registry.py:66`) but omitted from serialize/deserialize — the audit join lost on every crash/resume | `test_registry_persists_receipt_nonce` failed on main; additive field in `_record_to_dict`/`_record_from_dict` | 77d9079 |
| **D2** correlation threading | `launch` + `synthetic_observation` hardcoded `correlation_id=None` (`supervisor.py`) | 2 red cells (kwarg TypeErrors); additive params + timeout-site spec read; `pane_runner.py:117` already propagates | 0f8ef20 |
| **D1** supervised codex composition | `_run_supervised` called `build_command(model, effort=, profile=)` — codex signature `(model, cwd, *, effort)` has no `profile` kwarg → TypeError on EVERY supervised codex build (and codex is the only `MUTATING_FS_SANDBOXED` engine, so every supervised mutating dispatch died) | 3 red cells; `compose_supervised_argv` selects by engine + `profile.mutating` (mutating codex → `build_mutating_command` with worktree-pinned `writable_roots`); root cause pinned by `test_codex_build_command_rejects_profile_kwarg` | f4e36c8 |
| **D3** supervised audit append | Supervised branch appended its receipt but never its Observation — a completed/timed-out supervised job vanished from the routing audit | 2 red cells; STEP 6.5 verdict-independent append (completed / completed_with_residue / timed_out; `exited_no_sentinel` = receipt-only → #557), stamped `dispatched_lane` + correlation, mirroring the sync path | f4e36c8 |
| **D7** promote path-policy | `promote(path_policy=None)` skipped the changed-path check — a fail-open promotion boundary (`worktree.py`) | red: omission promoted silently; `path_policy` now REQUIRED, `None` refused (TypeError), `PROMOTE_ANY` is the explicit allow-all; 9 test callers updated (no production callers — grepped) | 86792a9 |

Coherence: these make the supervised dispatch path *composable* (D1), *correlation-bound* (D2),
*auditable* (D3), its promotion boundary *fail-closed* (D7), and its recovery join *durable* (F7)
— the foundation #560's builds sit on, with zero new machinery.

## 4. A4/F11 honest disposition

The SIGKILL-proxy assumption for quota residue remains **UNCONFIRMED**. A genuine usage-limit exit
was **not captured** in this run — the capability to detect and reconcile one does not exist yet
(gaps 2 and 5 above; #554/#558). The acceptance-criteria doc
(`docs/planning/2026-07-17-orchestrator-executor-acceptance-criteria.md`) is corrected in this PR
to record the deferral instead of claiming a capture that did not happen. #559 re-runs the full
end-to-end cycle (including AC2 via #556) once the prerequisites exist.

## 5. What was NOT checked

- No live codex mutating spawn was executed in #472 (D1's composition is validated by the
  existing compose-time self-check, not a live run) — that live cell is #556/#559.
- No live quota exhaustion or account switch was driven (owner-accepted burn deferral, D-8) —
  #558/#559.
- The supervised tmux path end-to-end (probe → launch → await → promote) has unit coverage with
  injected seams only; the RUN_LIVE cell is #559.
