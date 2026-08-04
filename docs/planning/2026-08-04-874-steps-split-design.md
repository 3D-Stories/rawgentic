# #874 — Split WF2 `references/steps.md` into step-local files + targeted characterization pins

**Issue:** #874 (Part of epic #875, M1 STAY SMALL) · **Date:** 2026-08-04 ·
**Base:** `origin/main` = `590c6d4e` (v3.125.2) · **Baseline suite:** 4798 passed / 0 failed, exit 0
**Complexity (authoritative, Step 2):** `complex_feature` — 24 estimated impl files
(`lane_decision` → `tier=full`, reason "complex_feature — full spine"). Not lane, not trivial.

## Problem

`skills/implement-feature/references/steps.md` is **1,938 lines / 157,313 bytes** carrying 18
`## Step` sections behind a **236-line preamble** (lines 1–236) that holds the step-semantic blocks
(`<small-standard-lane>`, `<trivial-work-check>`, `<learning-config>`, `### Delegated reads`).
Every per-step lookup loads all 18 sections. `SKILL.md` is **549 lines** — 49 above AC1's <500 target
and moving the wrong way (it grew 17 lines since the issue was filed).

## Approaches considered

**A. Split flat in `references/`, one file per `## Step` section + a preamble file.** Corpus glob
keeps seeing every file; concatenation order is preserved (probed below). Location pins break and are
redirected per-pin. **CHOSEN.**

