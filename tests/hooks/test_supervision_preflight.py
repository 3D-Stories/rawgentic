"""Departure preflight staging, campaign-scoped answers (#947 Part B §3).

Answers stage un-bound to any supervision revision; binding happens only inside
`declare`'s own atomic write (T10). This module owns the staging file only:
begin/record/read/abandon. Round-1 finding 1: every answer is campaign-scoped, refused
before staging if the campaign isn't in the token's own list. Round-2 finding 2: a
`resolved` answer must cite the write that actually applied it (`applied_ref`).
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import supervision_preflight as sp  # noqa: E402


class TestBeginPreflight:
    def test_creates_a_staging_file_and_returns_a_token(self, tmp_path):
        token = sp.begin_preflight(str(tmp_path), session_id="sess-1",
                                   campaign_ids=["epic-871"])
        assert token
        record = sp.read_preflight(str(tmp_path), token)
        assert record["token"] == token
        assert record["session_id"] == "sess-1"
        assert record["campaign_ids"] == ["epic-871"]
        assert record["answers"] == []

    def test_token_is_not_a_revision(self, tmp_path):
        """Explicitly random, never a small sequential int a caller could guess or
        collide across sessions."""
        t1 = sp.begin_preflight(str(tmp_path), session_id="sess-1", campaign_ids=["a"])
        t2 = sp.begin_preflight(str(tmp_path), session_id="sess-1", campaign_ids=["a"])
        assert t1 != t2


class TestRecordPreflightAnswer:
    def _token(self, tmp_path, campaign_ids=("epic-871",)):
        return sp.begin_preflight(str(tmp_path), session_id="sess-1",
                                  campaign_ids=list(campaign_ids))

    def test_records_a_deferred_answer_with_no_applied_ref(self, tmp_path):
        token = self._token(tmp_path)
        sp.record_preflight_answer(
            str(tmp_path), token, campaign_id="epic-871", blocker_id="b1",
            question_kind="merge_policy", answer="wait", disposition="deferred",
            authority_basis="owner-only")
        record = sp.read_preflight(str(tmp_path), token)
        assert len(record["answers"]) == 1
        assert record["answers"][0]["disposition"] == "deferred"
        assert record["answers"][0]["applied_ref"] is None

    def test_resolved_without_applied_ref_is_refused_before_staging(self, tmp_path):
        token = self._token(tmp_path)
        with pytest.raises(sp.PreflightError):
            sp.record_preflight_answer(
                str(tmp_path), token, campaign_id="epic-871", blocker_id="b1",
                question_kind="merge_policy", answer="proceed",
                disposition="resolved", authority_basis="owner-only")
        record = sp.read_preflight(str(tmp_path), token)
        assert record["answers"] == []

    def test_resolved_with_applied_ref_is_recorded(self, tmp_path):
        token = self._token(tmp_path)
        sp.record_preflight_answer(
            str(tmp_path), token, campaign_id="epic-871", blocker_id="b1",
            question_kind="merge_policy", answer="proceed", disposition="resolved",
            authority_basis="owner-only", applied_ref="D267")
        record = sp.read_preflight(str(tmp_path), token)
        assert record["answers"][0]["applied_ref"] == "D267"

    def test_campaign_id_outside_the_tokens_list_refused_before_staging(self, tmp_path):
        token = self._token(tmp_path, campaign_ids=("epic-871",))
        with pytest.raises(sp.PreflightError):
            sp.record_preflight_answer(
                str(tmp_path), token, campaign_id="epic-906", blocker_id="b1",
                question_kind="merge_policy", answer="x", disposition="deferred",
                authority_basis="owner-only")
        record = sp.read_preflight(str(tmp_path), token)
        assert record["answers"] == []

    def test_two_campaigns_reusing_the_same_blocker_id_shape_do_not_collide(self, tmp_path):
        """Answer identity is (campaign_id, blocker_id, question_kind), not
        (blocker_id, question_kind) — two campaigns may both ask "merge policy?"."""
        token = self._token(tmp_path, campaign_ids=("epic-871", "epic-906"))
        sp.record_preflight_answer(
            str(tmp_path), token, campaign_id="epic-871", blocker_id="b1",
            question_kind="merge_policy", answer="auto", disposition="deferred",
            authority_basis="owner-only")
        sp.record_preflight_answer(
            str(tmp_path), token, campaign_id="epic-906", blocker_id="b1",
            question_kind="merge_policy", answer="manual", disposition="deferred",
            authority_basis="owner-only")
        record = sp.read_preflight(str(tmp_path), token)
        assert len(record["answers"]) == 2
        by_campaign = {a["campaign_id"]: a["answer"] for a in record["answers"]}
        assert by_campaign == {"epic-871": "auto", "epic-906": "manual"}

    def test_off_vocabulary_disposition_refused(self, tmp_path):
        token = self._token(tmp_path)
        with pytest.raises(sp.PreflightError):
            sp.record_preflight_answer(
                str(tmp_path), token, campaign_id="epic-871", blocker_id="b1",
                question_kind="merge_policy", answer="x", disposition="maybe",
                authority_basis="owner-only")

    def test_unknown_token_refused(self, tmp_path):
        with pytest.raises(sp.PreflightError):
            sp.record_preflight_answer(
                str(tmp_path), "pf-does-not-exist", campaign_id="epic-871",
                blocker_id="b1", question_kind="merge_policy", answer="x",
                disposition="deferred", authority_basis="owner-only")

    def test_a_retry_after_a_simulated_failed_fold_in_still_holds_its_staged_answers(
            self, tmp_path):
        """If `declare` (T10) fails after answers are staged, the staging file must be
        untouched — a retry re-reads it and skips already-answered blockers."""
        token = self._token(tmp_path)
        sp.record_preflight_answer(
            str(tmp_path), token, campaign_id="epic-871", blocker_id="b1",
            question_kind="merge_policy", answer="proceed", disposition="resolved",
            authority_basis="owner-only", applied_ref="D267")
        # Simulate declare() failing and never consuming the token: the staging file
        # must simply still be there, unchanged, for a retry to re-read.
        record = sp.read_preflight(str(tmp_path), token)
        assert len(record["answers"]) == 1
        assert record["answers"][0]["applied_ref"] == "D267"


class TestBeginConsuming:
    """Step 11 cross-model review, High finding 3: the staging file is not sealed
    before the supervision record is persisted and is deleted only afterward, so a
    concurrent `record_preflight_answer` can append after the fold snapshot and before
    deletion — the new owner answer is then deleted without ever entering
    `preflight_results`. `begin_consuming` closes this by sealing the token to
    `"consuming"` and returning the answers snapshot as ONE locked operation."""

    def test_returns_the_staged_answers(self, tmp_path):
        token = sp.begin_preflight(str(tmp_path), session_id="sess-1",
                                   campaign_ids=["epic-871"])
        sp.record_preflight_answer(
            str(tmp_path), token, campaign_id="epic-871", blocker_id="b1",
            question_kind="merge_policy", answer="proceed", disposition="resolved",
            authority_basis="owner-only", applied_ref="D267")
        answers = sp.begin_consuming(str(tmp_path), token)
        assert len(answers) == 1
        assert answers[0]["blocker_id"] == "b1"

    def test_seals_the_token_against_a_later_answer(self, tmp_path):
        """The exact race this fixes: an answer landing AFTER the fold snapshot but
        BEFORE the staging file is deleted must be refused, not silently lost."""
        token = sp.begin_preflight(str(tmp_path), session_id="sess-1",
                                   campaign_ids=["epic-871"])
        sp.begin_consuming(str(tmp_path), token)
        with pytest.raises(sp.PreflightError):
            sp.record_preflight_answer(
                str(tmp_path), token, campaign_id="epic-871", blocker_id="b1",
                question_kind="merge_policy", answer="proceed",
                disposition="resolved", authority_basis="owner-only",
                applied_ref="D267")

    def test_idempotent_replay_returns_the_same_snapshot(self, tmp_path):
        """A `declare` retried after a crash between sealing and deletion must see the
        SAME answers again, not an error and not a second, different snapshot."""
        token = sp.begin_preflight(str(tmp_path), session_id="sess-1",
                                   campaign_ids=["epic-871"])
        sp.record_preflight_answer(
            str(tmp_path), token, campaign_id="epic-871", blocker_id="b1",
            question_kind="merge_policy", answer="proceed", disposition="resolved",
            authority_basis="owner-only", applied_ref="D267")
        first = sp.begin_consuming(str(tmp_path), token)
        second = sp.begin_consuming(str(tmp_path), token)
        assert first == second

    def test_unknown_token_raises(self, tmp_path):
        with pytest.raises(sp.PreflightError):
            sp.begin_consuming(str(tmp_path), "pf-nope")


class TestAbandonPreflight:
    def test_abandon_removes_the_staging_file(self, tmp_path):
        token = sp.begin_preflight(str(tmp_path), session_id="sess-1",
                                   campaign_ids=["epic-871"])
        sp.abandon_preflight(str(tmp_path), token)
        with pytest.raises(sp.PreflightError):
            sp.read_preflight(str(tmp_path), token)

    def test_abandon_is_idempotent(self, tmp_path):
        token = sp.begin_preflight(str(tmp_path), session_id="sess-1",
                                   campaign_ids=["epic-871"])
        sp.abandon_preflight(str(tmp_path), token)
        sp.abandon_preflight(str(tmp_path), token)  # must not raise


class TestReadPreflight:
    def test_unknown_token_raises(self, tmp_path):
        with pytest.raises(sp.PreflightError):
            sp.read_preflight(str(tmp_path), "pf-nope")

    def test_malformed_file_raises(self, tmp_path):
        token = sp.begin_preflight(str(tmp_path), session_id="sess-1",
                                   campaign_ids=["epic-871"])
        path = sp.preflight_path(str(tmp_path), token)
        Path(path).write_text("{not json")
        with pytest.raises(sp.PreflightError):
            sp.read_preflight(str(tmp_path), token)


class TestPreflightTokenPathSafety:
    """Step 8a cross-model review, High finding 4 (confirmed): tokens are
    self-generated by begin_preflight (never attacker-controlled there), but
    read_preflight/record_preflight_answer/abandon_preflight all accept a
    CALLER-SUPPLIED token and joined it directly into a filesystem path with no
    validation -- a crafted token could escape claude_docs/.supervision-preflight/."""

    def test_read_preflight_refuses_a_traversal_token(self, tmp_path):
        # One "../" from .supervision-preflight/ lands in claude_docs/ -- plant the
        # decoy THERE so the test proves protection rather than "path didn't exist".
        decoy_dir = tmp_path / "claude_docs"
        decoy_dir.mkdir(parents=True)
        (decoy_dir / "escape.json").write_text(json.dumps(
            {"token": "escape", "session_id": "attacker", "campaign_ids": [], "answers": []}))
        with pytest.raises(sp.PreflightError):
            sp.read_preflight(str(tmp_path), "../escape")

    def test_record_preflight_answer_refuses_a_traversal_token(self, tmp_path):
        decoy_dir = tmp_path / "claude_docs"
        decoy_dir.mkdir(parents=True)
        (decoy_dir / "escape.json").write_text(json.dumps(
            {"token": "escape", "session_id": "attacker", "campaign_ids": ["c"], "answers": []}))
        with pytest.raises(sp.PreflightError):
            sp.record_preflight_answer(
                str(tmp_path), "../escape", campaign_id="c", blocker_id="b",
                question_kind="k", answer="a", disposition="deferred",
                authority_basis="owner-only")

    def test_abandon_preflight_refuses_a_traversal_token_rather_than_deleting(self, tmp_path):
        # TWO "../" from .supervision-preflight/ lands in tmp_path/ itself.
        escape_target = tmp_path / "important-unrelated-file.json"
        escape_target.write_text("{}")
        with pytest.raises(sp.PreflightError):
            sp.abandon_preflight(str(tmp_path), "../../important-unrelated-file")
        assert escape_target.exists()

    def test_a_real_generated_token_still_works(self, tmp_path):
        token = sp.begin_preflight(str(tmp_path), session_id="s", campaign_ids=["c"])
        assert sp.read_preflight(str(tmp_path), token)["token"] == token
