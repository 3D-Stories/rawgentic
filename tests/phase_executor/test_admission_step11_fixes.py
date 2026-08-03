"""#855 — fixes from the Step-11 pre-PR review wave.

The most important one is a fail-OPEN that the Step-8a fix round INTRODUCED: `read()` decided
"nothing written yet" with a pathname `.exists()` check, which FOLLOWS symlinks, so a dangling
issue-directory symlink read as absent and returned empty state. Two reviewers caught it
independently. That is the case for a second review round, in one test.

Two others are defects in the TESTS from the previous round — a durability assertion that accepted
any fsync, and a "short read" that actually simulated premature EOF. Both are fixed here rather
than left as coverage that looks like proof.
"""
from __future__ import annotations

import json
import os
import stat as stat_mod

import pytest
from phase_executor import admission_journal as AJ
from phase_executor import ledger as L


def _j(tmp_path, issue="855"):
    return AJ.AdmissionJournal(tmp_path, issue)


def _rec(**over):
    base = {"kind": "member_reserved", "workflow": "wf2", "gate": "11",
            "generation": 0, "slot": "reviewer-1", "correlation_id": "c1",
            "fencing_token": "tok-1", "attempt": 0}
    base.update(over)
    return base


def _admission(**over):
    base = {"schema_version": 1, "issue_key": "855", "workflow": "wf2", "gate": "11",
            "generation": 0, "digest": "sha256:abc", "slot": "reviewer-1", "attempt": 0,
            "fencing_token": "tok-1"}
    base.update(over)
    return base


# -- the fail-open the previous round introduced -------------------------------------------------

def test_dangling_issue_dir_symlink_raises_rather_than_reading_empty(tmp_path):
    """`.exists()` follows symlinks, so a dangling link looked "absent" and read() returned [].

    Returning empty state means precheck sees no reservations — every slot free. That is the exact
    fail-open this module exists to prevent.
    """
    root = tmp_path / "root"
    root.mkdir()
    j = AJ.AdmissionJournal(root, "855")
    (root / j.component).symlink_to(root / "does-not-exist", target_is_directory=True)

    with pytest.raises(AJ.AdmissionJournalError):
        j.read()


