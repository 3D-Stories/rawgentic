"""Tests for hooks/post_update_reconcile.py — the SessionStart post-update reconcile.

Hole 1: when the plugin gains a feature, existing projects shouldn't stay on the
old defaults until the user notices. On a plugin VERSION CHANGE this hook:
  - turns ON any new opt-OUT ("auto-on") feature whose flag is absent, while
    HONORING an explicit opt-out already on record,
  - leaves opt-in features (headless) untouched,
  - does NOT silently enable answer-required features (WF5/adversarialReview);
    instead it nudges the user to run /rawgentic:setup,
  - records the reconciled version so it runs ONCE per version (record-once).

reconcile_projects() is pure and unit-tested with an injected manifest; main() is
integration-tested via subprocess with a manifest override + a fake version.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import post_update_reconcile as pur  # noqa: E402


AUTO_ON = {"key": "fancyFeature", "policy": "auto-on", "default_value": True}
OPT_IN = {"key": "headlessEnabled", "policy": "opt-in"}
NEEDS_Q = {"key": "adversarialReview", "policy": "needs-question", "nudge": "adversarial review (WF5)"}
MANIFEST = [AUTO_ON, OPT_IN, NEEDS_Q]


def _proj(name, active=True, **extra):
    p = {"name": name, "path": f"./projects/{name}", "active": active}
    p.update(extra)
    return p


class TestReconcileProjects:
    def test_auto_on_absent_is_enabled(self):
        projects = [_proj("p")]
        out, changes, needs_q = pur.reconcile_projects(projects, MANIFEST)
        assert out[0]["fancyFeature"] is True
        assert ("p", "fancyFeature", True) in changes

    def test_auto_on_explicit_optout_is_respected(self):
        # An explicit false is a recorded opt-out — must be left alone.
        projects = [_proj("p", fancyFeature=False)]
        out, changes, needs_q = pur.reconcile_projects(projects, MANIFEST)
        assert out[0]["fancyFeature"] is False
        assert changes == []

    def test_auto_on_already_true_is_no_change(self):
        projects = [_proj("p", fancyFeature=True)]
        _, changes, _ = pur.reconcile_projects(projects, MANIFEST)
        assert changes == []

    def test_opt_in_feature_never_touched(self):
        projects = [_proj("p")]
        out, changes, _ = pur.reconcile_projects(projects, MANIFEST)
        assert "headlessEnabled" not in out[0]
        assert all(c[1] != "headlessEnabled" for c in changes)

    def test_needs_question_absent_on_active_is_nudged(self):
        projects = [_proj("p", active=True)]
        out, changes, needs_q = pur.reconcile_projects(projects, MANIFEST)
        # not silently enabled...
        assert "adversarialReview" not in out[0]
        # ...but flagged for a setup nudge
        assert any(n[0] == "p" and n[1] == "adversarialReview" for n in needs_q)

    def test_needs_question_absent_on_inactive_is_not_nudged(self):
        projects = [_proj("p", active=False)]
        _, _, needs_q = pur.reconcile_projects(projects, MANIFEST)
        assert needs_q == []

    def test_needs_question_present_is_not_nudged(self):
        projects = [_proj("p", adversarialReview={"enabled": True, "workflows": []})]
        _, _, needs_q = pur.reconcile_projects(projects, MANIFEST)
        assert needs_q == []


class TestProductionManifest:
    def test_headless_is_opt_in_and_wf5_is_needs_question(self):
        by_key = {f["key"]: f for f in pur.FEATURE_MANIFEST}
        assert by_key["headlessEnabled"]["policy"] == "opt-in"
        assert by_key["adversarialReview"]["policy"] == "needs-question"

    def test_no_feature_is_both_silently_forced_and_answer_required(self):
        for f in pur.FEATURE_MANIFEST:
            assert f["policy"] in ("auto-on", "opt-in", "needs-question")


# --------------------------------------------------------------------------
# main() integration
# --------------------------------------------------------------------------

SCRIPT = HOOKS_DIR / "post_update_reconcile.py"


def _write_ws(tmp_path, projects):
    ws = tmp_path / ".rawgentic_workspace.json"
    ws.write_text(json.dumps({"version": 1, "projects": projects}, indent=2))
    return ws


def _write_manifest(tmp_path, manifest):
    mf = tmp_path / "manifest.json"
    mf.write_text(json.dumps(manifest))
    return mf


def _run(tmp_path, ws, *, version, state_dir=None, manifest=None, extra_env=None):
    state_dir = state_dir or (tmp_path / "state")
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    if manifest is not None:
        env["RAWGENTIC_RECONCILE_MANIFEST"] = str(manifest)
    if extra_env:
        env.update(extra_env)
    cmd = ["python3", str(SCRIPT), "--workspace", str(ws),
           "--state-dir", str(state_dir), "--version", version]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
    return r, Path(state_dir)


class TestMainIntegration:
    def test_version_change_records_and_nudges_for_wf5(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj("p", active=True)])
        r, state_dir = _run(tmp_path, ws, version="2.43.0")
        assert r.returncode == 0, r.stderr
        # WF5 unset on an active project -> nudge to run setup
        assert "setup" in r.stdout.lower()
        assert "2.43.0" in r.stdout
        # version recorded -> record-once
        assert (state_dir / "rawgentic-reconciled-version").read_text().strip() == "2.43.0"

    def test_same_version_is_silent_and_untouched(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj("p", active=True)])
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        (state_dir / "rawgentic-reconciled-version").write_text("2.43.0\n")
        before = ws.read_text()
        r, _ = _run(tmp_path, ws, version="2.43.0", state_dir=state_dir)
        assert r.returncode == 0
        assert r.stdout.strip() == "", "no version change -> no output"
        assert ws.read_text() == before, "workspace must be untouched"

    def test_record_once_second_run_silent(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj("p", active=True)])
        r1, state_dir = _run(tmp_path, ws, version="2.43.0")
        assert "setup" in r1.stdout.lower()
        r2, _ = _run(tmp_path, ws, version="2.43.0", state_dir=state_dir)
        assert r2.stdout.strip() == "", "must not re-nag for the same version"

    def test_wf5_already_configured_no_nudge(self, tmp_path):
        ws = _write_ws(tmp_path, [
            _proj("p", active=True, adversarialReview={"enabled": False, "workflows": []})])
        r, _ = _run(tmp_path, ws, version="2.43.0")
        assert r.returncode == 0
        assert "adversarial" not in r.stdout.lower(), "configured WF5 must not be nudged"

    def test_auto_on_feature_is_written_back(self, tmp_path):
        manifest = _write_manifest(tmp_path, [AUTO_ON])
        ws = _write_ws(tmp_path, [_proj("p", active=True)])
        r, _ = _run(tmp_path, ws, version="2.43.0", manifest=manifest)
        assert r.returncode == 0
        data = json.loads(ws.read_text())
        assert data["projects"][0]["fancyFeature"] is True
        assert "fancyFeature" in r.stdout or "enabled" in r.stdout.lower()

    def test_auto_on_optout_preserved_across_reconcile(self, tmp_path):
        manifest = _write_manifest(tmp_path, [AUTO_ON])
        ws = _write_ws(tmp_path, [_proj("p", active=True, fancyFeature=False)])
        r, _ = _run(tmp_path, ws, version="2.43.0", manifest=manifest)
        data = json.loads(ws.read_text())
        assert data["projects"][0]["fancyFeature"] is False, "recorded opt-out must survive"

    def test_version_not_recorded_when_workspace_write_fails(self, tmp_path):
        """If an auto-on change can't be persisted to the workspace, the version
        marker must NOT be recorded — otherwise next session short-circuits
        (last == current) and the change is lost forever with no retry."""
        if os.geteuid() == 0:
            pytest.skip("root bypasses directory write permissions")
        manifest = _write_manifest(tmp_path, [AUTO_ON])
        wsdir = tmp_path / "wsdir"
        wsdir.mkdir()
        ws = wsdir / ".rawgentic_workspace.json"
        ws.write_text(json.dumps({"version": 1, "projects": [_proj("p", active=True)]}))
        state_dir = tmp_path / "state"
        before = ws.read_text()
        wsdir.chmod(0o555)  # readable, NOT writable -> atomic write (mkstemp) fails
        try:
            r, sd = _run(tmp_path, ws, version="2.43.0", state_dir=state_dir,
                         manifest=manifest)
        finally:
            wsdir.chmod(0o755)
        assert r.returncode == 0
        # version must NOT be recorded (so the reconcile retries next session)
        assert not (sd / "rawgentic-reconciled-version").exists()
        # workspace must be unchanged (the write failed, nothing half-written)
        assert ws.read_text() == before

    def test_missing_workspace_is_silent_no_crash(self, tmp_path):
        r, _ = _run(tmp_path, tmp_path / "nope.json", version="2.43.0")
        assert r.returncode == 0
        assert r.stdout.strip() == ""

    def test_corrupt_workspace_is_silent_no_crash(self, tmp_path):
        ws = tmp_path / ".rawgentic_workspace.json"
        ws.write_text("{ not json")
        r, _ = _run(tmp_path, ws, version="2.43.0")
        assert r.returncode == 0


class TestSessionStartWiring:
    """session-start must invoke the reconcile on startup|resume."""

    def _text(self):
        return (HOOKS_DIR / "session-start").read_text()

    def test_session_start_invokes_reconcile(self):
        t = self._text()
        assert "post_update_reconcile.py" in t
        assert "_do_post_update_reconcile" in t

    def test_reconcile_runs_on_startup_and_resume(self):
        t = self._text()
        # the guard is inside _do_post_update_reconcile
        seg = t[t.index("_do_post_update_reconcile()"):]
        assert "startup|resume" in seg[:400]
