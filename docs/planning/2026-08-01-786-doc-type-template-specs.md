# Ten doc types, one component set

Template specifications for the hosted-doc publish pipeline. Extends the design of record
`docs/planning/2026-07-31-publish-pipeline-vdl-design.md` (merged in #780) with the part that
document left open: what each template actually contains. Owner-directed 2026-08-01, from
sixteen hosted pages the owner named as the standard to hit.

**This document uses no numbered lists, no inline links and no italics.** Not a style choice.
The renderer silently corrupts all three, so a document that used them would render wrong on
the very page arguing they should render right. Section 2 is the measurement.

## 0. The verdict, before anything else

**Fourteen of the owner's sixteen favourite pages were hand-authored. The renderer produced
two of them, and one of those two is the page this work exists to fix.** The template system
is not underpowered at the margin. It has never produced a page the owner liked.

Worse, and not previously recorded anywhere: **the renderer does not just look thin, it
silently drops document content.** Ordered lists, links, italics, images, nested lists and
horizontal rules are all emitted as raw markdown into the body text. This is measured, not
inferred, and it is visible on the epic's own hosted design doc today.

Three things follow, in priority order.

- **A new issue is needed for the markdown parser** — ahead of #784. Better template bodies
  wrapped around a parser that eats numbered lists is polish on a broken floor.
- **#784 gets the component specifications in section 4** rather than the single line
  "give the thin templates real bodies".
- **The template roster grows from seven to ten.** The owner named `analysis`, `uat` and
  `workflow` as needed types. This supersedes decision 4 of #786 ("no new `--style` names").

## 1. Why `hooks/render_artifact.py` is still in rawgentic

**Because it belongs there, and the extraction was correct.** It is a plugin hook, not a
skill. The claude-skills manual is explicit that plugin-shipped skills and their engines stay
in the plugin repo, and seven rawgentic plugin skills call this one:

| Caller in rawgentic | Kind |
|---|---|
| `skills/create-issue/SKILL.md` | plugin skill |
| `skills/epic-post-mortem/SKILL.md` | plugin skill |
| `skills/run-feedback/SKILL.md` | plugin skill |
| `skills/session-mining/SKILL.md` | plugin skill |
| `skills/implement-feature/references/steps.md` | WF2 step definitions |
| `skills/fix-bug/references/steps.md` | WF3 step definitions |
| `skills/setup/references/integrations.md` | setup reference |
| `hooks/adversarial_review_lib.py` | hook library |
| `tests/hooks/test_render_artifact.py`, `tests/hooks/test_artifact_lifecycle.py` | its tests |

Moving it would break all of them. It stays.

### The real defect: bare relative paths in machine-wide skills

The extracted `design-doc-publish` skill invokes it as `python3 hooks/render_artifact.py`.
That path resolves only when the shell's working directory is `projects/rawgentic`. The skill
is user-tier and its own description claims scope over "any project in this workspace", so
the command it prescribes is unrunnable in eight of the nine projects it claims to serve.

The same audit across every extracted skill found six more instances. Every one is a bare
`hooks/<file>.py` that assumes a cwd the skill never sets:

| Skill | Tier | Bare path | Verdict |
|---|---|---|---|
| `design-doc-publish` | user | `hooks/render_artifact.py` (lines 111, 196) | **Broken.** Claims workspace-wide scope. |
| `epic-run-analysis` | workspace | `hooks/render_artifact.py` (line 55), `hooks/step_state.py` (line 29) | **Broken.** Workspace-tier, runs from any project. |
| `ask-owner` | workspace | `hooks/hermes_bridge.py` (lines 22, 34, 91) | **Broken, and self-contradicting.** Line 10 gives the correct qualified path `projects/rawgentic/hooks/hermes_bridge.py`, then the three runnable commands drop the prefix. |
| `clear-prep` | user | `hooks/work_summary.py` (line 28), `hooks/launcher_lib.py` (lines 121, 129) | **Broken.** User-tier, fires at any seam in any project. |
| `quality-bar` | workspace | `hooks/security_scan.py` (line 21) | **Borderline.** Workspace-tier but the checklist item is rawgentic-shaped. |
| `pr-preflight` | user | `hooks/security_scan.py` (line 58) | **Acceptable.** The surrounding section is explicitly the rawgentic lane. |
| `add-skill` | user | `hooks/skill_registration_check.py` (line 88) | **Acceptable.** The skill is entirely about the rawgentic plugin repo. |
| `notify-owner` | workspace | `hooks/hermes_bridge.py` (line 13) | **Acceptable.** Prose reference, not a command. |

Four are genuinely broken, one is borderline, three are fine. The fix is uniform and cheap:
resolve against the workspace root, not the cwd.

```
python3 ~/rawgentic/projects/rawgentic/hooks/<file>.py ...
```

This is a claude-skills PR, independent of everything else here, and it should not wait on
the pipeline work. Note that `#783` removes the need for the `design-doc-publish` instance
specifically, by putting `publish_doc.py` in front of the renderer — but the other three
survive #783 untouched.

## 2. What the renderer does to a document — measured 2026-08-01

A probe document containing one of each common markdown construct was rendered with
`--style design`. Counting tags in the output:

| Construct | Written | Rendered as | Status |
|---|---|---|---|
| Heading | `## Section` | `<h2>` | works |
| Bullet list | `- item` | `<ul><li>` | works, flat only |
| Nested bullet | two-space indent | flattened to the parent level | **broken** |
| Table | GFM pipes | `<table>` | works |
| Blockquote | `> line` | `<blockquote>` | works, single line |
| Fenced code | triple backtick | `<pre><code>` | works |
| Bold | double asterisk | `<strong>` | works |
| Inline code | backticks | `<code>` | works |
| **Ordered list** | `1.` `2.` `3.` | **one run-on paragraph, zero `<ol>`** | **broken** |
| **Link** | bracket-paren | **literal `[text](url)` in body text, zero `<a>`** | **broken** |
| **Italic** | single asterisk or underscore | **literal asterisks and underscores** | **broken** |
| **Image** | bang-bracket-paren | **literal, zero `<img>`** | **broken** |
| **Horizontal rule** | three dashes | **literal `---` as text** | **broken** |
| **Document title** | `--title` plus the doc's own `# H1` | **two `<h1>` with identical text** | **broken** |

Root cause, both in `hooks/render_artifact.py`:

- `_inline` at line 55 applies exactly two transforms, backtick-code and double-asterisk bold.
  Nothing else. Links, emphasis and images were never implemented.
- The block loop at line 178 matches `[-*]\s+` only. There is no branch for `\d+\.`, so an
  ordered list falls through to the paragraph accumulator and is glued into prose.

### This is live, not hypothetical

The epic's own design doc, hosted at `rawgentic-design-publish-pipeline.vercel.app`, shows
all three of the worst cases right now. Its section 1 is written as a four-item numbered list
of evidence and renders as a single wall-of-text paragraph. Its thesis paragraph links to the
docs-index and renders the raw markdown, brackets and parentheses included. Its title appears
twice, once from the header block and once from the markdown's own `# `.

Any design doc in this workspace that used a numbered list has been shipping corrupted since
the renderer was written. Nobody noticed because nobody compares the rendered page to the
source.

**This page shows the duplicate-title bug too**, and deliberately so. The heading appears
twice above, once from `--title` and once from the markdown's own `# `. Avoiding it would
mean either editing the renderer inside a design PR or shipping a markdown file with no
heading, and neither is worth doing to make one page look tidier than the tool it documents.
The other five defects are absent only because this document was written around them.

Which is the honest summary of the whole situation: **the page you are reading is what the
mandated helper produces when an author actively avoids everything it breaks.** Its plainness
against the sixteen pages in section 3 is the argument, not a shortfall in the writing.

### Cost of the fix

Small. An `<ol>` branch mirrors the existing `<ul>` branch. Links and emphasis are two more
regexes in `_inline`, applied after escaping, so the escape-first safety property holds. The
`plain` byte-identity test is the real constraint and it is satisfiable: gate the new inline
transforms behind the same `inline_fn` seam the templates already use, and leave `plain`
pointing at today's `_inline`.

## 3. What the sixteen favourites have in common

Read as a set, the owner's picks are one design language already. Twelve components recur
across pages that were written months apart by different sessions, which is strong evidence
they are the real vocabulary rather than one author's habit.

- **Eyebrow.** Monospace, uppercase, letter-spaced, above the title. Carries project, doc
  type, ref and date. Present on fifteen of sixteen.
- **Verdict headline.** The finding stated as a sentence, not a topic. Two lines, with one
  phrase in the accent colour. "The failures are real. The story was wrong." — "It flew. It
  did not work." — "5,702 tests. Delete none." — "One install session. Five blockers closed."
  This single component is most of what "draws my eye to where it needs to be drawn" means.
- **Lede.** Two to four sentences at larger-than-body size, stating what the reader gets.
- **Meta chip row.** Monospace facts: branch at commit, model, cost, run id, counts.
- **Stat strip.** Three to six cells, big numeral over a small monospace label. Present on
  nine of sixteen. The number that matters is coloured; the rest are not.
- **Status chip.** Colour-coded pill for a state. Every page has these; the palettes agree
  more than they disagree, which is why a shared token set is viable.
- **Severity callout.** Left accent rule, tinted background, bold claim then explanation.
- **Legend.** States what the colours and glyphs mean before the reader meets them. On every
  page that colour-codes anything non-obvious.
- **Segmented meter.** A bar split by state, used for epic and milestone progress.
- **Labelled gutter row.** A monospace label in a left gutter beside a claim. Used for
  finding lists and change lists.
- **Provenance tail.** A dim trailing clause naming the evidence for the claim just made:
  "confirmed: gh api — PR #15 merge date".
- **Provenance section.** A closing block titled some variant of "How this was built and what
  to distrust", separating what was verified from what was assumed, and naming the claim most
  likely to be wrong.

**Those last two deserve to be mandatory in every template.** They appear across the
favourites more consistently than any visual flourish, and they are the operating
instructions' confirmed-versus-inferred rule made visible. A template that has no slot for
provenance quietly encourages leaving it out.

## 4. The ten templates

### 4a. How a markdown-only author reaches these components

The pipeline's contract is that the model writes markdown and the renderer owns presentation.
That leaves an unanswered question the design of record only gestures at: how does an author
express a stat strip in markdown?

**Proposal: typed fenced blocks.** A fence whose info string names a block type the renderer
recognises. Opening a fence with `stats` instead of `python`, the body reads:

```
82 | sessions read
155 | findings mined
28/44 | highs confirmed | accent
```

This is the lazy answer and it is the right one. Fences already parse. Any other markdown
viewer degrades it to a code block rather than mangling it. The block set is closed and
per-type, so an unknown tag can warn instead of failing. And the author never writes a colour,
which is the property that keeps the VDL enforceable.

Block tags proposed: `stats`, `verdict`, `chips`, `callout`, `legend`, `meter`, `findings`,
`steps`, `nodes`, `provenance`. Which types accept which tags is the per-template contract in
the table below.

### 4b. The spine every template shares

Eyebrow, verdict headline, lede, meta chips, Edmonton timestamp, closing provenance section,
light and dark themes, AA contrast in both. A template customises what sits between the lede
and the provenance section; it does not get to skip the spine.

### 4c. The roster

`plain` stays frozen and byte-identical. The other nine are specified below. Owner-nominated
exemplars are marked; unmarked exemplars are this analysis's proposal and are the thing most
worth correcting.

| Type | Status | Primary exemplar | First-read element |
|---|---|---|---|
| `plain` | frozen | none — no change | existing h1 |
| `analysis` | NEW | `rawgentic-analysis-owner-notes` (owner: plain candidate) | headline answer block above the question index |
| `roadmap` | rework | `saystory-epic-state-map` (owner: epic progress) | stat strip plus a READ THIS FIRST callout stack |
| `report` | rework | `3dstories-bench-report-phases`, `rawgentic-test-suite-review` | verdict headline plus KPI strip |
| `design` | rework | `sysop-design-network-topology` (owner: diagrams) | the single change that makes it work, as a callout |
| `dashboard` | rework | `3dstories-bench-design-judge-v3` (owner: clear dashboard), `herdr-dashboard-issue-audit` (owner: layout) | sticky state bar plus TL;DR panel |
| `review` | rework | `workspace-audit-forensics-0730` (owner: leads with what matters) | verdict headline plus confirmed/refuted counts |
| `spec` | rework | `herdr-dashboard-plan` | requirement count and gate state |
| `uat` | NEW | `saystory-uat-checklist` (owner: hands-down favourite) | progress meter, zero of N |
| `workflow` | NEW | `sysop-design-network-topology` (owner: workflow diagrams) | legend, then the before/after diagram pair |

### 4d. Per-template component sets

**`analysis`** — a question answered at length. Blocks: `verdict`, `chips`, `callout`,
`provenance`. Structure: a headline-answer block, then a jump index of the questions, then one
numbered section per question where the first line is the answer and everything after it is
the evidence. Each answer carries a confidence chip: measured, confirmed, or inferred. Marker:
`.tpl-analysis`, with `.an-q`, `.an-answer`, `.an-conf`.

**`roadmap`** — what is planned and what is blocking. Blocks: `stats`, `callout`, `legend`,
`meter`, `chips`, `provenance`. Structure: stat strip, a READ THIS FIRST stack of two to four
severity callouts, a legend for the state colours, then epic cards each carrying a segmented
meter and a wrapped grid of child chips, then a "where to start" table ordered by leverage
rather than by epic. Markers: `.tpl-roadmap`, `.rm-epic`, `.rm-meter`, `.rm-child`.

**`report`** — what was measured. Blocks: `stats`, `verdict`, `callout`, `provenance`.
Structure: verdict headline, KPI strip, then one section per measured thing where the heading
is the finding and not the topic, data tables with inline bars, and caveat callouts inline
where the caveat applies. The closing provenance section is titled "How this was measured".
Markers: `.tpl-report`, `.rp-kpi`, `.rp-bar`, `.rp-caveat`. Keeps the existing
`_decorate_scores`.

**`design`** — a proposal to change something. Blocks: `verdict`, `callout`, `nodes`, `chips`,
`provenance`. Structure: a lead block naming the single change that makes the proposal work,
then today-versus-proposed side by side, then decision callouts, then an affected-components
table with have/need/buy status badges, then risks and the corrections this design makes to a
prior plan. Markers: `.tpl-design`, `.dz-lead`, `.dz-compare`, `.dz-decision`. Replaces the
current one-rule heading underline.

**`dashboard`** — current state at a glance. Blocks: `stats`, `chips`, `callout`, `findings`,
`provenance`. Structure: a sticky monospace state bar carrying branch at commit, tree state,
open PR count and gate counts; a TL;DR panel with a left accent rule and three to five bolded
lead findings; a verdict chip row; the KPI strip; then an ATTENTION panel of labelled gutter
rows, each with a provenance tail. Markers: `.tpl-dashboard`, `.db-statebar`, `.db-tldr`,
`.db-attention`, `.db-prov`.

**`review`** — findings, ranked. Blocks: `stats`, `findings`, `callout`, `chips`,
`provenance`. Structure: verdict headline, a KPI strip whose cells are confirmed and refuted
counts, a hypothesis card grid with outlined status badges, then one section per finding with
a severity gutter, and a closing block titled "The claim most likely to be wrong". Markers:
`.tpl-review`, `.rv-hypo`, `.rv-sev`, `.rv-weakest`. Keeps `_decorate_severity`.

**`spec`** — what must be true. Blocks: `chips`, `callout`, `steps`, `provenance`. Structure:
requirement rows with a stable ID and a MUST/SHOULD chip, an acceptance-criteria checklist,
gate badges for each exit condition, and an explicit out-of-scope block. Markers: `.tpl-spec`,
`.sp-req`, `.sp-ac`, `.sp-gate`. Keeps `_decorate_requirements`.

**`uat`** — a checklist a human executes. Blocks: `steps`, `callout`, `chips`, `meter`.
Structure: a progress meter reading zero of N, then step cards each with a part badge and a
time estimate, containing checkbox items, STOP callouts where proceeding would invalidate the
run, and free-text feedback boxes. Closes with an export control that turns the filled-in
state into a paste-back report. Markers: `.tpl-uat`, `.ut-step`, `.ut-item`, `.ut-stop`,
`.ut-export`.

**`uat` is the only interactive template, and it carries the only genuinely new constraints.**
Checkbox and textarea state must survive a page reload, which means localStorage. The export
control must build its report with `document.createElement` and `append`, never `innerHTML` —
a Write hook in this workspace blocks `innerHTML` assignments, and the page must stay
CSP-safe. This is enough extra surface that it deserves its own issue rather than riding
inside #784.

**`workflow`** — how something flows, or how it is wired. Blocks: `nodes`, `legend`,
`callout`, `chips`, `provenance`. Structure: a legend defining what box borders and line
styles mean, then diagram frames of node boxes with labelled edges, laid out in CSS grid with
no SVG library and no external dependency, then a "what this means" inset inside each frame,
then the before/after pair. Markers: `.tpl-workflow`, `.wf-node`, `.wf-edge`, `.wf-legend`.

The `nodes` block is the one piece of real invention here. A workable markdown shape, taken
from how the topology page is structured — a fence tagged `nodes`, whose body reads:

```
[internet] --WAN--> [router: TP-Link GE800 | 6GHz 5760 | ROLE: router+firewall]
[router] --10G Cat6a--> [charlie: 10.0.17.200 | 2x E5-2680 | 126G RAM]
[router] -.25G DAC.-> [beagle: 10.0.17.211 | no RJ45 port]
```

Solid arrows for existing, dashed for proposed. Pipe-separated spec lines inside a node. This
is the smallest grammar that reproduces the page the owner singled out.

## 5. One decision the owner needs to make

**The page nominated as the `plain` exemplar is not plain.** `rawgentic-analysis-owner-notes`
was rendered with `--style dashboard`. So the look the owner identified as the right baseline
is today's dashboard template, while `plain` is frozen byte-identical by test and by two
separate assertions in the source.

Two ways forward, and this analysis recommends the first.

- **Leave `plain` frozen and make `analysis` the new default** for anything without a
  declared type. The owner's actual preference is then honoured under the name that matches
  what the page is, the byte-identity test stays green, and nothing that currently renders
  `plain` changes.
- **Unfreeze `plain`.** Cheaper conceptually, but it breaks a pinned test, contradicts a
  recorded decision, and changes every existing plain page.

## 6. What changes in the epic

- **New issue, ahead of #784: fix the markdown parser.** Ordered lists, links, emphasis,
  images, horizontal rules, nested lists, duplicate H1. Section 2 is the specification and the
  probe is reproducible. Small, no dependencies, and it blocks the value of everything else.
- **New issue: the `uat` template.** Split out of #784 because it is the only interactive
  template and carries localStorage, export and CSP constraints the other nine do not.
- **#784 gains section 4 as its specification** and grows from seven templates to ten. Its
  acceptance criterion that `plain` stays byte-identical is unaffected.
- **#786 decision 4 is superseded.** "No new `--style` names" is replaced by the ten-type
  roster; the seven existing names all survive, so this is purely additive.
- **#783 and #785 are unchanged.** The pipeline command and the VDL packs are orthogonal to
  what the templates contain.

## 7. Confirmed, inferred, and what to distrust

**Confirmed by running it.** Every row of the section 2 table came from rendering a probe
document through `hooks/render_artifact.py` at commit 26f865e and counting tags in the output.
The two root causes were then read at `render_artifact.py:55` and `render_artifact.py:178`.
The fourteen-of-sixteen hand-authored count came from fetching all sixteen pages and grepping
for the renderer's own signature; the two renderer-made pages carry `tpl-dashboard` and
`tpl-design` markers, the other fourteen carry none.

**Confirmed by reading.** The seven rawgentic callers in section 1 and the eight skill
instances in the audit table are grep results with file and line numbers, taken from tracked
files only.

**Inferred, and worth checking.** The component list in section 3 is this analysis's reading
of sixteen pages; the owner named reasons for eight of them and the rest are inference from
structure. The exemplar assignments in 4c for `analysis`, `report`, `spec` and `review` are
proposals, not owner statements.

**The claim most likely to be wrong** is that typed fenced blocks are the right authoring
grammar. It is untested. It is clean on paper and degrades well, but no one has yet written a
real document in it, and the `nodes` grammar in particular could turn out to be more awkward
to write than the hand-authored HTML it replaces. The cheapest way to find out is to author
one real page of each type in the grammar before implementing the renderer side.
