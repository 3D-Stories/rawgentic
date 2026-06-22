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


def _enable_security_guidance(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(exist_ok=True)
    (claude_dir / "settings.json").write_text(json.dumps({
        "enabledPlugins": {"security-guidance@claude-plugins-official": True}
    }))


def _decision_file(tmp_path):
    return tmp_path / ".rawgentic" / "security-guidance-decision"


class TestRecordOnceDecision:
    """Hole 3: the disable recommendation must be a decision recorded ONCE, not a
    per-session nag. Once surfaced (or once the user records keep/disable), later
    sessions stay silent even while security-guidance is still enabled."""

    def test_surfaces_once_then_records_and_goes_silent(self, tmp_path):
        _enable_security_guidance(tmp_path)
        # First session: enabled + no decision on record -> nag once.
        out1, _, rc1 = run_hook(HOOK_NAME, STDIN_PAYLOAD,
                                env_override={"HOME": str(tmp_path)})
        assert rc1 == 0
        assert parse_hook_output(out1) is not None, "first session must surface the recommendation"
        # ...and the decision must now be on record.
        assert _decision_file(tmp_path).exists(), "must record that it surfaced the recommendation"
        # Second session: still enabled, but a decision is recorded -> SILENT.
        out2, _, rc2 = run_hook(HOOK_NAME, STDIN_PAYLOAD,
                                env_override={"HOME": str(tmp_path)})
        assert rc2 == 0
        assert parse_hook_output(out2) is None, "must NOT re-nag once a decision is recorded"

    def test_recorded_kept_suppresses_even_when_enabled(self, tmp_path):
        _enable_security_guidance(tmp_path)
        df = _decision_file(tmp_path)
        df.parent.mkdir(parents=True, exist_ok=True)
        df.write_text("kept\n")  # user chose to keep both
        out, _, rc = run_hook(HOOK_NAME, STDIN_PAYLOAD,
                              env_override={"HOME": str(tmp_path)})
        assert rc == 0
        assert parse_hook_output(out) is None, "a recorded 'kept' decision must suppress the nag"

    def test_recorded_disabled_suppresses(self, tmp_path):
        _enable_security_guidance(tmp_path)
        df = _decision_file(tmp_path)
        df.parent.mkdir(parents=True, exist_ok=True)
        df.write_text("disabled\n")
        out, _, rc = run_hook(HOOK_NAME, STDIN_PAYLOAD,
                              env_override={"HOME": str(tmp_path)})
        assert rc == 0
        assert parse_hook_output(out) is None

    def test_not_enabled_neither_nags_nor_records(self, tmp_path):
        """If security-guidance isn't enabled there's nothing to warn about; the
        hook stays silent AND does not pre-record, so a user who deliberately
        enables it later still gets the one-time heads-up."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({
            "enabledPlugins": {"other@vendor": True}
        }))
        out, _, rc = run_hook(HOOK_NAME, STDIN_PAYLOAD,
                              env_override={"HOME": str(tmp_path)})
        assert rc == 0
        assert parse_hook_output(out) is None
        assert not _decision_file(tmp_path).exists()

    def test_recommendation_explains_record_once(self, tmp_path):
        _enable_security_guidance(tmp_path)
        out, _, rc = run_hook(HOOK_NAME, STDIN_PAYLOAD,
                              env_override={"HOME": str(tmp_path)})
        ctx = parse_hook_output(out)["hookSpecificOutput"]["additionalContext"].lower()
        # the user must learn this is a one-time notice and how to keep both
        assert "keep" in ctx
        assert "once" in ctx or "won't" in ctx or "will not" in ctx
