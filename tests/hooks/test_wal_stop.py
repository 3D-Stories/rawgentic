"""Tests for hooks/wal-stop — Stop hook that writes session end markers.

Covers:
- Writing COMPLETE marker to session notes for a bound session
- Writing STOP entry to the per-project WAL
- Skipping duplicate COMPLETE markers for the same session_id
- Falling back to first active project when registry has no match
- Silent exit (rc=0) when workspace file is missing
- Silent exit (rc=0) when workspace has no projects
"""
import json
from pathlib import Path

from tests.hooks.conftest import run_hook, Workspace

HOOK = "wal-stop"


def _run_stop(session_id: str, cwd: Path) -> tuple[str, str, int]:
    """Run the wal-stop hook with the given session_id and cwd."""
    return run_hook(HOOK, {"session_id": session_id}, cwd=cwd)


class TestWalStop:
    """Tests for the wal-stop hook."""

    def test_writes_complete_marker(self, make_workspace) -> None:
        """Bound session writes a COMPLETE marker with the session_id to notes."""
        ws: Workspace = make_workspace(
            registry_entries=[
                {"session_id": "s1", "project": "testproj", "ts": "2026-03-08T00:00:00Z"}
            ],
        )

        stdout, stderr, rc = _run_stop("s1", ws.root)

        assert rc == 0
        notes_file = ws.notes_dir / "testproj.md"
        assert notes_file.exists(), "Session notes file should be created"
        content = notes_file.read_text()
        assert "COMPLETE" in content, "Notes should contain COMPLETE marker"
        assert "s1" in content, "Notes should contain session_id"

    def test_writes_stop_wal_entry(self, make_workspace) -> None:
        """Bound session writes a STOP WAL entry with phase, session, and project."""
        ws: Workspace = make_workspace(
            registry_entries=[
                {"session_id": "s1", "project": "testproj", "ts": "2026-03-08T00:00:00Z"}
            ],
        )

        stdout, stderr, rc = _run_stop("s1", ws.root)

        assert rc == 0
        wal_file = ws.wal_dir / "testproj.jsonl"
        assert wal_file.exists(), "WAL file should be created"
        lines = [l for l in wal_file.read_text().strip().splitlines() if l.strip()]
        assert len(lines) >= 1, "Should have at least one WAL entry"
        entry = json.loads(lines[-1])
        assert entry["phase"] == "STOP"
        assert entry["session"] == "s1"
        assert entry["project"] == "testproj"

    def test_skips_duplicate_complete(self, make_workspace) -> None:
        """Pre-populated COMPLETE marker for same session_id is not duplicated."""
        ws: Workspace = make_workspace(
            registry_entries=[
                {"session_id": "s1", "project": "testproj", "ts": "2026-03-08T00:00:00Z"}
            ],
            session_notes={
                "testproj": "# Session Notes\n\n---\n# Session: 2026-03-08T00:00:00Z | ID: s1 | Status: COMPLETE\n"
            },
        )

        stdout, stderr, rc = _run_stop("s1", ws.root)

        assert rc == 0
        content = (ws.notes_dir / "testproj.md").read_text()
        # Count occurrences of COMPLETE markers for session s1
        marker_count = content.count("ID: s1 | Status: COMPLETE")
        assert marker_count == 1, f"Expected exactly 1 COMPLETE marker, found {marker_count}"

    def test_registry_miss_falls_back_to_active(self, make_workspace) -> None:
        """Empty registry with one active project 'alpha' → notes for 'alpha'."""
        ws: Workspace = make_workspace(
            projects=[
                {
                    "name": "alpha",
                    "path": "./projects/alpha",
                    "active": True,
                    "lastUsed": "2026-01-01T00:00:00Z",
                    "configured": True,
                }
            ],
            registry_entries=[],  # empty registry
        )

        stdout, stderr, rc = _run_stop("s99", ws.root)

        assert rc == 0
        notes_file = ws.notes_dir / "alpha.md"
        assert notes_file.exists(), "Notes file should be created for fallback project 'alpha'"
        content = notes_file.read_text()
        assert "COMPLETE" in content, "Notes should contain COMPLETE marker"

    def test_missing_workspace_exits_silently(self, tmp_path: Path) -> None:
        """cwd with no .rawgentic_workspace.json exits with rc=0."""
        # tmp_path is a bare directory — no workspace file
        stdout, stderr, rc = _run_stop("s1", tmp_path)

        assert rc == 0

    def test_no_project_exits_silently(self, make_workspace) -> None:
        """Workspace with no projects exits with rc=0."""
        ws: Workspace = make_workspace(
            projects=[],  # no projects at all
        )

        stdout, stderr, rc = _run_stop("s1", ws.root)

        assert rc == 0
