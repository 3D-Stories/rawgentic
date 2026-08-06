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

    def test_corrupt_driver_state_denies_rather_than_widens(self, tmp_path):
        """Step 8a cross-model review, High finding 5 (confirmed): a driver-state file
        that EXISTS but is corrupt (as opposed to legitimately absent) used to read as
        {} -- dropping a restrictive supervision_override AND the grant check, silently
        WIDENING permission via file corruption. Must deny instead (fail-safe for
        authority, matching installs_forbidden's own established convention)."""
        rev = _declare(tmp_path)
        path = Path(tmp_path) / "claude_docs" / ".driver-state" / "epic-871.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json")
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="epic-871",
                                    project_root=str(tmp_path), now=NOW)
        assert view.merge_denied is True
        assert view.merge_permitted_by_grant is False
        assert view.granted is False

    def test_invalid_campaign_id_is_also_a_safe_pass_through_not_a_crash(self, tmp_path):
        """Found by this task's own self-review: _driver_state_path's ValueError for an
        unsafe campaign_id used to raise OUTSIDE _read_driver_state's try/except,
        contradicting this same function's own "never a crash" docstring."""
        _declare(tmp_path)
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="../escape",
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


class TestGovernedCampaignIds:
    """Step 8a cross-model review, Critical finding 2 (confirmed): evaluate_campaign
    copied the workspace-level consult grant into EVERY campaign, never checking
    governed_campaign_ids -- a declaration scoped to campaign A leaked its consult
    grant to campaign B. Part A's own supervision_lib.py deliberately does NOT narrow
    by governed_campaign_ids and says so explicitly: "The campaign-scoped evaluator is
    #947" -- meaning this check was ALWAYS meant to live here, and was simply missing."""

    def test_ungoverned_campaign_gets_no_consult_grant(self, tmp_path):
        sa.declare(str(tmp_path), state="away", until=None, session_id="sess-1",
                  campaign_ids=["epic-871"], consult_providers=["gpt"],
                  consult_granted=True, now=NOW)
        _write_driver_state(tmp_path, "epic-906",
                            {"schema_version": 2, "campaign": "epic-906", "issues": []})
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="epic-906",
                                    project_root=str(tmp_path), now=NOW)
        assert view.granted is False
        assert view.consult_providers == ()

    def test_governed_campaign_still_gets_the_grant(self, tmp_path):
        sa.declare(str(tmp_path), state="away", until=None, session_id="sess-1",
                  campaign_ids=["epic-871"], consult_providers=["gpt"],
                  consult_granted=True, now=NOW)
        _write_driver_state(tmp_path, "epic-871",
                            {"schema_version": 2, "campaign": "epic-871", "issues": []})
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="epic-871",
                                    project_root=str(tmp_path), now=NOW)
        assert view.granted is True
        assert view.consult_providers == ("gpt",)

    def test_empty_governed_list_covers_every_campaign(self, tmp_path):
        sa.declare(str(tmp_path), state="away", until=None, session_id="sess-1",
                  campaign_ids=[], consult_providers=["gpt"], consult_granted=True,
                  now=NOW)
        _write_driver_state(tmp_path, "any-campaign-at-all",
                            {"schema_version": 2, "campaign": "any-campaign-at-all",
                             "issues": []})
        view = sr.evaluate_campaign(workspace_root=str(tmp_path),
                                    campaign_id="any-campaign-at-all",
                                    project_root=str(tmp_path), now=NOW)
        assert view.granted is True


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


# ------------------------------------------------------------------- authority_permits (§7)

def _attended_view():
    base = sl.SupervisionView(
        state="attended", declared="attended", until=None, expired=False, revision=1,
        declared_at=None, load_status="valid", consult_providers=(), granted=False,
        transport_verified=False, transport_verified_at=None,
        transport_verified_session_id=None,
    )
    return sr.CampaignView(base=base, merge_denied=False, merge_permitted_by_grant=False,
                           consult_providers=(), granted=False)


