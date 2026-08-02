"""#840 — `revalidation_worklist`: which remaining children need a look, and how hard a one.

Owner ruling 2026-08-02, after both pass-2 reviewers independently refuted the earlier design:
**the cited-paths intersection decides HOW HARD to look, never WHETHER.** Nothing is ever
auto-cleared. Every eligible child that is not stamped at the current head appears in the
worklist; the intersection only sets `depth`.

The refuted alternative auto-cleared a child whose cited files a merge did not touch. #835 is
the standing proof that this was wrong: its body was incorrect about the *cause*, not about a
filename, so a path filter would have waved it straight through — and #835 is one of the three
incidents that caused #840 to be filed.

Pure functions imported directly per `docs/testing.md:5-8`.
"""
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import driver_lib as dl  # noqa: E402

HEAD = "a" * 40
OLD = "b" * 40
BASE = "c" * 40


def _state(issues, base=BASE):
    return {"schema_version": 2, "campaign": "epic-756", "epic": 756,
            "base_default_branch_sha": base, "issues": issues}


def _iss(number, status="queued", validated=None, depends=None):
    entry = {"number": number, "status": status, "depends_on": depends or []}
    if validated is not None:
        entry["validated_against"] = validated
    return entry


def _by_number(items):
    return {item["number"]: item for item in items}


class TestWhichChildrenAppear:
    def test_a_never_validated_child_appears_with_the_campaign_base_as_from_sha(self):
        state = _state([_iss(840)])
        items = dl.revalidation_worklist(
            state, HEAD, extractions={840: ([], "none")}, changed_by_child={840: set()})
        assert len(items) == 1
        assert items[0]["number"] == 840
        assert items[0]["from_sha"] == BASE
        assert items[0]["to_sha"] == HEAD

    def test_a_child_stamped_at_an_older_head_appears(self):
        state = _state([_iss(840, validated=OLD)])
        items = dl.revalidation_worklist(
            state, HEAD, extractions={840: ([], "none")}, changed_by_child={840: set()})
        assert _by_number(items)[840]["from_sha"] == OLD

    def test_a_child_stamped_at_the_current_head_does_NOT_appear_WHEN_A_RECEIPT_ATTESTS_IT(self):
        """**Tightened at Step-11 round 6 (High 3).** This used to skip on the stamp alone. That
        made the gate's own refusal unclearable: a child stamped at the observed head under a
        STALE or ABSENT receipt was skipped, the worklist came back empty, and the skill the
        refusal names had nothing to audit and no way to advance the receipt. A stamp is a claim;
        the receipt is the evidence behind it, and only evidence earns the skip."""
        state = _state([_iss(840, validated=HEAD)])
        state["queue_revalidation"] = {
            "version": 1, "extractor_version": 1, "validated_head": HEAD,
            "children": {"840": {"body_hash": "9" * 64, "from_sha": BASE, "to_sha": HEAD,
                                 "extraction": "paths", "depth": "quick",
                                 "outcome": "still_valid",
                                 "claims": [{"kind": "cause", "quoted_from_body": "x",
                                             "checked_against": "y", "evidence": "z",
                                             "verdict": "holds"}],
                                 "validated_at": 1}}}
        assert dl.revalidation_worklist(
            state, HEAD, extractions={}, changed_by_child={}) == []

    def test_the_same_stamp_with_NO_receipt_is_audited_again(self):
        """The half that was missing, and the reason the old contract produced a dead end."""
        items = dl.revalidation_worklist(
            _state([_iss(840, validated=HEAD)]), HEAD,
            extractions={840: ([], "none")}, changed_by_child={840: set()})
        assert [i["number"] for i in items] == [840]
        assert items[0]["depth"] == "deep" and items[0]["baseline"] == "unavailable", items[0]

    def test_nothing_is_auto_cleared_even_when_no_cited_path_was_touched(self):
        """The owner ruling, asserted directly. Under the refuted design this child would have
        been stamped without a look; it must still appear, merely as `quick`."""
        state = _state([_iss(840)])
        items = dl.revalidation_worklist(
            state, HEAD,
            extractions={840: (["hooks/untouched.py"], "paths")},
            changed_by_child={840: {"docs/other.md"}})
        assert len(items) == 1, "a child whose cited files were untouched must STILL be looked at"
        assert items[0]["depth"] == "quick"


class TestEligibility:
    @pytest.mark.parametrize("status", ["merged", "abandoned", "deferred",
                                        "in_progress", "pr_open"])
    def test_only_queued_children_are_eligible(self, status):
        state = _state([_iss(840, status=status)])
        assert dl.revalidation_worklist(
            state, HEAD, extractions={}, changed_by_child={}) == []

    def test_an_externally_closed_child_is_excluded_via_the_effective_status_map(self):
        """A `queued` entry whose real issue the probe confirms merged must NOT be eligible.
        Using durable status here would block the queue forever on a revalidation nobody can
        meaningfully perform — a pass-3 finding."""
        state = _state([_iss(840), _iss(841)])
        items = dl.revalidation_worklist(
            state, HEAD,
            extractions={841: ([], "none")}, changed_by_child={841: set()},
            issue_state_probe=lambda n: "confirmed_merged" if n == 840 else "unknown")
        assert _by_number(items).keys() == {841}

    def test_a_probe_failure_conservatively_keeps_the_child_eligible(self):
        """`effective_issue_statuses` never vetoes on an outage, and neither may this.

        VACUITY NOTE (Step-11 review): the original version passed even when probe forwarding
        was removed entirely, because the durable status was `queued` anyway and the probe was
        never called. It now asserts the probe IS invoked, so dropping the forwarding turns
        this red.
        """
        called = []

        def boom(n):
            called.append(n)
            raise RuntimeError("github down")

        state = _state([_iss(840)])
        items = dl.revalidation_worklist(
            state, HEAD, extractions={840: ([], "none")}, changed_by_child={840: set()},
            issue_state_probe=boom)
        assert called == [840], "the probe was never forwarded, so this proved nothing"
        assert _by_number(items).keys() == {840}


