"""Tests for wal-context — UserPromptSubmit context injection."""
import json

import pytest
from tests.hooks.conftest import run_hook, parse_hook_output


class TestBoundSession:
    def test_emits_context(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "s1", "cwd": str(ws.root)},
            cwd=ws.root,
        )
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        ctx = output.get("additionalContext", "")
        assert "testproj" in ctx
        assert "s1" in ctx

    def test_writes_current_session_id(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        run_hook("wal-context", {"session_id": "s1", "cwd": str(ws.root)}, cwd=ws.root)

        sid_file = ws.root / "claude_docs" / ".current_session_id"
        assert sid_file.exists()
        assert sid_file.read_text() == "s1"


class TestUnboundSingleActive:
    def test_auto_binds_and_emits(self, make_workspace):
        ws = make_workspace(
            projects=[{"name": "solo", "path": "./projects/solo", "active": True,
                       "configured": True, "lastUsed": "2026-03-08T00:00:00Z"}],
            registry_entries=[],
        )
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "new-sess", "cwd": str(ws.root)},
            cwd=ws.root,
        )
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        ctx = output.get("additionalContext", "")
        assert "solo" in ctx

        # Should have auto-registered in session registry
        registry = ws.registry.read_text()
        assert "new-sess" in registry
        assert "solo" in registry


class TestProtectionLevelInContext:
    def test_default_strict_in_header(self, make_workspace):
        """No .rawgentic.json → strict default appears in context header."""
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
        )
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "s1", "cwd": str(ws.root)},
            cwd=ws.root,
        )
        output = parse_hook_output(stdout)
        ctx = output.get("additionalContext", "")
        assert "strict" in ctx

    def test_sandbox_level_in_header(self, make_workspace):
        """protectionLevel: sandbox appears in context header."""
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
            project_configs={"testproj": {"protectionLevel": "sandbox"}},
        )
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "s1", "cwd": str(ws.root)},
            cwd=ws.root,
        )
        output = parse_hook_output(stdout)
        ctx = output.get("additionalContext", "")
        assert "sandbox" in ctx

    def test_standard_level_in_header(self, make_workspace):
        """protectionLevel: standard appears in context header."""
        ws = make_workspace(
            registry_entries=[{"session_id": "s1", "project": "testproj",
                               "project_path": "./projects/testproj"}],
            project_configs={"testproj": {"protectionLevel": "standard"}},
        )
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "s1", "cwd": str(ws.root)},
            cwd=ws.root,
        )
        output = parse_hook_output(stdout)
        ctx = output.get("additionalContext", "")
        assert "standard" in ctx


class TestUnboundMultiActive:
    def test_emits_switch_prompt(self, make_workspace):
        ws = make_workspace(
            projects=[
                {"name": "alpha", "path": "./projects/alpha", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
                {"name": "beta", "path": "./projects/beta", "active": True, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            registry_entries=[],
        )
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "new-sess", "cwd": str(ws.root)},
            cwd=ws.root,
        )
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        ctx = output.get("additionalContext", "")
        assert "switch" in ctx.lower() or "Multiple" in ctx


class TestUnboundNoActive:
    def test_emits_no_projects_message(self, make_workspace):
        ws = make_workspace(
            projects=[
                {"name": "off", "path": "./projects/off", "active": False, "configured": True, "lastUsed": "2026-03-08T00:00:00Z"},
            ],
            registry_entries=[],
        )
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "new-sess", "cwd": str(ws.root)},
            cwd=ws.root,
        )
        assert rc == 0
        output = parse_hook_output(stdout)
        assert output is not None
        ctx = output.get("additionalContext", "")
        assert "No active" in ctx or "no active" in ctx


class TestMissingWorkspace:
    def test_exits_silently(self, tmp_path):
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "s1", "cwd": str(tmp_path)},
            cwd=tmp_path,
        )
        assert rc == 0

    def test_unknown_session_exits_silently(self):
        stdout, stderr, rc = run_hook(
            "wal-context",
            {"session_id": "unknown", "cwd": "/nonexistent"},
        )
        assert rc == 0