def _invalid_view(*, merge_permitted_by_grant=True, merge_denied=False):
    """A state==\"attended\" view whose load_status is \"invalid\" -- Part A's own
    fail-open-for-AVAILABILITY convention (`evaluate_workspace` maps invalid/absent to
    state="attended"), which must NOT also be fail-open for AUTHORITY."""
    base = sl.SupervisionView(
        state="attended", declared="invalid", until=None, expired=False, revision=0,
        declared_at=None, load_status="invalid", consult_providers=(), granted=False,
        transport_verified=False, transport_verified_at=None,
        transport_verified_session_id=None,
    )
    return sr.CampaignView(base=base, merge_denied=merge_denied,
                           merge_permitted_by_grant=merge_permitted_by_grant,
                           consult_providers=(), granted=False)


class TestAuthorityPermits:
    def test_invalid_state_never_permits_even_with_a_grant(self):
        """Step 8a cross-model review, Critical finding 3 (confirmed): authority_permits
        keyed ONLY on view.base.state == "attended", and an invalid/corrupt/deleted
        .supervision.json ALSO reads as state="attended" (Part A's own established
        fail-open-for-AVAILABILITY convention) -- silently unlocking full autonomous
        authority via file corruption. installs_forbidden already defends against this
        exact class of bug by checking load_status separately; authority_permits must
        too (fail-safe for AUTHORITY, matching that established convention)."""
        view = _invalid_view(merge_permitted_by_grant=True, merge_denied=False)
        assert sr.authority_permits("merge", view=view) is False
        assert sr.authority_permits("install", view=view) is False

    def test_attended_permits_everything_checked_first(self):
        view = _attended_view()
        assert sr.authority_permits("merge", view=view) is True
        assert sr.authority_permits("install", view=view) is True
        assert sr.authority_permits("anything_at_all", view=view) is True

    def test_away_merge_true_only_when_granted_and_not_denied(self):
        view = sr.CampaignView(base=_view(state="away").base, merge_denied=False,
                               merge_permitted_by_grant=True, consult_providers=(),
                               granted=False)
        assert sr.authority_permits("merge", view=view) is True

    def test_away_merge_false_when_denied_even_if_granted(self):
        """The exact finding-5 regression: merge_denied=True makes authority_permits
        False even when the grant says auto-merge-scoped-to-run."""
        view = sr.CampaignView(base=_view(state="away").base, merge_denied=True,
                               merge_permitted_by_grant=True, consult_providers=(),
                               granted=False)
        assert sr.authority_permits("merge", view=view) is False

    def test_away_merge_false_when_no_grant(self):
        view = sr.CampaignView(base=_view(state="away").base, merge_denied=False,
                               merge_permitted_by_grant=False, consult_providers=(),
                               granted=False)
        assert sr.authority_permits("merge", view=view) is False

    def test_sleeping_merge_true_only_when_granted_and_not_denied(self):
        view = sr.CampaignView(base=_view(state="sleeping").base, merge_denied=False,
                               merge_permitted_by_grant=True, consult_providers=(),
                               granted=False)
        assert sr.authority_permits("merge", view=view) is True

    @pytest.mark.parametrize("action_kind", ["install", "delete", "publish", "deploy"])
    def test_every_other_action_kind_always_false_away_or_sleeping(self, action_kind):
        view = sr.CampaignView(base=_view(state="away").base, merge_denied=False,
                               merge_permitted_by_grant=True, consult_providers=(),
                               granted=True)
        assert sr.authority_permits(action_kind, view=view) is False

    def test_no_signature_variant_accepts_a_bare_grant(self):
        import inspect
        sig = inspect.signature(sr.authority_permits)
        assert set(sig.parameters) == {"action_kind", "view"}


# --------------------------------------------------------------- consult_permitted (§8)

