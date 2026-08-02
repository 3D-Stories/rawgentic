---
name: revalidate-children
description: Re-check an epic's remaining child issues against the current main after a merge, and write the receipt that lets the driver hand out the next child. Use when the queue-revalidation gate refuses ("refusing to hand out the next child", `revalidation_required`, rc 6 from `launcher_lib handoff`), after every merge inside an epic auto-run, when a handoff is refused at the `queue_revalidated` rung, or when the user says "revalidate the children", "re-check the remaining issues", "are the other issues still accurate", "did that merge rot the queue". Report-and-stamp only — it posts correction COMMENTS and never edits an issue body, and it never closes a child. Do NOT use to implement a child, to review code, or to re-run an audit whose receipt already attests the current head (it is a no-op then).
argument-hint: the campaign's driver-state path, or the epic number
---

<role>
You mechanise a pass that was already proven by hand. On 2026-08-02 an audit of epic #756 re-checked
all 23 open children against `origin/main` and found **14 carrying claims that had rotted**. That
pass is what this skill automates — not a new method, a repeatable one.

You produce EVIDENCE, never a verdict on your own authority. A child is stamped only when you can
quote the claim from its body, name what you checked it against, and quote what you found. An empty
`claims` list is refused by the type checker, and that refusal is the whole point: without it an
agent could fetch 24 bodies, stamp them all `still_valid`, and satisfy the gate having validated
nothing.
</role>

## Why the gate refuses, and what clears it

Every merge moves `main`. Every child issue's body is a set of claims *about* `main` — file:line
anchors, root causes, acceptance criteria. A merge can falsify any of them, and nothing tells the
issue. A successor then implements from a body that is quietly wrong.

`driver_lib.next_ready_issue` therefore refuses to hand out the next child unless:

- the campaign receipt's `validated_head` equals a **freshly observed** `origin/main`, AND
- every eligible child carries `validated_against == that head`, AND
- **no durably-undisposed child carries a `pending_disposition`** — note the scope difference,
  corrected at round-7 finding 2. Stamp freshness is checked on ELIGIBLE children only; the
  pending marker is checked on EVERY child whose durable status is not `deferred`,
  `abandoned` or `merged`. A `pr_open` child cannot be selected but can still SATISFY a
  dependency, so an obsolete one would otherwise hand out somebody else's work.

