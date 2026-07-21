"""#467 W4 — TmuxSupervisor: async seat dispatch on a PRIVATE tmux server (extraction-clean;
NO hooks import).

The spawn path that survives orchestrator compaction: a dedicated tmux server on a short
private socket OUTSIDE /tmp owns the pane process (`python -m phase_executor.pane_runner
<spec>` as the pane's INITIAL process — never send-keys, F7); completion is process exit +
an atomic ``observation.json`` sentinel at a path the supervisor fixed at launch (AC-E3).

Lifecycle security through-line (CF-17, two-group + descendant-snapshot kill): the in-pane
adapter start_new_sessions the provider into its OWN process group, so every kill path
(a) snapshots the pane's /proc descendant set BEFORE signalling, (b) SIGTERMs the pane group
(pane_runner's handler kills the provider tree gracefully), (c) escalates to SIGKILL of the
pane group + every snapshot pid + the sidecar-surfaced provider pgid, then (d) verifies the
WHOLE snapshot and BOTH groups dead (Z-state = dead) before ``kill-session``. A verify
failure is ``completed_with_residue`` — handed to the reaper, never claimed clean.

``quota_paused`` is entered via collection-time detection (``quota_detect``, #558 —
shadow-gated behind the ``CALIBRATED_CLASSIFIERS`` allowlist, which ships EMPTY) or the
injected classification (``mark_quota_paused``); ``status`` defaults every ambiguous
nonzero exit to ``exited_no_sentinel`` (NO auto-resume, CF-6).
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import signal
import stat as stat_mod
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Optional, Tuple

from jsonschema import ValidationError as _SchemaError

from . import contract, quota_detect, routing
from .capture import atomic_write_text, ensure_private_dir, hash_text
from .engine import PROVIDER_ENGINE, _effective_timeout
from .pane_runner import _descendants, expected_capture_dir, sidecar_path
from .quota import QuotaTimeout
from .registry import (JobRecord, JobRegistry, ReapPlan, ReapPolicy, classify_recovery,
                       command_digest, handle_from_record, reap_plan, session_name)
from .worktree import WorktreeHandle, WorktreeIdentity, component_for

TMUX_VERSION_FLOOR = (3, 0)  # verbs probed individually below; 3.4 is the spike-verified build
_SUN_PATH_MAX = 108          # AF_UNIX sun_path limit (linux)
_KILL_GRACE_S = 5.0


class SupervisorError(RuntimeError):
    """Fail-loud supervisor failure (unusable socket, launch failure, spec error)."""


@dataclass(frozen=True)
class PreflightResult:
    supported: bool
    reason: str = ""


@dataclass(frozen=True)
class RecoveryAction:
    """One recover() verdict: ``action`` ∈ {adopt, quarantine, relaunch, fail};
    ``record`` is the post-action record (the NEW record for a relaunch)."""
    identity: WorktreeIdentity
    action: str
    record: JobRecord


@dataclass(frozen=True)
class _GuardProfile:
    """The two profile facts the #558 pause guards read, reconstructed from the
    VERIFIED spec (never trusted from an unverified read)."""
    session_policy: str
    mutating: bool


def _profile_from_spec(spec: dict) -> Optional[_GuardProfile]:
    """Strictly-validated guard profile from a digest-verified spec — a malformed
    profile inside a digest-valid spec is spec_unverified too (pass-4 S-F9)."""
    prof = (spec.get("request") or {}).get("profile")
    if not isinstance(prof, dict):
        return None
    sp = prof.get("session_policy")
    mut = prof.get("mutating")
    if sp not in ("fresh", "resume") or not isinstance(mut, bool):
        return None
    return _GuardProfile(session_policy=sp, mutating=mut)


@dataclass(frozen=True)
class PauseDecision:
    """#558: the typed auto-pause verdict — guards evaluate in declared order and the
    FIRST failing guard's reason is the persisted ``refusal`` (deterministic
    single-reason evidence, pass-4 A-F4)."""
    allowed: bool
    refusal: Optional[str] = None


#: #558 D-12 shadow gate: the EXACT (classifier_version, rule_table_digest) pairs
#: calibrated against a GENUINE captured usage-limit exit. Ships EMPTY — v1 and every
#: future uncalibrated pair runs in SHADOW (evidence persisted, auto-pause refused as
#: "uncalibrated_classifier"). #559's live proving run adds the first pair.
CALIBRATED_CLASSIFIERS: frozenset = frozenset()

_ENVELOPE_CEILING_BYTES = 256 * 1024  # bounded _envelope_meta prefix (pass-3 S-F4)
_ENVELOPE_SUBTYPE_MAXLEN = 64
_SESSION_ID_MAXLEN = 256  # 8a M2: claude session ids are UUIDs; anything huge is not one
_ENVELOPE_SUBTYPE_ALLOWLIST = frozenset({
    # known claude CLI result-envelope subtypes; anything else persists as
    # "unknown" + its sha256 — no raw provider prose reaches jobs.json (pass-4 A-F5)
    "success", "error", "error_during_execution", "error_max_budget_usd",
    "error_max_turns"})


def _default_run(cmd, *, env=None, cwd=None, timeout=30):
    return subprocess.run(list(cmd), capture_output=True, text=True, env=env, cwd=cwd,
                          timeout=timeout, check=False)


def _self_and_ancestors() -> set:
    """This process and its PPID chain — NEVER a kill target (defense-in-depth for the
    _kill_job guards)."""
    out = set()
    pid = os.getpid()
    while pid > 1 and pid not in out:
        out.add(pid)
        try:
            with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
                pid = int(fh.read().rsplit(")", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            break
    return out


def _pid_alive(pid: int) -> bool:
    """Alive = exists and not a zombie (tmux/parent reaps zombies; Z counts as dead, CF-17)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
            state = fh.read().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except OSError:
        return False


def _group_pids(pgid: int) -> set:
    """Live (non-zombie) pids currently in process group ``pgid`` (one /proc scan)."""
    out = set()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            if os.getpgid(pid) == pgid and _pid_alive(pid):
                out.add(pid)
        except OSError:
            continue
    return out


def resolve_socket(run_id: str, *, runtime_dir: Optional[str] = None,
                   state_dir: Optional[str] = None, tmpdir: Optional[str] = None) -> str:
    """Private-socket path for ``run_id`` (CF-2/CF-20): ``/run/user/$UID`` when usable, else
    ``~/.local/state/rawgentic/run`` (created 0700). Rejects a /tmp-or-$TMPDIR-contained dir
    (a codex child's workspace-write covers /tmp — #452) and a path at/over the AF_UNIX
    108-byte limit. Fail-loud on an unusable resolution."""
    if runtime_dir is None:
        runtime_dir = f"/run/user/{os.getuid()}"
    if state_dir is None:
        state_dir = os.path.join(os.path.expanduser("~"), ".local", "state", "rawgentic", "run")
    if tmpdir is None:
        tmpdir = os.environ.get("TMPDIR", "/tmp")
    if os.path.isdir(runtime_dir) and os.access(runtime_dir, os.W_OK):
        base = runtime_dir
    else:
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        os.chmod(state_dir, 0o700)
        base = state_dir
    resolved = os.path.realpath(base)
    for forbidden in ("/tmp", os.path.realpath(tmpdir)):
        if resolved == forbidden or resolved.startswith(forbidden.rstrip("/") + "/"):
            raise SupervisorError(
                f"socket dir {resolved!r} is contained in {forbidden!r} — refusing (#452)")
    sock = os.path.join(base, f"rg-{component_for(run_id)}.sock")
    if len(sock.encode("utf-8")) >= _SUN_PATH_MAX:
        raise SupervisorError(f"socket path {sock!r} >= {_SUN_PATH_MAX} bytes (AF_UNIX limit)")
    return sock


def synthetic_observation(*, run_id: str, seat: str, attempt_id: str, engine: str,
                          requested_model: str, prompt: str, parse_status: str,
                          reason: str, routing_config_digest: str,
                          correlation_id: Optional[str] = None) -> dict:
    """A schema-valid Observation the SUPERVISOR emits when the pane could not (timeout with
    no child sentinel). Never overwrites a validated child observation (CF-12)."""
    obs = contract.Observation(
        run_id=run_id, attempt_id=attempt_id, correlation_id=correlation_id, seat=seat, engine=engine,
        transport="native", requested_model=requested_model, actual_model=None,
        prompt_hash=hash_text(prompt), context_hashes=[], usage=None, timing_ms=0, queued_ms=0,
        process={"exit_code": None, "timed_out": parse_status == contract.TIMEOUT},
        parse_status=parse_status, parsed_payload=None, raw_capture_path=None,
        fallback_reason=reason[:500], routing_config_digest=routing_config_digest)
    d = obs.to_dict()
    contract.validate_observation(d)
    return d


