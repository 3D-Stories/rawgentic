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


class TestWalReadStdin:
    """Tests for wal_read_stdin() — reads stdin into WAL_RAW_INPUT."""

    def test_sets_wal_raw_input(self):
        payload = json.dumps({"tool_name": "Bash", "session_id": "s1"})
        script = f"""
source "{WAL_LIB}"
echo '{payload}' | wal_read_stdin
echo "$WAL_RAW_INPUT"
"""
        # wal_read_stdin runs in a subshell due to pipe, so use heredoc instead
        script = f"""
source "{WAL_LIB}"
wal_read_stdin <<'STDIN_EOF'
{payload}
STDIN_EOF
echo "$WAL_RAW_INPUT"
"""
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        parsed = json.loads(stdout)
        assert parsed["tool_name"] == "Bash"
        assert parsed["session_id"] == "s1"


class TestWalParseFields:
    """Tests for wal_parse_fields() — extracts fields from WAL_RAW_INPUT."""

    def _parse_field(self, json_input, field):
        raw = json.dumps(json_input)
        script = f"""
source "{WAL_LIB}"
WAL_RAW_INPUT='{raw}'
wal_parse_fields
echo "${{{field}}}"
"""
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        return stdout

    def test_extracts_tool_name(self):
        result = self._parse_field(
            {"tool_name": "Edit", "session_id": "s1", "tool_use_id": "tu1", "cwd": "/tmp"},
            "WAL_TOOL_NAME",
        )
        assert result == "Edit"

    def test_extracts_session_id(self):
        result = self._parse_field(
            {"tool_name": "Bash", "session_id": "sess-42", "tool_use_id": "tu1", "cwd": "/tmp"},
            "WAL_SESSION_ID",
        )
        assert result == "sess-42"

    def test_extracts_tool_use_id(self):
        result = self._parse_field(
            {"tool_name": "Bash", "session_id": "s1", "tool_use_id": "tu-99", "cwd": "/tmp"},
            "WAL_TOOL_USE_ID",
        )
        assert result == "tu-99"

    def test_extracts_cwd(self):
        result = self._parse_field(
            {"tool_name": "Bash", "session_id": "s1", "tool_use_id": "tu1", "cwd": "/home/user/project"},
            "WAL_CWD",
        )
        assert result == "/home/user/project"

    def test_sets_wal_input_from_raw(self):
        """WAL_INPUT should be set to the same value as WAL_RAW_INPUT."""
        raw = json.dumps({"tool_name": "Bash", "session_id": "s1", "tool_use_id": "tu1", "cwd": "/tmp"})
        script = f"""
source "{WAL_LIB}"
WAL_RAW_INPUT='{raw}'
wal_parse_fields
[ "$WAL_INPUT" = "$WAL_RAW_INPUT" ] && echo "match" || echo "mismatch"
"""
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        assert stdout == "match"

    def test_defaults_missing_fields(self):
        result = self._parse_field({}, "WAL_TOOL_NAME")
        assert result == "unknown"

        result = self._parse_field({}, "WAL_CWD")
        assert result == "."


class TestWalResolveProjectPath:
    """Tests for WAL_PROJECT_PATH in wal_resolve_project()."""

    def test_sets_project_path_from_registry(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{
                "session_id": "s1",
                "project": "testproj",
                "project_path": "./projects/testproj",
            }],
        )
        script = f"""
source "{WAL_LIB}"
WAL_SESSION_ID="s1"
WAL_CWD="{ws.root}"
WAL_WORKSPACE_ROOT="{ws.root}"
wal_resolve_project
echo "$WAL_PROJECT_PATH"
"""
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        # Relative path should be resolved against workspace root
        assert stdout == f"{ws.root}/./projects/testproj"

    def test_absolute_project_path_unchanged(self, make_workspace):
        ws = make_workspace(
            registry_entries=[{
                "session_id": "s1",
                "project": "testproj",
                "project_path": "/absolute/path/to/project",
            }],
        )
        script = f"""
source "{WAL_LIB}"
WAL_SESSION_ID="s1"
WAL_CWD="{ws.root}"
WAL_WORKSPACE_ROOT="{ws.root}"
wal_resolve_project
echo "$WAL_PROJECT_PATH"
"""
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        assert stdout == "/absolute/path/to/project"

    def test_empty_when_no_registry_match(self, make_workspace):
        ws = make_workspace()
        script = f"""
source "{WAL_LIB}"
WAL_SESSION_ID="no-match"
WAL_CWD="{ws.root}"
WAL_WORKSPACE_ROOT="{ws.root}"
wal_resolve_project
echo "path:$WAL_PROJECT_PATH"
"""
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        assert stdout == "path:"


