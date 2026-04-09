"""Tests for notes-size-handler.py — session notes size handler.

Tests the standalone Python script that trims oversized session notes files
and optionally ingests content to the memorypalace server before trimming.
Uses subprocess invocation to match the hook testing pattern.
"""
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tests.hooks.conftest import HOOKS_DIR

HANDLER_SCRIPT = HOOKS_DIR / "notes-size-handler.py"


def _run_handler(
    notes_file: Path,
    *,
    session_id: str = "test-session",
    port: int | None = None,
    timeout: int = 10,
) -> tuple[str, str, int]:
    """Run notes-size-handler.py as a subprocess."""
    cmd = ["python3", str(HANDLER_SCRIPT), str(notes_file), "--session-id", session_id]
    if port is not None:
        cmd.extend(["--port", str(port)])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout, result.stderr, result.returncode


def _make_notes(path: Path, project: str, num_lines: int) -> Path:
    """Create a notes file with the given number of lines."""
    notes_file = path / f"{project}.md"
    header = f"# Session Notes -- {project}\n"
    content = header + "".join(f"line {i}\n" for i in range(1, num_lines))
    notes_file.write_text(content)
    return notes_file


class TestNoOp:
    """Tests for cases where no trimming should occur."""

    def test_under_threshold_no_trim(self, tmp_path):
        """Notes under 800 lines should not be trimmed."""
        notes_file = _make_notes(tmp_path, "testproj", 500)
        original = notes_file.read_text()

        stdout, stderr, rc = _run_handler(notes_file)
        assert rc == 0

        result = json.loads(stdout.strip())
        assert result["trimmed"] is False

        # File unchanged
        assert notes_file.read_text() == original

    def test_exactly_800_lines_no_trim(self, tmp_path):
        """Exactly 800 lines should NOT trigger trim (threshold is >800)."""
        notes_file = _make_notes(tmp_path, "testproj", 800)
        original = notes_file.read_text()

        stdout, stderr, rc = _run_handler(notes_file)
        assert rc == 0

        result = json.loads(stdout.strip())
        assert result["trimmed"] is False
        assert notes_file.read_text() == original

    def test_nonexistent_file_exits_zero(self, tmp_path):
        """Missing file should exit 0 with trimmed=false."""
        fake_file = tmp_path / "nonexistent.md"

        stdout, stderr, rc = _run_handler(fake_file)
        assert rc == 0

        result = json.loads(stdout.strip())
        assert result["trimmed"] is False

    def test_empty_file_no_trim(self, tmp_path):
        """Empty file should not be trimmed."""
        notes_file = tmp_path / "testproj.md"
        notes_file.write_text("")

        stdout, stderr, rc = _run_handler(notes_file)
        assert rc == 0

        result = json.loads(stdout.strip())
        assert result["trimmed"] is False


class TestTrimming:
    """Tests for the trimming behavior."""

    def test_trim_keeps_last_200_lines(self, tmp_path):
        """Notes exceeding 800 lines should keep the last 200 lines."""
        notes_file = _make_notes(tmp_path, "testproj", 1000)

        stdout, stderr, rc = _run_handler(notes_file)
        assert rc == 0

        result = json.loads(stdout.strip())
        assert result["trimmed"] is True
        assert result["line_count"] == 1000
        assert result["kept_lines"] == 200
        assert result["project"] == "testproj"

        # Verify content: last 200 lines preserved
        content = notes_file.read_text()
        lines = content.split("\n")
        # Should contain the header and trim marker, then the last 200 original lines
        assert "# Session Notes -- testproj" in content
        assert "Trimmed from 1000 lines" in content
        # The last line of original was "line 999"
        assert "line 999" in content
        # The first lines (like "line 1") should be gone
        assert "line 1\n" not in content

    def test_trim_at_801_lines(self, tmp_path):
        """801 lines (just over threshold) should trigger trim."""
        notes_file = _make_notes(tmp_path, "testproj", 801)

        stdout, stderr, rc = _run_handler(notes_file)
        assert rc == 0

        result = json.loads(stdout.strip())
        assert result["trimmed"] is True
        assert result["line_count"] == 801

    def test_trim_header_format(self, tmp_path):
        """Trimmed file should have proper header with project name and timestamp."""
        notes_file = _make_notes(tmp_path, "myproject", 900)

        stdout, _, _ = _run_handler(notes_file)
        result = json.loads(stdout.strip())
        assert result["trimmed"] is True

        content = notes_file.read_text()
        assert content.startswith("# Session Notes -- myproject\n")
        assert "<!-- Trimmed from 900 lines at " in content
        # Timestamp should be ISO format
        assert "T" in content.split("Trimmed from")[1]

    def test_atomic_write_no_partial(self, tmp_path):
        """Verify the original file is intact if we read it immediately."""
        notes_file = _make_notes(tmp_path, "testproj", 1000)

        _run_handler(notes_file)

        # File should be valid (not empty, not partial)
        content = notes_file.read_text()
        assert len(content) > 0
        assert content.startswith("# Session Notes")
        # No .tmp files left behind
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0


