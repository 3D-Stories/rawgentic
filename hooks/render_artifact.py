#!/usr/bin/env python3
"""Shared HTML design-artifact renderer (#174).

Turns a design/spec markdown doc into a self-contained, CSP-safe HTML artifact:
inline CSS only, no external hosts (no CDN link/script/font/img), so it renders
anywhere and survives a strict Content-Security-Policy. Optionally embeds a run's
telemetry (read from the run-record structure — never hand-retyped) and always
stamps a visible "Last updated" datetime.

SECURITY (the load-bearing property): this is an HTML generator fed
possibly-untrusted spec text, so it is **escape-first**. Every piece of text —
markdown body, title, telemetry values — is `html.escape`d BEFORE any block
transform runs, and the block transforms only ever wrap already-escaped text in
a fixed whitelist of tags. A `<script>` (or an `onerror=` attribute) in a spec
therefore renders as inert text, never as active markup.

STDLIB ONLY: the CI env installs just pytest + jsonschema, so this pulls in no
markdown library. The renderer handles the common blocks (headings, lists,
fenced code, blockquotes, tables, bold/inline-code, paragraphs) and leaves
anything else as an escaped paragraph — a lossy-but-safe floor, never an
injection. Consecutive plain lines (no blank/block line between them) form ONE
paragraph with soft-wrap joining — a single space between lines — and two-space
hard breaks (a line ending in 2+ spaces becomes a `<br>`), standard markdown
semantics (changed in #344; single-line paragraphs are unchanged).

Datetime default is mountain time (owner preference for rawgentic reports,
#174); pass `generated_at` for a deterministic stamp.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import datetime, timezone, timedelta


def _mountain_now() -> str:
    """Current wall-clock in Calgary/Alberta mountain time, with the CORRECT
    seasonal label (MDT in summer, MST in winter) — owner is in Calgary, AB, so
    use the real America/Edmonton zone rather than a fixed offset (a fixed UTC-7
    would read an hour slow and mislabel 'MST' during daylight time)."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/Edmonton"))
        return now.strftime("%Y-%m-%d %H:%M %Z")
    except Exception:
        # Fallback if tzdata is unavailable: UTC, honestly labelled (never a wrong MST/MDT).
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# --- inline markdown (escape-first: input here is ALREADY html.escape'd) ---

def _inline(escaped: str) -> str:
    """Apply inline emphasis to already-escaped text. Operates only on the
    escaped string, so it can never introduce an unescaped `<`."""
    # `code` first (so ** inside code is left alone)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    return escaped


# --- table detection helpers (escape-first callers still html.escape each cell) ---

_TABLE_SEP_CELL = re.compile(r"^:?-+:?$")


def _is_table_separator(line: str) -> bool:
    """A GFM separator row: pipe-delimited cells each matching ``:?-+:?`` (dashes
    with optional leading/trailing alignment colon), e.g. ``| --- | :-: |``."""
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2):
        return False
    cells = stripped.strip("|").split("|")
    return bool(cells) and all(_TABLE_SEP_CELL.match(c.strip()) for c in cells)


