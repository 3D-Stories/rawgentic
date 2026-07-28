# Adversarial Review — 2026-07-28-665-mid-child-handoff-design.md

- Date: unknown-date
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 8 (Critical 0, High 6, Medium 2, Low 0)

## Summary

The design extends the existing driver and launcher handoff path with durable mid-child position, successor verification, and successor-driven teardown. Its main risks are an explicit acceptance-contract deviation, insufficient identity binding before destructive teardown, and several unproven or internally inconsistent platform assumptions.

## Findings

### 1. [High] consistency · high confidence — Section 5, “Deviation from AC4's stated order”

> **Deviation from AC4's stated order, declared rather than quietly reordered.** AC4 lists
> `project switched` before `prompt landed` and puts `goal armed` fourth.

The issue body is declared to be the contract, but the design knowingly implements a different ordering. Even if the proposed order is safer, implementation as written will not satisfy AC4 and will create an acceptance/rework dispute.

**Recommendation:** Before implementation, amend AC4 through the owner-decision process so it requires exactly `spawned`, `goal_armed`, `prompt_landed`, `project_switched`, then `state_claimed`; update Section 5 to cite that amended contract rather than declaring a deviation.

### 2. [High] correctness · high confidence — Section 5, verification-ladder step 4

> | 4 | `project_switched` | `claude_docs/session_registry.jsonl` below the offset: a line carrying the NEW session id |

A registry row containing the new session ID proves registration, not that the successor bound the intended project. A successor registered against the wrong project could pass this check, claim the state, and retire the valid predecessor before continuing in the wrong repository.

**Recommendation:** Change the `project_switched` check in Section 5 to require the same row to match the new session ID and an expected durable project identity such as canonical project root or repository ID. Add an exact-object-kind call site or live spike proving those fields are emitted only after the intended rebind.

### 3. [High] correctness · high confidence — Section 6, steps 6–7

> 6. `/goal clear` to the anchor pane via the proven `send-text` + `send-keys Enter` route, return code checked.
> 7. `herdr pane close <anchor>`, return code checked.

The destructive steps operate on the stored anchor pane without first proving that the pane still hosts the originating predecessor session. Syntax validation cannot detect a stale or reused pane ID, so teardown can clear the guard and close an unrelated session.

**Recommendation:** Extend `handoff_pending.position` with `predecessor_session`, capture it when opening the handoff, and add a mandatory pre-teardown check that `herdr pane get <anchor>` still returns that exact session ID. Refuse both `/goal clear` and `pane close` on any mismatch or missing value.

### 4. [High] correctness · high confidence — Section 6, steps 1–2; Section 8, `fcntl.flock` and atomic replacement entries

> 1. `handoff_claim(state, generation, claimant=<successor session id>, now_ts=)` under an exclusive lock on the state file; a refusal returns `claim_refused` and touches nothing.
> 2. Persist the claimed state via `atomic_write_lib.atomic_write_text` (the repo's one home for tmp+replace).

Locking the state file itself and then replacing it atomically is not a stable mutual-exclusion boundary: `flock` follows the opened inode, while tmp+replace installs a new inode at the pathname. Concurrent waiters or new openers can consequently hold locks on different inodes and perform overlapping read-modify-write operations, losing claim or acknowledgement updates and potentially permitting competing handoffs.

**Recommendation:** Change Section 6 and the platform declaration to lock a stable sidecar such as `.driver-state.lock`, acquire it before rereading `.driver-state`, hold it through validation and atomic replacement, and release it only after the replacement is durable.

### 5. [High] internal-consistency · high confidence — Section 9, `/goal clear` or `pane close` failure row

> | `/goal clear` or `pane close` fails | reported with its return code as a failed teardown, not swallowed; predecessor may still be alive and is still guarded |

If `/goal clear` succeeds but `pane close` fails, the predecessor may be alive but is no longer guarded. The failure table therefore masks the design's dangerous partial-success state, where both sessions can remain live after the predecessor's guard has been removed.

**Recommendation:** Replace this Section 9 row with separate clear-failure and close-failure cases. For close failure after a successful clear, specify bounded close retries plus a proven recovery action that re-arms or otherwise quiesces the predecessor; report the state explicitly as `alive_and_unguarded` until containment succeeds.

### 6. [High] internal-consistency · high confidence — Section 8, `/goal clear` platform surface

> surface: retire_predecessor checks the return code of both the send-text and the pane close and reports a failed teardown rather than swallowing it; a cleared-but-surviving session is bounded by the pane close that follows

The route consists of `send-text`, `send-keys Enter`, and `pane close`, but this surfacing contract checks only the first and third calls. If `send-keys` fails silently, `/goal clear` remains unsubmitted and the subsequent close reproduces the design's own prohibited close-before-clear outcome.

**Recommendation:** Replace the Section 8 surface sentence with: “`retire_predecessor` checks and logs the return code of `send-text`, `send-keys Enter`, and `pane close`; failure of either clear command aborts before `pane close`.” Add a regression test for successful `send-text` followed by failed `send-keys`.
**Ambiguity:** Section 6's singular phrase “return code checked” could be intended to cover both commands, but Section 8 explicitly enumerates only send-text and pane close.

### 7. [Medium] feasibility · high confidence — Section 5, verification-ladder step 3; Section 8

> | 3 | `prompt_landed` | successor transcript below the offset: the handoff marker token from the resume prompt |

The platform declaration proves cross-pane text delivery and the separate `goal_status` record shape, but it provides no exact-object-kind call site or spike proving that this project's Claude transcript persists an arbitrary pasted marker token verbatim. This load-bearing behavior is unverifiable from the provided text; normalization or omission would make every handoff fail at `prompt_landed`.

**Recommendation:** Add a `platform_apis` entry for transcript persistence of pasted user content, backed by a live spike under the real configuration and a real-shaped fixture. If exact persistence is unavailable, replace token scanning with an explicit durable successor acknowledgement artifact.

### 8. [Medium] feasibility · high confidence — Section 8, platform-feasibility declaration

> - api: /goal clear submitted cross-pane through that same send-text route
>   feasibility: verified via spike — run live 2026-07-27 (epic #667 log D-16 and #654): the target session reports met=true sentinel=true, so the guard is provably removed; this is proof of guard REMOVAL only and NOT of a cleared session exiting, which stays unverified and is why teardown closes the pane afterwards
>   failure: fail-loud
>   surface: retire_predecessor checks the return code of both the send-text and the pane close and reports a failed teardown rather than swallowing it; a cleared-but-surviving session is bounded by the pane close that follows

`herdr pane close <anchor>` is load-bearing but has no separate capability-file, exact call-site, or spike evidence for the pinned 0.7.5 configuration. Mentioning it as containment for another API does not prove its accepted argv, target semantics, timeout behavior, or availability, so the claim that every load-bearing API was probed is unverifiable from the provided text.

**Recommendation:** Add a distinct `platform_apis` entry for `herdr pane close <pane>` citing its exact shipped builder and exact-object-kind call site plus a live 0.7.5 spike. Specify how nonzero exit, timeout, and an already-closed pane are surfaced and classified.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._