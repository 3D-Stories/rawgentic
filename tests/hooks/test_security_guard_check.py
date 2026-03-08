"""Tests for security-guard-check.sh SessionStart hook.

This hook checks whether the official security-guidance plugin is enabled
in ~/.claude/settings.json. If it is, the hook emits a warning via JSON
stdout advising the user to disable it. Otherwise it exits silently.

We override HOME via env_override so that tests use a temporary directory
instead of the real ~/.claude/settings.json.
"""
import json

from tests.hooks.conftest import parse_hook_output, run_hook


HOOK_NAME = "security-guard-check.sh"
STDIN_PAYLOAD = {"session_id": "test-sess", "hook_event_name": "startup"}


class TestSecurityGuardCheck:
    """Integration tests for the security-guard-check.sh hook."""

    def test_conflict_detected(self, tmp_path):
        """When the official security-guidance plugin is enabled, emit a warning."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {
            "enabledPlugins": {
                "security-guidance@claude-plugins-official": True,
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings))

        stdout, stderr, rc = run_hook(
            HOOK_NAME,
            STDIN_PAYLOAD,
            env_override={"HOME": str(tmp_path)},
        )

        assert rc == 0
        parsed = parse_hook_output(stdout)
        assert parsed is not None, f"Expected JSON output, got: {stdout!r}"
        assert parsed["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "security-guidance" in parsed["hookSpecificOutput"]["additionalContext"]

    def test_no_conflict(self, tmp_path):
        """When settings.json exists but the plugin is not listed, exit silently."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings = {
            "enabledPlugins": {
                "some-other-plugin@vendor": True,
            }
        }
        (claude_dir / "settings.json").write_text(json.dumps(settings))

        stdout, stderr, rc = run_hook(
            HOOK_NAME,
            STDIN_PAYLOAD,
            env_override={"HOME": str(tmp_path)},
        )

        assert rc == 0
        assert parse_hook_output(stdout) is None, "Expected no JSON output"

    def test_settings_missing(self, tmp_path):
        """When ~/.claude/settings.json does not exist, exit silently."""
        # tmp_path has no .claude directory at all
        stdout, stderr, rc = run_hook(
            HOOK_NAME,
            STDIN_PAYLOAD,
            env_override={"HOME": str(tmp_path)},
        )

        assert rc == 0
        assert parse_hook_output(stdout) is None, "Expected no JSON output"

    def test_malformed_settings(self, tmp_path):
        """When settings.json contains invalid JSON, exit silently."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{ this is not valid json !!!")

        stdout, stderr, rc = run_hook(
            HOOK_NAME,
            STDIN_PAYLOAD,
            env_override={"HOME": str(tmp_path)},
        )

        assert rc == 0
        assert parse_hook_output(stdout) is None, "Expected no JSON output"
