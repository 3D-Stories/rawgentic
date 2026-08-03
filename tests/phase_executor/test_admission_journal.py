"""#855 Task 2 — the issue-scoped admission journal PRIMITIVE.

Scope is deliberately the primitive, not the wave state machine: durable locked append, hardened
parse, an indivisible check-then-append, and a transaction that HOLDS the journal lock while the
caller appends to the run ledger (the fixed issue-journal -> run-ledger order). Wave, generation
and roster semantics arrive in PR 1b and are tested there — asserting them here would test
behavior a later task introduces, which is the ordering defect the Step-6 review flagged.
"""
from __future__ import annotations

import json
import os
import threading

import pytest
from phase_executor import admission_journal as AJ


def _j(tmp_path, issue="855"):
    return AJ.AdmissionJournal(tmp_path, issue)


def _rec(**over):
    base = {"kind": "member_reserved", "workflow": "wf2", "gate": "11",
            "generation": 0, "slot": "reviewer-1", "correlation_id": "c1",
            "fencing_token": "tok-1", "attempt": 0}
    base.update(over)
    return base


# -- round trip and identity -------------------------------------------------------------------

def test_append_then_read_round_trips(tmp_path):
    j = _j(tmp_path)
    j.append(_rec())
    recs = j.read()
    assert len(recs) == 1
    assert recs[0]["slot"] == "reviewer-1"
    assert recs[0]["issue_key"] == "855"          # stamped by the journal, like the ledger's run_id


def test_absent_journal_reads_empty(tmp_path):
    assert _j(tmp_path).read() == []


def test_issue_key_mismatch_refused(tmp_path):
    j = _j(tmp_path)
    j.append(_rec())
    j.path.write_text(
        json.dumps({**_rec(), "issue_key": "999"}, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(AJ.AdmissionJournalError):
        j.read()


def test_issue_key_is_sanitized_into_the_path(tmp_path):
    """A hostile issue key must not escape the journal root (design section 17).

    The property is CONTAINMENT, not the absence of dot characters: sanitize_component collapses
    the separators, so "../../etc/evil" becomes one literal directory name that happens to contain
    dots and traverses nowhere. Asserting on the characters instead of the resolved location is
    what an earlier version of this test got wrong.
    """
    j = AJ.AdmissionJournal(tmp_path, "../../etc/evil")
    j.append(_rec())

    root = os.path.realpath(tmp_path)
    assert os.path.realpath(j.path).startswith(root + os.sep)
    component = j.path.parent.name
    assert os.sep not in component and component not in ("..", ".")


# -- durability --------------------------------------------------------------------------------

def test_append_fsyncs(tmp_path, monkeypatch):
    """Without an fsync a crash can lose the reservation, which would hand back a FREE wave —
    the one direction this design must never fail in."""
    seen = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (seen.append(fd), real(fd))[1])
    _j(tmp_path).append(_rec())
    assert seen, "append must fsync the journal fd"


def test_record_survives_a_crash_after_append(tmp_path):
    """Fail-closed asymmetry: a reservation that landed stays landed even if the caller dies
    before its run-ledger append. It costs an attempt; it must not become free capacity."""
    j = _j(tmp_path)
    with pytest.raises(RuntimeError):
        with j.transaction() as txn:
            txn.append(_rec())
            raise RuntimeError("caller died before the run-ledger append")
    assert len(_j(tmp_path).read()) == 1


# -- indivisible check-then-append --------------------------------------------------------------

def test_precheck_sees_state_under_the_lock(tmp_path):
    j = _j(tmp_path)
    j.append(_rec(slot="reviewer-1"))

    def refuse_duplicate_slot(records):
        if any(r["slot"] == "reviewer-1" for r in records):
            raise AJ.AdmissionJournalError("slot already reserved")

    with pytest.raises(AJ.AdmissionJournalError):
        j.append(_rec(slot="reviewer-1"), precheck=refuse_duplicate_slot)
    assert len(j.read()) == 1        # the refused append wrote nothing


def test_concurrent_appends_serialize_and_only_one_wins(tmp_path):
    """Two threads, separate journal objects (separate fds), same precheck. The lock must make
    check-then-append indivisible, so exactly one lands."""
    _j(tmp_path).append(_rec(slot="seed"))
    winners, errors = [], []

    def claim(n):
        def precheck(records):
            if any(r["slot"] == "reviewer-1" for r in records):
                raise AJ.AdmissionJournalError("taken")
        try:
            _j(tmp_path).append(_rec(slot="reviewer-1", correlation_id=f"c{n}"), precheck=precheck)
            winners.append(n)
        except AJ.AdmissionJournalError as e:
            errors.append(str(e))

    ts = [threading.Thread(target=claim, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    assert len(winners) == 1, f"expected exactly one winner, got {winners}"
    assert len(errors) == 1
    assert sum(1 for r in _j(tmp_path).read() if r["slot"] == "reviewer-1") == 1


def test_transaction_holds_the_lock_across_the_body(tmp_path):
    """The run-ledger append happens INSIDE this body, so the lock must still be held — otherwise
    the fixed issue-journal -> run-ledger order buys nothing."""
    j = _j(tmp_path)
    j.append(_rec(slot="seed"))
    blocked = []

    with j.transaction() as txn:
        txn.append(_rec(slot="reviewer-1"))

        def other():
            try:
                _j(tmp_path).append(_rec(slot="reviewer-2"), timeout=0.2)
            except AJ.AdmissionJournalError:
                blocked.append(True)

        t = threading.Thread(target=other)
        t.start()
        t.join()

    assert blocked, "a second appender must not acquire the journal lock mid-transaction"


# -- hardened parse ----------------------------------------------------------------------------

def test_symlinked_journal_refused(tmp_path):
    j = _j(tmp_path)
    j.append(_rec())
    real = j.path.with_suffix(".real")
    j.path.rename(real)
    j.path.symlink_to(real)
    with pytest.raises(AJ.AdmissionJournalError):
        j.read()


def test_oversized_journal_refused(tmp_path, monkeypatch):
    j = _j(tmp_path)
    j.append(_rec())
    monkeypatch.setattr(AJ, "MAX_JOURNAL_BYTES", 4)
    with pytest.raises(AJ.AdmissionJournalError):
        j.read()


def test_non_utf8_journal_refused(tmp_path):
    j = _j(tmp_path)
    j.append(_rec())
    j.path.write_bytes(b"\xff\xfe not utf-8\n")
    with pytest.raises(AJ.AdmissionJournalError):
        j.read()


def test_malformed_json_line_refused(tmp_path):
    j = _j(tmp_path)
    j.append(_rec())
    with j.path.open("a", encoding="utf-8") as f:
        f.write("{not json\n")
    with pytest.raises(AJ.AdmissionJournalError):
        j.read()


@pytest.mark.parametrize("bad", [
    {**_rec(), "kind": "not-a-kind"},
    {k: v for k, v in _rec().items() if k != "slot"},
    _rec(generation="0"),
    _rec(generation=True),
    _rec(workflow="wf9"),
])
def test_malformed_record_refused_and_appends_nothing(tmp_path, bad):
    j = _j(tmp_path)
    j.append(_rec(slot="seed"))
    before = j.path.read_bytes()
    with pytest.raises(AJ.AdmissionJournalError):
        j.append(bad)
    assert j.path.read_bytes() == before