class TestDepth:
    def _one(self, extraction, cited, changed):
        state = _state([_iss(840)])
        items = dl.revalidation_worklist(
            state, HEAD, extractions={840: (cited, extraction)},
            changed_by_child={840: changed})
        return items[0]

    def test_intersecting_cited_paths_make_it_deep(self):
        assert self._one("paths", ["hooks/a.py"], {"hooks/a.py"})["depth"] == "deep"

    def test_non_intersecting_cited_paths_make_it_quick(self):
        assert self._one("paths", ["hooks/a.py"], {"docs/b.md"})["depth"] == "quick"

    def test_an_ambiguous_body_is_always_deep(self):
        """Path-shaped but unreadable — fail toward MORE scrutiny."""
        assert self._one("ambiguous", [], {"docs/b.md"})["depth"] == "deep"

    def test_a_citation_free_body_is_always_deep(self):
        """AC2, owner-confirmed: a body naming no files still gets a real look. #835's body
        was wrong about the CAUSE, and no path filter would ever have caught that."""
        assert self._one("none", [], {"docs/b.md"})["depth"] == "deep"

    def test_a_rename_old_path_citation_still_intersects(self):
        """`parse_changed_paths` puts BOTH sides of a rename in the changed set, so a child
        citing the pre-rename path is correctly escalated to deep."""
        assert self._one("paths", ["old_name.py"],
                         {"old_name.py", "new_name.py"})["depth"] == "deep"


class TestFailClosed:
    def test_a_missing_extractions_entry_raises(self):
        """'No data' must never read as 'no changes'. A silently-absent extraction would
        default the child to quick, which fails toward LESS scrutiny."""
        state = _state([_iss(840)])
        with pytest.raises(dl.DriverStateError):
            dl.revalidation_worklist(state, HEAD, extractions={},
                                     changed_by_child={840: set()})

    def test_a_missing_changed_by_child_entry_raises(self):
        state = _state([_iss(840)])
        with pytest.raises(dl.DriverStateError):
            dl.revalidation_worklist(state, HEAD, extractions={840: ([], "none")},
                                     changed_by_child={})

    def test_a_malformed_observed_head_raises(self):
        state = _state([_iss(840)])
        with pytest.raises(dl.DriverStateError):
            dl.revalidation_worklist(state, "not-a-sha", extractions={840: ([], "none")},
                                     changed_by_child={840: set()})

    def test_a_malformed_existing_stamp_no_longer_raises_it_falls_back(self):
        """**INVERTED at Step-11 round 3 (High 1), deliberately.** This asserted that a corrupt
        stamp raises. That WAS the jam: once the gate became universal, a campaign carrying one
        was refused by the gate while the clearing skill could not build the worklist that would
        clear it. The contract now is fail-toward-MORE-scrutiny instead of fail-shut — the range
        collapses to the observed head and depth is forced `deep`, so nothing is waved through.

        Inverted rather than deleted: a guard that no longer describes the code is worse than no
        guard, and this one has to keep saying that a corrupt stamp is never merely ignored."""
        state = _state([_iss(840, validated="short")])
        items = dl.revalidation_worklist(state, HEAD, extractions={840: ([], "none")},
                                         changed_by_child={840: set()})
        assert [i["number"] for i in items] == [840], "the child must still be looked at"
        assert items[0]["from_sha"] == HEAD and items[0]["to_sha"] == HEAD, items[0]
        assert items[0]["depth"] == "deep", items[0]
        assert items[0]["baseline"] == "unavailable", items[0]


class TestReviewFindingsValueValidation:
    """Adversarial-diff review, 2026-08-02: the function checked only that a KEY was present,
    never that its VALUE was usable. Confirmed by execution before the fix — a corrupt payload
    produced `quick`, directly contradicting this function's own docstring promise that
    unreadable data must never become "no changes"."""

    def _run(self, extractions, changed):
        state = _state([_iss(840)])
        return dl.revalidation_worklist(state, HEAD, extractions=extractions,
                                        changed_by_child=changed)

    def test_a_null_changed_set_raises_instead_of_becoming_quick(self):
        with pytest.raises(dl.DriverStateError):
            self._run({840: ([], "none")}, {840: None})

    def test_null_cited_paths_with_a_paths_verdict_raises(self):
        """`(None, "paths")` claimed a successful extraction with nothing extracted, and the
        empty intersection then produced `quick`."""
        with pytest.raises(dl.DriverStateError):
            self._run({840: (None, "paths")}, {840: {"hooks/a.py"}})

    def test_an_off_vocabulary_extraction_verdict_raises(self):
        with pytest.raises(dl.DriverStateError):
            self._run({840: ([], "probably")}, {840: set()})

    def test_a_non_tuple_extraction_raises(self):
        with pytest.raises(dl.DriverStateError):
            self._run({840: "paths"}, {840: set()})

    def test_a_paths_verdict_with_an_empty_list_raises(self):
        """`paths` asserts at least one resolved citation; an empty list is incoherent."""
        with pytest.raises(dl.DriverStateError):
            self._run({840: ([], "paths")}, {840: set()})

    def test_a_non_string_inside_the_cited_list_raises(self):
        with pytest.raises(dl.DriverStateError):
            self._run({840: ([42], "paths")}, {840: set()})
