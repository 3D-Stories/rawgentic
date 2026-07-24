"""#638 (epic #635 C2) — HerdrBackend: a `TerminalBackend` implementation on herdr's pane
primitives, config-gated for the `build` seat only (tmux stays the default until independently
proven). Grounded in the live #633 qualification (GO, 40/40 PID-identity reps against pinned
herdr 0.7.5): `docs/planning/2026-07-23-633-herdr-build-seat-launch-backend-design.md`.

**Split targeting (Step-11 review, round 2 — a real bug, confirmed live).** `pane split` has NO
`--workspace` option (`unknown option: --workspace`, confirmed against the pinned real binary) —
an earlier draft of this module wrongly assumed one. The correct model (confirmed live, and
matching how this backend is actually used: an orchestrating Claude Code session, itself running
inside its own herdr pane, spins up a build-seat subprocess by splitting ITS OWN current pane):
`pane split --current --direction <right|down> --cwd <cwd>` — `--current` resolves the CALLING
PROCESS's own pane context, and the new pane inherits that pane's workspace implicitly (confirmed
live: splitting from pane `w1:p1` produced a new pane also scoped to workspace `w1`). If this
backend is ever invoked from a genuinely pane-less process (no controlling terminal — e.g. a
cron-launched headless session with no herdr wrapping), `--current` has nothing to resolve and the
split fails loud, which is the correct outcome for an opt-in, non-default backend rather than
guessing at a fallback target.

**Name ownership (the sharp edge — `terminal_backend.py`'s "Name ownership contract").** herdr's
`pane split` assigns its OWN pane id; it does not accept a caller-chosen one. Confirmed live
(this repo's own herdr daemon, read-only + a reversible rename-then-clear round-trip on an
existing pane, #638 Step 2/3): `pane rename <pane_id> <label>` sets a durable `label` field
visible in both `pane get` and `pane list --workspace`, but `pane get`/`process-info --pane` do
NOT accept a label as their argument — only the native `pane_id` (`pane_not_found` on a label).
So every method that only has the caller-chosen `name` resolves it via `pane list --workspace
<endpoint>` filtered by `label == name` to find the current `pane_id`, THEN calls the underlying
verb with that id. This is fully backend-internal (no registry/protocol change) and durable
across process restarts — it is a live query against the daemon, never an in-memory cache, so a
freshly-constructed `HerdrBackend` in a separate CLI invocation (recovery, status) resolves the
same pane correctly. `_resolve_pane_id` distinguishes a genuinely-empty successful list (pane not
found — `None`) from a FAILED/malformed list command (`_PaneListError`, Step-11 finding: the two
were conflated, which made `kill_session` falsely report a transient list failure as a clean
idempotent close, and could make `reap()`'s liveness union misclassify a live job as gone).

**Two-call, not one, and NOT atomic (named risk, #633/#638).** herdr has no single primitive
matching tmux's `new-session` (create + launch in one call). `new_session` here is THREE herdr
calls in sequence: `pane split` (create) -> `pane rename` (durable tag) -> `pane run ... exec
...` (launch). This is safe only single-threaded per spawn — never parallelize split+run against
one target pane from two callers (the same constraint the design doc names for the two-call
tmux-equivalent split). A failure after `split` (rename or run, INCLUDING an exception such as a
runner timeout — Step-11 finding: the original `if returncode != 0` guards never fired on a raised
exception, orphaning the pane with no cleanup attempt) best-effort closes the orphaned pane via a
`try/finally`, mirroring `TmuxSupervisor.launch`'s own best-effort `kill_session` on a post-spawn
failure (`supervisor.py:528-532`) — but here the cleanup lives inside this backend, since the
supervisor never sees the intermediate pane id. A successful split (`returncode == 0`) whose JSON
body is empty/malformed is also treated as a failure (Step-11 finding: it previously returned the
split's own `returncode` of 0, falsely satisfying the caller's success contract).

**Pane environment (Step-11 review, accepted risk).** herdr has no private-per-run channel for
environment variables the way tmux's `subprocess.run(..., env=...)` on a per-run socket does — the
only mechanism is `pane split --env KEY=VALUE`, passed as CLI arguments to a HOST-WIDE SHARED
daemon. `pane_env` (e.g. `PYTHONPATH`, never credentials) is threaded through this way. Owner-
accepted risk (2026-07-24): these values are visible via `ps` to any user on the host for the
process's lifetime and are not persisted beyond the pane's own life — acceptable for host-local,
ephemeral, non-credential values; this backend must never be handed anything credential-bearing.

**herdr in CI: absent.** `.github/workflows/ci.yml` installs no herdr binary — every test for this
module mocks the injected `run` callable exactly as `TmuxBackend`'s tests mock `tmux`; nothing
here is exercised against a real herdr daemon in CI.
"""
from __future__ import annotations

