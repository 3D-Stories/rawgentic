"""Tests for the closing-keyword guard — `plan_lib check-pr-refs` (#901).

GitHub's closing-keyword parser does not understand negation: it matches
``close #N`` inside "this PR does not close #N" and closes the issue on merge.
This has fired TWICE in this repo:

- 2026-07-21: issue #568 auto-closed on the #573 merge from the body text
  "this PR does not close #568".
- 2026-08-04: issue #874 auto-closed on the #898 merge from the body text
  "AC1 is NOT delivered, and this PR does not close #874".

Both sentences are pinned verbatim below — the negated form MUST flag, because
that is the observed failure (#901 AC3).

CLI is exercised black-box via subprocess (the house pattern).
"""
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import plan_lib  # noqa: E402

CLI = str(HOOKS_DIR / "plan_lib.py")

# The two live incidents, verbatim.
INCIDENT_874 = "AC1 is NOT delivered, and this PR does not close #874"
INCIDENT_568 = "this PR does not close #568"


def _run(args, cwd=None):
    return subprocess.run(
        [sys.executable, CLI, *args],
        capture_output=True, text=True, cwd=cwd, timeout=30,
    )


def _body(tmp_path, text, name="pr-body.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# --- find_closing_refs: the pure scanner ----------------------------------

def test_negated_close_is_still_a_hit_874():
    """The observed failure. Negation is invisible to GitHub, so it must be
    invisible to this scanner too."""
    hits = plan_lib.find_closing_refs(INCIDENT_874)
    assert len(hits) == 1
    assert hits[0].issue == 874
    assert hits[0].keyword == "close"


def test_negated_close_is_still_a_hit_568():
    hits = plan_lib.find_closing_refs(INCIDENT_568)
    assert [h.issue for h in hits] == [568]


def test_all_nine_github_keywords_match():
    for kw in ("close", "closes", "closed",
               "fix", "fixes", "fixed",
               "resolve", "resolves", "resolved"):
        hits = plan_lib.find_closing_refs(f"this {kw} #12")
        assert [h.issue for h in hits] == [12], f"{kw} did not match"


def test_keyword_matching_is_case_insensitive():
    assert [h.issue for h in plan_lib.find_closing_refs("CLOSES #5")] == [5]
    assert [h.issue for h in plan_lib.find_closing_refs("Fixes #6")] == [6]


def test_colon_and_extra_whitespace_separators_match():
    for text in ("closes: #7", "closes:#7", "closes   #7", "closes : #7"):
        assert [h.issue for h in plan_lib.find_closing_refs(text)] == [7], text


def test_single_line_break_between_keyword_and_ref_matches():
    assert [h.issue for h in plan_lib.find_closing_refs("closes\n#8")] == [8]


def test_keyword_embedded_in_a_longer_word_does_not_match():
    """`foreclose #1` is not a closing keyword — word boundary required."""
    assert plan_lib.find_closing_refs("foreclose #1") == ()
    assert plan_lib.find_closing_refs("unfixed #2") == ()


def test_part_of_and_refs_are_not_closing_keywords():
    assert plan_lib.find_closing_refs("Part of #874") == ()
    assert plan_lib.find_closing_refs("Refs #874") == ()
    assert plan_lib.find_closing_refs("See #874") == ()


def test_cross_repo_and_url_reference_forms_match():
    assert [h.issue for h in
            plan_lib.find_closing_refs("closes owner/repo#31")] == [31]
    assert [h.issue for h in plan_lib.find_closing_refs(
        "closes https://github.com/3D-Stories/rawgentic/issues/32")] == [32]


def test_gh_dash_reference_form_matches():
    assert [h.issue for h in plan_lib.find_closing_refs("closes GH-33")] == [33]


def test_hit_records_its_line_number_and_matched_text():
    body = "line one\nline two\nthis PR does not close #874\n"
    (hit,) = plan_lib.find_closing_refs(body)
    assert hit.line == 3
    assert "close #874" in hit.text


def test_multiple_hits_all_reported_in_order():
    body = "closes #1\nfixes #2\nresolves #3\n"
    assert [h.issue for h in plan_lib.find_closing_refs(body)] == [1, 2, 3]


def test_keyword_inside_backticks_is_still_flagged_by_design():
    """GitHub IGNORES a closing keyword inside a code span (verified live
    2026-07-28: a `Closes #4` span was inert and #4 stayed open). We flag it
    anyway — fail toward asking (#901 AC2), and a keyword in backticks is a
    trap in the other direction too (an INTENDED closure silently does not
    fire). Pinned so nobody 'optimizes' the false positive away unknowingly."""
    assert [h.issue for h in plan_lib.find_closing_refs("`Closes #4`")] == [4]


# --- assert_pr_body_closing_refs: the gate --------------------------------

def test_part_of_pr_with_no_closing_ref_passes():
    ok, errors = plan_lib.assert_pr_body_closing_refs(
        "## Summary\nA change.\n\nPart of #875\n", frozenset())
    assert ok is True
    assert errors == []


def test_part_of_pr_with_a_negated_closing_ref_fails():
    ok, errors = plan_lib.assert_pr_body_closing_refs(
        f"## Summary\n{INCIDENT_874}\n\nPart of #875\n", frozenset())
    assert ok is False
    assert len(errors) == 1
    assert "874" in errors[0]


def test_intended_closure_passes():
    ok, errors = plan_lib.assert_pr_body_closing_refs(
        "## Summary\nA fix.\n\nCloses #901\n", frozenset({901}))
    assert ok is True
    assert errors == []


def test_closing_ref_to_an_unintended_issue_fails_even_when_another_is_intended():
    body = "## Summary\nA fix.\n\nCloses #901\n\nthis PR does not close #874\n"
    ok, errors = plan_lib.assert_pr_body_closing_refs(body, frozenset({901}))
    assert ok is False
    assert len(errors) == 1
    assert "874" in errors[0]
    assert "901" not in errors[0]


# --- the CLI -------------------------------------------------------------

def test_cli_rc0_when_gate_holds(tmp_path):
    body = _body(tmp_path, "## Summary\nA change.\n\nPart of #875\n")
    r = _run(["check-pr-refs", "--pr-body-file", body,
              "--project-root", str(tmp_path)])
    assert r.returncode == 0, r.stderr


def test_cli_rc1_on_the_observed_failure(tmp_path):
    body = _body(tmp_path, f"## Summary\n{INCIDENT_874}\n\nPart of #875\n")
    r = _run(["check-pr-refs", "--pr-body-file", body,
              "--project-root", str(tmp_path)])
    assert r.returncode == 1, r.stderr
    assert "874" in r.stdout


def test_cli_rc0_when_the_closure_is_intended(tmp_path):
    body = _body(tmp_path, "## Summary\nA fix.\n\nCloses #901\n")
    r = _run(["check-pr-refs", "--pr-body-file", body,
              "--closes", "901", "--project-root", str(tmp_path)])
    assert r.returncode == 0, r.stderr


def test_cli_rc2_on_empty_body(tmp_path):
    """Step-4 finding #1: an empty body is a wrong path or a failed draft
    write, not a clean gate — the #796 vacuous-pass precedent."""
    body = _body(tmp_path, "")
    r = _run(["check-pr-refs", "--pr-body-file", body,
              "--project-root", str(tmp_path)])
    assert r.returncode == 2, r.stdout
    assert "REFUSED" in r.stderr


def test_cli_rc2_on_whitespace_only_body(tmp_path):
    body = _body(tmp_path, "   \n\t\n")
    r = _run(["check-pr-refs", "--pr-body-file", body,
              "--project-root", str(tmp_path)])
    assert r.returncode == 2, r.stdout


def test_cli_rc2_on_missing_body_file(tmp_path):
    r = _run(["check-pr-refs",
              "--pr-body-file", str(tmp_path / "nope.md"),
              "--project-root", str(tmp_path)])
    assert r.returncode == 2


def test_cli_rc2_when_body_file_escapes_project_root(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("Closes #1\n", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    r = _run(["check-pr-refs", "--pr-body-file", str(outside),
              "--project-root", str(root)])
    assert r.returncode == 2
    assert "REFUSED" in r.stderr


def test_cli_rc2_on_non_numeric_closes(tmp_path):
    body = _body(tmp_path, "Closes #901\n")
    r = _run(["check-pr-refs", "--pr-body-file", body,
              "--closes", "nine-oh-one", "--project-root", str(tmp_path)])
    assert r.returncode == 2