def _split_table_row(line: str) -> list[str]:
    """Split a ``| a | b |`` row into stripped cells, dropping the empty cells
    the leading/trailing pipes produce."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _render_body_plain(markdown: str, inline_fn=_inline) -> str:
    """Escape-first block renderer. Every line is escaped before classification;
    transforms only wrap escaped text in whitelisted tags. ``inline_fn`` is the
    inline pass applied to already-escaped text — defaults to bare ``_inline``;
    template renderers pass a decorator-wrapped variant (#344). It is never applied
    to fenced-code content (code stays verbatim, only html-escaped)."""
    # Normalize CR line endings so callers passing raw-CRLF strings get the same
    # hard-break detection as the CLI's universal-newline file read (#344 8a review).
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    i = 0
    in_list = False
    para: list[str] = []  # buffered consecutive plain source lines

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    def flush_para():
        """Emit the buffered plain lines as one <p>. Single-line buffers stay
        byte-identical to the pre-#344 renderer (escape the raw line, inline once).
        Multi-line buffers join with soft-wrap spaces, honouring two-space hard
        breaks (standard markdown): the placeholder \\x00 marks a hard break in the
        joined string, survives the single inline pass, then becomes <br>. Source
        \\x00 is stripped first so a literal null in the input can't forge a break."""
        if not para:
            return
        buf = para[:]
        para.clear()
        if len(buf) == 1:
            out.append(f"<p>{inline_fn(html.escape(buf[0]))}</p>")
            return
        src = [ln.replace("\x00", "") for ln in buf]  # (a) collision guard
        n = len(src)
        parts: list[str] = []
        for idx, ln in enumerate(src):
            hard = bool(re.search(r" {2,}$", ln))  # (b) 2+ trailing spaces
            if hard:
                ln = ln.rstrip(" ")
            parts.append(html.escape(ln))          # (c) escape each line
            if idx < n - 1:                         # separator; last-line break dropped
                parts.append("\x00" if hard else " ")
        joined = inline_fn("".join(parts))          # (d) inline ONCE over the whole
        joined = joined.replace("\x00", "<br>")     # (e) placeholder -> <br>
        out.append(f"<p>{joined}</p>")              # (f)

    while i < len(lines):
        raw = lines[i]

        # fenced code block — capture verbatim, escape the whole thing, no inline
        if raw.strip().startswith("```"):
            flush_para()
            close_list()
            i += 1
            code: list[str] = []
            closed = False
            while i < len(lines):
                if lines[i].strip().startswith("```"):
                    closed = True
                    i += 1
                    break
                code.append(lines[i])
                i += 1
            if closed:
                out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            else:
                # Unclosed fence: do NOT swallow the rest of the doc into one code
                # block (that silently drops every heading/section after it — a real
                # hazard for spec docs, which routinely contain fences). Render the
                # captured lines as normal blocks and warn.
                print("render_artifact: WARNING unclosed ``` fence — rendering the "
                      "remainder as normal text, not code", file=sys.stderr)
                out.append(_render_body_plain("\n".join(code), inline_fn=inline_fn))
            continue

        stripped = raw.strip()
        if not stripped:
            flush_para()
            close_list()
            i += 1
            continue

        m = re.match(r"(#{1,6})\s+(.*)", raw)
        if m:
            flush_para()
            close_list()
            level = len(m.group(1))
            out.append(f"<h{level}>{inline_fn(html.escape(m.group(2)))}</h{level}>")
            i += 1
            continue

        if re.match(r"[-*]\s+", stripped):
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            item = re.sub(r"^[-*]\s+", "", stripped)
            out.append(f"<li>{inline_fn(html.escape(item))}</li>")
            i += 1
            continue

        if stripped.startswith(">"):
            flush_para()
            close_list()
            quote = re.sub(r"^>\s?", "", stripped)
            out.append(f"<blockquote>{inline_fn(html.escape(quote))}</blockquote>")
            i += 1
            continue

        # table: a "| ... |" header row immediately followed by a "| --- | :-: |"
        # separator row. A pipe row with no separator next is NOT a table (falls
        # through to the paragraph branch below, unchanged).
        if (stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2
                and i + 1 < len(lines) and _is_table_separator(lines[i + 1])):
            flush_para()
            close_list()
            header_cells = _split_table_row(lines[i])
            out.append("<table>")
            out.append("<thead><tr>" + "".join(
                f"<th>{inline_fn(html.escape(c))}</th>" for c in header_cells) + "</tr></thead>")
            i += 2  # skip header + separator
            body_rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body_rows.append(_split_table_row(lines[i]))
                i += 1
            if body_rows:
                out.append("<tbody>")
                for cells in body_rows:
                    out.append("<tr>" + "".join(
                        f"<td>{inline_fn(html.escape(c))}</td>" for c in cells) + "</tr>")
                out.append("</tbody>")
            out.append("</table>")
            continue

        # plain line: no block pattern matched — buffer it; flushed on the next
        # blank/block boundary or EOF into ONE <p> (soft-wrap join + hard breaks).
        close_list()
        para.append(raw)
        i += 1

    flush_para()
    close_list()
    return "\n".join(out)


# --- #199: opt-in "roadmap" style — bubble cards + completion chips ---

# Completion status → chip class, in PRECEDENCE order. The chip LABEL is always the
# matched keyword from THIS fixed vocabulary (never raw section text), so it is
# escape-safe by construction — an injection in the status position can never reach
# the label. Word-boundary matched so "incomplete" does not match "complete".
_STATUS_VOCAB: tuple[tuple[tuple[str, ...], str], ...] = (
    (("done", "shipped", "merged", "complete"), "c-conf"),
    (("abandoned", "blocked", "dropped", "halted", "reverted"), "c-defer"),
    (("planned", "next", "not started", "in progress", "pending", "todo"), "c-plan"),
)


def status_chip(text: str) -> tuple[str, str]:
    """Return (css_class, label) for a section's completion status. Scans by
    category precedence (done > attention > planned), word-boundary matched.
    Fail-safe neutral ``("c-plan", "—")`` when no keyword is found. The label is
    drawn from the fixed vocab above, never the raw input — see the note there."""
    low = text.lower()
    for words, cls in _STATUS_VOCAB:
        for w in words:
            if re.search(rf"\b{re.escape(w)}\b", low):
                return (cls, w.upper())
    return ("c-plan", "—")


def _render_roadmap(markdown: str, inline_fn=_inline) -> str:
    """Render each ``## `` (h2) section as a dashboard-style ``.mstone`` bubble card
    titled with a completion chip. Preamble before the first h2 renders plain. All
    text stays escape-first: the heading is escaped exactly as the plain renderer
    does, the body goes through the plain renderer, and the chip label is fixed vocab.
    ``inline_fn`` threads through to the plain body render and the heading inline pass
    (#344) so dashboard-style templates decorate their card bodies; the chip label is
    NEVER inline_fn'd — it is fixed vocab, html-escaped only."""
    lines = markdown.split("\n")
    h2 = re.compile(r"##(?!#)\s+(.*)")  # h2 only — ### and deeper stay in-card

    # Section boundaries = h2 lines OUTSIDE fenced code blocks. Splitting must be
    # fence-aware: a "## " line inside a ``` fence (e.g. a Makefile `## help`
    # target or a doc quoting slot markdown) is content, not a new card.
    boundaries: list[int] = []
    in_fence = False
    for idx, ln in enumerate(lines):
        if ln.strip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and h2.match(ln):
            boundaries.append(idx)

    out: list[str] = []
    first = boundaries[0] if boundaries else len(lines)
    pre = lines[:first]
    if any(l.strip() for l in pre):
        out.append(_render_body_plain("\n".join(pre), inline_fn=inline_fn))

    for bi, start in enumerate(boundaries):
        end = boundaries[bi + 1] if bi + 1 < len(boundaries) else len(lines)
        heading = h2.match(lines[start]).group(1)
        sec = lines[start + 1:end]
        # A DEFINITIVE heading status (done/abandoned) wins — that's where the
        # author states it. A neutral/weak heading (c-plan, e.g. an incidental
        # "next" in "Next.js") does NOT suppress a definitive body status, so an
        # actually-shipped section still reads DONE. Fall through to the heading's
        # own neutral label only when the body is neutral too.
        h_cls, h_label = status_chip(heading)
        if h_cls in ("c-conf", "c-defer"):
            cls, label = h_cls, h_label
        else:
            b_cls, b_label = status_chip("\n".join(sec))
            cls, label = (b_cls, b_label) if b_cls in ("c-conf", "c-defer") else (h_cls, h_label)
        body_html = _render_body_plain("\n".join(sec), inline_fn=inline_fn)
        out.append(
            f'<section class="mstone"><h3>{inline_fn(html.escape(heading))} '
            f'<span class="chip {cls}">{html.escape(label)}</span></h3>'
            f'{body_html}</section>'
        )
    return "\n".join(out)


# --- #344: inline decorators — badge/chip markup on ALREADY-escaped-and-inlined text ---
#
# A decorator receives the output of `_inline` (escape-first: may contain only
# attribute-free <code>/<strong>, guarded by test_inline_closed_grammar_guard). It
# adds badge <span>s to the text OUTSIDE <code>…</code> spans only — a code span is
# a literal quote (e.g. `Severity: High`) and must render undecorated. Because the
# input is already escaped, every inserted span wraps inert text; a spec's <script>
# stays &lt;script&gt;. Each decorator runs ONE re.sub pass and never rescans its own
# inserted markup.

_CODE_SPAN_RE = re.compile(r"<code>.*?</code>", re.DOTALL)


def _decorate_outside_code(fragment: str, seg_fn) -> str:
    """Apply ``seg_fn`` to the parts of ``fragment`` OUTSIDE ``<code>…</code>`` spans,
    passing code spans through verbatim. ``fragment`` is `_inline` output."""
    out: list[str] = []
    last = 0
    for m in _CODE_SPAN_RE.finditer(fragment):
        out.append(seg_fn(fragment[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(seg_fn(fragment[last:]))
    return "".join(out)


def _decorate_scores(fragment: str) -> str:
    """Wrap ``N/5`` fidelity scores (N in 0–5, optional one decimal) in a ``.score`` chip."""
    return _decorate_outside_code(
        fragment,
        lambda seg: re.sub(r"\b([0-5](?:\.\d)?)/5\b",
                           r'<span class="score">\1/5</span>', seg))


def _decorate_severity(fragment: str) -> str:
    """Wrap a ``Severity: <Level>`` level in a ``.sev .sev-<level>`` badge (level lowercased)."""
    return _decorate_outside_code(
        fragment,
        lambda seg: re.sub(
            r"(Severity: )(Critical|High|Medium|Low)\b",
            lambda m: f'{m.group(1)}<span class="sev sev-{m.group(2).lower()}">{m.group(2)}</span>',
            seg))


def _decorate_requirements(fragment: str) -> str:
    """Wrap RFC-2119 keywords in a ``.req .req-<slug>`` badge. Longest-first alternation
    so ``MUST NOT`` becomes ONE ``req-must-not`` span, never a nested ``MUST`` span."""
    return _decorate_outside_code(
        fragment,
        lambda seg: re.sub(
            r"\b(MUST NOT|SHOULD NOT|MUST|SHOULD|MAY)\b",
            lambda m: f'<span class="req req-{m.group(1).lower().replace(" ", "-")}">{m.group(1)}</span>',
            seg))


def _render_body(markdown: str, style: str = "plain") -> str:
    """Dispatch on style via the ``_TEMPLATES`` registry (defined below with the CSS
    blocks). ``plain`` (default) is byte-for-byte the pre-#199 renderer. A decorator,
    if the template has one, is composed after ``_inline`` and applied wherever the
    body renderer runs its inline pass. Unknown styles fall back to plain."""
    renderer, _css, dec = _TEMPLATES.get(style, _TEMPLATES["plain"])
    inline_fn = (lambda esc: dec(_inline(esc))) if dec else _inline
    return renderer(markdown, inline_fn=inline_fn)


# --- telemetry (read-only consumer of the run-record shape) ---

def _telemetry_html(t: dict) -> str:
    """Render a run-record dict as an escaped telemetry table. Tolerant of missing
    keys (partial records are valid mid-lifecycle); every value is escaped."""
    if not isinstance(t, dict):
        # A telemetry value that isn't an object (schema drift, wrong file) must
        # not crash the render — surface it visibly instead of a raw traceback.
        return "<section class='telemetry-section'><h2>Run telemetry</h2>" \
               "<p><em>telemetry unavailable (not a run-record object)</em></p></section>"

    def esc(v):
        return html.escape(str(v))

    rows: list[str] = []
    issue = t.get("issue") or {}
    if issue:
        rows.append(f"<tr><th>Issue</th><td>#{esc(issue.get('number','?'))} "
                    f"({esc(issue.get('type','?'))}, {esc(issue.get('complexity','?'))})</td></tr>")
    if "lane" in t:
        rows.append(f"<tr><th>Lane</th><td>{esc(t['lane'])}</td></tr>")
    tests = t.get("tests") or {}
    if tests:
        rows.append(f"<tr><th>Tests</th><td>{esc(tests.get('added','?'))} added · "
                    f"{esc(tests.get('passing','?'))}/{esc(tests.get('total','?'))} passing</td></tr>")
    sec = t.get("security_scan") or {}
    if sec:
        skipped = ", ".join(esc(s) for s in (sec.get("skipped") or [])) or "none"
        rows.append(f"<tr><th>Security scan</th><td>{esc(sec.get('blocking_resolved',0))} blocking resolved · "
                    f"{esc(sec.get('advisory',0))} advisory · skipped: {skipped}</td></tr>")
    outcome = t.get("outcome") or {}
    if outcome:
        rows.append(f"<tr><th>Outcome</th><td>PR {esc(outcome.get('pr_number','?'))} · "
                    f"merged: {esc(outcome.get('merged','?'))} · CI: {esc(outcome.get('ci','?'))}</td></tr>")
    usage = t.get("usage") or {}
    if usage:
        wc = usage.get("wall_clock_s")
        rows.append(f"<tr><th>Usage</th><td>in {esc(usage.get('input_tokens','?'))} / "
                    f"out {esc(usage.get('output_tokens','?'))} tokens"
                    + (f" · {esc(wc)}s wall" if wc is not None else "") + "</td></tr>")

    gates = t.get("gates") or []
    gate_rows = ""
    for g in gates:
        if not isinstance(g, dict):
            continue  # drifted record: skip a non-dict gate entry rather than crash
        gate_rows += (f"<tr><td>{esc(g.get('step','?'))}</td><td>{esc(g.get('name','?'))}</td>"
                      f"<td>{esc(g.get('findings',0))}</td><td>{esc(g.get('resolved',0))}</td>"
                      f"<td>{esc(g.get('status','?'))}</td></tr>")
    gate_table = ""
    if gate_rows:
        gate_table = ("<h2>Quality gates</h2><table class='gates'><thead><tr>"
                      "<th>Step</th><th>Gate</th><th>Findings</th><th>Resolved</th><th>Status</th>"
                      "</tr></thead><tbody>" + gate_rows + "</tbody></table>")

    summary = ("<table class='telemetry'><tbody>" + "".join(rows) + "</tbody></table>") if rows else ""
    if not summary and not gate_table:
        # Passed a dict but nothing recognized (run-record schema drift) — a visible
        # placeholder beats silently emitting no telemetry on a lifecycle artifact
        # whose whole point is embedding the run data.
        return "<section class='telemetry-section'><h2>Run telemetry</h2>" \
               "<p><em>telemetry unavailable (no recognized run-record fields)</em></p></section>"
    return "<section class='telemetry-section'><h2>Run telemetry</h2>" + summary + gate_table + "</section>"


_STYLE = """
:root{--bg:#f6f7f8;--surface:#fff;--ink:#1a2126;--ink-2:#4b5a63;--ink-3:#7b8a92;
--line:#dde3e6;--accent:#0f766e;--code:#eef1f3}
@media(prefers-color-scheme:dark){:root{--bg:#12181c;--surface:#1a2228;--ink:#e7edf0;
--ink-2:#a8b6bd;--ink-3:#71808a;--line:#2a353c;--accent:#2dd4bf;--code:#232d34}}
:root[data-theme=dark]{--bg:#12181c;--surface:#1a2228;--ink:#e7edf0;--ink-2:#a8b6bd;
--ink-3:#71808a;--line:#2a353c;--accent:#2dd4bf;--code:#232d34}
:root[data-theme=light]{--bg:#f6f7f8;--surface:#fff;--ink:#1a2126;--ink-2:#4b5a63;
--ink-3:#7b8a92;--line:#dde3e6;--accent:#0f766e;--code:#eef1f3}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.6 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:900px;margin:0 auto;padding:0 20px 72px}
header{padding:40px 0 18px;border-bottom:1px solid var(--line);margin-bottom:20px}
.eyebrow{color:var(--accent);font-size:12px;font-weight:700;letter-spacing:.12em;text-transform:uppercase}
h1{font-size:clamp(22px,4vw,30px);margin:.3em 0;font-weight:750;letter-spacing:-.02em}
h2{font-size:19px;font-weight:700;margin:1.4em 0 .4em}
h3{font-size:16px;font-weight:650;margin:1.2em 0 .3em}
p,li{color:var(--ink-2)}
code{background:var(--code);border-radius:4px;padding:1px 5px;font:12.5px/1.4 ui-monospace,Menlo,Consolas,monospace}
pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:12px 14px;overflow-x:auto}
pre code{background:none;padding:0}
blockquote{border-left:3px solid var(--accent);margin:.6em 0;padding:.2em 0 .2em 14px;color:var(--ink-3)}
table{border-collapse:collapse;width:100%;margin:.6em 0;font-size:13.5px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}
.telemetry th{white-space:nowrap;color:var(--ink-3);width:1%}
.telemetry-section{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:4px 18px 14px;margin-top:24px}
footer{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);color:var(--ink-3);font-size:12.5px}
"""

# #199: injected ONLY in roadmap style, so plain output stays byte-identical to
# pre-#199. Adds the chip/card color tokens (dashboard values, light + dark) plus
# the .mstone / .chip / completion-color component rules the dashboard uses.
_ROADMAP_STYLE = """
:root{--chip-c:#0f766e;--chip-c-bg:#e6f2f0;--defer:#a16207;--defer-bg:#f8f2e2}
@media(prefers-color-scheme:dark){:root{--chip-c:#2dd4bf;--chip-c-bg:#123531;--defer:#fbbf24;--defer-bg:#302a14}}
:root[data-theme=dark]{--chip-c:#2dd4bf;--chip-c-bg:#123531;--defer:#fbbf24;--defer-bg:#302a14}
:root[data-theme=light]{--chip-c:#0f766e;--chip-c-bg:#e6f2f0;--defer:#a16207;--defer-bg:#f8f2e2}
.mstone{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:12px;padding:14px 16px;margin:14px 0}
.mstone h3{margin:0 0 .4em;font-size:15px;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.chip{font-size:11px;font-weight:700;letter-spacing:.04em;padding:2px 8px;border-radius:999px;text-transform:uppercase;white-space:nowrap}
.c-conf{color:var(--chip-c);background:var(--chip-c-bg)}
.c-defer{color:var(--defer);background:var(--defer-bg)}
.c-plan{color:var(--ink-2);background:var(--code)}
"""

# #344: shared component styles for the decorator badges — injected by every
# non-plain template (never plain, so plain stays byte-identical). Defines the
# severity/requirement color tokens in all three theme blocks (:root light, @media
# dark, [data-theme] overrides), consistent with the existing palette.
_COMPONENT_STYLE = """
:root{--sev-crit:#b91c1c;--sev-crit-bg:#fdecec;--sev-high:#c2410c;--sev-high-bg:#fdeee2;--sev-med:#a16207;--sev-med-bg:#f8f2e2;--sev-low:#4b5a63;--sev-low-bg:#eef1f3;--req-c:#0f766e;--req-c-bg:#e6f2f0}
@media(prefers-color-scheme:dark){:root{--sev-crit:#f87171;--sev-crit-bg:#3b1717;--sev-high:#fb923c;--sev-high-bg:#3a2410;--sev-med:#fbbf24;--sev-med-bg:#302a14;--sev-low:#a8b6bd;--sev-low-bg:#232d34;--req-c:#2dd4bf;--req-c-bg:#123531}}
:root[data-theme=dark]{--sev-crit:#f87171;--sev-crit-bg:#3b1717;--sev-high:#fb923c;--sev-high-bg:#3a2410;--sev-med:#fbbf24;--sev-med-bg:#302a14;--sev-low:#a8b6bd;--sev-low-bg:#232d34;--req-c:#2dd4bf;--req-c-bg:#123531}
:root[data-theme=light]{--sev-crit:#b91c1c;--sev-crit-bg:#fdecec;--sev-high:#c2410c;--sev-high-bg:#fdeee2;--sev-med:#a16207;--sev-med-bg:#f8f2e2;--sev-low:#4b5a63;--sev-low-bg:#eef1f3;--req-c:#0f766e;--req-c-bg:#e6f2f0}
.score{font:11.5px/1.4 ui-monospace,Menlo,Consolas,monospace;font-weight:700;background:var(--code);color:var(--accent);border-radius:5px;padding:1px 6px}
.sev{font-size:11px;font-weight:700;letter-spacing:.03em;padding:1px 7px;border-radius:999px;text-transform:uppercase;white-space:nowrap}
.sev-critical{color:var(--sev-crit);background:var(--sev-crit-bg)}
.sev-high{color:var(--sev-high);background:var(--sev-high-bg)}
.sev-medium{color:var(--sev-med);background:var(--sev-med-bg)}
.sev-low{color:var(--sev-low);background:var(--sev-low-bg)}
.req{font-size:11px;font-weight:700;letter-spacing:.03em;padding:1px 6px;border-radius:5px;color:var(--req-c);background:var(--req-c-bg)}
.req-must-not,.req-should-not{color:var(--sev-crit);background:var(--sev-crit-bg)}
.req-may{color:var(--ink-3);background:var(--code)}
"""

# #344: per-template accent blocks — small, tasteful, palette-only. Each carries its
# OWN distinct marker selector (.tpl-<name>) so tests can pin presence/absence.
_REPORT_STYLE = """
.tpl-report blockquote{background:var(--code);border-left-color:var(--accent)}
.tpl-report tbody tr:nth-child(even){background:var(--code)}
"""
_DESIGN_STYLE = """
.tpl-design h2{border-bottom:2px solid var(--accent);padding-bottom:.15em}
"""
_REVIEW_STYLE = """
.tpl-review tbody tr:nth-child(even){background:var(--code)}
.tpl-review blockquote{border-left-color:var(--accent);color:var(--ink-2)}
"""
_SPEC_STYLE = """
.tpl-spec table th{color:var(--accent)}
.tpl-spec li{margin:.2em 0}
"""
_DASHBOARD_STYLE = """
.tpl-dashboard tbody tr:nth-child(even){background:var(--code)}
.tpl-dashboard .mstone{padding:10px 14px;margin:10px 0}
"""

# #344: template registry — name -> (body_renderer, [extra_css_blocks], decorator|None).
# Insertion order is meaningful (drives argparse choices ordering). ``plain`` stays
# renderer=_render_body_plain, no CSS, no decorator → byte-identical to pre-#344.
_TEMPLATES = {
    "plain":     (_render_body_plain, [], None),
    "roadmap":   (_render_roadmap,    [_COMPONENT_STYLE, _ROADMAP_STYLE], None),
    "report":    (_render_body_plain, [_COMPONENT_STYLE, _REPORT_STYLE], _decorate_scores),
    "design":    (_render_body_plain, [_COMPONENT_STYLE, _DESIGN_STYLE], None),
    "dashboard": (_render_roadmap,    [_COMPONENT_STYLE, _ROADMAP_STYLE, _DASHBOARD_STYLE], None),
    "review":    (_render_body_plain, [_COMPONENT_STYLE, _REVIEW_STYLE], _decorate_severity),
    "spec":      (_render_body_plain, [_COMPONENT_STYLE, _SPEC_STYLE], _decorate_requirements),
}


def render_artifact(markdown: str, *, title: str, subtitle: str = "",
                    telemetry: dict | None = None, generated_at: str | None = None,
                    style: str = "plain") -> str:
    """Render `markdown` to a self-contained CSP-safe HTML string. All text is
    HTML-escaped before rendering (see module docstring). `generated_at` defaults
    to the current mountain-time stamp. `style` selects a template from ``_TEMPLATES``
    (plain default, unchanged; roadmap #199; report/design/dashboard/review/spec #344).
    Non-plain templates stamp ``<body class="tpl-<style>">`` and inject their CSS
    blocks; plain keeps a bare ``<body>`` (byte-stable). Unknown styles fall back to
    plain rendering and get no body class."""
    stamp = generated_at or _mountain_now()
    etitle = html.escape(title)
    esub = html.escape(subtitle)
    _renderer, css_blocks, _dec = _TEMPLATES.get(style, _TEMPLATES["plain"])
    body = _render_body(markdown, style=style)
    # `telemetry is not None` (not truthiness): an explicit empty {} means "record
    # present but empty" → the placeholder, distinct from None ("no telemetry").
    tel = _telemetry_html(telemetry) if telemetry is not None else ""
    sub_html = f'<p class="sub">{esub}</p>' if subtitle else ""
    css = _STYLE + "".join(css_blocks)
    # Body class marks non-plain templates for their accent selectors; plain stays a
    # bare <body> so plain output is byte-identical to pre-#344.
    body_class = f' class="tpl-{style}"' if style in _TEMPLATES and style != "plain" else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{etitle}</title>
<style>{css}</style>
</head>
<body{body_class}>
<div class="wrap">
<header>
<div class="eyebrow">rawgentic design artifact · updated {html.escape(stamp)}</div>
<h1>{etitle}</h1>
{sub_html}
</header>
<main>
{body}
{tel}
</main>
<footer>Last updated: {html.escape(stamp)} · generated by hooks/render_artifact.py — self-contained, no external resources.</footer>
</div>
</body>
</html>
"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render a markdown design doc to a CSP-safe HTML artifact.")
    ap.add_argument("--md", required=True, help="input markdown file")
    ap.add_argument("--out", required=True, help="output HTML file")
    ap.add_argument("--title", required=True)
    ap.add_argument("--subtitle", default="")
    ap.add_argument("--telemetry", help="run-record JSON file to embed (optional)")
    ap.add_argument("--generated-at", dest="generated_at", help="datetime stamp (default: mountain time now)")
    ap.add_argument("--style", choices=tuple(_TEMPLATES), default="plain",
                    help="template: plain (default), roadmap (h2 bubble cards + chips), "
                         "or report/design/dashboard/review/spec (#344 component styles)")
    args = ap.parse_args(argv)

    try:
        md = open(args.md, encoding="utf-8").read()
    except OSError as e:
        print(f"render_artifact: could not read markdown {args.md}: {e}", file=sys.stderr)
        return 2
    tel = None
    if args.telemetry:
        try:
            tel = json.load(open(args.telemetry, encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"render_artifact: could not read telemetry {args.telemetry}: {e}", file=sys.stderr)
            return 2
    html_out = render_artifact(md, title=args.title, subtitle=args.subtitle,
                               telemetry=tel, generated_at=args.generated_at,
                               style=args.style)
    open(args.out, "w", encoding="utf-8").write(html_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