class TestWalResolveProtectionLevel:
    """Tests for wal_resolve_protection_level()."""

    def _resolve(self, make_workspace, *, project_configs=None,
                 workspace_extra=None, project_path_override=None):
        """Helper to run wal_resolve_protection_level and return (level, guards, stderr)."""
        ws = make_workspace(
            project_configs=project_configs,
        )
        project_path = project_path_override or f"{ws.root}/projects/testproj"

        # Optionally patch workspace json with extra fields
        if workspace_extra:
            ws_data = json.loads(ws.workspace_json.read_text())
            ws_data.update(workspace_extra)
            ws.workspace_json.write_text(json.dumps(ws_data))

        script = f"""
source "{WAL_LIB}"
WAL_PROJECT_PATH="{project_path}"
WAL_WORKSPACE_ROOT="{ws.root}"
wal_resolve_protection_level
echo "level:$WAL_PROTECTION_LEVEL"
echo "guards:$WAL_ACTIVE_WAL_GUARDS"
"""
        stdout, stderr, rc = _run_bash(script)
        assert rc == 0
        lines = stdout.split("\n")
        level = ""
        guards = ""
        for line in lines:
            if line.startswith("level:"):
                level = line[len("level:"):]
            elif line.startswith("guards:"):
                guards = line[len("guards:"):]
        return level, guards, stderr

    def test_explicit_guards_wal_array(self, make_workspace):
        """guards.wal explicit array takes highest priority."""
        level, guards, _ = self._resolve(
            make_workspace,
            project_configs={"testproj": {"guards": {"wal": ["ssh-prod", "scp-prod"]}}},
        )
        assert level == "custom"
        assert guards == "ssh-prod scp-prod"

    def test_protection_level_sandbox(self, make_workspace):
        level, guards, _ = self._resolve(
            make_workspace,
            project_configs={"testproj": {"protectionLevel": "sandbox"}},
        )
        assert level == "sandbox"
        assert guards == ""

    def test_protection_level_standard(self, make_workspace):
        level, guards, _ = self._resolve(
            make_workspace,
            project_configs={"testproj": {"protectionLevel": "standard"}},
        )
        assert level == "standard"
        assert "scp-prod" in guards
        assert "docker-prod-destroy" in guards
        assert "ssh-prod" not in guards

    def test_protection_level_strict(self, make_workspace):
        level, guards, _ = self._resolve(
            make_workspace,
            project_configs={"testproj": {"protectionLevel": "strict"}},
        )
        assert level == "strict"
        assert guards == "ALL"

    def test_workspace_default_protection_level(self, make_workspace):
        """Falls back to workspace defaultProtectionLevel when project has none."""
        level, guards, _ = self._resolve(
            make_workspace,
            workspace_extra={"defaultProtectionLevel": "sandbox"},
        )
        assert level == "sandbox"
        assert guards == ""

    def test_project_level_overrides_workspace_default(self, make_workspace):
        """Project protectionLevel takes priority over workspace default."""
        level, guards, _ = self._resolve(
            make_workspace,
            project_configs={"testproj": {"protectionLevel": "sandbox"}},
            workspace_extra={"defaultProtectionLevel": "strict"},
        )
        assert level == "sandbox"
        assert guards == ""

    def test_no_config_defaults_to_strict(self, make_workspace):
        """No project config and no workspace default -> strict."""
        level, guards, _ = self._resolve(make_workspace)
        assert level == "strict"
        assert guards == "ALL"

    def test_invalid_level_defaults_to_strict_with_warning(self, make_workspace):
        """Invalid protectionLevel -> strict + stderr warning."""
        level, guards, stderr = self._resolve(
            make_workspace,
            project_configs={"testproj": {"protectionLevel": "yolo"}},
        )
        assert level == "strict"
        assert guards == "ALL"
        assert "WARNING" in stderr
        assert "yolo" in stderr

    def test_missing_project_path_defaults_to_strict(self, make_workspace):
        """If WAL_PROJECT_PATH is empty, falls back to workspace/strict."""
        level, guards, _ = self._resolve(
            make_workspace,
            project_path_override="",
        )
        assert level == "strict"
        assert guards == "ALL"

    def test_guards_wal_overrides_protection_level(self, make_workspace):
        """guards.wal takes priority even when protectionLevel is also set."""
        level, guards, _ = self._resolve(
            make_workspace,
            project_configs={"testproj": {
                "protectionLevel": "strict",
                "guards": {"wal": ["ssh-prod"]},
            }},
        )
        assert level == "custom"
        assert guards == "ssh-prod"


