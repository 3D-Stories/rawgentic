# Design: revalidate-children hardening (#944)

**Issue:** #944 — claim-inventory coverage binding (AC1) + the obsolete-child owner gate (AC2-4).
**Design authority:** `docs/planning/2026-08-05-871-m4-session-continuity-away-mode.md` §3.4, §5
point 10. **Explicitly out of scope** (owner declined 2026-08-05): merging the whole revalidation
boundary into one script, and cheaper passes. This design ships exactly AC1-AC4, nothing broader.

## 1. AC1 — claim-inventory coverage binding

### 1.1 What exists today (verified at `48002ffc`)

- `validate_claims(claims)` (`hooks/driver_lib.py:324`) checks claim SHAPE only: each claim has
  the five required fields, `kind` ∈ `{citation, cause, ac}`, `verdict` ∈ `{holds, broken}`, and
  no field is blank. It never checks that the claims RECORDED cover what the body ASSERTS.
- `build_revalidation_record` (`:896`) is the sanctioned constructor — `skills/revalidate-children/
  SKILL.md` already says "build each record with the constructor, never by hand" — and it is the
  ONLY caller of `validate_revalidation_child`. **Nothing in the test suite or the skill calls it
  today** (`grep -rl build_revalidation_record` finds it only in its own definition and the
  skill's prose), so extending its signature carries zero blast radius on existing tests.
- The skill's own prose (`SKILL.md:145-153`, whitespace-normalized — the sentence wraps across a
  newline) names the gap exactly: *"a `deep` record carrying one `cause` claim is structurally
  valid and makes the child selectable... depth is your obligation, and nothing downstream will
  catch you skimping on it."*

### 1.2 Why the receipt itself cannot check this (corrected — see §1.5 for where it actually lives)

A `queue_revalidation.children[<n>]` record stores `body_hash` (a SHA-256 of the normalized body),
never the body itself — `validate_revalidation_child` has no body to check coverage against, and
neither does `rebuild_receipt` when it carries a prior record forward. **Round 1 concluded from
this that the constructor (`build_revalidation_record`) was therefore THE right enforcement
point, since it is the one call site holding both the body and the claims. Round 2's review
proved that conclusion wrong** (§1.5): the constructor is not on the production write path at
all. What this section's observation actually establishes is narrower and still true — the
RECEIPT's own validator (`validate_revalidation_child`) can never check coverage, because it
never sees a body. Which caller CAN and must is answered in §1.5, not here.

### 1.3 The mechanical inventory

A new pure function, stdlib-only (matches the module's own no-I/O contract):

```python
def extract_claim_inventory(body: str, resolves) -> dict:
    """{"citation": [<path>, ...], "cause": [<item text>, ...], "ac": [<item text>, ...],
    "errors": [<str>, ...]}"""
```

- **`citation`** — **revised after Step-4 review (finding 4, High, confirmed live: `cited_paths`
  drops an unresolved candidate from its returned list entirely — reproduced against the real
  function before accepting the finding).** `cited_paths(body, resolves)[0]` returns only the
  RESOLVED subset, by design, for ITS purpose (deep/quick classification). But the skill's own
  claim schema explicitly anticipates an unresolved citation — `checked_against: "<no-file:
  reasoning>"` is a documented, valid claim form for exactly that case — so dropping unresolved
  candidates from the INVENTORY means the coverage check can never require the claim the schema
  itself already has a slot for. `cited_paths` is refactored to delegate its candidate-building
  loop to a new `_cited_candidates(body, resolves) -> list[str]` (ALL path-shaped candidates,
  resolved or not, same root-level-file filter); `cited_paths` becomes a two-line wrapper filtering
  that list by `known` — byte-identical output, so its own existing tests are unaffected. The
  inventory uses `_cited_candidates` directly: every cited path needs a claim, not only the ones
  that still exist.
- **`ac` and `cause` share ONE extractor, `_extract_section(lines, heading_re)`**, returning
  `(items, unclassified)`. It finds the heading, then walks every line up to the next heading,
  classifying each non-blank line as EITHER a new top-level list item (`^\s{0,3}(?:[-*]|\d+[.)])
  \s+`), a continuation of the current item, OR — **added after Step-4 review round 2 (finding 1,
  High, confirmed by inspection: the round-1 fix only detected a WHOLLY empty extraction, so a
  section mixing a real list with a stray unlisted paragraph silently dropped the paragraph)** —
  **unclassified**, collected into `unclassified` rather than silently discarded. When the section
  has NO list at all, the WHOLE section's non-blank text is one item (§1.3's original fallback,
  unchanged) and `unclassified` is empty (nothing was a candidate item to misclassify). `ac` also
  keeps round 1's fix: a bare "acceptance criteria" phrase found ANYWHERE in the body with zero
  items extracted is itself one more `unclassified` signal.
