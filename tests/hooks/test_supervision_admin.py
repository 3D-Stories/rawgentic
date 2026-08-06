"""Supervision declaration writer — locked, validated, fail-LOUD (#943 Part A).

Fail mode is deliberately the OPPOSITE of `supervision_lib`'s read path. A read that
quietly degrades is correct, because it rides a hook on every tool call. A WRITE that
quietly fails is not: it would leave a session believing the owner is recorded as away
when nothing was written, which is precisely the false belief the state file exists to
prevent. So every refusal here is loud, and nothing is written on a refusal.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS))

import supervision_admin as sa  # noqa: E402
import supervision_lib as sl  # noqa: E402

NOW = datetime(2026, 8, 5, 21, 0, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=2)
EARLIER = NOW - timedelta(hours=2)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _real_future(hours=2):
    """An `until` the REAL clock has not reached yet.

    `_declare` injects the frozen `NOW`, so the in-process tests stay deterministic.
    The CLI subprocess cannot take that clock — `supervision_admin` has no `--now`
    override by design, because it gates outward installs — so it reads the real one.
    A frozen future constant crossing into `_cli` therefore expires for good once
    wall-clock time passes it: `LATER` is 2026-08-05T23:00:00Z, and the CLI declare
    test began failing permanently at that instant (#948).
    """
    return _iso(datetime.now(timezone.utc) + timedelta(hours=hours))


def _read(root):
    return json.loads(Path(sl.supervision_path(str(root))).read_text())


def _declare(root, **kw):
    kw.setdefault("state", "away")
    kw.setdefault("until", None)
    kw.setdefault("session_id", "sess-1")
    kw.setdefault("campaign_ids", [])
    kw.setdefault("consult_providers", ["gpt"])
    kw.setdefault("consult_granted", True)
    kw.setdefault("now", NOW)
    return sa.declare(str(root), **kw)


# ------------------------------------------------------------------ happy path

def test_declare_writes_the_file_at_revision_one(tmp_path):
    rec = _declare(tmp_path, state="away", until=_iso(LATER))
    assert rec["revision"] == 1
    on_disk = _read(tmp_path)
    assert on_disk["state"] == "away"
    assert on_disk["until"] == _iso(LATER)
    assert on_disk["declared_by_session"] == "sess-1"
    assert on_disk["consult_grant"] == {"providers": ["gpt"], "granted": True}
    assert on_disk["schema_version"] == 1
    assert on_disk["declared_at"]


def test_the_written_file_reads_back_as_valid(tmp_path):
    """The writer and the reader must agree on the schema — they share one validator."""
    _declare(tmp_path, state="sleeping", until=_iso(LATER))
    loaded = sl.read_state(str(tmp_path))
    assert loaded.load_status == "valid"
    view = sl.evaluate_workspace(loaded, now=NOW)
    assert view.state == "sleeping"
    assert sl.installs_forbidden(view) is True


def test_revision_increments_on_every_write(tmp_path):
    assert _declare(tmp_path)["revision"] == 1
    assert _declare(tmp_path)["revision"] == 2
    assert _declare(tmp_path, state="sleeping", until=_iso(LATER))["revision"] == 3


def test_declare_cannot_set_attended(tmp_path):
    """Raised independently by both pre-PR review waves.

    `declare`'s fence is OPTIONAL, `mark_attended`'s is mandatory. Allowing
    `declare --state attended` therefore offered an unfenced way to clear a newer absence
    and re-enable unattended installs, contradicting the documented "only /back lifts it".
    """
    _declare(tmp_path, state="away")
    with pytest.raises(sa.DeclarationRefused) as exc:
        _declare(tmp_path, state="attended")
    assert "mark_attended" in str(exc.value)
    assert _read(tmp_path)["state"] == "away", "the absence must survive"


def test_recovering_a_corrupt_file_jumps_the_revision_counter(tmp_path):
    """Restarting the counter at 1 after a recovery would let a delayed event still
    carrying expected_revision=1 from the PREVIOUS lineage satisfy the fence and clear a
    newer absence — the very hole the fence exists to close."""
    _declare(tmp_path)                                   # revision 1
    Path(sl.supervision_path(str(tmp_path))).write_text("{corrupt")
    rec = _declare(tmp_path, state="away")
    assert rec["revision"] > 1000, rec["revision"]
    with pytest.raises(sa.RevisionMismatch):
        sa.mark_attended(str(tmp_path), session_id="s", reason="stale",
                         expected_revision=1, now=NOW)


def test_mark_attended_clears_the_absence_and_the_grant(tmp_path):
    _declare(tmp_path, state="sleeping", until=_iso(LATER))
    rec = sa.mark_attended(str(tmp_path), session_id="sess-2",
                           reason="owner replied", expected_revision=1, now=NOW)
    assert rec["state"] == "attended"
    assert rec["until"] is None
    assert rec["revision"] == 2
    assert rec["consult_grant"]["granted"] is False, (
        "a consult grant must not outlive the absence it was given for")
    view = sl.evaluate_workspace(sl.read_state(str(tmp_path)), now=NOW)
    assert sl.installs_forbidden(view) is False


def test_declare_creates_the_parent_directory(tmp_path):
    root = tmp_path / "fresh-workspace"
    root.mkdir()
    _declare(root)
    assert Path(sl.supervision_path(str(root))).is_file()


# -------------------------------------------------------------- refusals (loud)

@pytest.mark.parametrize("kw, needle", [
    ({"state": "sleeping", "until": None}, "until"),
    ({"state": "attended", "until": _iso(LATER)}, "attended"),
    ({"state": "away", "until": _iso(EARLIER)}, "past"),
    ({"state": "away", "until": "tomorrow"}, "ISO"),
    ({"state": "napping"}, "state"),
])
def test_invalid_declarations_are_refused_before_any_write(tmp_path, kw, needle):
    with pytest.raises(sa.DeclarationRefused) as exc:
        _declare(tmp_path, **kw)
    assert needle.lower() in str(exc.value).lower()
    assert not Path(sl.supervision_path(str(tmp_path))).exists(), (
        "a refused declaration must write nothing at all")


def test_unknown_consult_provider_is_refused(tmp_path):
    with pytest.raises(sa.DeclarationRefused) as exc:
        _declare(tmp_path, consult_providers=["gpt", "hal9000"])
    assert "hal9000" in str(exc.value)
    assert not Path(sl.supervision_path(str(tmp_path))).exists()


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", "x" * 200])
def test_unsafe_campaign_id_is_refused(tmp_path, bad):
    with pytest.raises(sa.DeclarationRefused):
        _declare(tmp_path, campaign_ids=[bad])
    assert not Path(sl.supervision_path(str(tmp_path))).exists()


def test_declare_refuses_an_unresolvable_workspace_root(tmp_path):
    with pytest.raises(sa.DeclarationRefused):
        _declare(tmp_path / "nope")


# ------------------------------------------------------ revision fencing

def test_stale_expected_revision_aborts_rather_than_clobbering(tmp_path):
    """The optimistic-concurrency fence.

    A write computed against a stale read must not overwrite a fresher declaration —
    otherwise a long-running caller silently reverts someone else's `/back`.
    """
    _declare(tmp_path)                      # revision 1
    _declare(tmp_path)                      # revision 2
    with pytest.raises(sa.RevisionMismatch) as exc:
        _declare(tmp_path, expected_revision=1)
    assert "2" in str(exc.value), "the error must report the revision actually found"
    assert _read(tmp_path)["revision"] == 2, "the fresher record must survive intact"


def test_matching_expected_revision_is_accepted(tmp_path):
    _declare(tmp_path)
    rec = _declare(tmp_path, expected_revision=1)
    assert rec["revision"] == 2


def test_expected_revision_zero_means_the_file_must_not_exist_yet(tmp_path):
    rec = _declare(tmp_path, expected_revision=0)
    assert rec["revision"] == 1
    with pytest.raises(sa.RevisionMismatch):
        _declare(tmp_path, expected_revision=0)


def test_mark_attended_fences_on_revision_too(tmp_path):
    """A correlated owner reply that arrives late must not clear a NEWER absence."""
    _declare(tmp_path, state="away")                     # revision 1
    _declare(tmp_path, state="sleeping", until=_iso(LATER))   # revision 2
    with pytest.raises(sa.RevisionMismatch):
        sa.mark_attended(str(tmp_path), session_id="s", reason="late reply",
                         expected_revision=1, now=NOW)
    assert _read(tmp_path)["state"] == "sleeping"


# ----------------------------------------------------------------- atomicity

def test_no_stray_temp_files_are_left_behind(tmp_path):
    _declare(tmp_path)
    _declare(tmp_path)
    names = os.listdir(os.path.dirname(sl.supervision_path(str(tmp_path))))
    strays = [n for n in names
              if n != ".supervision.json" and not n.endswith(".lock")]
    assert strays == [], f"stray temp file(s): {strays}"


def test_a_write_failure_is_loud_and_not_silent(tmp_path):
    """An unwritable target must raise, never return as if it had written."""
    _declare(tmp_path)
    docs = Path(os.path.dirname(sl.supervision_path(str(tmp_path))))
    os.chmod(docs, 0o500)
    try:
        if os.geteuid() == 0:
            pytest.skip("root ignores directory permissions")
        with pytest.raises(Exception):
            _declare(tmp_path)
    finally:
        os.chmod(docs, 0o700)


def test_concurrent_writers_keep_revisions_monotonic(tmp_path):
    """The lock is held across the WHOLE read-modify-write cycle, so N writers land N
    increments and never a torn file."""
    n = 6
    procs = [
        subprocess.Popen(
            [sys.executable, str(HOOKS / "supervision_admin.py"), "declare",
             "--workspace", str(tmp_path), "--state", "away",
             "--session-id", f"sess-{i}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for i in range(n)
    ]
    for p in procs:
        p.communicate()
        assert p.returncode == 0

    record = _read(tmp_path)                     # must still be valid JSON
    assert record["revision"] == n
    assert sl.read_state(str(tmp_path)).load_status == "valid"


# --------------------------------------------------- preflight fold-in (#947 Part B §3)

import supervision_preflight as sp  # noqa: E402


def _stage_and_answer(root, *, campaign_id="epic-871", applied_ref="D267"):
    token = sp.begin_preflight(str(root), session_id="sess-1", campaign_ids=[campaign_id])
    sp.record_preflight_answer(
        str(root), token, campaign_id=campaign_id, blocker_id="b1",
        question_kind="merge_policy", answer="proceed", disposition="resolved",
        authority_basis="owner-only", applied_ref=applied_ref)
    return token


class TestDeclarePreflightFoldIn:
    def test_folds_staged_answers_and_stamps_the_new_revision(self, tmp_path):
        token = _stage_and_answer(tmp_path)
        rec = _declare(tmp_path, preflight_token=token)
        assert rec["revision"] == 1
        assert len(rec["preflight_results"]) == 1
        assert rec["preflight_results"][0]["supervision_revision"] == 1
        assert rec["preflight_results"][0]["applied_ref"] == "D267"

    def test_appends_the_token_to_consumed_preflight_tokens(self, tmp_path):
        token = _stage_and_answer(tmp_path)
        rec = _declare(tmp_path, preflight_token=token)
        assert rec["consumed_preflight_tokens"] == [token]

    def test_staging_file_is_deleted_after_a_successful_fold(self, tmp_path):
        token = _stage_and_answer(tmp_path)
        _declare(tmp_path, preflight_token=token)
        with pytest.raises(sp.PreflightError):
            sp.read_preflight(str(tmp_path), token)

    def test_retry_with_an_already_consumed_token_returns_the_current_record_unchanged(
            self, tmp_path):
        token = _stage_and_answer(tmp_path)
        first = _declare(tmp_path, preflight_token=token)
        # A second declare() call with the SAME token must be a pure no-op replay.
        second = sa.declare(str(tmp_path), state="sleeping", until=_iso(LATER),
                            session_id="sess-2", campaign_ids=[], consult_providers=[],
                            consult_granted=False, preflight_token=token, now=NOW)
        assert second == first
        assert second["revision"] == 1  # NOT bumped to 2

    def test_consumed_token_survives_an_unrelated_intervening_declaration(self, tmp_path):
        token = _stage_and_answer(tmp_path)
        first = _declare(tmp_path, preflight_token=token)
        # An unrelated declaration (no preflight token) bumps the revision.
        _declare(tmp_path, state="sleeping", until=_iso(LATER))
        # A delayed retry of the FIRST token must still be recognized as consumed —
        # it must NOT re-fold or bump the revision again, even though the ledger
        # entry is now two revisions behind current.
        third = sa.declare(str(tmp_path), state="away", until=None, session_id="sess-1",
                           campaign_ids=[], consult_providers=["gpt"],
                           consult_granted=True, preflight_token=token, now=NOW)
        assert third["revision"] == 2  # the intervening declaration's own revision
        assert third["consumed_preflight_tokens"] == [token]

    def test_consumed_tokens_capped_at_500_fifo_trim(self, tmp_path):
        # Seed 500 already-consumed tokens directly (folding 500 real ones would be slow).
        seeded = [f"pf-seed-{i}" for i in range(500)]
        rec = {
            "schema_version": sa.SCHEMA_VERSION, "revision": 1, "state": "away",
            "until": None, "declared_at": _iso(NOW), "declared_by_session": "sess-1",
            "governed_campaign_ids": [], "consult_grant": {"providers": [], "granted": False},
            "consumed_preflight_tokens": seeded,
        }
        path = sl.supervision_path(str(tmp_path))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        Path(path).write_text(json.dumps(rec))

        token = sp.begin_preflight(str(tmp_path), session_id="sess-1", campaign_ids=["epic-871"])
        new = sa.declare(str(tmp_path), state="sleeping", until=_iso(LATER),
                         session_id="sess-1", campaign_ids=[], consult_providers=[],
                         consult_granted=False, preflight_token=token, now=NOW)
        assert len(new["consumed_preflight_tokens"]) == 500
        assert new["consumed_preflight_tokens"][-1] == token
        assert "pf-seed-0" not in new["consumed_preflight_tokens"]  # oldest evicted

    def test_no_preflight_token_is_byte_identical_to_existing_behavior(self, tmp_path):
        rec = _declare(tmp_path)
        assert "preflight_results" not in rec
        assert "consumed_preflight_tokens" not in rec


class TestAdditiveFieldsSurviveDeclareAndMarkAttended:
    """Step 8a cross-model review, Critical finding 1 (confirmed): declare() and
    mark_attended() each build a brand-new record dict, so any additive field not
    explicitly re-listed silently vanishes on the very next write. This defeats
    mark_transport_verified's whole purpose (verify while attended, THEN declare away
    -- if declare() drops the verification, route_for never sees it) and would have
    silently re-broken the round-2/round-3 consumed-token-survives-an-intervening-
    declaration guarantee for mark_attended specifically (untested until now)."""

    def _verify_transport(self, root):
        sa.mark_attended(str(root), session_id="sess-1", reason="verify",
                         expected_revision=0, now=NOW)
        hermes_dir = root / "hermes"
        run_id = "supervision-transport-verify-sess-1"
        asks_dir = hermes_dir / "asks"
        asks_dir.mkdir(parents=True)
        (asks_dir / "tok1.json").write_text(json.dumps(
            {"token": "tok1", "run_id": run_id, "status": "answered",
             "answered_guid": "guid-1"}))
        evidence = tmp_path_evidence = root / "evidence.json"
        evidence.write_text(json.dumps(
            {"token": "tok1", "run_id": run_id, "guid": "guid-1",
             "dateCreated": int(NOW.timestamp() * 1000)}))
        return sa.mark_transport_verified(
            str(root), evidence_path=str(evidence), session_id="sess-1",
            hermes_state_dir=str(hermes_dir), now=NOW)

    def test_transport_verification_survives_a_subsequent_declare(self, tmp_path):
        verified = self._verify_transport(tmp_path)
        assert "transport_verification" in verified
        rec = sa.declare(str(tmp_path), state="away", until=None, session_id="sess-1",
                         campaign_ids=[], consult_providers=["gpt"], consult_granted=True,
                         now=NOW)
        assert rec["transport_verification"] == verified["transport_verification"]
        view = sl.evaluate_workspace(sl.read_state(str(tmp_path)), now=NOW,
                                     session_id="sess-1")
        assert view.transport_verified is True

    def test_transport_verification_survives_mark_attended(self, tmp_path):
        verified = self._verify_transport(tmp_path)
        rec = sa.mark_attended(str(tmp_path), session_id="sess-1", reason="back",
                               expected_revision=verified["revision"], now=NOW)
        assert rec["transport_verification"] == verified["transport_verification"]

    def test_consumed_preflight_tokens_survive_mark_attended(self, tmp_path):
        token = _stage_and_answer(tmp_path)
        declared = _declare(tmp_path, preflight_token=token)
        rec = sa.mark_attended(str(tmp_path), session_id="sess-1", reason="back",
                               expected_revision=declared["revision"], now=NOW)
        assert rec["consumed_preflight_tokens"] == [token]
        assert rec["preflight_results"] == declared["preflight_results"]

    def test_a_stale_token_retry_after_mark_attended_is_still_recognized_as_consumed(
            self, tmp_path):
        """The exact round-2 finding 3 regression, one level up: mark_attended must not
        be a gap that resets the consumed-token ledger between two declare() calls."""
        token = _stage_and_answer(tmp_path)
        first = _declare(tmp_path, preflight_token=token)
        sa.mark_attended(str(tmp_path), session_id="sess-1", reason="back",
                         expected_revision=first["revision"], now=NOW)
        retry = sa.declare(str(tmp_path), state="sleeping", until=_iso(LATER),
                           session_id="sess-2", campaign_ids=[], consult_providers=[],
                           consult_granted=False, preflight_token=token, now=NOW)
        assert retry["consumed_preflight_tokens"] == [token]
        # NOT re-folded: still exactly one preflight_results entry, not two.
        assert len(retry["preflight_results"]) == 1


# ------------------------------------------------- mark_transport_verified (#947 Part B §5)

def _hermes_ask_record(state_dir, token, run_id, *, answered=True, guid="guid-1"):
    asks_dir = Path(state_dir) / "asks"
    asks_dir.mkdir(parents=True, exist_ok=True)
    rec = {"token": token, "run_id": run_id, "question": "verify?",
          "sent_ts_ms": 0, "status": "answered" if answered else "sent",
          "recipient": "+1", "response_mode": "free_text"}
    if answered:
        rec["answered_guid"] = guid
    (asks_dir / f"{token}.json").write_text(json.dumps(rec))


def _evidence_file(tmp_path, *, token, run_id, date_created_ms, guid="guid-1"):
    doc = {"delivery_id": "d1", "run_id": run_id, "token": token, "guid": guid,
          "dateCreated": date_created_ms, "question": "verify?", "reply_text": "OK",
          "state": "ready", "reply": {"raw": "OK", "interpretation": "free_text"}}
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(doc))
    return str(path)


class TestMarkTransportVerified:
    def _ms(self, dt):
        return int(dt.timestamp() * 1000)

    def test_happy_path_writes_the_transport_verification_record(self, tmp_path):
        sa.mark_attended(str(tmp_path), session_id="sess-1", reason="verify", expected_revision=0, now=NOW)  # attended baseline
        hermes_dir = tmp_path / "hermes"
        run_id = "supervision-transport-verify-sess-1"
        _hermes_ask_record(hermes_dir, "tok1", run_id)
        evidence = _evidence_file(tmp_path, token="tok1", run_id=run_id,
                                  date_created_ms=self._ms(NOW - timedelta(minutes=2)))
        rec = sa.mark_transport_verified(
            str(tmp_path), evidence_path=evidence, session_id="sess-1",
            hermes_state_dir=str(hermes_dir), now=NOW)
        assert rec["transport_verification"]["verified_session_id"] == "sess-1"
        assert rec["transport_verification"]["evidence_token"] == "tok1"

    def test_wrong_run_id_refused(self, tmp_path):
        sa.mark_attended(str(tmp_path), session_id="sess-1", reason="verify", expected_revision=0, now=NOW)
        hermes_dir = tmp_path / "hermes"
        _hermes_ask_record(hermes_dir, "tok1", "supervision-transport-verify-OTHER-session")
        evidence = _evidence_file(tmp_path, token="tok1",
                                  run_id="supervision-transport-verify-OTHER-session",
                                  date_created_ms=self._ms(NOW))
        with pytest.raises(sa.DeclarationRefused):
            sa.mark_transport_verified(str(tmp_path), evidence_path=evidence,
                                       session_id="sess-1", hermes_state_dir=str(hermes_dir),
                                       now=NOW)

    def test_stale_evidence_past_10_minutes_refused(self, tmp_path):
        sa.mark_attended(str(tmp_path), session_id="sess-1", reason="verify", expected_revision=0, now=NOW)
        hermes_dir = tmp_path / "hermes"
        run_id = "supervision-transport-verify-sess-1"
        _hermes_ask_record(hermes_dir, "tok1", run_id)
        evidence = _evidence_file(tmp_path, token="tok1", run_id=run_id,
                                  date_created_ms=self._ms(NOW - timedelta(minutes=11)))
        with pytest.raises(sa.DeclarationRefused):
            sa.mark_transport_verified(str(tmp_path), evidence_path=evidence,
                                       session_id="sess-1", hermes_state_dir=str(hermes_dir),
                                       now=NOW)

    def test_not_currently_attended_refused(self, tmp_path):
        _declare(tmp_path, state="away", until=_iso(LATER))
        hermes_dir = tmp_path / "hermes"
        run_id = "supervision-transport-verify-sess-1"
        _hermes_ask_record(hermes_dir, "tok1", run_id)
        evidence = _evidence_file(tmp_path, token="tok1", run_id=run_id,
                                  date_created_ms=self._ms(NOW))
        with pytest.raises(sa.DeclarationRefused):
            sa.mark_transport_verified(str(tmp_path), evidence_path=evidence,
                                       session_id="sess-1", hermes_state_dir=str(hermes_dir),
                                       now=NOW)

    def test_state_change_between_the_fast_fail_and_the_write_is_caught_under_the_lock(
            self, tmp_path, monkeypatch):
        """Found by this task's own self-review: the fast-fail check reads the state
        BEFORE the hermes ask-record cross-check, outside the lock. A declare(away)
        landing in that window must not let the write proceed just because the
        outside check saw 'attended' a moment earlier."""
        sa.mark_attended(str(tmp_path), session_id="sess-1", reason="verify",
                         expected_revision=0, now=NOW)
        hermes_dir = tmp_path / "hermes"
        run_id = "supervision-transport-verify-sess-1"
        _hermes_ask_record(hermes_dir, "tok1", run_id)
        evidence = _evidence_file(tmp_path, token="tok1", run_id=run_id,
                                  date_created_ms=self._ms(NOW))

        real_read_state = sl.read_state
        calls = {"n": 0}

        def flaky_read_state(root):
            calls["n"] += 1
            if calls["n"] == 1:
                return real_read_state(root)
            # Simulate a concurrent declare(away) landing right after the fast-fail
            # check read "attended" — a DIRECT write (never through sa.declare, which
            # itself calls sl.read_state and would recurse into this same monkeypatch).
            rec = {"schema_version": sa.SCHEMA_VERSION, "revision": 99, "state": "away",
                  "until": _iso(LATER), "declared_at": _iso(NOW),
                  "declared_by_session": "sess-2", "governed_campaign_ids": [],
                  "consult_grant": {"providers": [], "granted": False}}
            Path(sl.supervision_path(root)).write_text(json.dumps(rec))
            return real_read_state(root)

        monkeypatch.setattr(sa.sl, "read_state", flaky_read_state)
        with pytest.raises(sa.DeclarationRefused, match="state changed"):
            sa.mark_transport_verified(str(tmp_path), evidence_path=evidence,
                                       session_id="sess-1", hermes_state_dir=str(hermes_dir),
                                       now=NOW)

    def test_ask_record_not_answered_refused(self, tmp_path):
        sa.mark_attended(str(tmp_path), session_id="sess-1", reason="verify", expected_revision=0, now=NOW)
        hermes_dir = tmp_path / "hermes"
        run_id = "supervision-transport-verify-sess-1"
        _hermes_ask_record(hermes_dir, "tok1", run_id, answered=False)
        evidence = _evidence_file(tmp_path, token="tok1", run_id=run_id,
                                  date_created_ms=self._ms(NOW))
        with pytest.raises(sa.DeclarationRefused):
            sa.mark_transport_verified(str(tmp_path), evidence_path=evidence,
                                       session_id="sess-1", hermes_state_dir=str(hermes_dir),
                                       now=NOW)

    def test_answered_guid_mismatch_refused(self, tmp_path):
        sa.mark_attended(str(tmp_path), session_id="sess-1", reason="verify", expected_revision=0, now=NOW)
        hermes_dir = tmp_path / "hermes"
        run_id = "supervision-transport-verify-sess-1"
        _hermes_ask_record(hermes_dir, "tok1", run_id, guid="guid-REAL")
        evidence = _evidence_file(tmp_path, token="tok1", run_id=run_id,
                                  date_created_ms=self._ms(NOW), guid="guid-FORGED")
        with pytest.raises(sa.DeclarationRefused):
            sa.mark_transport_verified(str(tmp_path), evidence_path=evidence,
                                       session_id="sess-1", hermes_state_dir=str(hermes_dir),
                                       now=NOW)

    def test_missing_ask_record_refused(self, tmp_path):
        sa.mark_attended(str(tmp_path), session_id="sess-1", reason="verify", expected_revision=0, now=NOW)
        hermes_dir = tmp_path / "hermes"
        (hermes_dir / "asks").mkdir(parents=True)
        evidence = _evidence_file(tmp_path, token="tok-does-not-exist",
                                  run_id="supervision-transport-verify-sess-1",
                                  date_created_ms=self._ms(NOW))
        with pytest.raises(sa.DeclarationRefused):
            sa.mark_transport_verified(str(tmp_path), evidence_path=evidence,
                                       session_id="sess-1", hermes_state_dir=str(hermes_dir),
                                       now=NOW)

    def test_path_traversal_token_refused_before_any_file_access(self, tmp_path):
        """Found by this task's own self-review: evidence["token"] comes from a
        CALLER-SUPPLIED file, not a value this module mints, and was joined directly
        into a filesystem path. A crafted evidence file naming a token with path
        separators must never let the read escape hermes_state_dir/asks/."""
        sa.mark_attended(str(tmp_path), session_id="sess-1", reason="verify",
                         expected_revision=0, now=NOW)
        hermes_dir = tmp_path / "hermes"
        (hermes_dir / "asks").mkdir(parents=True)
        # Plant a real "answered" ask-record OUTSIDE asks/ that a traversal would reach.
        escape_target = tmp_path / "escaped-ask.json"
        escape_target.write_text(json.dumps(
            {"token": "../escaped-ask", "run_id": "supervision-transport-verify-sess-1",
             "status": "answered", "answered_guid": "guid-1"}))
        evidence = _evidence_file(tmp_path, token="../escaped-ask",
                                  run_id="supervision-transport-verify-sess-1",
                                  date_created_ms=self._ms(NOW), guid="guid-1")
        with pytest.raises(sa.DeclarationRefused, match="safe path component"):
            sa.mark_transport_verified(str(tmp_path), evidence_path=evidence,
                                       session_id="sess-1", hermes_state_dir=str(hermes_dir),
                                       now=NOW)

# ----------------------------------------------------------------------- CLI

def _cli(*args):
    return subprocess.run(
        [sys.executable, str(HOOKS / "supervision_admin.py"), *args],
        capture_output=True, text=True)


def test_cli_declare_prints_json_on_stdout_and_a_human_line_on_stderr(tmp_path):
    r = _cli("declare", "--workspace", str(tmp_path), "--state", "sleeping",
             "--until", _real_future(), "--session-id", "sess-cli",
             "--campaign", "epic-871-m4-wave", "--provider", "gpt", "--granted")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["state"] == "sleeping"
    assert payload["revision"] == 1
    assert r.stderr.strip(), "the operator-visible confirmation goes to stderr"
    assert _read(tmp_path)["governed_campaign_ids"] == ["epic-871-m4-wave"]


def test_cli_refusal_exits_1_loudly(tmp_path):
    r = _cli("declare", "--workspace", str(tmp_path), "--state", "sleeping",
             "--session-id", "s")           # sleeping with no --until
    assert r.returncode == 1
    assert "until" in r.stderr.lower()
    assert not Path(sl.supervision_path(str(tmp_path))).exists()


def test_cli_mark_attended_round_trip(tmp_path):
    _cli("declare", "--workspace", str(tmp_path), "--state", "away",
         "--session-id", "s")
    r = _cli("mark-attended", "--workspace", str(tmp_path), "--session-id", "s2",
             "--reason", "owner texted back", "--expected-revision", "1")
    assert r.returncode == 0
    assert json.loads(r.stdout)["state"] == "attended"


def test_cli_stale_revision_exits_1(tmp_path):
    _cli("declare", "--workspace", str(tmp_path), "--state", "away",
         "--session-id", "s")
    _cli("declare", "--workspace", str(tmp_path), "--state", "away",
         "--session-id", "s")
    r = _cli("declare", "--workspace", str(tmp_path), "--state", "away",
             "--session-id", "s", "--expected-revision", "1")
    assert r.returncode == 1
    assert "revision" in r.stderr.lower()


def test_cli_missing_required_argument_is_a_usage_error(tmp_path):
    assert _cli("declare", "--workspace", str(tmp_path)).returncode == 2


