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

**Name ownership contract (Step-11 review finding, #636).** ``name`` (the pane/session
identifier passed to ``new_session`` and echoed to every later call) is CALLER-CHOSEN —
the supervisor picks it once via ``registry.session_name(identity)`` and expects every
later ``pane_pid``/``has_session``/``kill_session`` call for that job to address the SAME
pane using that SAME string. tmux's ``new-session -s NAME`` natively supports this (the
caller's name IS the tmux session name). A future backend whose native primitive instead
ASSIGNS its own opaque identifier at creation time (e.g. herdr's ``pane split``, which
returns a herdr-assigned pane id rather than accepting a caller-chosen one — confirmed
live, `docs/planning/2026-07-23-633-herdr-build-seat-launch-backend-design.md` AC3) MUST
maintain its own internal ``name -> native-id`` mapping (e.g. tag/rename the pane to
``name`` immediately after creation if the runtime supports it, or keep a private dict)
so every subsequent call using ``name`` still resolves to the correct underlying pane —
this backend-internal translation is deliberately NOT part of the protocol surface (kept
minimal) and is each backend implementation's own responsibility, never the supervisor's.
**KNOWN LIMITATION — permit release vs. process-tree death (#638 Step-11 pass 8, owner scope
decision 2026-07-24).** `CONFIRMED_GONE` is evidence about the NAMESPACE (the session/pane is
absent), NOT proof the process tree is dead. Those differ: the adapter starts the provider with
`start_new_session` in its OWN process group (`adapters/base.py`), so a pane/session can be
legitimately gone while the provider survives — `pane_runner`'s own docstring names this. Any
permit release that treats session-absence as death is therefore making an inference, and
`await_job`'s pre-existing `exited_no_sentinel` path does exactly that (it releases without
`_kill_job` verifying either group). Settling this needs a death-proof protocol — identity
established BEFORE the provider starts, or a launch handshake — which is #467-era supervisor
machinery, outside this issue's ACs (a HerdrBackend + its build-seat config gate). It is filed
separately; four successive attempts to substitute something cheaper each produced a new defect
class, so the substitution is deliberately NOT repeated here. The tri-state above is still a
strict improvement: it removes the "couldn't query" ⇄ "confirmed absent" conflation, which was
the cause of every finding in review passes 1-7.
"""
from __future__ import annotations

import enum
import re
import subprocess
from typing import Optional, Protocol


class Liveness(str, enum.Enum):
    """The TRI-STATE a liveness/teardown probe can actually report (#638 Step-11 pass 7).

    Seven review passes on #638 each found the same bug class: an OPERATIONAL failure being
    read as a definitive answer. The root cause was structural, not a series of unrelated
    slips — every probe returned a ``CompletedProcess`` whose ``returncode`` conflates "I
    confirmed it is gone" with "I could not tell", so every call site had to guess, and each
    guess-site fixed in isolation created a new wrong guess somewhere adjacent.

    A probe therefore reports one of THREE things, and the supervisor branches on the enum
    instead of on a returncode:

    - ``CONFIRMED_ALIVE``   — the pane/session verifiably exists.
    - ``CONFIRMED_GONE``    — the SESSION/PANE verifiably does not exist. This is evidence
      about the NAMESPACE only: it does NOT establish that the process tree is dead (see the
      KNOWN LIMITATION above — the provider runs in its own process group and can outlive its
      pane). For a teardown it is the only outcome that confirms the session is gone.
    - ``INDETERMINATE``     — the probe itself failed (socket permission error, daemon
      hiccup, timeout, unparseable body). NOT a fact about the job: the supervisor holds
      the permit and excludes the record from destructive sweeps for that cycle.
    """

    CONFIRMED_ALIVE = "confirmed_alive"
    CONFIRMED_GONE = "confirmed_gone"
    INDETERMINATE = "indeterminate"


# tmux's own stderr, classified from a live probe against the pinned binary (#638 pass 7 —
# every string below was OBSERVED, none inferred). The sharp edge: "error connecting to
# <sock>" splits on its PARENTHETICAL — `(No such file or directory)` means the socket does
# not exist, so no server exists, so the session verifiably does not exist; `(Permission
# denied)` means we could not look at all. Same prefix, OPPOSITE verdicts.
# tmux's ABSENCE diagnostics, as WHOLE-MESSAGE patterns — an ALLOWLIST, not a denylist of
# operational errors (Step-11 pass 10). Every string was OBSERVED from the pinned binary. The
# earlier design searched for absence phrases as substrings and separately denylisted known
# operational phrases, which could never be airtight: a real tmux operational diagnostic absent
# from the denylist (`failed to send command`) sitting beside an absence phrase still classified
# CONFIRMED_GONE. Inverting it removes the failure mode by construction — a message must MATCH a
# known absence diagnostic in full to mean "gone"; anything unrecognised is INDETERMINATE.
#
# The sharp edge: `error connecting to <sock>` splits on its PARENTHETICAL —
# `(No such file or directory)` means the socket does not exist, so no server, so the session
# verifiably does not exist; `(Permission denied)` means we could not look at all.
_TMUX_ABSENCE_MESSAGES = (
    # NOTE the variable parts use `.*`, never `\S+`: a tmux SESSION NAME and a SOCKET PATH may
    # both contain spaces, and a live probe confirmed the real messages then failed to match —
    # e.g. `can't find session: has space`, `no server running on /tmp/tm x/s p.sock`. That
    # direction of miss is not harmless: an ORDINARY absence would classify INDETERMINATE, which
    # holds quota permits and excludes records from the sweep (pass 10 self-audit). `.` still
    # excludes newlines (no DOTALL), so a multi-line/mixed body cannot whole-match.
    re.compile(r"\A\s*can't find session:?.*\Z", re.I),          # server up, session absent
    re.compile(r"\A\s*no server running on\s+.*\Z", re.I),       # socket present, no server
    re.compile(r"\A\s*error connecting to\s+.*\(no such file or directory\)\s*\Z", re.I),
    re.compile(r"\A\s*no sessions\s*\Z", re.I),                  # empty but running server
)


def classify_tmux_result(res: subprocess.CompletedProcess) -> Liveness:
    """Classify a raw tmux invocation into the tri-state. rc=0 is ALIVE/confirmed-success.

    A nonzero is CONFIRMED_GONE only when EVERY non-empty stream matches a known absence
    diagnostic IN FULL. Anything else — an unrecognised message, a mixed/multi-line body, an
    operational error beside an absence phrase, a stream this function does not understand — is
    INDETERMINATE. Fail-safe by construction: mistaking "couldn't look" for "it's dead" is what
    kills healthy jobs, and an allowlist cannot be defeated by a tmux diagnostic nobody
    enumerated (Step-11 pass 10).
    """
    if res.returncode == 0:
        return Liveness.CONFIRMED_ALIVE
    streams = [t for t in ((res.stderr or "").strip(), (res.stdout or "").strip()) if t]
    if not streams:
        return Liveness.INDETERMINATE       # nonzero with no message at all explains nothing
    if all(any(pat.match(t) for pat in _TMUX_ABSENCE_MESSAGES) for t in streams):
        return Liveness.CONFIRMED_GONE
    return Liveness.INDETERMINATE




class TerminalBackend(Protocol):
    """The primitives that differ between terminal runtimes. Every method's ``endpoint``
    parameter is the opaque identifier ``resolve_endpoint`` returned — never constructed
    or interpreted by the caller. Every method's ``name`` parameter is the CALLER-CHOSEN
    identifier from ``new_session`` — see the module docstring's "Name ownership contract"
    for what a backend whose native primitive assigns its own id (not the caller's) must
    do internally to honor it."""

    def preflight(self, endpoint: str) -> "PreflightResultLike": ...

    def new_session(self, endpoint: str, name: str, cwd: str, argv: list,
                    timeout: float = 30) -> subprocess.CompletedProcess: ...

    def pane_pid(self, endpoint: str, name: str, timeout: float = 30) -> subprocess.CompletedProcess: ...

    def has_session(self, endpoint: str, name: str, timeout: float = 30) -> subprocess.CompletedProcess:
        """MAY raise instead of returning a CompletedProcess when the underlying liveness
        check itself failed/was unparseable (herdr's ``_PaneListError`` case, #638 Step-11
        finding round 2) — genuinely indeterminate, NOT the same as a clean nonzero "not
        found". Supervisor._live() catches this and re-raises SupervisorError so
        recover()/reap() exclude the record for this cycle rather than reading it as
        confirmed dead. TmuxBackend never raises here (tmux has-session's own nonzero exit
        already means "not found", never "couldn't tell")."""
        ...

    def list_sessions(self, endpoint: str, timeout: float = 30) -> subprocess.CompletedProcess:
        """MAY raise instead of returning a CompletedProcess when the enumeration itself
        failed/was unparseable — genuinely indeterminate (#638 Step-11 finding round 2),
        distinct from TmuxBackend's routine nonzero exit on "no sessions on this socket",
        which IS a confirmed-empty result and must never raise. `reap()` relies on this
        split: it excludes a backend's records from the sweep only on a raised exception,
        never merely on a nonzero returncode (TmuxBackend's normal empty-socket case)."""
        ...

    def kill_session(self, endpoint: str, name: str, timeout: float = 30) -> subprocess.CompletedProcess: ...

    def resolve_endpoint(self, run_id: str) -> str: ...

    def teardown_endpoint(self, endpoint: str, timeout: float = 30) -> subprocess.CompletedProcess: ...

    # -- the TRI-STATE surface (#638 Step-11 pass 7) --------------------------------------
    # These are what the supervisor actually calls for any decision with a destructive or
    # quota consequence. They wrap the raw methods above and classify the result, so a
    # backend's own knowledge of what its errors MEAN stays inside that backend and the
    # supervisor never re-derives it from a returncode. The raw methods remain for callers
    # that only need transport.

    def probe_session(self, endpoint: str, name: str, timeout: float = 30) -> "Liveness":
        """Does this session exist? Never raises — an unusable probe is INDETERMINATE."""
        ...

    def close_session(self, endpoint: str, name: str, timeout: float = 30) -> "Liveness":
        """Tear it down. CONFIRMED_GONE is the ONLY outcome that confirms teardown (an
        already-absent session counts — that is the idempotent case). Never raises."""
        ...

    def enumerate_sessions(self, endpoint: str,
                           timeout: float = 30) -> "tuple[Liveness, list]":
        """(verdict, names). CONFIRMED_* ⇒ `names` is a reliable enumeration (possibly
        empty). INDETERMINATE ⇒ `names` is meaningless and the caller must not treat an
        absent name as evidence of death. Never raises."""
        ...


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

    # -- tri-state surface (#638 Step-11 pass 7) -----------------------------------------

    def probe_session(self, endpoint: str, name: str, timeout: float = 30) -> Liveness:
        try:
            return classify_tmux_result(self.has_session(endpoint, name, timeout=timeout))
        except Exception:  # noqa: BLE001 — a raising runner (timeout) is "couldn't tell"
            return Liveness.INDETERMINATE

    def close_session(self, endpoint: str, name: str, timeout: float = 30) -> Liveness:
        """A tmux `kill-session` against an ALREADY-ABSENT session/server exits nonzero with
        an absence message — confirmed-gone, i.e. teardown IS confirmed (the idempotent case).
        Pass-7 finding: treating that ordinary nonzero as an unconfirmed teardown made every
        routine spawn refusal pin a quota permit until the process exited."""
        try:
            res = self.kill_session(endpoint, name, timeout=timeout)
        except Exception:  # noqa: BLE001
            return Liveness.INDETERMINATE
        if res.returncode == 0:
            return Liveness.CONFIRMED_GONE  # kill-session succeeded ⇒ it is gone
        return (Liveness.CONFIRMED_GONE
                if classify_tmux_result(res) is Liveness.CONFIRMED_GONE
                else Liveness.INDETERMINATE)

    def enumerate_sessions(self, endpoint: str, timeout: float = 30) -> "tuple[Liveness, list]":
        try:
            res = self.list_sessions(endpoint, timeout=timeout)
        except Exception:  # noqa: BLE001
            return Liveness.INDETERMINATE, []
        verdict = classify_tmux_result(res)
        if verdict is Liveness.INDETERMINATE:
            return verdict, []
        # CONFIRMED_GONE here means "no server / no sessions" — a RELIABLE empty enumeration,
        # which is exactly tmux's routine idle-socket answer and must keep flowing through the
        # ordinary dead-job sweep rather than excluding the records.
        names = [l.strip() for l in (res.stdout or "").splitlines() if l.strip()]
        return verdict, names
