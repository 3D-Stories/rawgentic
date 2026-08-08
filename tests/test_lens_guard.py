"""The Step-11 lens-coverage guard (#1002).

The failure it exists to stop is measured, not hypothetical: a draft dispatched the lane reviewer
"on the security lens" only, silently dropping `mechanical`, `bug_logic` and `architecture`. The
run looked reviewed and was not.

Two design decisions this file pins, both taken at the 2026-08-08 design gate:

- **Coverage is over the UNION of the wave's briefs, never per-reviewer** (owner decision D305).
  That is the only reading compatible with the shipped `<review-lens-routing>` split, which gives
  Reviewer 1 mechanical+bug_logic and Reviewer 2 architecture+security. A per-reviewer reading
  would make the shipped split fail its own guard.
- **One input, the wave's dispatch manifest** (D306). Earlier drafts implied three inputs and
  specified none. A guard fed loose brief files cannot tell a two-reviewer wave from a
  one-reviewer wave that lost a brief — which is why binding comes before coverage.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks"))

import plan_lib  # noqa: E402

LENSES = ("mechanical", "bug_logic", "security", "architecture")


def _prompt(tmp: Path, name: str, lenses, *, body="Look hard at this.", closed=True) -> Path:
    p = tmp / name
    parts = []
    for lens in lenses:
        parts.append(f"<!-- lens:{lens} -->")
        parts.append(body)
        if closed:
            parts.append(f"<!-- /lens:{lens} -->")
    p.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return p


def _wave(tmp: Path, *, issue=1002, task_class="production", reviewers=None) -> dict:
    """A valid production wave: two reviewers, the shipped #492 split."""
    if reviewers is None:
        r1 = _prompt(tmp, "r1.md", ("mechanical", "bug_logic"))
        r2 = _prompt(tmp, "r2.md", ("architecture", "security"))
        reviewers = [
            {"id": "r1", "lenses": ["mechanical", "bug_logic"], "prompt_file": r1.name},
            {"id": "r2", "lenses": ["architecture", "security"], "prompt_file": r2.name},
        ]
    return {"issue": issue, "step": "11", "task_class": task_class, "reviewers": reviewers}


def _snapshot(tmp: Path, issue=1002, task_class="production") -> None:
    d = tmp / "claude_docs" / ".wf2-state" / str(issue)
    d.mkdir(parents=True, exist_ok=True)
    (d / "task_class.json").write_text(
        json.dumps({"task_class": task_class, "provenance": "canonical"}), encoding="utf-8")


def _check(tmp: Path, manifest: dict, *, issue=1002):
    return plan_lib.assert_lens_coverage(manifest, project_root=tmp, issue=issue)


# ---------------------------------------------------------------------------
# The two positive cases — the whole point of the union reading


def test_the_production_two_reviewer_split_passes(tmp_path) -> None:
    """The shipped #492 split: 2 lenses each, 4 across the union. A per-reviewer reading would
    fail this, which is the evidence that decided D305."""
    _snapshot(tmp_path)
    ok, problems = _check(tmp_path, _wave(tmp_path))
    assert ok, problems


def test_a_single_reviewer_wave_passes_only_with_all_four(tmp_path) -> None:
    """For the one-reviewer classes the union IS that one brief, so union and per-reviewer
    coincide there — the single reviewer must carry all four."""
    _snapshot(tmp_path, task_class="disposable")
    p = _prompt(tmp_path, "solo.md", LENSES)
    good = _wave(tmp_path, task_class="disposable", reviewers=[
        {"id": "solo", "lenses": list(LENSES), "prompt_file": p.name}])
    ok, problems = _check(tmp_path, good)
    assert ok, problems

    short = _prompt(tmp_path, "short.md", ("security",))
    bad = _wave(tmp_path, task_class="disposable", reviewers=[
        {"id": "solo", "lenses": ["security"], "prompt_file": short.name}])
    ok, problems = _check(tmp_path, bad)
    assert not ok
    assert any("mechanical" in p for p in problems), problems


