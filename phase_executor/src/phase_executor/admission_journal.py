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

import errno
import fcntl
import json
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, List, Optional

from .capture import sanitize_component
from .platform_gate import assert_posix_primitives

_JOURNAL_NAME = "admission.jsonl"

MAX_JOURNAL_BYTES = 4 * 1024 * 1024
MAX_JOURNAL_RECORDS = 100_000

#: Extended by the PR-1b tasks that add wave creation and observations. A record whose kind is not
#: listed is malformed rather than ignored — an unknown kind is how a policy bug or a tampered file
#: would otherwise slip through the fold unnoticed.
_WORKFLOWS = frozenset({"wf2", "wf3"})
_STR_FIELDS = ("workflow", "gate", "slot", "correlation_id", "fencing_token")
_INT_FIELDS = ("generation", "attempt")
#: Per-kind CLOSED key set, including the journal-stamped ``issue_key``. Extended by the PR-1b
#: tasks that add wave creation and observations. Closed rather than "required fields present":
#: an unknown key that survives validation is how a policy field gets smuggled past the checker,
#: and it is what the ledger's own admission validator already refuses.
_KIND_KEYS = {
    "member_reserved": frozenset(
        ("kind", "issue_key") + _STR_FIELDS + _INT_FIELDS),
}
_KINDS = frozenset(_KIND_KEYS)
_MAX_STR_LEN = 512
_LOCK_POLL_SECONDS = 0.01
#: errnos that mean "another holder has the lock"; anything else is a real failure and must not be
#: swallowed as contention until it surfaces as a bogus timeout.
_WOULD_BLOCK = frozenset({errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK})


class AdmissionJournalError(RuntimeError):
    """Fail-loud admission-journal failure (malformed/oversized/symlinked, or a refused precheck)."""


def _validate(rec, where: str) -> dict:
    if not isinstance(rec, dict):
        raise AdmissionJournalError(f"{where}: record must be an object, got {type(rec).__name__}")
    kind = rec.get("kind")
    # `in` on a set raises TypeError for an unhashable value (a list/dict kind), which would leak
    # out of this module's "malformed input raises AdmissionJournalError" contract.
    if not isinstance(kind, str) or kind not in _KINDS:
        raise AdmissionJournalError(f"{where}: unknown kind {kind!r}")
    keys = frozenset(rec)
    if keys != _KIND_KEYS[kind]:
        missing = sorted(_KIND_KEYS[kind] - keys)
        extra = sorted(keys - _KIND_KEYS[kind])
        raise AdmissionJournalError(f"{where}: {kind} keys missing={missing} extra={extra}")
    for f in _STR_FIELDS + ("issue_key",):
        v = rec.get(f)
        if not isinstance(v, str) or not v:
            raise AdmissionJournalError(f"{where}: {f} must be a non-empty string")
        if len(v) > _MAX_STR_LEN:
            raise AdmissionJournalError(
                f"{where}: {f} exceeds {_MAX_STR_LEN} characters")
    for f in _INT_FIELDS:
        # bool is an int subclass; a True generation would sort and compare like 1.
        v = rec.get(f)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise AdmissionJournalError(f"{where}: {f} must be a non-negative int")
    if not isinstance(rec["workflow"], str) or rec["workflow"] not in _WORKFLOWS:
        raise AdmissionJournalError(
            f"{where}: workflow {rec['workflow']!r} not in {sorted(_WORKFLOWS)}")
    return dict(rec)


