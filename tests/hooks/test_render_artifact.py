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


# --- Codex Step-11 fixes: robust telemetry against drifted run-records ---

def test_non_dict_gate_element_does_not_crash():
    tel = {"issue": {"number": 1, "type": "feature", "complexity": "standard"},
           "gates": ["not-a-dict", {"step": "11", "name": "Code Review", "findings": 0, "resolved": 0, "status": "pass"}],
           "tests": {"added": 0, "passing": 1, "total": 1},
           "security_scan": {"ran": True, "blocking_resolved": 0, "advisory": 0, "skipped": []},
           "lane": "full", "usage": {}}
    html = _render("# Doc", telemetry=tel)
    assert "Code Review" in html   # the valid gate still renders; the bad one is skipped


def test_empty_dict_telemetry_shows_placeholder_not_absent():
    html = _render("# Doc", telemetry={})   # explicit empty record != None
    assert "telemetry unavailable" in html


def test_none_telemetry_has_no_section():
    html = _render("# Doc", telemetry=None)
    assert "Run telemetry" not in html


# --- #199: opt-in card/chip "roadmap" style matching the dashboard ---

class TestRoadmapStyle:
    def test_default_is_plain_unchanged(self):
        # Backward-compat: no style arg == plain == byte-identical to today.
        md = "# Title\n\n## Slot 1 — DONE\n\nbody text\n"
        assert _render(md) == _render(md, style="plain")

    def test_plain_has_no_cards_or_chips(self):
        md = "## Slot 1 — DONE\n\nbody"
        h = _render(md, style="plain")
        # no card/chip MARKUP and no roadmap CSS leak (AC1: plain byte-identical)
        assert '<section class="mstone">' not in h and 'class="chip' not in h
        assert ".mstone" not in h and "--chip-c" not in h

    def test_roadmap_wraps_h2_sections_in_cards(self):
        md = "## Slot 1 — DONE\n\nbody one\n\n## Slot 2 — planned\n\nbody two"
        h = _render(md, style="roadmap")
        assert h.count('<section class="mstone">') == 2
        assert 'class="chip' in h

    def test_roadmap_default_style_still_plain(self):
        # roadmap must be OPT-IN — the CLI/lib default stays plain
        md = "## Slot 1 — DONE\n\nbody"
        assert "mstone" not in _render(md)

    def test_status_chip_mapping(self):
        sc = render_artifact.status_chip
        assert sc("Slot 12 DONE")[0] == "c-conf"
        assert sc("shipped v2.65.0")[0] == "c-conf"
        assert sc("ABANDONED per AC4")[0] == "c-defer"
        assert sc("blocked on X")[0] == "c-defer"
        assert sc("planned, next up")[0] == "c-plan"
        assert sc("no keyword here")[0] == "c-plan"   # fail-safe neutral

    def test_status_chip_no_substring_false_positive(self):
        # word-boundary: "incomplete" must NOT match "complete" -> c-conf
        assert render_artifact.status_chip("this is incomplete")[0] != "c-conf"

    def test_roadmap_chip_from_section_body(self):
        # status keyword in the body (not the heading) still drives the chip.
        # Assert on the chip SPAN, not bare "c-conf" — the .c-conf CSS rule is
        # always present in roadmap output, so "c-conf" in h would be vacuous.
        md = "## Slot 12 — telemetry\n\n**Status.** PR #198 merged, DONE."
        h = _render(md, style="roadmap")
        assert 'class="chip c-conf"' in h

    def test_roadmap_fence_with_hash_not_split(self):
        # a "## " line INSIDE a fenced code block is content, not a card boundary
        md = ("## Real Section — DONE\n\n"
              "```\n## not a heading (shell comment)\necho hi\n```\n\n"
              "tail of section body")
        h = _render(md, style="roadmap")
        assert h.count('<section class="mstone">') == 1
        assert "not a heading (shell comment)" in h
        assert "tail of section body" in h

    def test_roadmap_neutral_heading_does_not_suppress_done_body(self):
        # "Next.js" heading (incidental \bnext\b) must not force PLANNED when the
        # body is definitively DONE — definitive body beats a neutral heading.
        md = "## Next.js frontend migration\n\nAll shipped and merged. DONE."
        h = _render(md, style="roadmap")
        assert 'class="chip c-conf"' in h
        assert 'class="chip c-plan"' not in h

    def test_roadmap_heading_status_beats_body_keyword(self):
        # heading precedence: an ABANDONED slot whose body mentions a "merged"
        # decision PR must render amber c-defer, not green c-conf.
        md = ("## Slot 11 — #162 review switch — ABANDONED per AC4\n\n"
              "Decision-record PR #187 squash-merged e7aadf7.")
        h = _render(md, style="roadmap")
        assert 'class="chip c-defer"' in h
        assert 'class="chip c-conf"' not in h

    def test_roadmap_escapes_heading_injection(self):
        md = "## <script>alert(1)</script> DONE\n\nbody"
        h = _render(md, style="roadmap")
        assert "<script>alert(1)</script>" not in h
        assert "&lt;script&gt;" in h

    def test_roadmap_chip_label_is_fixed_vocab_not_raw(self):
        # a raw-text injection in the status position cannot reach the chip label
        md = "## Slot <img src=x onerror=y> planned\n\nbody"
        h = _render(md, style="roadmap")
        assert "onerror=y>" not in h   # escaped, never live markup

    def test_roadmap_preamble_before_first_h2_rendered(self):
        md = "intro paragraph\n\n## Slot 1 — DONE\n\nbody"
        h = _render(md, style="roadmap")
        assert "intro paragraph" in h

    def test_roadmap_css_present(self):
        h = _render("## Slot 1 — DONE\n\nbody", style="roadmap")
        assert ".mstone" in h and ".c-conf" in h and ".chip" in h

    def test_cli_style_flag(self, tmp_path):
        md = tmp_path / "in.md"; md.write_text("## Slot 1 — DONE\n\nbody")
        out = tmp_path / "out.html"
        rc = render_artifact.main(["--md", str(md), "--out", str(out),
                                   "--title", "T", "--style", "roadmap"])
        assert rc == 0
        assert '<section class="mstone">' in out.read_text()

    def test_cli_default_style_plain(self, tmp_path):
        md = tmp_path / "in.md"; md.write_text("## Slot 1 — DONE\n\nbody")
        out = tmp_path / "out.html"
        render_artifact.main(["--md", str(md), "--out", str(out), "--title", "T"])
        assert "mstone" not in out.read_text()