# ---------------------------------------------------------------------------
# #471 W8 — pure status derivation (read-only; shared by TmuxSupervisor.status
# and the `status --run` CLI surface, which composes these without a supervisor)
# ---------------------------------------------------------------------------

_TERMINAL_STATES = frozenset({"completed", "completed_with_residue", "failed",
                              "quarantined", "quota_paused", "timed_out"})


def read_sentinel(record: JobRecord) -> Optional[dict]:
    """The validated child observation, or None. Validity = schema-valid + identity match
    (run/seat/attempt), INDEPENDENT of ``.incomplete`` (CF-9)."""
    path = Path(record.capture_dir) / "observation.json"
    try:
        with open(path, encoding="utf-8") as fh:
            obs = json.load(fh)
        contract.validate_observation(obs)
    except (OSError, ValueError, _SchemaError):
        # absent/unreadable/malformed/schema-invalid = no sentinel. Anything else
        # (a bug in validation itself) raises — masking it as "no sentinel" would
        # silently reroute completed jobs to exited_no_sentinel (8a R2 finding).
        return None
    if (obs.get("run_id") == record.identity.run_id
            and obs.get("seat") == record.identity.seat
            and obs.get("attempt_id") == record.attempt_id):
        return obs
    return None


def derive_state(record: JobRecord, *, sentinel: Optional[dict], live: bool) -> str:
    """Derived state: terminal recorded state passes through; valid sentinel → completed;
    live session → running; else exited_no_sentinel (NEVER quota_paused — that
    classification is injected, CF-6)."""
    if record.state in _TERMINAL_STATES:
        return record.state
    if sentinel is not None:
        return "completed"
    if live:
        return "running"
    return "exited_no_sentinel"


def run_status(records, *, live_fn, sentinel_fn, spec_fn, activity_fn, clock) -> list:
    """AC-J1 per-seat rows for a run — pure composition, all I/O injected (AC-J3: the
    status surface reads registry/spec/capture only, never mutates run state).

    Injected contracts: ``live_fn(record) -> (live: bool, probe_error: Optional[str])``
    (gpt-diff A3 — a failed/unavailable probe is visible per row, never silently "dead");
    ``spec_fn(record) -> (spec: Optional[dict], status: "ok"|"missing"|"corrupt")``
    (gpt-diff A5); ``sentinel_fn(record) -> Optional[dict]``.

    Every record produces a row — stale/abnormal entries stay visible, never filtered.
    ``recorded_state`` keeps the raw registry state alongside the derived ``state`` so
    all nine OQ-8 states are distinguishable (derivation never outputs ``launched``).
    A terminal record skips only the tmux probe — the sentinel is still read, because
    completed is exactly when ``actual_model`` IS known (gpt-diff A4). ``eta`` is the
    literal ``"no estimate"`` until AC-I3 wall-time history exists (#449) — never a
    fabricated number."""
    now = clock()
    run_start = min((r.created_at for r in records), default=now)
    rows = []
    for record in records:
        sentinel = sentinel_fn(record)
        if record.state in _TERMINAL_STATES or sentinel is not None:
            live, probe_error = False, None       # settled/sentinel-bearing: no tmux probe
        else:
            live, probe_error = live_fn(record)
            live = bool(live)
        spec, spec_status = spec_fn(record)
        spec = spec or {}
        request = spec.get("request") or {}
        rows.append({
            "seat": record.identity.seat,
            "attempt": record.attempt_id,
            "state": derive_state(record, sentinel=sentinel, live=live),
            "recorded_state": record.state,
            "session_name": record.session_name,
            "run_socket": record.run_socket,
            "worktree_path": record.worktree_path,
            "capture_dir": record.capture_dir,
            "requested_model": request.get("requested_model"),
            "effort": request.get("effort"),
            "engine": spec.get("engine"),
            "spec_status": spec_status,
            "probe_error": probe_error,
            "actual_model": (sentinel or {}).get("actual_model"),
            "correlation_id": (sentinel or {}).get("correlation_id"),
            "eta": "no estimate",
            "elapsed_s": max(0, int(now - record.created_at)),
            "run_elapsed_s": max(0, int(now - run_start)),
            "resume_attempts": record.resume_attempts,
            "quarantine_reason": record.quarantine_reason,
            "last_activity": activity_fn(record),
        })
    return rows


