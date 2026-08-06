"""Tests for hooks/driver_lib.py — multi-issue driver dependency-DAG helpers (#163).

Covers the narrow DAG + schema-readability surface (design #134, slot-10 fork
decision = Codex option C):
- parse_depends_on: strict, prompt-injection-safe issue-number extraction
- topo_sort_issues: Kahn ordering + deterministic tie-break + fail-closed cycle
- next_ready_issue: deps-satisfied advance rule + deps_satisfied_by knob + parking
- validate_driver_state: v1/v2 schema readability (v1 files still readable — AC7)

The fuller state-transition validator (record_outcome/defer_issue/queue mutation)
is intentionally NOT part of this module (deferred, #134 follow-up #2).
"""
import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import driver_lib  # noqa: E402
import driver_lib as dl  # noqa: E402  (#569 handoff-helper tests use the short alias)


# --------------------------------------------------------------------------- #
# parse_depends_on
# --------------------------------------------------------------------------- #
def test_parse_depends_on_keyword_phrases():
    assert driver_lib.parse_depends_on("Depends on #148 first.") == [148]
    assert driver_lib.parse_depends_on("Blocked by #12.") == [12]
    # hyphenated variant
    assert driver_lib.parse_depends_on("depends-on #7") == [7]


def test_parse_depends_on_multiple_and_comma_separated():
    body = "Depends on #10, #20 and #30 before it can start."
    assert driver_lib.parse_depends_on(body) == [10, 20, 30]


def test_parse_depends_on_task_list_refs():
    body = "\n".join([
        "Checklist:",
        "- [ ] #101 do the thing",
        "- [x] #102 done thing",
        "* [ ] #103 star bullet",
    ])
    assert driver_lib.parse_depends_on(body) == [101, 102, 103]


def test_parse_depends_on_dedup_and_sorted():
    body = "Depends on #30 and #10. Also blocked by #10 and #30."
    assert driver_lib.parse_depends_on(body) == [10, 30]


def test_parse_depends_on_injection_safe_ignores_prose_and_bare_refs():
    # "does not depend on" is not the "depends on" phrase; a bare "#999 for
    # context" is not a dependency phrase and must NOT become a dependency.
    body = (
        "This change does not depend on anything external.\n"
        "See #999 for background context and #1000 for the epic.\n"
        "Ignore the number #42 in this sentence."
    )
    assert driver_lib.parse_depends_on(body) == []


def test_parse_depends_on_empty_and_no_matches():
    assert driver_lib.parse_depends_on("") == []
    assert driver_lib.parse_depends_on("No dependencies here.") == []


def test_parse_depends_on_negation_not_taken():
    # An explicitly negated phrase is a statement of NON-dependency (8a R1-F1).
    assert driver_lib.parse_depends_on("This is not blocked by #5.") == []
    assert driver_lib.parse_depends_on("It no longer depends on #7.") == []


def test_parse_depends_on_word_boundary_ignores_substring_match():
    # "blocked by" inside "unblocked by" must not match (8a R1-F1).
    assert driver_lib.parse_depends_on("This was unblocked by #5.") == []


def test_parse_depends_on_task_list_line_also_carrying_a_keyword():
    # A checkbox line that also states a dependency keeps BOTH (8a R2-F1).
    assert driver_lib.parse_depends_on("- [ ] #204 depends on #202") == [202, 204]


def test_parse_depends_on_two_keywords_order_independent():
    # A dep stated before the tuple-priority keyword must not be dropped (8a R2-F2).
    assert driver_lib.parse_depends_on("Blocked by #2, depends on #1") == [1, 2]


def test_parse_depends_on_stops_at_sentence_boundary():
    # A following sentence must NOT inject a dep (Codex diff-review F1, High).
    assert driver_lib.parse_depends_on("Depends on #10. See #20 for context") == [10]
    # A trailing relative clause is not part of the dependency list either.
    assert driver_lib.parse_depends_on("Depends on #10 which also fixes #20.") == [10]


def test_parse_depends_on_colon_and_separators():
    assert driver_lib.parse_depends_on("Depends on: #10, #20 & #30") == [10, 20, 30]


def test_parse_depends_on_case_insensitive_on_raw_line():
    # Matches on the raw line case-insensitively (no lowercase-then-slice), so
    # offsets can't drift (8a Claude-review hardening note).
    assert driver_lib.parse_depends_on("BLOCKED BY #5") == [5]
    assert driver_lib.parse_depends_on("This is NOT blocked by #5.") == []


def test_parse_depends_on_not_markdown_aware_documented_limitation():
    # DOCUMENTED, not a hard boundary: a phrase quoted in a blockquote IS taken.
    # Locked so the limitation stays honest (Codex/Claude review Cl-F2).
    assert driver_lib.parse_depends_on("> reviewer said: depends on #666") == [666]


def test_parse_depends_on_noun_phrasing_before_ref():
    # F2: an optional "issue/PR/epic" noun before a #N must not block capture.
    assert driver_lib.parse_depends_on("Depends on issue #10.") == [10]
    assert driver_lib.parse_depends_on("Blocked by issues #10 and #20.") == [10, 20]
    assert driver_lib.parse_depends_on("depends on the epic #30") == [30]
    assert driver_lib.parse_depends_on("Blocked by PR #44") == [44]
    # noun repeated before each subsequent #N in the list, too
    assert driver_lib.parse_depends_on("depends on issue #10 and issue #11") == [10, 11]


def test_parse_depends_on_negation_modal_be_get_bridge():
    # F3: a modal negation with a "be"/"get" bridge still negates the phrase.
    assert driver_lib.parse_depends_on("cannot be blocked by #5") == []
    assert driver_lib.parse_depends_on("This will never be blocked by #5") == []
    assert driver_lib.parse_depends_on("won't be blocked by #5") == []


def test_parse_depends_on_negation_scoped_to_first_phrase_composite():
    # F3 must-not-regress: negation applies only to the phrase it precedes; a
    # later un-negated phrase on the same line still contributes its deps.
    assert driver_lib.parse_depends_on("not blocked by #5, but blocked by #6") == [6]


# --------------------------------------------------------------------------- #
# topo_sort_issues
# --------------------------------------------------------------------------- #
def _issue(n, deps=None, status="queued"):
    d = {"number": n, "status": status}
    if deps is not None:
        d["depends_on"] = deps
    return d


def test_topo_sort_linear_chain():
    # 3 depends on 2 depends on 1  =>  order [1, 2, 3]
    issues = [_issue(3, [2]), _issue(2, [1]), _issue(1)]
    assert driver_lib.topo_sort_issues(issues) == [1, 2, 3]


def test_topo_sort_deterministic_tiebreak_lowest_number_first():
    # No deps: independent nodes come out in ascending number order (stable).
    issues = [_issue(30), _issue(10), _issue(20)]
    assert driver_lib.topo_sort_issues(issues) == [10, 20, 30]


def test_topo_sort_diamond():
    # 4 depends on 2 and 3; both depend on 1.
    issues = [_issue(4, [2, 3]), _issue(2, [1]), _issue(3, [1]), _issue(1)]
    order = driver_lib.topo_sort_issues(issues)
    assert order.index(1) < order.index(2) < order.index(4)
    assert order.index(1) < order.index(3) < order.index(4)
    # deterministic: 2 before 3 (tie-break by number)
    assert order.index(2) < order.index(3)


def test_topo_sort_ignores_external_deps_not_in_queue():
    # #5 depends on #99 which is not in the queue -> external, ignored for order.
    issues = [_issue(5, [99]), _issue(1)]
    assert driver_lib.topo_sort_issues(issues) == [1, 5]


def test_topo_sort_cycle_fails_closed_with_cycle_printed():
    issues = [_issue(1, [2]), _issue(2, [1])]
    with pytest.raises(driver_lib.DependencyCycleError) as exc:
        driver_lib.topo_sort_issues(issues)
    msg = str(exc.value)
    assert "#1" in msg and "#2" in msg
    # DependencyCycleError is a DriverStateError (fail-closed marker type)
    assert isinstance(exc.value, driver_lib.DriverStateError)


def test_topo_sort_duplicate_numbers_rejected():
    with pytest.raises(driver_lib.DriverStateError):
        driver_lib.topo_sort_issues([_issue(1), _issue(1)])


def test_topo_sort_empty():
    assert driver_lib.topo_sort_issues([]) == []


def test_topo_sort_missing_number_raises_driver_state_error():
    # A missing "number" fails closed with the typed error, not a bare KeyError
    # (8a R1-F2 / R2-F5).
    with pytest.raises(driver_lib.DriverStateError):
        driver_lib.topo_sort_issues([{"status": "queued"}])


def test_topo_sort_non_int_depends_on_entry_raises_driver_state_error():
    # F4: a string dep entry ("148") would silently impose no edge (treated as
    # external/satisfied). Fail closed instead, naming the offending issue.
    issues = [
        {"number": 163, "status": "queued", "depends_on": ["148"]},
        {"number": 148, "status": "queued"},
    ]
    with pytest.raises(driver_lib.DriverStateError) as exc:
        driver_lib.topo_sort_issues(issues)
    assert "163" in str(exc.value)


# --------------------------------------------------------------------------- #
# next_ready_issue
# --------------------------------------------------------------------------- #
def test_next_ready_issue_picks_first_queued_with_deps_merged():
    state = {"schema_version": 2, "campaign": "c", "issues": [
        _issue(1, status="merged"),
        _issue(2, [1], status="queued"),
        _issue(3, [2], status="queued"),
    ]}
    assert driver_lib.next_ready_issue(state) == 2


def test_next_ready_issue_none_when_deps_unmerged():
    state = {"schema_version": 2, "campaign": "c", "issues": [
        _issue(1, status="pr_open"),
        _issue(2, [1], status="queued"),
    ]}
    # default deps_satisfied_by=merged: #1 only pr_open -> #2 not ready
    assert driver_lib.next_ready_issue(state) is None


def test_next_ready_issue_pr_open_knob():
    state = {"schema_version": 2, "campaign": "c", "issues": [
        _issue(1, status="pr_open"),
        _issue(2, [1], status="queued"),
    ]}
    assert driver_lib.next_ready_issue(state, deps_satisfied_by="pr_open") == 2


def test_next_ready_issue_deferred_dep_parks_dependent_but_continues():
    # #1 deferred parks #2 (its dependent); #3 independent -> ready.
    state = {"schema_version": 2, "campaign": "c", "issues": [
        _issue(1, status="deferred"),
        _issue(2, [1], status="queued"),
        _issue(3, status="queued"),
    ]}
    assert driver_lib.next_ready_issue(state) == 3


def test_next_ready_issue_external_dep_assumed_satisfied():
    # #99 not in queue -> external -> assumed satisfied; #5 ready.
    state = {"schema_version": 2, "campaign": "c", "issues": [
        _issue(5, [99], status="queued"),
    ]}
    assert driver_lib.next_ready_issue(state) == 5


def test_next_ready_issue_invalid_knob_raises():
    state = {"schema_version": 2, "campaign": "c", "issues": []}
    with pytest.raises(driver_lib.DriverStateError):
        driver_lib.next_ready_issue(state, deps_satisfied_by="whenever")


def test_next_ready_issue_none_when_nothing_queued():
    state = {"schema_version": 2, "campaign": "c", "issues": [
        _issue(1, status="merged"),
    ]}
    assert driver_lib.next_ready_issue(state) is None


def test_next_ready_issue_missing_number_raises_driver_state_error():
    # Fail-closed with the typed error, not a bare KeyError (8a R1-F2 / R2-F5).
    state = {"schema_version": 2, "campaign": "c", "issues": [{"status": "queued"}]}
    with pytest.raises(driver_lib.DriverStateError):
        driver_lib.next_ready_issue(state)


def test_next_ready_issue_non_int_depends_on_entry_raises_driver_state_error():
    # F4: a string dep entry must fail closed here too (both entry points route
    # through _in_queue_deps), naming the offending issue.
    state = {"schema_version": 2, "campaign": "c", "issues": [
        {"number": 163, "status": "queued", "depends_on": ["148"]},
        {"number": 148, "status": "queued"},
    ]}
    with pytest.raises(driver_lib.DriverStateError) as exc:
        driver_lib.next_ready_issue(state)
    assert "163" in str(exc.value)


# --------------------------------------------------------------------------- #
# The obsolete-child owner gate (#944, AC2) — next_ready_issue's pending-disposition raise
# --------------------------------------------------------------------------- #
_OBSOLETE_HEAD = "a" * 40


def _valid_receipt_record(pending=None, head=_OBSOLETE_HEAD):
    """A fully valid queue_revalidation.children[<n>] record, built via the real constructor —
    `next_ready_issue`'s receipt-freshness gate (`_refuse_unrevalidated_queue`) runs the FULL
    `validate_queue_revalidation` check whenever ANY receipt is present, so a hand-rolled partial
    record fails structural validation before this new logic is ever reached."""
    kind, verdict = ("cause", "broken") if pending else ("ac", "holds")
    claim = {"kind": kind, "quoted_from_body": "irrelevant", "verdict": verdict,
             "checked_against": "<no-file: gone>", "evidence": "x"}
    kwargs = dict(body="a trivial body with no headings at all", from_sha=head, to_sha=head,
                 extraction="none", depth="deep", claims=[claim], validated_at=1, resolves=set())
    if pending:
        kwargs["pending_disposition"] = pending
    return driver_lib.build_revalidation_record(**kwargs)


def _revalidated_state(specs, head=_OBSOLETE_HEAD):
    """`specs`: [(number, status, pending_or_None), ...]. Every `queued` issue is stamped
    `validated_against=head` and given a matching receipt record (the linkage
    `_validate_queue_revalidation` requires); non-queued issues get neither, since the
    per-child provenance clause only applies to ELIGIBLE (queued) children."""
    issues = []
    children = {}
    for number, status, pending in specs:
        entry = {"number": number, "status": status}
        if status == "queued":
            entry["validated_against"] = head
            children[str(number)] = _valid_receipt_record(pending, head)
        issues.append(entry)
    state = {"schema_version": 2, "campaign": "c", "issues": issues,
             "queue_revalidation": {"version": 1, "extractor_version": 1,
                                    "validated_head": head, "children": children}}
    return state, head


class TestObsoletePendingChild:
    def test_a_lone_obsolete_pending_candidate_raises(self):
        state, head = _revalidated_state([(1, "queued", "issue_obsolete")])
        with pytest.raises(driver_lib.ObsoletePendingChild) as exc:
            driver_lib.next_ready_issue(state, observed_head=head)
        assert exc.value.issue == 1

    def test_an_unrelated_later_ready_candidate_is_selected_instead(self):
        """The round-2 review's finding 4, and the reason this whole task exists: raising on
        the FIRST obsolete-pending candidate stopped the entire scan, even when a completely
        unrelated, independent, ready candidate exists later in queue order."""
        state, head = _revalidated_state(
            [(1, "queued", "issue_obsolete"), (2, "queued", None)])
        assert driver_lib.next_ready_issue(state, observed_head=head) == 2

    def test_raises_only_when_nothing_else_is_selectable(self):
        state, head = _revalidated_state([(1, "queued", "issue_obsolete"), (2, "merged", None)])
        with pytest.raises(driver_lib.ObsoletePendingChild) as exc:
            driver_lib.next_ready_issue(state, observed_head=head)
        assert exc.value.issue == 1

    def test_a_deferred_child_never_raises_at_all(self):
        """The moment an owner (or a future automation) calls record-child-outcome, the child
        is no longer `queued` — next_ready_issue's existing status filter already skips it, with
        no new 'exclude' mechanism needed."""
        state, head = _revalidated_state(
            [(1, "deferred", "issue_obsolete"), (2, "queued", None)])
        assert driver_lib.next_ready_issue(state, observed_head=head) == 2

    def test_a_merged_child_never_raises_either(self):
        state, head = _revalidated_state([(1, "merged", "issue_obsolete")])
        assert driver_lib.next_ready_issue(state, observed_head=head) is None

    def test_dependency_satisfaction_still_gates_before_the_pending_check(self):
        """An obsolete-pending child whose OWN deps are unsatisfied is simply not ready yet —
        the pending-disposition raise only fires for a candidate that would otherwise BE
        selected."""
        state, head = _revalidated_state([(2, "queued", "issue_obsolete")])
        # #1 is pr_open (not eligible, so it needs no stamp/receipt entry of its own) and #2
        # depends on it — #2's dep is unsatisfied, so #2 is not ready regardless of the pending
        # marker.
        state["issues"].insert(0, _issue(1, status="pr_open"))
        state["issues"][1]["depends_on"] = [1]
        assert driver_lib.next_ready_issue(state, observed_head=head) is None

    def test_first_obsolete_issue_is_the_one_named_when_several_exist(self):
        state, head = _revalidated_state(
            [(1, "queued", "issue_obsolete"), (2, "queued", "issue_obsolete")])
        with pytest.raises(driver_lib.ObsoletePendingChild) as exc:
            driver_lib.next_ready_issue(state, observed_head=head)
        assert exc.value.issue == 1


