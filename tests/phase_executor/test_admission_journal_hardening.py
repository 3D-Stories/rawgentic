"""#855 — hardening the admission journal, from the Step-8a review wave.

Two tests that lived here were SUPERSEDED by stronger versions in
``test_admission_step11_fixes.py`` after the Step-11 review found them too weak: a durability
assertion that accepted any fsync rather than the specific fds, and a "short read" that actually
simulated premature EOF. They were removed rather than left as coverage that looks like proof.

Every test here exists because a reviewer named the defect. Both reviewers independently found the
same three: the platform gate REQUIRED ``dir_fd`` support and the journal then opened by pathname
anyway; durability stopped at the file and never reached its directory entry; and the size/record
caps were checked only against the pre-append state, so one legal append could leave a journal that
every later read refuses.
"""
from __future__ import annotations

import errno
import json
import os

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


# -- containment: the dir_fd the platform gate demands (M1 / S1) --------------------------------

def test_symlinked_issue_directory_is_refused(tmp_path):
    """O_NOFOLLOW on the leaf does nothing about a swapped PARENT. Opening the issue dir relative
    to the trusted root with O_NOFOLLOW is what the gate's dir_fd requirement is for."""
    root = tmp_path / "root"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    j = AJ.AdmissionJournal(root, "855")
    (root / j.component).symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(AJ.AdmissionJournalError):
        j.append(_rec())
    with pytest.raises(AJ.AdmissionJournalError):
        j.read()


def test_issue_path_that_is_a_file_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    j = AJ.AdmissionJournal(root, "855")
    (root / j.component).write_text("not a directory", encoding="utf-8")
    with pytest.raises(AJ.AdmissionJournalError):
        j.append(_rec())


def test_hard_linked_journal_is_refused(tmp_path):
    """O_NOFOLLOW admits a hard link; a second name for the journal inode is not the journal."""
    j = _j(tmp_path)
    j.append(_rec())
    os.link(j.path, j.path.parent / "second-name.jsonl")
    with pytest.raises(AJ.AdmissionJournalError):
        j.read()


def test_fifo_leaf_is_refused(tmp_path):
    j = _j(tmp_path)
    j.path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(j.path)
    with pytest.raises(AJ.AdmissionJournalError):
        j.append(_rec())


# -- durability reaches the directory entry (M2 / S4) -------------------------------------------

# -- prospective limits (M3 / S6) ---------------------------------------------------------------

def test_append_that_would_cross_the_byte_cap_is_refused(tmp_path, monkeypatch):
    j = _j(tmp_path)
    j.append(_rec(slot="seed"))
    size = j.path.stat().st_size
    monkeypatch.setattr(AJ, "MAX_JOURNAL_BYTES", size + 10)   # room for far less than one record
    before = j.path.read_bytes()
    with pytest.raises(AJ.AdmissionJournalError) as ei:
        j.append(_rec(slot="reviewer-2"))
    assert "over cap" in str(ei.value)
    assert j.path.read_bytes() == before          # refused BEFORE writing, not after


def test_append_at_exactly_the_byte_cap_is_allowed(tmp_path, monkeypatch):
    j = _j(tmp_path)
    j.append(_rec(slot="seed"))
    payload = len(json.dumps(
        {**_rec(slot="reviewer-2"), "issue_key": "855"}, sort_keys=True).encode()) + 1
    monkeypatch.setattr(AJ, "MAX_JOURNAL_BYTES", j.path.stat().st_size + payload)
    j.append(_rec(slot="reviewer-2"))             # exactly at the cap must pass
    assert len(j.read()) == 2


def test_append_that_would_cross_the_record_cap_is_refused(tmp_path, monkeypatch):
    j = _j(tmp_path)
    j.append(_rec(slot="seed"))
    monkeypatch.setattr(AJ, "MAX_JOURNAL_RECORDS", 1)
    before = j.path.read_bytes()
    with pytest.raises(AJ.AdmissionJournalError):
        j.append(_rec(slot="reviewer-2"))
    assert j.path.read_bytes() == before


# -- short read / short write (M4 / S3) ---------------------------------------------------------

def test_short_write_is_completed_not_reported_as_success(tmp_path, monkeypatch):
    j = _j(tmp_path)
    real = os.write
    state = {"first": True}

    def stingy(fd, data):
        if state["first"] and len(data) > 1:
            state["first"] = False
            return real(fd, data[:1])            # write one byte, like a real short write
        return real(fd, data)

    monkeypatch.setattr(os, "write", stingy)
    j.append(_rec())
    monkeypatch.undo()
    assert len(j.read()) == 1                    # the record is COMPLETE and parses


# -- closed schema and write-side type discipline (S5 / M6) ------------------------------------

def test_unknown_key_is_refused(tmp_path):
    j = _j(tmp_path)
    before_count = len(j.read())
    with pytest.raises(AJ.AdmissionJournalError) as ei:
        j.append({**_rec(), "smuggled": "policy"})
    assert "extra" in str(ei.value)
    assert len(j.read()) == before_count


def test_overlong_string_is_refused(tmp_path):
    j = _j(tmp_path)
    with pytest.raises(AJ.AdmissionJournalError):
        j.append(_rec(slot="x" * (AJ._MAX_STR_LEN + 1)))


@pytest.mark.parametrize("bad", ["a string", ["a", "list"], 7, None])
def test_non_dict_record_raises_the_modules_own_error(tmp_path, bad):
    """Not a TypeError from the {**rec} expansion — the module's contract says every malformed
    input surfaces as AdmissionJournalError."""
    j = _j(tmp_path)
    with pytest.raises(AJ.AdmissionJournalError):
        j.append(bad)


# -- lock errno discrimination (M7) -------------------------------------------------------------

def test_non_contention_lock_error_is_not_reported_as_a_timeout(tmp_path, monkeypatch):
    j = _j(tmp_path)
    j.append(_rec())

    def broken(fd, op):
        raise OSError(errno.EIO, "device failure")

    monkeypatch.setattr(AJ.fcntl, "flock", broken)
    with pytest.raises(AJ.AdmissionJournalError) as ei:
        j.append(_rec(slot="reviewer-2"), timeout=0.05)
    msg = str(ei.value)
    assert "lock failed" in msg and "not acquired within" not in msg