# ---------------------------------------------------------------------------
# Binding — the manifest must be THIS wave's manifest (design finding R2-F1/F5)


def test_a_manifest_for_a_different_issue_is_refused(tmp_path) -> None:
    _snapshot(tmp_path)
    with pytest.raises(plan_lib.PlanFormatError, match="issue"):
        _check(tmp_path, _wave(tmp_path, issue=999))


def test_a_class_that_disagrees_with_the_snapshot_is_refused(tmp_path) -> None:
    """The write-once snapshot is the single source of the class. A manifest may not restate it
    differently — that is how a production wave would be certified against lane demands."""
    _snapshot(tmp_path, task_class="production")
    with pytest.raises(plan_lib.PlanFormatError, match="task_class"):
        _check(tmp_path, _wave(tmp_path, task_class="disposable"))


def test_a_missing_snapshot_is_refused_not_defaulted(tmp_path) -> None:
    """No snapshot means the class is unknown. Guessing `production` would be a vacuous pass on
    the strictest class, and guessing `disposable` would be worse."""
    with pytest.raises(plan_lib.PlanFormatError, match="snapshot"):
        _check(tmp_path, _wave(tmp_path))


def test_a_short_reviewer_list_is_refused(tmp_path) -> None:
    """A wave that lost a reviewer between planning and dispatch must fail here rather than pass
    on a shrunken union."""
    _snapshot(tmp_path)
    solo = _prompt(tmp_path, "solo.md", LENSES)
    with pytest.raises(plan_lib.PlanFormatError, match="reviewer count"):
        _check(tmp_path, _wave(tmp_path, reviewers=[
            {"id": "r1", "lenses": list(LENSES), "prompt_file": solo.name}]))


def test_duplicate_reviewer_ids_are_refused(tmp_path) -> None:
    """R2-F2: array length does not prove reviewer count. Two duplicate entries would certify a
    one-reviewer wave as production."""
    _snapshot(tmp_path)
    p = _prompt(tmp_path, "same.md", LENSES)
    q = _prompt(tmp_path, "other.md", LENSES)
    with pytest.raises(plan_lib.PlanFormatError, match="unique"):
        _check(tmp_path, _wave(tmp_path, reviewers=[
            {"id": "r1", "lenses": list(LENSES), "prompt_file": p.name},
            {"id": "r1", "lenses": list(LENSES), "prompt_file": q.name}]))


def test_duplicate_prompt_files_are_refused(tmp_path) -> None:
    """The other half of R2-F2: two ids pointing at one brief is one reviewer wearing two hats."""
    _snapshot(tmp_path)
    p = _prompt(tmp_path, "same.md", LENSES)
    with pytest.raises(plan_lib.PlanFormatError, match="unique"):
        _check(tmp_path, _wave(tmp_path, reviewers=[
            {"id": "r1", "lenses": list(LENSES), "prompt_file": p.name},
            {"id": "r2", "lenses": list(LENSES), "prompt_file": p.name}]))


def test_a_prompt_path_escaping_the_project_root_is_refused(tmp_path) -> None:
    _snapshot(tmp_path)
    with pytest.raises(plan_lib.PlanFormatError, match="outside"):
        _check(tmp_path, _wave(tmp_path, reviewers=[
            {"id": "r1", "lenses": ["mechanical", "bug_logic"],
             "prompt_file": "../escape.md"},
            {"id": "r2", "lenses": ["architecture", "security"],
             "prompt_file": "r2.md"}]))


def test_a_missing_prompt_file_fails_rather_than_being_skipped(tmp_path) -> None:
    """An unreadable surface is exactly the state a silent drop produces, so it must never be
    treated as 'nothing to check here'."""
    _snapshot(tmp_path)
    _prompt(tmp_path, "r1.md", ("mechanical", "bug_logic"))
    with pytest.raises(plan_lib.PlanFormatError, match="does not exist"):
        _check(tmp_path, _wave(tmp_path, reviewers=[
            {"id": "r1", "lenses": ["mechanical", "bug_logic"], "prompt_file": "r1.md"},
            {"id": "r2", "lenses": ["architecture", "security"], "prompt_file": "gone.md"}]))