class TestHasPendingDependents:
    def test_true_when_a_remaining_child_directly_depends_on_it(self):
        state = {"issues": [_issue(1, status="queued"), _issue(2, [1], status="queued")]}
        assert driver_lib.has_pending_dependents(state, 1) is True

    def test_false_when_nothing_depends_on_it(self):
        state = {"issues": [_issue(1, status="queued"), _issue(2, status="queued")]}
        assert driver_lib.has_pending_dependents(state, 1) is False

    def test_a_merged_dependent_does_not_count(self):
        """A TERMINAL child will never run again — its historical dependency on the obsolete
        issue is not something that still needs unblocking."""
        state = {"issues": [_issue(1, status="queued"), _issue(2, [1], status="merged")]}
        assert driver_lib.has_pending_dependents(state, 1) is False

    def test_an_abandoned_dependent_does_not_count_either(self):
        state = {"issues": [_issue(1, status="queued"), _issue(2, [1], status="abandoned")]}
        assert driver_lib.has_pending_dependents(state, 1) is False

    def test_a_deferred_dependent_still_counts(self):
        """Deferred is NOT terminal — a parked child can legitimately be re-queued later, and
        would then need issue 1 resolved."""
        state = {"issues": [_issue(1, status="queued"), _issue(2, [1], status="deferred")]}
        assert driver_lib.has_pending_dependents(state, 1) is True


class TestFreshSessionHandoffObsoletePending:
    def test_returns_the_obsolete_pending_outcome_not_blocked(self):
        """Round-1's own principle, re-applied: a recoverable refusal must never collapse into
        the generic 'blocked' outcome — the same reason revalidation_required exists."""
        state, head = _revalidated_state([(1, "queued", "issue_obsolete")])
        disposition = driver_lib.fresh_session_handoff(
            state, mode=driver_lib.FRESH_SESSION_MODE, observed_head=head)
        assert disposition["outcome"] == "obsolete_pending"
        assert disposition["issue"] == 1
        assert disposition["has_pending_dependents"] is False

    def test_has_pending_dependents_is_true_when_something_depends_on_it(self):
        state, head = _revalidated_state(
            [(1, "queued", "issue_obsolete"), (2, "queued", None)])
        state["issues"][1]["depends_on"] = [1]
        disposition = driver_lib.fresh_session_handoff(
            state, mode=driver_lib.FRESH_SESSION_MODE, observed_head=head)
        assert disposition["outcome"] == "obsolete_pending"
        assert disposition["has_pending_dependents"] is True


# --------------------------------------------------------------------------- #
# validate_driver_state (v1/v2 readability)
# --------------------------------------------------------------------------- #
def test_validate_driver_state_valid_v2():
    state = {"schema_version": 2, "campaign": "issues-148-163", "issues": [
        _issue(1, status="merged"),
        _issue(2, [1], status="queued"),
    ]}
    ok, errors = driver_lib.validate_driver_state(state)
    assert ok, errors


def test_validate_driver_state_v1_still_readable():
    # v1 has no depends_on on its issues; a v2-aware reader must accept it (AC7).
    state = {"schema_version": 1, "campaign": "issues-131-140", "issues": [
        {"number": 133, "status": "merged"},
        {"number": 137, "status": "queued"},
    ]}
    ok, errors = driver_lib.validate_driver_state(state)
    assert ok, errors


def test_validate_driver_state_unknown_schema_version():
    ok, errors = driver_lib.validate_driver_state(
        {"schema_version": 3, "campaign": "c", "issues": []})
    assert not ok
    assert any("schema_version" in e for e in errors)


def test_validate_driver_state_missing_campaign():
    ok, errors = driver_lib.validate_driver_state(
        {"schema_version": 2, "campaign": "", "issues": []})
    assert not ok
    assert any("campaign" in e for e in errors)


def test_validate_driver_state_bad_status():
    ok, errors = driver_lib.validate_driver_state(
        {"schema_version": 2, "campaign": "c", "issues": [
            {"number": 1, "status": "wormhole"}]})
    assert not ok
    assert any("status" in e for e in errors)


def test_validate_driver_state_bad_depends_on_type():
    ok, errors = driver_lib.validate_driver_state(
        {"schema_version": 2, "campaign": "c", "issues": [
            {"number": 1, "status": "queued", "depends_on": ["two"]}]})
    assert not ok
    assert any("depends_on" in e for e in errors)


def test_validate_driver_state_duplicate_number():
    ok, errors = driver_lib.validate_driver_state(
        {"schema_version": 2, "campaign": "c", "issues": [
            {"number": 1, "status": "queued"},
            {"number": 1, "status": "merged"}]})
    assert not ok
    assert any("duplicate" in e for e in errors)


def test_validate_driver_state_serial_active_invariant():
    # F1: the serial invariant is in_progress-ONLY — at most one build at a time,
    # but pr_open may accumulate awaiting human merge (headless stacked-PR flow).
    # (a) one in_progress + one pr_open -> VALID (was rejected before F1).
    ok, errors = driver_lib.validate_driver_state(
        {"schema_version": 2, "campaign": "c", "issues": [
            {"number": 1, "status": "in_progress"},
            {"number": 2, "status": "pr_open"}]})
    assert ok, errors
    # (b) two in_progress -> invalid; error names in_progress.
    ok, errors = driver_lib.validate_driver_state(
        {"schema_version": 2, "campaign": "c", "issues": [
            {"number": 1, "status": "in_progress"},
            {"number": 2, "status": "in_progress"}]})
    assert not ok
    assert any("in_progress" in e for e in errors)
    # (c) two pr_open + one in_progress -> VALID (pr_open accumulates).
    ok, errors = driver_lib.validate_driver_state(
        {"schema_version": 2, "campaign": "c", "issues": [
            {"number": 1, "status": "pr_open"},
            {"number": 2, "status": "pr_open"},
            {"number": 3, "status": "in_progress"}]})
    assert ok, errors


def test_validate_driver_state_single_active_ok():
    ok, errors = driver_lib.validate_driver_state(
        {"schema_version": 2, "campaign": "c", "issues": [
            {"number": 1, "status": "in_progress"},
            {"number": 2, "status": "queued"}]})
    assert ok, errors


def test_validate_driver_state_bool_number_rejected():
    # True is an int in Python; the validator must not accept it as a number.
    ok, errors = driver_lib.validate_driver_state(
        {"schema_version": 2, "campaign": "c", "issues": [
            {"number": True, "status": "queued"}]})
    assert not ok
    assert any("number" in e for e in errors)


# --------------------------------------------------------------------------- #
# committed schema + example state files (AC1/AC2/AC7)
# --------------------------------------------------------------------------- #
# The git-tracked schema + examples live in docs/ (claude_docs/ is gitignored —
# it holds runtime session/campaign working state, not committed source).
REPO = Path(__file__).resolve().parent.parent.parent
DRIVER_STATE_DIR = REPO / "docs" / "driver-state"


def test_committed_schema_and_examples_exist():
    assert (DRIVER_STATE_DIR / "queue.schema.json").exists()
    assert (DRIVER_STATE_DIR / "example-v2.campaign.json").exists()
    assert (DRIVER_STATE_DIR / "example-v1.campaign.json").exists()


def test_committed_examples_validate_against_json_schema():
    # Hard import (not importorskip): jsonschema is a CI test dependency, and this
    # is the ONLY check that the committed schema matches the examples — a silent
    # skip would let schema/example drift pass unnoticed (8a R1-F3).
    import jsonschema
    schema = json.loads((DRIVER_STATE_DIR / "queue.schema.json").read_text())
    for name in ("example-v2.campaign.json", "example-v1.campaign.json"):
        data = json.loads((DRIVER_STATE_DIR / name).read_text())
        jsonschema.validate(data, schema)  # raises on failure


def test_committed_examples_pass_pure_python_validator():
    for name in ("example-v2.campaign.json", "example-v1.campaign.json"):
        data = json.loads((DRIVER_STATE_DIR / name).read_text())
        ok, errors = driver_lib.validate_driver_state(data)
        assert ok, f"{name}: {errors}"


class TestCampaignGoalText:
    """#192: the driver seam that builds ONE epic-level /goal at campaign kickoff
    from the epic anchor + the topo-ordered child queue."""

    def _state(self):
        return {
            "schema_version": 2,
            "campaign": "epic-188",
            "epic": 188,
            "issues": [
                {"number": 192, "depends_on": [191]},
                {"number": 190},
                {"number": 191},
            ],
        }

    def test_builds_epic_goal_with_topo_ordered_children(self):
        text = driver_lib.campaign_goal_text(self._state())
        assert "Epic #188" in text
        # topo order: 190, 191, then 192 (depends on 191)
        assert text.index("#190") < text.index("#191") < text.index("#192")
        assert "pause" in text.lower()  # tolerant escape clause carried through

    def test_missing_epic_raises(self):
        state = self._state()
        del state["epic"]
        with pytest.raises(driver_lib.DriverStateError):
            driver_lib.campaign_goal_text(state)

    def test_cycle_in_queue_raises(self):
        state = self._state()
        state["issues"] = [
            {"number": 1, "depends_on": [2]},
            {"number": 2, "depends_on": [1]},
        ]
        with pytest.raises(driver_lib.DependencyCycleError):
            driver_lib.campaign_goal_text(state)


# ======================================================================= #
# #569: fresh-session-per-child handoff helpers
# ======================================================================= #
def _st(issues, *, mode=None, generation=None, campaign="epic-475", extra=None,
        project="rawgentic"):
    # #682: `project` is part of a workable campaign state — without it there is no valid
    # `/rawgentic:switch <project>` to put at the head of a resume prompt, so
    # `fresh_session_handoff` returns `no_project` rather than `ready`. Default it here so existing
    # tests keep testing what they were written to test; pass `project=None` to exercise the refusal.
    s = {"schema_version": 2, "campaign": campaign, "issues": issues}
    if project is not None:
        s["project"] = project
    if mode is not None:
        s["session_mode"] = mode
    if generation is not None:
        s["generation"] = generation
    if extra:
        s.update(extra)
    return s


def _iss(n, status, deps=None):
    i = {"number": n, "status": status}
    if deps is not None:
        i["depends_on"] = deps
    return i


class TestFreshSessionHandoff:
    def test_single_session_when_mode_absent(self):
        s = _st([_iss(1, "merged"), _iss(2, "queued")])
        assert dl.fresh_session_handoff(s, mode=s.get("session_mode", "single-session")) == {"outcome": "single_session"}

    def test_ready_returns_next_and_generation(self):
        s = _st([_iss(1, "merged"), _iss(2, "queued")], mode="fresh-session", generation=3)
        d = dl.fresh_session_handoff(s, mode="fresh-session")
        assert d["outcome"] == "ready"
        assert d["next_issue"] == 2
        assert d["generation"] == 4  # monotonic bump
        assert isinstance(d["resume_prompt"], str) and "2" in d["resume_prompt"]

    def test_complete_only_when_all_merged(self):
        s = _st([_iss(1, "merged"), _iss(2, "merged")], mode="fresh-session")
        assert dl.fresh_session_handoff(s, mode="fresh-session")["outcome"] == "complete"

    def test_blocked_when_unmerged_but_none_ready(self):
        # a deferred child + a queued child that depends on it → nothing ready, not complete.
        s = _st([_iss(1, "deferred"), _iss(2, "queued", deps=[1])], mode="fresh-session")
        d = dl.fresh_session_handoff(s, mode="fresh-session")
        assert d["outcome"] == "blocked"  # #569 [2]: never "complete" while a child is unmerged

    def test_abandoned_alone_is_blocked_not_complete(self):
        s = _st([_iss(1, "merged"), _iss(2, "abandoned")], mode="fresh-session")
        assert dl.fresh_session_handoff(s, mode="fresh-session")["outcome"] == "blocked"


class TestFreshSessionAvailable:
    def test_ok_when_armed_writable_and_fresh_supported(self):
        ok, reason = dl.fresh_session_available(
            _st([]), launcher_armed=True, handoff_writable=True, fresh_launch_supported=True)
        assert ok is True

    def test_not_armed_fails(self):
        ok, reason = dl.fresh_session_available(
            _st([]), launcher_armed=False, handoff_writable=True, fresh_launch_supported=True)
        assert ok is False and "launcher" in reason.lower()

    def test_resume_first_launcher_fails_F1(self):
        # Step-11 F1 (Critical): an armed+writable launcher that does NOT advertise fresh-launch
        # support must be rejected — else fresh mode silently defeats AC1 on the resume-first launcher.
        ok, reason = dl.fresh_session_available(
            _st([]), launcher_armed=True, handoff_writable=True, fresh_launch_supported=False)
        assert ok is False and "fresh" in reason.lower()

    def test_not_writable_fails(self):
        ok, reason = dl.fresh_session_available(
            _st([]), launcher_armed=True, handoff_writable=False, fresh_launch_supported=True)
        assert ok is False and "writ" in reason.lower()

    # --- #611 Step-11 High 1: the herdr decision belongs at THIS boundary ---------------

    def test_launch_mode_omitted_on_a_single_session_campaign_changes_nothing(self):
        """Back-compat: a campaign with no process boundary is unaffected by the new argument."""
        ok, _ = dl.fresh_session_available(
            _st([]), launcher_armed=True, handoff_writable=True, fresh_launch_supported=True)
        assert ok is True

    def test_an_omitted_launch_mode_fails_CLOSED_for_a_fresh_session_campaign(self):
        """#611 Step-11 pass-4 High 2: leaving the verdict optional made the guard depend on
        skill prose remembering to pass it. By the time the launcher discovers the truth the
        driver has written `handoff_pending` and ended, so 'keep the current loop' is already
        impossible. For a campaign that HAS a boundary, silence must not read as launchable."""
        ok, reason = dl.fresh_session_available(
            _st([], mode="fresh-session"), launcher_armed=True, handoff_writable=True,
            fresh_launch_supported=True)
        assert ok is False and "verdict not supplied" in reason

    def test_single_session_launch_mode_refuses(self):
        """A herdr-gated project with no reachable pane must keep its current loop. Deciding
        this only inside the launcher left the real boundary — the driver's own availability
        check — still saying yes (#611 Step-11 High 1)."""
        ok, reason = dl.fresh_session_available(
            _st([]), launcher_armed=True, handoff_writable=True, fresh_launch_supported=True,
            launch_mode="single_session")
        assert ok is False and "single_session" in reason

    def test_herdr_and_pane_less_launch_modes_are_both_available(self):
        for mode in ("herdr", "pane_less"):
            ok, _ = dl.fresh_session_available(
                _st([]), launcher_armed=True, handoff_writable=True,
                fresh_launch_supported=True, launch_mode=mode)
            assert ok is True, mode

    def test_an_unknown_launch_mode_fails_closed(self):
        """Fail-open means degrading to the single-session loop, never launching on a mode
        nobody recognises."""
        ok, reason = dl.fresh_session_available(
            _st([]), launcher_armed=True, handoff_writable=True, fresh_launch_supported=True,
            launch_mode="turbo")
        assert ok is False and "turbo" in reason


def _pending(gen=5, nxt=7):
    # a ready state: generation counter == the pending generation (F2/F4 monotonic contract).
    return _st([], generation=gen, extra={"handoff_pending": {"generation": gen, "next_issue": nxt}})


class TestOpenHandoff:
    def test_persists_generation_and_pending_F2(self):
        s = _st([_iss(1, "merged"), _iss(2, "queued")], mode="fresh-session", generation=4)
        disp = dl.fresh_session_handoff(s, mode="fresh-session")
        new = dl.open_handoff(s, disp, now_ts=1000)
        assert new["generation"] == disp["generation"] == 5  # F2: counter advanced
        assert new["handoff_pending"] == {"generation": 5, "next_issue": 2, "written_ts": 1000}

    def test_non_ready_unchanged(self):
        s = _st([_iss(1, "merged")], mode="fresh-session")
        assert dl.open_handoff(s, {"outcome": "complete"}, now_ts=1) is s


class TestHandoffClaim:
    def test_claim_matching_generation(self):
        ok, new = dl.handoff_claim(_pending(5), 5, claimant="sess-A", now_ts=100)
        assert ok is True and new["handoff_claim"]["generation"] == 5
        assert new["handoff_claim"]["claimant"] == "sess-A" and new["handoff_claim"]["started"] is False

    def test_live_second_claim_rejected_singleton(self):
        s = _pending(5)
        ok1, s1 = dl.handoff_claim(s, 5, claimant="A", now_ts=100)
        ok2, s2 = dl.handoff_claim(s1, 5, claimant="B", now_ts=200)  # within lease, unstarted
        assert ok1 is True and ok2 is False  # exactly-one-successor

    def test_started_claim_never_reclaimed(self):
        s = _pending(5)
        _, s1 = dl.handoff_claim(s, 5, claimant="A", now_ts=100)
        _, s2 = dl.handoff_ack_started(s1, 5, "A")
        ok, _ = dl.handoff_claim(s2, 5, claimant="B", now_ts=100 + 10**9)  # far past lease
        assert ok is False  # a started (successful) takeover is never reclaimed

    def test_crashed_claim_reclaimable_after_lease_F3(self):
        s = _pending(5)
        _, s1 = dl.handoff_claim(s, 5, claimant="A", now_ts=100)  # claimed, never started
        ok, s2 = dl.handoff_claim(s1, 5, claimant="B", now_ts=100 + 1801)  # past 1800s lease
        assert ok is True and s2["handoff_claim"]["claimant"] == "B"  # F3: crashed successor reclaimed

    def test_stale_greater_generation_rejected_F4(self):
        # F4: a corrupt state where handoff_claimed/generation is out of sync must not replay.
        s = _st([], generation=4, extra={"handoff_pending": {"generation": 5, "next_issue": 7}})
        ok, _ = dl.handoff_claim(s, 5, claimant="A", now_ts=100)
        assert ok is False  # pending(5) != current generation(4) → reject

    def test_negative_generation_rejected(self):
        s = _st([], generation=-1, extra={"handoff_pending": {"generation": -1, "next_issue": 7}})
        ok, _ = dl.handoff_claim(s, -1, claimant="A", now_ts=100)
        assert ok is False

    def test_no_pending_not_claimed(self):
        ok, new = dl.handoff_claim(_st([], generation=1), 1, claimant="A", now_ts=100)
        assert ok is False


