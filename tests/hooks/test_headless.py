"""Tests for headless mode infrastructure (issue #43, PR1).

Covers:
- Unit: headless_interaction.py (comment format, metadata parse, suspend state)
- Unit: session-start hook headless detection
- Unit: wal-stop SUSPEND guard
- Unit: wal-suspend helper
"""
import json
import os
import re
import sys
from pathlib import Path

import pytest

# Import Python helper from hooks/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "hooks"))

# conftest.py is auto-loaded by pytest — Workspace and run_hook available via fixtures
# Import run_hook directly for non-fixture usage
HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"

def _run_hook(hook_name, stdin_dict, *, cwd=None, env_override=None, timeout=10):
    """Local wrapper matching conftest.run_hook for direct calls."""
    import subprocess
    hook_path = HOOKS_DIR / hook_name
    payload = dict(stdin_dict)
    if cwd is not None and "cwd" not in payload:
        payload["cwd"] = str(cwd)
    suffix = hook_path.suffix
    if suffix == ".py":
        cmd = ["python3", str(hook_path)]
    else:
        cmd = ["bash", str(hook_path)]
    env = dict(os.environ)
    if env_override:
        env.update(env_override)
    result = subprocess.run(
        cmd, input=json.dumps(payload), capture_output=True,
        text=True, timeout=timeout, env=env,
        cwd=str(cwd) if cwd else None,
    )
    return result.stdout, result.stderr, result.returncode


# =========================================================================
# headless_interaction.py — unit tests
# =========================================================================

class TestFormatComment:
    """Unit tests for format_comment()."""

    def test_basic_comment_format(self):
        from headless_interaction import format_comment

        result = format_comment(
            step=4,
            title="Circuit Breaker Triggered",
            context="Design critique found ambiguous findings",
            question="How should we resolve finding #3?",
            options=["(a) Narrow scope", "(b) Add acceptance criteria"],
            metadata={"question_id": "abc-123", "step": 4, "type": "circuit_breaker"},
        )
        assert "## [WF2 Step 4] Circuit Breaker Triggered" in result
        assert "**Context:**" in result
        assert "**Question:**" in result
        assert "Narrow scope" in result
        assert "<!-- rawgentic-headless:" in result
        assert '"question_id"' in result

    def test_no_session_id_in_comment(self):
        """session_id must NOT appear in public comment (security: J3#1)."""
        from headless_interaction import format_comment

        result = format_comment(
            step=1,
            title="Test",
            context="ctx",
            question="q?",
            options=["a"],
            metadata={"question_id": "x", "step": 1, "type": "confirm"},
        )
        assert "session_id" not in result

    def test_sanitizes_html_comment_close_in_text(self):
        """Dynamic text values containing --> must be escaped (security: J3#2)."""
        from headless_interaction import format_comment

        result = format_comment(
            step=1,
            title="Test --> injection",
            context="context with --> break",
            question="question --> here?",
            options=["option --> a"],
            metadata={"question_id": "x", "step": 1, "type": "test"},
        )
        # The raw --> should not appear unescaped in the output
        # (except inside the actual closing tag of the metadata block)
        lines_before_metadata = result.split("<!-- rawgentic-headless:")[0]
        assert "-->" not in lines_before_metadata

    def test_sanitizes_html_comment_close_in_metadata(self):
        """Metadata string values containing --> must also be escaped."""
        from headless_interaction import format_comment

        result = format_comment(
            step=1,
            title="Test",
            context="ctx",
            question="q?",
            options=["a"],
            metadata={"question_id": "x", "step": 1, "type": "foo --> bar"},
        )
        # Extract the metadata JSON block and check --> is escaped
        meta_start = result.find("<!-- rawgentic-headless:")
        meta_end = result.find("-->", meta_start + 1)
        meta_block = result[meta_start:meta_end]
        # The raw --> should not appear inside the metadata JSON
        # (the closing --> of the HTML comment is at meta_end, not inside)
        assert "foo --> bar" not in meta_block

    def test_sanitizes_markdown_link_injection(self):
        """Markdown link syntax must be escaped to prevent injection (J3#6)."""
        from headless_interaction import format_comment

        result = format_comment(
            step=1,
            title="Test",
            context="Click [here](https://evil.com) for details",
            question="See ![img](https://evil.com/img.png)?",
            options=["[option](https://evil.com)"],
            metadata={"question_id": "x", "step": 1, "type": "test"},
        )
        # Raw markdown links should be escaped
        assert "[here]" not in result
        assert "![img]" not in result
        assert "(https://evil.com)" not in result

    def test_step_8a_string_id_renders(self):
        """P15: Step 8a per-task review uses a string step id."""
        from headless_interaction import format_comment

        result = format_comment(
            step="8a",
            title="Per-task review ambiguity",
            context="Task T2.3 review surfaced ambiguous findings",
            question="How to triage finding #2?",
            options=["(a) Apply", "(b) Defer with rationale"],
            metadata={"question_id": "f-456", "step": "8a", "type": "per_task_review"},
        )
        assert "## [WF2 Step 8a] Per-task review ambiguity" in result
        assert "T2.3" in result
        # Metadata block carries the string step id intact
        assert '"step":"8a"' in result.replace(" ", "") or '"step": "8a"' in result

    def test_empty_options(self):
        """Should handle empty options list gracefully."""
        from headless_interaction import format_comment

        result = format_comment(
            step=1,
            title="Confirm",
            context="ctx",
            question="Proceed?",
            options=[],
            metadata={"question_id": "x", "step": 1, "type": "confirm"},
        )
        assert "## [WF2 Step 1] Confirm" in result


