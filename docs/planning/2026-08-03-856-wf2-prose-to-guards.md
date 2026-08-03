# #856 — WF2 prose: freeze the obligations, then move them into guards

**Issue:** #856 (`chore(wf2)`), epic #756. **Date:** 2026-08-03. **Author:** WF2 run
`wf2-856-29379605`. **Status:** design, for Step 4 critique.

## 1. What is actually wrong

WF2's enforceable directives exist **only as prose an orchestrator must remember**. Measured this
session (not quoted from the issue):

| surface | lines | bytes |
|---|---|---|
| `skills/implement-feature/SKILL.md` | 584 | 60,969 |
| `references/steps.md` | 2,041 | 182,834 |
| `references/run-record.md` | 287 | 22,322 |
| `references/headless.md` | 272 | 14,864 |
| `references/whole-issue-delegation.md` | 170 | 8,925 |
| `references/state-and-resume.md` | 105 | 8,355 |
| `references/quality-bar.md` | 64 | 3,360 |
| **corpus total** | **3,529** | **301,635** |

≈ **75,400 tokens** at 4 bytes/token. Probed live: `skill_corpus('implement-feature')` returns
exactly 301,635 bytes / 3,529 lines (`tests/corpus.py:22`).

**The issue's directive count is a fair proxy — I tried to refute it and failed.** I suspected the
`MUST|NEVER|ALWAYS|REQUIRED|mandatory|do NOT|never` sweep was inflated by lowercase `never` in
descriptive prose. Measured at clause level: **337 clauses carry a normative modal, and only 35 are
"reassurance"** (`never` + blocks/gates/fails, e.g. "advisory — never blocks"). That leaves ~302
obligation-bearing clauses corpus-wide, ~254 over SKILL.md + steps.md. The filed figure of 267
stands. My initial "it's inflated ~5×" read came from comparing ALL-CAPS-only (47) against the full
regex (316) and is withdrawn.

### 1.1 The finding that reframes the issue

AC2 asks that "every enforceable directive has a guard". The expensive reading — write ~250 new
guards — is **wrong, because most of the guards already exist and are simply never called.**

**All figures below are mechanically derived** (AST parse of `hooks/plan_lib.py` for public
module-level functions; regex over `skill_corpus('implement-feature')` for prose surfaces). An earlier
hand-written version of this table was wrong on five anchors and on one headline claim; a reviewer
caught it, and the corrected numbers make the case *stronger*, not weaker. Distinguish four different
things that were previously conflated:

| measure | count |
|---|---|
| public module-level functions in `plan_lib.py` | **48** |
| of those, named somewhere in the WF2 corpus | **48** (all of them) |
| runnable `python3 hooks/plan_lib.py <subcmd>` invocations in the corpus | **2** (`assert-pr-body`, `close-design-gate`) |
| runnable `python3 -c` shims importing `plan_lib` | **4** |
| symbols reachable through those runnable sites | **7** |
| **symbols named in prose but reachable from no runnable site** | **41 of 48** |
| `plan_lib.` mentions in the corpus | **99** (87 in `SKILL.md` + `steps.md` alone) |

So the gap is not ten functions — it is **41 of 48**. Representative cases, with verified anchors:

| guard (verified line) | described at | what the prose asks a reader to do |
|---|---|---|
| `plan_lib.py:1177` `assert_review_coverage` | `steps.md:1047`,`:1121`; +2 refs | named 4×, reachable from no runnable site |
| `plan_lib.py:1243` `assert_no_unresolved_high_deferrals` | `steps.md:1352`,`:1164` | the whole Step-11 exit gate is an instruction |
| `plan_lib.py:611` `validate_parallel_groups` | `steps.md:806`,`:1054` | "after decomposition, call …" |
| `plan_lib.py:729`/`:737` `compute_risk_ratio`/`check_ratio_band` | `steps.md:825-829` | 4 lines explaining a **return shape** a CLI would hide |
| `plan_lib.py:122` `estimate_agents` | `steps.md:435`,`:440`,`:846` | "derived via … — never hard-coded", then no invocation |
| `plan_lib.py:1351`/`:1601` `compute_finding_key`/`strip_reopens` | `steps.md:730` | ~200 words describing a hashing algorithm the library implements |
| `plan_lib.py:2145`/`:2190` `classify_branch_protection`/`branch_protection_line` | `steps.md:299` | classification rules restated beside the call |
| `plan_lib.py:841` `should_run_diff_review` | `steps.md:1302` | "pure, tested; it raises on str/None, so pass a real list" |
| `plan_lib.py:2212` `quarantine_protection_contradiction` | `steps.md:1738` | "call … a non-None message means …" |

