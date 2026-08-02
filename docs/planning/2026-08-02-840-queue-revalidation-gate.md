# Design — #840: re-validate remaining children after every merge

**Issue:** #840 (epic #756, Tier 1, owner-ordered FIRST)
**Author:** WF2, session f704bd0c, 2026-08-02. **Revision 4** — supersedes r3 after the pass-3 gate
(self-review 2 High / 2 Med / 1 Low + adversarial 6 High / 3 Med, 3 of them ambiguous). The
`design` loop-back budget was already 2/2, and three ambiguous findings made the #798
budget-exhausted self-close ineligible, so the gate ESCALATED to the owner. Both escalated
questions were answered (§4a "what a look is", §8 the mandatory correction consumer) and the full
finding set was applied together, per the ambiguity-breaker contract.
Loop-back budget: **design 2/2 (exhausted), spec_tighten 0/2, global 2/3.**
**Base:** `origin/main` `3d4e1607`, v3.116.2. Every claim read from a tree verified identical to
`origin/main` (0/0 ahead/behind, no tracked modifications).

---

## 1. The problem

Every merge moves `main` under every child that has not started. Their bodies were written against
older code, so file:line citations, root-cause claims and acceptance criteria rot. Nothing notices.

Measured: the 2026-08-02 audit of epic #756 found **14 of 23** open children carrying claims that no
longer held; three had already cost real work (#838's design written against an unmerged branch,
#839's recorded fix shape invalidated by a `credential_ref` fact nobody re-checked, #835's body
naming a root cause that turned out to be wrong).

Two weaker designs were rejected by the owner: a revalidation *step* in the epic-run skill (prose
is skipped exactly under the pressure that makes children stale), and revalidate-after-every-merge
(only helps a session that SURVIVES; this epic has ended three sessions mid-child).

## 2. THE PREMISE — owner ruling 2026-08-02, and why r1/r2 were wrong

r1 and r2 both made the cited-paths ∩ changed-files intersection an **auto-clear**: a child whose
cited files the merge did not touch was stamped valid without anyone looking.

Both pass-2 reviewers independently refused that, for the same reason: **intersection is an
incomplete dependency model.** A merge can invalidate a root-cause claim, an API assumption or an
AC through a file the child never cites. **#835 is the live proof** — its body was wrong about the
*cause*, not about a filename, so a path filter would have cleared it. That is exactly the rot this
issue exists to stop.

**Owner ruling: the intersection decides HOW HARD to look, never WHETHER to look.**

- Every **eligible** remaining child must carry `validated_against == <current head>` to be
  selectable. Nothing is ever cleared without a look.
- The intersection survives as a **depth hint**: `deep` when the merge touched a cited path or the
  body could not be parsed; `quick` otherwise.

This is strictly simpler than r2 and it resolves six of the pass-2 findings outright, because the
extraction no longer has to be a completeness proof. Rename edge cases, unparseable bodies and
citation ambiguity now only mis-set a *priority*, never wave a child through.

It also supersedes the issue body's "a child needs revalidation **only if** those sets intersect"
and subsumes the earlier AC2 ruling (a citation-free body was already required to go to the
worklist; now every child does).

## 3. State — normative, no ellipses

Per-child, on each `issues[]` entry:

```
"validated_against": "<40-char sha>"      # absent => never validated
```

Campaign-level:

```
"queue_revalidation": {
  "version": 1,
  "extractor_version": 1,
  "validated_head": "<40-char sha>",      # advanced ATOMICALLY, only when every eligible child is stamped
  "children": {
    "<number>": {
      "body_hash": "<sha256 of the normalized body read at revalidation time>",
      "from_sha": "<sha>", "to_sha": "<sha>",
      "extraction": "paths" | "none" | "ambiguous",
      "depth": "deep" | "quick",
      "outcome": "still_valid" | "body_corrected",
      "pending_disposition": "issue_obsolete" | null,
      "claims": [ <claim record, see §4a> ],
      "correction_comment": "<url>" | null,
      "validated_at": <int epoch>
    }
  }
}
```

