"""Terminal-for-now campaign state + the goal-text clause (#943 Part A, AC 5).

Two things are asserted here that look like bookkeeping and are not:

1. `campaign_wait` is a NEW TOP-LEVEL field, not a new value of the per-issue `status`.
   That vocabulary is closed and enforced twice (`driver_lib.VALID_STATUSES`, refused by
   `record_child_outcome` and again by `validate_driver_state`), with three further
   closed sets keyed off it. `additionalProperties: true` permits new FIELDS, never new
   `status` VALUES — a distinction an earlier draft of this design got wrong.
2. `clears_when` is REQUIRED. A pause whose exit condition nobody can state is a stall
   wearing a pause's clothes, and the whole point of an honest wait is that a Stop-hook
   goal loop can tell the difference.

This issue ships the FIELD and its validator. The behavioural consumers — scheduling,
Stop-hook release, resume, teardown — are #927's and #586's, and nothing here claims
that writing the field halts a run.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "hooks"))

import driver_lib  # noqa: E402
import plan_lib  # noqa: E402


def _state(**kw):
    base = {
        "schema_version": 2,
        "campaign": "epic-871-m4-wave",
        "project": "rawgentic",
        "epic": 871,
        "issues": [{"number": 943, "status": "queued", "depends_on": []}],
    }
    base.update(kw)
    return base


def _wait(**kw):
    w = {
        "status": "waiting_for_owner",
        "reason": "owner-only disposition outstanding",
        "blocker_id": "b-1",
        "entered_at": "2026-08-05T21:00:00Z",
        "clears_when": "the owner writes back a disposition for #944",
    }
    w.update(kw)
    return w


# ------------------------------------------------- the field is purely additive

def test_a_campaign_without_the_field_still_validates():
    """Every pre-existing campaign file must keep validating byte-unchanged."""
    ok, errors = driver_lib.validate_driver_state(_state())
    assert ok, errors


def test_both_wait_statuses_validate():
    for status in ("waiting_for_owner", "waiting_for_reset"):
        ok, errors = driver_lib.validate_driver_state(
            _state(campaign_wait=_wait(status=status)))
        assert ok, (status, errors)


def test_null_campaign_wait_is_accepted_as_not_waiting():
    ok, errors = driver_lib.validate_driver_state(_state(campaign_wait=None))
    assert ok, errors


def test_the_per_issue_status_vocabulary_is_NOT_extended():
    """The guard against the mistake this design nearly made.

    If a later hand adds a wait word to the per-issue vocabulary, this fails loudly —
    which is the point, because `record_child_outcome` and three closed sets keyed off
    `VALID_STATUSES` would all silently change meaning.
    """
    assert driver_lib.VALID_STATUSES == frozenset({
        "queued", "in_progress", "pr_open", "merged", "deferred", "abandoned"})
    assert "waiting_for_owner" not in driver_lib.VALID_STATUSES
    assert "waiting_for_reset" not in driver_lib.VALID_STATUSES


def test_a_wait_word_used_as_an_issue_status_is_still_refused():
    ok, errors = driver_lib.validate_driver_state(_state(
        issues=[{"number": 943, "status": "waiting_for_owner", "depends_on": []}]))
    assert not ok
    assert any("status" in e for e in errors)


# ----------------------------------------------------------------- validation

def test_a_non_object_campaign_wait_is_refused():
    ok, errors = driver_lib.validate_driver_state(_state(campaign_wait="waiting"))
    assert not ok and any("campaign_wait" in e for e in errors)


def test_an_off_vocabulary_wait_status_is_refused():
    ok, errors = driver_lib.validate_driver_state(
        _state(campaign_wait=_wait(status="waiting_for_godot")))
    assert not ok and any("campaign_wait" in e for e in errors)


@pytest.mark.parametrize("missing", [
    "status", "reason", "blocker_id", "entered_at", "clears_when"])
def test_every_required_wait_field_is_required(missing):
    w = _wait()
    del w[missing]
    ok, errors = driver_lib.validate_driver_state(_state(campaign_wait=w))
    assert not ok, f"{missing} must be required"
    assert any(missing in e for e in errors)


@pytest.mark.parametrize("blank", ["", "   ", None, 7])
def test_clears_when_must_carry_visible_text(blank):
    """A pause nobody can state the exit condition for is a stall, not a pause."""
    ok, errors = driver_lib.validate_driver_state(
        _state(campaign_wait=_wait(clears_when=blank)))
    assert not ok and any("clears_when" in e for e in errors)


def test_the_committed_schema_allows_additional_top_level_properties():
    """The basis for "no schema_version bump": the contract is open at the top level,
    so a new field validates against it without a version change."""
    schema = json.loads(
        (REPO_ROOT / "docs" / "driver-state" / "queue.schema.json").read_text())
    assert schema.get("additionalProperties") is True


# ------------------------------------------------------------ the goal clause

def test_the_campaign_goal_names_both_terminal_for_now_states():
    text = plan_lib.build_goal_text(871, [], variant="campaign",
                                   child_issues=[943, 947])
    assert "waiting_for_owner" in text
    assert "waiting_for_reset" in text


def test_the_campaign_goal_still_carries_its_original_escape_clause():
    text = plan_lib.build_goal_text(871, [], variant="campaign", child_issues=[943])
    assert "closed not-planned" in text
    assert "merged with green CI" in text


def test_the_clause_survives_the_no_children_fallback():
    """The fallback branch is what a long child list degrades to, so the honest-pause
    clause has to be on BOTH campaign paths or it vanishes exactly when the epic is
    biggest."""
    text = plan_lib.build_goal_text(871, [], variant="campaign", child_issues=[])
    assert "waiting_for_owner" in text


def test_the_campaign_goal_stays_within_the_cap_at_the_boundary():
    many = list(range(1000, 1400))          # a child list that overflows the cap
    text = plan_lib.build_goal_text(871, [], variant="campaign", child_issues=many)
    assert len(text) <= plan_lib._GOAL_CAP
    assert "waiting_for_owner" in text, (
        "the overflow fallback must keep the clause, not drop it to fit")


def test_the_per_issue_variants_do_not_gain_the_clause():
    """wf2/wf3 goals are PR-terminal and per-issue; a campaign-wait clause there would
    hand a single-issue run an escape it has no state to justify."""
    for variant in ("wf2", "wf3"):
        text = plan_lib.build_goal_text(943, ["ACs as written"], variant=variant)
        assert "waiting_for_owner" not in text
        assert "ERROR protocol" in text
