# #559 Proving-Run Report — end-to-end proving run: codex mutating cell + account-switch recovery (H6)

**Issue:** #559 (last child of epic #560, executor-hardening) · **Branch:** `feature/559-proving-run` · **Design:** [`2026-07-21-559-proving-run-design.md`](./2026-07-21-559-proving-run-design.md) (r6, Step-4 gate PASSED) · **Date:** 2026-07-21

## Verdict (first)

- **BUILD — COMPLETE.** Seven security-hardening tasks (H1/H2/H3, F1, the recovery-dispatch chokepoint, the AC1 promote/`work_product` glue, the AC2a account-identity probe + resume-dispatch) shipped **test-first**. Full suite **4340 → 4396, 0 failing**; both pylint lanes **10.00/10**; a two-reviewer Step-8a wave (independent Opus reviewers, security/correctness + contract/regression lenses) found **no Critical or High defect**.
- **OPS (live cells) — DEFERRED, honest partial.** Every paid cell and every account switch is an **owner-gated** durable pause (design §3). The owner is away and pre-directed *"defer per design §0."* Per the §0 conditionality, this PR ships the hardening code + this report as **`Part of #559` / `Part of #560`**; issues #559 and #560 **stay OPEN** for an owner-attended live pass.
- **Net.** The wired executor path's **hardening is proven in code** — unit + integration, red-first, independently reviewed. The **live end-to-end proof** (a genuine `quota_paused` transition and a real cross-account recovery) awaits an owner-attended session; the design anticipated exactly this fork (§0), so a deferral here is a designed outcome, not a failure.

## 1. Scope (design §0)