class TestWalResolveClaudeDocs:
    """Tests for wal_resolve_claude_docs()."""

    def _resolve(self, ws, *, home_dir=None):
        """Run wal_resolve_claude_docs and return WAL_CLAUDE_DOCS."""
        env = None
        if home_dir:
            env = {"HOME": str(home_dir)}
        script = f"""
source "{WAL_LIB}"
WAL_WORKSPACE_FILE="{ws.workspace_json}"
WAL_WORKSPACE_ROOT="{ws.root}"
WAL_CWD="{ws.root}"
wal_resolve_claude_docs
echo "$WAL_CLAUDE_DOCS"
"""
        stdout, stderr, rc = _run_bash(script, env_override=env)
        assert rc == 0
        return stdout, stderr

    def test_reads_claude_docs_path_from_config(self, make_workspace, tmp_path):
        """When claudeDocsPath is set, resolves to the expanded path."""
        target = tmp_path / "fakehome" / "claude_docs"
        target.mkdir(parents=True)
        ws = make_workspace(claude_docs_path=str(target))
        result, _ = self._resolve(ws)
        assert result == str(target)

    def test_tilde_expansion(self, make_workspace, tmp_path):
        """Tilde in claudeDocsPath is expanded to $HOME."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        ws = make_workspace()
        # Manually set claudeDocsPath with tilde
        import json as _json
        ws_data = _json.loads(ws.workspace_json.read_text())
        ws_data["claudeDocsPath"] = "~/claude_docs"
        ws.workspace_json.write_text(_json.dumps(ws_data))
        result, _ = self._resolve(ws, home_dir=fake_home)
        assert result == str(fake_home / "claude_docs")

    def test_falls_back_to_workspace_relative(self, make_workspace):
        """When claudeDocsPath is missing, falls back to workspace-relative."""
        ws = make_workspace()
        result, _ = self._resolve(ws)
        assert result == f"{ws.root}/claude_docs"

    def test_rejects_path_traversal(self, make_workspace, tmp_path):
        """Paths that resolve outside $HOME are rejected; falls back."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        ws = make_workspace()
        import json as _json
        ws_data = _json.loads(ws.workspace_json.read_text())
        ws_data["claudeDocsPath"] = "~/../../etc/evil"
        ws.workspace_json.write_text(_json.dumps(ws_data))
        result, stderr = self._resolve(ws, home_dir=fake_home)
        # Should fall back to workspace-relative, not resolve to /etc/evil
        assert result == f"{ws.root}/claude_docs"
        assert "traversal" in stderr.lower() or "rejected" in stderr.lower()

    def test_init_file_uses_resolved_path(self, make_workspace, tmp_path):
        """wal_init_file() uses WAL_CLAUDE_DOCS for the WAL directory."""
        target = tmp_path / "fakehome" / "claude_docs"
        target.mkdir(parents=True)
        ws = make_workspace(claude_docs_path=str(target))
        script = f"""
source "{WAL_LIB}"
WAL_WORKSPACE_FILE="{ws.workspace_json}"
WAL_WORKSPACE_ROOT="{ws.root}"
WAL_CWD="{ws.root}"
WAL_PROJECT="testproj"
wal_resolve_claude_docs
wal_init_file
echo "$WAL_DIR"
echo "$WAL_FILE"
"""
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        lines = stdout.strip().split("\n")
        assert lines[0] == str(target / "wal")
        assert lines[1] == str(target / "wal" / "testproj.jsonl")

    def test_resolve_project_uses_resolved_path(self, make_workspace, tmp_path):
        """wal_resolve_project() reads registry from WAL_CLAUDE_DOCS."""
        target = tmp_path / "fakehome" / "claude_docs"
        target.mkdir(parents=True)
        ws = make_workspace(
            claude_docs_path=str(target),
            registry_entries=[{
                "session_id": "s1",
                "project": "testproj",
                "project_path": "./projects/testproj",
            }],
        )
        script = f"""
source "{WAL_LIB}"
WAL_WORKSPACE_FILE="{ws.workspace_json}"
WAL_WORKSPACE_ROOT="{ws.root}"
WAL_CWD="{ws.root}"
WAL_SESSION_ID="s1"
wal_resolve_claude_docs
wal_resolve_project
echo "$WAL_PROJECT"
"""
        stdout, _, rc = _run_bash(script)
        assert rc == 0
        assert stdout == "testproj"
