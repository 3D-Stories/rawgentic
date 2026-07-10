# Peer Consult — .rawgentic-peer-problem-344.md

- Date: 2026-07-10
- Reviewer: Codex (peer designer)

## Approach

Introduce a small data-driven template registry over the existing two rendering engines. Keep one escape-first Markdown-subset pipeline, add paragraph buffering for multi-line emphasis, and give each new template a narrowly scoped structural mode, decorator set, CSS layer, and required lead section. New templates validate their human-first skeleton; legacy plain/roadmap remain permissive and unchanged by default. Documentation and workflow prose are pinned to the same registry vocabulary through tests.

## Key decisions

- Add a TemplateProfile registry with fields such as name, renderer, required_first_h2, body_class, decorators, and css_factory. Dispatch, CSS selection, and argparse choices derive from this registry, replacing the three independent if/else registries.
- Keep plain and roadmap as compatibility profiles. Default remains plain. dashboard reuses the roadmap section renderer but adds dashboard-only status-table and summary styling; it is not a literal alias, so its output is independently testable. Unknown or invalid configured values continue to resolve to plain.
- report uses the plain block renderer plus score-chip decoration for escaped standalone n/5 values, evidence block styling, and a required first H2 of “At a glance”.
- design uses shared H2 section wrappers, decision/rationale callouts, and a required first H2 of “Decision”. It does not introduce another Markdown parser.
- dashboard uses the existing roadmap H2-to-card structure, status chips/tables, and a required first H2 of “Status”. Existing roadmap output and pinned c-conf/c-defer/c-plan classes remain intact.
- review uses the plain renderer plus fixed verdict badges and Critical/High/Medium/Low severity badges, with “Verdict” required as the first H2.
- spec uses shared section wrappers, MUST/SHOULD/MAY requirement badges, acceptance-checklist styling, and “Summary” required as the first H2.
- Decorator transforms run only on already escaped text and emit fixed whitelisted span tags/classes. They skip fenced code and protect inline-code segments so score or severity text inside code is never decorated. No source text becomes an attribute or class name.
- Implement multi-line emphasis by buffering consecutive paragraph lines, escaping each source line first, joining them with a single space, then applying the existing inline transform once. Flush the buffer at blank lines and every recognized block boundary. Preserve explicit two-space line breaks as <br>.
- Strict skeleton validation applies only to report/design/dashboard/review/spec. The first H2 must match the profile’s required lead heading; failure produces a clear CLI error. plain and roadmap remain permissive for backward compatibility.
- CSS is component-oriented and included only when needed: artifact-section, lead-panel, score-chip, verdict-badge, severity-badge, evidence-block, status-table, requirement-badge, and the existing roadmap mstone/chip components. Shared tokens cover typography, semantic palette, spacing, radius, borders, and light/dark values.
- Make the Python registry the enforceable source of truth. docs/design-language.md records token semantics, component contracts, template skeletons, allowed decorator patterns, accessibility rules, and security invariants. Tests compare documented template/component markers and canonical invocation sentences against registry values.
- Expand design_artifact_style to accept the seven public names. Missing, unknown, or malformed configuration still falls back to plain; no implicit mapping to a more decorative style.
- Update WF1, WF2, WF3, WF14, and workspace-publish prose so each invocation names its template and includes one canonical sentence defining the required lead section. Add exact drift-guard assertions for those sentences.
- Produce the exemplar from a committed Markdown fixture rendered with the design language, then commit both source and HTML. A reproducibility test rerenders the fixture and byte-compares it with the exemplar.
- Bump the minor version in all three version-bearing locations, and update README and changelog with the template table, compatibility behavior, and skeleton requirements.

## Risks

- The biggest risk is paragraph buffering changing legacy plain HTML shape and line-break behavior, causing broad snapshot churn or subtle prose rendering changes. Minimize it with focused compatibility fixtures, explicit hard-break handling, and unchanged treatment of lists, tables, quotes, headings, and fences.
- Strict first-H2 validation can reject older artifacts when users opt into a new template. Keeping legacy styles permissive and returning an actionable error limits the compatibility impact.
- Regex decorators may produce false positives in prose. Require bounded, explicit forms, protect code spans, and test punctuation, HTML-like input, and adversarial escaped content.
- Shared section wrappers could accidentally leak roadmap markup or CSS into plain output. Profile-scoped CSS assembly and literal negative tests must preserve the existing plain invariants.
- The workspace publish skill is outside the repository, so its edit cannot be protected by the normal PR alone. Record its expected canonical sentence in a repo drift-guard fixture and verify the external file in the intended integration environment.

## Sketch

hooks/render_artifact.py:
- Add TemplateProfile and TEMPLATES for plain, roadmap, report, design, dashboard, review, spec.
- Add _render_blocks(lines, profile), _render_sectioned(...), _validate_skeleton(...), _decorate_inline(...), and _css_for(profile).
- Refactor _render_body, CSS concatenation, and argparse choices to use TEMPLATES.
- Escape each input line before paragraph buffering; decorators and inline transforms consume escaped strings only.

docs/design-language.md:
- Token table for type, color/light-dark semantics, spacing, radius, and borders.
- Component contract table with exact class names and allowed content patterns.
- Template matrix: renderer mode, required first H2, decorators, and intended artifact surface.
- Escape-first/CSP/accessibility invariants and exemplar regeneration command.

docs/design-language-example.md + .html:
- Exercise all shared components, dark-mode tokens, tables, and multi-line emphasis in a reproducible exemplar.

Workflow surfaces:
- WF14 → report; WF2/WF3 → design; WF1 → spec; workspace publish → template selected by artifact type with explicit style; setup prose documents the vocabulary and plain fallback.

Tests:
- One initially failing fixture per new template asserting unique structure or decoration.
- Skeleton success/failure tests for all five templates.
- Compatibility tests for default/plain/roadmap, including absence of .mstone and --chip-c in plain and preservation of pinned chip classes.
- Multi-line bold/emphasis, explicit hard break, block-boundary, table, quote, fence, and inline-code cases.
- Escape/adversarial tests for every decorator.
- Registry/argparse/CSS coverage tests, config fallback tests, documentation marker tests, five workflow drift guards, and exemplar byte-reproduction test.
- Run the full suite against the 2556-test baseline and account for every new test.

---
_Peer proposal (report-only). Synthesize at your discretion._
