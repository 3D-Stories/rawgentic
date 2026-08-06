"""Revision-bound action claims, execute-once (#947 Part B §6).

The claims lifecycle is the only mechanism in this design that authorizes an outward
side effect. Its two load-bearing properties, each covered below:

1. Identity EXCLUDES `bound_revision` — `(campaign_id, blocker_id, action_kind,
   action_target, action_digest)` is what makes two claims "the same real action"; the
   revision is an AUTHORIZATION FENCE checked separately, never part of what the action
   IS (round 3 finding 6).
2. `begin_execution` takes a FIXED lock order — the supervision-file lock, then the
   claims-file lock — so it can never observe a revision `mark_attended`/`cancel_claims`
   is mid-way through changing (round 3 finding 5).
"""

import contextlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import supervision_admin as sa  # noqa: E402
import supervision_claims as sc  # noqa: E402
import supervision_lib as sl  # noqa: E402


def _declare_and_get_revision(workspace_root, *, state="away", until=None):
    sa.declare(str(workspace_root), state=state, until=until, session_id="sess-1",
              campaign_ids=["epic-871"], consult_providers=["gpt"], consult_granted=True)
    loaded = sl.read_state(str(workspace_root))
    return loaded.record["revision"]


def _params(pr=960):
    return {"pr_number": pr, "method": "squash"}


class TestActionTargetAndDigest:
    def test_pr_shape_normalized(self):
        assert sc.normalize_action_target("PR:3D-Stories/rawgentic#960/") == \
            "pr:3d-stories/rawgentic#960"

    def test_issue_shape_normalized(self):
        assert sc.normalize_action_target("Issue:947") == "issue:947"

    def test_off_shape_rejected(self):
        with pytest.raises(sc.ClaimError):
            sc.normalize_action_target("merge:960")

    def test_digest_is_computed_from_structured_params_not_a_caller_hash(self):
        d1 = sc.compute_action_digest({"pr_number": 960, "method": "squash"})
        d2 = sc.compute_action_digest({"method": "squash", "pr_number": 960})
        assert d1 == d2  # key order must not matter (sort_keys)

    def test_different_params_produce_different_digest(self):
        d1 = sc.compute_action_digest({"pr_number": 960})
        d2 = sc.compute_action_digest({"pr_number": 961})
        assert d1 != d2


