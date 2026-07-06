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

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS_DIR = REPO_ROOT / "hooks"
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


def _run(tmp_path, ws, *, version, state_dir=None, manifest=None, extra_env=None,
         extra_args=None):
    state_dir = state_dir or (tmp_path / "state")
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    if manifest is not None:
        env["RAWGENTIC_RECONCILE_MANIFEST"] = str(manifest)
    if extra_env:
        env.update(extra_env)
    cmd = ["python3", str(SCRIPT), "--workspace", str(ws),
           "--state-dir", str(state_dir), "--version", version]
    if extra_args:
        cmd += list(extra_args)
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


# --------------------------------------------------------------------------
# #184 — version-aware gating, opt-out, prompt wording, drift guard
# --------------------------------------------------------------------------

OLD_FEAT = {"key": "featOld", "policy": "needs-question", "nudge": "old feature", "since": "2.10.0"}
NEW_FEAT = {"key": "featNew", "policy": "needs-question", "nudge": "new feature", "since": "2.60.0"}
NO_SINCE = {"key": "featAny", "policy": "needs-question", "nudge": "any feature"}


def _seed_state(tmp_path, last):
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    (state_dir / "rawgentic-reconciled-version").write_text(last + "\n")
    return state_dir


class TestVerTuple:
    def test_basic(self):
        assert pur._ver_tuple("2.66.0") == (2, 66, 0)

    def test_numeric_not_string_compare(self):
        # The classic semver-as-string bug: "2.9.0" > "2.10.0" lexically.
        assert pur._ver_tuple("2.9.0") < pur._ver_tuple("2.10.0")

    def test_unparseable_returns_none(self):
        assert pur._ver_tuple("garbage") is None
        assert pur._ver_tuple("") is None
        assert pur._ver_tuple(None) is None


class TestSinceGating:
    """AC2: prompt only when the version jump crosses a feature's `since`;
    otherwise bump the marker silently."""

    def test_no_crossed_feature_is_silent_bump(self, tmp_path):
        manifest = _write_manifest(tmp_path, [OLD_FEAT, NEW_FEAT])
        ws = _write_ws(tmp_path, [_proj("p", active=True)])
        state_dir = _seed_state(tmp_path, "2.63.0")
        r, sd = _run(tmp_path, ws, version="2.66.0", state_dir=state_dir, manifest=manifest)
        assert r.returncode == 0
        assert r.stdout.strip() == "", "no newly-shipped feature -> no prompt"
        assert (sd / "rawgentic-reconciled-version").read_text().strip() == "2.66.0", \
            "marker must still be bumped silently"

    def test_crossed_feature_nudges_only_the_new_one(self, tmp_path):
        manifest = _write_manifest(tmp_path, [OLD_FEAT, NEW_FEAT])
        ws = _write_ws(tmp_path, [_proj("p", active=True)])
        state_dir = _seed_state(tmp_path, "2.45.0")
        r, _ = _run(tmp_path, ws, version="2.66.0", state_dir=state_dir, manifest=manifest)
        assert "new feature" in r.stdout, "since=2.60.0 crossed by 2.45.0->2.66.0"
        assert "old feature" not in r.stdout, "since=2.10.0 predates the jump — no re-nag"

    def test_missing_marker_treated_as_version_zero(self, tmp_path):
        # Fresh install: nudge features that exist at the current version,
        # never ones introduced later.
        manifest = _write_manifest(tmp_path, [OLD_FEAT, NEW_FEAT])
        ws = _write_ws(tmp_path, [_proj("p", active=True)])
        r, _ = _run(tmp_path, ws, version="2.30.0", manifest=manifest)
        assert "old feature" in r.stdout
        assert "new feature" not in r.stdout, "since=2.60.0 > current=2.30.0 — not shipped yet"

    def test_since_less_entry_is_always_eligible(self, tmp_path):
        manifest = _write_manifest(tmp_path, [NO_SINCE])
        ws = _write_ws(tmp_path, [_proj("p", active=True)])
        state_dir = _seed_state(tmp_path, "2.63.0")
        r, _ = _run(tmp_path, ws, version="2.66.0", state_dir=state_dir, manifest=manifest)
        assert "any feature" in r.stdout, "no `since` -> legacy always-eligible"

    def test_unparseable_since_fails_open_to_eligible(self, tmp_path):
        bad = dict(NEW_FEAT, since="garbage")
        manifest = _write_manifest(tmp_path, [bad])
        ws = _write_ws(tmp_path, [_proj("p", active=True)])
        state_dir = _seed_state(tmp_path, "2.63.0")
        r, _ = _run(tmp_path, ws, version="2.66.0", state_dir=state_dir, manifest=manifest)
        assert "new feature" in r.stdout, "unparseable version must never permanently silence"

    def test_production_2_63_to_2_66_is_silent(self, tmp_path):
        # The real-world nag this issue fixes: 2.63.0 -> 2.66.0 shipped no new
        # setup-requiring feature, so an unconfigured project must NOT be nagged.
        ws = _write_ws(tmp_path, [_proj("p", active=True)])
        state_dir = _seed_state(tmp_path, "2.63.0")
        r, sd = _run(tmp_path, ws, version="2.66.0", state_dir=state_dir)
        assert r.returncode == 0
        assert r.stdout.strip() == ""
        assert (sd / "rawgentic-reconciled-version").read_text().strip() == "2.66.0"


