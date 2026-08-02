---
name: revalidate-children
description: Re-check an epic's remaining child issues against the current main after a merge, and write the receipt that lets the driver hand out the next child. Use when the queue-revalidation gate refuses ("refusing to hand out the next child", `revalidation_required`, rc 6 from `launcher_lib handoff`), after every merge inside an epic auto-run, when a handoff is refused at the `queue_revalidated` rung, or when the user says "revalidate the children", "re-check the remaining issues", "are the other issues still accurate", "did that merge rot the queue". Report-and-stamp only — it posts correction COMMENTS and never edits an issue body, and it never closes a child. Do NOT use to implement a child, to review code, or to re-run an audit when BOTH gate clauses are already satisfied — the receipt attests the current head AND every eligible child is stamped at it (it is a no-op only then; a current receipt alone is not enough, and an unstamped eligible child is exactly what this skill fixes).
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
- every eligible child carries `validated_against == that head`.

There is no third clause today. **A `pending_disposition` gates NOTHING — the owner gate was cut
from #840 and is being rebuilt in #848** (owner decision 2026-08-02, after it broke in four
consecutive review rounds). You still RECORD the marker when an audit concludes a child is
obsolete: it is the evidence an owner acts on, and #848 is what will make it refuse. Until then,
say plainly in your report that an obsolete child is NOT yet blocked from satisfying a
dependent's dependency.

**This skill clears both clauses above.** Every refusal it can meet is cleared by re-running it
and rebuilding the receipt.

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
`validated_against` is absent, differs from the observed head, **or** equals it while the
receipt does not COVER that child. "Covers" means all three of: `queue_revalidation.validated_head
== observed_head`, an entry exists for the child, AND that entry both passes
`validate_revalidation_child` and carries `to_sha == observed_head`. A structurally invalid entry
is not evidence — omitting the validity half is what made a `body_hash: "bad"` record look like
coverage, so the child was excluded here and `revalidation_worklist` then raised
`no extraction supplied for child #N` (round-11 finding). `driver_lib._receipt_covers_child` is
the same predicate the code uses; call it rather than reimplementing this list.

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
| any `broken`, and the issue no longer makes sense | set `pending_disposition: "issue_obsolete"` and report it to the owner — it does NOT block anything yet (#848) |

**Corrections are comments. Never edit an issue body.** The body stays as filed; the comment is the
authority. State that in the comment.

**`issue_obsolete` is not an outcome.** Closing a child is an owner decision, so record the
marker and REPORT it — never choose for them. Until #848 lands the marker is INFORMATIONAL: it
gates nothing, and the child is stamped and selectable like any other. Say that plainly in your
report, or an owner reads "obsolete" and assumes the queue is holding when it is not.

**7. Write the receipt with the `rebuild-receipt` command.** Do NOT assemble it by hand and do
NOT call `driver_lib.rebuild_receipt` yourself — it is PURE, so calling it changes no file, and
rebuilding from the state you read back in step 1 silently erases anything another session
committed while you were auditing (a `record-child-outcome` landing mid-audit is the real case).
The command re-reads the state under the lock, observes `origin/main` itself, rebuilds against
that, writes atomically, then re-reads from disk and validates what actually landed:

```bash
python3 hooks/launcher_lib.py rebuild-receipt \
  --driver-state <state.json> --project-root . --audited audited.json
```

`audited.json` holds `{"<issue number>": <record>}` for the children you actually looked at —
omit the flag entirely when nothing needed auditing, which is legitimate and still arms the
campaign. Exit 0 prints the validated head; **6** means the rebuild was refused (the message
names the remedy); **5** means the head could not be observed.

Build each record with the constructor, never by hand — the validator requires eight fields and
a `body_hash` over a specific normalization, which is why hand-built records failed:

```python
driver_lib.build_revalidation_record(
    body=<the issue body you just read>, from_sha=<item["from_sha"]>, to_sha=<observed_head>,
    extraction=<item["extraction"]>, depth=<item["depth"]>, claims=[...],
    validated_at=<epoch int>, outcome="still_valid")          # or pending_disposition=...
```

It hashes `normalize_issue_body(body)` for you and validates the record before returning it.

The command REBUILDS the receipt from evidence rather than editing it in place, which is what
makes the gate recoverable:

- a record that no longer validates, or that attests a different head, is **dropped** — it is not
  evidence, and carrying it forward once made a corrupt entry unrecoverable for any child the
  worklist does not audit (it audits eligible children only);
- a stamp whose evidence was dropped, or that names no usable commit, is **cleared** — the stamp
  is the claim and the record is the evidence, so the claim must never outlive it;
- a `pending_disposition` is carried forward as EVIDENCE of what the audit found, and the child
  is still STAMPED. It withheld the stamp while the owner gate existed, because a stamped child is
  selectable; with nothing gating on the marker that rule stopped protecting anything and started
  jamming the queue instead — the child was never stamped, so the provenance clause refused for
  ever. #848 restores both together;
- an EMPTY `audited` still advances `validated_head`. That is correct, not a shortcut: a campaign
  whose children are all merged or in flight has nothing to audit, and the head clause refuses it
  regardless — so if this could not arm it, the gate would be shut for good on the mid-child
  handoff it exists to serve.

The command validates the PERSISTED file after writing it, so exit 0 means the receipt on disk is
structurally valid. It does **not** promise selection will then succeed: an incomplete audit
leaves eligible children stamped at an older head, and the gate refuses that on purpose. Step 8
is what confirms the gate actually opened.

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
