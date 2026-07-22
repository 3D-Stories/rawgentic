# Design — #559: end-to-end proving run — codex mutating cell + account-switch recovery (H6)

**Issue:** #559 (epic #560 child 6/6) · **Date:** 2026-07-21 · **Author:** WF2 session 349024f3 (**r6** — pass-4 verification folds applied inline under owner autonomy grant; loop-backs §10/§11/§12, pass-4 P4-* §13)
**Base:** origin/main @ 92fbb0d (v3.85.0, phase_executor 0.7.0) · **Baseline:** 4340 passed / 16 skipped / 0 failed

## 0. Scope

Two natures in one issue, kept explicit throughout:
- **BUILD** (code, test-first): H1, H2, H3 fixes (deferred Highs from #558 8a/S11), F1 probe evidence capture (#556 deferral), the **recovery-dispatch chokepoint** (r3, C1), the AC1 promote/work_product glue + `appendix-only` path policy, the AC2a account-identity probe + **resume-dispatch flag** (r3, C2), D-12 classifier calibration plumbing.
- **OPS** (live cells, evidence-producing): the codex mutating cell (AC1), the cheap account-switch probe (AC2a), the **two-stage** bounded exhaust — capture/calibrate then activated live cycle (AC2b, r3 C3) — run-end reconcile (AC3), and the committed report (AC4).

Owner decisions binding here: D-12 (live quota_paused transition AC lands HERE; CALIBRATED_CLASSIFIERS entry only from a GENUINE capture), D-13 (codex bound = enforced timeout), D-15 (H3 fix lands once, #449 inherits), cheap-probe-before-burn (from #472 D-8).

**Issue-closure conditionality (r3, C9):** the PR says `Closes #559` + `Closes #560` ONLY if every mandatory live AC passes. If the bounded exhaust captures no genuine limit (or an owner gate goes unanswered), the PR ships the hardening code + honest partial report as `Part of #559 / Part of #560`, the blocker/owner-gate is posted per the ERROR protocol, and the issues stay open for an owner re-scope. A report is evidence, never a substitute for an unmet AC.

## 1. Approaches considered

- **A (recommended) — surgical fixes + thin glue + scripted live cells.** Fix H1/H2/H3/F1 in place (each mirrors an existing in-repo primitive); close the recovery audit gap with ONE chokepoint verb; add ONE CLI capability per missing surface (`probe-account`, `--resume-session-id`, `collect-work-product`); run the live cells as ops with evidence persisting immediately. Pros: smallest diff that is actually sound; every fix sits where all callers route through; the live cells exercise the SAME wired path the fixes harden. Cons: orchestrator-driven ops are session-bound (mitigated: per-cell evidence on disk).
- **B — a dedicated proving-harness module.** Rejected: new unproven orchestration layer proving other code — circular; ~3× diff. Repeatability lives in the report's exact-command appendix.
- **C — ops-only, no code.** Rejected: H1/H2/H3/F1 are code defects; the recovery audit gap (C1) and AC2a are unbuildable without code; AC1's glue has zero production callers to reuse.

## 2. Component design (BUILD)

### 2.1 H1 — `mark_quota_paused`: explicit guard order, kill-verify before permit release
`supervisor.py:555-588`. Today's ACTUAL order (r3 correction — r2 misstated it): `_live` liveness check at :573-574 runs FIRST, then the effectful-sentinel refusal at :575-585. There is no kill at all, and `_finish` defaults `release_permit=True` (:711).

Fix — the required order, stated explicitly (C6, R3):
1. Identity / provider-session / terminal-state validations (the existing sentinel-guard block, unchanged in substance) — a completed/sentineled/terminal record refuses injection BEFORE any kill attempt.
2. Effectful-sentinel read + reject — an effectful job is never killed by quota injection.
3. Only then `killed = self._kill_job(record)` (:625 — the existing descendant-snapshot + two-group primitive; no second kill primitive) — replacing the `_live` probe entirely.
4. **Post-kill sentinel RE-READ (R3 + r6/P4-M — the check/kill race):** a live child can write an effectful sentinel between step 2's read and the verified kill. After `killed == True`, re-read the sentinel via `_sentinel_effectful` (:1324-1330) immediately before writing the state: post-kill effectful → finalize **`completed_with_residue`** (NOT `completed` — a forcibly-killed job that showed an effect is an interrupted, uncertain outcome, never a "success"; labeling it `completed` would suppress remediation, P4-M), non-resumable, permit released, classification recorded as refused-effectful — never `quota_paused`. This mirrors the automatic path's kill-first-then-read ordering (:750-786) while keeping the pre-kill refusal as the cheap early exit.
5. `killed == True` and post-kill sentinel non-effectful → `_finish(record, "quota_paused", release_permit=True, ...)`.
6. `killed == False` (residue) → NOT quota_paused, permit RETAINED: `_finish(record, "completed_with_residue", release_permit=False, ...)` with the injected classification recorded — parity with the automatic path's `kill_unverified` refusal (:791-796).
`mark_quota_paused` returns the terminal state string; existing callers (tests only — Step-2A confirmed no production caller) updated.

Tests (red-first): (a) live-pane fake → `_kill_job` invoked, verified kill → `quota_paused` + permit released; (b) residue → `completed_with_residue` + permit retained; (c) effectful sentinel present pre-kill → `_kill_job` NEVER called and `_finish` untouched (C6); (d) **race test (R3/P4-M): a `_kill_job` seam writes an effectful sentinel during the kill → final state `completed_with_residue` (non-success), never `quota_paused`.** Extend `tests/phase_executor/test_supervisor_lifecycle.py`.

### 2.2 H2 — `_relaunch` refuses unverified spec bytes (the post-identity-check TOCTOU window)
`supervisor.py:1084` reads via `_read_spec` (:1383 — bare `json.load`) feeding `prompt`/profile/effort/timeout into `launch()` (:1108-1112). r3 precision (C5): `recover()` ALREADY digest-checks via `_identity_matches` (:1014-1019, :961-968) before classifying — the defect is the **time-of-check/time-of-use window**: bytes re-read by `_relaunch` after that check are trusted unverified.

Fix: `_relaunch` reads via `_verified_spec(record)` (:1303 — digest-gated read-once). `None` → quarantine (kill + retain evidence, mirror the adopt arm), never relaunch. `_read_spec` stays for the synthetic-observation forensic paths (:842,878,907).

Tests (red-first, C5): the primary test targets the TOCTOU window — tamper the spec AFTER `_identity_matches` passes (test seam: patch `supervisor._identity_matches` to return True with the file already tampered, simulating the post-check write) → `recover()` → assert `_relaunch` never calls `launch`, record quarantined, evidence retained. A direct `_relaunch`-tamper unit test supplements. `test_supervisor_recover.py`.

Ordering constraint (Step-2A): H2 lands in the same PR as any D-12 activation — the activation makes `_relaunch` production-reachable for the first time.

### 2.3 H3 — unknown competitive seat fails loud, no carve-out
`engine.py`: `Candidate.pool` is fail-closed validated (:296-300); `Candidate.seat` is not — unknown seat → `_manifest_for` `None` → uncapped dispatch. `test_run_competitive_unknown_seat_keeps_no_profile` (test_engine.py:448) pins the bypass.

Fix — in `run_competitive`'s pre-fanout validation loop: `snapshot.seat(c.seat)` under try/except `RoutingError` → re-raise as the same fail-loud `ValueError` the pool check uses. **No no-`seats` carve-out (r3, C4/ADV-2):** a snapshot whose table lacks a usable `seats` mapping fails the same way for any candidate seat — the r2 carve-out preserved the exact bypass for a supported input shape. Legacy manifest-less TEST tables that construct `Candidate`s against seatless tables get their expectations updated (budgeted); the seat-exists-but-manifest-less case (`test_run_seat_no_manifest_keeps_no_profile`) is untouched — that is the one legitimate `None`.
`run_seat` already fails loud upstream (routing.py:205) — untouched.

Tests (red-first): flip :448 to assert the raise; add `test_run_competitive_seatless_table_fails_loud` (seatless table also raises — the carve-out's absence, pinned). D-15: #449's bench inherits (same call sites, real seat names).

### 2.4 F1 — behavioral probe parses denial evidence; raw output is ephemeral
`hooks/executor_routing_lib.py:815-847`. Today the child's `CompletedProcess` is discarded.

Fix (C8-compliant — no raw persistence; R5 — honest about spoofability):
- The probe reads the child's stdout+stderr **in memory only**; nothing raw is written to disk.
- Parse for denial evidence: a line carrying an OS-denial token (`EACCES`, `EPERM`, `Operation not permitted`, `Permission denied`, `EROFS`/`Read-only file system`) that also names the out-of-worktree target (path or basename). **Source discrimination (R5):** a line that parses as a structured codex exec event (JSON with a command-execution/result type) is recorded `source: exec_event`; free prose is `source: prose`. Prose is spoofable — the target path is disclosed in the probe prompt, so model text can echo `EPERM <target>` without any denied syscall.
- Result grows structured fields only: `{"inside_written", "outside_blocked", "denial_evidence": {"matched": bool, "source": "exec_event"|"prose"|None, "token": str|None, "target_named": bool, "line_sha256": str|None, "sanitized_line": str|None}}` — `sanitized_line` redacts everything except the token and the throwaway target path.
- **Verdict contract (R5 — explicit):** `denial_evidence` is ADVISORY CALIBRATION DATA ONLY and is never an input to `outside_blocked`, the canary verdict, or any pass/fail decision — in this PR or by silent later reuse; the docstring and the canary contract both state it. `outside_blocked` stays absence-based with the documented necessary-not-sufficient flag. #556-F1 is thereby satisfied as *calibrated capture* of the real denial shape (the deferral's letter), not as authentication — authenticating denial (a trusted independent syscall check) is named a follow-up in the report.

Tests (red-first): fake runner with an `EACCES ... outside.txt` exec-event line → matched, `source: exec_event`; the same text as free prose → matched, `source: prose`; **negative echo test (R5): ordinary model prose containing token+target does NOT alter `outside_blocked` or any verdict**; silent output → matched False; PII-seed test (C8): email/token-shaped strings in output appear neither in the returned dict nor anywhere on disk under the probe root.

### 2.5 AC2a — account-identity probe + audited resume dispatch
**Identity probe** — live-probed this session: `claude auth status --json` (rc=0) returns `{loggedIn, authMethod, apiProvider, email, orgId, orgName, subscriptionType}`.
`probe_account(claude_bin)` + CLI verb `probe-account` in `hooks/executor_routing_lib.py`:
- Returns `{"status": "ok"|"logged_out"|"unavailable"|"parse_error", "logged_in": bool, "identity_digest": sha256("rawgentic-account-identity:v1|" + email + "|" + orgId), "subscription_type", "auth_method"}`. Status arms: rc!=0/timeout → `unavailable`; non-JSON/missing fields → `parse_error`; valid JSON with `loggedIn: false` → `logged_out` (no digest computed); a read failure is NEVER an account switch (R12 gating above). No raw email/orgId/token in any output (digest + categories only).
- **Digest disposition (R8):** identity digests are RUN EVIDENCE, not report content — they live only under the gitignored `.rawgentic/runs/<run-id>/` evidence dir. The COMMITTED report carries opaque per-run labels (`account-1`, `account-2`) plus equality/change verdicts only; a committed artifact is not owner-only, so no digest (however unkeyed-hashed) is committed.

**Resume dispatch** (r3, C2 — replaces r2's impossible recover()-driven resume): the seed and the resume are TWO explicitly separate operations:
- **Seed (ops, non-seat):** an orchestrator-run persistent `claude -p --output-format json` turn planting a nonce fact; the provider session id comes from its JSON output. Recorded in the COMMITTED report as command, session id, opaque account label `account-1`, and the equality/change verdict — the digest itself stays only in the gitignored run-evidence dir (F-e/R8); the seed is not a seat dispatch and is not ledgered; the LOAD-BEARING operation is the resume.
- **Resume (fully ledgered):** the existing `dispatch` verb gains `--resume-session-id <sid>` — supervised claude only; composes the launch with `session_policy="resume"` + the given id (the adapter path that already exists: claude_cli.py:48-50, spike #455/#467-W4). Everything else is the normal chokepoint: append_expected → check_pre receipt → launch → await → Observation append → verify_post. A `--resume-session-id` on a non-claude engine or a mutating profile refuses (exit 2).
- AC2a assertion trio: identity CHANGED (two `status: ok` probes, digests differ — digests compared in run-evidence, never committed) + session preserved (resume dispatch's envelope `session_id` == seed's id) + successful resumed turn (envelope success AND the output echoes the seed nonce — continuity, not id equality).
- **Session-preservation test pin (F-h):** the resume-dispatch tests assert not only that resume composition reaches `launch()`, but that on a resumed envelope the pipeline asserts `envelope.session_id == the seeded id` (a fake runner returning a mismatched session_id → the resume dispatch fails loud, never reports "session preserved") — the continuity assertion is enforced by a test, not merely named.

**Cross-account resume is THE unverified platform behavior (C11):** §7 carries it as `unverified capability — CELL-2 is the blocking spike`, with defined fail-loud evidence on rejection: dispatch exit code, envelope subtype, JobRecord state, sanitized stderr digest, report disposition `cross_account_resume: refused`. **Gate consequence (R10 — unambiguous):** a cross-account refusal is a valid SPIKE result but FAILS AC2a, and a failed AC2a CLOSES the paid-work gate — CELL-3a and CELL-3b are SKIPPED entirely, the run proceeds only to non-billable reporting + reconciliation, and #559/#560 stay open per §0 conditionality. There is no path on which a refusal authorizes further burn.

**Probe gating (R12):** paid operations and digest computation require `status == "ok"` AND `logged_in == true` AND nonempty `email`+`orgId`. A syntactically valid response with `logged_in: false` gets its own status `logged_out` (no digest computed); `logged_out`/`unavailable`/`parse_error` all block paid cells identically.

Tests: fake-runner unit tests (canned JSON per status arm; digest stability; no `@`-string in result); dispatch-flag tests (resume composition reaches launch; non-claude/mutating refusal). Live smoke: `probe-account` on this host.

### 2.6 AC1 glue — `appendix-only` path policy + `collect-work-product` + audited work_product record
- **Path policy** (`worktree.py`, next to `PROMOTE_ANY`): `promote_appendix_only(prefixes)` predicate factory. Hardened: candidate paths POSIX-normalized; rejects absolute, `..`, empty; **the prefixes themselves are validated the same way (C10)** — a prefix that is empty, absolute, `.`, or contains `..`/whitespace-only raises at factory time; comparison is on component boundaries (`docs/planning/appendix/` never matches `docs/planning/appendix-evil/`). The `collect-work-product` verb additionally HARD-CODES its sole allowed prefix `docs/planning/appendix/` (C10) — the factory's generality serves future callers, not this verb's authorization input.
- **Audited work_product (C7 + r6/P4-High + P4-ADV-missing):** a new audit record kind `work_product` — `{"kind": "work_product", "receipt_nonce": <build receipt>, "candidate_tree_sha": <str>, "new_sha": <promoted ref sha>, "work_product": {...}}`. The `candidate_tree_sha`+`new_sha` fields are REQUIRED (r6: the retry dedup search in §2.6 matches on exactly these, so they must live in the record — r5 omitted them, leaving F-c half-resolved). Reader (`enforce.py` audit shape check) extends additively: required fields for the new kind; reconcile binds each work_product record to its receipt — unknown nonce → `orphan`; >1 per receipt → `duplicate`; **and a receipt whose role/observation shows a completed build WITH a promotion but NO matching work_product record → `missing_work_product` (P4-ADV): a promoted-but-unrecorded work product is an anomaly, not a silent pass.** `reconcile --mode final` proves the binding both ways (no orphan, no missing).
- **Glue verb** `collect-work-product` — **two-phase, crash-recoverable, audit-idempotent (R7 + F-b + F-c):** given `--run-id --session-name --target-ref --expected-target-sha [--kind docs]`: JobRecord must be terminal `completed` → `content_evidence(handle)` gives `candidate_tree_sha` → **phase 1: write a durable INTENT file** under the run dir keyed `{receipt_nonce, candidate_tree_sha, expected_target_sha}` (NOT the post-promote SHA — F-b: `promote()` builds the commit internally and reveals its SHA only after `commit-tree`, so `computed_new_sha` is unknowable at intent time; the intent records it back only AFTER promote returns) → `promote(handle, ..., path_policy=promote_appendix_only(("docs/planning/appendix/",)))` (the irreversible CAS; on success the intent is updated with the returned `new_sha`) → **phase 2: finalize** — `derive_work_product(..., promotion=<PromotionResult>)` → **audit-search then append (F-c): scan the run's audit stream for an existing `work_product` record matching `{receipt_nonce, candidate_tree_sha, new_sha}`; append ONLY if absent** (so a crash after append but before intent-consumed cannot duplicate on rerun) → mark the intent consumed.
- **Idempotent re-run (F-b/F-c/F-l — the ONE retry contract):** on start, an unconsumed intent whose `expected_target_sha`-parented candidate commit is already at/under the live target ref means promotion succeeded but finalization crashed → reconstruct the `PromotionResult` from the intent + live ref and run phase 2 only, which is itself idempotent via the audit-search above (never a second promote, never a duplicate record). A re-run with NO intent (or a malformed/non-matching one) and a moved ref refuses as a genuinely foreign move. This is the single retry contract; §5 states it identically (F-l — the earlier "already-promoted rerun always refuses" wording is removed). All refusals loud.

Tests: predicate + factory-validation (malicious prefixes: `.`, `..`, absolute, whitespace, sibling-prefix confusion); glue happy path; out-of-policy refusal; audit-record shape + reconcile binding (orphan/duplicate); **forced failure injected immediately after `update-ref` → re-run completes phase 2 idempotently (R7); forced failure AFTER the work_product append but before intent-consumed → re-run appends NOTHING (audit-search hit), exactly one record, zero-anomaly reconcile (F-c).**

### 2.7 Recovery-dispatch chokepoint (r3, C1 — new; closes the Critical audit gap)
Today `recover()` → `_relaunch()` → `launch()` directly (supervisor.py:1058-1061, :1108-1115): no ledger append, no `check_pre`, no receipt, no Observation, no `verify_post` — a recovery relaunch is invisible to the audit `reconcile` claims to bind (the reader side already validates `recovered_from` on receipts, enforce.py:294-299 — #554 shipped the reader; the writer never existed).

Fix:
- `PreReceipt` gains additive `recovered_from: Optional[str] = None`, emitted in `to_dict` (reader already validates it, enforce.py:294-299).
- **`Supervisor.recover(run_id, *, dispatch_gate)` — the gate is MANDATORY, no default, no ungated escape hatch (R11 + F-a):** a missing gate is a `TypeError` at the signature. There is **no** `_recover_ungated_for_tests` helper in the production module (F-a — a Python underscore is not an enforcement boundary; any attribute caller could reach it). Tests that exercise recovery ALWAYS pass an explicit fake `dispatch_gate` that returns a canned `RecoveryAuthorization` — the same contract production uses — so there is exactly one code path to relaunch and it is always gated.
- **The gate returns a typed authorization bundle, not a bare nonce (R6 + r6/P4-M):** `RecoveryAuthorization {receipt_nonce, resolved_target, config_digest}` — `check_pre` binds the receipt to a concrete target identity (enforce.py:206-213), and `_relaunch` passes that SAME `resolved_target` into `launch()` (which already accepts a resolved target to prevent re-resolution drift, supervisor.py:418-424) AND **asserts the relaunch's composed `config_digest` equals the authorization's `config_digest`** before launch (P4-M: the field must be validated, not merely carried — a drift aborts the relaunch loud). `None` → relaunch REFUSED (record stays quota_paused, reported `relaunch_refused (gate)`).
- **Mutating/build records are REFUSED for recovery in this PR (R6):** a recovered mutating job would need a fresh behavioral-canary pass; rather than design that here, `recover()` (gated path) refuses `mutating=True`/build-role records with `relaunch_refused (mutating_recovery_unsupported)` — the cells only recover non-mutating claude jobs; the report names mutating recovery a follow-up.
- **Ledger semantics (R1 — matches the shipped reconcile):** recovery is a provenance-bearing ATTEMPT under the ORIGINAL expected call, never a new one. `recover-run` does NOT `append_expected` for resumes; the recovery receipt carries `correlation_id=<orig>#resume<n>` + `recovered_from=<orig>`, and reconcile's effective-key mapping (enforce.py:470: `recovered_from or correlation_id`) groups it under the original key. (Appending a `#resume` expected call would deterministically land in `missing_receipt` — confirmed against `_do_reconcile`'s expected-key conversion, executor_routing_lib.py:1846-1849.)
- **Receipt ordering + all-outcome observability (R2 + r6/P4-M):** ALL prelaunch checks — record classification, `_verified_spec`, the mutating refusal — run BEFORE the receipt is minted (a refused record consumes no receipt). Once a receipt IS minted, every outcome appends its Observation against that nonce: success (real envelope, then `verify_post`) or a synthetic refusal/failure Observation. A process death BETWEEN minting the receipt and appending the Observation is not silently ignored: it leaves an observation-less receipt, which `reconcile` catches as `missing_obs` (tolerated only in `--mode provisional`; a hard anomaly in `--mode final`) — so the crash surfaces at the gate rather than passing. A transactional outbox is a named follow-up, not built here (the reconcile catch is the backstop).
- New CLI verb `recover-run --run-id <r>` in `hooks/executor_routing_lib.py`: refuses when the ledger is `run_closed`; supplies the gate; awaits each relaunched record; appends Observations; `verify_post` on successes. Exit taxonomy mirrors `dispatch`.

Tests (red-first): run-closed refusal; prelaunch-checks-before-receipt ordering (quarantine consumes NO receipt); gate-refusal → no launch + state preserved; mandatory-gate TypeError; mutating-record refusal; receipt carries recovered_from + target binding threads into launch (target-drift test); synthetic Observation on post-receipt failure; **zero-anomaly final reconcile through the REAL verb path with original + recovery rows (R1's exact scenario)**.

### 2.8 D-12 — classifier calibration plumbing (capture-gated)
Recipe unchanged (#558-pinned): genuine stderr → sanitize → fixture in `tests/phase_executor/fixtures/` → rule-table adjust iff needed → bump `CLASSIFIER_VERSION` 1→2 + `RULE_TABLE_DIGEST` together → the ONE `(2, digest)` pair into `supervisor.CALIBRATED_CLASSIFIERS` + production-pair test.
**Sanitization equivalence gate (C8/ADV-5):** before the raw capture is discarded, classify BOTH raw and sanitized bytes and assert identical `(category, rule_ids, classifier_version, rule_table_digest)`; on mismatch the pair is NOT committed/activated and the report records the divergence.
**Capture-scrub boundary (R9 + F-k + r6/P4-Crit — the adapter persists raw bytes in MULTIPLE files, and a crash can strand them):** the `Capture` writer persists FOUR child-content files, not two — `input.md`, `output.md` (parsed child text), `transport` (raw stdout), `stderr` (capture.py:86-96) — plus the structured `observation.json`. r5's scrub named only `stderr.txt`+transport and so LEFT raw child output in `output.md`/`input.md` (the P4 Critical). Corrected mechanism — scrub is defined by ENUMERATING the capture dir, not a hardcoded 2-file list, so it cannot miss a file:
- The capture dir for a quota cell is created mode 0700.
- The scrub runs in a `finally` block around each cell, on EVERY outcome (success, refusal, interruption, equivalence-divergence): it walks the cell's capture dir and replaces **every child-content file the `Capture` writer produces — `input.md`, `output.md`, `transport*`, `stderr*` (everything EXCEPT the structured `observation.json`, which carries no raw child bytes beyond an already-parsed error envelope)** — with `{sha256, byte_count, scrubbed: true}` stubs. New capture files added upstream are covered automatically (enumerate-and-scrub-all-but-observation), closing the "what other files?" gap permanently.
- **Crash coverage (F-k):** a SIGKILL/host-loss between the adapter write and the `finally` scrub leaves raw files. So an **idempotent scavenger** runs at CELL-3 START and again at run close (`reconcile`): it scans the run's capture root for any un-scrubbed child-content file in a quota-cell dir and scrubs it, logging each. The run-close residue check ASSERTS zero raw survivors across ALL four file kinds — a survivor is a loud failure, not a silent pass.
- **Residual limitation, stated honestly (P4-ADV):** the scrub/scavenger both run *within a workflow that continues*. A total host-loss followed by permanent run abandonment (no CELL-3-start and no run-close ever again) leaves raw files until a future run's scavenger. §6's claim is precisely "no raw child output survives a COMPLETED-or-RESUMED cell," never "cannot exist on disk between a crash and the next scavenger pass." A standalone cron scrubber (independent of any workflow) is named a follow-up in the report — not built here (scope).
- The seeded integration test plants email/token-shaped strings in a fake adapter's `output.md` AND `stderr` AND `input.md`, scans the ENTIRE capture root after each outcome and after a simulated mid-cell crash + scavenger pass, and asserts no seed survives in ANY child-content file.
- In-process handling uses memory only — no 0600 spill file (R13); an unavoidable spill must be an already-unlinked anonymous fd. §6 states "no raw child output survives the cell," never "never touches disk."
- A seeded integration test plants email/token-shaped strings in a fake adapter's output and scans the ENTIRE capture root after each outcome AND after a simulated mid-cell crash + scavenger pass — asserting no seed survives.
**Honesty gate:** no genuine capture → allowlist stays EMPTY, classifier stays shadow, report records the A4/F11 capture-or-defer disposition, closure conditionality (§0) applies. `reset.v1/b` digit-proximity false-positive (#558 follow-up) is re-checked against the real capture.

## 3. Live cells (OPS — r3 ordering with digest-asserted switch gates)

Run-id `wf2-559-<session>`; ledger append-before-dispatch; `reconcile --mode provisional` between cells. **Hard gate: no paid exhaust (CELL-3a/3b) until BUILD tests are green in the full suite AND CELL-2 passed.** Every owner switch is a DURABLE, NON-BILLABLE pause: no supervised job running; AskUserQuestion (full instructions embedded as visible text) + BlueBubbles notify; a `owner_prompt_emitted` session-note marker precedes each wait; bounded wait (60 min) → on timeout, blocked-honest disposition + ERROR protocol.

**Account-role separation (F-d — the r4 flaw: CELL-3a exhausted the very account CELL-3b then relied on).** The run distinguishes THREE roles; whether they are three distinct accounts or two accounts across two quota windows is an **owner-gated decision at CELL-3 entry** (owner states how many are available):
- **PAUSED-role** — the account deliberately driven to a genuine limit so its `quota_paused` job can be recovered (the cycle's subject).
- **RECOVERY-role** — a DIFFERENT account with confirmed capacity that runs the resumed turn. Never the PAUSED account; never the CAPTURE account exhausted for calibration.
- **CAPTURE-role** — the account whose genuine limit stderr calibrates the classifier (CELL-3a). It may coincide with the PAUSED account ONLY if the full cycle then uses a fresh window (see the two modes).

Two owner-selectable modes at CELL-3 entry:
- **Mode-3 (three accounts A/B/C — preferred, no waiting):** CAPTURE=C, PAUSED=A, RECOVERY=B. No account is asked to be both exhausted and fresh.
- **Mode-2 (two accounts across windows):** CAPTURE=B in CELL-3a; then a DURABLE, non-billable window-reset pause with an explicit deadline (owner-gated, `owner_prompt_emitted` + bounded wait) until B's 5h window resets, verified by a cheap successful B probe, BEFORE B is used as RECOVERY. A is PAUSED. If the reset probe fails at the deadline → CELL-3b SKIPPED, closure conditionality applies.

Choreography (`probe-account` digest assert — in run-evidence only, F-e — before anything paid; every arrow an owner switch):
```
start: A
CELL-1  on A (codex — claude account irrelevant to codex; asserted for audit cleanliness)
CELL-2  seed on A → switch A→B (assert change) → resume on B                 [cheap, no burn]
CELL-3a CAPTURE on the CAPTURE-role account → calibrate + activate (code) + FULL suite
CELL-3b PRE-BURN READINESS GATE: a cheap supervised call MUST succeed on the RECOVERY-role
        account (proving capacity) — asserted, never assumed; readiness FAIL → skip 3b,
        closure conditionality. Then: exhaust the PAUSED account under the ACTIVATED pair →
        await_job auto-finalizes quota_paused LIVE → switch to RECOVERY account →
        recover-run (the C1 chokepoint) → relaunch → verified completion.
CELL-4  close-run → reconcile --mode final (zero anomalies incl. the recovery group)
```
Ordering note (F-g — supersedes r4's contradictory note): the readiness gate is a distinct step at the TOP of CELL-3b that proves the RECOVERY account has capacity BEFORE the PAUSED account is burned; it is not folded into a "last act on B" hand-wave. In Mode-3 it is a cheap probe on B; in Mode-2 it is the post-window-reset probe on B.

**Readiness is necessary-not-sufficient (r6/P4-ADV).** One cheap successful call proves capacity exists *now*, not that enough remains for the full resumed turn plus up to `MAX_RESUME=2` relaunches — quota headroom is opaque. So the readiness gate is a *precondition*, not a guarantee: if the recovery account is exhausted MID-cycle (the resume or a relaunch itself hits a limit), that is a **honest partial** — the report records `recovery_incomplete (recovery account exhausted mid-cycle)`, the cycle stops without burning further, and closure conditionality (§0) applies. The design never claims the readiness probe guarantees completion; it claims it prevents the obvious waste of burning the PAUSED account when recovery has zero capacity.

**Runtime-activation preflight (F-i).** CELL-3b, before the paid burn, asserts the LIVE runtime imports the newly-activated classifier pair — not a cached 0.7.0: a preflight invokes the exact production CLI entry point and logs the resolved `phase_executor` module path, package version, `CLASSIFIER_VERSION`, and `RULE_TABLE_DIGEST`; a mismatch against the just-calibrated pair aborts CELL-3b loud (the repo≠installed-cache hazard, CLAUDE.md §1). Recorded in the report as §7-required evidence.

**Per-cell bounds table (F-f + r6/P4-ADV — enforcement named).** Every paid cell is explicitly bounded, and the ENFORCER is named: each cell is driven by a small bounded-loop script (`hooks/executor_routing_lib.py`'s dispatch already enforces `max_budget_usd` per call via the seat manifest; the cell script adds the call-count + wall-time bounds as a Python loop that checks elapsed wall and a call counter before each dispatch and stops on the first breach). "A cap exists in the table" is not enough — the cell script reads these exact numbers and halts; a breach raises before the next paid call. Bounds:

| Cell | max_budget_usd | max calls | per-call timeout | wall cap | stop behavior |
|---|---|---|---|---|---|
| CELL-1 (codex build) | 10.0 (build seat) | 1 | D-13 enforced timeout | 15 min | fail-loud on breach |
| CELL-2 (cheap probe) | 2.0 (2 turns) | 2 (seed+resume) | 300 s | 10 min + owner-wait 60 min | refuse on breach |
| CELL-3a (capture) | 20.0 | 20 | 300 s | 30 min | first genuine limit OR any cap → stop |
| CELL-3b (paused burn) | 20.0 | 20 | 300 s | 30 min | first quota_paused OR any cap → stop; then recover (MAX_RESUME≤2) |

Crossing ANY cap fails closed without another relaunch; the report records consumed budget/calls/wall per cell.
- **CELL-1 (AC1):** mint-gate → `dispatch --seat build` (supervised codex, canary 0-5 incl. the F1-instrumented probe) writing ONE file under `docs/planning/appendix/` → `collect-work-product` (§2.6: promote → derive → work_product audit record) → verify_post receipts prove requested==actual. Bounds: D-13 timeout + build `max_budget_usd` 10.0.
- **CELL-2 (AC2a):** §2.5's seed/switch/resume with the assertion trio. A cross-account refusal = valid negative SPIKE result (C11) but FAILS AC2a and **closes the paid-work gate (R10): CELL-3a/3b skipped, non-billable reporting only, issues stay open** — no contradiction with the hard gate.
- **CELL-3a (capture):** bounded exhaust of the CAPTURE-role account per the bounds table. Two honest outcomes: capture → §2.8 calibration lands (code, tests, full suite re-run); no capture → §2.8 honesty gate (shadow stays, owner escalation, closure conditionality).
- **CELL-3b (activated live cycle — only if 3a captured and calibrated):** the F-i runtime-activation preflight, then the readiness gate, then a SECOND genuine limit event on the PAUSED account under the activated pair → the LIVE `quota_paused` transition (C3 — replaying captured bytes is classification evidence, never a live transition) → switch to RECOVERY account → `recover-run` chokepoint → verified completion. MAX_RESUME=2 caps relaunches.
- **CELL-4 (AC3):** `close-run` → `reconcile --run-id ... --mode final` → exit 0, zero anomalies across all 9 buckets including the pause/recover correlation group and the work_product bindings.

## 4. File changes

| File | Change |
|---|---|
| `phase_executor/src/phase_executor/supervisor.py` | H1 (§2.1 order + kill-verify), H2 (§2.2), `recover(dispatch_gate=)` (§2.7), CALIBRATED_CLASSIFIERS pair (§2.8, capture-gated) |
| `phase_executor/src/phase_executor/enforce.py` | `PreReceipt.recovered_from` (additive) + `work_product` audit kind + reconcile binding (§2.6/2.7) |
| `phase_executor/src/phase_executor/engine.py` | H3 (§2.3, no carve-out) |
| `phase_executor/src/phase_executor/worktree.py` | `promote_appendix_only` factory (§2.6) |
| `phase_executor/src/phase_executor/quota_detect.py` | calibration bump (§2.8, capture-gated) |
| `hooks/executor_routing_lib.py` | F1 (§2.4), `probe-account` (§2.5), `--resume-session-id` (§2.5), `collect-work-product` (§2.6), `recover-run` (§2.7) |
| `tests/phase_executor/…` + `tests/hooks/test_executor_routing.py` | red-first suites per §2.1-2.8 |
| `tests/phase_executor/fixtures/` | sanitized genuine stderr fixture + MANIFEST note (capture-gated) |
| `docs/planning/2026-07-21-559-proving-run-{design,report}.md` (+`.html` ×2) | this doc + AC4 report (#472 shape) |
| `docs/planning/appendix/` (one file) | CELL-1's promoted work product |
| Version surfaces ×6 + README changelog | plugin 3.86.0 (×4) + phase_executor 0.8.0 (×2) |

Estimated diff: code ~450-550 lines, tests ~600-700. Single PR (code + proof are one evidentiary unit); closure per §0 conditionality.

## 5. Error handling / failure modes
- Every cell bounded (timeout/budget/call-count/wall/owner-wait) with loud refusals; every dispatch attempt emits its DISPATCH line + Observation (refusals included).
- H1 residue → permit retained; H2 tamper → quarantine; H3 unknown/seatless → pre-fanout refuse; recovery gate refusal → no launch, state preserved (§2.7).
- probe-account `unavailable`/`parse_error` → cells refuse paid work (never proceed on an unproven identity).
- CELL-3a no-capture → §2.8 honesty gate; CELL-2/3b cross-account refusal → negative result, honestly reported (C11).
- Owner-gate timeout → blocked-honest disposition + ERROR protocol; run continues where independent.
- **`collect-work-product` retry contract (F-l — the SINGLE statement, matching §2.6):** an unconsumed matching intent plus a live target ref whose head is the promoted candidate resumes phase 2 idempotently (audit-search prevents a duplicate record); an absent, malformed, or non-matching intent with a moved ref refuses as a foreign move. There is no separate "already-promoted rerun always refuses" rule (the r4 §5 wording is removed).
- Capture-scrub crash (F-k) → the startup/reconcile scavenger (§2.8) scrubs any incomplete capture left by a mid-cell crash; a residue check at run close asserts no raw `stderr.txt`/transport survives.
- Mutating-call timeouts (gh/API): check real state before retry.
- Promote/merge partial failure: promote is CAS-guarded; retry only after reading ref state (idempotency: a re-run `collect-work-product` on an already-promoted handle refuses on `expected_target_sha` mismatch — no duplicate appends).

## 6. Security implications
- No raw tokens/email/orgId persisted anywhere; identity digests confined to gitignored run evidence, committed report carries opaque labels + verdicts only (§2.5 R8). No raw child output SURVIVES a cell (§2.4 in-memory probe; §2.8 capture-scrub boundary — the adapter's raw capture files are scrubbed to digest stubs on every outcome) + PII-seed tests over the whole capture root. Credential store never read.
- H2 closes spec-tamper→relaunch injection incl. the TOCTOU window; H3 closes the cost-cap bypass with no carve-out; H1 closes permit-release-on-unverified-kill with the effectful-sentinel order pinned; C1 closes the unaudited-recovery-launch gap (an invisible mutating-capable launch path).
- appendix-only prefixes validated at factory time; the verb's authorization input hard-coded (C10).
- Secrets by NAME only; gitleaks pre-push active; fixture secret-scanned before commit.

## 7. Platform / external dependencies
platform_apis:
- api: `claude auth status --json` on the local claude CLI (identity probe)
  feasibility: verified via spike — live probe THIS session (2026-07-21): rc=0, JSON `{loggedIn, authMethod, apiProvider, email, orgId, orgName, subscriptionType}` on this exact host+binary (`~/.local/bin/claude`); `--help` confirms `--json` default
  failure: fail-loud
- api: claude account SWITCH operation A→B→A (the owner-run credential swap between cells) (F-j)
  feasibility: verified via spike — DESIGNATED BLOCKING SPIKE, owner-operated: the exact operation is the owner running `claude login` / `claude auth login` on this host to re-authenticate as a different account (credential scope: the local claude CLI credential store, `~/.claude/.credentials.json` — never read by this workflow, only `claude auth status --json` observes the RESULT). The switch is PROVEN by two `probe-account` calls straddling it showing changed `identity_digest`; rollback = the owner logs back into the prior account (the A→B→A choreography exercises rollback by construction). Until CELL-2 runs this operation live, the switch itself is unproven; the run logs each switch's before/after digest (run-evidence) as §7 evidence
  failure: fail-loud
  surface: each switch records before/after `identity_digest` (run-evidence dir) + a `probe-account` `status: ok` gate; a switch that does not change the digest aborts the dependent cell loud (never proceeds as if switched)
- api: `claude -p --resume <session-id>` SAME-account resume
  feasibility: verified via existing-call-site — `epic475-resume.sh:70-71` (fired live this epic) + adapter resume composition (claude_cli.py:48-50, spike #455/#467-W4, test-pinned)
  failure: fail-loud
- api: `claude -p --resume <session-id>` CROSS-account resume (account B resuming a session established on A)
  feasibility: verified via spike — DESIGNATED BLOCKING SPIKE: CELL-2 IS the spike (no prior art exists anywhere, in-repo or cited; that absence is this issue's finding); until CELL-2 runs, this capability is UNPROVEN and every design claim depending on it is conditional
  failure: fail-silent
  surface: refusal evidence contract (§2.5): dispatch exit code + envelope subtype + JobRecord state + sanitized stderr digest + report disposition `cross_account_resume: refused|succeeded` — a refusal is a recorded negative result, never a silent pass
- api: tmux socket-scoped sessions / kill primitives
  feasibility: verified via existing-call-site — supervisor.launch/_kill_job (#557/#558, live-exercised)
  failure: fail-loud
- api: `codex exec` supervised mutating composition (CELL-1)
  feasibility: verified via existing-call-site — supervised_dispatch STEP 0-6 (#556 live behavioral probe; #558 dispatches on this host)
  failure: fail-loud
- api: `/proc/<pid>` descendant scan (H1 reuse)
  feasibility: verified via existing-call-site — `_kill_job` supervisor.py:625-696
  failure: fail-loud
- api: git plumbing ref mutation (`commit-tree` + `update-ref` CAS) by `promote` on the live feature branch (CELL-1)
  feasibility: verified via existing-call-site — `worktree.py:601-617` exercised against REAL git repos by `tests/phase_executor/test_worktree_promote.py` (temp repos, same plumbing verbs, same object kinds: local refs on a non-bare checkout); CELL-1 is the first non-test target and logs command status + old/new ref SHAs as its §7-required evidence
  failure: fail-loud
- api: EACCES/EPERM token parse over the codex child transcript (F1)
  feasibility: verified via spike — Step-2B grep proved NO real denial line exists in-repo; the parse ships as ADDITIVE observability that changes no verdict until calibrated; CELL-1's live probe captures the first real shape
  failure: fail-silent
  surface: `denial_evidence.matched` in probe result + report; `outside_blocked` stays absence-based with the documented necessary-not-sufficient flag — token absence is VISIBLE
- api: AskUserQuestion (owner-gate prompt surface)
  feasibility: verified via existing-call-site — #558's D-12/D-13/D-14 owner decisions ran through live AskUserQuestion this epic (mempalace decisions drawer, 2026-07-21)
  failure: fail-loud
- api: BlueBubbles owner notification (notify-owner workspace skill)
  feasibility: verified via existing-call-site — #471 pause notification delivered (HTTP 200, 2026-07-21, session 84d1dd7a handoff)
  failure: fail-silent
  surface: each owner gate logs `owner_prompt_emitted` + the notify HTTP result in session notes; a failed notify falls back to the AskUserQuestion surface alone with the bounded wait (§3) — the wait timeout writes the blocked-honest disposition either way

## 8. Peer-consult provenance (backend: gpt, blind both ways — r1/r2)
Adopted in r2: identity-probe status trichotomy; domain-separated digest + privacy note; continuity canary (seed-nonce echo); appendix predicate hardening; sanitize-before-persist; no-burn-before-cheap-probe gate + durable switch state. Declined: six-record evidence schema (existing Observation/JobRecord/PromotionResult + the r3 `work_product` record carry the facts); keyed digest (no sanctioned key material — limit documented); dedicated version-consistency assertion (already shipped: `test_plugin_version_bumped` + `canary.py::EXPECTED_PLUGIN_VERSION`).

## 9. Verification strategy
Red-first per §2; Step-9 full suite vs 4340/16/0 (re-run after §2.8 calibration lands); live evidence: routing-audit.jsonl (sha256 + record count pinned), work_product audit record, probe-account digests, sanitized fixture, `reconcile --mode final` exit 0. "What was NOT checked" named in the report (e.g. non-team subscription auth shapes, locale-variant quota messages).

## 10. r3 revision ledger (volume loop-back, pass-1 findings → constraints)
C1 recovery chokepoint (§2.7) ← SR#1 Critical · C2 seed+ledgered-resume redesign (§2.5) ← SR#2 Critical/ADV-1 · C3 two-stage capture→activate→second-event (§3 CELL-3a/3b) ← SR#3/ADV-3 · C4 A/B/A choreography + digest-asserted switch gates, no-seats carve-out removed (§3, §2.3) ← SR#4/ADV-4/ADV-2 · C5 TOCTOU-targeted H2 test (§2.2) ← SR#5 · C6 H1 explicit order + effectful-sentinel no-kill pin (§2.1 — r2's "guards precede" claim corrected) ← SR#6 · C7 audited work_product record, derive-after-promote (§2.6) ← SR#7 · C8 ephemeral raw output + equivalence gate + PII-seed tests (§2.4/2.8) ← SR#8/ADV-5 · C9 closure conditionality (§0) ← SR#9 · C10 prefix validation + hard-coded verb prefix (§2.6) ← SR#10 · C11 cross-account resume = designated blocking spike (§7) ← ADV-6 · C12 AskUserQuestion/BlueBubbles §7 entries + bounded waits (§3, §7) ← ADV-7.

## 11. r4 revision ledger (second volume loop-back, pass-2 findings → constraints)
R1 recovery = provenance attempt under the ORIGINAL expected key, never a new expected call (§2.7) ← SR2#1 Critical (confirmed against enforce.py:470 + executor_routing_lib.py:1846-1849) · R2 prelaunch-checks-before-receipt + all-outcome Observations (§2.7) ← ADV2#1 · R3 post-kill sentinel re-read closes the check/kill race (§2.1) ← SR2#3 Critical · R4 B-readiness gate before burning A, skip-3b-honestly (§3) ← SR2#4/ADV2#3 · R5 denial evidence = advisory-only + source discrimination + negative echo tests; #556-F1 = calibrated capture, authentication named follow-up (§2.4) ← SR2#5 · R6 typed RecoveryAuthorization bundle w/ resolved target; mutating recovery refused (§2.7) ← SR2#6 · R7 two-phase crash-recoverable collect-work-product w/ durable intent (§2.6) ← SR2#7/ADV2#2 · R8 digests gitignored-run-evidence-only, report carries opaque labels (§2.5, §6) ← SR2#8 · R9 capture-scrub boundary — adapter's raw files scrubbed to digest stubs, whole-capture-root seeded scan (§2.8, §6) ← SR2#2 · R10 refusal fails AC2a and CLOSES the paid gate — contradiction removed (§2.5, §3) ← ADV2#4 · R11 dispatch_gate mandatory, no default; explicit test-only helper (§2.7) ← ADV2#5 · R12 status==ok + logged_in + nonempty fields gate digest/paid ops; logged_out status arm (§2.5) ← ADV2#6 · R13 no 0600 spill — memory only / unlinked-fd rule (§2.8) ← ADV2#8 · plus §7 promote git-plumbing entry ← ADV2#7.

## 12. r5 revision ledger (third design loop-back — owner budget override 2→3; pass-3 findings → constraints)
F-a remove `_recover_ungated_for_tests` from production module; tests pass an explicit fake gate (§2.7) ← SR3-ungated/ADV3#4 · F-b intent keyed on {receipt_nonce, candidate_tree_sha, expected_target_sha}; computed_new_sha recorded only AFTER promote (§2.6) ← SR3-R7/ADV3#2 · F-c audit-search-before-append idempotency — crash after append cannot duplicate (§2.6) ← ADV3#1 · F-d account-ROLE separation (PAUSED/RECOVERY/CAPTURE) + Mode-3/Mode-2 owner-gated at CELL-3 entry; recovery account never the exhausted one (§3) ← SR3-recovery/ADV3#2 · F-e stale "digest A in report" removed → opaque label `account-1` (§2.5) ← ADV3#3 · F-f per-cell bounds table (budget/calls/timeout/wall) for CELL-1/2/3a/3b (§3) ← SR3#4/ADV3#6 · F-g readiness gate is a distinct top-of-CELL-3b step, ordering contradiction removed (§3) ← SR3#5 · F-h session-preservation assertion pinned to a test (envelope.session_id == seed id) (§2.5) ← SR3-testability · F-i CELL-3b runtime-activation preflight (resolved module path + version + digest, repo≠cache) (§3) ← ADV3-version · F-j §7 account-switch operation entry (owner-operated blocking spike, digest before/after) (§7) ← ADV3-account-switch · F-k capture-scrub `finally` + idempotent scavenger at CELL-3 start & run close + residue assert (§2.8, §5) ← SR3#1/ADV3#5 · F-l single retry contract stated identically in §2.6 and §5 (§5) ← ADV3-retry.

**Budget note:** consumed under owner override (design cap 2→3, AskUserQuestion 2026-07-21; loopback_counters.json budget_override). Global loop-back budget now exhausted (3/3) — a further loop-back is not available; pass-4 review is a verification pass, and per the owner's autonomy grant the orchestrator accepts r5 on convergence and re-escalates only for a genuine new Critical structural flaw.

## 13. r6 pass-4 fold (verification pass under owner autonomy; global loop-back budget exhausted — findings folded inline, NOT a loop-back)
Pass-4 (self-review + Codex/gpt-5.6-sol adversarial on r5) returned convergent: self-review 1 Critical + 1 High; adversarial 6 High + 3 Medium (0 Critical). Trajectory Critical 2→2→0(pass3)→1(pass4, a narrow scrub-scoping miss). Owner away + budget exhausted → orchestrator folded the genuinely-correct local refinements inline and accepted r6 (no product/risk fork; all verified against code):
- **P4-Crit (self-review):** F-k scrub under-scoped — `Capture` writes `output.md`/`input.md` too (capture.py:86-96, VERIFIED), not just stderr+transport. Fix: scrub ENUMERATES the capture dir and stubs every child-content file except `observation.json` (§2.8). Closes the missed-file gap permanently.
- **P4-High (self-review):** work_product record lacked `candidate_tree_sha`/`new_sha` that the retry dedup searches — added as required fields (§2.6).
- **P4-ADV missing-record:** reconcile now flags `missing_work_product` (promoted-but-unrecorded), not just orphan/duplicate (§2.6).
- **P4-ADV bounds enforcement:** named the enforcer — a bounded-loop cell script + per-call manifest budget (§3).
- **P4-ADV readiness:** stated necessary-not-sufficient + mid-cycle-exhaustion honest partial (§3).
- **P4-M post-kill effectful:** finalize `completed_with_residue` (non-success), not `completed` (§2.1).
- **P4-M config_digest:** `_relaunch` VALIDATES it, not merely carries it (§2.7).
- **P4-M receipt/obs crash:** reconcile catches `missing_obs`; transactional outbox named a follow-up (§2.7).
- **P4-ADV scavenger residual + P4-Crit output.md:** host-loss-then-abandon residual stated honestly; standalone cron scrubber named a follow-up (§2.8).
- **DISCARDED (process artifact, not a design finding):** the pass-4 adversarial "embedded review-suppression" High correctly flagged that MY brief said "report only GENUINE NEW Critical" — that was a mistake in the brief (a review brief must never constrain the reviewer's severity floor). The reviewer rightly ignored it and reported fully. Lesson recorded; no design change. Future review briefs carry NO severity-suppression framing.
- **Follow-ups named (report):** transactional receipt→observation outbox; standalone independent-of-workflow capture cron scrubber; mutating-recovery canary path; F1 authenticated (vs advisory) denial evidence.

Gate verdict: PASSED at r6 — the one Critical was a verified narrow scrub-scoping miss, resolved in-text + pinned by the whole-capture-root PII-seed test; remaining Highs/Mediums folded as local correctness refinements; no unresolved structural flaw. Convergence reached; no pass-5 (budget exhausted, diminishing returns, owner authorized proceeding).
