#!/usr/bin/env python3
"""Session notes size handler — trims oversized notes files, non-destructively.

Usage: notes-size-handler.py <notes_file> [<notes_file> ...] [--session-id ID]

Accepts multiple files in one invocation (#269) so session-start's cost does
not scale with project count; per-file failures are isolated (a broken file
yields its own error result, the rest are still processed).

When a session notes file exceeds THRESHOLD_CHARS characters:
1. Write the content being cut to `.notes-archive/<file>.<ts>.archive.md`
   (create-only, in a dot-directory outside the `*.md` glob)
2. Trim to the most recent KEEP_LINES lines, further capped at KEEP_CHARS
3. Write the result atomically (tempfile + os.replace)

FAIL MODE: **fail-CLOSED** (#847). If the archive cannot be written, the trim
does NOT happen and the original file is left byte-identical. Destroying a file
is worse than failing to shrink it. This is deliberately the opposite of the
convenience hooks' fail-open posture: this is a destruction boundary, and the
repo convention (CLAUDE.md §3) is that a boundary which cannot evaluate must
fail closed.

Some files are NEVER trimmed, matched by NAME (see EXCLUDED_SUFFIXES). Decision
logs record reasoning whose OLDEST entries are the valuable ones, so the tail-
keeping strategy here is exactly wrong for them. Before #847 they were spared
only when their filename happened to contain a dot (PROJECT_NAME_RE below) —
an accident, not a guarantee. The name list is the guarantee.

Called by: session-start Section 2 (on startup and compact events)

Always exits 0 on non-fatal errors. Outputs JSON result to stdout.
"""

import argparse
import fcntl
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_write_lib import atomic_write_text  # noqa: E402

# Characters, not lines (#847). Lines were an inverted proxy for the context
# cost this hook exists to bound: measured on the pre-fix handler, a 100-line
# 2.0 MB file was spared while an 801-line 2.4 KB file was destroyed.
THRESHOLD_CHARS = 64_000
KEEP_LINES = 200
KEEP_CHARS = 16_000

# Warn (never fail) once the archives beside a file get large. This keeps the
# compounding failure visible: disk fills -> archive write fails -> fail-closed
# -> the file is never trimmed again. A numeric retention policy is deliberately
# not implemented here; see the #847 PR's deferred-findings section.
ARCHIVE_WARN_BYTES = 50 * 1024 * 1024

# Archives live in this dot-directory beside the notes, outside the *.md glob.
ARCHIVE_DIRNAME = ".notes-archive"

# Files that are never trimmed, matched by name (#847).
EXCLUDED_SUFFIXES = (".archive.md", "-autorun-log.md", ".handoff.md")

# Must match archive-notes.py validation
PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def is_excluded(path: Path) -> bool:
    """True when this file must never be trimmed, decided by NAME alone.

    Name-based on purpose: it holds even if PROJECT_NAME_RE is ever relaxed,
    and it is what the tests pin.
    """
    return path.name.endswith(EXCLUDED_SUFFIXES)


def select_tail(lines: list[str]) -> list[str]:
    """The tail to keep: last KEEP_LINES lines, then capped to KEEP_CHARS.

    `lines` carries its own line terminators (``splitlines(keepends=True)``), so
    the character total is exact — counting a separator per line would
    over-count by one and drop a line that fits precisely at KEEP_CHARS.

    Whole lines only — a line is never split. A single line longer than
    KEEP_CHARS is kept intact rather than mangled.
    """
    kept = lines[-KEEP_LINES:]
    while len(kept) > 1 and sum(len(ln) for ln in kept) > KEEP_CHARS:
        kept.pop(0)
    return kept


def _fsync_dir(directory: Path) -> None:
    """fsync a directory so a newly created entry survives a crash. Best effort."""
    try:
        dfd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def archive_dir(path: Path) -> Path:
    """Where `path`'s archives live: a dot-directory beside it.

    Deliberately NOT alongside the notes files. `session-start` globs
    `session_notes/*.md`, so an archive written next to its source would be
    re-discovered and re-trimmed on the next startup — archives of archives.
    A dot-directory is outside that glob by construction, which is a stronger
    guarantee than the name-based EXCLUDED_SUFFIXES list (kept as defence in
    depth). It also isolates an archive-write failure from the notes write,
    which is what makes the fail-closed guard testable.
    """
    return path.parent / ARCHIVE_DIRNAME