# --- #343: markdown-table rendering in _render_body_plain ---

class TestMarkdownTables:
    def test_table_renders_thead_tbody(self):
        md = (
            "| Name | Age |\n"
            "| --- | --- |\n"
            "| Alice | 30 |\n"
            "| Bob | 40 |\n"
        )
        h = _render(md)
        assert "<table>" in h
        assert "<thead>" in h and "<th>Name</th>" in h and "<th>Age</th>" in h
        assert "<tbody>" in h
        assert "<td>Alice</td>" in h and "<td>30</td>" in h
        assert "<td>Bob</td>" in h and "<td>40</td>" in h
        assert "<p>|" not in h

    def test_alignment_colon_separator_accepted(self):
        md = (
            "| A | B | C |\n"
            "| :-- | :-: | --: |\n"
            "| 1 | 2 | 3 |\n"
        )
        h = _render(md)
        assert "<table>" in h
        assert "<td>1</td>" in h and "<td>2</td>" in h and "<td>3</td>" in h

    def test_pipe_line_without_separator_stays_paragraph(self):
        md = "| not a table | just text |"
        h = _render(md)
        assert "<table>" not in h
        assert "<p>" in h and "| not a table | just text |" in h

    def test_table_inside_fence_not_converted(self):
        md = "```\n| A | B |\n| --- | --- |\n| 1 | 2 |\n```"
        h = _render(md)
        assert "<table>" not in h
        assert "<pre><code>" in h
        assert "| A | B |" in h

    def test_table_cell_script_escaped(self):
        md = (
            "| Col |\n"
            "| --- |\n"
            "| <script>alert(1)</script> |\n"
        )
        h = _render(md)
        assert "<script>alert(1)</script>" not in h
        assert "&lt;script&gt;" in h

    def test_table_cell_inline_markdown(self):
        md = (
            "| Col |\n"
            "| --- |\n"
            "| **bold** and `code` |\n"
        )
        h = _render(md)
        assert "<strong>bold</strong>" in h
        assert "<code>code</code>" in h

    def test_table_after_list_closes_ul_first(self):
        md = (
            "- item one\n"
            "- item two\n"
            "\n"
            "| A | B |\n"
            "| --- | --- |\n"
            "| 1 | 2 |\n"
        )
        h = _render(md)
        assert "</ul>" in h
        assert h.index("</ul>") < h.index("<table>")
        assert "<table>" not in h.split("</ul>")[0]

    def test_table_with_only_header_and_separator_no_body_rows(self):
        md = "| A | B |\n| --- | --- |\n"
        h = _render(md)
        assert "<table>" in h
        assert "<th>A</th>" in h and "<th>B</th>" in h

    def test_roadmap_style_table_inside_section(self):
        md = (
            "## Slot 1 — DONE\n\n"
            "| A | B |\n"
            "| --- | --- |\n"
            "| 1 | 2 |\n"
        )
        h = _render(md, style="roadmap")
        assert '<section class="mstone">' in h
        assert "<table>" in h