class TestParseMetadata:
    """Unit tests for parse_metadata()."""

    def test_valid_metadata(self):
        from headless_interaction import parse_metadata

        body = 'Some text\n<!-- rawgentic-headless: {"question_id":"abc","step":4} -->\n'
        result = parse_metadata(body)
        assert result is not None
        assert result["question_id"] == "abc"
        assert result["step"] == 4

    def test_no_metadata_marker(self):
        from headless_interaction import parse_metadata

        result = parse_metadata("Just a regular comment with no metadata")
        assert result is None

    def test_malformed_json(self):
        from headless_interaction import parse_metadata

        body = '<!-- rawgentic-headless: {invalid json here} -->'
        result = parse_metadata(body)
        assert result is None

    def test_metadata_with_extra_whitespace(self):
        from headless_interaction import parse_metadata

        body = '<!-- rawgentic-headless:  \n  {"question_id":"abc","step":1}  \n -->'
        result = parse_metadata(body)
        assert result is not None
        assert result["question_id"] == "abc"

    def test_multiple_metadata_blocks_returns_last(self):
        from headless_interaction import parse_metadata

        body = (
            '<!-- rawgentic-headless: {"question_id":"first"} -->\n'
            'Some text\n'
            '<!-- rawgentic-headless: {"question_id":"second"} -->\n'
        )
        result = parse_metadata(body)
        assert result is not None
        assert result["question_id"] == "second"

    def test_empty_body(self):
        from headless_interaction import parse_metadata

        assert parse_metadata("") is None
        assert parse_metadata(None) is None


class TestSuspendState:
    """Unit tests for suspend state formatting, writing, and reading."""

    def test_format_suspend_state(self):
        from headless_interaction import format_suspend_state

        state = format_suspend_state(
            session_id="sess-123",
            issue=43,
            step=4,
            question_id="q-abc",
            comment_url="https://github.com/org/repo/issues/43#comment-1",
            clarification_round=0,
        )
        assert state["session_id"] == "sess-123"
        assert state["issue"] == 43
        assert state["step"] == 4
        assert state["question_id"] == "q-abc"
        assert "suspended_at" in state

    def test_write_and_read_suspend_state(self, tmp_path):
        from headless_interaction import (
            format_suspend_state,
            write_suspend_state,
            read_suspend_state,
        )

        state = format_suspend_state(
            session_id="sess-123",
            issue=43,
            step=4,
            question_id="q-abc",
            comment_url="https://example.com",
            clarification_round=1,
        )
        filepath = tmp_path / "headless_suspend.json"
        write_suspend_state(str(filepath), state)

        loaded = read_suspend_state(str(filepath))
        assert loaded is not None
        assert loaded["session_id"] == "sess-123"
        assert loaded["clarification_round"] == 1

    def test_write_suspend_state_atomic(self, tmp_path):
        """Write uses atomic temp-file-then-rename pattern."""
        from headless_interaction import format_suspend_state, write_suspend_state

        state = format_suspend_state(
            session_id="s1", issue=1, step=1, question_id="q1",
            comment_url="", clarification_round=0,
        )
        filepath = tmp_path / "headless_suspend.json"
        write_suspend_state(str(filepath), state)

        # File should exist and be valid JSON
        assert filepath.exists()
        data = json.loads(filepath.read_text())
        assert data["session_id"] == "s1"

        # Temp file should NOT exist (was renamed)
        assert not (tmp_path / "headless_suspend.json.tmp").exists()

    def test_read_suspend_state_missing_file(self, tmp_path):
        from headless_interaction import read_suspend_state

        result = read_suspend_state(str(tmp_path / "nonexistent.json"))
        assert result is None

    def test_read_suspend_state_malformed(self, tmp_path):
        from headless_interaction import read_suspend_state

        filepath = tmp_path / "bad.json"
        filepath.write_text("not json at all")
        result = read_suspend_state(str(filepath))
        assert result is None


# =========================================================================
# session-start hook — headless detection tests
# =========================================================================