class TestValidation:
    """Tests for path and project name validation."""

    def test_invalid_project_name_rejected(self, tmp_path):
        """Filenames with invalid characters should be rejected."""
        bad_file = tmp_path / "../../etc/passwd.md"
        # Create a valid file with a traversal-style name
        bad_dir = tmp_path / ".." / ".." / "etc"
        bad_dir.mkdir(parents=True, exist_ok=True)
        bad_file_resolved = bad_dir / "passwd.md"
        bad_file_resolved.write_text("x\n" * 900)

        stdout, stderr, rc = _run_handler(bad_file_resolved)
        # Should exit 0 but not trim (invalid project name or path)
        assert rc == 0

    def test_valid_project_names_accepted(self, tmp_path):
        """Standard project names should be accepted."""
        for name in ["testproj", "my-project", "project_123", "CamelCase"]:
            notes_file = _make_notes(tmp_path, name, 900)
            stdout, _, rc = _run_handler(notes_file)
            assert rc == 0
            result = json.loads(stdout.strip())
            assert result["trimmed"] is True, f"Failed for project name: {name}"


class TestMemorypalaceIngestion:
    """Tests for the optional memorypalace server POST."""

    def test_no_server_trims_anyway(self, tmp_path):
        """When memorypalace is unreachable, trim proceeds without error."""
        notes_file = _make_notes(tmp_path, "testproj", 900)

        # Port 1 is almost certainly not listening
        stdout, stderr, rc = _run_handler(notes_file, port=1)
        assert rc == 0

        result = json.loads(stdout.strip())
        assert result["trimmed"] is True
        assert result["ingested"] is False

    def test_server_receives_content(self, tmp_path):
        """When memorypalace is reachable, full content is POSTed before trim."""
        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                received["body"] = json.loads(body)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *args):
                pass  # suppress logging

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        try:
            notes_file = _make_notes(tmp_path, "testproj", 900)
            original_content = notes_file.read_text()

            stdout, _, rc = _run_handler(notes_file, port=port, session_id="sess-123")
            assert rc == 0

            result = json.loads(stdout.strip())
            assert result["trimmed"] is True
            assert result["ingested"] is True

            # Verify the POST payload
            assert "body" in received
            assert received["body"]["project"] == "testproj"
            assert received["body"]["session_id"] == "sess-123"
            assert received["body"]["source"] == "size-handler"
            # Full content was sent (not trimmed content)
            assert "line 1" in received["body"]["notes"]
            assert "line 899" in received["body"]["notes"]
        finally:
            server.server_close()
            thread.join(timeout=3)

    def test_no_server_call_under_threshold(self, tmp_path):
        """Under threshold, no HTTP call should be made."""
        received = {"called": False}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received["called"] = True
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{}')

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        try:
            notes_file = _make_notes(tmp_path, "testproj", 500)
            _run_handler(notes_file, port=port)
            assert received["called"] is False
        finally:
            server.server_close()
            thread.join(timeout=3)


class TestExitBehavior:
    """Tests that the script always exits 0 on non-fatal errors."""

    def test_permission_error_exits_zero(self, tmp_path):
        """If the file can't be read, exit 0 gracefully."""
        notes_file = tmp_path / "testproj.md"
        notes_file.write_text("x\n" * 900)
        notes_file.chmod(0o000)

        try:
            stdout, stderr, rc = _run_handler(notes_file)
            assert rc == 0
        finally:
            notes_file.chmod(0o644)
