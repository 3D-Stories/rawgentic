"""#855: the issue-scoped review-admission journal.

The authoritative record of review-wave admission for ONE issue. It exists because the loop-back
budget must survive across runs: a per-run ledger cannot bound anything an operator can restart
under a new run id, so admission state is keyed by issue and never reset by a new run.

This module is the PRIMITIVE — a durable, locked, hardened append-and-fold log. Wave, generation
and roster SEMANTICS ride on top of it (PR 1b); keeping them out keeps the locking testable on its
own and keeps this module free of workflow policy.

Two properties are load-bearing and both are tested:

* **Indivisible check-then-append.** ``precheck`` runs against the freshly-parsed records while the
  exclusive ``flock`` is HELD, so two concurrent claimants cannot both observe a free slot. This
  mirrors ``ExpectedCallLedger._locked_append`` deliberately — same discipline, same failure mode.
* **The lock spans the run-ledger append.** ``transaction()`` keeps the journal lock held while the
  caller appends to the per-run ledger, which is what makes the fixed
  issue-journal -> run-ledger order mean anything. Checking outside the lock would reintroduce the
  read-then-append window ``executor_routing_lib`` eliminates at its dispatch choke.

Fail-loud, like the ledger: every malformed, oversized, symlinked or non-UTF-8 journal RAISES.
Durability is fail-CLOSED in one direction on purpose — a reservation that landed stays landed even
if its caller dies, so a crash costs an attempt and never hands back free capacity.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, List, Optional

from .capture import sanitize_component

_JOURNAL_NAME = "admission.jsonl"

MAX_JOURNAL_BYTES = 4 * 1024 * 1024
MAX_JOURNAL_RECORDS = 100_000

#: Extended by the PR-1b tasks that add wave creation and observations. A record whose kind is not
#: listed is malformed rather than ignored — an unknown kind is how a policy bug or a tampered file
#: would otherwise slip through the fold unnoticed.
_KINDS = frozenset({"member_reserved"})
_WORKFLOWS = frozenset({"wf2", "wf3"})
_STR_FIELDS = ("workflow", "gate", "slot", "correlation_id", "fencing_token")
_INT_FIELDS = ("generation", "attempt")
_LOCK_POLL_SECONDS = 0.01


class AdmissionJournalError(RuntimeError):
    """Fail-loud admission-journal failure (malformed/oversized/symlinked, or a refused precheck)."""


def _validate(rec, where: str) -> dict:
    if not isinstance(rec, dict):
        raise AdmissionJournalError(f"{where}: record must be an object, got {type(rec).__name__}")
    kind = rec.get("kind")
    if kind not in _KINDS:
        raise AdmissionJournalError(f"{where}: unknown kind {kind!r}")
    for f in _STR_FIELDS:
        if not isinstance(rec.get(f), str) or not rec[f]:
            raise AdmissionJournalError(f"{where}: {f} must be a non-empty string")
    for f in _INT_FIELDS:
        # bool is an int subclass; a True generation would sort and compare like 1.
        v = rec.get(f)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise AdmissionJournalError(f"{where}: {f} must be a non-negative int")
    if rec["workflow"] not in _WORKFLOWS:
        raise AdmissionJournalError(
            f"{where}: workflow {rec['workflow']!r} not in {sorted(_WORKFLOWS)}")
    return dict(rec)


class _Txn:
    """The handle yielded by :meth:`AdmissionJournal.transaction` — appends under the held lock."""

    def __init__(self, journal: "AdmissionJournal", fd: int, records: List[dict]) -> None:
        self._journal = journal
        self._fd = fd
        self.records = records

    def append(self, rec: dict, precheck: Optional[Callable[[List[dict]], None]] = None) -> dict:
        written = self._journal._write_locked(self._fd, rec, precheck, self.records)
        self.records.append(written)
        return written


class AdmissionJournal:
    """Append + hardened-parse one issue's ``admission.jsonl``."""

    def __init__(self, journal_dir, issue_key: str) -> None:
        self.issue_key = str(issue_key)
        # sanitize_component is the same guard the capture root already applies to run_id
        # (executor_routing_lib builds the run dir with it), so a hostile issue key cannot
        # traverse out of the journal root.
        self.path = Path(journal_dir) / sanitize_component(self.issue_key) / _JOURNAL_NAME

    # -- reading ---------------------------------------------------------------

    def _read_text_nofollow(self) -> Optional[str]:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError as e:  # ELOOP (symlink) and friends fail closed
            raise AdmissionJournalError(f"journal {self.path}: unopenable ({e})") from e
        try:
            st = os.fstat(fd)
            if st.st_size > MAX_JOURNAL_BYTES:
                raise AdmissionJournalError(
                    f"journal {self.path}: {st.st_size} bytes exceeds cap {MAX_JOURNAL_BYTES}")
            data = os.read(fd, MAX_JOURNAL_BYTES + 1)
        finally:
            os.close(fd)
        if len(data) > MAX_JOURNAL_BYTES:
            raise AdmissionJournalError(f"journal {self.path}: exceeds byte cap {MAX_JOURNAL_BYTES}")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise AdmissionJournalError(f"journal {self.path}: not valid UTF-8 ({e})") from e

    def read(self) -> List[dict]:
        text = self._read_text_nofollow()
        return [] if text is None else self._parse(text)

    def _parse(self, text: str) -> List[dict]:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) > MAX_JOURNAL_RECORDS:
            raise AdmissionJournalError(
                f"journal {self.path}: {len(lines)} records exceeds cap {MAX_JOURNAL_RECORDS}")
        out: List[dict] = []
        for i, ln in enumerate(lines, 1):
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError as e:
                raise AdmissionJournalError(
                    f"journal {self.path} line {i}: malformed JSON ({e})") from e
            rec = _validate(rec, f"journal {self.path} line {i}")
            if rec.get("issue_key") != self.issue_key:
                raise AdmissionJournalError(
                    f"journal {self.path} line {i}: issue_key {rec.get('issue_key')!r} != "
                    f"{self.issue_key!r}")
            out.append(rec)
        return out

    # -- appending -------------------------------------------------------------

    def _open_locked(self, timeout: Optional[float]) -> int:
        os.makedirs(self.path.parent, mode=0o700, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as e:
            raise AdmissionJournalError(
                f"journal {self.path}: unopenable for append ({e})") from e
        try:
            if timeout is None:
                fcntl.flock(fd, fcntl.LOCK_EX)
                return fd
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return fd
                except OSError:
                    if time.monotonic() >= deadline:
                        raise AdmissionJournalError(
                            f"journal {self.path}: lock not acquired within {timeout}s") from None
                    time.sleep(_LOCK_POLL_SECONDS)
        except BaseException:
            os.close(fd)
            raise

    def _records_under_lock(self, fd: int) -> List[dict]:
        st = os.fstat(fd)
        if st.st_size > MAX_JOURNAL_BYTES:
            raise AdmissionJournalError(f"journal {self.path}: {st.st_size} bytes exceeds cap")
        os.lseek(fd, 0, os.SEEK_SET)
        data = os.read(fd, MAX_JOURNAL_BYTES + 1)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise AdmissionJournalError(f"journal {self.path}: not valid UTF-8 ({e})") from e
        return self._parse(text) if text.strip() else []

    def _write_locked(self, fd: int, rec: dict, precheck, records: List[dict]) -> dict:
        """Validate, run ``precheck`` against the state held under the lock, append, fsync."""
        stamped = _validate({**rec, "issue_key": self.issue_key}, f"journal {self.path}")
        if precheck is not None:
            precheck(records)          # raises AdmissionJournalError on a refused transition
        payload = (json.dumps(stamped, sort_keys=True) + "\n").encode("utf-8")
        os.lseek(fd, 0, os.SEEK_END)
        os.write(fd, payload)
        os.fsync(fd)                   # a lost reservation would hand back a FREE wave
        return stamped

    def append(self, rec: dict, precheck: Optional[Callable[[List[dict]], None]] = None,
               *, timeout: Optional[float] = None) -> dict:
        fd = self._open_locked(timeout)
        try:
            records = self._records_under_lock(fd)
            return self._write_locked(fd, rec, precheck, records)
        finally:
            os.close(fd)               # releases the flock

    @contextmanager
    def transaction(self, *, timeout: Optional[float] = None):
        """Hold the journal lock across the body.

        The caller appends to the per-run ledger INSIDE this body, which is the whole point of the
        fixed lock order. An exception in the body releases the lock but does NOT undo an append
        already made — that asymmetry is deliberate (see the module docstring).
        """
        fd = self._open_locked(timeout)
        try:
            yield _Txn(self, fd, self._records_under_lock(fd))
        finally:
            os.close(fd)
