# Why #762 took so long — a telemetry retrospective

**Commissioned by the owner 2026-08-01 (decision log D32), written at the close of child 7/21 of
epic #756.** Every number below comes from a recorded artifact named at the point of use —
`docs/measurements/run_records.jsonl`, `hooks/step_state.py timing`, the executor observation
receipts under `.rawgentic/runs/wf2-762-*/`, the session transcripts under
`~/.claude/projects/`, and the append-only decision log. Nothing here is estimated from memory.
Where a figure could not be recovered, it says so.

---

## The short answer

#762 was the most expensive child of this epic by a wide margin, and the reason is not one big
stall — it is **four structural taxes that each cost 20–60 minutes and stacked**.

| | #762 | next-worst child | ratio |
|---|---|---|---|
| Cost | **$254.21** | $116.99 (#733) | **2.2×** |
| Input tokens | **467.5M** | 280.8M (#733) | **1.7×** |
| Output tokens | **2.53M** | 1.24M (#733) | **2.0×** |
| Sessions (legs) | **5** | 3 (#765) | — |
| Active (non-idle) work | **3.08 h** | 2.68 h (#765) | 1.15× |
| Files changed | **38** | 27 (#765) | 1.4× |

The wall-clock number is the one that misleads. By raw wall clock #762 (6.74 h) reads *shorter*
than #735 (12.13 h) and #767 (8.51 h) — but those two spent 83% and 69% of their clock **idle**,
waiting on the owner or a quota pause. Strip idle out and #762 has the **longest active working
time of any child in the epic**, and it cost more than twice the next child.

So the honest framing is: #762 did not sit around waiting. It genuinely worked longer, and burned
more tokens per unit of work, than anything else in this epic.

---

## Where the clock actually went

`hooks/step_state.py timing --project rawgentic --issue 762` — status `complete`, total 24,276 s
(6.74 h), idle threshold 1,800 s:

| phase | seconds | share of total | share of *active* |
|---|---|---|---|
| idle | 13,186 | 54.3% | — |
| design | 3,817 | 15.7% | 34.4% |
| implement | 2,340 | 9.6% | 21.1% |
| review | 2,131 | 8.8% | 19.2% |
| plan | 1,897 | 7.8% | 17.1% |
| pr_ci | 893 | 3.7% | 8.1% |
| wrap | 12 | 0.05% | 0.1% |
| **active total** | **11,090** | **45.7%** | **100%** |

The 13,186 s of idle is three interval gaps that hit the 1,800 s cap — at Step 1, Step 3, and
Step 6 — booked to `idle` rather than to the step they interrupted (owner-away and quota pauses).
That accounting is correct and is not the story.

**Design is the largest active phase at 34%.** That is where the money went.

Transcript spans corroborate the total independently: first event 2026-07-31T19:04:37Z (leg
`ff40b6d5`), last 2026-08-01T02:10:30Z (leg `65a021d4`) — 7.10 h end to end, with the per-leg
spans summing to 25,885 s (legs overlap ~1 min each at handoff).

| leg | span | hours | transcript records |
|---|---|---|---|
| `ff40b6d5` | 19:04:37 → 21:14:11 | 2.16 | 1,058 |
| `89f42f76` | 21:13:14 → 00:01:19 | 2.80 | 853 |
| `3fecd708` | 00:00:27 → 00:44:17 | 0.73 | 868 |
| `f0517473` | 00:43:04 → 01:48:19 | 1.09 | 937 |
| `65a021d4` | 01:45:50 → 02:10:30 | 0.41 | 470 |

---

## The four taxes, measured

### Tax 1 — Five sessions, four mid-child handoffs (the largest single cost)

#762 ran across **five sessions with four handoffs, every one of them mid-child**. No other child
in this epic needed more than three legs.

The advisory context-meter tier fired at 45%, 36%, and 39% — always *inside* a child, never at a
clean child boundary. Each handoff then paid a full state-reconstruction cost: the run contract,
the handoff file, the decision log (D1→D34 by the end), and the relevant `session_notes.md`
section, re-read from scratch before any work could resume.

The measurable signature is the input-token count: **467.5M input tokens across 5 legs**, against
#733's 280.8M across 2 legs. Per-leg input is roughly flat (92M–123M) — which is exactly what a
fixed re-read tax looks like, paid once per leg regardless of how much new work that leg did. Leg
`3fecd708` did 0.73 h of work and still spent 92.4M input tokens.

Cost of this tax: reconstruction is not separately clocked, but the token arithmetic puts it at
roughly **90M input tokens ≈ $40 of the $254** in avoidable re-reading, and it is the direct cause
of #762's 2.2× cost ratio.

### Tax 2 — The Step-4 design gate ran three passes and still closed budget-exhausted

Step 4 consumed **both** design loop-backs (2/2, global 2/3), produced **24 unique findings**, and
then hit the wall with pass-3 findings still classified `fold=design` — so it closed
budget-exhausted and self-resolved on precedent.

Reviewer time alone, from the receipts:

| dispatch | model | seconds |
|---|---|---|
| `762-s4-qbar` | gpt-5.6-sol | 608.0 |
| `762-s4-verify2` | gpt-5.6-sol | 455.9 |
| `762-s4-verify3` | gpt-5.6-sol | 406.8 |
| **total** | | **1,470.7** (24.5 min) |

Plus the design rewrites between passes — rev 1 → rev 5 — which live inside the 3,817 s design
phase.

**This is not a #762 problem. It is the epic's dominant pattern.** Six consecutive children closed
Step 4 the same way: #735 (D4), #733 (D11), #758 (D18), #767 (D22), #765 (D25), #762 (D30). Every
single time the escalation asked the owner the same question, and every single time — now seven,
counting today's ratification — the answer was *apply the final fixes, proceed, no further review
pass*.

`MAX_DESIGN_LOOPBACK_ITERATIONS = 2` is therefore not describing these designs. Three passes is
what they actually take, and the third pass's verdict is a foregone conclusion.

### Tax 3 — The analysis seat wasted 78% of its time failing

Step 2's analysis fan-out is the worst-performing seat in the run. From the observation receipts
under `.rawgentic/runs/wf2-762-ff40b6d5/analysis/`:

| model | attempts | ok | failed | failed seconds |
|---|---|---|---|---|
| claude-opus-5 | 4 | 1 | 3 | 689.3 |
| claude-fable-5 | 3 | 0 | 3 | 228.4 |
| claude-sonnet-5 | 3 | 1 | 2 | 311.7 |
| **total** | **10** | **2** | **8** | **1,229.4** (20.5 min) |

Useful analysis-seat time: **347 s**. Wasted: **1,229 s** — the seat spent **3.5× longer failing
than succeeding**, and burned every entry of its fallback chain. Every failure was
`parse_status: nonzero_exit`, and the same correlation ids (`762-s2-deps`, `762-s2-surfaces`)
failed across all three models — which points at the brief or the harness, not at any one model.

By contrast the `build` seat went 6/6 (1,756 s, zero failures) and `review` went 7/7 (3,149 s,
zero failures) on the same infrastructure in the same run.

### Tax 4 — Thirteen stale pin-guards, found twice, the second time after the first lesson

Two implementation tasks changed a constant or a prose contract without their file allowlist
carrying the guards that pin it:

- **T3** (routing-table retune to the owner's 8-seat matrix) omitted the driver-bench fixtures
  (`f01`, `f09`, `f10`), `stubbed-baseline.json`, and `test_driver_bench.py`'s own wrong-value pin.
- **T5** (WF3 build adoption widening `fix-bug` SKILL.md's DISPATCH regex to
  `role=(review|implementation)`) omitted `tests/test_wf2_clarity.py`'s `TestDispatchRegexIdentity`.

**4 stale pins surfaced at Step 8a. Nine more surfaced at Step 9** — as a red full suite
(6,505 passed / 9 failed) that had to be root-caused before the gate could pass. Each full suite
run is ~250 s, so this cost at minimum **two extra full-suite runs (~500 s)** plus the diagnosis.

The second occurrence is the expensive part: T3's lesson was already recorded at Step 8a
("a retune task's allowlist must carry its pin-guard files") and T5 repeated the same class of
miss anyway, because the lesson was a note rather than a mechanism.

---

## Three smaller costs, named for completeness

**The two-run epoch chain (~one reconcile's worth of work).** T3's routing-table edit changed the
config digest, which correctly ended run A's dispatch epoch (`run_digest_conflict`, #474 working as
designed). Run A had to be closed honestly with a non-reconciling final record, run B declared, and
the reconcile performed twice. Lesson recorded at the time: **sequence config-digest changes last**.

**The high-task/bake-off structural gap (16 min of owner round-trip, D31).** Task 2 was
`riskLevel: high`; a task-scoped gate decides bake-off on `risk_high` alone; `check_pre` refuses
any bake-off outcome on a single dispatch; and the build bake-off has no workflow caller. So a HIGH
task **structurally cannot dispatch through the build seat today**. No bypass exists (relabeling
raises `GateTamperError`) — fail-closed working as designed, but it cost an `/ask-owner` round trip
and forced Task 2 inline. Tracked in #779.

**The concurrent-merge race (~10 min, at the very last step).** PR #780 (v3.111.1, docs-only)
merged to main at 01:49:14Z — **9 minutes after #762's CI went green** and 6 minutes after the
merge gate had read `mergeStateStatus: CLEAN`. The merge then failed on conflicts in all four
version surfaces plus the README changelog. Resolution: merge commit `77a7a4f`, full suite re-run
(251 s), CI re-run (~5 min).

---

## What to change, ranked by measured payoff

### 1. Codify the budget-exhausted Step-4 close. Stop asking.
**Evidence:** 6 of 7 children, 7 identical owner answers.
**Cost today:** a blocking escalation per child, plus a third reviewer pass whose verdict is
already known (24.5 min of reviewer time on #762 alone).
**Change:** either raise `MAX_DESIGN_LOOPBACK_ITERATIONS` to 3 so the budget matches reality, or
make the budget-exhausted close an automatic *apply-and-proceed with the decision logged* rather
than an owner stop. This is the single highest-value change available and it is prose plus a
constant.

### 2. Root-cause the analysis seat's `nonzero_exit`.
**Evidence:** 8/10 attempts failed, all three chain entries exhausted, 1,229 s wasted, while
`build` and `review` went 13/13 on the same infrastructure.
**Change:** read the stderr on those eight receipts before the next analysis fan-out. A seat that
fails 78% of the time is a bug, not a routing preference — and because it exhausts the whole chain,
it also burns the fallback budget that exists for real availability failures.

### 3. Derive pin-guard surfaces into the task allowlist mechanically.
**Evidence:** 13 stale pins in one child, across two gates, with the second batch landing *after*
the lesson from the first was written down.
**Change:** when a plan task changes a constant or a pinned sentence, compute the guard files that
reference it and add them to that task's allowlist at plan time. A note in a session file did not
prevent the recurrence; a mechanism would.

### 4. Make a child fit one session, or make the handoff cheap.
**Evidence:** 5 legs, 4 mid-child handoffs, ~90M input tokens (~$40) of re-read tax, flat per-leg
input regardless of work done.
**Change:** two independent options. (a) The advisory tier fires at 36–45%, always mid-child —
if a child of this size cannot fit one session, the tier is firing too late to reach a boundary.
(b) The re-read is dominated by the decision log, which is now 35 entries and grows every child; a
rolling summary with the full log kept as an appendix would cut the per-leg fixed cost directly.

### 5. Sequence config-digest changes last within a plan.
Already recorded during the run; repeating it here so it survives the session.

---

## One process defect this retrospective itself found

The completion gate's item 13 keys on the exact string `## Deferred verification`
(`hooks/plan_lib.py:342`). PR #781's body carried that section as an **H1** — matching the body's
other headings — so the mechanical guard failed at the end of the run, *after the PR had already
merged*. The shipped template is correct and even says "do not reword it"
(`references/steps.md:1541`); this was an authoring slip.

Nothing had actually vanished — the deferral was legible in the body throughout — but the guard is
the guard, so the heading was corrected on the merged PR body rather than the check being waived.
Two things worth carrying forward:

- **Call `assert_pr_body_has_deferred_section` at Step-12 authoring time**, not only at the
  end-of-run gate. Here it fired when the fix was maximally awkward.
- **`gh pr edit` is unusable on this repo.** It aborts on the Projects-classic GraphQL deprecation
  (`repository.pullRequest.projectCards`) and the edit does *not* land. The working path is
  `gh api -X PATCH repos/<owner>/<repo>/pulls/<n> --input <json>`.

---

## Cross-child data, for the record

Recorded step-timing totals and usage, all seven merged children of epic #756:

| issue | lane | wall (h) | idle % | active (h) | cost | in (M) | out (k) | files | tests+ |
|---|---|---|---|---|---|---|---|---|---|
| #735 | full | 12.13 | 83% | 2.02 | $76.80 | 184.3 | 776 | 15 | 4 |
| #733 | full | 3.84 | 40% | 2.32 | $116.99 | 280.8 | 1,243 | 23 | 79 |
| #732 | small-standard | 1.03 | 0% | 1.03 | $54.16 | 117.8 | 512 | 12 | 19 |
| #758 | small-standard | 1.87 | 0% | 1.87 | $63.65 | 147.8 | 609 | 11 | 56 |
| #767 | small-standard | 8.51 | 69% | 2.65 | $113.14 | 236.8 | 1,040 | 19 | 66 |
| #765 | full | 2.80 | 4% | 2.68 | $101.44 | 201.2 | 1,056 | 27 | 33 |
| **#762** | **full** | **6.74** | **54%** | **3.08** | **$254.21** | **467.5** | **2,533** | **38** | **77** |

Related: the owner-commissioned before/after token-usage doc for the executor switch is live at
<https://token-usage-deploy.vercel.app> (verified 200, cache-busted, title *"Token usage: before vs
after the executor switch (2026-07-31)"*).

**Not checked, and named as such:** per-handoff reconstruction time is inferred from token counts,
not separately clocked — no timer brackets the "read the contract and the log" phase, so the ~$40
figure in Tax 1 is arithmetic on flat per-leg input, not a measured interval. The eight
`nonzero_exit` analysis receipts were counted and timed but their stderr was **not** read, so
lever 2 names a symptom, not a root cause.