def test_validate_tolerates_fresh_session_fields():
    # #569 T2: session_mode/generation/handoff_pending/handoff_claimed are additive optional
    # top-level fields — a fresh-session state validates unchanged (backward-compatible schema).
    s = _st([_iss(1, "merged")], mode="fresh-session", generation=2,
            extra={"handoff_pending": {"generation": 2, "next_issue": 3}, "handoff_claimed": 1})
    ok, errs = dl.validate_driver_state(s)
    assert ok is True and errs == []


# --------------------------------------------------------------------------- #
# #665: mid-child handoff — the interactive, context-driven case #569 does not cover
# --------------------------------------------------------------------------- #

def _position(**over):
    """A complete mid-child position. Every field is required, so the base is complete and
    each test removes or corrupts exactly one thing."""
    p = {
        "issue": 665,
        "step": "8",
        "branch": "feat/665-mid-child-handoff",
        "test_baseline": "5362 passed, 21 skipped, 0 failed, exit 0",
        "predecessor_pane": "w1:p1",
        "predecessor_session": "aaaaaaaa-1111-2222-3333-444444444444",
        "goal_condition": "PR open with green CI, or a blocker is posted via the ERROR protocol",
        "project": "rawgentic",
        "project_path": "./projects/rawgentic",
        "repo_root": "/home/x/rawgentic/projects/rawgentic",
    }
    p.update(over)
    return p


class TestValidateMidChildPosition:
    def test_complete_position_validates(self):
        ok, errors = dl.validate_mid_child_position(_position())
        assert (ok, errors) == (True, [])

    @pytest.mark.parametrize("field", [
        "issue", "step", "branch", "test_baseline", "predecessor_pane",
        "predecessor_session", "goal_condition", "project", "project_path", "repo_root",
    ])
    def test_every_field_is_required(self, field):
        p = _position()
        del p[field]
        ok, errors = dl.validate_mid_child_position(p)
        assert ok is False and any(field in e for e in errors)

    def test_empty_string_is_not_a_value(self):
        ok, errors = dl.validate_mid_child_position(_position(branch="   "))
        assert ok is False and any("branch" in e for e in errors)

    def test_issue_must_be_an_int_and_not_a_bool(self):
        assert dl.validate_mid_child_position(_position(issue="665"))[0] is False
        # bool is an int subclass in Python; a True issue number is nonsense.
        assert dl.validate_mid_child_position(_position(issue=True))[0] is False

    def test_non_dict_is_refused(self):
        ok, errors = dl.validate_mid_child_position("nope")
        assert ok is False and errors


class TestMidChildHandoff:
    def test_ready_carries_the_in_progress_child_and_bumps_the_generation(self):
        s = _st([_iss(1, "merged"), _iss(665, "in_progress"), _iss(2, "queued")], generation=4)
        disp = dl.mid_child_handoff(s, position=_position())
        assert disp["outcome"] == "ready"
        assert disp["next_issue"] == 665          # the ACTIVE child, not the next queued one
        assert disp["generation"] == 5
        assert disp["kind"] == dl.MID_CHILD_HANDOFF_KIND
        assert disp["position"]["branch"] == "feat/665-mid-child-handoff"
        assert disp["resume_prompt"]

    def test_no_active_child(self):
        s = _st([_iss(1, "merged"), _iss(2, "queued")], generation=4)
        assert dl.mid_child_handoff(s, position=_position())["outcome"] == "no_active_child"

    def test_invalid_position_reports_errors(self):
        s = _st([_iss(665, "in_progress")], generation=4)
        p = _position()
        del p["goal_condition"]
        disp = dl.mid_child_handoff(s, position=p)
        assert disp["outcome"] == "invalid_position" and disp["errors"]

    def test_position_for_a_different_issue_is_a_mismatch(self):
        s = _st([_iss(665, "in_progress")], generation=4)
        disp = dl.mid_child_handoff(s, position=_position(issue=612))
        assert disp["outcome"] == "position_mismatch" and disp["errors"]

    def test_does_not_require_fresh_session_mode(self):
        """A context-driven handover is cron-free (D-16) and must work for a campaign that
        loops in-session — gating on FRESH_SESSION_MODE would refuse exactly the runs it serves."""
        s = _st([_iss(665, "in_progress")], generation=1)   # no session_mode key at all
        assert dl.mid_child_handoff(s, position=_position())["outcome"] == "ready"

    def test_resume_prompt_names_branch_step_and_baseline_and_carries_the_marker(self):
        s = _st([_iss(665, "in_progress")], generation=4, extra={"epic": 667})
        disp = dl.mid_child_handoff(s, position=_position())
        prompt = disp["resume_prompt"]
        assert "feat/665-mid-child-handoff" in prompt
        assert "5362 passed" in prompt
        assert "#665" in prompt and "667" in prompt
        assert dl.mid_child_marker(665, 5) in prompt

    def test_marker_is_generation_bound(self):
        """The marker is the prompt_landed evidence, so it must not match a PRIOR handoff's
        prompt sitting in the same transcript."""
        assert dl.mid_child_marker(665, 5) != dl.mid_child_marker(665, 6)


class TestOpenHandoffCarriesMidChildFields:
    def test_position_and_kind_are_persisted(self):
        s = _st([_iss(665, "in_progress")], generation=4)
        disp = dl.mid_child_handoff(s, position=_position())
        new = dl.open_handoff(s, disp, now_ts=1000)
        pend = new["handoff_pending"]
        assert pend["generation"] == 5 and pend["next_issue"] == 665
        assert pend["kind"] == dl.MID_CHILD_HANDOFF_KIND
        assert pend["position"]["predecessor_session"] == _position()["predecessor_session"]

    def test_shape_is_byte_identical_when_no_position_is_supplied(self):
        """The #569 contract must not shift under this change: a child-boundary disposition
        carries no position, and its written record keeps exactly the three original keys."""
        s = _st([_iss(1, "merged"), _iss(2, "queued")], mode="fresh-session", generation=4)
        disp = dl.fresh_session_handoff(s, mode="fresh-session")
        new = dl.open_handoff(s, disp, now_ts=1000)
        assert new["handoff_pending"] == {"generation": 5, "next_issue": 2, "written_ts": 1000}

    def test_claim_and_ack_work_against_a_mid_child_pending_record(self):
        """The reuse AC1 demands: the SAME #569 primitives operate on the mid-child record."""
        s = _st([_iss(665, "in_progress")], generation=4)
        new = dl.open_handoff(s, dl.mid_child_handoff(s, position=_position()), now_ts=1000)
        ok, claimed = dl.handoff_claim(new, 5, claimant="succ-1", now_ts=1100)
        assert ok is True
        ok2, acked = dl.handoff_ack_started(claimed, 5, "succ-1")
        assert ok2 is True and acked["handoff_claim"]["started"] is True
        # the position survives claim + ack untouched
        assert acked["handoff_pending"]["position"]["branch"] == "feat/665-mid-child-handoff"


class TestTheResumePromptBindsFirst:
    """#682: `perform_handoff` gives a fresh successor `SWITCH_POLL_ATTEMPTS (40) x
    SWITCH_POLL_DELAY_S (3.0)` = 120 s to bind and append to the session registry, then declares
    `failed_step: project_switched` and CLOSES ITS PANE. Observed live three times (epic #667 UAT,
    check L2). The observation is CONFOUNDED and the issue says so — a synthetic issue 99998 sent the
    successor investigating a nonexistent issue — but the unconfounded concern stands: the budget
    assumes the bind comes first and nothing enforced it.

    **This class is shaped by three review passes, each of which refuted the previous attempt:**

    1. A bare `/rawgentic:switch` does not bind at all — the skill enters LIST MODE and waits for a
       human, so the first version of the fix would not have fixed anything.
    2. A keyword-position classifier is unsound: it refused "Do not run git fetch before binding.
       FIRST run /rawgentic:switch rawgentic" and accepted "First read the handoff; then switch".
    3. Substring matching accepted `rawgentic-next` for `rawgentic`, and an optional `project`
       degraded the check to "has any argument", which accepts the English word after a bare
       directive.

    So the contract is now a PREFIX check with an EXACT token compare and a REQUIRED project — no
    classification of prose anywhere.
    """

    def test_the_canonical_prompt_opens_with_the_bind(self):
        s = _st([_iss(682, "queued")], generation=1, extra={"epic": 684, "project": "rawgentic"})
        prompt = dl.fresh_session_handoff(s, mode=dl.FRESH_SESSION_MODE)["resume_prompt"]
        assert prompt.startswith("/rawgentic:switch rawgentic")
        assert dl.resume_prompt_binds_first(prompt, project="rawgentic")[0]

    def test_the_mid_child_prompt_opens_with_its_marker_then_the_bind(self):
        """The marker must stay first — it is the `prompt_landed` evidence — so the prefix check
        skips exactly one marker and no more."""
        s = _st([_iss(665, "in_progress")], generation=4, extra={"epic": 667})
        prompt = dl.mid_child_handoff(s, position=_position())["resume_prompt"]
        assert prompt.startswith(dl.mid_child_marker(665, 5))
        assert dl.resume_prompt_binds_first(prompt, project=_position()["project"])[0], prompt

    def test_a_bare_bind_is_refused_with_the_list_mode_reason(self):
        ok, why = dl.resume_prompt_binds_first("/rawgentic:switch then work.", project="rawgentic")
        assert not ok and ("list mode" in why or "not the expected project" in why)

    def test_a_prompt_that_reads_before_binding_is_refused(self):
        """The case every softer rule accepted, and the reason the check is a prefix: 'bind
        eventually' is exactly the ordering that burns the budget."""
        ok, why = dl.resume_prompt_binds_first(
            "Read the handoff first, then run /rawgentic:switch rawgentic.", project="rawgentic")
        assert not ok and "does not OPEN with" in why

    def test_a_prefix_sharing_project_is_refused(self):
        """Substring matching bound the successor to the WRONG project: `/rawgentic:switch
        rawgentic-next` satisfied a `find()` for `/rawgentic:switch rawgentic`."""
        ok, why = dl.resume_prompt_binds_first(
            "/rawgentic:switch rawgentic-next — go.", project="rawgentic")
        assert not ok and "rawgentic-next" in why

    def test_switch_off_is_not_a_bind(self):
        """`/rawgentic:switch off <name>` DEACTIVATES a project — it satisfies a naive
        directive-plus-argument rule while doing the opposite of binding."""
        assert not dl.resume_prompt_binds_first("/rawgentic:switch off rawgentic.",
                                               project="rawgentic")[0]

    def test_the_project_is_required_not_optional(self):
        """An optional project degraded the check to "has any argument", which accepts the English
        word after a bare directive — a guard admitting the defect it exists to stop."""
        ok, why = dl.resume_prompt_binds_first("/rawgentic:switch rawgentic.", project=None)
        assert not ok and "no valid project name was supplied" in why

    @pytest.mark.parametrize("bad", ["", "   ", "../etc", "a b", "x" * 70, "-lead", None, 42])
    def test_a_project_name_must_look_like_a_project_name(self, bad):
        """The name is interpolated into prompt text sent to a pane with `send-text`, and was
        validated only as "a non-empty string" — so control characters or instruction-like prose
        could ride in. Step-11 provenance finding."""
        assert not dl.valid_project_name(bad)

    def test_ordinary_project_names_are_accepted(self):
        for good in ("rawgentic", "rawgentic-next", "3dstories-studio", "chore_board", "a"):
            assert dl.valid_project_name(good), good

    def test_a_non_string_prompt_is_refused_rather_than_crashing(self):
        for bad in (None, 42, [], {}):
            assert not dl.resume_prompt_binds_first(bad, project="rawgentic")[0]

    def test_a_handoff_with_no_project_never_reaches_ready(self):
        """Step-11 finding: the guard used to run inside `perform_handoff`, i.e. AFTER the contract
        has the predecessor call `open_handoff` — which bumps `generation` and writes
        `handoff_pending`. A refusal there stranded an unclaimed generation that every retry refused
        identically. Refusing at DISPOSITION time means nothing is persisted at all."""
        s = _st([_iss(682, "queued")], generation=1, extra={"epic": 684}, project=None)
        disp = dl.fresh_session_handoff(s, mode=dl.FRESH_SESSION_MODE)
        assert disp["outcome"] == "no_project" and disp["errors"]
        # `open_handoff` acts only on "ready", so there is nothing to roll back.
        assert dl.open_handoff(s, disp, now_ts=1000) == s

    def test_an_explicit_project_overrides_a_stateless_campaign(self):
        s = _st([_iss(682, "queued")], generation=1, extra={"epic": 684}, project=None)
        disp = dl.fresh_session_handoff(s, mode=dl.FRESH_SESSION_MODE, project="rawgentic")
        assert disp["outcome"] == "ready"
        assert dl.resume_prompt_binds_first(disp["resume_prompt"], project="rawgentic")[0]

    def test_the_prompt_still_carries_every_instruction_it_used_to(self):
        """A reviewer checked this by hand; pinning it so a future reword cannot quietly drop one."""
        s = _st([_iss(682, "queued")], generation=1, extra={"epic": 684, "project": "rawgentic"})
        prompt = dl.fresh_session_handoff(s, mode=dl.FRESH_SESSION_MODE)["resume_prompt"]
        for needle in ("durable state", "never in-context memory", "merged/closed child",
                       "auth grant", "ERROR comment", "git fetch origin", "#682"):
            if needle == "#682":
                continue
            assert needle in prompt, needle


class TestIncludeBindLeavesThePromptForTheHerdrPath:
    """#694: the herdr handoff sends `/rawgentic:switch <project>` as its OWN turn, gated on the
    session-registry row, so the prompt it delivers must not bind as well — a second bind makes the
    successor run the switch skill twice.

    This does NOT retract #682. The bind still leads on every path that delivers exactly ONE prompt
    (the interactive hand-back and the `claude -p` fallback launch have no second send to put it in),
    which is why `include_bind` defaults to True. The flag exists so the ONE caller with a second
    send can use it.
    """

    def test_the_default_still_binds_first_on_both_builders(self):
        """The regression that matters most: a default flipped to False would silently strand every
        single-prompt path at an unbound session, which cannot Read under projects/ at all."""
        s = _st([_iss(682, "queued")], generation=1, extra={"epic": 684, "project": "rawgentic"})
        fresh = dl.fresh_session_handoff(s, mode=dl.FRESH_SESSION_MODE)["resume_prompt"]
        assert dl.resume_prompt_binds_first(fresh, project="rawgentic")[0]
        mid = dl.mid_child_handoff(
            _st([_iss(665, "in_progress")], generation=4, extra={"epic": 667}),
            position=_position())["resume_prompt"]
        assert dl.resume_prompt_binds_first(mid, project=_position()["project"])[0]

    def test_include_bind_false_removes_the_directive_entirely(self):
        s = _st([_iss(682, "queued")], generation=1, extra={"epic": 684, "project": "rawgentic"})
        prompt = dl.fresh_session_handoff(
            s, mode=dl.FRESH_SESSION_MODE, include_bind=False)["resume_prompt"]
        assert dl.BIND_DIRECTIVE not in prompt
        assert not dl.resume_prompt_binds_first(prompt, project="rawgentic")[0]

    def test_the_mid_child_marker_still_leads_without_the_bind(self):
        """The marker is the `prompt_landed` evidence, so it must survive the bind's removal — and
        it must still be FIRST, because that is what the launcher matches."""
        s = _st([_iss(665, "in_progress")], generation=4, extra={"epic": 667})
        prompt = dl.mid_child_handoff(
            s, position=_position(), include_bind=False)["resume_prompt"]
        assert prompt.startswith(dl.mid_child_marker(665, 5))
        assert dl.BIND_DIRECTIVE not in prompt

    @pytest.mark.parametrize("include_bind", [True, False])
    def test_no_instruction_is_lost_either_way(self, include_bind):
        """The two shapes share one body, so a reword cannot drop an instruction from only one of
        them — which is exactly how the two builders drifted before."""
        s = _st([_iss(682, "queued")], generation=1, extra={"epic": 684, "project": "rawgentic"})
        prompt = dl.fresh_session_handoff(
            s, mode=dl.FRESH_SESSION_MODE, include_bind=include_bind)["resume_prompt"]
        for needle in ("durable state", "never in-context memory", "merged/closed child",
                       "auth grant", "ERROR comment", "git fetch origin"):
            assert needle in prompt, needle

    def test_the_bindless_prompt_still_reads_as_a_sentence(self):
        """`_lead_with_bind` capitalises the body when it is no longer a continuation of the bind
        clause — a prompt opening with a stray lowercase fragment is a wording defect that reaches
        a live successor."""
        s = _st([_iss(682, "queued")], generation=1, extra={"epic": 684, "project": "rawgentic"})
        prompt = dl.fresh_session_handoff(
            s, mode=dl.FRESH_SESSION_MODE, include_bind=False)["resume_prompt"]
        assert prompt[0].isupper(), prompt[:40]
        assert not prompt.startswith("THEN")

    def test_a_project_is_still_required_when_the_prompt_does_not_bind(self):
        """The launcher builds SEND 1 from the project name, so dropping the bind from the prompt
        must not drop the requirement — refusing at disposition time is what keeps `open_handoff`
        from writing an unclaimable generation (#682 Step-11)."""
        s = _st([_iss(682, "queued")], generation=1, extra={"epic": 684}, project=None)
        disp = dl.fresh_session_handoff(s, mode=dl.FRESH_SESSION_MODE, include_bind=False)
        assert disp["outcome"] == "no_project", disp


