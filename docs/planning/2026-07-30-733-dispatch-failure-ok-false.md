# #733 — a SIGKILLed seat returns ok:true / exit 0: design

Issue: [#733](https://github.com/3D-Stories/rawgentic/issues/733) · Epic #756 child 2/17 ·
Complexity: standard_feature (full spine) · Author session: cf8ac68a · Date: 2026-07-30 ·
**rev 4 — FINAL** (pass-3 budget-exhausted close, owner RG-255373 option 1: apply final fixes,
proceed to build, no fourth review round; pass-1 owner-approved RG-562362)

## Problem (confirmed at main c46d8e8)

`executor_routing_lib.py dispatch` returns `ok: true` / exit 0 for a seat whose process was
SIGKILLed at the timeout, so a killed, half-finished review reads as a passed gate.

Root cause chain (all confirmed at source, Step 2 + gate pass 1):

1. `run_seat` (`phase_executor/src/phase_executor/engine.py:163-166`) correctly treats
   `AVAILABILITY_FAILURES = {nonzero_exit, timeout, launch_error, no_response}` as
   walk-the-chain failures — and on chain exhaustion deliberately **returns the last failed
   observation** ("honest, non-ok") so the audit keeps the evidence.
2. `enforce.verify_post` (`phase_executor/src/phase_executor/enforce.py:244-278`) returns
   `verified=True` for **any** observation carrying a matching attested `actual_model`,
   regardless of `parse_status` — deliberate identity semantics. It answers "did we route to
   the right model?", never "did the dispatch succeed?".
3. **Every consumer that equates `verified` with success inherits the bug** (gate pass-1
   finding, all confirmed at source):
   - sync dispatch result assembly (`hooks/executor_routing_lib.py:894-908`) — the observed
     #733 case;
   - `reconcile_run`'s served-call check (`enforce.py:649-651` — a verified-identity timeout
     counts as `saw_verified`, so a killed attempt reads as a served expected call);
   - `collect_work_product` authorization (`executor_routing_lib.py:1573-1578` — a
     verified-identity timeout observation can authorize promotion of PARTIAL build output);
   - `recover_run`'s completed branch (`executor_routing_lib.py:1803-1808`);
   - supervised (`:1357-1384`) and resume (`:1467-1488`) gate `state != "completed"` first,
     so only the completed-with-availability-envelope sibling remains there.

Out of scope / already done: the flat 300 s default timeout was fixed by PR #753
(`resolve_dispatch_timeout`, `executor_routing_lib.py:83`, wired `:2357`) — AC5's remaining
half is contract prose only (D9 reassessment).

## Approaches

**A (chosen, rev 2 shape) — one process-success predicate in the core contract, applied at
every consumer.** The predicate lives in `phase_executor/src/phase_executor/contract.py` (the
shared leaf both `enforce` and the hooks CLI can import without cycles — the
`AVAILABILITY_FAILURES` precedent), and every consumer that today reads `pc.verified` as
success also consults it. Identity verification (`verify_post`) is deliberately untouched.

**B (rejected) — overload `verify_post`/`verified`.** `verified` has precise identity
semantics reconcile/audit depend on (`usage_unavailable` with matching id is deliberately
verified); overloading re-creates the confusion this bug is made of.

**C (rejected) — raise from `run_seat` on chain exhaustion.** Returning the last observation
is a deliberate engine contract (audit keeps the evidence); raising loses the partial payload.

## Design (rev 2)

### 1. Core predicate — `contract.observation_process_failure(obs) -> Optional[str]`

New pure function in `phase_executor/src/phase_executor/contract.py`, beside
`AVAILABILITY_FAILURES` (its consumers: hooks CLI, `enforce.reconcile_run`,
`collect_work_product`, `recover_run`):