def _read_exactly(fd: int, size: int, where: str) -> bytes:
    """``os.read`` may return short. A short read ending on a line boundary would silently hide
    later reservations from ``precheck`` — a fail-OPEN, which is the one direction this must never
    take. Loop to the fstat-pinned length and refuse a premature EOF."""
    chunks, remaining = [], size
    while remaining > 0:
        chunk = os.read(fd, remaining)
        if not chunk:
            raise AdmissionJournalError(
                f"{where}: premature EOF with {remaining} of {size} bytes unread")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _write_all(fd: int, payload: bytes, where: str) -> None:
    """``os.write`` may write short. Reporting a partial reservation as admitted would let the
    caller proceed on a record that is not fully on disk."""
    written = 0
    while written < len(payload):
        n = os.write(fd, payload[written:])
        if n <= 0:
            raise AdmissionJournalError(
                f"{where}: wrote {written} of {len(payload)} bytes")
        written += n


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
        # Assert the primitives this journal's atomicity rests on BEFORE it can be used. A gate
        # with no call site is the exact defect #855 exists to remove, so it is enforced here
        # rather than left for a caller to remember.
        assert_posix_primitives()
        self.issue_key = str(issue_key)
        # sanitize_component is the same guard the capture root already applies to run_id
        # (executor_routing_lib builds the run dir with it), so a hostile issue key cannot
        # traverse out of the journal root.
        self.root = Path(journal_dir)
        self.component = sanitize_component(self.issue_key)
        self.path = self.root / self.component / _JOURNAL_NAME

    # -- containment -----------------------------------------------------------

    def _open_issue_dir(self, *, create: bool) -> int:
        """Open the issue directory RELATIVE to the journal root, refusing a symlinked component.

        Resolving by pathname and then opening is a check/open race: a concurrent process can swap
        a parent between the two. Every component here is opened relative to the trusted root fd
        with ``O_NOFOLLOW``, which is what the platform gate's ``dir_fd`` requirement is FOR — an
        earlier revision required the capability in the gate and then never used it, which two
        independent reviewers caught.
        """
        try:
            root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        except OSError as e:
            raise AdmissionJournalError(
                f"journal root {self.root}: unopenable ({e})") from e
        try:
            if create:
                try:
                    os.mkdir(self.component, mode=0o700, dir_fd=root_fd)
                    os.fsync(root_fd)   # the new directory ENTRY must be durable too
                except FileExistsError:
                    pass
                except OSError as e:
                    raise AdmissionJournalError(
                        f"journal {self.path}: cannot create issue dir ({e})") from e
            # OSError propagates with its errno intact. The read path needs to tell a genuine
            # ENOENT (nothing written yet) from ELOOP/ENOTDIR/EACCES, and an earlier revision
            # decided that with a pathname `.exists()` check — which FOLLOWS symlinks, so a
            # dangling issue-directory symlink read as "absent" and returned empty state. That is
            # a fail-OPEN in the one module that must never have one; two reviewers caught it.
            return os.open(self.component,
                           os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        finally:
            os.close(root_fd)

    @staticmethod
    def _assert_plain_file(fd: int, where: str) -> None:
        """A regular, single-linked file. ``O_NOFOLLOW`` refuses a symlinked leaf but admits a
        FIFO, a device, or a hard link — none of which may back this journal."""
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise AdmissionJournalError(f"{where}: not a regular file")
        if st.st_nlink != 1:
            raise AdmissionJournalError(f"{where}: {st.st_nlink} links — refusing a hard-linked journal")

    # -- reading ---------------------------------------------------------------

    def _read_text_nofollow(self) -> Optional[str]:
        try:
            dir_fd = self._open_issue_dir(create=False)
        except FileNotFoundError:
            return None                     # nothing written yet is a legitimate empty state
        except OSError as e:                # ELOOP / ENOTDIR / EACCES all fail CLOSED
            raise AdmissionJournalError(
                f"journal {self.path}: issue dir unopenable ({e})") from e
        try:
            # O_NONBLOCK so a FIFO leaf cannot park this read forever before _assert_plain_file
            # gets to refuse it — the refusal has to be reachable to be a refusal.
            fd = os.open(_JOURNAL_NAME,
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
        except FileNotFoundError:
            return None
        except OSError as e:  # ELOOP (symlink) and friends fail closed
            raise AdmissionJournalError(f"journal {self.path}: unopenable ({e})") from e
        finally:
            os.close(dir_fd)
        try:
            self._assert_plain_file(fd, f"journal {self.path}")
            st = os.fstat(fd)
            if st.st_size > MAX_JOURNAL_BYTES:
                raise AdmissionJournalError(
                    f"journal {self.path}: {st.st_size} bytes exceeds cap {MAX_JOURNAL_BYTES}")
            data = _read_exactly(fd, st.st_size, f"journal {self.path}")
        finally:
            os.close(fd)
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
        try:
            dir_fd = self._open_issue_dir(create=True)
        except OSError as e:
            raise AdmissionJournalError(
                f"journal {self.path}: issue dir unopenable ({e})") from e
        try:
            try:
                fd = os.open(_JOURNAL_NAME,
                             os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600,
                             dir_fd=dir_fd)
            except OSError as e:
                raise AdmissionJournalError(
                    f"journal {self.path}: unopenable for append ({e})") from e
            try:
                # fsync the issue directory on EVERY append, not only on first create. Deciding
                # "was it new?" from a prior stat is a race, and a leaked fd from a raising fsync
                # was the alternative bug — this is both cheaper to reason about and always right.
                os.fsync(dir_fd)
            except OSError as e:
                os.close(fd)               # the leaf fd must not leak if the fsync raises
                raise AdmissionJournalError(
                    f"journal {self.path}: directory fsync failed ({e})") from e
        finally:
            os.close(dir_fd)
        try:
            self._assert_plain_file(fd, f"journal {self.path}")
            if timeout is None:
                fcntl.flock(fd, fcntl.LOCK_EX)
                return fd
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return fd
                except OSError as e:
                    # Only genuine contention is retried; EBADF/EIO/ENOLCK must surface as
                    # themselves rather than as a bogus timeout.
                    if e.errno not in _WOULD_BLOCK:
                        raise AdmissionJournalError(
                            f"journal {self.path}: lock failed ({e})") from e
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
        data = _read_exactly(fd, st.st_size, f"journal {self.path}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise AdmissionJournalError(f"journal {self.path}: not valid UTF-8 ({e})") from e
        return self._parse(text) if text.strip() else []

    def _write_locked(self, fd: int, rec: dict, precheck, records: List[dict]) -> dict:
        """Validate, run ``precheck`` against the state held under the lock, append, fsync."""
        where = f"journal {self.path}"
        if not isinstance(rec, dict):
            # Checked BEFORE the {**rec} expansion below, which would otherwise raise TypeError
            # and break this module's "malformed input raises AdmissionJournalError" contract.
            raise AdmissionJournalError(
                f"{where}: record must be an object, got {type(rec).__name__}")
        stamped = _validate({**rec, "issue_key": self.issue_key}, where)
        if precheck is not None:
            precheck(records)          # raises AdmissionJournalError on a refused transition
        payload = (json.dumps(stamped, sort_keys=True) + "\n").encode("utf-8")
        # PROSPECTIVE limits: checking only the pre-append state lets one legal append cross the
        # cap and leave a journal that every later read refuses — a self-inflicted wedge.
        size = os.fstat(fd).st_size
        if size + len(payload) > MAX_JOURNAL_BYTES:
            raise AdmissionJournalError(
                f"{where}: append would reach {size + len(payload)} bytes, over cap "
                f"{MAX_JOURNAL_BYTES}")
        if len(records) + 1 > MAX_JOURNAL_RECORDS:
            raise AdmissionJournalError(
                f"{where}: append would reach {len(records) + 1} records, over cap "
                f"{MAX_JOURNAL_RECORDS}")
        os.lseek(fd, 0, os.SEEK_END)
        _write_all(fd, payload, where)
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