class TestCliRefusalStub:
    """#905: driver_lib invoked as a CLI must refuse loudly (rc 2), never exit 0 silently.

    A silent rc-0 `driver_lib.py next-child` invocation was read as a passing gate while the
    real gate (`launcher_lib.py next-child`) was refusing with rc 6 — observed live 2026-08-04.
    Black-box via subprocess, exactly as a shell would invoke it (docs/testing.md philosophy).
    """

    CLI = str(HOOKS_DIR / "driver_lib.py")

    def _run(self, *args):
        return subprocess.run([sys.executable, self.CLI, *args],
                              capture_output=True, text=True, timeout=30)

    def test_cli_invocation_with_args_refuses_rc2_on_stderr(self):
        proc = self._run("next-child", "--driver-state", "nope.json")
        assert proc.returncode == 2, f"expected loud refusal rc 2, got {proc.returncode}"
        assert "pure library" in proc.stderr
        # Pin the FULL remediation string, not just the filename — a regression that
        # dropped the `hooks/` path component must fail here (runner review finding).
        assert "python3 hooks/launcher_lib.py <subcommand>" in proc.stderr
        assert proc.stdout == ""  # nothing success-shaped on stdout for a gate to misread

    def test_bare_cli_invocation_refuses_the_same_way(self):
        proc = self._run()
        assert proc.returncode == 2
        assert "pure library" in proc.stderr
        assert proc.stdout == ""

    def test_importing_the_module_stays_silent(self):
        # Characterization pin (green from the start): the stub must not add any
        # import-time behavior — consumers import driver_lib in every session.
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import driver_lib" % str(HOOKS_DIR)],
            capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0
        assert proc.stdout == ""
        assert proc.stderr == ""


# ------------------------------------------------ #927 transport model + migration

class TestCampaignTransport:
    """#927: `session_mode` becomes a PREFERENCE, and the legacy field migrates on READ.

    The migration is the highest-risk piece of this issue: a wrong mapping silently changes the
    boundary behaviour of every campaign already on disk, and nothing would fail loudly.
    """

    def test_a_recorded_preference_wins(self) -> None:
        st = {"preferred_transport": "pane_chain"}
        assert dl.campaign_transport(st) == ("pane_chain", "recorded")

    def test_legacy_fresh_session_migrates_to_pane_chain(self) -> None:
        assert dl.campaign_transport({"session_mode": "fresh-session"}) == (
            "pane_chain", "migrated")

    def test_legacy_single_session_migrates_to_inline(self) -> None:
        assert dl.campaign_transport({"session_mode": "single-session"}) == (
            "inline", "migrated")

    def test_neither_field_is_the_legacy_default_only(self) -> None:
        """`inline` here is for a PRE-EXISTING campaign carrying neither field.

        It must never be how a NEW campaign is defaulted — that is the creation contract, and
        conflating the two is the AC-1 regression this issue exists to prevent.
        """
        assert dl.campaign_transport({}) == ("inline", "legacy_default")

    def test_the_canonical_field_wins_over_a_disagreeing_legacy_field(self) -> None:
        st = {"preferred_transport": "inline", "session_mode": "fresh-session"}
        assert dl.campaign_transport(st) == ("inline", "recorded")

    def test_an_unrecognised_legacy_value_is_never_guessed(self) -> None:
        transport, provenance = dl.campaign_transport({"session_mode": "sideways"})
        assert transport == "inline"
        assert provenance == "unrecognized"

    def test_an_unrecognised_CANONICAL_value_is_also_refused(self) -> None:
        transport, provenance = dl.campaign_transport({"preferred_transport": "teleport"})
        assert transport == "inline"
        assert provenance == "unrecognized"

    def test_a_non_dict_state_does_not_raise(self) -> None:
        assert dl.campaign_transport(None) == ("inline", "legacy_default")

    def test_the_transport_vocabulary_is_closed(self) -> None:
        assert dl.TRANSPORTS == frozenset({"pane_chain", "inline"})
        assert dl.PANE_CHAIN_TRANSPORT == "pane_chain"
        assert dl.INLINE_TRANSPORT == "inline"

    def test_the_legacy_mapping_is_exhaustive_over_the_old_vocabulary(self) -> None:
        """Both legacy values must map, or a campaign silently lands on the default."""
        for legacy in ("fresh-session", "single-session"):
            transport, provenance = dl.campaign_transport({"session_mode": legacy})
            assert provenance == "migrated", f"{legacy} did not migrate"
            assert transport in dl.TRANSPORTS

    def test_resolution_is_PURE_and_never_writes(self) -> None:
        """Migration materialises on the next locked write, never on a read path."""
        st = {"session_mode": "fresh-session"}
        before = json.dumps(st, sort_keys=True)
        dl.campaign_transport(st)
        assert json.dumps(st, sort_keys=True) == before


class TestTransportProjection:
    """The write-only compatibility projection that keeps a rolled-back build correct."""

    def test_pane_chain_projects_to_the_legacy_fresh_session(self) -> None:
        assert dl.legacy_session_mode("pane_chain") == "fresh-session"

    def test_inline_projects_to_the_legacy_single_session(self) -> None:
        assert dl.legacy_session_mode("inline") == "single-session"

    def test_the_projection_round_trips_through_the_resolver(self) -> None:
        """The two directions must agree, or a rollback executes the wrong transport."""
        for transport in sorted(dl.TRANSPORTS):
            projected = dl.legacy_session_mode(transport)
            back, provenance = dl.campaign_transport({"session_mode": projected})
            assert (back, provenance) == (transport, "migrated")

    def test_an_unknown_transport_has_no_projection(self) -> None:
        assert dl.legacy_session_mode("teleport") is None


class TestTransitionLog:
    """#927: the effect of a transition is TWO immutable events, never one mutable field.

    A single record carrying `outcome` would have to be written before the action that
    determines the outcome -- so it would be invented early or mutated later, and an
    append-only record you mutate is neither. Splitting them keeps both immutable and makes
    "a resolution with no terminal event" the crash signature recovery keys on.
    """

    def _resolved(self, st, **kw):
        kw.setdefault("transition_id", "b:camp:3")
        kw.setdefault("generation", 3)
        kw.setdefault("trigger", "child_boundary")
        kw.setdefault("kind", "child_boundary")
        kw.setdefault("preferred", "pane_chain")
        kw.setdefault("effective", "pane_chain")
        kw.setdefault("probe_reason", "probe_ok")
        kw.setdefault("probe_ms", 12)
        kw.setdefault("pane_ref", "w1:aaa")
        kw.setdefault("panes_before", ["w1:aaa"])
        kw.setdefault("now_ts", 1000)
        return dl.append_resolution(st, **kw)

    def test_a_resolution_lands_before_any_action(self) -> None:
        st = {}
        rid = self._resolved(st)
        rec = dl.transition_events(st)[0]
        assert rec["resolution_id"] == rid
        assert rec["successor_pane"] is None, "nothing has been launched yet"
        assert rec["split_attempted"] is False

    def test_the_resolution_id_correlates_the_two_events(self) -> None:
        st = {}
        rid = self._resolved(st)
        dl.append_terminal_outcome(st, resolution_id=rid, outcome="successor_acked", now_ts=1001)
        terminals = [e for e in dl.transition_events(st) if e.get("outcome")]
        assert len(terminals) == 1
        assert terminals[0]["resolution_id"] == rid

    def test_the_attempt_is_part_of_the_resolution_id(self) -> None:
        """A reclaim is a second ATTEMPT at the same transition and must not collide."""
        st = {}
        first = self._resolved(st, attempt=1)
        second = self._resolved(st, attempt=2)
        assert first != second
        assert first.startswith("b:camp:3#")
        assert second.startswith("b:camp:3#")

    def test_a_resolution_with_no_terminal_event_is_the_crash_signature(self) -> None:
        st = {}
        rid = self._resolved(st)
        assert dl.unterminated_resolutions(st) == [rid]
        dl.append_terminal_outcome(st, resolution_id=rid, outcome="inline_continued", now_ts=2)
        assert dl.unterminated_resolutions(st) == []

    def test_the_terminal_vocabulary_is_closed(self) -> None:
        """#927 PR 2 added `no_split_attempted`, and this guard is why that had to be deliberate.

        It exists so a new outcome cannot be introduced by accident: `no_split_attempted` splits
        "herdr was unlistable, nothing was attempted" out of `launch_failed`, because §16.4's
        downgrade must be able to tell an OBSERVATION failure from a creation REFUSAL.
        """
        assert dl.TERMINAL_OUTCOMES == frozenset({
            "successor_acked", "inline_continued", "launch_failed", "no_split_attempted",
            "start_failed", "reconciled_no_action", "created", "parked_unreconcilable"})

    def test_launch_indeterminate_is_NOT_a_terminal_outcome(self) -> None:
        """It was, in an earlier draft, while other sections required the same transition to
        stay reclaimable -- so an implementation treating terminals as closed would strand it
        forever. An indeterminate launch is the ABSENCE of a terminal event."""
        assert "launch_indeterminate" not in dl.TERMINAL_OUTCOMES
        st = {}
        rid = self._resolved(st)
        with pytest.raises(dl.DriverStateError):
            dl.append_terminal_outcome(st, resolution_id=rid,
                                       outcome="launch_indeterminate", now_ts=2)

    def test_an_unknown_outcome_is_refused(self) -> None:
        st = {}
        rid = self._resolved(st)
        with pytest.raises(dl.DriverStateError):
            dl.append_terminal_outcome(st, resolution_id=rid, outcome="vibes", now_ts=2)

    def test_a_terminal_event_for_an_unknown_resolution_is_refused(self) -> None:
        with pytest.raises(dl.DriverStateError):
            dl.append_terminal_outcome({}, resolution_id="b:camp:9#1",
                                       outcome="created", now_ts=2)

    def test_the_successor_pane_amendment_is_the_only_permitted_in_place_write(self) -> None:
        st = {}
        rid = self._resolved(st)
        dl.mark_split_attempted(st, resolution_id=rid)
        assert dl.resolution(st, rid)["split_attempted"] is True
        dl.record_successor_pane(st, resolution_id=rid, pane="w1:bbb")
        assert dl.resolution(st, rid)["successor_pane"] == "w1:bbb"

    def test_split_attempted_lands_BEFORE_the_pane_id(self) -> None:
        """The ordering that makes `null` unambiguous.

        split_attempted False + successor_pane None means the split was never called, which is
        the only state that authorises a relaunch. If the marker landed after the split, a crash
        in between would look identical to "never started" and could launch a second successor
        beside a live one -- the Critical this ordering exists to remove.
        """
        st = {}
        rid = self._resolved(st)
        rec = dl.resolution(st, rid)
        assert (rec["split_attempted"], rec["successor_pane"]) == (False, None)
        dl.mark_split_attempted(st, resolution_id=rid)
        rec = dl.resolution(st, rid)
        assert (rec["split_attempted"], rec["successor_pane"]) == (True, None), (
            "the indeterminate window is representable")

    def test_panes_before_is_carried_so_a_diff_is_possible_later(self) -> None:
        st = {}
        rid = self._resolved(st, panes_before=["w1:aaa", "w1:bbb"])
        assert sorted(dl.resolution(st, rid)["panes_before"]) == ["w1:aaa", "w1:bbb"]

    def test_events_are_appended_never_rewritten(self) -> None:
        st = {}
        first = self._resolved(st)
        dl.append_terminal_outcome(st, resolution_id=first,
                                   outcome="parked_unreconcilable", now_ts=2)
        before = len(dl.transition_events(st))
        second = self._resolved(st, attempt=2)
        dl.append_terminal_outcome(st, resolution_id=second,
                                   outcome="successor_acked", now_ts=3)
        events = dl.transition_events(st)
        assert len(events) == before + 2
        assert any(e.get("outcome") == "parked_unreconcilable" for e in events), (
            "the parked event survives a later attempt")

    def test_a_validated_state_tolerates_the_transition_log(self) -> None:
        st = {"schema_version": 1, "campaign": "camp", "issues": []}
        self._resolved(st)
        ok, errors = dl.validate_driver_state(st)
        assert ok, list(errors)


class TestChildBoundaryFence:
    """#845, folded into #927: the child boundary gets the fence mid-child already has.

    Deliberately NOT by adding a `kind` (D232). `_refuse_foreign_kind` documents that this entry
    point handles only the boundary handoff "which carries no kind at all" and refuses ANY kind
    with rc 3, and the record's three-key shape is pinned. The paths are already separate by the
    absent-kind convention; what the boundary LACKS is the claim, so that is what is added.
    """

    def _st(self, **kw):
        base = {"schema_version": 1, "campaign": "camp",
                "issues": [{"number": 7, "status": "queued"},
                           {"number": 8, "status": "queued"}]}
        base.update(kw)
        return base

    def test_a_queued_next_child_with_none_in_flight_satisfies_the_precondition(self) -> None:
        ok, reason = dl.child_boundary_precondition(self._st(), next_issue=7)
        assert ok is True
        assert reason == "ready"

    def test_a_child_in_flight_is_the_MID_CHILD_case_not_this_one(self) -> None:
        st = self._st(issues=[{"number": 7, "status": "in_progress"},
                              {"number": 8, "status": "queued"}])
        ok, reason = dl.child_boundary_precondition(st, next_issue=8)
        assert ok is False
        assert reason == "child_in_flight"

    def test_a_next_child_that_is_not_queued_is_refused(self) -> None:
        st = self._st(issues=[{"number": 7, "status": "merged"}])
        ok, reason = dl.child_boundary_precondition(st, next_issue=7)
        assert ok is False
        assert reason == "next_child_not_queued"

    def test_an_unknown_next_child_is_refused(self) -> None:
        ok, reason = dl.child_boundary_precondition(self._st(), next_issue=999)
        assert ok is False
        assert reason == "next_child_not_queued"

    def test_the_fence_refuses_a_second_caller_on_one_generation(self) -> None:
        """The whole point: two boundary calls, one successor."""
        st = self._st(generation=4,
                      handoff_pending={"generation": 4, "next_issue": 7, "written_ts": 10})
        first, claimed = dl.handoff_claim(st, 4, claimant="pane-a", now_ts=100)
        assert first is True
        # `handoff_claim` is PURE — the claim lives in the RETURNED state, not in `st`.
        assert dl.handoff_claim_blocked_by_live_claim(
            claimed, 4, now_ts=101, lease_s=1800) is True
        second, _ = dl.handoff_claim(claimed, 4, claimant="pane-b", now_ts=101)
        assert second is False, "a second claimant must not also launch a successor"

    def test_an_expired_lease_becomes_reclaimable(self) -> None:
        st = self._st(generation=4,
                      handoff_pending={"generation": 4, "next_issue": 7, "written_ts": 10})
        _ok, claimed = dl.handoff_claim(st, 4, claimant="pane-a", now_ts=100)
        assert dl.handoff_claim_blocked_by_live_claim(
            claimed, 4, now_ts=100 + 1801, lease_s=1800) is False

    def test_opening_a_boundary_handoff_twice_reuses_the_generation(self) -> None:
        """Idempotent open: a competing invocation gets the SAME generation, not a second one."""
        st = self._st(generation=4)
        disp = {"outcome": "ready", "generation": 5, "next_issue": 7}
        once = dl.open_handoff(st, disp, now_ts=10)
        twice = dl.open_handoff(once, disp, now_ts=11)
        assert once["generation"] == twice["generation"] == 5
        assert twice["handoff_pending"]["next_issue"] == 7

    def test_the_boundary_record_still_carries_NO_kind(self) -> None:
        """D232: the absent-kind convention is what keeps the two paths apart. Preserve it."""
        st = self._st(generation=4)
        new = dl.open_handoff(st, {"outcome": "ready", "generation": 5, "next_issue": 7},
                              now_ts=10)
        assert "kind" not in new["handoff_pending"], (
            "an explicit kind would make _refuse_foreign_kind reject the boundary's own record")

    def test_the_mid_child_precondition_is_untouched_by_this_helper(self) -> None:
        """The boundary precondition must not become a way to bypass the mid-child one."""
        st = self._st(issues=[{"number": 7, "status": "in_progress"}])
        ok, _ = dl.child_boundary_precondition(st, next_issue=7)
        assert ok is False