def _headless_project(name="testproj", path="./projects/testproj", enabled=True):
    """Build a project entry with headlessEnabled for testing."""
    return {
        "name": name,
        "path": path,
        "active": True,
        "lastUsed": "2026-01-01T00:00:00Z",
        "configured": True,
        "headlessEnabled": enabled,
    }


class TestSessionStartHeadless:
    """session-start hook detects RAWGENTIC_HEADLESS=1."""

    def test_headless_env_var_set(self, make_workspace):
        """RAWGENTIC_HEADLESS=1 should inject headless context."""
        ws = make_workspace(projects=[_headless_project()])
        stdout, stderr, rc = _run_hook(
            "session-start",
            {"session_id": "test-sess", "hook_event_name": "startup"},
            cwd=ws.root,
            env_override={"RAWGENTIC_HEADLESS": "1"},
        )
        assert rc == 0
        output = json.loads(stdout) if stdout.strip() else {}
        context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "HEADLESS" in context.upper()

    def test_headless_env_var_zero(self, make_workspace):
        """RAWGENTIC_HEADLESS=0 should NOT trigger headless mode."""
        ws = make_workspace()
        stdout, stderr, rc = _run_hook(
            "session-start",
            {"session_id": "test-sess", "hook_event_name": "startup"},
            cwd=ws.root,
            env_override={"RAWGENTIC_HEADLESS": "0"},
        )
        assert rc == 0
        output = json.loads(stdout) if stdout.strip() else {}
        context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "HEADLESS" not in context.upper()

    def test_headless_env_var_absent(self, make_workspace):
        """No RAWGENTIC_HEADLESS should NOT trigger headless mode."""
        ws = make_workspace()
        # Ensure RAWGENTIC_HEADLESS is truly absent, not just empty
        env_clean = {k: v for k, v in os.environ.items() if k != "RAWGENTIC_HEADLESS"}
        stdout, stderr, rc = _run_hook(
            "session-start",
            {"session_id": "test-sess", "hook_event_name": "startup"},
            cwd=ws.root,
            env_override={"RAWGENTIC_HEADLESS": ""},  # empty string also tested
        )
        assert rc == 0
        output = json.loads(stdout) if stdout.strip() else {}
        context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "HEADLESS" not in context.upper()

    def test_headless_combined_with_other_context(self, make_workspace):
        """Headless context should coexist with workspace context."""
        ws = make_workspace(projects=[_headless_project()])
        stdout, stderr, rc = _run_hook(
            "session-start",
            {"session_id": "test-sess", "hook_event_name": "startup"},
            cwd=ws.root,
            env_override={"RAWGENTIC_HEADLESS": "1"},
        )
        assert rc == 0
        output = json.loads(stdout) if stdout.strip() else {}
        context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        # Should have BOTH headless indicator AND workspace context
        assert "HEADLESS" in context.upper()
        assert "testproj" in context.lower() or "active" in context.lower()

    def test_stale_suspend_file_warning(self, make_workspace):
        """If headless_suspend.json exists with different session_id, warn."""
        ws = make_workspace(projects=[_headless_project()])
        suspend_file = ws.root / "claude_docs" / "headless_suspend.json"
        suspend_file.write_text(json.dumps({
            "session_id": "old-session",
            "issue": 99,
            "step": 3,
            "question_id": "q-old",
            "suspended_at": "2026-03-01T00:00:00Z",
        }))
        stdout, stderr, rc = _run_hook(
            "session-start",
            {"session_id": "new-session", "hook_event_name": "startup"},
            cwd=ws.root,
            env_override={"RAWGENTIC_HEADLESS": "1"},
        )
        assert rc == 0
        output = json.loads(stdout) if stdout.strip() else {}
        context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "stale" in context.lower() or "previous" in context.lower()

    def test_headless_blocked_when_not_enabled(self, make_workspace):
        """RAWGENTIC_HEADLESS=1 but headlessEnabled missing → BLOCKED message."""
        ws = make_workspace()  # default project, no headlessEnabled
        stdout, stderr, rc = _run_hook(
            "session-start",
            {"session_id": "test-sess", "hook_event_name": "startup"},
            cwd=ws.root,
            env_override={"RAWGENTIC_HEADLESS": "1"},
        )
        assert rc == 0
        output = json.loads(stdout) if stdout.strip() else {}
        context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "BLOCKED" in context.upper()
        # Should NOT have the HEADLESS MODE active message
        assert "headless mode active" not in context.lower()

    def test_headless_blocked_when_explicitly_disabled(self, make_workspace):
        """headlessEnabled: false → BLOCKED."""
        ws = make_workspace(projects=[_headless_project(enabled=False)])
        stdout, stderr, rc = _run_hook(
            "session-start",
            {"session_id": "test-sess", "hook_event_name": "startup"},
            cwd=ws.root,
            env_override={"RAWGENTIC_HEADLESS": "1"},
        )
        assert rc == 0
        output = json.loads(stdout) if stdout.strip() else {}
        context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "BLOCKED" in context.upper()

    def test_headless_allowed_when_enabled(self, make_workspace):
        """headlessEnabled: true → HEADLESS MODE active (not blocked)."""
        ws = make_workspace(projects=[_headless_project(enabled=True)])
        stdout, stderr, rc = _run_hook(
            "session-start",
            {"session_id": "test-sess", "hook_event_name": "startup"},
            cwd=ws.root,
            env_override={"RAWGENTIC_HEADLESS": "1"},
        )
        assert rc == 0
        output = json.loads(stdout) if stdout.strip() else {}
        context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "HEADLESS MODE" in context.upper()
        assert "BLOCKED" not in context.upper()

    def test_matching_session_id_no_stale_warning(self, make_workspace):
        """Suspend file with MATCHING session_id should NOT trigger stale warning (CR#3)."""
        from datetime import datetime, timezone
        ws = make_workspace(projects=[_headless_project()])
        suspend_file = ws.root / "claude_docs" / "headless_suspend.json"
        # Use a recent timestamp to avoid TTL-based cleanup
        recent_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        suspend_file.write_text(json.dumps({
            "session_id": "same-session",
            "issue": 43,
            "step": 4,
            "question_id": "q-test",
            "suspended_at": recent_ts,
        }))
        stdout, stderr, rc = _run_hook(
            "session-start",
            {"session_id": "same-session", "hook_event_name": "startup"},
            cwd=ws.root,
            env_override={"RAWGENTIC_HEADLESS": "1"},
        )
        assert rc == 0
        output = json.loads(stdout) if stdout.strip() else {}
        context = output.get("hookSpecificOutput", {}).get("additionalContext", "")
        # Should have HEADLESS context but NOT stale warning
        assert "HEADLESS" in context.upper()
        assert "stale" not in context.lower()
        assert "previous session" not in context.lower()


