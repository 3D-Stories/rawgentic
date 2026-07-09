# Official Workflow Diagram

`docs/workflow-diagram.html` is rawgentic's **official, versioned workflow diagram**
(#197, epic #188): a self-contained, hash-routed single-file page rendering every
workflow's spine as a drafting document — clickable per-station drill-down, a REV
**dropdown** over historical WF2 spines (a `<select>`, so it scales to many
revisions), drafting-convention revision triangles (Δ) on stations changed since the
prior rev, and loop-back budgets drawn as return arcs.

It is the **canonical workflow reference** — separate from the WF2 *health/proposals*
review artifact (`docs/planning/2026-07-04-workflow-modernization-review.html`), which
is a point-in-time review overlay.

**Keeping it current is a standing pre-PR requirement**, exactly like the README:
any PR that alters a documented workflow spine (WF1/WF2/WF3/WF5 steps, gates,
loop-backs, lane behavior) must append a new REV here before merge. See the Pre-PR
Checklist in the repo-root `CLAUDE.md` (item 4). A PR that touches no workflow spine
needs no diagram edit — confirm that deliberately rather than skipping silently.

## Viewing it

- **Interactive:** open `docs/workflow-diagram.html` in any browser (fully offline —
  fonts embedded, zero external requests), or via **GitHub Pages** once enabled
  (Settings → Pages → Deploy from branch → `main` / `/docs`, or
  `gh api repos/3D-Stories/rawgentic/pages -X POST -f "source[branch]=main" -f "source[path]=/docs"`),
  at `https://3d-stories.github.io/rawgentic/workflow-diagram.html`.
- **README:** the repo main page embeds theme-aware snapshots
  (`docs/assets/workflow-diagram-{light,dark}.png`) via GitHub's
  `<picture>`/`prefers-color-scheme` pattern, linking to the interactive page.

## Content contract

Per-station content (purpose, sub-steps, gate facts, lane behavior) is derived from
the **pinned plugin sources**: `skills/<workflow>/SKILL.md` +
`references/steps.md` at the version named in the sheet's REV stamp. WF2 carries
full drill-down detail; WF1 / WF3 / WF5 are phase-level registry entries
("skeletal sheets") proving the multi-workflow structure — their full detail sheets
arrive in later revisions. The structural pytest guards live in
`tests/test_workflow_diagram.py` (station coverage, both snapshots, self-containment,
no-innerHTML rendering, theme completeness, README embed).

## Appending the next revision (the reason this exists)

WF2 is actively evolving. When a merged PR changes the WF2 spine:

1. In `docs/workflow-diagram.html`, find `const DATA` → `workflows.wf2`.
2. Add the new version key to `revs` (newest **first** — `revs[0]` is the default view)
   and a matching entry under `versions`.
3. The new entry holds the **full** steps array; the now-previous version keeps its
   snapshot. For small deltas use the compact form: `steps:null` +
   `overrides:{<station id>: {sum, purpose, sub, facts, lane}}` — the loader builds
   ANY `steps:null` entry from that workflow's newest rev (`revs[0]`) automatically
   (overrides replace whole fields; `rev` markers are stripped on old sheets). The
   "Revision Delta · since <prior>" panel title and the default landing rev are both
   derived from the `revs` order — no code edits needed.
4. Mark each changed station in the NEW version with
   `rev:{delta:"…", refs:["#NNN"]}` — that renders the red revision triangle and the
   "Revision Delta" panel.
5. Set the old version's `superseded:"<new>"` so it renders the SUPERSEDED stamp.
6. Manual updates that remain: the provenance footer string (`@ plugin X.Y.Z`) and the
   README snapshots (below). Run `pytest tests/test_workflow_diagram.py`.

Skeletal workflows follow the same shape with `skeletal:true` and phase-level steps.

## Regenerating the README snapshots

Serve the file over localhost (browsers block `file:` in headless tooling), render at
a 1440px-wide viewport, and take a **full-page** screenshot (not viewport-only — a
1200px-tall viewport capture clips the sheet after ~6 stations and still passes CI) of
the WF2 overview once per theme (the theme is forced
by stamping `data-theme` on `<html>`):

```
python3 -m http.server 8478 --bind 127.0.0.1   # from a dir containing the html
# Playwright (or any browser automation):
#   goto http://127.0.0.1:8478/workflow-diagram.html#/wf2/<rev>
#   documentElement.setAttribute('data-theme','light')  → screenshot docs/assets/workflow-diagram-light.png
#   documentElement.setAttribute('data-theme','dark')   → screenshot docs/assets/workflow-diagram-dark.png
```

Screenshot at device scale (2×) for crisp README rendering.

## Design notes

- **Visual concept:** an engineering drafting document — title block, REV stamps as
  the version selector, revision triangles, dimension-line connectors; light theme is
  a colored-ink drawing on vellum, dark theme a luminous blueprint print.
- **Rendering:** DOM-builder only (`h()` + `replaceChildren`) — **no `innerHTML`**
  anywhere; this is both the repo security-hook contract and showcase hygiene, and
  it is test-enforced.
- **Station drill-down = modal overlay (#227):** clicking a station opens its detail in
  a native `<dialog>` (`showModal()`) over the dimmed, still-rendered overview — the
  reader keeps their place in the spine, instead of a full-page swap. The hash still
  carries `#/wf2/<ver>/s/<id>` (deep-links + back/forward open/close the modal); Esc, the
  close button, and a backdrop click all route back to `#/wf2/<ver>`. `<dialog>` provides
  focus-trap, focus-restore, and `::backdrop` for free; open/close animation honors
  `prefers-reduced-motion`. `render()` (re)builds the overview only on a wf/rev change and
  opens/closes the modal per the `/s/<id>` segment. A drift guard pins the modal so a later
  edit can't silently revert to the full-page swap.
- **Fonts** are embedded as base64 woff2 latin subsets, all licensed under the
  **SIL Open Font License 1.1**: IBM Plex Mono / IBM Plex Sans (© IBM Corp.,
  github.com/IBM/plex) and Big Shoulders (© the Big Shoulders Project Authors,
  github.com/xotypeco/big_shoulders). The ~200KB file size is dominated by these
  subsets — accepted for a fully-offline single-file artifact.
- **Showcase seed (AC6):** the registry-per-workflow data model, the standalone
  single-file build, and the Pages-servable `docs/` location are the pieces a public
  rawgentic showcase site would anchor on.
