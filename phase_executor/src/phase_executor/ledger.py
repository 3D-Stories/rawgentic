"""#555 (H2) — the expected-call ledger.

An append-only, per-run record of every seat call the orchestrator EXPECTS to dispatch, plus a
terminal ``run_closed`` marker. It gives the dispatch choke-point a fail-closed answer to "may a
new call still start?" (no, once ``run_closed``) and gives ``reconcile_run`` the authoritative
expected-set to bind the routing audit against — so "zero uninstrumented dispatch" (#472 AC1) is
enforceable AT the choke-point, not by convention.

Home: ``phase_executor`` (NOT ``hooks``) — ``enforce.reconcile_run`` and the ``reconcile`` CLI verb
consume it without importing hooks; the hooks-side choke-point calls ``append_expected`` /
``append_run_closed`` / ``read``. Sibling of the run's ``routing-audit.jsonl`` under
``<capture_root>/<run_id>/``.

Threat model (honest, #555 8a review): the PRIMARY containment is worktree ISOLATION — a mutating
(codex) child runs in its own worktree with no write path to the main checkout's
``.rawgentic/runs/``, so it cannot reach this ledger. The file-level hardening below is
defense-in-depth against an OTHER-uid actor and accidental corruption; it does NOT defend a
same-uid writer with run-dir access (``O_NOFOLLOW`` refuses a symlinked leaf but not a hardlink, and
a 0600 file is same-uid-writable). Isolation is the boundary, not the file mode.

Hardening (AC1), all fail-closed (raise ``LedgerError``): the leaf is opened ``O_NOFOLLOW`` (a
symlinked leaf is refused); every mutation is an atomic read-check-append under an exclusive
``flock`` (two concurrent dispatch processes cannot both seed 'initial' nor slip an 'expected' past
a concurrent close — matching RoutingAuditLog's every-append lock); a byte cap and a record cap
bound a tampered/huge file; every record carries the run_id and a mismatch is refused; the FIRST
record must be ``initial`` (exactly once); ``run_closed`` may appear only as the LAST record and only
once; no append after ``run_closed``; duplicate ``(seat, correlation_id)`` among the ``expected``
records is refused. A malformed line, a bad kind, a non-UTF-8 byte, or a missing field raises —
never silently skipped.
"""
from __future__ import annotations

import fcntl
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from .enforce import ExpectedCall

# Bounds — a real run has O(10²) calls; these are runaway/tamper backstops, not tuning knobs.
MAX_LEDGER_BYTES = 4 * 1024 * 1024   # 4 MiB
MAX_LEDGER_RECORDS = 100_000

_KINDS = frozenset({"initial", "expected", "run_closed"})
_LEDGER_NAME = "expected-calls.jsonl"
# #474: the run-level architecture vocabulary. Two-valued for forward-compat even though the
# begin-run producer only ever writes "executor" (legacy runs have no ledger).
ARCHITECTURES = frozenset({"executor", "legacy"})


class LedgerError(RuntimeError):
    """Fail-loud ledger failure (symlink/oversized/tampered/malformed, or an illegal transition)."""


@dataclass(frozen=True)
class LedgerExpected:
    """One expected call, with #554 recovery provenance (recovered_from = the original call's
    correlation_id, or None for a first dispatch)."""
    seat: str
    correlation_id: str
    recovered_from: Optional[str] = None

    def as_expected_call(self) -> ExpectedCall:
        """The (seat, correlation_id) identity reconcile_run keys on (recovery join lives on the
        audit receipt's recovered_from — the ledger merely records provenance for the audit)."""
        return ExpectedCall(self.seat, self.correlation_id)


@dataclass(frozen=True)
class LedgerState:
    initial_digest: Optional[str] = None
    expected: List[LedgerExpected] = field(default_factory=list)
    closed: bool = False
    # #474: the run's pinned architecture. None = a pre-3.93 ledger (compat: every ≤3.92
    # producer was an executor CLI path — the legacy path writes no ledger — so None is
    # establishable as executor BY CONSTRUCTION; consumers treat it as executor + advisory,
    # bounded to the 3.93.x line).
    architecture: Optional[str] = None


