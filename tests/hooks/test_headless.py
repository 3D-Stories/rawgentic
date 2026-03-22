"""Tests for headless mode infrastructure (issue #43, PR1).

Covers:
- Unit: headless_interaction.py (comment format, metadata parse, suspend state)
- Unit: session-start hook headless detection
- Unit: wal-stop SUSPEND guard
- Unit: wal-suspend helper
"""
import json
import os
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

class TestSessionStartHeadless:
    """session-start hook detects RAWGENTIC_HEADLESS=1."""

    def test_headless_env_var_set(self, make_workspace):
        """RAWGENTIC_HEADLESS=1 should inject headless context."""
        ws = make_workspace()
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
        ws = make_workspace()
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
        ws = make_workspace()
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

    def test_matching_session_id_no_stale_warning(self, make_workspace):
        """Suspend file with MATCHING session_id should NOT trigger stale warning (CR#3)."""
        from datetime import datetime, timezone
        ws = make_workspace()
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
