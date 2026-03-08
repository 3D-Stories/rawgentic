"""Tests for hooks/wal-bind-guard — PreToolUse guard enforcing session binding.

Covers:
- Bound session: same-project allow, cross-project deny, edge cases
- Unbound session: multi-active deny, workspace-file exception, single-active allow
- Fail-open: missing jq allows operation
"""
import json
from pathlib import Path

import pytest

from tests.hooks.conftest import Workspace, parse_hook_output, run_hook

HOOK = "wal-bind-guard"

# Reusable project definitions for multi-project workspaces
ALPHA_PROJECT = {
    "name": "alpha",
    "path": "./projects/alpha",
    "active": True,
    "configured": True,
    "lastUsed": "2026-03-08T00:00:00Z",
}
BETA_PROJECT = {
    "name": "beta",
    "path": "./projects/beta",
    "active": True,
    "configured": True,
    "lastUsed": "2026-03-08T00:00:00Z",
}


def _make_stdin(
    tool_name: str,
    session_id: str,
    cwd: str,
    tool_input: dict | None = None,
) -> dict:
    """Build the stdin payload for wal-bind-guard."""
    return {
        "tool_name": tool_name,
        "session_id": session_id,
        "cwd": cwd,
        "tool_input": tool_input or {},
    }


def _decision(stdout: str) -> str:
    """Return 'allow' or 'deny' from hook stdout."""
    parsed = parse_hook_output(stdout)
    if parsed is None:
        return "allow"
    perm = parsed.get("hookSpecificOutput", {}).get("permissionDecision", "")
    return "deny" if perm == "deny" else "allow"


# ── Bound session tests ──────────────────────────────────────────────────


class TestBoundSession:
    """Tests for sessions already bound to a project in the registry."""

    def test_same_project_allows(self, make_workspace) -> None:
        """Bound to alpha, editing a file in alpha -> allow."""
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
            registry_entries=[
                {"session_id": "s1", "project": "alpha", "ts": "2026-03-08T00:00:00Z"},
            ],
        )
        file_path = str(ws.root / "projects" / "alpha" / "src" / "main.py")
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        assert _decision(stdout) == "allow"

    def test_cross_project_denies(self, make_workspace) -> None:
        """Bound to alpha, editing a file in beta -> deny mentioning beta."""
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
            registry_entries=[
                {"session_id": "s1", "project": "alpha", "ts": "2026-03-08T00:00:00Z"},
            ],
        )
        file_path = str(ws.root / "projects" / "beta" / "lib" / "utils.py")
        stdin = _make_stdin("Edit", "s1", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        assert _decision(stdout) == "deny"
        parsed = parse_hook_output(stdout)
        msg = parsed["systemMessage"]
        assert "beta" in msg.lower(), f"Deny message should mention beta: {msg}"

    def test_no_file_path_allows(self, make_workspace) -> None:
        """Empty tool_input (no file_path) -> allow."""
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
            registry_entries=[
                {"session_id": "s1", "project": "alpha", "ts": "2026-03-08T00:00:00Z"},
            ],
        )
        stdin = _make_stdin("Write", "s1", str(ws.root), {})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        assert _decision(stdout) == "allow"

    def test_relative_path_allows(self, make_workspace) -> None:
        """Relative path 'foo/bar.txt' -> allow (not absolute, skipped)."""
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
            registry_entries=[
                {"session_id": "s1", "project": "alpha", "ts": "2026-03-08T00:00:00Z"},
            ],
        )
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": "foo/bar.txt"})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        assert _decision(stdout) == "allow"


# ── Unbound session tests ────────────────────────────────────────────────


class TestUnboundSession:
    """Tests for sessions not yet bound to any project."""

    def test_multi_active_project_file_denies(self, make_workspace) -> None:
        """Unbound, 2 active projects, file in alpha -> deny."""
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
        )
        file_path = str(ws.root / "projects" / "alpha" / "src" / "main.py")
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        assert _decision(stdout) == "deny"

    def test_multi_active_workspace_file_allows(self, make_workspace) -> None:
        """Unbound, 2 active projects, file is .rawgentic_workspace.json -> allow."""
        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
        )
        file_path = str(ws.root / ".rawgentic_workspace.json")
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        assert _decision(stdout) == "allow"

    def test_single_active_allows(self, make_workspace) -> None:
        """Unbound, only 1 active project -> allow (wal-context handles auto-bind)."""
        ws: Workspace = make_workspace(
            projects=[
                {
                    "name": "alpha",
                    "path": "./projects/alpha",
                    "active": True,
                    "configured": True,
                    "lastUsed": "2026-03-08T00:00:00Z",
                },
            ],
        )
        file_path = str(ws.root / "projects" / "alpha" / "src" / "main.py")
        stdin = _make_stdin("Write", "s1", str(ws.root), {"file_path": file_path})
        stdout, _stderr, rc = run_hook(HOOK, stdin, cwd=ws.root)
        assert rc == 0
        assert _decision(stdout) == "allow"


# ── Fail-open tests ─────────────────────────────────────────────────────


class TestFailOpen:
    """Verify fail-open when jq is unavailable."""

    def test_missing_jq_allows(self, make_workspace, tmp_path) -> None:
        """Without jq, wal-bind-guard should fail-open (exit 0, no output).

        Since jq and bash typically share /usr/bin, the no_jq_env fixture
        strips bash too.  We build a shadow /usr/bin with all binaries
        except jq, mirroring the approach in test_wal_guard.py.
        """
        import os as _os
        import shutil
        import subprocess as _sp

        from tests.hooks.conftest import HOOKS_DIR

        ws: Workspace = make_workspace(
            projects=[ALPHA_PROJECT, BETA_PROJECT],
        )

        # Build shadow bin directory: symlink everything in /usr/bin except jq
        shadow = tmp_path / "shadow_bin"
        shadow.mkdir()
        for entry in Path("/usr/bin").iterdir():
            if entry.name == "jq":
                continue
            target = shadow / entry.name
            try:
                target.symlink_to(entry)
            except OSError:
                pass

        # Use a fake HOME so ~/.local/bin/jq is not found
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()

        # Build PATH: replace /usr/bin with shadow, drop any dir containing jq
        original_path = _os.environ.get("PATH", "")
        new_dirs: list[str] = []
        for d in original_path.split(_os.pathsep):
            if not d:
                continue
            if _os.path.normpath(d) == "/usr/bin":
                new_dirs.append(str(shadow))
            elif _os.path.isfile(_os.path.join(d, "jq")):
                continue
            else:
                new_dirs.append(d)

        env = dict(_os.environ)
        env["PATH"] = _os.pathsep.join(new_dirs)
        env["HOME"] = str(fake_home)

        file_path = str(ws.root / "projects" / "beta" / "lib" / "utils.py")
        stdin = _make_stdin("Edit", "s1", str(ws.root), {"file_path": file_path})

        hook_path = HOOKS_DIR / HOOK
        result = _sp.run(
            [str(shadow / "bash"), str(hook_path)],
            input=json.dumps(stdin),
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            cwd=str(ws.root),
        )

        assert result.returncode == 0, f"Expected rc=0 (fail-open), got rc={result.returncode}"
        assert _decision(result.stdout) == "allow"