# ---------------------------------------------------------------------------
# Coverage — the two checks


def test_a_lens_missing_from_the_union_fails(tmp_path) -> None:
    _snapshot(tmp_path)
    r1 = _prompt(tmp_path, "r1.md", ("mechanical", "bug_logic"))
    r2 = _prompt(tmp_path, "r2.md", ("architecture",))
    ok, problems = _check(tmp_path, _wave(tmp_path, reviewers=[
        {"id": "r1", "lenses": ["mechanical", "bug_logic"], "prompt_file": r1.name},
        {"id": "r2", "lenses": ["architecture"], "prompt_file": r2.name}]))
    assert not ok
    assert any("security" in p for p in problems), problems


def test_a_lens_claimed_in_the_manifest_but_absent_from_the_prompt_fails(tmp_path) -> None:
    """The reason check 2 exists: a manifest can claim four lenses while the prompt carries one,
    and checking only the manifest would pass exactly that."""
    _snapshot(tmp_path)
    r1 = _prompt(tmp_path, "r1.md", ("mechanical", "bug_logic"))
    r2 = _prompt(tmp_path, "r2.md", ("architecture",))          # claims security, carries none
    ok, problems = _check(tmp_path, _wave(tmp_path, reviewers=[
        {"id": "r1", "lenses": ["mechanical", "bug_logic"], "prompt_file": r1.name},
        {"id": "r2", "lenses": ["architecture", "security"], "prompt_file": r2.name}]))
    assert not ok
    assert any("security" in p and "r2" in p for p in problems), problems


def test_an_empty_lens_section_is_not_coverage(tmp_path) -> None:
    """R2-F3: four immediately-closed markers would otherwise certify a prompt that carries no
    lens instructions at all."""
    _snapshot(tmp_path)
    r1 = _prompt(tmp_path, "r1.md", ("mechanical", "bug_logic"))
    r2 = _prompt(tmp_path, "r2.md", ("architecture", "security"), body="   ")
    ok, problems = _check(tmp_path, _wave(tmp_path, reviewers=[
        {"id": "r1", "lenses": ["mechanical", "bug_logic"], "prompt_file": r1.name},
        {"id": "r2", "lenses": ["architecture", "security"], "prompt_file": r2.name}]))
    assert not ok
    assert problems


def test_an_unclosed_section_is_not_coverage(tmp_path) -> None:
    _snapshot(tmp_path)
    r1 = _prompt(tmp_path, "r1.md", ("mechanical", "bug_logic"))
    r2 = _prompt(tmp_path, "r2.md", ("architecture", "security"), closed=False)
    ok, problems = _check(tmp_path, _wave(tmp_path, reviewers=[
        {"id": "r1", "lenses": ["mechanical", "bug_logic"], "prompt_file": r1.name},
        {"id": "r2", "lenses": ["architecture", "security"], "prompt_file": r2.name}]))
    assert not ok


def test_a_duplicated_lens_section_is_refused(tmp_path) -> None:
    """At most one section per lens per file. A duplicate is exit 2, not a pass — two sections
    disagreeing about the same lens has no defined meaning."""
    _snapshot(tmp_path)
    r1 = _prompt(tmp_path, "r1.md", ("mechanical", "bug_logic"))
    r2 = tmp_path / "r2.md"
    r2.write_text("<!-- lens:security -->\nA\n<!-- /lens:security -->\n"
                  "<!-- lens:security -->\nB\n<!-- /lens:security -->\n"
                  "<!-- lens:architecture -->\nC\n<!-- /lens:architecture -->\n", encoding="utf-8")
    with pytest.raises(plan_lib.PlanFormatError, match="more than once"):
        _check(tmp_path, _wave(tmp_path, reviewers=[
            {"id": "r1", "lenses": ["mechanical", "bug_logic"], "prompt_file": r1.name},
            {"id": "r2", "lenses": ["architecture", "security"], "prompt_file": r2.name}]))


