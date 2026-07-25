"""#639 (epic #635 C3) — CI-safe tests for the AC1 PID-identity protocol harness.

herdr is absent from CI entirely, so the LIVE reps live in
`tests/phase_executor/live/test_herdr_pid_identity_live.py` (marked `live`, skipped unless
RUN_LIVE=1). These tests cover everything about the harness that does NOT need a real daemon:

- the encoded GO threshold and its TRI-STATE verdict (AC3) — a fake daemon that stops honoring
  `exec` must produce NO_GO, and a fake daemon whose provisioning fails must produce ERROR, never
  a silent GO and never a false regression claim;
- the launch-argv drift pin — the argv the harness issues must EQUAL the argv
  `HerdrBackend.new_session` issues, so the check can never end up qualifying a form the product
  code no longer uses;
- the skip predicate (AC2) — herdr absent / below floor / no calling pane each SKIP with a reason;
- the sentinel's own on-disk contract, exercised as a plain subprocess (no herdr involved).
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

HARNESS_PATH = pathlib.Path(__file__).resolve().parent / "live" / "herdr_ac1_protocol.py"


def _load_harness():
    """Load by PATH rather than trusting sys.path ordering — the harness lives in the `live/`
    subdir, which pytest only inserts when it collects a module from there. Registering in
    sys.modules before exec is required, not optional: @dataclass resolves its annotations by
    looking its own module up there."""
    spec = importlib.util.spec_from_file_location("herdr_ac1_protocol", HARNESS_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ac1 = _load_harness()


def _completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


class FakeDaemon:
    """A herdr stand-in: enough of the pane verbs for one rep, with switches for the two
    failure modes that matter (exec silently not honored; provisioning broken)."""

    def __init__(self, *, workspace="w1", honor_exec=True, split_rc=0, shell_pid=4242,
                 exec_pid=4242, write_sentinel=True, sentinel_pid=None):
        self.workspace = workspace
        self.honor_exec = honor_exec
        self.split_rc = split_rc
        self.shell_pid = shell_pid
        self.exec_pid = exec_pid
        self.write_sentinel = write_sentinel
        self.sentinel_pid = sentinel_pid
        self.panes = {}            # pane_id -> label or None
        self.exec_argv = {}        # pane_id -> the argv handed to `run <pane> exec ...`
        self.calls = []
        self._next = 0

    # -- the injected runner contract: run(cmd, *, env=None, cwd=None, timeout=30)
    def __call__(self, cmd, *, env=None, cwd=None, timeout=30):  # noqa: ARG002
        self.calls.append(list(cmd))
        verb = cmd[2] if len(cmd) > 2 else ""
        if verb == "split":
            if self.split_rc != 0:
                return _completed(cmd, self.split_rc, "", '{"error":{"code":"no_current_pane"}}')
            self._next += 1
            pane_id = f"{self.workspace}:p{self._next}"
            self.panes[pane_id] = None
            body = {"result": {"pane": {"pane_id": pane_id, "workspace_id": self.workspace}}}
            return _completed(cmd, 0, json.dumps(body))
        if verb == "rename":
            self.panes[cmd[3]] = cmd[4]
            return _completed(cmd, 0, "{}")
        if verb == "run":
            pane_id = cmd[3]
            if len(cmd) > 4 and cmd[4] == "exec":
                argv = list(cmd[5:])
                self.exec_argv[pane_id] = argv
                if self.write_sentinel:
                    self._write_sentinel(argv)
            return _completed(cmd, 0, "{}")
        if verb == "process-info":
            pane_id = cmd[4]
            if pane_id not in self.panes:
                return _completed(cmd, 1, "", '{"error":{"code":"pane_not_found"}}')
            # A herdr that stopped honoring `exec` keeps reporting the SHELL: the worker became a
            # child instead of replacing the shell. That is the regression this check exists for.
            launched = pane_id in self.exec_argv
            pid = self.exec_pid if (launched and self.honor_exec) else self.shell_pid
            body = {"result": {"process_info": {"shell_pid": pid}}}
            return _completed(cmd, 0, json.dumps(body))
        if verb == "get":
            if cmd[3] in self.panes:
                return _completed(cmd, 0, "{}")
            return _completed(cmd, 1, "", '{"error":{"code":"pane_not_found"}}')
        if verb == "list":
            panes = [{"pane_id": p, **({"label": l} if l else {})} for p, l in self.panes.items()]
            return _completed(cmd, 0, json.dumps({"result": {"panes": panes}}))
        if verb == "close":
            self.panes.pop(cmd[3], None)
            return _completed(cmd, 0, "{}")
        return _completed(cmd, 1, "", f"unknown verb {verb}")

    def _write_sentinel(self, argv):
        """Stand in for the worker: drop the observation the harness waits for. The fake writes
        the terminal record straight away (the release-file handshake is live-only behaviour,
        covered for real by the sentinel subprocess test)."""
        obs = pathlib.Path(argv[argv.index("--sentinel") + 1])
        pid = self.sentinel_pid if self.sentinel_pid is not None else self.exec_pid
        obs.parent.mkdir(parents=True, exist_ok=True)
        obs.write_text(json.dumps({
            "phase": "completed", "exit_code": 0, "pid": pid, "cmdline": list(argv),
        }), encoding="utf-8")

    def cmdline_for(self, pid):
        """/proc stand-in: the exec'd pid shows the worker argv; the shell pid shows a shell."""
        for pane_id, argv in self.exec_argv.items():
            if self.honor_exec and pid == self.exec_pid and pane_id in self.panes:
                return list(argv)
        return ["/bin/bash"]