Two natures in one issue: **BUILD** (code, test-first) and **OPS** (live, paid, evidence-producing cells). Binding owner decisions: D-12 (live `quota_paused` transition + genuine-capture calibration land here), D-13 (codex bound = enforced timeout), D-15 (H3 fix lands once; #449 inherits), cheap-probe-before-burn. **Issue-closure is conditional (§0):** the PR closes #559 + #560 only if *every* mandatory live AC passes; otherwise it ships the code + an honest partial report as "Part of", posts the owner-gate blocker, and leaves the issues open.

## 2. BUILD results — T1–T7 (all committed + pushed, red-first)

| Task | Hardens | Key file:line | Red-first tests | Verdict |
|---|---|---|---|---|
| T1 · H1 | `mark_quota_paused` guard order + **post-kill sentinel re-read** (check/kill race → `completed_with_residue`, never `quota_paused`); permit released only on verified kill | `supervisor.py:610-648` | verified-kill / residue / pre-kill-effectful / post-kill-race | ✅ closed (Reviewer A bypass attempt held) |
| T2 · H2 | `_relaunch` reads via `_verified_spec` — the post-`_identity_matches` **TOCTOU** window; tamper → quarantine, never launch | `supervisor.py:1179-1183`, `_verified_spec:1419` | TOCTOU (patch identity True over tampered file) + direct tamper unit | ✅ closed |
| T3 · H3 | unknown/seatless competitive seat **fails loud**, no carve-out | `engine.py:301-307` | flip `:448` to raise + seatless-table-fails-loud | ✅ closed |
| T4 · F1 | behavioral-probe denial-evidence parsed **in memory**, raw ephemeral, **advisory-only** (never flips a verdict), no PII | `executor_routing_lib.py:839-869` | exec_event vs prose source · negative-echo · silent · PII-seed | ✅ closed |
| T5 · AC2a | `probe-account` identity observation (digest+categories, no PII) + `dispatch --resume-session-id` (claude-only, non-mutating, session-preservation asserted) | `executor_routing_lib.py:872-922`, resume dispatch | status arms · digest stability · no-`@` · resume compose/refuse/mismatch | ✅ closed |
| T6 · AC1 | `promote_appendix_only` path policy (traversal-hardened, component-boundary) + `work_product` audit kind + `collect-work-product` two-phase idempotent | `worktree.py:103-136`, `enforce.py`, `executor_routing_lib.py:1341-1452` | factory/candidate malicious-prefix · orphan/duplicate · idempotent double-run | ✅ closed (see §4 L1/L2 for the one binding gap) |
| T7 · C1 | **recovery-dispatch chokepoint** — `recover(*, dispatch_gate)` mandatory, prelaunch-before-receipt, mutating refused, `RecoveryAuthorization` target/digest bound, recovery groups under original key | `supervisor.py:1060,1179-1214`, `enforce.py`, `executor_routing_lib.py:1455` | run-closed · mandatory-gate TypeError · mutating-refused · target-drift · synthetic-obs | ✅ closed |

Commits: `0cc0db5` (T1) · `1423b10` (T2) · `1d0296d` (T3) · `e663c7a` (T4) · `6af24fe`/`296a2d6` (T5) · `e03c3c2`/`1620c26`/`47d3e84` (T6) · `2ba866b` (T7) · `8c94a14` (Step-8a remediation).

## 3. Live cells (OPS) — dispositions

**All DEFERRED.** Design §3's hard gate is *no paid exhaust until the full suite is green AND CELL-2 passed*; CELL-2 requires an owner-gated account switch (A→B). Owner away → CELL-2 cannot pass → every downstream paid cell is gated shut. No `AskUserQuestion`/BlueBubbles interrupt was raised for the switch: the owner **pre-decided** "defer per design §0" (an account switch is not the "genuine Critical" the owner reserved BlueBubbles for).

| Cell | AC | What it would prove | Disposition |
|---|---|---|---|
| CELL-1 | AC1 | live codex **mutating** dispatch → `collect-work-product` → CAS promote → `work_product` audit | DEFERRED — paid burn behind the CELL-2 gate |
| CELL-2 | AC2a | seed on A → **switch A→B** → resumed turn echoes the seed nonce; two `ok` probes' digests differ | DEFERRED — **owner-gated** account switch |
| CELL-3a/3b | AC2b | genuine usage-limit capture → calibrate + **activate** classifier → live `quota_paused` → cross-account `recover-run` | DEFERRED — gated behind CELL-2 + paid exhaust |
| CELL-4 | AC3 | `close-run` → `reconcile --mode final`, zero anomalies incl. the recovery group | DEFERRED — nothing live to reconcile; run-end verb is #420-scoped |

**Non-billable evidence gathered this session:** a live `probe-account` smoke on this host returned `{status: ok, logged_in: true, subscription_type: team, auth_method: claude.ai, identity_digest: set}` with **no email/orgId/token** in the output — confirming the AC2a identity-probe machinery works live. This is necessary-not-sufficient for AC2a, which requires the *change* between two distinct accounts (the owner-gated switch).

## 4. Step-8a review — findings + dispositions

Two independent Opus reviewers, neutral briefs. **No Critical/High.** Five findings, triaged together:

| Finding | Sev | Disposition |
|---|---|---|
| **L3** — `probe_account` digest concatenated `email + "\|" + orgId` with an unescaped delimiter (`("a\|b","c")` and `("a","b\|c")` collide) → a real A→B switch could read "no change" | Low | **FIXED** (`8c94a14`) — JSON-encode the pair (injective), prefix `v1→v2`, red-first collision test |
| 5× pre-existing T1–T7 **lint errors** (`no-name-in-module` on sys.path-shimmed `phase_executor` submodule imports) — never surfaced because the branch was scoped-tested, never full-linted | — | **FIXED** (`8c94a14`) — suppressed per the established in-file pattern; lint is a HARD CI gate |
| **L2 / F-1** — `missing_work_product` reconcile guard is **dead in the real pipeline** (fires only on an Observation-embedded promotion; `collect_work_product` writes the audit record instead) | Med | **Documented + follow-up [#570](https://github.com/3D-Stories/rawgentic/issues/570).** Fix = a new "expected work_product" signal (design addition); zero operational impact this run |
| **L1** — `collect_work_product` crash-window (host-loss between `promote()` update-ref and the `new_sha` write) loses the record; design F-l's live-ref reconstruction is unimplemented | Low | **Documented + [#570](https://github.com/3D-Stories/rawgentic/issues/570).** Correctness-sensitive two-phase logic; no rushed redesign under owner-away autonomy |
| **F-2** — no e2e `recover_run → reconcile` test with original+recovery rows | Low/Med | **#420-scoped.** `enforce.py:498-500` states reconcile has no live caller until #420; both seam sides are already unit-covered (`recover_run` emits `recovered_from`+obs @ test 2175; grouping reconciles clean @ `test_enforce.py:527-538`). A hand-wired test fights fake-vs-real target alignment #420 will wire properly |

Out-of-scope one-liners (pre-existing fail-closed posture, not this PR): a *misused* `--resume-session-id` on a non-claude seat leaves an orphan expected-call; a corrupt `routing-audit.jsonl` makes `collect_work_product` raise a bare traceback.

## 4b. Step-11 + 11.5 pre-PR review (independent Opus + cross-model Codex)

- **Step 11 (independent Opus, full pre-PR diff):** no new Critical/High/Medium. Re-ran the whole gate green (suite 4396/16/0, both pylint lanes 10.00/10); confirmed the L3 fix sound + complete and every 8a-cleared hardening holds. One Low (atomic-write convention deviation) → #571.
- **Step 11.5 (cross-model, Codex `gpt-5.6-sol` diff review):** 0 Critical, 7 High + 3 Medium — all confirmed against code, all in OPS-cell-only paths that DEFER this run (zero operational impact now). Report: `docs/reviews/559-prepr-diff-2026-07-21.md`.

Fixed in-branch (commit `2456513`, red-first):

| Finding | Sev | Fix |
|---|---|---|
| F3 | High | `_norm_rel_components` treated `\` as a path separator → a literal-backslash filename folded into the appendix prefix (policy bypass). Now **rejected** (POSIX filename char, not a separator). |
| F9 | Med | `resume_dispatch` appended a foreign-correlation observation **before** the mismatch check → audit poisoning. Check **moved before append**. |
| F10 | Med | `_is_exec_event` docstring claimed "OS-attested, not spoofable" while admitting the schema is unknown. Softened to "event-shaped, NOT authenticated" (sound only because the field is advisory and never gates a verdict). |

Deferred to **#571** (design-scope / disproportionate test cost, all defer): F5 (recover target binding), F6 (recover obs correlation parity — belt-and-suspenders: `await_job` already session-id-asserts resumes), F7 (collect preconditions), F8 (`_run_resume` except breadth), atomic-write hygiene. F1/F2 remain in **#570**.

## 5. AC verdicts

- **AC1** (codex mutating cell): **code shipped + tested**; live CELL-1 deferred → live-UNPROVEN.
- **AC2a** (account-switch recovery): probe + resume-dispatch shipped + tested; probe live-smoked on one account; cross-account CHANGE + resumed turn (CELL-2) deferred → live-UNPROVEN.
- **AC2b** (genuine capture + calibration + activated cycle): D-12 plumbing shipped + tested; CELL-3a/3b deferred → **allowlist stays EMPTY, classifier stays SHADOW** (§2.8 honesty gate engaged) → live-UNPROVEN.
- **AC3** (final reconcile): recovery-group binding tested (unit); run-end verb #420-scoped; CELL-4 not run → live-UNPROVEN.
- **AC4** (committed report): **SATISFIED** (this document + its rendered HTML + published Artifact).

## 6. Closure (design §0)

Not every mandatory live AC passed → the PR ships as **`Part of #559` / `Part of #560`**. Issues **#559 and #560 stay OPEN**. An owner-gate blocker is posted on #559. A future **owner-attended** session runs the live cells via the repeatability appendix below.

## 7. Repeatability appendix — owner-attended live pass

Prereqs: full suite green (✅ 4396/16/0), plugin installed at the merged version, ≥2 (ideally 3) Claude accounts available. Run from `projects/rawgentic`, run-id `wf2-559-<session>`.

1. **CELL-1 (AC1):** `dispatch --seat build` (supervised codex, canary 0–5) writing one file under `docs/planning/appendix/` → `collect-work-product --run-id <r> --session-name <s> --target-ref refs/heads/integration --expected-target-sha <sha>` → `verify_post` requested==actual. Bounds: D-13 timeout, build `max_budget_usd` 10.0.
2. **CELL-2 (AC2a):** seed a `claude -p --output-format json` nonce turn on account A (capture its session id) → **owner switch A→B** (`AskUserQuestion` + BlueBubbles + `owner_prompt_emitted` marker, 60-min bound) → `probe-account` digests differ → `dispatch --resume-session-id <sid>` on B → assert the resumed envelope's `session_id` == seed id AND the output echoes the nonce. A cross-account **refusal** is a valid negative spike that FAILS AC2a and **closes the paid gate** (skip 3a/3b).
3. **CELL-3a/3b (AC2b):** bounded exhaust the CAPTURE account (20 calls / 30 min / first limit) → sanitize + equivalence-gate + secret-scan → fixture → bump `CLASSIFIER_VERSION`+`RULE_TABLE_DIGEST` → add the one `(2, digest)` pair to `CALIBRATED_CLASSIFIERS` → **full-suite re-run** → F-i runtime-activation preflight → readiness probe on the RECOVERY account → exhaust the PAUSED account under the activated pair → live `quota_paused` → `recover-run` (MAX_RESUME≤2) → verified completion. No genuine capture → honesty gate: allowlist stays EMPTY, closure conditionality applies.
4. **CELL-4 (AC3):** `close-run` → `reconcile --mode final` → exit 0, zero anomalies across all buckets incl. the pause/recover correlation group and the `work_product` bindings.

Per-cell bounds and owner-gate points are in design §3 (bounds table + choreography).

## 8. Confirmed engine gaps → follow-ups

- **[#570](https://github.com/3D-Stories/rawgentic/issues/570)** (filed): `work_product` reconcile "missing" guard unreachable + `collect_work_product` crash-window (8a-L1/L2).
- **[#571](https://github.com/3D-Stories/rawgentic/issues/571)** (filed): pre-PR cross-model hardening follow-ups — recover/resume/collect binding + robustness (F5/F6/F7/F8 + atomic-write hygiene).
- **F-2 → #420**: the run-end `reconcile_run` real-record join (`enforce.py:498-500`).
- The `reset.v1/b` digit-proximity false-positive (#558 follow-up) can only be re-checked against a **genuine** captured stderr — deferred with CELL-3a.

## 9. What was NOT checked (explicit)

- Live codex **mutating** dispatch (CELL-1) — no paid burn.
- Live **cross-account** resume + digest *change* (CELL-2) — owner switch required; only a single-account probe was smoked.
- Genuine usage-limit **capture** and the **activated** live `quota_paused` transition (CELL-3a/3b) — paid, gated.
- Run-end **final reconcile** over real records (CELL-4) — #420-scoped + gated.
- The L3 digest change was verified against **synthetic** identities only; a real second-account digest-difference is exactly what CELL-2 would prove.
