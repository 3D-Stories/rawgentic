# Adversarial Review — 2026-07-28-665-mid-child-handoff-design.md

- Date: 2026-07-28-pass3
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 7 (Critical 1, High 4, Medium 2, Low 0)

## Summary

The design adds durable mid-child state, a six-step verification ladder, and successor-controlled predecessor teardown. Its safety claim is undermined by a crash window after guard clearing, unlocked existing state writers, and several verification inputs whose acquisition or freshness is not defined.

## Findings

### 1. [Critical] correctness · high confidence — §6, steps 8–9 and “The partial-success state is named and contained”

> 8. **Clear the guard, then CONFIRM it cleared (R3).** Baseline the PREDECESSOR's transcript, send `herdr pane send-text <anchor> "/goal clear"` then `herdr pane send-keys <anchor> Enter` (both return codes checked; failure of either aborts before `pane close`), then **poll the predecessor's transcript below that baseline for a `goal_status` row with `met:true` and `sentinel:true`**. A zero return code proves only that keystrokes were transported — not that the slash command was parsed or that the guard actually changed state. Without the semantic confirmation a silently ignored `/goal clear` reaches exactly the close-before-clear outcome this design forbids, with every implemented check green. On timeout or malformed evidence: report `clear_unconfirmed` and **leave the pane open**.
> 9. **Close the pane.** `herdr pane close <anchor>`, return code checked, bounded retries (2).
> 
> **The partial-success state is named and contained.** If the clear is confirmed but the close then
> fails, the predecessor may be alive and **no longer guarded** — strictly worse than either failure
> alone. Handling: retry the close twice; if it still fails, RE-ARM the predecessor by sending
> `position.goal_condition` (the predecessor's OWN recorded condition, per §3) back to the anchor pane
> and confirming a fresh `met:false` row appears in the predecessor's transcript; then report the
> terminal state as `alive_and_re_armed`, or `alive_and_unguarded` if the re-arm or its confirmation
> also fails. `alive_and_unguarded` is the one state this design treats as an incident.

The recovery logic only runs when `pane close` returns failure. If the successor process dies after the clear is confirmed but before close completes or before the failure handler re-arms the guard, the predecessor remains alive and unguarded. This directly contradicts the later claim that a successor dying mid-teardown leaves the predecessor guarded.

**Recommendation:** Replace §6's two-step clear/close sequence with a recoverable state machine. Persist `teardown_phase: "clearing"` before sending `/goal clear`, and add a fenced recovery actor permitted after claimant death or lease expiry to verify pane/session identity, inspect the current predecessor goal state, and either close the pane or re-arm it. Add crash-injection tests at every boundary between clear, confirmation, close, and re-arm; remove the unconditional safe-state claim until all boundaries recover.

### 2. [High] completeness · high confidence — §5, verification ladder step 5

> Six checks, in **causal** order. Each reads an on-disk artifact or live git state; none reads pane
> text.
> 
> | # | check | artifact |
> |---|---|---|
> | 1 | `spawned` | `herdr pane get <new>` yields a non-empty `agent_session.value` |
> | 2 | `goal_armed` | successor transcript below the pre-launch offset: a `goal_status` attachment with `met:false` whose condition equals the one actually armed |
> | 3 | `prompt_landed` | successor transcript below the offset: the handoff marker token, matched as a plain SUBSTRING |
> | 4 | `project_switched` | `claude_docs/session_registry.jsonl` below the offset: ONE line carrying the NEW session id AND `project` AND `project_path` equal to the recorded values |
> | 5 | `position_rebuilt` | the successor's LIVE git state: `git rev-parse --abbrev-ref HEAD` equals `position.branch`, and the step + baseline it reports back equal `position.step` + `position.test_baseline` |
> | 6 | `state_claimed` | `.driver-state` `handoff_claim` with the matching generation and claimant and `started: true` |

Only the branch component of `position_rebuilt` has a defined evidence source. The design does not specify where or how the successor “reports back” its step and baseline, how those values are associated with the generation and claimant, or why they prove a rebuild rather than merely echoing the durable record. Implementers therefore cannot build the stated check consistently, and an echo-only implementation permits predecessor retirement before position is actually rebuilt.

**Recommendation:** Define the complete `position_rebuilt` evidence contract in §5: name its persisted artifact, schema, writer, generation and claimant binding, and the operation that produces each observed value. Do not accept step or baseline as free CLI arguments; persist them from the workflow checkpoint that performs the rebuild and validate that record under the state lock before acknowledgement.
**Ambiguity:** The report-back transport, schema, producer, and trust boundary are unspecified.

### 3. [High] correctness · high confidence — §3, “All `.driver-state` read-modify-write goes through ONE locked helper”

> **All `.driver-state` read-modify-write goes through ONE locked helper (R3).** An advisory lock only
> serialises writers that participate, so a lock held by `retire_predecessor` alone would not stop
> another writer from clobbering the claim, the ack, the generation, or the position. Both writers this
> change introduces — the predecessor's `mid-child-handoff` and the successor's `retire-predecessor` —
> go through a single helper that holds `plan_lib.file_lock(<state path>)` across read → validate →
> `atomic_write_text`. **Stated boundary:** the pre-existing prose-driven writers (the epic-run skill's
> status updates) are outside this change and do not yet take the lock; that is recorded as a follow-up
> rather than silently implied to be done.

The design explicitly leaves existing writers able to perform unlocked read-modify-write operations on the same file. Such a writer can erase a persisted claim, acknowledgement, generation, or position while teardown continues from previously computed results, allowing destructive action without the state record that is supposed to fence it.

**Recommendation:** Make migration of every `.driver-state` writer to the shared locked helper a prerequisite in §3, including the epic-run status writers. If that scope cannot be included, move the handoff generation, position, claim, acknowledgement, and teardown phase into a separate atomically replaced file that no legacy writer rewrites.

### 4. [High] correctness · high confidence — §5, “Precise claim for step 2”

> **Precise claim for step 2.** `transcript_has_unmet_goal` proves the guard **was armed and unmet at
> some point after the baseline** — not that it is unmet at read time. That is the correct claim for
> teardown authorisation: the question is whether the successor was ever handed a guard.

Historical presence of an unmet-goal row does not prove the successor remains guarded when the predecessor is retired. A subsequent goal clear or other terminal goal-status row can exist while step 2 still passes, recreating the artifact's initial failure mode: the predecessor is destroyed while the continuing session has no active completion guard.

**Recommendation:** Change step 2 in §5 to require that the latest applicable post-baseline `goal_status` transition for the exact armed condition is still `met:false`, with no later clear or replacement. Re-run this current-state check immediately before clearing the predecessor guard, after claim, acknowledgement, and target-identity validation.

### 5. [High] feasibility · high confidence — §5, verification ladder step 1

> | 1 | `spawned` | `herdr pane get <new>` yields a non-empty `agent_session.value` |

The successor is required to re-derive this check, but the durable record contains only the predecessor pane and the described successor entry point only receives `--anchor-pane`. The artifact provides no source for `<new>`, no exact call site mapping the caller session to its pane, and no capability or spike proving such discovery works under the project configuration. Consequently the successor cannot implement `spawned` as specified without an assumed platform dependency or an extra unverified input.

**Recommendation:** Amend §§3, 5, and 6 so the predecessor records the newly created successor pane and its observed session ID under the state lock after `pane get`, binding both to the generation. Require `retire_predecessor` to prove that the caller session matches that stored successor identity, and add a platform-feasibility entry and spike for this exact write/read path.
**Ambiguity:** The design never defines how the successor obtains the `<new>` pane identifier needed by the command.

### 6. [Medium] feasibility · high confidence — §8, `git rev-parse --abbrev-ref HEAD` platform declaration

> - api: git rev-parse --abbrev-ref HEAD as the position_rebuilt evidence
>   feasibility: verified via existing-call-site — this repo's own hooks and skills already read git state through subprocess with shell=False (hooks/plan_lib.py scan helpers, WF2 Step 7's base assertion); the command is run through the same injected runner the rest of retire_predecessor uses, so tests drive it without a real repo
>   failure: fail-loud
>   surface: a non-zero return, an unparseable branch name, or a branch that differs from position.branch fails position_rebuilt closed, which blocks teardown while leaving the predecessor guarded