class TestConsultPermitted:
    def test_not_granted_refuses_zero_calls_into_review_runner(self, monkeypatch):
        called = []
        monkeypatch.setattr(sr.review_runner, "backend_available",
                            lambda name: called.append(name) or True)
        view = sr.CampaignView(base=_view().base, merge_denied=False,
                               merge_permitted_by_grant=False, consult_providers=("gpt",),
                               granted=False)
        ok, reason = sr.consult_permitted(view, "gpt")
        assert ok is False
        assert "not granted" in reason
        assert called == []

    def test_backend_not_in_granted_providers_refuses(self, monkeypatch):
        called = []
        monkeypatch.setattr(sr.review_runner, "backend_available",
                            lambda name: called.append(name) or True)
        view = sr.CampaignView(base=_view().base, merge_denied=False,
                               merge_permitted_by_grant=False, consult_providers=("gpt",),
                               granted=True)
        ok, reason = sr.consult_permitted(view, "glm")
        assert ok is False
        assert "glm" in reason
        assert called == []

    def test_readiness_false_refuses(self, monkeypatch):
        monkeypatch.setattr(sr.review_runner, "backend_available", lambda name: False)
        view = sr.CampaignView(base=_view().base, merge_denied=False,
                               merge_permitted_by_grant=False, consult_providers=("gpt",),
                               granted=True)
        ok, reason = sr.consult_permitted(view, "gpt")
        assert ok is False
        assert "unavailable" in reason

    def test_all_three_checks_pass_permits(self, monkeypatch):
        monkeypatch.setattr(sr.review_runner, "backend_available", lambda name: True)
        view = sr.CampaignView(base=_view().base, merge_denied=False,
                               merge_permitted_by_grant=False, consult_providers=("gpt",),
                               granted=True)
        ok, reason = sr.consult_permitted(view, "gpt")
        assert ok is True
        assert reason == ""


# --------------------------------------------------------- validate_supervision_override (§9)

class TestValidateSupervisionOverride:
    def _override(self, mode, rev=1, **kw):
        value = {"mode": mode, "set_by_session": "s1", "set_at": _iso(NOW),
                 "expires_at": None, "bound_revision": rev}
        value.update(kw)
        return value

    def test_none_to_anything_is_a_legal_tighten(self):
        ok, err = sr.validate_supervision_override(
            self._override("no_merge"), current=None, now=NOW)
        assert ok is True
        assert err == ""

    def test_legal_tighten_from_no_merge_to_attended_only(self):
        ok, err = sr.validate_supervision_override(
            self._override("attended_only"), current=self._override("no_merge"), now=NOW)
        assert ok is True

    def test_illegal_weakening_refused(self):
        ok, err = sr.validate_supervision_override(
            self._override("none"), current=self._override("attended_only"), now=NOW)
        assert ok is False
        assert err

    def test_incomparable_transition_refused(self):
        ok, err = sr.validate_supervision_override(
            self._override("no_consult"), current=self._override("no_merge"), now=NOW)
        assert ok is False

    def test_expired_current_treated_as_none(self):
        expired = self._override("attended_only", expires_at=_iso(NOW - timedelta(hours=1)))
        ok, err = sr.validate_supervision_override(
            self._override("no_merge"), current=expired, now=NOW)
        assert ok is True

    def test_invalid_new_value_shape_refused(self):
        ok, err = sr.validate_supervision_override(
            {"mode": "bogus"}, current=None, now=NOW)
        assert ok is False
        assert err

    def test_mirrors_the_setters_transition_table(self):
        """T2's set_supervision_override and this function must agree on every legal
        and illegal transition -- kept in sync deliberately."""
        import driver_lib
        for frm in driver_lib.SUPERVISION_OVERRIDE_MODES:
            for to in driver_lib.SUPERVISION_OVERRIDE_MODES:
                current = None if frm == "none" else self._override(frm)
                new_value = self._override(to)
                ok, _ = sr.validate_supervision_override(new_value, current=current, now=NOW)

                state = {"schema_version": 2, "campaign": "c", "issues": []}
                if current is not None:
                    state["supervision_override"] = current
                try:
                    driver_lib.set_supervision_override(state, new_value, now=_iso(NOW))
                    setter_ok = True
                except driver_lib.DriverStateError:
                    setter_ok = False
                assert ok == setter_ok, f"{frm} -> {to}: validate={ok}, setter={setter_ok}"


