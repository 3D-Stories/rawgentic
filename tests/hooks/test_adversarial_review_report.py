"""Tests for adversarial_review_lib report rendering + path + egress (issue #77, Task 3)."""
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import adversarial_review_lib as arl  # noqa: E402


# --- slugify ---

@pytest.mark.parametrize("name,expected", [
    ("design.md", "design-md"),
    ("../../etc/passwd", "passwd"),
    ("My Spec (v2).md", "my-spec-v2-md"),
    ("a/b/c.txt", "c-txt"),
    ("....", "artifact"),
])
def test_slugify(name, expected):
    assert arl.slugify(name) == expected


def test_slugify_truncates():
    assert len(arl.slugify("x" * 200)) <= 50


# --- review_report_path ---

def test_review_report_path_under_docs_reviews(tmp_path):
    p = arl.review_report_path(str(tmp_path), "design.md", "2026-06-14")
    assert p == str(tmp_path / "docs" / "reviews" / "design-md-2026-06-14.md")


def test_review_report_path_sanitizes_traversal(tmp_path):
    p = arl.review_report_path(str(tmp_path), "../../evil.md", "2026-06-14")
    assert "/docs/reviews/" in p
    assert ".." not in Path(p).name


def test_review_report_path_sanitizes_malicious_date(tmp_path):
    # F1 regression: a traversal payload in date_str must not escape docs/reviews/
    p = arl.review_report_path(str(tmp_path), "design.md", "2026/../../../etc/passwd")
    reviews = str(tmp_path / "docs" / "reviews") + "/"
    assert p.startswith(reviews)
    assert ".." not in p[len(reviews):]
    assert "/" not in Path(p).name


def test_review_report_path_empty_date(tmp_path):
    p = arl.review_report_path(str(tmp_path), "design.md", "")
    assert "/docs/reviews/" in p
    assert Path(p).name.endswith(".md")


# --- egress_warning ---

def test_egress_warning_base_mentions_openai():
    w = arl.egress_warning()
    assert "OpenAI" in w or "Codex" in w


def test_egress_warning_names_secrets():
    w = arl.egress_warning(["API key", "password"])
    assert "API key" in w and "password" in w


# --- render_report_md ---

def _f(sev, cat="security", desc="d", rec="r", loc="S1", **kw):
    base = {"severity": sev, "category": cat, "description": desc,
            "recommendation": rec, "location": loc}
    base.update(kw)
    return base


def test_render_includes_counts_and_findings():
    findings = [_f("Critical"), _f("Low")]
    md = arl.render_report_md(findings, {"artifact": "design.md", "date": "2026-06-14"})
    assert "# Adversarial Review — design.md" in md
    assert "Critical 1" in md and "Low 1" in md
    assert "[Critical]" in md


def test_render_empty_findings():
    md = arl.render_report_md([], {"artifact": "x.md", "date": "2026-06-14"})
    assert "No findings" in md


def test_render_marks_truncation_and_secrets():
    md = arl.render_report_md([], {"artifact": "x.md", "date": "d",
                                   "truncated": True, "secrets": ["API key"]})
    assert "truncated" in md.lower()
    assert "API key" in md


def test_render_includes_ambiguity():
    f = _f("High", ambiguity_flag=True, ambiguity_reason="unclear scope")
    md = arl.render_report_md([f], {"artifact": "x.md", "date": "d"})
    assert "unclear scope" in md


def test_render_is_report_only_note():
    md = arl.render_report_md([], {"artifact": "x.md", "date": "d"})
    assert "does not edit" in md.lower()
