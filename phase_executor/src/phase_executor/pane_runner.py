"""#467 W4 — the in-pane emitter (extraction-clean; NO hooks import).

The tmux pane's initial process. Reads a supervisor-written FIXED spec (target, prompt,
capture identity, permit ref), calls the resolved adapter DIRECTLY (``mod.run`` with the
supervisor-fixed ``attempt_id``/``capture_root`` — the ONE authoritative observation.json at a
path the supervisor KNOWS, CF-18), then dir-fsyncs the capture dir so the atomic sentinel is
durable before the pane exits.

CF-17 kill model (2026-07-19 checkpoint decision — supersedes the design's "adapter surfaces
the provider pgid": ``mod.run`` never exposes the provider pid, so pane_runner can only reach
the provider via its OWN process tree):
- A SIGTERM handler walks this process's /proc descendant tree (PPID chain) and SIGKILLs every
  descendant — reaching the provider REGARDLESS of its process group (the adapter spawns it
  ``start_new_session=True``, so a pane-group kill alone would miss it).
- A best-effort daemon thread scans direct children for a pgid different from our own and
  surfaces it to the sibling sidecar ``<capture_dir>.provider_pgid`` (a SIBLING file, not
  inside the capture dir — writing inside would pre-create the dir and break create_capture's
  exist_ok=False). The supervisor uses it for the direct-kill path when pane_runner itself was
  SIGKILL'd; the LOAD-BEARING mechanism stays the descendant walk + the supervisor's own
  pre-kill descendant snapshot.

``RAWGENTIC_PANE_ADAPTER`` (env) swaps the adapter module by import path — a TEST seam for the
kill-tree integration tests. The process runs as the invoking user with the user's own env;
the variable grants nothing an env-setting caller could not already do.
"""
from __future__ import annotations

import importlib
import json
import os
import signal
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional

from . import contract
from .adapters import ADAPTERS
from .capture import atomic_write_text, sanitize_component

_SIGTERM_EXIT = 143  # 128 + SIGTERM
_SCAN_INTERVAL_S = 0.2


def _descendants(pid: int) -> set:
    """All live descendant pids of ``pid`` via one /proc scan (PPID chain, BFS).
    Crosses process-group AND session boundaries — exactly what the group-kill misses."""
    children: dict = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", encoding="ascii", errors="replace") as fh:
                # comm may contain spaces/parens: fields after the LAST ')' are fixed-position
                rest = fh.read().rsplit(")", 1)[1].split()
            ppid = int(rest[1])
        except (OSError, IndexError, ValueError):
            continue
        children.setdefault(ppid, []).append(int(entry))
    out: set = set()
    frontier = [pid]
    while frontier:
        p = frontier.pop()
        for c in children.get(p, ()):
            if c not in out:
                out.add(c)
                frontier.append(c)
    return out


def _kill_descendant_tree(pid: int) -> None:
    """SIGKILL every descendant of ``pid`` (deepest-last is unnecessary: SIGKILL is not
    trappable, and a re-parented orphan is caught by the reaper backstop)."""
    for p in _descendants(pid):
        try:
            os.kill(p, signal.SIGKILL)
        except OSError:
            pass


def expected_capture_dir(capture_root, run_id: str, seat: str, attempt_id: str) -> Path:
    """The EXACT dir ``create_capture(capture_root, run_id, seat, attempt_id)`` will use —
    the supervisor derives the sentinel path from this, so the two must never diverge."""
    return (Path(capture_root).resolve()
            / sanitize_component(run_id) / sanitize_component(seat) / sanitize_component(attempt_id))


def sidecar_path(capture_dir: Path) -> Path:
    return capture_dir.with_name(capture_dir.name + ".provider_pgid")


def _profile_from_spec(d: dict) -> contract.LaunchProfile:
    prof = contract.LaunchProfile(
        session_policy=d["session_policy"],
        mutating=bool(d["mutating"]),
        worktree=d.get("worktree"),
        tool_grants=tuple(d.get("tool_grants") or ()),
        max_budget_usd=d.get("max_budget_usd"),
    )
    # effective_grants is init=False (populated only by profile_from_manifest); restore the
    # supervisor-derived value verbatim so the adapters' pre-spawn assert still binds.
    object.__setattr__(prof, "effective_grants", tuple(d.get("effective_grants") or ()))
    return prof


