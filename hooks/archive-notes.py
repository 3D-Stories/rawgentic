#!/usr/bin/env python3
"""Archive session notes to JSONL format.

Usage: archive-notes.py <notes_file> <archive_dir>

Reads a session notes markdown file, trims it, appends a JSONL entry
to archive/<project>.jsonl, and resets the notes file.

Exit 0: success, outputs JSON result
Exit 1: error, outputs JSON error
"""

import fcntl
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Project name: alphanumeric, hyphens, underscores only
PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# Collapse 3+ consecutive blank lines to 2
BLANK_COLLAPSE_RE = re.compile(r"\n{4,}")


def trim_notes(content: str) -> str:
    """Trim session notes: strip trailing whitespace per line, collapse blank lines."""
    lines = [line.rstrip() for line in content.split("\n")]
    trimmed = "\n".join(lines)
    trimmed = BLANK_COLLAPSE_RE.sub("\n\n\n", trimmed)
    return trimmed.strip()


def archive_notes(notes_file: Path, archive_dir: Path) -> dict:
    """Archive a session notes file to JSONL."""
    if not notes_file.exists():
        raise ValueError(f"Notes file does not exist: {notes_file}")

    project_name = notes_file.stem
    if not PROJECT_NAME_RE.match(project_name):
        raise ValueError(f"Invalid project name: {project_name!r}")

    content = notes_file.read_text(encoding="utf-8")
    line_count = content.count("\n")
    trimmed = trim_notes(content)

    entry = {
        "schema_version": 1,
        "archived_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_file": notes_file.name,
        "line_count": line_count,
        "note": trimmed,
        "insights": None,
    }

    entry_line = json.dumps(entry, ensure_ascii=False) + "\n"

    archive_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = archive_dir / f"{project_name}.jsonl"
    fd = os.open(str(jsonl_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, entry_line.encode("utf-8"))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    notes_file.write_text(
        f"# Session Notes -- {project_name}\n", encoding="utf-8"
    )

    return {
        "archived": True,
        "project": project_name,
        "line_count": line_count,
        "archive_file": str(jsonl_path),
    }


def main():
    if len(sys.argv) != 3:
        print(json.dumps({"error": "Usage: archive-notes.py <notes_file> <archive_dir>"}))
        sys.exit(1)

    notes_file = Path(sys.argv[1])
    archive_dir = Path(sys.argv[2])

    try:
        result = archive_notes(notes_file, archive_dir)
        print(json.dumps(result))
    except (ValueError, OSError) as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