**Correction, and it matters for how PR 2 is sized:** `assert_feasibility_declared`
(`plan_lib.py:516`) **is** reachable — `steps.md:622` carries a runnable `python3 -c` shim, which this
run executed. It is therefore *not* an example of the defect and has been removed from the list. That
correction cuts the other way too: a reachable shim is still only *reachable*, not *unavoidable* —
Step 4 runs it because the prose asks, which is precisely the voluntary-enforcement problem below.

**This repo has already paid for this exact mistake and already knows part of the fix.**
`SKILL.md:579` documents, about a *different* pair: `assert_pr_body_has_deferred_section` and
`assert_deferrals_recorded` were *"previously invoked **NOWHERE in production**, which is why the
#781 H1 slip fired after merge."* The remedy applied then was a CLI subcommand plus a prose line
that **runs** it.

**A CLI alone is not the fix, and the peer consult was right to say so** (§10). Converting
"remember this rule" into "remember to run this command" shrinks the prose but leaves enforcement
*voluntary* — the same failure class, one step further out. Ten new subcommands the orchestrator is
asked to remember would reproduce exactly what is broken now. So the unit of work is not
"directive → guard" but:

> **obligation → executable check → an invocation the workflow cannot proceed past.**

Where no such choke point exists, the honest classification is **advisory**, not "enforced". This is
the single most important correction to this design and it changes AC2 (§4.2).

**Live corroboration from this very run.** Step 1 item 9 tells the orchestrator to "classify with
`plan_lib.classify_branch_protection(status, body)`". The function requires `body` as a **parsed
dict** (`plan_lib.py:2182` tests `isinstance(body, dict)`). Passing the captured JSON *text* — the
natural reading of "capture the status AND body" — silently returns `unknown` instead of
`unprotected`. I hit this exactly, both ways, this session: an enforceable directive that is
mechanically wrong in prose and fails **silently toward the less-safe answer**. No test catches it,
because nothing invokes it.

### 1.2 What is unguarded today

- **Total prose size: nothing measures it.** `steps.md` can double and every test stays green.
- **`<review-severity>`** (the newest shared block) has no test beyond the sync `--check`: empty its
  source, re-run the sync, both copies go empty together — green.
- **Directive duplication/contradiction:** the same rule stated in three places and later
  contradicted in one of them fails nothing.

## 2. Approaches considered

**A — Inventory-first (literal AC order).** Hand-classify all ~267 keyword hits, then add guards.
*Rejected:* a 267-row hand judgment artifact rots on the first prose edit; many hits are fragments of
one obligation, so per-hit classification is often meaningless; and it produces **zero** enforcement
until the whole table is done.

**B — Freeze the obligation set mechanically, then put the obligations behind checks that cannot be
bypassed.** *Selected.* AC1's inventory becomes **generated** (so it cannot rot), AC5's freeze becomes a
digest over structured obligation records (detecting silent loss and relocation during a restructure —
the actual risk), and AC4's budget becomes a total + per-file byte ceiling. AC2 is **not** cheap: the
checks largely exist as pure functions, but making them *unavoidable* is the hard part and needs a
proven gating surface (§4.1).

**C — A persisted state machine owns transitions, loop accounting and deferrals** (the consult's
literal direction). *Deferred, and deliberately not duplicated:* this is what **#855 is already
building** (`plan_lib.consume_loopback`, `claude_docs/.wf2-state/<issue>/`, deferral records,
`admission_journal.py`). Starting it here would collide head-on with #855's pending PRs 1b–4. B is
the smallest thing that makes C safe to land.

## 3. Design (Approach B)

### 3.1 The instrument, and why one metric is not enough

CI must assert something not gameable. Neither of these works alone:

- A **byte ceiling** is ungameable upward (bytes are bytes) but gameable **downward by deleting
  obligations** — "we got it under budget" by dropping rules.
- An **obligation-set digest** catches deletion but says nothing about size.

They are complementary, and this repo already learned the fake-green lesson the hard way.
`tests/test_switch_skill_trim.py` guards a different skill with a ceiling (`REGROWTH_CEILING_BYTES =
6_000`, `:40`), a **floor** on the file the rationale was relocated into (`WHY_FLOOR_BYTES = 5_000`,
`:50`) so a "split" that deletes instead of relocating fails, **and** eight required topic anchors
(`:56-72`) — added because two Step-11 reviewers found the byte floor alone was fake-green (`:46-49`).
Its own docstring names the residual gap verbatim (`:9-14`): its constraints are all presence pins,
and "every one of them still passes if a maintainer re-inlines the rationale and the file grows back
to its old size."

