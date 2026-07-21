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

Hardening (AC1), all fail-closed (raise ``LedgerError``): the leaf is opened ``O_NOFOLLOW`` (a
symlinked ledger is refused — a codex child's workspace-write cannot redirect it); a byte cap and a
record cap bound a tampered/huge file; every record carries the run_id and a mismatch is refused;
the FIRST record must be ``initial`` (exactly once); ``run_closed`` may appear only as the LAST
record and only once; no append after ``run_closed``; duplicate ``(seat, correlation_id)`` among the
``expected`` records is refused. A malformed line, a bad kind, or a missing field raises — the
ledger is a security boundary, never silently skipped.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .enforce import ExpectedCall

# Bounds — a real run has O(10²) calls; these are runaway/tamper backstops, not tuning knobs.
MAX_LEDGER_BYTES = 4 * 1024 * 1024   # 4 MiB
MAX_LEDGER_RECORDS = 100_000

_KINDS = frozenset({"initial", "expected", "run_closed"})
_LEDGER_NAME = "expected-calls.jsonl"


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
        return data.decode("utf-8")

    def read(self) -> LedgerState:
        """Parse + fully validate the ledger. Fail-closed on ANY anomaly. An absent ledger is a
        valid empty/open state (initial_digest=None, no expected, not closed)."""
        text = self._read_text_nofollow()
        if text is None:
            return LedgerState()
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) > MAX_LEDGER_RECORDS:
            raise LedgerError(
                f"ledger {self.path}: {len(lines)} records exceeds cap {MAX_LEDGER_RECORDS}")
        initial_digest: Optional[str] = None
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
        return LedgerState(initial_digest=initial_digest, expected=expected, closed=closed)

    # -- appending -----------------------------------------------------------

    def _append_line_nofollow(self, rec: dict) -> None:
        """Append one JSON line, creating the leaf O_NOFOLLOW|O_APPEND (a pre-planted symlink at the
        leaf is refused). The directory is the supervisor-owned 0700 run dir."""
        rec = {"run_id": self.run_id, **rec}
        payload = (json.dumps(rec, sort_keys=True) + "\n").encode("utf-8")
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        except OSError as e:  # ELOOP (symlinked leaf) fails closed
            raise LedgerError(f"ledger {self.path}: unopenable for append ({e})") from e
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)

    def append_initial(self, initial_digest: str) -> None:
        st = self.read()
        if st.initial_digest is not None or st.expected or st.closed:
            raise LedgerError(f"ledger {self.path}: initial must be the first and only 'initial'")
        if not isinstance(initial_digest, str) or not initial_digest:
            raise LedgerError("append_initial: initial_digest must be a non-empty string")
        self._append_line_nofollow({"kind": "initial", "initial_digest": initial_digest})

    def append_expected(self, seat: str, correlation_id: str,
                        recovered_from: Optional[str] = None) -> None:
        st = self.read()  # read-validate the whole ledger first (fail-closed)
        if st.initial_digest is None:
            raise LedgerError(f"ledger {self.path}: append_expected before append_initial")
        if st.closed:
            raise LedgerError(f"ledger {self.path}: run_closed — dispatch refused (#555 AC2)")
        if any(e.seat == seat and e.correlation_id == correlation_id for e in st.expected):
            raise LedgerError(f"ledger {self.path}: duplicate expected call ({seat}, {correlation_id})")
        self._append_line_nofollow({"kind": "expected", "seat": seat,
                                    "correlation_id": correlation_id,
                                    "recovered_from": recovered_from})

    def append_run_closed(self) -> None:
        st = self.read()
        if st.closed:
            raise LedgerError(f"ledger {self.path}: already run_closed")
        self._append_line_nofollow({"kind": "run_closed"})

    def is_closed(self) -> bool:
        return self.read().closed