class TestBoundaryReconciliation:
    """#927: the Critical, enforced. `null` must NEVER be read as "nothing was created".

    An earlier draft let a crash between `pane split` returning and the amendment landing leave
    `successor_pane: null`, and treated that as proof no successor existed -- authorising a
    relaunch beside a live pane. The fix is ordering plus an inventory diff, and these tests are
    what hold it.
    """

    def _rec(self, **kw):
        base = {"resolution_id": "b:camp:3#1", "panes_before": ["w1:anchor", "w1:old"],
                "split_attempted": False, "successor_pane": None}
        base.update(kw)
        return base

    def test_a_split_never_attempted_permits_a_relaunch(self) -> None:
        verdict, reason = dl.reconcile_boundary(
            self._rec(), fresh_panes={"w1:anchor", "w1:old"},
            panes_with_agents=set(), anchor_pane="w1:anchor")
        assert verdict == "relaunch_permitted"
        assert reason == "never_started"

    def test_an_INDETERMINATE_split_with_a_new_pane_REFUSES_a_relaunch(self) -> None:
        """The Critical. split_attempted=True + null must not authorise a second successor."""
        verdict, reason = dl.reconcile_boundary(
            self._rec(split_attempted=True),
            fresh_panes={"w1:anchor", "w1:old", "w1:orphan"},
            panes_with_agents={"w1:orphan"}, anchor_pane="w1:anchor")
        assert verdict != "relaunch_permitted", (
            "a pane appeared after panes_before — relaunching would make two successors")
        assert verdict == "park"
        assert reason == "indeterminate_pane_appeared"

    def test_an_indeterminate_split_with_NO_new_pane_permits_a_relaunch(self) -> None:
        """Proven by diff, not assumed from a null."""
        verdict, reason = dl.reconcile_boundary(
            self._rec(split_attempted=True), fresh_panes={"w1:anchor", "w1:old"},
            panes_with_agents=set(), anchor_pane="w1:anchor")
        assert verdict == "relaunch_permitted"
        assert reason == "diff_proves_nothing_created"

    def test_the_anchor_is_excluded_from_the_diff(self) -> None:
        """The predecessor's own pane must never look like a successor."""
        verdict, _ = dl.reconcile_boundary(
            self._rec(split_attempted=True, panes_before=["w1:old"]),
            fresh_panes={"w1:anchor", "w1:old"},
            panes_with_agents={"w1:anchor"}, anchor_pane="w1:anchor")
        assert verdict == "relaunch_permitted"

    def test_a_recorded_successor_that_is_alive_and_running_is_adopted(self) -> None:
        verdict, reason = dl.reconcile_boundary(
            self._rec(split_attempted=True, successor_pane="w1:new"),
            fresh_panes={"w1:anchor", "w1:old", "w1:new"},
            panes_with_agents={"w1:new"}, anchor_pane="w1:anchor")
        assert verdict == "adopt_successor"
        assert reason == "successor_alive"

    def test_a_pane_with_NO_agent_is_start_failed_not_a_live_successor(self) -> None:
        """`pane split` succeeding does not mean `agent start` did.

        Acking an empty pane as a running successor would stall the campaign forever with
        nothing to notice it.
        """
        verdict, reason = dl.reconcile_boundary(
            self._rec(split_attempted=True, successor_pane="w1:new"),
            fresh_panes={"w1:anchor", "w1:old", "w1:new"},
            panes_with_agents=set(), anchor_pane="w1:anchor")
        assert verdict == "start_failed"
        assert reason == "pane_without_agent"

    def test_a_recorded_successor_that_died_permits_a_relaunch(self) -> None:
        verdict, reason = dl.reconcile_boundary(
            self._rec(split_attempted=True, successor_pane="w1:new"),
            fresh_panes={"w1:anchor", "w1:old"},
            panes_with_agents=set(), anchor_pane="w1:anchor")
        assert verdict == "relaunch_permitted"
        assert reason == "successor_gone"

    def test_an_unreadable_inventory_REFUSES_to_relaunch(self) -> None:
        """A stalled run a human can restart beats two successors nobody notices."""
        for record in (self._rec(), self._rec(split_attempted=True),
                       self._rec(split_attempted=True, successor_pane="w1:new")):
            verdict, reason = dl.reconcile_boundary(
                record, fresh_panes=None, panes_with_agents=None, anchor_pane="w1:anchor")
            assert verdict == "park"
            assert reason == "inventory_unreadable"

    def test_a_missing_panes_before_is_treated_as_unprovable(self) -> None:
        """No baseline means no diff is possible, so nothing can be PROVEN absent."""
        verdict, reason = dl.reconcile_boundary(
            self._rec(split_attempted=True, panes_before=None),
            fresh_panes={"w1:anchor"}, panes_with_agents=set(), anchor_pane="w1:anchor")
        assert verdict == "park"
        assert reason == "no_baseline_to_diff"

    def test_every_verdict_is_from_the_closed_set(self) -> None:
        assert dl.RECONCILE_VERDICTS == frozenset(
            {"relaunch_permitted", "adopt_successor", "start_failed", "park"})


class TestTransportSetGuard:
    """#927 AC 2: an in-flight campaign's preference can be changed by a sanctioned command."""

    def test_a_quiet_campaign_permits_the_change(self) -> None:
        st = {"issues": [{"number": 7, "status": "queued"}]}
        assert dl.transport_set_blocked(st, now_ts=100) == (False, "ready")

    def test_a_child_in_flight_refuses(self) -> None:
        """A mode flip while a child runs is the mid-child-handoff case, not this one."""
        st = {"issues": [{"number": 7, "status": "in_progress"}]}
        blocked, reason = dl.transport_set_blocked(st, now_ts=100)
        assert blocked is True
        assert reason == "child_in_flight"

    def test_a_LIVE_CLAIM_also_refuses(self) -> None:
        """Not just a child -- a boundary mid-launch must not have the answer changed under it."""
        st = {"issues": [], "generation": 4,
              "handoff_pending": {"generation": 4, "next_issue": 7, "written_ts": 1},
              "handoff_claim": {"generation": 4, "claimant": "pane-a",
                                "claimed_at": 100, "started": False}}
        blocked, reason = dl.transport_set_blocked(st, now_ts=101)
        assert blocked is True
        assert reason == "handoff_claim_active"

    def test_an_EXPIRED_claim_no_longer_refuses(self) -> None:
        st = {"issues": [], "generation": 4,
              "handoff_pending": {"generation": 4, "next_issue": 7, "written_ts": 1},
              "handoff_claim": {"generation": 4, "claimant": "pane-a",
                                "claimed_at": 100, "started": False}}
        assert dl.transport_set_blocked(st, now_ts=100 + 1801)[0] is False


class TestUnparkGuard:
    """#927: unparking is a command, not hand-editing driver-state."""

    def _parked(self):
        st = {}
        rid = dl.append_resolution(
            st, transition_id="b:camp:3", generation=3, trigger="child_boundary",
            kind="child_boundary", preferred="pane_chain", effective="pane_chain",
            probe_reason="probe_ok", probe_ms=5, pane_ref="w1:a",
            panes_before=["w1:a"], now_ts=1)
        dl.append_terminal_outcome(st, resolution_id=rid,
                                   outcome="parked_unreconcilable", now_ts=2)
        return st, rid

    def test_a_parked_resolution_may_be_unparked(self) -> None:
        st, rid = self._parked()
        assert dl.unpark_blocked(st, resolution_id=rid) == (False, "ready")

    def test_a_resolution_that_is_not_parked_is_refused(self) -> None:
        st = {}
        rid = dl.append_resolution(
            st, transition_id="b:camp:3", generation=3, trigger="child_boundary",
            kind="child_boundary", preferred="inline", effective="inline",
            probe_reason="probe_ok", probe_ms=5, pane_ref=None,
            panes_before=[], now_ts=1)
        dl.append_terminal_outcome(st, resolution_id=rid,
                                   outcome="inline_continued", now_ts=2)
        blocked, reason = dl.unpark_blocked(st, resolution_id=rid)
        assert blocked is True
        assert reason == "not_parked"

    def test_an_unknown_resolution_is_refused(self) -> None:
        assert dl.unpark_blocked({}, resolution_id="nope#1")[0] is True

    def test_unparking_APPENDS_and_never_rewrites_the_parked_event(self) -> None:
        st, rid = self._parked()
        before = len(dl.transition_events(st))
        dl.append_unpark(st, resolution_id=rid, outcome="reconciled_no_action",
                         operator="owner", reason="pane was debris", now_ts=9)
        events = dl.transition_events(st)
        assert len(events) == before + 1
        assert any(e.get("outcome") == "parked_unreconcilable" for e in events), (
            "the parked event must survive as the audit record")
        assert events[-1]["outcome"] == "reconciled_no_action"
        assert events[-1]["operator"] == "owner"

    def test_unparking_refuses_an_outcome_outside_the_closed_set(self) -> None:
        st, rid = self._parked()
        with pytest.raises(dl.DriverStateError):
            dl.append_unpark(st, resolution_id=rid, outcome="vibes",
                             operator="owner", reason="x", now_ts=9)


class TestBoundaryAdvisory:
    """#927 AC 4: an inline boundary is VISIBLE, and says so exactly once."""

    def test_the_line_names_the_preference_and_the_reason(self) -> None:
        line = dl.boundary_advisory_line(preferred="pane_chain", effective="inline",
                                         reason="herdr_unreachable")
        assert "inline" in line
        assert "pane_chain" in line
        assert "herdr_unreachable" in line

    def test_a_pane_chain_boundary_has_nothing_to_advise(self) -> None:
        assert dl.boundary_advisory_line(preferred="pane_chain", effective="pane_chain",
                                         reason="probe_ok") is None

    def test_the_advisory_never_echoes_probe_output(self) -> None:
        """Only a fixed reason token. Raw daemon output must never reach a terminal."""
        line = dl.boundary_advisory_line(
            preferred="pane_chain", effective="inline",
            reason="probe_unparseable") or ""
        assert "\x1b" not in line and "\n" not in line.strip()

    def test_it_fires_once_per_transition_not_once_per_generation(self) -> None:
        """`creation` and `boundary_resume` do not bump a generation and would share a key."""
        seen = set()
        assert dl.advisory_due("b:camp:3#1", seen) is True
        seen.add("b:camp:3#1")
        assert dl.advisory_due("b:camp:3#1", seen) is False
        assert dl.advisory_due("r:camp:3:2#1", seen) is True


class TestStep11Fixes:
    """Regressions for the ten findings the pre-PR cross-model review raised. Each names its own."""

    def _rec(self, **kw):
        base = {"resolution_id": "b:camp:3#1", "panes_before": ["w1:anchor"],
                "split_attempted": False, "successor_pane": None}
        base.update(kw)
        return base

    def test_f3_a_MISSING_split_marker_does_not_authorise_a_relaunch(self) -> None:
        """Only an explicit False proves the split was never called.

        A corrupt or partially-written resolution has no marker; absence of evidence is not
        evidence of absence, and relaunching on it could put a second successor beside a live one.
        """
        rec = self._rec()
        del rec["split_attempted"]
        verdict, _ = dl.reconcile_boundary(
            rec, fresh_panes={"w1:anchor", "w1:mystery"},
            panes_with_agents=set(), anchor_pane="w1:anchor")
        assert verdict != "relaunch_permitted"

    def test_f3_a_malformed_split_marker_is_not_a_false(self) -> None:
        verdict, _ = dl.reconcile_boundary(
            self._rec(split_attempted="no"), fresh_panes={"w1:anchor", "w1:mystery"},
            panes_with_agents=set(), anchor_pane="w1:anchor")
        assert verdict != "relaunch_permitted"

    def test_f4_unknown_agent_state_parks_rather_than_declaring_start_failed(self) -> None:
        """An unreadable agent inventory must not be coerced to "no agent"."""
        verdict, reason = dl.reconcile_boundary(
            self._rec(split_attempted=True, successor_pane="w1:new"),
            fresh_panes={"w1:anchor", "w1:new"}, panes_with_agents=None,
            anchor_pane="w1:anchor")
        assert verdict == "park"
        assert reason == "agent_state_unknown"

    def test_f5_a_duplicate_resolution_id_is_refused(self) -> None:
        st = {}
        kw = dict(transition_id="b:camp:3", generation=3, trigger="child_boundary",
                  kind="child_boundary", preferred="pane_chain", effective="pane_chain",
                  probe_reason="probe_ok", probe_ms=1, pane_ref=None, panes_before=[],
                  now_ts=1)
        dl.append_resolution(st, attempt=1, **kw)
        with pytest.raises(dl.DriverStateError):
            dl.append_resolution(st, attempt=1, **kw)

    def test_f7_unparking_twice_is_refused(self) -> None:
        """`_terminal_for` must report the LATEST outcome, not the original park."""
        st = {}
        rid = dl.append_resolution(
            st, transition_id="b:camp:3", generation=3, trigger="child_boundary",
            kind="child_boundary", preferred="pane_chain", effective="pane_chain",
            probe_reason="probe_ok", probe_ms=1, pane_ref=None, panes_before=[], now_ts=1)
        dl.append_terminal_outcome(st, resolution_id=rid,
                                   outcome="parked_unreconcilable", now_ts=2)
        assert dl.unpark_blocked(st, resolution_id=rid) == (False, "ready")
        dl.append_unpark(st, resolution_id=rid, outcome="reconciled_no_action",
                         operator="owner", reason="debris", now_ts=3)
        blocked, reason = dl.unpark_blocked(st, resolution_id=rid)
        assert blocked is True
        assert reason == "not_parked", "a resolved park must not accept a second decision"

    def test_f9_the_transport_guard_fails_CLOSED_without_readable_state(self) -> None:
        assert dl.transport_set_blocked(None, now_ts=1)[0] is True
        assert dl.transport_set_blocked({}, now_ts=1)[0] is True
        assert dl.transport_set_blocked({"issues": "not a list"}, now_ts=1)[0] is True


# --- #927 PR 2 — the pure additions the boundary wiring needs -------------------------------
#
# Design authority: docs/planning/2026-08-05-927-epic-run-transport-rework.md §16.
# F1 (Step-4 pass 2) is why `handoff_claim_release` exists at all: NOTHING in this module
# released a claim, so an inline continuation left one live for its full 1800 s lease.


def _boundary_state(*, generation=4, claimant="sess-A", terminal=None):
    """A campaign mid-boundary: a claim held, a resolution appended, optionally closed."""
    state = {
        "campaign": "epic-1", "epic": 1, "generation": generation,
        "issues": [{"number": 10, "status": "queued", "depends_on": []}],
        "handoff_claim": {"generation": generation, "claimant": claimant,
                          "claimed_at": 1000, "started": False},
    }
    rid = dl.append_resolution(
        state, transition_id=f"b:epic-1:{generation}", generation=generation,
        trigger="child_boundary", kind="child_boundary", preferred="pane_chain",
        effective="pane_chain", probe_reason="probe_ok", probe_ms=12,
        pane_ref="w1:pAA", panes_before=["w1:pAA"], now_ts=1001)
    if terminal is not None:
        dl.append_terminal_outcome(state, resolution_id=rid, outcome=terminal, now_ts=1002)
    return state, rid


def test_no_split_attempted_is_a_terminal_outcome():
    """§16.3: 'herdr was unlistable' must not share a name with 'creation was refused'."""
    assert "no_split_attempted" in dl.TERMINAL_OUTCOMES
    state, rid = _boundary_state()
    dl.append_terminal_outcome(state, resolution_id=rid, outcome="no_split_attempted",
                               now_ts=1003)
    assert dl._terminal_for(state, rid)["outcome"] == "no_split_attempted"


def test_handoff_claim_release_clears_a_matching_closed_claim():
    state, _rid = _boundary_state(terminal="inline_continued")
    released, new = dl.handoff_claim_release(state, 4, claimant="sess-A")
    assert released is True
    assert new.get("handoff_claim") is None
    assert state.get("handoff_claim") is not None, "PURE — the input must not be mutated"


def test_handoff_claim_release_refuses_while_the_resolution_is_still_open():
    """An INDETERMINATE launch must keep its lease: the lease is what protects a live successor."""
    state, _rid = _boundary_state(terminal=None)
    released, new = dl.handoff_claim_release(state, 4, claimant="sess-A")
    assert released is False
    assert new is state or new.get("handoff_claim") is not None


