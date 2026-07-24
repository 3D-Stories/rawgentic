"""#636 (epic #635 C1) — TerminalBackend protocol + TmuxBackend: the injected seam for
TmuxSupervisor's launch/liveness/enumerate/close primitives. Pure refactor, zero behavior
change — every TmuxBackend method is a byte-identical mirror of the tmux invocation
TmuxSupervisor used inline before this extraction (same args, same ``-S <socket>`` server
model, same merged environment). Backend-agnostic machinery (the two-group +
descendant-snapshot ``os.killpg`` kill, ``read_sentinel``/``observation.json`` status
derivation, ``recover``/``reap`` verdict logic) stays in ``supervisor.py`` and calls into
this seam only for the primitives below.

``resolve_endpoint``/``teardown_endpoint`` are named backend-agnostically (not
``resolve_socket``/``kill_server``) because a future backend's addressing model may not be
a socket path at all — e.g. herdr's is workspace+pane ids, not a per-run private socket
(epic #635 child #638's own grounding). The supervisor treats the return value as an
OPAQUE backend-specific identifier: it never interprets its contents, only passes it back
to this SAME backend's other methods.
"""
from __future__ import annotations

import subprocess
from typing import Optional, Protocol


class TerminalBackend(Protocol):
    """The primitives that differ between terminal runtimes. Every method's ``endpoint``
    parameter is the opaque identifier ``resolve_endpoint`` returned — never constructed
    or interpreted by the caller."""

    def preflight(self, endpoint: str) -> "PreflightResultLike": ...

    def new_session(self, endpoint: str, name: str, cwd: str, argv: list,
                    timeout: float = 30) -> subprocess.CompletedProcess: ...

    def pane_pid(self, endpoint: str, name: str, timeout: float = 30) -> subprocess.CompletedProcess: ...

    def has_session(self, endpoint: str, name: str, timeout: float = 30) -> subprocess.CompletedProcess: ...

    def list_sessions(self, endpoint: str, timeout: float = 30) -> subprocess.CompletedProcess: ...

    def kill_session(self, endpoint: str, name: str, timeout: float = 30) -> subprocess.CompletedProcess: ...

    def resolve_endpoint(self, run_id: str) -> str: ...

    def teardown_endpoint(self, endpoint: str, timeout: float = 30) -> subprocess.CompletedProcess: ...


class PreflightResultLike(Protocol):
    """Structural type only — `TmuxBackend.preflight` returns `supervisor.PreflightResult`
    (a frozen dataclass with `supported`/`reason`); declared here, not imported, to avoid
    a supervisor<->terminal_backend import cycle (supervisor imports THIS module)."""
    supported: bool
    reason: str


class TmuxBackend:
    """Default `TerminalBackend` — the EXACT current tmux invocations, self-contained (owns
    its own `run`/`env`/socket-addressing state; never reaches into a supervisor's private
    attributes).

    ``run`` defaults to ``supervisor._default_run`` itself (deferred import — supervisor
    imports THIS module), never a locally-duplicated copy: `preflight`'s ``self._run is
    _default_run`` identity check (the tmux-binary-presence short-circuit) must compare
    against the SAME function object regardless of whether this backend was constructed
    directly or via `TmuxSupervisor`'s default-backend passthrough."""

    def __init__(self, *, run=None, env: Optional[dict] = None,
                 runtime_dir: Optional[str] = None, state_dir: Optional[str] = None,
                 tmpdir: Optional[str] = None):
        if run is None:
            from .supervisor import _default_run  # noqa: PLC0415 — supervisor imports THIS module
            run = _default_run
        self._run = run
        self._env = env
        self._runtime_dir = runtime_dir
        self._state_dir = state_dir
        self._tmpdir = tmpdir

    def _tmux(self, endpoint: str, *args, timeout=30):
        return self._run(["tmux", "-S", endpoint, *args], env=self._env, timeout=timeout)

    def resolve_endpoint(self, run_id: str) -> str:
        from .supervisor import resolve_socket  # noqa: PLC0415 — supervisor imports THIS module
        return resolve_socket(run_id, runtime_dir=self._runtime_dir,
                              state_dir=self._state_dir, tmpdir=self._tmpdir)

    def preflight(self, endpoint: str) -> "PreflightResultLike":
        from .supervisor import (  # noqa: PLC0415 — supervisor imports THIS module
            PreflightResult, TMUX_VERSION_FLOOR, _default_run as _sup_default_run)
        import os
        import re
        import shutil
        import uuid
        try:
            if shutil.which("tmux") is None and self._run is _sup_default_run:
                return PreflightResult(False, "tmux binary not found")
            sock_dir = os.path.dirname(endpoint)
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
                res = self._tmux(endpoint, *args)
                if res.returncode != 0:
                    self._tmux(endpoint, "kill-session", "-t", probe)
                    return PreflightResult(False, f"tmux {verb} failed: {(res.stderr or '').strip()}")
            return PreflightResult(True, "")
        except Exception as exc:  # noqa: BLE001 — preflight NEVER raises; unusable == unsupported
            return PreflightResult(False, f"preflight error: {exc}")

    def new_session(self, endpoint: str, name: str, cwd: str, argv: list,
                    timeout: float = 30) -> subprocess.CompletedProcess:
        return self._tmux(endpoint, "new-session", "-d", "-s", name, "-c", cwd, "--", *argv,
                          timeout=timeout)

    def pane_pid(self, endpoint: str, name: str, timeout: float = 30) -> subprocess.CompletedProcess:
        return self._tmux(endpoint, "display-message", "-p", "-t", name, "#{pane_pid}",
                          timeout=timeout)

    def has_session(self, endpoint: str, name: str, timeout: float = 30) -> subprocess.CompletedProcess:
        return self._tmux(endpoint, "has-session", "-t", name, timeout=timeout)

    def list_sessions(self, endpoint: str, timeout: float = 30) -> subprocess.CompletedProcess:
        return self._tmux(endpoint, "list-sessions", "-F", "#{session_name}", timeout=timeout)

    def kill_session(self, endpoint: str, name: str, timeout: float = 30) -> subprocess.CompletedProcess:
        return self._tmux(endpoint, "kill-session", "-t", name, timeout=timeout)

    def teardown_endpoint(self, endpoint: str, timeout: float = 30) -> subprocess.CompletedProcess:
        return self._tmux(endpoint, "kill-server", timeout=timeout)