**B. Split into a `references/steps/` subdirectory.** Rejected: `tests/corpus.py:22` reads the FLAT
glob `sorted(refs.glob("*.md"))`, so a subdirectory silently escapes the corpus and un-pins every
content guard — AC2 names this exact hazard, and it is the M0 lesson ("guards that stop looking look
exactly like guards that pass"). Would require changing the corpus helper, widening blast radius
across all 16 corpus-reading test files for no gain.

**C. Split by step-groups (e.g. 1–4, 5–9, 10–16).** Rejected: fewer files but AC1 asks that "each
WF2 step's detail is loadable alone"; a group still loads its neighbours, so it does not deliver the
progressive-disclosure win, while costing the same pin churn.

## Chosen design

### 1. Partition mechanically, verify by byte equality

Partition at `^## Step ` boundaries with a **throwaway script** (scratchpad, never committed) so the
157 KB never enters the orchestrator's context. Section bodies are copied **verbatim** — no
reflowing, no header rewriting — and the split is verified by asserting
`concat(preamble, step files in step order) == original steps.md` **byte for byte** before anything
is committed. **Byte equality proves content preservation only; AC6 additionally requires the
reading-contract checks in §2b.** The assertion runs TWICE — once at partition, and again **after
`sync_shared_blocks.py` and immediately before the mechanical commit**, comparing the final
concatenation against the `origin/main` `steps.md` blob — because the sync is a later
content-writing operation against `step-12.md` and could otherwise invalidate an assertion that had
already passed (adversarial F3).

Files (flat, zero-padded so the glob sorts in step order):

```
references/step-00-preamble.md   lines 1–236   (reading contract + the step-semantic blocks)
references/step-01.md  step-01b.md  step-02.md … step-08.md
references/step-09.md … step-11.md  step-11_5.md  step-12.md … step-16.md
```

`steps.md` is DELETED (its content is fully redistributed; leaving a stub would keep the monolith's
bytes in the corpus twice and defeat AC5's budget shrink).

### 2. Why corpus content pins survive — probed live

`<probe-before-design>` demanded the exact invocation, not a proxy. Running `sorted(refs.glob("*.md"))`
— literally what `tests/corpus.py:22` calls — over the simulated post-split name set yields:

```
quality-bar · run-record · state-and-resume · step-00-preamble · step-01 · step-01b · step-02 …
step-08 · step-09 … step-11 · step-11_5 · step-12 … step-16 · whole-issue-delegation
```

The split files occupy **exactly the slot `steps.md` held, in step order** (`step-` sorts after
`state-` and before `whole-`; `.` < `_` puts `step-11.md` before `step-11_5.md`). Consequences, and these are the load-bearing ones:

- **Corpus CONTENT pins survive** — the sentence is still in the concatenation.
- **Corpus SLICE pins survive** — a guard slicing `text.index("## Step 4:")…index("## Step 5:")`
  still resolves, because step order is preserved across file boundaries.
- **Only LOCATION pins break** — tests that read `(REFERENCES / "steps.md").read_text()` raise
  `FileNotFoundError`. That failure is LOUD, which is the desired direction: AC2's silent-un-pinning
  hazard is avoided precisely because the flat split cannot make a guard quietly stop looking.

### 3. Pin handling — no transitional helper

Rejected adding a `wf2_steps_text()` concatenating helper to `tests/corpus.py`. It would keep every
pin able to search the whole step corpus, which directly contradicts AC3's "each pin anchors ONE
canonical sentence in ONE file" and would survive as a permanent invitation to re-monolith the pins.
Instead each surviving pin is redirected to **the specific step file it is about** (e.g.
`TestStep7BranchBase` → `step-07.md`); the handful of genuinely cross-step pins (e.g. the
`<review-pipelining>` guard asserting ≥3 pointer sites at §4/§8a/§11) concatenate exactly those
named files inline.

Direct-reader inventory to redirect (implement-feature only — WF3's `fix-bug/references/steps.md` is
**untouched** by this issue, so `test_wf3_clarity.py` and friends are out of scope):

| File | Sites |
|---|---|
| `tests/test_wf2_clarity.py` | ~20 (1,629 lines, 36 classes, 161 test functions) |
| `tests/test_wf2_prose_budget.py` | :13 (docstring table), :45, :49 (budget row) |
| `tests/test_feasibility_gate.py` | :264 |
| `tests/test_wf2_error_and_ci.py` | :38 |
| `tests/hooks/test_wf_review_sites.py` | :63, :67 |
| `tests/test_render_addon_block.py` | :24 |
| `tests/test_bundled_agents.py` | :163, :207, :219 |

### 4. AC3 — which pins die, by a stated criterion

AC3 deletes drift guards on purpose, so the criterion must be written down rather than felt. A pin is
**RETAINED** when it pins one of the six contract areas the roadmap names verbatim — mandatory review
sites · reviewer≠author · artifact delivery · loop-back debit · deferral honesty · no executor
vocabulary — i.e. when its failure would mean a **gate stopped being enforceable**. A pin is
**DELETED** when it only asserts that a particular sentence is phrased a particular way and its
failure would mean only that prose was reworded. Every deletion is enumerated in the PR body with its
class and test name, and the net is counted in the changelog `Suite old→new` tail (which will go
**down** — worth stating plainly, since every other entry in that changelog goes up).

### 5. Shared-block MANIFEST — the coupling that fails CI if missed

`scripts/sync_shared_blocks.py` MANIFEST **line 70** registers
`implement-feature/references/steps.md` as a `render-addon` call site. The block occupies
`steps.md:1427–1555`, i.e. inside **Step 12**, so the entry becomes
`implement-feature/references/step-12.md`. `tests/test_shared_block_drift.py` (CI) fails otherwise —
and `sync_shared_blocks.py --check` is the gate that catches it.

### 6. AC7 — the STALE CEILING violation class

New violation class in `tests/test_wf2_prose_budget.py`: a budget entry whose ceiling exceeds the
file's actual size by more than an allowed margin FAILS, naming the path and the excess bytes.

Margin shape: **percentage with an absolute floor** — fail when
`ceiling - actual > max(STALE_CEILING_MIN_BYTES, actual * STALE_CEILING_PCT)`. Percentage alone
punishes small files (a 400-byte file cannot carry a sane absolute ceiling); an absolute alone
punishes large ones. Concrete values are sized **after** the split, against the real post-split
sizes, and recorded here before Step 8 closes. Unit tests cover over-margin, within-margin, and the
total-ceiling analog, per AC7.

## File changes

- **New:** 19 `references/step-*.md` files. **Deleted:** `references/steps.md`.
- `skills/implement-feature/SKILL.md` — reading contract rewritten to point at per-step files;
  must land **<500 lines** (AC1). Absorbing 20 pointers while cutting 49+ lines means the per-step
  bullet list is the thing that pays: each step's `(read references/steps.md §N …)` tail becomes a
  single terse filename, and the redundant prose restating `<happy-path>` and `<mandatory-steps>`
  inside the per-step bullets is cut. If <500 proves unreachable without cutting a gate semantic,
  that is a Step-4 finding, not something to force.
- `scripts/sync_shared_blocks.py` — MANIFEST line 70 retargeted; run the sync.
- `tests/` — the 7 files above; `test_wf2_prose_budget.py` gains 20 budget rows + AC7's class.
- `README.md` — the `references/steps.md` architecture sentence at :241 + a changelog entry.
- Version ×3 surfaces: `.claude-plugin/plugin.json`,
  `plugins/rawgentic/.codex-plugin/plugin.json`, `tests/hooks/test_adversarial_review_registration.py`.
- **Historical docs are NOT rewritten** — `docs/planning/*` and `docs/reviews/*` reference
  `references/steps.md` as history (they record what was true at their date); the repo convention is
  that history stays as filed.

## Platform / external dependencies

platform_apis: none — this change adds no platform, framework, or external API call. The only
load-bearing mechanism is Python stdlib `pathlib.Path.glob` ordering under `sorted()`, which is
already the shipped mechanism at `tests/corpus.py:22` (an existing in-repo call site) and was
additionally probed live with that exact invocation (§2 above). No network, no subprocess, no new
dependency.

## Error handling and failure modes

- **Half-migrated tree (AC2's hazard).** Mitigated structurally: the flat split cannot silently
  un-pin, because a stale location pin raises `FileNotFoundError` rather than passing vacuously.
  Additionally the mechanical commit is not made until byte equality is asserted.
- **Sort-order regression.** If a future file is added to `references/` whose name sorts between the
  step files, corpus slice pins could straddle it. Named as a residual risk; the zero-padded
  `step-NN` namespace makes an accidental collision unlikely.
- **`git rm` leftovers** (repo mistake #7): follow the delete with an explicit existence re-check.
- **`git checkout -- <path>` is banned in this run.** The round-1 handoff records it discarding an
  entire uncommitted implementation. Sabotage cleanup uses `cp` from an explicit backup.

## Security implications

None material: no executable surface, no input parsing, no credential path, no network. The change is
markdown redistribution plus test edits. The one security-adjacent property is that the
**security-relevant gate prose** (Step 11.5's scan contract, Step 11's review mandate) must survive
the move verbatim — guaranteed by the byte-equality assertion, and re-checked by the retained
mandatory-review-site pins (one of AC3's six contract areas).

## Multi-PR assessment

>500 lines changed, but **single PR**: the split, its pin sweep, and its budget update are one
atomic correctness unit — a PR that split the file without re-anchoring the pins would ship a tree
whose guards do not describe it. AC4's reviewability requirement is met by **commit separation
inside the one PR**, not by splitting the PR:

1. `refactor(wf2)` — mechanical partition + `steps.md` deletion + MANIFEST retarget + per-pin
   location redirects + budget rows. Suite green at this commit.
2. `test(wf2)` — the AC3 pin sweep (deletions + six-area re-anchoring).
3. `test(wf2)` — AC7's STALE CEILING class + its unit tests.
4. `chore(wf2)` — version ×3, README architecture sentence + changelog entry.

## Peer consult (WF13 layer, cross-model, blind both ways)

Backend `gpt`, reviewer **gpt-5.6-sol**, author `claude-opus-5[1m]` (reviewer≠author enforced by the
runner). Result: `status=success`, `attempts=1`, `diagnostic=true`, `input_sha256` matched the problem
file at collection (fresh). My design draft was on disk before the result was read — blindness held.

The consult changed the design. Adopted:

- **P1 — progressive-disclosure UNDER-READING is the real silent behaviour change, and I had missed
  it.** Byte equality proves the *content* survives; it proves nothing about what an agent *loads*.
  An agent that reads only its current step file can miss a preamble invariant (lane eligibility,
  the trivial-work check, delegated-read rules) or a prerequisite set in another step — and **every
  corpus test stays green, because all the sentences still exist somewhere**. This is exactly the
  AC6 "no behaviour change" hole. **Adopted:** the reading contract declares an explicit
  **ALWAYS-READ** set (`step-00-preamble.md`) plus per-step declared prerequisites, and the preamble
  is not optional context. This is now a design requirement, not a nicety.
- **P2 — lexicographic order is an accidental dependency.** A later `step-8.md` or `step-11.5.md`
  would reorder corpus slices while every file stays discoverable. The peer proposed an explicit
  ordered manifest file. **Adopted in the cheaper form:** a drift guard asserting (a) the sorted glob
  order equals the expected step order and (b) each step file contains exactly one top-level
  `## Step` heading matching its filename. Same protection, no new runtime artifact to keep in sync
  (a manifest is a second source of truth; the test is a guard).
- **P3 — a redirect can accidentally BROADEN coverage.** Repointing a direct-file pin at a
  corpus-wide search can pass because the sentence appears elsewhere. Independent second reason to
  keep the no-helper decision: every redirected pin keeps **file ownership** or becomes a recorded
  deletion.
- **P4 — the deletion criterion must be per-ASSERTION, not per test function.** One function can mix
  a prose pin with a structural/behavioural assertion; deleting the function would silently drop the
  non-prose invariant. **Adopted:** classify at assertion level and split functions where mixed.
- **P5 — packaging parity is not implied by byte equality.** Verify the packaged/mirrored surface
  actually contains the new reference files (the Codex mirror symlinks the whole skill dir, so this
  should hold — but it gets checked, not assumed).
- **P6 — AC7 margins, concrete and symmetric.** `per_file_allowed = max(256, ceil(actual * 0.05))`;
  `total_allowed = max(1024, ceil(actual_total * 0.02))` — a tighter percentage on the aggregate so
  many individually-fine gaps cannot accumulate into paste-sized corpus slack. Validation is
  **symmetric**: fail when `actual > ceiling` AND fail STALE_CEILING when
  `ceiling - actual > allowed`. The diagnostic reports path, actual, ceiling, allowed, excess.
- **P7 — a meta-check on the six areas.** Assert exactly six canonical prose pins survive, each with
  exactly one owning file. Cheap, and it makes AC3's "deliberately" checkable rather than asserted.
- **P8 — target ~450–470 lines for SKILL.md**, not "just under 500", to leave maintenance headroom.

Noted, NOT adopted this PR (scope): splitting the preamble's universal invariants from its
step-specific material (the peer's point that an always-read preamble which stays large partly
undermines the win). Recorded as a follow-up — it is a content-edit judgement call that wants its own
issue rather than riding a mechanical split.

---

# Revision 2 — Step 4 pass 1 findings applied (2026-08-04)

Step 4 pass 1 produced 7 findings (2 inline self-review, 5 cross-model adversarial-on-design;
reviewer **gpt-5.6-sol**, `status=success`, `diagnostic=true`, `input_sha256` FRESH). Volume
thresholds did NOT trip (2 High / 5 Medium vs High:5 / Medium:10). Fold of Critical/High
`Loopback-class` entries — `classify_loopback_source(['design-flaw','spec-tightening'])` → **`design`**
— so **exactly one `design` loop-back was consumed** (`consume_loopback` → `ok=True`,
`{"design":1,...,"total":1}`, persisted at `claude_docs/.wf2-state/874/loopback_counters.json`).
This section IS the Step-3 return. Step 4 pass 2 re-runs against the revised doc.

**Ambiguity circuit breaker: TRIGGERED then DISSOLVED, not escalated.** Adversarial F2 and F4 carried
`ambiguity_flag: true`. Both were of the form "the design does not say WHICH check" — not a conflict
between findings, and not a trade-off requiring owner judgement. In both cases one option is strictly
STRONGER, so the ambiguity is dissolved by adopting it (the precedent this campaign set on #822's F4:
dissolve by enumerating, escalate only when the choice is genuinely the owner's). Neither dissolution
weakens a guard — both make it stricter. Breaker result after dissolution: **clear**. No finding
conflicted with another; all seven recommendations are mutually compatible and AC-aligned.

## §2b — Reading contract: the resolved read set (adversarial F1, High, design-flaw)

The gap: adopting peer P1 (always-read preamble + per-step prerequisites) was stated but never
*enumerated* or made *checkable*. An implementation could preserve every byte while an agent omits a
prerequisite and changes lane, delegation, or step behaviour — with all corpus tests still green.
That is the AC6 hole, and byte equality cannot see it.

**Every step file's resolved read set is declared as a table in `SKILL.md` and asserted by test.**

| Reading | Files, in order |
|---|---|
| ALWAYS (every step) | `step-00-preamble.md` |
| Step N detail | `step-<N>.md` |
| Step 3 prerequisite | `+ probe-before-design` block (moved — see §3b) |
| Step 8 prerequisite | `+ early-smoke-install` block (moved — see §3b) |
| Step 16 prerequisite | `+ completion-gate` block (moved — see §3b) |
| Step 4 / 8a / 11 | `+ step-00-preamble.md` review-wave pointers (already in ALWAYS) |

`step-00-preamble.md` is **mandatory input, never optional context** — it carries lane eligibility,
the trivial-work check, `<learning-config>`, and the delegated-read rules, each of which changes step
behaviour if unread. Characterization tests assert each step's resolved read set **exactly** matches
this table (set equality, not superset), so a dropped prerequisite fails loudly instead of silently.

## §3b — AC1's line arithmetic, resolved (self-review F1, High)

`SKILL.md` = 549 lines, **486 inside XML-ish blocks**, only ~63 outside. Trimming the per-step
pointer list therefore cannot find 50 lines, and **165 block lines are shared-block GENERATED**
content (`<model-routing-resolve>` 75, `<review-severity>` 51, `<loop-back-budget>` 39) — editing
them inline fails `sync_shared_blocks.py --check` (repo mistake #14), and `review-severity` is shared
with fix-bug, so reshaping at source hits WF3.

**Resolution: option (a) — move the three STEP-SCOPED blocks into their step files**, declared as
prerequisites in §2b's table so the move is explicit rather than a silent change in what the
orchestrator sees:

- `<completion-gate>` (24 lines) → `step-16.md`
- `<probe-before-design>` (12) → `step-03.md`
- `<early-smoke-install>` (14) → `step-08.md`

= 50 lines → **549 → 499**. Target **≤480** (peer P8 wanted headroom below 500), the balance from
rewriting `<references>` (16 lines) into the per-step table and cutting per-step bullet prose that
merely restates `<happy-path>`/`<mandatory-steps>`. These three are the *right* blocks to move: each
is consulted by exactly one step, so moving them IS progressive disclosure, not a dodge. The
always-loaded spine protocols (`<mandatory-steps>`, `<loop-back-budget>`, `<review-severity>`,
`<model-routing-resolve>`, `<step-tracking>`, `<error-protocol>`) **do not move** — a gate depends on
each being in context at every step.

**If the measured result at Step 9 is still ≥500**, that is a Step-9 finding: either move a further
step-scoped block or report AC1 as deliberately PARTIAL with this arithmetic as the rationale. AC1 is
NOT forced by moving a block whose always-loaded status a gate depends on.

## §4b — Ordering guard, fully specified (adversarial F2 — ambiguity dissolved)

The guard asserts the **exact ordered list of all 19 split filenames**, AND that their indices form
**one contiguous interval** in the FULL `sorted(refs.glob("*.md"))` result — no other file between
the first and last split file. Dissolution: the design had not said whether the guard reads the
complete glob or pre-filters to `step-*`; a filtered check would still pass with a non-step markdown
file sorted between two step files, which is the exact residual that contaminates corpus slices.
The unfiltered contiguous-interval form is strictly stronger, so it is chosen. No ellipses: the
expected list is written out literally in the test.

## §4c — The six canonical pins vs the negative scan (adversarial F4 — ambiguity dissolved)

"No executor vocabulary" is a **negative invariant over a scope**, not ownership of one canonical
sentence. Reducing it to a positive sentence-existence pin would let forbidden vocabulary survive
elsewhere while the six-pin meta-check passed. Dissolution — adopt the stricter reading:

- The **exactly-six** meta-check (peer P7) counts **canonical prose-sentence pins only**.
- The executor-vocabulary guard is retained as a **separately counted NEGATIVE assertion**, with the
  files it scans named explicitly, and is **excluded** from the six-pin count.
- AC3's deletion sweep never removes a non-prose structural or negative guard: the criterion is
  applied **per assertion** (peer P4), and a structural/behavioural/shared-block/negative-scan
  assertion is retained or rewritten against a narrow owner — never deleted as a "prose pin".

## §5b — Packaging parity (adversarial F5)

"Should hold" is not evidence. The mechanism: `plugins/rawgentic/skills/<name>` is a **symlink to
`../../../skills/<name>`** (whole-directory), so new `references/*.md` files are mirrored
automatically — but that is a claim about a symlink, and it gets asserted, not assumed. The check
enumerates the shipped/mirrored surface and asserts **all 19 `step-*.md` paths exist and are readable
through the same packaged paths `SKILL.md` references**, failing with the artifact and path names.
`tests/test_codex_plugin_packaging.py` is the existing home for that assertion.

## Net effect on the plan

The four-commit shape is unchanged. Commit 1 additionally carries the §2b read-set table + its tests,
the §3b block moves, the §4b ordering guard, and the §5b packaging assertion; commit 2's sweep is
explicitly per-assertion and never touches the negative scan.

## Revision 2a — the partition dry run corrected a factual error in this design

Running the partition script `--dry-run` **before writing any file** (byte-equality + sort-order
proved, nothing written) established the real shape, and it is **19 files, not 20**:

`step-00-preamble` (18,023 B / 236 lines) · `step-01` · `step-01b` · `step-02` · `step-03` ·
`step-04` (22,597 B / 188 L) · `step-05` · `step-06` · `step-07` · `step-08` (22,435 B / 209 L) ·
`step-09` · `step-10` · `step-11` (14,307 B) · `step-11_5` · `step-12` (12,654 B / 245 L) ·
`step-13` · `step-14` · `step-15` · `step-16` (10,995 B). Total **157,313 bytes**, 18 sections.

**There is no `## Step 8a` heading.** Step 8a's detail is a LEVEL-3 subsection
(`### Step 8a sub-step: Per-task Review (P15)`) nested inside the Step 8 section
(`steps.md:1049`, within 903–1112), so it lands inside `step-08.md`. This design, the round-3
handoff, and the peer-consult problem statement all said "20 files" and named a `step-08a.md`
that does not exist — corrected here rather than left to fail at implementation.

Consequences:
- The §4b ordering guard asserts the exact **19**-name list (not 20).
- The §5b packaging assertion covers **19** `step-*.md` paths.
- **Step 8a is not independently loadable, and that is deliberate.** AC1 allows "one file per step
  **or small step-groups — implementer's call"**, and 8a fires only after Step 8's plan tasks are
  committed, so any agent reaching 8a already has `step-08.md` loaded. The §2b read-set table maps
  **Step 8a → `step-08.md`** explicitly, so the reading contract points at something real rather
  than at a `§8a` that would resolve nowhere.

## Revision 2b — CRITICAL correction: byte equality and the block moves are mutually exclusive

An advisory cross-model pass (rejected as a gate verdict — it read a pre-edit doc, see the run log)
surfaced a **Critical internal contradiction between two of this design's own revisions**, and it is
right:

- §1 (as amended by pass-1 F3) requires the final concatenation of the split files to equal the
  `origin/main` `steps.md` blob **byte-for-byte, immediately before the mechanical commit**.
- §3b moves `<completion-gate>`, `<probe-before-design>` and `<early-smoke-install>` **into**
  `step-16.md` / `step-03.md` / `step-08.md` — content that was never in `steps.md`.

Both cannot hold. Byte equality is destroyed the moment a block is moved in.

**Resolution — this is exactly why AC4 separates mechanical from content commits, so the fix is
sequencing, not weakening either check:**

1. **Commit 1 (mechanical, byte-equal).** Partition only. `concat(step-00-preamble, step files in
   glob order) == origin/main:steps.md` **byte for byte** — asserted from disk, before commit.
   `sync_shared_blocks.py` runs here and must be a **no-op on content** (the MANIFEST retarget only
   changes which file the block is generated INTO; the block text is already identical), and byte
   equality is re-asserted AFTER the sync to prove that. **No block moves in this commit.**
2. **Commit 2 (content — the §3b block moves + SKILL.md condensation).** Byte equality **no longer
   applies and must not be claimed.** Its verification is instead: (a) each moved block appears
   exactly once in the tree, in its new home; (b) the corpus still contains every moved block's
   canonical sentence (so content pins survive the move); (c) `sync_shared_blocks.py --check` clean;
   (d) full suite green. State plainly in the PR that byte equality bounds commit 1 only.

The earlier §1 wording "again after `sync_shared_blocks.py` and immediately before the mechanical
commit" stands — it scopes to commit 1. What was wrong was carrying that claim across the content
commit.

**Also recorded from the same advisory pass, for the next Step-4 pass to resolve properly:**

- **(High, design-flaw) §2b's read-set test may be circular.** A test asserting that a declared table
  contains the expected entries proves the *table*, not that the orchestrator *loads* those files.
  What IS mechanically checkable, and should replace the circular form: every prerequisite named in
  the table **resolves to a real file** (no dangling pointer), and every step file is named by at
  least one table row. That the agent actually reads them is **not** mechanically verifiable in this
  repo — say so as a stated limitation rather than implying coverage that does not exist.
- **(High, spec-tightening) §3b's "if ≥500, report AC1 partial" is a self-granted escape** that makes
  AC1 unenforceable. Either commit to <500, or record the deviation as an owner decision — not as an
  option the implementer may take unilaterally.
- **(High, ambiguity) does `step-00-preamble.md` count as a "step file"?** It matches the
  `step-*.md` glob but is not a step. The ordering guard and packaging assertion must state which set
  they mean; "19 files / 18 sections" is the honest split.
- **(Medium, ambiguity) §4c's six-areas-vs-six-pins count is inconsistent**: executor-vocabulary is
  one of the six *areas* but is excluded from the six canonical *prose pins*, so a meta-check
  asserting "exactly six" is checking two different sixes. Name them separately.
- **(Medium) §5b conflates the source-tree symlink with the shipped package.** Traversing
  `plugins/rawgentic/skills/<name>` proves the mirror, not the installed artifact.

---

# Revision 3 — the five open advisory findings, resolved. THIS IS THE SETTLED DESIGN.

Revisions 2/2a/2b left five findings open. All five are resolved below. **The doc is frozen from
here for the Step-4 pass-3 review** — no edits until that result lands (the pass-2 result was
rejected precisely because the artifact moved under it).

## R3.1 — AC1's escape hatch removed (was High, spec-tightening)

§3b previously let the implementer "report AC1 as deliberately PARTIAL". That is a self-granted
escape and it makes a numeric AC unenforceable. **Deleted.** The commitment is:

- The §3b block moves land SKILL.md at **≤480 lines** (from 549: −50 from the three block moves,
  the balance from folding `<references>` into the per-step table and cutting per-step bullet prose
  that merely restates `<happy-path>`/`<mandatory-steps>`).
- **If the measured result is ≥500, that is a BLOCKER, not an implementer's option.** It goes to the
  owner as a decision (ERROR protocol in an unattended run), because the alternatives — moving a
  block a gate depends on being always-loaded, or shipping an AC unmet — are both owner calls.

## R3.2 — "split file" vs "step section file", defined (was High, ambiguity)

The two sets were conflated. Fixed by naming them:

- **SPLIT FILES = 19** — every file matching `references/step-*.md`, i.e. `step-00-preamble.md` plus
  the 18 section files. This is the set the **ordering guard** and the **packaging assertion** use.
- **STEP SECTION FILES = 18** — the split files carrying a top-level `## Step` heading.
  `step-00-preamble.md` is NOT one: it holds the material before the first `^## Step ` boundary.

`step-00-preamble.md` deliberately matches the `step-*` glob so it sorts into the corpus at the slot
`steps.md` occupied (probed in §2). Wherever this design says "step file" unqualified, it means a
STEP SECTION FILE.

## R3.3 — the read-set test, de-circularised (was High, design-flaw)

The finding is correct: a test asserting a declared table contains its expected entries proves the
**table**, not that the orchestrator **loads** those files. That is circular, and shipping it as
AC6 evidence would be exactly the "guards that stop looking look like guards that pass" failure this
issue exists to avoid. Replaced with the checks that are genuinely mechanical:

1. **No dangling pointer** — every path named in the §2b read-set table resolves to a real file on
   disk. This catches the failure that actually bites: a reading contract pointing at a file the
   split renamed or never created (e.g. the `§8a` that resolves nowhere — see Revision 2a).
2. **Total coverage** — every one of the 19 SPLIT FILES is named by at least one table row, so the
   split cannot silently orphan a file that nothing tells the agent to read.
3. **ALWAYS row is present and names `step-00-preamble.md`**, pinned as one canonical sentence (this
   is a prose pin and counts toward R3.4's prose-pin set).

**Stated limitation, not papered over: whether the agent actually reads its resolved set is NOT
mechanically verifiable in this repo.** There is no harness that observes an LLM's file reads. The
three checks above bound the *contract*; the *behaviour* rests on the contract being legible and on
`<step-tracking>`'s markers. AC6's "no behaviour change" is therefore evidenced by byte equality
(commit 1) plus these contract checks — and the residual is named here rather than implied away.

## R3.4 — the two "sixes", separated (was Medium, ambiguity)

§4c asserted "exactly six" over a mixed set. The six CONTRACT AREAS are not six prose pins, because
one of them ("no executor vocabulary") is a negative invariant over a scope. Fixed:

- **Six contract areas** (unchanged as the RETENTION criterion): mandatory review sites ·
  reviewer≠author · artifact delivery · loop-back debit · deferral honesty · no executor vocabulary.
- **Meta-check asserts, separately and by name:** (a) **five canonical prose-sentence pins**, each
  anchoring ONE sentence in ONE owning file, one owner apiece; and (b) **one negative
  executor-vocabulary scan**, with the files it scans enumerated in the test. Plus the R3.3 ALWAYS-row
  pin. **No assertion counts prose pins and the negative scan in the same total.**

## R3.5 — packaging claim scoped to what a source-tree test can prove (was Medium)

Traversing `plugins/rawgentic/skills/<name>` (a whole-directory symlink to `../../../skills/<name>`)
proves the **source-tree mirror**, not the **installed plugin**. Scoped honestly:

- **Asserted in-suite:** all 19 SPLIT FILES are reachable and readable through the mirrored path, so
  the Codex mirror cannot silently omit them. Home: `tests/test_codex_plugin_packaging.py`.
- **NOT asserted, and said plainly:** that an installed plugin cache contains them. The suite runs on
  the source tree; it has no installed artifact to inspect, and inventing one would be a fake check.
  The real guard there is §7 of the repo manual (reinstall + new session), which is a human step.

## Net plan (unchanged in shape, precise in content)

1. **`refactor(wf2)` — mechanical, BYTE-EQUAL.** Run the proven partition script; assert byte equality
   from disk in glob order against `origin/main:steps.md`; delete `steps.md`; retarget
   `sync_shared_blocks.py` MANIFEST :70 → `step-12.md`; run the sync; **re-assert byte equality after
   the sync** (it must be a content no-op); redirect every location pin to its owning file; add the
   §4b ordering guard (19-name exact list + contiguous interval in the FULL unfiltered glob), the
   R3.3 contract checks, and the R3.5 mirror assertion; rewrite budget rows. **No block moves.**
   Suite green here.
2. **`refactor(wf2)` — content: the §3b block moves + SKILL.md condensation to ≤480.** Byte equality
   NO LONGER applies and is not claimed; verified by each moved block appearing exactly once in its
   new home, the corpus still carrying its canonical sentence, `--check` clean, suite green.
3. **`test(wf2)` — the AC3 sweep**, per ASSERTION, never deleting a structural/negative guard;
   R3.4's meta-check; every deletion enumerated in the PR body.
4. **`test(wf2)` — AC7 STALE CEILING**, symmetric, `per_file = max(256, ceil(actual*0.05))`,
   `total = max(1024, ceil(actual_total*0.02))`, diagnostics naming path/actual/ceiling/allowed/excess;
   unit tests for over-margin, within-margin, total analog.
5. **`chore(wf2)` — version ×3 + `README.md:241` + changelog** (the `Suite old→new` tail goes DOWN).

---

# Revision 4 — Step 4 pass 3 (FRESH, valid verdict) dispositions

Pass 3 ran against frozen doc `3459a42c…` and came back **FRESH: True** — the first valid gate verdict
in this gate. 4 findings (2 High, 2 Medium); volume clear (High 2/5, Medium 2/10). Breaker: F3 was
`ambiguity_flag: true`, dissolved below → **clear**. Fold over adopted Critical/High →
`classify_loopback_source(['design-flaw'])` → **`design`**; second and last design loop-back consumed
(**design 2/2, global 2/3**).

## F1 (High, design-flaw) — PARTIALLY ACCEPTED, with a mechanism that makes it observable

The finding: the R3.3 checks prove paths exist and are mentioned, not that the execution path loads
them — an agent can omit `step-00-preamble.md`, lose lane/delegation/review-gate semantics, and leave
every test green. R3.3 already conceded this as a stated limitation; the finding is that a conceded
limitation is not AC6 evidence. **That is fair, and there is a concrete remedy I had missed: convert
the unobservable into an observable using the machinery this repo already has.**

- **The reading contract requires each step's `— DONE` marker to record the reference files it
  loaded.** `<step-tracking>` markers are already mandatory, append-only, and grepped by the
  resumption protocol, so a run that skipped `step-00-preamble.md` becomes **visible after the fact
  in session notes** rather than invisible forever. A drift guard pins that requirement sentence.
- This does not make loading *enforced* — nothing in this repo can — but it makes an omission
  **detectable and auditable**, which is the difference between a residual risk and a blind spot.
- **AC6's evidence boundary, stated for the PR body:** byte equality (commit 1) proves content
  preservation; the contract checks prove the pointers are real and total; the marker requirement
  makes read-set omissions auditable. **What remains unevidenced: that an agent in fact reads its
  resolved set.** No harness observes an LLM's reads. This is disclosed, not implied away.

## F2 (High, design-flaw) — REFUTED with cited evidence

The finding: the source-tree symlink does not prove packaging includes the 19 new files, so a release
could install an incomplete skill. **Refuted — per-file omission is not a reachable failure mode,
because nothing enumerates files.** Evidence, all read at `590c6d4e`:

- `.claude-plugin/plugin.json` keys are `author, description, homepage, keywords, license, name,
  repository, version` — **no `files`/`include`/`skills` enumeration**.
- `plugins/rawgentic/.codex-plugin/plugin.json` → `"skills": "./skills/"` — a **directory** string,
  not a file list.
- `plugins/rawgentic/skills/implement-feature` → `../../../skills/implement-feature`, a
  **whole-directory** symlink.
- `git grep -l 'references/steps.md|references/\*.md' -- '*.json'` → **no matches**: no tracked
  manifest names reference files at all.

A new `references/*.md` therefore ships iff its skill directory ships. The only reachable failure is
the whole skill directory being absent, which existing guards already cover
(`test_codex_plugin_packaging.py`'s symlink+resolve assertions, and the marketplace whitelist tests).
R3.5's scoping stands; no new packaging assertion is warranted, and inventing one would be a check
that cannot fail for the stated reason.

## F3 (Medium, ambiguity) — DISSOLVED by adopting the computed-set form

The executor-vocabulary scan's authoritative file set was undefined, so an implementation could scan a
narrow subset and still satisfy the meta-check. Dissolved with the stronger form: **the scan set is
COMPUTED from the tree** — `skills/implement-feature/SKILL.md` plus every `references/step-*.md`
(all 19 SPLIT FILES, via the glob) — **never a hardcoded list**. A file added later is therefore in
scope automatically, and the test asserts the computed set is non-empty and contains all 19 split
files so an empty/narrowed glob cannot pass vacuously.

## F4 (Medium, consistency) — ACCEPTED, sequencing corrected

Correct and I had missed it: commit 1 finalized budget rows, but commit 2 then adds blocks to
`step-03.md` / `step-08.md` / `step-16.md` and removes content from `SKILL.md`, so commit 1's ceilings
would be wrong the moment commit 2 lands — and would later trip AC7's own symmetric stale-ceiling
check. **Corrected: commit 1 writes PROVISIONAL rows (so its suite is green), and commit 2
RECALIBRATES every affected row against measured post-move sizes.** AC7's check lands in commit 4,
after all sizes are final — so the check never runs against rows it would itself condemn.

## Remaining budget and the terminating path

**design 2/2 (cap reached), global 2/3.** Pass 4 runs against this revision. If it returns another
Critical/High design finding, the `design` source cap is reached while the global cap is not, so —
provided the breaker is `clear` and no finding is ambiguous/conflicting — **the #798 carve-out
applies: Step 4 CLOSES budget-exhausted via `plan_lib.py close-design-gate` and continues to Step 5.
That is a legitimate close, not an ERROR**, and on an unattended run it never triggers the ERROR
protocol. Note its preconditions strictly: a refusal caused by the GLOBAL cap, or involving any other
source, or any ambiguous/conflicting finding, still STOPS and escalates.

---

# Revision 5 — Step 4 pass 4: §3b is REFUTED by evidence. AC1 is an owner blocker.

Pass 4 (FRESH, 4 findings, **0 ambiguous → breaker clear**, volume clear) produced the finding that
invalidates this design's AC1 resolution. Recorded before any close.

## F4 (Medium) — CONFIRMED against the tree, and it kills §3b

§3b claimed the three moved blocks are "each consulted by exactly one step". **I never verified that,
and it is FALSE for all three.** Grepped at `590c6d4e`:

| Block | Actually referenced from |
|---|---|
| `<completion-gate>` | `steps.md:810` (**Step 5**), `:1159` (**Step 9**), `:1619` (**Step 12**), `SKILL.md:242` (termination-rule) — not just Step 16 |
| `<probe-before-design>` | `steps.md:520`, `:599` (**Step 4's own platform-feasibility check**), `SKILL.md:498` (Step 3) — not just Step 3 |
| `<early-smoke-install>` | `steps.md:940` (Step 8), `:1745` (**Step 15**) — not just Step 8 |

Moving any of them into a single step file would break the other steps' access to it — and would
worsen the very P1/pass-1-F1 under-reading hazard this design spent three revisions closing. **§3b is
withdrawn.** These blocks stay in the always-loaded spine.

## Consequence: AC1's `<500` is a BLOCKER for the owner, not an implementer's call

With §3b withdrawn there is no safe path to `<500`:

- SKILL.md is **549 lines; 486 inside blocks; ~63 outside** — trimming the per-step pointer list
  cannot find 50 lines.
- **165 block lines are shared-block GENERATED** (`<model-routing-resolve>` 75,
  `<review-severity>` 51, `<loop-back-budget>` 39). Editing them inline fails
  `sync_shared_blocks.py --check` (repo mistake #14), and `review-severity` is shared with **fix-bug**,
  so reshaping at source changes WF3 — outside this issue's scope.
- Every remaining block is cross-step or always-loaded, per the table above.

**R3.1's own clause therefore fires:** "If the measured result is ≥500, that is a BLOCKER, not an
implementer's option. It goes to the owner as a decision." Pass 4's F2 independently demands the same
— that the workflow must not proceed to implement a design carrying a known unmet AC purely because a
review budget ran out. Both point the same way.

**The split itself (AC2–AC7) is unaffected and remains ready to implement.** Only AC1's numeric target
is blocked. The owner's decision is between:

- **(i) Ship the split with AC1 recorded as unmet**, SKILL.md ending ~549 lines (still a short index by
  role, just not <500). Cheapest, delivers all the progressive-disclosure value.
- **(ii) Widen scope** to reshape `shared/blocks/review-severity.md` et al. at source, accepting a WF3
  blast radius and a bigger review surface.
- **(iii) Re-scope AC1** to a different, achievable target (e.g. "SKILL.md gains no lines" or a byte
  ceiling), amending the issue.

Recommendation: **(i)** — it delivers AC1's actual intent (a spine that does not load 18 steps) and the
numeric target was authored against a 532-line file that has since grown to 549 for unrelated reasons.

## The other pass-4 findings

- **F1 (High) — accepted, claim weakened.** The `— DONE` marker records the agent's **self-reported
  attestation**, not an observed file-read; an agent could copy the filename in without reading. So the
  marker makes an omission *auditable when honestly reported*, and nothing more. It is no longer
  offered as AC6 evidence, only as a detection aid.
- **F2 (High) — accepted as a machinery observation, and it is why this gate ESCALATES rather than
  taking the #798 close.** The carve-out would let the run proceed on budget exhaustion alone; with an
  unmet AC that needs owner acceptance, closing-and-continuing would be exactly the failure F2 names.
  Worth filing against WF2 itself (a #798 refinement: require explicit disposition of any surviving
  Critical/High before a budget-exhausted close).
- **F3 (Medium) — accepted.** Installed-plugin validation is the repo-manual §7 human step
  (`claude plugin remove/install` then a fresh session, checking the reported version). In-suite scope
  stays the source-tree mirror per R3.5 and the F2 refutation in Revision 4.
