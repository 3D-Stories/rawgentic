"""Tests for session-start hook — WAL recovery, rotation, size handler, context, staleness."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from tests.hooks.conftest import run_hook, parse_hook_output, scanner_test_guard


def _run_session_start(cwd, session_id="test-sess", event_type="startup", env_override=None):
    if env_override is None:
        env_override = {}
    # Always isolate HOME to prevent migration from writing to real ~/claude_docs/
    if "HOME" not in env_override:
        fake_home = Path(str(cwd)) / ".test_home"
        fake_home.mkdir(exist_ok=True)
        env_override["HOME"] = str(fake_home)
    # Send the REAL Claude Code SessionStart shape: hook_event_name is always the
    # literal "SessionStart"; the startup/resume/clear/compact subtype is in `source`.
    # (Previously this put the subtype in hook_event_name, which laundered the
    # source-vs-hook_event_name bug — every subtype-gated test passed against the
    # wrong contract.)
    stdin = {
        "session_id": session_id,
        "cwd": str(cwd),
        "hook_event_name": "SessionStart",
        "source": event_type,
    }
    return run_hook("session-start", stdin, cwd=cwd, env_override=env_override)


class TestScannerInstallLeakGuard:
    """Regression (#576): a session-start UNIT invocation must never fire the real
    background `pipx install semgrep` — it orphaned 117G of `semgrep-core` into
    /tmp (166 copies) and filled the host disk. The guard lives at the shared
    `run_hook` chokepoint (scoped to session-start), so EVERY session-start caller
    is covered — the `_run_session_start` helper AND the direct callers in
    `TestEventSourceContract` (which bypass the helper), not just one helper. A
    caller that drives the scanner path itself (the e2e installer tests set
    `RAWGENTIC_SCANNER_INSTALLER`) opts out of the default and is left untouched."""

    def test_run_hook_defaults_scanner_skip_for_session_start(self):
        env = {}
        scanner_test_guard("session-start", env)
        assert env.get("RAWGENTIC_SKIP_SCANNER_INSTALL") == "1"

    def test_caller_installer_opts_out_of_the_default(self):
        env = {"RAWGENTIC_SCANNER_INSTALLER": "/fake/installer.sh"}
        scanner_test_guard("session-start", env)
        assert "RAWGENTIC_SKIP_SCANNER_INSTALL" not in env

    def test_explicit_skip_value_preserved(self):
        env = {"RAWGENTIC_SKIP_SCANNER_INSTALL": "0"}
        scanner_test_guard("session-start", env)
        assert env["RAWGENTIC_SKIP_SCANNER_INSTALL"] == "0"

    def test_non_session_start_hooks_untouched(self):
        env = {}
        scanner_test_guard("wal-guard", env)
        assert "RAWGENTIC_SKIP_SCANNER_INSTALL" not in env

    def test_default_session_start_records_skip_and_leaves_no_install(
        self, make_workspace, tmp_path
    ):
        """AC1 behavioral: a DEFAULT session-start call (no scanner env) records a
        skipped-optout outcome and leaves no real scanner install under the fake
        HOME — proving the leak path is closed end-to-end, not just at the helper."""
        ws = make_workspace(registry_entries=[
            {"session_id": "test-sess", "project": "testproj",
             "project_path": "./projects/testproj"}])
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        _run_session_start(ws.root, event_type="startup",
                           env_override={"HOME": str(fake_home)})
        status = fake_home / ".rawgentic" / "scanner-status.json"
        assert status.exists(), "scanner_bootstrap did not run"
        st = json.loads(status.read_text())
        assert st["outcome"] == "skipped-optout-env", st
        assert not list(fake_home.rglob("semgrep-core")), "a real scanner install leaked"


class TestEventSourceContract:
    """Claude Code delivers the startup/resume/clear/compact subtype in the
    `source` field; `hook_event_name` is always the literal "SessionStart".
    The hook must read `source` (with a legacy fallback to `hook_event_name`),
    or every subtype-gated section is dead code at runtime (the bug that left
    the scanner bootstrap, migration, reconciliation, handoff, size-handler,
    and staleness-check all silently disabled in production)."""

    def test_real_payload_shape_fires_subtype_gated_section(self, make_workspace):
        """REAL Claude Code shape: hook_event_name='SessionStart', source='startup'.
        A subtype-gated section (project reconciliation, startup|resume) must fire.
        Pre-fix EVENT_TYPE='SessionStart' != 'startup' so it never ran."""
        ws = make_workspace(
            projects=[
                {"name": "gone", "path": "./projects/gone", "active": True,
                 "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            create_project_dirs=False,
        )
        fake_home = ws.root / ".test_home"
        fake_home.mkdir(exist_ok=True)
        stdin = {
            "session_id": "test-sess",
            "cwd": str(ws.root),
            "hook_event_name": "SessionStart",  # the EVENT name, not the subtype
            "source": "startup",                 # the subtype lives here
        }
        run_hook("session-start", stdin, cwd=ws.root,
                 env_override={"HOME": str(fake_home)})
        updated = json.loads(ws.workspace_json.read_text())
        assert updated["projects"][0]["active"] is False, (
            "reconciliation (a `source`-gated section) did not fire on the real "
            "SessionStart payload — the hook is reading hook_event_name not source"
        )

    def test_legacy_shape_still_supported_via_fallback(self, make_workspace):
        """Legacy/test callers that put the subtype in hook_event_name (no source)
        must still work via the `.source // .hook_event_name` fallback."""
        ws = make_workspace(
            projects=[
                {"name": "gone", "path": "./projects/gone", "active": True,
                 "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            create_project_dirs=False,
        )
        fake_home = ws.root / ".test_home"
        fake_home.mkdir(exist_ok=True)
        stdin = {
            "session_id": "test-sess",
            "cwd": str(ws.root),
            "hook_event_name": "startup",  # legacy shape: subtype in hook_event_name
        }
        run_hook("session-start", stdin, cwd=ws.root,
                 env_override={"HOME": str(fake_home)})
        updated = json.loads(ws.workspace_json.read_text())
        assert updated["projects"][0]["active"] is False


class TestBootstrapHelperWiring:
    """End-to-end: a REAL startup payload through session-start must actually
    invoke the scanner-bootstrap and post-update-reconcile helpers. These guard
    the source-field regression behaviorally — the grep-only wiring drift-guards
    can't catch the bootstrap silently never firing (which is exactly what the
    source/hook_event_name bug did)."""

    def _fake_installer(self, tmp_path):
        sentinel = tmp_path / "install.sentinel"
        p = tmp_path / "fake-install-scanners.sh"
        p.write_text(
            "#!/usr/bin/env bash\n"
            'if [ "${1:-}" = "--check" ]; then echo "present: gitleaks"; '
            'echo "MISSING: trivy"; exit 1; fi\n'
            f'touch "{sentinel}"\n'
            "exit 0\n"
        )
        p.chmod(0o755)
        return p, sentinel

    def test_session_start_fires_scanner_bootstrap(self, make_workspace, tmp_path):
        ws = make_workspace(registry_entries=[
            {"session_id": "test-sess", "project": "testproj",
             "project_path": "./projects/testproj"}])
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        installer, sentinel = self._fake_installer(tmp_path)
        _run_session_start(ws.root, event_type="startup", env_override={
            "HOME": str(fake_home),
            "RAWGENTIC_SCANNER_INSTALLER": str(installer),
        })
        # The bootstrap wrote its status file -> it was actually invoked via the
        # real EVENT_TYPE path (pre-fix EVENT_TYPE='SessionStart' never reached it).
        status = fake_home / ".rawgentic" / "scanner-status.json"
        assert status.exists(), "session-start did not invoke scanner_bootstrap.py"
        st = json.loads(status.read_text())
        assert st["outcome"] == "installing"
        assert "trivy" in st["missing"]

    def test_scanner_bootstrap_does_not_fire_on_compact(self, make_workspace, tmp_path):
        ws = make_workspace(registry_entries=[
            {"session_id": "test-sess", "project": "testproj",
             "project_path": "./projects/testproj"}])
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        installer, _ = self._fake_installer(tmp_path)
        _run_session_start(ws.root, event_type="compact", env_override={
            "HOME": str(fake_home),
            "RAWGENTIC_SCANNER_INSTALLER": str(installer),
        })
        # compact is NOT in startup|resume -> bootstrap must not run
        assert not (fake_home / ".rawgentic" / "scanner-status.json").exists()

    def test_session_start_fires_post_update_reconcile(self, make_workspace, tmp_path):
        # Default project ("testproj") has no adversarialReview -> reconcile nudges
        # and records the version into claude_docs.
        ws = make_workspace(registry_entries=[
            {"session_id": "test-sess", "project": "testproj",
             "project_path": "./projects/testproj"}])
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        # Avoid the scanner bootstrap doing real work: opt it out for this test.
        stdout, _, rc = _run_session_start(ws.root, event_type="startup", env_override={
            "HOME": str(fake_home),
            "RAWGENTIC_SKIP_SCANNER_INSTALL": "1",
        })
        assert rc == 0
        # reconcile recorded the version per-workspace -> it was invoked via session-start
        marker = ws.claude_docs / "rawgentic-reconciled-version"
        assert marker.exists(), "session-start did not invoke post_update_reconcile.py"
        assert marker.read_text().strip(), "reconciled-version marker is empty"


class TestReconciliation:
    def test_deactivates_missing_project_dir(self, make_workspace):
        ws = make_workspace(
            projects=[
                {"name": "exists", "path": "./projects/exists", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
                {"name": "gone", "path": "./projects/gone", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            create_project_dirs=False,
        )
        # Only create the "exists" directory
        (ws.root / "projects" / "exists").mkdir(parents=True)

        _run_session_start(ws.root)

        updated = json.loads(ws.workspace_json.read_text())
        for p in updated["projects"]:
            if p["name"] == "gone":
                assert p["active"] is False
            if p["name"] == "exists":
                assert p["active"] is True

    def test_reconciliation_only_on_startup_resume(self, make_workspace):
        ws = make_workspace(
            projects=[
                {"name": "gone", "path": "./projects/gone", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            create_project_dirs=False,
        )
        # compact event should NOT trigger reconciliation
        _run_session_start(ws.root, event_type="compact")

        updated = json.loads(ws.workspace_json.read_text())
        assert updated["projects"][0]["active"] is True  # Not deactivated


def _iso_ago(days=0, hours=0):
    """ISO-8601Z timestamp <days>d<hours>h in the past — the announce filter is
    age-relative, so fixture timestamps must be dynamic (a fixed date silently
    crosses the cutoff as wall-clock time passes)."""
    from datetime import datetime, timedelta, timezone
    t = datetime.now(timezone.utc) - timedelta(days=days, hours=hours)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestWalRecovery:
    def test_detects_incomplete_operations(self, make_workspace):
        wal_entries = [
            {"ts": _iso_ago(hours=1), "phase": "INTENT", "session": "old",
             "tool": "Bash", "tool_use_id": "orphan-1", "summary": "rm -rf /", "cwd": "/tmp"},
            {"ts": _iso_ago(hours=1), "phase": "INTENT", "session": "old",
             "tool": "Edit", "tool_use_id": "complete-1", "summary": "edit file", "cwd": "/tmp"},
            {"ts": _iso_ago(hours=1), "phase": "DONE", "session": "old",
             "tool": "Edit", "tool_use_id": "complete-1"},
        ]
        ws = make_workspace(wal_entries={"testproj": wal_entries},
                            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                                               "project_path": "./projects/testproj"}])

        stdout, stderr, rc = _run_session_start(ws.root)
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "incomplete" in ctx.lower() or "WAL RECOVERY" in ctx
        assert "orphan-1" in ctx or "rm -rf" in ctx

    def test_no_incomplete_no_recovery_message(self, make_workspace):
        wal_entries = [
            {"ts": "2026-03-08T00:00:00Z", "phase": "INTENT", "session": "old",
             "tool": "Bash", "tool_use_id": "ok-1", "summary": "ls", "cwd": "/tmp"},
            {"ts": "2026-03-08T00:00:01Z", "phase": "DONE", "session": "old",
             "tool": "Bash", "tool_use_id": "ok-1"},
        ]
        ws = make_workspace(wal_entries={"testproj": wal_entries},
                            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                                               "project_path": "./projects/testproj"}])

        stdout, stderr, rc = _run_session_start(ws.root)
        output = parse_hook_output(stdout)
        if output:
            ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
            assert "WAL RECOVERY" not in ctx


class TestWalRecoveryExpiry:
    """#303: incomplete INTENTs older than the age cutoff (default 7 days) are
    hidden from the session-start announce — but never deleted from disk."""

    def _ws(self, make_workspace, wal_entries):
        return make_workspace(
            wal_entries={"testproj": wal_entries},
            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                               "project_path": "./projects/testproj"}])

    def _ctx(self, ws, env_override=None):
        stdout, stderr, rc = _run_session_start(ws.root, env_override=env_override)
        assert rc == 0
        output = parse_hook_output(stdout)
        ctx = ""
        if output:
            ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        return ctx, stderr

    def test_fresh_intent_still_announces(self, make_workspace):
        ws = self._ws(make_workspace, [
            {"ts": _iso_ago(days=6), "phase": "INTENT", "session": "s",
             "tool": "Bash", "tool_use_id": "fresh-1", "summary": "fresh op", "cwd": "/tmp"},
        ])
        ctx, _ = self._ctx(ws)
        assert "WAL RECOVERY" in ctx
        assert "fresh op" in ctx

    def test_stale_intent_hidden(self, make_workspace):
        ws = self._ws(make_workspace, [
            {"ts": _iso_ago(days=30), "phase": "INTENT", "session": "s",
             "tool": "Bash", "tool_use_id": "stale-1", "summary": "march op", "cwd": "/tmp"},
        ])
        ctx, _ = self._ctx(ws)
        assert "march op" not in ctx

    def test_fresh_shows_while_stale_hidden(self, make_workspace):
        ws = self._ws(make_workspace, [
            {"ts": _iso_ago(days=30), "phase": "INTENT", "session": "s",
             "tool": "Bash", "tool_use_id": "stale-1", "summary": "march op", "cwd": "/tmp"},
            {"ts": _iso_ago(days=1), "phase": "INTENT", "session": "s",
             "tool": "Edit", "tool_use_id": "fresh-1", "summary": "fresh op", "cwd": "/tmp"},
        ])
        ctx, _ = self._ctx(ws)
        assert "fresh op" in ctx
        assert "march op" not in ctx

    def test_suppression_is_visible_not_silent(self, make_workspace):
        ws = self._ws(make_workspace, [
            {"ts": _iso_ago(days=30), "phase": "INTENT", "session": "s",
             "tool": "Bash", "tool_use_id": "stale-1", "summary": "march op", "cwd": "/tmp"},
            {"ts": _iso_ago(days=1), "phase": "INTENT", "session": "s",
             "tool": "Edit", "tool_use_id": "fresh-1", "summary": "fresh op", "cwd": "/tmp"},
        ])
        ctx, _ = self._ctx(ws)
        assert "suppressed" in ctx
        assert "1" in ctx  # the suppressed count

    def test_expiry_hides_never_deletes(self, make_workspace):
        entries = [
            {"ts": _iso_ago(days=30), "phase": "INTENT", "session": "s",
             "tool": "Bash", "tool_use_id": "stale-1", "summary": "march op", "cwd": "/tmp"},
        ]
        ws = self._ws(make_workspace, entries)
        wal_file = Path(str(ws.root)) / "claude_docs" / "wal" / "testproj.jsonl"
        before = wal_file.read_text()
        ctx, _ = self._ctx(ws)
        assert "march op" not in ctx
        assert wal_file.read_text() == before  # on disk for audit, untouched

    def test_env_override_tightens_cutoff(self, make_workspace):
        ws = self._ws(make_workspace, [
            {"ts": _iso_ago(days=2), "phase": "INTENT", "session": "s",
             "tool": "Bash", "tool_use_id": "two-day", "summary": "two day op", "cwd": "/tmp"},
        ])
        ctx, _ = self._ctx(ws, env_override={"WAL_RECOVERY_MAX_AGE_DAYS": "1"})
        assert "two day op" not in ctx

    def test_malformed_env_falls_back_to_default(self, make_workspace):
        ws = self._ws(make_workspace, [
            {"ts": _iso_ago(days=3), "phase": "INTENT", "session": "s",
             "tool": "Bash", "tool_use_id": "three-day", "summary": "three day op", "cwd": "/tmp"},
            {"ts": _iso_ago(days=30), "phase": "INTENT", "session": "s",
             "tool": "Bash", "tool_use_id": "stale-1", "summary": "march op", "cwd": "/tmp"},
        ])
        ctx, stderr = self._ctx(ws, env_override={"WAL_RECOVERY_MAX_AGE_DAYS": "banana"})
        assert "three day op" in ctx   # default 7d still applies
        assert "march op" not in ctx
        # The invalid value must be VISIBLY noted, not silently defaulted — the
        # hook's whole stderr is discarded at the callsite (`_do_wal_ops
        # 2>/dev/null`), so the note must ride the session context instead.
        assert "WAL_RECOVERY_MAX_AGE_DAYS" in ctx

    def test_missing_ts_announces_fail_open(self, make_workspace):
        ws = self._ws(make_workspace, [
            {"phase": "INTENT", "session": "s",
             "tool": "Bash", "tool_use_id": "no-ts", "summary": "undated op", "cwd": "/tmp"},
        ])
        ctx, _ = self._ctx(ws)
        assert "undated op" in ctx


class TestLegacyArchivalRemoved:
    """Tests that legacy archival and enrichment code is removed.

    These tests verify post-removal behavior: no archival, no enrichment
    dispatch, and graceful handling when archive data exists but
    query-archive.py is absent.
    """

    def test_no_archival_on_startup_large_notes(self, make_workspace):
        """Large session notes (>600 lines) should NOT be archived to JSONL."""
        large_content = "# Notes\n" + ("x\n" * 700)
        ws = make_workspace(
            session_notes={"testproj": large_content},
            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )

        _run_session_start(ws.root, event_type="startup")

        # Archive directory should NOT be created by archival
        archive_dir = ws.notes_dir / "archive"
        assert not archive_dir.exists(), "Archival should not create archive directory"

    def test_no_enrichment_instruction_on_startup(self, make_workspace):
        """No ARCHIVE_ENRICHMENT instruction should be emitted."""
        large_content = "# Notes\n" + ("x\n" * 700)
        ws = make_workspace(
            session_notes={"testproj": large_content},
            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )

        stdout, stderr, rc = _run_session_start(ws.root, event_type="startup")
        assert rc == 0
        output = parse_hook_output(stdout)
        if output:
            ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
            assert "ARCHIVE_ENRICHMENT" not in ctx

    def test_section_2b_graceful_fail_with_archive_data(self, make_workspace):
        """Section 2b completes without error even when query-archive.py is absent."""
        ws = make_workspace(
            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        # Create archive data that Section 2b would try to query
        archive_dir = ws.notes_dir / "archive"
        archive_dir.mkdir(parents=True)
        entry = {
            "schema_version": 1,
            "archived_at": "2026-03-10T18:00:00Z",
            "source_file": "testproj.md",
            "line_count": 800,
            "note": "# Session\nSome work done.",
            "insights": {
                "summary": "Database migration and auth refactoring",
                "sessions": [],
            },
        }
        (archive_dir / "testproj.jsonl").write_text(json.dumps(entry) + "\n")

        stdout, stderr, rc = _run_session_start(ws.root, event_type="startup")
        assert rc == 0
        # Should not inject archive context (query-archive.py is absent)
        output = parse_hook_output(stdout)
        if output:
            ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
            assert "ARCHIVE CONTEXT" not in ctx


class TestContextEmission:
    def test_emits_valid_json(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        stdout, stderr, rc = _run_session_start(ws.root)
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        assert "hookSpecificOutput" in output
        assert "hookEventName" in output["hookSpecificOutput"]

    def test_writes_current_session_id(self, make_workspace):
        ws = make_workspace()
        _run_session_start(ws.root, session_id="my-session")

        sid_file = ws.root / "claude_docs" / ".current_session_id"
        assert sid_file.exists()
        assert sid_file.read_text() == "my-session"

    def test_no_workspace_emits_no_workspace_message(self, tmp_path):
        stdout, stderr, rc = _run_session_start(tmp_path)
        output = parse_hook_output(stdout)
        if output:
            ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
            assert "No rawgentic workspace" in ctx or "new-project" in ctx


class TestSecurityStaleness:
    """Tests for Section 2c: security pattern staleness check."""

    @staticmethod
    def _setup_official_plugin(tmp_path, content="SECURITY_PATTERNS = []"):
        """Create a mock official security-guidance plugin directory."""
        plugin_dir = tmp_path / "official-plugin" / "plugins" / "security-guidance"
        hooks_dir = plugin_dir / "hooks"
        hooks_dir.mkdir(parents=True)
        pattern_file = hooks_dir / "patterns.py"
        pattern_file.write_text(content)
        return plugin_dir.parent.parent  # returns the dir to set as OFFICIAL_SECURITY_PLUGIN_DIR

    def test_warns_when_patterns_stale(self, make_workspace, tmp_path):
        """When official plugin hash differs from stored marker, emit warning."""
        ws = make_workspace()
        official_dir = self._setup_official_plugin(tmp_path, content="PATTERNS_V2 = [1,2,3]")

        # Write a marker with a different (outdated) hash
        marker_dir = tmp_path / "marker"
        marker_dir.mkdir()
        (marker_dir / ".last-security-sync-hash").write_text("oldhash000")

        env = {
            "OFFICIAL_SECURITY_PLUGIN_DIR": str(official_dir),
            "SECURITY_SYNC_MARKER_DIR": str(marker_dir),
        }
        stdout, stderr, rc = _run_session_start(ws.root, env_override=env)
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "security patterns" in ctx.lower() or "sync-security-patterns" in ctx.lower()

    def test_no_warning_when_up_to_date(self, make_workspace, tmp_path):
        """When hash matches stored marker, no warning emitted."""
        ws = make_workspace()
        content = "SECURITY_PATTERNS = [{'rule': 'test'}]"
        official_dir = self._setup_official_plugin(tmp_path, content=content)

        # Compute the real hash and write it as the marker
        pattern_file = (
            official_dir / "plugins" / "security-guidance" / "hooks" / "patterns.py"
        )
        real_hash = hashlib.sha256(pattern_file.read_bytes()).hexdigest()

        marker_dir = tmp_path / "marker"
        marker_dir.mkdir()
        (marker_dir / ".last-security-sync-hash").write_text(real_hash)

        env = {
            "OFFICIAL_SECURITY_PLUGIN_DIR": str(official_dir),
            "SECURITY_SYNC_MARKER_DIR": str(marker_dir),
        }
        stdout, stderr, rc = _run_session_start(ws.root, env_override=env)
        assert rc == 0
        output = parse_hook_output(stdout)
        if output:
            ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
            assert "security patterns" not in ctx.lower()
            assert "sync-security-patterns" not in ctx.lower()

    def test_warns_again_when_patterns_py_content_drifts(self, make_workspace, tmp_path):
        """#579 AC2: the staleness check must hash patterns.py (the upstream data file),
        so a real change to patterns.py re-fires the nudge even after a prior sync.
        Regression guard: before the fix the check hashed security_reminder_hook.py,
        which no longer changes when patterns.py entries change, so drift never re-fired."""
        ws = make_workspace()
        v1 = "SECURITY_PATTERNS = [{'ruleName': 'a'}]"
        official_dir = self._setup_official_plugin(tmp_path, content=v1)
        pattern_file = (
            official_dir / "plugins" / "security-guidance" / "hooks" / "patterns.py"
        )
        marker_dir = tmp_path / "marker"
        marker_dir.mkdir()
        # Marker records the post-sync hash of patterns.py v1 -> no warning.
        (marker_dir / ".last-security-sync-hash").write_text(
            hashlib.sha256(pattern_file.read_bytes()).hexdigest()
        )
        env = {
            "OFFICIAL_SECURITY_PLUGIN_DIR": str(official_dir),
            "SECURITY_SYNC_MARKER_DIR": str(marker_dir),
        }
        stdout, _, rc = _run_session_start(ws.root, env_override=env)
        assert rc == 0
        output = parse_hook_output(stdout)
        if output:
            ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
            assert "sync-security-patterns" not in ctx.lower()

        # patterns.py drifts (new rule added) -> marker is now stale -> warning re-fires.
        pattern_file.write_text("SECURITY_PATTERNS = [{'ruleName': 'a'}, {'ruleName': 'b'}]")
        stdout, _, rc = _run_session_start(ws.root, env_override=env)
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "security patterns" in ctx.lower() or "sync-security-patterns" in ctx.lower()

    def test_no_warning_when_official_plugin_missing(self, make_workspace, tmp_path):
        """When official plugin is not installed, no warning or error."""
        ws = make_workspace()
        env = {
            "OFFICIAL_SECURITY_PLUGIN_DIR": str(tmp_path / "nonexistent"),
            "SECURITY_SYNC_MARKER_DIR": str(tmp_path / "also-nonexistent"),
        }
        stdout, stderr, rc = _run_session_start(ws.root, env_override=env)
        assert rc == 0
        output = parse_hook_output(stdout)
        if output:
            ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
            assert "security patterns" not in ctx.lower()
            assert "sync-security-patterns" not in ctx.lower()

    def test_warns_when_marker_missing(self, make_workspace, tmp_path):
        """When official plugin exists but no marker file, emit warning."""
        ws = make_workspace()
        official_dir = self._setup_official_plugin(tmp_path)

        marker_dir = tmp_path / "marker-empty"
        marker_dir.mkdir()
        # No marker file written

        env = {
            "OFFICIAL_SECURITY_PLUGIN_DIR": str(official_dir),
            "SECURITY_SYNC_MARKER_DIR": str(marker_dir),
        }
        stdout, stderr, rc = _run_session_start(ws.root, env_override=env)
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "security patterns" in ctx.lower() or "sync-security-patterns" in ctx.lower()


class TestClaudeDocsMigration:
    """Tests for Section 0.5: one-time migration to ~/claude_docs/."""

    def test_fresh_migration(self, make_workspace, tmp_path):
        """Migrate workspace claude_docs/ to ~/claude_docs/ on first startup."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()

        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj",
                               "started": "2026-01-01T00:00:00Z"}],
            wal_entries={"testproj": [
                {"ts": "2026-01-01T00:00:00Z", "phase": "INTENT",
                 "session": "s1", "tool": "Bash", "tool_use_id": "t1",
                 "summary": "ls", "cwd": "."},
            ]},
            session_notes={"testproj": "# Session Notes -- testproj\n"},
        )

        env = {"HOME": str(fake_home), "RAWGENTIC_ENABLE_CLAUDE_DOCS_MIGRATION": "1"}
        stdout, stderr, rc = _run_session_start(ws.root, env_override=env)
        assert rc == 0

        # Target should exist with migrated files
        target = fake_home / "claude_docs"
        assert target.is_dir()
        assert (target / "session_registry.jsonl").is_file()
        assert (target / "wal" / "testproj.jsonl").is_file()
        assert (target / "session_notes" / "testproj.md").is_file()

        # Source should be a symlink or .bak should exist
        source = ws.root / "claude_docs"
        assert source.is_symlink() or (ws.root / "claude_docs.bak").exists()

        # Workspace config should have claudeDocsPath
        ws_data = json.loads(ws.workspace_json.read_text())
        assert ws_data.get("claudeDocsPath") == "~/claude_docs"

    def test_skip_when_already_migrated(self, make_workspace, tmp_path):
        """Skip migration when claudeDocsPath already set in config."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        target = fake_home / "claude_docs"
        target.mkdir(parents=True)

        ws = make_workspace(claude_docs_path=str(target))

        env = {"HOME": str(fake_home), "RAWGENTIC_ENABLE_CLAUDE_DOCS_MIGRATION": "1"}
        stdout, stderr, rc = _run_session_start(ws.root, env_override=env)
        assert rc == 0
        # Source should still be a symlink (unchanged)
        assert (ws.root / "claude_docs").is_symlink()

    def test_merge_with_existing_target(self, make_workspace, tmp_path):
        """Merge workspace data with existing ~/claude_docs/ from another workspace."""
        fake_home = tmp_path / "fakehome"
        target = fake_home / "claude_docs"
        target.mkdir(parents=True)
        (target / "wal").mkdir()
        (target / "session_notes").mkdir()

        # Pre-existing data at target (from another workspace)
        (target / "wal" / "other_proj.jsonl").write_text(
            '{"ts":"2026-01-01","phase":"INTENT","session":"x1","tool":"Bash","tool_use_id":"ox1","summary":"echo","cwd":"."}\n'
        )
        (target / "session_notes" / "other_proj.md").write_text("# Other project\n")
        (target / "session_registry.jsonl").write_text(
            '{"session_id":"x1","project":"other_proj","project_path":"./projects/other_proj","started":"2026-01-01T00:00:00Z"}\n'
        )

        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj",
                               "started": "2026-02-01T00:00:00Z"}],
            wal_entries={"testproj": [
                {"ts": "2026-02-01T00:00:00Z", "phase": "INTENT",
                 "session": "s1", "tool": "Edit", "tool_use_id": "t1",
                 "summary": "edit foo", "cwd": "."},
            ]},
            session_notes={"testproj": "# Session Notes -- testproj\n"},
        )

        env = {"HOME": str(fake_home), "RAWGENTIC_ENABLE_CLAUDE_DOCS_MIGRATION": "1"}
        _run_session_start(ws.root, env_override=env)

        # Both projects' data should exist at target
        assert (target / "wal" / "other_proj.jsonl").is_file()
        assert (target / "wal" / "testproj.jsonl").is_file()
        assert (target / "session_notes" / "other_proj.md").is_file()
        assert (target / "session_notes" / "testproj.md").is_file()

        # Registry should have entries from both
        registry = (target / "session_registry.jsonl").read_text()
        assert "x1" in registry
        assert "s1" in registry

    def test_only_runs_on_startup(self, make_workspace, tmp_path):
        """Migration should only run on startup event, not compact/resume."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()

        ws = make_workspace()
        env = {"HOME": str(fake_home), "RAWGENTIC_ENABLE_CLAUDE_DOCS_MIGRATION": "1"}
        _run_session_start(ws.root, event_type="compact", env_override=env)

        # Should NOT have migrated
        assert not (fake_home / "claude_docs").exists()
        ws_data = json.loads(ws.workspace_json.read_text())
        assert "claudeDocsPath" not in ws_data

    def test_migration_dormant_without_optin(self, make_workspace, tmp_path):
        """The migration is DEFERRED by default: even on a real `startup` payload,
        it must NOT move data unless RAWGENTIC_ENABLE_CLAUDE_DOCS_MIGRATION=1."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj",
                               "started": "2026-01-01T00:00:00Z"}],
            session_notes={"testproj": "# Session Notes -- testproj\n"},
        )
        # Real startup payload, but NO opt-in env.
        _run_session_start(ws.root, event_type="startup",
                           env_override={"HOME": str(fake_home)})
        # No data moved: target absent, source still a real dir, no claudeDocsPath.
        assert not (fake_home / "claude_docs").exists()
        assert (ws.root / "claude_docs").is_dir()
        assert not (ws.root / "claude_docs").is_symlink()
        ws_data = json.loads(ws.workspace_json.read_text())
        assert "claudeDocsPath" not in ws_data


class TestSizeHandler:
    """Tests for Section 2a: session notes size handler integration."""

    def test_trims_oversized_notes_on_startup(self, make_workspace):
        """Notes exceeding 800 lines should be trimmed on startup."""
        large_content = "# Notes\n" + "".join(f"line {i}\n" for i in range(1, 850))
        ws = make_workspace(
            session_notes={"testproj": large_content},
            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )

        _run_session_start(ws.root, event_type="startup")

        notes_file = ws.notes_dir / "testproj.md"
        content = notes_file.read_text()
        lines = content.strip().split("\n")
        # Size handler trims notes > 800 lines to last 200 on startup.
        # For this test, notes are 850 lines > 800, so handler trims.
        # After archival: file is reset to 1-line header.
        assert len(lines) < 600

    def test_trims_oversized_notes_on_compact(self, make_workspace):
        """Notes exceeding 800 lines should be trimmed on compact events."""
        # 850 lines — above the 800-line threshold
        large_content = "# Notes\n" + "".join(f"line {i}\n" for i in range(1, 900))
        ws = make_workspace(
            session_notes={"testproj": large_content},
            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )

        _run_session_start(ws.root, event_type="compact")

        notes_file = ws.notes_dir / "testproj.md"
        content = notes_file.read_text()
        # On compact: archival does NOT run, so size handler handles it
        # Should be trimmed to ~200 lines + header
        lines = content.strip().split("\n")
        assert len(lines) <= 210  # 200 kept + header + trim marker
        assert "Trimmed from" in content
        assert "line 899" in content  # last line preserved
        assert "line 1\n" not in content  # early lines removed

    def test_no_trim_on_compact_under_threshold(self, make_workspace):
        """Notes under 800 lines should not be trimmed on compact."""
        small_content = "# Notes\n" + "".join(f"line {i}\n" for i in range(1, 500))
        ws = make_workspace(
            session_notes={"testproj": small_content},
            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )

        _run_session_start(ws.root, event_type="compact")

        notes_file = ws.notes_dir / "testproj.md"
        content = notes_file.read_text()
        # Unchanged
        assert content == small_content

    def test_no_trim_on_resume(self, make_workspace):
        """Size handler should NOT run on resume events."""
        large_content = "# Notes\n" + "".join(f"line {i}\n" for i in range(1, 900))
        ws = make_workspace(
            session_notes={"testproj": large_content},
            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )

        _run_session_start(ws.root, event_type="resume")

        notes_file = ws.notes_dir / "testproj.md"
        content = notes_file.read_text()
        # Should be untrimmed (resume doesn't trigger size handler)
        assert content == large_content


class TestPerProjectHandoff:
    """Per-project handoff: rawgentic-workspace projects share one
    CLAUDE_PROJECT_DIR, so the generic remember plugin can't separate their
    handoffs. The session-start hook gives each BOUND project its own handoff at
    claude_docs/session_notes/<project>.handoff.md — injected as a briefing and
    surfaced as the write target. Persistent (not consumed-on-read)."""

    def _bound(self):
        return [{"session_id": "test-sess", "project": "testproj",
                 "project_path": "./projects/testproj"}]

    def _ctx(self, stdout):
        out = parse_hook_output(stdout)
        return out.get("hookSpecificOutput", {}).get("additionalContext", "") if out else ""

    def test_injects_handoff_content_for_bound_project(self, make_workspace):
        ws = make_workspace(registry_entries=self._bound())
        (ws.notes_dir / "testproj.handoff.md").write_text("# Handoff\nNEXT: ship the thing")
        stdout, _, rc = _run_session_start(ws.root)
        assert rc == 0
        assert "NEXT: ship the thing" in self._ctx(stdout)

    def test_surfaces_write_target_even_with_no_handoff_yet(self, make_workspace):
        ws = make_workspace(registry_entries=self._bound())
        stdout, _, rc = _run_session_start(ws.root)
        assert rc == 0
        assert "testproj.handoff.md" in self._ctx(stdout)

    def test_no_handoff_when_session_not_bound(self, make_workspace):
        # registry binds a DIFFERENT session id → our session is unbound
        ws = make_workspace(registry_entries=[
            {"session_id": "someone-else", "project": "testproj",
             "project_path": "./projects/testproj"}])
        (ws.notes_dir / "testproj.handoff.md").write_text("SHOULD NOT APPEAR")
        stdout, _, rc = _run_session_start(ws.root, session_id="test-sess")
        ctx = self._ctx(stdout)
        assert "SHOULD NOT APPEAR" not in ctx
        assert "handoff.md" not in ctx

    def test_not_injected_on_compact(self, make_workspace):
        ws = make_workspace(registry_entries=self._bound())
        (ws.notes_dir / "testproj.handoff.md").write_text("COMPACT-BODY-XYZ")
        stdout, _, rc = _run_session_start(ws.root, event_type="compact")
        assert "COMPACT-BODY-XYZ" not in self._ctx(stdout)

    @pytest.mark.parametrize("event", ["startup", "resume", "clear"])
    def test_injected_on_fresh_context_events(self, make_workspace, event):
        ws = make_workspace(registry_entries=self._bound())
        (ws.notes_dir / "testproj.handoff.md").write_text(f"BRIEF-{event}")
        stdout, _, rc = _run_session_start(ws.root, event_type=event)
        assert f"BRIEF-{event}" in self._ctx(stdout)

    def test_handoff_is_persistent_not_consumed(self, make_workspace):
        ws = make_workspace(registry_entries=self._bound())
        f = ws.notes_dir / "testproj.handoff.md"
        f.write_text("PERSIST ME")
        _run_session_start(ws.root)
        assert f.read_text() == "PERSIST ME"  # not cleared on read

    def test_oversized_handoff_is_truncated(self, make_workspace):
        ws = make_workspace(registry_entries=self._bound())
        (ws.notes_dir / "testproj.handoff.md").write_text("X" * 5000)
        stdout, _, rc = _run_session_start(
            ws.root, env_override={"RAWGENTIC_HANDOFF_MAX_CHARS": "100"})
        ctx = self._ctx(stdout)
        assert "truncated" in ctx.lower()
        assert "X" * 5000 not in ctx

    def test_missing_handoff_file_no_crash(self, make_workspace):
        ws = make_workspace(registry_entries=self._bound())
        stdout, stderr, rc = _run_session_start(ws.root)
        assert rc == 0
        assert "testproj.handoff.md" in self._ctx(stdout)

    def test_rejects_unsafe_project_name(self, make_workspace):
        """A corrupt/tampered registry binding a path-traversal project name must
        not become a file-path component — the handoff section is skipped, no
        crash, nothing injected."""
        ws = make_workspace(registry_entries=[
            {"session_id": "test-sess", "project": "../../etc",
             "project_path": "x"}])
        stdout, stderr, rc = _run_session_start(ws.root)
        assert rc == 0
        assert "RAWGENTIC HANDOFF" not in self._ctx(stdout)

    def test_unreadable_handoff_falls_back_to_write_target(self, make_workspace):
        """An existing-but-unreadable handoff must still surface the write target
        (not silently emit nothing) and never leak/crash."""
        if os.geteuid() == 0:
            pytest.skip("root bypasses file permissions")
        ws = make_workspace(registry_entries=self._bound())
        f = ws.notes_dir / "testproj.handoff.md"
        f.write_text("SECRET UNREADABLE BODY")
        f.chmod(0o000)
        try:
            # resume (not startup) → skips the startup-only claude_docs
            # migration, isolating this to SECTION 2e's read path.
            stdout, stderr, rc = _run_session_start(ws.root, event_type="resume")
        finally:
            f.chmod(0o644)
        ctx = self._ctx(stdout)
        assert rc == 0
        assert "SECRET UNREADABLE BODY" not in ctx
        assert "testproj.handoff.md" in ctx


class TestProjectNameEscapeSessionStart:
    """#265 (C22): session-start builds WAL paths from the registry project name
    WITHOUT wal_resolve_project — a name like '../evil' escaped wal/ and let a
    tampered registry read an arbitrary existing .jsonl into session context
    (and rewrite it via WAL rotation when >5000 lines)."""

    def test_escaped_wal_file_is_not_read(self, make_workspace):
        ws = make_workspace(registry_entries=[{
            "session_id": "test-sess", "project": "../evil",
            "project_path": "./projects/testproj"}])
        # A juicy INTENT file OUTSIDE wal/, exactly where wal/../evil.jsonl lands.
        evil = ws.claude_docs / "evil.jsonl"
        evil.write_text(
            '{"ts":"2026-03-08T00:00:00Z","phase":"INTENT","session":"old",'
            '"tool":"Bash","tool_use_id":"leak-1","summary":"secret-marker-xyz",'
            '"cwd":"/tmp"}\n')

        stdout, _stderr, rc = _run_session_start(ws.root)
        assert rc == 0
        output = parse_hook_output(stdout)
        ctx = ""
        if output:
            ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "secret-marker-xyz" not in ctx, (
            "registry name '../evil' read a file outside wal/ into context")
        assert "leak-1" not in ctx


class TestSpawnConsolidation:
    """#269: startup python3 spawn count is bounded — independent of how many
    session-notes files exist — and the dead query-archive path is gone."""

    def _count_python3_spawns(self, ws, tmp_path, tag):
        import shutil as _shutil
        import subprocess as sp
        from tests.hooks.conftest import HOOKS_DIR
        real_py = _shutil.which("python3")
        count_file = tmp_path / f"py-count-{tag}"
        shim_dir = tmp_path / f"shimbin-{tag}"
        shim_dir.mkdir()
        shim = shim_dir / "python3"
        shim.write_text(
            f'#!/usr/bin/env bash\necho x >> "{count_file}"\n'
            f'exec "{real_py}" "$@"\n'
        )
        shim.chmod(0o755)
        fake_home = tmp_path / f"home-{tag}"
        fake_home.mkdir()
        env = dict(os.environ)
        env["HOME"] = str(fake_home)
        env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
        payload = json.dumps({
            "session_id": "test-sess", "cwd": str(ws.root),
            "hook_event_name": "SessionStart", "source": "startup",
        })
        result = sp.run(
            ["bash", str(HOOKS_DIR / "session-start")],
            input=payload, capture_output=True, text=True,
            timeout=30, cwd=str(ws.root), env=env,
        )
        assert result.returncode == 0, result.stderr
        spawns = (
            len(count_file.read_text().splitlines())
            if count_file.exists()
            else 0
        )
        return spawns

    def test_spawns_independent_of_notes_file_count(
        self, make_workspace, tmp_path
    ):
        """1 notes file vs 5 notes files -> IDENTICAL python3 spawn count
        (the notes-size loop must batch into one invocation)."""
        big = "# Notes\n" + ("line\n" * 900)
        ws1 = make_workspace(
            session_notes={"testproj": big},
            registry_entries=[{"session_id": "test-sess",
                               "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        n1 = self._count_python3_spawns(ws1, tmp_path, "one")

        many = {f"proj{i}": big for i in range(5)}
        many["testproj"] = big
        ws5 = make_workspace(
            session_notes=many,
            registry_entries=[{"session_id": "test-sess",
                               "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        n5 = self._count_python3_spawns(ws5, tmp_path, "five")
        assert n1 == n5, (
            f"python3 spawns scale with notes-file count: {n1} for 1 file "
            f"vs {n5} for 6 files — the notes-size loop must batch"
        )

    def test_query_archive_block_gone(self):
        from tests.hooks.conftest import HOOKS_DIR
        text = (HOOKS_DIR / "session-start").read_text()
        assert "query-archive" not in text, (
            "the dead query-archive block (C13) must be removed"
        )

    def test_jq_fallback_newline_does_not_shift_fields(
        self, make_workspace, tmp_path
    ):
        """Codex diff-review pin: with jq unavailable (python fallback), a
        newline inside a field must not shift the following fields — the
        fallback transports fields as shell-quoted assignments."""
        import subprocess as sp
        from tests.hooks.conftest import HOOKS_DIR
        ws = make_workspace(
            session_notes={"testproj": "# Notes\n" + ("line\n" * 900)},
            registry_entries=[{"session_id": "sid",
                               "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        fake_home = tmp_path / "home-nl"
        fake_home.mkdir()
        # Shadow /usr/bin without jq (established no-jq pattern — jq and bash
        # share /usr/bin, so a plain PATH strip would lose bash too)
        shadow = tmp_path / "shadow_bin"
        shadow.mkdir()
        for entry in Path("/usr/bin").iterdir():
            if entry.name == "jq":
                continue
            try:
                (shadow / entry.name).symlink_to(entry)
            except (OSError, FileExistsError):
                continue
        env = dict(os.environ)
        env["HOME"] = str(fake_home)
        env["PATH"] = str(shadow)
        # Discriminator: the notes-size trim is EVENT_TYPE-gated
        # (startup|compact only) and needs python3 but not jq. A newline in
        # session_id + source="clear" shifted the OLD newline-delimited
        # transport to EVENT_TYPE="startup" (the second line of session_id),
        # which TRIMS the 900-line notes file; the shlex transport keeps
        # EVENT_TYPE="clear" and must leave it untouched. R1 catch: the
        # first draft of this pin passed on both parsers (jq-less runs
        # cannot bind a project, so context-based signals were dead);
        # mutation-verified — the buggy transport trims and fails this.
        notes_file = ws.notes_dir / "testproj.md"
        assert len(notes_file.read_text().splitlines()) == 901
        payload = json.dumps({
            "session_id": "sid\nstartup",
            "cwd": str(ws.root),
            "hook_event_name": "SessionStart", "source": "clear",
        })
        result = sp.run(
            ["bash", str(HOOKS_DIR / "session-start")],
            input=payload, capture_output=True, text=True,
            timeout=30, cwd=str(ws.root), env=env,
        )
        assert result.returncode == 0, result.stderr
        lines_after = len(notes_file.read_text().splitlines())
        assert lines_after == 901, (
            f"notes file trimmed to {lines_after} lines on a 'clear' event — "
            "a newline in session_id shifted EVENT_TYPE in the jq-fallback "
            "transport"
        )
