# Adversarial Review — .rawgentic-design-344.md

- Date: 2026-07-10
- Artifact type: design
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 7 (Critical 0, High 0, Medium 7, Low 0)

## Summary

The artifact proposes a shared Markdown renderer with CSS-driven templates, post-inline decorators, centralized style resolution, and skill/documentation updates. The main risks are an invalid HTML-safety assumption, underspecified semantic styling, silent configuration fallback, and unproven permission to modify an out-of-repository workspace surface.

## Findings

### 1. [Medium] ambiguity · high confidence — Renderer changes, items 3–4

> - `_decorate_requirements` (spec): `\b(MUST NOT|MUST|SHOULD NOT|SHOULD|MAY)\b` →
>      `<span class="req req-<slug>">…</span>` (longest-first alternation).

The decorator recognizes `MUST NOT` and `SHOULD NOT`, but the component vocabulary defines only `req-must`, `req-should`, and `req-may`. The `<slug>` mapping is unspecified: conventional slugs produce `req-must-not` and `req-should-not`, which have no declared styling, while collapsing them to positive classes is also not stated. Implementations can therefore produce unstyled negative requirement badges or incompatible class vocabularies.

**Recommendation:** In Renderer changes items 3 and 4, define an explicit token-to-class mapping. Either add and style `req-must-not` and `req-should-not`, or state and test that negative forms intentionally map to `req-must` and `req-should`.
**Ambiguity:** The transformation represented by `<slug>` is not defined for the two multiword tokens.

### 2. [Medium] correctness · high confidence — Renderer changes, item 1, normative algorithm step (d)

> (d) apply the template's
>    inline function (`_inline` composed with the decorator, item 4) to each
>    between-break segment;

Applying `_inline` independently on each side of a hard break prevents paired inline delimiters from spanning that break. For example, `**a  \nb**` is divided into `**a` and `b**`, so neither call can emit the intended `<strong>a<br>b</strong>` and literal asterisks remain. This conflicts with the item’s combined goal of multi-line emphasis and hard-break handling.

**Recommendation:** Change item 1 to parse inline markup across the complete joined paragraph while representing hard breaks with protected tokens, then replace those tokens with `<br>` after inline parsing. Add fixtures for emphasis and code spans crossing hard-break boundaries.

### 3. [Medium] correctness · medium confidence — Config + prose changes, item 6

> - Key ABSENT (no designArtifact block, no style key, missing project/file):
>      return **`design`** — the documented default for design artifacts.

The design treats a missing project or configuration file as equivalent to an intentionally omitted style key and emits no warning. A wrong path, unavailable file, or failed project discovery will therefore silently select `design`, hiding the operational failure and producing the wrong template without the surfacing assertion or log required for silent-failure calls.

**Recommendation:** In Config + prose changes item 6, distinguish intentional absence of the optional key from failure to locate or read an expected project/file. Keep `design` for a successfully read configuration with no key, but warn or fail for an explicitly supplied missing or unreadable path, and test stderr for that case.
**Ambiguity:** The text does not define whether “missing project/file” is an expected optional state or a failed lookup of an expected input.

### 4. [Medium] correctness · high confidence — Config + prose changes, item 6

> Template names come from `render_artifact._TEMPLATES` via
>    cross-hook import; `except ImportError` ONLY falls back to a literal tuple AND
>    prints a stderr warning that the fallback engaged [F:M6].

Catching `ImportError` around the cross-hook import also catches import failures raised from inside `render_artifact` or its dependencies, not just absence of the registry symbol/module. The resolver can consequently mask a broken renderer as a recoverable registry fallback, accept a style from the literal tuple, and defer failure until actual rendering.

**Recommendation:** In Config + prose changes item 6, catch `ModuleNotFoundError` only when its `name` identifies the intended optional module, and re-raise nested import failures. Prefer moving the template-name constants into a dependency-neutral shared module so configuration resolution does not import the renderer and its transitive dependencies.

### 5. [Medium] feasibility · high confidence — Renderer changes, item 2, dashboard definition

> `dashboard` is the roadmap
>    renderer plus its own `_DASHBOARD_STYLE` accent (status-table zebra + summary-row
>    emphasis) — a distinct, independently-testable template, not a literal alias
>    [F:H3]

The dashboard adds only CSS and retains the roadmap renderer, but the design provides no class, attribute, or structural contract identifying a summary row. CSS cannot determine semantic row purpose from generic table markup; a positional selector would emphasize the last row of every matching table and miss summaries located elsewhere.

**Recommendation:** In Renderer changes item 2, define how summary rows are identified. Add an escape-safe renderer annotation such as `class="summary-row"` from an explicit source marker, or replace “summary-row emphasis” with a precisely documented positional rule and test its behavior on non-summary tables.
**Ambiguity:** Neither the source syntax nor generated markup that distinguishes a summary row is specified.

### 6. [Medium] feasibility · high confidence — Config + prose changes, item 7; Platform / external dependencies

> **Workspace surface [F:H1]:** `.claude/skills/design-doc-publish/SKILL.md` lives
>    OUTSIDE this repo — it is NOT an AC4 deliverable of this PR. Handling: (a) its
>    canonical invocation (`--style design`) is recorded repo-side in
>    docs/design-language.md's template table (the enforceable pin); (b) the file is
>    edited in place as workspace-side follow-through (the issue's own wording); (c) a
>    follow_ups entry records that the workspace skill has no CI guard and any other
>    checkout must apply the same edit.

The required in-place mutation is outside the repository, but the artifact cites no capability/permission file or completed spike proving that this project’s sandbox can write that path. It is also deliberately absent from the PR and CI, so other checkouts are guaranteed not to receive it through the proposed delivery mechanism. If workspace writes are restricted, even the originating checkout remains stale.

**Recommendation:** In Config + prose changes item 7 and Platform / external dependencies, cite the exact workspace capability granting writes and add a preflight spike for this path. If that permission cannot be proven, move the canonical skill into a versioned repository/plugin surface or explicitly remove the external edit from the issue’s delivered scope and make it a separately owned deployment task.
**Ambiguity:** The provided text contains no workspace capability or sandbox configuration establishing permission to edit the external path.

### 7. [Medium] security · high confidence — Renderer changes, item 4

> 4. **Decorators — inline-stage, never over rendered HTML [F:H2].** Each template's
>    optional decorator COMPOSES with `_inline`: the body renderers call
>    `inline_fn(escaped_text)` where `inline_fn = lambda esc: decorate(_inline(esc))`.

The decorator does operate over rendered inline HTML: `_inline(esc)` runs first and produces tags. Protecting only `<code>…</code>` does not establish that attributes are unreachable. If `_inline` emits links, images, or any other attribute-bearing element, a score, severity, or requirement token inside an attribute can be replaced with a `<span>`, corrupting the generated markup. The provided text neither constrains `_inline` to attribute-free output nor tests every attribute-bearing output kind.

**Recommendation:** Change Renderer changes item 4 so decoration occurs on parsed inline text tokens before HTML serialization. If `_inline` cannot expose tokens, document its complete output grammar and protect all generated tags and attributes, with fixtures containing decorator patterns in every attribute-bearing inline construct.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._