"""#855: the POSIX platform gate.

The admission journal's atomicity guarantees are not portable claims — they rest on four concrete
primitives:

* ``fcntl.flock`` — the exclusive lock that makes check-then-append indivisible
* ``os.fsync`` — durability, without which a lost reservation hands back a FREE wave
* ``O_NOFOLLOW`` — refusing a symlinked leaf
* ``os.open(..., dir_fd=...)`` — opening relative to a trusted directory descriptor rather than
  re-resolving a pathname that a concurrent process can swap

Where any of these is absent the correct behaviour is to STOP, not to fall back: a silent
degradation to a non-atomic path is exactly the failure mode this whole issue exists to remove. So
the gate raises, names every missing primitive at once, and carries the shipped taxonomy's exit 5
``platform_unsupported``.

Scope, stated honestly (design §18): this project is POSIX-only. CI runs Ubuntu and every fleet
host is Linux or macOS; there is no Windows lane. The gate is what turns that from an assumption
into an enforced, discoverable requirement.
"""
from __future__ import annotations

import fcntl
import os
from typing import List


class PlatformUnsupported(RuntimeError):
    """A required POSIX primitive is unavailable. Fail-loud; never degrade to a non-atomic path."""

    #: the shipped exit taxonomy — 5 is "internal", where an unrunnable environment belongs
    exit_code = 5
    error_code = "platform_unsupported"


def missing_primitives() -> List[str]:
    """Names of the required primitives this interpreter/platform does not provide.

    Pure: returns a list so a caller can report every gap at once. Attribute lookups are done
    dynamically (``getattr`` on the module) so a test can remove one and observe the gate react —
    a gate that cached them at import time could not be tested at all.
    """
    missing: List[str] = []
    if not hasattr(fcntl, "flock"):
        missing.append("fcntl.flock")
    if not hasattr(os, "fsync"):
        missing.append("os.fsync")
    if not hasattr(os, "O_NOFOLLOW"):
        missing.append("O_NOFOLLOW")
    # `supports_dir_fd` is the documented capability set; membership of os.open is what the
    # journal's containment opens actually need.
    if os.open not in getattr(os, "supports_dir_fd", set()):
        missing.append("os.open dir_fd")
    return missing


def assert_posix_primitives() -> None:
    """Raise :class:`PlatformUnsupported` naming every missing primitive, or return silently."""
    missing = missing_primitives()
    if missing:
        raise PlatformUnsupported(
            "admission journal requires POSIX primitives that are unavailable here: "
            + ", ".join(missing)
            + " — refusing rather than degrading to a non-atomic path (#855)")
