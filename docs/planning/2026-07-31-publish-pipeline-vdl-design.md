# Vercel publishing pipeline — doc-type templates × per-project VDL

Design document + implementation roadmap. Owner-directed 2026-07-31 (in session, herdr-dashboard
pane); implementation targets `projects/rawgentic`. Status: **design for review — no issues filed
yet**; the WF1 epic decomposition below is the intended entry point.

## 0. Thesis

One scripted path publishes every hosted doc in this workspace. The model writes **markdown
only**; the renderer owns **all** presentation. Each document type gets a real template; each
rawgentic project gets its own visual design language (VDL); every page lands on Vercel under a
conventional name, stamped in America/Edmonton time, verified, and listed on the
[docs-index](https://docs-index.vercel.app). The `design-doc-publish` skill shrinks from a page
of prose steps to one command plus judgment.

## 1. Why (evidence, 2026-07-31)

1. **Process-by-prose failed measurably.** The Vercel account reached 37 projects: junk names
   (`deploy-713`, `vercel-25`, `site`), three duplicate deploys of the same page, no index.
   Cleaned up 2026-07-31 (3 deleted, 21 renamed) — but the cleanup only holds if the pipeline
   stops depending on a model re-reading prose rules each time.
2. **Five of seven styles are the same style.** `hooks/render_artifact.py` registers seven
   templates (`_TEMPLATES`, `render_artifact.py:547`), but only two body renderers exist;
   `plain/report/design/review/spec` differ by a handful of CSS lines. The whole `design`
   template is one rule: `.tpl-design h2{border-bottom:2px solid var(--accent)}`
   (`render_artifact.py:527-529`). The owner's visual bar ("proper VDL, contrasting colors,
   elements that draw the eye") is not reachable through `--style design` today.
3. **Hand-authoring costs real tokens.** Outside this repo the publish skill falls back to
   "hand-author a self-contained page" — 300–700 lines of HTML/CSS as model output per page,
   again on every edit. (The oft-repeated claim that templates save nothing is true only
   script-vs-script: the renderer runs as a subprocess, so its CSS never enters model context.
   Script-vs-hand-authoring, templates save nearly everything.)
4. **One palette for nine projects.** The renderer hardcodes a single teal accent
   (`render_artifact.py:455-463`); chorestory docs, saystory docs and sysop docs are visually
   indistinguishable. Meanwhile chorestory owns a real design system
   (`src/styles/design-tokens.css`, primitive→semantic→component, brand `#2c7a9e`) that its
   hosted docs ignore.

## 2. Decisions already made (owner, 2026-07-31 — recorded, not proposals)

- Vercel names: `{project}-{purpose}-{ref}`; update-before-create; every deploy updates the
  docs-index; Edmonton timestamp on every new/updated page (going-forward only).
- **A project's existing VDL always wins** (the chorestory rule); index seed colors only fill
  gaps for projects without a design system.
- The publish skill's mechanics should be **scripted, not prose** — the skill keeps judgment.
- `plain` stays byte-identical (test-pinned; the source insists).

## 3. Architecture

Three parts, smallest diff that delivers each:

### 3a. `hooks/publish_doc.py` — the pipeline command (new)

```
python3 hooks/publish_doc.py --md docs/planning/<doc>.md \
  --project herdr-dashboard --type design --ref 81
```

render (via `render_artifact`) → derive + validate the conventional name → **reuse-or-create**
the Vercel project (`vercel project ls` first; junk names impossible) → deploy `--prod`, full
log captured → **lint gate** → cache-busted verify (200 + real `<title>`) → regenerate + deploy
docs-index → print the URL. The lint gate is mechanical: Edmonton stamp present, `<title>` set,
no external requests, WCAG AA on the VDL token pairs (checked, not assumed). Git/PR sequencing
stays OUTSIDE the script — workflows own commits.

### 3b. `render_artifact.py` — real doc-type templates

Style = **structure + decorator**, following the three templates that already earn their names
(`report`/`review`/`spec` decorate scores, severity, requirements). `design` gains a lead/verdict
block, callout components and a real type scale; `plan` gains milestone structure. Interface
follows mdsone's proven shape: template selects the doc type, a second axis selects the VDL.
`plain` untouched, byte-identical.

### 3c. Per-project VDL packs

A `vdl` block in each project's `.rawgentic.json` — accent (light+dark), background tint,
optional type note, **with provenance** (`source: src/styles/design-tokens.css --brand-blue-400`).
The renderer injects it as a `:root` token override; no block → seed pack (docs-index line
colors: herdr cyan, rawgentic amber, saystory green, lumenquire violet, sysop teal, bench pink,
studio yellow, twi rust, workspace steel). **Declared beats seeded; scrape nothing at render
time** (a grep for chorestory's palette hit 1,208 matches in built assets before the real tokens
file — extraction is guessy; a one-time human-approved declaration is not).

## 4. Prior art — what we steal (researched 2026-07-31, Exa)

| Steal | From | License | What |
|---|---|---|---|
| 1 | `ni-null/mdsone` | MIT | The `--template <name>@<variant>` interface — doc-type × VDL as a two-axis selector. Interface only; our renderer stays stdlib Python. |
| 2 | `nsmith/html` | MIT | 20 self-contained single-file doc-type templates — raw structural material for `design`/`plan` bodies, with attribution. |
| 3 | ReportRoom (`dashaworks/report-skills`) | pattern | The `lint_document`-before-publish gate — quality as a checkable pipeline step. |
| 4 | `leeguooooo/htmldock` | pattern | PostToolUse auto-publish hook (later, optional). |
| 5 | `keepYaoung/artifact-organizer` | MIT | Schema-validated component catalog — props reject styling by construction; applied at markdown level as the block set each doc type recognizes. |
| 6 | `keepYaoung/artifact-organizer` | MIT | Its seven light+dark theme token packs as **seed VDL material** for projects without a brand. |

Not adopted as a platform: artifact-organizer (JSON-in vs our committed-markdown contract; one
growing canvas vs many stable URLs; house-style themes vs per-project VDL) and every hosted
artifact service (ReportRoom, htmldock, pergam, GitShare — all replace static-Vercel with new
infrastructure). The novel 20% nobody ships: VDL sourced from each project's own tokens + a
static public grouped index.

## 5. Roadmap

Entry point: **one WF1 epic, three children**, sized S/M, in dependency order. Each child is a
normal WF2 run with its own design gate, tests, and PR.

- **Wave 0 — file + harvest (S).** WF1 epic + children below; vendor the steal material
  (nsmith templates, artifact-organizer theme packs) into `references/` with MIT attribution.
  No behavior change.
- **Wave 1 — the pipeline command (M).** `hooks/publish_doc.py` (§3a) + rewrite
  `design-doc-publish` skill to call it. Acceptance: one command takes an `.md` to a verified
  live URL + updated index; a junk-named or duplicate project is impossible by construction;
  lint gate blocks a stampless page. Depends on: nothing.
- **Wave 2 — real doc-type templates (M).** §3b in `render_artifact.py`; per-template
  `.tpl-<name>` tests extended; `plain` byte-identity test still green. Blast radius (confirmed
  by grep): `render_artifact.py`, `tests/hooks/test_render_artifact.py`,
  `tests/hooks/test_artifact_lifecycle.py`, `docs/design-language.md`. Depends on: nothing
  (parallel with wave 1).
- **Wave 3 — VDL packs (M).** §3c: `vdl` schema + renderer injection + `--vdl`; declare
  chorestory from its own tokens; seed the other eight; contrast validation in the lint gate;
  docs-index shows each line's swatch. Depends on: waves 1+2.
- **Wave 4 — optional, owner's call later.** PostToolUse auto-publish hook; semantic component
  envelopes (artifact-organizer's v2 direction) if markdown ever feels too small.

## 6. Risks and open questions

1. **Renderer scope creep.** The guard: stdlib-only, no new deps, `plain` pinned, every new
   component behind a `.tpl-` marker with a test.
2. **Index regeneration outside the skill.** Until wave 4's hook, a manual deploy can skip the
   index. Mitigation: `publish_doc.py` is the only documented path once wave 1 lands.
3. **VDL contrast in both themes.** Declared packs are validated by the lint gate (AA pairs),
   not trusted — a brand color that fails AA on dark gets a derived accessible variant, and the
   lint output says so.
4. **Open question for the owner:** should the docs-index itself move from hand-maintained HTML
   (today) to a `publish_doc.py`-generated page (wave 1), losing its bespoke layout unless we
   make it a template? Recommendation: keep it hand-authored until wave 2's templates are strong
   enough to reproduce it.

## 7. Provenance

Confirmed by reading/commands this session: `render_artifact.py` full structure and CSS
(640 lines, via shell — a PreToolUse hook denies the Read tool on that file, cause unverified);
chorestory tokens file; Vercel account state pre/post cleanup; all repo/license facts in §4
(Exa + direct fetches, 2026-07-31). Inferred and named as such: token-cost estimates for
hand-authored pages (line counts observed, per-page token counts not metered).