def _request_from_spec(spec: dict):
    from .adapters.base import AdapterRequest  # noqa: PLC0415 — after ADAPTERS to match package import order

    r = spec["request"]
    return AdapterRequest(
        seat=r["seat"],
        requested_model=r["requested_model"],
        prompt=r["prompt"],
        transport=r.get("transport", "native"),
        context=tuple(r.get("context") or ()),
        correlation_id=r.get("correlation_id"),
        effort=r.get("effort"),
        timeout=float(r.get("timeout", 300.0)),
        credential_ref=r.get("credential_ref"),
        profile=_profile_from_spec(r["profile"]),
        containment_root=r.get("containment_root"),
        resume_session_id=spec.get("resume_session_id"),
    )


def _starttime(pid: int) -> Optional[str]:
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as fh:
            return fh.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _pgid_scanner(sidecar: Path, stop: threading.Event) -> None:
    """Best-effort: scan our descendant tree (full BFS, not just direct children — a deep
    grandchild that re-groups is also surfaced) for a pgid different from ours and write it
    to the sidecar as ``<pgid> <leader-starttime>`` — the starttime is the group leader's
    /proc stat field 22, the PGID-reuse guard the supervisor verifies before any killpg."""
    own_pgid = os.getpgid(0)
    seen: Optional[int] = None
    while not stop.is_set():
        for child in _descendants(os.getpid()):
            try:
                pgid = os.getpgid(child)
            except OSError:
                continue
            if pgid != own_pgid and pgid != seen:
                started = _starttime(pgid)  # leader pid == pgid for a new session/group
                try:
                    atomic_write_text(sidecar, f"{pgid} {started}\n" if started else f"{pgid}\n")
                    seen = pgid
                except OSError:
                    pass
                break
        stop.wait(_SCAN_INTERVAL_S)


def _resolve_adapter(engine: str):
    override = os.environ.get("RAWGENTIC_PANE_ADAPTER")
    if override:
        return importlib.import_module(override)
    if engine not in ADAPTERS:
        raise KeyError(f"unknown engine {engine!r} (known: {sorted(ADAPTERS)})")
    return ADAPTERS[engine]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(argv) != 1:
            raise ValueError("usage: python -m phase_executor.pane_runner <spec.json>")
        with open(argv[0], encoding="utf-8") as fh:
            spec = json.load(fh)
        for key in ("engine", "run_id", "attempt_id", "capture_root",
                    "routing_config_digest", "request"):
            if key not in spec:
                raise KeyError(f"spec missing required field {key!r}")
        mod = _resolve_adapter(spec["engine"])
        req = _request_from_spec(spec)
        cap_dir = expected_capture_dir(
            spec["capture_root"], spec["run_id"], req.seat, spec["attempt_id"])

        def _on_sigterm(_signum, _frame):
            _kill_descendant_tree(os.getpid())
            os._exit(_SIGTERM_EXIT)

        signal.signal(signal.SIGTERM, _on_sigterm)
        stop = threading.Event()
        scanner = threading.Thread(
            target=_pgid_scanner, args=(sidecar_path(cap_dir), stop), daemon=True)
        scanner.start()
        try:
            kwargs = dict(run_id=spec["run_id"], attempt_id=spec["attempt_id"],
                          capture_root=spec["capture_root"],
                          routing_config_digest=spec["routing_config_digest"],
                          fallback_reason=spec.get("fallback_reason"))
            if spec["engine"] == "codex":
                kwargs["cwd"] = os.getcwd()  # engine._dispatch_real parity
            mod.run(req, **kwargs)
        finally:
            stop.set()
        # dir-fsync: the adapter's atomic writes fsync file CONTENT; fsyncing the directory
        # makes the observation.json ENTRY durable before the pane exits (the sentinel contract).
        try:
            fd = os.open(cap_dir, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass  # capture dir may not exist when the adapter failed pre-capture
        return 0
    except Exception:  # noqa: BLE001 — the pane's exit code IS the failure signal (fail-loud)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