The cited generic subprocess usage proves that Git can be invoked, but not that this exact successor-side invocation runs in the repository recorded by `project_path`. No `cwd`, `git -C`, or top-level identity assertion is specified. Running from another repository with the same branch name can therefore satisfy the live-state check and authorize teardown for the wrong working tree.

**Recommendation:** Change the §8 API entry and §5 check to run Git with an explicit validated repository directory and first assert `git rev-parse --show-toplevel` identifies the repository bound by the matched registry record. Add an exact-object-kind test or spike showing the successor-side entry point receives and uses that directory under the real hook configuration.
**Ambiguity:** The injected runner's working directory for this entry point is not stated or proven.

### 7. [Medium] internal-consistency · high confidence — §3, “A failed handoff leaves a stale `handoff_pending`, harmlessly”

> **A failed handoff leaves a stale `handoff_pending`, harmlessly.** The record is written before the
> pane is split (the successor cannot claim what was never written), so an aborted handoff leaves one
> behind. It cannot be claimed by anything: `handoff_claim` is pinned to the generation, and the next
> `open_handoff` bumps the counter and supersedes it.

Generation pinning does not make the record unclaimable before the next `open_handoff` occurs; at that point it is still the current generation. A delayed successor or another caller can claim it, consume the lease, or create state that the text labels impossible. The record only becomes stale and fenced after a later generation is persisted.

**Recommendation:** Rewrite §3 to distinguish an abandoned current-generation record from a superseded record. Specify who may claim the former, how an aborted launch marks it cancelled, and require `handoff_claim` to refuse cancelled records; reserve “unclaimable” for records whose generation is lower than the persisted current generation.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._