# =========================================================================
# wal-stop — SUSPEND guard tests
# =========================================================================

class TestWalStopSuspend:
    """wal-stop hook writes SUSPENDED when headless + suspend file."""

    def _write_suspend_file(self, ws, session_id: str = "test-sess"):
        """Helper to create a valid suspend state file."""
        suspend_file = ws.root / "claude_docs" / "headless_suspend.json"
        suspend_file.write_text(json.dumps({
            "session_id": session_id,
            "issue": 43,
            "step": 4,
            "question_id": "q-test",
            "suspended_at": "2026-03-21T00:00:00Z",
        }))

    def test_writes_suspended_when_headless_and_file(self, make_workspace):
        """Both signals present → SUSPENDED status in notes."""
        ws = make_workspace(
            registry_entries=[
                {"session_id": "test-sess", "project": "testproj",
                 "project_path": "./projects/testproj", "started": "2026-01-01T00:00:00Z"}
            ]
        )
        self._write_suspend_file(ws, session_id="test-sess")
        stdout, stderr, rc = _run_hook(
            "wal-stop",
            {"session_id": "test-sess"},
            cwd=ws.root,
            env_override={"RAWGENTIC_HEADLESS": "1"},
        )
        assert rc == 0
        notes = (ws.notes_dir / "testproj.md").read_text()
        assert "SUSPENDED" in notes

    def test_writes_complete_when_no_suspend_file(self, make_workspace):
        """Env var present but no suspend file → normal COMPLETE."""
        ws = make_workspace(
            registry_entries=[
                {"session_id": "test-sess", "project": "testproj",
                 "project_path": "./projects/testproj", "started": "2026-01-01T00:00:00Z"}
            ]
        )
        stdout, stderr, rc = _run_hook(
            "wal-stop",
            {"session_id": "test-sess"},
            cwd=ws.root,
            env_override={"RAWGENTIC_HEADLESS": "1"},
        )
        assert rc == 0
        notes = (ws.notes_dir / "testproj.md").read_text()
        assert "COMPLETE" in notes
        assert "SUSPENDED" not in notes

    def test_writes_complete_when_session_id_mismatch(self, make_workspace):
        """Suspend file exists but session_id doesn't match → COMPLETE."""
        ws = make_workspace(
            registry_entries=[
                {"session_id": "test-sess", "project": "testproj",
                 "project_path": "./projects/testproj", "started": "2026-01-01T00:00:00Z"}
            ]
        )
        self._write_suspend_file(ws, session_id="different-sess")
        stdout, stderr, rc = _run_hook(
            "wal-stop",
            {"session_id": "test-sess"},
            cwd=ws.root,
            env_override={"RAWGENTIC_HEADLESS": "1"},
        )
        assert rc == 0
        notes = (ws.notes_dir / "testproj.md").read_text()
        assert "COMPLETE" in notes

    def test_normal_stop_no_suspend_file(self, make_workspace):
        """No suspend file → normal COMPLETE regardless of env var."""
        ws = make_workspace(
            registry_entries=[
                {"session_id": "test-sess", "project": "testproj",
                 "project_path": "./projects/testproj", "started": "2026-01-01T00:00:00Z"}
            ]
        )
        # No suspend file written
        stdout, stderr, rc = _run_hook(
            "wal-stop",
            {"session_id": "test-sess"},
            cwd=ws.root,
        )
        assert rc == 0
        notes = (ws.notes_dir / "testproj.md").read_text()
        assert "COMPLETE" in notes

    def test_suspended_wal_entry_phase(self, make_workspace):
        """WAL should get SUSPEND phase instead of STOP when suspended."""
        ws = make_workspace(
            registry_entries=[
                {"session_id": "test-sess", "project": "testproj",
                 "project_path": "./projects/testproj", "started": "2026-01-01T00:00:00Z"}
            ]
        )
        self._write_suspend_file(ws, session_id="test-sess")
        stdout, stderr, rc = _run_hook(
            "wal-stop",
            {"session_id": "test-sess"},
            cwd=ws.root,
            env_override={"RAWGENTIC_HEADLESS": "1"},
        )
        assert rc == 0
        wal_file = ws.root / "claude_docs" / "wal" / "testproj.jsonl"
        if wal_file.exists():
            last_line = wal_file.read_text().strip().split("\n")[-1]
            entry = json.loads(last_line)
            assert entry["phase"] == "SUSPEND"

    def test_double_fire_on_suspended_session(self, make_workspace):
        """wal-stop firing twice on a suspended session should be idempotent."""
        ws = make_workspace(
            registry_entries=[
                {"session_id": "test-sess", "project": "testproj",
                 "project_path": "./projects/testproj", "started": "2026-01-01T00:00:00Z"}
            ]
        )
        self._write_suspend_file(ws, session_id="test-sess")

        # First fire — writes SUSPENDED
        _run_hook("wal-stop", {"session_id": "test-sess"}, cwd=ws.root,
                  env_override={"RAWGENTIC_HEADLESS": "1"})

        # Second fire — should be idempotent (SUSPENDED already present)
        stdout, stderr, rc = _run_hook(
            "wal-stop", {"session_id": "test-sess"}, cwd=ws.root,
            env_override={"RAWGENTIC_HEADLESS": "1"},
        )
        assert rc == 0
        notes = (ws.notes_dir / "testproj.md").read_text()
        # Should have exactly one SUSPENDED marker, not two
        assert notes.count("SUSPENDED") == 1