def write_archive(path: Path, text: str) -> Path:
    """Create-only archive write. An existing archive is NEVER replaced.

    `atomic_write_lib.atomic_write_text` is deliberately not used here: it ends
    in `os.replace` (atomic_write_lib.py:48), so two trims landing in the same
    clock second would have the second silently destroy the first archive —
    which would defeat the entire point of archiving.
    """
    adir = archive_dir(path)
    adir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for n in range(1000):
        stamp = ts if n == 0 else f"{ts}-{n}"
        cand = adir / f"{path.name}.{stamp}.archive.md"
        try:
            fd = os.open(str(cand), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # fsync the DIRECTORY too: on several filesystems the file's contents can
        # be durable while its directory entry is not, which would let the
        # truncation survive a crash that lost the archive.
        _fsync_dir(adir)
        return cand
    raise OSError(f"could not find a free archive name for {path.name}")


def warn_if_archives_large(path: Path) -> None:
    """Loud stderr warning when archives beside `path` grow large. Never raises."""
    try:
        total = sum(
            p.stat().st_size for p in archive_dir(path).glob("*.archive.md")
        )
    except OSError:
        return
    if total >= ARCHIVE_WARN_BYTES:
        print(
            f"notes-size-handler: WARNING archives in {archive_dir(path)} total "
            f"{total // (1024 * 1024)} MB. Trimming fails CLOSED, so a full disk "
            f"will stop trimming entirely. Prune old *.archive.md files.",
            file=sys.stderr,
        )


def _render(project: str, char_count: int, archive_name: str,
            kept: list[str]) -> str:
    """The exact content a trim would write. Shared by the real write and the
    prospective-size check so the two can never disagree."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = (
        f"# Session Notes -- {project}\n"
        f"\n"
        f"<!-- Trimmed from {char_count} chars at {ts}; "
        f"cut content archived to {archive_name} -->\n"
        f"\n"
    )
    out = header + "".join(kept)
    return out if out.endswith("\n") else out + "\n"


def trim_notes(path: Path, session_id: str = "unknown") -> dict:
    """Check and trim a notes file if it exceeds THRESHOLD_CHARS.

    Uses fcntl.flock for exclusive access during read+write to prevent
    data loss from concurrent appends by active sessions.
    """
    if not path.exists():
        return {"trimmed": False, "reason": "file_not_found"}

    # Only ever touch markdown notes. Handed anything else — most importantly a
    # path inside the decisions store — refuse. Found by this fix's own
    # integration test: without it, `notes-size-handler.py decisions/x.jsonl`
    # happily destroyed the append-only store it exists to protect.
    if path.suffix != ".md" or path.parent.name == "decisions":
        return {"trimmed": False, "reason": "not_a_notes_file"}

    if is_excluded(path):
        return {"trimmed": False, "reason": "excluded"}

    # Validate project name from filename stem
    project = path.stem
    if not PROJECT_NAME_RE.match(project):
        return {"trimmed": False, "reason": "invalid_project_name"}

    if path.stat().st_size < THRESHOLD_CHARS:
        # Bytes >= chars for UTF-8, so a small file cannot be over the char
        # threshold — cheap pre-check that avoids reading most files at all.
        return {"trimmed": False, "reason": "under_threshold"}

    # Acquire exclusive lock before reading (hold through write)
    fd = os.open(str(path), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        # dup() so the with-block close doesn't release the original fd's lock
        # newline="" disables universal-newline translation. Without it a CRLF
        # file is silently normalised: char_count under-counts what is actually
        # stored, and the archive does not byte-preserve the content it exists
        # to preserve.
        with os.fdopen(os.dup(fd), "r", encoding="utf-8", newline="") as f:
            content = f.read()

        char_count = len(content)
        if char_count <= THRESHOLD_CHARS:
            return {"trimmed": False, "char_count": char_count}

        # keepends: each element carries its own terminator, so join is exact.
        lines = content.splitlines(keepends=True)
        kept = select_tail(lines)
        cut = lines[: len(lines) - len(kept)]

        # Measure what the file would ACTUALLY become, header included. Comparing
        # only the kept tail misses the case where tail + header crosses back
        # over the threshold: the trim "succeeds", the file stays oversized, and
        # the next session archives the header and does it again, forever.
        prospective = _render(project, char_count, "x" * 40, kept)
        if len(prospective) > THRESHOLD_CHARS:
            print(
                f"notes-size-handler: {path} cannot be reduced below "
                f"{THRESHOLD_CHARS} chars without splitting a line "
                f"(smallest achievable is {len(prospective)} chars). Not trimming.",
                file=sys.stderr,
            )
            return {"trimmed": False, "reason": "irreducible",
                    "char_count": char_count}

        if not cut:
            # One logical line longer than the threshold. There is nothing to
            # cut, so trimming would only prepend a header and make the file
            # BIGGER while reporting success — every run, forever.
            return {"trimmed": False, "reason": "nothing_to_cut",
                    "char_count": char_count}

        # Archive FIRST, and fail CLOSED if it does not land (#847).
        try:
            archive = write_archive(path, "".join(cut))
        except OSError as exc:
            print(
                f"notes-size-handler: REFUSING to trim {path} — archive write "
                f"failed ({exc}). The file is unchanged.",
                file=sys.stderr,
            )
            return {"trimmed": False, "reason": "archive_failed"}

        new_content = _render(project, char_count, archive.name, kept)

        # Atomic write via the shared helper (#264). fsync: the archive is
        # already durable, so the replacement must be too — otherwise a crash
        # can make the truncation survive while the archive does not.
        atomic_write_text(path, new_content, fsync=True)
        warn_if_archives_large(path)

        return {
            "trimmed": True,
            "char_count": char_count,
            "kept_lines": len(kept),
            "archive": archive.name,
            "project": project,
        }
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description="Trim oversized session notes")
    parser.add_argument("notes_file", nargs="+",
                        help="Path(s) to session notes .md file(s)")
    parser.add_argument("--session-id", default="unknown", help="Current session ID")
    args = parser.parse_args()

    # One JSON result line per file, in argument order; a failing file emits
    # its own error result and never blocks the rest (#269).
    for notes_file in args.notes_file:
        try:
            result = trim_notes(Path(notes_file), session_id=args.session_id)
            print(json.dumps(result))
        except Exception:
            # Guarantee exit 0 on all non-fatal errors
            print(json.dumps({"trimmed": False, "reason": "error"}))


if __name__ == "__main__":
    main()
