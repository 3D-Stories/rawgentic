"""Capture-dir discipline + atomic writes + hashing.

Adopted from the proven bench cell-runner (model_bench_lib): a `.incomplete` marker is
written FIRST and unlinked only on success (an interrupted run leaves it as a crash marker),
and every write is atomic (mkstemp in the target dir -> fsync -> os.replace) so a crash mid-write
can never leave a half-written file at the final path.

Reimplements atomic-write locally (not `hooks/atomic_write_lib`) on purpose: this package is
extraction-ready (kukakuka consumes it) and must not depend on rawgentic's `hooks/`.

Path components derived from caller data (seat / model / ids) are sanitized AND the final path is
asserted to stay within the capture root — no traversal out of the run tree.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional

INCOMPLETE = ".incomplete"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_COMPONENT = 128


def sanitize_component(name: Any) -> str:
    """Return a safe single path component. Rejects empty / all-dot names (``.``, ``..``,
    ``...``) and maps every char outside ``[A-Za-z0-9._-]`` (including ``/`` and ``\\``) to ``_``,
    so no component can escape its parent."""
    s = str(name).strip()
    if not s:
        raise ValueError("empty path component")
    s = _UNSAFE.sub("_", s)
    if not s.strip("."):  # "", ".", "..", "..." -> all dots/empty
        raise ValueError(f"unsafe path component: {name!r}")
    return s[:_MAX_COMPONENT]


def hash_text(text: str, *, algo: str = "sha256") -> str:
    """Return ``sha256:<hexdigest>`` for a text (utf-8) input."""
    h = hashlib.new(algo)
    h.update(text.encode("utf-8"))
    return f"{algo}:{h.hexdigest()}"


def hash_context(items: Optional[Iterable[str]]) -> list:
    """Hash each context item in order; empty list for None/empty."""
    return [hash_text(it) for it in (items or [])]


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically write ``text`` to ``path`` (mkstemp in the same dir -> fsync -> os.replace).
    The temp file is removed if anything fails before the rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Capture:
    """A single call's capture directory. Files written atomically; ``finalize`` clears the
    ``.incomplete`` crash marker on success."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _write(self, name: str, text: str) -> Path:
        p = self.path / name
        atomic_write_text(p, text)
        return p

    def write_input(self, prompt: str) -> Path:
        return self._write("input.md", prompt)

    def write_transport(self, stdout: str) -> Path:
        return self._write("transport.stdout.txt", stdout)

    def write_output(self, text: str) -> Path:
        return self._write("output.md", text)

    def write_stderr(self, stderr: str) -> Path:
        return self._write("stderr.txt", stderr)

    def write_observation(self, obs: dict) -> Path:
        return self._write("observation.json", json.dumps(obs, indent=2, sort_keys=True))

    def finalize(self) -> None:
        marker = self.path / INCOMPLETE
        try:
            marker.unlink()
        except FileNotFoundError:
            pass

    @property
    def incomplete(self) -> bool:
        return (self.path / INCOMPLETE).exists()


def ensure_private_dir(target: os.PathLike | str, *, exist_ok: bool = True) -> Path:
    """Create ``target`` (and any missing parents), chmod every dir this call
    CREATES to 0700 (#513): capture trees hold the raw transport envelope
    (provider session_id) — same posture as the supervisor's specs/registry
    dirs. mkdir's mode= is umask-masked, chmod is not (the supervisor.py
    pattern). Pre-existing dirs keep their mode — posture is set at creation
    time. Shared by ``create_capture`` and the supervisor's timeout path so
    the two capture-tree creation sites cannot diverge (#513 review F1)."""
    target_p = Path(target)
    created = []
    probe = target_p
    while not probe.exists():
        created.append(probe)
        probe = probe.parent
    target_p.mkdir(parents=True, exist_ok=exist_ok)
    for d in created:
        os.chmod(d, 0o700)
    return target_p


def create_capture(root: os.PathLike | str, *parts: Any) -> Capture:
    """Create a fresh capture dir ``root/<sanitized parts...>``, refusing to reuse an existing
    one (``exist_ok=False``), and write the ``.incomplete`` marker first. Raises ValueError if
    the resolved path would escape ``root``.

    Every dir this call CREATES — including the capture root itself when it
    did not pre-exist (#513 review F3) — is chmod 0700 via
    ``ensure_private_dir``; a pre-existing root or intermediate keeps its
    mode."""
    root_p = Path(root).resolve()
    safe = [sanitize_component(p) for p in parts]
    target = root_p.joinpath(*safe)
    # Containment: resolved target must stay under root.
    resolved = target.resolve()
    if resolved != root_p and root_p not in resolved.parents:
        raise ValueError(f"capture path escapes root: {target}")
    ensure_private_dir(target, exist_ok=False)
    atomic_write_text(target / INCOMPLETE, "engine invocation has not completed\n")
    return Capture(target)