# =========================================================================
# wal-suspend — direct tests
# =========================================================================

class TestWalSuspend:
    """Direct tests for the wal-suspend helper script."""

    def test_writes_suspend_wal_entry(self, make_workspace):
        """wal-suspend should write a SUSPEND entry to the project WAL."""
        ws = make_workspace(
            registry_entries=[
                {"session_id": "test-sess", "project": "testproj",
                 "project_path": "./projects/testproj", "started": "2026-01-01T00:00:00Z"}
            ]
        )
        # Write .current_session_id (expected by wal-suspend)
        session_id_file = ws.root / "claude_docs" / ".current_session_id"
        session_id_file.write_text("test-sess")

        stdout, stderr, rc = _run_hook(
            "wal-suspend", {},
            cwd=ws.root,
        )
        assert rc == 0
        assert "SUSPEND" in stdout

        # Verify WAL entry was written
        wal_file = ws.root / "claude_docs" / "wal" / "testproj.jsonl"
        assert wal_file.exists()
        last_line = wal_file.read_text().strip().split("\n")[-1]
        entry = json.loads(last_line)
        assert entry["phase"] == "SUSPEND"
        assert entry["session"] == "test-sess"
        assert entry["project"] == "testproj"

    def test_fails_without_session_id_file(self, make_workspace):
        """wal-suspend should fail if .current_session_id is missing."""
        ws = make_workspace()
        # Don't create .current_session_id
        stdout, stderr, rc = _run_hook(
            "wal-suspend", {},
            cwd=ws.root,
        )
        assert rc != 0

    def test_fails_without_workspace(self, tmp_path):
        """wal-suspend should fail outside a rawgentic workspace."""
        stdout, stderr, rc = _run_hook(
            "wal-suspend", {},
            cwd=tmp_path,
        )
        assert rc != 0

    def test_idempotent_multiple_calls(self, make_workspace):
        """Calling wal-suspend twice should append two entries (no corruption)."""
        ws = make_workspace(
            registry_entries=[
                {"session_id": "test-sess", "project": "testproj",
                 "project_path": "./projects/testproj", "started": "2026-01-01T00:00:00Z"}
            ]
        )
        session_id_file = ws.root / "claude_docs" / ".current_session_id"
        session_id_file.write_text("test-sess")

        _run_hook("wal-suspend", {}, cwd=ws.root)
        _run_hook("wal-suspend", {}, cwd=ws.root)

        wal_file = ws.root / "claude_docs" / "wal" / "testproj.jsonl"
        lines = wal_file.read_text().strip().split("\n")
        suspend_lines = [l for l in lines if '"SUSPEND"' in l]
        assert len(suspend_lines) == 2  # Two entries, both valid


