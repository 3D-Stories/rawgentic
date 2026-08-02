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
- no eligible child carries a `pending_disposition`.

Only this skill clears that. It is also the producer for the `queue_revalidated` rung on the
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

**3. Build the worklist.** For each eligible child (effective status `queued`) whose
`validated_against` is absent or != the observed head, compute:

- its cited paths, via `driver_lib.cited_paths(body, resolves)` — you supply `resolves`, the set of
  paths that exist in either endpoint tree, probed with
  `git -C . cat-file -e origin/main:<path>` (rc 0 present, rc 128 absent);
- its changed set, from `git -C . diff --name-status -M <from_sha> <to_sha>` parsed by
  `driver_lib.parse_changed_paths` — **rename-aware, both old and new path count**;
- the range is **per child and cumulative**: from that child's own `validated_against` to the
  observed head, or from `base_default_branch_sha` when it has never been validated. **When
  the campaign carries no `base_default_branch_sha` either** — legal, the field is optional —
  the range collapses to `from_sha == to_sha == observed_head` and the depth is forced to
  `deep`. With no baseline nothing can be shown to be untouched, so every claim is checked
  against the current tree. This is the FIRST-ARM path and it must work, or a legacy campaign
  refused by the gate could never be armed at all.
- The range is never **last-merge-only** — that misses the crash gap, the skipped-merge gap and the
  multi-session gap, which are the cases this exists for.

Then `driver_lib.revalidation_worklist(state, observed_head, extractions, changed_by_child)` returns
one item per child with a `depth`.

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

**7. Write the receipt, atomically, and only when it is complete.** Under the state lock, write each
child's `validated_against` and its `queue_revalidation.children["<n>"]` record, and advance
`validated_head` to the observed head **only when every eligible child is stamped or marked**. A
half-written receipt that advances the head would open the gate on children nobody looked at.

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