So the guard is a **pair**, and the ceiling half is **per-file as well as total**:

1. `CORPUS_CEILING_BYTES` **plus a per-file ceiling map.** A single total ceiling misses
   redistribution — `steps.md` can balloon while the total stays under budget because another file
   shrank. (Adopted from the peer consult, §10.)
2. `OBLIGATION_SET_DIGEST` — sha256 over **structured records**, order-preserving. Any obligation that
   disappears, moves to a different step, or is reworded fails until the digest is deliberately
   regenerated.

**The digest hashes records, not bare clause text — because clause-text-only was shown to be
defeatable.** Round 1's reviewer constructed two edits that pass the ceiling *and* a clause-text
digest while changing WF2's behaviour:

- **Heading reassignment.** Changing `## Step 4` to `## Step 5` changes which step every contained
  obligation governs, while preserving per-file bytes and the ordered clause list.
- **Case collision.** Lowercasing normalization maps `DISPATCH` → `dispatch` to the same hash, but the
  DISPATCH audit protocol is explicitly case-sensitive and parsed with `^DISPATCH`
  (`SKILL.md:201`,`:216`).

So each hashed record is `(section_identity, normalized_clause)`, and normalization **preserves case
inside code spans, protocol markers, paths and identifiers** — only prose outside backticks is
lowercased. Two mutation tests are acceptance criteria for PR 1, one per construction above: each must
change the digest. A guard that cannot fail is the fake-green pattern this design exists to prevent.

**And the whole-set digest is explicitly NOT offered as proof that semantics are frozen.** It detects
loss and relocation; it cannot detect an obligation that stays textually identical while the behaviour
it names drifts. Targeted behavioural and location pins remain the real characterization; the digest is
the coarse net under them (see §3.2).

**Why bytes and not a pinned tokenizer.** The peer consult argued for tokens from a pinned tokenizer.
Rejected for PR 1: CI installs only `pytest jsonschema pyyaml` (`.github/workflows/ci.yml`), so a
tokenizer is a new CI dependency, and a tokenizer version change would move the metric under us. Bytes
need no dependency, cannot drift, and are monotonic with tokens for English prose. The measurement is
reported in both (`bytes`, and `bytes/4` as a labelled approximation) but only **bytes** is asserted.

Digest-with-explicit-version is an established idiom here, twice: `quota_detect.RULE_TABLE_DIGEST`
with `CLASSIFIER_VERSION` (`phase_executor/src/phase_executor/quota_detect.py:42`,`:71` — "any table
change fails the digest test until BOTH are bumped together") and `EXPECTED_REGISTRATION_DIGEST`.

**Normalization is deliberately shallow and order-preserving:** collapse whitespace, strip markdown
emphasis, lowercase. It does **not** sort, dedupe, or drop stopwords — because "must X before Y" and
"must X after Y" must produce different digests.

### 3.2 What the pair still misses — stated, not hidden

- **A directive that becomes false while its text is unchanged.** No text metric catches this. That
  is precisely what AC2's guards are for, which is why the budget is a *complement* to
  CLI-ification, never a substitute.
- **A reworded-but-equivalent clause** churns the digest for no semantic change. Accepted cost: the
  fix is a one-line regeneration, reviewed like any other pinned constant.
- **An obligation relocated out of WF2 into another skill** reads as a deletion. Correct behavior,
  but the failure message must say "moved or deleted", not "deleted".
- **Contradiction between two surviving obligations.** Out of scope for PR 1; named as follow-up.
- **An obligation that keeps its text but loses its guard.** The digest cannot see this. PR 2's
  inventory columns (guard? invocation? choke point?) are what close it.

**The digest is migration-scoped, deliberately.** An exact-set pin freezes accidental wording as well
as semantics, so if it outlives the migration it becomes snapshot churn and trains maintainers to
regenerate it reflexively — at which point it guards nothing, exactly like a flaky test. It exists to
make PRs 2–3 safe. **Follow-up (to file at PR 3): retire the whole-set digest in favour of targeted
structural pins once the split has landed.** The peer consult reached this conclusion independently
(§10), which is why it is a stated exit condition rather than a vague caveat.

### 3.3 Reuse, not a third copy — this section was wrong in round 1

Round 1 argued the hook should re-implement the corpus read because `tests/corpus.py` is not
importable from `hooks/`, citing `phase_executor/capture.py` as precedent. **Both halves were wrong,
and a reviewer caught it while I was citing "one helper, one home" elsewhere in this very document.**

- **The production-side corpus reader already exists:** `hooks/skill_registration_check.py:72`
  `skill_corpus(root, name)`, whose docstring already says it *"mirrors tests/corpus.py (which lives in
  tests/ and is not importable from hooks/)"*. A new hook-local copy would be the **third**.