class ExpectedCallLedger:
    """Append + hardened-parse the per-run ``expected-calls.jsonl``. Every mutation reads-validates
    the current file first (fail-closed) and appends only a legal next record."""

    def __init__(self, run_dir, run_id: str) -> None:
        self.run_id = run_id
        self.path = Path(run_dir) / _LEDGER_NAME

    # -- reading -------------------------------------------------------------

    def _read_text_nofollow(self) -> Optional[str]:
        """Read the leaf with O_NOFOLLOW (a symlink → OSError → LedgerError) under the byte cap.
        Returns None when the ledger does not exist yet (a legitimately empty, open run)."""
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        except OSError as e:  # ELOOP (symlink) and friends fail closed
            raise LedgerError(f"ledger {self.path}: unopenable ({e})") from e
        try:
            st = os.fstat(fd)
            if st.st_size > MAX_LEDGER_BYTES:
                raise LedgerError(
                    f"ledger {self.path}: {st.st_size} bytes exceeds cap {MAX_LEDGER_BYTES}")
            data = os.read(fd, MAX_LEDGER_BYTES + 1)
        finally:
            os.close(fd)
        if len(data) > MAX_LEDGER_BYTES:
            raise LedgerError(f"ledger {self.path}: exceeds byte cap {MAX_LEDGER_BYTES}")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as e:  # a corrupted/tampered non-UTF-8 ledger fails CLOSED, never
            raise LedgerError(f"ledger {self.path}: not valid UTF-8 ({e})") from e  # a bare crash

    def read(self) -> LedgerState:
        """Parse + fully validate the ledger. Fail-closed on ANY anomaly. An absent ledger is a
        valid empty/open state (initial_digest=None, no expected, not closed)."""
        text = self._read_text_nofollow()
        if text is None:
            return LedgerState()
        return self._parse(text)

    def _parse(self, text: str) -> LedgerState:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) > MAX_LEDGER_RECORDS:
            raise LedgerError(
                f"ledger {self.path}: {len(lines)} records exceeds cap {MAX_LEDGER_RECORDS}")
        initial_digest: Optional[str] = None
        architecture: Optional[str] = None
        expected: List[LedgerExpected] = []
        seen_keys = set()
        closed = False
        for i, ln in enumerate(lines, 1):
            try:
                rec = json.loads(ln)
            except json.JSONDecodeError as e:
                raise LedgerError(f"ledger {self.path} line {i}: malformed JSON ({e})") from e
            if not isinstance(rec, dict):
                raise LedgerError(f"ledger {self.path} line {i}: not a JSON object")
            kind = rec.get("kind")
            if kind not in _KINDS:
                raise LedgerError(f"ledger {self.path} line {i}: unknown kind {kind!r}")
            if rec.get("run_id") != self.run_id:
                raise LedgerError(
                    f"ledger {self.path} line {i}: run_id {rec.get('run_id')!r} != {self.run_id!r}")
            if closed:  # nothing may follow run_closed
                raise LedgerError(f"ledger {self.path} line {i}: record after run_closed")
            if i == 1:
                if kind != "initial":
                    raise LedgerError(f"ledger {self.path}: first record is {kind!r}, not 'initial'")
                digest = rec.get("initial_digest")
                if not isinstance(digest, str) or not digest:
                    raise LedgerError(f"ledger {self.path} line 1: missing initial_digest")
                initial_digest = digest
                arch = rec.get("architecture")  # absent on ≤3.92 ledgers → None (compat)
                if arch is not None and arch not in ARCHITECTURES:
                    raise LedgerError(
                        f"ledger {self.path} line 1: architecture {arch!r} not in {sorted(ARCHITECTURES)}")
                architecture = arch
                continue
            if kind == "initial":
                raise LedgerError(f"ledger {self.path} line {i}: a second 'initial' record")
            if kind == "run_closed":
                closed = True
                continue
            # kind == "expected"
            seat, cid = rec.get("seat"), rec.get("correlation_id")
            if not isinstance(seat, str) or not seat or not isinstance(cid, str) or not cid:
                raise LedgerError(f"ledger {self.path} line {i}: expected missing seat/correlation_id")
            rf = rec.get("recovered_from")
            if not (rf is None or isinstance(rf, str)):
                raise LedgerError(f"ledger {self.path} line {i}: recovered_from not a string-or-null")
            key = (seat, cid)
            if key in seen_keys:
                raise LedgerError(f"ledger {self.path} line {i}: duplicate expected call {key}")
            seen_keys.add(key)
            expected.append(LedgerExpected(seat, cid, rf))
        return LedgerState(initial_digest=initial_digest, expected=expected, closed=closed,
                           architecture=architecture)

    # -- appending -----------------------------------------------------------

    def _locked_append(self, rec: dict, precheck: Callable[[LedgerState], None]) -> None:
        """Atomic read-check-append under an exclusive flock on the leaf (matching the
        RoutingAuditLog's every-append lock). The lock makes the check-then-append indivisible
        across the ≤3 concurrent dispatch PROCESSES, so two concurrent first-dispatches cannot
        both write 'initial' and a dispatch cannot slip an 'expected' past a concurrent close
        (#555 8a review F1/F3 — the AC2 refusal is enforced at the choke-point, not by
        convention). The leaf is opened O_NOFOLLOW (a symlinked leaf is refused); O_CREAT makes
        the first append self-seed the file. ``precheck`` validates the freshly-parsed state
        HELD under the lock and raises LedgerError on an illegal transition."""
        os.makedirs(self.path.parent, mode=0o700, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as e:  # ELOOP (symlinked leaf) and friends fail closed
            raise LedgerError(f"ledger {self.path}: unopenable for append ({e})") from e
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            st = os.fstat(fd)
            if st.st_size > MAX_LEDGER_BYTES:
                raise LedgerError(f"ledger {self.path}: {st.st_size} bytes exceeds cap")
            data = os.read(fd, MAX_LEDGER_BYTES + 1)
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as e:
                raise LedgerError(f"ledger {self.path}: not valid UTF-8 ({e})") from e
            state = self._parse(text) if text.strip() else LedgerState()
            precheck(state)  # raises LedgerError on an illegal transition (HELD under the lock)
            payload = (json.dumps({"run_id": self.run_id, **rec}, sort_keys=True) + "\n").encode("utf-8")
            os.lseek(fd, 0, os.SEEK_END)
            os.write(fd, payload)
        finally:
            os.close(fd)  # releases the flock

    def append_initial(self, initial_digest: str, *, architecture: str) -> None:
        """#474: the initial record pins the run's architecture (BREAKING: the kwarg is
        required — a declaration without an architecture is exactly the pre-flip ambiguity
        this record exists to remove)."""
        if not isinstance(initial_digest, str) or not initial_digest:
            raise LedgerError("append_initial: initial_digest must be a non-empty string")
        if architecture not in ARCHITECTURES:
            raise LedgerError(
                f"append_initial: architecture {architecture!r} not in {sorted(ARCHITECTURES)}")

        def _check(st: LedgerState) -> None:
            if st.initial_digest is not None or st.expected or st.closed:
                raise LedgerError(f"ledger {self.path}: initial must be the first and only 'initial'")
        self._locked_append({"kind": "initial", "initial_digest": initial_digest,
                             "architecture": architecture}, _check)

    def append_expected(self, seat: str, correlation_id: str,
                        recovered_from: Optional[str] = None,
                        *, expected_architecture: Optional[str] = None) -> None:
        """``expected_architecture`` (#474, optional): assert the run's pinned architecture
        UNDER the same flock that appends — no read-then-append window. A ``None`` pinned
        architecture (pre-3.93 ledger) passes the assertion (bounded compat window)."""
        def _check(st: LedgerState) -> None:
            if st.initial_digest is None:
                raise LedgerError(f"ledger {self.path}: append_expected before append_initial")
            if (expected_architecture is not None and st.architecture is not None
                    and st.architecture != expected_architecture):
                raise LedgerError(
                    f"ledger {self.path}: run architecture {st.architecture!r} != expected "
                    f"{expected_architecture!r} — mixed-architecture dispatch refused (#474)")
            if st.closed:
                raise LedgerError(f"ledger {self.path}: run_closed — dispatch refused (#555 AC2)")
            if any(e.seat == seat and e.correlation_id == correlation_id for e in st.expected):
                raise LedgerError(
                    f"ledger {self.path}: duplicate expected call ({seat}, {correlation_id})")
        self._locked_append({"kind": "expected", "seat": seat, "correlation_id": correlation_id,
                             "recovered_from": recovered_from}, _check)

    def append_run_closed(self) -> None:
        def _check(st: LedgerState) -> None:
            if st.initial_digest is None:
                raise LedgerError(f"ledger {self.path}: run_closed before initial")
            if st.closed:
                raise LedgerError(f"ledger {self.path}: already run_closed")
        self._locked_append({"kind": "run_closed"}, _check)

    def is_closed(self) -> bool:
        return self.read().closed
