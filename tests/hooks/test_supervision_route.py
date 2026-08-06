"""Campaign-scoped supervision decision layer (#947 Part B §4, §7, §8, §9).

`CampaignView` is the ONE input every decision function in this module accepts —
`evaluate_campaign` is its only constructor (AST-tested), so no combination of calls can
pair one campaign's view with a foreign campaign's grant, override, or workspace state
(round 3 finding 10, closing findings 5/7 one level further up).
"""

import ast
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import supervision_admin as sa  # noqa: E402
import supervision_lib as sl  # noqa: E402
import supervision_route as sr  # noqa: E402

NOW = datetime(2026, 8, 6, 21, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _driver_state_path(project_root, campaign_id):
    return os.path.join(str(project_root), "claude_docs", ".driver-state",
                        f"{campaign_id}.json")


def _write_driver_state(project_root, campaign_id, state):
    path = _driver_state_path(project_root, campaign_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Path(path).write_text(json.dumps(state))


def _declare(workspace_root, *, state="away", until=None, providers=("gpt",),
            granted=True, now=NOW):
    sa.declare(str(workspace_root), state=state, until=until, session_id="sess-1",
              campaign_ids=["epic-871"], consult_providers=list(providers),
              consult_granted=granted, now=now)
    loaded = sl.read_state(str(workspace_root))
    return loaded.record["revision"]


class TestEvaluateCampaignBasics:
    def test_no_grant_no_override_is_a_permissive_pass_through(self, tmp_path):
        rev = _declare(tmp_path)
        _write_driver_state(tmp_path, "epic-871",
                            {"schema_version": 2, "campaign": "epic-871", "issues": []})
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="epic-871",
                                    project_root=str(tmp_path), now=NOW)
        assert view.merge_permitted_by_grant is False
        assert view.merge_denied is False
        assert view.granted is True   # from the workspace-level consult_grant
        assert view.consult_providers == ("gpt",)

    def test_auto_merge_scoped_to_run_grant_permits_merge(self, tmp_path):
        _declare(tmp_path)
        _write_driver_state(tmp_path, "epic-871", {
            "schema_version": 2, "campaign": "epic-871", "issues": [],
            "policy": {"merge_policy": "auto-merge-scoped-to-run"}})
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="epic-871",
                                    project_root=str(tmp_path), now=NOW)
        assert view.merge_permitted_by_grant is True

    def test_missing_driver_state_is_a_safe_pass_through_not_a_crash(self, tmp_path):
        _declare(tmp_path)
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="no-such-campaign",
                                    project_root=str(tmp_path), now=NOW)
        assert view.merge_permitted_by_grant is False
        assert view.merge_denied is False

    def test_base_carries_part_a_view_untouched(self, tmp_path):
        _declare(tmp_path, state="sleeping", until="2099-01-01T00:00:00Z")
        _write_driver_state(tmp_path, "epic-871",
                            {"schema_version": 2, "campaign": "epic-871", "issues": []})
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="epic-871",
                                    project_root=str(tmp_path), now=NOW)
        assert view.base.state == "sleeping"


