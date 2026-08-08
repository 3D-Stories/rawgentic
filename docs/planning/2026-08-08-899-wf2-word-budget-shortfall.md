# #899 — the SKILL.md word shortfall, reported per AC1's own alternative

**Issue:** #899 (epic #906 M2) · **Date:** 2026-08-08 · **Head:** `c7eb1fce`
**Decision:** D304 · **Baseline:** 6460 passed, 0 failed, exit 0

---

## The answer first

`skills/implement-feature/SKILL.md` is **6,442 words**. AC1's target is **5,000**, so **1,442 words**
must go. By AC1's stated means — *"moving genuinely step-scoped prose into the step files #874
created"* — the number of words that can move is **zero**.

AC1 anticipated this and supplies the alternative in its own text:

> `implement-feature/SKILL.md` is **at or under 5,000 words** — **or, if that proves to require
> moving an always-loaded contract, the shortfall is reported with the specific blocks that would
> have to move and why they cannot.**

This document is that report.

## The measurement

Every XML-ish block in `SKILL.md` at `c7eb1fce`, classified and summed:

| Class | Words | Why it cannot move |
|---|---:|---|
| **SYNCED** | **2,112** | Generated into `SKILL.md` by `scripts/sync_shared_blocks.py`. |
| **AC2-protected** | **753** | AC2 names these three explicitly and re-confirms them cross-step. |
| **Cross-step** | **2,718** | Each is read by several steps; moving any into one step file breaks the others — the same argument AC2 makes for its three. |
| Headings, the step list, spine prose | 859 | The spine itself. Not a block, not movable. |
| **Genuinely free to move** | **0** | — |
| **Total** | **6,442** | |

### The synced blocks (2,112 words)

| Block | Words | Synced into |
|---|---:|---|
| `model-routing-resolve` | 774 | implement-feature |
| `review-severity` | 544 | implement-feature **and fix-bug** |
| `loop-back-budget` | 442 | implement-feature |
| `config-loading` | 352 | implement-feature |

Moving one out means editing `scripts/sync_shared_blocks.py`'s MANIFEST. `review-severity` is
**shared with `fix-bug`**, and **AC3 forbids touching it here**:

> **Shared-block sources are NOT edited here** — `shared/blocks/review-severity.md` is shared with
> **fix-bug**, so reshaping it changes WF3. That belongs in its own issue if ever wanted.

Verified at `c7eb1fce`: `scripts/sync_shared_blocks.py:63` reads
`"review-severity.md": ["implement-feature", "fix-bug"]`.

### The AC2-protected blocks (753 words)

| Block | Words | Read by |
|---|---:|---|
| `completion-gate` | 454 | `step-05.md`, `step-09.md`, `step-12.md`, `state-and-resume.md` |
| `early-smoke-install` | 159 | `step-08.md`, `step-15.md` |
| `probe-before-design` | 140 | `step-03.md`, `step-04.md` |

All three re-verified live at `c7eb1fce` by grepping the `references/` directory. AC2 is correct and
remains correct.

### The cross-step blocks (2,718 words) — the finding AC1 did not anticipate

These carry no AC protection, and they are the obvious candidates for a trim. **They cannot move for
exactly AC2's reason**: each is read by more than one step, so relocating it into a single step file
breaks every other reader.

| Block | Words | Why it is cross-step |
|---|---:|---|
| `mandatory-steps` | 714 | Names which steps may never be skipped — a statement ABOUT all steps. |
| `step-tracking` | 539 | The marker contract every step's DONE line must satisfy. |
| `test-run-discipline` | 291 | Binds Step 2 (baseline) and Step 9 (regression gate) TOGETHER. |
| `constants` | 176 | Read by Steps 4, 8a and 11. |
| `review-lens-routing` | 177 | Assigns lenses at Steps 4, 8a and 11. |
| `happy-path` | 174 | The spine's own ordering. |
| `review-pipelining` | 158 | Applies at every review wave (4, 8a, 11). |
| `references` | 104 | The read-set contract for the step files. |
| `ambiguity-circuit-breaker` | 73 | Active at Steps 4, 6, 9, 11, 15. |
| `error-protocol` | 209 | Any step may hit a blocker. |
| `role` | 66 | The orchestrator's identity. |
| `termination-rule` | 37 | WF2's terminal condition. |

