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
fenced code, blockquotes, bold/inline-code, paragraphs) and leaves anything
else as an escaped paragraph — a lossy-but-safe floor, never an injection.

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


def _render_body(markdown: str) -> str:
    """Escape-first block renderer. Every line is escaped before classification;
    transforms only wrap escaped text in whitelisted tags."""
    lines = markdown.split("\n")
    out: list[str] = []
    i = 0
    in_list = False

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    while i < len(lines):
        raw = lines[i]

        # fenced code block — capture verbatim, escape the whole thing, no inline
        if raw.strip().startswith("```"):
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
                out.append(_render_body("\n".join(code)))
            continue

        stripped = raw.strip()
        if not stripped:
            close_list()
            i += 1
            continue

        esc = html.escape(raw)

        m = re.match(r"(#{1,6})\s+(.*)", raw)
        if m:
            close_list()
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(html.escape(m.group(2)))}</h{level}>")
            i += 1
            continue

        if re.match(r"[-*]\s+", stripped):
            if not in_list:
                out.append("<ul>")
                in_list = True
            item = re.sub(r"^[-*]\s+", "", stripped)
            out.append(f"<li>{_inline(html.escape(item))}</li>")
            i += 1
            continue

        if stripped.startswith(">"):
            close_list()
            quote = re.sub(r"^>\s?", "", stripped)
            out.append(f"<blockquote>{_inline(html.escape(quote))}</blockquote>")
            i += 1
            continue

        close_list()
        out.append(f"<p>{_inline(esc)}</p>")
        i += 1

    close_list()
    return "\n".join(out)


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


def render_artifact(markdown: str, *, title: str, subtitle: str = "",
                    telemetry: dict | None = None, generated_at: str | None = None) -> str:
    """Render `markdown` to a self-contained CSP-safe HTML string. All text is
    HTML-escaped before rendering (see module docstring). `generated_at` defaults
    to the current mountain-time stamp."""
    stamp = generated_at or _mountain_now()
    etitle = html.escape(title)
    esub = html.escape(subtitle)
    body = _render_body(markdown)
    tel = _telemetry_html(telemetry) if telemetry else ""
    sub_html = f'<p class="sub">{esub}</p>' if subtitle else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{etitle}</title>
<style>{_STYLE}</style>
</head>
<body>
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
                               telemetry=tel, generated_at=args.generated_at)
    open(args.out, "w", encoding="utf-8").write(html_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