class TestOptOut:
    """AC5: workspace-level `setupPrompt: false` silences the prompt; the
    marker is still bumped silently."""

    def _write_ws_optout(self, tmp_path, value):
        ws = tmp_path / ".rawgentic_workspace.json"
        ws.write_text(json.dumps({"version": 1, "setupPrompt": value,
                                  "projects": [_proj("p", active=True)]}, indent=2))
        return ws

    def test_optout_suppresses_output_but_bumps_marker(self, tmp_path):
        manifest = _write_manifest(tmp_path, [NEW_FEAT])
        ws = self._write_ws_optout(tmp_path, False)
        state_dir = _seed_state(tmp_path, "2.45.0")
        r, sd = _run(tmp_path, ws, version="2.66.0", state_dir=state_dir, manifest=manifest)
        assert r.returncode == 0
        assert r.stdout.strip() == "", "opted out -> no prompt"
        assert (sd / "rawgentic-reconciled-version").read_text().strip() == "2.66.0"

    def test_optout_true_still_prompts(self, tmp_path):
        manifest = _write_manifest(tmp_path, [NEW_FEAT])
        ws = self._write_ws_optout(tmp_path, True)
        state_dir = _seed_state(tmp_path, "2.45.0")
        r, _ = _run(tmp_path, ws, version="2.66.0", state_dir=state_dir, manifest=manifest)
        assert "new feature" in r.stdout


class TestPromptWording:
    """AC3/AC4: the prompt names the feature(s), offers setup, and carries the
    decline instructions (run later, preserves config, no re-nag, opt-out)."""

    def test_message_carries_decline_instructions(self, tmp_path):
        manifest = _write_manifest(tmp_path, [NEW_FEAT])
        ws = _write_ws(tmp_path, [_proj("p", active=True)])
        state_dir = _seed_state(tmp_path, "2.45.0")
        r, _ = _run(tmp_path, ws, version="2.66.0", state_dir=state_dir, manifest=manifest)
        out = r.stdout
        assert "new feature" in out                      # names the feature
        assert "/rawgentic:setup" in out                 # offers setup
        assert "preserv" in out.lower()                  # setup preserves existing config
        assert "2.66.0" in out                           # names the version
        assert "setupPrompt" in out                      # names the opt-out
        assert "won't repeat" in out                     # the no-re-nag guarantee (AC4)
        assert "any time later" in out                   # run-it-later instruction (AC4)


class TestManifestDriftGuard:
    """AC6: the manifest and setup's staged workspace fields must not drift."""

    SETUP_SKILL = HOOKS_DIR.parent / "skills" / "setup" / "SKILL.md"

    def _staged_fields(self):
        import re
        text = self.SETUP_SKILL.read_text()
        anchor = "Apply any pending per-project field changes"
        idx = text.index(anchor)  # missing anchor -> loud ValueError
        sentence = text[idx:idx + 600]
        fields = set(re.findall(r"`(\w+)` \(Step 2[a-z]\)", sentence))
        assert fields, "write-back sentence found but no staged fields extracted"
        return fields

    def test_manifest_keys_match_setup_staged_fields(self):
        manifest_keys = {f["key"] for f in pur.FEATURE_MANIFEST}
        assert manifest_keys == self._staged_fields(), (
            "hooks/post_update_reconcile.py FEATURE_MANIFEST must list exactly the "
            "workspace fields setup stages (SKILL.md write-back sentence). A new "
            "setup opt-in step must add a manifest entry with its `since` version.")

    def test_production_entries_have_valid_since(self):
        installed = json.loads(
            (HOOKS_DIR.parent / ".claude-plugin" / "plugin.json").read_text())["version"]
        cur = pur._ver_tuple(installed)
        for f in pur.FEATURE_MANIFEST:
            s = pur._ver_tuple(f.get("since"))
            assert s is not None, f"{f['key']}: production entry needs a parseable `since`"
            assert s <= cur, f"{f['key']}: since {f['since']} is newer than installed {installed}"


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


# --------------------------------------------------------------------------
# #234: per-project config-staleness nudge (independent of the reconcile marker)
# --------------------------------------------------------------------------

