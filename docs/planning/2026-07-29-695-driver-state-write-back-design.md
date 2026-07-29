# Design — #695: one owner for the driver-state terminal write

**Issue:** #695 (epic #626). **AC1 is the design decision:** pick ONE owner for the
write-back and state it. This document is that decision.

## The defect, restated precisely

`claude_docs/.driver-state/epic-684-watcher-fires.json` still read
`{"number": 687, "status": "queued"}` after #687 was merged (PR #691) and closed. A
fresh-session resume, obeying its own correct rule — *"derive position from durable
state, never in-context memory"* — announced #687 as the next ready child. A successor
would have re-done a merged child and opened a duplicate PR.

**It is structural.** Confirmed by reading, not inferred:

- `next_ready_issue` (`hooks/driver_lib.py:232`) selects the first issue whose
  `status == "queued"`. It reads the FILE only. Nothing corroborates it.
- WF2 Step 14 merges; Step 16 persists the run-record. Neither writes
  `claude_docs/.driver-state/<campaign>.json`.
- The epic driver writes that file when IT sequences children — so a run invoked as
  `/rawgentic:implement-feature <n>` directly bypasses the writer entirely. That is
  exactly what an epic auto-run does after a handoff, and what the documented resume
  prompt tells a successor to do.

So the gap reproduces on EVERY non-driver child, silently: the run looks successful,
GitHub auto-ticks the epic checkbox when the PR closes the issue, the run-record
persists, and only the driver-state lies.

## Two facts that constrain the design

1. **`driver_lib` is deliberately pure.** *"Pure, stdlib-only, no I/O and no side
   effects"* (`hooks/driver_lib.py:34-35`). And its scope boundary is explicit
   (`:28-32`): the state-transition layer *"is intentionally NOT here — it stays
   evidence-gated. Extend this module with that layer only when campaign experience
   shows hand-maintained state transitions are error-prone."*

   **#695 is precisely that evidence.** The condition the author set for opening this
   layer is met, so adding it is following that instruction rather than overriding it.

2. **There is already exactly ONE locked driver-state writer**, and its singularity is
   load-bearing: `launcher_lib._locked_state_update` (`hooks/launcher_lib.py:1483`)
   holds `plan_lib.file_lock` across the whole read → mutate → atomic-replace cycle,
   and locks a stable SIDECAR rather than the state file because `atomic_write_text`
   installs a new inode at the pathname (`:1492-1495`). A second locked writer that
   did not share that reasoning would interleave and silently erase a claim.

## Revision 2 — after the cross-model design review

The v1 decision named **WF2 Step 16** as the owner. An adversarial review
(`docs/reviews/2026-07-29-695-driver-state-write-back-design-md-2026-07-29.md`, 7 findings,
4 High) refuted parts of it. All seven were checked against the code and **all seven hold**.
What changed, and why:

| # | Finding | Verdict | Change |
|---|---|---|---|
| H1 | AC2 ships **dead** — no caller was required to pass the optional probe | **Confirmed.** The only production caller of `next_ready_issue` is `fresh_session_handoff` (`hooks/driver_lib.py:446`). An optional-and-unwired probe changes nothing. | The probe is threaded through `fresh_session_handoff` and **supplied by the launcher CLI**. Named call site, not an option. |
| H2 | Step 16 is **not atomic** with the merge: a crash after merge but before Step 16 leaves the child `queued` — the original defect | **Confirmed.** Step 14 merges; Step 16 can be minutes later or never. | **Owner is the locked COMMAND**, invoked at each authoritative terminal event: at Step 14 immediately after the merge is confirmed, and again at Step 16 as idempotent reconciliation. |
| H3 | Skipping a stale `queued` child does not make it satisfy **dependents** — an already-stale campaign can report "no ready child" while its prerequisite really merged | **Confirmed** — and v1's own test asserted the wrong behaviour. | A confirmed outcome becomes an **effective-status overlay** applied before BOTH dependency evaluation and candidate selection. |
| H4 | `status in VALID_STATUSES` validates vocabulary, not a legal **transition** — a merged child could be regressed | **Confirmed.** | Explicit transition table; terminal states are non-regressible. |
| M1 | Campaign-file **discovery** and multi-match cardinality undefined | **Confirmed** — a single-session run does not know its campaign, so "pass the path" only moved the problem. | Discovery rule + explicit cardinality policy (below). |
| M2 | Fail-open's stdout may be **suppressed**, so a real miss looks like a deliberate no-op | **Confirmed.** | The reason goes to **stderr as well**, and Step 16 captures it into the run-record. |
| M3 | The probe was **unverifiable** — no cited query, no tri-state, no timeout policy | **Confirmed, and it was a real blocker.** The installed `gh` exposes neither `stateReason` nor `closedByPullRequestsReferences` on `gh issue view`. | Verified live via `gh api graphql` (below). Tri-state contract defined. |