class TestClaimAction:
    def test_mints_a_new_pending_claim(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        claim = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:3D-Stories/rawgentic#960", action_params=_params(),
            bound_revision=rev, session_id="sess-1")
        assert claim["state"] == "pending"
        assert claim["bound_revision"] == rev
        assert claim["action_target"] == "pr:3d-stories/rawgentic#960"

    def test_two_calls_same_identity_return_the_same_pending_claim(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        c1 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(), bound_revision=rev,
            session_id="sess-1")
        c2 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(), bound_revision=rev,
            session_id="sess-2")
        assert c1["claim_id"] == c2["claim_id"]

    def test_same_target_different_digest_is_a_different_claim(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        c1 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(960), bound_revision=rev,
            session_id="sess-1")
        c2 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(961), bound_revision=rev,
            session_id="sess-1")
        assert c1["claim_id"] != c2["claim_id"]

    def test_identity_excludes_bound_revision_an_executing_old_revision_claim_blocks_a_new_one(
            self, tmp_path):
        """Round-3 finding 6's exact regression: a stale-revision claim already
        `executing` must not coexist with a fresh claim for the SAME real action."""
        rev1 = _declare_and_get_revision(tmp_path)
        c1 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(), bound_revision=rev1,
            session_id="sess-1")
        assert sc.begin_execution(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", claim_id=c1["claim_id"]) is True

        rev2 = _declare_and_get_revision(tmp_path, state="sleeping", until="2099-01-01T00:00:00Z")
        assert rev2 != rev1
        c2 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(), bound_revision=rev2,
            session_id="sess-2")
        assert c2["claim_id"] == c1["claim_id"]
        assert c2["state"] == "executing"

    def test_cancelled_claim_allows_a_fresh_claim_for_the_same_identity(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        c1 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(), bound_revision=rev,
            session_id="sess-1")
        cancelled = sc.cancel_claims(project_root=str(tmp_path), campaign_id="epic-871")
        assert cancelled[0]["claim_id"] == c1["claim_id"]
        c2 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(), bound_revision=rev,
            session_id="sess-2")
        assert c2["claim_id"] != c1["claim_id"]
        assert c2["state"] == "pending"

    def test_refuses_a_new_claim_when_bound_revision_no_longer_matches_current(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        with pytest.raises(sc.ClaimError):
            sc.claim_action(
                project_root=str(tmp_path), workspace_root=str(tmp_path),
                campaign_id="epic-871", blocker_id="b1", action_kind="merge",
                action_target="pr:x/y#1", action_params=_params(),
                bound_revision=rev + 999, session_id="sess-1")

    def test_invalid_campaign_id_refuses_before_any_write(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        with pytest.raises(sc.ClaimError):
            sc.claim_action(
                project_root=str(tmp_path), workspace_root=str(tmp_path),
                campaign_id="../escape", blocker_id="b1", action_kind="merge",
                action_target="pr:x/y#1", action_params=_params(), bound_revision=rev,
                session_id="sess-1")
        assert not (tmp_path / "claude_docs" / ".supervision-claims").exists()


class TestBeginExecution:
    def test_refuses_when_claim_is_not_pending(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        c1 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(), bound_revision=rev,
            session_id="sess-1")
        assert sc.begin_execution(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", claim_id=c1["claim_id"]) is True
        # already executing -> a second call refuses
        assert sc.begin_execution(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", claim_id=c1["claim_id"]) is False

    def test_refuses_a_cancelled_claim(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        c1 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(), bound_revision=rev,
            session_id="sess-1")
        sc.cancel_claims(project_root=str(tmp_path), campaign_id="epic-871")
        assert sc.begin_execution(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", claim_id=c1["claim_id"]) is False

    def test_refuses_when_the_bound_revision_no_longer_matches(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        c1 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(), bound_revision=rev,
            session_id="sess-1")
        _declare_and_get_revision(tmp_path, state="sleeping", until="2099-01-01T00:00:00Z")
        assert sc.begin_execution(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", claim_id=c1["claim_id"]) is False

    def test_unknown_claim_id_refuses(self, tmp_path):
        _declare_and_get_revision(tmp_path)
        assert sc.begin_execution(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", claim_id="c-does-not-exist") is False

    def test_lock_acquisition_order_is_supervision_then_claims(self, tmp_path, monkeypatch):
        """Round-3 finding 5's exact regression: the reverse order would let a
        revision bump land between the read and the transition."""
        rev = _declare_and_get_revision(tmp_path)
        c1 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(), bound_revision=rev,
            session_id="sess-1")

        order = []
        real_file_lock = sc.plan_lib.file_lock

        @contextlib.contextmanager
        def spy(path):
            order.append(path)
            with real_file_lock(path):
                yield

        monkeypatch.setattr(sc.plan_lib, "file_lock", spy)
        assert sc.begin_execution(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", claim_id=c1["claim_id"]) is True
        assert len(order) == 2
        assert order[0].endswith(".supervision.json")
        assert "supervision-claims" in order[1]


class TestMarkExecuted:
    def test_executing_to_executed(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        c1 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(), bound_revision=rev,
            session_id="sess-1")
        sc.begin_execution(project_root=str(tmp_path), workspace_root=str(tmp_path),
                           campaign_id="epic-871", claim_id=c1["claim_id"])
        done = sc.mark_executed(project_root=str(tmp_path), campaign_id="epic-871",
                                claim_id=c1["claim_id"], evidence={"merge_sha": "abc123"})
        assert done["state"] == "executed"

    def test_refuses_from_pending(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        c1 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(), bound_revision=rev,
            session_id="sess-1")
        with pytest.raises(sc.ClaimError):
            sc.mark_executed(project_root=str(tmp_path), campaign_id="epic-871",
                             claim_id=c1["claim_id"], evidence={})

    def test_refuses_from_executed(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        c1 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(), bound_revision=rev,
            session_id="sess-1")
        sc.begin_execution(project_root=str(tmp_path), workspace_root=str(tmp_path),
                           campaign_id="epic-871", claim_id=c1["claim_id"])
        sc.mark_executed(project_root=str(tmp_path), campaign_id="epic-871",
                         claim_id=c1["claim_id"], evidence={})
        with pytest.raises(sc.ClaimError):
            sc.mark_executed(project_root=str(tmp_path), campaign_id="epic-871",
                             claim_id=c1["claim_id"], evidence={})


class TestCancelClaims:
    def test_cancels_only_pending_never_executing_or_executed(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        pending = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(1), bound_revision=rev,
            session_id="sess-1")
        executing = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b2", action_kind="merge",
            action_target="pr:x/y#2", action_params=_params(2), bound_revision=rev,
            session_id="sess-1")
        sc.begin_execution(project_root=str(tmp_path), workspace_root=str(tmp_path),
                           campaign_id="epic-871", claim_id=executing["claim_id"])
        executed = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b3", action_kind="merge",
            action_target="pr:x/y#3", action_params=_params(3), bound_revision=rev,
            session_id="sess-1")
        sc.begin_execution(project_root=str(tmp_path), workspace_root=str(tmp_path),
                           campaign_id="epic-871", claim_id=executed["claim_id"])
        sc.mark_executed(project_root=str(tmp_path), campaign_id="epic-871",
                         claim_id=executed["claim_id"], evidence={})

        cancelled = sc.cancel_claims(project_root=str(tmp_path), campaign_id="epic-871")
        assert [c["claim_id"] for c in cancelled] == [pending["claim_id"]]

        path = sc.claims_path(str(tmp_path), "epic-871")
        data = json.loads(Path(path).read_text())
        states = {c["claim_id"]: c["state"] for c in data["claims"]}
        assert states[pending["claim_id"]] == "cancelled"
        assert states[executing["claim_id"]] == "executing"
        assert states[executed["claim_id"]] == "executed"

    def test_no_campaign_id_cancels_across_every_campaign(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        c1 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(1), bound_revision=rev,
            session_id="sess-1")
        c2 = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-906", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#2", action_params=_params(2), bound_revision=rev,
            session_id="sess-1")
        cancelled = sc.cancel_claims(project_root=str(tmp_path))
        ids = {c["claim_id"] for c in cancelled}
        assert ids == {c1["claim_id"], c2["claim_id"]}

    def test_no_claims_file_returns_empty_list(self, tmp_path):
        assert sc.cancel_claims(project_root=str(tmp_path), campaign_id="nope") == []


class TestReconcileClaim:
    def _executing_claim(self, tmp_path, rev):
        c = sc.claim_action(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", blocker_id="b1", action_kind="merge",
            action_target="pr:x/y#1", action_params=_params(), bound_revision=rev,
            session_id="sess-1")
        sc.begin_execution(project_root=str(tmp_path), workspace_root=str(tmp_path),
                           campaign_id="epic-871", claim_id=c["claim_id"])
        return c

    def test_resolved_transitions_to_executed(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        c = self._executing_claim(tmp_path, rev)
        outcome = sc.reconcile_claim(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", claim_id=c["claim_id"],
            evidence_probe=lambda claim: "resolved")
        assert outcome == "resolved"
        path = sc.claims_path(str(tmp_path), "epic-871")
        data = json.loads(Path(path).read_text())
        assert data["claims"][0]["state"] == "executed"

    def test_retry_transitions_to_pending_when_revision_still_matches(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        c = self._executing_claim(tmp_path, rev)
        outcome = sc.reconcile_claim(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", claim_id=c["claim_id"],
            evidence_probe=lambda claim: "retry")
        assert outcome == "retry"
        path = sc.claims_path(str(tmp_path), "epic-871")
        data = json.loads(Path(path).read_text())
        assert data["claims"][0]["state"] == "pending"

    def test_retry_transitions_to_cancelled_when_revision_has_since_moved(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        c = self._executing_claim(tmp_path, rev)
        _declare_and_get_revision(tmp_path, state="sleeping", until="2099-01-01T00:00:00Z")
        outcome = sc.reconcile_claim(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", claim_id=c["claim_id"],
            evidence_probe=lambda claim: "retry")
        assert outcome == "retry"
        path = sc.claims_path(str(tmp_path), "epic-871")
        data = json.loads(Path(path).read_text())
        assert data["claims"][0]["state"] == "cancelled"

    def test_unknown_leaves_the_claim_executing_unchanged(self, tmp_path):
        rev = _declare_and_get_revision(tmp_path)
        c = self._executing_claim(tmp_path, rev)
        outcome = sc.reconcile_claim(
            project_root=str(tmp_path), workspace_root=str(tmp_path),
            campaign_id="epic-871", claim_id=c["claim_id"],
            evidence_probe=lambda claim: "unknown")
        assert outcome == "unknown"
        path = sc.claims_path(str(tmp_path), "epic-871")
        data = json.loads(Path(path).read_text())
        assert data["claims"][0]["state"] == "executing"
