#!/usr/bin/env python3
"""Shared atomic file write — the ONE home for tmp+replace (#264, review C6).

Every hook that atomically writes a file routes through `atomic_write_text`;
do not reimplement mkstemp/os.replace inline (that is exactly the duplication
this module removed — seven divergent copies, two of them weaker).

Contract:
- Crash-safe: a reader sees the old file or the complete new one, never a
  partial write (tempfile in the SAME directory + `os.replace`).
- No stray temp: the temp file is unlinked on ANY failure (`BaseException`,
  so KeyboardInterrupt/SystemExit can't leak one either).
- Symlink-safe temp: `mkstemp` creates with O_CREAT|O_EXCL, so a symlink
  planted at the temp name makes creation fail instead of following it.
- Fail-mode: fail-open by re-raising — the CALLER owns error policy (some
  sites fail-closed on OSError, some log-and-continue); this helper never
  swallows the error itself.
"""
import os
import tempfile
from pathlib import Path


def atomic_write_text(path, text, *, prefix=".atomic-", suffix=".tmp",
                      mkdir=False, encoding="utf-8"):
    """Atomically replace `path` with `text`.

    `prefix`/`suffix` name the temp file (keep a site-specific prefix where a
    test observes stray-temp absence for that site). `mkdir=True` creates the
    parent directory first. Raises the underlying OSError on failure — after
    unlinking the temp.
    """
    p = Path(path)
    if mkdir:
        p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=prefix, suffix=suffix)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
        os.replace(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