## Why this is the state, and not a failure

**#874 already did the work #899 assumes is still available.** It split `references/steps.md` into
per-step files and moved every genuinely step-scoped paragraph out. What remains in `SKILL.md` is,
by construction, the part that is *not* step-scoped: the always-loaded contract.

That makes AC1's premise — that step-scoped prose is still sitting in the body — **true when #899
was filed and false by the time it ran**. The right response is the one AC1 itself names.

## What ships instead

**AC4, in full**, and it is the durable half. `tests/test_wf2_prose_budget.py` gains a word budget
beside its byte ceilings, reusing the existing violation-reporting shape:

- `SKILL_WORD_CEILINGS` pins `SKILL.md` at **6,764** = actual 6,442 + 322 allowed headroom, matching
  how the byte ceilings are calibrated.
- `word_violations()` mirrors `budget_violations()`: same classes, same "name the path, carry actual
  + ceiling + delta" contract, same never-quote-content rule.
- The guard fails in **both** directions — growth past the ceiling, and a ceiling left stale above a
  shrink — so the ceiling must come DOWN as prose shrinks.

**Proof it bites, measured before shipping:** run against the live file with the ceiling set to
**6,292** — the count #899's body recorded at `0d2ba0e0` — the guard reports
`OVER WORD CEILING: SKILL.md is 6442 words — 150 over its 6292-word ceiling`. **The exact drift
#899 was filed about would have failed CI** had this guard existed at #874.

## The follow-on, named rather than implied

Reaching 5,000 words needs a change AC3 deliberately puts out of scope: reshaping the shared blocks,
starting with `review-severity` (544 words, shared with `fix-bug`). That is a WF3-affecting change
and belongs in its own issue with its own review. **Not filed** — per the issue throttle, filing is
the owner's call, not this run's.

The word ceiling lowers as prose shrinks, so the guard tracks progress toward 5,000 automatically
rather than needing to be remembered again.

## The claim most likely to be wrong — checked, and it WAS partly wrong

I first wrote that the twelve "cross-step" blocks were classified by reading what each is *about*
rather than by grepping for readers, and that a block with exactly one reader would be movable.
**I then ran that grep instead of leaving it as a caveat.** The result corrected the reasoning:

| Block | Step-file readers |
|---|---|
| `references` | 9 — step-02, 04, 05, 06, 08, 09, 14, 15, 16 |
| `role` | 5 — step-00-preamble, 04, 08, 11, run-record |
| `test-run-discipline` | 4 — step-02, 08, 09, 12 |
| `constants` | 3 — step-08, 11, run-record |
| `review-lens-routing` | 3 — step-04, 08, 11 |
| `review-pipelining` | 3 — step-04, 08, 11 |
| `mandatory-steps` | **0** |
| `step-tracking` | **0** |
| `error-protocol` | **0** |
| `happy-path` | **0** |
| `ambiguity-circuit-breaker` | **0** |
| `termination-rule` | **0** |

Six blocks (1,746 words) have **zero** readers among the step files. My stated test would have
called those the movable ones. **That reading is wrong, and the opposite is true:** a block no step
file references cannot be moved *into* a step file, because no step would then read it.
`mandatory-steps` is the clearest case — it states which steps may never be skipped, so filing it
under `step-08.md` hides it from Steps 1-7. These six are global spine content that the orchestrator
reads directly from `SKILL.md`.

So the conclusion stands — zero words are movable — but for two distinct reasons, not one: six
blocks are cross-step (multiple readers, moving breaks the others), and six are global (no step
owns them). The original single-reader test was the wrong instrument; it would have identified a
movable block correctly, but there are none, and it says nothing about the global six.

**What I would now most expect to be wrong:** that the six zero-reader blocks are all genuinely
load-bearing rather than merely unreferenced. A block with no readers could also be *dead prose* —
and dead prose is trimmable without moving anything. **What would confirm or refute it:** for each
of the six, find the behaviour it governs and the run that would change if it were deleted.
`termination-rule` (37 words) and `ambiguity-circuit-breaker` (73) are the cheapest to check and the
likeliest to be genuinely load-bearing; `happy-path` (174) restates the spine that the step list
below it already gives, and is the one I would examine first for redundancy.