- **`extract_claim_inventory` fails closed on ANY `unclassified` content, for either kind** —
  returned in `errors`, naming the kind and the offending text. `validate_claim_coverage` refuses
  construction outright when `errors` is non-empty (below), so a section this parser cannot fully
  account for blocks the record rather than silently under-counting it.
- **`cause` — separately revised after Step-4 review round 1 (finding 1, Critical, confirmed:
  presence-only checking cannot close the stated failure mode — the SAME single claim keeps
  satisfying validation no matter how many distinct causes the body asserts; #944's own body is
  the counter-example, a NUMBERED LIST of two causes).** `cause` uses the SAME `_extract_section`
  as `ac`, pointed at a "Problem"/"Root cause"/"Cause" heading: a list means itemized coverage
  exactly like `ac`; free prose means one whole-section item — honest that unstructured narrative
  cannot be split further than "it exists" without real NLP (declined for the reason the skill's
  own prose already declined it: this is not the "much larger change... deliberately not
  attempted" the original gap named, it is the structurally-evident subset of it).

### 1.4 Coverage check and refusal

```python
def missing_claim_coverage(inventory: dict, claims: list, depth: str) -> dict:
    """{"citation": [<uncovered path>...], "cause": [<uncovered item>...], "ac": [<uncovered item>...]}"""

def validate_claim_coverage(body: str, resolves, claims: list, depth: str) -> None:
    """Raises DriverStateError naming every missing item when coverage fails, or when
    `extract_claim_inventory` itself reported an extraction error (fail-closed, §1.3)."""
```

- `deep` requires all three kinds; `quick` requires `cause` + `ac` only — this is not a new rule,
  it is the skill's own existing depth semantics (`SKILL.md:139-143`: quick "take[s] citation
  claims as-is") made enforceable.
- **Maximum bipartite matching, ACROSS ALL THREE KINDS.** Round 1 added one-to-one consumption
  (finding 3, round 1) via a GREEDY first-match. **Round 2 found the greedy version itself
  incorrect (finding 2, High, confirmed by construction: a two-item, two-claim case exists where
  processing order determines whether a complete matching is found)** — greedy consumption can
  report a real coverage gap that does not exist, simply because an earlier item happened to grab
  a claim a later item also needed. Replaced with a standard augmenting-path maximum-matching
  algorithm (stdlib-only, no dependency; the item/claim counts per issue body are small enough
  that the naive O(V·E) algorithm is more than fast enough) over the bipartite graph of "item can
  match claim" edges, computed PER KIND (citation, cause, ac matched independently — an `ac` claim
  can never satisfy a `cause` item regardless of text). An item left unmatched after a genuine
  MAXIMUM matching is missing coverage; this is now provably correct (a complete matching is
  found whenever one exists), not merely "whatever the processing order happened to find."
  - **Citation match rule**: a resolved path matches a claim whose `checked_against` starts with
    `"<path>@"`; an UNRESOLVED path (present in the inventory per the finding-4/round-1 fix above)
    matches a claim whose `quoted_from_body` contains the path text verbatim — `checked_against`
    for an unresolved citation is the generic `"<no-file: reasoning>"` form (`SKILL.md:161`),
    which names no path, so `quoted_from_body` (which should quote the citation from the body) is
    the only field that CAN distinguish which unresolved path a given claim addresses.
  - **AC / cause match rule — tightened after Step-4 review round 2 (finding 3, High, confirmed:
    symmetric substring matching lets a short, generic claim fragment — "the", "error" — satisfy
    ANY item containing that fragment, one-to-one consumption notwithstanding, since enough weak
    claims can still fill every slot).** Substring tolerance is REMOVED. A claim matches an item
    when its `quoted_from_body`, whitespace-normalized and casefolded, EQUALS the item's text under
    the same normalization — enforcing the skill's OWN existing word for this field, "verbatim"
    (`SKILL.md:159`: *"the claim, verbatim from the body"*), rather than the looser
    "clipped-or-reworded-fragment" tolerance the round-1 draft invented. This is not a new
    requirement on the auditor — it is the field's documented meaning, now actually checked.