class TmuxSupervisor:
    """Async seat execution: launch/status/await_job/cancel (+ recover/reap in the lifecycle
    tier). ``run`` (subprocess runner) and ``clock`` are injected for the pure tests; the
    integration tests drive a real tmux 3.4 private server.

    ``pane_env`` is merged onto ``os.environ`` for every tmux invocation — the FIRST command
    on a private socket starts that server, and panes inherit the server's environment, so
    this is the one place to thread PYTHONPATH/credentials to pane_runner."""

    def __init__(self, *, snapshot, quota, capture_root: str, registry_root: str,
                 registry: Optional[JobRegistry] = None, run=_default_run, clock=time.time,
                 runtime_dir: Optional[str] = None, state_dir: Optional[str] = None,
                 pane_env: Optional[dict] = None, worktree_manager=None,
                 allow_adapter_override: bool = False):
        self._snapshot = snapshot
        self._quota = quota
        self._capture_root = capture_root
        self._registry_root = registry_root
        self._registry = registry or JobRegistry(registry_root)
        self._run = run
        self._clock = clock
        self._runtime_dir = runtime_dir
        self._state_dir = state_dir
        self._env = {**os.environ, **(pane_env or {})}
        self._permits: dict = {}  # session_name -> live acquire() context manager
        # W3 disposition machinery (retain-if-dirty). Optional: when None, retain steps are
        # recorded in the reap summary but not executed — the caller owns W3 wiring.
        self._worktree_manager = worktree_manager
        # test harnesses only: lets the pane honor RAWGENTIC_PANE_ADAPTER (spec-gated)
        self._allow_adapter_override = allow_adapter_override

    # -- tmux plumbing -------------------------------------------------------

    def _tmux(self, sock: str, *args, timeout=30):
        return self._run(["tmux", "-S", sock, *args], env=self._env, timeout=timeout)

    def resolve_socket(self, run_id: str) -> str:
        return resolve_socket(run_id, runtime_dir=self._runtime_dir, state_dir=self._state_dir)

    # -- preflight (AC-E1) ---------------------------------------------------

    def preflight(self, run_socket: str) -> PreflightResult:
        """Fail-closed BOTH ways (CF-13): tmux resolvable, version floor, socket dir usable,
        and every verb the supervisor uses probed ON the private socket."""
        try:
            if shutil.which("tmux") is None and self._run is _default_run:
                return PreflightResult(False, "tmux binary not found")
            sock_dir = os.path.dirname(run_socket)
            try:
                os.makedirs(sock_dir, exist_ok=True)
            except OSError as exc:
                return PreflightResult(False, f"socket dir not creatable: {exc}")
            if not os.access(sock_dir, os.W_OK):
                return PreflightResult(False, f"socket dir not writable: {sock_dir}")
            ver = self._run(["tmux", "-V"], env=self._env)
            if ver.returncode != 0:
                return PreflightResult(False, f"tmux -V failed: {ver.stderr.strip()}")
            m = re.search(r"(\d+)\.(\d+)", ver.stdout or "")
            if not m or (int(m.group(1)), int(m.group(2))) < TMUX_VERSION_FLOOR:
                return PreflightResult(
                    False, f"tmux version below floor {TMUX_VERSION_FLOOR}: {ver.stdout.strip()!r}")
            probe = f"rg-preflight-{uuid.uuid4().hex[:8]}"
            steps = (
                ("new-session", ("new-session", "-d", "-s", probe, "--", "sleep", "30")),
                ("has-session", ("has-session", "-t", probe)),
                ("display-message", ("display-message", "-p", "-t", probe, "#{pane_pid}")),
                ("list-sessions", ("list-sessions", "-F", "#{session_name}")),
                ("kill-session", ("kill-session", "-t", probe)),
            )
            for verb, args in steps:
                res = self._tmux(run_socket, *args)
                if res.returncode != 0:
                    self._tmux(run_socket, "kill-session", "-t", probe)
                    return PreflightResult(False, f"tmux {verb} failed: {(res.stderr or '').strip()}")
            return PreflightResult(True, "")
        except Exception as exc:  # noqa: BLE001 — preflight NEVER raises; unusable == unsupported
            return PreflightResult(False, f"preflight error: {exc}")

    # -- launch (AC-E2/E3/E5) ------------------------------------------------

    def launch(self, seat: str, prompt: str, *, identity: WorktreeIdentity,
               handle: WorktreeHandle, profile: Optional[contract.LaunchProfile] = None,
               effort: Optional[str] = None, timeout: float = 300.0,
               target: Optional[dict] = None, receipt_nonce: Optional[str] = None,
               author_provider: Optional[str] = None, resume_session_id: Optional[str] = None,
               resume_attempts: int = 0, quota_timeout: float = 300.0,
               snapshot_dir: Optional[str] = None,
               snapshot_digest: Optional[str] = None,
               correlation_id: Optional[str] = None,
               recovered_from: Optional[str] = None,
               quota_classification: Optional[dict] = None) -> JobRecord:
        """Resolve routing + acquire the quota permit HERE (AC-E5 — the supervisor holds it
        for the job's lifetime), write the FIXED pane spec, and spawn the pane.

        #470 Task-3: ``snapshot_dir`` + ``snapshot_digest`` (a supervised mutating launch, staged
        by the dispatch choke-point) are bound into the spec so ``pane_runner`` re-verifies the
        staged snapshot's CONTENTS immediately before executing (TOCTOU freeze). The spec's own
        content digest is delivered to ``pane_runner`` out-of-band (argv[2]) for the same reason —
        neither is embeddable in the spec it protects."""
        if target is None:  # Step-11 H7: a dispatcher that already resolved (and canary-bound) a
            # target passes it in — launch must not re-resolve and risk diverging from the
            # composition the canary attested. Standalone callers keep the self-resolve path.
            targets = routing.eligible_targets(seat, self._snapshot, author_provider=author_provider)
            if not targets:
                raise routing.ChainExhausted(f"seat {seat!r}: no eligible target")
            target = targets[0]  # async tier launches the primary; chain fallback is the sync engine's
        lane = target["lane"]
        engine = PROVIDER_ENGINE.get(lane["provider"], lane["provider"])
        eff = contract.resolve_effort(target["model"], effort, engine=engine)
        profile = profile or contract.LaunchProfile()
        # #558 S-F6: one effective-timeout rule on BOTH paths — the manifest's declared
        # bound tightens, never loosens, the caller's operational timeout (pre-#558 the
        # caller value was written raw into the pane spec, ignoring a tighter bound)
        try:
            manifest = self._snapshot.seat(seat).get("manifest") if self._snapshot else None
        except routing.RoutingError:
            manifest = None
        timeout = _effective_timeout(manifest, timeout)

        name = session_name(identity)
        sock = self.resolve_socket(identity.run_id)
        attempt_id = f"{resume_attempts}-{uuid.uuid4().hex[:8]}"
        cap_dir = expected_capture_dir(self._capture_root, identity.run_id, seat, attempt_id)

        acct = lane.get("credential_ref") or "default"
        cm = self._quota.acquire(lane["pool"], account=acct, timeout=quota_timeout)
        token = cm.__enter__()
        permit_ref = str(token) if token is not None else "unbounded"
        spawned = False
        try:
            spec = {
                "engine": engine,
                "run_id": identity.run_id,
                "attempt_id": attempt_id,
                "capture_root": self._capture_root,
                "routing_config_digest": self._snapshot.config_digest,
                "resume_session_id": resume_session_id,
                "allow_adapter_override": self._allow_adapter_override,
                "request": {
                    "seat": seat, "requested_model": target["model"], "prompt": prompt,
                    "transport": lane["transport"], "context": [], "correlation_id": correlation_id,
                    "recovered_from": recovered_from,
                    "effort": eff.native, "timeout": timeout,
                    "credential_ref": lane.get("credential_ref"),
                    "containment_root": handle.root,
                    "profile": {
                        "session_policy": profile.session_policy, "mutating": profile.mutating,
                        "worktree": profile.worktree, "tool_grants": list(profile.tool_grants),
                        "max_budget_usd": profile.max_budget_usd,
                        "max_tokens": profile.max_tokens,
                        "effective_grants": list(profile.effective_grants),
                    },
                },
            }
            # #470 Task-3: bind the staged snapshot (contents re-verified in the pane) when supplied.
            if snapshot_dir is not None or snapshot_digest is not None:
                spec["snapshot_dir"] = snapshot_dir
                spec["expected_snapshot_digest"] = snapshot_digest
            specs_dir = Path(self._registry_root) / "specs"
            specs_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(specs_dir, 0o700)
            spec_path = specs_dir / f"{name}.json"
            spec_text = json.dumps(spec, indent=2, sort_keys=True)
            atomic_write_text(spec_path, spec_text)
            # FULL-content digest (Step-11 codex #4): adoption re-verifies the spec bytes,
            # not just the fixed argv — a swapped prompt/engine/grants file can't adopt.
            spec_digest = hash_text(spec_text)

            # #470 Task-3: the expected spec digest rides argv[2] so pane_runner refuses a spec
            # swapped after this atomic write (out-of-band — a spec cannot carry its own digest).
            argv = [sys.executable, "-m", "phase_executor.pane_runner", str(spec_path), spec_digest]
            # digest EXCLUDES the interpreter path (argv[0]) — a venv rebuild / python
            # upgrade must not quarantine-kill adoptable work on recovery (8a R2 finding)
            digest = command_digest(argv[1:])
            res = self._tmux(sock, "new-session", "-d", "-s", name, "-c", handle.path, "--", *argv)
            if res.returncode != 0:
                raise SupervisorError(f"tmux new-session failed: {(res.stderr or '').strip()}")
            spawned = True
            shown = self._tmux(sock, "display-message", "-p", "-t", name, "#{pane_pid}")
            if shown.returncode != 0 or not (shown.stdout or "").strip().isdigit():
                raise SupervisorError(f"pane_pid unreadable: {(shown.stderr or '').strip()}")
            pane_pid = int(shown.stdout.strip())
            try:
                pane_pgid = os.getpgid(pane_pid)
            except OSError:
                pane_pgid = pane_pid
            record = JobRecord(
                identity=identity, session_name=name, run_socket=sock, pane_pid=pane_pid,
                pane_pgid=pane_pgid, provider_pgid=None,
                pane_start_time=_proc_start_time(pane_pid),
                worktree_path=handle.path, worktree_base_sha=handle.base_sha,
                worktree_root=handle.root, worktree_gitdir=handle.gitdir,
                worktree_repo=handle.repo, capture_dir=str(cap_dir), attempt_id=attempt_id,
                permit_ref=permit_ref, command_digest=digest, spec_digest=spec_digest,
                provider_session_id=resume_session_id, provider_exit_code=None,
                resume_attempts=resume_attempts, state="running",
                created_at=self._clock(), quarantine_reason=None,
                receipt_nonce=receipt_nonce, recovered_from=recovered_from,
                quota_classification=quota_classification)
            self._registry.upsert(record)
            self._permits[name] = cm
            return record
        except BaseException:
            # a post-spawn failure (unreadable pane_pid, registry write error) must not
            # leak a LIVE unregistered pane outside the ceiling (Step-11 R1/codex #5):
            # kill the session first, then release the permit
            if spawned:
                try:
                    self._tmux(sock, "kill-session", "-t", name)
                except Exception:  # noqa: BLE001 — best-effort teardown on the raise path
                    pass
            cm.__exit__(None, None, None)  # never leak a permit on a failed launch (AC-E5)
            raise

    # -- status / sentinel ----------------------------------------------------

    def _live(self, record: JobRecord) -> bool:
        return self._tmux(record.run_socket, "has-session", "-t", record.session_name).returncode == 0

    def _sentinel(self, record: JobRecord) -> Optional[dict]:
        return read_sentinel(record)

    def status(self, identity: WorktreeIdentity) -> str:
        """Derived state via the lifted ``derive_state`` (#471 W8 — one derivation source for
        the method and the read-only status surface)."""
        record = self._registry.get(identity)
        if record is None:
            raise SupervisorError(f"unknown job {identity}")
        if record.state in _TERMINAL_STATES:
            return record.state
        # 8a R1#1: keep the pre-lift short-circuit — a sentinel-bearing job never spawns
        # the tmux probe (and a hung socket can't turn 'completed' into a raise).
        sentinel = self._sentinel(record)
        live = self._live(record) if sentinel is None else False
        return derive_state(record, sentinel=sentinel, live=live)

    def mark_quota_paused(self, identity: WorktreeIdentity,
                          provider_session_id: Optional[str]) -> JobRecord:
        """The INJECTED quota classification (owner Q6/W9 owns the discriminator): records
        quota_paused + the provider session id the relaunch will ``--resume``.

        Fail-closed preconditions (Step-11 codex #7 — a mislabelled pause could relaunch a
        COMPLETED mutating job and duplicate its side effects, or admit concurrent work
        while a live provider still runs): the job must be DEAD, have NO valid sentinel,
        not already be terminal, and carry a non-empty session id."""
        record = self._registry.get(identity)
        if record is None:
            raise SupervisorError(f"unknown job {identity}")
        if not provider_session_id:
            raise SupervisorError("mark_quota_paused: a non-empty provider_session_id is required")
        if record.state not in ("launched", "running", "exited_no_sentinel"):
            raise SupervisorError(
                f"mark_quota_paused: record is {record.state!r} — only a non-terminal dead "
                f"job can be quota-paused")
        if self._live(record):
            raise SupervisorError("mark_quota_paused: session is still live — not a quota exit")
        sent = self._sentinel(record)
        if self._sentinel_effectful(sent):
            # Resuming a job that already produced an EFFECTFUL result would duplicate it
            # (the shared _sentinel_effectful predicate — see its docstring; the
            # supervisor's own synthetic exited_no_sentinel observation carries
            # actual_model=None with an availability status, so it correctly does NOT
            # trip this guard — pause/resume is exactly its designed recovery).
            raise SupervisorError(
                "mark_quota_paused: sentinel indicates an effectful result (an "
                "envelope-producing status or an attested actual_model) — resuming "
                "would duplicate its effects")
        # routes through _finish (release_permit=True): the provider exited (usage-limit
        # exit-1) — free the pool slot NOW, else the relaunch under the same session_name
        # strands the old permit context manager and deadlocks a concurrency-1 pool on
        # the job's own permit (8a R2 finding). Injected-classification evidence rides
        # the same upsert (#558 pass-2 F9).
        return self._finish(record, "quota_paused",
                            provider_session_id=provider_session_id,
                            quota_classification={"injected": True, "paused": True})

    # -- kill (AC-E4, CF-17) ---------------------------------------------------

    def _provider_target(self, record: JobRecord) -> Optional[int]:
        """The provider pgid as a VERIFIED kill target, or None. PGIDs recycle like PIDs
        (8a R2 finding — the incident class on the pgid axis), so a group is a killpg
        target ONLY when the sidecar's persisted leader start-time still matches /proc and
        the group is not the caller's own or an ancestor's. Unverifiable → None: the
        descendant snapshot and the reaper backstop still cover our own pids."""
        try:
            raw = sidecar_path(Path(record.capture_dir)).read_text(encoding="ascii").split()
        except OSError:
            return None
        try:
            pgid = int(raw[0])
        except (IndexError, ValueError):
            return None
        if pgid <= 1 or len(raw) < 2:
            return None
        if _proc_start_time(pgid) != raw[1]:
            return None  # leader gone or pgid recycled — never a group-wide target
        protected_groups = {os.getpgid(0)}
        for p in _self_and_ancestors():
            try:
                protected_groups.add(os.getpgid(p))
            except OSError:
                pass
        if pgid in protected_groups:
            return None
        return pgid

    def _kill_job(self, record: JobRecord, *, grace_s: float = _KILL_GRACE_S) -> bool:
        """The two-group + descendant-snapshot kill. Returns True iff verified dead.

        HARD GUARDS (a live incident 2026-07-19: a record carrying pane_pid=1 made
        _descendants(1) the ENTIRE host tree and the SIGKILL loop swept the caller's own
        process tree):
        - a pid/pgid <= 1 is NEVER killed or snapshotted (init / \"every group\");
        - a pane_pid whose /proc start-time no longer matches the record is a REUSED pid —
          a foreign process; it is treated as already dead and never snapshotted;
        - the caller's own pid and ancestor chain are excluded from any kill set."""
        pane_identity_ok = (record.pane_pid > 1
                            and _pid_alive(record.pane_pid)
                            and _proc_start_time(record.pane_pid) == record.pane_start_time)
        protected = _self_and_ancestors()
        protected_groups = {os.getpgid(0)}
        for p in protected:
            try:
                protected_groups.add(os.getpgid(p))
            except OSError:
                pass
        snapshot = set()
        if pane_identity_ok:
            snapshot = (_descendants(record.pane_pid) | {record.pane_pid}) - protected
        provider_pgid = self._provider_target(record)
        pane_group_ok = (pane_identity_ok and record.pane_pgid > 1
                         and record.pane_pgid not in protected_groups)
        if pane_group_ok:
            try:
                os.killpg(record.pane_pgid, signal.SIGTERM)  # graceful: pane_runner kills its tree
            except OSError:
                pass
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline and any(_pid_alive(p) for p in snapshot):
            time.sleep(0.1)

        def _pane_group() -> set:
            if not pane_group_ok:
                return set()
            return _group_pids(record.pane_pgid) - protected

        def _provider_group() -> set:
            if provider_pgid is None:
                return set()
            return _group_pids(provider_pgid) - protected

        if any(_pid_alive(p) for p in snapshot) or _provider_group():
            if pane_group_ok:
                try:
                    os.killpg(record.pane_pgid, signal.SIGKILL)
                except OSError:
                    pass
            for pid in snapshot:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            if provider_pgid is not None:
                try:
                    os.killpg(provider_pgid, signal.SIGKILL)
                except OSError:
                    pass
        # verify: the WHOLE snapshot and BOTH groups dead (a deliberate re-setsid past the
        # provider group is the reaper backstop — no hard-guarantee claim here)
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            if not (any(_pid_alive(p) for p in snapshot) or _pane_group() or _provider_group()):
                break
            time.sleep(0.1)
        residue = any(_pid_alive(p) for p in snapshot) or _pane_group() or _provider_group()
        if not residue:
            self._tmux(record.run_socket, "kill-session", "-t", record.session_name)
            return True
        return False

    def _release_permit(self, record: JobRecord) -> None:
        cm = self._permits.pop(record.session_name, None)
        if cm is not None:
            cm.__exit__(None, None, None)
            return
        # post-compaction path: the permit token is a file another process of ours wrote
        if record.permit_ref and record.permit_ref != "unbounded":
            try:
                os.unlink(record.permit_ref)
            except OSError:
                pass

    def _finish(self, record: JobRecord, state: str, *, release_permit: bool = True,
                **updates) -> JobRecord:
        """Terminal-state stamp. ``release_permit=False`` on any path where process death
        was NOT verified (residue states) — the slot stays held until the reaper confirms
        death, else a new provider over-admits past the ceiling (Step-11 codex #3).
        QuotaCoordinator's stale-reap (dead holder pid) remains the leak backstop."""
        record = replace(record, state=state, **updates)
        self._registry.upsert(record)
        if release_permit:
            self._release_permit(record)
        return record

    # -- await (AC-E3/E4, CF-9/CF-12) ------------------------------------------

    def await_job(self, record: JobRecord, *, poll_s: float = 1.0,
                  timeout_s: float = 3600.0,
                  expect_session_id: Optional[str] = None) -> Tuple[str, Optional[dict]]:
        """Poll for the validated sentinel; on valid → collect ⇒ kill (kill-fail →
        completed_with_residue). On deadline → the CF-17 kill, THEN re-check and prefer a
        valid child obs (the writer was in the killed pane group — race-free); else emit the
        supervisor's synthetic timeout observation. Ambiguous dead-no-sentinel →
        exited_no_sentinel (no auto-resume) with the supervisor's synthetic ``no_response``
        observation (#557 — every terminal state yields a reconcilable Observation).

        ``expect_session_id`` (CF-10, resume-identity assert): on collect, the transport's
        ``session_id`` MUST equal it — a resumed launch that landed in the wrong session
        (wrong cwd, spike #455's LOUD failure shape) is registered ``failed`` and raised."""
        deadline = time.monotonic() + timeout_s
        while True:
            obs = self._sentinel(record)
            if obs is not None:
                # a RESUMED job asserts identity AUTOMATICALLY against its persisted
                # session id (Step-11 codex #8) — the caller param only overrides/adds
                if expect_session_id is None and record.resume_attempts > 0:
                    expect_session_id = record.provider_session_id or "<missing>"
                # KILL-VERIFY FIRST (#558 r6 evidence ordering): every evidence read
                # below (envelope, stderr) sees a stable, dead-writer snapshot — the
                # resume-identity assert is relocated to run post-kill on the bounded
                # _envelope_meta (its failure action, finish "failed" + raise, unchanged)
                killed = self._kill_job(record)
                is_claude_nonzero = (
                    obs.get("parse_status") == contract.NONZERO_EXIT
                    and self._read_spec(record).get("engine") == "claude")
                meta = (self._envelope_meta(record)
                        if (expect_session_id is not None or is_claude_nonzero) else None)
                if expect_session_id is not None:
                    got = meta.session_id
                    if got != expect_session_id:
                        self._finish(record, "failed", release_permit=killed)
                        raise SupervisorError(
                            f"resume identity mismatch: transport session_id {got!r} != "
                            f"persisted {expect_session_id!r} (wrong-cwd resume shape)")
                # #558 AC1: collection-time quota detection — classifier invocation is
                # the persistence trigger on a claude NONZERO_EXIT collect (pass-3 S-F10)
                cls = None
                vspec = None
                refusal_reason = None
                if is_claude_nonzero:
                    vspec = self._verified_spec(record)
                    if vspec is not None:  # tampered/missing spec: classify nothing
                        ev = self._read_stderr(record)
                        cls = quota_detect.classify_quota_exit(
                            engine=vspec.get("engine", ""), exit_code=_exit_code_of(obs),
                            stderr=ev, envelope=meta)
                if cls is not None and cls.verdict:
                    decision = self._auto_pause_allowed(
                        record, obs, _profile_from_spec(vspec),
                        meta.session_id if meta else None, cls, killed=killed)
                    if decision.allowed:  # implies killed — kill-verified is a guard conjunct
                        self._finish(record, "quota_paused", release_permit=True,
                                     provider_session_id=meta.session_id,
                                     provider_exit_code=_exit_code_of(obs),
                                     quota_classification=dict(asdict(cls), paused=True))
                        return "quota_paused", obs
                    if decision.refusal == "kill_unverified":
                        self._finish(record, "completed_with_residue", release_permit=False,
                                     provider_exit_code=_exit_code_of(obs),
                                     quota_classification=dict(asdict(cls), paused=False,
                                                               refusal=decision.refusal))
                        return "completed_with_residue", obs
                    refusal_reason = decision.refusal
                extra = {}
                if cls is not None:
                    qc = dict(asdict(cls), paused=False)
                    if refusal_reason:
                        qc["refusal"] = refusal_reason
                    extra["quota_classification"] = qc
                elif is_claude_nonzero:  # spec missing/tampered: say why, classify nothing
                    extra["quota_classification"] = {
                        "paused": False, "refusal": "spec_unverified",
                        "classifier_version": quota_detect.CLASSIFIER_VERSION}
                state = "completed" if killed else "completed_with_residue"
                self._finish(record, state, release_permit=killed,
                             provider_exit_code=_exit_code_of(obs), **extra)
                return state, obs
            if not self._live(record):
                obs = self._sentinel(record)  # one post-exit re-check (write vs exit race)
                if obs is not None:
                    continue
                # #557 AC1/AC2: death-without-sentinel emits the supervisor's synthetic
                # Observation (correlation + digest from the spec), so reconcile sees a
                # BOUND, recorded failure instead of a missing pair. CF-12 holds — reached
                # only when the sentinel re-check found no VALID child observation.
                #
                # #557 8a + Step-11 review (both waves converged): the failure STATUS is
                # laundering-sensitive. A death is SUSPICIOUS when the child left evidence
                # it ran the provider — either a schema-invalid observation.json, OR a
                # captured transport.stdout.txt (written immediately after the provider
                # process returns, before the observation — adapters/claude_cli.py:142), so
                # an effectful/billed run that died before writing (a valid) observation is
                # caught too. A suspicious death → PARSE_ERROR, which verify_post reads as
                # identity_missing → a breach reconcile refuses even with a verified sibling
                # (it must not be laundered, and pre-#557 this exact state was fail-closed
                # via missing_obs). Only a CLEAN death (no such evidence) is a genuine
                # availability failure → NO_RESPONSE, forgivable by a verified sibling
                # (chain-fallback parity with timed_out). The malformed original is
                # preserved as forensics before the synthetic replaces observation.json
                # (reconcile reads only the audited records, so raw evidence must survive).
                cap_dir = Path(record.capture_dir)
                obs_path = cap_dir / "observation.json"
                malformed_present = obs_path.exists()
                ran_evidence = malformed_present or (cap_dir / "transport.stdout.txt").exists()
                spec = self._read_spec(record)
                obs = synthetic_observation(
                    run_id=record.identity.run_id, seat=record.identity.seat,
                    attempt_id=record.attempt_id, engine=spec.get("engine", "claude"),
                    requested_model=spec.get("request", {}).get("requested_model", "unknown"),
                    prompt=spec.get("request", {}).get("prompt", ""),
                    parse_status=(contract.PARSE_ERROR if ran_evidence
                                  else contract.NO_RESPONSE),
                    reason=("child exited after producing provider output but without a "
                            "valid observation" if ran_evidence
                            else "child exited without sentinel"),
                    routing_config_digest=spec.get("routing_config_digest", "sha256:unknown"),
                    correlation_id=spec.get("request", {}).get("correlation_id"))
                cap = ensure_private_dir(Path(record.capture_dir))
                if malformed_present:
                    try:  # preserve the suspicious envelope as forensics — never crash on it
                        obs_path.replace(cap / "observation.malformed.json")
                    except OSError:
                        pass
                atomic_write_text(cap / "observation.json",
                                  json.dumps(obs, indent=2, sort_keys=True))
                self._finish(record, "exited_no_sentinel")
                return "exited_no_sentinel", obs
            if time.monotonic() >= deadline:
                kill_clean = self._kill_job(record)
                obs = self._sentinel(record)
                if obs is not None:  # CF-12: the child's validated result wins
                    state = "completed" if kill_clean else "completed_with_residue"
                    self._finish(record, state, provider_exit_code=_exit_code_of(obs))
                    return state, obs
                spec = self._read_spec(record)
                obs = synthetic_observation(
                    run_id=record.identity.run_id, seat=record.identity.seat,
                    attempt_id=record.attempt_id, engine=spec.get("engine", "claude"),
                    requested_model=spec.get("request", {}).get("requested_model", "unknown"),
                    prompt=spec.get("request", {}).get("prompt", ""),
                    parse_status=contract.TIMEOUT,
                    reason=f"supervisor timeout after {timeout_s}s",
                    routing_config_digest=spec.get("routing_config_digest", "sha256:unknown"),
                    correlation_id=spec.get("request", {}).get("correlation_id"))
                # #513: the child may have died before create_capture ran, so this
                # mkdir can be the tree's CREATION site — same 0700 posture applies.
                cap = ensure_private_dir(Path(record.capture_dir))
                atomic_write_text(cap / "observation.json",
                                  json.dumps(obs, indent=2, sort_keys=True))
                # an unverified kill leaves residue the reaper must see — never silent,
                # and the permit stays held until death is confirmed
                self._finish(record, "timed_out", release_permit=kill_clean,
                             quarantine_reason=(
                                 None if kill_clean else "timeout kill unverified: residue"))
                return "timed_out", obs
            time.sleep(poll_s)

    def cancel(self, record: JobRecord) -> str:
        """CF-17 kill + finish. The worktree disposition (retain-if-dirty) is the caller's
        W3 ``finalize`` — the supervisor never deletes a worktree here."""
        clean = self._kill_job(record)
        state = "failed" if clean else "completed_with_residue"
        self._finish(record, state, release_permit=clean)
        return state

    def run_seat_tmux(self, seat: str, prompt: str, *, identity: WorktreeIdentity,
                      handle: WorktreeHandle, poll_s: float = 1.0, timeout_s: float = 3600.0,
                      **launch_kw) -> Tuple[str, Optional[dict]]:
        """The synchronous OQ-5 wrapper: launch + await_job."""
        record = self.launch(seat, prompt, identity=identity, handle=handle, **launch_kw)
        return self.await_job(record, poll_s=poll_s, timeout_s=timeout_s)

    # -- pre-spawn canary probe session (#470 Task-3, design §2a phase 2) ------

    def probe_session(self, composition, probe_plan, *, snapshot_dir, quota, pool: str,
                      account: str = "default", timeout: float = 180.0,
                      quota_timeout: float = 60.0, run=None) -> list:
        """A trusted, SHORT-LIVED, NON-mutating probe run STRICTLY before the task pane exists.

        It acquires its OWN quota permit (a real provider invocation must respect the pool ceiling)
        released finally-equivalent on EVERY path (success, refusal, timeout, cancellation — the
        ``acquire`` context manager guarantees it), then runs a credential-free ``claude -p
        --output-format stream-json`` probe whose scripted commands exercise each mutating matcher
        class against OS-NON-WRITABLE targets, and returns the parsed stream events. The caller
        (``executor_routing_lib.supervised_dispatch``) feeds them to
        ``canary_evidence.complete_evidence``; the canary evaluation happens THERE, so this method
        never decides pass/refuse — a repeated refusal downstream therefore never shrinks the pool
        (the probe permit is always released here). Probe-session FAILURE is a refusal downstream
        (fail-closed), never a skip: a raise propagates. ``run`` overrides the provider runner for
        tests (the live spawn is the ``#472`` proving ground / the RUN_LIVE cell)."""
        runner = run if run is not None else self._probe_run
        with quota.acquire(pool, account=account, timeout=quota_timeout):
            return runner(composition=composition, probe_plan=probe_plan,
                          snapshot_dir=snapshot_dir, timeout=timeout)

    def _probe_run(self, *, composition, probe_plan, snapshot_dir, timeout) -> list:  # pragma: no cover
        """Live probe runner (exercised by the RUN_LIVE cell / #472, never CI). A disposable,
        credential-free cwd (own throwaway dir; no repo write access, no secrets threaded); the
        prompt drives one probe per mutating matcher class against an OS-non-writable target."""
        import tempfile  # noqa: PLC0415
        prompt = probe_prompt(probe_plan)
        cmd = ["claude", "--print", "--output-format", "stream-json", "--verbose"]
        env = {**self._env, "RAWGENTIC_HEADLESS": "1"}
        with tempfile.TemporaryDirectory(prefix="rg-probe-") as workdir:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                  timeout=timeout, env=env, cwd=workdir, check=False)
        return parse_stream_events(proc.stdout)

    def kill_server(self, run_id: str) -> None:
        """Tear down the whole private server for ``run_id`` (test/run cleanup)."""
        self._tmux(self.resolve_socket(run_id), "kill-server")

    # -- recover (OQ-8, CF-6/CF-7/CF-10) ----------------------------------------

    def _identity_matches(self, record: JobRecord) -> bool:
        """FULL identity match for adoption: recomputed command digest, pane start-time
        (PID-reuse guard, live jobs), and the worktree still on disk. ANY mismatch is a
        quarantine — never a silent adopt (CF-7)."""
        spec_path = Path(self._registry_root) / "specs" / f"{record.session_name}.json"
        # interpreter-independent digest — must mirror launch()'s argv[1:] computation, INCLUDING
        # the argv[2] expected-spec-digest (#470 Task-3); record.spec_digest is that same value.
        if command_digest(["-m", "phase_executor.pane_runner", str(spec_path),
                           record.spec_digest]) != record.command_digest:
            return False
        # FULL spec-content digest (Step-11 codex #4): the bytes on disk must be the bytes
        # launch() wrote — a swapped prompt/engine/grants/capture-root can never adopt
        try:
            spec_text = spec_path.read_text(encoding="utf-8")
        except OSError:
            return False
        if record.spec_digest is None or hash_text(spec_text) != record.spec_digest:
            return False
        spec = self._read_spec(record)
        req = spec.get("request") or {}
        if not req:
            return False
        # identity fields must agree between spec and record (belt over the digest)
        if (spec.get("run_id") != record.identity.run_id
                or req.get("seat") != record.identity.seat
                or spec.get("attempt_id") != record.attempt_id):
            return False
        if not os.path.isdir(record.worktree_path):
            return False
        if _pid_alive(record.pane_pid):
            if _proc_start_time(record.pane_pid) != record.pane_start_time:
                return False  # PID reused by a foreign process
        return True

    def _reestablish_adopt_permit(self, record: JobRecord) -> bool:
        """#467 D-12: on recover-ADOPT, re-key the adopted job's quota permit under THIS
        orchestrator's pid so the pool ceiling keeps counting the still-live job (the launcher
        that acquired it has exited — its pid is dead, so QuotaCoordinator would stale-reap the
        slot and the pool would over-admit). Re-resolves the seat's pool from the routing
        snapshot (the same source launch() acquired against). Fail-closed: a QuotaTimeout (the
        slot is gone and re-taking it would over-admit) or a routing error propagates so
        recover() refuses the adoption rather than over-admit."""
        if record.permit_ref == "unbounded":
            return True
        # Step-11 re-review RH2: the pool is derived from the permit token's OWN parent dir —
        # the pool the launch actually acquired under. Re-resolving eligible_targets[0] here
        # would name the PRIMARY lane's pool, which can differ from the launched lane's pool
        # (the H1 mutating-eligibility filter launches build on its codex chain entry while the
        # primary is claude) — a wrong pool name checks the wrong ceiling on reclaim.
        pool = Path(record.permit_ref).parent.parent.name  # <root>/<POOL>/<account>/permit-*
        # Step-11 H4: surface the CAS outcome — False = another live orchestrator won and OWNS
        # the job (recover() yields it), True = this process owns the permit.
        return self._quota.reestablish_permit(pool, record.permit_ref)

    def recover(self, run_id: str) -> list:
        """Per non-terminal record: ``classify_recovery`` → adopt (re-attach, nothing to do) /
        quarantine (kill both groups + W3 retain — the untrusted writer never survives) /
        relaunch (``--resume`` from the seat's worktree cwd, capped at MAX_RESUME) / fail.
        Returns [RecoveryAction], one per record considered."""
        actions = []
        for record in self._registry.by_run(run_id):
            if record.state in ("completed", "completed_with_residue", "failed", "quarantined"):
                continue
            live = self._live(record)
            matches = self._identity_matches(record)
            # spec content must also still parse for a live adopt (tamper evidence)
            sentinel_valid = self._sentinel(record) is not None
            verdict = classify_recovery(record, live=live, identity_matches=matches,
                                        sentinel_valid=sentinel_valid)
            if verdict == "adopt":
                try:
                    owned = self._reestablish_adopt_permit(record)
                except (QuotaTimeout, routing.RoutingError, OSError) as exc:
                    # D-12 fail-closed: the permit could not be re-established under THIS
                    # orchestrator's pid within the ceiling — refuse the adoption (kill +
                    # retain) rather than leave a live job over-admitting past the ceiling.
                    # OSError included (8a F4): a token/dir write failure quarantines THIS
                    # record and the sweep continues — the relaunch arm's R1 contract; an
                    # adopted-but-unpermitted job is exactly the over-admission hole.
                    killed = self._kill_job(record)
                    reason = f"adopt refused: {exc}"
                    if not killed:
                        reason += "; kill unverified: residue"
                    done = self._finish(record, "quarantined", release_permit=killed,
                                        quarantine_reason=reason)
                    self._retain(done)
                    actions.append(RecoveryAction(record.identity, "quarantine", done))
                else:
                    if owned is False:
                        # Step-11 H4: the permit CAS was won by ANOTHER live orchestrator — that
                        # process owns the job. Recording "adopt" here would leave two
                        # orchestrators both managing it; yield instead (no kill — the job is
                        # healthy and permitted, just not ours).
                        actions.append(RecoveryAction(record.identity, "yielded", record))
                    else:
                        actions.append(RecoveryAction(record.identity, "adopt", record))
            elif verdict == "quarantine":
                killed = self._kill_job(record)
                reason = ("identity mismatch" if not matches else "no valid sentinel")
                if not killed:
                    # the untrusted writer may STILL be live — say so, keep the permit,
                    # the reaper retries (Step-11 codex #6: never claim a clean quarantine)
                    reason += "; kill unverified: residue"
                done = self._finish(record, "quarantined", release_permit=killed,
                                    quarantine_reason=reason)
                self._retain(done)
                actions.append(RecoveryAction(record.identity, "quarantine", done))
            elif verdict == "relaunch":
                try:
                    new = self._relaunch(record)
                    actions.append(RecoveryAction(record.identity, "relaunch", new))
                except (SupervisorError, routing.RoutingError, QuotaTimeout):
                    # non-claude engine / routing gone / pool full — fail THIS record
                    # without burning a resume slot; recovery of OTHER records continues
                    # (Step-11 R1 High: an uncaught raise here aborted the whole sweep)
                    done = self._finish(record, "failed")
                    actions.append(RecoveryAction(record.identity, "fail", done))
            else:  # fail: resume cap reached
                done = self._finish(record, "failed")
                actions.append(RecoveryAction(record.identity, "fail", done))
        return actions

    def _relaunch(self, record: JobRecord) -> JobRecord:
        """Relaunch a quota_paused job: same identity/worktree, a resume-policy profile, the
        persisted provider session id (claude ``--resume``), resume_attempts + 1. The
        resume-identity assert runs at collect time (await_job ``expect_session_id``).

        #554 recovery provenance: the relaunch carries its OWN correlation_id
        (``<original>#resume<n>``) plus ``recovered_from`` = the ORIGINAL call's correlation_id
        (the ``recovered_from`` already on this record if it is itself a re-recovery, else the
        original spec's correlation). ``reconcile_run`` groups the recovery under the original
        expected call via that link — one authorized pause+resume, not an orphan or a new key."""
        spec = self._read_spec(record)
        req = spec.get("request") or {}
        if not req:
            raise SupervisorError(f"relaunch {record.session_name}: spec unreadable")
        # the join key is the FIRST call's correlation: a re-recovery inherits the record's
        # recovered_from, so every attempt in the chain reconciles under the one original call.
        origin_cid = record.recovered_from or req.get("correlation_id")
        next_attempt = record.resume_attempts + 1
        recovery_cid = (f"{origin_cid}#resume{next_attempt}" if origin_cid is not None else None)
        if spec.get("engine") != "claude":
            # resume is claude-only (adapters refuse); re-resolving routing could land a
            # different engine and burn a resume slot on a guaranteed compose failure
            raise SupervisorError(
                f"relaunch {record.session_name}: resume is claude-only, "
                f"spec engine {spec.get('engine')!r}")
        prof_d = dict(req.get("profile") or {})
        prof_d["session_policy"] = "resume"
        profile = contract.LaunchProfile(
            session_policy="resume", mutating=bool(prof_d.get("mutating")),
            worktree=prof_d.get("worktree"), tool_grants=tuple(prof_d.get("tool_grants") or ()),
            max_budget_usd=prof_d.get("max_budget_usd"),
            max_tokens=prof_d.get("max_tokens"))
        object.__setattr__(profile, "effective_grants",
                           tuple(prof_d.get("effective_grants") or ()))
        handle = handle_from_record(record)
        return self.launch(
            record.identity.seat, req.get("prompt", ""), identity=record.identity,
            handle=handle, profile=profile, effort=req.get("effort"),
            timeout=float(req.get("timeout", 300.0)),
            resume_session_id=record.provider_session_id,
            resume_attempts=next_attempt,
            correlation_id=recovery_cid, recovered_from=origin_cid,
            quota_classification=record.quota_classification)

    def _retain(self, record: JobRecord) -> None:
        """W3 disposition on a failure-shaped exit: retain-if-dirty, owner-visible evidence.
        No manager wired → the caller owns W3 (the reap summary still lists the record)."""
        if self._worktree_manager is None:
            return
        try:
            self._worktree_manager.finalize(handle_from_record(record), "failed")
        except Exception:  # noqa: BLE001 — retention is evidence-preservation, never a crash path
            pass

    # -- reap (AC-E6, CF-8/CF-11/CF-19) ------------------------------------------

    def _default_dead_fn(self, record: JobRecord) -> bool:
        """CONFIRMED dead: pane pid gone-or-reused AND both groups empty (Z = dead).
        A pid/pgid <= 1 is out-of-domain (init / \"every group\") — never treated as OUR
        live process (the 2026-07-19 pane_pid=1 incident class). The provider group counts
        only when its identity VERIFIES (start-time-matched leader) — a recycled pgid must
        not wedge the reaper reporting a foreign group as \"not dead\" forever."""
        if record.pane_pid > 1 and _pid_alive(record.pane_pid) and (
                _proc_start_time(record.pane_pid) == record.pane_start_time):
            return False
        if record.pane_pgid > 1 and record.pane_pid > 1 and _pid_alive(record.pane_pid) \
                and _group_pids(record.pane_pgid):
            return False
        provider_pgid = self._provider_target(record)
        if provider_pgid and _group_pids(provider_pgid):
            return False
        return True

    def _default_clean_fn(self, record: JobRecord) -> bool:
        """Worktree-clean probe (runs ONLY after confirmed death — CF-8). A missing worktree
        has nothing to retain and counts clean."""
        if not os.path.isdir(record.worktree_path):
            return True
        res = self._run(["git", "-C", record.worktree_path, "status", "--porcelain"])
        return res.returncode == 0 and not (res.stdout or "").strip()

    def reap(self, run_id: str, *, policy: Optional[ReapPolicy] = None,
             fresh_s: float = 900.0, clean_fn=None) -> ReapPlan:
        """One sweep: derived liveness (``has-session`` + freshest capture mtime — never a
        written heartbeat, AC-I4), ``reap_plan``'s three tiers with every kill gated on
        confirmed BOTH-group death BEFORE the clean probe, W3 retention on dirty worktrees,
        orphaned-permit release (CF-19). Unknown sessions (live on the socket, not in the
        registry) are REPORTED in ``quarantine``, never killed blind — they have no record
        to age. Returns the executed plan."""
        policy = policy or ReapPolicy()
        # quota_paused records belong to recover() (a pending --resume needs its worktree
        # and cwd intact) — reap never retains/kills what recover is about to relaunch
        # (Step-11 R2 finding; masked today by worktree_manager=None, real once W3-wired)
        records = [r for r in self._registry.by_run(run_id) if r.state != "quota_paused"]
        now = self._clock()
        live_names = set()
        if records:
            res = self._tmux(records[0].run_socket, "list-sessions", "-F", "#{session_name}")
            if res.returncode == 0:
                live_names = {l.strip() for l in (res.stdout or "").splitlines() if l.strip()}

        def _fresh(record: JobRecord) -> bool:
            # an INFANT job (younger than fresh_s) is fresh by age — a just-launched pane
            # has written no capture yet and must never hit the wedge tier for that
            if (now - record.created_at) < fresh_s:
                return True
            try:
                mtimes = [p.stat().st_mtime for p in Path(record.capture_dir).glob("*")]
                return bool(mtimes) and (now - max(mtimes)) < fresh_s  # one clock source
            except OSError:
                return False

        live_fresh = {r.session_name for r in records
                      if r.session_name in live_names and _fresh(r)}
        plan = reap_plan(records, live_fresh=live_fresh, now=now, policy=policy,
                         dead_fn=self._default_dead_fn, clean_fn=clean_fn or self._default_clean_fn)
        # execute: wedged live trees first (kill, verify, retain), then session removals
        for record in plan.kill_tree:
            killed = self._kill_job(record)
            self._finish(record, "quarantined", release_permit=killed,
                         quarantine_reason="wedged: killed by reaper"
                         + ("" if killed else "; kill unverified: residue"))
            self._retain(record)
        for record in plan.kill_session:
            self._tmux(record.run_socket, "kill-session", "-t", record.session_name)
            self._release_permit(record)
        for record in plan.retain_worktree:
            # repeat-safety stamp: without it every future sweep re-invokes W3 finalize on
            # the same dead-dirty record (8a R1 finding — no prune path exists by design)
            if not (record.quarantine_reason or "").startswith("reaped:"):
                self._retain(record)
                self._registry.upsert(replace(
                    record, quarantine_reason="reaped: dirty worktree retained"))
            self._release_permit(record)
        # CF-11 exact-segment scope: sessions live on this run's socket but unknown to the
        # registry — owner-visible, never blind-killed (no record, no age to reason over)
        known = {r.session_name for r in records}
        plan.quarantine.extend(sorted(live_names - known))
        return plan

    # -- helpers ----------------------------------------------------------------

    # -- #558 evidence readers + pause guards (bounded, O_NOFOLLOW, fstat) -------

    @staticmethod
    def _evidence_read(path: Path, ceiling: int):
        """Bounded raw-byte read → ``(data, true_byte_count, read_error)``. O_NOFOLLOW +
        regular-file + fstat: a symlink or non-regular target is
        ``unreadable: irregular``; content past the ceiling (including growth during
        the read) is ``oversized`` with the prefix returned and the true size
        recorded (pass-4 S-F7 snapshot posture — callers kill-verify first)."""
        try:
            fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return b"", 0, "missing"
        except OSError as exc:
            reason = "irregular" if exc.errno == errno.ELOOP else (exc.strerror or "error")
            return b"", 0, f"unreadable: {reason}"
        try:
            st = os.fstat(fd)
            if not stat_mod.S_ISREG(st.st_mode):
                return b"", 0, "unreadable: irregular"
            chunks = []
            remaining = ceiling
            while remaining > 0:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if os.read(fd, 1):  # more content past the ceiling, or growth during read
                return data, os.fstat(fd).st_size, "oversized"
            return data, len(data), None
        finally:
            os.close(fd)

    def _read_stderr(self, record: JobRecord) -> quota_detect.StderrEvidence:
        """The capture's ``stderr.txt`` as bounded StderrEvidence — the ONLY sanctioned
        classifier verdict input (#558 AC1)."""
        data, count, err = self._evidence_read(
            Path(record.capture_dir) / "stderr.txt", quota_detect.CEILING_BYTES)
        return quota_detect.StderrEvidence(
            decoded_text=data.decode("utf-8", errors="replace"),
            raw_sha256=(hashlib.sha256(data).hexdigest()
                        if err in (None, "oversized") else ""),
            byte_count=count, read_error=err)

    def _envelope_meta(self, record: JobRecord) -> quota_detect.EnvelopeMeta:
        """Bounded (256 KiB) replacement for the unbounded ``_transport_session_id``
        read on the collect path (pass-3 S-F4): session_id + ALLOWLISTED subtype only —
        observability, never a verdict input."""
        data, _count, err = self._evidence_read(
            Path(record.capture_dir) / "transport.stdout.txt", _ENVELOPE_CEILING_BYTES)
        if err == "oversized":
            return quota_detect.EnvelopeMeta(None, None, None, "oversized")
        if err is not None:
            return quota_detect.EnvelopeMeta(
                None, None, None, "missing" if err == "missing" else "malformed")
        try:
            env = json.loads(data.decode("utf-8", errors="replace"))
        except ValueError:
            return quota_detect.EnvelopeMeta(None, None, None, "malformed")
        if not isinstance(env, dict):
            return quota_detect.EnvelopeMeta(None, None, None, "malformed")
        sid = env.get("session_id")
        # 8a security M2: a session id is provider-controlled — only a bounded,
        # strictly-encodable string may reach jobs.json / a future --resume argv
        if isinstance(sid, str) and sid and len(sid) <= _SESSION_ID_MAXLEN:
            try:
                sid.encode("utf-8")  # rejects lone surrogates from JSON escapes
            except UnicodeError:
                sid = None
        else:
            sid = None
        subtype = env.get("subtype")
        sub_sha = None
        if isinstance(subtype, str):
            if (subtype not in _ENVELOPE_SUBTYPE_ALLOWLIST
                    or len(subtype) > _ENVELOPE_SUBTYPE_MAXLEN):
                # 8a security M1: backslashreplace keeps the hash total (lone
                # surrogates from JSON escapes must never abort collection)
                sub_sha = hashlib.sha256(
                    subtype.encode("utf-8", "backslashreplace")).hexdigest()
                subtype = "unknown"
        else:
            subtype = None
        return quota_detect.EnvelopeMeta(session_id=sid, subtype=subtype,
                                         subtype_sha256=sub_sha, error=None)

    def _verified_spec(self, record: JobRecord) -> Optional[dict]:
        """The pane spec parsed from digest-VERIFIED bytes (read once — pass-3 S-F2,
        the same trust rule recovery applies), or None on missing/malformed/mismatch."""
        path = Path(self._registry_root) / "specs" / f"{record.session_name}.json"
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        if not record.spec_digest or hash_text(text) != record.spec_digest:
            return None
        try:
            spec = json.loads(text)
        except ValueError:
            return None
        return spec if isinstance(spec, dict) else None

    @staticmethod
    def _sentinel_effectful(sent: Optional[dict]) -> bool:
        """True when a sentinel indicates an EFFECTFUL result — an envelope-producing
        parse_status (not an availability failure) or a provider-ATTESTED actual_model.
        Resuming such a job would duplicate its effects (#557 8a review)."""
        return sent is not None and (
            sent.get("parse_status") not in contract.AVAILABILITY_FAILURES
            or bool(sent.get("actual_model")))

    def _pause_common_allowed(self, record: JobRecord, obs: Optional[dict],
                              session_id: Optional[str]) -> Optional[str]:
        """The guard set shared by BOTH quota_paused writers (pass-3 A-F2/S-F3) —
        returns the refusal reason, or None when the pause is permissible."""
        if not session_id:
            # a quota_paused record with no session id would make classify_recovery's
            # relaunch arm compose an impossible --resume
            return "no_resumable_session"
        if self._sentinel_effectful(obs):
            return "effectful_sentinel"
        if record.state in _TERMINAL_STATES:
            return "terminal_state"
        return None

    def _auto_pause_allowed(self, record: JobRecord, obs: dict,
                            profile: Optional[_GuardProfile],
                            session_id: Optional[str],
                            cls: quota_detect.QuotaClassification, *,
                            killed: bool) -> PauseDecision:
        """Automatic-mode pause guards, declared order = refusal precedence:
        verified-spec/profile → read-only → resume-policy → calibration allowlist →
        common → kill-verified. ``allowed=True`` implies ``killed`` (r7: pause only on
        verified-dead; every evidence byte was read after that verification)."""
        if profile is None:
            return PauseDecision(False, "spec_unverified")
        if profile.mutating:
            # a mutating job may have applied Edit/Bash effects before the quota exit
            return PauseDecision(False, "mutating_requires_manual")
        if profile.session_policy != "resume":
            # a fresh launch composed --no-session-persistence; resuming it is
            # spike-#455's LOUD failure
            return PauseDecision(False, "session_not_persisted")
        if (cls.classifier_version, quota_detect.RULE_TABLE_DIGEST) not in CALIBRATED_CLASSIFIERS:
            return PauseDecision(False, "uncalibrated_classifier")
        common = self._pause_common_allowed(record, obs, session_id)
        if common is not None:
            return PauseDecision(False, common)
        if not killed:
            return PauseDecision(False, "kill_unverified")
        return PauseDecision(True, None)

    def _transport_session_id(self, record: JobRecord) -> Optional[str]:
        """The provider session id from the transport capture (claude envelope's
        ``session_id``) — the machine signal the resume-identity assert compares."""
        try:
            with open(Path(record.capture_dir) / "transport.stdout.txt", encoding="utf-8") as fh:
                env = json.load(fh)
            sid = env.get("session_id")
            return sid if isinstance(sid, str) else None
        except (OSError, ValueError):
            return None

    def _read_spec(self, record: JobRecord) -> dict:
        try:
            with open(Path(self._registry_root) / "specs" / f"{record.session_name}.json",
                      encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}