def test_handoff_claim_release_refuses_a_foreign_claimant_and_generation():
    state, _rid = _boundary_state(terminal="successor_acked")
    assert dl.handoff_claim_release(state, 4, claimant="sess-B")[0] is False
    assert dl.handoff_claim_release(state, 9, claimant="sess-A")[0] is False
    assert state["handoff_claim"]["claimant"] == "sess-A"


def test_handoff_claim_release_on_a_state_with_no_claim_is_a_no_op():
    released, new = dl.handoff_claim_release({"generation": 1}, 1, claimant="x")
    assert released is False
    assert "handoff_claim" not in new


def test_claim_advisory_is_one_shot_per_transition_id():
    """§16.7: the check and the record happen in ONE mutation, so two callers cannot both print."""
    state = {"campaign": "epic-1"}
    claimed, state = dl.claim_advisory(state, "b:epic-1:4", now_ts=10)
    assert claimed is True
    again, state = dl.claim_advisory(state, "b:epic-1:4", now_ts=11)
    assert again is False
    other, state = dl.claim_advisory(state, "b:epic-1:5", now_ts=12)
    assert other is True


def test_claim_advisory_records_pending_then_a_delivery_state():
    state = {"campaign": "epic-1"}
    _claimed, state = dl.claim_advisory(state, "b:epic-1:4", now_ts=10)
    pending = dl.advisory_deliveries(state)
    assert [e["state"] for e in pending] == ["pending"]
    state = dl.record_advisory_delivery(state, transition_id="b:epic-1:4", delivered=True,
                                        now_ts=11)
    assert [e["state"] for e in dl.advisory_deliveries(state)] == ["pending", "emitted"]


def test_a_failed_print_leaves_a_failed_delivery_and_is_observable():
    """AC 4 as at-most-once printing: a `pending` with no terminal state IS the visible defect."""
    state = {"campaign": "epic-1"}
    _c, state = dl.claim_advisory(state, "b:epic-1:4", now_ts=10)
    state = dl.record_advisory_delivery(state, transition_id="b:epic-1:4", delivered=False,
                                        now_ts=11)
    assert dl.advisory_deliveries(state)[-1]["state"] == "failed"
    assert dl.undelivered_advisories(state) == ["b:epic-1:4"]
    state2 = {"campaign": "epic-1"}
    _c2, state2 = dl.claim_advisory(state2, "b:epic-1:9", now_ts=10)
    assert dl.undelivered_advisories(state2) == ["b:epic-1:9"], "a bare pending counts as undelivered"


def test_validate_operator_note_caps_length_and_rejects_control_characters():
    """S2 (self-review): operator text lands in a durable audit record."""
    assert dl.validate_operator_note("adopting w1:pAB", what="reason") == "adopting w1:pAB"
    with pytest.raises(dl.DriverStateError):
        dl.validate_operator_note("x" * 201, what="reason")
    with pytest.raises(dl.DriverStateError):
        dl.validate_operator_note("line\x1b[31m", what="reason")
    with pytest.raises(dl.DriverStateError):
        dl.validate_operator_note("", what="operator")


def test_with_boundary_clause_carries_the_resolution_id_and_never_a_launch_token():
    """§4.5 / pass-3 finding C6: the design once demanded a `launch_token` pass 3 had deleted."""
    out = dl.with_boundary_clause("fresh-session resume for epic #871: git fetch origin",
                                  generation=4, claimant="sess-A", kind="child_boundary",
                                  resolution_id="b:epic-871:4#1")
    assert out.startswith("fresh-session resume for epic #871"), "APPENDED — the bind stays first"
    assert "resolution b:epic-871:4#1" in out
    assert "generation 4" in out and "claim sess-A" in out
    assert "task list back up" in out
    assert "launch_token" not in out
    with pytest.raises(dl.DriverStateError):
        dl.with_boundary_clause("  ", generation=1, claimant="x", kind="child_boundary",
                                resolution_id="r")


def test_inline_mode_advisory_names_the_recorded_preference_as_the_reason():
    """AC 4's OTHER half: `next-child` returning ready under an inline campaign is a CHOICE, and
    an operator must see it rather than infer it from silence. `boundary_advisory_line` cannot
    express it — there is no degradation, preferred and effective agree."""
    line = dl.inline_mode_advisory_line(preferred="inline", provenance="recorded", next_issue=612)
    assert line is not None
    assert "transport=inline" in line and "#612" in line and "recorded" in line
    assert dl.inline_mode_advisory_line(preferred="pane_chain", provenance="recorded",
                                        next_issue=612) is None, \
        "a pane_chain campaign is not making this choice"


# --- #927 PR 2, Step-11 findings F2 + F4: the replay hole the per-generation claim never closed ---


def _campaign_with_boundary(*, outcome=None, next_issue=10, consumed=None):
    state = {"campaign": "epic-1", "epic": 1, "generation": 4,
             "issues": [{"number": next_issue, "status": "queued", "depends_on": []}]}
    if consumed is not None:
        state["boundary_consumed"] = consumed
    if outcome is not None:
        rid = dl.append_resolution(
            state, transition_id="b:epic-1:4", generation=4, trigger="child_boundary",
            kind="child_boundary", preferred="pane_chain", effective="pane_chain",
            probe_reason="probe_ok", probe_ms=1, pane_ref="w1:pA", panes_before=["w1:pA"],
            now_ts=1)
        if outcome != "OPEN":
            dl.append_terminal_outcome(state, resolution_id=rid, outcome=outcome, now_ts=2)
    return state


def test_a_boundary_may_not_open_while_another_is_still_in_flight():
    """Step-11 F4, CONFIRMED against the code: `handoff_claim_blocked_by_live_claim` returns False
    whenever the claim's generation differs from the one being claimed, `handoff_claim_is_live` is
    scoped to the CURRENT generation, and `open_handoff` never consults the claim at all (#846).
    So a second invocation that reads state AFTER the first claimed derives generation+1 and claims
    it unopposed — two successors. The per-generation claim cannot see that; this refusal can."""
    state = _campaign_with_boundary(outcome="OPEN")
    ok, why = dl.child_boundary_precondition(state, 10)
    assert ok is False
    assert why == "boundary_in_flight"


def test_a_terminated_boundary_does_not_block_the_next_one():
    state = _campaign_with_boundary(outcome="inline_continued")
    assert dl.child_boundary_precondition(state, 10) == (True, "ready")


def test_a_consumed_boundary_refuses_a_replay_for_the_same_child():
    """Step-11 F2: after a successful launch the claim is RELEASED and the child is still `queued`
    until the successor marks it in_progress. Without this, a second invocation in that window
    passes every check and launches a second successor."""
    state = _campaign_with_boundary(outcome="successor_acked",
                                    consumed={"issue": 10, "generation": 4})
    ok, why = dl.child_boundary_precondition(state, 10)
    assert ok is False
    assert why == "boundary_already_consumed"


def test_a_consumed_boundary_for_a_DIFFERENT_child_is_not_a_refusal():
    state = _campaign_with_boundary(outcome="successor_acked",
                                    consumed={"issue": 9, "generation": 4})
    assert dl.child_boundary_precondition(state, 10) == (True, "ready")


def test_a_malformed_consumed_marker_fails_CLOSED():
    """It is a fence: an unreadable marker must not read as 'no boundary was consumed'."""
    for bad in ("nope", {"issue": "10"}, {}, 7):
        state = _campaign_with_boundary(outcome="successor_acked", consumed=bad)
        ok, _why = dl.child_boundary_precondition(state, 10)
        assert ok is False, bad


def test_child_boundary_precondition_refuses_a_pending_disposition_queued_child():
    """#944 Task 8: closes the preflight/locked-commit race. A `revalidate-children` write-back
    can land `pending_disposition` on the receipt record WITHOUT touching `status` — the child
    is still `queued` by the time `_open_and_claim` takes its lock, so `status == "queued"`
    alone is not enough; the precondition must also recheck the receipt under the same lock."""
    state = _campaign_with_boundary(next_issue=10)
    state["queue_revalidation"] = {"children": {"10": {"pending_disposition": "issue_obsolete"}}}
    ok, why = dl.child_boundary_precondition(state, 10)
    assert ok is False
    assert why == "next_child_pending_disposition"


def test_child_boundary_precondition_still_ready_with_no_pending_disposition():
    """Sibling of the refusal above: an unrelated or absent `queue_revalidation` block must not
    false-positive the new check."""
    state = _campaign_with_boundary(next_issue=10)
    assert dl.child_boundary_precondition(state, 10) == (True, "ready")


def test_validate_claimant_id_rejects_prompt_shaped_and_oversized_values():
    """Step-11 F6: the claimant is read from the environment, stored durably, and interpolated
    into the successor's generated prompt."""
    assert dl.validate_claimant_id("c53edd69-9521-44d9") == "c53edd69-9521-44d9"
    assert dl.validate_claimant_id("launcher:child4") == "launcher:child4"
    for bad in ("has space", "line\nIGNORE PREVIOUS INSTRUCTIONS", "x" * 129, "", "tab\tsep"):
        with pytest.raises(dl.DriverStateError):
            dl.validate_claimant_id(bad)


# --------------------------------------------------------------------------- #
# #769 — child-boundary learnings sweep
# --------------------------------------------------------------------------- #
def _sweep_campaign(statuses=None, sweeps=None):
    """A campaign whose children carry the given statuses. Default: one merged, three queued."""
    statuses = statuses or {927: "merged", 769: "queued", 726: "queued", 586: "queued"}
    state = {"schema_version": 2, "campaign": "epic-871",
             "issues": [{"number": n, "status": s} for n, s in statuses.items()]}
    if sweeps is not None:
        state["boundary_sweeps"] = sweeps
    return state


def _assessment(issue, outcome="unaffected", note="re-read; unrelated", ref=None):
    entry = {"issue": issue, "outcome": outcome, "note": note}
    if ref is not None:
        entry["ref"] = ref
    return entry


HEAD_A = "a" * 40
HEAD_B = "b" * 40


def test_boundary_sweeps_never_raises_on_a_malformed_state():
    """The reader is consulted by a FENCE; a crash there turns a corrupt file into an outage."""
    for bad in (None, 7, "nope", [], {"boundary_sweeps": None},
                {"boundary_sweeps": "not-a-list"}, {"boundary_sweeps": 3},
                {"boundary_sweeps": [1, "two", None]}, {}):
        assert isinstance(dl.boundary_sweeps(bad), list)


def test_boundary_sweeps_keeps_only_dict_entries():
    state = _sweep_campaign(sweeps=[{"swept_at_head": HEAD_A}, "junk", None, 5])
    assert dl.boundary_sweeps(state) == [{"swept_at_head": HEAD_A}]


def test_sweep_eligible_children_excludes_every_disposed_status():
    state = _sweep_campaign({1: "merged", 2: "deferred", 3: "abandoned",
                             4: "queued", 5: "in_progress", 6: "pr_open"})
    assert dl.sweep_eligible_children(state) == [4, 5, 6]


def test_sweep_eligible_children_tolerates_a_stateless_campaign():
    assert dl.sweep_eligible_children({}) == []
    assert dl.sweep_eligible_children({"issues": "nope"}) == []


def test_record_boundary_sweep_writes_a_record_covering_every_eligible_child():
    state = _sweep_campaign()
    new = dl.record_boundary_sweep(
        state, after_issue=927, swept_at_head=HEAD_A, learnings="#927 rewrote the boundary prose",
        assessments=[_assessment(769), _assessment(726), _assessment(586)], now_ts=1000)
    assert len(dl.boundary_sweeps(new)) == 1
    record = dl.boundary_sweeps(new)[0]
    assert record["swept_at_head"] == HEAD_A
    assert record["after_issue"] == 927
    assert record["observed_at"] == 1000
    assert dl.boundary_sweeps(state) == []          # PURE: the input is untouched


def test_record_boundary_sweep_refuses_a_missing_eligible_child():
    """Coverage is set EQUALITY. This is the whole mechanically-checkable claim of the design."""
    state = _sweep_campaign()
    with pytest.raises(dl.DriverStateError):
        dl.record_boundary_sweep(state, after_issue=927, swept_at_head=HEAD_A, learnings="x",
                                 assessments=[_assessment(769), _assessment(726)], now_ts=1)


def test_record_boundary_sweep_refuses_a_foreign_child():
    state = _sweep_campaign()
    with pytest.raises(dl.DriverStateError):
        dl.record_boundary_sweep(
            state, after_issue=927, swept_at_head=HEAD_A, learnings="x",
            assessments=[_assessment(769), _assessment(726), _assessment(586),
                         _assessment(4242)], now_ts=1)


def test_record_boundary_sweep_refuses_a_duplicate_assessment():
    state = _sweep_campaign()
    with pytest.raises(dl.DriverStateError):
        dl.record_boundary_sweep(
            state, after_issue=927, swept_at_head=HEAD_A, learnings="x",
            assessments=[_assessment(769), _assessment(769), _assessment(726),
                         _assessment(586)], now_ts=1)


def test_record_boundary_sweep_refuses_an_unknown_outcome():
    state = _sweep_campaign()
    with pytest.raises(dl.DriverStateError):
        dl.record_boundary_sweep(
            state, after_issue=927, swept_at_head=HEAD_A, learnings="x",
            assessments=[_assessment(769, outcome="blocked", ref="https://x/1"),
                         _assessment(726), _assessment(586)], now_ts=1)


def test_record_boundary_sweep_requires_a_ref_when_a_child_changed():
    state = _sweep_campaign()
    for outcome in ("commented", "rescoped"):
        with pytest.raises(dl.DriverStateError):
            dl.record_boundary_sweep(
                state, after_issue=927, swept_at_head=HEAD_A, learnings="x",
                assessments=[_assessment(769, outcome=outcome), _assessment(726),
                             _assessment(586)], now_ts=1)


def test_record_boundary_sweep_checks_the_ref_GRAMMAR_not_merely_its_presence():
    """Step-4 pass-3 Medium: `ref` exists to point at an artifact, so "done" must not satisfy it."""
    state = _sweep_campaign()
    for bad in ("done", "see the comment", "ftp://x/1", "/etc/passwd", "../escape"):
        with pytest.raises(dl.DriverStateError):
            dl.record_boundary_sweep(
                state, after_issue=927, swept_at_head=HEAD_A, learnings="x",
                assessments=[_assessment(769, outcome="commented", ref=bad),
                             _assessment(726), _assessment(586)], now_ts=1)
    for good in ("https://github.com/o/r/issues/1#issuecomment-2",
                 "docs/planning/x.md", "claude_docs/session_notes.md"):
        assert dl.record_boundary_sweep(
            state, after_issue=927, swept_at_head=HEAD_A, learnings="x",
            assessments=[_assessment(769, outcome="commented", ref=good),
                         _assessment(726), _assessment(586)], now_ts=1) is not None


def test_record_boundary_sweep_refuses_empty_learnings_or_note():
    state = _sweep_campaign()
    with pytest.raises(dl.DriverStateError):
        dl.record_boundary_sweep(state, after_issue=927, swept_at_head=HEAD_A, learnings="  ",
                                 assessments=[_assessment(769), _assessment(726),
                                              _assessment(586)], now_ts=1)
    with pytest.raises(dl.DriverStateError):
        dl.record_boundary_sweep(
            state, after_issue=927, swept_at_head=HEAD_A, learnings="x",
            assessments=[_assessment(769, note=""), _assessment(726), _assessment(586)], now_ts=1)


def test_record_boundary_sweep_bounds_every_operator_string_including_ref():
    """Self-review High: the draft guarded `note` and left `ref` — equally durable, equally rendered."""
    state = _sweep_campaign()
    for kwargs in ({"learnings": "x" * 5000},
                   {"assessments": [_assessment(769, note="n" * 5000), _assessment(726),
                                    _assessment(586)]},
                   {"assessments": [_assessment(769, outcome="commented",
                                                ref="https://x/" + "y" * 5000),
                                    _assessment(726), _assessment(586)]},
                   {"learnings": "bad\x1b[31mescape"},
                   {"assessments": [_assessment(769, note="tab\tsep"), _assessment(726),
                                    _assessment(586)]}):
        call = {"after_issue": 927, "swept_at_head": HEAD_A, "learnings": "x",
                "assessments": [_assessment(769), _assessment(726), _assessment(586)],
                "now_ts": 1}
        call.update(kwargs)
        with pytest.raises(dl.DriverStateError):
            dl.record_boundary_sweep(state, **call)


def test_record_boundary_sweep_refuses_a_malformed_head():
    state = _sweep_campaign()
    for bad in ("abc", "A" * 40, True, None, 7, HEAD_A[:39]):
        with pytest.raises(dl.DriverStateError):
            dl.record_boundary_sweep(
                state, after_issue=927, swept_at_head=bad, learnings="x",
                assessments=[_assessment(769), _assessment(726), _assessment(586)], now_ts=1)


def test_after_issue_accepts_null_for_a_head_move_with_no_completion():
    """Self-review High: PR #949 moved main between children with nothing completing."""
    state = _sweep_campaign()
    new = dl.record_boundary_sweep(
        state, after_issue=None, swept_at_head=HEAD_A,
        learnings="main moved via an unplanned blocker fix; no child completed",
        assessments=[_assessment(769), _assessment(726), _assessment(586)], now_ts=1)
    assert dl.boundary_sweeps(new)[0]["after_issue"] is None


