#!/usr/bin/env python3
"""Shared atomic file write — the one home for python tmp+replace (#264, C6).

Every python hook that atomically writes a file routes through
`atomic_write_text`; do not reimplement mkstemp/os.replace inline (that is
exactly the duplication this module removed — nine divergent copies, three of
them weaker). Known deliberate exclusion: the `python3 -c` registry-write
snippet embedded in `hooks/session-start` (can't import from an inline -c
string; slated for consolidation with the other session-start spawns, review
child 4d).

Contract:
- Crash-safe: a reader sees the old file or the complete new one, never a
  partial write (tempfile in the SAME directory + `os.replace`).
- No stray temp: the temp file is unlinked on ANY failure (`BaseException`,
  so KeyboardInterrupt/SystemExit can't leak one either).
- Symlink-safe temp: `mkstemp` creates with O_CREAT|O_EXCL, so a symlink
  planted at the temp name makes creation fail instead of following it.
- Fail-mode: policy-neutral — the helper re-raises (never swallows), so the
  CALLER owns fail-open vs fail-closed (some sites fail-closed on OSError,
  some log-and-continue).
"""
import os
import tempfile
from pathlib import Path


def atomic_write_text(path, text, *, prefix=".atomic-", suffix=".tmp",
                      mkdir=False, encoding="utf-8", fsync=False):
    """Atomically replace `path` with `text`.

    `prefix`/`suffix` name the temp file (keep a site-specific prefix where a
    test observes stray-temp absence for that site). `mkdir=True` creates the
    parent directory first. `fsync=True` flushes the temp to disk before the
    rename (crash-durability, e.g. headless suspend state). Raises the
    underlying OSError on failure — after unlinking the temp.
    """
    p = Path(path)
    if mkdir:
        p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=prefix, suffix=suffix)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