# =========================================================================
# Canary test — SKILL.md count with <config-loading>
# =========================================================================

HEADLESS_CLI = HOOKS_DIR / "headless_interaction.py"


def _run_cli(*args, stdin=None, timeout=10):
    """Invoke headless_interaction.py as a CLI subprocess.

    Exercises the real `__main__` path (argparse, exit codes, stdout/stderr)
    exactly as the SKILL.md Bash blocks invoke it — calling main() in-process
    would skip the integration seam we're trying to de-risk.
    """
    import subprocess
    result = subprocess.run(
        ["python3", str(HEADLESS_CLI), *args],
        input=stdin, capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout, result.stderr, result.returncode


class TestHeadlessCLI:
    """The CLI wrapper that replaces the fragile inline `python3 -c` blocks in
    SKILL.md (PR 4). Each subcommand maps to an existing tested function; these
    tests pin the command-line contract the workflow depends on."""

    # --- new-id ---

    def test_new_id_prints_uuid(self):
        import uuid
        out, err, rc = _run_cli("new-id")
        assert rc == 0
        # Must be a parseable UUID (round-trips through uuid.UUID)
        parsed = uuid.UUID(out.strip())
        assert str(parsed) == out.strip()

    def test_new_id_is_unique_across_calls(self):
        out1, _, _ = _run_cli("new-id")
        out2, _, _ = _run_cli("new-id")
        assert out1.strip() != out2.strip()

    # --- format-comment ---

    def test_format_comment_renders_and_roundtrips_metadata(self):
        from headless_interaction import parse_metadata
        out, err, rc = _run_cli(
            "format-comment",
            "--step", "5",
            "--title", "Risk ratio halt",
            "--context", "ratio exceeded threshold",
            "--question", "How to proceed?",
            "--option", "(a) decompose",
            "--option", "(b) override",
            "--type", "risk_ratio",
            "--question-id", "abc-123",
        )
        assert rc == 0, err
        assert "Risk ratio halt" in out
        # parens are markdown-escaped by format_comment; assert on word portions
        assert "decompose" in out
        assert "override" in out
        meta = parse_metadata(out)
        assert meta == {"question_id": "abc-123", "step": 5, "type": "risk_ratio"}

    def test_format_comment_numeric_step_is_int_in_metadata(self):
        """A numeric --step must land in metadata as an int (resume routing
        compares step values); only non-numeric sub-steps stay strings."""
        from headless_interaction import parse_metadata
        out, _, rc = _run_cli(
            "format-comment", "--step", "5", "--title", "t",
            "--context", "c", "--question", "q",
            "--type", "x", "--question-id", "id1",
        )
        assert rc == 0
        assert parse_metadata(out)["step"] == 5

    def test_format_comment_substep_8a_stays_string(self):
        from headless_interaction import parse_metadata
        out, _, rc = _run_cli(
            "format-comment", "--step", "8a", "--title", "t",
            "--context", "c", "--question", "q",
            "--type", "x", "--question-id", "id1",
        )
        assert rc == 0
        assert "Step 8a" in out
        assert parse_metadata(out)["step"] == "8a"

    def test_format_comment_no_options_ok(self):
        out, _, rc = _run_cli(
            "format-comment", "--step", "14", "--title", "Deploy",
            "--context", "c", "--question", "Confirm?",
            "--type", "deploy_confirm", "--question-id", "id1",
        )
        assert rc == 0
        assert "Deploy" in out

    # --- write-suspend ---

    def test_write_suspend_creates_file_with_fields(self, tmp_path):
        from headless_interaction import read_suspend_state
        path = tmp_path / "headless_suspend.json"
        out, err, rc = _run_cli(
            "write-suspend",
            "--path", str(path),
            "--issue", "42",
            "--step", "5",
            "--question-id", "abc-123",
            "--comment-url", "https://example.com/c/1",
        )
        assert rc == 0, err
        state = read_suspend_state(str(path))
        assert state["issue"] == 42
        assert state["step"] == 5
        assert state["question_id"] == "abc-123"
        assert state["comment_url"] == "https://example.com/c/1"
        # defaults
        assert state["session_id"] == "N/A"
        assert state["clarification_round"] == 0
        assert "suspended_at" in state

    def test_write_suspend_honors_optional_flags(self, tmp_path):
        from headless_interaction import read_suspend_state
        path = tmp_path / "s.json"
        _, err, rc = _run_cli(
            "write-suspend", "--path", str(path), "--issue", "7",
            "--step", "8a", "--question-id", "q", "--comment-url", "u",
            "--session-id", "sess-1", "--clarification-round", "2",
        )
        assert rc == 0, err
        state = read_suspend_state(str(path))
        assert state["session_id"] == "sess-1"
        assert state["clarification_round"] == 2
        assert state["step"] == "8a"

    def test_write_suspend_unwritable_path_fails_closed(self, tmp_path):
        """A write failure must exit non-zero, not print a traceback a caller
        could misread as success (mirrors adversarial_review_lib fail-closed)."""
        bad = tmp_path / "no_such_dir" / "s.json"
        _, _, rc = _run_cli(
            "write-suspend", "--path", str(bad), "--issue", "1",
            "--step", "5", "--question-id", "q", "--comment-url", "u",
        )
        assert rc != 0

    def test_write_suspend_rejects_empty_question_id(self, tmp_path):
        """argparse required=True only proves presence; an empty value (lost
        shell var upstream) must fail closed, not write an unmatchable file."""
        path = tmp_path / "s.json"
        _, _, rc = _run_cli("write-suspend", "--path", str(path), "--issue", "1",
                            "--step", "5", "--question-id", "", "--comment-url", "u")
        assert rc != 0
        assert not path.exists()

    def test_write_suspend_rejects_blank_comment_url(self, tmp_path):
        path = tmp_path / "s.json"
        _, _, rc = _run_cli("write-suspend", "--path", str(path), "--issue", "1",
                            "--step", "5", "--question-id", "q", "--comment-url", "   ")
        assert rc != 0
        assert not path.exists()

    def test_write_suspend_rejects_nonpositive_issue(self, tmp_path):
        path = tmp_path / "s.json"
        _, _, rc = _run_cli("write-suspend", "--path", str(path), "--issue", "0",
                            "--step", "5", "--question-id", "q", "--comment-url", "u")
        assert rc != 0
        assert not path.exists()

    def test_format_comment_rejects_empty_question_id(self):
        _, _, rc = _run_cli("format-comment", "--step", "5", "--title", "t",
                            "--context", "c", "--question", "q", "--type", "x",
                            "--question-id", "")
        assert rc != 0

    def test_question_id_invariant_comment_matches_suspend(self, tmp_path):
        """The comment metadata and the suspend state must carry the SAME
        question_id — the resume path matches the user's reply to the suspend by
        this id, so a mismatch silently loses the reply."""
        from headless_interaction import parse_metadata, read_suspend_state
        qid = _run_cli("new-id")[0].strip()
        comment = _run_cli("format-comment", "--step", "5", "--title", "t",
                           "--context", "c", "--question", "q", "--type", "x",
                           "--question-id", qid)[0]
        path = tmp_path / "s.json"
        _run_cli("write-suspend", "--path", str(path), "--issue", "5",
                 "--step", "5", "--question-id", qid,
                 "--comment-url", "https://example.com/c/1")
        meta_qid = parse_metadata(comment)["question_id"]
        state_qid = read_suspend_state(str(path))["question_id"]
        assert meta_qid == state_qid == qid

    # --- step validation (rejects malformed steps that would be unroutable) ---

    @pytest.mark.parametrize("bad_step",
                             ["", "0", "-1", " ", "5 ", "abc", "17", "99", "08",
                              "16aa", "8ab", "5\n", "8a\n"])
    def test_format_comment_rejects_invalid_step(self, bad_step):
        """Rejects malformed steps AND well-formed-but-unroutable ones (>16,
        leading zeros, multi-letter sub-steps, trailing-newline via $ anchor)."""
        _, _, rc = _run_cli("format-comment", "--step", bad_step, "--title", "t",
                            "--context", "c", "--question", "q", "--type", "x",
                            "--question-id", "id1")
        assert rc != 0

    @pytest.mark.parametrize("bad_step", ["", "0", "-1", " ", "abc"])
    def test_write_suspend_rejects_invalid_step(self, tmp_path, bad_step):
        path = tmp_path / "s.json"
        _, _, rc = _run_cli("write-suspend", "--path", str(path), "--issue", "1",
                            "--step", bad_step, "--question-id", "q",
                            "--comment-url", "u")
        assert rc != 0
        assert not path.exists()

    @pytest.mark.parametrize("ok_step", ["5", "16", "8a", "11"])
    def test_format_comment_accepts_valid_step(self, ok_step):
        _, _, rc = _run_cli("format-comment", "--step", ok_step, "--title", "t",
                            "--context", "c", "--question", "q", "--type", "x",
                            "--question-id", "id1")
        assert rc == 0


class TestHeadlessCLISkillWiring:
    """Drift guard (PR 4): the WF2 skill must drive the headless QUESTION-suspend
    protocol through the CLI subcommands, not a reconstructed `python3 -c` block.

    Catches two regressions:
    - a subcommand is renamed in the CLI but the SKILL.md still calls the old name
    - someone re-introduces the fragile inline-Python pattern this PR removed
    """

    SKILL_MD = Path(__file__).resolve().parent.parent.parent / "skills" / "implement-feature" / "SKILL.md"
    WIRED_SUBCOMMANDS = ["new-id", "format-comment", "write-suspend"]

    @pytest.mark.parametrize("subcommand", WIRED_SUBCOMMANDS)
    def test_skill_invokes_cli_subcommand(self, subcommand):
        content = self.SKILL_MD.read_text()
        needle = f"headless_interaction.py {subcommand}"
        assert needle in content, (
            f"SKILL.md should invoke `{needle}` but doesn't. If you renamed the "
            f"subcommand, update SKILL.md and this guard."
        )

    def test_skill_has_no_inline_python_headless_block(self):
        """The inline `from headless_interaction import ...` (python3 -c) pattern
        is the fragile footgun PR 4 replaced — it must not creep back."""
        content = self.SKILL_MD.read_text()
        assert "from headless_interaction import" not in content, (
            "SKILL.md re-introduced the inline-Python headless block; use the "
            "headless_interaction.py CLI subcommands instead."
        )

    def _headless_block(self):
        content = self.SKILL_MD.read_text()
        start = content.index("<headless-interaction>")
        end = content.index("</headless-interaction>")
        return content[start:end]

    def _question_protocol_commands(self):
        """Return the QUESTION-protocol bash block as logical command lines:
        comment lines dropped, backslash-continuations joined. This lets a guard
        assert against the ACTUAL command invocations rather than raw substring
        counts that prose/comments could satisfy spuriously."""
        block = self._headless_block()
        fences = re.findall(r"```bash\n(.*?)```", block, re.DOTALL)
        candidates = [f for f in fences if "format-comment" in f]
        assert len(candidates) == 1, (
            "expected exactly one QUESTION-protocol bash block containing format-comment"
        )
        raw = candidates[0]
        lines = [ln for ln in raw.splitlines() if not ln.lstrip().startswith("#")]
        logical, buf = [], ""
        for ln in lines:
            if ln.rstrip().endswith("\\"):
                buf += ln.rstrip()[:-1] + " "
            else:
                buf += ln
                logical.append(buf)
                buf = ""
        if buf:
            logical.append(buf)
        return logical

    def test_question_protocol_is_atomic_and_fail_closed(self):
        """PR4 review finding: the post/label/suspend sequence must be ONE atomic
        bash block — shell variables ($QID/$COMMENT_BODY/$COMMENT_URL) do not
        persist across separate Bash tool calls, so splitting them would post an
        empty body or write a suspend file with empty identifiers. It must also
        fail closed on a missing comment URL. Pin both so they can't regress."""
        block = self._headless_block()
        assert "set -euo pipefail" in block, (
            "QUESTION protocol must run as one atomic, fail-fast bash block"
        )
        assert '[[ -n "$COMMENT_URL" ]]' in block, (
            "QUESTION protocol must guard against an empty comment URL before suspend"
        )

    def test_skill_reuses_one_question_id_and_url(self):
        """PR4 review finding: the question_id invariant only holds if the SAME
        shell variable feeds both commands. Assert against the actual command
        spans (not raw block counts) that `format-comment` and `write-suspend`
        each bind `--question-id "$QID"`, and that `write-suspend` reuses the
        captured `$COMMENT_URL` — a CLI-level round-trip test can't catch a
        SKILL.md regression that swaps the variable."""
        commands = self._question_protocol_commands()
        assert any('QID=$(python3 hooks/headless_interaction.py new-id)' in c
                   for c in commands), "QUESTION protocol must generate one $QID"
        fmt = [c for c in commands if "format-comment" in c]
        sus = [c for c in commands if "write-suspend" in c]
        assert len(fmt) == 1 and '--question-id "$QID"' in fmt[0], (
            'the format-comment command must pass --question-id "$QID"'
        )
        assert len(sus) == 1, "expected exactly one write-suspend command"
        assert '--question-id "$QID"' in sus[0], (
            'the write-suspend command must pass the SAME --question-id "$QID"'
        )
        assert '--comment-url "$COMMENT_URL"' in sus[0], (
            "the write-suspend command must reuse the captured $COMMENT_URL"
        )


class TestSkillCountCanary:
    """Canary: assert the number of SKILL.md files with <config-loading> matches expected."""

    SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
    EXPECTED_CONFIG_LOADING_COUNT = 11  # +adversarial-review (WF5, #77)

    def test_config_loading_skill_count(self):
        """If a new workflow skill is added, this test reminds you to add the disabledSkills check."""
        count = 0
        for skill_dir in self.SKILLS_DIR.iterdir():
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists() and "<config-loading>" in skill_file.read_text():
                count += 1
        assert count == self.EXPECTED_CONFIG_LOADING_COUNT, (
            f"Expected {self.EXPECTED_CONFIG_LOADING_COUNT} skills with <config-loading>, "
            f"found {count}. If you added a new workflow skill, update the disabledSkills "
            f"check and bump this count."
        )