def test_after_issue_must_name_a_DISPOSED_child_of_this_queue():
    """Adversarial High: an unvalidated provenance field let any number open the gate."""
    state = _sweep_campaign()
    for bad in (4242, 769, "927", True, 0.5):     # foreign, still-active, wrong type
        with pytest.raises(dl.DriverStateError):
            dl.record_boundary_sweep(
                state, after_issue=bad, swept_at_head=HEAD_A, learnings="x",
                assessments=[_assessment(769), _assessment(726), _assessment(586)], now_ts=1)


def test_an_exact_replay_writes_nothing_even_with_a_different_clock():
    """Replay equality is SEMANTIC: `observed_at` is excluded, assessments are order-insensitive."""
    state = _sweep_campaign()
    first = dl.record_boundary_sweep(
        state, after_issue=927, swept_at_head=HEAD_A, learnings="x",
        assessments=[_assessment(769), _assessment(726), _assessment(586)], now_ts=1000)
    assert dl.record_boundary_sweep(
        first, after_issue=927, swept_at_head=HEAD_A, learnings="x",
        assessments=[_assessment(586), _assessment(769), _assessment(726)], now_ts=9999) is None
    assert dl.boundary_sweeps(first)[0]["observed_at"] == 1000   # first record keeps its stamp


def test_a_DIFFERING_replay_at_one_identity_is_refused_so_state_stays_single_valued():
    state = _sweep_campaign()
    first = dl.record_boundary_sweep(
        state, after_issue=927, swept_at_head=HEAD_A, learnings="x",
        assessments=[_assessment(769), _assessment(726), _assessment(586)], now_ts=1)
    with pytest.raises(dl.DriverStateError):
        dl.record_boundary_sweep(
            first, after_issue=927, swept_at_head=HEAD_A, learnings="DIFFERENT",
            assessments=[_assessment(769), _assessment(726), _assessment(586)], now_ts=2)
    assert len(dl.boundary_sweeps(first)) == 1


def test_two_sweeps_at_one_head_with_different_after_issue_are_different_boundaries():
    """Pass-3 High: a deferral moves no commit, so a second boundary can share a head."""
    state = _sweep_campaign({927: "merged", 769: "deferred", 726: "queued", 586: "queued"})
    first = dl.record_boundary_sweep(
        state, after_issue=927, swept_at_head=HEAD_A, learnings="from 927",
        assessments=[_assessment(726), _assessment(586)], now_ts=1)
    second = dl.record_boundary_sweep(
        first, after_issue=769, swept_at_head=HEAD_A, learnings="from the 769 deferral",
        assessments=[_assessment(726), _assessment(586)], now_ts=2)
    assert second is not None and len(dl.boundary_sweeps(second)) == 2


def test_boundary_sweep_status_is_not_due_before_any_child_is_disposed():
    state = _sweep_campaign({769: "queued", 726: "in_progress"})
    assert dl.boundary_sweep_status(state, HEAD_A) == "not_due"


def test_boundary_sweep_status_is_swept_only_at_the_observed_head():
    state = _sweep_campaign()
    new = dl.record_boundary_sweep(
        state, after_issue=927, swept_at_head=HEAD_A, learnings="x",
        assessments=[_assessment(769), _assessment(726), _assessment(586)], now_ts=1)
    assert dl.boundary_sweep_status(new, HEAD_A) == "swept"
    assert dl.boundary_sweep_status(new, HEAD_B) == "missing"


def test_a_sweep_stops_covering_once_another_child_is_disposed_at_the_SAME_head():
    """Pass-3 High, the defect head-as-sole-key created: a second deferral at one head.

    Without the current-eligible-set comparison the stored record still reads `swept`, so the
    second boundary is silently skipped AND its record would be refused as a differing replay.
    """
    state = _sweep_campaign()
    swept = dl.record_boundary_sweep(
        state, after_issue=927, swept_at_head=HEAD_A, learnings="x",
        assessments=[_assessment(769), _assessment(726), _assessment(586)], now_ts=1)
    assert dl.boundary_sweep_status(swept, HEAD_A) == "swept"
    deferred = dict(swept, issues=[dict(i, status="deferred") if i["number"] == 769 else dict(i)
                                   for i in swept["issues"]])
    assert dl.boundary_sweep_status(deferred, HEAD_A) == "missing"


def test_boundary_sweep_status_fails_CLOSED_on_a_malformed_field():
    for bad in ("nope", 7, [1, 2], [{"swept_at_head": "short"}], [{}]):
        state = _sweep_campaign(sweeps=bad)
        assert dl.boundary_sweep_status(state, HEAD_A) == "unreadable"


def test_boundary_sweep_status_never_returns_the_deleted_conflict_status():
    """`conflict` was designed at pass 2 and deleted at pass 3 as unreachable and unrepairable."""
    assert "conflict" not in dl.SWEEP_STATUSES


def test_a_campaign_with_no_sweep_key_is_grandfathered_not_gated():
    """Migration, not a loophole: gating retroactively would refuse `next-child` for every
    campaign in flight at upgrade, over a boundary that is already past and cannot be swept
    honestly now. New campaigns are seeded with [] at creation, so they ARE gated."""
    state = _sweep_campaign()                    # has a merged child, no boundary_sweeps key
    assert "boundary_sweeps" not in state
    assert dl.boundary_sweep_status(state, HEAD_A) == "not_due"


def test_an_EMPTY_sweep_list_opts_the_campaign_IN():
    """The seeded-at-creation value. Present-but-empty means 'gated, and nothing swept yet'."""
    state = _sweep_campaign(sweeps=[])
    assert dl.boundary_sweep_status(state, HEAD_A) == "missing"


def test_an_opted_in_campaign_is_still_not_due_before_the_first_disposal():
    state = _sweep_campaign({769: "queued", 726: "in_progress"}, sweeps=[])
    assert dl.boundary_sweep_status(state, HEAD_A) == "not_due"


# --- Step-11 review fixes ---------------------------------------------------
def test_the_reader_does_not_RAISE_on_an_unhashable_issue_value():
    """Step-11 High: `{"issue": []}` made a set comprehension raise TypeError out of a function
    documented as never raising — a corrupt file became a next-child OUTAGE, not an rc-8 refusal."""
    state = _sweep_campaign(sweeps=[{"swept_at_head": HEAD_A, "learnings": "x",
                                     "assessments": [{"issue": []}]}])
    assert dl.boundary_sweep_status(state, HEAD_A) == "unreadable"


def test_a_record_with_matching_issue_numbers_but_no_evidence_is_NOT_swept():
    """Step-11 High: the fence checked head + issue-number set and nothing else, so a
    hand-written record with no learnings, no outcomes and no notes opened a gate whose whole
    promise is record integrity."""
    hollow = {"swept_at_head": HEAD_A, "assessments": [{"issue": 769}, {"issue": 726},
                                                       {"issue": 586}]}
    assert dl.boundary_sweep_status(_sweep_campaign(sweeps=[hollow]), HEAD_A) == "unreadable"


def test_a_record_missing_a_required_ref_is_not_accepted_by_the_READER_either():
    """Write-path and read-path must agree, or the gate trusts what the writer would refuse."""
    bad = {"swept_at_head": HEAD_A, "after_issue": 927, "learnings": "x",
           "assessments": [{"issue": 769, "outcome": "commented", "note": "n"},
                           {"issue": 726, "outcome": "unaffected", "note": "n"},
                           {"issue": 586, "outcome": "unaffected", "note": "n"}]}
    assert dl.boundary_sweep_status(_sweep_campaign(sweeps=[bad]), HEAD_A) == "unreadable"


def test_one_corrupt_record_makes_the_WHOLE_field_unreadable():
    """Reading around corruption is a fence reporting 'done' it never verified."""
    state = _sweep_campaign()
    good = dl.record_boundary_sweep(
        state, after_issue=927, swept_at_head=HEAD_A, learnings="x",
        assessments=[_assessment(769), _assessment(726), _assessment(586)], now_ts=1)
    good[dl.BOUNDARY_SWEEPS_KEY] = good[dl.BOUNDARY_SWEEPS_KEY] + [{"swept_at_head": "junk"}]
    assert dl.boundary_sweep_status(good, HEAD_A) == "unreadable"


def test_sweep_record_is_intact_is_total_over_arbitrary_garbage():
    for junk in (None, 7, "x", [], {}, {"swept_at_head": HEAD_A},
                 {"swept_at_head": HEAD_A, "learnings": "x", "assessments": {}},
                 {"swept_at_head": HEAD_A, "learnings": "x",
                  "assessments": [{"issue": 1, "outcome": "nope", "note": "n"}]}):
        assert dl.sweep_record_is_intact({}, junk) is False


def test_body_hash_is_not_part_of_the_record():
    """Step-11 Medium: the design claimed every assessment records one, nothing computed it, and
    no example supplied it — an optional field that would always be absent."""
    state = _sweep_campaign()
    new = dl.record_boundary_sweep(
        state, after_issue=927, swept_at_head=HEAD_A, learnings="x",
        assessments=[dict(_assessment(769), body_hash="deadbeef"), _assessment(726),
                     _assessment(586)], now_ts=1)
    assert "body_hash" not in new[dl.BOUNDARY_SWEEPS_KEY][0]["assessments"][0]


# --------------------------------------------------------------------------- #
# _extract_section (#944 — claim-inventory coverage binding, AC1)
# --------------------------------------------------------------------------- #
class TestExtractSection:
    """`_extract_section(lines, heading_re)` -> (items, unclassified). Shared by the `ac` and
    `cause` claim-inventory extraction (design doc §1.3): a heading match, top-level list items
    collected with their wrapped continuations, and — fail-closed — any non-blank content the
    parser could not attribute to a real item, kept SEPARATE from the deliberate free-prose
    fallback (a section with no list at all is not an error; a section that mixes a real list
    with something the parser cannot classify is)."""

    def _lines(self, text):
        return text.split("\n")

    def test_clean_numbered_list(self):
        text = "## Acceptance criteria\n\n1. First thing.\n2. Second thing.\n\n## Next section\n"
        items, unclassified = dl._extract_section(self._lines(text), dl._AC_HEADING_RE)
        assert items == ["First thing.", "Second thing."]
        assert unclassified == []

    def test_wrapped_continuation_lines_join_the_item(self):
        text = ("## Acceptance criteria\n\n"
                "1. First thing that wraps\n"
                "   onto a second physical line.\n"
                "2. Second thing.\n")
        items, unclassified = dl._extract_section(self._lines(text), dl._AC_HEADING_RE)
        assert items == ["First thing that wraps onto a second physical line.", "Second thing."]
        assert unclassified == []

    def test_heading_with_no_list_falls_back_to_one_whole_section_item(self):
        text = "## Root cause\n\nThe cause is a race between two writers.\n\n## Next\n"
        items, unclassified = dl._extract_section(self._lines(text), dl._CAUSE_HEADING_RE)
        assert items == ["The cause is a race between two writers."]
        assert unclassified == []

    def test_list_plus_a_stray_unlisted_paragraph_is_unclassified(self):
        """The round-2 review's finding 1: a section mixing a real list with unattributed
        prose must fail closed, not silently drop the stray paragraph."""
        text = ("## Acceptance criteria\n\n"
                "1. First thing.\n\n"
                "Some extra unlisted requirement floats here.\n\n"
                "2. Second thing.\n")
        items, unclassified = dl._extract_section(self._lines(text), dl._AC_HEADING_RE)
        assert items == ["First thing.", "Second thing."]
        assert unclassified == ["Some extra unlisted requirement floats here."]

    def test_no_heading_at_all_is_empty_with_no_error(self):
        text = "Just some prose with no relevant heading at all.\n"
        items, unclassified = dl._extract_section(self._lines(text), dl._AC_HEADING_RE)
        assert items == []
        assert unclassified == []

    def test_bulleted_list_with_dash_and_star(self):
        text = "## Problem\n\n- First cause.\n* Second cause.\n"
        items, unclassified = dl._extract_section(self._lines(text), dl._CAUSE_HEADING_RE)
        assert items == ["First cause.", "Second cause."]

    def test_a_markdown_checkbox_prefix_is_stripped_from_the_item_text(self):
        """Step-8a review finding 3: `_TOP_LEVEL_LIST_ITEM_RE` captures everything after the
        marker, so `- [ ] X` extracted as `[ ] X` — which then fails EXACT-match claim coverage
        against a claim quoting only `X`. This is the skill's OWN fully-worked example
        (`skills/revalidate-children/SKILL.md`), so the documented step was not executable."""
        text = "## Acceptance criteria\n\n- [ ] X is checked before Y runs.\n"
        items, unclassified = dl._extract_section(self._lines(text), dl._AC_HEADING_RE)
        assert items == ["X is checked before Y runs."]

    def test_checked_and_uppercase_checkbox_variants_are_also_stripped(self):
        text = "## Acceptance criteria\n\n- [x] Done thing.\n- [X] Also done.\n"
        items, unclassified = dl._extract_section(self._lines(text), dl._AC_HEADING_RE)
        assert items == ["Done thing.", "Also done."]

    def test_stops_at_the_next_heading_of_any_level(self):
        text = "## Acceptance criteria\n\n1. Only thing.\n\n### Unrelated subsection\n\nOther stuff.\n"
        items, unclassified = dl._extract_section(self._lines(text), dl._AC_HEADING_RE)
        assert items == ["Only thing."]
        assert unclassified == []

    def test_trailing_content_after_the_last_item_is_not_unclassified(self):
        """Real-body regression: #944's own '## Problem' section has a citation line AFTER its
        two-item cause list, before the next heading. Flagging that would be a false positive
        on the exact fixture this feature exists to handle — only content BETWEEN markers is
        genuinely suspicious."""
        text = ("## Problem\n\n"
                "1. First cause.\n"
                "2. Second cause.\n\n"
                "Design: see the linked doc for details.\n\n"
                "## Acceptance criteria\n")
        items, unclassified = dl._extract_section(self._lines(text), dl._CAUSE_HEADING_RE)
        assert items == ["First cause.", "Second cause."]
        assert unclassified == []

    def test_944s_own_problem_section_is_a_two_item_numbered_list(self):
        """The exact real-world case that drove the round-1 Critical finding: #944's own body
        itemizes two distinct causes under '## Problem'."""
        text = (
            "## Problem\n\n"
            "Two documented holes in the queue-revalidation machinery, both stated in\n"
            "`skills/revalidate-children/SKILL.md` rather than fixed:\n\n"
            "1. **Coverage gap.** The receipt attests that a look happened.\n"
            "2. **The obsolete-child marker gates nothing.** It is informational only.\n\n"
            "## Acceptance criteria\n")
        items, unclassified = dl._extract_section(self._lines(text), dl._CAUSE_HEADING_RE)
        assert len(items) == 2
        assert "Coverage gap" in items[0]
        assert "obsolete-child marker gates nothing" in items[1]
        assert unclassified == []

    def test_a_second_matching_heading_is_aggregated_not_ignored(self):
        """Step-8a review finding 1: `_CAUSE_HEADING_RE` treats 'Problem', 'Root cause' and
        'Cause' as synonyms, but a body can legitimately carry BOTH a '## Problem' section and a
        separate later '## Root cause' section. The original version stopped at the FIRST
        matching heading, so the second section's claims silently never entered the inventory —
        a claim set could omit them entirely and still pass coverage."""
        text = ("## Problem\n\n1. First cause.\n\n"
                "## Root cause\n\n2. Second cause.\n\n"
                "## Acceptance criteria\n")
        items, unclassified = dl._extract_section(self._lines(text), dl._CAUSE_HEADING_RE)
        assert items == ["First cause.", "Second cause."]
        assert unclassified == []

    def test_unclassified_content_in_a_later_matching_section_is_not_lost_either(self):
        state = ("## Problem\n\n1. First.\n\nStray unlisted content.\n\n2. Second.\n\n"
                 "## Root cause\n\n1. Third.\n\nMore stray content.\n\n2. Fourth.\n")
        items, unclassified = dl._extract_section(self._lines(state), dl._CAUSE_HEADING_RE)
        assert items == ["First.", "Second.", "Third.", "Fourth."]
        assert unclassified == ["Stray unlisted content.", "More stray content."]


