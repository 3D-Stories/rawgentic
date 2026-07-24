"""#638 (epic #635 C2) — HerdrBackend: a `TerminalBackend` implementation on herdr's pane
primitives, config-gated for the `build` seat only (tmux stays the default until independently
proven). Grounded in the live #633 qualification (GO, 40/40 PID-identity reps against pinned
herdr 0.7.5): `docs/planning/2026-07-23-633-herdr-build-seat-launch-backend-design.md`.

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
same pane correctly.

**Two-call, not one, and NOT atomic (named risk, #633/#638).** herdr has no single primitive
matching tmux's `new-session` (create + launch in one call). `new_session` here is THREE herdr
calls in sequence: `pane split` (create) -> `pane rename` (durable tag) -> `pane run ... exec
...` (launch). This is safe only single-threaded per spawn — never parallelize split+run against
one target pane from two callers (the same constraint the design doc names for the two-call
tmux-equivalent split). A failure after `split` (rename or run) best-effort closes the orphaned
pane before raising, mirroring `TmuxSupervisor.launch`'s own best-effort `kill_session` on a
post-spawn failure (`supervisor.py:528-532`) — but here the cleanup lives inside this backend,
since the supervisor never sees the intermediate pane id.

**herdr in CI: absent.** `.github/workflows/ci.yml` installs no herdr binary — every test for this
module mocks the injected `run` callable exactly as `TmuxBackend`'s tests mock `tmux`; nothing
here is exercised against a real herdr daemon in CI.
"""
from __future__ import annotations

import json
import subprocess
from typing import Optional

HERDR_VERSION_FLOOR = (0, 7, 5)  # the pinned version #633 qualified live (40/40 PID-identity reps)


def _run_completed(args: list, returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    """Synthesize the `subprocess.CompletedProcess`-shaped result every `TerminalBackend`
    method returns — `TmuxSupervisor` reads only `.returncode`/`.stdout`/`.stderr`, never
    anything backend-specific (`terminal_backend.py`'s Protocol contract)."""
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


class HerdrBackend:
    """`TerminalBackend` on herdr's pane primitives. `run` is the SAME injected
    ``run(cmd, *, env=None, cwd=None, timeout=30) -> subprocess.CompletedProcess`` contract
    `TmuxBackend`/`supervisor._default_run` already use — never a locally-invented shape."""

    def __init__(self, *, run, env: Optional[dict] = None, workspace_id: Optional[str] = None):
        self._run = run
        self._env = env
        self._workspace_id = workspace_id

    def _herdr(self, *args, timeout: float = 30) -> subprocess.CompletedProcess:
        return self._run(["herdr", "pane", *args], env=self._env, timeout=timeout)

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
        from .supervisor import PreflightResult  # noqa: PLC0415 — supervisor imports THIS module
        import shutil
        import uuid

        try:
            if shutil.which("herdr") is None:
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
            split_res = self._herdr("split", "--workspace", endpoint, "--cwd", "/tmp")
            split_obj = self._parse_json(split_res)
            if split_res.returncode != 0 or not split_obj:
                return PreflightResult(False, f"herdr pane split failed: {(split_res.stderr or '').strip()}")
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
        docstring). Returns None (not found) or raises on a genuine ambiguity (>1 match) —
        never an arbitrary pick."""
        res = self._herdr("list", "--workspace", endpoint)
        obj = self._parse_json(res)
        if res.returncode != 0 or not obj:
            return None
        panes = obj.get("result", {}).get("panes", [])
        matches = [p["pane_id"] for p in panes if p.get("label") == name]
        if len(matches) > 1:
            raise RuntimeError(f"herdr: duplicate label match for {name!r}: {matches}")
        return matches[0] if matches else None

    def new_session(self, endpoint: str, name: str, cwd: str, argv: list,
                    timeout: float = 30) -> subprocess.CompletedProcess:
        # NOT atomic (module docstring) — safe only single-threaded per spawn; never
        # parallelize split+run against one target pane from two callers.
        split_res = self._herdr("split", "--workspace", endpoint, "--cwd", cwd, timeout=timeout)
        split_obj = self._parse_json(split_res)
        if split_res.returncode != 0 or not split_obj:
            return _run_completed(["herdr", "pane", "split"], split_res.returncode,
                                  split_res.stdout or "", split_res.stderr or "")
        pane_id = split_obj.get("result", {}).get("pane", {}).get("pane_id")
        if not pane_id:
            return _run_completed(["herdr", "pane", "split"], 1, "", "herdr pane split: no pane_id in result")
        rename_res = self._herdr("rename", pane_id, name, timeout=timeout)
        if rename_res.returncode != 0:
            self._herdr("close", pane_id, timeout=timeout)  # best-effort orphan cleanup
            return _run_completed(["herdr", "pane", "rename"], rename_res.returncode,
                                  rename_res.stdout or "", rename_res.stderr or "")
        run_res = self._herdr("run", pane_id, "exec", *argv, timeout=timeout)
        if run_res.returncode != 0:
            self._herdr("close", pane_id, timeout=timeout)  # best-effort orphan cleanup
        return run_res

    def pane_pid(self, endpoint: str, name: str, timeout: float = 30) -> subprocess.CompletedProcess:
        pane_id = self._resolve_pane_id(endpoint, name)
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
        pane_id = self._resolve_pane_id(endpoint, name)
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
        pane_id = self._resolve_pane_id(endpoint, name)
        if pane_id is None:
            return _run_completed(["herdr", "pane", "close"], 0, "", "")  # already gone: idempotent
        res = self._herdr("close", pane_id, timeout=timeout)
        return _run_completed(["herdr", "pane", "close"], res.returncode, res.stdout or "", res.stderr or "")

    def teardown_endpoint(self, endpoint: str, timeout: float = 30) -> subprocess.CompletedProcess:  # noqa: ARG002
        # herdr is a host-wide singleton daemon — there is no per-run server to tear down
        # (unlike tmux's kill-server). Intentional no-op, not a stub.
        return _run_completed(["herdr", "pane", "teardown"], 0, "", "")