**This skill clears the first two; it CANNOT clear the third** (corrected at round-5 finding 4 —
this line used to read "only this skill clears that", contradicting the skill's own step 6 below).
A `pending_disposition` is an OWNER decision by design: re-running this skill rediscovers the same
marker and changes nothing, because choosing between `deferred` and `abandoned` is deliberately not
a machine's call. Only
`python3 hooks/launcher_lib.py record-child-outcome --issue <n> --status deferred|abandoned`
clears it. Say so when you report a refusal, or the operator re-runs this skill for ever.

It is also the producer for the `queue_revalidated` rung on the
pane-handoff ladder — `teardown_allowed` refuses to retire a predecessor until the receipt is current.

## The procedure

**1. Bind and locate.** Resolve the campaign's driver-state file
(`claude_docs/.driver-state/<campaign>.json`). Read `epic`, `issues[]`, `base_default_branch_sha`,
and any existing `queue_revalidation`.

**2. Observe the head — never assume it.**

```bash
python3 -c "import sys; sys.path.insert(0,'hooks'); import launcher_lib; print(launcher_lib.observe_head('.'))"
```

That wrapper runs `git -C . fetch origin` then `git -C . rev-parse origin/main` and checks BOTH
return codes. Do not substitute a cached SHA, `HEAD`, or `validated_head` — a stale value compares
equal to itself and opens the gate on a moved main.

**3. Build the worklist.** The candidate set is every eligible child (effective status `queued`)
that is **not already attested at the observed head** — and "attested" means BOTH a stamp and a
current receipt covering it, not a stamp alone. Concretely, include a child when its
`validated_against` is absent, differs from the observed head, **or** equals it while
`queue_revalidation.validated_head != observed_head` or the receipt carries no entry for that
child.

> **Round-7 finding 2 — this step used to say "absent or != the observed head", and that made the
> gate unclearable.** A child stamped at the head under a stale or absent receipt was excluded
> here, so you produced empty `extractions`/`changed_by_child`, and `revalidation_worklist` — which
> since round 6 correctly re-audits exactly that child — raised
> `DriverStateError: no extraction supplied for child #N`. The Python was right and the procedure
> could not feed it. Compute the candidate set the same trust-aware way the code does, or the
> refusal has no remedy.

For each candidate, compute:

- its cited paths, via `driver_lib.cited_paths(body, resolves)` — you supply `resolves`, the set of
  paths that exist in either endpoint tree, probed with
  `git -C . cat-file -e origin/main:<path>` (rc 0 present, rc 128 absent);
- its changed set, from `git -C . diff --name-status -M <from_sha> <to_sha>` parsed by
  `driver_lib.parse_changed_paths` — **rename-aware, both old and new path count**;
- the range is **per child and cumulative**: from that child's own `validated_against` to the
  observed head, or from `base_default_branch_sha` when it has never been validated. **When that
  commit is UNUSABLE the range collapses to `from_sha == to_sha == observed_head` and the depth is
  forced to `deep`** — with no baseline nothing can be shown to be untouched, so every claim is
  checked against the current tree. This is the FIRST-ARM path and it must work, or a legacy
  campaign refused by the gate could never be armed at all. Unusable means either of:
  - **absent or malformed** — the field is optional, nullable, and schema-constrained only to
    "a string", so `null`, `""` and `"abc"` all occur in schema-valid state. `revalidation_worklist`
    decides this one itself.
  - **unresolvable** — a well-formed SHA whose object is gone (force-pushed, pruned, or from
    another repository). Format cannot see it, so **you probe**: run
    `git -C . cat-file -e <sha>^{commit}` once per distinct baseline (rc 0 present, non-zero gone)
    and pass every failure in `unresolvable_shas`. Skip the probe and the worklist builds, but the
    `git diff` above then fails on a left endpoint that does not exist.
- The range is never **last-merge-only** — that misses the crash gap, the skipped-merge gap and the
  multi-session gap, which are the cases this exists for.

Then `driver_lib.revalidation_worklist(state, observed_head, extractions, changed_by_child,
issue_state_probe=<probe>, unresolvable_shas=<probed set>)` returns one item per child with a
`depth` and a `baseline` provenance of `stamp`, `base` or `unavailable`.

**Pass the `issue_state_probe`** — build it with
`launcher_lib.build_issue_state_probe(launcher_lib.repo_from_git('.'))`. Without it a child that
is durably `queued` but has really already merged counts as eligible, so the worklist asks you to
revalidate an issue that is closed, and the gate and this skill end up disagreeing about what the
queue contains (round-8 High 1). A repo it cannot derive degrades to "the file wins" — the skill
still runs. **A child carrying an unusable stamp does NOT fall
back to the campaign base** — that would date the range from a commit it was never validated at, and
a wider-but-wrong range can buy `quick` on a real change. It goes straight to `unavailable`/`deep`.

**4. Look — and the depth decides HOW HARD, never WHETHER.**

This is an owner ruling (2026-08-02) and it supersedes any earlier "skip children whose files
weren't touched" reading. **Nothing is auto-cleared.** A merge can invalidate a root-cause claim
through a file the child never cites — #835 is the standing proof: its body was wrong about the
*cause*, not about a filename, so a path filter would have waved it straight through.

- **`deep`** (a cited path was touched, or extraction was `ambiguous`): check every claim of all
  three kinds — `citation`, `cause`, `ac`.
- **`quick`** (nothing cited was touched): check the `cause` and `ac` claims; take citation claims
  as-is. This is the affordability lever and it is honest — it drops the class of check a merge is
  least likely to invalidate, never the class #835 failed on.

**What the receipt actually proves — read this before trusting a `deep` stamp** (Step-11 round 3,
finding 4). `deep` is an INSTRUCTION to you, not a mechanically verified property of the receipt.
`validate_claims` refuses an empty claims list and validates the shape of every claim it is given,
but it does **not** check completeness or kind coverage: a `deep` record carrying one `cause` claim
is structurally valid and makes the child selectable. So the receipt attests *that a look happened
and left evidence*, never *that every claim in the body was examined*. Closing that gap needs a
mechanical inventory of the issue body bound claim-by-claim to the receipt — a much larger change
than this machinery carries, and deliberately not attempted here. **The consequence for you: depth
is your obligation, and nothing downstream will catch you skimping on it.**

**5. Record each claim as evidence.** Per claim:

```json
{"kind": "citation" | "cause" | "ac",
 "quoted_from_body": "<the claim, verbatim from the body>",
 "checked_against": "<path>@<sha>" | "<no-file: reasoning>",
 "evidence": "<verbatim quote from `git show origin/main:<path>`, or the explicit statement that the claim no longer holds>",
 "verdict": "holds" | "broken"}
```

`evidence` must be something you actually read. Quote it. A summary of what you expect a file to say
is not evidence.

**6. Derive the outcome — do not assert it.**

| all claims | outcome |
|---|---|
| `holds` | `still_valid` |
| any `broken`, and the issue is still worth doing | `body_corrected` — **post a correction COMMENT** and record its URL |
| any `broken`, and the issue no longer makes sense | leave it UNSTAMPED and set `pending_disposition: "issue_obsolete"` |

**Corrections are comments. Never edit an issue body.** The body stays as filed; the comment is the
authority. State that in the comment.

**`issue_obsolete` is not an outcome and never stamps a child.** Closing a child is an owner
decision, so record the marker and let the gate keep refusing until an owner moves the child to
`deferred` or `abandoned` via `launcher_lib record-child-outcome`. The machine's job here is to
refuse, not to choose.

**7. Write the receipt with `driver_lib.rebuild_receipt`, under the state lock.** Do NOT assemble
it by hand — this step was prose until round 8, and prose is where three review rounds found
defects, because every session re-derives "which records survive" slightly differently.

```python
new_state = driver_lib.rebuild_receipt(state, observed_head, audited)
```

`audited` is `{issue_number: record}` — one entry per child you actually looked at, each record
carrying `to_sha == observed_head`. The function REBUILDS the receipt from evidence rather than
editing it in place, which is what makes the gate recoverable:

- a record that no longer validates, or that attests a different head, is **dropped** — it is not
  evidence, and carrying it forward once made a corrupt entry unrecoverable for any child the
  worklist does not audit (it audits eligible children only);
- a stamp whose evidence was dropped, or that names no usable commit, is **cleared** — the stamp
  is the claim and the record is the evidence, so the claim must never outlive it;
- a child whose record carries a `pending_disposition` is recorded but **never stamped**;
- an EMPTY `audited` still advances `validated_head`. That is correct, not a shortcut: a campaign
  whose children are all merged or in flight has nothing to audit, and the head clause refuses it
  regardless — so if this could not arm it, the gate would be shut for good on the mid-child
  handoff it exists to serve.

It validates its own output before returning, so it can never be the source of a receipt the gate
then refuses.

Validate before you rely on it:

```bash
python3 -c "import sys,json; sys.path.insert(0,'hooks'); import driver_lib; driver_lib.validate_queue_revalidation(json.load(open('<state>'))); print('receipt OK')"
```

That call also checks the LINKAGE — a child stamped at `validated_head` with no receipt entry is
refused, which is exactly the fabricated-provenance case.

**8. Confirm the gate opens.** Re-run the selection. If it still refuses, the reason names the child
and why; fix that, do not loosen the gate.

## What this skill must never do

- **Never stamp without claims.** The type check refuses an empty list; do not work around it by
  inventing a claim.
- **Never edit an issue body.** Comments only.
- **Never close, defer or abandon a child.** Owner decision.
- **Never advance `validated_head` past an unstamped eligible child.**
- **Never pass a SHA you did not just observe.**
- Secrets by NAME only, as everywhere in this repo.

## Reporting

Close with: the observed head; how many children were eligible, deep, quick; how many
`still_valid` / `body_corrected` / marked obsolete; the correction-comment URLs; and whether the
gate now opens. Name anything you could NOT check and why — an honestly reported gap beats a stamp
that implies a check nobody ran.