- **`hooks/atomic_write_lib.py` is explicitly the one home** for tmp+replace: *"the one home for python
  tmp+replace (#264, C6) … do not reimplement mkstemp/os.replace inline (that is exactly the
  duplication this module removed — nine divergent copies, three of them weaker)"* (`:2`,`:5-8`).
  Round 1's §5 proposed exactly that reimplementation.
- **The `capture.py` precedent is inapplicable:** that module duplicates atomic-write *specifically
  because `phase_executor` must not depend on `hooks/`* (`capture.py:8-9`). The module proposed here
  lives **in** `hooks/`, so the exemption does not reach it.

**Revised:** `skill_prose_budget.py` imports `skill_corpus` from `hooks/skill_registration_check.py`
and `atomic_write_text` from `hooks/atomic_write_lib.py`. If importing from the registration checker
reads as the wrong dependency direction, the corpus reader moves to a neutral `hooks/` library consumed
by both — but **no third copy ships either way**. A drift-guard test still asserts the production
corpus reader is byte-identical to `tests/corpus.py::skill_corpus`, since
the two definitions cannot diverge. This is the repo's standard mirror-with-a-drift-guard shape.

### 3.4 Files

**New — `hooks/skill_prose_budget.py`** (skill-agnostic core; only `implement-feature` is *pinned* in
PR 1 — the core takes a skill name rather than hardcoding one, which is not speculative generality,
just not baking in a constant). Pure core + thin CLI, the `registry_prune.py` shape:

- **corpus read: imported**, not reimplemented — `skill_registration_check.skill_corpus` (§3.3)
- **atomic write: imported** — `atomic_write_lib.atomic_write_text` (§3.3)
- `extract_obligations(text, *, source) -> list[Obligation]` — `(file, line, section, clause, sha)`;
  section resolved by nearest enclosing heading **or** XML-ish block open, tracking heading level so
  `### Instructions` does not shadow its `## Step N` ancestor
- `classify_clause(clause) -> "obligation" | "reassurance"`
- `corpus_metrics(...) -> dict` — total + **per-file** bytes and lines, obligation and reassurance counts
- `inventory_digest(obligations) -> str` — `sha256:<hex>` over ordered `(section_identity,
  normalized_clause)` records, case preserved inside code spans (§3.1)
- `render_inventory(...) -> str` — the committed markdown table (AC1)
- CLI: `measure` (JSON) · `inventory --out <path>` · `inventory --check <path>` (byte-compares the
  committed artifact against a fresh render) · `check --policy <policy.json>` (rc 1 on breach, rc 2
  caller error)

**`check` takes a policy file, not loose flags** — round 1 found two defects in the flag form. A single
`--ceiling N` cannot express the per-file ceiling map §3.1 requires; and a one-way `--digest D` cannot
produce the added/removed diff §5 promised, because a hash does not carry the baseline clauses. So:

```json
{ "corpus_ceiling_bytes": <int>,
  "per_file_ceiling_bytes": { "<relpath>": <int>, ... },
  "obligation_set_digest": "sha256:…",
  "baseline_inventory": "<repo-relative path>" }
```

`check` validates that the baseline inventory's own hash equals `obligation_set_digest` (so the pair
cannot drift), then diffs the baseline's records against a fresh extraction to report added/removed
obligations with `file:line`. An unexpected or missing corpus file is itself a breach, not a silent
skip.

**Failure mode: fail-CLOSED.** Per the repo's per-hook decision guide (`CLAUDE.md` §3): a gate that
cannot evaluate must not pass. Stated in the docstring and asserted by a test.

**New — `tests/test_skill_prose_budget.py`**: the ceiling, the digest pin, the corpus-mirror drift
guard, plus unit tests of each pure function (happy / malformed / boundary) and the CLI exercised via
`subprocess.run([sys.executable, CLI, …])` per the repo's hook checklist.

**New — `docs/planning/2026-08-03-856-wf2-directive-inventory.md`**: the generated inventory (AC1),
regenerable with one command, so it cannot rot into a lie.

