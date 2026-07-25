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

**Malformed-split residual risk (Step-11 finding, round 2 — accepted, not fixed).** In the
`not split_obj` / missing-`pane_id` early-return paths, the split call itself may have
already created a real pane on the daemon before its response came back unparseable — and
this code has no `pane_id` at all in that branch (structurally: the ONLY place herdr hands
us the id is the very body we just failed to parse), so no `close` cleanup is possible from
here. This differs from the exception/malformed-run-result cases above, which DO have a
`pane_id` and are cleaned up. Residual: an unlabeled pane can leak on this narrow,
malformed-response path. Accepted per the pane_env risk's same standard (host-local,
disappears when the pane is closed/destroyed) — not fixed because reliably identifying the
new pane without an id would require a list-diff-by-timing heuristic (racy, adds its own
false-positive risk) for a path that requires the daemon to return `rc=0` with a broken body.
Note (pass 9): the SUPERVISOR could never have cleaned this pane either, so the residual is a
property of the malformed response and not of where cleanup lives. Its teardown addresses panes
by the caller-chosen NAME; the leaked pane was never renamed (rename runs after split), and
`kill_session` on an unlabeled pane resolves to no match and reports an idempotent success —
confirmed by driving it.

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


class _PaneGetError(RuntimeError):
    """`pane get` on an already-resolved pane_id failed with something OTHER than a
    confirmed `error.code == "pane_not_found"` (Step-11 finding, round-2 confirming pass:
    the `list` step can succeed and resolve a real pane_id, but the FOLLOW-UP `get` call
    can still fail operationally — daemon hiccup, timeout, malformed body — and that is
    genuinely indeterminate, not "confirmed gone"; conflating the two let `has_session()`
    forward an ordinary nonzero result that `_live()` read as definitively dead)."""


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
        # Step-11 pass 5 breadcrumb: whether the LAST orphan-cleanup `close` confirmed the
        # pane is gone. None = no cleanup has run. The supervisor does not read this (its own
        # `kill_session` result is the load-bearing safety signal); it exists so an
        # unconfirmed cleanup is inspectable rather than silently swallowed. NOTE (pass 9):
        # the supervisor no longer branches on this — its launch teardown releases the permit
        # unconditionally per the documented KNOWN LIMITATION (#648). This stays purely as an
        # inspectable breadcrumb; it is NOT load-bearing for any safety decision.
        self._last_cleanup_confirmed: Optional[bool] = None

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
            probe_pane = split_obj.get("result", {}).get("pane", {})
            pane_id = probe_pane.get("pane_id") if isinstance(probe_pane, dict) else None
            if not pane_id:
                return PreflightResult(False, "herdr pane split: no pane_id in result")
            # Step-11 finding (pass 4, HIGH): the same workspace-mismatch hole new_session had.
            # `--current` splits into the CALLING pane's workspace; if that is not `endpoint`,
            # every later name-resolution call is blind to this pane. Preflight must FAIL here,
            # because its whole job is to prove this backend can address what it creates.
            probe_ws = probe_pane.get("workspace_id")
            if probe_ws != endpoint:
                try:
                    self._herdr("close", pane_id)
                except Exception:  # noqa: BLE001 — best-effort; the mismatch is the finding
                    pass
                return PreflightResult(
                    False,
                    f"herdr pane split created its probe pane in workspace {probe_ws!r} but this "
                    f"backend is scoped to {endpoint!r} — panes it creates would not be "
                    f"addressable by later calls")
            try:
                steps = (
                    ("rename", ("rename", pane_id, probe_label)),
                    ("run", ("run", pane_id, "exec", "sleep", "5")),
                    ("get", ("get", pane_id)),
                    ("process-info", ("process-info", "--pane", pane_id)),
                )
                for verb, args in steps:
                    res = self._herdr(*args)
                    if res.returncode != 0:
                        return PreflightResult(False, f"herdr {verb} failed: {(res.stderr or '').strip()}")
                # ...and the list step must prove the probe pane is actually VISIBLE under
                # `endpoint` (Step-11 pass 4: checking only `rc == 0` let an empty successful
                # listing of the wrong workspace pass, which is exactly the mismatch case).
                # This also proves the rename->list label round-trip every later
                # `_resolve_pane_id` call depends on.
                try:
                    labels = [p.get("label") for p in self._list_panes(endpoint)]
                except _PaneListError as exc:
                    return PreflightResult(False, f"herdr list failed: {exc}")
                if probe_label not in labels:
                    return PreflightResult(
                        False,
                        f"herdr pane list --workspace {endpoint!r} does not show the probe pane "
                        f"{pane_id!r} labeled {probe_label!r} — this backend cannot resolve the "
                        f"panes it creates")
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

    def _list_panes(self, endpoint: str, timeout: float = 30) -> list:
        """Fetch this workspace's current pane list, strictly validated. Raises
        `_PaneListError` if the list command itself failed, OR if it returned rc=0 with
        an unparseable/wrong-shape body (Step-11 finding, round 2: a valid-JSON-but-
        wrong-SHAPE success — e.g. `{"result": {}}`, missing "panes" entirely — must NOT
        silently default to "zero panes" via a bare `.get(..., [])`; that reads as the
        SAME false-success class as a bare truthiness check on the parsed body). This is
        the single shared source of the list-failure-vs-empty distinction used by both
        `_resolve_pane_id` and `list_sessions` — a caller that gets a return value back
        has a CONFIRMED (possibly empty) enumeration; a caller that catches the
        exception has genuinely indeterminate information, never "empty"."""
        res = self._herdr("list", "--workspace", endpoint, timeout=timeout)
        obj = self._parse_json(res)
        if res.returncode != 0 or not obj:
            raise _PaneListError(
                f"herdr pane list failed or returned unparseable output (rc={res.returncode}): "
                f"{(res.stderr or res.stdout or '').strip()}")
        result_obj = obj.get("result")
        if not isinstance(result_obj, dict) or not isinstance(result_obj.get("panes"), list):
            raise _PaneListError(
                f"herdr pane list returned rc=0 with an unexpected shape (missing/invalid "
                f"'result.panes'): {(res.stdout or '').strip()[:200]}")
        panes = result_obj["panes"]
        # Step-11 finding (round 2, confirming pass): the top-level shape check alone isn't
        # enough — a malformed ENTRY (not a dict, or missing "pane_id") must not silently
        # read as "not one of ours" the same way an unlabeled entry legitimately does.
        # Every real pane herdr reports always carries a pane_id (only "label" is ever
        # absent, for a pane this backend doesn't manage) — an entry missing it is
        # corrupted data, not a foreign pane, and must fail loud rather than be dropped.
        for pane in panes:
            if not isinstance(pane, dict) or not isinstance(pane.get("pane_id"), str) \
                    or not pane["pane_id"]:
                raise _PaneListError(
                    f"herdr pane list returned a malformed pane entry (missing/invalid "
                    f"pane_id): {pane!r}")
            # ...and a PRESENT `label` must be a string (Step-11 finding, pass 4: validating
            # only pane_id left `{"pane_id": "w1:p9", "label": []}` passing, where the
            # malformed label silently compares unequal in `_resolve_pane_id` -> a DEFINITIVE
            # "not found" for a job that is actually alive, sending a healthy record to
            # quarantine/kill; a falsey malformed label is likewise dropped by
            # `list_sessions`, letting reap() put the live process in kill_tree). Absent/None
            # stays legitimate — that is a pane this backend does not manage.
            label = pane.get("label")
            if label is not None and not isinstance(label, str):
                raise _PaneListError(
                    f"herdr pane list returned a pane whose 'label' is present but not a "
                    f"string ({type(label).__name__}): {pane!r}")
            # Step-11 finding (pass 5): a present-but-BLANK label (`""`, `"   "`, newline-only)
            # passed the isinstance check and then vanished exactly like the non-string case —
            # it compares unequal in `_resolve_pane_id` (definitive not-found for a live job),
            # is falsey so `list_sessions` drops it, and `reap()` strips whitespace and drops it
            # too. Per this module's own contract, ONLY absent/None is a legitimate unlabeled
            # pane; a present blank label is corrupted data and fails closed.
            if isinstance(label, str) and not label.strip():
                raise _PaneListError(
                    f"herdr pane list returned a pane whose 'label' is present but blank "
                    f"({label!r}): only an ABSENT/null label denotes an unmanaged pane: {pane!r}")
        return panes

    def _resolve_pane_id(self, endpoint: str, name: str) -> Optional[str]:
        """Resolve a caller-chosen `name` to herdr's own current `pane_id` via `pane list` +
        filter by `label == name` — the durable, cross-process resolution mechanism (module
        docstring). Returns None ONLY for a genuinely-empty SUCCESSFUL list (pane not
        found); propagates `_PaneListError` from `_list_panes` untouched (Step-11 finding:
        conflating "list failed" with "not found" let a transient list failure masquerade
        as "not found", so `kill_session` would falsely report a clean idempotent close,
        and `reap()`'s liveness union would silently drop a live job's session from
        consideration). Raises plain `RuntimeError` on a genuine ambiguity (>1 match) —
        never an arbitrary pick."""
        panes = self._list_panes(endpoint)
        matches = [p["pane_id"] for p in panes if p.get("label") == name]  # every entry is a validated dict, _list_panes
        if len(matches) > 1:
            raise RuntimeError(f"herdr: duplicate label match for {name!r}: {matches}")
        return matches[0] if matches else None

    def _close_and_verify(self, pane_id: str, *, timeout: float = 30, attempts: int = 3) -> bool:
        """Best-effort but PERSISTENT orphan cleanup for a pane this call created. Returns True
        only when the pane is verifiably gone — either `close` succeeded, or it reported
        `pane_not_found` (#633: the exec'd process auto-closes its own pane on exit), or a
        follow-up `pane get` confirms absence. Never raises: a cleanup failure must not replace
        the real failure the caller is already returning (Python `finally` semantics)."""
        for _ in range(max(1, attempts)):
            try:
                res = self._herdr("close", pane_id, timeout=timeout)
                if res.returncode == 0:
                    return True
                err = self._parse_json(res)
                err_obj = err.get("error") if isinstance(err, dict) else None
                if isinstance(err_obj, dict) and err_obj.get("code") == "pane_not_found":
                    return True          # already gone: the outcome cleanup exists to produce
            except Exception:  # noqa: BLE001 — retry; never propagate out of a finally block
                pass
            # close did not confirm — ask directly whether the pane still exists
            try:
                got = self._herdr("get", pane_id, timeout=timeout)
                if got.returncode != 0:
                    err = self._parse_json(got)
                    err_obj = err.get("error") if isinstance(err, dict) else None
                    if isinstance(err_obj, dict) and err_obj.get("code") == "pane_not_found":
                        return True      # verifiably absent despite the close's own complaint
            except Exception:  # noqa: BLE001
                pass
        return False

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
        split_pane = split_obj.get("result", {}).get("pane", {})
        pane_id = split_pane.get("pane_id") if isinstance(split_pane, dict) else None
        if not pane_id:
            return _run_completed(["herdr", "pane", "split"], 1, "", "herdr pane split: no pane_id in result")
        # Step-11 finding (pass 4, HIGH): `--current` splits into the CALLING pane's workspace,
        # which is NOT necessarily `endpoint` (the configured HERDR_WORKSPACE_ID). On a mismatch
        # the pane is created in workspace A while every later call — pane_pid, has_session,
        # kill_session — searches B: pane_pid reports not-found, and launch cleanup's
        # kill_session against B finds B genuinely empty and reports IDEMPOTENT SUCCESS while
        # the process in A stays alive, unregistered, with its permit released. Verify
        # membership from the split response's own `workspace_id` (confirmed present live) and
        # fail closed, cleaning up the pane we DO have the id for, rather than launching into a
        # scope we cannot later address.
        split_ws = split_pane.get("workspace_id")
        if split_ws != endpoint:
            try:
                self._herdr("close", pane_id, timeout=timeout)
            except Exception:  # noqa: BLE001 — best-effort; the mismatch is the reported failure
                pass
            return _run_completed(
                ["herdr", "pane", "split"], 1, "",
                f"herdr pane split created pane {pane_id!r} in workspace {split_ws!r} but this "
                f"backend is scoped to {endpoint!r} — refusing to launch into a workspace whose "
                f"panes it cannot address (set HERDR_WORKSPACE_ID to the calling pane's "
                f"workspace, or run from a pane in {endpoint!r})")
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
                # #638 Step-11 finding (round 2): an exception raised HERE (e.g. a runner
                # timeout on close itself) would replace whatever `return` is pending from
                # the try/except above (Python `finally`-exception semantics) — masking the
                # real failure with an unrelated cleanup error. Cleanup is best-effort only.
                #
                # Step-11 pass 5: it is best-effort but no longer SILENT. A cleanup that
                # cannot confirm the pane is gone means a possibly-live payload, so record
                # that on the returned result. NOTE (pass 9): the supervisor's launch teardown
                # releases the permit UNCONDITIONALLY (documented KNOWN LIMITATION, #648), so
                # this is an operator-visible breadcrumb only — not a safety signal.
                # Step-11 pass 10: cleanup RETRIES and then VERIFIES, using the pane_id we
                # already hold. Previously a single close attempt was made and a failure just
                # set the breadcrumb — and since pass 9 correctly stopped the supervisor from
                # making a second, NAME-based attempt (that name-based cleanup is what let a
                # `duplicate session` refusal destroy a pre-existing session), this backend is
                # now the ONLY thing that can clean the pane it created. So it must actually
                # try: bounded retries, then a `pane get` probe to confirm absence. Retrying by
                # pane_id is safe in a way retrying by NAME never was — the id addresses only
                # the pane this call created.
                self._last_cleanup_confirmed = self._close_and_verify(pane_id, timeout=timeout)

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
        # Step-11 finding (round 2): a transient `pane list` failure must NOT collapse to
        # an ordinary nonzero CompletedProcess here — `_live()` treats every nonzero
        # result as "definitively dead" and routes a healthy-but-unlisted job to
        # `_kill_job()`. Let `_PaneListError` propagate so the supervisor can distinguish
        # "confirmed not found" from "couldn't tell" and skip the record for this cycle.
        pane_id = self._resolve_pane_id(endpoint, name)
        if pane_id is None:
            return _run_completed(["herdr", "pane", "get"], 1, "", f"pane {name!r} not found")
        res = self._herdr("get", pane_id, timeout=timeout)
        if res.returncode == 0:
            return _run_completed(["herdr", "pane", "get"], 0, res.stdout or "", "")
        # Step-11 finding (round 2, confirming pass): `list` can succeed and resolve a real
        # pane_id, but the FOLLOW-UP `get` call can still fail for a reason OTHER than the
        # pane genuinely being gone — a daemon hiccup, timeout, or malformed body. Only a
        # confirmed `error.code == "pane_not_found"` (e.g. a genuine list-then-get race) is
        # "confirmed dead"; anything else is indeterminate and must raise, not return an
        # ordinary nonzero result `_live()` would read as "definitively dead".
        err_obj = self._parse_json(res)
        err = err_obj.get("error") if isinstance(err_obj, dict) else None
        err_code = err.get("code") if isinstance(err, dict) else None
        if err_code == "pane_not_found":
            return _run_completed(["herdr", "pane", "get"], res.returncode, "", res.stderr or "")
        raise _PaneGetError(
            f"herdr pane get failed for a resolved pane (rc={res.returncode}): "
            f"{(res.stderr or res.stdout or '').strip()}")

    def list_sessions(self, endpoint: str, timeout: float = 30) -> subprocess.CompletedProcess:
        """Raises `_PaneListError` (via `_list_panes`) on a genuine list-command failure or
        an rc=0-but-malformed body — NEVER returns a nonzero CompletedProcess for that case
        (Step-11 finding, round 2: returning rc!=0 here reads, to a generic Protocol caller,
        the same as TmuxBackend's routine "no sessions on this socket" nonzero exit, which
        `reap()` correctly treats as reliable-confirmed-empty; herdr's failure is NOT that —
        it's "couldn't tell" — so it must be distinguishable by exception, not returncode)."""
        panes = self._list_panes(endpoint, timeout=timeout)
        # only panes THIS supervisor labeled are "sessions" it manages — an unrelated host
        # pane (a human's own terminal tab, no label) is never surfaced as one.
        names = [p["label"] for p in panes if p.get("label")]  # every entry is a validated dict, _list_panes
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
        if res.returncode == 0:
            return _run_completed(["herdr", "pane", "close"], 0, res.stdout or "", "")
        # Step-11 finding (pass 6): the ORDINARY list-then-close disappearance race — the list
        # resolved the pane, then the exec'd process exited and auto-closed its own pane before
        # `close` landed (#633's confirmed behaviour) — came back as a structured
        # `pane_not_found` and was forwarded as a teardown FAILURE. That is confirmed-gone, the
        # very outcome kill_session exists to produce; this backend already treats the same code
        # as confirmed during its own orphan cleanup. Forwarding it as failure made the
        # supervisor hold a quota permit for a pane that verifiably no longer exists.
        err = self._parse_json(res)
        err_obj = err.get("error") if isinstance(err, dict) else None
        if isinstance(err_obj, dict) and err_obj.get("code") == "pane_not_found":
            return _run_completed(["herdr", "pane", "close"], 0, "",
                                  "pane already gone (pane_not_found): idempotent close")
        return _run_completed(["herdr", "pane", "close"], res.returncode, res.stdout or "", res.stderr or "")

    # -- tri-state surface (#638 Step-11 pass 7) -----------------------------------------
    # herdr already distinguishes the three states internally: a confirmed `pane_not_found`
    # vs a raised _PaneListError/_PaneGetError (the probe itself failed). Those map straight
    # onto the shared `Liveness` enum, so the supervisor consumes ONE vocabulary for both
    # backends instead of re-deriving meaning from a returncode.

    def probe_session(self, endpoint: str, name: str, timeout: float = 30) -> "Liveness":
        from .terminal_backend import Liveness  # noqa: PLC0415 — avoids an import cycle
        try:
            res = self.has_session(endpoint, name, timeout=timeout)
        except (_PaneListError, _PaneGetError):
            return Liveness.INDETERMINATE      # the probe failed; NOT a fact about the job
        except Exception:  # noqa: BLE001 — a raising runner (timeout) is also "couldn't tell"
            return Liveness.INDETERMINATE
        return Liveness.CONFIRMED_ALIVE if res.returncode == 0 else Liveness.CONFIRMED_GONE

    def close_session(self, endpoint: str, name: str, timeout: float = 30) -> "Liveness":
        from .terminal_backend import Liveness  # noqa: PLC0415
        try:
            res = self.kill_session(endpoint, name, timeout=timeout)
        except Exception:  # noqa: BLE001
            return Liveness.INDETERMINATE
        # kill_session already normalises the two confirmed-gone cases to rc=0 (a successful
        # close, and the list-then-close `pane_not_found` race); every remaining nonzero is a
        # list/close failure we could not confirm.
        return Liveness.CONFIRMED_GONE if res.returncode == 0 else Liveness.INDETERMINATE

    def enumerate_sessions(self, endpoint: str, timeout: float = 30) -> "tuple[Liveness, list]":
        from .terminal_backend import Liveness  # noqa: PLC0415
        try:
            res = self.list_sessions(endpoint, timeout=timeout)
        except Exception:  # noqa: BLE001 — _PaneListError and friends: enumeration unusable
            return Liveness.INDETERMINATE, []
        if res.returncode != 0:
            return Liveness.INDETERMINATE, []
        names = [l.strip() for l in (res.stdout or "").splitlines() if l.strip()]
        return Liveness.CONFIRMED_ALIVE, names

    def teardown_endpoint(self, endpoint: str, timeout: float = 30) -> subprocess.CompletedProcess:  # noqa: ARG002
        # herdr is a host-wide singleton daemon — there is no per-run server to tear down
        # (unlike tmux's kill-server). Intentional no-op, not a stub.
        return _run_completed(["herdr", "pane", "teardown"], 0, "", "")