- The refusal message names every missing item ("acceptance-criteria item(s) have no matching 'ac'
  claim: …", "citation claims missing for cited path(s): …", "cause item(s) have no matching
  'cause' claim: …") — AC1's "with the missing claims named." An `extract_claim_inventory`
  extraction error (§1.3's fail-closed case) is refused with its own message BEFORE coverage is
  even computed — an ungradeable inventory has nothing to bind claims to.

### 1.5 Where coverage is actually enforced (rewritten after Step-4 review round 2, finding 5)

**Round 1's placement was wrong, and the review proved it by reading further than I had.**
`build_revalidation_record` is not merely uncalled by any TEST (§1.1's true observation) — it is
uncalled by the PRODUCTION CLI PATH too. `_cmd_rebuild_receipt` (`hooks/launcher_lib.py:4873`,
invoked as `launcher_lib.py rebuild-receipt --audited audited.json` — the skill's own step 7,
the ONLY way a real campaign's receipt is ever written) reads `--audited` as **raw JSON dicts**
and passes them straight to `driver_lib.rebuild_receipt`, which calls `validate_revalidation_child`
for STRUCTURAL checks only. **Confirmed by reading `_cmd_rebuild_receipt` end to end
(`:4899-4953`): no call to `build_revalidation_record`, no call to `validate_claim_coverage`,
anywhere in that function.** An agent following the skill's OWN documented step 7 never goes
through the constructor at all in the real flow — only in a test that calls it directly. Shipping
AC1 with the check ONLY in the constructor would enforce nothing in production, exactly as the
review said.

**The fix moves enforcement to `_cmd_rebuild_receipt` itself**, the one place real records are
actually written:

- A new required-when-`--audited`-is-non-empty argument, `--bodies <file>`: JSON
  `{"<issue number str>": "<raw issue body, verbatim>"}`. **Revised after WF5's plan review
  (finding 3, High/security, confirmed by re-reading Task 6's own schema against itself): the
  original draft ALSO threaded a caller-supplied `resolves` list through `--bodies`, verified by
  NOTHING — only `body` was hash-checked, so a fabricated or stale `resolves` (which paths "exist")
  could change citation-coverage matching with no integrity check catching it.** `resolves` is
  REMOVED from the file entirely. `_cmd_rebuild_receipt` already has git/subprocess access (it
  already calls `observe_head`), so it DERIVES `resolves` itself: extract candidate citation paths
  from `body` via `_cited_candidates` (no `resolves` needed for candidate extraction, only for
  resolution), then probe each candidate with `git -C <project_root> cat-file -e
  <sha>:<path>` against BOTH `record["from_sha"]` and `record["to_sha"]` (either counts as
  resolved — the skill's own documented meaning of `resolves`, "paths that exist in EITHER
  endpoint tree"). No caller-supplied resolution data exists to fabricate, closing the gap
  completely rather than adding a second thing to verify. `--audited` entries with no matching
  `--bodies` entry refuse (rc 2, naming the missing issue) before anything is written.
- For each `(number, record)` pair, BEFORE calling `driver_lib.rebuild_receipt`:
  1. verify `hashlib.sha256(driver_lib.normalize_issue_body(body)).hexdigest() ==
     record["body_hash"]` — an integrity check that was free once both values are in hand, closing
     a body/hash swap that nothing previously checked;
  2. derive `resolves` via the git probes above;
  3. call `driver_lib.validate_claim_coverage(body, resolves, record["claims"],
     record["depth"])`, skipped when `record.get("pending_disposition")` is set (§1.4's rule,
     unchanged) — a coverage or integrity failure refuses the WHOLE command (rc 2, naming the
     child and the missing items) before `rebuild_receipt` is ever called, so nothing partial is
     written.
- `build_revalidation_record` KEEPS its own `resolves` parameter and coverage call — not as "the"
  enforcement point any more, but as a correct, unit-testable primitive for anything that DOES go
  through it (a future programmatic caller, or a test exercising the coverage logic in isolation
  without a live CLI round-trip). Demoting its role, not deleting the check, is the honest
  correction: the logic was right, its placement was not the only place that needed it.
- `skills/revalidate-children/SKILL.md` step 7 gains one line: build `bodies.json` alongside
  `audited.json` and pass `--bodies bodies.json` to the `rebuild-receipt` command.

### 1.6 Skill and doc prose

`skills/revalidate-children/SKILL.md:145-153` — the "depth is your obligation" paragraph — is
rewritten to describe the mechanical check and the new `rebuild-receipt --bodies` requirement
(§1.5), replacing "a much larger change than this machinery carries, and deliberately not attempted here"
(now attempted, narrowly, by #944).

## 2. AC2-4 — the obsolete-child owner gate

### 2.1 What already exists, half-built

- `validate_revalidation_child` already enforces `pending_disposition` and `outcome` as MUTUALLY
  EXCLUSIVE (`:368-380`) — a pending-disposition record is never stamped with a real outcome.
- `_DISPOSED_STATUSES = frozenset({"deferred", "abandoned", "merged"})` (`:295`) is **already**
  documented as "statuses that SETTLE a pending owner decision" — this vocabulary exists
  specifically so `rebuild_receipt` can tell a settled disposition from a live one. **The write-back
  command AC3 asks for already exists**: `record-child-outcome --issue N --status
  deferred|abandoned|merged`, already lock-safe via `_locked_state_update`. #944 does not need a
  new command — it needs to make selection actually REFUSE while the disposition is live, which
  is the piece `docs/multi-issue-driver.md:342-346` explicitly predicts and defers: *"When #848
  lands, this bullet returns: only the owner clears it... because choosing between `deferred` and
  `abandoned` is deliberately not a machine's decision."*
- `next_ready_issue` (`:1456`) — the selection function — has NO pending-disposition check at all
  today; an obsolete-marked child is `queued` and selectable like any other.

### 2.2 The refusal, at the selection boundary

A new exception, mirroring `QueueRevalidationRequired`'s existing pattern exactly (a distinct type
so the CLI can map it to its OWN return code):

```python
class ObsoletePendingChild(DriverStateError):
    def __init__(self, message, *, issue):
        super().__init__(message)
        self.issue = issue
```

**Revised after Step-4 review round 2 (finding 4, High, confirmed by re-reading the round-1 loop
against its own behavior): raising at the FIRST ready-but-obsolete-pending candidate stops the
ENTIRE scan, even when a completely unrelated, independent child later in the queue is ALSO ready
and has nothing to do with the obsolete one.** `has_pending_dependents` (below) answers "does
something depend on the obsolete child", but round 1's loop never asked the more basic question
first: "is there other real work regardless." The corrected loop does not stop at the first
obsolete-pending candidate — it remembers the FIRST one seen (for an actionable message) and
CONTINUES scanning:

```python
first_obsolete = None
for issue in issues:
    if effective[issue["number"]] != "queued":
        continue
    deps = _in_queue_deps(issue, numset)
    if not all(effective[d] in satisfied for d in deps):
        continue
    pending = _child_pending_disposition(state, issue["number"])
    if pending is not None:
        if first_obsolete is None:
            first_obsolete = issue["number"]
        continue                      # do not stop the whole scan for one child
    return issue["number"]
if first_obsolete is not None:
    raise ObsoletePendingChild(..., issue=first_obsolete)
return None
```

`ObsoletePendingChild` now fires ONLY when the scan reaches the end with no genuinely selectable
candidate — i.e. every remaining ready child is obsolete-pending, or nothing is ready at all. A
`deferred`/`abandoned`/`merged` child never reaches the check either way — the loop's existing
`if effective[...] != "queued": continue` already skips it, which is exactly what makes the
existing write-back command (§2.5) sufficient to clear it on the NEXT selection call, with no new
"exclude" mechanism needed.

`fresh_session_handoff` gains one more `except` arm alongside its existing
`QueueRevalidationRequired` handling, returning a new explicit disposition
(`{"outcome": "obsolete_pending", "issue": N, "has_pending_dependents": <bool>}`) rather than
letting it collapse into `blocked` — the same "never let a recoverable refusal read as generic"
principle §840 already established for `revalidation_required`.

```python
def has_pending_dependents(state: dict, issue_number: int) -> bool:
    """True when some NOT-YET-TERMINAL child depends on issue_number."""
```

Pure, reuses the existing `_in_queue_deps`/`TERMINAL_STATUSES` machinery — this is the mechanical
half of design §5 point 10 ("a sleeping run defers an obsolete-marked child only when no remaining
child depends on it").

### 2.3 Return-code composition (the freshest correction comment's constraint)

As of `1cc353f4` the handout path already carries rc 6 (revalidation), 7 (claim-refused, handoff
only), 8/9 (sweep), 10 (in-flight/session-path). **The next free code is 11.** Per the same
comment's ordering principle — *"an owner-gated refusal is NOT self-clearing... putting the
non-clearable gate first would mask a refusal the run could have cleared itself"* — the CLI checks
every SELF-clearing gate before surfacing the owner gate:

```
revalidation (6, unavoidably first — it runs before selection)
  → sweep (8/9)
  → in-flight (10, handoff only)
  → obsolete-pending (11)
  → ready (0)
```

Both `_cmd_next_child` and `_cmd_handoff` are restructured so the sweep gate (and, for `handoff`,
the in-flight gate) run whenever the disposition outcome is `"ready"` **or** `"obsolete_pending"`
— not only `"ready"` as today — so a caller who hasn't yet declared in-flight work, or hasn't
swept the last boundary, sees THAT refusal first and clears it before ever learning about the
obsolete child underneath it.

### 2.4 Supervision routing (AC2's "supervision-aware")

`hooks/supervision_lib.py` (#943 Part A, already shipped) supplies exactly what this needs —
`read_state` + `evaluate_workspace` → a `SupervisionView.state` ∈ `{attended, away, sleeping,
attended-overdue}` — and its own docstring says the CAMPAIGN-scoped evaluator is #947's, not
required here: this gate only needs the WORKSPACE-global view, so **#944 has no dependency on
#947**, even though #947 is queued after it.

`launcher_lib.py` gains a small workspace-root resolver (`_find_workspace_root`, the same walk-up
idiom `context_meter.find_workspace` already uses — reimplemented locally rather than
cross-imported, matching the existing precedent of two independent copies of this idiom) and a
lazy `_supervision_lib()` import mirroring `_driver_lib()`. **Round 2, finding 7 (Medium,
ambiguous) asked what happens on a missing/malformed supervision state or a failed workspace
resolution — resolved from the already-read code (`supervision_lib.py:156` "NEVER raises";
`evaluate_workspace`'s `load_status != "valid"` branch, `:250-261`, already returns a safe
default `SupervisionView(state="attended", ...)`), not escalated.** `_find_workspace_root`
returning `None` (no workspace found) is handled the same way `read_state` already handles it —
`read_state(None)` returns `Loaded({}, "absent")`, which `evaluate_workspace` turns into the same
safe `attended` default. `_supervision_view_for` cannot crash or degrade into a generic error;
it degrades to the SAME conservative default the library already guarantees. This was a gap in
the design doc's PROSE, not in the underlying code — fixed here by stating it. The rc-11 refusal
text then branches on `view.state`:

**Scope correction after Step-4 review round 2 (finding 6, High — this PARTIALLY OVERTURNS D256's
round-1 resolution, and says so plainly rather than quietly).** Round 1 (finding 5) asked "who
executes the sleeping/no-dependents defer-and-continue sequence", and D256 answered "the same
orchestrating session that already executes every other rc-N remedy in this file." Round 2 pushed
back, correctly: the OTHER refusals (rc 6, 8, 10) name a SINGLE remedy command for a HUMAN or the
NEXT skill invocation to run — they never claim the run then AUTOMATICALLY continues unattended.
"Defer, post the ERROR comment, run the write-back, retry selection, and continue" is a claim of
FOUR chained actions happening with no human and no cited orchestrator code tying them together —
that is a materially stronger claim than the other refusal codes make, and D256 was wrong to treat
it as the same shape. **Corrected scope: #944 ships the MECHANICAL REFUSAL ONLY, in every
supervision state, with no automatic continuation of any kind.** rc 11 always refuses; only the
printed text differs:

- **`attended` / `away` / `attended-overdue`** → "ask the owner" — names the write-back command.
  The CLI never calls `AskUserQuestion` itself (it has no such tool); it only names the
  recommended action, exactly as `_sweep_refusal_text` never runs `gh` itself.
- **`sleeping`, `has_pending_dependents == False`** → informational text naming the RECOMMENDED
  action (post the ERROR-comment-protocol blocker, run the write-back, retry) — but #944 does
  not execute any of it, and the design no longer claims it happens automatically. Building that
  automation (WHO calls it, when, with what retry/verification) is explicitly deferred, most
  naturally to #947 (which already owns "blocker routing with route_for and the transport
  capability gate") or a dedicated follow-up — not silently absorbed into #944's scope.
- **`sleeping`, `has_pending_dependents == True`** → informational text naming PARK as the
  recommended action (nothing else can advance past this one; a human decision is needed). Same
  scope limit: the CLI refuses and prints guidance, it does not write `campaign_wait` (still a
  validator-only field, `_campaign_wait_errors`, `:3231`, no writer — #947's territory) or take
  any other action.

The practical effect for THIS issue: rc 11 is always a plain refusal. The message differs by
supervision state so that a FUTURE consumer (human or automation) has the right recommendation,
but #944 itself performs no multi-step recovery, sleeping or otherwise. This is the honest scope
the reviewer's second pass forced — better caught now than shipped as an overclaim.

### 2.5 AC3 — recoverability

The remedy is `record-child-outcome --issue N --status deferred|abandoned|merged` — already
lock-safe, already existing. Every rc-11 message names it verbatim (the #840 failure mode this AC
guards against: a gate whose printed remedy cannot actually clear it). The regression test is
mechanical: build a state with a pending-disposition `queued` child, confirm `next_ready_issue`
raises, call `record_child_outcome(..., "deferred")`, confirm the NEXT call selects past it cleanly.

### 2.6 Closing the preflight/commit race (adopted from peer consult, see §4)

`_cmd_handoff` computes `disposition` (and therefore the obsolete-pending check) BEFORE taking the
state lock, then commits under the lock via `_open_and_claim` → `child_boundary_precondition(s,
next_issue)`, which re-checks `status == "queued"` but nothing about `pending_disposition`. A
concurrent session can rebuild the receipt and mark `next_issue` pending-disposition (its `status`
field is untouched by that — `pending_disposition` lives only in the receipt) in the window
between the two. `child_boundary_precondition` gains one more check, under the SAME lock, right
after its existing `status == "queued"` check: `_child_pending_disposition(state, next_issue) is
not None` → `(False, "next_child_pending_disposition")`. `_cmd_next_child` needs no equivalent —
it never claims or mutates anything, so it carries no commit step for this race to hit.

**Revised after Step-4 review (finding 6, Medium, confirmed: the original draft named the new
precondition reason but never said what `_open_and_claim`/`_cmd_handoff` DO with it).** Without an
explicit mapping, `next_child_pending_disposition` would fall through to whatever generic failure
`_open_and_claim`'s other precondition reasons already produce (`gate["verdict"] = "precondition"`
surfaces as a plain refusal today) — losing the rc-11 shape and the write-back remedy the moment it
matters most (an ACTUAL race, not merely a preflight-time read). `_cmd_handoff` gains one more
branch on `gate.get("reason")` immediately after `_open_and_claim` returns a precondition failure:
when the reason is `"next_child_pending_disposition"`, emit the IDENTICAL `obsolete_pending`
JSON payload and rc-11 refusal text §2.4 already defines for the preflight path (recomputing
`has_pending_dependents` and the supervision view under the now-current state), rather than the
generic precondition-failure branch. The regression test for §2.5's race (Task 5, plan below) is
extended to assert the FULL rc-11 payload, not just the bare precondition tuple.

### 2.7 Doc updates (drift-guard test already pins this section)

`docs/multi-issue-driver.md:288-293` and `:342-346` currently state the gate was CUT and is
"being rebuilt in #848" — `tests/hooks/test_multi_issue_driver.py::test_doc_documents_queue_
revalidation_840` pins `"That clause was CUT (#848)"` verbatim. This is the SAME pin-inversion
pattern the #848-cut PR itself used (inverting an earlier "the gate is LIVE" pin) — the test is
updated in the same commit to pin the RESTORED prose (crediting #944 as the #848 rebuild), never
left pointing at prose that is now false.

## 3. Files touched

- `hooks/driver_lib.py` — `_cited_candidates` (extracted from `cited_paths`), `extract_claim_
  inventory`, `_extract_list_section`, `_section_has_content`, `missing_claim_coverage`,
  `validate_claim_coverage`, `ObsoletePendingChild`, `has_pending_dependents`,
  `_child_pending_disposition`; `build_revalidation_record` gains `resolves`; `next_ready_issue`
  gains the pending-disposition raise; `child_boundary_precondition` gains the locked recheck
  (§2.6).
- `hooks/launcher_lib.py` — `_find_workspace_root`, `_supervision_lib`, `_supervision_view_for`,
  `_obsolete_pending_refusal_text`, `OBSOLETE_PENDING_RC = 11`; `_cmd_next_child`/`_cmd_handoff`
  reordered per §2.3; `_cmd_handoff` gains the locked-recheck-failure branch (§2.6).
- `skills/revalidate-children/SKILL.md` — §1.5.
- `docs/multi-issue-driver.md` — §2.6.
- `tests/hooks/test_driver_lib.py` or a new `tests/hooks/test_claim_inventory.py` — inventory
  extraction, coverage matching, constructor refusal/acceptance, `pending_disposition` skip.
- `tests/hooks/test_launcher_lib.py` — rc-11 composition ordering, all three supervision branches,
  the write-back regression test.
- `tests/hooks/test_multi_issue_driver.py` — updated doc pins (§2.6).

## 4. Peer consult (gpt-5.6-sol, backend gpt, 2026-08-06)

Independent proposal requested at design time (blind — drafted before reading it), for both AC1
and AC2. Disposition per finding, D179-honest:

**Adopted:**
- **The preflight/locked-commit race (§2.6).** The peer's sharpest finding: a CLI-only owner check
  is bypassable by an internal caller, so the marker must be enforced again at the locked handout
  commit, not only at the earlier preflight read. My original draft had the gate only in
  `next_ready_issue` (read at disposition time); this closes the TOCTOU window in
  `child_boundary_precondition` under the same lock `_open_and_claim` already holds.
- **`attended-overdue` behaves as `attended`**, and **`away` is NOT folded into `sleeping`** via
  `nobody_to_ask` — the peer stated the reasoning more sharply than my own draft had ("this
  issue's required policy distinguishes only `sleeping` for automatic deferral"), confirming a
  choice I'd made but not yet argued explicitly.

**Declined, with reasons:**
- **A full claim-ID + digest-bound inventory** (`{id, kind, locator, text_digest}` on every claim,
  stored inventory versioned separately from the receipt). More correct against claim
  re-ordering/rebinding than substring matching, but it changes the CLAIM schema itself
  (`_CLAIM_REQUIRED`) — a bigger, rippling change than this "note"-priority issue's own AC1 asks
  for ("with the missing claims named", not "matched by stable ID"), and the owner declined
  "merging the whole boundary into one script" for this session. Flagged here as a real follow-up
  if AC-item churn in practice turns out to make substring matching unreliable — not attempted now.
- **A discriminated result type replacing `next_ready_issue`'s `int | None`** (`Ready` /
  `OwnerDispositionRequired` / `SafeSleepingDeferral` / `ParkedOnObsoleteDependency` /
  `NoReadyChild`). Raising `ObsoletePendingChild` achieves the same branching at the call site with
  a much smaller diff, and matches this module's OWN existing precedent
  (`QueueRevalidationRequired` is exception-based, not a return-type wrapper) — adopting the
  peer's shape here would be LESS consistent with the surrounding code, not more.
- **A durable outbox for the ERROR-comment before the state mutation is considered final.** Real
  concern, but the durable-run-record/transactional-outbox work is #888's territory (already
  shipped, persist-before-merge ordering) and #947's (departure preflight, blocker routing) —
  building a NEW outbox here for one gate would be exactly the over-reach the owner declined.
- **Transitive (not just direct) dependent checking.** `has_pending_dependents` checks direct
  `depends_on` edges only — but that already suffices for this yes/no question: if child B
  directly depends on the obsolete child C, B alone is already blocked, which is sufficient reason
  to park; there is no need to also enumerate C's further downstream dependents to justify the
  same park decision. This also matches `next_ready_issue`'s own existing dependency model, which
  is direct-edge only throughout (satisfaction propagates by repeated re-selection, not an explicit
  transitive closure).
- **A new `child disposition resolve --decision keep|rescope|close` command.** Sidestepped
  entirely: the write-back reuses the EXISTING, already-lock-safe `record-child-outcome --status
  deferred|abandoned|merged` (§2.5) rather than inventing new decision vocabulary — which also
  sidesteps the peer's own flagged risk that "rescope is underspecified and can alter dependency
  identity."

## 5. Step-4 adversarial-on-design review, round 1 (gpt-5.6-sol, backend gpt, 2026-08-06)

Dispatched against the round-1 draft (the one §4 describes). 6 findings: 1 Critical, 4 High, 1
Medium. Loop-back: one `design`-source loop-back consumed (`design_loopback_count` 0→1,
`global_loopback_total` 0→1, both within budget). Volume check did not trigger (1 Critical < 5,
4 High < 5 — the per-tier independent thresholds in `VOLUME_THRESHOLDS`). Disposition, all
confirmed against real evidence before acting (never applied on the reviewer's say-so alone):

| # | Severity | Claim | Confirmed how | Disposition |
|---|---|---|---|---|
| 1 | Critical | Presence-only `cause` check doesn't close the stated failure mode for multiple distinct causes | Re-read §1.3 against AC1's own stated goal; #944's own body IS a counter-example (a numbered two-cause list) | **Fixed** — §1.3 `cause` now itemizes via `_extract_list_section`, same as `ac` |
| 2 | High | No fail-closed behavior for an unrecognized AC heading/structure — silent vacuous pass | Re-read §1.3's extraction logic; confirmed no fallback existed | **Fixed** — §1.3 now detects the bare phrase "acceptance criteria" as a structural mismatch signal and refuses (fail-closed) rather than passing vacuously |
| 3 | High | Independent substring matching lets one claim spuriously cover multiple inventory items | Re-read §1.4's original matching rule; confirmed no one-to-one constraint existed | **Fixed** — §1.4 now does greedy one-to-one bipartite matching across all three kinds |
| 4 | High | `cited_paths` silently drops unresolved candidates, so an unresolved citation never needs a claim | **Reproduced live**: `cited_paths(body_with_missing_file, resolves)` returns only the resolved path, confirmed by execution | **Fixed** — §1.3/§1.4: new `_cited_candidates` returns ALL candidates; unresolved paths matched via `quoted_from_body` (the schema's own `<no-file: reasoning>` form names no path) |
| 5 | High, ambiguous | No named executor for the sleeping-mode defer-and-continue action | Read WF2's own `<error-protocol>` block and the existing rc-6/8/10 refusal-text pattern in `launcher_lib.py` — same shape, same non-answer, already accepted throughout this file | **Resolved from code, not escalated** (D256) — reclassified spec-tightening; §2.4 prose tightened to name the existing executor explicitly |
| 6 | Medium | The locked-recheck failure path isn't mapped to the rc-11 payload | Re-read §2.6; confirmed no mapping was specified | **Fixed** — §2.6 now specifies the exact branch in `_cmd_handoff` |

A fresh adversarial-on-design pass against this revised draft is the next action (Step 4, round 2)
before Step 5 finalizes.

## 6. Step-4 adversarial-on-design review, round 2 (gpt-5.6-sol, backend gpt, 2026-08-06)

Dispatched against the round-1-revised draft. 6 High findings + 1 Medium (ambiguous) — the 6 High
count MEETS `VOLUME_THRESHOLDS.High = 5`, triggering the volume loop-back. A second `design`-source
loop-back was consumed (`design_loopback_count` 1→2, AT the per-source cap;
`global_loopback_total` 1→2, still under the global cap of 3). Per the #798 carve-out (design
source cap reached, global cap NOT reached), this gate CLOSES budget-exhausted after applying
this round's findings — it does not loop to a third design/review round. Finding 7's ambiguity
was resolved from code first, clearing the breaker to `clear` before the close.

| # | Severity | Claim | Confirmed how | Disposition |
|---|---|---|---|---|
| 1 | High | Round-1's fail-closed AC check only catches a WHOLLY empty extraction, not a section mixing a real list with unclassified content | Re-read §1.3's round-1 fix against the finding's own mixed-format example | **Fixed** — §1.3 now flags ANY unclassified non-blank line in the section, not only a zero-item result |
| 2 | High | Greedy one-to-one matching is order-dependent and can report a false gap when a complete matching exists | Confirmed by construction: reproduced the exact two-item/two-claim case in reasoning before accepting | **Fixed** — §1.4 replaced with a standard maximum-bipartite-matching algorithm (augmenting paths), stdlib-only |
| 3 | High | Symmetric substring matching lets a short/generic claim fragment satisfy any item containing it | Re-read §1.4's original match rule; confirmed no minimum-specificity requirement existed | **Fixed** — §1.4 AC/cause matching now requires EXACT normalized equality, enforcing the skill's own documented "verbatim" meaning for `quoted_from_body` rather than a looser tolerance I had invented |
| 4 | High | `next_ready_issue` raising at the FIRST obsolete-pending candidate can park the whole run while an unrelated ready child exists later in queue order | Re-read the round-1 loop against its own single-track behavior; confirmed the gap | **Fixed** — §2.2: the loop now continues scanning past an obsolete-pending candidate, raising only when NO other candidate is selectable |
| 5 | High | `build_revalidation_record` (the round-1 enforcement point) has no caller on the PRODUCTION path — `_cmd_rebuild_receipt` reads raw JSON directly | **Reproduced by reading `_cmd_rebuild_receipt` end to end** (`hooks/launcher_lib.py:4899-4953`): confirmed no call to the constructor or the coverage check anywhere in that function | **Fixed** — §1.5 (new): enforcement moves to `_cmd_rebuild_receipt` via a new `--bodies` argument, checked before `rebuild_receipt` is called; the constructor keeps its check as a demoted, still-correct primitive |
| 6 | High | The sleeping-mode "defer and continue" claim has no named executor for its four chained actions — a materially stronger claim than the other refusal codes make | Re-read D256's reasoning against the actual claim being defended; confirmed the analogy was imprecise | **Fixed, and D256 partially overturned (D257)** — §2.4 now ships the mechanical refusal only, in every supervision state, with no claim of automatic continuation |
| 7 | Medium, ambiguous | Undefined behavior on a missing/malformed supervision state or failed workspace resolution | Re-read `supervision_lib.py:156` ("NEVER raises") and `:250-261` (safe-default branch), already read this session | **Resolved from code** — the underlying library already guarantees a safe default; §2.4 now states this explicitly (a documentation gap, not a code gap) |

Gate closed via `plan_lib.close_design_gate` immediately after this table — see the Step 16
run-record's top-level `extra` for the `design_gate_close` entry this produces.

## 7. WF5 Step-6 plan review (gpt-5.6-sol, backend gpt, 2026-08-06)

Dispatched against the finalized implementation plan (`.rawgentic-impl-plan-944.md`). 3 High + 2
Medium (one ambiguous). None consumed a design loop-back — the source is exhausted (2/2) and D258
records the judgment: each fix is a refinement within the design's already-reviewed architecture,
not a change to it.

| # | Severity | Claim | Confirmed how | Disposition |
|---|---|---|---|---|
| 1 | High | Section absence returns an empty inventory with no error, contradicting fail-closed | Re-read Task 2's AND Task 3's RED lines together — the finding evaluated Task 2 alone | **Refuted** — Tasks 2 (genuine absence → no error, by design) and 3 (phrase-mentioned-but-unparseable → `errors`, fail-closed) already draw exactly this distinction; the plan is tightened to state the cross-task connection explicitly so it reads as one rule, not two isolated bullets |
| 2 | High | No task creates `--bodies`, migrates callers, or proves a real campaign can produce it | The skill (an LLM agent following prose) has no code "caller" to migrate beyond its own SKILL.md; the underlying concern (an end-to-end, skill-shaped test) is fair | **Applied as a plan refinement** — Task 6's tests gain a skill-shaped fixture (built the way the skill's own procedure would build it); Task 12's skill prose gains a fully worked example, not just a description |
| 3 | High, security | `--bodies`'s `resolves` field is never integrity-checked; a fabricated list changes citation coverage with no hash mismatch to catch it | Re-read Task 6's schema against its own stated integrity check — confirmed `resolves` was outside it entirely | **Fixed, §1.5 revised (D258)** — `resolves` is REMOVED from `--bodies`; `_cmd_rebuild_receipt` derives it itself via git probes against both endpoint commits, closing the gap by eliminating the untrusted input rather than adding a second check |
| 4 | Medium, ambiguous | "Diffed against the baseline" has no defined pass/fail rule | Answerable from WF2's own already-read contract (`<test-run-discipline>`: the Step-9 gate's regression rule) | **Resolved from the contract** — Verification strategy now states the rule explicitly: zero new failures, zero newly-skipped tests, every difference enumerated and dispositioned |
| 5 | Medium | No drift-guard test for the skill's `--bodies` prose | Re-read Task 12; confirmed no such test was planned | **Applied** — Task 12/14 gain a test asserting the skill documents the `--bodies` step and its schema |

## Platform / external dependencies

platform_apis: none — everything above is in-repo state (a driver-state JSON file, an existing
workspace-scoped state file already read elsewhere in this repo) and stdlib. No new external
service, API, or platform surface is introduced.

## Security implications

None material. The gate only reads existing on-disk state (driver-state, supervision-state) that
other parts of this codebase already read the same way; it adds no new write path beyond the
already-existing, already-tested `record_child_outcome`.
