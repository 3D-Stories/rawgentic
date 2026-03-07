#!/usr/bin/env python3
"""End-to-end tests for security-guard.py hook.

Invokes the hook as a subprocess with simulated stdin,
just like Claude Code does.
"""
import json
import os
import subprocess
import tempfile
import pytest
from pathlib import Path


HOOK_SCRIPT = str(Path(__file__).resolve().parent.parent.parent / "hooks" / "security-guard.py")


def run_hook(tool_name, tool_input):
    """Run security-guard.py with given input. Returns (exit_code, stdout_parsed, stderr)."""
    input_data = json.dumps({
        "tool_name": tool_name,
        "tool_input": tool_input,
        "session_id": "test-session",
    })
    result = subprocess.run(
        ["python3", HOOK_SCRIPT],
        input=input_data,
        capture_output=True,
        text=True,
        timeout=10,
    )
    stdout_parsed = None
    if result.stdout.strip():
        try:
            stdout_parsed = json.loads(result.stdout)
        except json.JSONDecodeError:
            pass
    return result.returncode, stdout_parsed, result.stderr


class TestE2EAllow:
    def test_safe_content_allowed(self):
        code, out, _ = run_hook("Write", {
            "file_path": "/tmp/test/src/app.js",
            "content": "console.log('hello')",
        })
        assert code == 0
        assert out is None

    def test_no_file_path_allowed(self):
        code, out, _ = run_hook("Write", {"content": "eval(x)"})
        assert code == 0
        assert out is None

    def test_unknown_tool_allowed(self):
        code, out, _ = run_hook("Bash", {"command": "echo eval(x)"})
        assert code == 0
        assert out is None


class TestE2EDeny:
    def test_eval_blocked(self):
        code, out, _ = run_hook("Write", {
            "file_path": "/tmp/test/src/app.js",
            "content": "x = eval(code)",
        })
        assert code == 0
        assert out is not None
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "eval_injection" in out["systemMessage"]

    def test_innerhtml_blocked(self):
        code, out, _ = run_hook("Write", {
            "file_path": "/tmp/test/src/app.js",
            "content": 'el.innerHTML = "<b>hi</b>"',
        })
        assert code == 0
        assert out is not None
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_medieval_not_blocked(self):
        code, out, _ = run_hook("Write", {
            "file_path": "/tmp/test/src/game.js",
            "content": "medieval(castle)",
        })
        assert code == 0
        assert out is None

    def test_edit_only_checks_new_string(self):
        """Removing eval should be allowed."""
        code, out, _ = run_hook("Edit", {
            "file_path": "/tmp/test/src/app.js",
            "old_string": "x = eval(code)",
            "new_string": "x = JSON.parse(code)",
        })
        assert code == 0
        assert out is None

    def test_notebook_edit_blocked(self):
        code, out, _ = run_hook("NotebookEdit", {
            "notebook_path": "/tmp/test/nb.ipynb",
            "new_source": "import pickle\npickle.loads(data)",
        })
        assert code == 0
        assert out is not None
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestE2EExceptions:
    def test_exception_allows_blocked_pattern(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "securityExceptions": [{
                    "rule": "eval_injection",
                    "pathPattern": "**/__tests__/**",
                    "addedBy": "user",
                    "date": "2026-03-07",
                }]
            }
            with open(os.path.join(tmpdir, ".rawgentic.json"), "w") as f:
                json.dump(config, f)

            test_dir = os.path.join(tmpdir, "src", "__tests__")
            os.makedirs(test_dir)
            test_file = os.path.join(test_dir, "foo.test.js")

            code, out, _ = run_hook("Write", {
                "file_path": test_file,
                "content": "eval(testCode)",
            })
            assert code == 0
            assert out is None  # allowed by exception

    def test_exception_does_not_apply_to_wrong_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "securityExceptions": [{
                    "rule": "eval_injection",
                    "pathPattern": "**/__tests__/**",
                    "addedBy": "user",
                    "date": "2026-03-07",
                }]
            }
            with open(os.path.join(tmpdir, ".rawgentic.json"), "w") as f:
                json.dump(config, f)

            src_dir = os.path.join(tmpdir, "src")
            os.makedirs(src_dir)
            src_file = os.path.join(src_dir, "app.js")

            code, out, _ = run_hook("Write", {
                "file_path": src_file,
                "content": "eval(userInput)",
            })
            assert code == 0
            assert out is not None
            assert out["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_rawgentic_json_without_exceptions_key(self):
        """rawgentic.json with no securityExceptions key -> all patterns block."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, ".rawgentic.json"), "w") as f:
                json.dump({"project": "test"}, f)

            src_dir = os.path.join(tmpdir, "src")
            os.makedirs(src_dir)

            code, out, _ = run_hook("Write", {
                "file_path": os.path.join(src_dir, "app.js"),
                "content": "eval(x)",
            })
            assert code == 0
            assert out is not None
            assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestE2EErrorHandling:
    def test_malformed_stdin(self):
        result = subprocess.run(
            ["python3", HOOK_SCRIPT],
            input="not json at all",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_empty_stdin(self):
        result = subprocess.run(
            ["python3", HOOK_SCRIPT],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
