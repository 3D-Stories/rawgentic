# Adversarial Review — 2026-07-28-665-mid-child-handoff-design.md

- Date: 2026-07-28-pass2
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 8 (Critical 0, High 4, Medium 4, Low 0)

## Summary

The design adds durable mid-child state, a five-step handoff ladder, and successor-controlled predecessor teardown. Its main risks are that the ladder does not prove the successor rebuilt working context, guard transitions are inferred from transport success, and recovery or concurrent state mutation can defeat the claimed safety properties.

## Findings

### 1. [High] completeness · high confidence — Section 6, teardown step 1; Section 3 `open_handoff` persistence

> 1. **Hold the lock first.** `plan_lib.file_lock(<driver-state path>)` locks the stable sidecar `<path>.lock` — NOT the state file itself. This matters: `flock` follows the opened inode while `atomic_write_text` installs a NEW inode at the pathname, so locking the state file would let two waiters hold locks on different inodes and interleave read-modify-write. The lock is acquired BEFORE the state is read and released only after the replacement has landed.

The lock is required only for `retire_predecessor`; the design does not establish that `open_handoff` and every other `.driver-state` writer acquire the same advisory sidecar lock. `flock` cannot serialize a writer that does not participate, so another atomic writer can overwrite the claim, acknowledgement, generation, or position and re-enable duplicate handoff or incorrect teardown behavior.

**Recommendation:** In Sections 3, 6, and 8, make the sidecar lock part of the shared `.driver-state` write contract: every read-modify-write path, including `open_handoff`, status changes, claim, and acknowledgement, must hold the same `<path>.lock` from read through replacement. Add a concurrency test that interleaves `open_handoff` with claim/ack and proves neither update is lost.

### 2. [High] correctness · high confidence — Sections 5–6, successor verification and teardown steps 4–6

> 4. **Verify, from its own artifacts:** `spawned`, `goal_armed`, `prompt_landed`, `project_switched`.
> 5. **Ack.** `handoff_ack_started(state, generation, claimant)`, persisted the same way, yielding `state_claimed`.
> 6. **Gate.** `teardown_allowed(results, steps=<the five-step ladder>)`.

The successor creates its own acknowledgement after checking only launch, prompt, goal, and project artifacts. Nothing verifies that it re-derived the WF2 step, checked out the recorded branch, reconciled the baseline, or otherwise rebuilt the mid-child position. Calling `retire_predecessor` prematurely therefore produces all five passing steps and can destroy the only session that still holds the live working context.

**Recommendation:** Change Sections 5 and 6 to add an independent `position_rebuilt` step before acknowledgement and teardown. Require a durable successor-produced record containing the generation, successor session, issue, confirmed WF2 step, actual branch, and rebuild completion status; validate those values against `handoff_pending.position` before `handoff_ack_started` may run.

### 3. [High] correctness · high confidence — Section 6, teardown step 8; Section 8 `/goal clear` platform entry

> 8. **Clear the guard.** `herdr pane send-text <anchor> "/goal clear"` then `herdr pane send-keys <anchor> Enter`. **Both return codes are checked**; failure of EITHER aborts before `pane close`, because an unsubmitted `/goal clear` followed by a close is exactly the prohibited close-before-clear outcome.

Successful `send-text` and `send-keys` return codes prove only that input was transported, not that Claude parsed `/goal clear` or changed the goal state. The design then closes the pane without observing a new `met:true, sentinel:true` record. A silently ignored or delayed slash command therefore reaches the prohibited close-before-clear outcome despite every implemented check passing.

**Recommendation:** In Section 6 step 8, capture a predecessor-transcript baseline before sending `/goal clear`, then poll below that baseline for a matching `goal_status` record with `met:true` and `sentinel:true`. Permit `pane close` only after that semantic confirmation; report timeout or malformed evidence as `clear_unconfirmed` and leave the pane open.

### 4. [High] internal-consistency · high confidence — Section 6, teardown step 2 and late-retirement claim

