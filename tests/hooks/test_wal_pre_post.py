"""Tests for hooks/wal-pre, wal-post, wal-post-fail — WAL logging hooks.

These three hooks are structurally identical WAL loggers that differ only in
the phase they record:
- wal-pre:       phase=INTENT, includes summary and cwd
- wal-post:      phase=DONE, no summary/cwd
- wal-post-fail: phase=FAIL, no summary/cwd

All hooks are error-tolerant (always exit 0) and silently skip logging when
the session is unbound (no matching entry in session_registry.jsonl).
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests.hooks.conftest import HOOKS_DIR, Workspace, run_hook


# -- Helpers ---------------------------------------------------------------


def _make_stdin(
    tool_name: str = "Bash",
    session_id: str = "test-sess",
    tool_use_id: str = "tu-1",
    cwd: str = ".",
    tool_input: dict | None = None,
) -> dict:
    """Build a stdin payload for WAL hooks."""
    if tool_input is None:
        tool_input = {"command": "echo hello"}
    return {
        "tool_name": tool_name,
        "session_id": session_id,
        "tool_use_id": tool_use_id,
        "cwd": cwd,
        "tool_input": tool_input,
    }


def _bound_registry(session_id: str = "test-sess", project: str = "testproj") -> list[dict]:
    """Return a single-entry registry binding *session_id* to *project*."""
    return [{"session_id": session_id, "project": project, "ts": "2026-01-01T00:00:00Z"}]


def _read_wal_entries(ws: Workspace, project: str = "testproj") -> list[dict]:
    """Read and parse all JSONL entries from the WAL file for *project*."""
    wal_file = ws.wal_dir / f"{project}.jsonl"
    if not wal_file.exists():
        return []
    entries = []
    for line in wal_file.read_text().strip().splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


# -- TestWalPre ------------------------------------------------------------


class TestWalPre:
    """Tests for hooks/wal-pre (phase=INTENT)."""

    HOOK = "wal-pre"

    def test_writes_intent_entry(self, make_workspace) -> None:
        """Bound session with Bash tool produces a WAL entry with all expected fields."""
        ws: Workspace = make_workspace(
            registry_entries=_bound_registry(),
        )
        stdin = _make_stdin(cwd=str(ws.root))
        stdout, stderr, rc = run_hook(self.HOOK, stdin, cwd=ws.root)

        entries = _read_wal_entries(ws)
        assert len(entries) == 1, f"Expected 1 WAL entry, got {len(entries)}"
        entry = entries[0]
        assert entry["phase"] == "INTENT"
        assert entry["session"] == "test-sess"
        assert entry["tool"] == "Bash"
        assert entry["tool_use_id"] == "tu-1"
        assert "ts" in entry
        assert "summary" in entry
        assert "cwd" in entry

    def test_intent_summary_bash(self, make_workspace) -> None:
        """Bash command 'ls -la /tmp' appears in the INTENT summary."""
        ws: Workspace = make_workspace(
            registry_entries=_bound_registry(),
        )
        stdin = _make_stdin(
            cwd=str(ws.root),
            tool_input={"command": "ls -la /tmp"},
        )
        run_hook(self.HOOK, stdin, cwd=ws.root)

        entries = _read_wal_entries(ws)
        assert len(entries) == 1
        assert "ls -la /tmp" in entries[0]["summary"]

    def test_intent_summary_write(self, make_workspace) -> None:
        """Write tool with file_path produces a summary containing 'write'."""
        ws: Workspace = make_workspace(
            registry_entries=_bound_registry(),
        )
        stdin = _make_stdin(
            tool_name="Write",
            cwd=str(ws.root),
            tool_input={"file_path": "/foo/bar.py", "content": "hello"},
        )
        run_hook(self.HOOK, stdin, cwd=ws.root)

        entries = _read_wal_entries(ws)
        assert len(entries) == 1
        assert "write" in entries[0]["summary"].lower()

    def test_unbound_session_writes_nothing(self, make_workspace) -> None:
        """An empty registry (unbound session) produces no WAL file."""
        ws: Workspace = make_workspace(
            registry_entries=[],
        )
        stdin = _make_stdin(cwd=str(ws.root))
        stdout, stderr, rc = run_hook(self.HOOK, stdin, cwd=ws.root)

        assert rc == 0
        entries = _read_wal_entries(ws)
        assert len(entries) == 0

    def test_missing_jq_exits_silently(self, make_workspace, no_jq_env) -> None:
        """Without jq the hook exits 0 with no output and no WAL file.

        Uses a shadow /usr/bin without jq and a fake HOME to ensure
        ~/.local/bin/jq is also unavailable.
        """
        ws: Workspace = make_workspace(
            registry_entries=_bound_registry(),
        )
        stdin = _make_stdin(cwd=str(ws.root))

        tmpdir = Path(tempfile.mkdtemp(prefix="wal-pre-nojq-"))
        try:
            # Build shadow /usr/bin without jq
            shadow = tmpdir / "shadow_bin"
            shadow.mkdir()
            usr_bin = Path("/usr/bin")
            for entry in usr_bin.iterdir():
                if entry.name == "jq":
                    continue
                target = shadow / entry.name
                try:
                    target.symlink_to(entry)
                except OSError:
                    pass

            fake_home = tmpdir / "fakehome"
            fake_home.mkdir()

            original_path = os.environ.get("PATH", "")
            new_dirs: list[str] = []
            for d in original_path.split(os.pathsep):
                if not d:
                    continue
                if os.path.normpath(d) == "/usr/bin":
                    new_dirs.append(str(shadow))
                elif os.path.isfile(os.path.join(d, "jq")):
                    continue
                else:
                    new_dirs.append(d)

            env = dict(os.environ)
            env["PATH"] = os.pathsep.join(new_dirs)
            env["HOME"] = str(fake_home)

            hook_path = HOOKS_DIR / self.HOOK
            result = subprocess.run(
                [str(shadow / "bash"), str(hook_path)],
                input=json.dumps(stdin),
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
                cwd=str(ws.root),
            )

            assert result.returncode == 0
            assert result.stdout.strip() == ""
            entries = _read_wal_entries(ws)
            assert len(entries) == 0
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# -- TestWalPost -----------------------------------------------------------


class TestWalPost:
    """Tests for hooks/wal-post (phase=DONE)."""

    HOOK = "wal-post"

    def test_writes_done_entry(self, make_workspace) -> None:
        """Bound session produces a WAL entry with phase=DONE."""
        ws: Workspace = make_workspace(
            registry_entries=_bound_registry(),
        )
        stdin = _make_stdin(cwd=str(ws.root))
        run_hook(self.HOOK, stdin, cwd=ws.root)

        entries = _read_wal_entries(ws)
        assert len(entries) == 1, f"Expected 1 WAL entry, got {len(entries)}"
        entry = entries[0]
        assert entry["phase"] == "DONE"
        assert entry["session"] == "test-sess"
        assert entry["tool"] == "Bash"


# -- TestWalPostFail -------------------------------------------------------


class TestWalPostFail:
    """Tests for hooks/wal-post-fail (phase=FAIL)."""

    HOOK = "wal-post-fail"

    def test_writes_fail_entry(self, make_workspace) -> None:
        """Bound session produces a WAL entry with phase=FAIL."""
        ws: Workspace = make_workspace(
            registry_entries=_bound_registry(),
        )
        stdin = _make_stdin(cwd=str(ws.root))
        run_hook(self.HOOK, stdin, cwd=ws.root)

        entries = _read_wal_entries(ws)
        assert len(entries) == 1, f"Expected 1 WAL entry, got {len(entries)}"
        entry = entries[0]
        assert entry["phase"] == "FAIL"
        assert entry["session"] == "test-sess"
        assert entry["tool"] == "Bash"