**Inventory schema — occurrences map to canonical obligations, so duplicates collapse but nothing
vanishes** (adopted from the peer consult, §10). AC1 says "an inventory of the 267 directives", but
there are not 267 distinct rules: the keyword sweep counts *source occurrences*, and the same rule is
often stated in three places. So each row is a **canonical obligation** carrying:

| column | meaning |
|---|---|
| `id` | `WF2-OBL-<fingerprint>` — derived from the clause's **content hash**, never document order |
| `clause` | the normalized obligation text |
| `occurrences` | every `file:line` that states it — so a reconciliation check can prove **every** baseline occurrence maps to some live id |
| `section` | nearest enclosing `## Step N` / `<block>` (part of the digest, §3.1) |
| `class` | `unclassified` \| `enforceable` \| `judgemental` \| `advisory` |
| `guard` | the symbol that **rejects** a violation, or `none` |
| `invocation` | the executable call site, or `none` — **PR 2 fills this** |
| `choke_point` | whether the workflow can proceed without the check running — **PR 2 fills this** |

**IDs are content-derived, not positional.** Round 1 caught that "assigned in document order at first
generation" renumbers every row after an insertion, so PR 2's curated `guard`/`invocation`/`choke_point`
annotations — keyed by id — would silently reattach to the *wrong* obligations. A content fingerprint is
stable under insertion and reordering; when a clause is reworded its id changes, which is correct,
because the annotation was reviewed against the old wording and must be re-reviewed. Reconciliation
fails hard on an unmatched or ambiguous row rather than guessing.

**PR 1 leaves `class` and `guard` `unclassified` unless a *rejecting* assertion covers the whole
clause.** Round 1's other correction: naming a function does not establish that the function asserts
the obligation. `compute_risk_ratio` and `strip_reopens` are computational helpers — they return values,
they reject nothing — so auto-tagging a clause `enforceable` because it mentions one would manufacture
coverage that does not exist. That is the same false-enforcement error as §1.1, one level down. So the
mechanical pass may only propose a candidate; `enforceable` requires a reviewed mapping to an assertion
whose contract covers the complete clause, and PR 2 must add a **negative** test showing a violation is
actually rejected. The count of `unclassified` rows is the honest measure of what PR 2 still owes.

**Touched:** `README.md` (changelog entry + the "before" measurement), the four version surfaces.

**Not touched in PR 1: no WF2 prose at all.** That is what makes this PR unable to change WF2
behavior and unable to collide with #855.

## 4. Sequencing — three PRs, and PRs 2–3 wait on #855

AC5 ("semantics frozen first") plus the blast-radius measurement makes the order forced.

**PR 1 is PARTIAL groundwork on AC1/AC4/AC5 — it does not close any of the three.** Round 1 caught this
document claiming otherwise, and the claim was false:

| AC | as filed | what PR 1 actually delivers |
|---|---|---|
| AC1 | every directive **classified** | the inventory + reconciliation; rows are `unclassified` until PR 2 reviews them |
| AC4 | measured **before/after** token count + an asserted budget | the **before** measurement + an asserted **byte** budget. The after-measurement needs PR 3's restructure to exist, and bytes are not tokens |
| AC5 | semantics frozen first | loss/relocation detection + loader hardening. It does **not** freeze behaviour — §3.1's closing paragraph |

So PR 1 closes nothing on its own; it makes PRs 2–3 safe. The AC amendments in §4.2 are **requests to
the owner**, not accomplished facts, and #856 must not be closed on PR 1.

- **PR 1 (this one).** Generator, inventory, ceiling+digest guard, measured before, **plus the
  characterization hardening that must precede any split** (§4.3). Additive; changes no workflow prose.
