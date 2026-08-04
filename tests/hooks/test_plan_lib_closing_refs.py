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


# --- Step 8a fix round: cross-model findings 1-4 + inline findings A/B, and
#     the tests finding 5 asked for (its claim was refuted, but the pins make
#     `_contained`'s contract verifiable rather than assumed). ---

def test_qualified_ref_records_its_repo():
    (hit,) = plan_lib.find_closing_refs("closes owner/other-repo#901")
    assert hit.repo == "owner/other-repo"
    assert hit.issue == 901


def test_unqualified_ref_has_no_repo():
    (hit,) = plan_lib.find_closing_refs("closes #901")
    assert hit.repo is None


def test_url_ref_records_its_repo():
    (hit,) = plan_lib.find_closing_refs(
        "closes https://github.com/3D-Stories/rawgentic/issues/901")
    assert hit.repo == "3d-stories/rawgentic"


def test_local_declaration_does_not_authorize_a_cross_repo_ref():
    """Cross-model finding 2: declaring local #901 must NOT authorize
    other/repo#901 — same integer, different issue."""
    ok, errors = plan_lib.assert_pr_body_closing_refs(
        "## Summary\nA fix.\n\nCloses #901\n\nAlso closes other/repo#901\n",
        frozenset({901}))
    assert ok is False
    assert len(errors) == 1
    assert "other/repo#901" in errors[0]
    assert "repo-qualified" in errors[0]


def test_non_string_body_fails_closed():
    """Cross-model finding 3: the public function must not pass input it
    cannot evaluate."""
    for bad in (None, 123, b"Closes #1", ["Closes #1"]):
        ok, errors = plan_lib.assert_pr_body_closing_refs(bad, frozenset())
        assert ok is False, f"{bad!r} must not pass"
        assert any("caller error" in e for e in errors)


def test_blank_body_fails_closed_in_the_pure_function():
    for blank in ("", "   ", "\n\t\n"):
        ok, errors = plan_lib.assert_pr_body_closing_refs(blank, frozenset())
        assert ok is False
        assert errors


def test_scanner_stays_permissive_on_empty_input():
    """The gate fail-closes; the SCANNER one level down stays permissive."""
    assert plan_lib.find_closing_refs("") == ()
    assert plan_lib.find_closing_refs(None) == ()


def test_non_integer_intended_closes_is_a_caller_error():
    """Inline finding A: a bad declaration must be reported, not raise."""
    ok, errors = plan_lib.assert_pr_body_closing_refs(
        "Closes #901\n", frozenset({"901"}))
    assert ok is False
    assert any("not an integer" in e for e in errors)


def test_booleans_are_rejected_as_intended_closes():
    ok, errors = plan_lib.assert_pr_body_closing_refs(
        "Closes #1\n", frozenset({True}))
    assert ok is False
    assert any("not an integer" in e for e in errors)


def test_cli_rc2_on_invalid_utf8_body(tmp_path):
    """Cross-model finding 4: UnicodeDecodeError is a ValueError, not an
    OSError, so it escaped as a traceback whose rc 1 was indistinguishable
    from a real gate finding."""
    p = tmp_path / "bad-bytes.md"
    p.write_bytes(b"Closes #1\n\xff\xfe invalid utf-8 \x80\n")
    r = _run(["check-pr-refs", "--pr-body-file", str(p),
              "--project-root", str(tmp_path)])
    assert r.returncode == 2, f"rc={r.returncode} stdout={r.stdout}"
    assert "Traceback" not in r.stderr
    assert "cannot read" in r.stderr


# --- _contained contract (finding 5 asked for these; the claim itself was
#     refuted by reading the helper — realpath on BOTH sides plus a
#     component-aware `root + os.sep` compare). ---