### Platform feasibility (#226) — verified, not assumed

`gh issue view --json` **cannot** answer this on the installed CLI: its field list offers
`state` and `closed` but not `stateReason` or `closedByPullRequestsReferences`. The design
would have been unbuildable as written.

`gh api graphql` does, verified live on this host against the actual regression:

```
$ gh api graphql -f query='{ repository(owner:"3D-Stories", name:"rawgentic") {
    issue(number: 687) { number state stateReason
      closedByPullRequestsReferences(first:5, includeClosedPrs:true) {
        nodes { number merged state } } } } }'
{"data":{"repository":{"issue":{"number":687,"state":"CLOSED","stateReason":"COMPLETED",
 "closedByPullRequestsReferences":{"nodes":[{"number":691,"merged":true,"state":"MERGED"}]}}}}}
```

That is #687 — the child epic #684's state file still calls `queued` — proven closed by a
**merged** PR #691. Failure mode: **fail-open to `unknown`** (see the tri-state below).

### The probe contract (tri-state, injected)

| Verdict | Derived from | Effective status | Selectable? | Satisfies dependents? |
|---|---|---|---|---|
| `confirmed_merged` | `state == CLOSED` and any `closedByPullRequestsReferences` node with `merged: true` | `merged` | no | **yes** |
| `confirmed_abandoned` | `state == CLOSED` with no merged closing PR (e.g. `stateReason: NOT_PLANNED`) | `abandoned` | no | no |
| `confirmed_open` | `state == OPEN` | none — file wins | yes | per file |
| `unknown` | query failed, timed out, rate-limited, or unparseable | none — file wins | yes | per file |

`unknown` deliberately does **not** veto. The probe is corroboration; once the write-back
keeps the file correct the file is primary, and turning a GitHub outage into a total
campaign stall is a worse failure than a visible duplicate PR — the stall is the silent
one. This is a decision, not an oversight, and it is tested.

## Decision (rev 2): the locked command owns the write, invoked at every terminal event

Why not Step 16 alone, and not the other candidates:

| Candidate owner | Why it loses |
|---|---|
| **The epic driver** (today's answer) | It structurally cannot see a non-driver run. That IS the bug — choosing it again just restates the defect. |
| **WF2 Step 16 alone** (v1's answer) | Refuted by H2. Step 16 does run at the end of every child, but it is **not atomic with the merge**: a crash, cancellation or hook failure after the remote merge succeeds and before Step 16 completes leaves the child `queued` — the exact defect, faithfully reimplemented. |
| **WF2 Step 14 alone** | Merge is where terminal status becomes *true*, but Step 14 is not a single terminus: a multi-PR issue merges more than once, and a run can legitimately end at `pr_open` or `deferred` without ever merging. |
| **Resume-time derivation only** (never store it) | This is AC2, not AC1. It leaves the file permanently untrustworthy, which contradicts the resume prompt's own rule; and it needs network I/O on every resume even when the file is fine. Good as corroboration, wrong as the owner. |
| **`work_summary.py`** | It owns the run-record store. Conflating two append-only/mutable stores in one tool means a driver-state failure can fail a telemetry write, or vice versa. |

**The owner is the locked `record-child-outcome` command**, invoked at **each authoritative
terminal event** rather than at one step:

- **Step 14, immediately after the merge is confirmed** — the write follows the event that
  makes it true, which is what closes H2's window.
- **Step 16, as idempotent reconciliation** — the backstop for a run whose Step 14 write
  never happened (interrupted step, or a non-merge terminal outcome).

Naming the *command* as owner rather than a step is what keeps "one owner" true while still
having two call sites: both go through one implementation, one lock, one transition table.
Idempotency is what makes the second call free.

### Shape

- `driver_lib.record_child_outcome(state, issue, status) -> dict | None` — **pure**: state
  in, new state out; `None` means "nothing to do", which is exactly
  `_locked_state_update`'s abort signal. This is the deferred transition layer, now
  evidence-gated open by #695 itself.
- **A transition table, not a vocabulary check (H4).** Terminal statuses (`merged`,
  `abandoned`) are **non-regressible**: once recorded, a request to move away from them is
  refused rather than silently corrupting the state resume treats as authoritative.
  Recording the status a child already has is a no-op (`None`), not an error.
- A `record-child-outcome` CLI subcommand composing `_locked_state_update` with that pure
  function, so there remains **exactly one** locked driver-state writer. It lives beside the
  existing writer for that reason, not because it is topically at home there.
- **Discovery and cardinality (M1).** `--driver-state <path>` stays available, but a
  single-session run does not know its campaign, so with the path omitted the command scans
  `claude_docs/.driver-state/*.json` and selects every file whose queue names the issue.
  **Zero matches → logged no-op** (the normal case: a run outside any campaign). **One →
  update it.** **More than one → update every validated match**, in sorted filename order:
  each is an authoritative campaign, and updating only the first leaves the others stale,
  which is the defect.
- **Fail-open, but never silent (M2).** Absent file, no match, or an unreadable directory →
  exit 0, write nothing, and print the reason to **stdout AND stderr**, so a wrapper that
  discards one still records it; Step 16 captures the reason into the run-record. A
  malformed status or a corrupt state file is a **caller/data error, not a no-op**: non-zero
  exit, file untouched.

## AC2 — corroborate before believing `queued`

`next_ready_issue` gains an injected probe, so `driver_lib` keeps its no-I/O promise:

```python
next_ready_issue(state, deps_satisfied_by="merged", issue_state_probe=None)
```

**It is threaded through to a real call site (H1), not left as an option.**
`fresh_session_handoff` (`hooks/driver_lib.py:446`) is the sole production caller, so it
takes the probe and passes it down, and the launcher CLI constructs the `gh api graphql`
probe verified above. An optional parameter nobody passes would have shipped AC2 dead —
exactly what the review caught.

**A confirmed verdict becomes an effective-status overlay (H3)**, applied before *both*
dependency evaluation and candidate selection, per the tri-state table. This matters for
precisely the stale campaign #695 describes: with selection-only filtering, a child whose
prerequisite really merged but still reads `queued` would report "no ready child" forever.
Under the overlay the prerequisite counts as `merged`, so its dependent advances.

The probe is injected rather than called directly because the alternative is `driver_lib`
doing network I/O, which would break the purity its docstring promises and make the module
unimportable from the `python3 -c` one-liner the docs use.

**Belt-and-braces, deliberately.** AC1 stops the file going stale; AC2 stops a stale file
being believed. Either alone leaves a live failure: AC1 cannot fix the files already on
disk (epic #684's is stale right now), and AC2 alone leaves the file wrong forever.

## Risk

- The pure function is additive; no existing caller changes.
- `next_ready_issue`'s new parameter defaults to `None`, so the #163 contract and its
  tests are byte-for-byte unaffected.
- The genuine risk is **fail-open hiding a real miss**: a campaign whose file exists but
  whose queue is malformed would silently write nothing. Mitigated by saying so on
  stdout rather than exiting quietly, so a run's own log carries the reason.
- Not addressed here, and stated rather than implied: **the stale files already on disk**
  are not repaired by this change. AC2 is what stops them causing harm.

## Out of scope

- Repairing existing stale driver-state files (AC2 neutralises them; a migration is not
  asked for).
- Any change to the status machine's vocabulary or to `topo_sort_issues`.
- The epic-checkbox mirror — GitHub already ticks those when a PR closes an issue.
