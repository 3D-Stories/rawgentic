# Rawgentic artifact design language

Every artifact `hooks/render_artifact.py` renders — a WF1 issue spec, a WF2/WF3 design
doc, a WF14 run-feedback report, a campaign dashboard, an adversarial-review report —
shares ONE visual system: a single palette, one type scale, one set of component badges,
and one human-first document skeleton. This doc is the reference for that system; the
values below are read from `hooks/render_artifact.py` (the `_STYLE` and `_COMPONENT_STYLE`
blocks), never invented, and drift-guarded by `tests/hooks/test_render_artifact.py`.

## Tokens

Base palette — every value is a CSS custom property defined in three theme blocks in
`_STYLE` (`:root` light, `@media(prefers-color-scheme:dark)`, and the
`:root[data-theme=…]` toggle overrides), so the artifact honours both the OS theme and a
viewer's explicit toggle.

| Token       | Light     | Dark      | Role                         |
| ----------- | --------- | --------- | ---------------------------- |
| `--bg`      | `#f6f7f8` | `#12181c` | page background              |
| `--surface` | `#ffffff` | `#1a2228` | card / telemetry surface     |
| `--ink`     | `#1a2126` | `#e7edf0` | primary text + headings      |
| `--ink-2`   | `#4b5a63` | `#a8b6bd` | body copy, list items        |
| `--ink-3`   | `#7b8a92` | `#71808a` | muted: blockquote, footer    |
| `--line`    | `#dde3e6` | `#2a353c` | borders, rules, table lines  |
| `--accent`  | `#0f766e` | `#2dd4bf` | eyebrow, links, left accents |
| `--code`    | `#eef1f3` | `#232d34` | code background, zebra rows   |

Component tokens — defined in `_COMPONENT_STYLE`, injected by every non-plain template
(the four severity ramps drive both the review badges and the requirement prohibition
badges; `--req-c` drives the affirmative RFC-2119 badge):

| Token           | Light     | Dark      | Role                       |
| --------------- | --------- | --------- | -------------------------- |
| `--sev-crit`    | `#b91c1c` | `#f87171` | critical severity text     |
| `--sev-crit-bg` | `#fdecec` | `#3b1717` | critical severity fill     |
| `--sev-high`    | `#c2410c` | `#fb923c` | high severity text         |
| `--sev-high-bg` | `#fdeee2` | `#3a2410` | high severity fill         |
| `--sev-med`     | `#a16207` | `#fbbf24` | medium severity text       |
| `--sev-med-bg`  | `#f8f2e2` | `#302a14` | medium severity fill       |
| `--sev-low`     | `#4b5a63` | `#a8b6bd` | low severity text          |
| `--sev-low-bg`  | `#eef1f3` | `#232d34` | low severity fill          |
| `--req-c`       | `#0f766e` | `#2dd4bf` | affirmative requirement    |
| `--req-c-bg`    | `#e6f2f0` | `#123531` | affirmative requirement bg |

Type scale (from `_STYLE`):

- **Body** — `15px/1.6` system stack (`-apple-system, "Segoe UI", Roboto, Helvetica,
  Arial, sans-serif`), on `--ink` over `--bg`.
- **h1** — `clamp(22px, 4vw, 30px)`, weight `750`, letter-spacing `-.02em`.
- **h2** — `19px`, weight `700`.
- **h3** — `16px`, weight `650`.
- **code / pre** — `12.5px/1.4` mono stack (`ui-monospace, Menlo, Consolas, monospace`)
  on a `--code` fill; tables are `13.5px`.

Spacing conventions (from `_STYLE`):

- `.wrap` — `max-width: 900px`, centred, `padding: 0 20px 72px`.
- `header` — `padding: 40px 0 18px`, bottom rule in `--line`.
- `pre` — `padding: 12px 14px`, `--line` border, 8px radius; `blockquote` — `14px` left
  pad behind a 3px `--accent` rule.

## Components

Contract table — the exact CSS class, what it means, and which template(s) emit it. A
decorator only fires under the template whose registry entry names it (see Templates);
the zebra and evidence accents are plain CSS carried by a template's accent block.

| Class                                                              | Purpose                                        | Emitted by (template)                    |
| ------------------------------------------------------------------ | ---------------------------------------------- | ---------------------------------------- |
| `.score`                                                           | `N/5` fidelity chip (`_decorate_scores`)       | report                                   |
| `.sev` + `.sev-critical`/`.sev-high`/`.sev-medium`/`.sev-low`      | `Severity: <Level>` badge (`_decorate_severity`) | review                                 |
| `.req` + `.req-must`/`.req-must-not`/`.req-should`/`.req-should-not`/`.req-may` | RFC-2119 keyword badge (`_decorate_requirements`) | spec                              |
| `.chip` + `.c-conf`/`.c-defer`/`.c-plan`                           | completion-status chip on a card title         | roadmap, dashboard                       |
| `.mstone`                                                          | h2 section rendered as a bubble card           | roadmap, dashboard                       |
| `table` / `thead` zebra (`tbody tr:nth-child(even)` on `--code`)   | striped rows for scan-ability                  | report, review, dashboard (accent block) |
| `blockquote` evidence accent (left rule / fill in `--accent`/`--code`) | quoted evidence stands out from prose      | report, review (accent block)            |

