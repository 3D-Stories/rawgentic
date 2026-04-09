#!/usr/bin/env python3
"""Session notes size handler — trims oversized notes files.

Usage: notes-size-handler.py <notes_file> [--session-id ID] [--port PORT]

When a session notes file exceeds THRESHOLD lines (800):
1. Optionally POST full content to memorypalace /ingest (best-effort)
2. Trim to keep the most recent KEEP_LINES (200) lines
3. Write result atomically (tempfile + os.replace)

Called by: session-start Section 2a (on startup and compact events)

Always exits 0 on non-fatal errors. Outputs JSON result to stdout.
"""

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

THRESHOLD = 800
KEEP_LINES = 200

# Must match archive-notes.py validation
PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def count_lines(path: Path) -> int:
    """Count lines in a file without reading entire content into memory."""
    with open(path) as f:
        return sum(1 for _ in f)


def try_ingest(content: str, project: str, session_id: str, port: int) -> bool:
    """Best-effort POST to memorypalace /ingest. Returns True on success."""
    try:
        payload = json.dumps({
            "project": project,
            "session_id": session_id,
            "notes": content,
            "source": "size-handler",
        }).encode("utf-8")
        req = Request(
            f"http://localhost:{port}/ingest",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urlopen(req, timeout=2)
        return True
    except (URLError, OSError, ValueError):
        return False


def trim_notes(
    path: Path,
    session_id: str = "unknown",
    port: int = 9077,
) -> dict:
    """Check and trim a notes file if it exceeds THRESHOLD.

    Uses fcntl.flock for exclusive access during read+write to prevent
    data loss from concurrent appends by active sessions.
    """
    if not path.exists():
        return {"trimmed": False, "reason": "file_not_found"}

    # Validate project name from filename stem
    project = path.stem
    if not PROJECT_NAME_RE.match(project):
        return {"trimmed": False, "reason": "invalid_project_name"}

    line_count = count_lines(path)
    if line_count <= THRESHOLD:
        return {"trimmed": False, "line_count": line_count}

    # Acquire exclusive lock before reading (hold through write)
    fd = os.open(str(path), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(os.dup(fd), "r") as f:
            content = f.read()

        lines = content.split("\n")
        # Re-check line count under lock (may have changed)
        # Use count("\n") for consistency with count_lines() / wc -l
        line_count = content.count("\n")
        if line_count <= THRESHOLD:
            return {"trimmed": False, "line_count": line_count}

        # Try memorypalace ingestion (best-effort, before trim)
        ingested = try_ingest(content, project, session_id, port)

        # Keep last KEEP_LINES
        kept = lines[-KEEP_LINES:]

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        header = (
            f"# Session Notes -- {project}\n"
            f"\n"
            f"<!-- Trimmed from {line_count} lines at {ts} -->\n"
            f"\n"
        )
        new_content = header + "\n".join(kept)
        if not new_content.endswith("\n"):
            new_content += "\n"

        # Atomic write: tempfile in same directory + os.replace
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w") as f:
                f.write(new_content)
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        return {
            "trimmed": True,
            "line_count": line_count,
            "kept_lines": KEEP_LINES,
            "ingested": ingested,
            "project": project,
        }
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description="Trim oversized session notes")
    parser.add_argument("notes_file", help="Path to the session notes .md file")
    parser.add_argument("--session-id", default="unknown", help="Current session ID")
    parser.add_argument("--port", type=int, default=9077, help="Memorypalace server port")
    args = parser.parse_args()

    try:
        result = trim_notes(
            Path(args.notes_file),
            session_id=args.session_id,
            port=args.port,
        )
        print(json.dumps(result))
    except Exception:
        # Guarantee exit 0 on all non-fatal errors
        print(json.dumps({"trimmed": False, "reason": "error"}))


if __name__ == "__main__":
    main()
