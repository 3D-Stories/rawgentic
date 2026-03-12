"""Tests for archive-notes.py — JSONL session notes archival.

Tests the standalone Python script that converts session notes markdown
to structured JSONL archive entries. Uses subprocess invocation to match
the hook testing pattern.
"""
import json
import subprocess
from pathlib import Path

from tests.hooks.conftest import HOOKS_DIR


def _run_archive(notes_file: Path, archive_dir: Path, timeout: int = 10):
    """Run archive-notes.py directly as a subprocess."""
    result = subprocess.run(
        ["python3", str(HOOKS_DIR / "archive-notes.py"), str(notes_file), str(archive_dir)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout, result.stderr, result.returncode


class TestArchiveCreation:
    def test_creates_jsonl_archive(self, tmp_path):
        notes_dir = tmp_path / "session_notes"
        notes_dir.mkdir()
        notes_file = notes_dir / "testproj.md"
        notes_file.write_text("# Notes\n" + ("x\n" * 700))
        archive_dir = notes_dir / "archive"

        stdout, stderr, rc = _run_archive(notes_file, archive_dir)
        assert rc == 0

        jsonl_file = archive_dir / "testproj.jsonl"
        assert jsonl_file.exists()

        entry = json.loads(jsonl_file.read_text().strip())
        assert entry["schema_version"] == 1
        assert entry["source_file"] == "testproj.md"
        assert entry["line_count"] == 701  # header + 700 "x" lines (wc -l semantics)
        assert entry["insights"] is None
        assert "archived_at" in entry
        assert "note" in entry

    def test_appends_to_existing_jsonl(self, tmp_path):
        notes_dir = tmp_path / "session_notes"
        notes_dir.mkdir()
        archive_dir = notes_dir / "archive"
        archive_dir.mkdir()

        # Seed existing entry
        jsonl_file = archive_dir / "testproj.jsonl"
        existing = {
            "schema_version": 1,
            "archived_at": "2026-03-01T00:00:00Z",
            "source_file": "testproj.md",
            "line_count": 650,
            "note": "old notes",
            "insights": None,
        }
        jsonl_file.write_text(json.dumps(existing) + "\n")

        # Archive new notes
        notes_file = notes_dir / "testproj.md"
        notes_file.write_text("# New Notes\n" + ("y\n" * 700))

        stdout, stderr, rc = _run_archive(notes_file, archive_dir)
        assert rc == 0

        lines = [json.loads(line) for line in jsonl_file.read_text().strip().split("\n")]
        assert len(lines) == 2
        assert lines[0]["note"] == "old notes"
        assert "New Notes" in lines[1]["note"]

    def test_creates_archive_directory(self, tmp_path):
        notes_dir = tmp_path / "session_notes"
        notes_dir.mkdir()
        notes_file = notes_dir / "testproj.md"
        notes_file.write_text("# Notes\n" + ("x\n" * 700))
        archive_dir = notes_dir / "archive"  # does not exist yet

        stdout, stderr, rc = _run_archive(notes_file, archive_dir)
        assert rc == 0
        assert archive_dir.exists()


class TestTrimming:
    def test_collapses_excessive_blank_lines(self, tmp_path):
        notes_dir = tmp_path / "session_notes"
        notes_dir.mkdir()
        notes_file = notes_dir / "testproj.md"
        content = "# Notes\n\n\n\n\n\nSection 1\n\n\n\n\n\nSection 2\n" + ("x\n" * 690)
        notes_file.write_text(content)
        archive_dir = notes_dir / "archive"

        _run_archive(notes_file, archive_dir)

        entry = json.loads((archive_dir / "testproj.jsonl").read_text().strip())
        # 5+ blank lines collapsed — no run of 4+ newlines in a row
        assert "\n\n\n\n" not in entry["note"]
        assert "Section 1" in entry["note"]
        assert "Section 2" in entry["note"]

    def test_strips_trailing_whitespace_per_line(self, tmp_path):
        notes_dir = tmp_path / "session_notes"
        notes_dir.mkdir()
        notes_file = notes_dir / "testproj.md"
        content = "# Notes   \nLine with trailing spaces   \n" + ("x\n" * 700)
        notes_file.write_text(content)
        archive_dir = notes_dir / "archive"

        _run_archive(notes_file, archive_dir)

        entry = json.loads((archive_dir / "testproj.jsonl").read_text().strip())
        for line in entry["note"].split("\n"):
            assert line == line.rstrip(), f"Line has trailing whitespace: {line!r}"


class TestNotesReset:
    def test_resets_notes_file_after_archival(self, tmp_path):
        notes_dir = tmp_path / "session_notes"
        notes_dir.mkdir()
        notes_file = notes_dir / "testproj.md"
        notes_file.write_text("# Notes\n" + ("x\n" * 700))
        archive_dir = notes_dir / "archive"

        _run_archive(notes_file, archive_dir)

        reset_content = notes_file.read_text()
        assert reset_content.startswith("# Session Notes -- testproj")
        assert len(reset_content.splitlines()) < 5


class TestValidation:
    def test_rejects_invalid_project_name(self, tmp_path):
        notes_dir = tmp_path / "session_notes"
        notes_dir.mkdir()
        notes_file = notes_dir / "bad name!.md"
        notes_file.write_text("# Notes\n" + ("x\n" * 700))
        archive_dir = notes_dir / "archive"

        stdout, stderr, rc = _run_archive(notes_file, archive_dir)
        assert rc == 1

    def test_rejects_dot_dot_project_name(self, tmp_path):
        notes_dir = tmp_path / "session_notes"
        notes_dir.mkdir()
        notes_file = notes_dir / "..evil.md"
        notes_file.write_text("# Notes\n" + ("x\n" * 700))
        archive_dir = notes_dir / "archive"

        stdout, stderr, rc = _run_archive(notes_file, archive_dir)
        assert rc == 1

    def test_nonexistent_notes_file(self, tmp_path):
        archive_dir = tmp_path / "archive"
        notes_file = tmp_path / "nonexistent.md"

        stdout, stderr, rc = _run_archive(notes_file, archive_dir)
        assert rc == 1

    def test_wrong_arg_count(self, tmp_path):
        result = subprocess.run(
            ["python3", str(HOOKS_DIR / "archive-notes.py")],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 1


class TestUnicodeHandling:
    def test_preserves_unicode_content(self, tmp_path):
        notes_dir = tmp_path / "session_notes"
        notes_dir.mkdir()
        notes_file = notes_dir / "testproj.md"
        content = "# Notes\nUnicode: 你好 🎉 café ñ\n" + ("x\n" * 700)
        notes_file.write_text(content)
        archive_dir = notes_dir / "archive"

        stdout, stderr, rc = _run_archive(notes_file, archive_dir)
        assert rc == 0

        entry = json.loads((archive_dir / "testproj.jsonl").read_text().strip())
        assert "你好" in entry["note"]
        assert "🎉" in entry["note"]
        assert "café" in entry["note"]


class TestOutputFormat:
    def test_stdout_is_valid_json(self, tmp_path):
        notes_dir = tmp_path / "session_notes"
        notes_dir.mkdir()
        notes_file = notes_dir / "testproj.md"
        notes_file.write_text("# Notes\n" + ("x\n" * 700))
        archive_dir = notes_dir / "archive"

        stdout, stderr, rc = _run_archive(notes_file, archive_dir)
        assert rc == 0

        result = json.loads(stdout.strip())
        assert result["archived"] is True
        assert result["project"] == "testproj"
        assert result["line_count"] == 701
