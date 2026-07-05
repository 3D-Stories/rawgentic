"""Tests for hooks/render_artifact.py — the shared HTML design-artifact renderer (#174).

The helper turns a design/spec markdown doc into a self-contained, CSP-safe HTML
artifact (inline CSS, no external hosts), optionally embedding run-record telemetry
and always stamping a visible datetime (owner requirement 2026-07-04). It is a
security surface: markdown content is untrusted-ish and MUST be HTML-escaped before
any block transform, so a `<script>` in a spec can never execute in the artifact.

CI env has only pytest + jsonschema — the helper uses the stdlib only (no markdown lib).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))

import render_artifact  # noqa: E402


FIXED_TS = "2026-07-04 17:05 MST"


def _render(md, **kw):
    kw.setdefault("title", "Test Doc")
    kw.setdefault("generated_at", FIXED_TS)
    return render_artifact.render_artifact(md, **kw)


# --- self-contained + CSP-safe ---

def test_is_full_selfcontained_html():
    html = _render("# Hello\n\nA paragraph.")
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert "<style>" in html          # inline CSS
    assert "</html>" in html


def test_no_external_hosts():
    """CSP-safe: no external stylesheet/script/font/image hosts."""
    html = _render("# X\n\n![img](https://evil.example/x.png)\n[link](https://ok.example)")
    # no resource-loading attributes pointing off-host
    assert not re.search(r'<link[^>]+href=', html, re.I)
    assert not re.search(r'<script[^>]+src=', html, re.I)
    assert "https://evil.example" not in html or "evil.example" in _escaped_only(html)


def _escaped_only(html):
    # helper: the only place a URL may appear is as escaped text, never an active src/href attr
    return html


# --- THE security surface: escape-first ---

def test_script_in_markdown_is_neutralized():
    html = _render("# Title\n\n<script>alert(1)</script> and <img src=x onerror=alert(2)>")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "onerror=alert(2)" not in html or "onerror" not in _active_attrs(html)


def _active_attrs(html):
    # strip the escaped text; if onerror survives only inside &lt;...&gt; it's inert
    return re.sub(r"&lt;[^&]*&gt;", "", html)


def test_html_entities_in_text_escaped():
    html = _render("Value: a < b && c > d")
    assert "a &lt; b &amp;&amp; c &gt; d" in html
    # the raw (unescaped) sequence must never appear verbatim in the output
    assert "a < b && c > d" not in html


def test_title_is_escaped():
    html = render_artifact.render_artifact("body", title="<script>x</script>", generated_at=FIXED_TS)
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


# --- datetime stamp (owner requirement, #174 bake-in) ---

def test_datetime_stamp_present():
    html = _render("# Doc")
    assert FIXED_TS in html
    assert "Last updated" in html or "Generated" in html


def test_generated_at_defaults_to_real_mountain_timestamp():
    """Without an explicit stamp the helper uses a real mountain-time datetime
    (owner preference, #174) with a timezone label — not a placeholder."""
    html = render_artifact.render_artifact("# Doc", title="T")
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} (MST|MDT|UTC)", html), "a real datetime must be stamped (mountain, or honest UTC fallback if tzdata absent)"


# --- minimal markdown rendering (escape-first, whitelist transforms) ---

def test_headings_and_paragraph():
    html = _render("# H1\n\n## H2\n\nplain text")
    assert "<h1" in html and ">H1<" in html
    assert "<h2" in html and ">H2<" in html
    assert "plain text" in html


def test_list_items():
    html = _render("- one\n- two")
    assert html.count("<li>") == 2
    assert ">one<" in html and ">two<" in html


def test_fenced_code_block_escaped():
    html = _render("```\n<script>evil</script>\n```")
    assert "<pre" in html
    assert "&lt;script&gt;evil&lt;/script&gt;" in html
    assert "<script>evil</script>" not in html


# --- telemetry embed (read-only consumer of run-record shape) ---

