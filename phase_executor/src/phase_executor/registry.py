"""#467 W4 — durable job registry for the tmux supervisor (extraction-clean; NO hooks import).

Pure decision core (session_name, command_digest, handle_from_record, classify_recovery, reap_plan)
+ an atomic ``JobRegistry`` (jobs.json, 0700). A post-compaction orchestrator reconstructs everything
it needs — the WorktreeHandle (for W3 inspect/finalize), both process groups (for the two-group kill),
the quota permit — from the durable record; NOTHING about a job is knowable only from memory.

The recovery trust boundary (OQ-8): a found tmux session is ADOPTED only on a full identity match,
else QUARANTINED. The reaper gates every kill on CONFIRMED process-death (both the pane group AND the
provider's own group — the in-pane adapter start_new_sessions the provider, CF-17) BEFORE the
worktree-clean probe, and NEVER sweeps a live+fresh-mtime session.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import capture
from .worktree import WorktreeIdentity, WorktreeHandle, component_for

MAX_RESUME = 2  # CF-6: hard cap on --resume relaunches before a job is failed (bounds the loop)


class RegistryCorrupt(RuntimeError):
    """jobs.json exists but cannot be read/parsed — fail-loud, never an empty view."""

# OQ-8 job states. `completed`/`completed_with_residue` = a valid sentinel collected; the rest are
# the abnormal/in-flight states the reaper + recover reason over.
JOB_STATES = frozenset({
    "launched", "running", "exited_no_sentinel", "quota_paused", "timed_out",
    "completed", "completed_with_residue", "failed", "quarantined",
})
_FINALIZED = frozenset({"completed", "completed_with_residue"})


@dataclass(frozen=True)
class JobRecord:
    identity: WorktreeIdentity
    session_name: str
    run_socket: str
    pane_pid: int
    pane_pgid: int
    provider_pgid: Optional[int]      # CF-17: the adapter's start_new_session group (distinct from pane)
    pane_start_time: str              # /proc/<pane_pid>/stat field 22 — a PID-reuse guard
    worktree_path: str                # the 6 WorktreeHandle fields (CF-4 — rehydrate for inspect/finalize)
    worktree_base_sha: str
    worktree_root: str
    worktree_gitdir: str
    worktree_repo: str
    capture_dir: str
    attempt_id: str
    permit_ref: str                   # CF-19: the quota permit the SUPERVISOR holds for this job
    command_digest: str
    provider_session_id: Optional[str]
    provider_exit_code: Optional[int]
    resume_attempts: int
    state: str
    created_at: float
    quarantine_reason: Optional[str]
    spec_digest: Optional[str] = None  # sha256 of the FULL serialized pane spec (adoption trust,
    #                                    Step-11 codex #4 — argv digest alone misses spec content)
    receipt_nonce: Optional[str] = None  # Step-11 RH1 (#470): the check_pre enforcement receipt
    #                                      this launch was authorized under — joins the JobRecord
    #                                      to the RoutingAuditLog receipt (additive; None pre-#470)
    recovered_from: Optional[str] = None  # #554: on a recovery relaunch, the ORIGINAL call's
    #                                       correlation_id — the reconcile pause/recover join key,
    #                                       so a resumed call reconciles as ONE authorized attempt
    #                                       of the original (additive; None on a first launch)


@dataclass(frozen=True)
class ReapPolicy:
    max_age_s: int = 604800  # quarantined+clean is only auto-killed past this age


@dataclass
class ReapPlan:
    kill_session: list = field(default_factory=list)   # finalized/aged-clean → remove the session
    kill_tree: list = field(default_factory=list)      # a wedged LIVE child → kill both groups first
    quarantine: list = field(default_factory=list)     # (reserved) mark owner-visible
    retain_worktree: list = field(default_factory=list)  # dead + dirty → W3 retain, never kill
    keep: list = field(default_factory=list)           # live+fresh, or not-yet-aged quarantine


# ---------------------------------------------------------------------------
# pure decision core
# ---------------------------------------------------------------------------


def session_name(identity: WorktreeIdentity) -> str:
    """``rg-<run>-<seat>-<attempt>`` via W3 ``component_for`` (sanitize + sha256[:8] — defeats a
    tmux-name collision; `.`/`:` (tmux-reserved for pane/window addressing) map to `_`)."""
    return "rg-" + "-".join(
        component_for(x) for x in (identity.run_id, identity.seat, identity.attempt))


def command_digest(argv) -> str:
    h = hashlib.sha256()
    h.update("\x00".join(str(a) for a in argv).encode("utf-8"))
    return f"sha256:{h.hexdigest()}"


def handle_from_record(record: JobRecord) -> WorktreeHandle:
    """Reconstruct the frozen WorktreeHandle W3 ``inspect``/``finalize`` need (CF-4) — all six fields
    are filesystem-durable, so a post-compaction orchestrator rehydrates a valid handle from disk."""
    return WorktreeHandle(
        path=record.worktree_path, identity=record.identity, base_sha=record.worktree_base_sha,
        root=record.worktree_root, gitdir=record.worktree_gitdir, repo=record.worktree_repo)


def classify_recovery(record: JobRecord, *, live: bool, identity_matches: bool,
                      sentinel_valid: bool) -> str:
    """`adopt` iff a live session with a FULL identity/digest/worktree/capture match (`identity_matches`
    is the caller's AND of those); `relaunch` iff quota_paused + not live + under MAX_RESUME, else
    `fail`; a completed-with-valid-sentinel dead job re-adopts its result; anything else quarantines
    (CF-6/CF-7 — a mismatch is never silently adopted; the caller kills+retains it)."""
    # identity FIRST across the whole trust boundary (Step-11 codex Critical): a known
    # mismatch is NEVER relaunched or adopted — a tampered/corrupted recovery spec must
    # quarantine even on the quota_paused path.
    if not identity_matches:
        return "quarantine"
    if record.state == "quota_paused" and not live:
        return "relaunch" if record.resume_attempts < MAX_RESUME else "fail"
    if live:
        return "adopt"
    if sentinel_valid:
        return "adopt"
    return "quarantine"


def _aged(record: JobRecord, now: float, policy: ReapPolicy) -> bool:
    return (now - record.created_at) > policy.max_age_s


def reap_plan(records, *, live_fresh, now: float, policy: ReapPolicy,
              dead_fn: Callable[[JobRecord], bool], clean_fn: Callable[[JobRecord], bool]) -> ReapPlan:
    """AC-E6 three tiers as a pure function. ``live_fresh`` = session names that are live AND
    fresh-mtime (DERIVED liveness, CF-8 — never swept). Every kill gates on ``dead_fn`` (BOTH groups
    confirmed dead, CF-17) BEFORE ``clean_fn`` (the worktree-clean probe is TOCTOU-torn on a live
    tree, CF-8). A wedged LIVE child (not dead) → kill the tree first; dead+dirty → retain, never
    kill the worktree; quarantined+clean+aged+dead → remove."""
    plan = ReapPlan()
    for r in records:
        if r.session_name in live_fresh:
            plan.keep.append(r)
            continue
        if not dead_fn(r):                       # still-alive process → kill the tree before any sweep
            plan.kill_tree.append(r)
            continue
        # confirmed dead below this line
        clean = clean_fn(r)
        if r.state == "quarantined":
            if clean and _aged(r, now, policy):
                plan.kill_session.append(r)
            elif not clean:
                plan.retain_worktree.append(r)   # dirty quarantine → retain evidence, never kill
            else:
                plan.keep.append(r)              # clean but not yet aged → owner-visible
            continue
        if r.state in _FINALIZED:
            plan.kill_session.append(r)
            continue
        # dead, non-finalized, non-quarantined (exited_no_sentinel / timed_out): dirty → retain, else kill
        (plan.retain_worktree if not clean else plan.kill_session).append(r)
    return plan


# ---------------------------------------------------------------------------
# atomic durable registry
# ---------------------------------------------------------------------------


def _identity_dict(i: WorktreeIdentity) -> dict:
    return {"run_id": i.run_id, "seat": i.seat, "attempt": i.attempt}


def _record_to_dict(r: JobRecord) -> dict:
    return {
        "identity": _identity_dict(r.identity), "session_name": r.session_name,
        "run_socket": r.run_socket, "pane_pid": r.pane_pid, "pane_pgid": r.pane_pgid,
        "provider_pgid": r.provider_pgid, "pane_start_time": r.pane_start_time,
        "worktree_path": r.worktree_path, "worktree_base_sha": r.worktree_base_sha,
        "worktree_root": r.worktree_root, "worktree_gitdir": r.worktree_gitdir,
        "worktree_repo": r.worktree_repo, "capture_dir": r.capture_dir, "attempt_id": r.attempt_id,
        "permit_ref": r.permit_ref, "command_digest": r.command_digest,
        "spec_digest": r.spec_digest, "receipt_nonce": r.receipt_nonce,
        "recovered_from": r.recovered_from,
        "provider_session_id": r.provider_session_id, "provider_exit_code": r.provider_exit_code,
        "resume_attempts": r.resume_attempts, "state": r.state, "created_at": r.created_at,
        "quarantine_reason": r.quarantine_reason,
    }


def _record_from_dict(d: dict) -> JobRecord:
    idn = d["identity"]
    return JobRecord(
        identity=WorktreeIdentity(run_id=idn["run_id"], seat=idn["seat"], attempt=idn["attempt"]),
        session_name=d["session_name"], run_socket=d["run_socket"], pane_pid=d["pane_pid"],
        pane_pgid=d["pane_pgid"], provider_pgid=d.get("provider_pgid"),
        pane_start_time=d["pane_start_time"], worktree_path=d["worktree_path"],
        worktree_base_sha=d["worktree_base_sha"], worktree_root=d["worktree_root"],
        worktree_gitdir=d["worktree_gitdir"], worktree_repo=d["worktree_repo"],
        capture_dir=d["capture_dir"], attempt_id=d["attempt_id"], permit_ref=d["permit_ref"],
        command_digest=d["command_digest"], spec_digest=d.get("spec_digest"),
        receipt_nonce=d.get("receipt_nonce"), recovered_from=d.get("recovered_from"),
        provider_session_id=d.get("provider_session_id"),
        provider_exit_code=d.get("provider_exit_code"), resume_attempts=d["resume_attempts"],
        state=d["state"], created_at=d["created_at"], quarantine_reason=d.get("quarantine_reason"))


class JobRegistry:
    """``<registry_root>/jobs.json`` — an atomic map keyed by session_name, 0700.
    ponytail: single-writer read-modify-write (one orchestrator). If launch/recover ever run
    concurrently, guard upsert with an flock on the index (the W3 CF-6 lesson)."""

    def __init__(self, registry_root: str, *, clock=None):
        self._root = registry_root
        if clock is None:
            import time  # noqa: PLC0415

            clock = time.time
        self._clock = clock
        os.makedirs(self._root, exist_ok=True)
        os.chmod(self._root, 0o700)

    def _file(self) -> str:
        return os.path.join(self._root, "jobs.json")

    def _read(self) -> dict:
        """Missing file = empty registry (first run). A PRESENT-but-unreadable/corrupt file
        raises RegistryCorrupt: this store is what the kill/reap paths trust post-compaction,
        and silently returning {} would let the next upsert's read-modify-write PERSIST the
        empty view — orphaning every live pane/provider and leaking their permits (fail-open
        in a fail-closed-critical store; 8a R2 finding)."""
        try:
            with open(self._file(), encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise RegistryCorrupt(f"jobs.json unreadable/corrupt at {self._file()}: {exc}") from exc

    def _write(self, data: dict) -> None:
        capture.atomic_write_text(Path(self._file()), json.dumps(data, indent=2, sort_keys=True))

    def upsert(self, record: JobRecord) -> None:
        # key by the CANONICAL session_name(identity) so upsert/get always agree (one identity =
        # one job), independent of the record's informational session_name field.
        data = self._read()
        data[session_name(record.identity)] = _record_to_dict(record)
        self._write(data)

    def get(self, identity: WorktreeIdentity) -> Optional[JobRecord]:
        want = session_name(identity)
        d = self._read().get(want)
        return _record_from_dict(d) if d else None

    def by_run(self, run_id: str) -> list:
        return [r for r in self.all() if r.identity.run_id == run_id]

    def all(self) -> list:
        return [_record_from_dict(d) for d in self._read().values()]


def read_all(registry_root: str) -> list:
    """READ-ONLY view of ``<registry_root>/jobs.json`` (#471 AC-J3): unlike constructing a
    ``JobRegistry`` (whose ``__init__`` mkdir/chmods the root — a metadata write), this
    touches nothing. Missing file = empty (first run); present-but-corrupt raises
    ``RegistryCorrupt`` (same fail-loud contract as ``JobRegistry._read``)."""
    path = os.path.join(registry_root, "jobs.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise RegistryCorrupt(f"jobs.json unreadable/corrupt at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryCorrupt(f"jobs.json at {path} is not an object (got {type(data).__name__})")
    try:
        return [_record_from_dict(d) for d in data.values()]
    except (KeyError, TypeError, AttributeError) as exc:
        # a structurally-malformed record is the same corruption class as unparseable
        # JSON (gpt-diff A1) — never a bare KeyError escaping to the caller
        raise RegistryCorrupt(f"jobs.json at {path} holds a malformed record: {exc!r}") from exc