# --------------------------------------------------------------------------- #
# extract_claim_inventory (#944 — claim-inventory coverage binding, AC1)
# --------------------------------------------------------------------------- #
class TestExtractClaimInventory:
    def test_944s_own_body_end_to_end(self):
        """The primary fixture: #944's own real body — a 2-item numbered Problem list, a
        4-item Acceptance criteria list, and a design-doc citation."""
        body = (
            "## Problem\n\n"
            "Two documented holes, both stated in `skills/revalidate-children/SKILL.md`:\n\n"
            "1. **Coverage gap.** The receipt attests that a look happened.\n"
            "2. **The obsolete-child marker gates nothing.** It is informational only.\n\n"
            "Design: `docs/planning/2026-08-05-871-m4-session-continuity-away-mode.md` §3.4.\n\n"
            "## Acceptance criteria\n\n"
            "1. Claim-inventory coverage binding.\n"
            "2. Obsolete-child owner gate.\n"
            "3. The gate is recoverable.\n"
            "4. Tests cover the above.\n")
        resolves = {"docs/planning/2026-08-05-871-m4-session-continuity-away-mode.md"}
        inv = dl.extract_claim_inventory(body, resolves)
        # Both citations appear — the SKILL.md path is a genuine, UNRESOLVED candidate here
        # (not in `resolves`), exactly the case #944 needs the inventory to see (finding 4).
        assert inv["citation"] == [
            "skills/revalidate-children/SKILL.md",
            "docs/planning/2026-08-05-871-m4-session-continuity-away-mode.md"]
        assert len(inv["cause"]) == 2
        assert len(inv["ac"]) == 4
        assert inv["errors"] == []

    def test_ac_mentioned_but_unparseable_is_an_extraction_error(self):
        """Round-1 fix (finding 2): the bare phrase present with zero structured items
        extracted must fail closed, not silently pass with an empty ac inventory."""
        body = "## What must be true\n\nSee the acceptance criteria discussed on the call.\n"
        inv = dl.extract_claim_inventory(body, resolves=set())
        assert inv["ac"] == []
        assert any("acceptance criteria" in e.lower() for e in inv["errors"])

    def test_ac_genuinely_absent_is_not_an_error(self):
        body = "## Problem\n\nA bug fix with no acceptance-criteria-shaped body at all.\n"
        inv = dl.extract_claim_inventory(body, resolves=set())
        assert inv["ac"] == []
        assert inv["errors"] == []

    def test_a_stray_unlisted_ac_line_is_an_extraction_error(self):
        body = ("## Acceptance criteria\n\n1. First.\n\n"
                "An extra requirement with no list marker.\n\n2. Second.\n")
        inv = dl.extract_claim_inventory(body, resolves=set())
        assert inv["ac"] == ["First.", "Second."]
        assert inv["errors"]

    def test_unresolved_citation_is_present_not_dropped(self):
        body = "See hooks/nonexistent_file.py for the cause."
        inv = dl.extract_claim_inventory(body, resolves=set())
        assert inv["citation"] == ["hooks/nonexistent_file.py"]

    def test_no_headings_at_all_is_a_fully_empty_inventory(self):
        inv = dl.extract_claim_inventory("Just a short bug report with no structure.", set())
        assert inv == {"citation": [], "cause": [], "ac": [], "errors": []}


# --------------------------------------------------------------------------- #
# missing_claim_coverage / claim_coverage_ok (#944 — AC1, maximum bipartite matching)
# --------------------------------------------------------------------------- #
class TestMissingClaimCoverage:
    def _claim(self, kind, quoted, checked="<no-file: reasoning>", verdict="holds"):
        return {"kind": kind, "quoted_from_body": quoted, "checked_against": checked,
                "evidence": "x", "verdict": verdict}

    def test_deep_requires_all_three_kinds(self):
        inventory = {"citation": ["hooks/a.py"], "cause": ["The cause."], "ac": ["The AC."]}
        missing = dl.missing_claim_coverage(inventory, [], "deep")
        assert missing == {"citation": ["hooks/a.py"], "cause": ["The cause."], "ac": ["The AC."]}

    def test_quick_does_not_require_citation(self):
        inventory = {"citation": ["hooks/a.py"], "cause": ["The cause."], "ac": ["The AC."]}
        missing = dl.missing_claim_coverage(inventory, [], "quick")
        assert missing == {"citation": [], "cause": ["The cause."], "ac": ["The AC."]}

    def test_full_coverage_reports_nothing_missing(self):
        inventory = {"citation": ["hooks/a.py"], "cause": ["The cause."], "ac": ["The AC."]}
        claims = [
            self._claim("citation", "hooks/a.py mentioned", checked="hooks/a.py@" + "0" * 40),
            self._claim("cause", "The cause."),
            self._claim("ac", "The AC."),
        ]
        missing = dl.missing_claim_coverage(inventory, claims, "deep")
        assert dl.claim_coverage_ok(missing)

    def test_exact_match_required_for_ac_and_cause_not_substring(self):
        """Round-2 review finding 3: a short generic claim fragment must NOT cover an item it
        is merely a substring of — the field is documented 'verbatim', not 'clipped'."""
        inventory = {"citation": [], "cause": [], "ac": ["The system must validate all input."]}
        claims = [self._claim("ac", "the")]
        missing = dl.missing_claim_coverage(inventory, claims, "deep")
        assert missing["ac"] == ["The system must validate all input."]

    def test_exact_match_tolerates_only_whitespace_and_case_normalization(self):
        inventory = {"citation": [], "cause": [], "ac": ["The AC.  "]}
        claims = [self._claim("ac", "  the ac.")]
        missing = dl.missing_claim_coverage(inventory, claims, "deep")
        assert missing["ac"] == []

    def test_maximum_matching_finds_an_assignment_greedy_would_miss(self):
        """Round-2 review finding 2: a GREEDY first-match can report a false coverage gap when
        a complete matching exists. Citation matching (substring/containment) is where this
        naturally arises: one claim mentions BOTH paths, another mentions only one — processing
        the multi-match item first and greedily taking the shared claim starves the other item,
        even though a valid assignment (swap) covers both."""
        inventory = {"citation": ["hooks/a.py", "hooks/b.py"], "cause": [], "ac": []}
        claims = [
            self._claim("citation", "See hooks/a.py and hooks/b.py, both gone."),
            self._claim("citation", "hooks/a.py was removed."),
        ]
        missing = dl.missing_claim_coverage(inventory, claims, "deep")
        assert missing["citation"] == [], (
            "a complete matching exists (item hooks/a.py -> claim 1, item hooks/b.py -> "
            "claim 0) but a greedy first-match would report hooks/b.py as missing")

    def test_citation_coverage_via_checked_against_prefix_for_resolved_path(self):
        inventory = {"citation": ["hooks/a.py"], "cause": [], "ac": []}
        claims = [self._claim("citation", "irrelevant text", checked="hooks/a.py@" + "1" * 40)]
        missing = dl.missing_claim_coverage(inventory, claims, "deep")
        assert missing["citation"] == []

    def test_citation_coverage_via_quoted_from_body_for_unresolved_path(self):
        inventory = {"citation": ["hooks/ghost.py"], "cause": [], "ac": []}
        claims = [self._claim("citation", "hooks/ghost.py no longer exists")]
        missing = dl.missing_claim_coverage(inventory, claims, "deep")
        assert missing["citation"] == []

    def test_a_claim_of_the_wrong_kind_never_covers_an_item(self):
        inventory = {"citation": [], "cause": [], "ac": ["The AC."]}
        claims = [self._claim("cause", "The AC.")]
        missing = dl.missing_claim_coverage(inventory, claims, "deep")
        assert missing["ac"] == ["The AC."]

    def test_one_to_one_a_single_claim_cannot_cover_two_items(self):
        inventory = {"citation": [], "cause": [], "ac": ["Same text.", "Same text."]}
        claims = [self._claim("ac", "Same text.")]
        missing = dl.missing_claim_coverage(inventory, claims, "deep")
        assert len(missing["ac"]) == 1

    def test_unknown_depth_raises(self):
        with pytest.raises(dl.DriverStateError):
            dl.missing_claim_coverage({"citation": [], "cause": [], "ac": []}, [], "bogus")


# --------------------------------------------------------------------------- #
# validate_claim_coverage / build_revalidation_record (#944 — AC1, demoted primitive)
# --------------------------------------------------------------------------- #
class TestValidateClaimCoverage:
    def _claim(self, kind, quoted, checked="<no-file: reasoning>"):
        return {"kind": kind, "quoted_from_body": quoted, "checked_against": checked,
                "evidence": "x", "verdict": "holds"}

    def test_full_coverage_passes(self):
        body = "## Acceptance criteria\n\n1. The AC.\n"
        dl.validate_claim_coverage(body, set(), [self._claim("ac", "The AC.")], "deep")

    def test_under_coverage_raises_naming_the_missing_item(self):
        body = "## Acceptance criteria\n\n1. The AC.\n"
        with pytest.raises(dl.DriverStateError, match="The AC"):
            dl.validate_claim_coverage(body, set(), [], "deep")

    def test_an_extraction_error_refuses_before_coverage_is_even_computed(self):
        body = "## What must be true\n\nSee the acceptance criteria discussed on the call.\n"
        with pytest.raises(dl.DriverStateError, match="acceptance criteria"):
            dl.validate_claim_coverage(body, set(), [], "deep")


class TestBuildRevalidationRecordCoverage:
    def _claim(self, kind, quoted, checked="<no-file: reasoning>"):
        return {"kind": kind, "quoted_from_body": quoted, "checked_against": checked,
                "evidence": "x", "verdict": "holds"}

    def test_omitting_resolves_raises_loudly(self):
        with pytest.raises(TypeError):
            dl.build_revalidation_record(   # pylint: disable=missing-kwoa
                body="## Acceptance criteria\n\n1. The AC.\n",
                from_sha="a" * 40, to_sha="b" * 40, extraction="none", depth="deep",
                claims=[self._claim("ac", "The AC.")], validated_at=1)

    def test_under_coverage_refuses_construction(self):
        with pytest.raises(dl.DriverStateError):
            dl.build_revalidation_record(
                body="## Acceptance criteria\n\n1. The AC.\n",
                from_sha="a" * 40, to_sha="b" * 40, extraction="none", depth="deep",
                claims=[], validated_at=1, resolves=set())

    def test_full_coverage_succeeds(self):
        record = dl.build_revalidation_record(
            body="## Acceptance criteria\n\n1. The AC.\n",
            from_sha="a" * 40, to_sha="b" * 40, extraction="none", depth="deep",
            claims=[self._claim("ac", "The AC.")], validated_at=1, resolves=set())
        assert record["outcome"] == "still_valid"

    def test_pending_disposition_skips_coverage_entirely(self):
        """A pending-disposition record is not a stamped, selectable outcome (AC2) — full
        inventory coverage does not gate it; it already requires >=1 broken claim under the
        existing coherence rule."""
        record = dl.build_revalidation_record(
            body="## Acceptance criteria\n\n1. Something entirely uncovered.\n",
            from_sha="a" * 40, to_sha="b" * 40, extraction="none", depth="deep",
            claims=[self._claim("cause", "unrelated", checked="<no-file: gone>")],
            validated_at=1, resolves=set(), outcome=None,
            pending_disposition="issue_obsolete")
        assert record["pending_disposition"] == "issue_obsolete"


# --------------------------------------------------- supervision_override (#947 Part B)
#
# §9: an override may only TIGHTEN. `set_supervision_override` is the ONE function in
# this module allowed to write the field, and it refuses a transition that would weaken
# it. Strictness poset: none < no_merge < no_merge_no_consult = attended_only,
# none < no_consult < no_merge_no_consult = attended_only. no_merge and no_consult are
# themselves incomparable (neither is a tightening of the other).

def _base_campaign_state():
    return {"schema_version": 2, "campaign": "epic-871", "issues": [{"number": 947}]}


class TestSupervisionOverrideErrors:
    def test_none_is_valid(self):
        assert dl._supervision_override_errors(None) == []

    def test_non_dict_is_rejected(self):
        assert dl._supervision_override_errors("no_merge") != []

    def test_every_pre_947_fixture_still_validates_unchanged(self):
        for name in ("example-v2.campaign.json", "example-v1.campaign.json"):
            data = json.loads((DRIVER_STATE_DIR / name).read_text())
            ok, errors = dl.validate_driver_state(data)
            assert ok, f"{name}: {errors}"

    @pytest.mark.parametrize("mode", ["none", "no_merge", "no_consult",
                                       "no_merge_no_consult", "attended_only"])
    def test_valid_modes_pass(self, mode):
        value = {"mode": mode, "set_by_session": "s1", "set_at": "2026-08-06T00:00:00Z",
                 "expires_at": None, "bound_revision": 1}
        assert dl._supervision_override_errors(value) == []

    def test_off_vocabulary_mode_rejected(self):
        value = {"mode": "no_everything", "set_by_session": "s1",
                 "set_at": "2026-08-06T00:00:00Z", "expires_at": None, "bound_revision": 1}
        errors = dl._supervision_override_errors(value)
        assert any("mode" in e for e in errors)

    def test_missing_required_string_fields_rejected(self):
        value = {"mode": "no_merge", "set_by_session": "", "set_at": "2026-08-06T00:00:00Z",
                 "expires_at": None, "bound_revision": 1}
        errors = dl._supervision_override_errors(value)
        assert any("set_by_session" in e for e in errors)

    def test_non_int_bound_revision_rejected(self):
        value = {"mode": "no_merge", "set_by_session": "s1",
                 "set_at": "2026-08-06T00:00:00Z", "expires_at": None, "bound_revision": "7"}
        errors = dl._supervision_override_errors(value)
        assert any("bound_revision" in e for e in errors)

    def test_wired_into_validate_driver_state(self):
        state = _base_campaign_state()
        state["supervision_override"] = {"mode": "not-a-real-mode"}
        ok, errors = dl.validate_driver_state(state)
        assert not ok
        assert any("supervision_override" in e for e in errors)


class TestSetSupervisionOverride:
    def _override(self, mode, **kw):
        value = {"mode": mode, "set_by_session": "s1", "set_at": "2026-08-06T00:00:00Z",
                 "expires_at": None, "bound_revision": 1}
        value.update(kw)
        return value

    def test_none_to_anything_is_a_legal_tighten(self):
        state = _base_campaign_state()
        new = dl.set_supervision_override(state, self._override("no_merge"), now="2026-08-06T01:00:00Z")
        assert new["supervision_override"]["mode"] == "no_merge"
        # pure — the input state is untouched
        assert "supervision_override" not in state

    @pytest.mark.parametrize("frm,to", [
        ("no_merge", "no_merge_no_consult"),
        ("no_consult", "no_merge_no_consult"),
        ("no_merge", "attended_only"),
        ("no_consult", "attended_only"),
        ("no_merge_no_consult", "attended_only"),
        ("attended_only", "no_merge_no_consult"),
    ])
    def test_legal_tightening_transitions(self, frm, to):
        state = _base_campaign_state()
        state["supervision_override"] = self._override(frm)
        new = dl.set_supervision_override(state, self._override(to), now="2026-08-06T01:00:00Z")
        assert new["supervision_override"]["mode"] == to

    @pytest.mark.parametrize("frm,to", [
        ("no_merge", "none"),
        ("no_consult", "none"),
        ("attended_only", "none"),
        ("no_merge_no_consult", "no_merge"),
        ("attended_only", "no_consult"),
        ("no_merge", "no_consult"),       # incomparable — not a tighten either direction
        ("no_consult", "no_merge"),
    ])
    def test_illegal_weakening_transitions_refused(self, frm, to):
        state = _base_campaign_state()
        state["supervision_override"] = self._override(frm)
        with pytest.raises(dl.DriverStateError):
            dl.set_supervision_override(state, self._override(to), now="2026-08-06T01:00:00Z")

    def test_expired_current_treated_as_none_so_any_new_value_is_legal(self):
        state = _base_campaign_state()
        state["supervision_override"] = self._override(
            "attended_only", expires_at="2026-08-06T00:30:00Z")
        # now is PAST expires_at -> current is effectively "none"
        new = dl.set_supervision_override(
            state, self._override("no_merge"), now="2026-08-06T01:00:00Z")
        assert new["supervision_override"]["mode"] == "no_merge"

    def test_not_yet_expired_current_still_enforces_tighten(self):
        state = _base_campaign_state()
        state["supervision_override"] = self._override(
            "attended_only", expires_at="2026-08-06T23:00:00Z")
        with pytest.raises(dl.DriverStateError):
            dl.set_supervision_override(
                state, self._override("no_merge"), now="2026-08-06T01:00:00Z")

    def test_invalid_new_value_refuses_before_write(self):
        state = _base_campaign_state()
        with pytest.raises(dl.DriverStateError):
            dl.set_supervision_override(state, {"mode": "bogus"}, now="2026-08-06T01:00:00Z")

    def test_every_other_top_level_field_survives_untouched(self):
        state = _base_campaign_state()
        state["campaign_wait"] = {"status": "waiting_for_owner", "reason": "x",
                                  "blocker_id": "b1", "entered_at": "2026-08-06T00:00:00Z",
                                  "clears_when": "y"}
        new = dl.set_supervision_override(state, self._override("no_merge"), now="2026-08-06T01:00:00Z")
        assert new["campaign_wait"] == state["campaign_wait"]
        assert new["issues"] == state["issues"]

    def test_set_supervision_override_is_the_only_writer_in_driver_lib(self):
        src = (HOOKS_DIR / "driver_lib.py").read_text(encoding="utf-8")
        writers = set()
        for fn in ast.walk(ast.parse(src)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Subscript) \
                            and isinstance(target.slice, ast.Constant) \
                            and target.slice.value == "supervision_override":
                        writers.add(fn.name)
        assert writers == {"set_supervision_override"}, \
            f"supervision_override must have exactly one writer in driver_lib, found {writers}"