# --------------------------------------------------------- consult call-site wiring (T9b)
#
# Step-6 finding 1: consult_permitted (§8) existed but nothing called it at the three
# real `review_runner.py consult` call sites this issue's own AC6 names. `consult_check`
# is the ONE integration point the skill prose (implement-feature, peer-consult) now
# calls before dispatching -- testable here so the wiring isn't prose no test can verify.

class TestConsultCheck:
    def _grant(self, tmp_path, providers=("gpt",), granted=True):
        sa.declare(str(tmp_path), state="away", until=None, session_id="sess-1",
                  campaign_ids=["epic-871"], consult_providers=list(providers),
                  consult_granted=granted)

    def test_denied_makes_zero_calls_into_review_runner(self, tmp_path, monkeypatch):
        self._grant(tmp_path, granted=False)
        called = []
        monkeypatch.setattr(sr.review_runner, "backend_available",
                            lambda name: called.append(name) or True)
        result = sr.consult_check(workspace_root=str(tmp_path), project_root=str(tmp_path),
                                  campaign_id="epic-871", backend="gpt")
        assert result["permitted"] is False
        assert called == []

    def test_permitted_derives_allowed_backends_from_the_view_never_hardcoded(self, tmp_path, monkeypatch):
        self._grant(tmp_path, providers=("gpt", "glm"), granted=True)
        monkeypatch.setattr(sr.review_runner, "backend_available", lambda name: True)
        result = sr.consult_check(workspace_root=str(tmp_path), project_root=str(tmp_path),
                                  campaign_id="epic-871", backend="gpt")
        assert result["permitted"] is True
        assert sorted(result["allowed_backends"]) == ["glm", "gpt"]

    def test_ungranted_backend_refused(self, tmp_path, monkeypatch):
        self._grant(tmp_path, providers=("gpt",), granted=True)
        monkeypatch.setattr(sr.review_runner, "backend_available", lambda name: True)
        result = sr.consult_check(workspace_root=str(tmp_path), project_root=str(tmp_path),
                                  campaign_id="epic-871", backend="glm")
        assert result["permitted"] is False

    def test_cli_exit_code_reflects_permitted(self, tmp_path, monkeypatch):
        self._grant(tmp_path, granted=False)
        rc = sr.main(["consult-check", "--workspace-root", str(tmp_path),
                     "--project-root", str(tmp_path), "--campaign-id", "epic-871",
                     "--backend", "gpt"])
        assert rc == 1

    def test_cli_exit_zero_when_permitted(self, tmp_path, monkeypatch):
        self._grant(tmp_path, granted=True)
        monkeypatch.setattr(sr.review_runner, "backend_available", lambda name: True)
        rc = sr.main(["consult-check", "--workspace-root", str(tmp_path),
                     "--project-root", str(tmp_path), "--campaign-id", "epic-871",
                     "--backend", "gpt"])
        assert rc == 0


# ------------------------- deleted-declaration denial, end to end (#963 AC2)
#
# #947 Step 11 deferred findings 1, 4 and 7 to this issue. Nothing in the tree
# previously deleted a previously-declared state file and asserted ANYTHING; these
# tests are that missing coverage, at the caller level rather than the unit level.


def _declare_governing(workspace_root, *, campaign_ids, granted=True, state="away"):
    sa.declare(str(workspace_root), state=state, until=None, session_id="sess-1",
               campaign_ids=list(campaign_ids), consult_providers=["gpt"],
               consult_granted=granted, now=NOW)


def _delete_state(root):
    os.unlink(sl.supervision_path(str(root)))


