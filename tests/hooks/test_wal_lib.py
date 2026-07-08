"""Unit tests for wal-lib.sh shared library functions.

Tests individual bash functions by sourcing wal-lib.sh in a subprocess
and echoing variable values after function calls.
"""
import base64
import json
import os
import shlex
import shutil
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
        """When claudeDocsPath is set (absolute, under $HOME), resolves to it."""
        fake_home = tmp_path / "fakehome"
        target = fake_home / "claude_docs"
        target.mkdir(parents=True)
        ws = make_workspace(claude_docs_path=str(target))
        result, _ = self._resolve(ws, home_dir=fake_home)
        assert result == str(target)

    def test_absolute_outside_home_rejected(self, make_workspace, tmp_path):
        """#262 (C21): an absolute claudeDocsPath OUTSIDE $HOME is rejected with
        a warning and falls back to workspace-relative — the same containment
        the sibling resolvers (wal-stop/wal-suspend/wal-bind-guard) always
        applied. Before the unification the lib trusted it, so WAL/registry
        writes could land where the guards never read."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        outside = tmp_path / "elsewhere" / "claude_docs"
        outside.mkdir(parents=True)
        ws = make_workspace(claude_docs_path=str(outside))
        result, stderr = self._resolve(ws, home_dir=fake_home)
        assert result == f"{ws.root}/claude_docs"
        assert "rejected" in stderr.lower()

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
        stdout, _, rc = _run_bash(script, env_override={"HOME": str(tmp_path / "fakehome")})
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
        stdout, _, rc = _run_bash(script, env_override={"HOME": str(tmp_path / "fakehome")})
        assert rc == 0
        assert stdout == "testproj"


class TestSharedResolutionRouting:
    """#262: claudeDocsPath path RESOLUTION lives in exactly two places —
    wal-lib.sh (bash source of truth) and security-guard.py (python mirror,
    same containment semantic). Every bash consumer sources the lib instead of
    carrying an inline copy; inline copies are what diverged (C7/C21)."""

    CONSUMERS = ["wal-stop", "wal-suspend", "wal-bind-guard", "session-start"]
    # These three had full inline resolver copies before #262 and have NO other
    # legitimate claudeDocsPath use, so the string must be entirely absent.
    # (session-start keeps two non-resolution uses: the migration presence
    # check and the migration write — both act on the raw field, not a path.)
    NO_INLINE = ["wal-stop", "wal-suspend", "wal-bind-guard"]

    @pytest.mark.parametrize("script", CONSUMERS)
    def test_consumer_sources_wal_lib(self, script):
        text = (HOOKS_DIR / script).read_text()
        assert 'source "$SCRIPT_DIR/wal-lib.sh"' in text, (
            f"{script} must source wal-lib.sh for shared resolution")
        assert "wal_resolve_claude_docs" in text, (
            f"{script} must resolve claude_docs via the shared function")

    @pytest.mark.parametrize("script", NO_INLINE)
    def test_no_inline_claude_docs_parse(self, script):
        text = (HOOKS_DIR / script).read_text()
        assert "claudeDocsPath" not in text, (
            f"{script} carries an inline claudeDocsPath parse — the divergent-"
            f"copy pattern #262 removed; route through wal-lib.sh instead")


class TestProjectNameValidation:
    """#265 (C22): a registry-derived project name containing a path separator
    or leading '..' must be rejected centrally in wal_resolve_project — every
    path-building hook routes through it, so one guard covers them all."""

    BAD_NAMES = ["../evil", "a/b", "a\\b", "..", "..hidden"]

    def _resolve_project(self, ws, name, tmp_path):
        script = f"""
source "{WAL_LIB}"
WAL_WORKSPACE_FILE="{ws.workspace_json}"
WAL_WORKSPACE_ROOT="{ws.root}"
WAL_CWD="{ws.root}"
WAL_SESSION_ID="s-val"
wal_resolve_claude_docs
wal_resolve_project
echo "project=$WAL_PROJECT"
"""
        return _run_bash(script)

    @pytest.mark.parametrize("bad", BAD_NAMES)
    def test_rejects_malicious_registry_name(self, make_workspace, tmp_path, bad):
        ws = make_workspace(registry_entries=[{
            "session_id": "s-val", "project": bad,
            "project_path": "./projects/x",
        }])
        stdout, stderr, rc = self._resolve_project(ws, bad, tmp_path)
        assert rc == 0
        assert stdout.splitlines()[-1] == "project=", (
            f"malicious name {bad!r} must resolve to an EMPTY project")

    def test_accepts_normal_names(self, make_workspace, tmp_path):
        for good in ["testproj", "my-app", "app_2", "a.b"]:
            ws = make_workspace(registry_entries=[{
                "session_id": "s-val", "project": good,
                "project_path": "./projects/x",
            }])
            stdout, _, rc = self._resolve_project(ws, good, tmp_path)
            assert rc == 0
            assert stdout.splitlines()[-1] == f"project={good}"


class TestWalParseFieldsSingleSpawn:
    """#266: wal_parse_fields makes exactly ONE jq invocation per event and
    survives hostile field values (quotes, newlines, command substitution)."""

    def _make_counting_shim(self, tmp_path):
        """A jq shim that logs each invocation to a count file, then delegates
        to the real jq binary."""
        real_jq = os.path.expanduser("~/.local/bin/jq")
        if not os.access(real_jq, os.X_OK):
            real_jq = shutil.which("jq")
        assert real_jq, "no jq binary available for the counting shim"
        count_file = tmp_path / "jq-spawn-count"
        shim = tmp_path / "counting-jq"
        shim.write_text(
            f'#!/usr/bin/env bash\necho x >> "{count_file}"\n'
            f'exec "{real_jq}" "$@"\n'
        )
        shim.chmod(0o755)
        return shim, count_file

    def _parse_with_shim(self, tmp_path, raw, fields):
        shim, count_file = self._make_counting_shim(tmp_path)
        # base64 each value so hostile content (newlines, delimiters) cannot
        # corrupt the harness's own result parsing (Codex diff-review #2)
        echoes = "\n".join(
            f'printf "%s" "${{{f}}}" | base64 -w0; echo' for f in fields
        )
        script = f"""