def test_contained_rejects_a_symlink_escaping_the_root(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("Closes #1", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "sneaky.md").symlink_to(outside)
    assert plan_lib._contained(str(root / "sneaky.md"), str(root)) is False


def test_contained_rejects_dotdot_traversal(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside.md").write_text("x", encoding="utf-8")
    assert plan_lib._contained(str(root / ".." / "outside.md"), str(root)) is False


def test_contained_rejects_a_sibling_with_the_root_as_a_string_prefix(tmp_path):
    """`/x/root-evil` must not count as inside `/x/root` — the compare is
    component-aware, not a bare startswith."""
    root = tmp_path / "root"
    root.mkdir()
    evil = tmp_path / "root-evil"
    evil.mkdir()
    (evil / "x.md").write_text("x", encoding="utf-8")
    assert plan_lib._contained(str(evil / "x.md"), str(root)) is False


def test_contained_accepts_a_plain_inside_path(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "ok.md"
    inside.write_text("ok", encoding="utf-8")
    assert plan_lib._contained(str(inside), str(root)) is True


def test_cli_rc2_when_a_symlink_inside_root_escapes(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("Closes #1\n", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    link = root / "sneaky.md"
    link.symlink_to(outside)
    r = _run(["check-pr-refs", "--pr-body-file", str(link),
              "--project-root", str(root)])
    assert r.returncode == 2
    assert "REFUSED" in r.stderr


# --- --commit-range: GitHub parses closing keywords in commit messages too
#     (cross-model finding 1: a body-only check was a real fail-open). ---

def _git_repo(tmp_path, messages):
    """A throwaway repo with one commit per message, returning (root, range)."""
    root = tmp_path / "repo"
    root.mkdir()
    def git(*a):
        return subprocess.run(["git", *a], cwd=str(root), capture_output=True,
                              text=True, timeout=30, check=True)
    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "T")
    git("commit", "-q", "--allow-empty", "-m", "base")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root),
                          capture_output=True, text=True, check=True).stdout.strip()
    for i, m in enumerate(messages):
        (root / f"f{i}.txt").write_text(str(i), encoding="utf-8")
        git("add", f"f{i}.txt")
        git("commit", "-q", "-m", m)
    return root, f"{base}..HEAD"


def test_commit_range_flags_a_negated_close_in_a_commit_message(tmp_path):
    """Body is clean; a COMMIT says it does not close #N. Must be rc 1."""
    root, rng = _git_repo(tmp_path, ["fix: a thing\n\nthis PR does not close #874\n"])
    body = _body(root, "## Summary\nA change.\n\nPart of #875\n")
    r = _run(["check-pr-refs", "--pr-body-file", body,
              "--commit-range", rng, "--project-root", str(root)])
    assert r.returncode == 1, f"rc={r.returncode} stdout={r.stdout} stderr={r.stderr}"
    assert "874" in r.stdout
    assert "commit" in r.stdout


def test_commit_range_passes_when_every_message_is_clean(tmp_path):
    root, rng = _git_repo(tmp_path, ["fix: a thing\n\nPart of #875\n",
                                     "docs: notes\n\nRefs #875\n"])
    body = _body(root, "## Summary\nA change.\n\nPart of #875\n")
    r = _run(["check-pr-refs", "--pr-body-file", body,
              "--commit-range", rng, "--project-root", str(root)])
    assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"


def test_commit_range_honours_the_closes_declaration(tmp_path):
    root, rng = _git_repo(tmp_path, ["fix: a thing (closes #901)\n"])
    body = _body(root, "## Summary\nA fix.\n\nCloses #901\n")
    r = _run(["check-pr-refs", "--pr-body-file", body, "--closes", "901",
              "--commit-range", rng, "--project-root", str(root)])
    assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"


def test_commit_range_rejects_a_leading_dash(tmp_path):
    """A range starting with '-' would be read by git as an option.

    The `--opt=value` form is required to reach the in-code guard: given
    `--commit-range --all`, argparse itself refuses first ("expected one
    argument") and also exits 2, so that spelling proves nothing about this
    guard. Both layers refuse; this pins the inner one.
    """
    body = _body(tmp_path, "Part of #875\n")
    r = _run(["check-pr-refs", "--pr-body-file", body,
              "--commit-range=--all", "--project-root", str(tmp_path)])
    assert r.returncode == 2
    assert "REFUSED" in r.stderr
    assert "git option" in r.stderr


def test_commit_range_rc2_on_an_unresolvable_range(tmp_path):
    root, _ = _git_repo(tmp_path, ["fix: a thing\n"])
    body = _body(root, "Part of #875\n")
    r = _run(["check-pr-refs", "--pr-body-file", body,
              "--commit-range", "deadbeef1234..HEAD", "--project-root", str(root)])
    assert r.returncode == 2, f"rc={r.returncode} stdout={r.stdout}"
    assert "REFUSED" in r.stderr


# --- Step 11 fix round: cross-model findings 1, 3, 5 ---

def test_commit_message_containing_the_old_record_delimiter_is_still_scanned():
    """Cross-model finding 3: the first implementation split `git log` output on
    \\x1e and skipped any segment without \\x1f, so a commit message carrying
    that delimiter could hide a closing reference. The two-stage reader has no
    delimiter to poison — pinned at the scanner level, which is what a crafted
    message would exploit."""
    msg = "fix: a thing\n\x1e\x1f\nthis PR does not close #874\n"
    assert [h.issue for h in plan_lib.find_closing_refs(msg)] == [874]


def test_control_characters_do_not_hide_a_reference_from_the_commit_scan(tmp_path):
    root, rng = _git_repo(tmp_path, ["fix: a thing\n\x1erecord\x1fsplit\n"
                                     "this PR does not close #874\n"])
    body = _body(root, "## Summary\nClean.\n\nPart of #875\n")
    r = _run(["check-pr-refs", "--pr-body-file", body,
              "--commit-range", rng, "--project-root", str(root)])
    assert r.returncode == 1, f"rc={r.returncode} stdout={r.stdout} stderr={r.stderr}"
    assert "874" in r.stdout


def test_commit_range_rc2_on_an_undecodable_commit_message(tmp_path):
    """Cross-model finding 5: `text=True` decodes `git log` output strictly, so a
    non-UTF-8 commit message raised UnicodeDecodeError — which is a ValueError,
    not an OSError — and escaped as a traceback at rc 1, the code reserved for
    real findings.

    `i18n.commitEncoding` is load-bearing to the repro and was measured, not
    guessed: with the default config git TRANSCODES a non-UTF-8 message to UTF-8
    and warns ("commit message did not conform to UTF-8"), so the naive attempt
    decodes cleanly and proves nothing. Setting a non-UTF-8 commit encoding makes
    git store and emit the raw bytes, which is the configuration that reaches the
    handler.
    """
    root = tmp_path / "repo"
    root.mkdir()

    def git(*a, **kw):
        return subprocess.run(["git", *a], cwd=str(root), capture_output=True,
                              timeout=30, check=True, **kw)
    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "T")
    git("config", "i18n.commitEncoding", "ISO-8859-1")
    git("commit", "-q", "--allow-empty", "-m", "base")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root),
                          capture_output=True, text=True, check=True).stdout.strip()
    (root / "msg.bin").write_bytes(b"fix: caf\xe9 latin1 \xff\xfe\n")
    (root / "f.txt").write_text("x", encoding="utf-8")
    git("add", "f.txt")
    git("commit", "-q", "-F", "msg.bin")
    body = _body(root, "## Summary\nClean.\n\nPart of #875\n")
    r = _run(["check-pr-refs", "--pr-body-file", body,
              "--commit-range", f"{base}..HEAD", "--project-root", str(root)])
    assert r.returncode == 2, f"rc={r.returncode} stdout={r.stdout} stderr={r.stderr}"
    assert "Traceback" not in r.stderr


def test_a_project_contained_body_path_is_accepted(tmp_path):
    """Cross-model finding 1: the workflows now prescribe a body file INSIDE the
    project root, because the containment check refuses anything outside it. This
    executes that documented shape."""
    root = tmp_path / "repo"
    (root / ".rawgentic").mkdir(parents=True)
    drafted = root / ".rawgentic" / "wf2-pr-body.md"
    drafted.write_text("## Summary\nA fix.\n\nCloses #901\n", encoding="utf-8")
    r = _run(["check-pr-refs", "--pr-body-file", "./.rawgentic/wf2-pr-body.md",
              "--closes", "901", "--project-root", "."], cwd=str(root))
    assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr}"


def test_a_tmp_body_path_is_refused_which_is_why_the_prose_moved(tmp_path):
    """The defect finding 1 named: a /tmp draft can never satisfy the gate."""
    outside = tmp_path / "wf2-pr-body.md"
    outside.write_text("Closes #901\n", encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()
    r = _run(["check-pr-refs", "--pr-body-file", str(outside),
              "--closes", "901", "--project-root", str(root)])
    assert r.returncode == 2
    assert "REFUSED" in r.stderr