class TestEvaluateCampaignOverride:
    def _override(self, mode, rev, **kw):
        value = {"mode": mode, "set_by_session": "s1", "set_at": _iso(NOW),
                 "expires_at": None, "bound_revision": rev}
        value.update(kw)
        return value

    @pytest.mark.parametrize("mode,merge_denied,consult_cleared", [
        ("no_merge", True, False),
        ("no_consult", False, True),
        ("no_merge_no_consult", True, True),
        ("attended_only", True, True),
    ])
    def test_override_restrictions_applied(self, tmp_path, mode, merge_denied, consult_cleared):
        rev = _declare(tmp_path)
        _write_driver_state(tmp_path, "epic-871", {
            "schema_version": 2, "campaign": "epic-871", "issues": [],
            "policy": {"merge_policy": "auto-merge-scoped-to-run"},
            "supervision_override": self._override(mode, rev)})
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="epic-871",
                                    project_root=str(tmp_path), now=NOW)
        assert view.merge_denied is merge_denied
        if consult_cleared:
            assert view.consult_providers == ()
            assert view.granted is False
        else:
            assert view.consult_providers == ("gpt",)
            assert view.granted is True

    def test_expired_override_reads_as_none_at_evaluator_time(self, tmp_path):
        """Medium finding fix: the EVALUATOR, not only the setter, treats an expired
        override as none — independent of whether set_supervision_override was ever
        involved (e.g. a hand-edited file)."""
        rev = _declare(tmp_path)
        _write_driver_state(tmp_path, "epic-871", {
            "schema_version": 2, "campaign": "epic-871", "issues": [],
            "policy": {"merge_policy": "auto-merge-scoped-to-run"},
            "supervision_override": self._override(
                "attended_only", rev, expires_at=_iso(NOW - timedelta(hours=1)))})
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="epic-871",
                                    project_root=str(tmp_path), now=NOW)
        assert view.merge_denied is False
        assert view.merge_permitted_by_grant is True
        assert view.granted is True

    def test_revision_mismatched_override_reads_as_none(self, tmp_path):
        """A stale override bound to a since-superseded declaration must not silently
        keep restricting a campaign under a completely different absence."""
        rev = _declare(tmp_path)
        # Bump the workspace revision by declaring again.
        _declare(tmp_path, state="sleeping", until="2099-01-01T00:00:00Z")
        _write_driver_state(tmp_path, "epic-871", {
            "schema_version": 2, "campaign": "epic-871", "issues": [],
            "policy": {"merge_policy": "auto-merge-scoped-to-run"},
            "supervision_override": self._override("attended_only", rev)})  # STALE bound_revision
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="epic-871",
                                    project_root=str(tmp_path), now=NOW)
        assert view.merge_denied is False

    def test_not_yet_expired_current_revision_override_is_enforced(self, tmp_path):
        rev = _declare(tmp_path)
        _write_driver_state(tmp_path, "epic-871", {
            "schema_version": 2, "campaign": "epic-871", "issues": [],
            "policy": {"merge_policy": "auto-merge-scoped-to-run"},
            "supervision_override": self._override(
                "attended_only", rev, expires_at=_iso(NOW + timedelta(hours=1)))})
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="epic-871",
                                    project_root=str(tmp_path), now=NOW)
        assert view.merge_denied is True
        assert view.granted is False


class TestNoCrossCampaignMismatch:
    def test_evaluating_campaign_a_never_reads_campaign_bs_grant_or_override(self, tmp_path):
        rev = _declare(tmp_path)
        _write_driver_state(tmp_path, "campaign-a", {
            "schema_version": 2, "campaign": "campaign-a", "issues": [],
            "supervision_override": {"mode": "no_merge", "set_by_session": "s1",
                                     "set_at": _iso(NOW), "expires_at": None,
                                     "bound_revision": rev}})
        _write_driver_state(tmp_path, "campaign-b", {
            "schema_version": 2, "campaign": "campaign-b", "issues": [],
            "policy": {"merge_policy": "auto-merge-scoped-to-run"}})
        view_a = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="campaign-a",
                                      project_root=str(tmp_path), now=NOW)
        assert view_a.merge_denied is True
        assert view_a.merge_permitted_by_grant is False  # never sees campaign-b's grant


