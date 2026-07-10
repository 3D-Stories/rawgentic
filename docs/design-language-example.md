# Design-language exemplar

This fixture exercises every markdown block the renderer handles, so the committed
HTML doubles as a visual proof of the design language. It is rendered with the
`design` style (h2 underline accent, component tokens available).

## Decision

Adopt the shared design language for every artifact `hooks/render_artifact.py`
renders. This exemplar is the reproducible reference: regenerate it from source and
byte-compare (see the doc's Exemplar section).

## Prose and emphasis

A paragraph can carry **bold that wraps
across a soft line** and still read as one run. A two-space hard break forces a
visible line break here:  
this sentence starts on its own line.

## Tokens at a glance

| Token      | Light     | Dark      |
| ---------- | --------- | --------- |
| `--bg`     | `#f6f7f8` | `#12181c` |
| `--accent` | `#0f766e` | `#2dd4bf` |
| `--ink`    | `#1a2126` | `#e7edf0` |

## Evidence

> Every templated artifact opens with its verdict-first lead section, so a human
> reads the document top-down before diving into detail.

## Component samples

- **Fidelity 4/5** — the score chip lives in the report template.
- Severity: High — the severity badge lives in the review template.
- The client MUST retry on 503. It MUST NOT retry on 400. It SHOULD back off. It MAY log.

## A literal block

Inside a fenced code block, `MUST` and `3/5` stay verbatim — never decorated:

```
requirement: MUST hold the invariant
fidelity: 3/5
```