def _patched(daemon, call):
    """A FakeDaemon whose __call__ is replaced (instance-level), so a test can corrupt ONE verb's
    response without reimplementing the whole daemon."""
    class _Patched:
        def __init__(self, inner):
            self.__dict__["_inner"] = inner
        def __call__(self, cmd, **kwargs):
            return call(cmd, **kwargs)
        def __getattr__(self, name):
            return getattr(self.__dict__["_inner"], name)
    return _Patched(daemon)

def _run_one(daemon, tmp_path, condition="cold"):
    from phase_executor.herdr_backend import HerdrBackend

    backend = HerdrBackend(run=daemon, workspace_id=daemon.workspace)
    return ac1.run_rep(
        condition,
        backend=backend,
        endpoint=daemon.workspace,
        run_herdr=daemon,
        workdir=tmp_path,
        label=f"{ac1.LABEL_PREFIX}test",
        read_cmdline=daemon.cmdline_for,
        deadline_s=1.0,
        poll_s=0.01,
    )


# --------------------------------------------------------------------------- the encoded threshold


def test_go_threshold_is_zero_failures_per_condition():
    assert ac1.GO_MAX_FAILURES == 0
    assert ac1.REPS_PER_CONDITION == 20
    assert ac1.CONDITIONS == ("cold", "reused")


def test_decide_go_only_when_everything_is_clean():
    reps = [ac1.RepResult(condition=c, label="l") for c in ("cold", "reused") for _ in range(2)]
    assert ac1.decide(reps, leaked=(), expected_reps=2) is ac1.Verdict.GO


def test_decide_identity_failure_is_no_go():
    clean = ac1.RepResult(condition="cold", label="l")
    broken = ac1.RepResult(condition="reused", label="l", identity_failures=["pid 1 != pid 2"])
    verdict = ac1.decide([clean, broken], leaked=(), expected_reps=1)
    assert verdict is ac1.Verdict.NO_GO


def test_decide_env_fault_alone_is_error_never_go():
    rep = ac1.RepResult(condition="cold", label="l", env_faults=["split failed"])
    assert ac1.decide([rep], leaked=(), expected_reps=1) is ac1.Verdict.ERROR


def test_decide_no_go_outranks_a_coincident_env_fault():
    """A CONFIRMED identity mismatch is a definitive fact; a separate environment fault in
    another rep must not downgrade it to 'could not tell'."""
    ident = ac1.RepResult(condition="cold", label="l", identity_failures=["pid mismatch"])
    env = ac1.RepResult(condition="cold", label="l", env_faults=["daemon hiccup"])
    assert ac1.decide([ident, env], leaked=(), expected_reps=2) is ac1.Verdict.NO_GO


def test_decide_short_run_is_error_not_go():
    """Fewer completed reps than the protocol demands can never read as GO."""
    reps = [ac1.RepResult(condition=c, label="l") for c in ("cold", "reused")]
    assert ac1.decide(reps, leaked=(), expected_reps=20) is ac1.Verdict.ERROR


def test_decide_leaked_label_is_error():
    reps = [ac1.RepResult(condition=c, label="l") for c in ("cold", "reused")]
    assert ac1.decide(reps, leaked=("rg639-ac1-abc",), expected_reps=1) is ac1.Verdict.ERROR


def test_decide_missing_condition_is_error():
    """A condition that produced no reps at all is an environment failure, not a pass."""
    reps = [ac1.RepResult(condition="cold", label="l")]
    assert ac1.decide(reps, leaked=(), expected_reps=1) is ac1.Verdict.ERROR