class TestProjectFeatureGaps:
    """Pure: needs-question features a project has NOT configured that EXIST at the
    current version — surfaces a stale project even after the plugin already updated."""

    def test_absent_needs_question_is_a_gap(self):
        gaps = pur.project_feature_gaps(_proj("p"), MANIFEST, "9.9.9")
        assert ("adversarialReview", "adversarial review (WF5)") in gaps

    def test_configured_feature_is_not_a_gap(self):
        gaps = pur.project_feature_gaps(_proj("p", adversarialReview=True),
                                        MANIFEST, "9.9.9")
        assert gaps == []

    def test_auto_on_and_opt_in_are_not_gaps(self):
        # only needs-question (answer-required) features count as a setup gap
        gaps = pur.project_feature_gaps(_proj("p"), MANIFEST, "9.9.9")
        keys = {k for k, _ in gaps}
        assert "fancyFeature" not in keys and "headlessEnabled" not in keys

    def test_feature_newer_than_current_is_not_yet_a_gap(self):
        future = [{"key": "adversarialReview", "policy": "needs-question",
                   "nudge": "WF5", "since": "99.0.0"}]
        assert pur.project_feature_gaps(_proj("p"), future, "3.14.0") == []


class TestStalenessCLI:
    def test_staleness_project_nudges_when_behind(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj("p", active=True)])
        r, _ = _run(tmp_path, ws, version="9.9.9", manifest=_write_manifest(tmp_path, MANIFEST),
                    extra_args=["--staleness-project", "p"])
        assert r.returncode == 0, r.stderr
        assert "setup" in r.stdout.lower()
        assert "p" in r.stdout

    def test_staleness_project_silent_when_configured(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj("p", active=True, adversarialReview=True)])
        r, _ = _run(tmp_path, ws, version="9.9.9", manifest=_write_manifest(tmp_path, MANIFEST),
                    extra_args=["--staleness-project", "p"])
        assert r.stdout.strip() == ""

    def test_staleness_project_always_shows_no_marker_gate(self, tmp_path):
        # /switch is an explicit action — the gap surfaces every switch (no once-gate)
        ws = _write_ws(tmp_path, [_proj("p", active=True)])
        mf = _write_manifest(tmp_path, MANIFEST)
        r1, _ = _run(tmp_path, ws, version="9.9.9", manifest=mf, extra_args=["--staleness-project", "p"])
        r2, _ = _run(tmp_path, ws, version="9.9.9", manifest=mf, extra_args=["--staleness-project", "p"])
        assert "setup" in r1.stdout.lower() and "setup" in r2.stdout.lower()

    def test_staleness_active_nudges_once_per_version(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj("p", active=True)])
        mf = _write_manifest(tmp_path, MANIFEST)
        r1, sd = _run(tmp_path, ws, version="9.9.9", manifest=mf, extra_args=["--staleness-active"])
        assert "setup" in r1.stdout.lower()
        r2, _ = _run(tmp_path, ws, version="9.9.9", state_dir=sd, manifest=mf,
                     extra_args=["--staleness-active"])
        assert r2.stdout.strip() == "", "must not re-nag the same project for the same version"

    def test_staleness_active_skips_inactive(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj("p", active=False)])
        r, _ = _run(tmp_path, ws, version="9.9.9", manifest=_write_manifest(tmp_path, MANIFEST),
                    extra_args=["--staleness-active"])
        assert r.stdout.strip() == ""

    def test_staleness_respects_setupprompt_optout(self, tmp_path):
        ws = tmp_path / ".rawgentic_workspace.json"
        ws.write_text(json.dumps({"version": 1, "setupPrompt": False,
                                  "projects": [_proj("p", active=True)]}))
        r, _ = _run(tmp_path, ws, version="9.9.9", manifest=_write_manifest(tmp_path, MANIFEST),
                    extra_args=["--staleness-project", "p"])
        assert r.stdout.strip() == ""

    def test_default_invocation_unchanged_no_staleness(self, tmp_path):
        # regression: without a staleness flag, same-version stays SILENT (#184 contract)
        ws = _write_ws(tmp_path, [_proj("p", active=True)])
        sd = tmp_path / "state"
        sd.mkdir()
        (sd / "rawgentic-reconciled-version").write_text("9.9.9\n")
        r, _ = _run(tmp_path, ws, version="9.9.9", state_dir=sd,
                    manifest=_write_manifest(tmp_path, MANIFEST))
        assert r.stdout.strip() == "", "default same-version run must stay silent"


class TestStalenessWiring:
    def test_session_start_runs_staleness_active(self):
        t = (HOOKS_DIR / "session-start").read_text()
        assert "--staleness-active" in t

    def test_switch_skill_references_staleness_project(self):
        sw = (REPO_ROOT / "skills" / "switch" / "SKILL.md").read_text()
        assert "--staleness-project" in sw