- **PR 2 (after #855 lands) — AC2.** For each enforceable obligation: an executable check, a call site,
  **an invocation-path test proving the workflow cannot proceed past it**, and a negative test proving a
  violation is rejected. Obligations with no available choke point are relabelled `advisory` rather than
  claimed as enforced.

  **The choke-point mechanism is an OPEN QUESTION requiring a spike, and round 1 refuted this document's
  first candidate.** Both reviewers independently showed `hooks/step_state_post.py` cannot serve:
  it is observational, Bash-only and **fail-open** (`:19`,`:305`), registered only under Bash
  `PostToolUse` (`hooks/hooks.json:73`), and `tests/hooks/test_step_state.py:228` deliberately pins step
  state **away from every gating-capable event**. PR 2 must therefore cite a real gating surface and
  prove it: candidates are `PreToolUse` (which genuinely denies — `wal-guard` and `security-guard.py`
  do exactly this), the `Stop` hook, or a command entry point every path must traverse (review
  admission). The spike must show a refusal actually blocks advancement on the interactive, headless
  **and** resume paths, and must attempt a bypass. **If no unavoidable gate exists, the honest outcome
  is that those obligations are labelled `advisory` and AC2 is met by saying so** — not by shipping a
  guard that only looks like enforcement.
- **PR 3 (after #855 lands) — AC3.** Split `steps.md`, carrying the ~57 test updates in the same
  commit, and retire the migration-scoped digest (§3.2).

### 4.2 Two acceptance criteria need amending — for the owner

Both are recorded as requests, not unilateral changes; PR 1 satisfies the ACs as filed either way.

1. **AC2 as filed** ("every enforceable directive has a guard") is satisfiable by a guard nothing
   calls — which is the present bug, not the fix. Proposed: *every enforceable obligation has a
   tested guard **and a demonstrated invocation path at a workflow choke point**; a
   callable-but-optional guard is not enforcement, and an obligation with no choke point is labelled
   advisory.*
2. **AC1 as filed** ("an inventory of the 267 directives") encodes a keyword-sweep artifact as a
   target. Proposed: *account for all 267 source occurrences, but inventory canonical semantic
   obligations* — duplicates map to one id, and obligation clauses that state a rule without any of
   the seven keywords are added rather than excluded to preserve the number.
3. **AC4 as filed** wants a before/**after** token count. The after-count cannot exist until PR 3
   restructures, and this design asserts **bytes** rather than tokens on purpose (§3.1). Proposed:
   *AC4 is met across PRs 1 and 3 — PR 1 records the before-state and the asserted byte budget; PR 3
   records the after-state* — or, if a token figure is required to be the asserted metric, say so and
   accept a new CI dependency on a pinned tokenizer.
4. **Merge order (§4.4's coordination point).** Recommended: merge **#859 and #862 first**, then land
   PR 1 so the frozen baseline already includes #855's prose. That needs a merge authorization this
   run does not have. The alternative — PR 1 first, #855 owns the digest reset — is workable but puts
   generated-artifact churn on #855.

### 4.3 Characterization hardening lands in PR 1, not PR 3

The peer consult argued the fragile test loaders must be fixed *before* any split, and it is right:
that work is AC5's ("semantics frozen first"), it changes no prose, and leaving it to PR 3 means the
riskiest PR also carries the refactor. Moved into PR 1: `tests/test_feasibility_gate.py:264`'s
**module-level** `steps.md` read moves inside the four tests that use it, so a future split cannot
take 25 unrelated `plan_lib` unit tests down with a collection error. Deliberately **not** in PR 1:
re-anchoring `test_security_scan.py:832`'s ordering pin and `test_skill_helpers.py`'s heading slicer —
both are *correct today* and only degrade after the split, so they belong in the commit that splits.

**Why 2–3 wait:** #855's pending PRs 1b–4 edit WF2 prose to point at the loop-back/confidence/
deferral guards. Decision D143 ruled those edits *surgical* — true for surgical edits, **not** for
relocating 2,041 lines out from under them.

**But PR 1 creates a deliberate CI coordination point with #855, and that must be stated rather than
discovered.** PR 1 avoids a *file-level* conflict (it edits no prose), yet its digest is defined to fail
on any obligation rewording — which is exactly what #855's prose edits do. So one of two things must be
true, and it is the owner's call which:

1. **#859/#862 merge first, then PR 1 freezes the baseline** — cleanest, and it costs nothing but
   ordering. It needs a merge authorization that does not exist for this run.
2. **PR 1 lands first and #855 owns the digest/inventory reset** — a one-command regeneration
   (`inventory --out` + repin) plus an explicit semantic-delta review of what changed, so the reset is a
   reviewed decision and not a reflex.

Option 1 is recommended. This is recorded as a request in §4.2.

### 4.4 What PR 3 must carry (measured, so PR 3 does not rediscover it)

A `steps.md` split breaks **~57 tests across 8 files**. Ranked by how *silently* they fail:

1. `tests/hooks/test_security_scan.py:832` — `index("## Step 11.5") < index("## Step 12:")`
   **stays green while measuring filename sort order instead of document order.** The only pin that
   never tells you it broke.
2. `tests/test_skill_helpers.py:29-44` — 16 helper→step pins; can pass with a helper under the
   **wrong** step if section boundaries shift.
3. `tests/hooks/test_wf_review_sites.py:53`,`:61` — stale relpath keys; fails loudly but the message
   blames the prose, not the path.
4. `tests/test_wf2_clarity.py` — 13 direct `steps.md` reads (~17 tests); five are **cross-step count
   pins** (`>= 3`, `>= 2`) that a naive one-file path swap silently reduces to a subset.
5. `tests/test_render_addon_block.py:24` + `scripts/sync_shared_blocks.py:70` — the `<render-addon>`
   site moves from `steps.md:1490` into `step-12.md`; MANIFEST and `SHIPPED_SITES` must move together.
6. `tests/test_feasibility_gate.py:264` — **module-level** read ⇒ collection error takes all 29 tests,
   including 25 pure `plan_lib` unit tests. Loudest, biggest collateral.
7. `tests/test_bundled_agents.py:173`,`:226`,`:262`; 8. `test_model_routing_resolve_prose.py:215-222`;
   9. `tests/hooks/test_seat_outcomes.py:729` (ordering, only meaningful within one file);
   10. `tests/test_wf2_error_and_ci.py:47`.

Two split decisions that are load-bearing:

- **The `steps.md:1-241` preamble must land in `step-00.md`** (something sorting *before* the step
  files). `TestDelegatedReads` slices `"### Delegated reads (#314)"` → `"\n## Step "`; if the preamble
  sorts after `step-16.md` there is no following `## Step ` and 8 tests die with `ValueError`.
- **No per-file `## <Capital>` headings.** `test_skill_helpers.py:41`'s terminator is
  `^## (?:Step\s+\S+|[A-Z])`; an added `## Overview` truncates sections silently.

## 5. Error handling and failure modes

| condition | behavior |
|---|---|
| skill dir / `SKILL.md` missing | rc 2, message names the path (caller error, not a gate result) |
| corpus unreadable / undecodable | **rc 1 — fail-closed.** A gate that cannot read cannot pass |
| ceiling exceeded | rc 1, reports actual vs ceiling and the per-file breakdown |
| digest mismatch | rc 1. The added/removed diff with `file:line` comes from the policy's **baseline inventory**, not from the hash (a hash cannot carry the baseline — §3.4); wording is "moved or deleted" |
| baseline inventory's hash ≠ policy digest | rc 1 — the pinned pair has drifted; refuse rather than trust either side |
| committed inventory ≠ fresh render (`inventory --check`) | rc 1 — the artifact has rotted |
| corpus file present that the policy does not pin | rc 1 — an unpinned file is unmeasured, which is a breach, not a skip |
| `--out` path outside the repo | rc 2, refused (path canonicalized + containment-checked) |
| inventory write interrupted | `atomic_write_lib.atomic_write_text` (the one home, §3.3) — crash-safe, no stray temp, symlink-safe |

## 6. Security implications

Reads repo-local markdown; writes one file under `docs/`. No network, no subprocess, no untrusted
input. Two hardening points, both from the repo's hook checklist: the `--out` path is canonicalized
and containment-checked against the repo root (no traversal), and the inventory write is atomic so a
crash cannot leave a half-written doc at the final path. Regex runs only over repo-local prose, never
untrusted input, so catastrophic-backtracking exposure is not a live concern — patterns are
line-scoped and bounded regardless.

## 7. Platform / external dependencies

platform_apis: none

**Scope of that declaration: PR 1 only, and it is now true by construction.** Everything PR 1 uses is
Python stdlib (`re`, `hashlib`, `pathlib`, `json`) plus pytest — proven across ~20 existing hooks — and
two in-repo helpers it now *imports* rather than reimplements (`skill_registration_check.skill_corpus`,
`atomic_write_lib.atomic_write_text`, §3.3). The corpus invocation was probed live this session
(`skill_corpus('implement-feature')` → 301,635 bytes / 3,529 lines). No platform, framework or external
API is involved.

**Round 1 correctly flagged that this declaration was hiding something, and it was.** The earlier draft
named Claude Code's `PostToolUse` hook as PR 2's refusing choke point while declaring `none` — a
platform-behaviour commitment with no evidence, which is precisely the #226 failure. That commitment is
**withdrawn** (§4.1): the cited precedent refutes it, PR 2 must run a spike against a genuinely gating
event, and **PR 2's design will carry its own `platform_apis:` block declaring whichever hook event it
depends on, with `spike` evidence citing the real invocation.** PR 1 depends on no hook event, so `none`
is accurate here rather than convenient.

## 8. Multi-PR assessment

Flagged for Step 5: three PRs (§4). PR 1 is well under 500 lines of production change (one hook, one
test file, one generated doc).

## 9. The claim I would most expect to be wrong

**That the obligation extractor's clause boundaries are stable enough for a digest to be useful
rather than merely noisy.** The digest's value depends entirely on the extractor splitting prose into
clauses the same way across unrelated edits. If a maintainer rewraps a paragraph and the clause
boundaries shift, the digest churns and the guard trains people to regenerate it reflexively — at
which point it stops guarding anything, exactly like a flaky test. Mitigations in the design:
normalize whitespace before hashing (so rewrapping is invisible), split on sentence terminators
rather than line breaks, and pin the extractor's own behavior with unit tests over fixtures.

**PR 1's acceptance evidence must therefore include fixtures proving that wrapping-only edits preserve
the digest and that obligation edits change it** — specifically the two mutation cases from §3.1
(heading reassignment, `DISPATCH`→`dispatch`) plus a pure rewrap. That is a checkable deliverable rather
than a caveat. (Round 1 flagged the previous phrasing — "the first thing a reviewer should attack" — as
itself steering a reviewer's attention toward an acknowledged risk and away from unacknowledged ones,
which is the mirror of the repo's own rule against telling a reviewer what to conclude. The phrasing is
replaced accordingly.)

**The peer consult reached the same conclusion independently** (§10): *"Exact snapshots freeze
accidental wording as well as semantics. Use them temporarily during migration, then retain targeted
structural and behavioural pins rather than making all future edits snapshot churn."* Two independent
routes to the same top risk is why §3.2 now carries a stated exit condition for the digest rather than
a caveat.

## 10. Peer consult provenance

Backend `gpt` (Codex CLI) via `hooks/adversarial_review_lib.py consult`, exit 0; report at
`docs/reviews/peer-rawgentic-peer-problem-856-2026-08-03.md`. Blind both ways: §1–§9 as first written
were on disk before the proposal was read.

**Adopted from the peer, each one changing this design:**

1. **A CLI is not enforcement** — *"merely exposing another ten subcommands would reproduce the current
   failure mode"*; a validator that exists while the workflow can bypass it is **false enforcement**.
   Rewrote §1.1's conclusion and AC2 (§4.2). The single most valuable contribution.
2. **Per-file ceilings, not just a total** — a total-only budget misses redistribution between files (§3.1).
3. **Occurrences vs canonical obligations** — the 267 are source *occurrences*; duplicates should map to
   one id while every occurrence stays accounted for. Became the inventory schema (§3.4) and the AC1
   amendment (§4.2).
4. **Fix the fragile test loaders before splitting, not during it** — moved into PR 1 (§4.3).
5. **Retire the exact-set digest after migration** — became a stated exit condition (§3.2).
6. **"Advisory" is an honest classification** — an obligation with no available choke point must be
   labelled advisory, never claimed as enforced (§3.4 `class` column).

**Not adopted, with reasons:**

- **A pinned tokenizer for the budget metric.** CI installs only `pytest jsonschema pyyaml`; a
  tokenizer is a new CI dependency whose version changes would move the metric under us. Bytes are
  asserted; tokens are reported as a labelled approximation (§3.1).
- **A full `policy/wf2/vN/directives.yaml` + `hooks/wf2_policy.py` state machine owning transitions.**
  That is Approach C (§2), and **#855 is already building it** — `plan_lib.consume_loopback`,
  `claude_docs/.wf2-state/<issue>/`, deferral records, `admission_journal.py`. The peer was told #855
  exists but was not given its design, so it could not see the overlap. A second state machine here
  would collide with #855's pending PRs 1b–4.
- **Its step-file `manifest.yaml` with explicit numeric order** instead of filename sorting. Deferred to
  PR 3 as a real option and recorded there — the peer is right that *tests* must not infer order from
  filename sorting, which is exactly the silent-degradation trap already measured at
  `test_security_scan.py:832` (§4.4 item 1).

**Peer risks carried forward but not acted on in PR 1:** state files can be copied, hand-edited or used
concurrently (bind state to a run id, validate event sequencing); a structurally valid state can still
encode a wrong judgement; and if the host offers no unavoidable lifecycle hook, no design can promise
non-bypass — which is why the choke-point candidate in §4.1 is a **hook**, the one surface here that
can refuse.

## 11. Review history

Round-1 critique (14 findings, all applied) is recorded separately in
`docs/reviews/2026-08-03-856-design-round1-log.md` — deliberately NOT in this document. A measured
finding in this repo (#840 round 13) is that a brief carrying accumulated round history *suppressed*
findings: three reviewers told an area was settled duly looked elsewhere and missed a Critical the
neutral arm found. The rationale for each design choice stays inline above, where a reviewer needs it;
the list of past conclusions lives outside the artifact under review.