# --------------------------------------------------------------------------- the drift pin


def test_launch_argv_equals_what_new_session_issues():
    """The harness must qualify the SAME invocation the product code ships. If
    HerdrBackend.new_session ever changes its launch form, this fails in CI (where herdr is
    absent) instead of the check silently qualifying a form nothing uses any more."""
    from phase_executor.herdr_backend import HerdrBackend

    daemon = FakeDaemon()
    backend = HerdrBackend(run=daemon, workspace_id="w1")
    argv = ["python3", "-c", "pass"]
    backend.new_session("w1", "some-label", "/tmp", argv)

    run_calls = [c for c in daemon.calls if len(c) > 4 and c[2] == "run" and c[4] == "exec"]
    assert len(run_calls) == 1, daemon.calls
    pane_id = run_calls[0][3]
    assert run_calls[0] == ac1.launch_argv(pane_id, argv)


# --------------------------------------------------------------------------- driven reps


def test_clean_fake_daemon_passes_a_rep(tmp_path):
    rep = _run_one(FakeDaemon(), tmp_path)
    assert rep.identity_failures == [] and rep.env_faults == [], rep
    assert rep.pre_pid == rep.mid_pid == rep.sentinel_pid


def test_reused_condition_warms_with_a_plain_non_exec_run(tmp_path):
    daemon = FakeDaemon()
    rep = _run_one(daemon, tmp_path, condition="reused")
    assert rep.env_faults == [] and rep.identity_failures == [], rep
    plain = [c for c in daemon.calls if c[2] == "run" and c[4] != "exec"]
    assert plain, f"reused condition must issue one plain non-exec run: {daemon.calls}"


def test_regressed_daemon_that_stops_honoring_exec_yields_no_go(tmp_path):
    """The regression this whole check guards: `pane run <pane> exec ...` no longer replaces the
    shell, so the reported pid stays the shell's and the worker runs as a child."""
    daemon = FakeDaemon(honor_exec=False, shell_pid=1111, exec_pid=1111, sentinel_pid=2222)
    rep = _run_one(daemon, tmp_path)
    assert rep.identity_failures, "a broken exec must be reported, not tolerated"
    assert any("2222" in f or "1111" in f for f in rep.identity_failures), rep.identity_failures
    assert ac1.decide([rep], leaked=(), expected_reps=1) is ac1.Verdict.NO_GO


def test_unchanged_cmdline_is_an_identity_failure(tmp_path):
    """Even when the pids happen to line up, the /proc cmdline must have become the worker."""
    daemon = FakeDaemon(honor_exec=False, shell_pid=7777, exec_pid=7777, sentinel_pid=7777)
    rep = _run_one(daemon, tmp_path)
    assert any("cmdline" in f for f in rep.identity_failures), rep.identity_failures


def test_provisioning_failure_is_an_env_fault_not_a_regression(tmp_path):
    daemon = FakeDaemon(split_rc=1)
    rep = _run_one(daemon, tmp_path)
    assert rep.env_faults and not rep.identity_failures, rep
    assert ac1.decide([rep], leaked=(), expected_reps=1) is ac1.Verdict.ERROR


def test_self_timed_out_worker_is_an_env_fault_not_a_pass(tmp_path):
    """A worker that hit its own self-timeout was never released, so the "mid-run" read is not
    provably mid-run — the rep's guarantee is weakened and must not count as a clean pass."""
    daemon = FakeDaemon()
    original = daemon._write_sentinel

    def timed_out(argv):
        original(argv)
        obs = pathlib.Path(argv[argv.index("--sentinel") + 1])
        payload = json.loads(obs.read_text(encoding="utf-8"))
        payload["timed_out"] = True
        obs.write_text(json.dumps(payload), encoding="utf-8")

    daemon._write_sentinel = timed_out
    rep = _run_one(daemon, tmp_path)
    assert rep.env_faults and not rep.identity_failures, rep
    assert any("SENTINEL_SELF_TIMEOUT_S" in f for f in rep.env_faults), rep.env_faults
    assert ac1.decide([rep], leaked=(), expected_reps=1) is ac1.Verdict.ERROR


