"""#840 — parsing `git diff --name-status -M` into a changed-path set.

The load-bearing fact, established by probe on 2026-08-02 and NOT by reading docs: a rename
row carries THREE tab-separated fields (`R100<TAB>old<TAB>new`) where `M`/`A`/`D` rows carry
two. A parser that assumes two fields silently misreads every rename — it would take `old`
as the status and `new` as the path.

The same probe corrected a claim in the design: the `R` row appears with AND without `-M`,
because git enables rename detection by default. `-M` is retained only so the behaviour does
not depend on a repo-local `diff.renames=false`.

Pure functions imported directly per `docs/testing.md:5-8`.
"""
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import driver_lib as dl  # noqa: E402

T = "\t"


class TestTwoFieldRows:
    @pytest.mark.parametrize("status", ["M", "A", "D", "T"])
    def test_a_simple_status_row_yields_its_one_path(self, status):
        assert dl.parse_changed_paths(f"{status}{T}hooks/a.py") == {"hooks/a.py"}

    def test_multiple_rows_accumulate(self):
        diff = f"M{T}hooks/a.py\nA{T}docs/b.md\nD{T}tests/c.py"
        assert dl.parse_changed_paths(diff) == {"hooks/a.py", "docs/b.md", "tests/c.py"}

    def test_an_empty_diff_is_an_empty_set_not_an_error(self):
        """A merge that changed nothing is legitimate; a merge we could not READ is not.
        Those two must stay distinguishable, so only the latter raises."""
        assert dl.parse_changed_paths("") == set()
        assert dl.parse_changed_paths("\n\n  \n") == set()


class TestRenameAndCopyRows:
    def test_a_rename_row_yields_BOTH_paths(self):
        """The mutation-sensitive core of this module. If three-field handling is removed,
        this fails loudly instead of silently recording `old_name.py` as a status."""
        got = dl.parse_changed_paths(f"R100{T}old_name.py{T}new_name.py")
        assert got == {"old_name.py", "new_name.py"}

    def test_a_partial_similarity_rename_is_still_a_rename(self):
        got = dl.parse_changed_paths(f"R087{T}hooks/old.py{T}hooks/new.py")
        assert got == {"hooks/old.py", "hooks/new.py"}

    def test_a_copy_row_yields_both_paths(self):
        got = dl.parse_changed_paths(f"C075{T}src/a.py{T}src/b.py")
        assert got == {"src/a.py", "src/b.py"}

    def test_the_status_token_is_never_mistaken_for_a_path(self):
        """The exact silent-corruption shape: a two-field parser would emit 'old_name.py'
        as the STATUS and 'new_name.py' as the path, losing the old path entirely."""
        got = dl.parse_changed_paths(f"R100{T}old_name.py{T}new_name.py")
        assert "R100" not in got
        assert len(got) == 2

    def test_renames_mix_with_simple_rows(self):
        diff = (f"M{T}hooks/a.py\n"
                f"R100{T}old.py{T}new.py\n"
                f"A{T}docs/c.md")
        assert dl.parse_changed_paths(diff) == {
            "hooks/a.py", "old.py", "new.py", "docs/c.md"}

    def test_the_real_probe_output_parses(self):
        """Verbatim from the 2026-08-02 scratch-repo probe (`git mv` then commit)."""
        assert dl.parse_changed_paths(
            "R100\told_name.py\tnew_name.py") == {"old_name.py", "new_name.py"}


class TestFailClosed:
    def test_a_row_with_no_tab_raises(self):
        with pytest.raises(dl.DriverStateError):
            dl.parse_changed_paths("M hooks/a.py")

    def test_a_rename_row_missing_its_second_path_raises(self):
        """Fail closed rather than silently degrading to one path — a half-read rename would
        under-report the changed set, which biases a child toward `quick` when it needs
        `deep`. Failing toward less scrutiny is the one direction that must never happen."""
        with pytest.raises(dl.DriverStateError):
            dl.parse_changed_paths(f"R100{T}old_name.py")

    def test_an_unknown_status_letter_raises(self):
        with pytest.raises(dl.DriverStateError):
            dl.parse_changed_paths(f"Z{T}hooks/a.py")

    def test_a_two_field_row_for_a_rename_status_raises(self):
        with pytest.raises(dl.DriverStateError):
            dl.parse_changed_paths(f"R{T}only-one-path.py")

    def test_a_non_string_raises(self):
        with pytest.raises(dl.DriverStateError):
            dl.parse_changed_paths(None)

    def test_an_empty_path_field_raises(self):
        with pytest.raises(dl.DriverStateError):
            dl.parse_changed_paths(f"M{T}")


class TestReviewFindingsParserStrictness:
    """Adversarial-diff review, 2026-08-02: the parser called itself fail-closed while
    accepting malformed input. All three cases below were CONFIRMED by execution before the
    fix — each under-reports the changed set, which downgrades a child to `quick`."""

    def test_a_malformed_status_word_is_rejected(self):
        """Only the first character was checked, so `MALFORMED` read as `M`."""
        with pytest.raises(dl.DriverStateError):
            dl.parse_changed_paths(f"MALFORMED{T}hooks/a.py")

    def test_a_one_path_status_with_a_second_path_is_rejected(self):
        """`M<TAB>a.py<TAB>b.py` silently returned only a.py, losing b.py entirely."""
        with pytest.raises(dl.DriverStateError):
            dl.parse_changed_paths(f"M{T}a.py{T}b.py")

    def test_a_rename_row_with_a_fourth_field_is_rejected(self):
        with pytest.raises(dl.DriverStateError):
            dl.parse_changed_paths(f"R100{T}a.py{T}b.py{T}c.py")

    def test_a_rename_status_without_a_similarity_score_is_accepted(self):
        assert dl.parse_changed_paths(f"R{T}a.py{T}b.py") == {"a.py", "b.py"}

    @pytest.mark.parametrize("bad", ["R1000", "RX", "M1", "Rabc"])
    def test_a_malformed_similarity_score_is_rejected(self, bad):
        with pytest.raises(dl.DriverStateError):
            dl.parse_changed_paths(f"{bad}{T}a.py{T}b.py")
