"""#855 Task 3 — the POSIX platform gate.

The admission journal's atomicity rests on four primitives: ``fcntl.flock``, ``os.open`` with a
``dir_fd``, ``O_NOFOLLOW``, and ``os.fsync``. If any is unavailable the journal would silently
degrade to a non-atomic path, which is the one failure this design cannot tolerate — so the gate
fails LOUD and names the primitive rather than falling back.
"""
from __future__ import annotations

import fcntl
import os

import pytest
from phase_executor import platform_gate as PG


def test_gate_passes_on_this_host():
    """CI runs Ubuntu and every fleet host is Linux or macOS (design section 20)."""
    PG.assert_posix_primitives()


@pytest.mark.parametrize("attr_owner,attr,label", [
    (fcntl, "flock", "fcntl.flock"),
    (os, "fsync", "os.fsync"),
    (os, "O_NOFOLLOW", "O_NOFOLLOW"),
])
def test_missing_primitive_fails_loud_and_names_it(monkeypatch, attr_owner, attr, label):
    monkeypatch.delattr(attr_owner, attr, raising=True)
    with pytest.raises(PG.PlatformUnsupported) as ei:
        PG.assert_posix_primitives()
    assert label in str(ei.value)


def test_missing_dir_fd_support_fails_loud(monkeypatch):
    monkeypatch.setattr(os, "supports_dir_fd", set(), raising=True)
    with pytest.raises(PG.PlatformUnsupported) as ei:
        PG.assert_posix_primitives()
    assert "dir_fd" in str(ei.value)


def test_error_carries_the_exit_code_the_design_pins():
    """Exit 5 `platform_unsupported` in the shipped taxonomy (design section 21)."""
    assert PG.PlatformUnsupported.exit_code == 5
    assert PG.PlatformUnsupported.error_code == "platform_unsupported"


def test_reports_every_missing_primitive_not_just_the_first(monkeypatch):
    """A single-primitive message would send an operator round the loop once per primitive."""
    monkeypatch.delattr(os, "fsync", raising=True)
    monkeypatch.delattr(fcntl, "flock", raising=True)
    with pytest.raises(PG.PlatformUnsupported) as ei:
        PG.assert_posix_primitives()
    msg = str(ei.value)
    assert "os.fsync" in msg and "fcntl.flock" in msg


def test_journal_construction_enforces_the_gate(tmp_path, monkeypatch):
    """The gate is load-bearing, not advisory: a journal cannot be built without its primitives.

    A gate with no production call site is precisely the defect #855 exists to remove, so this
    test pins the wiring rather than only the gate function.
    """
    from phase_executor import admission_journal as AJ

    monkeypatch.delattr(os, "fsync", raising=True)
    with pytest.raises(PG.PlatformUnsupported):
        AJ.AdmissionJournal(tmp_path, "855")
