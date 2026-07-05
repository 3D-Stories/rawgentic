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
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import driver_lib  # noqa: E402


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


def test_validate_campaign_start_headless_requires_epic():
    # #163 AC5: headless refuses to start without an epic (Codex diff-review F3).
    base = {"schema_version": 2, "campaign": "c", "issues": [
        {"number": 1, "status": "queued"}]}
    ok, errors = driver_lib.validate_campaign_start(base, headless=True)
    assert not ok
    assert any("epic" in e for e in errors)
    # with an epic, headless start is fine
    ok2, _ = driver_lib.validate_campaign_start({**base, "epic": 200}, headless=True)
    assert ok2
    # non-headless start does not require an epic
    ok3, _ = driver_lib.validate_campaign_start(base, headless=False)
    assert ok3


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