def test_unreadable_issue_dir_raises_rather_than_reading_empty(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    j = AJ.AdmissionJournal(root, "855")
    (root / j.component).write_text("a file, not a directory", encoding="utf-8")
    with pytest.raises(AJ.AdmissionJournalError):
        j.read()


def test_genuinely_absent_journal_still_reads_empty(tmp_path):
    """The fix must not turn a legitimate empty state into an error."""
    root = tmp_path / "root"
    root.mkdir()
    assert AJ.AdmissionJournal(root, "855").read() == []


def test_fifo_leaf_read_is_refused_without_blocking(tmp_path):
    """O_NONBLOCK so the refusal is REACHABLE — a blocking open on a FIFO parks forever, and a
    check that never runs is not a check."""
    j = _j(tmp_path)
    j.path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(j.path)
    with pytest.raises(AJ.AdmissionJournalError):
        j.read()


# -- test-quality fixes from the review ----------------------------------------------------------

def test_durability_syncs_the_specific_fds_not_merely_something(tmp_path, monkeypatch):
    """The previous assertion accepted ANY directory fsync plus ANY file fsync, so deleting either
    real sync could still have passed. Identity is now pinned by device+inode."""
    synced = set()
    real = os.fsync

    def record(fd):
        st = os.fstat(fd)
        synced.add((st.st_dev, st.st_ino))
        return real(fd)

    monkeypatch.setattr(os, "fsync", record)
    j = _j(tmp_path)
    j.append(_rec())
    monkeypatch.undo()

    dir_st = os.stat(j.path.parent)
    leaf_st = os.stat(j.path)
    assert (dir_st.st_dev, dir_st.st_ino) in synced, "the issue directory itself must be fsynced"
    assert (leaf_st.st_dev, leaf_st.st_ino) in synced, "the journal leaf itself must be fsynced"


def test_positive_short_read_is_completed(tmp_path, monkeypatch):
    """A genuine short read — some bytes, then the rest — must SUCCEED via the retry loop.

    The previous test returned b"" after five bytes, which is premature EOF; it would have passed
    an implementation that rejected every short read, the opposite of the documented behaviour.
    """
    j = _j(tmp_path)
    j.append(_rec(slot="a"))
    j.append(_rec(slot="b"))
    real = os.read
    monkeypatch.setattr(os, "read", lambda fd, n: real(fd, min(n, 5)))
    assert len(j.read()) == 2
    monkeypatch.undo()


def test_premature_eof_still_raises(tmp_path, monkeypatch):
    j = _j(tmp_path)
    j.append(_rec())
    real = os.read
    monkeypatch.setattr(os, "read", lambda fd, n: real(fd, min(n, 5)) and b"")
    with pytest.raises(AJ.AdmissionJournalError):
        j.read()


# -- vocabulary fields must be typed before set membership ---------------------------------------

@pytest.mark.parametrize("bad_kind", [["list"], {"a": 1}])
def test_unhashable_kind_raises_the_modules_error_not_typeerror(tmp_path, bad_kind):
    j = _j(tmp_path)
    with pytest.raises(AJ.AdmissionJournalError):
        j.append({**_rec(), "kind": bad_kind})


@pytest.mark.parametrize("bad_workflow", [["wf2"], {"wf2": 1}])
def test_unhashable_ledger_workflow_raises_ledgererror(tmp_path, bad_workflow):
    led = L.ExpectedCallLedger(tmp_path, "r1")
    led.append_initial("sha256:d", architecture="executor")
    with pytest.raises(L.LedgerError):
        led.append_expected("review", "c1", expected_architecture="executor",
                            review_admission=_admission(workflow=bad_workflow))


# -- ledger-side hardening (the journal already had these) ---------------------------------------

def test_unsupported_admission_schema_version_is_refused(tmp_path):
    """An unknown version must not be parsed as though its semantics were supported."""
    led = L.ExpectedCallLedger(tmp_path, "r1")
    led.append_initial("sha256:d", architecture="executor")
    with pytest.raises(L.LedgerError) as ei:
        led.append_expected("review", "c1", expected_architecture="executor",
                            review_admission=_admission(schema_version=999))
    assert "schema_version" in str(ei.value)


def test_overlong_admission_string_is_refused(tmp_path):
    led = L.ExpectedCallLedger(tmp_path, "r1")
    led.append_initial("sha256:d", architecture="executor")
    with pytest.raises(L.LedgerError):
        led.append_expected("review", "c1", expected_architecture="executor",
                            review_admission=_admission(digest="x" * 600))


def test_ledger_append_that_would_cross_the_cap_is_refused(tmp_path, monkeypatch):
    led = L.ExpectedCallLedger(tmp_path, "r1")
    led.append_initial("sha256:d", architecture="executor")
    monkeypatch.setattr(L, "MAX_LEDGER_BYTES", led.path.stat().st_size + 5)
    before = led.path.read_bytes()
    with pytest.raises(L.LedgerError) as ei:
        led.append_expected("review", "c1", expected_architecture="executor")
    assert "over cap" in str(ei.value)
    assert led.path.read_bytes() == before


def test_ledger_short_write_is_completed(tmp_path, monkeypatch):
    led = L.ExpectedCallLedger(tmp_path, "r1")
    led.append_initial("sha256:d", architecture="executor")
    real = os.write
    state = {"first": True}

    def stingy(fd, data):
        if state["first"] and len(data) > 1:
            state["first"] = False
            return real(fd, data[:1])
        return real(fd, data)

    monkeypatch.setattr(os, "write", stingy)
    led.append_expected("review", "c1", expected_architecture="executor")
    monkeypatch.undo()
    assert len(led.read().expected) == 1


def test_ledger_append_fsyncs(tmp_path, monkeypatch):
    led = L.ExpectedCallLedger(tmp_path, "r1")
    led.append_initial("sha256:d", architecture="executor")
    seen = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (seen.append(os.fstat(fd).st_ino), real(fd))[1])
    led.append_expected("review", "c1", expected_architecture="executor")
    monkeypatch.undo()
    assert os.stat(led.path).st_ino in seen
