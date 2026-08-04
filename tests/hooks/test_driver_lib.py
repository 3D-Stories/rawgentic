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
