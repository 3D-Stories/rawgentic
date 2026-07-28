# #665 — interactive mid-child session handoff (design)

Epic #667, child 4. Written 2026-07-28. **Revision 4.** The loop-back budget is fully spent
(design 2/2, spec_tighten 1/2, global 3/3), so no further design cycle is available — see D-18 in
`claude_docs/session_notes/epic-667-autorun-log.md` for how this revision closed and what is
deliberately left open.

Revision history, because it is the evidence that the gates worked — and, twice, that they caught
me claiming more than I had:

- **R1** first draft.
- **R2** after a Step-4 volume loop-back: 8 High findings (a same-model quality-bar self-review plus a cross-model adversarial review). Report `docs/reviews/2026-07-28-665-mid-child-handoff-design-md-unknown-date.md`.
- **R3** after a second Step-4 loop-back (fold → `design`): 4 High + 4 Medium, all confirmed against the code before being acted on. Report `docs/reviews/2026-07-28-665-mid-child-handoff-design-md-2026-07-28-pass2.md`.
- **R4** after a third pass (1 Critical + 4 High + 2 Medium) reached the spec-tightening path, plus the incremental verifier that pass required. Reports `...-2026-07-28-pass3.md` and the verifier observation under `.rawgentic/runs/wf2-665-ceaabfe6/review/`. The verifier resolved four findings, left two partially resolved, and raised **two new High findings — one of which falsified an R4 amendment I had just written** (see §5's `position_rebuilt` note). Both new findings are applied here.

**What is NOT closed, stated up front rather than buried:** the crash window between a confirmed
`/goal clear` and a successful `pane close` leaves the predecessor alive and unguarded, and this
design contains it and declares it rather than fixing it (§6, §9, §10). `position_rebuilt` is an
attestation by the successor, not independent proof (§5).

Contract: the issue body as rewritten 2026-07-27, plus owner decision D-16 (epic #667 autorun log):
this is **Service A, the context-driven handover**. Its trigger is context exhaustion, never cron.
The 5-hour cron window is Service B and resumes the already-active session, so it needs none of
this machinery.

## 1. The gap, stated precisely

`driver_lib` (#569) already crosses a **child boundary**: a child reaches a terminal outcome, the
session ends, a launcher spawns a fresh `claude`, and the successor rebuilds position from
`.driver-state`. `launcher_lib` (#611) already supplies the **pane engine**: split from an explicit
anchor, `agent start` carrying no goal, `agent wait --until idle`, `pane get` for the successor's
session id, launch-bound artifact baselines, the goal paste route, and pane-ownership discipline.

Neither covers *"I am mid-child, out of context, hand me over and keep going"*. Done by hand on
2026-07-27 it half-worked: the goal never armed (an AUTO-MERGE run with no completion guard), the
predecessor would not die (its unmet goal blocked Stop nine times until the block cap ended the
turn, leaving the session alive and idle), and the task list did not transfer. Root cause was not a
missing call — it was **no verification between steps**, because the handoff bypassed `driver_lib`
entirely.

Three things are therefore missing, and only three: durable **mid-child position**, the
**claim/ack wiring** to `driver_lib`, and **successor-driven teardown**. Everything else exists.

## 2. Approaches

**Approach A — extend the two existing modules. CHOSEN.** `driver_lib` gains the mid-child
disposition plus a position record persisted through the *same* `open_handoff`. `launcher_lib` gains
a parameterised verification ladder, the new checks, and one successor-side entry point that claims,
acks, and only then retires the predecessor. No new module, no second sequence.

**Approach B — a new `handoff_lib.py` that orchestrates both. REJECTED.** AC1 forbids a parallel
handoff path in as many words, and AC7 asks for a test that fails if one appears. A third module
holding a second ordered sequence is exactly that defect, and the repo rule "one helper, one home"
says the same thing independently.

**Approach C — teardown stays predecessor-driven, as #611 has it. REJECTED.** `perform_handoff`
today closes the anchor pane itself once its three checks pass. AC5 requires the successor to do
it, and the reason is asymmetric risk: the predecessor cannot observe whether the successor really
took over, and a predecessor that retires itself on its own optimistic report is how failure 2 above
becomes unrecoverable.

## 3. Durable mid-child position (AC2, AC3)

`.driver-state` gains an OPTIONAL `position` object **inside the existing `handoff_pending` object
that `open_handoff` already writes** — not a new top-level key, and not a second file:

```json
"handoff_pending": {
  "generation": 4,
  "next_issue": 665,
  "written_ts": 1769580000,
  "kind": "mid_child",
  "cancelled": false,
  "teardown_phase": null,
  "position": {
    "issue": 665,
    "step": "8",
    "branch": "feat/665-mid-child-handoff",
    "test_baseline": "5362 passed, 21 skipped, 0 failed, exit 0",
    "predecessor_pane": "w1:p1",
    "predecessor_session": "ceaabfe6-80fc-476b-83c2-a4e27e425d1b",
    "goal_condition": "<the predecessor's last unmet goal condition, verbatim>",
    "project": "rawgentic",
    "project_path": "./projects/rawgentic",
    "repo_root": "/home/rocky00717/rawgentic/projects/rawgentic"
  },
  "successor": {
    "pane": "w1:p9",
    "session": "<the id herdr returned from pane get, written under the lock>"
  }
}
```

**R4 — the predecessor records the SUCCESSOR's identity; the successor does not re-derive it.** An
earlier revision asked the successor to re-verify `spawned` via `herdr pane get <new>`, which it
cannot do: the durable record held only the predecessor's pane, and a session has no way to discover
its own pane id (herdr 0.7.5 exposes no pane environment — the same fact that makes
`_report_possible_orphan` report-only). So the predecessor, which DID observe both values, writes
`successor: {pane, session}` under the state lock immediately after its own `pane get`, bound to this
generation. `retire_predecessor` then asserts that its OWN `$CLAUDE_CODE_SESSION_ID` equals
`successor.session`. That is stronger than the check it replaces: it is an identity binding, so a
session that was never the intended successor cannot authorise a teardown at all.

`open_handoff` copies `disposition["position"]` and `disposition["kind"]` through when present.
When absent the written shape is byte-identical to today's — `tests/hooks/test_driver_lib.py:611`
asserts that shape by exact equality and keeps passing, which is the compatibility proof.
`docs/driver-state/queue.schema.json` carries `additionalProperties: true` at root, issue, and
nested level (lines 8, 22, 54 — read, not assumed), so the extended keys validate with no schema
change.

**R3 — `goal_condition` is recorded here, not re-derived later.** The partial-success recovery in §6
re-arms the predecessor's guard. Reading that condition from the SUCCESSOR's transcript would arm the
predecessor with the successor's guard, and if the 4000-char cap applied, with a silently TRUNCATED
one. The predecessor records its own last unmet condition verbatim at handoff time — it is the only
party that can — and recovery uses that value.

**R3 — `project_path` joins `project`.** A project *label* is not proof of a repository: nothing in
this configuration establishes that labels are globally unique. Both fields are matched. The honest
bound: this is an exact-string comparison against the value the registry's own producer writes
(workspace-relative, e.g. `./projects/rawgentic`) — it is NOT a filesystem canonicalisation and
claims nothing about symlinks.

**The `kind` discriminator is a CLOSED contract (R3).** `handoff_pending` previously had one meaning:
*start the next child*. It now has two, and #611's launcher entry point (`launcher_lib._cmd_handoff`)
reads the same file. The rule is therefore an allowlist, not an equality test:

| `kind` | `_cmd_handoff` behaviour |
|---|---|
| absent | legacy child-boundary handoff — proceed exactly as #611 does today |
| `"mid_child"` | REFUSE, naming the reason (a mid-child resume is already in flight; a second successor would compete for one generation) |
| any other value, including a misspelling, a different case, or a non-string | REFUSE as an unrecognised handoff kind |

Equality-only matching would let `kind: "MID_CHILD"` or `kind: 42` fall through to the legacy branch
and launch a second successor from a malformed record. Fail closed instead.

**All `.driver-state` read-modify-write goes through ONE locked helper (R3).** An advisory lock only
serialises writers that participate, so a lock held by `retire_predecessor` alone would not stop
another writer from clobbering the claim, the ack, the generation, or the position. Both writers this
change introduces — the predecessor's `mid-child-handoff` and the successor's `retire-predecessor` —
go through a single helper that holds `plan_lib.file_lock(<state path>)` across read → validate →
`atomic_write_text`.

**R4 — the boundary, with the reason it is acceptable here rather than just declared.** The
pre-existing prose-driven writers (the epic-run skill's status updates) do NOT take the lock, and a
review correctly notes that an unlocked writer could erase a claim. Why that is tolerable for
Service A specifically: the epic driver and the predecessor are the SAME session, the driver's status
writes happen at CHILD boundaries while a mid-child handoff by definition happens between them, and
the predecessor and successor are serialised by the handoff itself — the successor does not exist
until the predecessor has finished writing. So within this service there is one writer at a time by
construction, and the generation/claim machinery fences the cross-session case semantically. The
residual risk is a SECOND driver session on the same campaign, which is already outside #569's
model. Migrating the skill's prose writers onto the locked helper is filed as a follow-up and named
in §10 — not implied to be done here. The alternative the review offered (move the handoff fields to
a separate file no legacy writer touches) is rejected: it would fork durable state and break AC2,
which requires the successor to rebuild from `.driver-state`.

`validate_mid_child_position` fails closed on a missing or empty field, a non-int `issue`, or a
non-string elsewhere. It deliberately does NOT validate the pane id's grammar: pane-id shape is
`launcher_lib.validate_pane_id`'s job and is checked there before any herdr call, and importing
`launcher_lib` from `driver_lib` would invert the existing lazy-import direction.

**Why a task list is still not the transfer unit.** The harness task tools are session-scoped, and
the live predecessor on 2026-07-27 held 30 task subjects spanning three unrelated projects. The
successor rebuilds from `.driver-state` plus the position record and re-derives its own list.

**R4 — an aborted handoff CANCELS its record; it is not "harmlessly stale".** The record is written
before the pane is split (the successor cannot claim what was never written), so an aborted handoff
leaves one behind. An earlier revision called that harmless because `handoff_claim` is
generation-pinned — which was wrong: until a later `open_handoff` bumps the counter, the abandoned
record IS the current generation and therefore claimable, so a delayed or stray successor could take
a lease on it. Two distinct states, named:

- **cancelled** — the aborting predecessor sets `handoff_pending.cancelled: true` under the lock on every failure path, **but only while the claim is not yet `started`**. Both `retire_predecessor` and `_cmd_handoff` refuse a cancelled record. **R4 fence (verifier finding B):** checking `cancelled` once at entry is not enough, because the predecessor can set it AFTER the successor passed that check and the successor would still go on to clear a guard and close a pane. So the rule is monotonic and the winner is defined: a cancel that arrives before `handoff_ack_started` wins and the teardown refuses; once the claim is `started` a cancel is refused instead, because takeover has already happened. `retire_predecessor` re-reads and re-validates BOTH `cancelled` and the claim under the lock immediately before the destructive step, not only at entry.
- **superseded** — its generation is lower than the persisted current generation. Unclaimable by `handoff_claim` itself, and that word is now reserved for this case only.

`driver_lib.handoff_claim` is deliberately NOT modified — it is #569's tested primitive and changing
its semantics would reach beyond this issue. The cancelled-record refusal lives in this change's own
callers, which is where the new state was introduced.

## 4. The mid-child disposition (AC1)

`mid_child_handoff(state, *, position)` mirrors `fresh_session_handoff`'s disposition contract so
that `open_handoff` consumes it unchanged — that shared consumption IS the reuse AC1 demands:

| outcome | when |
|---|---|
| `no_active_child` | no issue is `in_progress` — there is nothing mid-child to hand over |
| `invalid_position` | `validate_mid_child_position` failed; carries `errors` |
| `position_mismatch` | `position["issue"]` is not the `in_progress` issue |
| `ready` | carries `next_issue` (the in-progress child), `generation` (current + 1), `campaign`, `kind`, `position`, `resume_prompt` |

One deliberate difference from `fresh_session_handoff`: it does **not** gate on
`mode == FRESH_SESSION_MODE`. A context-driven handover is cron-free by D-16 and must work for a
campaign documented to loop in-session — gating on that mode would refuse exactly the runs this
serves. That difference is why this is a sibling function rather than a flag on the existing one:
`fresh_session_handoff`'s `single_session` verdict is load-bearing for #569 and must not change
meaning.

The resume prompt is built by `_build_mid_child_resume_prompt`, next to #569's
`_build_resume_prompt` and for the same reason: two copies of that wording would drift. It names the
child, the WF2 step, the branch, and the recorded baseline; it instructs the successor to re-bind the
project, check out the branch, re-derive position from durable state, claim the handoff, ack it after
the rebuild, and retire the predecessor LAST. It embeds a **handoff marker token** so `prompt_landed`
has an artifact to match on.

## 5. The verification ladder (AC4, AC6)

Six checks, in **causal** order. Each reads an on-disk artifact or live git state; none reads pane
text.

| # | check | artifact | verified by |
|---|---|---|---|
| 1 | `spawned` | predecessor side: `herdr pane get <new>` yields a non-empty `agent_session.value`, which is then WRITTEN to `handoff_pending.successor`. Successor side: its own `$CLAUDE_CODE_SESSION_ID` equals that recorded `successor.session` | both |
| 2 | `goal_armed` | successor transcript below the pre-launch offset: a `goal_status` attachment with `met:false` whose condition equals the one actually armed | both |
| 3 | `prompt_landed` | successor transcript below the offset: the handoff marker token, matched as a plain SUBSTRING | both |
| 4 | `project_switched` | `claude_docs/session_registry.jsonl` below the offset: ONE line carrying the NEW session id AND `project` AND `project_path` equal to the recorded values | both |
| 5 | `position_rebuilt` | a REBUILD RECEIPT the successor writes into `.driver-state` under the lock, carrying `{generation, claimant, branch_observed, repo_root_observed, step, ts}`, validated against `handoff_pending.position` and against the claim's generation + claimant. The two `git -C <repo_root> rev-parse` readings (`--show-toplevel`, `--abbrev-ref HEAD`) are recorded IN the receipt as corroboration | successor |
| 6 | `state_claimed` | `.driver-state` `handoff_claim` with the matching generation and claimant and `started: true` | successor |

`evaluate_verifications` and `teardown_allowed` take an optional `steps` argument defaulting to
#611's three-step launch tuple, so the ladder logic stays single-sourced and #611's pinned
`["spawned","goal_armed","project_switched"]` contract is untouched. Fail-closed is unchanged: an
unreported step counts as failed.

**R3 — `position_rebuilt` exists because the other five checks do not prove the successor is ready
to work.** They prove a session was spawned, guarded, prompted, bound to the right project, and that
it claimed the state. A successor that called `retire-predecessor` immediately — before checking out
the branch or re-deriving anything — would pass all five and destroy the only session still holding
the live working context. That is the worst outcome this design can produce, and it was reachable.

**R4 — the echo was theatre, and the git-only replacement was VACUOUS. What it is now, and the
honest limit of it.** R3 had the successor "report back" its step and baseline for comparison against
the record it copied them from: that proves nothing, and it was removed. R4 first replaced it with
live git readings — and the incremental verifier then falsified THAT too, correctly: the repository
at `repo_root` is the SHARED checkout, and it is *already* on `position.branch` because the
predecessor was working there. A successor that did nothing at all would pass. The claim that the
state "can only be made true by actually doing the checkout" was simply false.

So `position_rebuilt` is now a **rebuild receipt**: the successor writes
`{generation, claimant, branch_observed, repo_root_observed, step, ts}` into `.driver-state` under
the lock, and teardown validates it against `handoff_pending.position` AND against the claim's own
generation and claimant. The two `git -C <repo_root> rev-parse` readings are recorded inside the
receipt as corroboration rather than treated as proof.

**Stated plainly: this is an ATTESTATION by the successor, not independent proof that it rebuilt.**
No artifact this platform exposes would give independent proof — that was the mistake in both
earlier attempts, each of which dressed a weak signal as a strong one. What the receipt does buy is
real and bounded: it cannot be satisfied by inaction (something must be written), it is bound to
this generation and this claimant (a stale or foreign receipt fails), and it is written under the
same lock as the claim, so it cannot race the ack. The remaining protection against premature
retirement is structural rather than evidential: teardown is successor-driven, it is a separate
explicit command the successor runs only after its resume prompt's rebuild instructions, and only
the recorded successor session may run it at all.

**Step 4 proves the PROJECT, not just registration.** `registry_has_session` matched only the session
id, so a successor bound to the WRONG project would pass, claim, and retire a valid predecessor
before continuing in the wrong repository. The registry line already carries both fields (confirmed
live: this session's own bind line is `{"session_id": "...", "project": "rawgentic",
"project_path": "./projects/rawgentic", ...}`), and both are now required on the SAME line.

**Step 3 is a substring match by design, and here is the probe.** A live probe of this session's own
transcript (2026-07-28) searched for a phrase from a prompt pasted into the pane: present **3 times,
verbatim**, but carried in `{"type":"queue-operation","operation":"enqueue","content":"…"}` and
`{"type":"attachment","attachment":{"type":"queued_command","prompt":"…"}}` rows — **not** a
`type:"user"` row with `message.content`. A structured row match keyed on `type:"user"` would have
failed every handoff, which is precisely the class of defect #611 shipped once with its invented
`goal_status` shape. So `prompt_landed` matches the marker as a plain substring and asserts nothing
about row shape.

**Precise claim for step 2, and the CURRENT-state re-check it requires (R4).**
`transcript_has_unmet_goal` proves the guard **was armed and unmet at some point after the baseline**
— not that it is unmet at read time. R3 called that sufficient. It is not: a later clear or
replacement row can exist while step 2 still passes, so the predecessor could be retired while the
continuing session has no live guard — the artifact's original failure mode, reintroduced through the
back door. So immediately before the destructive step (after claim, ack and target-identity
validation) `retire_predecessor` re-checks the **LAST `goal_status` row in the transcript, whatever its
condition**, and requires it to be an unmet row for the ARMED condition. Scoping the "latest" to
rows matching the armed condition would have missed a *replacement* guard: a session that re-armed
with a different condition has retired the old one, so the armed condition is stale and teardown
must refuse. Both a later `met:true` row and a later row for any other condition therefore fail this
check.

**Implementation note added 2026-07-28, resolving a contradiction inside this revision rather than
re-opening the design.** §5 above says the latest row decides "whatever its condition", while §6
step 8 says "the LATEST `goal_status` row **for the armed condition**". Those are different rules,
and Task 3's written tests pin the second one (`test_a_different_conditions_rows_are_ignored`
asserts a foreign row is IGNORED). Both requirements are satisfied by splitting them, because they
answer different questions:

- `goal_currently_unmet(text, condition)` implements §6's condition-scoped reading — "is THIS
  condition still owed?" — which is what its tests pin.
- `latest_goal_status_condition(text)` implements §5's replacement rule, and
  `retire_predecessor` refuses when the newest row belongs to any other condition.

So the strict behaviour §5 asks for is enforced at the destructive gate, where it protects
something, rather than folded into a predicate where it would conflate "still owed" with "not
replaced". No behaviour §5 required was dropped; the loop-back budget was not touched. The ladder check answers "was a guard ever
handed over"; this pre-clear re-check answers "is it still in force right now", and only the second
one authorises destruction.

**AC4's stated order is amended, not deviated from.** AC4 lists `project switched` before
`prompt landed` and puts `goal armed` fourth. That order cannot hold: the registry row is a
*consequence* of the resume prompt, so checking it earlier can only pass on stale evidence, and
arming the goal after handing over work reproduces failure 1 — the defect this issue exists to fix.
The amendment is on the record as a comment on issue #665 before implementation, including the added
sixth check. All of AC4's artifacts are still verified, each before the next runs.

Checks 1 to 4 are the predecessor's; 5 and 6 are the successor's. The successor re-derives 1 to 4
from its OWN artifacts instead of trusting a report handed to it. It needs no pre-launch offsets:
launch mode `fresh` carries no `--resume`, so its transcript contains only its own rows.

## 6. Successor-driven teardown (AC5, AC6)

`retire_predecessor` runs in the successor:

1. **Take the lock, then read.** The single locked helper from §3 holds `plan_lib.file_lock(<state path>)` — the stable sidecar `<state>.lock`, NOT the state file, because `flock` follows the opened inode while `atomic_write_text` installs a new inode at the pathname. Every read → validate → replace in this change happens inside that lock.
2. **Derive, then sanity-check.** `generation` comes from `handoff_pending.generation` (one source of truth, not a CLI argument). Refuse when `kind != "mid_child"`, when `cancelled` is true, when there is no `position`, when `position.predecessor_session` equals the caller's own session id (a session is never its own predecessor), or when the caller's own session id does NOT equal the recorded `successor.session` (R4 — the identity binding that replaced the unimplementable successor-side `spawned` re-derivation).
3. **Claim, idempotently.** `handoff_claim(state, generation, claimant=<successor session id>, now_ts=)`. A refusal whose cause is that the claim is ALREADY OURS for this generation — same claimant, `started` or not — is an accepted continuation, not a failure. Live probe (2026-07-28): `handoff_claim` returns `False` for a same-claimant re-claim inside the 1800 s lease AND after `started`, so without this branch one failed teardown would block its own retry for the whole lease. Any other refusal returns `claim_refused` and touches nothing.
4. **Verify** `spawned` (the recorded identity binding), `goal_armed`, `prompt_landed`, `project_switched` from its own artifacts, then `position_rebuilt` from live git state in the recorded repository.
5. **Ack.** `handoff_ack_started(state, generation, claimant)`, persisted under the same lock, yielding `state_claimed`.
6. **Gate.** `teardown_allowed(results, steps=<the six-step ladder>)`. Not allowed means return now: **the predecessor is left running AND still guarded** (AC6).
7. **Prove the target's identity before touching it.** `herdr pane get <anchor>` must return `agent_session.value` EXACTLY equal to `position.predecessor_session`. A pane id is a reusable handle and syntax validation cannot detect a stale or recycled one. The successor must also PASS `--anchor-pane`, and it must equal `position.predecessor_pane`: two independent sources must agree before anything destructive happens. Any mismatch, or a missing session value, refuses both destructive steps.
8. **Re-check that the guard is still in force,** per §5's current-state rule: the LATEST `goal_status` row for the armed condition must still be `met:false`. If it is not, refuse — retiring the predecessor when the continuing session is already unguarded is the original defect.
9. **Record the phase, then clear the guard, then CONFIRM it cleared.** Persist `teardown_phase: "clearing"` under the lock BEFORE sending anything (R4 — so a crash in the window below is discoverable rather than invisible). Then send `herdr pane send-text <anchor> "/goal clear"` and `herdr pane send-keys <anchor> Enter` — both return codes checked, failure of either aborts before `pane close` — and **poll the predecessor's transcript below a baseline taken before the send for a `goal_status` row with `met:true` and `sentinel:true`**. A zero return code proves only that keystrokes were transported, not that the slash command was parsed or that the guard changed state; without the semantic confirmation a silently ignored `/goal clear` reaches exactly the close-before-clear outcome this design forbids with every check green. On timeout or malformed evidence: report `clear_unconfirmed` and **leave the pane open**.
10. **Close the pane** — `herdr pane close <anchor>`, return code checked, bounded retries (2) — then clear `teardown_phase` under the lock.

**The partial-success state is named and contained.** If the clear is confirmed but the close then
fails, the predecessor may be alive and **no longer guarded** — strictly worse than either failure
alone. Handling: retry the close twice; if it still fails, RE-ARM the predecessor by sending
`position.goal_condition` (the predecessor's OWN recorded condition, per §3) back to the anchor pane
and confirming a fresh `met:false` row appears in the predecessor's transcript; then report the
terminal state as `alive_and_re_armed`, or `alive_and_unguarded` if the re-arm or its confirmation
also fails. `alive_and_unguarded` is the one state this design treats as an incident.

**Who may complete a retirement (R3).** Only the CLAIMANT session. A later, different session cannot:
`handoff_claim` rejects a foreign claimant inside the lease and rejects one unconditionally once
`started` is true (both probe-confirmed). An earlier revision claimed "any later session can complete
the retirement" — that was false, and it is withdrawn rather than papered over. The recovery path for
a successor that dies mid-teardown is a NEW handoff: `open_handoff` bumps the generation and
supersedes the stale pending record. Until that happens the predecessor is alive and still guarded —
the safe state, and the same one AC6 requires of an aborted handoff. A fenced multi-claimant takeover
would be the alternative; it is deliberately NOT built here, because nothing in Service A needs it
and the safe state is already the default.

**R4 — the one window where a crash does NOT leave the predecessor guarded, stated plainly.** An
earlier revision claimed a successor dying mid-teardown always leaves the predecessor alive and
guarded. That is false between a CONFIRMED clear (step 9) and a successful close (step 10): in that
window the predecessor is alive and **unguarded**, and if the successor dies there, nothing re-arms
it. The window is now (a) bounded — up to three close attempts, each preceded by its own identity probe and state fence, with the clear already confirmed — and
(b) **discoverable**, because `teardown_phase: "clearing"` is persisted before the clear is sent, so
an operator or a later handoff generation reads the state and knows exactly what was in flight
instead of inferring it. The consequence is a stalled run, not lost work: the predecessor's context
and its branch are intact, and recovery is a new handoff generation.

What is deliberately NOT built: the fenced recovery ACTOR the review recommended (a separate agent
permitted, after claimant death or lease expiry, to inspect goal state and either close or re-arm).
It is a genuine improvement and it is genuinely larger than this feature — a new fencing token, a new
liveness test, and crash-injection coverage at four boundaries. Service A does not need it to be
safe, only to be tidier after a rare crash, so it is listed in §10 as out of scope rather than
half-built here.

**If the successor never calls `retire_predecessor` at all**, the predecessor stays alive with its
guard intact — the designed-safe failure. `handoff_pending.position` durably records the pane, the
session, and the goal condition, so an operator (or a later handoff generation) has everything needed
to finish the job.

## 7. The anti-parallel-path guard (AC7)

`tests/hooks/test_mid_child_handoff.py::test_no_parallel_handoff_path` is a **source-level drift
guard**, and R3 narrows the claim to exactly that: it makes a second handoff path fail the suite when
someone writes one in the obvious ways. It is not a proof of architectural impossibility — Python
offers no such enclosure — and the earlier revision's wording overstated it.

What it asserts:

- no `hooks/*.py` other than `launcher_lib.py` constructs a herdr `pane split` or `agent start` argv, invokes those builders, or shells out to `herdr` through a command string;
- `launcher_lib.py` defines exactly one `perform_handoff` and exactly one `retire_predecessor` — a second ordered sequence inside the same module fails too;
- `launcher_lib.py` obtains the disposition, the generation bump, and the claim/ack from `driver_lib`, so a hand-rolled generation increment or claim write fails;
- `driver_lib.py` has exactly one writer of `handoff_pending`, namely `open_handoff`.

The scanner is a plain function, so its own negative cases are testable: companion tests feed it
synthetic modules exercising each bypass form above and assert every one is flagged. A guard that has
never been shown to fail is not a guard.

## 8. Platform feasibility declaration (#226)

platform_apis:
- api: herdr pane split --pane <anchor> --direction <d> --cwd <path>
  feasibility: verified via existing-call-site — hooks/launcher_lib.py:236 build_split_argv ships this exact argv and is driven by perform_handoff:709; probed against the pinned 0.7.5 binary --help 2026-07-27 (explicit pane id accepted, not only --current)
  failure: fail-loud
- api: herdr agent start <name> --kind claude --pane <id> --timeout <ms>
  feasibility: verified via existing-call-site — hooks/launcher_lib.py:247 build_agent_start_argv, driven at perform_handoff:729; name grammar and the >3000ms timeout floor mirrored locally so a bad value fails before a pane exists
  failure: fail-loud
- api: herdr agent wait <target> --until idle --timeout <ms>
  feasibility: verified via existing-call-site — hooks/launcher_lib.py:265 build_agent_wait_argv, driven at perform_handoff:738; the top-level herdr wait does NOT exist in 0.7.5 (#659), herdr agent wait does
  failure: fail-loud
- api: herdr pane send-text <pane> <text> then herdr pane send-keys <pane> Enter
  feasibility: verified via spike — #654 comment 5097646359 armed a 2847-char 41-newline condition live on 2026-07-27; it arrives as one collapsed bracketed paste and does not submit early, and hooks/launcher_lib.py:308 build_send_text_argv is the shipped builder
  failure: fail-loud
- api: /goal clear submitted cross-pane through that same send-text plus send-keys route
  feasibility: verified via spike — run live 2026-07-27 (epic #667 log D-16 and #654): the target session reports met=true sentinel=true. That is proof of guard REMOVAL only; a cleared session exiting on its own is NOT proven and is not relied on
  failure: fail-silent
  surface: a zero return code proves transport, not parsing, so retire_predecessor baselines the predecessor transcript and polls below it for a met:true sentinel:true goal_status row before pane close is permitted; a timeout reports clear_unconfirmed and leaves the pane OPEN, and the return codes of send-text, send-keys AND pane close are each checked and logged
- api: herdr pane close <pane>
  feasibility: verified via existing-call-site — hooks/launcher_lib.py:349 build_teardown_argv is the shipped builder and hooks/launcher_lib.py:903 _close_tentative_pane already drives it on #611's abort path; argv is exactly ["herdr","pane","close",<validated pane id>], no flags
  failure: fail-loud
  surface: a non-zero return code is recorded on the step record and never swallowed, with two bounded retries; because herdr 0.7.5 gives no way to distinguish an already-closed pane from a genuine failure, the report names both possibilities rather than assuming the benign one, and a runner timeout surfaces as the same failed-teardown path
- api: herdr pane get <pane> yielding agent_session.value as a Claude session id
  feasibility: verified via spike — confirmed live 2026-07-27, pane w1:pB3 returned a value equal to that session's CLAUDE_CODE_SESSION_ID; parser is hooks/launcher_lib.py:387 parse_pane_agent_session
  failure: fail-silent
  surface: a missing or unparseable value returns None; the spawned check FAILS CLOSED at perform_handoff:751, and the pre-teardown identity check refuses when the value is absent or unequal to position.predecessor_session, so neither an unproven spawn nor an unproven target can reach a destructive step
- api: herdr pane list as the pre-split pane inventory
  feasibility: verified via existing-call-site — hooks/launcher_lib.py:842 _pane_inventory, mandatory before the split at perform_handoff:697; herdr 0.7.5 exposes no pane environment through pane get or pane list (confirmed against the live server), so ownership is never attributed by token
  failure: fail-silent
  surface: a malformed member yields None (a partial inventory is not an inventory) and the handoff refuses with pane_inventory_unavailable; anything not provably new is REPORTED as a possible orphan and never closed
- api: Claude Code goal_status transcript record, shape {"attachment":{"type":"goal_status","met":false,"sentinel":true,"condition":"..."}}
  feasibility: verified via existing-call-site — hooks/launcher_lib.py:422 transcript_has_unmet_goal reads this real shape, confirmed by grepping live transcripts under ~/.claude/projects/ during #611; the real-shaped regression fixture is tests/fixtures/herdr/goal_status_transcript.jsonl, which also carries a met:true row used by the clear-confirmation reader. The invented {"goal_status":{"met":false}} shape is the #611 Step-11 defect this must not reintroduce
  failure: fail-silent
  surface: goal_armed fails closed when no matching row appears within the bounded poll and requires the ARMED (post-truncation) condition text; the clear confirmation likewise fails closed to clear_unconfirmed, which leaves the pane open
- api: Claude Code transcript persistence of text pasted into a pane (the prompt_landed evidence)
  feasibility: verified via spike — probed live 2026-07-28 on this session's own transcript: a phrase from a prompt pasted into the pane is present VERBATIM 3 times, carried in rows of type queue-operation (operation enqueue, text under "content") and attachment (attachment.type queued_command, text under "prompt"), NOT in a type "user" row with message.content. The text persists and is greppable; its ROW SHAPE is not a stable contract
  failure: fail-silent
  surface: prompt_landed matches the marker token as a plain substring over the transcript tail and asserts nothing about row shape; it fails closed on the bounded poll, and a fixture carrying a real queue-operation-shaped row pins the behaviour so a future structured-match refactor breaks a test instead of the feature
- api: claude_docs/session_registry.jsonl line carrying session_id, project and project_path
  feasibility: verified via existing-call-site — the switch skill appends exactly {"session_id","project","project_path","started","cwd"} and this session's own bind line was read back with project "rawgentic" and project_path "./projects/rawgentic" on 2026-07-28; reader is hooks/launcher_lib.py:405 registry_has_session
  failure: fail-silent
  surface: project_switched requires ONE line matching all three recorded values, so a successor bound to the wrong project or path fails the check and cannot authorise teardown; the comparison is an exact string match against the same producer's representation and claims no filesystem canonicalisation
- api: git -C <repo_root> rev-parse --show-toplevel and git -C <repo_root> rev-parse --abbrev-ref HEAD as the position_rebuilt evidence
  feasibility: verified via existing-call-site — this repo reads git state through subprocess with shell=False in hooks/plan_lib.py's scan helpers and in WF2 Step 7's own base assertion, and `git -C <dir>` is the established way to pin the target repository rather than inheriting a process cwd; both commands run through the same injected runner the rest of retire_predecessor uses, so tests drive them without a real repo
  failure: fail-loud
  surface: the repository is named explicitly with -C rather than inherited from cwd, and --show-toplevel must equal position.repo_root BEFORE the branch is compared — otherwise a same-named branch in a DIFFERENT repository would satisfy the check and authorise teardown for the wrong working tree; a non-zero return, an unparseable value, or either mismatch fails position_rebuilt closed, which blocks teardown while leaving the predecessor guarded
- api: .driver-state JSON extension with handoff_pending.position and handoff_pending.kind
  feasibility: verified via capabilities-file — docs/driver-state/queue.schema.json declares additionalProperties true at lines 8, 22 and 54 (read this session), so the added keys validate with no schema change, and driver_lib.validate_driver_state checks structure only
  failure: fail-loud
- api: fcntl.flock exclusive lock on a stable sidecar path during driver-state read-modify-write
  feasibility: verified via existing-call-site — hooks/plan_lib.py:2262 already locks path + ".lock" (a sidecar, not the state file) and hooks/notes-size-handler.py:93 plus hooks/seat_outcomes_lib.py:645 use the same primitive; this design promotes the plan_lib helper to a public name rather than adding a second implementation
  failure: fail-loud
  surface: the sidecar inode is stable across atomic replacement of the state file, so waiters cannot end up locking different inodes; both writers this change introduces hold it from read through replace, and a concurrency test interleaves open_handoff with claim/ack to prove neither update is lost
- api: atomic file replacement for the driver-state write
  feasibility: verified via existing-call-site — hooks/atomic_write_lib.py atomic_write_text is the repo's single home for tmp+replace and is already used by registry_prune.py and plan_lib.py
  failure: fail-loud

## 9. Failure modes and what each leaves behind

| failure | left behind |
|---|---|
| invalid position / no active child / unrecognised `kind` | nothing written, no pane created, predecessor untouched |
| a previous handoff aborted (`cancelled: true`) | both `retire_predecessor` and `_cmd_handoff` refuse the record; no lease can be taken on it |
| the caller is not the recorded successor session | refused at step 2, before any claim; predecessor untouched |
| the successor's guard is no longer in force at the pre-clear re-check | refused; predecessor alive and guarded (retiring it here would leave the run unguarded) |
| successor crashes between a CONFIRMED clear and a successful close | predecessor alive and **UNGUARDED** — the one window where that is true; `teardown_phase: "clearing"` is on disk so the state is discoverable; the run stalls, the branch and context survive, recovery is a new handoff generation |
| pane inventory unavailable, or split response not provably new | refuse before or instead of claiming ownership; possible orphan REPORTED, never closed (#611 discipline, unchanged) |
| goal never arms | successor pane closed (ownership proven), predecessor alive and guarded |
| resume prompt never lands, or project/path never matches | same as above |
| successor is not on the recorded branch, is not in the recorded repository, or its receipt disagrees with the record on generation, claimant or step | `position_rebuilt` fails; teardown refused; predecessor alive, guarded, and still holding the live context. (The receipt carries no `test_baseline` — an earlier revision of this row implied it did, corrected at Step 11) |
| successor cannot claim (stale generation, foreign claimant) | predecessor alive and guarded; the run continues in place |
| pre-teardown identity check fails (pane no longer hosts the recorded session, or the anchor argument disagrees with durable state) | both destructive steps refused; predecessor alive and guarded; reported loudly |
| `send-text` for `/goal clear` returns a definite failure | abort BEFORE `pane close`; nothing transported; predecessor alive and STILL guarded |
| `send-keys` fails, or either call is AMBIGUOUS (the runner raised) | abort BEFORE `pane close`; the clear may be STAGED unsubmitted in the predecessor's input, so it is guarded now but a later Enter would submit it. `teardown_phase: "clear_staged_unsubmitted"` records exactly this (Step 11 pass-2 — an earlier revision of this row claimed "STILL guarded" for both cases) |
| `/goal clear` transported but never confirmed (`met:true` never appears) | `clear_unconfirmed`; pane left OPEN. **Guard state genuinely AMBIGUOUS** — the clear may have been parsed while its confirmation was unreadable or arrived after the poll budget, so the predecessor is either still guarded or alive-and-unguarded and this cannot distinguish them. `teardown_phase: "clear_unconfirmed"` is persisted so an operator can. An earlier revision of this table claimed "STILL guarded", which was wrong (Step 11) |
| `pane close` fails after a CONFIRMED clear | bounded retries, then re-arm from `position.goal_condition` with confirmation; terminal state `alive_and_re_armed`, or `alive_and_unguarded` if the re-arm fails — the one state treated as an incident |
| successor dies BEFORE the clear is sent | predecessor alive and guarded; recovery is a NEW handoff generation, not a foreign claim |
| the anchor stops provably hosting the predecessor AFTER a confirmed clear | `target_changed_after_clear`; NEITHER close nor re-arm is attempted, because both would act on a pane that may now belong to someone else. The predecessor is alive-and-unguarded OR has exited on its own and this cannot distinguish them (Step 11 pass-3 — this state was producible and this table omitted it) |

## 10. Out of scope, explicitly

Deciding WHEN to hand off (#654's PreCompact detector and Stop-hook channel — the next child in this
epic), the cron/launcher path (#611, and Service B by D-16), the availability guard (#666, held),
and `clear-prep` (user-level).

Three items are out of scope **as deliberate decisions with reasons**, each filed as a follow-up
rather than half-built:

1. **A fenced recovery actor for the clear-to-close window** (§6). Needs a fencing token, a claimant-liveness test, and crash-injection coverage at four boundaries. The window is bounded and now discoverable; the failure is a stall, not lost work.
2. **A fenced multi-claimant takeover** so a later session can finish another's retirement (§6). `handoff_claim` refuses foreign claimants by design; a new handoff generation is the sanctioned recovery.
3. **Retrofitting the driver-state lock onto the epic-run skill's prose-driven status writers** (§3). Acceptable here because Service A has one writer at a time by construction; a second driver session on one campaign is already outside #569's model.
