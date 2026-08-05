# #909 — Skill description diet + incident references/ split

**Issue:** [#909](https://github.com/3D-Stories/rawgentic/issues/909) — `chore(skills):` · epic [#875](https://github.com/3D-Stories/rawgentic/issues/875) (M1 — STAY SMALL)
**Date:** 2026-08-05 · **Base:** `1a03b9cb` · **Complexity:** standard_feature · **Lane:** full spine (9 impl files > 7)
**Task class:** production (provenance=default)

---

## 1. Problem, as measured

All figures are first-hand at `1a03b9cb`, by exact `yaml.safe_load` over `skills/*/SKILL.md`.

| measure | value |
|---|---|
| skills | 21 |
| total description chars, **as the harness loads them** | 10,784 |
| descriptions over Anthropic's documented 1,024-char cap | **1** (`pane-handoff`, 1,174) |
| next largest | revalidate-children 991 · adversarial-review 842 · create-issue 811 · peer-consult 690 |
| `incident/SKILL.md` | **537 lines**, 25,416 bytes, **0** `references/` files |

Two Anthropic-published limits are in play: the `description` maximum of 1,024 characters
(skill-authoring best practices, platform.claude.com, fetched 2026-08-04 per the issue) and the
"keep SKILL.md under 500 lines" guidance. Claude Code does not enforce the former client-side, so
`pane-handoff` works today by luck, not by contract.

### 1a. A defect the issue does not name, found while measuring

In an **unquoted YAML plain scalar, ` #` opens a comment.** Two descriptions therefore lose their
tail in what the harness actually loads:

| skill | true chars | loaded chars | lost | cut at |
|---|---|---|---|---|
| `epic-run` | 534 | **131** | **403 (75%)** | `epic #N"` |
| `pane-handoff` | 1,203 | 1,174 | 29 | ` #713)` |

`epic-run` loses every trigger phrase after *"cycle through all issues in epic"* — including
*"write me a goal for the epic"*, *"auto-run these children"*, and the whole `Do NOT use` clause.
`pane-handoff` loses `Requires HERDR_ENV=1.`, its own gating precondition.

**Confirmed twice, independently:** (a) `yaml.safe_load` over the repo tree; (b) the
available-skills listing injected into a live Claude Code session on 2026-08-05, which ends
`epic-run`'s entry mid-phrase at exactly that byte. `peer-consult` is **not** affected — it is
single-quoted; an initial 2-char discrepancy was the measuring regex counting its own quote
characters, and is retracted.

Owner authorized folding the fix into this issue on 2026-08-05 ("fix that").

---

## 2. Scope decision (owner, 2026-08-05 — D211)

Asked while the owner was present, before the run went unattended. Chosen: **cut only what is
broken.** Rejected: rewriting all 21 descriptions to shrink the 10,784-char total; and a
per-skill-cap-plus-total-budget variant.

Rationale for the rejection, in the owner's terms: every rewrite is a chance a skill stops firing
on the words that used to trigger it, and the evals do not cover every phrasing. The
context-per-session win is therefore **smaller than the issue's problem statement implies**, and
that is a deliberate, recorded trade.

So AC1 here means: the one over-cap description, plus body-material that is objectively
misfiled — not a stylistic pass over descriptions that are merely long.

---

## 3. Design

### AC1 — descriptions

| skill | change | why |
|---|---|---|
| `pane-handoff` | **the exact cut, named:** remove the opening workflow-summary clause (`— spawn it, bind it, give it the prompt, arm its goal, each step verified against the successor's own artifacts`) and the trailing rationale/provenance (`because that is when the pass-off is expected to happen and there may be nobody awake to answer (owner decision 2026-07-29, #713)`). Target **≤ 900 loaded chars**, not merely ≤ 1,024, for regrowth headroom.
**The arithmetic, stated because it did not previously add up:** true length 1,203; the two fragments
above are 111 and 129 chars, so removing both lands at **963** — under the cap but 63 over the 900
target. Three further compressions close the gap, none of which touches a pinned phrase:
`Use whenever the user asks to pass off, pass over, hand off or send work to another pane or session,
however phrased` → `Use when the user asks to hand work to another pane or session, however phrased`
(−38); `Dictated variants are the same request — "herder" means herdr and "pain" means pane.` →
`"herder" = herdr, "pain" = pane (dictated variants).` (−33); and `do not offer, do not ask "say the
word"` → `do not offer` (−26). That reaches ≈ 866. The **guard** enforces 1,024 (repository policy);
900 is the working target, and the final char count is recorded in the PR body. **KEEP** every dictated variant, the `ALSO RUN it unprompted … (#732)` **directive**, and `Requires HERDR_ENV=1.` | the only skill over the cap. The two cut fragments are exactly what the bar excludes — a workflow summary and a rationale. The directive is NOT cut even though `skills/pane-handoff/SKILL.md:27-28` repeats the rule in the body, because the body loads only once the skill is invoked and this directive exists to fire when nobody invoked it |
| `epic-run` | single-quote the scalar so the 403 lost chars load | 75% of the description is dead text today |
| `adversarial-review` | move `pip install "zhipuai>=2.1.5"` + the Codex-CLI/API-key install notes to the body | install instructions are body material by the repo's own bar; the description keeps *when to use it* |
| `peer-consult` | same install-note move | identical defect, identical fix; already quoted so no scalar change |
| everything else | untouched | not broken (§2) |

`revalidate-children` (991) and `create-issue` (811) are **left alone deliberately**: both are
under the cap and both are trigger-phrase text, not workflow summary or install notes.

**Quoting style — single quotes, not a block scalar.** Both are YAML-spec fixes. Single-quoting is
the one whose live behavior is *confirmed*: `peer-consult` is single-quoted in this repo today and
its description renders complete in a live session's available-skills listing. Block-scalar
(`>-`) precedent exists (`~/.claude/skills/local-llm-recommender`, Anthropic's own firecrawl
plugin) but **no** block-scalar skill's description is displayed in that listing, so its rendering
by Claude Code's loader could not be confirmed either way. Choosing the confirmed mechanism.
Cost: internal apostrophes double (`epic's` → `epic''s`). The AC3 guard verifies the parsed
result, so a mis-escape fails CI rather than shipping.

### AC2 — incident split

`incident/SKILL.md` 537 lines → SKILL.md keeps the contract and the spine; procedure detail moves
to a **flat** `references/` (no subdirectories — #874 lesson 2, because `tests/corpus.py` reads a
flat `references/*.md` glob and a subdirectory would silently un-pin every content guard).

| file | content |
|---|---|
| `skills/incident/SKILL.md` | frontmatter · `<role>` · `<constants>` · `<phase-step-mapping>` · `<config-loading>` · `<learning-config>` · `<environment-setup>` · `<step-marker-enforcement>` · `<termination-rule>` · `<ambiguity-circuit-breaker>` · `<context-compaction>` · `<principle-relaxations>` · **`<mandatory-verification>` (SEV-1/SEV-2)** · **the destructive-action approval rule** · `<references>` · one-line-per-step spine with read pointers · `<mandatory-rule>` · `<completion-gate>` · Workflow Resumption |
| `references/quick-diagnostic-playbook.md` | the three playbooks (service / database / service-specific) |
| `references/phase-a-stabilize.md` | Steps 1–6 instructions + failure modes — **elaboration only**; the two safety rules above are NOT moved here |
| `references/phase-b-analyze.md` | Steps 7–14 instructions + failure modes |

**Why the two safety rules stay in SKILL.md — a self-contradiction in this design's first draft,
caught at the Step-4 gate.** That draft asserted "a split that relocated a safety gate into a
lazily-read file would be the real hazard here" and then moved `<mandatory-verification>` into
`phase-a-stabilize.md`, doing precisely that. A read condition is **prose-enforced, not
loader-enforced**: references *are* read in practice — `implement-feature` runs entirely on 23 of
them, and this very issue was executed by reading `references/step-01.md` … `step-05.md` on the
spine's instruction, which is direct live evidence that the mechanism works — but "the model
complied with an instruction" is not "the requirement cannot be bypassed". For ordinary procedure
detail that is an acceptable trade. For **SEV-1/SEV-2 restoration verification** and **approval
before a destructive rollback or DB operation**, it is not: an incident response could skip either
and produce no test failure and no runtime error. So both rules stay in the always-loaded body,
and Phase A's reference carries only their elaboration.

**`${CLAUDE_PLUGIN_ROOT}` does not constrain this split:** it occurs **zero** times in
`incident/SKILL.md` (grep, `1a03b9cb`), so the CLAUDE.md §1 rule that it is not substituted inside
`references/*.md` has nothing to bind here.

**Two location pins must be handled — this is the split's only real hazard.** Both read
`SKILL.md` directly rather than via `skill_corpus`, and both assert **ordering**:

| guard | asserts | lives in |
|---|---|---|
| `tests/test_incident_skill.py:16-30` | `gh label create incident` precedes `gh issue create … --label incident` | both on SKILL.md:150 (Step 1 item 5) |
| `tests/test_wf2_clarity.py:424-431` | `git fetch origin` precedes `git checkout -b hotfix/` | both on SKILL.md:369 (Step 10 item 1) |

Handling: each pinned pair stays **inside one destination file** (pair 1 → `phase-a-stabilize.md`,
pair 2 → `phase-b-analyze.md`), so relative order survives, and both guards are **rebuilt on a
`{path: text}` mapping** (see the next two paragraphs for why *not* `skill_corpus()`). Rebuilding
them is *not* weakening them: neither pin has any reason to require SKILL.md specifically; what they
protect is "the bootstrap comes first", wherever the prose lives.

**But a naive corpus migration WOULD weaken them, so it is not what ships.** In a joined corpus,
`git fetch origin` occurring anywhere earlier — in a different file, for a different purpose —
satisfies a bare "fetch appears before checkout" assertion vacuously.

**And `skill_corpus()` cannot express the fix, which is a fact about it, not a guess.**
`tests/corpus.py:19-26` returns `"\n".join(parts)` — a plain string with **no** file provenance, so
no assertion over that value can prove two matches came from the same file. A "same-file" claim
built on the joined string would be another vacuous assertion wearing a stronger label. Therefore
the migrated guards do **not** consume `skill_corpus()`; they build a `{path: text}` mapping of
`SKILL.md` plus each `references/*.md` and assert, over that mapping:

1. each protected command occurs **exactly once** across the whole mapping (a duplicate fails, so a
   reference file cannot clone a canonical pair and make ordering ambiguous);
2. both members of a pair live in the **same, named** file; and
3. within that one file, the required command precedes the other by offset.

`skill_corpus()` remains the right tool for ordinary *content* pins, which only ask whether prose
still exists somewhere; it is the wrong tool for an *ordering* pin, which needs provenance it does
not carry. This is strictly stronger than today's single-file `find()` pair.

**Discoverability (the split's other real risk).** References load lazily, so prose that moves out
of SKILL.md is prose Claude may never read. Every extracted file therefore gets an explicit link
**and a read condition** in its spine step — the `implement-feature` pattern, e.g. *"(read
references/phase-a-stabilize.md before executing Steps 1–6)"*. A split without read conditions
would trade a too-long SKILL.md for silently-skipped procedure, which is worse.

**Line target: ≤ 400 lines** (from 537), not the tightest possible number. The 500-line guidance is
about SKILL.md, not the corpus, and leaving ~100 lines of headroom keeps the file from re-crossing
the line on the next operative edit — while an over-aggressive strip is what makes detail
undiscoverable.

Three additional structural guards, all cheap and all protecting constraints the ACs state but
nothing currently enforces: `references/` is **flat**, spine links and reference files are in
**bijection with a read condition each**, and no reference file contains `${CLAUDE_PLUGIN_ROOT}`.

**The link guard runs in BOTH directions, plus the read condition** — a one-way "every link
resolves" check would pass an **orphan** reference file that nothing links to, and would equally pass
a bare link with no `read … before executing` clause, so the discoverability requirement would be
satisfied on paper while the file stays unreachable in practice. It therefore asserts: every
`references/*.md` is linked from the spine, every spine link resolves to a file that exists, and each
such link carries its read condition. The last is safe to apply repo-wide: measured
**zero** occurrences across all `skills/*/references/*.md` today, while three SKILL.md bodies use it
legitimately and are untouched.

**The flat-layout guard must traverse RECURSIVELY, or it is the bug it exists to catch.** Written
with the same `references/*.md` glob the content pins use, it would inspect only direct children and
therefore *silently ignore* a nested reference — the exact un-pinning failure it is meant to
prevent. It is specified as a **recursive** walk below `references/` that fails when any discovered
descendant is not a direct `.md` child (i.e. its path relative to `references/` has more than one
component), with a synthetic nested-reference fixture that must fail. This is the same asymmetry
`tests/test_wf2_prose_budget.py` already documents — its corpus glob is recursive precisely so it
does not share the flat glob's blind spot — so the pattern is established here, not invented.

### AC3 — the guard

New `tests/test_skill_description_budget.py`, mirroring `tests/test_wf2_prose_budget.py`'s shape
(a pure violation collector + unit tests over it with synthetic inputs, then tests over the real
tree, every message naming the skill, the character count, and the character overage — characters,
not bytes, matching the unit the cap is measured in). No existing guard covers this surface —
grep for `1024` / `len(desc` / `DESCRIPTION` across `tests/` returns nothing.

**Parser, named and cited:** the guard uses **PyYAML** (`import yaml`, `yaml.safe_load`), which is
declared in the test lane's dependency line — `.github/workflows/ci.yml:47`,
`pip install pytest jsonschema pyyaml`, Python 3.12 — and already has an exact existing CI call site
in `tests/test_lint_workflow.py`, which imports `yaml` and passes in that lane today. So the guard
cannot fail at import time in CI, and it parses with the same library used for every measurement in
this document. The lint lane installs only `pylint` and runs with `--disable=import-error`, so the
import is a non-issue there.

Three checks:

1. **Present, a string, and non-empty** — in that order, as three distinct diagnoses. The type
   assertion is separate on purpose: `description: [foo]` is a YAML **sequence** that is non-empty,
   under the cap, and free of ` #`, so a length-only guard accepts frontmatter the loader cannot
   use. Sequence / mapping / numeric / boolean fixtures must all fail with the **parsed type named**;
   an absent key or explicit `description:` null reports MISSING rather than a type error, because
   "there is no description" is the more accurate diagnosis.
2. **Per-skill cap:** loaded description ≤ 1,024 chars, measured in Unicode characters on the
   **loaded** value (not the raw bytes). Names skill, chars, overage.
3. **No silent truncation:** compliant iff the scalar is quoted/block — first non-space character
   after `description:` ∈ `'`, `"`, `>`, `|` — **or** its scalar source contains no ` #`. Two clean
   primitives, **no length arithmetic**: a prototype that compared parsed length against raw length
   produced a false positive by counting a quoted scalar's own quote characters, so that approach is
   rejected on evidence. Intent-free — it detects the *hazard*, never a guess at authorial intent.
   Block scalars are exempt because `#` is literal inside them, which is why the check tests the
   scalar style rather than the text alone.

   **"Scalar source" is defined exactly, because a vague boundary makes check 3 two different
   checks.** The span runs from immediately after `description:` to the start of the **next
   top-level frontmatter key** (`^[A-Za-z][A-Za-z0-9_-]*:`), or to the end of the frontmatter if
   there is none. That boundary is what makes the check scalar-*local*: inspecting only the
   `description:` line would miss a ` #` on a **continuation line** of a multi-line plain scalar,
   while scanning the whole frontmatter would wrongly reject a legitimate comment belonging to
   `argument-hint:` or `name:`. A multi-line plain scalar whose ` #` sits on a continuation line is
   therefore a required fixture alongside the four style fixtures — it is the case both naive
   implementations get wrong.

**Fixture set (why the guard is trustworthy, not just present).** Check 3 is only credible if it
demonstrably fires, so the unit tests run it over synthetic **plain / single-quoted / double-quoted
/ folded-block** frontmatter, and include the regression fixture
`description: trigger A # trigger B` — which **parses cleanly and lands far under the cap**, so a
length-only guard passes it while half the triggers are silently dead. That fixture is the precise
reason length alone is the wrong surface.

Deliberately **no** per-skill budget dict and **no** total budget. Unlike the prose-budget guard,
the population is discovered by glob and every member is covered by one constant, so a new skill
cannot evade the guard and there is no stale-entry class to maintain. The total budget is the
option §2 rejected.

### Not in scope
Trimming the remaining 17 descriptions; a total-char ceiling; `implement-feature`'s 549 lines
(that is #899).

### Security implications

**None material, and the reason is structural, not a shrug:** this change adds no code path, takes
no input, constructs no subprocess, touches no credential, secret, auth, or network surface, and
changes no file outside `skills/**/*.md`, `tests/`, two `plugin.json` version strings, and
`README.md`. The one new executable artifact is a test that parses **this repo's own committed**
frontmatter — in-repo, authored here, not untrusted input — so the YAML-deserialization risk
criterion does not apply. No task path matches `DEFAULT_HIGH_RISK_PATH_PATTERNS`.

The one security-adjacent property worth naming: **`<mandatory-verification>` and the
destructive-action approval rule remain in `skills/incident/SKILL.md`;
`references/phase-a-stabilize.md` contains elaboration only. Read conditions are not relied on to
enforce either safety gate.** The `<termination-rule>`, `<ambiguity-circuit-breaker>` and
`<completion-gate>` that enforce the workflow's floor likewise stay in SKILL.md.

(This sentence previously said `<mandatory-verification>` "moves with Phase A", contradicting AC2
after AC2 was corrected — caught by the pass-2 gate review as its highest-severity finding. An
implementer following the old text would have moved a SEV-1/SEV-2 verification gate into a lazily
read file, which is precisely the hazard this design names. Recorded rather than silently patched,
because a fix applied in one place and missed in another is the defect pattern here.)

### Error handling and failure modes

| failure mode | how it shows up | defense |
|---|---|---|
| a description edit silently stops a skill triggering | no error at all — the skill just never fires, which is how #700 went unnoticed for 36 hours | phrase-coverage test over every eval prompt; every dictated variant kept verbatim |
| a mis-escaped apostrophe in a single-quoted scalar | frontmatter fails to parse → the skill may not load | the guard parses **every** `skills/*/SKILL.md`, so a broken scalar fails CI immediately |
| the split makes procedure undiscoverable | Claude executes a step without its detail; silent quality loss | explicit link + read condition per spine step; ≤ 400-line target keeps the spine readable |
| an ordering guard goes vacuous after the move | CI stays green while the protected invariant is unprotected | exactly-once + same-file + offset assertions, duplicates fail |
| a reference file re-crosses into a subdirectory later | flat-glob content pins silently un-pin (#874 lesson 2) | flat-layout guard |
| regrowth past the cap | nothing locally — see the note below on what is and is not proven | the cap guard, at 1,024 with a 900-char working target |

All are **fail-loud by construction** — each is a CI test failure, not a runtime surprise. The one
that cannot be made fail-loud in this repo is "the loaded description is subtly worse at
triggering", which is a judgment surface; phrase coverage bounds it but does not eliminate it, and
that residual is stated rather than papered over.

**What the 1,024 cap is, precisely.** It is the maximum published in Anthropic's skill-authoring
documentation, adopted here as a **repository policy limit**. The earlier draft went further and
claimed an over-cap description "would fail API-side validation" — that claim is **withdrawn**,
because the only evidence for it is documentation, and this repo's own bar explicitly rejects docs
as feasibility evidence (docs prove an API exists, not that a given path enforces it). Not proven,
and deliberately not asserted: that any request path this project uses rejects a 1,025-character
description, and that the platform counts Unicode code points rather than bytes or grapheme
clusters. The guard measures Python `len()` on the loaded string, which is code points. If the real
boundary later turns out to be bytes, the cap is conservative for ASCII and would need revisiting
for multi-byte text — recorded here so a future reader does not re-derive it as settled.

---

## 4. platform_apis (feasibility declaration, #226/#490)

| API / behavior | claim | evidence |
|---|---|---|
| YAML plain scalar treats ` #` as a comment start | **confirmed** | `yaml.safe_load` over the two affected files reproduces the exact truncation point; corroborated by a live session's own available-skills listing ending `epic-run` mid-phrase at that byte |
| A single-quoted description loads complete, through Claude Code's own loader | **confirmed, live, exact mechanism** | `skills/peer-consult/SKILL.md` is single-quoted today and its full description — including internal double quotes and a trailing `ZHIPUAI_API_KEY.` — appears complete in the available-skills listing of the session that wrote this doc. Not a proxy composition: same repo, same loader, same frontmatter field |
| A block-scalar (`>-`) description loads complete | **NOT confirmed — and therefore not used** | precedent files exist but none of their descriptions is displayed in the listing, so nothing could be observed either way |
| `tests/corpus.py::skill_corpus` = SKILL.md + sorted flat `references/*.md` | **confirmed** | `tests/corpus.py:19-26`, read directly |
| `plan_lib.lane_decision` with `laneImplExtensions=[".md"]` | **confirmed** | run: `count_impl_files` = 9 → `tier=full`, reason "9 impl files > 7 — full spine" |
| `tests/corpus.py::skill_corpus` carries file provenance | **confirmed FALSE** — which is why the ordering guards do not use it | `tests/corpus.py:19-26` returns `"\n".join(parts)`, a plain string. Read directly, not inferred |
| A `references/*.md` file is actually read at run time | **confirmed by working precedent, but prose-enforced** | `implement-feature` runs on 23 reference files, and this issue's own execution read `references/step-01.md`…`step-05.md` on the spine's instruction. Enough for procedure detail; deliberately NOT relied on for the two `incident` safety rules, which stay in the always-loaded body |
| The 1,024 cap is enforced by an API this project calls | **NOT confirmed — claim withdrawn, adopted as repo policy instead** | only documentation supports it, and docs are not accepted feasibility evidence by this repo's own bar. No over-cap rejection was observed through any configured path, and the platform's counting unit is unverified |

**Likeliest-wrong claim** (named per #226): that Claude Code's loader is YAML-spec-compliant in
*both* directions — i.e. that single-quoting fixes it and not merely that plain scalars break it.
The break direction is directly observed; the fix direction rests on `peer-consult` rendering
complete, which is strong but is one instance. If it were wrong, the fallback is to reword the two
descriptions so no bare ` #` appears at all, which needs no parser cooperation.

---

## 4a. Step-4 gate, pass 1 — findings and dispositions

Cross-model design review, backend `gpt`, reviewer `gpt-5.6-sol`, author `claude-opus-5[1m]`,
158.8s, `status: success`, `diagnostic: true`, freshness verified (result `input_sha256` matched
the reviewed file byte-for-byte). Raw result:
`claude_docs/.wf2-state/909/step4-design-review.json`. Every finding was checked against its cited
evidence before disposition; **all five were confirmed real** and are applied above.

| # | sev | category | finding, compressed | disposition |
|---|---|---|---|---|
| F1 | High | correctness | phrase coverage cannot replace the triggering gate — containment does not exercise skill *selection*, so CI stays green while the skill silently stops firing | **applied, scoped.** Diagnosis accepted in full and the overclaim withdrawn; phrase coverage is now stated as a preservation check, necessary-not-sufficient, with the residual named. The recommendation's behavioral release gate is **declined for this issue** — no such harness exists here and building one means installing a plugin build and observing live sessions, a new feature outside these ACs. Filed as a follow-up |
| F2 | High | security | the design called relocating a safety gate into a lazily-read file the real hazard, then moved `<mandatory-verification>` into `phase-a-stabilize.md` — doing exactly that | **applied in full.** `<mandatory-verification>` and the destructive-action approval rule now stay in SKILL.md; Phase A's reference carries elaboration only. A genuine self-contradiction, and the most valuable finding of the pass |
| F3 | Medium | ambiguity | "its raw text" was undefined for a multi-line plain scalar — line-only inspection misses a continuation-line ` #`, whole-frontmatter scanning wrongly rejects other fields' comments | **applied.** The span is now defined exactly (after `description:` → next top-level key), and a multi-line-plain-scalar continuation fixture is required. Already the implemented behavior; the defect was the doc's silence |
| F4 | Medium | feasibility | "would fail API-side validation" rests on documentation only, and the platform's counting unit is unverified | **applied in full.** Claim withdrawn; 1,024 is stated as a repository policy limit, with the counting-unit caveat recorded |
| F5 | Medium | feasibility | `skill_corpus()` is a joined string with no file provenance, so the promised "same-file" ordering assertion is not implementable on it | **applied in full.** Verified against `tests/corpus.py:19-26` — the reviewer is factually right. Ordering guards now use a `{path: text}` mapping; `skill_corpus()` keeps only the content pins |

**Loop-back:** two Critical/High findings, one of them `category: security` (which contributes
`untagged` unconditionally), so the fold returned `design`, not the cheap `spec_tighten` path. One
`design` loop-back consumed — state `{design: 1, total: 1}` against caps 2 and 3 — and the design
was revised and re-gated rather than waved through.

### Pass 2 — findings and dispositions

Same reviewer and shape, run against the pass-1-revised document; freshness verified. Raw result:
`claude_docs/.wf2-state/909/step4-design-review-pass3.json` is pass 3;
`…-pass2.json` is this one. 6 findings (3 High, 2 Medium, 1 Low).

| # | sev | category | finding, compressed | disposition |
|---|---|---|---|---|
| P2-F3 | High | security | the **Security implications** section still said `<mandatory-verification>` "moves with Phase A", directly contradicting the AC2 fix from pass 1 | **applied verbatim** as the reviewer worded it. A genuine live contradiction: pass 1's fix was applied in AC2 and missed in the Security section — a fix in one place and not the other, which is the exact defect pattern this issue is about. Highest-value finding of the pass |
| P2-F4 | Medium | ambiguity | "distinctive trigger phrase" had no deterministic rule, so the preservation test was not reproducible | **applied.** The doc now states what the code already does: an explicit enumerated 17-phrase tuple, not a runtime-derived substring, plus why `say the word` is deliberately excluded |
| P2-F5 | Medium | feasibility | the guard's parser was unnamed and its CI availability uncited, so the test could fail at import time | **applied.** Named PyYAML and cited `.github/workflows/ci.yml:47` (`pip install pytest jsonschema pyyaml`, Python 3.12) plus the existing passing call site `tests/test_lint_workflow.py`. Verified, not assumed |
| P2-F6 | Low | internal-consistency | AC3 said "byte delta" while the cap is measured in characters | **applied.** Now "character count and character overage" |
| P2-F1 | High | correctness | AC1 should be **release-blocking** on a live selection spike; "if that cannot be done, do not ship" | **declined — re-litigation of a disposed finding, plus a blocked mechanism.** This is pass-1 F1 re-raised with no new evidence after its diagnosis was accepted and its behavioral-gate recommendation declined with a recorded reason. The mechanism is additionally **unavailable in this environment**: testing selection requires the *installed plugin cache*, not the repo (this repo ≠ the running plugin), and reinstalling the plugin while sessions using its hooks are live is a documented prohibition — the run cannot satisfy it without violating a safety rule. Residual named, follow-up filed. **Surfaced to the owner as a ship/no-ship flag rather than silently absorbed** |
| P2-F2 | High | feasibility | require a loader-enforced eager include or an execution-time assertion; failing that, keep ALL load-bearing instructions in SKILL.md and extract only optional examples | **partially applied; absolutist form declined.** The part that matters was already applied in pass 1 — both safety gates stay in SKILL.md. The stronger form proves too much: it would forbid AC2's split as the issue specifies it, and would equally condemn `implement-feature`'s own 23-reference structure that this repo ships and that this very issue was executed by reading. No such loader-enforced mechanism exists on this platform to adopt instead |

### Pass 3 — findings and dispositions

5 findings (2 High, 3 Medium). **This result arrived STALE** — the pass-2 disposition table above was
appended after the review launched, so the result's `input_sha256` no longer matched the file. Per
the freshness rule a result whose subject moved is rejected for disposition, so its findings were
*read and applied* but the **verdict was re-obtained** against a frozen document rather than
consumed stale. Recorded because a stale verdict quietly consumed at a terminal gate is exactly the
failure the rule exists to stop.

| # | sev | category | finding, compressed | disposition |
|---|---|---|---|---|
| P3-F5 | Medium | correctness | none of the three checks requires `description` to be a **string** — `description: [foo]` is a YAML sequence that is non-empty, under the cap and ` #`-free, so "CI can accept frontmatter the loader rejects" | **applied — but the claimed hole does not exist in the implementation.** Verified by running the guard against sequence / mapping / numeric / boolean / null frontmatter: all five already fail, via `not isinstance(desc, str)`. The defect was the *design text* omitting the type requirement, and the *message* naming neither the type. Both fixed, five fixtures added, and the null case now reports MISSING rather than a type error. The finding's premise about CI is **declined as false**, on evidence |
| P3-F3 | Medium | ambiguity | the flat-layout guard's traversal was unspecified; written with a flat `references/*.md` glob it would ignore a nested reference — the very failure it guards | **applied in full.** Specified as a recursive walk failing on any descendant with more than one path component, with a nested-reference fixture. Correct and well-precedented: `test_wf2_prose_budget.py` uses a recursive glob for exactly this reason |
| P3-F4 | Medium | completeness | a phrase tuple is pinned only for `pane-handoff`, though `adversarial-review` is also touched | **applied by narrowing, with per-skill reasons.** `epic-run`/`pane-handoff` quoting only *adds* currently-dead text; `adversarial-review`/`peer-consult` lose only install-note text containing no trigger phrasing; and `adversarial-review`'s 3 eval cases are explicit `/rawgentic:adversarial-review <path>` invocations that never exercise description-based selection. Its invoke key is now pinned. Narrowing was the reviewer's own alternative, chosen because inventing a tuple for an unexercised surface would be ceremony |
| P3-F1 | High | completeness | (third raising) make AC1 release-blocking on a live selection smoke test | **declined — third re-litigation, unchanged grounds.** No new evidence across three passes; the mechanism remains blocked (selection runs from the *installed plugin cache*, and reinstalling mid-session is prohibited while hook-using sessions are live). Residual named, follow-up filed, surfaced to the owner |
| P3-F2 | High | feasibility | (third raising) keep every normative action in SKILL.md; extract only optional examples | **declined in its absolutist form, already applied in substance.** Both safety gates stay in SKILL.md (pass 1). The stronger form would forbid the split the issue's own AC2 mandates and would equally condemn `implement-feature`'s shipped 23-reference structure |

### Pass 3b — the verdict the close rests on

Pass 3 was re-run against a **frozen** document (sha256 `13b1dd48…`), no edits in flight.
`status: success`, freshness **verified**, 5 findings (4 High, 1 Medium), and — decisively —
**zero carrying `ambiguity_flag`**, so the ambiguity circuit breaker returns **clear** and the
`design`-source exhaustion close is legitimately available rather than forced.

| # | sev | category | finding, compressed | disposition |
|---|---|---|---|---|
| P3b-F3 | High | internal-consistency | **the pane-handoff cut arithmetic does not work.** The two named fragments total ~240 chars against a 1,203-char description, landing at ~963 — under the cap but over this design's own ≤900 target | **applied.** Verified exactly: 1,203 − 111 − 129 = **963**, 63 over target. The reviewer did arithmetic this design had simply never done. Three further compressions are now named (−38, −33, −26 → ≈866), none touching a pinned phrase |
| P3b-F4 | High | internal-consistency | **contradictory instruction:** the "Two location pins" paragraph still said both guards migrate to `skill_corpus()`, while the following paragraphs prohibit exactly that and require a `{path: text}` mapping | **applied.** The stale sentence is corrected. Same defect pattern as P2-F3 — a fix applied in one place and missed in another, twice in one document. That recurrence is itself the strongest argument for this issue's mechanical guards |
| P3b-F5 | Medium | completeness | the link guard was one-directional: an **orphan** reference nothing links to, or a link lacking its read condition, would pass all three structural guards | **applied.** Now a bijection plus a read-condition assertion, in both directions |
| P3b-F1 | High | feasibility | (fourth raising) lazy reference reads are prose-enforced with no execution-time assertion | **refuted.** The absolutist remedy would forbid the split AC2 mandates and would equally condemn `implement-feature`'s shipped 23-reference structure. The safety-critical subset — `<mandatory-verification>` and destructive-action approval — is already kept in the always-loaded body, so the risk the finding names is addressed where it actually bites |
| P3b-F2 | High | feasibility | (fourth raising) do not ship without a candidate-build selection test | **deferred to #928.** The mechanism needs an installed plugin build and live sessions; reinstalling the plugin while hook-using sessions run is prohibited, so it is unavailable to this run rather than merely costly. Residual named in §5; follow-up filed |

**On the two recurring Highs.** They were raised in all passes with identical grounds and no
new evidence. Both are genuinely out of this issue's scope — one needs a harness that cannot be built
without violating a documented prohibition, the other contradicts the issue's own AC. Their
persistence is not treated as escalating severity, but it *is* reported to the owner rather than
buried, because "the reviewer asked three times" is information even when the answer is unchanged.
Follow-up filed for the behavioral selection gate.

**Ambiguity circuit breaker:** three pass-1 findings carried `ambiguity_flag: true`, which normally stops
the workflow for the owner. It did not stop here, and that call is recorded as **D213** with its
undo: each flagged ambiguity was under-specified text in *this document* with a determinate
resolution obtainable from the repo itself (F3 already implemented, F4 resolved by withdrawing the
claim, F5 resolved by reading `corpus.py`), none was a judgment call only the owner could make, no
two findings conflicted, and all five were applied together in one pass rather than piecemeal. A
genuine product, risk or scope fork would have stopped the run.

---

## 5. Verification strategy

- **TDD, red first:** AC3's guard is written and run **before** the AC1 edits, so it must FAIL
  naming `pane-handoff` (over cap) and `epic-run` + `pane-handoff` (truncated). A guard that
  passes on the unfixed tree would be worthless.
- **The evals gate, honestly.** AC1 says "touched skills' evals re-run as the triggering gate", but
  **this repo has no eval runner**: no CI job and no test invokes `evals.json` (grep across
  `tests/`, `.github/workflows/`, `scripts/` — the only match is an unrelated string in
  `test_retirement_tripwire.py`). The `evals.json` files are data for a manual/LLM harness. So
  "re-run the evals" is not an executable claim here, and asserting it would be a false
  verification claim. What ships instead is **mechanical phrase coverage**: every eval prompt's
  distinctive trigger phrase must still appear verbatim in the touched skill's description, as a
  test. Evals present for adversarial-review, create-issue, incident, new-project, pane-handoff,
  setup, switch, fix-bug, implement-feature; `epic-run` and `peer-consult` have **no** evals dir.

  **Phrase coverage is a PRESERVATION check, not the triggering gate — necessary, not sufficient.**
  The first draft of this design called it "stronger than an unrunnable eval pass", and that
  overclaimed: containment of a phrase does not exercise skill *selection*. A description can retain
  every pinned phrase and still trigger worse, because selection depends on the surrounding
  semantics and on loader/model behavior that no string assertion touches — and that failure is
  silent, which is exactly the #700 failure mode. So the honest statement of what ships: phrase
  coverage proves **no mined phrasing was dropped**, and nothing more.
  **Named residual:** this repo has no behavioral triggering gate, and #909 does not build one —
  that would mean installing a plugin build and observing real sessions select the skill, a new
  harness well outside these ACs. The residual is therefore accepted and recorded rather than
  hidden, and a follow-up issue is filed for a behavioral eval harness (which would also let
  `epic-run`'s 403 restored characters be regression-tested for selection, not just presence).
  Because `pane-handoff`'s evals exist precisely because seven sessions failed to fire it in 36
  hours (#700), the diet keeps every mined phrasing **verbatim** rather than relying on the check.

  **"Distinctive trigger phrase" is not left to the implementer's judgement** — it is an explicit,
  enumerated tuple in the test (`PANE_HANDOFF_REQUIRED_PHRASES`), not a substring derived at
  runtime, so the check is reproducible and cannot be satisfied by picking a trivially short
  fragment. The 17 pinned phrases are the complete quoted-variant set extracted from the
  pre-change description: the 15 quoted trigger phrasings plus the two dictated mappings
  (`herder`, `pain`). `say the word` is deliberately **not** pinned — it belongs to the ALSO-RUN
  directive's phrasing rather than to a trigger, so pinning it would convert a legitimate future
  reword into a CI failure.

  **Why only `pane-handoff` gets a phrase tuple — narrowed deliberately, with the reason per
  skill.** It is the only skill whose description *loses* trigger-bearing text. `epic-run` and
  `pane-handoff`'s quoting change only **adds** text that is currently dead, which cannot reduce
  triggering. `adversarial-review` and `peer-consult` lose only their install-note tail
  (`pip install "zhipuai>=2.1.5"`, Codex-CLI-authenticated, `ZHIPUAI_API_KEY`), which contains no
  trigger phrasing — and `adversarial-review`'s three eval cases are all explicit
  `/rawgentic:adversarial-review <path>` invocations testing path containment and the egress
  notice, so they do not exercise description-based selection at all. The one thing pinned for it
  is that `/rawgentic:adversarial-review` itself survives in the description, since that is the key
  its evals actually use.
- Whole suite at Step 9 vs the recorded baseline **5208 passed / 0 failed / exit 0** at
  `1a03b9cb` (measured first-hand, not inherited).
- Both lint lanes verbatim from `.github/workflows/lint.yml`.
- Version 3.128.0 → **3.128.1** (patch: `chore`) across all three surfaces; README changelog entry
  carrying both mandatory tail tokens on one line.
- Diagram: **no WF1/WF2/WF3/WF5 spine change** — WF11's step count, gates and loop-backs are
  untouched and only prose location moves → no diagram REV (recorded either way, per the repo
  rule).

## 6. PR shape

**One PR**, not two. The issue allows two ("descriptions / incident split"), but the repo rule is
*one PR = one issue = one bump = one changelog entry*, and splitting would force either two bumps
for one issue or a bump-less first PR. `Closes #909`.

---

## 7. Peer consult provenance (WF13 sub-step, opt-in)

Independent cross-model proposal obtained **blind both ways** — my own design was on disk before
any result was read. Backend `gpt`, reviewer `gpt-5.6-sol`, author `claude-opus-5[1m]`,
159.7s, `status: success`, `diagnostic: true` (a proposal never authorizes a fix round).
Result: `.rawgentic-peer-result-909.json`, input sha256 `07fdcbcf…a30aae`, head `1a03b9cb`.

**Converged independently** (arrived at separately, which raises confidence): single-quoted scalars
for the two truncated descriptions with apostrophe doubling; install notes to the body; no broad
rewrite of the other descriptions; flat references; length alone being an insufficient guard; and —
notably — the peer independently warned against claiming an aggregate 10.8 KB reduction this
shipment does not deliver, which is the §2 trade already recorded.

**Adopted from the peer, changing this design:**

1. **Ordering guards hardened against a vacuous pass** — exactly-once + same-file + offset, and
   duplicates fail. My original plan (a plain corpus migration) would have let an unrelated earlier
   `git fetch origin` satisfy the assertion. This is the most valuable single contribution.
2. **The `description: trigger A # trigger B` regression fixture**, plus plain/quoted/folded fixture
   coverage — a case that parses cleanly and passes a length-only guard while half its triggers are
   dead. It is the proof that check 3 earns its place.
3. **≤ 900-char working target** for pane-handoff rather than "just under 1,024", and **~100 lines
   of headroom** on the incident split rather than the tightest strip.
4. **Read conditions per extracted reference**, because references load lazily.
5. **Flat-layout, link-closure, and no-`${CLAUDE_PLUGIN_ROOT}`-in-references guards** — verified
   safe repo-wide first (zero current occurrences).

**Declined, with reason:**

- *"Add an epic-run eval case exercising a formerly-truncated phrase."* `epic-run` has **no**
  `evals` dir, so this creates a new evals surface — and since #271 the evals fraction/membership
  are computed-checked count guards, making it a registration change outside #909's ACs. Phrase
  coverage covers the skills that do have evals; a new eval surface for `epic-run` is a clean
  follow-up, not this PR.
- *"Keep both canonical command pairs as normative blocks in SKILL.md."* AC2 says procedure detail
  moves to `references/`; keeping Step 10's hotfix commands in the spine would partly defeat that.
  The peer's underlying concern — that the pins must not weaken — is **fully adopted** via item 1
  instead, which is stronger than either original.
- *"Implement the integrity check from YAML node source marks rather than regexes."* The stated
  hazard (line regexes mishandling block scalars) is real, but the shipped detector tests the
  **scalar style** and exempts block scalars by construction, so node marks would add a PyYAML
  `compose()` dependency for no additional coverage. The peer's fixture set (item 2) is what
  actually validates this, and it is adopted.