def _proc_start_time(pid: int) -> str:
    """/proc/<pid>/stat field 22 (starttime) — the PID-reuse guard persisted on the record."""
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
            return fh.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return "0"


def _exit_code_of(obs: dict) -> Optional[int]:
    proc = obs.get("process") or {}
    code = proc.get("exit_code")
    return code if isinstance(code, int) else None


# -- #470 Task-3 pre-spawn probe helpers (pure; the live spawn is _probe_run) ----------
_PROBE_PAYLOADS = {
    # class-tool -> a scripted, INTRINSICALLY non-mutating probe line (OS-non-writable target, so
    # an OS denial can never masquerade as a hook success — the canary requires a hook-origin
    # marker, and the payload's own OS-unwritability is defense-in-depth, not the detector).
    "Bash": "run this exact bash command with the Bash tool: ssh example.invalid echo hi",
    "Edit": "use the Write tool to write the text 'x' to the absolute path /proc/1/rg-probe-denied",
    "Write": "use the Write tool to write the text 'x' to the absolute path /proc/1/rg-probe-denied",
}


def probe_prompt(probe_plan: dict) -> str:
    """Build the probe-session prompt: one probe line per mutating matcher class in the plan,
    keyed off the class's issued tool. Fail-closed on an empty plan (an empty prompt would yield
    no probes -> the canary refuses positive_deny, never a false pass)."""
    lines = []
    for cls, spec in (probe_plan or {}).items():
        tool = spec.get("issued_tool") if isinstance(spec, dict) else None
        payload = _PROBE_PAYLOADS.get(tool) or f"use the {tool} tool once on a read-only target"
        lines.append(f"- ({cls}) {payload}")
    body = "\n".join(lines)
    return ("You are a hook-layer probe. Perform EACH of the following actions EXACTLY ONCE, in "
            "order, reporting the tool result verbatim. Do not use any skill. Do not ask "
            f"questions.\n{body}\n")


def parse_stream_events(stdout: str) -> list:
    """Parse a ``claude -p --output-format stream-json`` stdout into a list of event dicts,
    skipping unparseable lines (fail-closed: junk never raises, it is simply absent)."""
    events = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events
