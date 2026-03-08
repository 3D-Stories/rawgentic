"""Unit tests for wal-lib.sh shared library functions.

Tests individual bash functions by sourcing wal-lib.sh in a subprocess
and echoing variable values after function calls.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
WAL_LIB = HOOKS_DIR / "wal-lib.sh"


def _run_bash(script, *, env_override=None, timeout=5):
    """Run a bash script fragment that sources wal-lib.sh."""
    env = os.environ.copy()
    if env_override:
        env.update(env_override)
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


class TestWalParseInput:
    def _parse_and_echo(self, json_input, field):
        script = f"""
source "{WAL_LIB}"
export WAL_INPUT='{json.dumps(json_input)}'
WAL_TOOL_NAME=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.tool_name // "unknown"')
WAL_SESSION_ID=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.session_id // "unknown"')
WAL_TOOL_USE_ID=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.tool_use_id // "unknown"')
WAL_CWD=$(printf '%s' "$WAL_INPUT" | "$WAL_JQ" -r '.cwd // "."')
echo "${{{field}}}"
"""
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        return stdout

    def test_extracts_tool_name(self):
        result = self._parse_and_echo(
            {"tool_name": "Bash", "session_id": "s1", "tool_use_id": "tu1", "cwd": "/tmp"},
            "WAL_TOOL_NAME",
        )
        assert result == "Bash"

    def test_extracts_session_id(self):
        result = self._parse_and_echo(
            {"tool_name": "Bash", "session_id": "my-session", "tool_use_id": "tu1", "cwd": "/tmp"},
            "WAL_SESSION_ID",
        )
        assert result == "my-session"

    def test_defaults_missing_fields(self):
        result = self._parse_and_echo({}, "WAL_TOOL_NAME")
        assert result == "unknown"


class TestWalExtractSummary:
    def _extract(self, tool_name, tool_input):
        input_json = json.dumps({
            "tool_name": tool_name,
            "tool_input": tool_input,
            "session_id": "s1",
            "tool_use_id": "tu1",
            "cwd": "/tmp",
        })
        script = f"""
source "{WAL_LIB}"
WAL_INPUT='{input_json}'
WAL_TOOL_NAME="{tool_name}"
WAL_SESSION_ID="s1"
WAL_TOOL_USE_ID="tu1"
WAL_CWD="/tmp"
wal_extract_summary
echo "$WAL_SUMMARY"
"""
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        return stdout

    def test_bash_command(self):
        assert "ls -la" in self._extract("Bash", {"command": "ls -la /tmp"})

    def test_write_file(self):
        result = self._extract("Write", {"file_path": "/foo/bar.py"})
        assert "write" in result.lower()
        assert "bar.py" in result

    def test_edit_file(self):
        result = self._extract("Edit", {"file_path": "/foo/bar.py"})
        assert "edit" in result.lower()

    def test_task_description(self):
        result = self._extract("Task", {"description": "run linting"})
        assert "run linting" in result

    def test_unknown_tool(self):
        result = self._extract("Grep", {})
        assert result == "Grep"


class TestWalFindWorkspace:
    def test_finds_workspace_in_cwd(self, make_workspace):
        ws = make_workspace()
        script = f"""
source "{WAL_LIB}"
WAL_CWD="{ws.root}"
wal_find_workspace
echo "$WAL_WORKSPACE_FILE"
"""
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        assert ".rawgentic_workspace.json" in stdout

    def test_finds_workspace_in_parent(self, make_workspace):
        ws = make_workspace()
        child = ws.root / "projects" / "testproj" / "src"
        child.mkdir(parents=True, exist_ok=True)
        script = f"""
source "{WAL_LIB}"
WAL_CWD="{child}"
wal_find_workspace
echo "$WAL_WORKSPACE_FILE"
"""
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        assert ".rawgentic_workspace.json" in stdout

    def test_returns_failure_when_missing(self, tmp_path):
        script = f"""
source "{WAL_LIB}"
WAL_CWD="{tmp_path}"
wal_find_workspace
echo "exit:$?"
"""
        stdout, _, _ = _run_bash(script)
        assert "exit:1" in stdout