import json
import subprocess
from typing import Optional

HERDR_VERSION_FLOOR = (0, 7, 5)  # the pinned version #633 qualified live (40/40 PID-identity reps)
HERDR_SPLIT_DIRECTIONS = ("right", "down")


class _PaneListError(RuntimeError):
    """`pane list` itself failed or returned unparseable output — DISTINCT from a genuinely
    empty successful list (Step-11 finding: conflating the two made a transient list failure
    look like "pane not found", which `kill_session` then reported as a clean idempotent
    close and `reap()`'s liveness union silently dropped)."""


def _run_completed(args: list, returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    """Synthesize the `subprocess.CompletedProcess`-shaped result every `TerminalBackend`
    method returns — `TmuxSupervisor` reads only `.returncode`/`.stdout`/`.stderr`, never
    anything backend-specific (`terminal_backend.py`'s Protocol contract)."""
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


class HerdrBackend:
    """`TerminalBackend` on herdr's pane primitives. `run` is the SAME injected
    ``run(cmd, *, env=None, cwd=None, timeout=30) -> subprocess.CompletedProcess`` contract
    `TmuxBackend`/`supervisor._default_run` already use — never a locally-invented shape."""

    def __init__(self, *, run=None, env: Optional[dict] = None, workspace_id: Optional[str] = None,
                 pane_env: Optional[dict] = None, direction: str = "right"):
        if run is None:
            from .supervisor import _default_run  # noqa: PLC0415 — supervisor imports THIS module
            run = _default_run
        if direction not in HERDR_SPLIT_DIRECTIONS:
            raise ValueError(f"direction must be one of {HERDR_SPLIT_DIRECTIONS} (got {direction!r})")
        self._run = run
        self._env = env
        self._workspace_id = workspace_id
        # #638 Step-11 finding: herdr has no private per-run env channel like tmux's
        # subprocess.run(env=...) on a per-run socket — the only way to set the SPAWNED
        # PANE's environment is `pane split --env KEY=VALUE`, visible via ps on this host
        # for the pane's lifetime. `pane_env` is deliberately SEPARATE from `self._env`
        # (which is only the env the `herdr` CLI CLIENT itself runs under) — never pass
        # the full merged os.environ here, only the specific overrides the pane needs
        # (e.g. PYTHONPATH), and never anything credential-bearing (owner-accepted risk,
        # 2026-07-24: host-local + ephemeral only).
        self._pane_env = pane_env or {}
        self._direction = direction

    def _herdr(self, *args, timeout: float = 30) -> subprocess.CompletedProcess:
        return self._run(["herdr", "pane", *args], env=self._env, timeout=timeout)

    def _pane_env_args(self) -> list:
        return [a for k, v in self._pane_env.items() for a in ("--env", f"{k}={v}")]

    def _parse_json(self, res: subprocess.CompletedProcess) -> Optional[dict]:
        """Parse whichever stream carries the JSON payload for this outcome — herdr writes a
        SUCCESS result to stdout, a structured error to stderr (confirmed live: `pane get` on
        a missing pane exits 1 with the error JSON on stderr, empty stdout). Returns None on
        anything unparseable rather than raising — every caller treats None as a clean failure
        shape for ITS OWN synthesized CompletedProcess, never crashes the backend."""
        raw = res.stdout if res.returncode == 0 else res.stderr
        try:
            return json.loads(raw) if raw else None
        except (ValueError, TypeError):
            return None

    def resolve_endpoint(self, run_id: str) -> str:  # noqa: ARG002 — herdr's addressing is
        # workspace-scoped, not per-run (no per-run private socket); run_id is accepted only to
        # satisfy the Protocol's shared signature with TmuxBackend.
        if not self._workspace_id:
            raise RuntimeError(
                "HerdrBackend: no workspace id configured (HERDR_WORKSPACE_ID) — refusing to "
                "guess a workspace scope; every later name-resolution list call would be blind "
                "to panes created under the wrong scope")
        return self._workspace_id

    def preflight(self, endpoint: str):
        from .supervisor import PreflightResult, _default_run as _sup_default_run  # noqa: PLC0415
        import shutil
        import uuid

        try:
            # CI-caught bug: this must mirror TmuxBackend.preflight's identity check exactly —
            # only probe the REAL host PATH when NO custom runner was injected (self._run is
            # still the default). A test supplying its own mock run= is never planning to
            # invoke a real herdr binary at all; checking shutil.which unconditionally made
            # every mocked-runner preflight test pass on a dev machine that happens to have a
            # real herdr installed (this one) and fail everywhere else, including CI.
            if shutil.which("herdr") is None and self._run is _sup_default_run:
                return PreflightResult(False, "herdr binary not found")
            ver = self._run(["herdr", "--version"], env=self._env)
            if ver.returncode != 0:
                return PreflightResult(False, f"herdr --version failed: {ver.stderr.strip()}")
            parts = (ver.stdout or "").strip().split()
            version_str = parts[-1] if parts else ""
            try:
                ver_tuple = tuple(int(p) for p in version_str.split("."))
            except ValueError:
                ver_tuple = ()
            if not ver_tuple or ver_tuple < HERDR_VERSION_FLOOR:
                return PreflightResult(
                    False, f"herdr version below floor {HERDR_VERSION_FLOOR}: {version_str!r}")
            probe_label = f"rg-preflight-{uuid.uuid4().hex[:8]}"
            split_res = self._herdr("split", "--current", "--direction", self._direction, "--cwd", "/tmp")
            split_obj = self._parse_json(split_res)
            if split_res.returncode != 0:
                return PreflightResult(False, f"herdr pane split failed: {(split_res.stderr or '').strip()}")
            if not split_obj:
                return PreflightResult(False, "herdr pane split: unparseable/empty response")
            pane_id = split_obj.get("result", {}).get("pane", {}).get("pane_id")
            if not pane_id:
                return PreflightResult(False, "herdr pane split: no pane_id in result")
            try:
                steps = (
                    ("rename", ("rename", pane_id, probe_label)),
                    ("run", ("run", pane_id, "exec", "sleep", "5")),
                    ("get", ("get", pane_id)),
                    ("process-info", ("process-info", "--pane", pane_id)),
                    ("list", ("list", "--workspace", endpoint)),
                )
                for verb, args in steps:
                    res = self._herdr(*args)
                    if res.returncode != 0:
                        return PreflightResult(False, f"herdr {verb} failed: {(res.stderr or '').strip()}")
                return PreflightResult(True, "")
            finally:
                close_res = self._herdr("close", pane_id)
                # #633's own confirmed finding: the exec'd probe process auto-closes its pane
                # on exit — a pane_not_found here means the pane is ALREADY gone, not a leak.
                if close_res.returncode != 0:
                    err_obj = self._parse_json(close_res) or {}
                    if err_obj.get("error", {}).get("code") != "pane_not_found":
                        return PreflightResult(False, f"herdr close (probe cleanup) failed: "
                                               f"{(close_res.stderr or '').strip()}")
        except Exception as exc:  # noqa: BLE001 — preflight NEVER raises; unusable == unsupported
            return PreflightResult(False, f"preflight error: {exc}")

    def _resolve_pane_id(self, endpoint: str, name: str) -> Optional[str]:
        """Resolve a caller-chosen `name` to herdr's own current `pane_id` via `pane list` +
        filter by `label == name` — the durable, cross-process resolution mechanism (module
        docstring). Returns None ONLY for a genuinely-empty SUCCESSFUL list (pane not
        found); raises `_PaneListError` if the list command itself failed or returned
        unparseable output (Step-11 finding: conflating these let a transient list failure
        masquerade as "not found", so `kill_session` would falsely report a clean
        idempotent close, and `reap()`'s liveness union would silently drop a live job's
        session from consideration). Raises plain `RuntimeError` on a genuine ambiguity
        (>1 match) — never an arbitrary pick."""
        res = self._herdr("list", "--workspace", endpoint)
        obj = self._parse_json(res)
        if res.returncode != 0 or not obj:
            raise _PaneListError(
                f"herdr pane list failed or returned unparseable output (rc={res.returncode}): "
                f"{(res.stderr or res.stdout or '').strip()}")
        panes = obj.get("result", {}).get("panes", [])
        matches = [p["pane_id"] for p in panes if p.get("label") == name]
        if len(matches) > 1:
            raise RuntimeError(f"herdr: duplicate label match for {name!r}: {matches}")
        return matches[0] if matches else None

    def new_session(self, endpoint: str, name: str, cwd: str, argv: list,  # noqa: ARG002 — endpoint
                    timeout: float = 30) -> subprocess.CompletedProcess:
        # unused for split (workspace is implicit via --current); kept for Protocol parity.
        # NOT atomic (module docstring) — safe only single-threaded per spawn; never
        # parallelize split+run against one target pane from two callers.
        split_res = self._herdr("split", "--current", "--direction", self._direction, "--cwd", cwd,
                                *self._pane_env_args(), timeout=timeout)
        if split_res.returncode != 0:
            return _run_completed(["herdr", "pane", "split"], split_res.returncode,
                                  split_res.stdout or "", split_res.stderr or "")
        split_obj = self._parse_json(split_res)
        if not split_obj:
            # Step-11 finding: a SUCCESSFUL split (rc=0) with an unparseable/empty body must
            # not falsely satisfy the caller's success contract by returning rc=0 anyway.
            return _run_completed(["herdr", "pane", "split"], 1, "",
                                  "herdr pane split: unparseable/empty response despite rc=0")
        pane_id = split_obj.get("result", {}).get("pane", {}).get("pane_id")
        if not pane_id:
            return _run_completed(["herdr", "pane", "split"], 1, "", "herdr pane split: no pane_id in result")
        succeeded = False
        try:
            rename_res = self._herdr("rename", pane_id, name, timeout=timeout)
            if rename_res.returncode != 0:
                return _run_completed(["herdr", "pane", "rename"], rename_res.returncode,
                                      rename_res.stdout or "", rename_res.stderr or "")
            run_res = self._herdr("run", pane_id, "exec", *argv, timeout=timeout)
            succeeded = run_res.returncode == 0
            return run_res
        except Exception as exc:  # noqa: BLE001 — Step-11 finding: an exception (e.g. a runner
            # timeout) from rename/run must still trigger orphan cleanup below, not bypass it.
            return _run_completed(["herdr", "pane", "run"], 1, "", f"herdr new_session error: {exc}")
        finally:
            # best-effort orphan cleanup ONLY on a non-success exit (failed rename, failed run,
            # or a caught exception) — NEVER on success, which would immediately close the pane
            # holding the process we just successfully launched. #633's own confirmed finding
            # means a pane_not_found close on a genuine failure path is common (the exec'd
            # process may have already auto-closed its own pane on exit), never itself a failure.
            if not succeeded:
                self._herdr("close", pane_id, timeout=timeout)

    def pane_pid(self, endpoint: str, name: str, timeout: float = 30) -> subprocess.CompletedProcess:
        try:
            pane_id = self._resolve_pane_id(endpoint, name)
        except _PaneListError as exc:
            return _run_completed(["herdr", "pane", "process-info"], 1, "", str(exc))
        if pane_id is None:
            return _run_completed(["herdr", "pane", "process-info"], 1, "", f"pane {name!r} not found")
        res = self._herdr("process-info", "--pane", pane_id, timeout=timeout)
        obj = self._parse_json(res)
        if res.returncode != 0 or not obj:
            return _run_completed(["herdr", "pane", "process-info"], 1, "",
                                  res.stderr or "process-info failed")
        shell_pid = obj.get("result", {}).get("process_info", {}).get("shell_pid")
        if shell_pid is None:
            return _run_completed(["herdr", "pane", "process-info"], 1, "", "no shell_pid in result")
        return _run_completed(["herdr", "pane", "process-info"], 0, str(shell_pid), "")

    def has_session(self, endpoint: str, name: str, timeout: float = 30) -> subprocess.CompletedProcess:
        try:
            pane_id = self._resolve_pane_id(endpoint, name)
        except _PaneListError as exc:
            # matches TmuxBackend's existing behavior on a raw command failure (has_session
            # just returns whatever the tmux invocation's own returncode was) — a transient
            # list failure reads as "not live" either way; this is not a NEW regression, the
            # binary CompletedProcess contract has no distinct "unknown" state to report.
            return _run_completed(["herdr", "pane", "get"], 1, "", str(exc))
        if pane_id is None:
            return _run_completed(["herdr", "pane", "get"], 1, "", f"pane {name!r} not found")
        res = self._herdr("get", pane_id, timeout=timeout)
        return _run_completed(["herdr", "pane", "get"], res.returncode, res.stdout or "", res.stderr or "")

    def list_sessions(self, endpoint: str, timeout: float = 30) -> subprocess.CompletedProcess:
        res = self._herdr("list", "--workspace", endpoint, timeout=timeout)
        obj = self._parse_json(res)
        if res.returncode != 0 or not obj:
            return _run_completed(["herdr", "pane", "list"], res.returncode, "", res.stderr or "")
        panes = obj.get("result", {}).get("panes", [])
        # only panes THIS supervisor labeled are "sessions" it manages — an unrelated host
        # pane (a human's own terminal tab, no label) is never surfaced as one.
        names = [p["label"] for p in panes if p.get("label")]
        return _run_completed(["herdr", "pane", "list"], 0, "\n".join(names), "")

    def kill_session(self, endpoint: str, name: str, timeout: float = 30) -> subprocess.CompletedProcess:
        try:
            pane_id = self._resolve_pane_id(endpoint, name)
        except _PaneListError as exc:
            # Step-11 finding: a list-command FAILURE is not the same as "genuinely gone" —
            # falsely reporting idempotent success here means a caller (e.g. reap()'s
            # release_permit) proceeds as if cleanup happened when we never even confirmed
            # the pane's real state. Propagate as a real failure instead.
            return _run_completed(["herdr", "pane", "close"], 1, "", str(exc))
        if pane_id is None:
            return _run_completed(["herdr", "pane", "close"], 0, "", "")  # already gone: idempotent
        res = self._herdr("close", pane_id, timeout=timeout)
        return _run_completed(["herdr", "pane", "close"], res.returncode, res.stdout or "", res.stderr or "")

    def teardown_endpoint(self, endpoint: str, timeout: float = 30) -> subprocess.CompletedProcess:  # noqa: ARG002
        # herdr is a host-wide singleton daemon — there is no per-run server to tear down
        # (unlike tmux's kill-server). Intentional no-op, not a stub.
        return _run_completed(["herdr", "pane", "teardown"], 0, "", "")