**`claims` and `pending_disposition` are part of the normative shape** (verifier finding #1 — r4
added both in later sections while calling §3 "normative, no ellipses", leaving an implementation
nowhere specified to persist them). Types and rules:

- `claims`: a non-empty list of §4a claim records. **Empty or absent ⇒ the child is not stamped**,
  enforced by the same raising type check as `validated_against`.
- `pending_disposition`: `"issue_obsolete"` or absent/null. Non-null ⇒ the child is treated as
  outstanding by `next_ready_issue` regardless of its stamps. **Cleared only by an owner-driven
  transition** to `deferred` or `abandoned` via `record_child_outcome` (`:342`), both of which leave
  the effective-`queued` population so selection skips them at `:323`. There is no self-clearing
  path — that is the point of it being an owner gate.
- `issue_obsolete` is therefore NOT a value of `outcome`; it lives only in `pending_disposition`,
  so a stamped child can never carry it.

Nothing in `driver_lib` rejects a new key: `validate_driver_state` (`:834-907`) iterates only
`number`/`status`/`depends_on` with no unknown-key branch, and
`docs/driver-state/queue.schema.json` sets `additionalProperties: true` at both levels. So **no
schema bump is needed and a malformed value would pass silently** — therefore the gate type-checks
`validated_against` itself, in the raising style of `_in_queue_deps:148-151`, not the
error-accumulating style of `validate_driver_state`.

