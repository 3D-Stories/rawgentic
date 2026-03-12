"""Tests for session-start hook — WAL recovery, rotation, archival, context."""
import json
import os
from pathlib import Path

import pytest
from tests.hooks.conftest import run_hook, parse_hook_output


def _run_session_start(cwd, session_id="test-sess", event_type="startup", env_override=None):
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
