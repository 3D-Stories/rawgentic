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
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((DRIVER_STATE_DIR / "queue.schema.json").read_text())
    for name in ("example-v2.campaign.json", "example-v1.campaign.json"):
        data = json.loads((DRIVER_STATE_DIR / name).read_text())
        jsonschema.validate(data, schema)  # raises on failure


def test_committed_examples_pass_pure_python_validator():
    for name in ("example-v2.campaign.json", "example-v1.campaign.json"):
        data = json.loads((DRIVER_STATE_DIR / name).read_text())
        ok, errors = driver_lib.validate_driver_state(data)
        assert ok, f"{name}: {errors}"
