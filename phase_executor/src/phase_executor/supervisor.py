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

``quota_paused`` is entered ONLY via the injected classification (``mark_quota_paused`` —
W9 #472 owns the genuine usage-limit discriminator); ``status`` defaults every ambiguous
nonzero exit to ``exited_no_sentinel`` (NO auto-resume, CF-6).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional, Tuple

from . import contract, routing
from .capture import atomic_write_text, hash_text
from .engine import PROVIDER_ENGINE
from .pane_runner import _descendants, expected_capture_dir, sidecar_path
from .registry import JobRecord, JobRegistry, command_digest, session_name
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


def _default_run(cmd, *, env=None, cwd=None, timeout=30):
    return subprocess.run(list(cmd), capture_output=True, text=True, env=env, cwd=cwd,
                          timeout=timeout, check=False)


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
                          reason: str, routing_config_digest: str) -> dict:
    """A schema-valid Observation the SUPERVISOR emits when the pane could not (timeout with
    no child sentinel). Never overwrites a validated child observation (CF-12)."""
    obs = contract.Observation(
        run_id=run_id, attempt_id=attempt_id, correlation_id=None, seat=seat, engine=engine,
        transport="native", requested_model=requested_model, actual_model=None,
        prompt_hash=hash_text(prompt), context_hashes=[], usage=None, timing_ms=0, queued_ms=0,
        process={"exit_code": None, "timed_out": parse_status == contract.TIMEOUT},
        parse_status=parse_status, parsed_payload=None, raw_capture_path=None,
        fallback_reason=reason[:500], routing_config_digest=routing_config_digest)
    d = obs.to_dict()
    contract.validate_observation(d)
    return d


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
                 pane_env: Optional[dict] = None):
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
               author_provider: Optional[str] = None, resume_session_id: Optional[str] = None,
               resume_attempts: int = 0, quota_timeout: float = 300.0) -> JobRecord:
        """Resolve routing + acquire the quota permit HERE (AC-E5 — the supervisor holds it
        for the job's lifetime), write the FIXED pane spec, and spawn the pane."""
        targets = routing.eligible_targets(seat, self._snapshot, author_provider=author_provider)
        if not targets:
            raise routing.ChainExhausted(f"seat {seat!r}: no eligible target")
        target = targets[0]  # async tier launches the primary; chain fallback is the sync engine's
        lane = target["lane"]
        engine = PROVIDER_ENGINE.get(lane["provider"], lane["provider"])
        eff = contract.resolve_effort(target["model"], effort, engine=engine)
        profile = profile or contract.LaunchProfile()

        name = session_name(identity)
        sock = self.resolve_socket(identity.run_id)
        attempt_id = f"{resume_attempts}-{uuid.uuid4().hex[:8]}"
        cap_dir = expected_capture_dir(self._capture_root, identity.run_id, seat, attempt_id)

        acct = lane.get("credential_ref") or "default"
        cm = self._quota.acquire(lane["pool"], account=acct, timeout=quota_timeout)
        token = cm.__enter__()
        permit_ref = str(token) if token is not None else "unbounded"
        try:
            spec = {
                "engine": engine,
                "run_id": identity.run_id,
                "attempt_id": attempt_id,
                "capture_root": self._capture_root,
                "routing_config_digest": self._snapshot.config_digest,
                "resume_session_id": resume_session_id,
                "request": {
                    "seat": seat, "requested_model": target["model"], "prompt": prompt,
                    "transport": lane["transport"], "context": [], "correlation_id": None,
                    "effort": eff.native, "timeout": timeout,
                    "credential_ref": lane.get("credential_ref"),
                    "containment_root": handle.root,
                    "profile": {
                        "session_policy": profile.session_policy, "mutating": profile.mutating,
                        "worktree": profile.worktree, "tool_grants": list(profile.tool_grants),
                        "max_budget_usd": profile.max_budget_usd,
                        "effective_grants": list(profile.effective_grants),
                    },
                },
            }
            specs_dir = Path(self._registry_root) / "specs"
            specs_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(specs_dir, 0o700)
            spec_path = specs_dir / f"{name}.json"
            atomic_write_text(spec_path, json.dumps(spec, indent=2, sort_keys=True))

            argv = [sys.executable, "-m", "phase_executor.pane_runner", str(spec_path)]
            res = self._tmux(sock, "new-session", "-d", "-s", name, "-c", handle.path, "--", *argv)
            if res.returncode != 0:
                raise SupervisorError(f"tmux new-session failed: {(res.stderr or '').strip()}")
            shown = self._tmux(sock, "display-message", "-p", "-t", name, "#{pane_pid}")
            if shown.returncode != 0 or not (shown.stdout or "").strip().isdigit():
                self._tmux(sock, "kill-session", "-t", name)
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
                permit_ref=permit_ref, command_digest=command_digest(argv),
                provider_session_id=None, provider_exit_code=None,
                resume_attempts=resume_attempts, state="running",
                created_at=self._clock(), quarantine_reason=None)
            self._registry.upsert(record)
            self._permits[name] = cm
            return record
        except BaseException:
            cm.__exit__(None, None, None)  # never leak a permit on a failed launch (AC-E5)
            raise

    # -- status / sentinel ----------------------------------------------------

    def _live(self, record: JobRecord) -> bool:
        return self._tmux(record.run_socket, "has-session", "-t", record.session_name).returncode == 0

    def _sentinel(self, record: JobRecord) -> Optional[dict]:
        """The validated child observation, or None. Validity = schema-valid + identity match
        (run/seat/attempt), INDEPENDENT of ``.incomplete`` (CF-9)."""
        path = Path(record.capture_dir) / "observation.json"
        try:
            with open(path, encoding="utf-8") as fh:
                obs = json.load(fh)
            contract.validate_observation(obs)
        except (OSError, ValueError, Exception):  # noqa: B014 — jsonschema error subclasses vary
            return None
        if (obs.get("run_id") == record.identity.run_id
                and obs.get("seat") == record.identity.seat
                and obs.get("attempt_id") == record.attempt_id):
            return obs
        return None

    def status(self, identity: WorktreeIdentity) -> str:
        """Derived state: valid sentinel → completed; live session → running; a dead session
        with no sentinel keeps its terminal recorded state or defaults to exited_no_sentinel
        (NEVER quota_paused — that classification is injected, CF-6)."""
        record = self._registry.get(identity)
        if record is None:
            raise SupervisorError(f"unknown job {identity}")
        if record.state in ("completed", "completed_with_residue", "failed", "quarantined",
                            "quota_paused", "timed_out"):
            return record.state
        if self._sentinel(record) is not None:
            return "completed"
        if self._live(record):
            return "running"
        return "exited_no_sentinel"

    def mark_quota_paused(self, identity: WorktreeIdentity,
                          provider_session_id: Optional[str]) -> JobRecord:
        """The INJECTED quota classification (owner Q6/W9 owns the discriminator): records
        quota_paused + the provider session id the relaunch will ``--resume``."""
        record = self._registry.get(identity)
        if record is None:
            raise SupervisorError(f"unknown job {identity}")
        record = replace(record, state="quota_paused", provider_session_id=provider_session_id)
        self._registry.upsert(record)
        return record

    # -- kill (AC-E4, CF-17) ---------------------------------------------------

    def _surfaced_provider_pgid(self, record: JobRecord) -> Optional[int]:
        try:
            raw = sidecar_path(Path(record.capture_dir)).read_text(encoding="ascii").strip()
            return int(raw)
        except (OSError, ValueError):
            return record.provider_pgid

    def _kill_job(self, record: JobRecord, *, grace_s: float = _KILL_GRACE_S) -> bool:
        """The two-group + descendant-snapshot kill. Returns True iff verified dead."""
        snapshot = _descendants(record.pane_pid) | {record.pane_pid}
        provider_pgid = self._surfaced_provider_pgid(record)
        try:
            os.killpg(record.pane_pgid, signal.SIGTERM)  # graceful: pane_runner kills its tree
        except OSError:
            pass
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            if not any(_pid_alive(p) for p in snapshot):
                break
            time.sleep(0.1)
        else:
            pass
        if any(_pid_alive(p) for p in snapshot) or (
                provider_pgid and _group_pids(provider_pgid)):
            try:
                os.killpg(record.pane_pgid, signal.SIGKILL)
            except OSError:
                pass
            for pid in snapshot:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            if provider_pgid:
                try:
                    os.killpg(provider_pgid, signal.SIGKILL)
                except OSError:
                    pass
        # verify: the WHOLE snapshot and BOTH groups dead (a deliberate re-setsid past the
        # provider group is the reaper backstop — no hard-guarantee claim here)
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            residue = any(_pid_alive(p) for p in snapshot) or _group_pids(record.pane_pgid) or (
                provider_pgid and _group_pids(provider_pgid))
            if not residue:
                break
            time.sleep(0.1)
        residue = any(_pid_alive(p) for p in snapshot) or _group_pids(record.pane_pgid) or (
            provider_pgid and _group_pids(provider_pgid))
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

    def _finish(self, record: JobRecord, state: str, **updates) -> JobRecord:
        record = replace(record, state=state, **updates)
        self._registry.upsert(record)
        self._release_permit(record)
        return record

    # -- await (AC-E3/E4, CF-9/CF-12) ------------------------------------------

    def await_job(self, record: JobRecord, *, poll_s: float = 1.0,
                  timeout_s: float = 3600.0) -> Tuple[str, Optional[dict]]:
        """Poll for the validated sentinel; on valid → collect ⇒ kill (kill-fail →
        completed_with_residue). On deadline → the CF-17 kill, THEN re-check and prefer a
        valid child obs (the writer was in the killed pane group — race-free); else emit the
        supervisor's synthetic timeout observation. Ambiguous dead-no-sentinel →
        exited_no_sentinel (no auto-resume)."""
        deadline = time.monotonic() + timeout_s
        while True:
            obs = self._sentinel(record)
            if obs is not None:
                clean = self._kill_job(record)
                state = "completed" if clean else "completed_with_residue"
                self._finish(record, state, provider_exit_code=_exit_code_of(obs))
                return state, obs
            if not self._live(record):
                obs = self._sentinel(record)  # one post-exit re-check (write vs exit race)
                if obs is not None:
                    continue
                self._finish(record, "exited_no_sentinel")
                return "exited_no_sentinel", None
            if time.monotonic() >= deadline:
                self._kill_job(record)
                obs = self._sentinel(record)
                if obs is not None:  # CF-12: the child's validated result wins
                    self._finish(record, "completed", provider_exit_code=_exit_code_of(obs))
                    return "completed", obs
                spec = self._read_spec(record)
                obs = synthetic_observation(
                    run_id=record.identity.run_id, seat=record.identity.seat,
                    attempt_id=record.attempt_id, engine=spec.get("engine", "claude"),
                    requested_model=spec.get("request", {}).get("requested_model", "unknown"),
                    prompt=spec.get("request", {}).get("prompt", ""),
                    parse_status=contract.TIMEOUT,
                    reason=f"supervisor timeout after {timeout_s}s",
                    routing_config_digest=spec.get("routing_config_digest", "sha256:unknown"))
                cap = Path(record.capture_dir)
                cap.mkdir(parents=True, exist_ok=True)
                atomic_write_text(cap / "observation.json",
                                  json.dumps(obs, indent=2, sort_keys=True))
                self._finish(record, "timed_out")
                return "timed_out", obs
            time.sleep(poll_s)

    def cancel(self, record: JobRecord) -> str:
        """CF-17 kill + finish. The worktree disposition (retain-if-dirty) is the caller's
        W3 ``finalize`` — the supervisor never deletes a worktree here."""
        clean = self._kill_job(record)
        state = "failed" if clean else "completed_with_residue"
        self._finish(record, state)
        return state

    def run_seat_tmux(self, seat: str, prompt: str, *, identity: WorktreeIdentity,
                      handle: WorktreeHandle, poll_s: float = 1.0, timeout_s: float = 3600.0,
                      **launch_kw) -> Tuple[str, Optional[dict]]:
        """The synchronous OQ-5 wrapper: launch + await_job."""
        record = self.launch(seat, prompt, identity=identity, handle=handle, **launch_kw)
        return self.await_job(record, poll_s=poll_s, timeout_s=timeout_s)

    def kill_server(self, run_id: str) -> None:
        """Tear down the whole private server for ``run_id`` (test/run cleanup)."""
        self._tmux(self.resolve_socket(run_id), "kill-server")

    # -- helpers ----------------------------------------------------------------

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
