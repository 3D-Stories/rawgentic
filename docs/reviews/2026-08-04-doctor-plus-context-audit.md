# doctor-plus context-engineering audit — rawgentic context stack

**Date:** 2026-08-04 · **Auditor:** Claude (Fable 5), following the doctor-plus skill
([robonuggets/doctor-plus](https://github.com/robonuggets/doctor-plus), single-commit clone,
report-only) · **Scope:** the context a rawgentic-bound Claude Code session actually loads

> doctor-plus audits a workspace against the 6 "then & now" context-engineering shifts
> Anthropic published for the Claude 5 models. **Source confirmed 2026-08-04:** an official
> Anthropic blog post — ["The new rules of context engineering for Claude 5 generation
> models"](https://claude.com/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models)
> by Thariq Shihipar (member of technical staff), 2026-07-24 — not just the X thread doctor-plus
> cites. Part 1 runs the built-in `claude doctor`; Part 2 audits the principles; Part 3 reports
> before changing anything. This document is that report. **Nothing has been changed.**

## Verdict table

| # | Shift (then → now) | Verdict | Worst offender | Suggested fix |
|---|---|---|---|---|
| 1 | Rules → judgement | **FLAG (mild)** | ponytail session hook's Output/Boundaries text contradicts the user tier's voice-ownership section (incl. a stale "pair with Caveman" line — Caveman is disabled) | Trim the hook's injected text to build-discipline only (C3) |
| 2 | Examples → interfaces | **PASS** | — | — |
| 3 | Upfront → progressive disclosure | **FLAG (biggest)** | ~75.5 KB (~19k tokens) of guidance loads every session; project manual §5 (~90 lines) duplicates the quality-bar skill created 2026-07-27 *specifically* to take that content out of every-session load | Trim §5 to a pointer (C1); tier de-dup (C2); description diet (C5) — all fit M1; skill splits (C4) → M1 #856 |
| 4 | Repetition → one home | **FLAG** | "git reset --hard under auto-mode" verbatim in two tiers; "timeout ≠ failure" in three; "never blanket git add" in four places | Delete the narrower copies that add nothing (C2, fits M1) |
| 5 | CLAUDE.md memory → auto-memory | **PASS** (deliberate architecture) | — | minor: move decision-archaeology prose out of always-loaded tiers (noted, low priority) |
| 6 | Simple specs → rich references | **PASS** (best-in-class) | two skill-count strings still hand-pinned | finish the computed-guard conversion (C6) |

**Part 1 (`claude doctor`):** clean — native 2.1.221, no installation issues found, exit 0
(confirmed, ran 2026-08-04).

## Scope and method

Audited what a rawgentic-bound session actually loads (confirmed by measurement, `wc`):

| Layer | What | Size | Loads |
|---|---|---|---|
| user tier | `~/.claude/CLAUDE.md` | 12.5 KB / 198 lines | every session |
| universal tier | `~/.claude/operating-instructions.md` | 10.0 KB / 141 lines | every session |
| tool note | `~/.claude/RTK.md` | 1.0 KB / 29 lines | every session |
| workspace tier | `~/rawgentic/CLAUDE.md` | 23.4 KB / 317 lines | every session under the workspace |
| project tier | `projects/rawgentic/CLAUDE.md` | 27.5 KB / 393 lines | when rawgentic is bound |
| memory index | auto-memory `MEMORY.md` (frozen) | 1.2 KB / 15 lines | every session |
| **total guidance** | | **75.5 KB ≈ 19k tokens** | **before any work starts** |
| skill descriptions | 21 plugin-skill frontmatter descriptions | 10.8 KB | every session (availability listing), plus workspace/user skill descriptions on top |
| session-start hooks | ponytail full prompt, claude-reflect notice, WAL note, project list, cross-project links | ~8 KB observed | every session |

On-demand context (SKILL.md bodies, `references/`, `docs/*.md`) was sampled, not exhaustively read.
Excluded per the skill: VCS internals, caches, generated output.

Audit stance per the skill: judge principles, not keyword patterns; never flag safety/approval/
destructive-action rules; every finding cites the file and passage.

---

## Findings per shift

### Shift 1 — judgement over rules: FLAG (mild)

**The one real defect is a contradiction between two always-loaded voices** (the skill's own
smell: "two directives that pull against each other"):

- The ponytail plugin's SessionStart hook injects its full prompt every session, including an
  Output section ("at most three short lines… if the explanation is longer than the code, delete
  the explanation") and a Boundaries section saying "pair with Caveman for terse prose."
- The universal tier's voice-ownership section (owner decision 2026-07-27) says the opposite:
  **Caveman is disabled**, no plugin owns voice, and "Ponytail is retained for BUILD DISCIPLINE
  only — it does not speak."
- The universal tier also requires substantive turns to close with fuller honest-state reporting,
  and the user tier's register rule requires explanatory owner-facing prose — both pull against
  the hook's compression text.

Confirmed: the contradiction is visible in this session's own startup injection vs.
`operating-instructions.md` ("Voice ownership" section). The user tier already adjudicated this
conflict — the hook text just never got trimmed to match.

**Deliberately NOT flagged:** the five-year-old register rule, AskUserQuestion-always, and the
auto-mode classifier protocol. These are absolute rules about communication style — textbook
"then" column — but each is a dated owner decision fixing a measured defect (owner confusion,
missed questions, silently dropped steps), which is exactly the skill's carve-out: constrain
where a wrong call is genuinely costly. Recommend keeping them.

### Shift 2 — interfaces over examples: PASS

The repo's core pattern IS interface design: skills shell out to typed CLI subcommands
(`python3 hooks/capabilities_lib.py derive`, `adversarial_review_lib.py is-enabled`,
`work_summary.py summarize`) instead of teaching by worked example; constants live in one Python
source of truth with drift-guard mirrors (project manual §4.21). The embedded exact commands in
the switch skill (expansion-free `printf`, no `$(...)`) look like worked examples but are
correctness-under-concurrency constraints — costly-wrong-call territory, not flagged.

### Shift 3 — progressive disclosure: FLAG — the biggest finding

**~19k tokens of guidance load before any work starts.** Three concrete offenders, best first:

1. **Project manual §5 re-inlines what the workspace already split out.** The workspace
   quality-bar skill's own header says it was "split out of the workspace CLAUDE.md §5 on
   2026-07-27 (harness audit F2 / doctor check 4) so it loads when a deliverable is being
   finished rather than in every session" (confirmed, `.claude/skills/quality-bar/SKILL.md`).
   Yet `projects/rawgentic/CLAUDE.md` §5 still carries ~90 lines of the same per-deliverable
   checklists (merged-ready PR, hook, skill, drift-guard test, bug fix, investigation, diagram
   REV) in every rawgentic-bound session. The split was done at one tier and not the other.
2. **Closing-step detail rides in every session.** The exact changelog entry shape, the diagram
   REV recipe, and the run-record schema quirks (§2 and §4.15 of the project manual) are needed
   only at the end of a WF run — and each already has an on-demand home (`pr-preflight`,
   `rev-diagram`, `docs/run-records.md`).
3. **Six of the ten largest plugin skills are monoliths with zero `references/`:** incident
   (25.4 KB), adversarial-review (23.9 KB), create-issue (23.7 KB), peer-consult (17.9 KB),
   pane-handoff (17.2 KB), epic-run (16.5 KB) — measured 2026-08-04. implement-feature proves
   the house pattern works (43.9 KB body + 23 reference files). Caveat: this costs per
   *invocation*, not per session, so it ranks below items 1–2. *(Post-verification note: against
   Anthropic's explicit 500-line SKILL.md guidance, only incident — 537 lines, zero references —
   is over among these; the others are byte-heavy but under 500 lines. See the verification
   section.)*

### Shift 4 — one home over repetition: FLAG

The tier map ("Where a rule belongs", workspace manual) is an explicit, owner-adjudicated
implementation of this principle — and it names the failure mode precisely: "restating it in a
narrower tier is how the copies drift apart and the staler one wins." Live violations of its own
rule (all confirmed by grep, 2026-08-04):

| Rule | Homes | Note |
|---|---|---|
| `git reset --hard` under auto-mode | workspace mistake #7 (`CLAUDE.md:258`) + project mistake #20 (`CLAUDE.md:278`) | near-verbatim; the workspace copy even explains it is kept at workspace level *because* the project manual may not load — making the project copy the redundant one by its own argument |
| Timeout ≠ failure for mutating calls | universal (inside "Know the undo") + workspace §3:234 + project §3:195 | three homes, no additions in the narrowest |
| Never blanket `git add` | universal:54 + workspace:112 **and** :243 + project:88 | four statements incl. twice in one file |
| A finding is a hypothesis | universal:21 + workspace:207 + project mistake #9 | project copy adds the vacuous-result signature — keep that half only |
| Vercel design-doc mandate | user:171 + workspace:132 | same decision, same date, restated; workspace copy adds the command (legitimate), user copy carries workspace mechanics |
| Ponytail "pair with Caveman" | hook injection vs. universal voice-ownership | the drifted-copy case: one home says the other is disabled |

Also under this shift's "simple tool descriptions" half: **10.8 KB of frontmatter descriptions
across the 21 plugin skills** load into every session. Top offenders: pane-handoff (1,174 chars —
over Anthropic's documented 1,024-character `description` maximum), revalidate-children (991),
adversarial-review (842). *(Figures corrected 2026-08-04 by exact YAML parsing; this report's
first revision overstated them — see the verification section.)* The repo's own bar (§5:
"description = triggering symptoms not workflow summary") is not met by several —
adversarial-review's description carries backend `pip install` instructions, which is body
material.
(pane-handoff's ~20 dictated trigger variants look deliberate — owner decision referenced in the
description itself — keep those, cut the summary prose around them.)

### Shift 5 — auto-memory over guidance-file memory: PASS (deliberate)

Memory has one authoritative home (the mempalace server; owner decision 2026-07-08, #304), and
the auto-memory `MEMORY.md` is deliberately frozen as a thin pointer index (15 lines, confirmed).
That is the "now" column implemented with a different memory system — working as designed; do not
fight it. Minor note, low priority: the guidance tiers carry long decision-archaeology narratives
(forensics adjudication trails, superseded-decision histories told in multiple places). The
*rules* must stay in guidance; the archaeology could live in memory or reference docs.

### Shift 6 — rich references over simple specs: PASS (best-in-class)

Drift-guard tests pin exact doc sentences; skill counts are computed from the tree (#271);
the workflow diagram is versioned with REV entries and snapshot tests; shared prose is
single-sourced in `shared/blocks/` with a sync script and a CI drift check; skills carry evals
JSON. This is the strongest shift-6 posture the auditor has seen. Residual: two count strings
are still hand-pinned — "All 7 config-driven skills" and "6 workspace management" (named as
hand-pinned in the project manual's own mistake #2, confirmed).

---

## What the changes would look like in rawgentic

Six changes, priority order. Blast radius verified where stated.

### C1 — Trim project manual §5 to a pointer (HIGH value, LOW risk)

Replace `projects/rawgentic/CLAUDE.md` §5's ~90 inline checklist lines with the pointer the
workspace tier already uses ("criteria live in the quality-bar skill; pr-preflight is the
runner"). **Pre-step:** diff §5 against the quality-bar skill first — anything §5 has that the
skill lacks (e.g. repo-specific hook/diagram-REV checklists) moves INTO the skill in the same
change, so nothing is lost. Confirmed cheap: no test pins the manuals' prose (grep of `tests/`
finds only comments and config-path uses). Ships as one `docs:` PR (precedent: `docs(reviews):`
PRs carry no version bump — c2fdca33, c3baac56).

### C2 — Delete the narrower duplicate copies across tiers (HIGH value, LOW risk)

Per the tier map's own rule. Project manual: delete mistake #20 (workspace #7 owns it, by its own
argument), delete the §3 timeout bullet (universal + workspace own it), thin mistake #9 to only
the vacuous-result signature, thin §3 "one helper" to just the repo's helper list. Workspace
manual: collapse the two `git add` statements into one. User tier: cut the workspace mechanics
from the Vercel block (the pointer to the tool already exists there). Note the split mechanics:
the project-manual half is a PR; the user/workspace tiers are not git repos — those are direct
file edits, so record them in the PR description for provenance.

### C3 — Trim the ponytail hook's injected text (MEDIUM value, owner-config change)

Remove the Output, Intensity, and Boundaries sections (including the stale Caveman line) from the
injected prompt, keeping the ladder / rules / when-not-to-be-lazy build-discipline core — which is
exactly the scope the 2026-07-27 voice-ownership decision already assigned it. This lives in the
ponytail plugin at the user level, not in the rawgentic repo — an owner-approved config/plugin
edit, not a PR here. Kills the one live contradiction found.

### C4 — References/-split the six monolith skills (MEDIUM value, MEDIUM effort, later)

Move procedure detail out of the six zero-`references/` SKILL.md bodies, keep trigger + step
spine inline (implement-feature is the proven pattern). Three real constraints, all from the
project manual: (1) `${CLAUDE_PLUGIN_ROOT}` is NOT substituted in `references/*.md` — any command
using it must stay in the SKILL.md body (#807); (2) content pins read the corpus (SKILL.md +
sorted references), so content-pinned prose survives the move, but location pins break — check
per skill; (3) synced shared blocks must stay in SKILL.md or the sync MANIFEST updated. One
WF2-shaped PR per skill (version bump each). Payoff is per-invocation, not per-session — do the
tier trims first.

### C5 — Description diet (MEDIUM value, ONE PR)

Rewrite the over-limit frontmatter descriptions to triggering-symptoms-only per the repo's own §5
bar; body absorbs the workflow summaries and install notes. Keep pane-handoff's dictated
variants. Risk: descriptions drive skill triggering — run each touched skill's evals
(`skills/<name>-workspace/evals/evals.json`) as the gate. One `chore(skills):` PR with a version
bump (behavioral surface).

### C6 — Finish the computed count guards (LOW effort, tidy)

Extend the #271 computed-guard approach to the two hand-pinned strings ("All 7 config-driven
skills", "6 workspace management"). Small `chore(tests):` PR.

**Suggested sequence:** C1+C2 together (one docs PR + two direct tier edits; removes
~150–200 always-loaded lines), then C3 (owner config), then C5, C6, and C4 last.

## Verification against primary sources (added 2026-08-04)

Owner-requested second pass: every shift re-checked against Anthropic's own publications
(fetched via Exa + direct doc fetch, 2026-08-04).

**The premise is confirmed and upgraded.** The "6 shifts" source is an official Anthropic blog
post — [claude.com/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models](https://claude.com/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models),
Thariq Shihipar, 2026-07-24 — not merely the X thread doctor-plus credits. Verbatim: *"We removed
over 80% of Claude Code's system prompt for models like Claude Opus 5 and Claude Fable 5 with no
measurable loss on our coding evaluations."* The post also states the best practices were put
into `claude doctor` itself — which is why Part 1 and Part 2 of this audit are the same exercise
at two depths.

Per-shift verification (blog post + [Anthropic's skill-authoring best
practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)):

- **Shift 1 — confirmed verbatim.** *"Overall, we found that we were overconstraining Claude
  Code… we have since found we can delete many of them and let the model use surrounding context
  and judgement instead."* The audit's practice of not flagging deliberate costly-wrong-call
  rules matches the post's own framing (old rules existed "to avoid worst case scenarios").
- **Shift 2 — confirmed, with a nuance that supports the PASS.** The best-practices doc still
  endorses worked input/output examples *"for Skills where output quality depends on seeing
  examples"* — examples are discouraged as tool-usage teaching ("giving examples actually
  constrains them to a certain exploration space"), not banned outright.
- **Shift 3 — confirmed and quantified.** *"Keep SKILL.md body under 500 lines for optimal
  performance"*; split when approaching the limit; *"consider having a tree of files that can be
  loaded at the right time."* Measured against the 500-line figure: **incident (537 lines, zero
  reference files)** and implement-feature (549 lines — but already split, 23 reference files)
  are the only two over; the four other byte-heavy skills sit under 500 lines. This *downgrades
  C4 further*: the per-invocation win is real but smaller than the tier trims.
- **Shift 4 — confirmed.** *"We found we could delete these repeat examples and put instructions
  on how to use tools in the tool descriptions rather than the system prompt."* Also verified: the
  `description` field maximum is **1,024 characters** — pane-handoff's is **1,174**, over the
  documented cap (it works today because Claude Code does not enforce the API-side validation;
  still a defect). **Correction:** this report's first revision cited 1,323/1,076/1,026 for three
  skills — a frontmatter-parsing overcount. Exact YAML parsing: pane-handoff 1,174,
  revalidate-children 991, adversarial-review 842; total 10.8 KB across 21 skills (not 13.3 KB
  across 30). Only pane-handoff exceeds the cap.
- **Shift 5 — confirmed.** *"Claude now automatically saves memories that are relevant to the
  work and to you."* The PASS stands: mempalace is a deliberate substitute implementing the same
  principle (facts out of guidance files, into a memory system).
- **Shift 6 — confirmed.** *"A HTML mockup of a design will generally produce better results than
  a description of the design or a screenshot"*; test suites and rubrics as references — exactly
  the repo's drift-guard/diagram/eval posture.

## Milestone-map fit (added 2026-08-04)

Owner-requested check against the current milestone map:
`docs/planning/2026-08-03-756-rationalization-roadmap.md`. State confirmed from its §10
retrospective: **M0 (UNBREAK) is DONE** — four PRs (#867/#868/#870/#872) + #869 merged
2026-08-04, suite 7031→4659 — so **M1 "STAY SMALL" is the active milestone.**

**Convergence, first:** the roadmap independently cites the *same* Anthropic guidance this audit
verifies (roadmap §5: "SKILL.md <500 lines, progressive disclosure, scripts over prose ('the
context window is a public good')") and is already executing shift 1 on the workflow prose
(D175: ~34 → ~15 WF2 gates) and shift 3 on the biggest skill (#856). The audit's findings are the
complementary remainder: the roadmap shrinks the workflow skills; C1–C6 shrink the guidance tiers
and skill metadata *around* them.

| Finding | Milestone fit |
|---|---|
| **C1** trim project §5 | Not in the roadmap; **fits M1 cleanly** — and should land BEFORE #856 pins CI byte ceilings, so ceilings capture the trimmed baseline |
| **C2** tier de-dup | Not in the roadmap; **fits M1** — M1 already touches the workspace manual (the D179 issue throttle lands there), same window |
| **C3** ponytail hook trim | **Outside the roadmap** (user-level plugin config) — standalone owner action, any time |
| **C4** references/ split | **Largely accomplished by M1 #856** as written (steps.md → step-local files; CI byte ceilings, total + per-file, glob-exact). Residual: add **incident** (the one zero-reference skill over 500 lines) to #856's scope; the ceilings police the rest |
| **C5** description diet | Not in the roadmap; **fits #856 naturally** — descriptions are always-loaded corpus, so per-file ceilings should include a frontmatter budget; pane-handoff's 1,174 chars is over Anthropic's hard cap regardless |
| **C6** computed count guards | **Rides M1 #822** — both extend the existing version-pin test |

Net: nothing in C1–C6 conflicts with the roadmap; four of six fold into already-scheduled M1
items (#856, #822), and the remaining two (C1+C2) are small M1-shaped additions. One consistency
note: roadmap §10 records *"docs/reviews/ is gitignored by design — the M0 review verdicts live
in the PR bodies"*, which matches the reading this report's own commit relied on (per-run
by-products stay local; curated standalone audits have committed precedent). The owner
adjudicates that at the PR.

## What was NOT checked

Other projects' manuals (chorestory, 3dstories-studio, saystory); mempalace content quality;
the full bodies of all 21 plugin skills (structure + sizes measured, ~6 read); user-level and
workspace-level skill description weight (listed, not measured); and other session-start hooks'
exact byte cost (observed qualitatively).

## Provenance

doctor-plus: github.com/robonuggets/doctor-plus, cloned 2026-08-04 (single commit `856d60c`),
one SKILL.md, no code — vetted before running. Audit executed inline from the cloned skill's
instructions; nothing was installed. Report-only: no context file was modified.
