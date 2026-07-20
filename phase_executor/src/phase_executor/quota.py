"""Inter-process concurrency quota (owner decision, #424 finding f1).

A process-local semaphore cannot enforce the Claude ceiling across separate OS processes
(a 2nd session, a hook, a worker). `QuotaCoordinator` uses a filesystem permit directory guarded
by an advisory `flock`, keyed by (pool, account), so the ceiling holds across ALL processes on the
host that share the same permit root.

Contract:
- One permit == one live token file under ``<root>/<pool>/<account>/permit-*``.
- Acquire: flock the pool dir, reap stale tokens (dead holder pid, or older than ``stale_after``),
  count live tokens; if under the pool limit, create a token and return; else retry until timeout.
- Release: unlink the token (idempotent). Acquire is a context manager so release runs in a
  ``finally`` on every path — success, exception, timeout during hold, cancellation.
"""
from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import re
import time
import uuid
from pathlib import Path
from typing import Callable, Dict, Optional

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


class QuotaTimeout(RuntimeError):
    """Raised when a permit could not be acquired within the timeout."""


def _safe(name: str) -> str:
    s = _UNSAFE.sub("_", str(name).strip()) or "default"
    return s[:128]


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno != errno.ESRCH  # EPERM => alive but not ours
    return True


@contextlib.contextmanager
def _flock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class QuotaCoordinator:
    """Cross-process, pool-keyed permit coordinator.

    ``limits`` maps pool name -> max concurrent permits. ``root`` is the shared permit tree
    (all processes that must share a ceiling pass the same root). ``clock`` is injectable for
    tests.
    """

    def __init__(
        self,
        root: os.PathLike | str,
        limits: Dict[str, int],
        *,
        stale_after: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ):
        self.root = Path(root)
        self.limits = dict(limits)
        self.stale_after = stale_after
        self._clock = clock
        self._wall = wall_clock

    def _pool_dir(self, pool: str, account: str) -> Path:
        return self.root / _safe(pool) / _safe(account)

    def _reap_stale(self, pool_dir: Path) -> None:
        now = self._wall()
        for token in pool_dir.glob("permit-*"):
            try:
                pid_str = token.read_text(encoding="utf-8").splitlines()[0].strip()
                pid = int(pid_str)
            except (OSError, ValueError, IndexError):
                pid = -1
            if pid > 0:
                # Known holder: reap ONLY if the process is dead. Never age-reap a live holder —
                # a legitimately long-running call (> stale_after) must keep its permit, else another
                # process could acquire the slot and exceed the ceiling.
                stale = not _pid_alive(pid)
            else:
                # Unknown/unparseable holder (e.g. a crash mid-write): fall back to age.
                try:
                    stale = (now - token.stat().st_mtime) > self.stale_after
                except OSError:
                    stale = True
            if stale:
                try:
                    token.unlink()
                except OSError:
                    pass

    def live_permits(self, pool: str, account: str = "default") -> int:
        """Count non-stale permits currently held for (pool, account)."""
        pool_dir = self._pool_dir(pool, account)
        if not pool_dir.exists():
            return 0
        with _flock(pool_dir / ".lock"):
            self._reap_stale(pool_dir)
            return len(list(pool_dir.glob("permit-*")))

    def reestablish_permit(self, pool: str, permit_ref: str) -> bool:
        """#467 D-12: re-key a recover-ADOPTED permit to THIS live process's pid so the pool
        ceiling keeps counting the adopted job across the recovery boundary. After the launching
        orchestrator exits, its permit token names a DEAD pid, so ``_reap_stale`` frees the slot
        and the pool over-admits past the ceiling for the still-live adopted job. The new
        orchestrator that adopts the job calls this to re-establish ownership.

        Serialized by the SAME pool flock ``acquire``/``_reap_stale`` hold, so two orchestrators
        adopting the same job race for the one token: whoever takes the lock while the owner pid
        is dead rewrites it to its own pid and wins; the other then sees a LIVE owner that is not
        itself and stands down. Neither creates a second token — the ceiling holds (no
        double-admit).

        Returns True iff this process owns the permit afterwards; False iff a live process already
        re-established it (the loser stands down — the job is permitted, no double-admit). Raises
        ``QuotaTimeout`` (fail-closed) if the token was already reaped and reclaiming a slot would
        exceed the ceiling — refuse the adoption rather than over-admit."""
        if permit_ref == "unbounded" or self.limits.get(pool) is None:
            return True  # unbounded pool: nothing to count
        token = Path(permit_ref)
        pool_dir = token.parent  # the token's own pool dir is authoritative for the flock/count
        mypid = os.getpid()
        with _flock(pool_dir / ".lock"):
            if token.exists():
                try:
                    cur = int(token.read_text(encoding="utf-8").splitlines()[0].strip())
                except (OSError, ValueError, IndexError):
                    cur = -1
                if cur == mypid:
                    return True  # idempotent re-adopt
                if cur > 0 and _pid_alive(cur):
                    return False  # a live owner already re-established it — stand down
                # owner dead (the launcher exited — D-12) or unparseable: re-key in place
                token.write_text(f"{mypid}\n{self._wall()}\n", encoding="utf-8")
                return True
            # token already reaped: reclaim a slot ONLY if under the ceiling (else refuse)
            pool_dir.mkdir(parents=True, exist_ok=True)
            self._reap_stale(pool_dir)
            if len(list(pool_dir.glob("permit-*"))) < self.limits[pool]:
                token.write_text(f"{mypid}\n{self._wall()}\n", encoding="utf-8")
                return True
            raise QuotaTimeout(
                f"pool {pool!r} full — cannot re-establish adopted permit {permit_ref!r} "
                f"without exceeding the ceiling (#467 D-12 fail-closed)")

    @contextlib.contextmanager
    def acquire(
        self,
        pool: str,
        account: str = "default",
        *,
        timeout: float = 300.0,
        poll: float = 0.05,
    ):
        """Acquire one permit for (pool, account); release on exit. Raises QuotaTimeout if the
        pool stays full past ``timeout``. A pool with no configured limit is unbounded (no-op)."""
        limit = self.limits.get(pool)
        if limit is None:
            yield None  # unbounded pool: no gating
            return
        pool_dir = self._pool_dir(pool, account)
        pool_dir.mkdir(parents=True, exist_ok=True)
        lock = pool_dir / ".lock"
        deadline = self._clock() + timeout
        token: Optional[Path] = None
        while True:
            with _flock(lock):
                self._reap_stale(pool_dir)
                live = len(list(pool_dir.glob("permit-*")))
                if live < limit:
                    token = pool_dir / f"permit-{os.getpid()}-{uuid.uuid4().hex}"
                    token.write_text(f"{os.getpid()}\n{self._wall()}\n", encoding="utf-8")
                    break
            if self._clock() >= deadline:
                raise QuotaTimeout(f"pool {pool!r} full ({limit}) for account {account!r} after {timeout}s")
            time.sleep(poll)
        try:
            yield token
        finally:
            try:
                token.unlink()
            except OSError:
                pass