def test_marker_matching_is_exact_and_case_sensitive(tmp_path) -> None:
    """No fuzzy heuristic, so the guard and its drift test cannot drift apart."""
    _snapshot(tmp_path)
    r1 = _prompt(tmp_path, "r1.md", ("mechanical", "bug_logic"))
    r2 = tmp_path / "r2.md"
    r2.write_text("<!-- LENS:security -->\nA\n<!-- /LENS:security -->\n"
                  "<!-- lens:architecture -->\nC\n<!-- /lens:architecture -->\n", encoding="utf-8")
    ok, problems = _check(tmp_path, _wave(tmp_path, reviewers=[
        {"id": "r1", "lenses": ["mechanical", "bug_logic"], "prompt_file": r1.name},
        {"id": "r2", "lenses": ["architecture", "security"], "prompt_file": r2.name}]))
    assert not ok


# ---------------------------------------------------------------------------
# The receipt — validated bytes are dispatched bytes (R2-F1)


def test_success_writes_a_digest_receipt(tmp_path) -> None:
    _snapshot(tmp_path)
    ok, _ = _check(tmp_path, _wave(tmp_path))
    assert ok
    receipt = tmp_path / "claude_docs" / ".wf2-state" / "1002" / "step11_lens_ok.json"
    assert receipt.is_file(), "no receipt written"
    data = json.loads(receipt.read_text())
    assert len(data["prompts"]) == 2
    for digest in data["prompts"].values():
        assert len(digest) == 64, digest


def test_no_receipt_is_written_when_coverage_fails(tmp_path) -> None:
    """A receipt is an authorization to dispatch. A failed guard must not leave one behind."""
    _snapshot(tmp_path)
    r1 = _prompt(tmp_path, "r1.md", ("mechanical", "bug_logic"))
    r2 = _prompt(tmp_path, "r2.md", ("architecture",))
    ok, _ = _check(tmp_path, _wave(tmp_path, reviewers=[
        {"id": "r1", "lenses": ["mechanical", "bug_logic"], "prompt_file": r1.name},
        {"id": "r2", "lenses": ["architecture"], "prompt_file": r2.name}]))
    assert not ok
    assert not (tmp_path / "claude_docs" / ".wf2-state" / "1002" / "step11_lens_ok.json").exists()


# ---------------------------------------------------------------------------
# The CLI — black-box via subprocess, per docs/testing.md


def _run_cli(tmp_path, manifest, issue=1002):
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps(manifest), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "hooks" / "plan_lib.py"), "assert-lens-coverage",
         "--manifest", str(mf), "--issue", str(issue), "--project-root", str(tmp_path)],
        capture_output=True, text=True)


def test_cli_exit_0_on_full_coverage(tmp_path) -> None:
    _snapshot(tmp_path)
    proc = _run_cli(tmp_path, _wave(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_cli_exit_1_names_the_missing_lens(tmp_path) -> None:
    _snapshot(tmp_path)
    r1 = _prompt(tmp_path, "r1.md", ("mechanical", "bug_logic"))
    r2 = _prompt(tmp_path, "r2.md", ("architecture",))
    proc = _run_cli(tmp_path, _wave(tmp_path, reviewers=[
        {"id": "r1", "lenses": ["mechanical", "bug_logic"], "prompt_file": r1.name},
        {"id": "r2", "lenses": ["architecture"], "prompt_file": r2.name}]))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "security" in proc.stdout


def test_cli_exit_2_on_a_malformed_manifest(tmp_path) -> None:
    """A caller error is never a vacuous pass."""
    _snapshot(tmp_path)
    mf = tmp_path / "manifest.json"
    mf.write_text("{not json", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "hooks" / "plan_lib.py"), "assert-lens-coverage",
         "--manifest", str(mf), "--issue", "1002", "--project-root", str(tmp_path)],
        capture_output=True, text=True)
    assert proc.returncode == 2, proc.stdout + proc.stderr