class TestCampaignViewSingleConstructor:
    def test_campaign_view_constructed_only_inside_evaluate_campaign(self):
        src = (HOOKS / "supervision_route.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        constructors = []
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id == "CampaignView":
                    constructors.append(fn.name)
        assert constructors == ["evaluate_campaign"], \
            f"CampaignView must be constructed only inside evaluate_campaign, found: {constructors}"


# ------------------------------------------------------------------ route_for (§4)

def _view(state="away", transport_verified=True, granted=True):
    base = sl.SupervisionView(
        state=state, declared=state, until=None, expired=False, revision=1,
        declared_at=_iso(NOW), load_status="valid", consult_providers=("gpt",),
        granted=granted, transport_verified=transport_verified,
        transport_verified_at=_iso(NOW) if transport_verified else None,
        transport_verified_session_id="sess-1" if transport_verified else None,
    )
    return sr.CampaignView(base=base, merge_denied=False, merge_permitted_by_grant=False,
                           consult_providers=("gpt",), granted=granted)


class TestRouteForBasics:
    def test_now_is_required(self):
        with pytest.raises(TypeError):
            sr.route_for(_view())  # pylint: disable=no-value-for-parameter

    def test_run_fatal_wins_regardless_of_everything_else(self):
        view = _view(state="sleeping")
        route = sr.route_for(view, now=NOW, run_fatal=True, owner_only=True,
                             dependency_safe=False,
                             ask_attempt=sr.AskAttempt("t1", None, "timeout", False))
        assert route.action == "notify_only"

    def test_sleeping_decides_immediately_with_no_ask_attempt(self):
        view = _view(state="sleeping")
        route = sr.route_for(view, now=NOW)
        assert route.action == "decide_locally"

    def test_confirmed_at_none_never_crashes_and_never_sets_a_deadline(self):
        view = _view(state="away")
        route = sr.route_for(view, now=NOW,
                             ask_attempt=sr.AskAttempt("t1", None, None, False))
        assert route.deadline is None

    def test_until_none_with_confirmed_at_set_computes_deadline_from_20min_alone(self):
        view = _view(state="away")
        confirmed = _iso(NOW)
        route = sr.route_for(view, now=NOW,
                             ask_attempt=sr.AskAttempt("t1", confirmed, None, False))
        assert route.deadline == NOW + timedelta(minutes=20)


class TestRouteForOwnerOnly:
    def test_no_answer_yet_deadline_not_passed_asks_and_waits(self):
        view = _view(state="away")
        route = sr.route_for(view, now=NOW, owner_only=True,
                             ask_attempt=sr.AskAttempt("t1", _iso(NOW), None, False))
        assert route.action == "ask_owner_and_wait"

    def test_timeout_dependency_safe_defers(self):
        view = _view(state="away")
        route = sr.route_for(view, now=NOW, owner_only=True, dependency_safe=True,
                             ask_attempt=sr.AskAttempt("t1", _iso(NOW - timedelta(minutes=25)),
                                                       "timeout", False))
        assert route.action == "wait_for_owner"

    def test_timeout_not_dependency_safe_parks(self):
        view = _view(state="away")
        route = sr.route_for(view, now=NOW, owner_only=True, dependency_safe=False,
                             ask_attempt=sr.AskAttempt("t1", _iso(NOW - timedelta(minutes=25)),
                                                       "timeout", False))
        assert route.action == "park_campaign"

    def test_owner_only_wins_over_sleepings_immediate_decide(self):
        """Owner-only exemption is checked BEFORE the generic sleeping row — sleeping
        never authorizes a local decision when owner_only is set."""
        view = _view(state="sleeping")
        route = sr.route_for(view, now=NOW, owner_only=True, dependency_safe=True,
                             ask_attempt=sr.AskAttempt("t1", _iso(NOW - timedelta(minutes=25)),
                                                       "timeout", False))
        assert route.action != "decide_locally"


class TestRouteForAway:
    def test_unverified_transport_never_reaches_ask_owner_and_wait(self):
        view = _view(state="away", transport_verified=False)
        route = sr.route_for(view, now=NOW,
                             ask_attempt=sr.AskAttempt("t1", _iso(NOW), None, False))
        assert route.action == "wait_for_owner"

    def test_timeout_past_deadline_decides_locally(self):
        view = _view(state="away", transport_verified=True)
        confirmed = _iso(NOW - timedelta(minutes=25))
        route = sr.route_for(view, now=NOW,
                             ask_attempt=sr.AskAttempt("t1", confirmed, "timeout", False))
        assert route.action == "decide_locally"

    def test_timeout_before_deadline_keeps_waiting(self):
        view = _view(state="away", transport_verified=True)
        confirmed = _iso(NOW - timedelta(minutes=5))
        route = sr.route_for(view, now=NOW,
                             ask_attempt=sr.AskAttempt("t1", confirmed, "timeout", False))
        assert route.action != "decide_locally"

    @pytest.mark.parametrize("disposition", ["ambiguous", "unreachable", "late"])
    def test_terminal_non_answer_dispositions_never_decide_locally(self, disposition):
        view = _view(state="away", transport_verified=True)
        route = sr.route_for(view, now=NOW,
                             ask_attempt=sr.AskAttempt("t1", _iso(NOW), disposition, False))
        assert route.action == "wait_for_owner"

    def test_send_failed_never_decides_locally(self):
        view = _view(state="away", transport_verified=True)
        route = sr.route_for(view, now=NOW,
                             ask_attempt=sr.AskAttempt("t1", _iso(NOW), None, True))
        assert route.action == "wait_for_owner"

    def test_no_ask_attempt_yet_asks_with_no_deadline(self):
        view = _view(state="away", transport_verified=True)
        route = sr.route_for(view, now=NOW, ask_attempt=None)
        assert route.action == "ask_owner_and_wait"
        assert route.deadline is None


class TestRouteActionVocabulary:
    def test_action_is_always_one_of_the_five(self):
        assert set(sr.Route._fields) == {"action", "reason", "deadline"}
        valid = {"notify_only", "ask_owner_and_wait", "wait_for_owner",
                 "park_campaign", "decide_locally"}
        for kwargs in [
            dict(run_fatal=True),
            dict(),
            dict(owner_only=True, ask_attempt=sr.AskAttempt("t", _iso(NOW), "timeout", False)),
        ]:
            route = sr.route_for(_view(state="sleeping" if not kwargs.get("owner_only") else "away"),
                                 now=NOW, **kwargs)
            assert route.action in valid