## Templates

The registry is `render_artifact._TEMPLATES` — one row per entry, in registry order.
"Renderer family" is the block renderer; "CSS layers" are the extra blocks appended to
`_STYLE`; "Decorator" is the inline pass composed after `_inline` (badge markup on
already-escaped text).

| Template    | Renderer family     | CSS layers                                | Decorator                | Surface + canonical invocation                                                                              |
| ----------- | ------------------- | ----------------------------------------- | ------------------------ | ----------------------------------------------------------------------------------------------------------- |
| `plain`     | `_render_body_plain`| none                                      | none                     | legacy / default — byte-identical to the pre-#199 renderer; the fallback for any unknown style.             |
| `roadmap`   | `_render_roadmap`   | component + roadmap                       | none                     | back-compat alias family of `dashboard` (h2 bubble cards + completion chips).                               |
| `report`    | `_render_body_plain`| component + report                        | `_decorate_scores`       | WF14 run-feedback reports — `--style report`.                                                               |
| `design`    | `_render_body_plain`| component + design                        | none                     | WF2/WF3 design artifacts + the WORKSPACE `design-doc-publish` skill — `--style design`. See the gap note below. |
| `dashboard` | `_render_roadmap`   | component + roadmap + dashboard           | none                     | campaign / roadmap shared docs — the denser card layout.                                                    |
| `review`    | `_render_body_plain`| component + review                        | `_decorate_severity`     | WF5 adversarial-review reports — `--style review`.                                                          |
| `spec`      | `_render_body_plain`| component + spec                          | `_decorate_requirements` | WF1 issue specs — `--style spec`.                                                                           |

**Gap note (design surface):** the `design-doc-publish` skill that invokes `--style
design` lives in the WORKSPACE skills tree (`.claude/skills/`), OUTSIDE this repo. It is
recorded here as the pin and edited in place; there is NO CI drift guard tying that skill
to this table, so a divergence there will not fail this repo's suite — check it by hand
when the design surface changes.

## Human-first skeleton

Every templated artifact opens with its verdict-first lead section — the at-a-glance
summary, decision, status, or verdict — before any evidence or detail, so the document
reads top-down for a human. The renderer does not enforce this; it is an authoring
contract on the markdown fed in. Per template, the lead section is:

- **report** → "At a glance" — the headline pass/fail and the one-line delta.
- **design** → "Decision" — what was chosen, up front, before the rationale.
- **dashboard** → "Status" — the campaign's current state in one card.
- **review** → "Verdict" — confirmed / refuted / inconclusive before the findings.
- **spec** → "Summary" — the issue's ask in one paragraph before the requirements.

This mirrors the WF14 rubric precedent — see `skills/run-feedback/references/rubric.md`,
"Report structure — human-first", which established verdict-first lead sections for
run-feedback reports.

## Security invariants

The renderer is fed possibly-untrusted spec text, so the design language is enforced on
already-safe HTML. These invariants are load-bearing, not stylistic:

- **Escape-first.** Every piece of text — markdown body, title, telemetry — is
  `html.escape`d BEFORE any block or inline transform. A `<script>` in a spec renders as
  inert `&lt;script&gt;` text, never active markup.
- **CSP-safe / self-contained.** Inline CSS only; no external host (no CDN link/script/
  font/img), so the artifact survives a strict Content-Security-Policy and renders
  anywhere offline.
- **Decorators run on escaped text, outside code spans.** Every badge decorator wraps the
  output of `_inline` and only touches the segments OUTSIDE `<code>…</code>`, so a literal
  quote like `` `Severity: High` `` or `` `MUST` `` stays undecorated, and every inserted
  `<span>` wraps inert, already-escaped text.
- **`_inline` closed grammar.** `_inline` may only emit attribute-free `<code>`/`<strong>`
  — pinned by `test_inline_closed_grammar_guard`. The decorators' code-span split relies
  on this; growing `_inline` (e.g. links/attributes) forces revisiting the decorators.

## Exemplar

`docs/design-language-example.md` is a fixture exercising every block the renderer
handles (headings, wrapped + hard-break bold, a table, a blockquote, a fenced code block
whose `MUST`/`3/5` stay verbatim, score/severity/RFC-2119 lines). Its committed render,
`docs/design-language-example.html`, is reproducible byte-for-byte from source with a
PINNED stamp and the `design` style. Regenerate it with exactly this one-liner (run from
the repo root):

```
python3 -c "import sys; sys.path.insert(0,'hooks'); from render_artifact import render_artifact; open('docs/design-language-example.html','w').write(render_artifact(open('docs/design-language-example.md').read(), title='Design-language exemplar', generated_at='2026-07-10 12:00 MDT', style='design'))"
```

**Reproducibility contract:** `test_exemplar_reproducible_byte_for_byte` re-renders the
fixture with the same pinned `generated_at` + `title` + `style` and byte-compares to the
committed HTML. Any renderer change that alters the design-style output must regenerate
this exemplar in the same commit, or the guard fails — the exemplar is the executable
proof that this doc and the renderer agree.