class TestDeletedDeclarationDeniesEveryPath:

    def test_authority_permits_denies_every_action_kind(self, tmp_path):
        """Finding 1, verbatim: deleting the file made this return True for EVERY
        action_kind — merge, publish, deploy, delete and anything added later."""
        _declare_governing(tmp_path, campaign_ids=[])
        _delete_state(tmp_path)
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="c1",
                                    project_root=str(tmp_path), now=NOW)
        for kind in ("merge", "publish", "deploy", "delete", "anything-new"):
            assert sr.authority_permits(kind, view=view) is False, kind

    def test_consult_is_refused(self, tmp_path):
        """Finding 4: the consult gate saw the same absence-reads-as-attended state."""
        _declare_governing(tmp_path, campaign_ids=[], granted=True)
        _delete_state(tmp_path)
        result = sr.consult_check(
            workspace_root=str(tmp_path), project_root=str(tmp_path),
            campaign_id="c1", backend="gpt", now=NOW)
        assert result["permitted"] is False

    def test_a_claim_cannot_be_minted(self, tmp_path):
        """The claims fence: `absent` mapped to revision 0, which is also a legitimate
        never-declared revision, so a claim could be minted under a deleted file."""
        import supervision_claims as sc
        _declare_governing(tmp_path, campaign_ids=[])
        _delete_state(tmp_path)
        with pytest.raises(sc.ClaimError):
            sc.claim_action(project_root=str(tmp_path), workspace_root=str(tmp_path),
                            campaign_id="c1", blocker_id="b1", action_kind="merge",
                            action_target="o/r#1", action_params={"pr": 1},
                            bound_revision=1, session_id="sess-1")

    def test_an_attended_workspace_is_unaffected(self, tmp_path):
        """AC3: attended sessions pass through unchanged, deletion or not."""
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="c1",
                                    project_root=str(tmp_path), now=NOW)
        assert sr.authority_permits("merge", view=view) is True


class TestGovernedCampaignMissingDriverState:
    """#947 Step 11 finding 7's consult residual — MEASURED and left deferred (#963).

    The proposed hardening (governed campaign + missing state file => deny) was
    implemented, run against this suite, and REFUTED by
    `TestConsultCheck::test_permitted_derives_allowed_backends_from_the_view_never_hardcoded`:
    the owner legitimately declares away naming a campaign that has not STARTED yet, so
    its driver-state file does not exist and denying there refuses consult for exactly
    the campaign the declaration authorized. Missing and never-created are
    indistinguishable without the durable campaign registry finding 7 itself named.

    Merge needs no such rule (no policy already means no grant). These tests pin the
    ordering that must keep working, so the refuted hardening is not re-added blind.
    """

    def test_a_governed_campaign_that_has_not_started_still_gets_its_grant(self, tmp_path):
        _declare_governing(tmp_path, campaign_ids=["c1"], granted=True)
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="c1",
                                    project_root=str(tmp_path), now=NOW)
        assert view.granted is True
        # ...and merge still denies, because no policy means no grant.
        assert sr.authority_permits("merge", view=view) is False

    def test_a_CORRUPT_driver_state_still_denies_both(self, tmp_path):
        """The distinction that IS decidable: the file exists and cannot be read."""
        _declare_governing(tmp_path, campaign_ids=["c1"], granted=True)
        path = _driver_state_path(str(tmp_path), "c1")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Path(path).write_text("{corrupt")
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="c1",
                                    project_root=str(tmp_path), now=NOW)
        assert view.granted is False
        assert sr.authority_permits("merge", view=view) is False

    def test_a_governed_campaign_WITH_state_is_unaffected(self, tmp_path):
        _declare_governing(tmp_path, campaign_ids=["c1"], granted=True)
        _write_driver_state(tmp_path, "c1", {"policy": {}})
        view = sr.evaluate_campaign(workspace_root=str(tmp_path), campaign_id="c1",
                                    project_root=str(tmp_path), now=NOW)
        assert view.granted is True