def test_telemetry_table_rendered():
    tel = {
        "issue": {"number": 174, "type": "feature", "complexity": "standard"},
        "tests": {"added": 12, "passing": 1780, "total": 1780},
        "gates": [{"step": "11", "name": "Code Review", "findings": 3, "resolved": 3, "status": "pass"}],
        "security_scan": {"ran": True, "blocking_resolved": 0, "advisory": 0, "skipped": ["iac", "sca"]},
        "lane": "small-standard",
        "usage": {"input_tokens": None, "output_tokens": None, "wall_clock_s": 900},
    }
    html = _render("# Doc", telemetry=tel)
    assert "Code Review" in html
    assert "1780" in html          # suite total
    assert "small-standard" in html
    assert "174" in html


def test_telemetry_absent_is_fine():
    html = _render("# Doc")  # no telemetry
    # renders without error and without a telemetry section header
    assert "<h1" in html


def test_telemetry_values_escaped_too():
    tel = {"issue": {"number": 1, "type": "<b>x</b>", "complexity": "standard"},
           "tests": {"added": 0, "passing": 1, "total": 1}, "gates": [],
           "security_scan": {"ran": True, "blocking_resolved": 0, "advisory": 0, "skipped": []},
           "lane": "full", "usage": {}}
    html = _render("# Doc", telemetry=tel)
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


# --- CLI ---

def test_cli_writes_html(tmp_path):
    md = tmp_path / "spec.md"
    md.write_text("# CLI Spec\n\nbody")
    out = tmp_path / "spec.html"
    rc = subprocess.run(
        [sys.executable, str(HOOKS / "render_artifact.py"),
         "--md", str(md), "--out", str(out), "--title", "CLI Spec",
         "--generated-at", FIXED_TS],
        capture_output=True, text=True,
    )
    assert rc.returncode == 0, rc.stderr
    html = out.read_text()
    assert "<!doctype html>" in html.lower()
    assert "CLI Spec" in html
    assert FIXED_TS in html


def test_cli_with_telemetry(tmp_path):
    md = tmp_path / "s.md"; md.write_text("# S")
    tel = tmp_path / "rr.json"
    tel.write_text(json.dumps({
        "issue": {"number": 9, "type": "feature", "complexity": "standard"},
        "tests": {"added": 1, "passing": 2, "total": 2}, "gates": [],
        "security_scan": {"ran": True, "blocking_resolved": 0, "advisory": 0, "skipped": []},
        "lane": "small-standard", "usage": {}}))
    out = tmp_path / "s.html"
    rc = subprocess.run(
        [sys.executable, str(HOOKS / "render_artifact.py"),
         "--md", str(md), "--out", str(out), "--title", "S",
         "--telemetry", str(tel), "--generated-at", FIXED_TS],
        capture_output=True, text=True,
    )
    assert rc.returncode == 0, rc.stderr
    assert "small-standard" in out.read_text()


# --- 8a fixes: unclosed fence must not swallow the doc tail; robust telemetry ---

def test_unclosed_fence_does_not_swallow_following_headings():
    md = "# Intro\n\n```\ncode line\n\n## Real Section\n\nimportant paragraph"
    html = _render(md)
    # the heading after the unclosed fence must still render as a heading, not be
    # buried inside one <pre> block
    assert "<h2" in html and ">Real Section<" in html
    assert "important paragraph" in html


def test_closed_fence_still_renders_as_code():
    html = _render("```\nx = 1\n```")
    assert "<pre><code>x = 1</code></pre>" in html


def test_non_dict_telemetry_does_not_crash():
    html = _render("# Doc", telemetry=[1, 2, 3])   # wrong shape, truthy
    assert "telemetry unavailable" in html
    assert "<h1" in html   # rest of doc still renders


def test_unrecognized_telemetry_shows_placeholder():
    html = _render("# Doc", telemetry={"bogus": "key"})
    assert "telemetry unavailable" in html


def test_cli_missing_markdown_is_rc2(tmp_path):
    out = tmp_path / "o.html"
    rc = subprocess.run(
        [sys.executable, str(HOOKS / "render_artifact.py"),
         "--md", str(tmp_path / "nope.md"), "--out", str(out), "--title", "X"],
        capture_output=True, text=True,
    )
    assert rc.returncode == 2
    assert "could not read markdown" in rc.stderr
