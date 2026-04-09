"""Tests for session-start hook — WAL recovery, rotation, archival, context, staleness."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from tests.hooks.conftest import run_hook, parse_hook_output


def _run_session_start(cwd, session_id="test-sess", event_type="startup", env_override=None):
    if env_override is None:
        env_override = {}
    # Always isolate HOME to prevent migration from writing to real ~/claude_docs/
    if "HOME" not in env_override:
        fake_home = Path(str(cwd)) / ".test_home"
        fake_home.mkdir(exist_ok=True)
        env_override["HOME"] = str(fake_home)
    stdin = {
        "session_id": session_id,
        "cwd": str(cwd),
        "hook_event_name": event_type,
    }
    return run_hook("session-start", stdin, cwd=cwd, env_override=env_override)


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


class TestWalRecovery:
    def test_detects_incomplete_operations(self, make_workspace):
        wal_entries = [
            {"ts": "2026-03-08T00:00:00Z", "phase": "INTENT", "session": "old",
             "tool": "Bash", "tool_use_id": "orphan-1", "summary": "rm -rf /", "cwd": "/tmp"},
            {"ts": "2026-03-08T00:00:01Z", "phase": "INTENT", "session": "old",
             "tool": "Edit", "tool_use_id": "complete-1", "summary": "edit file", "cwd": "/tmp"},
            {"ts": "2026-03-08T00:00:02Z", "phase": "DONE", "session": "old",
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


class TestArchival:
    def test_archives_large_session_notes_to_jsonl(self, make_workspace):
        large_content = "# Notes\n" + ("x\n" * 700)
        ws = make_workspace(
            session_notes={"testproj": large_content},
            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )

        _run_session_start(ws.root, event_type="startup")

        archive_dir = ws.notes_dir / "archive"
        assert archive_dir.exists()
        jsonl_file = archive_dir / "testproj.jsonl"
        assert jsonl_file.exists()

        entry = json.loads(jsonl_file.read_text().strip())
        assert entry["schema_version"] == 1
        assert entry["source_file"] == "testproj.md"
        assert entry["insights"] is None
        assert "note" in entry

        # Original file should be reset
        current = (ws.notes_dir / "testproj.md").read_text()
        assert len(current.splitlines()) < 5

    def test_no_archival_on_compact_event(self, make_workspace):
        large_content = "# Notes\n" + ("x\n" * 700)
        ws = make_workspace(session_notes={"testproj": large_content})

        _run_session_start(ws.root, event_type="compact")

        archive_dir = ws.notes_dir / "archive"
        assert not archive_dir.exists()

    def test_enrichment_instruction_emitted(self, make_workspace):
        large_content = "# Notes\n" + ("x\n" * 700)
        ws = make_workspace(
            session_notes={"testproj": large_content},
            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )

        stdout, stderr, rc = _run_session_start(ws.root, event_type="startup")
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "ARCHIVE_ENRICHMENT" in ctx
        assert "unenriched" in ctx.lower()


class TestArchiveContextInjection:
    def test_injects_archive_summary_for_bound_session(self, make_workspace):
        """Archive context is injected on startup when a bound session has archive data."""
        ws = make_workspace(
            registry_entries=[{"session_id": "test-sess", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        # Create archive with enriched entry
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
        output = parse_hook_output(stdout)
        assert output is not None
        ctx = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "ARCHIVE CONTEXT" in ctx

    def test_no_archive_context_without_registry(self, make_workspace):
        """No archive context injected for unbound sessions."""
        ws = make_workspace()
        archive_dir = ws.notes_dir / "archive"
        archive_dir.mkdir(parents=True)
        entry = {
            "schema_version": 1,
            "archived_at": "2026-03-10T18:00:00Z",
            "source_file": "testproj.md",
            "line_count": 800,
            "note": "# Session\nSome work.",
            "insights": None,
        }
        (archive_dir / "testproj.jsonl").write_text(json.dumps(entry) + "\n")

        stdout, stderr, rc = _run_session_start(ws.root, event_type="startup")
        assert rc == 0
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
        pattern_file = hooks_dir / "security_reminder_hook.py"
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
            official_dir / "plugins" / "security-guidance" / "hooks" / "security_reminder_hook.py"
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

        env = {"HOME": str(fake_home)}
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

        env = {"HOME": str(fake_home)}
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

        env = {"HOME": str(fake_home)}
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
        env = {"HOME": str(fake_home)}
        _run_session_start(ws.root, event_type="compact", env_override=env)

        # Should NOT have migrated
        assert not (fake_home / "claude_docs").exists()
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
        # After archival resets + size handler: archival fires first at 600+,
        # resetting to ~1 line, so size handler won't trigger.
        # But if archival fails or is bypassed, size handler catches it.
        # For this test, notes are 850 lines > 600, archival runs first.
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