**Eligible statuses (pass-2 finding #8, the ambiguous one — now defined).** Eligible =
**effective** status `queued`, not durable status (pass-3 finding #4). `merged` and `abandoned` are
terminal (`TERMINAL_STATUSES`, `:339`); `deferred` is parked; `in_progress`/`pr_open` are the active
child the run already holds in context. Eligibility is derived from the SAME
`effective_issue_statuses` map selection uses (`:244-286`) — r3 used durable status while
acknowledging two paragraphs later that the probe can confirm a `queued` entry already merged
without writing state, so an externally-closed child would have stayed eligible and blocked the
queue forever on a revalidation nobody could meaningfully perform. A probe failure conservatively
retains the durable `queued` status, matching that function's existing never-veto-on-outage rule.

**A missing entry in any injected map is NOT an empty set** — it raises, so "no data" can never be
read as "no changes".

**`issue_obsolete` must remove the child from selection (pass-3, both reviewers independently).**
r3 made it a valid stamped outcome while eligibility stayed `queued` and the gate opened once SHAs
matched — so revalidation could conclude a child was obsolete, stamp it, advance `validated_head`,
and then hand that obsolete child out to be implemented. Corrected: `issue_obsolete` is **not a
stampable terminal outcome**. It leaves the child unstamped and records
`pending_disposition: "issue_obsolete"`, which `next_ready_issue` treats as outstanding — the queue
stays refused until an owner decides between `deferred` and `abandoned`. Closing a child is an owner
decision, so the machine's job is to refuse, not to choose. A mutation-sensitive test asserts an
obsolete-marked child can never be returned by `next_ready_issue`.

## 4. What triggers the gate

r2 keyed it on a `stale_since` marker written by `record_child_outcome`. The pass-1 gate found the
bypass: `effective_issue_statuses` (`:244-286`) can overlay a `queued` child to `merged` from the
GitHub probe **without writing anything**. A marker somebody has to write is not fail-closed.

**Trigger = head movement AND per-child provenance**, both required (pass-2 findings #1, #2):

```
refuse if  observed_head != queue_revalidation.validated_head
       or  any eligible child's validated_against != observed_head
```

The second clause is what closes AC1 and AC3b: r2's head-only check let a brand-new campaign at its
base head, or a newly-added unstamped child at an unchanged head, hand out work with no provenance
at all.

**`observed_head` must be FRESHLY OBSERVED, not merely supplied (pass-3, both reviewers
independently).** r3 required the argument but never bound it to a live observation, so a caller
could pass a cached SHA — or `validated_head` itself — and satisfy *both* refusal clauses after main
had moved. That also silently defeated the abrupt-death recovery in the next paragraph, since a
crashed predecessor's stale head would compare equal to itself.

Corrected: **one production selection wrapper** owns the observation.
`driver_state.observe_head(repo_root)` runs `git -C <repo_root> fetch origin` then
`git -C <repo_root> rev-parse origin/main`, checks **both** return codes, and returns the full
40-char SHA. Every campaign selection goes through it; `next_ready_issue` receives its result and
nothing else. The call-site source test pins that the wrapper — not a literal, not a state field —
is what feeds the parameter. Integration test: advance the tracking head while state retains the
old one, and prove selection refuses.

**`observed_head` is REQUIRED on campaign paths, not optional (pass-2 finding #1).** r2 made it
optional "for compatibility", which both reviewers called a silent bypass — and this repo has
already shipped that exact defect once: `tests/hooks/test_driver_state_write_back.py:304-306`
documents an optional input that shipped dead because nothing threaded it into
`fresh_session_handoff`. So:

- `next_ready_issue(..., observed_head)` and `fresh_session_handoff(..., observed_head)` take it.
- Every **campaign** call site passes it, asserted by a source-level call-site test — the pattern
  `tests/hooks/test_wf_review_sites.py` already uses to pin `--requires-context`. Adding a campaign
  caller without it fails CI.
- Non-campaign callers use an **explicit** legacy contract (`observed_head=None` meaning
  "not a campaign selection"), never silent omission.

**Recovery from abrupt death (adversarial finding).** A session killed abruptly runs neither
`perform_handoff` nor `retire_predecessor`, so the ladder rung never fires for the very scenario it
was added for. That case is covered here, not there: the successor's **first** `next_ready_issue`
call is itself the recovery gate, because a crashed predecessor left `validated_head` behind the
head it died at. No separate startup gate is needed, and saying so is the point — r2 left it
unstated.

## 4a. What "a look" IS — owner ruling 2026-08-02 (pass-3 finding C)

r3 left this undefined, and a reviewer correctly showed that made the whole gate theatre: an agent
could fetch all 24 bodies, stamp every child `still_valid`, and satisfy the gate having validated
nothing. **The owner ruled: name the claim, check it, quote the proof.**

A child may be stamped ONLY when the revalidation produced, per claim, an evidence record:

```
"claims": [ { "kind": "citation" | "cause" | "ac",
              "quoted_from_body": "<the claim, verbatim from the body>",
              "checked_against": "<path>@<sha>" | "<no-file: reasoning>",
              "evidence": "<verbatim quote from `git show origin/main:<path>`, or the explicit "
                          "statement that the claim no longer holds>",
              "verdict": "holds" | "broken" } ]
```

- **`deep`** (the merge touched a cited path, or extraction was `ambiguous`): every claim of all
  three kinds is checked.
- **`quick`** (nothing cited was touched): the **cause** and **ac** claims are checked; citation
  claims are taken as-is. This is the affordability lever, and it is honest — it drops the class of
  check the merge is least likely to have invalidated, never the class #835 failed on.

**A stamp with an empty `claims` list is refused**, fail-closed, by the same type check that
validates `validated_against`. That is what makes "everyone got a look" a fact rather than a claim.
`outcome` is then derived, not asserted: all `holds` → `still_valid`; any `broken` → `body_corrected`
(a correction comment is posted) or `issue_obsolete`.

This is exactly the procedure the 2026-08-02 audit ran by hand — the pass that found 14 of 23
children rotted — so it is a mechanisation of a method already proven on this epic, not a new one.

## 5. The worklist

```python
revalidation_worklist(state, observed_head, extractions, changed_by_child) -> list[WorkItem]
```

Pure. Returns one `WorkItem{number, depth, extraction, from_sha, to_sha}` per eligible child whose
`validated_against != observed_head`. **`observed_head` is an explicit input** (pass-3 adversarial):
r3 omitted it while depending on it, so the function could not have determined staleness or
populated `to_sha` without reading an unstated value. `depth = "deep"` when `extraction != "paths"` **or** the
child's cited paths intersect its changed set; `"quick"` otherwise.

`cited_paths(body) -> (paths, extraction)` scans inline code spans, fenced blocks, markdown link
targets and explicit `path:line`, `path#Lx`, `path@line` forms; strips line/range suffixes;
normalizes separators and `./`; rejects absolute paths and traversal. A candidate counts only if it
**resolves in either endpoint's tree** — supplied as a fact, so `driver_lib` keeps its no-I/O
promise (enforced by the source grep at `tests/hooks/test_driver_state_write_back.py:295-299`).

Ranges are **per child, cumulative**: from that child's own `validated_against` to current head (the
campaign base when never validated). Not last-merge-only — that misses the crash gap, the
skipped-merge gap and the multi-session gap, which are the cases #840 exists for. Changed sets are
computed rename-aware (`--name-status -M`, probed below) and carry **both** old and new paths.

## 6. Refusal and propagation

`next_ready_issue` raises `QueueRevalidationRequired(DriverStateError)` — it does **not** return
`None`. `None` already means "nothing ready", and `resume_prompt_for_state` (`:3046-3059`) collapses
every non-ready disposition into `None` with a docstring saying it means "complete or blocked". A
refusal returning `None` would be reported as **"epic finished"** while the queue is stale. Raising
matches the module's own precedent (`topo_sort_issues` raises `DependencyCycleError`).

Propagation, defined explicitly (pass-2 finding #5 — r2 said only "will be updated", which an
implementation could satisfy while keeping the exact collapse):

- `fresh_session_handoff` catches it and returns `{"outcome": "revalidation_required", "worklist": [...]}`.
- `resume_prompt_for_state` returns a **result object** `{outcome, prompt}` rather than `str | None`,
  so `revalidation_required` is representable. A test asserts `revalidation_required` can never
  produce `None`.
- `_cmd_handoff` (`:3196-3203`) gets its own rc, distinct from a clean `complete`.
- `skills/epic-run/SKILL.md:114-132` handles the new disposition.

## 7. The enforcement precondition and the ladder rung

`perform_handoff`'s signature (`:1251-1263`) carries no driver-state path or repo root, and it is
deliberately shared with `ad-hoc-handoff` (`:3290-3298`, calling it at `:3404-3425`), which has no
campaign state — so a literally unconditional check would break every valid ad-hoc handoff.

**An explicit `campaign_context` parameter** (`{driver_state_path, repo_root, generation}`). Present
⇒ the receipt check runs, fail-closed. Absent ⇒ the ad-hoc case, check does not run. The omission is
made a **static property of the call sites**, asserted by the same source-level test that pins
`observed_head`. `retire_predecessor` already has `position["repo_root"]` and campaign state in
scope; no signature change.

**The rung:** `{"step": "queue_revalidated", "owner": "predecessor", "artifact": ...}`, placed FIRST
in `_MID_CHILD_VERIFICATION_STEPS` — the queue must be revalidated *before* a successor is spawned
to inherit it, exactly the owner's "inserted right before the pane-handoff". The rung **reports**
the precondition's decision; it does not enforce it.

**Why the producer is mandatory**, confirmed at four sites: `perform_handoff` takes
`gate_steps = _predecessor_steps(ladder)` (`:1362`), gating at `:1699` and `:1722`;
`retire_predecessor` takes `_predecessor_steps(mid_child_verification_steps())` (`:2500`), gating at
`:2501`; `evaluate_verifications` is fail-closed on an unreported step (`:1113-1114`, `:1139`).
**A rung with no producer makes every mid-child handoff and every teardown fail closed** — not
inert, a landmine that arms itself the moment #835 ships.

**Freshness:** the producer runs `git -C <repo_root> fetch origin` then
`git -C <repo_root> rev-parse origin/main`, **checking both return codes**. r2 omitted `-C` on the
fetch, which a reviewer correctly flagged as able to update a different checkout or fail outside a
repository while leaving the target stale. A fetch failure is fail-closed: no verdict ⇒ rung fails
⇒ predecessor stays alive.

**Body-mutation detection: CLAIM WITHDRAWN (pass-1 finding F2).** r1 claimed the receipt's
`body_hash` would catch a body edited after revalidation while also forbidding the verifier from
re-fetching bodies; those cannot both hold. Rather than put a network call in the teardown path —
where a blip strands a live predecessor — the guarantee is dropped and no test asserts it.
`body_hash` remains as evidence the *revalidation skill* reads on its next pass, where fetching is
already happening. The reduced claim goes in the changelog, not only here.

## 8. `handoff_pending.queue` — producers and consumer

**Producers (pass-2 finding #3 — r2 named none).** `fresh_session_handoff` and `mid_child_handoff`
each construct the canonical **ordered** queue snapshot under the same state lock that already
guards their disposition, and put it on the disposition. `open_handoff` then copies `queue` through
**only when present**, exactly as `kind`/`position` are (`:615-620`) — mandatory, not stylistic:
`tests/hooks/test_driver_lib.py:618` and `:791` both assert the written record equals
`{"generation", "next_issue", "written_ts"}` exactly, and `:791`'s docstring calls that the #569
contract.

**Consumer.** `handoff_claim` (`:677-712`) validates at claim time under the generation fence it
already enforces. r2 said "head and membership", which a reviewer refuted: that admits a reordered
queue or falsified per-child fields. Corrected — the claim validates the **complete ordered
payload**: exact order (order is load-bearing, `docs/multi-issue-driver.md:241-246`), every declared
field against durable state, plus `validated_head` and `generation`. Any mismatch fails the claim.

Payload per child: `{number, status, validated_against, extraction, depth, outcome, correction_comment}`.
`outcome` is carried because r3 omitted it, which left claim-time validation unable to see an
obsolete child (pass-3 adversarial).

**`queue` is REQUIRED for a campaign disposition, not optional (pass-3 ambiguous finding 1).** r3
made propagation optional at the persistence boundary, so a producer that dropped `queue` would have
`open_handoff` silently write the legacy three-key shape, bypassing ordered-payload validation.

**The discriminator is NOT `kind` — r4 got this wrong and the verifier caught it (finding #2).**
Confirmed at source: `fresh_session_handoff`'s ready disposition is
`{outcome, next_issue, generation, campaign, resume_prompt}` (`driver_lib.py:593-596`) — **no
`kind`** — while only `mid_child_handoff` sets one (`:828`). Yet `fresh_session_handoff` IS a
campaign producer. So keying on `kind` would either break
`tests/hooks/test_driver_lib.py:618`/`:791` (which pin exactly three persisted keys on that very
path) or silently drop the campaign queue and bypass claim-time validation. Both outcomes are wrong.

**Corrected discriminator: an explicit revalidation-enabled marker** — the campaign state carries a
valid `queue_revalidation` object (equivalently, the caller supplied `campaign_context`). So:

- state HAS `queue_revalidation` ⇒ `queue` is mandatory on the disposition; its absence RAISES at
  `open_handoff`.
- state LACKS it (every pre-#840 campaign, and both pinned tests' fixtures) ⇒ the legacy shape is
  written byte-identically, exactly as `:618` and `:791` assert.

This also makes the two pinned tests a genuine regression guard for old states rather than an
obstacle, and it does not depend on a field the campaign path never sets.

## 8a. The two remaining ambiguous findings, resolved

**`cat-file -e` non-zero must distinguish absence from failure (pass-3 ambiguous finding 2).** The
probe showed rc 0 for present and rc 128 with `fatal: path '<p>' does not exist in '<ref>'` for
absent. But rc 128 also covers an invalid ref and a corrupt repository, and a process-launch error
is not an exit code at all. Treating "non-zero" as "absent" would silently downgrade an operational
failure into "this citation does not resolve", which mis-sets `depth` toward `quick` — the wrong
direction. So: **absence is recognised ONLY by rc 128 whose stderr matches the
`does not exist in` form for the exact path queried.** Every other non-zero, and any launch error,
is an operational failure → the child's extraction is `ambiguous` → `depth: deep`. Fail toward more
scrutiny, never less.

**The `resume_prompt_for_state` return-type change needs a complete caller migration (pass-3
ambiguous finding 3).** It is a breaking change from `str | None` to a result object, and r3 named
only two consumers, which a reviewer correctly called unverifiable from the text. The
implementation step is therefore mechanical and stated here: enumerate every caller with
`grep -rn "resume_prompt_for_state" hooks/ skills/ tests/`, migrate each, and add a call-site source
test pinning the enumeration so a new caller on the old contract fails CI. If the enumeration turns
out to be large enough that migration is riskier than the fix, the fallback is to keep the existing
signature and add a **sibling** function for the campaign path — decided at implementation time
against the real caller count, not guessed now.

**`correction_comment` gets a MANDATORY consumer — owner ruling 2026-08-02 (pass-3 finding D).**
Three consecutive reviewer passes raised the same objection, and the third put it plainest: the
design knowingly stamps a child as revalidated while leaving its authoritative body stale, so a
selection path that ignores the resume prompt hands the implementer the same invalid root-cause
claims. r3 deferred this to a follow-up; worse, it *claimed* the follow-up was filed when nothing
had been (pass-3 finding, correct — I wrote it and filed nothing).

The owner ruled it **in scope**. So:

- `next_ready_issue` returning child N is not sufficient on its own. The campaign's child-start path
  MUST load that child's `queue_revalidation.children[N]` record and present its `claims` evidence
  and any `correction_comment` URL to the implementing agent **before implementation begins**.
- **Enforcement lives in the PROMPT BUILDER, not in skill prose (verifier finding #3 — r4 was wrong,
  and its "honest limit" understated the gap).** Confirmed at source: the fresh-session boundary
  ends the skill and launches a new `claude -p` session, and the canonical successor prompt built by
  `_build_resume_prompt` (`driver_lib.py:524-551`) carries no correction presentation at all — the
  string `correction` appears **zero times in the entire module**. Pinning only
  `skills/epic-run/SKILL.md` would let a source test prove the prose step exists while every
  production successor bypassed it. Presentation itself was not assured, not merely unproven.
- Corrected: **`_build_resume_prompt` and `_build_mid_child_resume_prompt` render the child's
  `claims` evidence and any `correction_comment` inline**, so the successor receives them in the
  very prompt that starts it — the one path every fresh successor provably takes. Every production
  child-start path is enumerated and pinned, not just the epic-run step.
- The honest limit, now correctly scoped: the successor is *handed* the corrections mechanically; no
  claim is made that it provably read them. That is a limit on attention, not on delivery — which is
  a materially weaker limit than the one r4 claimed.

## 9. Failure modes

| Failure | Behaviour |
|---|---|
| `observed_head` != `validated_head` | raise → `revalidation_required` |
| any eligible child unstamped or stamped at a different head | raise → `revalidation_required` |
| campaign call site omits `observed_head`/`campaign_context` | CI failure (source-level call-site test) |
| body unparseable, or cites nothing | child still in the worklist, `depth: deep` |
| missing entry in an injected map | raises — never read as "no changes" |
| `git -C <root> fetch` non-zero | no verdict → rung fails → predecessor stays alive |
| receipt absent / `validated_head` stale | rung fails → handoff refused |
| session dies abruptly | successor's first `next_ready_issue` is the recovery gate (§4) |
| ad-hoc handoff (no `campaign_context`) | check does not run; pinned by the call-site test |

## 10. Security implications

No new external input crosses a trust boundary. Issue bodies are already-untrusted text, read only
for path extraction; extracted strings are set-intersected against `git diff` output and never used
to open a file, build a path, or reach a shell. The path regex is anchored and bounded against
catastrophic backtracking on a hostile body. The receipt lives in the campaign state file, already
trusted (it holds `handoff_claim`), and is bound to live git state rather than to its own assertion.
Signing was considered and rejected: code that can write the state file can copy a signing secret.
Forgery resistance comes from independent recomputation.

## Platform / external dependencies

Every entry below was **probed live against this repo on 2026-08-02**, running the exact invocation
the design ships. r2's entries cited skill documentation, which a reviewer correctly refused —
`docs` is not an accepted evidence kind, and a generic runner is not the exact API on the exact
object.

platform_apis:
- api: `git -C <repo_root> fetch origin` then `git -C <repo_root> rev-parse origin/main`
  feasibility: verified via spike — run 2026-08-02 on `/home/rocky00717/rawgentic/projects/rawgentic`:
  fetch rc 0, rev-parse rc 0 printing `3d4e1607d2ccb7178956f9afa05ab0dbb0cbe25d`
  failure: fail-loud
- api: `git -C <repo_root> cat-file -e <ref>:<path>` for endpoint path resolution
  feasibility: verified via spike — 2026-08-02: `origin/main:hooks/context_meter.py` → rc 0;
  `origin/main:hooks/does_not_exist.py` → rc 128 with `fatal: path ... does not exist in 'origin/main'`.
  Present and absent are cleanly distinguishable by exit code
  failure: fail-loud
- api: `git -C <repo_root> show <ref>:<path>` for content retrieval by the skill
  feasibility: verified via spike — 2026-08-02: `git show origin/main:hooks/context_meter.py` → rc 0
  returning real file content. (Distinct from the in-repo `git show --name-only` call sites, which
  do NOT prove `<ref>:<path>` retrieval — the reviewer's point, accepted)
  failure: fail-loud
- api: `git -C <repo_root> diff --name-status -M <base>..<head>` for the rename-aware changed set
  feasibility: verified via spike — 2026-08-02, TWO probes. (1) over `origin/main~1..origin/main`
  → rc 0 with `M`-status rows for five real paths. (2) a reviewer correctly refused (1) as not
  proving rename handling, so a real rename was constructed and probed: `git mv` + commit yields
  `R100<TAB>old_name.py<TAB>new_name.py`, rc 0. **Correction to r3's claim:** the R row appears
  with AND without `-M` (git enables rename detection by default), so `-M` is retained only to be
  independent of a repo-local `diff.renames=false`, not because it is what surfaces renames. The
  load-bearing fact r3 never stated: **an `R` row has THREE tab-separated fields where `M`/`A`/`D`
  rows have two**, so the parser must handle both shapes or it misreads every rename. Both paths go
  into the changed set.
  failure: fail-loud
- api: `gh issue view <n> --json body` for authoritative body retrieval
  feasibility: verified via spike — 2026-08-02 against issue #797, returned its body (used to
  disprove this design's own fixture claim, §12)
  failure: fail-loud
  surface: a failed fetch leaves the child in the worklist and logs; it never stamps
- api: `gh issue comment <n> --body-file <f>` to post a correction comment
  feasibility: verified via spike — 2026-08-02 against issue #840, rc 0, returned
  `https://github.com/3D-Stories/rawgentic/issues/840#issuecomment-5155519800`
  failure: fail-loud
  surface: a failed post leaves the child unstamped, so the gate stays closed on it

## 11. Multi-PR decomposition

r1's split was harmful (PR 1 carried the lock, PR 2 the key; a live campaign would jam between
them). Corrected — neither PR is harmful alone:

- **PR 1 — inert machinery, no gate.** `cited_paths`, `revalidation_worklist`, the
  `queue_revalidation` shape and its type checks, `QueueRevalidationRequired` (defined, not raised),
  and their tests including the AC5 fixtures. Refuses nothing. `Part of #840`.
- **PR 2 — enforcement, atomically.** The head+provenance gate and its propagation, the
  `observe_head` selection wrapper, the ladder rung and its producer, the payload producers +
  consumer, the **mandatory correction consumer at child start**, the `revalidate-children` skill
  with full registration, and the three call-site source tests. `Closes #840`.

**`skills/revalidate-children/SKILL.md` does not exist yet** — verified 2026-08-02:
`ls skills/revalidate-children` → no such directory, and
`git show origin/main:skills/revalidate-children/SKILL.md` → *does not exist in 'origin/main'*. It
is specified in the owner's design comment on #840 and is created by PR 2. Registration spans up to
seven surfaces plus computed count guards and is executed via the `add-skill` workspace skill, which
is authoritative; `revalidate-children` sorts between `peer-consult` and `run-feedback`.

## 12. Test plan

**Red before green (AC6).** The AC2, AC3a, AC3b and AC4a tests are written and run against the
pre-implementation tree, and their observed failures recorded in session notes, before any
implementation lands. This is separate from sabotage-sensitivity; both are demonstrated.

**Mutation-sensitive — test the PRODUCER, never an injected rung result:**

| Test | Must fail when |
|---|---|
| ready child + one unstamped eligible child → raises | the per-child provenance clause is deleted |
| ready child + `observed_head` ahead of `validated_head` → raises | the head comparison is deleted |
| every other predecessor rung passes, receipt missing/stale → teardown refused, predecessor live | either call site's precondition is deleted |
| a valid receipt makes launcher code REPORT the rung passed | the producer is stubbed to a constant |
| an unreported canonical rung stays fail-closed | `evaluate_verifications`' fail-closed branch changes |
| a reordered or field-falsified `handoff_pending.queue` fails the claim | claim-time ordered validation is deleted |
| a stamp carrying an empty `claims` list is refused | the evidence check is deleted |
| tracking head advances while state keeps the old head → selection refuses | `observe_head` is replaced by a cached/state-derived value |
| a campaign disposition missing `queue` raises at `open_handoff` | the mandatory-`queue` branch is deleted |
| a pre-#840 state (no `queue_revalidation`) still writes exactly three keys | the legacy branch regresses |
| an `R` (rename) diff row puts BOTH paths in the changed set | the 3-field row shape is not handled |

**Four rows rewritten because the verifier proved they would stay GREEN under their own sabotage.**
That is precisely the failure this epic exists to eliminate, so they are corrected rather than
quietly kept:

| Row (rewritten) | Why the r4 version was blind | What it asserts now |
|---|---|---|
| `observed_head` provenance | asserted a source test EXISTS, so deleting that test made it vacuous | assert the **dataflow**: patch `observe_head` to a sentinel and prove the sentinel reaches the comparison — a state-derived or cached value fails |
| obsolete child never selected | the independent unstamped-provenance refusal blocked it anyway, so deleting the obsolete check left it green | build a child that is **otherwise fully stamped and current**, differing ONLY by `pending_disposition` — now only the obsolete check can refuse it |
| operational `cat-file` failure | asserted `depth == "deep"`, but a misclassification to `extraction: "none"` **also** yields `deep`, so the wrong path passed | assert `extraction == "ambiguous"` directly, not the depth it happens to imply |
| child start presents corrections | asserted a source test EXISTS, and pinned only epic-run prose | assert the **built prompt string** from `_build_resume_prompt` CONTAINS the claim evidence and the `correction_comment` URL — the artifact the successor actually receives |

The lesson these four share, worth stating because it recurs: **a test that asserts another test
exists, or that asserts a value some other rule already forces, is not mutation-sensitive.** Assert
the artifact, and construct the case so the guard under test is the only thing that can fail it.

**AC5 fixtures — recomputed from the real bodies, because r2 got one wrong.** r2 asserted #797 has
"no resolvable repo paths → `none`". **Disproved at source:** #797 cites
`hooks/context_meter.py:84-85` and `git show origin/main:hooks/context_meter.py` has real content at
exactly those lines, so its true classification is `paths`. Every fixture's expected classification
is recomputed against the captured body and the endpoint trees, and the body hash recorded, before
any is frozen. Candidates: #838 (dense `path:line`), #835 (prose-heavy, wrong root-cause claim —
the case the ruling exists for), #734 (single code-block citation), #763 (`depends on #N` prose plus
paths), plus one genuinely path-free body identified by measurement rather than assumption.

Table-driven extraction cases beyond the fixtures: markdown links, code spans, fenced blocks, line
anchors, renames, deletions, traversal attempts, absolute paths, URLs, glob-like citations,
malformed markdown.