def test_unreadable_proc_cmdline_is_an_env_fault_not_a_regression(tmp_path):
    """`/proc` unreadable means we could not observe the transition. The pid-identity chain is
    the definitive signal; an unobserved cmdline must not be reported as a regression."""
    daemon = FakeDaemon()
    rep = ac1.run_rep(
        "cold",
        backend=__import__("phase_executor.herdr_backend", fromlist=["HerdrBackend"]).HerdrBackend(
            run=daemon, workspace_id=daemon.workspace),
        endpoint=daemon.workspace, run_herdr=daemon, workdir=tmp_path,
        label=f"{ac1.LABEL_PREFIX}unreadable",
        read_cmdline=lambda _pid: None, deadline_s=1.0, poll_s=0.01,
    )
    assert rep.env_faults and not rep.identity_failures, rep
    assert any("cmdline" in f for f in rep.env_faults), rep.env_faults


def test_missing_sentinel_is_an_env_fault(tmp_path):
    daemon = FakeDaemon(write_sentinel=False)
    rep = _run_one(daemon, tmp_path)
    assert rep.env_faults and not rep.identity_failures, rep


def test_qualify_runs_every_condition_and_reports_go(tmp_path):
    from phase_executor.herdr_backend import HerdrBackend

    daemon = FakeDaemon()
    backend = HerdrBackend(run=daemon, workspace_id="w1")
    report = ac1.qualify(
        backend=backend, endpoint="w1", run_herdr=daemon, workdir=tmp_path,
        reps=2, read_cmdline=daemon.cmdline_for, deadline_s=1.0, poll_s=0.01,
    )
    assert report.verdict is ac1.Verdict.GO, report.summary()
    assert report.counts()["cold"]["reps"] == 2 and report.counts()["reused"]["reps"] == 2
    assert report.leaked == ()


# --------------------------------------------------------------------------- the skip predicate


def test_availability_requires_the_binary():
    ok, reason = ac1.herdr_available(which=lambda _n: None, run=lambda *a, **k: None,
                                     env={})
    assert not ok and "not on PATH" in reason


def test_availability_requires_the_version_floor():
    ok, reason = ac1.herdr_available(
        which=lambda _n: "/usr/bin/herdr",
        run=lambda cmd, **k: _completed(cmd, 0, "herdr 0.7.4"),
        env={"HERDR_WORKSPACE_ID": "w1"})
    assert not ok and "0.7.4" in reason and "floor" in reason


def test_availability_requires_a_resolvable_calling_pane():
    """herdr installed but the process has no pane (cron/headless): `split --current` cannot
    resolve, so the check must SKIP rather than fail (AC2)."""
    def run(cmd, **_k):
        if "--version" in cmd:
            return _completed(cmd, 0, "herdr 0.7.5")
        return _completed(cmd, 1, "", '{"error":{"code":"no_current_pane"}}')

    ok, reason = ac1.herdr_available(which=lambda _n: "/usr/bin/herdr", run=run, env={})
    assert not ok and "pane" in reason


def test_availability_resolves_workspace_from_pane_current():
    body = {"result": {"pane": {"pane_id": "w1:p1", "workspace_id": "w9"}}}

    def run(cmd, **_k):
        if "--version" in cmd:
            return _completed(cmd, 0, "herdr 0.7.5")
        return _completed(cmd, 0, json.dumps(body))

    ok, workspace = ac1.herdr_available(which=lambda _n: "/usr/bin/herdr", run=run, env={})
    assert ok and workspace == "w9"


# --------------------------------------------------------------------------- the gate entry point


def test_gate_exit_codes_reserve_zero_for_go():
    """`pytest` exits 0 when every test SKIPS, so the pytest form cannot BE the pre-upgrade gate:
    an operator reading only the exit status would accept an upgrade the gate never evaluated.
    This entry point exits 0 for GO and nothing else."""
    assert ac1.gate_exit_code(ac1.Verdict.GO) == 0
    assert ac1.gate_exit_code(ac1.Verdict.NO_GO) != 0
    assert ac1.gate_exit_code(ac1.Verdict.ERROR) != 0
    assert len({ac1.gate_exit_code(v) for v in ac1.Verdict}) == 3, "each verdict is distinguishable"
    assert ac1.GATE_EXIT_UNAVAILABLE != 0


def test_gate_reports_unavailable_rather_than_success_without_herdr(tmp_path):
    """Run for real as a subprocess with herdr off PATH: the gate must NOT exit 0."""
    env = {**os.environ, "PATH": "/usr/bin:/bin", "PYTHONPATH": str(
        pathlib.Path(__file__).resolve().parents[2] / "phase_executor" / "src")}
    proc = subprocess.run([sys.executable, str(HARNESS_PATH), "--gate"],
                          capture_output=True, text=True, env=env, timeout=60, check=False)
    assert proc.returncode == ac1.GATE_EXIT_UNAVAILABLE, (proc.returncode, proc.stderr)
    assert "not a pass" in proc.stderr, proc.stderr


