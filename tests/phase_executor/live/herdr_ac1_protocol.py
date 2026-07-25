"""#639 (epic #635 C3) — #633 §AC1's PID-identity qualification protocol, as re-runnable infra.

herdr's `pane run <pane> exec <argv>` launch is what #638's `HerdrBackend` is built on, and it is
**herdr-undocumented/unendorsed**: nothing stops a future herdr release from wrapping the command
in a child process instead of `exec`-replacing the pane's shell. If that happens, every PID the
supervisor holds names the wrong process — it would kill (or fail to kill) a shell while the real
worker runs on. #633 qualified the primitive live (GO, 40/40 reps against pinned herdr 0.7.5) using
a runner that lived in session-tmp and is now gone; this module is that protocol committed, so the
primitive can be **re-qualified rather than re-derived** before the backend is trusted across a
herdr version bump.

Run it (the pre-upgrade gate):

    RUN_LIVE=1 pytest tests/phase_executor/live/test_herdr_pid_identity_live.py -v

Design notes worth knowing before editing:

- **The verdict is TRI-STATE** (`Verdict.GO` / `NO_GO` / `ERROR`, precedence `NO_GO > ERROR > GO`).
  An environment fault (a failed split, a wedged daemon, an unconfirmed teardown) must never read
  as GO, and must never masquerade as a regression either. Conflating "the probe failed" with "the
  answer is no" is the exact defect class #638 spent eleven review passes removing from the
  supervisor; the guard against it must not reintroduce it. So `RepResult` keeps
  `identity_failures` and `env_faults` as SEPARATE lists and nothing ever sums them.
- **The GO threshold is `GO_MAX_FAILURES = 0` per condition, in code** — #633 fixed it before
  running, and `decide()` enforces it, so a regression fails loudly instead of relying on a human
  reading a table.
- **This module does not reach into `HerdrBackend`'s privates.** `launch_argv()` builds the launch
  invocation itself, and a CI-safe test
  (`tests/phase_executor/test_herdr_ac1_protocol.py::test_launch_argv_equals_what_new_session_issues`)
  pins it EQUAL to the argv `HerdrBackend.new_session` actually issues — so if the product code's
  launch form ever changes, that test fails in CI (where herdr is absent) rather than this check
  quietly re-qualifying a form nothing uses any more. Identity, liveness and teardown DO go through
  the backend's public surface (`pane_pid`, `probe_session`, `close_session`, `list_sessions`).
- **Why reps provision their own pane instead of calling `new_session` for everything.**
  `new_session` always splits a fresh pane (`split` -> `rename` -> `run ... exec ...`, one call), and
  #633's `reused` condition is by definition a pane that already ran a plain non-`exec` command, so
  that condition cannot be expressed through `new_session` at all. The live module additionally
  drives ONE rep through `new_session` end-to-end for the production entry path; that rep cannot
  observe a pre-`exec` pid (the three calls are atomic from the caller's side), which is a real
  limitation of that rep and not a property of the protocol.
- **The sentinel holds until released.** The worker announces itself, then waits for a release file
  the harness touches only after the mid-run PID read. Without that handshake the mid-run read
  races the worker's exit, and "0 failures" would mean "we got lucky". `SENTINEL_SELF_TIMEOUT_S`
  bounds the wait so a harness that dies mid-rep cannot leave a worker (and its pane) alive.
- **Blast radius on a real, host-wide, shared daemon:** only panes this module created, labeled
  `rg639-ac1-*`. No `close` is ever issued against a pane id this module did not create, and no
  `pane_env` is ever passed (so the owner-accepted `ps`-visibility risk is not exercised here).

Doubles as the worker: `python3 herdr_ac1_protocol.py --sentinel <observation.json> <release-file>
[timeout-seconds]`. Harness and worker share one file so their on-disk contract cannot drift.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

# #633's protocol constants, fixed before the qualification ran.
GO_MAX_FAILURES = 0
REPS_PER_CONDITION = 20
CONDITIONS = ("cold", "reused")

LABEL_PREFIX = "rg639-ac1-"
SENTINEL_SELF_TIMEOUT_S = 20.0
SENTINEL_POLL_S = 0.05
DEFAULT_DEADLINE_S = 30.0
DEFAULT_POLL_S = 0.05
SPLIT_DIRECTION = "right"
# Short leash on the availability probes: they run at pytest COLLECTION time, and a wedged
# daemon on the runner's 30s default would stall the whole suite twice over (cross-model
# review finding).
AVAILABILITY_TIMEOUT_S = 5.0

MODULE_PATH = str(pathlib.Path(__file__).resolve())
ENUMERATION_FAILED = "<pane enumeration failed>"


class Verdict(Enum):
    """GO = re-qualified. NO_GO = a real PID-identity regression. ERROR = the run could not
    answer the question (environment), which is neither a pass nor a regression."""

    GO = "go"
    NO_GO = "no_go"
    ERROR = "error"


@dataclass
class RepResult:
    condition: str
    label: str
    pre_pid: "int | None" = None
    mid_pid: "int | None" = None
    sentinel_pid: "int | None" = None
    pre_cmdline: "list | None" = None
    post_cmdline: "list | None" = None
    worker_argv: "list | None" = None
    sentinel: "dict | None" = None
    identity_failures: list = field(default_factory=list)
    env_faults: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.identity_failures and not self.env_faults


@dataclass
class Report:
    verdict: Verdict
    reps: tuple = ()
    leaked: tuple = ()

    def counts(self) -> dict:
        out: dict = {}
        for rep in self.reps:
            slot = out.setdefault(rep.condition, {"reps": 0, "identity_failures": 0, "env_faults": 0})
            slot["reps"] += 1
            slot["identity_failures"] += len(rep.identity_failures)
            slot["env_faults"] += len(rep.env_faults)
        return out

    def failures(self) -> list:
        return [f"[{r.condition} {r.label}] {msg}"
                for r in self.reps for msg in (*r.identity_failures, *r.env_faults)]

    def summary(self) -> str:
        per = ", ".join(
            f"{cond}: {c['reps']} reps, {c['identity_failures']} identity, {c['env_faults']} env"
            for cond, c in sorted(self.counts().items()))
        leaked = f", leaked={list(self.leaked)}" if self.leaked else ""
        return f"{self.verdict.value.upper()} ({per}{leaked})"


# --------------------------------------------------------------------------- invocation builders


def launch_argv(pane_id: str, argv) -> list:
    """The exec-injection launch. Pinned EQUAL to `HerdrBackend.new_session`'s own launch call by
    a CI-safe test — change both together or that test fails."""
    return ["herdr", "pane", "run", pane_id, "exec", *argv]


def sentinel_argv(observation, release, *, python: "str | None" = None,
                  timeout: float = SENTINEL_SELF_TIMEOUT_S) -> list:
    return [python or sys.executable, MODULE_PATH, "--sentinel",
            str(observation), str(release), str(timeout)]


# --------------------------------------------------------------------------- small helpers


def _json_body(res):
    """herdr writes a success payload to stdout and a structured error to stderr (the same
    convention `HerdrBackend._parse_json` reads)."""
    raw = res.stdout if res.returncode == 0 else res.stderr
    try:
        return json.loads(raw) if raw else None
    except (ValueError, TypeError):
        return None


def _err_code(res) -> "str | None":
    body = _json_body(res)
    err = body.get("error") if isinstance(body, dict) else None
    return err.get("code") if isinstance(err, dict) else None


def _msg(res) -> str:
    return ((res.stderr or res.stdout or "") or "").strip()[:200]


def _read_cmdline(pid):
    """procfs `cmdline` as a list, or None when unreadable. Same read shape `pane_runner`
    already uses on `/proc/<pid>/stat`."""
    if pid is None:
        return None
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    return raw.decode(errors="replace").rstrip("\0").split("\0") if raw else []


def _atomic_write_json(path, payload) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path):
    try:
        return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _wait_for_file(path, predicate, *, deadline_s: float, poll_s: float) -> bool:
    """True once `path` holds JSON satisfying `predicate`. A partially written file simply fails
    the predicate and is retried — the writer replaces atomically."""
    deadline = time.monotonic() + deadline_s
    while True:
        payload = _read_json(path)
        if isinstance(payload, dict) and predicate(payload):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_s)


def _ensure_phase_executor_importable() -> None:
    """Make the STANDALONE gate work as documented, with no PYTHONPATH ceremony. Under pytest the
    package is already importable (tests/phase_executor/conftest.py's shim); run directly, nothing
    puts `phase_executor/src` on the path — and because the repo has a `phase_executor/` DIRECTORY,
    a bare import can resolve to an empty NAMESPACE package instead of failing cleanly, so the
    failure surfaces later as a missing submodule. Insert the real src dir ahead of everything; a
    genuine absence still raises at the call site rather than being swallowed here."""
    try:
        from phase_executor import herdr_backend  # noqa: PLC0415,F401
        return
    except ImportError:
        pass
    src = pathlib.Path(MODULE_PATH).resolve().parents[3] / "phase_executor" / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
        sys.modules.pop("phase_executor", None)   # drop any namespace-package placeholder


def default_runner():
    """The SAME runner `HerdrBackend` uses by default, so the harness's own calls and the
    backend's calls cannot diverge in timeout/decoding behaviour."""
    from phase_executor.supervisor import _default_run  # noqa: PLC0415 — live path only

    return _default_run


def _int_or_none(text):
    try:
        return int((text or "").strip())
    except (TypeError, ValueError):
        return None


def _valid_pid(pid) -> bool:
    """A pid this protocol can reason about. `<= 1` is refused for the same reason every kill
    path in `supervisor.py` refuses it — 0/1 are never a launched worker, and treating one as a
    real observation is how a vacuous check passes."""
    return isinstance(pid, int) and pid > 1


# --------------------------------------------------------------------------- availability / skip


def herdr_available(*, which=None, run=None, env=None):
    """(True, workspace_id) when this process can actually drive the protocol, else
    (False, reason). Three independent requirements, each of which must SKIP rather than fail
    (#639 AC2): the binary, the qualified version floor, and a RESOLVABLE CALLING PANE —
    `pane split --current` resolves the caller's own pane, so a pane-less process (cron, a plain
    headless shell) can never run this check."""
    _ensure_phase_executor_importable()
    from phase_executor.herdr_backend import HERDR_VERSION_FLOOR  # noqa: PLC0415

    which = shutil.which if which is None else which
    env = os.environ if env is None else env
    if which("herdr") is None:
        return False, "herdr binary not on PATH"
    run = default_runner() if run is None else run

    try:
        ver = run(["herdr", "--version"], timeout=AVAILABILITY_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — a wedged daemon means UNAVAILABLE, never a crash
        return False, f"herdr --version raised ({exc!r})"
    if ver.returncode != 0:
        return False, f"herdr --version failed: {_msg(ver)}"
    parts = (ver.stdout or "").strip().split()
    version_str = parts[-1] if parts else ""
    try:
        ver_tuple = tuple(int(p) for p in version_str.split("."))
    except ValueError:
        ver_tuple = ()
    if not ver_tuple or ver_tuple < HERDR_VERSION_FLOOR:
        return False, (f"herdr {version_str!r} is below the qualified floor "
                       f"{'.'.join(str(p) for p in HERDR_VERSION_FLOOR)}")

    try:
        res = run(["herdr", "pane", "current"], timeout=AVAILABILITY_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        return False, f"herdr pane current raised ({exc!r})"
    pane = ((_json_body(res) or {}).get("result") or {}).get("pane") or {}
    workspace = pane.get("workspace_id") or env.get("HERDR_WORKSPACE_ID")
    if res.returncode != 0 or not workspace:
        return False, ("no resolvable calling pane (`herdr pane current` did not report one) — "
                       "`pane split --current` cannot resolve from a pane-less process")
    return True, workspace


# --------------------------------------------------------------------------- the protocol


def _teardown(rep: RepResult, *, backend, endpoint, run_herdr, pane_id, labeled: bool) -> None:
    """Leave nothing behind, and be honest about it. A pane that `exec`'d and exited has already
    auto-closed itself (#633) — that is CONFIRMED_GONE, not a failure. An INDETERMINATE teardown
    is an env fault: it is neither a clean close nor a leak we can prove."""
    from phase_executor.terminal_backend import Liveness  # noqa: PLC0415

    if not labeled:
        # rename never landed, so no label resolves to it — close the id we hold directly.
        res = run_herdr(["herdr", "pane", "close", pane_id])
        if res.returncode != 0 and _err_code(res) != "pane_not_found":
            rep.env_faults.append(f"could not close unlabeled pane {pane_id}: {_msg(res)}")
        return
    try:
        state = backend.probe_session(endpoint, rep.label)
        if state is Liveness.CONFIRMED_GONE:
            return
        closed = backend.close_session(endpoint, rep.label)
        if closed is not Liveness.CONFIRMED_GONE:
            rep.env_faults.append(
                f"teardown unconfirmed for {rep.label} (probe={state.name}, close={closed.name})")
    except Exception as exc:  # noqa: BLE001 — teardown must not replace the rep's own findings
        rep.env_faults.append(f"teardown raised for {rep.label}: {exc!r}")


def _assess(rep: RepResult, worker_argv: list) -> None:
    """#633's success criteria, evaluated. Every failure here is an IDENTITY failure — a
    statement about herdr's exec behaviour, not about our ability to observe it.

    `run_rep` guarantees `pre_pid`/`mid_pid` are valid pids before this runs (an unusable pid is
    an env fault that returns early), so these comparisons are UNCONDITIONAL — a cross-model
    review found the earlier `is not None` guards made the whole chain evaporate on a rep that
    never observed a pid."""
    if rep.pre_pid != rep.mid_pid:
        rep.identity_failures.append(
            f"pre-exec shell pid {rep.pre_pid} != mid-run pid {rep.mid_pid} — the exec'd process "
            f"is not the pane's own process")
    if rep.sentinel_pid is None:
        rep.identity_failures.append("the worker recorded no pid of its own")
    elif rep.sentinel_pid != rep.pre_pid:
        rep.identity_failures.append(
            f"worker's own os.getpid() {rep.sentinel_pid} != pre-exec shell pid {rep.pre_pid} — "
            f"exec did not replace the shell (a wrapping child would look exactly like this)")
    if rep.post_cmdline is None:
        # Self-review finding: an UNREADABLE /proc entry is "could not observe", not "the exec
        # transition failed" — the pid-identity assertions above are the definitive signal and
        # stand on their own, so this must not be dressed up as a regression.
        rep.env_faults.append(
            f"could not read /proc/{rep.mid_pid}/cmdline — cmdline transition unobserved")
    elif rep.post_cmdline != worker_argv:
        rep.identity_failures.append(
            f"post-exec /proc cmdline did not become the worker argv (got {rep.post_cmdline!r})")
    if rep.pre_cmdline is not None and rep.pre_cmdline == worker_argv:
        rep.identity_failures.append(
            "pre-exec /proc cmdline was ALREADY the worker argv — the pane was not a shell, so "
            "this rep proves nothing about the exec transition")
    payload = rep.sentinel or {}
    if payload.get("phase") != "completed" or payload.get("exit_code") != 0:
        rep.identity_failures.append(
            f"worker did not record a clean completion (phase={payload.get('phase')!r}, "
            f"exit_code={payload.get('exit_code')!r})")
    elif payload.get("timed_out"):
        # Self-review finding: the worker self-timed-out instead of being released, so the
        # release handshake did NOT hold — which means the "mid-run" read is not provably
        # mid-run and this rep's identity chain proves less than it appears to. A rep whose
        # guarantee was weakened must not be counted as a clean pass; it is an env fault
        # (slow host / stalled harness), never a regression claim.
        rep.env_faults.append(
            "worker hit its own SENTINEL_SELF_TIMEOUT_S instead of being released — the mid-run "
            "read is not provably mid-run, so this rep does not qualify anything")


def run_rep(condition: str, *, backend, endpoint: str, run_herdr, workdir, label: str,
            read_cmdline=_read_cmdline, deadline_s: float = DEFAULT_DEADLINE_S,
            poll_s: float = DEFAULT_POLL_S, direction: str = SPLIT_DIRECTION) -> RepResult:
    """One rep of #633 §AC1. `cold` = fresh split then immediate exec; `reused` = one plain
    non-exec command in the pane first (a pane cannot host two sequential execs — the first exit
    kills it — so this is the design doc's own documented reading of "reused slot")."""
    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {CONDITIONS} (got {condition!r})")
    rep = RepResult(condition=condition, label=label)
    rep_dir = pathlib.Path(workdir) / label
    rep_dir.mkdir(parents=True, exist_ok=True)
    observation = rep_dir / "observation.json"
    release = rep_dir / "release"
    worker_argv = sentinel_argv(observation, release)
    rep.worker_argv = worker_argv
    pane_id = None
    labeled = False
    try:
        res = run_herdr(["herdr", "pane", "split", "--current", "--direction", direction,
                         "--cwd", str(rep_dir)])
        pane = ((_json_body(res) or {}).get("result") or {}).get("pane") or {}
        pane_id = pane.get("pane_id")
        if res.returncode != 0 or not pane_id:
            rep.env_faults.append(f"pane split failed (rc={res.returncode}): {_msg(res)}")
            return rep
        if pane.get("workspace_id") != endpoint:
            rep.env_faults.append(
                f"split created its pane in workspace {pane.get('workspace_id')!r} but this run is "
                f"scoped to {endpoint!r} — later calls could not address it")
            return rep

        res = run_herdr(["herdr", "pane", "rename", pane_id, label])
        if res.returncode != 0:
            rep.env_faults.append(f"pane rename failed (rc={res.returncode}): {_msg(res)}")
            return rep
        labeled = True

        if condition == "reused":
            res = run_herdr(["herdr", "pane", "run", pane_id, "true"])
            if res.returncode != 0:
                rep.env_faults.append(f"warm-up run failed (rc={res.returncode}): {_msg(res)}")
                return rep

        pre = backend.pane_pid(endpoint, label)
        if pre.returncode != 0:
            rep.env_faults.append(f"pre-exec pane_pid failed: {_msg(pre)}")
            return rep
        rep.pre_pid = _int_or_none(pre.stdout)
        # Cross-model review finding (High): `pane_pid` returns rc=0 carrying whatever herdr put
        # in `shell_pid`, so an unparseable value (or a nonsense pid <= 1) yields pre_pid=None and
        # every downstream comparison in _assess — each guarded by `is not None` — silently
        # evaporates, letting a rep that observed NO pid at all count toward GO. Refuse here.
        if not _valid_pid(rep.pre_pid):
            rep.env_faults.append(
                f"pre-exec pane_pid returned rc=0 but no usable pid ({(pre.stdout or '').strip()!r})")
            return rep
        rep.pre_cmdline = read_cmdline(rep.pre_pid)

        res = run_herdr(launch_argv(pane_id, worker_argv))
        if res.returncode != 0:
            rep.env_faults.append(f"exec launch failed (rc={res.returncode}): {_msg(res)}")
            return rep

        if not _wait_for_file(observation, lambda d: d.get("phase") in ("started", "completed"),
                              deadline_s=deadline_s, poll_s=poll_s):
            rep.env_faults.append("the worker never announced itself on disk")
            return rep

        mid = backend.pane_pid(endpoint, label)
        if mid.returncode != 0:
            rep.env_faults.append(f"mid-run pane_pid failed: {_msg(mid)}")
            return rep
        rep.mid_pid = _int_or_none(mid.stdout)
        if not _valid_pid(rep.mid_pid):
            rep.env_faults.append(
                f"mid-run pane_pid returned rc=0 but no usable pid ({(mid.stdout or '').strip()!r})")
            return rep
        rep.post_cmdline = read_cmdline(rep.mid_pid)

        release.touch()
        _wait_for_file(observation, lambda d: d.get("phase") == "completed",
                       deadline_s=deadline_s, poll_s=poll_s)
        rep.sentinel = _read_json(observation)
        rep.sentinel_pid = (rep.sentinel or {}).get("pid")
        _assess(rep, worker_argv)
        return rep
    except Exception as exc:  # noqa: BLE001 — a raising rep is an environment fault, never a GO
        rep.env_faults.append(f"rep raised: {exc!r}")
        return rep
    finally:
        if pane_id is not None:
            _teardown(rep, backend=backend, endpoint=endpoint, run_herdr=run_herdr,
                      pane_id=pane_id, labeled=labeled)


def decide(reps, *, leaked=(), expected_reps: int = REPS_PER_CONDITION,
           conditions=CONDITIONS) -> Verdict:
    """The encoded GO threshold (#639 AC3). `NO_GO` outranks `ERROR`: a confirmed identity
    mismatch is a fact, and a coincident environment fault elsewhere in the run must not soften
    it to "could not tell". Everything short of a complete, clean run is `ERROR` — never `GO`."""
    per_condition = {cond: [r for r in reps if r.condition == cond] for cond in conditions}
    for cond_reps in per_condition.values():
        if sum(len(r.identity_failures) for r in cond_reps) > GO_MAX_FAILURES:
            return Verdict.NO_GO
    if any(r.identity_failures for r in reps):   # a rep tagged with an unknown condition
        return Verdict.NO_GO
    if leaked or any(r.env_faults for r in reps):
        return Verdict.ERROR
    if any(len(cond_reps) < expected_reps for cond_reps in per_condition.values()):
        return Verdict.ERROR
    return Verdict.GO


def sweep_labels(backend, endpoint: str, *, prefix: str = LABEL_PREFIX) -> tuple:
    """Labels carrying this check's prefix that are still enumerable afterwards. An enumeration
    that FAILS is reported as its own token rather than as "nothing left" — the difference is
    exactly the confirmed-vs-couldn't-tell distinction this module refuses to blur."""
    try:
        res = backend.list_sessions(endpoint)
    except Exception:  # noqa: BLE001
        return (ENUMERATION_FAILED,)
    if res.returncode != 0:
        return (ENUMERATION_FAILED,)
    return tuple(n.strip() for n in (res.stdout or "").splitlines()
                 if n.strip().startswith(prefix))


def qualify(*, backend, endpoint: str, run_herdr, workdir, reps: int = REPS_PER_CONDITION,
            conditions=CONDITIONS, read_cmdline=_read_cmdline,
            deadline_s: float = DEFAULT_DEADLINE_S, poll_s: float = DEFAULT_POLL_S,
            label_prefix: str = LABEL_PREFIX, on_rep=None) -> Report:
    """Run every condition's reps SERIALLY (`HerdrBackend`'s split->rename->run is not atomic;
    never parallelize against one daemon) and return the tri-state verdict."""
    results: list = []
    for condition in conditions:
        for index in range(reps):
            label = f"{label_prefix}{condition}-{index:02d}-{uuid.uuid4().hex[:6]}"
            rep = run_rep(condition, backend=backend, endpoint=endpoint, run_herdr=run_herdr,
                          workdir=workdir, label=label, read_cmdline=read_cmdline,
                          deadline_s=deadline_s, poll_s=poll_s)
            results.append(rep)
            if on_rep is not None:
                on_rep(rep)
    leaked = sweep_labels(backend, endpoint, prefix=label_prefix)
    return Report(verdict=decide(results, leaked=leaked, expected_reps=reps,
                                 conditions=conditions),
                  reps=tuple(results), leaked=leaked)


# --------------------------------------------------------------------------- the worker


def _sentinel_main(observation: str, release: str, timeout: float) -> int:
    """The exec'd worker: announce, wait to be released, record a clean completion. Shaped after
    `pane_runner`'s real sentinel contract (an observation.json the supervisor reads from disk),
    trimmed to what the PID-identity question needs."""
    pid = os.getpid()
    cmdline = _read_cmdline(pid) or []
    _atomic_write_json(observation, {"phase": "started", "pid": pid, "cmdline": cmdline})
    deadline = time.monotonic() + timeout
    timed_out = True
    while time.monotonic() < deadline:
        if os.path.exists(release):
            timed_out = False
            break
        time.sleep(SENTINEL_POLL_S)
    _atomic_write_json(observation, {"phase": "completed", "exit_code": 0, "pid": pid,
                                     "cmdline": cmdline, "timed_out": timed_out})
    return 0


# --------------------------------------------------------------------------- the gate entry point

# Cross-model review finding (High): `pytest` exits 0 when every test SKIPS, so the pytest form of
# this check reports SUCCESS on a host where the gate never ran — an operator or a script reading
# only the exit status would accept a herdr upgrade with no GO verdict, which is the very
# no-answer-as-success failure this protocol exists to prevent. The pytest module stays as the
# suite-collection surface (AC2 requires a visible SKIP there, never a red CI lane); THIS is the
# documented pre-upgrade gate, and it cannot exit 0 without a GO.
GATE_EXIT_GO = 0
GATE_EXIT_NO_GO = 2
GATE_EXIT_ERROR = 3
GATE_EXIT_UNAVAILABLE = 4

_GATE_EXITS = {Verdict.GO: GATE_EXIT_GO, Verdict.NO_GO: GATE_EXIT_NO_GO,
               Verdict.ERROR: GATE_EXIT_ERROR}


def gate_exit_code(verdict: Verdict) -> int:
    """Only `GO` maps to 0. Unavailable prerequisites map to GATE_EXIT_UNAVAILABLE, never 0."""
    return _GATE_EXITS[verdict]


def _gate_main(reps: int = REPS_PER_CONDITION) -> int:
    import tempfile  # noqa: PLC0415 — gate path only

    _ensure_phase_executor_importable()
    ok, detail = herdr_available()
    if not ok:
        print(f"UNAVAILABLE: {detail}\nThe gate did NOT run — this is not a pass.", file=sys.stderr)
        return GATE_EXIT_UNAVAILABLE
    from phase_executor.herdr_backend import HerdrBackend  # noqa: PLC0415

    backend = HerdrBackend(workspace_id=detail)
    with tempfile.TemporaryDirectory(prefix="rg639-gate-") as workdir:
        report = qualify(backend=backend, endpoint=detail, run_herdr=default_runner(),
                         workdir=workdir, reps=reps)
    print(report.summary())
    for failure in report.failures():
        print(f"  {failure}")
    return gate_exit_code(report.verdict)


_USAGE = ("usage: herdr_ac1_protocol.py --gate [reps]\n"
          "       herdr_ac1_protocol.py --sentinel <observation.json> <release-file> "
          "[timeout-seconds]")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--gate" and len(argv) in (1, 2):
        try:
            reps = int(argv[1]) if len(argv) == 2 else REPS_PER_CONDITION
        except ValueError:
            print(_USAGE, file=sys.stderr)
            return 1
        return _gate_main(reps)
    if not argv or argv[0] != "--sentinel" or len(argv) not in (3, 4):
        print(_USAGE, file=sys.stderr)
        return 1
    timeout = float(argv[3]) if len(argv) == 4 else SENTINEL_SELF_TIMEOUT_S
    return _sentinel_main(argv[1], argv[2], timeout)


if __name__ == "__main__":
    sys.exit(main())