> 2. **Claim, idempotently.** `handoff_claim(state, generation, claimant=<successor session id>, now_ts=)`. If it refuses, inspect why: a claim that is ALREADY OURS for this generation (same claimant, whether or not `started`) is an accepted continuation, not a failure — a retried teardown must not deadlock. Any other refusal returns `claim_refused` and touches nothing.

A successor can persist the claim or acknowledgement and then die before teardown. A later session necessarily has a different session ID, so this rule rejects it as another claimant. That contradicts the later assertion that any session can complete retirement and can strand a guarded predecessor indefinitely, especially once `started` is true and no expiry or takeover rule is specified.

**Recommendation:** Add a recovery transition to Section 6: a different successor may take over only after proving the recorded claimant session is no longer live and either the lease has expired or an explicit fenced recovery generation has been created. Specify how `started:true` claims are recovered, persist the new claimant atomically, and reject the old claimant by generation or fencing token.

### 5. [Medium] ambiguity · high confidence — Section 3, `kind` discriminator handling

> `_cmd_handoff` REFUSES when `kind == "mid_child"`, naming the reason, and absent `kind` keeps its
> existing meaning (child boundary).

Only the exact `mid_child` value and absence semantics are defined. An unknown, misspelled, or future `kind` has no specified behavior; an equality-only implementation can treat it as the legacy child-boundary form and launch a second successor from a malformed mid-child record.

**Recommendation:** In Section 3, define a closed discriminator contract: `_cmd_handoff` may accept only absent `kind` as legacy child-boundary state and must refuse every present value except explicitly supported kinds. Add validation and regression cases for unknown, non-string, and misspelled values.
**Ambiguity:** The behavior for any present discriminator other than `mid_child` is not specified.

### 6. [Medium] completeness · high confidence — Section 7, anti-parallel-path guard

> - no `hooks/*.py` other than `launcher_lib.py` constructs a herdr `pane split` or `agent start` argv, so a second mechanism cannot appear elsewhere under `hooks/`;

The asserted conclusion is broader than the scanner. A second hook can import the existing argv builder, call a wrapper, invoke a shell/helper outside `hooks`, or add a second ordered sequence inside `launcher_lib.py` without itself constructing the searched argv. The AC7 guard can therefore pass while a forbidden parallel handoff path exists.

**Recommendation:** Replace the source-pattern assertion in Section 7 with an architecture-level enforcement point: expose one launch capability owned by `launcher_lib`, make raw Herdr execution inaccessible to other hooks, and test the call graph or injected executor so every `pane split` and `agent start` used for handoff must pass through the single orchestrator. Retain the synthetic negative test for each supported bypass form.

### 7. [Medium] correctness · high confidence — Section 6, partial-success recovery

> RE-ARM the predecessor by sending its own last unmet goal condition back to the anchor
> pane (the same proven `send-text` + `send-keys` route, condition read verbatim from the successor's
> own transcript via `last_unmet_goal_condition`)

The predecessor's own goal condition is sourced from the successor's transcript, but the design never establishes that the two sessions were armed with identical conditions. On the close-failure path this can arm the predecessor with the successor's guard and then incorrectly report `alive_and_re_armed`.

**Recommendation:** Change Section 6 to record the predecessor's last unmet goal condition in `position` before handoff, or read it from the identity-verified predecessor transcript before clearing. Re-arm using that predecessor-bound value and confirm a new matching unmet `goal_status` record before reporting `alive_and_re_armed`.

### 8. [Medium] feasibility · medium confidence — Section 5, verification step 4; Section 8 session-registry platform entry

> `project_switched` | `claude_docs/session_registry.jsonl` below the offset: one line carrying BOTH the NEW session id AND `project` equal to the expected project

The check ignores `project_path` even though the artifact says the registry provides it, and the text supplies no capability or invariant proving project names uniquely identify repository paths in this configuration. A session bound under the expected project label but to a stale or wrong path can pass and authorize teardown in the wrong repository.

**Recommendation:** Extend `position` with the canonical expected project path and change `project_switched` to require one registry row matching session ID, project, and canonicalized `project_path`. Cite or add a real-config spike covering path comparison and symlink behavior.
**Ambiguity:** The provided text does not establish whether project labels are globally unique or whether registry paths are canonical.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._