"""#639 (epic #635 C3) — the AC1 PID-identity regression check. THE PRE-UPGRADE GATE.

Run this before trusting #638's `HerdrBackend` on a new herdr version:

    RUN_LIVE=1 pytest tests/phase_executor/live/test_herdr_pid_identity_live.py -v

It re-qualifies #633 §AC1 against the REAL host daemon: 20 cold-pane + 20 reused-pane reps of the
exec-injection launch, asserting the PID-identity chain (pre-exec shell pid == mid-run pid == the
pid the worker itself recorded), the `/proc` cmdline transition, and a clean on-disk completion —
plus one rep through the production entry point `HerdrBackend.new_session()`.

Gating (#639 AC2): module-marked `live`, so the shared conftest skips it unless `RUN_LIVE=1`, AND
skipped with a NAMED reason when herdr is missing, below the qualified version floor, or when this
process has no resolvable calling pane (`pane split --current` cannot resolve from a pane-less
process — cron/headless). It SKIPS VISIBLY in those cases; it never fails and never silently
passes. herdr is absent from CI entirely, so in CI this module always skips.

The GO threshold (0 failures per condition) lives in `herdr_ac1_protocol.decide` and is asserted
here as a tri-state verdict — `ERROR` (environment) is never accepted as a pass, and is never
reported as a regression either.
"""
import os
import pathlib
import sys
import uuid

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import herdr_ac1_protocol as ac1  # noqa: E402 — same-dir harness, loaded by explicit path

from phase_executor.herdr_backend import HerdrBackend  # noqa: E402

pytestmark = pytest.mark.live

# Evaluated once at collection, and ONLY under RUN_LIVE=1 (cross-model review finding): probing
# eagerly meant an ordinary suite run on any herdr-installed host spawned two herdr subprocesses at
# COLLECTION time, where a wedged daemon could stall or fail collection for opt-in tests that were
# never going to run. With RUN_LIVE unset the shared conftest skips this module anyway.
_HERDR_OK, _HERDR_DETAIL = (
    ac1.herdr_available() if os.environ.get("RUN_LIVE") == "1"
    else (False, "RUN_LIVE is not set (this check is opt-in)"))
requires_herdr = pytest.mark.skipif(
    not _HERDR_OK, reason=f"herdr unavailable for the AC1 live check: {_HERDR_DETAIL}")
WORKSPACE = _HERDR_DETAIL if _HERDR_OK else None


@requires_herdr
def test_ac1_pid_identity_qualification(tmp_path):
    """#633 §AC1 in full: both conditions, 20 reps each, GO threshold 0 failures per condition."""
    backend = HerdrBackend(workspace_id=WORKSPACE)
    report = ac1.qualify(
        backend=backend,
        endpoint=WORKSPACE,
        run_herdr=ac1.default_runner(),
        workdir=tmp_path,
    )
    detail = report.summary() + ("\n  " + "\n  ".join(report.failures()) if report.failures() else "")
    # ERROR is not a pass: an environment fault means the question was not answered.
    assert report.verdict is ac1.Verdict.GO, detail
    for condition in ac1.CONDITIONS:
        counts = report.counts()[condition]
        assert counts["reps"] == ac1.REPS_PER_CONDITION, detail
        assert counts["identity_failures"] == ac1.GO_MAX_FAILURES, detail
    assert report.leaked == (), f"panes left behind: {report.leaked}"


@requires_herdr
def test_production_path_new_session_preserves_identity(tmp_path):
    """The same claim through the PRODUCTION entry point. `new_session` performs
    split -> rename -> `run <pane> exec ...` as one call, so this rep cannot observe a pre-exec
    pid (that is a limitation of the entry point, not of the protocol) — everything downstream of
    the exec is asserted exactly as in the reps above."""
    from phase_executor.terminal_backend import Liveness

    backend = HerdrBackend(workspace_id=WORKSPACE)
    # uuid-suffixed like every qualify() rep: a FIXED label collides with any pane a crashed
    # earlier run left behind, and `_resolve_pane_id` raises on a duplicate label — which would
    # surface as a confusing hard failure instead of a clean run.
    label = f"{ac1.LABEL_PREFIX}newsession-{uuid.uuid4().hex[:6]}"
    workdir = tmp_path / label
    workdir.mkdir(parents=True, exist_ok=True)
    observation = workdir / "observation.json"
    release = workdir / "release"
    worker_argv = ac1.sentinel_argv(observation, release)

    launched = backend.new_session(WORKSPACE, label, str(workdir), worker_argv)
    assert launched.returncode == 0, launched.stderr
    try:
        assert ac1._wait_for_file(observation, lambda d: d.get("phase") in ("started", "completed"),
                                  deadline_s=ac1.DEFAULT_DEADLINE_S, poll_s=ac1.DEFAULT_POLL_S), \
            "worker never announced itself"

        reported = backend.pane_pid(WORKSPACE, label)
        assert reported.returncode == 0, reported.stderr
        pid = int(reported.stdout.strip())
        assert ac1._read_cmdline(pid) == worker_argv, ac1._read_cmdline(pid)

        release.touch()
        assert ac1._wait_for_file(observation, lambda d: d.get("phase") == "completed",
                                  deadline_s=ac1.DEFAULT_DEADLINE_S, poll_s=ac1.DEFAULT_POLL_S), \
            "worker never recorded a completion"
        payload = ac1._read_json(observation)
        assert payload["exit_code"] == 0 and payload["pid"] == pid, payload
    finally:
        if backend.probe_session(WORKSPACE, label) is not Liveness.CONFIRMED_GONE:
            backend.close_session(WORKSPACE, label)
    assert backend.probe_session(WORKSPACE, label) is Liveness.CONFIRMED_GONE
    assert label not in ac1.sweep_labels(backend, WORKSPACE)


@requires_herdr
def test_preflight_passes_against_the_real_daemon():
    """#638's own `preflight` probes every verb this backend needs (split/rename/run/get/
    process-info/list/close). Re-running it live here means a herdr upgrade that breaks any ONE
    of those verbs is reported by this same pre-upgrade gate, not only the exec transition."""
    backend = HerdrBackend(workspace_id=WORKSPACE)
    result = backend.preflight(WORKSPACE)
    assert result.supported, result.reason
    assert ac1.sweep_labels(backend, WORKSPACE) == ()