def test_gate_rejects_a_malformed_invocation_without_claiming_success():
    proc = subprocess.run([sys.executable, str(HARNESS_PATH)], capture_output=True, text=True,
                          timeout=60, check=False)
    assert proc.returncode not in (0, ac1.GATE_EXIT_GO), proc.stderr
    assert "usage:" in proc.stderr


# --------------------------------------------------------------------------- vacuous-check guards


@pytest.mark.parametrize("stdout", ["", "   ", "unknown", "0", "1", "-5"])
def test_unusable_pane_pid_is_an_env_fault_never_a_vacuous_pass(tmp_path, stdout):
    """`pane_pid` returns rc=0 carrying whatever herdr reported. If that is not a usable pid, every
    identity comparison would silently evaporate and the rep would count toward GO having observed
    nothing at all."""
    daemon = FakeDaemon()
    real_call = daemon.__call__

    def broken(cmd, **kwargs):
        res = real_call(cmd, **kwargs)
        if len(cmd) > 2 and cmd[2] == "process-info":
            return _completed(cmd, 0, json.dumps({"result": {"process_info": {"shell_pid": stdout}}}))
        return res

    rep = _run_one(_patched(daemon, broken), tmp_path)
    assert rep.env_faults, f"unusable pid {stdout!r} must be an env fault: {rep}"
    assert not rep.identity_failures, rep
    assert ac1.decide([rep], leaked=(), expected_reps=1) is ac1.Verdict.ERROR


def test_identity_comparisons_are_unconditional_once_pids_are_valid(tmp_path):
    """The pid chain must be compared, not skipped: a mid-run pid differing from the pre-exec pid
    is the primary regression signal."""
    daemon = FakeDaemon(shell_pid=5555, exec_pid=6666, sentinel_pid=6666)
    rep = _run_one(daemon, tmp_path)
    assert any("5555" in f and "6666" in f for f in rep.identity_failures), rep.identity_failures


def test_availability_treats_a_wedged_daemon_as_unavailable():
    """A raising/hanging probe must resolve to UNAVAILABLE, not propagate out of collection."""
    def raising(_cmd, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="herdr --version", timeout=5)

    ok, reason = ac1.herdr_available(which=lambda _n: "/usr/bin/herdr", run=raising, env={})
    assert not ok and "raised" in reason


# --------------------------------------------------------------------------- the sentinel itself


def test_sentinel_subprocess_writes_started_then_completed(tmp_path):
    """The worker's real on-disk contract, with no herdr in sight: it announces itself, waits for
    the release file, then records a clean completion carrying its OWN pid and cmdline."""
    obs = tmp_path / "observation.json"
    release = tmp_path / "release"
    proc = subprocess.Popen(
        [sys.executable, str(HARNESS_PATH), "--sentinel", str(obs), str(release), "10"])
    try:
        deadline_reached = ac1._wait_for_file(obs, lambda d: d.get("phase") == "started",
                                             deadline_s=10.0, poll_s=0.02)
        assert deadline_reached, "sentinel never announced itself"
        started = json.loads(obs.read_text(encoding="utf-8"))
        assert started["pid"] == proc.pid
        assert str(HARNESS_PATH) in started["cmdline"]

        release.touch()
        assert ac1._wait_for_file(obs, lambda d: d.get("phase") == "completed",
                                 deadline_s=10.0, poll_s=0.02)
        done = json.loads(obs.read_text(encoding="utf-8"))
        assert done["exit_code"] == 0 and done["pid"] == proc.pid
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_sentinel_self_times_out_without_a_release(tmp_path):
    """A harness that dies before touching the release file must not leave the worker (and its
    pane) alive indefinitely."""
    obs = tmp_path / "observation.json"
    proc = subprocess.Popen(
        [sys.executable, str(HARNESS_PATH), "--sentinel", str(obs), str(tmp_path / "never"), "1"])
    assert proc.wait(timeout=20) == 0
    payload = json.loads(obs.read_text(encoding="utf-8"))
    assert payload["phase"] == "completed" and payload["timed_out"] is True


def test_sentinel_timeout_default_is_bounded():
    assert 0 < ac1.SENTINEL_SELF_TIMEOUT_S <= 60


@pytest.mark.parametrize("condition", ["cold", "reused"])
def test_every_declared_condition_is_runnable(tmp_path, condition):
    rep = _run_one(FakeDaemon(), tmp_path, condition=condition)
    assert rep.condition == condition and rep.ok, rep