- Accepts an `Observation` or its dict form (the `verify_post` precedent).
- **Type-checked, never raises** (gate findings, adopted; Step-6 REOPENS 733-p2-1 resolved):
  `process` contributes only when it is a mapping (or the Observation's own dict);
  `exit_code` contributes only when it is an `int` and not a `bool` — a malformed
  `exit_code`/`process` reads as "no signal from THAT field". **`parse_status` is governed by
  the allowlist, not the no-signal rule:** a non-string or otherwise malformed
  `parse_status` is not an allowlisted success value, so under deny-by-default it FAILS with
  reason `"malformed_status"` — malformed evidence can weaken a failure label, never
  manufacture a success. T6 pins this exact outcome for list/dict/None status rows.
- **Success is an explicit ALLOWLIST (rev 4, pass-3 High — deny-by-default):** the predicate
  returns `None` (no failure) ONLY when `parse_status ∈ {OK, USAGE_UNAVAILABLE}` (the two
  deliberately-accepted states: clean success, and attested-identity output missing only
  usage counts) AND no process evidence contradicts it. Every other status fails — this
  closes the confirmed Hermes escape (`hermes_http.py:284-291` reports a definite submission
  failure with matching platform identity, `parse_error`, exit 0; `base.py:115` yields
  `PARSE_ERROR`; identity-only verification passes it) and the whole not-yet-enumerated
  class with it.
- **Classification precedence** (within a failing observation): timeout evidence first
  (`process.timed_out is True` or `parse_status == TIMEOUT`) → `"timeout"`; then **any
  non-zero integer `exit_code`** — `"signalled"` when negative, `"nonzero_exit"` when
  positive (a non-zero exit did not complete cleanly regardless of claimed status); then the
  non-allowlisted `parse_status` string itself (e.g. `"parse_error"`). Labeling note
  (pass-3): the same SIGKILL can surface as `-9` (POSIX child) or `137` (wrapper-mediated) —
  the failure VERDICT is identical either way; only the best-effort label differs, and no
  consumer branches on the label.
- Adapter provenance (gate finding, addressed as prose): real observations are produced by
  `adapters/base.py` — `resolve_parse_status` (`:102-110`) maps `timed_out` → `timeout` and
  any nonzero exit → `nonzero_exit`, so an adapter-produced `parse_status: "ok"` implies
  `exit_code ∈ {0, None}` and `timed_out: false` (`observation.schema.json` pins this).
  The negative-exit and `timed_out` checks therefore guard synthetic/legacy/supervised
  envelopes, not a platform-dependent decoding of adapter exits.

### 2. Result enrichment — partial evidence on EVERY observation-bearing failure path

Small helper in `hooks/executor_routing_lib.py`:

```python
def _attach_partial(res: dict, obs) -> dict:
    """Attach partial-output evidence from obs to a failure result (AC4). partial is
    true only when a payload exists — a dispatch that produced nothing must not claim
    a partial. Reads Observation or dict form; never raises."""
```

sets `partial` (payload-conditional), `parse_status`, `partial_payload`, `raw_capture_path`,
`observation` on the result dict. **`partial` is precisely `parsed_payload is not None`
(rev 4):** empty containers, `""`, `0`, and `false` ARE payloads (partial: true); only
absent/`null` is no-payload — key presence is not the test, the parsed value is. Applied to (gate finding, adopted — the no-identity paths
must not drop the payload):

- the NEW process-failure returns (below);
- the existing sync `chain_exhausted_availability` return (`:898-901`);
- the supervised/resume `state != "completed"` and `not pc.verified` returns, when an
  observation exists (`completed_with_residue` etc. carry one);
- correlation-valid `recover_run` entries whose completed observation fails the predicate
  (rev 3 — the recovery path is a failure path too, and its entry keeps the evidence).

**Correlation-ownership boundary (rev 3, pass-2 High — normative):** `_attach_partial` is
permitted ONLY on results whose observation the current dispatch OWNS (its own correlation).
The `correlation_mismatch` refusal paths (supervised `:1346-1354`, resume `:1457-1463`, and
recovery's foreign-correlation handling `:1785-1794`) are explicitly EXCLUDED: a foreign
observation's `parsed_payload`/`raw_capture_path` must never ride back to the current caller
— those results expose only the minimal mismatch metadata they already carry (the two
correlation ids). "Every observation-bearing failure path" in this design means every
correlation-owned one; regression tests pin the exclusion (T15).

**Failure precedence and evaluation order, stated normatively (rev 4, pass-3 High):** in each
of the four result assemblies (sync, supervised, resume, recovery), the process-failure value
is computed FIRST — before any correlation-owned failure return — and `_attach_partial` runs
on every correlation-owned failure result, the breach returns included. The WINNING VERDICT
is unchanged: an identity breach (`pc.ok == False`) stays `EXIT_ENFORCEMENT` (retrying
re-bills the wrong route; never softened to availability); process failure owns the verdict
only when no breach won. Foreign-correlation refusals stay excluded (§2 boundary).
**`retryable` per path (rev 4):** sync process-failure results carry `retryable: True` — the
engine itself killed/observed the subprocess, so death evidence is positive by construction.
Supervised and resume process-failure results carry `retryable: False` — a completed-state
envelope that fails the predicate is NOT proven death under the ratified policy (epic log
D3); the ERROR protocol owns any retry decision there.

### 3. Consumer application

| Consumer | Change |
|---|---|
| sync dispatch (`:894-908`) | after `pc` checks: `fail = contract.observation_process_failure(final_obs)` → `_attach_partial(_err(EXIT_AVAILABILITY, f"dispatch_{fail}", ..., retryable=True), final_obs)` |
| supervised (`:1366-1384`) | same guard between `pc.verified` and ok-return, code `supervised_dispatch_<fail>`; `_attach_partial` on its failure returns |
| resume (`:1472-1488`) | same, code `resume_dispatch_<fail>` |
| `reconcile_run` (`enforce.py:649-651`) | `saw_verified` requires `verified AND observation_process_failure(o) is None`; a verified-identity availability failure falls into the existing "legitimate fallback attempt" bucket (neither served nor breach) — the run-end verdict semantics for breaches are unchanged |
| `collect_work_product` (`:1573-1578`) | the authorizing `any(...)` additionally requires `observation_process_failure(...) is None` — a killed build can no longer authorize promotion of partial output |
| `recover_run` (`:1803-1808`) | in the completed branch, after the `pc` checks: process failure → `entry["verify"] = f"process_failure: {fail}"`, `worst = max(worst, EXIT_AVAILABILITY)`, and the entry carries the partial-evidence fields via `_attach_partial` (rev 3 — correlation-valid entries only) |

- Exit code: `EXIT_AVAILABILITY` (3) — no renumbering of shipped codes (#427/#464/#470).
- `retryable: True` is honest on the sync path: the engine itself killed the subprocess at
  the timeout, so death evidence is positive by construction — consistent with the ratified
  proven-death retry policy (epic log D3/D4). For supervised envelopes the retry decision
  stays the orchestrator's under that same policy (a timeout observation from a supervised
  job is NOT a verified kill).

### 4. Prose — exit taxonomy + emission harmonization (AC3, AC7)

- `shared/blocks/model-routing-resolve.md` (source; `scripts/sync_shared_blocks.py`
  regenerates the `implement-feature` copy):
  - taxonomy line: exit `3` gloss gains "timed-out or signalled dispatch — partial output,
    when any exists, is attached and flagged `partial: true`".
  - exit-3 table row condition gains the timed-out/signalled case; outcome mapping is
    unchanged (`error` when the workflow dispatch ends failed; `dead` only on an abandoned
    supervised job) — under the #735 owner-ratified **per-workflow-dispatch** emission rule,
    so the ONE canonical DISPATCH line for a dispatch whose final attempt timed out carries
    `outcome=error`, never `ok` (AC7). No per-attempt lines (rev-2 rewording; the rev-1 text
    said "attempt's line", contradicting the ratified rule).
  - the AC5 contract sentence: omitted `--timeout` defaults to the seat's declared bound
    (`resolve_dispatch_timeout`, #753); `--timeout` only tightens.
- `skills/fix-bug/SKILL.md` (bespoke copy, direct edit): the same taxonomy/table edits, PLUS
  **harmonizing its stale "Per-attempt emission rule" paragraph (`:134`) to the #735
  per-workflow-dispatch rule** — a confirmed #735 leftover: the two normative surfaces
  currently make mutually exclusive emission claims (gate finding, adopted).
- **Drift guard** (gate finding, adopted): one test pinning the new canonical exit-3
  sentence in ONE file (the shared block source), header-index-sliced, whitespace-normalized
  (repo mistake-#6 pattern) — plus the same-sentence pin on the fix-bug copy so the
  harmonized paragraphs cannot re-diverge.

### 5. Tests (red before green, `tests/hooks/test_executor_routing.py` + `tests/phase_executor/`)

1. **T1 (the bug, sync):** injected runner returns a `timeout` observation WITH matching
   attested identity and a real `parsed_payload` → `ok: false`, exit 3,
   `error.code == "dispatch_timeout"`, `partial: true`, payload verbatim,
   `raw_capture_path` present, `observation` attached. (Red: returns `ok: true` today.)
2. **T2 (signalled, schema-valid — rev 2):** `parse_status: "nonzero_exit"` with
   `process.exit_code: -9` and matching identity → `ok: false`, exit 3,
   `error.code == "dispatch_signalled"` (precedence: signalled beats the generic status).
3. **T3 (nonzero_exit positive):** exit 2 with identity → `ok: false`, exit 3,
   `dispatch_nonzero_exit`.
4. **T4 (clean run unchanged):** `ok` status, exit 0 → `ok: true`, exit 0, no `partial` key.
5. **T5 (chain fallback unchanged):** target 1 timeout → target 2 success → `ok: true`.
6. **T6 (predicate unit table, `tests/phase_executor/`):** status × process matrix;
   precedence rows (timeout beats signalled beats status); malformed inputs (non-mapping
   `process`, bool/str `exit_code`, absent fields) → no-signal, never raises; Observation
   and dict forms.
7. **T7 (no-identity partial preservation — rev 2):** timeout observation with NO identity →
   existing `chain_exhausted_availability` exit 3 now ALSO carries the partial fields.
8. **T8 (supervised assembly — rev 2):** in-process supervised harness (`test_executor_routing.py:1445+`
   precedent), state `completed` + matching identity + timeout envelope → exit 3,
   `supervised_dispatch_timeout`, partial fields, no ok-return; negative-exit variant.
9. **T9 (resume assembly — rev 2):** same pair through `resume_dispatch`.
10. **T10 (reconcile — rev 2, `tests/phase_executor/`):** an expected call whose only passed
    receipt binds a verified-identity timeout observation is NOT served (falls to
    `missing_receipt`/unserved), and a breach still wins over a sibling verified attempt.
11. **T11 (collect authorization — rev 2):** a verified-identity timeout observation bound to
    the build receipt does NOT authorize promotion (`unauthorized_work_product`).
12. **T12 (drift guards — rev 3):** canonical exit-3 sentence pins on ALL THREE normative
    surfaces — the shared block source, the GENERATED `skills/implement-feature/SKILL.md`
    copy (a failed/no-op sync must fail the guard, not pass silently), and the fix-bug
    bespoke copy; fix-bug emission paragraph carries the per-workflow-dispatch rule.
13. **T13 (recover_run — rev 3):** a recovered `state="completed"` entry with matching
    identity and a timeout/signalled observation → `verify == "process_failure: <fail>"`,
    `worst` raised to `EXIT_AVAILABILITY` (`ok: false`, exit 3), partial evidence retained
    on the entry.
14. **T14 (breach precedence — rev 3):** identity breach PLUS timeout/signalled evidence on
    the SAME observation, across all four result-assembly paths (sync, supervised, resume,
    recovery) → `EXIT_ENFORCEMENT` (4) retains precedence, never downgraded to 3.
15. **T15 (foreign-payload exclusion — rev 3):** `correlation_mismatch` results on
    supervised/resume/recovery carry NO `partial_payload`, `raw_capture_path`, or foreign
    observation — only the two correlation ids.
16. **T16 (allowlist end-to-end — rev 4):** an injected Hermes-shaped envelope
    (`parse_error`, matching identity, exit 0, `parsed_payload: null`) → sync dispatch
    `ok: false` exit 3 `dispatch_parse_error` with `partial: false`; the same observation is
    NOT served in reconcile and does NOT authorize collection. A `usage_unavailable`
    matching-identity observation stays `ok: true` (the allowlist's accepted degraded state).
    T14 additionally asserts breach results carry the partial-evidence fields (rev 4
    ordering).

### 6. File changes

| File | Change |
|---|---|
| `phase_executor/src/phase_executor/contract.py` | `observation_process_failure` predicate |
| `phase_executor/src/phase_executor/enforce.py` | reconcile `saw_verified` conjunct |
| `hooks/executor_routing_lib.py` | `_attach_partial`, guards at sync/supervised/resume, collect authorization conjunct, recover_run check |
| `tests/hooks/test_executor_routing.py` | T1–T5, T7–T9, T11 |
| `tests/phase_executor/` (new/existing modules) | T6, T10 |
| `tests/` guard file | T12 |
| `shared/blocks/model-routing-resolve.md` | taxonomy gloss + table row + AC5 sentence |
| `skills/implement-feature/SKILL.md` | regenerated by sync |
| `skills/fix-bug/SKILL.md` | same edits + emission-rule harmonization |
| version ×4 + README changelog | patch bump; `Suite old→new`; diagram decision |

Diagram decision (pre-assessed): **no workflow-spine change** — dispatch-contract prose
inside existing stations → no REV.

## Error handling and failure modes

- The predicate and `_attach_partial` never raise: malformed fields read as no-signal, and
  the existing verdicts then govern — the fix can only tighten, never loosen.
- Observation audit appends are untouched and happen before result assembly (per-attempt in
  `wrapped_dispatch`; STEP 6.5 supervised) — a failed dispatch never vanishes from the audit.
- Reconcile semantics: breaches and missing observations keep their existing precedence over
  `saw_verified` (the Step-8a/Step-11 rules); the new conjunct only stops a killed attempt
  from counting as served.

## Security implications

Gate-integrity fix: a killed review can no longer read as a passed gate, a killed build can
no longer authorize work-product promotion, and a killed attempt no longer satisfies run-end
reconciliation. No new inputs parsed, no new subprocesses, no path handling.

## Platform / external dependencies

platform_apis: none

## Multi-PR assessment

Single PR (~300 net lines incl. tests). No separable phases.

## Peer-consult provenance (WF13, backend gpt, 2026-07-30)

Independent proposal read AFTER the rev-1 draft was on disk (blindness rule). Convergent on
the classifier approach, three-signal predicate, exit 3, partial preservation, doc surfaces,
table-driven tests. Adopted from the peer: payload-conditional `partial`. Declined: `state`
param in the classifier (the state gates precede it).

## Gate pass-3 provenance (2026-07-30, budget-exhausted close — owner RG-255373 option 1)

Merged 2 High + 4 Medium. Adopted into rev 4: success allowlist `{ok, usage_unavailable}`
deny-by-default (self p3 #1 — Hermes `parse_error` escape confirmed at `hermes_http.py:284-291`
+ `base.py:115`); failure-evidence-first evaluation order with breach results enriched
(adv p3 #1 — rev-3 self-contradiction); precise `partial` definition (adv p3 #2); per-path
`retryable` (adv p3 #3); SIGKILL labeling note (adv p3 #5); T16 end-to-end regression.
Refuted: adv p3 #4 (runtime DISPATCH-emission test — emission is orchestrator prose by
architecture; pass-1 precedent; drift guards are the mechanical check). Design loop-back
budget was exhausted (2/2) — the close is owner-authorized with no fourth review pass.

## Gate pass-2 provenance (2026-07-30, resolved by source verification per the pass-1 policy)

Merged 2 High + 6 Medium + 1 Low (adversarial p2 dispositions-armed + executor self-review p2).
Adopted into rev 3: predicate tightened to fail any non-zero integer exit regardless of claimed
status + `parse_status` type guard (adv p2 #1, self p2 #4); partial attachment NARROWED to
correlation-owned observations with the foreign-payload exclusion normative + T15 (self p2 #1 —
a real information-flow catch); recover_run partial enrichment + T13 (adv p2 #3, self p2 #2);
T12 pins all three surfaces (adv p2 #4); T14 breach-precedence combined tests (self p2 #3).
Refuted with evidence: adv p2 #2 (per-attempt worktree isolation `worktree.py:235-241` +
`candidate_tree_sha` INTENT binding `:1523-1633` contradict the residue-promotion scenario);
adv p2 #5 (adapter provenance, and the rev-3 predicate makes signal encoding moot). Breaker
ambiguities all resolved factually at source; owner not re-asked per escalation hygiene and
the pass-1 standing policy (adopt-verified / refute-with-evidence / revise / re-run).

## Gate pass-1 provenance (2026-07-30, owner decision RG-562362 option 1)

Merged 2 High + 7 Medium across the executor self-review (gpt-5.6-sol) and the adversarial
review (gpt). Adopted into rev 2: core-contract predicate applied at reconcile/collect/
recover too (self H1); partial enrichment on all observation-bearing failure paths with
enforcement precedence stated (adv H1 residue + self M2); schema-valid T2 + classification
precedence (self M3); emission-rule harmonization + AC7 rewording to the per-workflow-dispatch
rule (self M4); supervised/resume assembly tests (adv M2); type-checked never-raise extraction
(adv M3); drift guards (adv M5). Refuted with evidence (not applied): adv H1's exit-code half
(no-identity timeout already exits 3 via `chain_exhausted_availability`); adv M4's platform
worry (`resolve_parse_status`, `adapters/base.py:102-110`, cannot produce `ok`+signalled).

## Step-11 pre-PR review amendments (2026-07-30, session 33b3f9ef, commit 8b5b793)

The pre-PR wave (2 executor review-seat agents on gpt-5.6-sol + adversarial diff review,
dispositions-armed) merged 7 High, 0 ambiguous. Four adopted — all implementation-class
alignment with this design's own rules, no design change:

1. **Residue is never retryable (R1-H1).** `await_job` returns `timed_out` whether or not
   `_kill_job` proved death; the state-based retryable sets in supervised/resume dispatch
   contradicted the ratified proven-death policy this design pins. New public
   `Supervisor.job_record` fresh-registry read; `retryable` gated on `quarantine_reason`
   absence (residue ⇒ `EXIT_INTERNAL`, parity with `completed_with_residue`).
2. **Evidence before verdicts, completed branch included (R1-H2).** The completed
   supervised/resume branches checked `not pc.verified` (retryable) before the process
   predicate (non-retryable) — a MISSING identity made a failed envelope MORE retryable.
   Reordered fail-before-unverified (breach precedence intact); recover_run's verify
   labels aligned to the same precedence.
3. **`usage_unavailable` requires a payload, reader-side (R2-H1).** The status means
   "output parsed, token counts missing"; legacy/in-flight producers pre-dating the
   adapters/base.py reorder can emit it for produced-nothing invocations. The predicate
   now returns `no_response` when `parsed_payload is None` (falsy payloads ARE payloads).
4. **The read boundary is as strict as the write boundary (R2-H2, adv-H3 converged).**
   `records()` schema-validates every inner observation per its declared version
   (fail-closed); `collect_work_product` authorization additionally binds the observation
   to the passing receipt's seat/run/correlation — nonce-sharing plus verified identity
   is not ownership.

Refuted with evidence (dispositions 733-s11-5/6): sync retryable classes are safe — sync is
structurally non-mutating (mutating compositions exist only via `compose_supervised_argv`
under `MUTATING_FS_SANDBOXED`, canary-refused at supervised STEP 0); recover's None-cid
stamping is the F6 binding rule, not an ownership bypass (both-None ownership = tmux-identity
adoption; 733-p2-2 not reopened).