source "{WAL_LIB}"
WAL_JQ="{shim}"
WAL_RAW_INPUT={shlex.quote(raw)}
wal_parse_fields
{echoes}
"""
        stdout, stderr, rc = _run_bash(script)
        assert rc == 0, stderr
        values = [
            base64.b64decode(line).decode()
            for line in stdout.splitlines()
            if line
        ]
        spawns = (
            len(count_file.read_text().splitlines())
            if count_file.exists()
            else 0
        )
        return values, spawns

    def test_exactly_one_jq_spawn(self, tmp_path):
        raw = json.dumps({
            "tool_name": "Bash", "session_id": "s1",
            "tool_use_id": "tu1", "cwd": "/tmp",
        })
        values, spawns = self._parse_with_shim(
            tmp_path, raw,
            ["WAL_TOOL_NAME", "WAL_SESSION_ID", "WAL_TOOL_USE_ID", "WAL_CWD"],
        )
        assert values == ["Bash", "s1", "tu1", "/tmp"]
        assert spawns == 1, f"wal_parse_fields spawned jq {spawns} times, want 1"

    def test_malformed_input_keeps_sentinel_defaults(self, tmp_path):
        values, _ = self._parse_with_shim(
            tmp_path, "not json at all",
            ["WAL_TOOL_NAME", "WAL_SESSION_ID", "WAL_TOOL_USE_ID", "WAL_CWD"],
        )
        assert values == ["unknown", "unknown", "unknown", "."], (
            "malformed stdin must leave the same sentinel defaults as missing "
            f"fields, got {values!r}"
        )

    def test_single_quote_value_is_literal(self, tmp_path):
        hostile = "a'b; echo pwned"
        raw = json.dumps({"tool_name": hostile, "session_id": "s1",
                          "tool_use_id": "tu1", "cwd": "/tmp"})
        values, _ = self._parse_with_shim(tmp_path, raw, ["WAL_TOOL_NAME"])
        assert values == [hostile]

    def test_newline_value_preserved_and_no_field_bleed(self, tmp_path):
        raw = json.dumps({"tool_name": "Bash", "session_id": "s1",
                          "tool_use_id": "tu1", "cwd": "/tmp/a\nb"})
        values, _ = self._parse_with_shim(
            tmp_path, raw,
            ["WAL_TOOL_NAME", "WAL_SESSION_ID", "WAL_TOOL_USE_ID", "WAL_CWD"],
        )
        assert values == ["Bash", "s1", "tu1", "/tmp/a\nb"]

    def test_multiple_json_documents_rejected(self, tmp_path):
        """Codex diff-review #1: a stdin holding TWO valid JSON documents must
        not have the second document's assignment group win via eval — the
        parse must fail like malformed input (sentinels kept, non-zero from
        jq under the hood), never silently adopt trailing-document fields."""
        raw = '{"tool_name":"First"} {"tool_name":"Second"}'
        values, _ = self._parse_with_shim(
            tmp_path, raw, ["WAL_TOOL_NAME", "WAL_CWD"]
        )
        assert values == ["unknown", "."], (
            f"multi-document stdin must keep sentinel defaults, got {values!r}"
        )

    def test_empty_stdin_keeps_defaults_and_rc_zero(self, tmp_path):
        """Empty stdin is tolerated (rc 0, sentinels) — hooks invoked with a
        closed stdin must not start failing (parity with pre-#266 rc 0)."""
        shim, _ = self._make_counting_shim(tmp_path)
        script = f"""
source "{WAL_LIB}"
WAL_JQ="{shim}"
WAL_RAW_INPUT=""
set -euo pipefail
wal_parse_fields
printf "%s" "$WAL_TOOL_NAME" | base64 -w0; echo
"""
        stdout, stderr, rc = _run_bash(script)
        assert rc == 0, stderr
        assert base64.b64decode(stdout.strip()).decode() == "unknown"

    def test_malformed_input_returns_nonzero_under_set_e(self, tmp_path):
        """Caller-fidelity pin (8a review): every real hook calls
        wal_parse_fields under `set -euo pipefail`; on malformed stdin the
        function must return jq's non-zero code (aborting a set -e caller,
        exactly as the old four-call form did) — sentinels are NOT consumed
        on this path."""
        shim, _ = self._make_counting_shim(tmp_path)
        script = f"""
source "{WAL_LIB}"
WAL_JQ="{shim}"
WAL_RAW_INPUT='not json at all'
set -euo pipefail
wal_parse_fields
echo "unreachable-on-malformed"
"""
        stdout, _, rc = _run_bash(script)
        assert rc != 0, "malformed stdin must propagate jq's non-zero exit"
        assert "unreachable-on-malformed" not in stdout

    def test_command_substitution_value_is_inert(self, tmp_path):
        marker = tmp_path / "pwned-marker"
        hostile = f"$(touch {marker})"
        raw = json.dumps({"tool_name": "Bash", "session_id": hostile,
                          "tool_use_id": "tu1", "cwd": "/tmp"})
        values, _ = self._parse_with_shim(tmp_path, raw, ["WAL_SESSION_ID"])
        assert values == [hostile], "value must land as a literal"
        assert not marker.exists(), (
            "command substitution inside a field value EXECUTED during parse"
        )
