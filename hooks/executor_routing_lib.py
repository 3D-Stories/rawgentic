"""Executor-routing glue (#427, E4) — the FIRST consumer of ``phase_executor``.

Routes the ship / intake / plan seats through ``phase_executor.run_seat`` as a verified choke
point, gated by a per-seat ``executorRouting`` toggle in ``.rawgentic_workspace.json``. Default
per-seat state is ``inherit`` (prior behavior), so merging #427 changes NO live workflow until an
operator opts a seat in — the executor "lands behind the existing prose path; seats cut over one at
a time".

Two CLI subcommands:
  * ``resolve-seat`` — decide ``inherit`` | ``executor`` | ``driver_only`` for a seat (the WF prose,
    wired later in #417, calls this to pick route-vs-prior-behavior).
  * ``dispatch`` — run a seat through the executor with per-attempt ``check_pre`` enforcement +
    ``verify_post`` + an append-only routing-audit log.

This module lives in ``hooks/`` (NOT ``phase_executor/``) because everything here is
rawgentic-specific — the toggle, the ``.rawgentic_workspace.json`` read, and the capture/permit
directory conventions — so ``phase_executor`` stays extraction-clean for kukakuka.

Design: pure core (config parse, seat classification, path derivation) + a thin ``main(argv)``.
``phase_executor`` is imported INSIDE ``main`` (guarded) so a stale-tree / missing-dep ImportError
maps to a structured exit 5, not a bare module-load traceback (a routing boundary that cannot load
fails closed, it does not silently inherit a routed seat). ``dispatch_seat`` takes the
``phase_executor`` pieces as injected params so tests drive it with a stub dispatch — no live
provider call.

run-end ``reconcile_run`` across a whole WF run is deferred to #420 (it needs the orchestration
lifecycle's expected-seat ledger); #427 produces the receipt/observation records it will consume.
The build seat is NOT wired here and stays fail-closed in ``enforce.check_pre`` until #429.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import os
import json
import re
import shutil
import subprocess
import sys
import time
import tempfile
import types
import uuid
from pathlib import Path
from typing import Any, Callable, Final, Optional

# Sibling hook imports (hooks/*.py import each other via PYTHONPATH=hooks / sys.path.insert).
import capabilities_lib  # #445 sanctioned .rawgentic.json reader for the seat-table pointer
import complexity_gate  # #429 gate authentication for the build-dispatch path (#464 §E)
import plan_lib  # #470 §2b: parse the live plan file to mint risk_level + file_count
from model_routing_lib import _ABSENT, _load_block, _load_project_entry

# --- constants ---------------------------------------------------------------------------------
# The executor seat VOCABULARY (#464 §B, AC2): the names enforcement/naming recognises — NOT proof
# each is single-dispatchable. ``COMPETITIVE_ONLY`` seats ARE in the vocabulary but the bake-off owns
# their dispatch, so they are refused from single-dispatch executorRouting / resolve-seat / dispatch.
# The design seat's owning dispatcher is ``bakeoff_policy.py design-round`` (WF2-wired, #765);
# the refusal semantics here are unchanged — single-dispatching a competitive seat stays exit 2.
WIRED_SEATS: Final[frozenset[str]] = frozenset(
    {"intake", "analysis", "design", "plan", "build", "review", "ship", "offload"})
COMPETITIVE_ONLY: Final[frozenset[str]] = frozenset({"design"})
DRIVER_ONLY: Final[frozenset[str]] = frozenset({"merge", "ci_triage", "deploy_verify", "step16"})
VALID_MODES: Final[frozenset[str]] = frozenset({"inherit", "executor"})
SUPPORTED_VERSION: Final[int] = 1

# Exit-code taxonomy (structured {ok:false,error:{code,message,retryable}} on every non-zero).
EXIT_OK: Final[int] = 0
EXIT_ANOMALY: Final[int] = 1        # #555 reconcile verb: ledger↔audit anomalies present (not ok)
EXIT_MALFORMED: Final[int] = 2      # bad input / config / invalid seat or mode (non-retryable)
EXIT_AVAILABILITY: Final[int] = 3   # chain exhaustion / quota / timeout / availability (retryable)
EXIT_ENFORCEMENT: Final[int] = 4    # pre-check denial or requested!=actual identity breach (non-retryable)
EXIT_INTERNAL: Final[int] = 5       # audit/capture/internal/import failure (non-retryable)
EXIT_REFUSED: Final[int] = 6        # #470 §2a: canary refusal (either phase) — ADDITIVE, no renumber

# #735 F4: last-resort dispatch timeout, used ONLY when the seat declares no usable
# `bounds.timeout_s`. This was the CLI's flat `--timeout` default, which is the bug:
# `engine._effective_timeout` is `min(caller, bound)` and therefore only ever TIGHTENS,
# so a flat 300 handed every caller who did not tune the flag a sixth of the review
# seat's declared 1800 and a twelfth of build's 3600. Two of two real Step 11 reviews
# exceeded it (#719 at 788 s, #720 at 399.7 s) and would have been SIGKILLed.
DISPATCH_TIMEOUT_FALLBACK_S: Final[float] = 300.0


def resolve_dispatch_timeout(seat_entry, caller_timeout=None) -> float:
    """Resolve a dispatch timeout in SECONDS from the seat's own declared bound.

    An omitted ``--timeout`` now means "the seat's sanctioned budget", not a flat 300 s.
    An explicit caller value is returned unchanged — clamping stays the sole job of
    ``engine._effective_timeout`` (one clamp, one place), so this must not pre-empt it.

    Fail-SAFE rather than fail-closed: a seat with no usable bound keeps the historical
    300 s instead of dispatching unbounded. ``bool`` is rejected explicitly because
    ``True`` would otherwise satisfy ``isinstance(x, int)`` and become a 1-second timeout.
    """
    if caller_timeout is not None:
        return float(caller_timeout)
    bounds = ((seat_entry or {}).get("manifest") or {}).get("bounds") or {}
    ts = bounds.get("timeout_s")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0:
        return float(ts)
    return DISPATCH_TIMEOUT_FALLBACK_S

# Providers whose MUTATING path is OS-confined (contract.py "SECURITY-LAYER ASYMMETRY"): codex runs
# under Landlock workspace-write pinned to the worktree; claude has no FS sandbox, so it is absent
# until a bwrap/landlock child ships (owner decision 2026-07-20, #470). supervised_dispatch STEP 0
# refuses any mutating engine not listed here — module constant, never caller-selectable.
MUTATING_FS_SANDBOXED: Final[frozenset] = frozenset({"codex"})

# #470 §2a phase-1: the canary checks evaluable from LOCAL (staged/composed) evidence ALONE —
# run BEFORE the probe session spawns, so a bad staged config refuses before any process exists.
# Per-engine local set = that engine's policy ∩ LOCAL_EVALUABLE (8a F1/F3 — the old fixed
# claude-subset tuple previewed checks the codex policy never required and previewed none it did).
# Checks OUTSIDE this set (lane_provisioned, positive_deny) need the phase-2 probe stream; an
# engine whose whole policy is local (codex) skips the probe session entirely — require_canary
# still runs the full policy exactly once.
LOCAL_EVALUABLE_CANARY_CHECKS: Final[frozenset] = frozenset(
    {"hooks_digest", "plugin_version", "bare_absent", "codex_containment"})


def local_canary_checks(engine: str, canary_mod) -> tuple:
    """The phase-1 subset for ``engine``'s mutating policy (empty tuple for an unknown policy —
    require_canary refuses unknown policies authoritatively at STEP 5)."""
    policy = canary_mod.POLICIES.get(f"{engine}_mutating", ())
    return tuple(c for c in policy if c in LOCAL_EVALUABLE_CANARY_CHECKS)


# #556: codex_behavioral is NOT local (a probe must launch), but it is ALSO not derived from the
# claude positive-deny STREAM — it comes from a separate behavioral write-probe. Exclude it from
# both the local set and the claude-stream trigger; it has its own gate (behavioral_needed).
_BEHAVIORAL_CANARY_CHECKS: Final[frozenset] = frozenset({"codex_behavioral"})


def probe_needed(engine: str, canary_mod) -> bool:
    """True iff the engine's mutating policy has checks that need the phase-2 (claude) probe STREAM
    — NOT the behavioral write-probe, which has its own gate (behavioral_needed)."""
    policy = canary_mod.POLICIES.get(f"{engine}_mutating", ())
    return any(c not in LOCAL_EVALUABLE_CANARY_CHECKS and c not in _BEHAVIORAL_CANARY_CHECKS
               for c in policy)


def behavioral_needed(engine: str, canary_mod) -> bool:
    """#556: True iff the engine's mutating policy requires the behavioral write-probe
    (codex_behavioral) — a pre-spawn sandboxed-child that verifies an in-worktree write LANDS and an
    out-of-worktree write is BLOCKED."""
    policy = canary_mod.POLICIES.get(f"{engine}_mutating", ())
    return any(c in _BEHAVIORAL_CANARY_CHECKS for c in policy)

_UNSAFE_COMPONENT: Final[re.Pattern] = re.compile(r"[/\\]|\.\.|[\x00-\x1f]")

# #445: the routing table is resolved per-project via resolve_table() — the retired
# _ROUTING_TABLE_REL repo-relative constant only ever existed in THIS repo's layout, so any
# other project ENOENT'd. The package default now comes from phase_executor.routing.
# default_table_path(); a project override comes from .rawgentic.json phaseExecutorTable.


# --- errors ------------------------------------------------------------------------------------
class MalformedConfig(Exception):
    """A present-but-invalid executorRouting block, an unknown seat, or a path-unsafe component.

    Maps to exit 2. Distinct from an ABSENT config (which is the legitimate default -> inherit)."""


class PreCheckDenied(Exception):
    """A ``check_pre`` fail verdict before any provider call. Maps to exit 4. The denial receipt is
    already appended to the audit log when this is raised; no Observation exists (A6)."""

    def __init__(self, violations, target_identity):
        self.violations = tuple(violations)
        self.target_identity = target_identity
        super().__init__("; ".join(self.violations) or "pre-check denied")


# --- pure core ---------------------------------------------------------------------------------
def parse_executor_routing(raw: object) -> dict:
    """Turn the raw ``executorRouting`` value (from ``_load_block(..., missing=_ABSENT)``) into a
    ``{seat: mode}`` map. Distinguishes ABSENT from INVALID (V3):

    - ``raw is _ABSENT`` (key not present) -> ``{}`` (all seats inherit; fail-safe default).
    - present but NOT a dict, unsupported ``version``, ``seats`` not a dict, an unknown seat key, or
      a mode not in {inherit, executor} -> ``MalformedConfig`` (the CLI maps it to exit 2). A typo'd
      ``executor`` must fail loud, never silently run the legacy path (false-cutover).
    """
    if raw is _ABSENT:
        return {}
    if not isinstance(raw, dict):
        raise MalformedConfig(f"executorRouting is present but not an object (got {type(raw).__name__})")
    if raw.get("version") != SUPPORTED_VERSION:
        raise MalformedConfig(f"executorRouting.version must be {SUPPORTED_VERSION} (got {raw.get('version')!r})")
    seats = raw.get("seats", {})
    if not isinstance(seats, dict):
        raise MalformedConfig("executorRouting.seats must be an object")
    modes: dict = {}
    for seat, mode in seats.items():
        if seat not in WIRED_SEATS:
            raise MalformedConfig(f"executorRouting.seats has unknown seat {seat!r} (wired: {sorted(WIRED_SEATS)})")
        if seat in COMPETITIVE_ONLY:
            raise MalformedConfig(
                f"seat {seat!r} is competitive-only (bake-off owns its dispatch) — "
                f"cannot opt into single-dispatch executorRouting")
        if mode not in VALID_MODES:
            raise MalformedConfig(f"executorRouting.seats[{seat!r}] mode {mode!r} not in {sorted(VALID_MODES)}")
        modes[seat] = mode
    return modes


def classify_seat(seat: str) -> str:
    """``driver_only`` for a driver-owned stage, ``wired`` for ship/intake/plan, else raise
    (an unknown name is a caller error, not a silent inherit)."""
    if seat in DRIVER_ONLY:
        return "driver_only"
    if seat in WIRED_SEATS:
        return "wired"
    raise MalformedConfig(f"unknown seat {seat!r} (wired: {sorted(WIRED_SEATS)}; driver-only: {sorted(DRIVER_ONLY)})")


_WS_ABSENT: Final[object] = object()  # sentinel: workspace file genuinely absent (#474)


def _load_workspace_snapshot(workspace_path: str) -> object:
    """ONE ``json.load`` of the workspace per resolution (#474 S3-TOCTOU: architecture, project
    entry, and ``executorRouting`` block are all read from this single in-memory snapshot — the
    load is the configuration linearization point).

    Returns the parsed top-level dict, or ``_WS_ABSENT`` when the file genuinely does not exist
    ("not configured" — under #474 that means the EXECUTOR default, not an error). A
    present-but-corrupt/unreadable/non-object workspace raises ``MalformedConfig`` (fail-CLOSED:
    an enforcement boundary that cannot evaluate DENIES — Step-11 D3/A3 discipline)."""
    try:
        with open(workspace_path, encoding="utf-8") as f:
            ws = json.load(f)
    except FileNotFoundError:
        # 8a F4: a DANGLING SYMLINK also raises FileNotFoundError on open, but it is a
        # present-but-unreadable entry — fail CLOSED, never the executor default. Only a
        # genuinely absent directory entry (lexists False) is "not configured".
        if os.path.lexists(workspace_path):
            raise MalformedConfig(
                f"workspace {workspace_path!r} is a dangling symlink — cannot evaluate "
                f"architecture (fail-closed)") from None
        return _WS_ABSENT
    except (OSError, ValueError) as exc:
        raise MalformedConfig(
            f"workspace unreadable/corrupt — cannot evaluate architecture (fail-closed): {exc}") from exc
    if not isinstance(ws, dict):
        raise MalformedConfig(
            f"workspace top level is {type(ws).__name__}, not an object (fail-closed)")
    return ws


def resolve_architecture_from_snapshot(ws: object) -> str:
    """#474: the dispatch-architecture selector. ``ws`` is a ``_load_workspace_snapshot`` result.

    - Workspace absent, or ``defaultArchitecture`` key absent -> ``"executor"`` — THE flip (AC2):
      no config anywhere means executor.
    - Exact strings ``"executor"`` / ``"legacy"`` -> as declared.
    - Any other value/type -> ``MalformedConfig`` (a typo'd architecture must never silently pick
      a side — the ``parse_executor_routing`` false-cutover discipline, both directions).
    """
    if ws is _WS_ABSENT:
        return "executor"
    arch = ws.get("defaultArchitecture", "executor")
    if arch not in ("executor", "legacy"):
        raise MalformedConfig(
            f"defaultArchitecture must be 'executor' or 'legacy' (got {arch!r}) — "
            f"refusing to guess an architecture (fail-closed, #474)")
    return arch


def resolve_architecture(workspace_path: str) -> str:
    """#474: file-path convenience over ``resolve_architecture_from_snapshot`` (same rules)."""
    return resolve_architecture_from_snapshot(_load_workspace_snapshot(workspace_path))


def _entry_from_snapshot(ws: object, project: str) -> dict | None:
    if ws is _WS_ABSENT:
        return None
    projects = ws.get("projects")
    if not isinstance(projects, list):
        return None
    return next((p for p in projects if isinstance(p, dict) and p.get("name") == project), None)


def resolve_seat_action(seat: str, workspace_path: str, project: str) -> tuple[str, str]:
    """File-path convenience over ``resolve_seat_action_from_snapshot`` (one load)."""
    return resolve_seat_action_from_snapshot(seat, _load_workspace_snapshot(workspace_path), project)


def resolve_seat_action_from_snapshot(seat: str, ws: object, project: str) -> tuple[str, str]:
    """Return ``(action, reason)`` where action is ``inherit`` | ``executor`` | ``driver_only``.

    #474: the workspace-level ``defaultArchitecture`` selects the tier (absent -> executor — the
    flip). ``executorRouting`` seat modes are still VALIDATED but no longer select; an explicit
    mode contradicting the architecture is refused naming the offending seat (the config-level
    third of AC1 "mixed run impossible"). ``inherit`` still means the legacy Agent-tool path, so
    all consumers keep their contract. ``ws`` is a ``_load_workspace_snapshot`` result — CLI
    entry points load ONCE and thread the snapshot everywhere (8a F1: the linearization point
    is the entry-point load, not each helper's own read).

    Raises ``MalformedConfig`` on an unknown seat, a present-but-malformed config, an invalid
    architecture value, or a mixed-architecture conflict (-> exit 2)."""
    kind = classify_seat(seat)  # raises on unknown
    if kind == "driver_only":
        return "driver_only", "driver-only stage, never a seat"
    if seat in COMPETITIVE_ONLY:
        # #464 §B: design is in the vocabulary but competitive owns its dispatch — single-dispatching
        # it would bypass the bake-off. Refuse on BOTH the resolve-seat and dispatch CLI paths (they
        # share this entry), before any workspace read or provider call.
        raise MalformedConfig(
            f"seat {seat!r} is competitive-only (bake-off owns its dispatch) — "
            f"cannot single-dispatch it through the executor")
    arch = resolve_architecture_from_snapshot(ws)
    entry = _entry_from_snapshot(ws, project)
    raw = _ABSENT if entry is None else entry.get("executorRouting", _ABSENT)
    modes = parse_executor_routing(raw)  # validation unchanged; malformed still refuses
    mode = modes.get(seat)
    # Mixed-architecture config refusal (#474): an explicit seat mode that contradicts the
    # architecture is a config conflict, named so the operator sees exactly which seat blocks
    # the flip/rollback — never silently resolved either way.
    if mode == "inherit" and arch == "executor":
        raise MalformedConfig(
            f"mixed-architecture config refused: seat {seat!r} declares mode 'inherit' but the "
            f"workspace architecture is 'executor' (#474) — remove the seat mode or set "
            f"defaultArchitecture: legacy (joint rollback)")
    if mode == "executor" and arch == "legacy":
        raise MalformedConfig(
            f"mixed-architecture config refused: seat {seat!r} declares mode 'executor' but the "
            f"workspace architecture is 'legacy' (#474) — remove the seat mode or set "
            f"defaultArchitecture: executor")
    if arch == "executor":
        return "executor", "executor architecture (default since #474)"
    return "inherit", "legacy architecture (defaultArchitecture: legacy — manual rollback, #474)"


def _safe_component(name: str, label: str) -> str:
    """Reject a path-unsafe id component (``/``, ``\\``, ``..``, control chars, empty/all-dot)."""
    s = "" if name is None else str(name)
    if not s or set(s) <= {"."} or _UNSAFE_COMPONENT.search(s):
        raise MalformedConfig(f"path-unsafe {label}: {name!r}")
    return s


def resolve_repo_root(workspace_path: str, project: str) -> Path:
    """Resolve the project REPO root (base for capture/permit dirs) from the workspace config's
    ``project.path`` — NOT the workspace root (which is not a git repo, so dirs there would escape
    every ``.gitignore``; finding V1). Raises ``MalformedConfig`` if the entry/path is missing."""
    return repo_root_from_snapshot(_load_workspace_snapshot(workspace_path), workspace_path, project)


def repo_root_from_snapshot(ws: object, workspace_path: str, project: str) -> Path:
    """Snapshot variant of ``resolve_repo_root`` (8a F1: entry points load the workspace ONCE
    and derive architecture + entry + repo root from that one object)."""
    entry = _entry_from_snapshot(ws, project)
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
        raise MalformedConfig(f"cannot resolve repo root for project {project!r} (missing entry/path)")
    ws_dir = Path(workspace_path).resolve().parent
    root = (ws_dir / entry["path"]).resolve()
    # Containment (Step-11 D4): project.path is workspace-RELATIVE. An absolute path (`/etc`) or a
    # `../`-traversing one resolves OUTSIDE the workspace dir — which would write capture/permit
    # dirs (prompts + observations) to an arbitrary location. Refuse anything not under ws_dir.
    if root != ws_dir and ws_dir not in root.parents:
        raise MalformedConfig(
            f"project {project!r} path {entry['path']!r} escapes the workspace dir (absolute or ../ traversal) — refused")
    return root


@dataclasses.dataclass(frozen=True)
class ResolvedTable:
    """One resolved routing epoch + its provenance (#445).

    `source` is `"project_file"` (config-declared override) or `"package_default"`.
    Guarantee: deterministic resolution given an unchanged filesystem — each consumer
    resolves independently and holds its own pinned snapshot (the package's epoch
    discipline); no cross-consumer transaction is claimed."""
    snapshot: Any  # phase_executor.routing.RoutingSnapshot (Any: lazy package import — a concrete annotation would need a module-level phase_executor import)
    source: str
    path: Path


def resolve_table(repo_root: Path, routing_module) -> "ResolvedTable":
    """THE single seat-table resolution both consumers call (#445, AC2).

    Fail-mode: fail-CLOSED (enforcement boundary). A declared-but-unusable override is
    ``MalformedConfig`` (exit 2 at the CLI) — never a silent package fallback (the
    false-cutover class ``parse_executor_routing`` refuses). ONLY a truly-absent config
    file or an absent ``phaseExecutorTable`` section means "not configured" -> package
    default. ``routing_module`` is the caller's lazily-imported ``phase_executor.routing``
    (keeps the ``_import_phase_executor`` structured-exit discipline + testability).

    Content-level failures (schema violation, referential integrity) propagate as the
    package's ``RoutingError``/validation errors — the CLI already maps those to exit 2;
    pointer-ACCESS failures are wrapped here with the declared path named (#445 A4/P2-F2).
    """
    cfg_path = repo_root / ".rawgentic.json"
    # Entry-presence probe: lstat does NOT follow symlinks, so ONLY FileNotFoundError means
    # truly absent. A dangling symlink lstats fine here, then fails inside load_config's
    # open() -> CapabilitiesError -> MalformedConfig (fail-closed, never mistaken for absent).
    try:
        os.lstat(cfg_path)
        cfg_present = True
    except FileNotFoundError:
        cfg_present = False
    except OSError as exc:
        raise MalformedConfig(f"cannot probe project config {cfg_path}: {exc}") from exc

    declared = None
    if cfg_present:
        try:
            caps = capabilities_lib.derive_capabilities(capabilities_lib.load_config(str(cfg_path)))
        except capabilities_lib.CapabilitiesError as exc:
            raise MalformedConfig(
                f"project config {cfg_path} cannot be evaluated (fail-closed): {exc}") from exc
        declared = caps["phase_executor_table"]

    if declared is None:
        default_path = routing_module.default_table_path()
        snap = routing_module.snapshot_from_file(default_path)
        _assert_no_dead_seat(snap, routing_module, default_path)
        return ResolvedTable(snapshot=snap, source="package_default", path=default_path)

    # Canonical containment (PL-1): resolve(strict=True) follows symlinks, so an in-repo
    # symlink whose TARGET escapes repo_root is refused; a missing file / broken symlink
    # raises here (declared-but-missing is an error, never a fallback).
    candidate = repo_root / declared
    root = repo_root.resolve()
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError, ValueError) as exc:  # ValueError: embedded NUL (belt to derive's reject)
        raise MalformedConfig(
            f"phaseExecutorTable.file {declared!r} declared in {cfg_path} is not usable "
            f"({type(exc).__name__}: {exc}) — a declared override never falls back") from exc
    if resolved != root and root not in resolved.parents:
        raise MalformedConfig(
            f"phaseExecutorTable.file {declared!r} resolves to {resolved} outside the project "
            f"root {root} (symlink escape or traversal) — refused")
    if not resolved.is_file():
        raise MalformedConfig(
            f"phaseExecutorTable.file {declared!r} resolves to {resolved} which is not a "
            f"regular file — refused")
    try:
        snap = routing_module.snapshot_from_file(resolved)
    except Exception as exc:  # noqa: BLE001 — uniform exit-2 for EVERY declared-override problem
        # A DECLARED override that cannot load — unreadable (OSError), schema-invalid
        # (jsonschema.ValidationError), or semantically broken (RoutingError) — is a CONFIG
        # error: legible exit 2 naming the declared path, never the generic internal exit 5
        # (#445 A4/AC4). The package-default path above propagates unwrapped instead; at the
        # CLI the existing arms then map RoutingError -> exit 2 and OSError/ValidationError ->
        # exit 5 (8a-A1: propagation preserves the pre-#445 shipped-table mapping as-is, it
        # does not promise a uniform internal-fault class).
        raise MalformedConfig(
            f"phaseExecutorTable.file {declared!r} ({resolved}) failed to load "
            f"({type(exc).__name__}: {exc})") from exc
    _assert_no_dead_seat(snap, routing_module, resolved)
    return ResolvedTable(snapshot=snap, source="project_file", path=resolved)


def resolve_terminal_backend(repo_root: Path) -> str:
    """#638: which `TerminalBackend` the `build` seat launches under — `"tmux"` (package
    default) or `"herdr"` (config-gated). Mirrors `resolve_table`'s config-read pattern:
    an absent/missing `.rawgentic.json` means "not configured" -> `"tmux"`; a PRESENT but
    malformed `executorTerminalBackend` section fails closed (`MalformedConfig`), never a
    silent fallback to tmux."""
    cfg_path = repo_root / ".rawgentic.json"
    try:
        os.lstat(cfg_path)
        cfg_present = True
    except FileNotFoundError:
        cfg_present = False
    except OSError as exc:
        raise MalformedConfig(f"cannot probe project config {cfg_path}: {exc}") from exc
    if not cfg_present:
        return "tmux"
    try:
        caps = capabilities_lib.derive_capabilities(capabilities_lib.load_config(str(cfg_path)))
    except capabilities_lib.CapabilitiesError as exc:
        raise MalformedConfig(
            f"project config {cfg_path} cannot be evaluated (fail-closed): {exc}") from exc
    return caps["executor_terminal_backend"]


def select_launch_terminal_backend(seat_role: Optional[str], configured_backend: str) -> str:
    """#638 AC4: the build-seat-only gate decision. `"herdr"` ONLY when the seat's DECLARED
    ROLE (never its literal name — a renamed seat still gates correctly) is `"build"` AND the
    project config selected herdr; every other seat always gets `"tmux"` regardless of what
    the config says, since AC4 scopes the config-gate to the build seat only."""
    return "herdr" if seat_role == "build" and configured_backend == "herdr" else "tmux"


def _assert_no_dead_seat(snap, routing_module, path) -> None:
    """Pass (d) (#445, hooks layer by design — A2): a seat whose ENTIRE primary+chain is
    forbidden by CONTEXT-FREE forbidden_combinations rows is statically dead — fail at
    resolution, not first-dispatch. ``eligible_targets(..., author_provider=None)`` evaluates
    exactly the context-free rows (``_row_matches`` skips ``cross_model_author`` without an
    author); per-target reasons come from the PUBLIC ``target_forbidden_reason`` (P3-A1)."""
    for seat_name, seat in snap.table.get("seats", {}).items():
        if routing_module.eligible_targets(seat_name, snap, author_provider=None):
            continue
        reasons = []
        for target in (seat["primary"], *seat.get("chain", [])):
            why = routing_module.target_forbidden_reason(target, snap, author_provider=None)
            reasons.append(f"{target.get('model')!r}: {why or 'forbidden'}")
        raise MalformedConfig(
            f"routing table {path}: seat {seat_name!r} is statically dead — every target in "
            f"primary+chain is forbidden by context-free rules ({'; '.join(reasons)})")


def seed_table(dest: Path) -> Path:
    """Verbatim byte-copy of the package default table to ``dest`` (#445 B.4, for #446's
    setup flow — a write-capable context; the read-only resolve/dispatch paths never call
    this). Refuses to overwrite: #446's tweak UX owns edits. Byte equality is the seed
    guarantee; the canonical config_digest is the routing-audit guarantee."""
    pe = _import_phase_executor()
    src = pe.routing.default_table_path()
    dest = Path(dest)
    if dest.exists() or dest.is_symlink():
        raise MalformedConfig(f"seed_table: refusing to overwrite existing {dest}")
    _publish_bytes(dest, src.read_bytes(), op="seed_table")
    return dest


def _publish_bytes(dest: Path, data: bytes, *, op: str) -> None:
    """#446 P3-G3: the atomic no-clobber publish factored out of seed_table (behavior
    unchanged there) so apply-table can materialize PATCHED candidate bytes through the
    same tested machinery. mkstemp in the target dir -> os.link (FileExistsError if dest
    appeared since the caller's check — os.replace would silently clobber); temp always
    unlinked; every failure legible MalformedConfig."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except (FileExistsError, OSError) as exc:  # e.g. an existing regular file where the dir should be
        raise MalformedConfig(f"{op}: cannot create parent directory for {dest}: {exc}") from exc
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            os.link(tmp_name, dest)
        except FileExistsError as exc:
            raise MalformedConfig(f"{op}: refusing to overwrite existing {dest}") from exc
        except OSError as exc:  # hardlink-unsupported filesystem (ENOTSUP/EPERM/EMLINK) — legible, not a traceback
            raise MalformedConfig(f"{op}: cannot publish table to {dest} ({exc})") from exc
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


# --- #446: sparse-patch apply-table ---------------------------------------------------------------
_PATCH_FIELDS: Final[frozenset[str]] = frozenset({"primary", "chain"})


def _lane_for_model(table: dict, model: str) -> Optional[dict]:
    """Find the lane for a model from ANY existing row of the base table (patch values are
    model-name strings; the lane vocabulary is whatever the base table already declares —
    an unknown model fails closed rather than inventing a lane)."""
    for seat in table.get("seats", {}).values():
        for target in (seat["primary"], *seat.get("chain", [])):
            if target.get("model") == model:
                return target["lane"]
    return None


def apply_seat_patch(base_table: dict, patch: dict) -> dict:
    """Apply a sparse per-seat patch (primary/chain model names ONLY) over a deep copy of
    the base table (#446 B.2). Chain semantics: a supplied list REPLACES the whole chain;
    omission inherits; explicit [] is intentional. Fail-closed on unknown seat, unknown
    field, non-string model, or a model with no known lane in the base table."""
    import copy  # noqa: PLC0415
    if not isinstance(patch, dict):
        raise MalformedConfig(f"apply-table: patch must be a JSON object (got {type(patch).__name__})")
    if not patch:
        raise MalformedConfig("apply-table: empty patch = keep defaults; nothing to write")
    out = copy.deepcopy(base_table)
    seats = out.get("seats", {})
    for seat_name, edits in patch.items():
        if seat_name not in seats:
            raise MalformedConfig(f"apply-table: unknown seat {seat_name!r} (table has {sorted(seats)})")
        if not isinstance(edits, dict):
            raise MalformedConfig(f"apply-table: patch for seat {seat_name!r} must be an object")
        if not edits:
            raise MalformedConfig(
                f"apply-table: empty patch for seat {seat_name!r} = keep defaults; nothing to write")
        unknown = set(edits) - _PATCH_FIELDS
        if unknown:
            raise MalformedConfig(
                f"apply-table: unknown field(s) {sorted(unknown)} for seat {seat_name!r} — "
                f"only {sorted(_PATCH_FIELDS)} are editable (floor/role/manifest/policy inherit)")
        def _target(model, slot):
            if not isinstance(model, str) or not model:
                raise MalformedConfig(f"apply-table: {slot} for seat {seat_name!r} must be a model name string")
            lane = _lane_for_model(base_table, model)
            if lane is None:
                raise MalformedConfig(
                    f"apply-table: model {model!r} has no known lane in the base table — "
                    f"cannot route seat {seat_name!r} to it (add the lane via a table edit, not a patch)")
            import copy as _c  # noqa: PLC0415
            return {"model": model, "lane": _c.deepcopy(lane)}
        if "primary" in edits:
            seats[seat_name]["primary"] = _target(edits["primary"], "primary")
        if "chain" in edits:
            chain = edits["chain"]
            if not isinstance(chain, list):
                raise MalformedConfig(f"apply-table: chain for seat {seat_name!r} must be a list (whole-chain replace)")
            seats[seat_name]["chain"] = [_target(m, f"chain[{i}]") for i, m in enumerate(chain)]
    return out


def _do_apply(args) -> int:
    try:
        pe = _import_phase_executor()
    except ImportError as e:
        return _emit(_err(EXIT_INTERNAL, "phase_executor_import_failed", str(e), retryable=False))
    try:
        if args.validate_only and args.expected_candidate_digest:
            raise MalformedConfig(
                "apply-table: --expected-candidate-digest is forbidden with --validate-only "
                "(validate-only is what PRINTS the candidate digest)")
        repo_root = resolve_repo_root(args.workspace, args.project)
        # Dest CANONICAL containment FIRST — both modes reject identically (P2-A4r).
        # resolve() (non-strict — the fresh-create leaf may not exist yet) canonicalizes a
        # symlinked PARENT, so an in-repo symlink whose target escapes the root is refused —
        # the S4 discipline, mirroring the read-side resolve_table check (8a-B1: a lexical
        # normpath here was probe-bypassed via a symlinked parent dir).
        root = repo_root.resolve()
        dest = (repo_root / args.dest).resolve()
        if dest != root and not dest.is_relative_to(root):
            raise MalformedConfig(
                f"apply-table: --dest {args.dest!r} resolves outside the project root {root} — refused")
        try:
            patch = json.loads(Path(args.patch_json).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MalformedConfig(f"apply-table: cannot read patch {args.patch_json!r}: {exc}") from exc
        # Base: the RESOLVED current table (A3) or the package default under --reset-to-default.
        # rt_current is ALWAYS resolved: the re-seed dest==pointer guard needs it even when
        # the PATCH BASE is the package table (8a-A note: without this, resetting an existing
        # override could never materialize — base_rt None made the guard refuse every re-seed).
        rt_current = resolve_table(repo_root, pe.routing)
        # The TOCTOU guard ALWAYS checks the CURRENTLY-RESOLVED table (what show-table
        # displayed) — under --reset-to-default the PATCH BASE is the package table, but
        # the thing that must not have drifted since the user looked is still the current
        # resolution (diff-DF1: guarding the package digest instead both broke the
        # documented flow and left the override unguarded).
        if args.expected_digest != rt_current.snapshot.config_digest:
            raise MalformedConfig(
                f"apply-table: base table changed since shown — --expected-digest "
                f"{args.expected_digest!r} != resolved {rt_current.snapshot.config_digest!r}")
        if args.reset_to_default:
            base_snap = pe.routing.snapshot_from_file(pe.routing.default_table_path())
        else:
            base_snap = rt_current.snapshot
        candidate = apply_seat_patch(base_snap.table, patch)
        # Validate through EXACTLY the #445 load path: temp file OUTSIDE the project +
        # snapshot_from_file (schema + referential integrity), then the dead-seat pass.
        fd, tmp_name = tempfile.mkstemp(suffix=".routing-candidate.json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(candidate, f, indent=2)
                f.write("\n")
            try:
                cand_snap = pe.routing.snapshot_from_file(tmp_name)
            except Exception as exc:  # noqa: BLE001 — uniform legible exit 2 for an invalid candidate
                raise MalformedConfig(
                    f"apply-table: patched table failed validation ({type(exc).__name__}: {exc})") from exc
            _assert_no_dead_seat(cand_snap, pe.routing, "patched candidate")
            cand_bytes = Path(tmp_name).read_bytes()
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        pointer = {"version": 1, "file": os.path.relpath(dest, root)}
        if args.validate_only:
            print(json.dumps({"config_digest": cand_snap.config_digest, "pointer": pointer}, indent=2))
            return EXIT_OK
        # Materialization: bind to the Step-5-approved candidate (P3-1).
        if not args.expected_candidate_digest:
            raise MalformedConfig(
                "apply-table: materialization requires --expected-candidate-digest "
                "(the value --validate-only printed)")
        if args.expected_candidate_digest != cand_snap.config_digest:
            raise MalformedConfig(
                f"apply-table: candidate changed since validated — --expected-candidate-digest "
                f"{args.expected_candidate_digest!r} != recomputed {cand_snap.config_digest!r}")
        if dest.exists() or dest.is_symlink():
            # Re-seed: only the file the CURRENT pointer names may be replaced (P3-G4), and
            # only while its content still matches the shown base digest (A1; base_rt.path is
            # the resolved override — for a re-seed the base guard above already proved it).
            if rt_current.source != "project_file" or dest.resolve() != rt_current.path:
                raise MalformedConfig(
                    f"apply-table: --dest {args.dest!r} is not the current phaseExecutorTable file — "
                    f"refusing to overwrite (re-seed may only replace the pointed-to table)")
            fd, tmp_name = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(cand_bytes)
                os.replace(tmp_name, dest)
            except OSError as exc:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise MalformedConfig(f"apply-table: cannot replace {dest}: {exc}") from exc
        else:
            _publish_bytes(dest, cand_bytes, op="apply-table")
        print(json.dumps({"path": str(dest), "config_digest": cand_snap.config_digest,
                          "pointer": pointer}, indent=2))
        return EXIT_OK
    except MalformedConfig as e:
        return _emit(_err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False))
    except pe.routing.RoutingError as e:
        return _emit(_err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False))
    except OSError as e:
        return _emit(_err(EXIT_INTERNAL, "table_io_error", str(e), retryable=False))
    except Exception as e:  # noqa: BLE001 — never leak a bare traceback from a config-write boundary
        return _emit(_err(EXIT_INTERNAL, "internal_error", f"{type(e).__name__}: {e}", retryable=False))


def pool_signature(pool_concurrency: dict) -> str:
    """Short stable hash of the pool→concurrency map, so runs with INCOMPATIBLE pool definitions get
    separate permit namespaces (never a silent shared ceiling; finding A3).

    Named limit (Step-11 D1): keying the permit dir by the WHOLE map means any config change — even
    to an unrelated pool — mints a new namespace, so runs straddling a mid-flight routing-table edit
    can briefly each acquire a full allowance (the ceiling is not coordinated ACROSS the change). For
    rawgentic this is not a live risk: one stable table, default-inherit until #417, and #420's
    run-end reconcile records the digest per run to flag a cross-config straddle. Accepted trade-off:
    guarding the more-dangerous silent-wrong-ceiling (incompatible defs sharing a dir) over the rarer
    brief-over-allocation-during-a-table-edit."""
    return hashlib.sha256(json.dumps(pool_concurrency, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def derive_paths(repo_root: Path, project: str, run_id: str, pool_concurrency: dict) -> dict:
    """Derive the run_id-LESS ``capture_root`` (passed to BOTH run_seat and RoutingAuditLog — they
    each append run_id exactly once, so no double-nest; finding V2) and the pool-sig-keyed
    ``permits_dir``. All under the project repo's git-ignored ``.rawgentic/``.

    Returns ``{capture_root, permits_dir, pool_sig}`` (strings). Raises on a path-unsafe component."""
    _safe_component(project, "project")
    _safe_component(run_id, "run_id")
    base = Path(repo_root)
    capture_root = base / ".rawgentic" / "runs"          # run_seat + RoutingAuditLog append <run_id>
    sig = pool_signature(pool_concurrency)
    permits_dir = base / ".rawgentic" / "runtime" / "permits" / sig
    return {"capture_root": str(capture_root), "permits_dir": str(permits_dir), "pool_sig": sig}


# --- executor dispatch (phase_executor pieces injected) ----------------------------------------
def dispatch_seat(
    *,
    seat: str,
    prompt: str,
    run_id: str,
    correlation_id: Optional[str],
    author_provider: Optional[str],
    effort: Optional[str],
    timeout: float,
    context: tuple,
    snapshot,
    quota,
    audit,
    capture_root: str,
    routing,
    enforce,
    run_seat: Callable,
    dispatch_real: Callable,
    gate_decision=None,
    plan_context=None,
    quota_timeout=(),
) -> dict:
    """Run ``seat`` through the executor with per-attempt enforcement, returning a result dict with
    an ``exit`` code (see the exit taxonomy). ``phase_executor`` pieces are injected so tests drive a
    stub ``dispatch_real`` — no live provider call.

    The dispatch decorator closes over the SAME ordered ``eligible_targets`` list run_seat iterates
    (finding S1: never reconstruct the full lane from the AdapterRequest, which lacks
    provider/auth_mode/pool). It selects ``targets[i]`` by the leading ``i`` of ``attempt_id``
    (``f"{i}-..."``), calls ``check_pre`` on that exact target, appends the receipt, then dispatches;
    so every real attempt (primary + each fallback) is enforced and audited. ``verify_post`` runs
    once on the final Observation to drive the exit code.

    #464 §E — build gate: a seat whose TABLE role == ``"build"`` REQUIRES both an authenticated #429
    ``gate_decision`` and a ``plan_context``. #470 §2b: the CLI mints ``plan_context`` INTERNALLY from
    the live plan file (``mint_plan_context``) — no caller-assembled context crosses the CLI boundary;
    ``dispatch_seat`` still accepts the minted dict (and the bench/tests pass one directly). The gate
    is authenticated ONCE here (pre-loop, pre-receipt) via ``complexity_gate.verified_decision`` so a
    missing/tampered/stale gate fails closed BEFORE any receipt is minted; the launch-bound
    ``GateAttestation`` is minted PER-ATTEMPT (its ``input_digest`` binds to the exact target, which
    differs across fallbacks) and passed into ``check_pre``. Non-build seats pass ``attestation=None``
    — byte-identical to #427.
    """
    # #464 §B: a competitive-only seat (design) can never be single-dispatched — the bake-off owns
    # its dispatch. Refuse BEFORE table load / provider call (exit 2, malformed-input class).
    if seat in COMPETITIVE_ONLY:
        return _err(EXIT_MALFORMED, "competitive_only_seat",
                    f"seat {seat!r} is competitive-only (bake-off owns its dispatch) — cannot single-dispatch it",
                    retryable=False, correlation_id=correlation_id, audit_path=str(audit.path))
    # eligible_targets → snapshot.seat raises RoutingError when the table lacks this seat (a
    # stale/edited/wrong-project table). Catch it into the structured taxonomy — the dispatch path
    # must not leak a bare traceback where _do_resolve's executor branch already maps it (Step-11 A2-F1).
    try:
        targets = routing.eligible_targets(seat, snapshot, author_provider=author_provider)
        role = snapshot.seat(seat).get("role")
    except routing.RoutingError as e:
        return _err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False,
                    correlation_id=correlation_id, audit_path=str(audit.path))

    # #464 §E: authenticate the build gate ONCE (pre-loop, pre-receipt). Missing evidence is a
    # malformed input (exit 2); a tampered/stale gate is an enforcement denial (exit 4). Either way
    # no receipt is minted — the denial happens before check_pre, so no attestation exists to bind.
    gate_outcome = None
    if role == "build":
        if gate_decision is None:
            return _err(EXIT_MALFORMED, "gate_file_required",
                        f"build seat {seat!r} requires an authenticated #429 gate decision (--gate-file)",
                        retryable=False, correlation_id=correlation_id, audit_path=str(audit.path))
        if not isinstance(plan_context, dict) or not plan_context:
            return _err(EXIT_MALFORMED, "plan_context_required",
                        f"build seat {seat!r} requires a non-empty plan context (minted internally "
                        f"from --plan-file); an empty context can never silently disable the "
                        f"stale-decision defense",
                        retryable=False, correlation_id=correlation_id, audit_path=str(audit.path))
        # Step-11 diff review (reopens step6-H1): exact key-set equality — a PARTIAL context
        # (any canonical key omitted) or a smuggled extra key silently narrows the cross-check,
        # so both refuse BEFORE verification. Names keys only, never values (plan text).
        supplied = frozenset(plan_context)
        required = complexity_gate.REQUIRED_PLAN_CONTEXT_KEYS
        if supplied != required:
            missing = sorted(required - supplied)
            extra = sorted(supplied - required)
            return _err(EXIT_MALFORMED, "plan_context_incomplete",
                        f"build seat {seat!r} plan context must carry exactly {sorted(required)}; "
                        f"missing={missing} extra={extra}",
                        retryable=False, correlation_id=correlation_id, audit_path=str(audit.path))
        try:
            bakeoff = complexity_gate.verified_decision(gate_decision, expected_context=plan_context)
        except complexity_gate.GateTamperError as e:
            return _err(EXIT_ENFORCEMENT, "gate_tampered", str(e), retryable=False,
                        correlation_id=correlation_id, audit_path=str(audit.path))
        gate_outcome = "bakeoff" if bakeoff else "single"

    def wrapped_dispatch(engine, req, *, run_id, attempt_id, capture_root, digest, queued_ms, fallback_reason):
        i = int(str(attempt_id).split("-", 1)[0])
        target = targets[i]
        # #464 §E: mint the launch-bound attestation PER-ATTEMPT — input_digest binds to THIS target.
        # check_pre verifies its shape + binding + outcome; a "bakeoff" outcome or a bad digest fails
        # the verdict (receipt-only). Non-build seats pass None (byte-identical to #427).
        attestation = None
        if role == "build":
            attestation = enforce.GateAttestation(
                gate_outcome=gate_outcome,
                policy_digest=gate_decision.policy_digest,
                input_digest=enforce.launch_input_digest(seat, target, correlation_id),
            )
        receipt = enforce.check_pre(
            seat, target, snapshot,
            correlation_id=correlation_id, attempt_id=attempt_id, author_provider=author_provider,
            attestation=attestation,
        )
        audit.append_receipt(receipt)  # recorded BEFORE launch — a fail verdict must not dispatch
        if receipt.verdict == "fail":
            raise PreCheckDenied(receipt.violations, receipt.target_identity)
        obs = dispatch_real(
            engine, req, run_id=run_id, attempt_id=attempt_id, capture_root=capture_root,
            digest=digest, queued_ms=queued_ms, fallback_reason=fallback_reason,
        )
        # Stamp the lane WE dispatched on so append_observation accepts it (run_seat stamps its own
        # returned copy afterwards; this is the per-attempt audit record). Append the observation for
        # EVERY attempt — an availability-failed fallback is a legitimate record reconcile forgives.
        stamped = dataclasses.replace(obs, dispatched_lane=dict(target["lane"]))
        audit.append_observation(stamped, receipt=receipt)
        return obs  # return the UN-stamped obs; run_seat stamps dispatched_lane on its own copy

    try:
        final_obs = run_seat(
            seat, prompt, snapshot=snapshot, quota=quota, capture_root=capture_root,
            context=context, correlation_id=correlation_id, author_provider=author_provider,
            run_id=run_id, effort=effort, timeout=timeout, dispatch=wrapped_dispatch,
        )
    except PreCheckDenied as d:
        return _err(EXIT_ENFORCEMENT, "pre_check_denied", "; ".join(d.violations), retryable=False,
                    correlation_id=correlation_id, audit_path=str(audit.path))
    except routing.ChainExhausted as e:
        return _err(EXIT_AVAILABILITY, "chain_exhausted", str(e), retryable=True,
                    correlation_id=correlation_id, audit_path=str(audit.path))
    except quota_timeout as e:  # pool saturation past the timeout — a retryable transient (R1 High)
        return _err(EXIT_AVAILABILITY, "quota_timeout", str(e), retryable=True,
                    correlation_id=correlation_id, audit_path=str(audit.path))
    except (OSError, ValueError) as e:  # audit/capture write failure AFTER a possible external call
        return _err(EXIT_INTERNAL, "audit_write_failed", str(e), retryable=False,
                    correlation_id=correlation_id, audit_path=str(audit.path))
    except Exception as e:  # noqa: BLE001 — outermost dispatch guard: a schema-validation error from
        # audit append (jsonschema.ValidationError is NOT OSError/ValueError) or any other internal
        # fault must still emit a structured exit 5 with the correlation id, never a bare traceback.
        return _err(EXIT_INTERNAL, "internal_error", f"{type(e).__name__}: {e}", retryable=False,
                    correlation_id=correlation_id, audit_path=str(audit.path))

    # #733: compute process-failure evidence FIRST (rev-4 evaluation order) — every failure
    # return below is correlation-owned (run_seat dispatched it), so partial evidence rides on
    # all of them; the WINNING VERDICT is unchanged (enforcement > availability > ok).
    fail = enforce.contract.observation_process_failure(final_obs)
    pc = enforce.verify_post(final_obs)
    if not pc.ok:  # requested!=actual identity breach (non-retryable) — receipt+obs already audited
        return _attach_partial(
            _err(EXIT_ENFORCEMENT, pc.reason, f"identity breach on seat {seat!r}", retryable=pc.retryable,
                 correlation_id=correlation_id, audit_path=str(audit.path)), final_obs)
    if not pc.verified:  # ok but unverified => the chain exhausted on availability failures
        return _attach_partial(
            _err(EXIT_AVAILABILITY, "chain_exhausted_availability",
                 f"seat {seat!r} exhausted its chain on availability failures", retryable=True,
                 correlation_id=correlation_id, audit_path=str(audit.path)), final_obs)
    if fail is not None:
        # a verified identity is NOT success (#733). Retryability derives from the FAILURE
        # CLASS, not blanket-true (8a R2-H3): process-death/transport classes are positive
        # death evidence (engine-observed); parse/identity/malformed classes are definite,
        # potentially effectful failures (hermes encodes 4xx submit failures as parse_error
        # with an explicit do-not-fall-back) — a retry must not be invited for those.
        return _attach_partial(
            _err(EXIT_AVAILABILITY, f"dispatch_{fail}",
                 f"seat {seat!r} final attempt failed ({fail}) — partial output preserved; "
                 f"see partial_payload/raw_capture_path", retryable=fail in _RETRYABLE_FAILS,
                 correlation_id=correlation_id, audit_path=str(audit.path)), final_obs)
    return {
        "ok": True, "exit": EXIT_OK, "action": "executor", "seat": seat,
        "requested_model": final_obs.requested_model, "actual_model": final_obs.actual_model,
        "parse_status": final_obs.parse_status, "verified": pc.verified,
        "dispatched_lane": final_obs.dispatched_lane, "correlation_id": correlation_id,
        "audit_path": str(audit.path), "observation": final_obs.to_dict(),
    }


def build_probe_plan(hooks_registration, *, canary, mk_correlation_id) -> dict:
    """#470 §2a — script ONE probe per mutating matcher class DERIVED from the staged hooks.json
    (never invented): ``{matcher_class: {issued_tool, issued_correlation_id}}`` — exactly the seam
    ``canary_evidence.complete_evidence`` correlates against. ``issued_tool`` is the class's first
    tool; ``issued_correlation_id`` is a fresh nonce per class (the live collector bridges it to
    claude's own tool_use id by tool NAME — Task-3 delta). An empty map (no mutating classes in the
    staged snapshot) yields an empty plan → ``require_canary`` refuses ``positive_deny`` (fail-closed,
    never a false pass)."""
    plan = {}
    for matcher in sorted(canary.mutating_guard_classes(hooks_registration)):
        plan[matcher] = {"issued_tool": matcher.split("|")[0],
                         "issued_correlation_id": mk_correlation_id(matcher)}
    return plan


def _audit_canary_refusal(capture_root: str, run_id: str, payload: dict) -> None:
    """Append a durable canary-refusal record to a DEDICATED refusals log next to the routing audit
    (never the routing-audit.jsonl itself — that log fail-closed-validates only receipt/observation/
    epoch line variants, so an unknown line would break ``RoutingAuditLog.records``). Best-effort:
    an audit-write failure never masks the refusal (the structured exit-6 result IS the primary
    audit surface); it only loses the durable copy."""
    try:
        safe_run = _safe_component(run_id, "run_id")
        target = Path(capture_root) / safe_run
        target.mkdir(parents=True, exist_ok=True)
        with open(target / "canary-refusals.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except OSError:
        pass


def compose_supervised_argv(adapters, engine: str, model: str, *, effort,
                            profile, worktree: str, containment_root: str) -> list:
    """#472 D1 — per-engine supervised composition. The adapters' build signatures deliberately
    differ (codex: ``build_command(model, cwd, *, effort)`` / ``build_mutating_command(model,
    worktree, *, effort, containment_root)``; claude: ``build_command(model, *, effort,
    profile)``), so a one-shape call site TypeErrors on every supervised codex build. Select
    the composition by engine + ``profile.mutating``; an engine with no explicit rule REFUSES
    (8a R2: a signature-compatible adapter must never be silently claude-shaped past its
    engine-specific containment). The allowlist check runs BEFORE the adapter lookup
    (Step-11: a KeyError would be an unaudited internal error, not the documented refusal)."""
    if engine not in ("codex", "claude"):
        raise ValueError(
            f"compose_supervised_argv: engine {engine!r} has no supervised composition rule "
            f"(known: codex, claude) — refusing to guess a signature (#472 8a R2)")
    adapter = adapters[engine]
    if engine == "codex":
        if profile is not None and profile.mutating:
            return adapter.build_mutating_command(
                model, worktree, effort=effort, containment_root=containment_root)
        return adapter.build_command(model, worktree, effort=effort)
    return adapter.build_command(model, effort=effort, profile=profile)


_DENIAL_TOKENS: Final[tuple] = (
    "EACCES", "EPERM", "Operation not permitted", "Permission denied",
    "EROFS", "Read-only file system")


def _is_exec_event(line: str) -> bool:
    """True when the line is SHAPED like a structured codex exec event (a JSON object whose ``type``
    names a command execution/result). NOT authenticated (8a-F10): the exact codex exec-event schema
    is calibration-pending (verified against real output in #559's CELL-1) and model prose can emit a
    JSON object carrying such a ``type``, so an ``exec_event`` source label means only "event-shaped",
    never a proven OS-attested denial. This is sound ONLY because ``denial_evidence`` is ADVISORY
    calibration data that never gates a verdict (design §2.4 R5); a trusted, schema-validated
    transport is the named follow-up before this could ever become load-bearing."""
    s = line.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return False
    try:
        obj = json.loads(s)
    except (ValueError, TypeError):
        return False
    if not isinstance(obj, dict):
        return False
    kind = str(obj.get("type", "")).lower()
    return "exec" in kind or "command" in kind


def parse_denial_evidence(output: Optional[str], *, target: str) -> dict:
    """ADVISORY-ONLY (#556-F1, design §2.4) calibration parse of a codex child transcript for a
    DENIED out-of-worktree write. Reads text ALREADY in memory; writes NOTHING to disk. It is
    NEVER an input to ``outside_blocked``, the canary verdict, or any pass/fail decision — in this
    PR or by later reuse — it is capture of the real denial SHAPE, not authentication of denial.

    Prose is spoofable: the throwaway target path is disclosed in the probe prompt, so model text
    can echo ``EACCES <target>`` without any denied syscall. A line that parses as a structured
    codex exec event is ``source: exec_event``; free prose is ``source: prose``. Returns
    ``{matched, source, token, target_named, line_sha256, sanitized_line}``; ``sanitized_line``
    keeps only the denial token and the throwaway target basename (no other transcript bytes)."""
    empty = {"matched": False, "source": None, "token": None,
             "target_named": False, "line_sha256": None, "sanitized_line": None}
    if not output:
        return empty
    base = os.path.basename(target.rstrip("/")) if target else ""
    for line in output.splitlines():
        token = next((t for t in _DENIAL_TOKENS if t in line), None)
        if token is None:
            continue
        if not (target and (target in line or (base and base in line))):
            continue  # a denial token that does NOT name the throwaway target is not our evidence
        source = "exec_event" if _is_exec_event(line) else "prose"
        return {"matched": True, "source": source, "token": token, "target_named": True,
                "line_sha256": hashlib.sha256(line.encode("utf-8", "replace")).hexdigest(),
                "sanitized_line": f"{token} {base}".strip()}
    return empty


_ACCOUNT_DIGEST_PREFIX: Final[str] = "rawgentic-account-identity:v2|"


def probe_account(claude_bin: str = "claude", *, runner=subprocess.run, timeout: float = 30.0) -> dict:
    """AC2a (#559, design §2.5): observe the ACTIVE claude account identity via
    ``claude auth status --json`` — WITHOUT reading the credential store (only the CLI's own
    status view). Returns ``{status, logged_in, identity_digest, subscription_type,
    auth_method}`` with NO raw email/orgId/token in any field: identity is a domain-separated
    sha256 digest, plus non-identifying categories. Status arms (R12):
      - rc!=0 / timeout / OSError → ``unavailable`` (a read failure is NEVER an account switch);
      - non-JSON / not-an-object / non-bool loggedIn / missing identity fields → ``parse_error``;
      - valid JSON with ``loggedIn: false`` → ``logged_out`` (NO digest computed);
      - valid + ``loggedIn: true`` + nonempty email+orgId → ``ok`` (digest computed).
    The digest is RUN EVIDENCE (design R8) — a caller persists it only under the gitignored
    run dir; the committed report carries opaque labels, never a digest."""
    def _empty(status: str) -> dict:
        return {"status": status, "logged_in": False, "identity_digest": None,
                "subscription_type": None, "auth_method": None}
    try:
        proc = runner([claude_bin, "auth", "status", "--json"],
                      capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return _empty("unavailable")
    if getattr(proc, "returncode", 1) != 0:
        return _empty("unavailable")
    try:
        data = json.loads(getattr(proc, "stdout", "") or "")
    except (ValueError, TypeError):
        return _empty("parse_error")
    if not isinstance(data, dict):
        return _empty("parse_error")
    logged_in = data.get("loggedIn")
    if logged_in is False:
        out = _empty("logged_out")
        out["subscription_type"] = data.get("subscriptionType")
        out["auth_method"] = data.get("authMethod")
        return out
    if logged_in is not True:
        return _empty("parse_error")  # missing / non-bool loggedIn
    email, org_id = data.get("email"), data.get("orgId")
    if not (isinstance(email, str) and email and isinstance(org_id, str) and org_id):
        return _empty("parse_error")  # authenticated but the identity fields are absent
    # domain-separate the identity fields unambiguously (8a-L3): a raw ``email + "|" + org_id``
    # concat collides across distinct identities when a field contains "|" (e.g. ("a|b","c") vs
    # ("a","b|c")). JSON-encoding the pair makes the boundary injective.
    digest = hashlib.sha256(
        (_ACCOUNT_DIGEST_PREFIX
         + json.dumps([email, org_id], separators=(",", ":"))).encode("utf-8")).hexdigest()
    return {"status": "ok", "logged_in": True, "identity_digest": digest,
            "subscription_type": data.get("subscriptionType"),
            "auth_method": data.get("authMethod")}


def account_probe_ok_for_paid(probe: dict) -> bool:
    """R12 gate: a paid operation (or a digest compare) proceeds ONLY on a fully-authenticated
    identity — ``status == ok`` AND ``logged_in`` AND a computed digest (which is set only when
    email+orgId were both nonempty). logged_out/unavailable/parse_error all block identically."""
    return bool(probe.get("status") == "ok" and probe.get("logged_in") is True
                and probe.get("identity_digest"))


def codex_behavioral_probe(*, adapters, model: str, effort, wt_root: str,
                           runner=subprocess.run) -> dict:
    """#556 H3 — launch the codex MUTATING composition in a THROWAWAY worktree with a probe prompt
    (touch an in-worktree file + an out-of-worktree sibling), then EXTERNALLY verify the sandbox
    confined it: the in-worktree write must land, the sibling write must be blocked. Returns
    ``{"inside_written": bool, "outside_blocked": bool}`` — both true means the sandbox is proven.
    Mirrors tests/phase_executor/live/test_canary_live.py. Requires the codex CLI (RUN_LIVE); on a
    host without codex the ``runner`` raises FileNotFoundError, which propagates so the caller
    fail-closes (a codex mutating launch cannot pass the behavioral gate without a working sandbox).

    F1 (#559, design §2.4): the probe now ALSO captures ADVISORY denial evidence. It reads the
    child transcript IN MEMORY (nothing raw is persisted) and parses (``parse_denial_evidence``)
    for an OS-denial token that names the out-of-worktree target, discriminating a structured
    codex exec event (``source: exec_event``, OS-attested) from spoofable free prose
    (``source: prose``). This is calibration capture of the real denial SHAPE, NOT authentication:
    ``denial_evidence`` is NEVER an input to ``outside_blocked`` or the canary verdict
    (``canary.py`` reads only ``inside_written``/``outside_blocked``). ``outside_blocked`` stays
    absence-based (necessary-not-sufficient). Authenticating denial (a trusted independent syscall
    check) is a named follow-up; the exact codex exec-event schema is calibrated against real
    output in #559's CELL-1. This probe fail-closes on any probe error."""
    os.makedirs(wt_root, exist_ok=True)  # #556 8a F2: the worktrees tree may not exist yet on first launch
    probe_root = Path(tempfile.mkdtemp(prefix="rg-behav-", dir=wt_root))
    wt = probe_root / "wt"
    wt.mkdir(parents=True)
    sibling = probe_root / "sibling"
    sibling.mkdir()
    try:
        argv = compose_supervised_argv(
            adapters, "codex", model, effort=effort,
            profile=types.SimpleNamespace(mutating=True), worktree=str(wt), containment_root=str(probe_root))
        outside_target = f"{sibling}/outside.txt"
        prompt = ("Run exactly these shell commands and report their outcomes: "
                  f"1) touch inside.txt  2) touch {outside_target}")
        proc = runner(argv, input=prompt, capture_output=True, text=True, timeout=300, check=False)
        # F1: read the transcript IN MEMORY only (never persisted) and parse ADVISORY evidence.
        combined = f"{getattr(proc, 'stdout', '') or ''}\n{getattr(proc, 'stderr', '') or ''}"
        return {"inside_written": (wt / "inside.txt").exists(),
                "outside_blocked": not (sibling / "outside.txt").exists(),
                "denial_evidence": parse_denial_evidence(combined, target=outside_target)}
    finally:
        shutil.rmtree(probe_root, ignore_errors=True)


def supervised_dispatch(
    *,
    seat: str,
    prompt: str,
    run_id: str,
    correlation_id: Optional[str],
    effort: Optional[str],
    timeout: float,
    engine: str,
    profile,
    final_argv,
    snapshot_dir: str,
    capture_root: str,
    audit,
    canary,
    canary_evidence,
    supervisor,
    probe_session: Callable,
    provision: Callable,
    behavioral_probe: Optional[Callable] = None,  # #556: codex sandboxed write-probe (injected)
    gate_decision,
    plan_context,
    mk_nonce: Callable,
    mk_probe_cid: Callable,
    target: dict,
    snapshot,
    enforce,
    await_timeout_s: float = 3600.0,
    containment_root: Optional[str] = None,
    author_provider: Optional[str] = None,
    terminal_backend: str = "tmux",
) -> dict:
    """#470 §1/§2a — the SUPERVISED internal branch for a MUTATING seat. Runs the fail-closed
    guardrail canary strictly BEFORE the task pane exists, in this EXACT order (all in the trusted
    orchestrator-side process), then launches. ``containment_root`` is the approved root the seat
    worktree is provisioned under — codex_containment evidence (8a F1); absent for a codex engine
    ⇒ that check refuses (fail-closed):

      1. gate authentication (re-verify the #429 decision the mint already froze — fail-closed);
      2. stage-and-bind: fresh ``dispatch_nonce`` + the staged snapshot's registration digest bind
         one immutable ``LaunchComposition``;
      3. phase-1 canary — LOCAL evidence (``LOCAL_CANARY_CHECKS``); refuse before any process exists;
      4. trusted pre-spawn probe session — its OWN short-lived permit (owned inside ``probe_session``),
         probe_plan scripted from the staged hooks.json; a probe FAILURE is a refusal, never a skip;
      5. ``require_canary`` — full policy, EXACTLY ONCE, strictly before the spawn;
      6. provision the seat worktree, then ``supervisor.launch`` (identity captured in JobRegistry);
      7. ONE dispatch result — emitted only after identity capture + the phase-2 pass.

    A CanaryRefused at phase 1 OR phase 2 → ``EXIT_REFUSED`` (6) with the violations, audited, and
    NOTHING created (no task permit, no JobRecord, no worktree — ``provision`` and ``supervisor.launch``
    are never reached). TOCTOU freeze: between the require_canary pass and launch, no route
    resolution / mutable read / command rewrite occurs. Provider-touching seams (``probe_session``,
    ``supervisor``, ``provision``) are injected — CI drives stubs; the live spawn is the RUN_LIVE
    cell / #472."""
    ce = correlation_id
    audit_path = str(audit.path)

    # STEP 0 — FS-sandbox constraint (contract.py "SECURITY-LAYER ASYMMETRY", owner decision
    # 2026-07-20): only providers whose mutating path is OS-confined may dispatch a mutating
    # profile. claude has NO FS sandbox (codex is Landlock-confined), so mutating-claude refuses
    # fail-closed until a sandbox child ships and adds it to MUTATING_FS_SANDBOXED. The canary
    # verifies the hook layer is intact — it does not confine the filesystem, so it is not a
    # substitute. Distinct violation tag so the future sandbox child can lift exactly this check.
    if engine not in MUTATING_FS_SANDBOXED:
        _audit_canary_refusal(capture_root, run_id,
                              {"phase": "constraint",
                               "violations": [f"mutating_{engine}_requires_fs_sandbox"],
                               "correlation_id": ce})
        return _err(EXIT_REFUSED, "canary_refused", f"mutating_{engine}_requires_fs_sandbox",
                    retryable=False, correlation_id=ce, audit_path=audit_path)

    # STEP 1 — gate authentication. A mutating seat is always the build seat and always carries a
    # gate; a missing one is a malformed-input refusal before anything is staged.
    if gate_decision is None or not (isinstance(plan_context, dict) and plan_context):
        return _err(EXIT_MALFORMED, "gate_file_required",
                    f"mutating seat {seat!r} requires an authenticated #429 gate decision + minted "
                    f"plan context (--gate-file/--plan-file)",
                    retryable=False, correlation_id=ce, audit_path=audit_path)
    try:
        bakeoff = complexity_gate.verified_decision(gate_decision, expected_context=plan_context)
    except complexity_gate.GateTamperError as e:
        return _err(EXIT_ENFORCEMENT, "gate_tampered", str(e), retryable=False,
                    correlation_id=ce, audit_path=audit_path)
    gate_outcome = "bakeoff" if bakeoff else "single"

    # STEP 2 — stage-and-bind: one immutable composition (fresh nonce + staged-snapshot digest).
    dispatch_nonce = mk_nonce()
    try:
        snapshot_digest = canary.compute_registration_digest(snapshot_dir)
    except Exception as e:  # noqa: BLE001 — an unreadable staged snapshot is a fail-closed refusal
        _audit_canary_refusal(capture_root, run_id,
                              {"phase": "stage", "violations": ["snapshot_unreadable"],
                               "detail": f"{type(e).__name__}: {e}", "correlation_id": ce})
        return _err(EXIT_REFUSED, "canary_refused", f"snapshot_unreadable: {e}", retryable=False,
                    correlation_id=ce, audit_path=audit_path)
    composition = canary.LaunchComposition(
        provider=engine, profile=profile,
        dispatch_nonce=dispatch_nonce, snapshot_digest=snapshot_digest)

    # STEP 3 — phase-1 canary: LOCAL evidence; refuse before any process exists. The subset is
    # derived from THIS engine's policy (8a F1/F3): codex previews codex_containment+bare_absent,
    # claude previews hooks_digest+plugin_version+bare_absent.
    evidence = canary_evidence.build_local_evidence(
        snapshot_dir=snapshot_dir, composition=composition, final_argv=list(final_argv),
        containment_root=containment_root)
    local_violations = []
    for check_id in local_canary_checks(engine, canary):
        result = canary._CHECKS[check_id](evidence)  # pylint: disable=protected-access
        if result.verdict != canary.PASS:
            local_violations.append(result.violation or f"unspecified_refuse:{check_id}")
    if local_violations:
        _audit_canary_refusal(capture_root, run_id,
                              {"phase": "local", "violations": local_violations, "correlation_id": ce})
        return _err(EXIT_REFUSED, "canary_refused", "; ".join(local_violations), retryable=False,
                    correlation_id=ce, audit_path=audit_path)

    # STEP 4 — trusted pre-spawn probe session (own permit inside probe_session). Failure = refusal.
    # Skipped when the engine's WHOLE policy is locally evaluable (codex — 8a F1): no runtime
    # evidence is required, so spawning a probe would only add a refusal path the policy never
    # consults. require_canary still runs the full policy exactly once either way.
    if probe_needed(engine, canary):
        probe_plan = build_probe_plan(evidence.hooks_registration, canary=canary,
                                      mk_correlation_id=mk_probe_cid)
        try:
            stream = probe_session(composition=composition, probe_plan=probe_plan,
                                   snapshot_dir=snapshot_dir)
        except Exception as e:  # noqa: BLE001 — probe-session failure is fail-closed (refuse, never skip)
            _audit_canary_refusal(capture_root, run_id,
                                  {"phase": "probe", "violations": ["probe_session_failed"],
                                   "detail": f"{type(e).__name__}: {e}", "correlation_id": ce})
            return _err(EXIT_REFUSED, "canary_refused", f"probe_session_failed: {e}", retryable=False,
                        correlation_id=ce, audit_path=audit_path)
        evidence = canary_evidence.complete_evidence(
            evidence=evidence, stream=stream, probe_plan=probe_plan)

    # STEP 4b (#556) — behavioral write-probe: for a codex mutating launch, a pre-spawn sandboxed
    # child (the EXACT mutating composition, in a THROWAWAY worktree) must land an in-worktree write
    # and be BLOCKED on an out-of-worktree write, externally verified — before the real launch.
    # Fail-closed: a missing probe seam, a probe exception, or a non-both-true result refuses; the
    # populated evidence feeds require_canary's codex_behavioral check below.
    if behavioral_needed(engine, canary):
        if behavioral_probe is None:
            _audit_canary_refusal(capture_root, run_id,
                                  {"phase": "behavioral", "violations": ["behavioral_probe_unwired"],
                                   "correlation_id": ce})
            return _err(EXIT_REFUSED, "canary_refused", "behavioral_probe_unwired", retryable=False,
                        correlation_id=ce, audit_path=audit_path)
        try:
            behav = behavioral_probe(composition=composition, snapshot_dir=snapshot_dir)
        except Exception as e:  # noqa: BLE001 — behavioral-probe failure is fail-closed (refuse, never skip)
            _audit_canary_refusal(capture_root, run_id,
                                  {"phase": "behavioral", "violations": ["behavioral_probe_failed"],
                                   "detail": f"{type(e).__name__}: {e}", "correlation_id": ce})
            return _err(EXIT_REFUSED, "canary_refused", f"behavioral_probe_failed: {e}",
                        retryable=False, correlation_id=ce, audit_path=audit_path)
        evidence = dataclasses.replace(evidence, codex_behavioral=behav)

    # STEP 5 — require_canary: full policy, EXACTLY ONCE, strictly before the spawn.
    try:
        canary_result = canary.require_canary(composition, evidence)
    except canary.CanaryRefused as refused:
        _audit_canary_refusal(capture_root, run_id,
                              {"phase": "probe", "policy_id": refused.result.policy_id,
                               "violations": list(refused.result.violations), "correlation_id": ce})
        out = _err(EXIT_REFUSED, "canary_refused", "; ".join(refused.result.violations),
                   retryable=False, correlation_id=ce, audit_path=audit_path)
        out["canary"] = refused.result.pass_summary()
        return out
    # -- TOCTOU FREEZE: no route resolution / mutable read / command rewrite past this line --

    # STEP 5.5 — per-attempt enforcement receipt (Step-11 C1+C2): the SAME check_pre the sync path
    # runs, minted against the exact canary-bound target. The attestation carries the AUTHENTIC
    # gate outcome — check_pre's existing logic refuses a "bakeoff" outcome on a single dispatch
    # (the bake-off owns that dispatch), so a gate that mandated a bake-off can never proceed here.
    # Recorded BEFORE launch; a fail verdict never launches.
    # Step-11 re-review RH3: the trio (target/snapshot/enforce) is REQUIRED — no launch-capable
    # call can skip the receipt, and only an EXPLICIT "pass" verdict launches (positive gate).
    attestation = enforce.GateAttestation(
        gate_outcome=gate_outcome,
        policy_digest=gate_decision.policy_digest,
        input_digest=enforce.launch_input_digest(seat, target, ce))
    receipt = enforce.check_pre(
        seat, target, snapshot, correlation_id=ce, attempt_id="0-supervised",
        author_provider=author_provider, attestation=attestation)
    audit.append_receipt(receipt)
    if receipt.verdict != "pass":
        return _err(EXIT_ENFORCEMENT, "pre_check_denied", "; ".join(receipt.violations)
                    or f"non-pass verdict {receipt.verdict!r}",
                    retryable=False, correlation_id=ce, audit_path=audit_path)

    # STEP 6 — provision the seat worktree, then launch (identity captured in JobRegistry).
    identity, handle = provision()
    record = supervisor.launch(
        seat, prompt, identity=identity, handle=handle, profile=profile,
        effort=effort, timeout=timeout, target=target, author_provider=author_provider,
        receipt_nonce=receipt.nonce, correlation_id=ce,
        snapshot_dir=snapshot_dir, snapshot_digest=snapshot_digest,
        terminal_backend=terminal_backend)
    # #558 S-F6 (3-reviewer converged): ONE effective timeout — the same
    # min(caller, manifest bound) that launch writes into the pane spec also
    # clamps the supervisor await deadline, so a hung pane cannot outlive the
    # declared operational bound holding its permit + worktree.
    from phase_executor.engine import _effective_timeout, _manifest_for  # noqa: PLC0415
    eff_deadline = _effective_timeout(_manifest_for(snapshot, seat), timeout)
    state, obs = supervisor.await_job(record, timeout_s=min(await_timeout_s, eff_deadline))

    # STEP 6.5 — #472 D3: verdict-INDEPENDENT audit append, mirroring the sync path's
    # per-attempt rule ("append the observation for EVERY attempt"). Every terminal state
    # that HAS an observation lands in the routing audit, stamped with the dispatched lane
    # + this dispatch's correlation, BEFORE any verdict branching — a failed supervised job
    # must not vanish from the audit. Since #557 that is ALL four terminal states: completed
    # / completed_with_residue / timed_out / exited_no_sentinel (the supervisor emits a
    # synthetic no_response observation for the last), so the `obs is not None` guard is
    # belt-and-braces, not a semantic branch.
    if obs is not None:
        stamped = dict(obs)
        stamped["dispatched_lane"] = dict(target["lane"])
        child_cid = stamped.get("correlation_id")
        if child_cid is None:
            # a synthetic/legacy observation carries no correlation — adopt the dispatch's
            stamped["correlation_id"] = ce
        audit.append_observation(stamped, receipt=receipt)
        if child_cid is not None and child_cid != ce:
            # Step-11 wave (3× converged; supersedes the 8a overwrite): a non-matching child
            # correlation is an IDENTITY VIOLATION — audited AS-IS above (the foreign value is
            # the evidence), then REFUSED. Relabeling it would launder a stale/crossed/tampered
            # sentinel into this dispatch and let it falsely satisfy reconciliation.
            return _err(EXIT_ENFORCEMENT, "correlation_mismatch",
                        f"child observation correlation {child_cid!r} != dispatch correlation "
                        f"{ce!r} on supervised seat {seat!r} — foreign observation refused",
                        retryable=False, correlation_id=ce, audit_path=audit_path)

    # STEP 7 — one dispatch result, only after identity capture + phase-2 pass.
    # #733: everything past the correlation_mismatch refusal above is correlation-OWNED, so
    # partial evidence rides on every observation-bearing failure return (rev-4 order: evidence
    # first, verdict precedence unchanged — enforcement > availability > ok).
    def _with_partial(res: dict) -> dict:
        return _attach_partial(res, obs) if obs is not None else res
    # 8a R1-H1/R2-H1 (narrowed): an ATTESTED-WRONG identity is a billed breach and wins over
    # the state verdict — but ONLY requested_actual_mismatch. identity_missing keeps the state
    # verdict: the supervisor deliberately emits no-identity synthetic envelopes (parse_error)
    # for suspicious deaths, and treating those as breaches would turn honest-death recovery
    # into non-retryable enforcement.
    pc = enforce.verify_post(obs) if obs is not None else None
    if pc is not None and not pc.ok and pc.reason == "requested_actual_mismatch":
        return _with_partial(_err(EXIT_ENFORCEMENT, pc.reason,
                                  f"identity breach on supervised seat {seat!r}", retryable=pc.retryable,
                                  correlation_id=ce, audit_path=audit_path))
    if state != "completed":
        retryable = state in ("timed_out", "exited_no_sentinel", "quota_paused")
        detail = f"supervised seat {seat!r} ended in state {state!r}"
        if retryable:
            # #733 Step-11 R1-H1: await_job returns "timed_out" whether or not _kill_job PROVED
            # death — the fresh registry record carries quarantine_reason exactly when it did
            # not. Residue is not proven death: the ratified policy forbids inviting a retry of
            # a possibly-still-running mutation (EXIT_INTERNAL parity with completed_with_residue).
            fresh = supervisor.job_record(record)
            if fresh is not None and getattr(fresh, "quarantine_reason", None):
                retryable = False
                detail += (f" — kill unverified ({fresh.quarantine_reason}); "
                           f"residue is not proven death, no retry")
        code = EXIT_AVAILABILITY if retryable else EXIT_INTERNAL
        return _with_partial(_err(code, f"supervised_{state}", detail, retryable=retryable,
                                  correlation_id=ce, audit_path=audit_path))
    # verify_post on the final observation (Step-11 C2) — same breach semantics as the sync path:
    # an envelope with a wrong/missing identity is a NON-retryable enforcement failure; an
    # availability-shaped obs is exit 3.
    fail = enforce.contract.observation_process_failure(obs or {})
    pc = pc if pc is not None else enforce.verify_post(obs or {})
    if not pc.ok:
        return _with_partial(_err(EXIT_ENFORCEMENT, pc.reason,
                                  f"identity breach on supervised seat {seat!r}", retryable=pc.retryable,
                                  correlation_id=ce, audit_path=audit_path))
    if fail is not None:
        # #733: a completed-state envelope that fails the process predicate is NOT proven death
        # under the ratified retry policy — retryable=False; the ERROR protocol owns any retry.
        # Step-11 R1-H2: evaluated BEFORE the unverified verdict — a missing identity must never
        # make a failed envelope MORE retryable than one that attested.
        return _with_partial(_err(EXIT_AVAILABILITY, f"supervised_dispatch_{fail}",
                                  f"supervised seat {seat!r} completed with a failed envelope ({fail}) — "
                                  f"partial output preserved", retryable=False,
                                  correlation_id=ce, audit_path=audit_path))
    if not pc.verified:
        return _with_partial(_err(EXIT_AVAILABILITY, "supervised_unverified",
                                  f"supervised seat {seat!r} produced no verifiable envelope ({pc.reason})",
                                  retryable=True, correlation_id=ce, audit_path=audit_path))
    return {
        "ok": True, "exit": EXIT_OK, "action": "executor_supervised", "seat": seat,
        "state": state, "correlation_id": ce, "audit_path": audit_path,
        "canary": canary_result.pass_summary(),
        "requested_model": (obs or {}).get("requested_model"),
        "actual_model": (obs or {}).get("actual_model"),
        "dispatched_lane": dict(target["lane"]) if target else None,
        "resolution": "primary",
        "observation": obs,
    }


def resume_dispatch(
    *,
    seat: str,
    prompt: str,
    run_id: str,
    correlation_id: Optional[str],
    resume_session_id: str,
    effort: Optional[str],
    timeout: float,
    engine: str,
    profile,
    target: dict,
    snapshot,
    capture_root: str,
    audit,
    supervisor,
    provision: Callable,
    enforce,
    await_timeout_s: float = 3600.0,
    author_provider: Optional[str] = None,
) -> dict:
    """#559 AC2a (design §2.5) — resume a claude provider session through the NORMAL chokepoint
    (check_pre receipt → launch → await → Observation append → verify_post), WITHOUT the mutating
    canary machinery. Supervised CLAUDE only, NON-mutating only: a non-claude engine or a mutating
    profile refuses (EXIT_MALFORMED) — a resumed mutating job would need a fresh behavioral canary,
    out of scope here. The launch composes ``session_policy='resume'`` (carried on ``profile``) +
    the given session id; ``await_job`` is passed ``expect_session_id`` so the resumed envelope's
    ``session_id`` MUST equal the seeded id (F-h — a mismatch is a fail-loud enforcement error,
    never a silent "session preserved"). Provider-touching seams are injected; the live spawn is
    the RUN_LIVE cell (CELL-2)."""
    from phase_executor.supervisor import SupervisorError  # noqa: PLC0415
    ce = correlation_id
    audit_path = str(audit.path)
    if engine != "claude":
        return _err(EXIT_MALFORMED, "resume_engine_unsupported",
                    f"--resume-session-id is claude-only; seat {seat!r} resolved to engine {engine!r}",
                    retryable=False, correlation_id=ce, audit_path=audit_path)
    if getattr(profile, "mutating", False):
        return _err(EXIT_MALFORMED, "resume_mutating_refused",
                    f"--resume-session-id refuses a mutating profile on seat {seat!r} "
                    f"(a resumed mutating job needs a fresh behavioral canary — unsupported here)",
                    retryable=False, correlation_id=ce, audit_path=audit_path)
    # per-attempt receipt — a resume is a non-build seat, so attestation=None (byte-identical to
    # the sync non-build path). Recorded BEFORE launch; a non-pass verdict never launches.
    receipt = enforce.check_pre(
        seat, target, snapshot, correlation_id=ce, attempt_id="0-resume",
        author_provider=author_provider, attestation=None)
    audit.append_receipt(receipt)
    if receipt.verdict != "pass":
        return _err(EXIT_ENFORCEMENT, "pre_check_denied",
                    "; ".join(receipt.violations) or f"non-pass verdict {receipt.verdict!r}",
                    retryable=False, correlation_id=ce, audit_path=audit_path)
    identity, handle = provision()
    record = supervisor.launch(
        seat, prompt, identity=identity, handle=handle, profile=profile,
        effort=effort, timeout=timeout, target=target, author_provider=author_provider,
        receipt_nonce=receipt.nonce, correlation_id=ce, resume_session_id=resume_session_id)
    # F-h: the resumed envelope MUST carry the seeded session id — await_job asserts it and
    # raises on mismatch (fail-loud), never reporting a false "session preserved".
    try:
        state, obs = supervisor.await_job(
            record, timeout_s=await_timeout_s, expect_session_id=resume_session_id)
    except SupervisorError as e:
        return _err(EXIT_ENFORCEMENT, "resume_identity_mismatch", str(e),
                    retryable=False, correlation_id=ce, audit_path=audit_path)
    # verdict-independent audit append (mirror the supervised/sync per-attempt rule)
    if obs is not None:
        stamped = dict(obs)
        stamped["dispatched_lane"] = dict(target["lane"])
        child_cid = stamped.get("correlation_id")
        if child_cid is not None and child_cid != ce:
            # 8a-F9: refuse a foreign-correlation observation BEFORE appending — a mismatched child
            # envelope must never be written to the audit (a post-append refusal poisons the ledger).
            return _err(EXIT_ENFORCEMENT, "correlation_mismatch",
                        f"child observation correlation {child_cid!r} != dispatch correlation "
                        f"{ce!r} on resume seat {seat!r} — foreign observation refused",
                        retryable=False, correlation_id=ce, audit_path=audit_path)
        if child_cid is None:
            stamped["correlation_id"] = ce
        audit.append_observation(stamped, receipt=receipt)
    # #733: past the correlation_mismatch refusal above every return is correlation-owned —
    # partial evidence rides on observation-bearing failures (rev-4 evaluation order).
    def _with_partial(res: dict) -> dict:
        return _attach_partial(res, obs) if obs is not None else res
    # 8a R1-H1/R2-H1 (narrowed, mirror of supervised): attested-wrong identity wins over the
    # state verdict; identity_missing keeps it (synthetic no-identity death envelopes).
    pc = enforce.verify_post(obs) if obs is not None else None
    if pc is not None and not pc.ok and pc.reason == "requested_actual_mismatch":
        return _with_partial(_err(EXIT_ENFORCEMENT, pc.reason,
                                  f"identity breach on resume seat {seat!r}",
                                  retryable=pc.retryable, correlation_id=ce, audit_path=audit_path))
    if state != "completed":
        retryable = state in ("timed_out", "exited_no_sentinel", "quota_paused")
        detail = f"resume seat {seat!r} ended in state {state!r}"
        if retryable:
            # #733 Step-11 R1-H1 (mirror of supervised): quarantine_reason on the fresh registry
            # record marks a kill that was NOT verified — residue is not proven death, no retry.
            fresh = supervisor.job_record(record)
            if fresh is not None and getattr(fresh, "quarantine_reason", None):
                retryable = False
                detail += (f" — kill unverified ({fresh.quarantine_reason}); "
                           f"residue is not proven death, no retry")
        code = EXIT_AVAILABILITY if retryable else EXIT_INTERNAL
        return _with_partial(_err(code, f"resume_{state}", detail,
                                  retryable=retryable, correlation_id=ce, audit_path=audit_path))
    fail = enforce.contract.observation_process_failure(obs or {})
    pc = pc if pc is not None else enforce.verify_post(obs or {})
    if not pc.ok:
        return _with_partial(_err(EXIT_ENFORCEMENT, pc.reason, f"identity breach on resume seat {seat!r}",
                                  retryable=pc.retryable, correlation_id=ce, audit_path=audit_path))
    if fail is not None:
        # #733: completed-but-failed envelope is NOT proven death — retryable=False (D3 policy).
        # Step-11 R1-H2: evaluated BEFORE the unverified verdict (mirror of supervised).
        return _with_partial(_err(EXIT_AVAILABILITY, f"resume_dispatch_{fail}",
                                  f"resume seat {seat!r} completed with a failed envelope ({fail}) — "
                                  f"partial output preserved", retryable=False,
                                  correlation_id=ce, audit_path=audit_path))
    if not pc.verified:
        return _with_partial(_err(EXIT_AVAILABILITY, "resume_unverified",
                                  f"resume seat {seat!r} produced no verifiable envelope ({pc.reason})",
                                  retryable=True, correlation_id=ce, audit_path=audit_path))
    return {
        "ok": True, "exit": EXIT_OK, "action": "executor_resume", "seat": seat,
        "state": state, "correlation_id": ce, "audit_path": audit_path,
        "resume_session_id": resume_session_id,
        "requested_model": (obs or {}).get("requested_model"),
        "actual_model": (obs or {}).get("actual_model"),
        "dispatched_lane": dict(target["lane"]) if target else None,
        "observation": obs,
    }


_APPENDIX_PREFIX: Final[str] = "docs/planning/appendix/"


def _atomic_write_json(path: Path, obj) -> None:
    """Write JSON atomically (tempfile + os.replace) so a crash never leaves a torn intent.

    #571: a UNIQUE mkstemp temp in the target dir + unlink-on-exception (repo hook checklist §5) —
    replaces the fixed ``str(path)+".tmp"`` (a same-dir collision surface) and guarantees no stray
    ``*.tmp`` survives a mid-write failure. os.replace still gives a torn-file-free swap."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(obj))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _intent_semantic_error(intent, receipt_nonce: str) -> Optional[str]:
    """Step-11 adv F4 + lane R1-F4: strict shape validation for an EXISTING nonce-named intent.
    Returns a reason string when the intent is semantically corrupt, else None. Legacy (pre-#767)
    intents legitimately lack target_ref/paths_digest — but if EITHER is present, both must be
    non-empty strings (a half-bound identity is corruption, mirroring the audit writers)."""
    if not isinstance(intent, dict):
        return f"not a JSON object (got {type(intent).__name__})"
    if intent.get("receipt_nonce") != receipt_nonce:
        return (f"receipt_nonce {intent.get('receipt_nonce')!r} does not match the nonce-named "
                f"file ({receipt_nonce!r})")
    for k in ("candidate_tree_sha", "expected_target_sha"):
        v = intent.get(k)
        if not isinstance(v, str) or not v:
            return f"{k} must be a non-empty string (got {v!r})"
    new_sha = intent.get("new_sha")
    if new_sha is not None and (not isinstance(new_sha, str) or not new_sha):
        return f"new_sha must be null or a non-empty string (got {new_sha!r})"
    if not isinstance(intent.get("consumed"), bool):
        return f"consumed must be a boolean (got {intent.get('consumed')!r})"
    has_ref, has_digest = "target_ref" in intent, "paths_digest" in intent
    if has_ref != has_digest:
        return "half-bound identity: target_ref and paths_digest must appear together"
    if has_ref:
        for k in ("target_ref", "paths_digest"):
            v = intent.get(k)
            if not isinstance(v, str) or not v:
                return f"{k} must be a non-empty string (got {v!r})"
    if "expected_feature_ref" in intent:
        # #762 R5-B: optional, but when present it must be a real branch ref
        v = intent.get("expected_feature_ref")
        if not isinstance(v, str) or not v.startswith("refs/heads/"):
            return f"expected_feature_ref must be a refs/heads/-rooted string (got {v!r})"
    return None


def collect_work_product(*, run_id: str, session_name: str, target_ref: str,
                         expected_target_sha: str, kind: str,
                         registry, manager, audit, intent_dir: str,
                         correlation_id: Optional[str] = None,
                         promote_paths: Optional[list] = None,
                         expected_feature_ref: Optional[str] = None) -> dict:
    """#559 AC1 (design §2.6) — promote a completed build's appendix work product onto
    ``target_ref`` and record an audited ``work_product`` binding. TWO-PHASE, crash-recoverable,
    audit-idempotent:

      phase 1: write a durable INTENT keyed {receipt_nonce, candidate_tree_sha, expected_target_sha}
               (new_sha UNKNOWN until promote returns — F-b), then the irreversible CAS ``promote``
               scoped by ``promote_appendix_only((docs/planning/appendix/,))``; on success the
               intent is updated with the returned new_sha;
      phase 2: ``derive_work_product`` → audit-SEARCH the run's stream for an existing record
               matching {receipt_nonce, candidate_tree_sha, new_sha}; append ONLY if absent (a crash
               after append cannot duplicate on rerun — F-c) → mark the intent consumed.

    Idempotent re-run: a consumed matching intent → no-op; an unconsumed matching intent whose
    new_sha is already recorded (promote succeeded, finalize crashed) → resume phase 2 only; an
    absent/non-matching intent with a moved ref refuses loud via promote's CAS.

    #767: ``promote_paths`` scopes the promotion. ``None`` → the historical appendix-prefix
    policy, byte-identical. A non-empty list → EXACT-path policy (``promote_paths_only`` —
    never prefix matching, Step-4 pass-2 F2). The durable intent + the audited bindings carry
    the promotion identity (``target_ref`` + ``paths_digest``, binding_version=2): a re-request
    for the same receipt against a DIFFERENT target/policy refuses loud (``intent_conflict``),
    never ``already_recorded`` (pass-2 F3). Git/registry seams are injected; the live path is
    CELL-1."""
    from phase_executor.registry import handle_from_record  # noqa: PLC0415
    from phase_executor.contract import derive_work_product  # noqa: PLC0415
    from phase_executor.worktree import (  # noqa: PLC0415
        promote_appendix_only, promote_paths_only, PromotionResult, WorktreeError,
        _norm_rel_components)
    ce = correlation_id
    if kind == "code" or expected_feature_ref is not None:
        # #762 R5-B: the destination is authorized at COLLECT time — a code collect must
        # persist the feature ref the landing verb will later require byte-equal; a supplied
        # ref on any kind must be a real branch ref.
        if not isinstance(expected_feature_ref, str) or not expected_feature_ref.startswith("refs/heads/"):
            return _err(EXIT_MALFORMED, "invalid_expected_feature_ref",
                        f"collect-work-product: --kind code requires --expected-feature-ref as a "
                        f"refs/heads/-rooted branch ref (got {expected_feature_ref!r}) — the "
                        f"landing destination is authorized by this collect-time record",
                        retryable=False, correlation_id=ce)
    if promote_paths is None:
        if kind == "code":
            # Step-11 R1-F1 (flag half): code collection must never silently downgrade to the
            # appendix policy — the exact-path staging backstop is the contract, not a flag.
            return _err(EXIT_MALFORMED, "invalid_promote_paths",
                        "collect-work-product: --kind code requires at least one --promote-path "
                        "(exact-path collection; the appendix default is for docs/review/design)",
                        retryable=False, correlation_id=ce)
        path_policy = promote_appendix_only((_APPENDIX_PREFIX,))
        paths_digest = "appendix-default"
    else:
        try:
            path_policy = promote_paths_only(tuple(promote_paths))
            norm = sorted("/".join(_norm_rel_components(p, what="declared path"))
                          for p in promote_paths)
        except (ValueError, TypeError) as e:
            return _err(EXIT_MALFORMED, "invalid_promote_paths", str(e), retryable=False,
                        correlation_id=ce)
        # NUL-joined + deduped: git paths may legally contain newlines, so a "\n" join would
        # collide ["a\nb","c"] with ["a","b\nc"] (8a R1-M3); NUL can never appear in a filename.
        paths_digest = "sha256:" + hashlib.sha256(
            "\x00".join(sorted(set(norm))).encode("utf-8")).hexdigest()
    recs = [r for r in registry.by_run(run_id) if r.session_name == session_name]
    if not recs:
        return _err(EXIT_MALFORMED, "unknown_job",
                    f"collect-work-product: no job {session_name!r} in run {run_id!r}",
                    retryable=False, correlation_id=ce)
    record = recs[0]
    if record.state != "completed":
        return _err(EXIT_MALFORMED, "job_not_completed",
                    f"collect-work-product: job {session_name!r} is {record.state!r} — only a "
                    f"terminal 'completed' build yields a work product", retryable=False,
                    correlation_id=ce)
    if not record.receipt_nonce:
        return _err(EXIT_MALFORMED, "no_build_receipt",
                    f"collect-work-product: job {session_name!r} has no receipt_nonce to bind",
                    retryable=False, correlation_id=ce)
    if promote_paths is not None:
        # Step-11 adv F2 + lane R1-F1 (ref half): exact-path collection is confined to the
        # per-receipt temp-ref namespace with CREATE semantics — a caller-controlled target
        # otherwise reaches update-ref and can advance a checked-out refs/heads/* branch while
        # its index/files stay stale, bypassing the guarded landing entirely.
        _canonical = f"refs/rawgentic/collect/{record.receipt_nonce}"
        if target_ref != _canonical or expected_target_sha != "0" * 40:
            return _err(EXIT_ENFORCEMENT, "invalid_collect_ref",
                        f"collect-work-product: exact-path collection must target "
                        f"{_canonical!r} with the all-zero expected SHA (create semantics) — "
                        f"got target {target_ref!r}, expected {expected_target_sha!r}; landing "
                        f"on the feature branch is land-work-product's job",
                        retryable=False, correlation_id=ce)
    # F7 (#571): a promotion must be AUTHORIZED, not merely terminal — another seat's output or a
    # stale/forged registry binding must never be promoted. Require exactly ONE audit receipt for
    # this nonce with verdict==pass AND role=="build" (a gated mutating build seat), plus at least
    # one VERIFIED completed observation bound to that nonce.
    from phase_executor.enforce import verify_post as _verify_post  # noqa: PLC0415
    _recs = audit.records()
    # Bind the authorizing receipt to THIS record's seat (Step-11 + the issue's run/seat/attempt AC),
    # not the nonce alone. (Deeper: an immutable work-product identity in the receipt would defeat a
    # fully-forged registry record — a contract change tracked as a #560 follow-up.)
    _pass_build = [r for r in _recs if r.get("kind") == "receipt"
                   and r.get("nonce") == record.receipt_nonce
                   and r.get("verdict") == "pass" and r.get("role") == "build"
                   and r.get("seat") == record.identity.seat]
    if len(_pass_build) != 1:
        return _err(EXIT_ENFORCEMENT, "unauthorized_work_product",
                    f"collect-work-product: expected exactly 1 passing build receipt for nonce "
                    f"{record.receipt_nonce!r}, found {len(_pass_build)} — refusing to promote",
                    retryable=False, correlation_id=ce)
    # #733: authorization requires a verified identity AND a completed process — a killed build
    # whose envelope still attested the right model must never authorize promoting partial output.
    # Step-11 R2-H2: the authorizing observation must also BIND to the passing receipt's identity
    # (same seat/run, matching correlation when both sides carry one) — nonce-sharing plus a
    # verified model is not ownership; a foreign observation must never authorize this promotion.
    from phase_executor.contract import observation_process_failure as _proc_fail  # noqa: PLC0415
    _receipt_cid = _pass_build[0].get("correlation_id")

    def _authorizes(envelope: dict) -> bool:
        inner = envelope.get("observation") or {}
        if inner.get("seat") != record.identity.seat or inner.get("run_id") != run_id:
            return False
        icid = inner.get("correlation_id")
        if icid is not None and _receipt_cid is not None and icid != _receipt_cid:
            return False
        return _verify_post(inner).verified and _proc_fail(inner) is None

    if not any(_authorizes(o) for o in _recs
               if o.get("kind") == "observation" and o.get("receipt_nonce") == record.receipt_nonce):
        return _err(EXIT_ENFORCEMENT, "unauthorized_work_product",
                    f"collect-work-product: no verified completed observation bound to receipt "
                    f"{record.receipt_nonce!r} (identity-bound: seat/run/correlation) — refusing "
                    f"to promote", retryable=False, correlation_id=ce)
    handle = handle_from_record(record)
    try:
        evidence = manager.content_evidence(handle)
    except Exception as e:  # noqa: BLE001 — a content/git read failure is fail-closed
        return _err(EXIT_INTERNAL, "content_evidence_failed", f"{type(e).__name__}: {e}",
                    retryable=False, correlation_id=ce)
    candidate_tree_sha = evidence["content_tree_sha"]
    if not evidence["changed_paths"]:
        # 8a R1/R2-H2 + Step-11 adv F1 (unconditional — the appendix default included): an empty
        # work product must never advance the branch — promote would happily create an empty
        # commit, which then satisfies "branch actually advanced" while every diff-scoped gate
        # passes vacuously.
        return _err(EXIT_ENFORCEMENT, "empty_work_product",
                    "collect-work-product: the worktree has no changed content — refusing a "
                    "vacuous collection (an empty commit would advance the branch with nothing "
                    "on it)", retryable=False, correlation_id=ce)
    Path(intent_dir).mkdir(parents=True, exist_ok=True)
    intent_path = Path(intent_dir) / f"collect-{record.receipt_nonce}.json"
    intent = None
    if intent_path.exists():
        try:
            intent = json.loads(intent_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            # 8a R2-H1: an unreadable intent must not silently degrade to "no intent" — a fresh
            # phase 1 could double-spend a receipt whose promotion already landed.
            return _err(EXIT_INTERNAL, "intent_corrupt",
                        f"collect-work-product: intent file {intent_path.name!r} is unreadable "
                        f"({type(e).__name__}) — refusing; inspect or remove it manually",
                        retryable=False, correlation_id=ce)
        # Step-11 adv F4 + lane R1-F4: a PARSEABLE-but-wrong intent is corruption too, never
        # absence — degrading to the absent-intent path overwrites the receipt's only recovery
        # identity. The file is nonce-NAMED, so a wrong nonce is corruption, not a foreign intent.
        why = _intent_semantic_error(intent, record.receipt_nonce)
        if why:
            return _err(EXIT_INTERNAL, "intent_corrupt",
                        f"collect-work-product: intent file {intent_path.name!r} is semantically "
                        f"corrupt ({why}) — refusing; inspect or remove it manually",
                        retryable=False, correlation_id=ce)
    # #767 intent-identity strictness (pass-2 F3 + 8a R1-H1/R2-H1). For a same-nonce intent:
    # - LANDED (new_sha set): the binding is LOCKED — any mismatch (candidate, expected, target,
    #   or policy) refuses loud. A legacy landed intent (pre-#767, no identity fields) succeeds
    #   only when the REQUESTED target actually holds the landed sha — never by default.
    # - UNLANDED (new_sha null): a LEGACY unlanded intent always refuses (Step-11 adv F3 — its
    #   original target is unprovable in the F-l window); a v2 one may rebind only for the SAME
    #   target+policy (the legitimate retry-after-CAS-refusal re-cut), and only after the live
    #   target proves the ORIGINAL promotion did not land (lane R1-F4).
    same_nonce = isinstance(intent, dict) and intent.get("receipt_nonce") == record.receipt_nonce
    same3 = (same_nonce
             and intent.get("candidate_tree_sha") == candidate_tree_sha
             and intent.get("expected_target_sha") == expected_target_sha)
    legacy_intent = same_nonce and ("target_ref" not in intent or "paths_digest" not in intent)
    same_binding = (same_nonce and not legacy_intent
                    and intent.get("target_ref") == target_ref
                    and intent.get("paths_digest") == paths_digest
                    and intent.get("expected_feature_ref") == expected_feature_ref)
    landed = same_nonce and bool(intent.get("new_sha"))
    legacy_verified = False
    if same_nonce:
        def _conflict(why: str):
            return _err(EXIT_MALFORMED, "intent_conflict",
                        f"collect-work-product: intent for receipt {record.receipt_nonce!r} "
                        f"({why}) is bound to target {intent.get('target_ref')!r} / paths_digest "
                        f"{intent.get('paths_digest')!r} / candidate "
                        f"{intent.get('candidate_tree_sha')!r} / expected "
                        f"{intent.get('expected_target_sha')!r} — refusing a re-request with a "
                        f"different binding", retryable=False, correlation_id=ce)
        if landed:
            if legacy_intent:
                try:
                    _tip = manager.target_tip(handle, target_ref)
                except Exception:  # noqa: BLE001 — an unreadable ref cannot verify the binding
                    _tip = None
                if same3 and _tip and _tip.get("sha") == intent.get("new_sha"):
                    legacy_verified = True
                else:
                    return _conflict("legacy, landed — requested target does not hold the "
                                     "landed sha")
            elif not (same3 and same_binding):
                return _conflict("landed")
        elif legacy_intent:
            # Step-11 adv F3: an unlanded LEGACY intent has no target identity — in the F-l
            # window its promotion may have landed on an unknown ORIGINAL target, so accepting
            # it for ANY requested target can spend the receipt twice. Manual recovery only.
            return _conflict("unlanded legacy — original target unprovable (F-l window); "
                             "manual recovery required")
        elif not same_binding:
            return _conflict("unlanded, possibly-landed F-l window")
        elif not same3:
            # Step-11 lane R1-F4 (F-l half): an identity-changed retry over an unlanded
            # same-binding intent is legitimate ONLY if the OLD promotion provably did not land.
            # Probe the live target with the OLD intent's identity (the same structural
            # fingerprint the landed-detection below uses): tip tree == its candidate, parented
            # on its expected (or a pure tree match for the all-zero create case). A hit means
            # the receipt is SPENT — rebinding would erase its only recovery identity.
            try:
                _old_tip = manager.target_tip(handle, target_ref)
            except Exception:  # noqa: BLE001 — an unreadable ref cannot prove non-landing
                _old_tip = None
            _old_cand = intent.get("candidate_tree_sha")
            _old_exp = intent.get("expected_target_sha")
            if (_old_tip and _old_tip.get("sha") and _old_tip["sha"] != _old_exp
                    and _old_tip.get("tree") == _old_cand
                    and (_old_exp == "0" * 40
                         or _old_exp in (_old_tip.get("parents") or ()))):
                return _conflict("unlanded intent whose promotion actually LANDED (live tip "
                                 "matches its candidate tree) — the receipt is spent; manual "
                                 "reconciliation required")
    matching = same3 and (same_binding or legacy_verified)
    if matching and intent.get("consumed"):
        return {"ok": True, "exit": EXIT_OK, "action": "collect_work_product",
                "status": "already_recorded", "receipt_nonce": record.receipt_nonce,
                "new_sha": intent.get("new_sha"), "candidate_tree_sha": candidate_tree_sha,
                "correlation_id": ce}
    if matching and intent.get("new_sha"):
        # promote already succeeded (new_sha recorded only AFTER promote returned) → phase 2 only
        new_sha = intent["new_sha"]
        promotion = PromotionResult(
            promoted=True, new_target_sha=new_sha, base_sha=evidence["base_sha"],
            head_sha=evidence["head_sha"], changed_paths=tuple(evidence["changed_paths"]),
            content_tree_sha=candidate_tree_sha)
    else:
        # #570 L1 (design F-l): a MATCHING intent whose new_sha is still unknown may mean promote's
        # update-ref LANDED but the finalize crashed before new_sha was recorded. Re-promoting would
        # CAS-fail against the advanced ref and record NOTHING for a commit that actually landed.
        # Before promoting, query the LIVE target ref: if it advanced past expected AND its tip is
        # OURS (its commit message carries the receipt_nonce, which promote embeds), the promotion
        # landed — reconstruct the result and resume phase 2 instead of re-promoting.
        landed_sha = None
        if matching:
            try:
                tip = manager.target_tip(handle, target_ref)
            except Exception:  # noqa: BLE001 — a ref-read failure falls back to promote (CAS-safe)
                tip = None
            # STRUCTURAL landed-match (never message text): a genuine landing is promote's own
            # commit — its tree IS our candidate tree and expected_target_sha is among its parents.
            # A foreign/crafted tip cannot forge our exact content tree, so it is never mistaken for
            # ours (a message-substring test would be spoofable). The all-zero expected (ref-create)
            # case has no prior tip to confuse, so the exact tree match alone authenticates it.
            # ponytail: reads only the CURRENT tip — a landing buried by a later collect's commit on
            # the SAME ref (interleaved collects) is missed and stays reconcile-blind. The model is
            # serialized (orchestrator-only, CELL-1 deferred) so that cannot happen today; upgrade to
            # an ancestry search (`git log --grep=<nonce> <expected>..<tip>`) if CELL-1 ever interleaves.
            _zero = "0" * 40
            if (tip and tip.get("sha") and tip["sha"] != expected_target_sha
                    and tip.get("tree") == candidate_tree_sha
                    and (expected_target_sha == _zero
                         or expected_target_sha in (tip.get("parents") or ()))):
                landed_sha = tip["sha"]
        if landed_sha:
            new_sha = landed_sha
            promotion = PromotionResult(
                promoted=True, new_target_sha=new_sha, base_sha=evidence["base_sha"],
                head_sha=evidence["head_sha"], changed_paths=tuple(evidence["changed_paths"]),
                content_tree_sha=candidate_tree_sha)  # tip.tree == candidate, just verified
            intent = {"receipt_nonce": record.receipt_nonce, "candidate_tree_sha": candidate_tree_sha,
                      "expected_target_sha": expected_target_sha, "new_sha": new_sha,
                      "target_ref": target_ref, "paths_digest": paths_digest, "consumed": False}
            if expected_feature_ref is not None:
                intent["expected_feature_ref"] = expected_feature_ref
            _atomic_write_json(intent_path, intent)
        else:
            # PHASE 1 — durable intent BEFORE the irreversible CAS (new_sha unknown yet, F-b)
            intent = {"receipt_nonce": record.receipt_nonce, "candidate_tree_sha": candidate_tree_sha,
                      "expected_target_sha": expected_target_sha, "new_sha": None,
                      "target_ref": target_ref, "paths_digest": paths_digest, "consumed": False}
            if expected_feature_ref is not None:
                intent["expected_feature_ref"] = expected_feature_ref
            _atomic_write_json(intent_path, intent)
            try:
                promotion = manager.promote(
                    handle, target_ref=target_ref, expected_target_sha=expected_target_sha,
                    message=f"collect work product ({kind}) for {record.receipt_nonce}",
                    path_policy=path_policy)
            except WorktreeError as e:
                return _err(EXIT_ENFORCEMENT, "promote_refused", str(e), retryable=False,
                            correlation_id=ce)
            if not promotion.promoted:
                # a moved ref / stale base: no write happened; refuse loud, record nothing (foreign move)
                return _err(EXIT_ENFORCEMENT, "promote_not_applied",
                            f"collect-work-product: promotion not applied ({promotion.reason}) — "
                            f"no work_product recorded", retryable=False, correlation_id=ce)
            _ptree = getattr(promotion, "content_tree_sha", None)
            if _ptree is not None and _ptree != candidate_tree_sha:
                # Step-11 lane R1-F3 (TOCTOU): promote's COMMITTED tree must be the candidate
                # tree the empty-check/intent captured (snapshot A == snapshot B) — a worktree
                # that drifted in between must refuse LOUD, never record. The temp-ref commit is
                # deliberately left in place as forensic evidence (fail-closed, manual recovery);
                # new_sha stays unrecorded so nothing downstream can consume it.
                return _err(EXIT_ENFORCEMENT, "promoted_tree_mismatch",
                            f"collect-work-product: promoted tree {_ptree!r} does not match the "
                            f"evidence candidate tree {candidate_tree_sha!r} — the worktree "
                            f"changed between evidence capture and promotion; refusing to record "
                            f"(manual reconciliation required)", retryable=False,
                            correlation_id=ce)
            new_sha = promotion.new_target_sha
            intent["new_sha"] = new_sha
            _atomic_write_json(intent_path, intent)  # F-b: record new_sha only AFTER promote returned
    # #570 L2 (rev per Step-11 finding): write the durable "a promotion LANDED, a work_product MUST
    # exist" marker IMMEDIATELY after new_sha is established and BEFORE derive_work_product — so a
    # derive failure on a landed promotion still leaves a marker reconcile can flag, closing the
    # fail-open missing-record path. Idempotent per (receipt_nonce, candidate_tree_sha, new_sha).
    existing = audit.records()
    def _binding_matches(r) -> bool:
        # Full-key dedup (8a R1-M4): a same-3-tuple record with a DIFFERENT binding must not
        # suppress ours. A legacy (fieldless) record satisfies the key ONLY on a legacy-verified
        # resume — else a pre-#767 crash resumed under new code would double-append.
        # #762 R5-B: the destination authorization is part of the binding — a same-target
        # record bound to a different feature ref is a different collect.
        if (r.get("target_ref") == target_ref and r.get("paths_digest") == paths_digest
                and r.get("expected_feature_ref") == expected_feature_ref):
            return True
        return legacy_verified and "target_ref" not in r

    already_expected = any(r.get("kind") == "expected_work_product"
                           and r.get("receipt_nonce") == record.receipt_nonce
                           and r.get("candidate_tree_sha") == candidate_tree_sha
                           and r.get("new_sha") == new_sha
                           and _binding_matches(r)
                           for r in existing)
    if not already_expected:
        audit.append_expected_work_product(receipt_nonce=record.receipt_nonce,
                                           candidate_tree_sha=candidate_tree_sha, new_sha=new_sha,
                                           target_ref=target_ref, paths_digest=paths_digest,
                                           expected_feature_ref=expected_feature_ref)
    # PHASE 2 — derive → audit-search-then-append (idempotent) → mark consumed
    try:
        wp = derive_work_product(manager, handle, kind=kind, promotion=promotion)
    except Exception as e:  # noqa: BLE001 — a derive/reconcile mismatch is fail-closed
        return _err(EXIT_INTERNAL, "derive_work_product_failed", f"{type(e).__name__}: {e}",
                    retryable=False, correlation_id=ce)
    already = any(r.get("kind") == "work_product"
                  and r.get("receipt_nonce") == record.receipt_nonce
                  and r.get("candidate_tree_sha") == candidate_tree_sha
                  and r.get("new_sha") == new_sha
                  and _binding_matches(r)
                  for r in existing)
    if not already:
        audit.append_work_product(receipt_nonce=record.receipt_nonce,
                                  candidate_tree_sha=candidate_tree_sha, new_sha=new_sha,
                                  work_product=wp, target_ref=target_ref,
                                  paths_digest=paths_digest,
                                  expected_feature_ref=expected_feature_ref)
    intent["consumed"] = True
    _atomic_write_json(intent_path, intent)
    return {"ok": True, "exit": EXIT_OK, "action": "collect_work_product",
            "status": "already_recorded" if already else "recorded",
            "receipt_nonce": record.receipt_nonce, "new_sha": new_sha,
            "candidate_tree_sha": candidate_tree_sha, "correlation_id": ce}


_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")


def _parse_porcelain_z(out: str) -> set:
    """R3-F: parse ``git status --porcelain=v1 -z`` output into the exact byte-path set.
    ``-z`` disables quoting (newline-in-name safe); rename/copy entries contribute BOTH
    paths (defensive — the caller passes ``--no-renames``, so none should appear)."""
    paths = set()
    toks = out.split("\0")
    i = 0
    while i < len(toks):
        t = toks[i]
        if not t:
            break
        xy, path = t[:2], t[3:]
        paths.add(path)
        if xy and xy[0] in "RC":
            i += 1
            if i < len(toks) and toks[i]:
                paths.add(toks[i])
        i += 1
    return paths


# R3-B: the landing IDENTITY — every record field except ts and landing_status (retry metadata).
_LANDING_IDENTITY_FIELDS = ("receipt_nonce", "feature_ref", "pre_sha", "new_sha", "temp_ref",
                            "run_id", "landing_version")


def land_work_product(*, repo: Optional[str] = None, expected_ref: str, pre_sha: str,
                      new_sha: str, temp_ref: str, correlation_id: Optional[str] = None,
                      git_runner=None, run_id: Optional[str] = None,
                      workspace: Optional[str] = None, project: Optional[str] = None,
                      no_audit: bool = False, audit=None, clock=None) -> dict:
    """#767 Step-11 + #762 D1/D2/R3-A/R3-B/R4-B/R4-C/R5-B: the PRODUCTION landing half of the
    two-step collection — fast-forward the checked-out feature branch onto the collected
    temp-ref commit, AUDITED by default. Fail-closed throughout.

    Two modes (rev-2 adoption 2 — an unaudited landing is a stated choice, never an accident):
    - AUDITED (default): requires ``run_id`` plus ``workspace``+``project`` (or an injected
      ``audit`` with an explicit ``repo``). The repository derives EXCLUSIVELY from
      workspace+project resolution; a ``repo`` also given must canonically equal it
      (``landing_repo_mismatch``). Authorization (R3-A/R4-C/R5-B) binds the landing to the
      collect-time record: exactly ONE code-kind work_product with this receipt nonce,
      ``target_ref == temp_ref`` and ``new_sha``; its inner ``base_sha == pre_sha``; its
      persisted ``expected_feature_ref == expected_ref`` — on BOTH tri-state paths.
    - ``no_audit=True``: the pure-git verb (standalone/ops use, greppable opt-out); identity
      args must be absent.

    Landing state machine (R4-B, normative order): authorize → merge + postconditions →
    append ``landed_work_product`` (idempotent on the full immutable identity; a same-key
    different-identity record is ``landing_identity_conflict``, exit 5) → CAS temp-ref delete.
    An append failure RETAINS the temp ref (exit 5; a retry heals via the already-landed
    path). Already-landed retry: temp ref present → must resolve to ``new_sha``; temp ref
    absent AND a matching landing record present → the record IS the heal; temp ref absent
    AND no record → refuse loudly.

    D2 (the live-probe finding): the dirty check is SCOPED to the collision set —
    ``changed = git diff --no-renames --name-only -z pre..new`` intersected with
    ``git status --porcelain=v1 -z --untracked-files=all --no-renames`` (exact byte paths,
    per-file untracked enumeration). Unrelated dirt lands; a colliding path refuses
    (``landing_dirty_paths``, naming the paths). git's own ff checkout-overwrite protection
    is retained as the fail-loud backstop.

    A bare ``update-ref`` on the checked-out branch is exactly what this exists to replace —
    it would move the ref while index/files stay at the pre-task state, so the scoped suite
    would test stale code. ``git_runner``/``audit``/``clock`` are injected for tests."""
    ce = correlation_id
    run = git_runner or _git_runner
    tick = clock or time.time
    if (not isinstance(expected_ref, str) or not expected_ref.startswith("refs/heads/")
            or not isinstance(temp_ref, str)
            or not temp_ref.startswith("refs/rawgentic/collect/")):
        return _err(EXIT_MALFORMED, "landing_invalid_input",
                    f"land-work-product: expected_ref must be refs/heads/-rooted and temp_ref "
                    f"must live under refs/rawgentic/collect/ (got {expected_ref!r}, "
                    f"{temp_ref!r})", retryable=False, correlation_id=ce)
    if not all(isinstance(s, str) and _SHA40_RE.match(s) for s in (pre_sha, new_sha)):
        return _err(EXIT_MALFORMED, "landing_invalid_input",
                    f"land-work-product: pre_sha and new_sha must be 40-hex commit SHAs "
                    f"(got {pre_sha!r}, {new_sha!r})", retryable=False, correlation_id=ce)
    # --- mode resolution (rev-2 adoption 2) ----------------------------------------------------
    if no_audit:
        if run_id is not None or workspace is not None or project is not None or audit is not None:
            return _err(EXIT_MALFORMED, "landing_audit_mode_conflict",
                        "land-work-product: --no-audit and audit identity args are mutually "
                        "exclusive — an unaudited landing is a stated choice", retryable=False,
                        correlation_id=ce)
        repo = repo or "."
    else:
        if not run_id or (audit is None and not (workspace and project)):
            return _err(EXIT_MALFORMED, "landing_identity_required",
                        "land-work-product: audited mode requires --run-id --workspace "
                        "--project (or pass --no-audit as the explicit unaudited opt-out)",
                        retryable=False, correlation_id=ce)
        if audit is None:
            try:
                pe = _import_phase_executor()
            except ImportError as e:
                return _err(EXIT_INTERNAL, "phase_executor_import_failed", str(e),
                            retryable=False, correlation_id=ce)
            try:
                repo_root = resolve_repo_root(workspace, project)
            except MalformedConfig as e:
                return _err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False,
                            correlation_id=ce)
            if repo is not None and Path(repo).resolve() != repo_root:
                # R3-A: the repository derives from workspace+project — a divergent --repo is
                # a cross-project landing attempt, refused before any git runs.
                return _err(EXIT_ENFORCEMENT, "landing_repo_mismatch",
                            f"land-work-product: --repo {repo!r} does not resolve to the "
                            f"project root {str(repo_root)!r} derived from workspace+project",
                            retryable=False, correlation_id=ce)
            repo = str(repo_root)
            try:
                # capture_root mirrors derive_paths (run_seat/RoutingAuditLog each append
                # run_id exactly once); landing deliberately skips the routing-table resolve —
                # a broken seat table must not block recovering an already-collected landing.
                audit = pe.enforce.RoutingAuditLog(repo_root / ".rawgentic" / "runs", run_id)
            except (OSError, ValueError) as e:
                return _err(EXIT_INTERNAL, "runtime_init_failed", f"{type(e).__name__}: {e}",
                            retryable=False, correlation_id=ce)
        elif repo is None:
            return _err(EXIT_MALFORMED, "landing_invalid_input",
                        "land-work-product: an injected audit requires an explicit repo",
                        retryable=False, correlation_id=ce)
    nonce = temp_ref[len("refs/rawgentic/collect/"):]
    if not nonce:
        return _err(EXIT_MALFORMED, "landing_invalid_input",
                    f"land-work-product: temp_ref {temp_ref!r} carries no receipt nonce",
                    retryable=False, correlation_id=ce)

    def _git(*args):
        return run(["git", "-C", repo, *args])

    # --- audited authorization (R3-A/R4-C/R5-B) — BEFORE any git mutation, both tri-states ----
    has_matching_landing = has_conflicting_landing = False
    ours = None
    if not no_audit:
        try:
            records = audit.records()
        except ValueError as e:
            return _err(EXIT_INTERNAL, "landing_audit_unreadable",
                        f"land-work-product: the run audit log failed fail-closed validation "
                        f"({e}) — inspect it manually", retryable=False, correlation_id=ce)
        wps = [r for r in records if r.get("kind") == "work_product"
               and r.get("receipt_nonce") == nonce
               and r.get("target_ref") == temp_ref
               and r.get("new_sha") == new_sha
               and (r.get("work_product") or {}).get("kind") == "code"]
        if len(wps) != 1:
            return _err(EXIT_ENFORCEMENT, "landing_unauthorized",
                        f"land-work-product: expected exactly 1 code work_product record "
                        f"binding receipt {nonce!r} to {temp_ref!r} at {new_sha!r}, found "
                        f"{len(wps)} — the collect record is the authorization; refusing",
                        retryable=False, correlation_id=ce)
        inner = wps[0].get("work_product") or {}
        if inner.get("base_sha") != pre_sha:
            # R4-C: pre_sha is bound to the collected evidence, not caller-asserted — a
            # fabricated pre_sha must not mint an audit record (either tri-state path).
            return _err(EXIT_ENFORCEMENT, "landing_unauthorized",
                        f"land-work-product: --pre-sha {pre_sha!r} does not equal the "
                        f"authorizing work product's base_sha "
                        f"{inner.get('base_sha')!r} — refusing", retryable=False,
                        correlation_id=ce)
        if wps[0].get("expected_feature_ref") != expected_ref:
            # R5-B: the destination is authorized by the collect-time record, not the caller —
            # replaying a valid work product onto a different branch refuses.
            return _err(EXIT_ENFORCEMENT, "landing_feature_ref_mismatch",
                        f"land-work-product: --expected-ref {expected_ref!r} does not equal "
                        f"the collect-time expected_feature_ref "
                        f"{wps[0].get('expected_feature_ref')!r} — refusing", retryable=False,
                        correlation_id=ce)
        ours = {"receipt_nonce": nonce, "feature_ref": expected_ref, "pre_sha": pre_sha,
                "new_sha": new_sha, "temp_ref": temp_ref, "run_id": run_id,
                "landing_version": 1}
        for r in records:
            if r.get("kind") != "landed_work_product":
                continue
            if r.get("receipt_nonce") != nonce or r.get("new_sha") != new_sha:
                continue
            if all(r.get(k) == ours[k] for k in _LANDING_IDENTITY_FIELDS):
                has_matching_landing = True
            else:
                has_conflicting_landing = True

    def _append_landing(status: str):
        """R4-B: append (idempotent, conflict-refusing) — returns an error dict or None."""
        if has_conflicting_landing:
            # R3-B: same (receipt_nonce, new_sha), different immutable identity — an
            # IMMEDIATE append-time integrity error; the temp ref is retained for forensics.
            return _err(EXIT_INTERNAL, "landing_identity_conflict",
                        f"land-work-product: an existing landed_work_product for receipt "
                        f"{nonce!r} at {new_sha!r} carries a DIFFERENT immutable identity — "
                        f"refusing to append; reconcile manually", retryable=False,
                        correlation_id=ce)
        if has_matching_landing:
            return None  # full-identity dedup: the record already exists
        try:
            audit.append_landed_work_product(
                receipt_nonce=nonce, feature_ref=expected_ref, pre_sha=pre_sha,
                new_sha=new_sha, temp_ref=temp_ref, landing_status=status, run_id=run_id,
                ts=int(tick()))
        except (OSError, ValueError) as e:
            # R4-B: the merge stands (git is the source of truth); the temp ref is retained
            # so a retry heals via the already-landed path.
            return _err(EXIT_INTERNAL, "landing_append_failed",
                        f"land-work-product: the landing record failed to append "
                        f"({type(e).__name__}: {e}) — the merge stands and the temp ref is "
                        f"retained; re-run to heal", retryable=False, correlation_id=ce)
        return None

    rc, ref, _e = _git("symbolic-ref", "-q", "HEAD")
    if rc != 0 or ref.strip() != expected_ref:
        return _err(EXIT_ENFORCEMENT, "landing_wrong_ref",
                    f"land-work-product: HEAD is {'detached' if rc != 0 else ref.strip()!r}, "
                    f"not the recorded feature ref {expected_ref!r} — refusing",
                    retryable=False, correlation_id=ce)
    rc, head, err = _git("rev-parse", "HEAD")
    if rc != 0:
        return _err(EXIT_INTERNAL, "landing_git_failed",
                    f"land-work-product: rev-parse HEAD failed: {err.strip()}", retryable=False,
                    correlation_id=ce)
    head = head.strip()
    tr_rc, tr_sha, _e = _git("rev-parse", "--verify", "--quiet", temp_ref)
    tr_sha = tr_sha.strip()
    if head == new_sha:
        # already landed (crash-after-merge recovery, R4-B)
        if no_audit:
            del_rc, _o, _e = _git("update-ref", "-d", temp_ref, new_sha)
            return {"ok": True, "exit": EXIT_OK, "action": "land_work_product",
                    "status": "already_landed", "new_sha": new_sha,
                    "temp_ref_deleted": del_rc == 0, "correlation_id": ce}
        if tr_rc == 0:
            if tr_sha != new_sha:
                return _err(EXIT_ENFORCEMENT, "landing_temp_ref_mismatch",
                            f"land-work-product: {temp_ref!r} resolves to {tr_sha!r}, not the "
                            f"landed SHA {new_sha!r} — refusing", retryable=False,
                            correlation_id=ce)
            fail = _append_landing("already_landed")
            if fail:
                return fail
            del_rc, _o, _e = _git("update-ref", "-d", temp_ref, new_sha)
            return {"ok": True, "exit": EXIT_OK, "action": "land_work_product",
                    "status": "already_landed", "new_sha": new_sha,
                    "temp_ref_deleted": del_rc == 0, "correlation_id": ce}
        if has_matching_landing:
            # the landing record IS the heal — merge landed, record present, temp ref gone
            return {"ok": True, "exit": EXIT_OK, "action": "land_work_product",
                    "status": "already_landed", "new_sha": new_sha,
                    "temp_ref_deleted": True, "correlation_id": ce}
        if has_conflicting_landing:
            return _err(EXIT_INTERNAL, "landing_identity_conflict",
                        f"land-work-product: the only landing evidence for receipt {nonce!r} "
                        f"at {new_sha!r} carries a DIFFERENT immutable identity — reconcile "
                        f"manually", retryable=False, correlation_id=ce)
        return _err(EXIT_ENFORCEMENT, "landing_record_missing",
                    f"land-work-product: {expected_ref!r} is already at {new_sha!r} but the "
                    f"temp ref is gone and NO landing record exists — cannot authorize the "
                    f"heal; reconcile manually", retryable=False, correlation_id=ce)
    if head != pre_sha:
        return _err(EXIT_ENFORCEMENT, "landing_unexpected_sha",
                    f"land-work-product: {expected_ref!r} is at {head!r} — neither the recorded "
                    f"pre-task SHA {pre_sha!r} nor the landed SHA {new_sha!r}; the branch moved "
                    f"underneath — refusing", retryable=False, correlation_id=ce)
    if tr_rc != 0 or tr_sha != new_sha:
        # R3-A: the temp ref must hold exactly the collected commit before a fresh landing
        return _err(EXIT_ENFORCEMENT, "landing_temp_ref_mismatch",
                    f"land-work-product: {temp_ref!r} "
                    f"{'does not exist' if tr_rc != 0 else 'resolves to ' + repr(tr_sha)}, "
                    f"expected {new_sha!r} — refusing", retryable=False, correlation_id=ce)
    # --- D2/R3-F: SCOPED dirty check — refuse only a changed∩dirty collision, by name --------
    rc, out, err = _git("diff", "--no-renames", "--name-only", "-z",
                        f"{pre_sha}..{new_sha}")
    if rc != 0:
        return _err(EXIT_INTERNAL, "landing_git_failed",
                    f"land-work-product: git diff failed: {err.strip()}", retryable=False,
                    correlation_id=ce)
    changed = {p for p in out.split("\0") if p}
    rc, out, err = _git("status", "--porcelain=v1", "-z", "--untracked-files=all",
                        "--no-renames")
    if rc != 0:
        return _err(EXIT_INTERNAL, "landing_git_failed",
                    f"land-work-product: git status failed: {err.strip()}", retryable=False,
                    correlation_id=ce)
    colliding = sorted(changed & _parse_porcelain_z(out))
    if colliding:
        return _err(EXIT_ENFORCEMENT, "landing_dirty_paths",
                    f"land-work-product: the landing would materialize over dirty paths "
                    f"{colliding!r} — commit/stash exactly these first (unrelated dirt is "
                    f"tolerated)", retryable=False, correlation_id=ce)
    rc, _o, err = _git("merge", "--ff-only", new_sha)
    if rc != 0:
        return _err(EXIT_ENFORCEMENT, "landing_ff_refused",
                    f"land-work-product: fast-forward to {new_sha!r} refused: "
                    f"{err.strip()[:200]}", retryable=False, correlation_id=ce)
    rc1, head2, _e1 = _git("rev-parse", "HEAD")
    rc2, ref2, _e2 = _git("rev-parse", expected_ref)
    if rc1 != 0 or rc2 != 0 or head2.strip() != new_sha or ref2.strip() != new_sha:
        return _err(EXIT_ENFORCEMENT, "landing_postcondition_failed",
                    f"land-work-product: post-merge state is not exactly {new_sha!r} "
                    f"(HEAD {head2.strip()!r}, {expected_ref} {ref2.strip()!r}) — inspect "
                    f"manually", retryable=False, correlation_id=ce)
    if not no_audit:
        fail = _append_landing("landed")
        if fail:
            return fail
    del_rc, _o, _e = _git("update-ref", "-d", temp_ref, new_sha)
    return {"ok": True, "exit": EXIT_OK, "action": "land_work_product", "status": "landed",
            "new_sha": new_sha, "temp_ref_deleted": del_rc == 0, "correlation_id": ce}


def recover_run(*, run_id: str, supervisor, snapshot, audit, routing, enforce,
                ledger_closed: bool, await_timeout_s: float = 3600.0,
                correlation_id: Optional[str] = None) -> dict:
    """#559 C1 (design §2.7): the recovery-dispatch chokepoint. Refuses a ``run_closed`` ledger;
    supplies the recovery gate (resolve target → ``check_pre`` → ``append_receipt`` →
    ``RecoveryAuthorization`` | ``None``) so every relaunch is RECEIPTED; runs
    ``supervisor.recover`` (the ONE gated relaunch path); then awaits each relaunched record,
    appends its Observation bound to the recovery receipt, and ``verify_post``s. NO
    ``append_expected`` — a recovery is an attempt under the ORIGINAL expected call, linked by
    ``recovered_from`` (reconcile groups it, R1). Exit taxonomy mirrors ``dispatch``."""
    import types as _types  # noqa: PLC0415
    from phase_executor.supervisor import RecoveryAuthorization  # noqa: PLC0415
    ce = correlation_id
    audit_path = str(audit.path)
    if ledger_closed:
        return _err(EXIT_ENFORCEMENT, "run_closed_recover_refused",
                    f"run {run_id!r} ledger is run_closed — recovery refused (#559)",
                    retryable=False, correlation_id=ce, audit_path=audit_path)
    nonce_by_sid, lane_by_sid, corr_by_sid = {}, {}, {}

    def gate(*, record, correlation_id, recovered_from):
        targets = routing.eligible_targets(record.identity.seat, snapshot)
        if not targets:
            return None
        # F5 (#571): bind the recovery to the ORIGINAL call's target, re-validated against the
        # CURRENT snapshot — never eligible_targets[0]. The original receipt (same seat,
        # correlation_id == recovered_from, a non-recovery pass) carries the target_identity that
        # created the provider session; resolve THAT identity in the current eligible set. If the
        # original target is no longer eligible, refuse rather than silently drift to a new target.
        # The record's receipt_nonce (JobRecord: the receipt this launch was authorized under) pins
        # the EXACT session-creating target — robust to a fallback that left sibling pass receipts
        # under the same correlation_id (Step-11). Legacy records with no nonce fall back to the
        # first non-recovery pass receipt for this call. A recovery with NO locatable original
        # receipt is an anomaly → REFUSE, never drift to targets[0] (Step-11 fail-open fix).
        recs = audit.records()
        orig_identity = None
        if record.receipt_nonce:
            for r in recs:
                if (r.get("kind") == "receipt" and r.get("nonce") == record.receipt_nonce
                        and r.get("target_identity")):
                    orig_identity = tuple(r["target_identity"])
                    break
        elif recovered_from is not None:
            for r in recs:  # legacy record (no receipt_nonce): match the original call by correlation
                if (r.get("kind") == "receipt" and r.get("correlation_id") == recovered_from
                        and r.get("seat") == record.identity.seat and r.get("verdict") == "pass"
                        and r.get("recovered_from") is None and r.get("target_identity")):
                    orig_identity = tuple(r["target_identity"])
                    break
        if orig_identity is None:
            return None  # no locatable original receipt — refuse rather than drift to a new target
        resolved_target = next(
            (t for t in targets if enforce.target_identity(t) == orig_identity), None)
        if resolved_target is None:
            return None  # original target no longer eligible under the current snapshot
        receipt = enforce.check_pre(
            record.identity.seat, resolved_target, snapshot, correlation_id=correlation_id,
            attempt_id=f"{record.resume_attempts + 1}-recover", recovered_from=recovered_from)
        audit.append_receipt(receipt)  # recorded BEFORE launch (R2); a fail verdict → refuse (None)
        if receipt.verdict != "pass":
            return None
        nonce_by_sid[record.session_name] = receipt.nonce
        lane_by_sid[record.session_name] = dict(resolved_target["lane"])
        corr_by_sid[record.session_name] = correlation_id  # F6: for the pre-append correlation check
        return RecoveryAuthorization(receipt.nonce, resolved_target, snapshot.config_digest)

    try:
        actions = supervisor.recover(run_id, dispatch_gate=gate)
    except routing.RoutingError as e:
        return _err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False,
                    correlation_id=ce, audit_path=audit_path)
    results, worst = [], EXIT_OK
    for a in actions:
        entry = {"seat": a.identity.seat, "run_id": a.identity.run_id, "action": a.action}
        if a.action == "relaunch":
            state, obs = supervisor.await_job(a.record, timeout_s=await_timeout_s)
            entry["state"] = state
            nonce = nonce_by_sid.get(a.record.session_name)
            # every terminal state that HAS an observation binds it to the recovery receipt (R2):
            # a post-receipt death would otherwise leave an observation-less receipt that reconcile
            # catches as missing_obs.
            if obs is not None and nonce:
                stamped = dict(obs)
                # F6 (#571): refuse a foreign-correlation observation BEFORE appending (mirror
                # resume_dispatch's F9) — a mismatched child envelope must never enter the ledger.
                child_cid = stamped.get("correlation_id")
                exp_cid = corr_by_sid.get(a.record.session_name)
                # 8a R2-H4: when the CHILD supplies a correlation it must match exactly, even
                # when the recovery derived no expected value (legacy) — a non-null foreign
                # correlation must never bypass the refusal just because exp_cid is None.
                if child_cid is not None and child_cid != exp_cid:
                    entry["verify"] = (f"correlation_mismatch: child {child_cid!r} != recovery "
                                       f"{exp_cid!r} — foreign observation refused")
                    worst = max(worst, EXIT_ENFORCEMENT)
                    results.append(entry)
                    continue
                if child_cid is None and exp_cid is not None:
                    # F6 (mirror resume_dispatch's F9): an unlabeled child obs is bound to the
                    # recovery correlation rather than left unbound in the ledger.
                    stamped["correlation_id"] = exp_cid
                if lane_by_sid.get(a.record.session_name) and not stamped.get("dispatched_lane"):
                    stamped["dispatched_lane"] = lane_by_sid[a.record.session_name]
                audit.append_observation(stamped, receipt=_types.SimpleNamespace(nonce=nonce))
            if state == "completed":
                # #733: the foreign-correlation refusal above already excluded unowned envelopes,
                # so this entry's observation is correlation-owned — evidence first, then verdicts.
                fail = enforce.contract.observation_process_failure(obs or {})
                pc = enforce.verify_post(obs or {})
                if not pc.ok:
                    entry["verify"] = pc.reason
                    worst = EXIT_ENFORCEMENT
                    if obs is not None:
                        _attach_partial(entry, obs)
                elif fail is not None:
                    # a recovered completed entry whose envelope failed the process predicate is
                    # availability, never a clean recovery (#733). Step-11 R1-H2: process-failure
                    # evidence labels the entry BEFORE the weaker unverified verdict (same
                    # precedence rule as the supervised/resume result assembly).
                    entry["verify"] = f"process_failure: {fail}"
                    worst = max(worst, EXIT_AVAILABILITY)
                    if obs is not None:
                        _attach_partial(entry, obs)
                elif not pc.verified:
                    entry["verify"] = f"unverified: {pc.reason}"
                    worst = max(worst, EXIT_AVAILABILITY)
                    if obs is not None:
                        _attach_partial(entry, obs)
            else:
                # 8a R1-H1 (recover leg): a non-completed recovery entry keeps its availability
                # verdict, but an ATTESTED-WRONG identity on its owned envelope is a billed
                # breach (mismatch-only — identity_missing keeps the state verdict), and any
                # owned envelope's partial evidence rides on the entry.
                if obs is not None:
                    pc_nc = enforce.verify_post(obs)
                    if not pc_nc.ok and pc_nc.reason == "requested_actual_mismatch":
                        entry["verify"] = pc_nc.reason
                        worst = EXIT_ENFORCEMENT
                    _attach_partial(entry, obs)
                worst = max(worst, EXIT_AVAILABILITY)
        elif a.action.startswith("relaunch_refused") or a.action in ("fail", "quarantine"):
            worst = max(worst, EXIT_AVAILABILITY)
        results.append(entry)
    return {"ok": worst == EXIT_OK, "exit": worst, "action": "recover_run",
            "run_id": run_id, "results": results, "audit_path": audit_path, "correlation_id": ce}


def _err(exit_code: int, code: str, message: str, *, retryable: bool, correlation_id=None, audit_path=None) -> dict:
    err = {"code": code, "message": message, "retryable": retryable}
    if correlation_id is not None:
        err["correlation_id"] = correlation_id
    out = {"ok": False, "exit": exit_code, "error": err}
    if audit_path is not None:
        out["audit_path"] = audit_path
    return out


# #733 (8a R2-H3): sync `dispatch_<fail>` reasons whose retry is safe — the process died or the
# transport delivered nothing (positive death / nothing-ran evidence). Every other reason
# (parse_error, identity_failure, malformed_status, ...) is a definite, potentially effectful
# failure: retryable=False, the ERROR protocol owns it.
_RETRYABLE_FAILS: Final[frozenset] = frozenset(
    {"timeout", "signalled", "nonzero_exit", "launch_error", "no_response"})


def _attach_partial(res: dict, obs) -> dict:
    """#733 AC4: attach partial-output evidence from a correlation-OWNED observation to a
    failure result. ``partial`` is precisely ``parsed_payload is not None`` (empty containers,
    "", 0 and False ARE payloads) — a dispatch that produced nothing must not claim a partial.
    Callers must NEVER pass a foreign-correlation observation (the correlation_mismatch refusal
    paths are excluded by design — another dispatch's payload must not ride back to this
    caller). Accepts an Observation or its dict form; reads, never raises (a to_dict() that
    raises or returns a non-dict degrades to minimal fields — 8a R1-M3)."""
    if isinstance(obs, dict):
        d = obs
    else:
        try:
            d = obs.to_dict() if hasattr(obs, "to_dict") else {}
        except Exception:  # noqa: BLE001 — never-raises contract inside result assembly
            d = {}
        if not isinstance(d, dict):
            d = {}
    payload = d.get("parsed_payload")
    res["partial"] = payload is not None
    res["parse_status"] = d.get("parse_status")
    res["partial_payload"] = payload
    res["raw_capture_path"] = d.get("raw_capture_path")
    res["observation"] = d
    return res


# --- CLI (guarded phase_executor import lives here) --------------------------------------------
def _ensure_pe_importable() -> None:
    """Put ``phase_executor/src`` (sibling of this hook's repo) on ``sys.path`` so the plain repo
    interpreter can import the package (verified: core modules are stdlib + jsonschema only)."""
    src = str(Path(__file__).resolve().parent.parent / "phase_executor" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def _pane_pythonpath() -> str:
    """PYTHONPATH for a supervised tmux pane, which runs ``python -m phase_executor.pane_runner``.
    It MUST include ``phase_executor/src`` (pane_runner imports ONLY the package, never hooks/) —
    a hooks/ PYTHONPATH made the pane die on ``No module named phase_executor`` before writing its
    observation.json, which the supervisor read as ``exited_no_sentinel`` (#559 live-proving find).
    phase_executor/src is PREPENDED to any inherited PYTHONPATH rather than clobbering it."""
    src = str(Path(__file__).resolve().parent.parent / "phase_executor" / "src")
    existing = os.environ.get("PYTHONPATH", "")
    return os.pathsep.join([src, existing]) if existing else src


def _import_phase_executor():
    """Guarded ``phase_executor`` import — returns a namespace of the pieces the CLI needs, or
    raises ImportError. Called INSIDE the subcommands (never at module top level) so a stale tree /
    missing dep maps to a structured exit 5, not a bare module-load traceback. A module-level
    function so a test can monkeypatch it to force the ImportError branch."""
    _ensure_pe_importable()
    import phase_executor.routing as routing  # noqa: PLC0415
    import phase_executor.enforce as enforce  # noqa: PLC0415
    import phase_executor.canary as canary  # noqa: PLC0415 — #470 §2a supervised branch
    import phase_executor.canary_evidence as canary_evidence  # noqa: PLC0415
    import phase_executor.contract as contract  # noqa: PLC0415
    import phase_executor.ledger as ledger  # noqa: PLC0415 — #555 expected-call ledger
    import phase_executor.capture as capture  # noqa: PLC0415 — #555 sanitize_component (dir colocation)
    from phase_executor import run_seat  # noqa: PLC0415
    from phase_executor.engine import _dispatch_real, PROVIDER_ENGINE  # noqa: PLC0415
    from phase_executor.quota import QuotaCoordinator, QuotaTimeout  # noqa: PLC0415
    from phase_executor.supervisor import TmuxSupervisor  # noqa: PLC0415
    import phase_executor.supervisor as supervisor_mod  # noqa: PLC0415 — #471 status surface
    from phase_executor.herdr_backend import HerdrBackend  # noqa: PLC0415 — #638
    from phase_executor.terminal_backend import TmuxBackend  # noqa: PLC0415 — #647 status surface
    from phase_executor.registry import JobRegistry, RegistryCorrupt  # noqa: PLC0415
    from phase_executor.registry import read_all as registry_read_all  # noqa: PLC0415
    from phase_executor.registry import session_name as registry_session_name  # noqa: PLC0415
    from phase_executor.worktree import WorktreeIdentity, WorktreeManager  # noqa: PLC0415
    import phase_executor.worktree as worktree_mod  # noqa: PLC0415 — #571 F8: pe.worktree.WorktreeError
    from phase_executor.adapters import ADAPTERS  # noqa: PLC0415
    return types.SimpleNamespace(
        routing=routing, enforce=enforce, run_seat=run_seat,
        dispatch_real=_dispatch_real, QuotaCoordinator=QuotaCoordinator, QuotaTimeout=QuotaTimeout,
        canary=canary, canary_evidence=canary_evidence, contract=contract,
        PROVIDER_ENGINE=PROVIDER_ENGINE, TmuxSupervisor=TmuxSupervisor,
        HerdrBackend=HerdrBackend, TmuxBackend=TmuxBackend,
        supervisor=supervisor_mod, JobRegistry=JobRegistry, RegistryCorrupt=RegistryCorrupt,
        ledger=ledger, capture=capture,
        registry_read_all=registry_read_all,
        registry_session_name=registry_session_name,
        WorktreeIdentity=WorktreeIdentity, WorktreeManager=WorktreeManager,
        worktree=worktree_mod, ADAPTERS=ADAPTERS,
    )


def _emit(obj: dict) -> int:
    """Print one JSON object to stdout; return its ``exit`` (default 0)."""
    code = obj.pop("exit", EXIT_OK) if isinstance(obj, dict) else EXIT_OK
    print(json.dumps(obj, separators=(",", ":")))
    return code


def _do_resolve(args) -> int:
    try:
        ws_snapshot = _load_workspace_snapshot(args.workspace)  # ONE read per invocation (S11 F4)
        action, reason = resolve_seat_action_from_snapshot(args.seat, ws_snapshot, args.project)
    except MalformedConfig as e:
        return _emit(_err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False))
    out = {"seat": args.seat, "action": action, "primary_model": None, "reason": reason, "exit": EXIT_OK}
    if action == "executor":
        # Look up the seat's chain[0] model for observability (needs phase_executor + the snapshot).
        try:
            pe = _import_phase_executor()
        except ImportError as e:
            return _emit(_err(EXIT_INTERNAL, "phase_executor_import_failed", str(e), retryable=False))
        try:
            repo_root = repo_root_from_snapshot(ws_snapshot, args.workspace, args.project)
            rt = resolve_table(repo_root, pe.routing)
            targets = pe.routing.eligible_targets(args.seat, rt.snapshot)
            out["primary_model"] = targets[0]["model"] if targets else None
            # #445 P3-A2 observability: which table this seat would route on, auditable from
            # the CLI output alone (the dispatch path pins routing_config_digest per Observation).
            out["table_source"] = rt.source
            out["config_digest"] = rt.snapshot.config_digest
        except MalformedConfig as e:
            return _emit(_err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False))
        except pe.routing.RoutingError as e:
            return _emit(_err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False))
        except OSError as e:
            return _emit(_err(EXIT_INTERNAL, "routing_table_unreadable", str(e), retryable=False))
        except Exception as e:  # noqa: BLE001 — never leak a bare traceback from resolve-seat
            return _emit(_err(EXIT_INTERNAL, "internal_error", f"{type(e).__name__}: {e}", retryable=False))
    return _emit(out)


def table_projection(rt: "ResolvedTable", repo_root: Path) -> dict:
    """#446: the ONE library-owned projection of a resolved table for setup's Step 2i.

    `build_bake_off` reports ``bakeoff_policy.BUILD_MODELS`` — the ACTUAL competitive
    candidate constant — never the build seat's primary+chain rows (they coincide today by
    accident and are mechanically decoupled; S1). `file` is the normalized project-relative
    override path when the source is a project file, else None."""
    import bakeoff_policy  # noqa: PLC0415 — sibling hook, lazy so resolve-only paths skip it
    declared = None
    if rt.source == "project_file":
        declared = os.path.relpath(rt.path, repo_root.resolve())
    seats = [{"seat": name, "role": seat.get("role"), "primary": seat["primary"]["model"],
              "chain": [c["model"] for c in seat.get("chain", [])]}
             for name, seat in rt.snapshot.table["seats"].items()]
    return {
        "projection_version": 1,
        "table_source": rt.source,
        "config_digest": rt.snapshot.config_digest,
        "file": declared,
        "seats": seats,
        "build_bake_off": list(bakeoff_policy.BUILD_MODELS),
        "build_bake_off_note": ("informational — not table-editable; candidates are "
                                "bakeoff_policy.BUILD_MODELS, not routing-table rows; "
                                "see the bake-off-config follow-up issue"),
    }


def _do_show(args) -> int:
    try:
        pe = _import_phase_executor()
    except ImportError as e:
        return _emit(_err(EXIT_INTERNAL, "phase_executor_import_failed", str(e), retryable=False))
    try:
        repo_root = resolve_repo_root(args.workspace, args.project)
        rt = resolve_table(repo_root, pe.routing)
        proj = table_projection(rt, repo_root)
    except MalformedConfig as e:
        return _emit(_err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False))
    except pe.routing.RoutingError as e:
        return _emit(_err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False))
    except OSError as e:
        return _emit(_err(EXIT_INTERNAL, "routing_table_unreadable", str(e), retryable=False))
    except Exception as e:  # noqa: BLE001 — a display command never leaks a bare traceback
        return _emit(_err(EXIT_INTERNAL, "internal_error", f"{type(e).__name__}: {e}", retryable=False))
    if args.json:
        print(json.dumps(proj, indent=2))
        return EXIT_OK
    for s in proj["seats"]:
        chain = " -> ".join(s["chain"]) if s["chain"] else "(none)"
        print(f"{s['seat']}: primary {s['primary']} | chain {chain} | role {s['role']}")
    print(f"build bake-off (informational): {', '.join(proj['build_bake_off'])}")
    print(f"table_source: {proj['table_source']}")
    if proj["file"]:
        print(f"file: {proj['file']}")
    print(f"config_digest: {proj['config_digest']}")
    return EXIT_OK


def mint_gate(plan_content: str, issue_complexity: str, plan_est_lines,
              cfg=None) -> dict:
    """#470 Step-11 H2/H3 — the gate.json PRODUCER. Derives the plan-side facts EXACTLY the way
    ``mint_plan_context`` later re-derives them (aggregate risk_level = high-if-any-task-high;
    file_count = DISTINCT files across tasks), so ``verified_decision``'s key-for-key cross-check
    passes on a fresh plan by construction. Records the plan digest (freshness binding). Returns
    the JSON-safe dict ``_load_gate_decision`` round-trips. Raises PlanFormatError/ValueError on
    malformed input (caller maps to exit 2).

    TRUST BOUNDARY (Step-11 re-review RH4): ``issue_complexity`` and ``plan_est_lines`` are
    ORCHESTRATOR-authoritative inputs — the WF2 Step-2 complexity classification and the plan
    estimate — under the same in-process trust model ``verified_decision`` documents (defends
    against authoring errors and stale reuse, not a hostile in-process caller). argparse pins
    the complexity vocabulary; lines are validated non-negative at the CLI."""
    tasks = plan_lib.parse_tasks(plan_content)
    if not tasks:
        raise plan_lib.PlanFormatError("mint-gate: plan parses to zero tasks (check heading form)")
    risk_level = "high" if any(t.risk_level == "high" for t in tasks) else "standard"
    files = sorted({f for t in tasks for f in (t.files or ())})
    gd = complexity_gate.needs_bakeoff(
        {"risk_level": risk_level},
        {"complexity": issue_complexity},
        {"lines": plan_est_lines, "file_count": len(files), "files": files},
        cfg=cfg, plan_content=plan_content)
    return {"decision": gd.decision, "reason_codes": list(gd.reason_codes),
            "input_snapshot": gd.input_snapshot, "policy_digest": gd.policy_digest}


def _do_mint_gate(args) -> int:
    try:
        plan_content = Path(args.plan_file).read_text(encoding="utf-8")
    except OSError as e:
        return _emit(_err(EXIT_MALFORMED, "plan_file_unreadable", str(e), retryable=False))
    if args.plan_est_lines < 0:
        return _emit(_err(EXIT_MALFORMED, "mint_gate_invalid_input",
                          "plan-est-lines must be non-negative", retryable=False))
    try:
        obj = mint_gate(plan_content, args.issue_complexity, args.plan_est_lines)
    except (plan_lib.PlanFormatError, ValueError) as e:
        return _emit(_err(EXIT_MALFORMED, "mint_gate_invalid_input", str(e), retryable=False))
    Path(args.out).write_text(json.dumps(obj, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return _emit({"ok": True, "exit": EXIT_OK, "action": "mint-gate", "out": args.out,
                  "decision": obj["decision"], "reason_codes": obj["reason_codes"]})


def _load_gate_decision(path):
    """Rebuild a #429 ``complexity_gate.GateDecision`` from the JSON the bake-off writes (fields:
    decision, reason_codes, input_snapshot, policy_digest). ``verified_decision`` recomputes the
    digest over ``input_snapshot``, so a round-tripped snapshot must be byte-reproducible — it is,
    the snapshot holds only JSON-safe scalars (``complexity_gate._json_safe``). A malformed object
    raises (ValueError/KeyError/TypeError); the caller maps it to exit 2 (bad input)."""
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"gate file {path!r}: not a JSON object")
    if not isinstance(obj.get("input_snapshot"), dict):
        raise ValueError(f"gate file {path!r}: input_snapshot must be a JSON object")
    return complexity_gate.GateDecision(
        decision=obj["decision"],
        reason_codes=tuple(obj.get("reason_codes", ())),
        input_snapshot=obj["input_snapshot"],
        policy_digest=obj["policy_digest"],
    )


class PlanStale(Exception):
    """#470 §2b enforcement refusal: a build gate no longer matches its live plan file. Carries a
    structured ``code`` — ``gate_stale_for_plan`` (the live plan's digest differs from the digest the
    gate recorded at mint — the plan was revised, so the gate must be re-run) or
    ``gate_missing_plan_digest`` (a pre-#470 gate that recorded no plan digest — a security control
    never silently passes on absent evidence). The CLI maps both to ``EXIT_ENFORCEMENT`` (4)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def mint_plan_context(gate_decision, plan_content: str, *, run_id=None, correlation_id=None):
    """#470 §2b — mint the canonical plan context INTERNALLY (no caller-assembled context object
    crosses the dispatch boundary) and ENFORCE gate-freshness against the live plan.

    Sources, per key (design §2b — the plan file alone cannot mint all four): ``risk_level`` +
    ``file_count`` from the live ``plan_content`` via ``plan_lib.parse_tasks`` — ``risk_level`` is the
    aggregate (``high`` if ANY task is high, else ``standard``) and ``file_count`` is the count of
    DISTINCT files declared across all tasks; ``complexity`` + ``lines`` are copied from the gate
    decision's OWN authenticated snapshot (the gate already authenticated them — one source of truth,
    no re-fetch). The sibling gate-minting step supplies ``plan_est`` facts that agree with the parsed
    plan, so ``verified_decision``'s later cross-check of the two plan-derived facts holds on a fresh
    plan.

    Freshness (R4′, fail-closed): recompute the live plan's ``plan_content_digest`` and compare it to
    the digest the gate RECORDED at mint. An ABSENT recorded digest (a pre-#470 gate) raises
    ``PlanStale('gate_missing_plan_digest')``; a MISMATCH raises ``PlanStale('gate_stale_for_plan')``.
    A byte-identical plan is the "nothing changed" case — the old gate IS current — and passes.

    Returns ``(plan_context, freshness_record)``. ``plan_context`` is exactly the canonical
    ``REQUIRED_PLAN_CONTEXT_KEYS`` mapping (dispatch re-authenticates it via ``verified_decision``);
    ``freshness_record`` is the audit tuple (gate ``policy_digest``, live plan digest, run_id,
    correlation_id) dispatch records alongside the receipt. Raises ``plan_lib.PlanFormatError`` on an
    unparseable plan — the CLI maps it to the malformed-input class (exit 2)."""
    snapshot = gate_decision.input_snapshot
    recorded = snapshot.get("plan_digest")
    if not recorded:  # None or "" — a pre-#470 gate recorded no plan digest: fail closed, distinctly
        raise PlanStale(
            "gate_missing_plan_digest",
            "gate decision recorded no plan digest (pre-#470 gate) — re-run the complexity gate so "
            "it binds the live plan before dispatching a build seat")
    live_digest = complexity_gate.plan_content_digest(plan_content)
    if live_digest != recorded:
        raise PlanStale(
            "gate_stale_for_plan",
            "live plan digest differs from the gate's recorded digest — the plan was revised; "
            "re-run the complexity gate to authorize this build dispatch")
    tasks = plan_lib.parse_tasks(plan_content)  # PlanFormatError bubbles → CLI exit 2 (malformed)
    risk_level = "high" if any(t.risk_level == "high" for t in tasks) else "standard"
    file_count = len({f for t in tasks for f in t.files})
    plan_context = {
        "risk_level": risk_level,
        "complexity": snapshot.get("complexity"),
        "lines": snapshot.get("lines"),
        "file_count": file_count,
    }
    freshness = {
        "gate_policy_digest": gate_decision.policy_digest,
        "plan_digest": live_digest,
        "run_id": run_id,
        "correlation_id": correlation_id,
    }
    return plan_context, freshness


def _git_runner(cmd, env=None):
    """WorktreeManager's injected git runner: ``(rc, out, err)``. Live/#472 path only."""
    proc = subprocess.run(list(cmd), capture_output=True, text=True, env=env, check=False)
    return proc.returncode, proc.stdout, proc.stderr


def _run_supervised(args, pe, snap, manifest, quota, audit, paths, repo_root,
                    prompt, gate_decision, plan_context, *, resolved_table: ResolvedTable) -> dict:
    """#470 §1 provisioning — construct the ``Supervisor`` (quota coordinator, registry/capture
    roots, tmux socket from the same config the CLI already resolved) + the seat's git worktree via
    ``WorktreeManager``, then run ``supervised_dispatch``. The provider-touching steps (probe-session
    spawn, ``supervisor.launch``) are the RUN_LIVE cell / #472 proving ground; the CANARY ORDERING
    and refusal semantics they wrap are unit-tested against ``supervised_dispatch`` directly. Any
    provisioning failure fails CLOSED to a structured exit 5 (never a bare traceback, never a silent
    inherit)."""
    ce = args.correlation_id
    try:
        from phase_executor.worktree import planned_path  # noqa: PLC0415
        targets = pe.routing.eligible_targets(args.seat, snap, author_provider=args.author_provider)
        # Step-11 H1 — mutating-eligibility filter (the chain-aware-skip idiom applied to the
        # FS-sandbox constraint): a mutating seat may only launch on a sandboxed provider, so
        # non-sandboxed chain entries are SKIPPED here (the shipped table's build primary is
        # claude — without this filter every primary-tier build refuses at STEP 0 and the codex
        # chain entry sits unused). Exhaustion is a handled hard failure, never a silent pass.
        sandboxed = [t for t in targets
                     if pe.PROVIDER_ENGINE.get(t["lane"]["provider"], t["lane"]["provider"])
                     in MUTATING_FS_SANDBOXED]
        if not sandboxed:
            return _err(EXIT_AVAILABILITY, "no_sandboxed_mutating_lane",
                        f"mutating seat {args.seat!r}: no FS-sandboxed provider in its chain "
                        f"(allowlist: {sorted(MUTATING_FS_SANDBOXED)}) — declare a codex lane or "
                        f"ship the FS-sandbox child; resolved table "
                        f"{resolved_table.source} at {resolved_table.path}", retryable=False,
                        correlation_id=ce, audit_path=str(audit.path))
        target = sandboxed[0]
        lane = target["lane"]
        engine = pe.PROVIDER_ENGINE.get(lane["provider"], lane["provider"])
        eff = pe.contract.resolve_effort(target["model"], args.effort, engine=engine)

        base = Path(repo_root)
        registry_root = base / ".rawgentic" / "runtime" / "registry"
        wt_root = base / ".rawgentic" / "runtime" / "worktrees"
        registry_root.mkdir(parents=True, exist_ok=True)
        wm = pe.WorktreeManager(_git_runner, forbid_tmp=True)

        # attempt token for the seat's worktree identity (distinct from launch()'s capture attempt).
        attempt = f"0-{uuid.uuid4().hex[:8]}"
        identity = pe.WorktreeIdentity(run_id=args.run_id, seat=args.seat, attempt=attempt)
        planned_wt = planned_path(str(wt_root), identity)
        profile = pe.contract.profile_from_manifest(manifest, engine=engine, worktree=planned_wt)
        final_argv = compose_supervised_argv(
            pe.ADAPTERS, engine, target["model"], effort=eff.native, profile=profile,
            worktree=planned_wt, containment_root=str(wt_root))

        rc, out, _err_txt = _git_runner(["git", "-C", str(repo_root), "rev-parse", "HEAD"])
        if rc != 0:
            return _err(EXIT_INTERNAL, "supervised_provision_failed",
                        "cannot resolve base_sha (git rev-parse HEAD)", retryable=False,
                        correlation_id=ce, audit_path=str(audit.path))
        base_sha = out.strip()

        # #638: HerdrBackend is constructed UNCONDITIONALLY (cheap — no connection opens
        # until a method call) so this supervisor can always correctly resolve/recover a
        # herdr-backed record even if the config gate later changes; only NEW launches on
        # the build seat with the gate on actually get routed to it (terminal_backend below).
        supervisor = pe.TmuxSupervisor(
            snapshot=snap, quota=quota, capture_root=paths["capture_root"],
            registry_root=str(registry_root), worktree_manager=wm,
            pane_env={"PYTHONPATH": _pane_pythonpath()},
            herdr_backend=pe.HerdrBackend(workspace_id=os.environ.get("HERDR_WORKSPACE_ID"),
                                          pane_env={"PYTHONPATH": _pane_pythonpath()}))
        seat_role = (snap.seat(args.seat).get("role") if snap else None)
        terminal_backend = select_launch_terminal_backend(
            seat_role, resolve_terminal_backend(repo_root))

        pool = lane["pool"]
        account = lane.get("credential_ref") or "default"

        def probe_session(*, composition, probe_plan, snapshot_dir):
            return supervisor.probe_session(
                composition, probe_plan, snapshot_dir=snapshot_dir,
                quota=quota, pool=pool, account=account)

        def provision():
            # fresh provision; a resumed run re-derives the handle from the registry by run_id+seat
            # (design §2 — the resume protocol wiring is §4's task; fresh is the W7 path).
            handle = wm.create(str(repo_root), identity, base_sha, root=str(wt_root))
            return identity, handle

        def behavioral_probe(*, composition, snapshot_dir):  # noqa: ARG001 — signature is the seam contract
            # #556: the real codex sandboxed write-probe (RUN_LIVE — needs codex on PATH; without it
            # the runner raises and supervised_dispatch fail-closes the codex mutating launch).
            return codex_behavioral_probe(adapters=pe.ADAPTERS, model=target["model"],
                                          effort=eff.native, wt_root=str(wt_root))

        # snapshot_dir: the plugin registration root (its hooks.json digest is the pinned
        # EXPECTED_REGISTRATION_DIGEST). A frozen read-only STAGING copy is the #472 hardening.
        return supervised_dispatch(
            seat=args.seat, prompt=prompt, run_id=args.run_id, correlation_id=ce,
            effort=args.effort, timeout=args.timeout, engine=engine, profile=profile,
            final_argv=final_argv, snapshot_dir=str(repo_root),
            capture_root=paths["capture_root"], audit=audit,
            canary=pe.canary, canary_evidence=pe.canary_evidence, supervisor=supervisor,
            probe_session=probe_session, provision=provision, behavioral_probe=behavioral_probe,
            gate_decision=gate_decision, plan_context=plan_context,
            mk_nonce=lambda: uuid.uuid4().hex,
            mk_probe_cid=lambda cls: f"probe-{uuid.uuid4().hex[:8]}",
            containment_root=str(wt_root),
            target=target, snapshot=snap, enforce=pe.enforce,
            author_provider=args.author_provider,
            terminal_backend=terminal_backend)
    except pe.routing.RoutingError as e:
        return _err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False,
                    correlation_id=ce, audit_path=str(audit.path))
    except pe.contract.CompositionError as e:
        # Step-11: CompositionError subclasses RuntimeError, NOT ValueError — without this
        # clause a compose-time containment refusal escaped as a bare traceback instead of
        # the design's documented structured exit 5.
        return _err(EXIT_INTERNAL, "composition_refused", str(e), retryable=False,
                    correlation_id=ce, audit_path=str(audit.path))
    except (ValueError, OSError) as e:
        return _err(EXIT_INTERNAL, "supervised_provision_failed", f"{type(e).__name__}: {e}",
                    retryable=False, correlation_id=ce, audit_path=str(audit.path))


def _run_resume(args, pe, snap, quota, audit, paths, repo_root, prompt) -> dict:
    """#559 AC2a: provision a fresh worktree + supervisor and resume the given claude provider
    session via ``resume_dispatch`` (design §2.5). A fresh worktree, a RESUMED provider session
    (``--resume`` restores the conversation, not the cwd). Provisioning failures fail CLOSED to a
    structured exit 5. The refusal semantics (claude-only, non-mutating) live in resume_dispatch
    and are unit-tested there; this is the live-provisioning seam (RUN_LIVE / CELL-2)."""
    ce = args.correlation_id
    try:
        from phase_executor.worktree import planned_path  # noqa: PLC0415
        targets = pe.routing.eligible_targets(args.seat, snap, author_provider=args.author_provider)
        if not targets:
            return _err(EXIT_MALFORMED, "routing_table_invalid",
                        f"resume seat {args.seat!r}: no eligible target", retryable=False,
                        correlation_id=ce, audit_path=str(audit.path))
        target = targets[0]  # resume the primary; resume_dispatch refuses a non-claude engine
        lane = target["lane"]
        engine = pe.PROVIDER_ENGINE.get(lane["provider"], lane["provider"])

        base = Path(repo_root)
        registry_root = base / ".rawgentic" / "runtime" / "registry"
        wt_root = base / ".rawgentic" / "runtime" / "worktrees"
        registry_root.mkdir(parents=True, exist_ok=True)
        wm = pe.WorktreeManager(_git_runner, forbid_tmp=True)
        attempt = f"0-{uuid.uuid4().hex[:8]}"
        identity = pe.WorktreeIdentity(run_id=args.run_id, seat=args.seat, attempt=attempt)
        planned_wt = planned_path(str(wt_root), identity)
        manifest = snap.seat(args.seat).get("manifest") or {}
        base_profile = pe.contract.profile_from_manifest(manifest, engine=engine, worktree=planned_wt)
        # resume composition: session_policy=resume; mutating carried through so resume_dispatch
        # refuses a mutating seat loud (a resumed mutating job needs a fresh behavioral canary).
        resume_profile = pe.contract.LaunchProfile(
            session_policy="resume", mutating=bool(base_profile.mutating),
            worktree=base_profile.worktree, tool_grants=tuple(base_profile.tool_grants or ()),
            max_budget_usd=base_profile.max_budget_usd, max_tokens=base_profile.max_tokens)
        object.__setattr__(resume_profile, "effective_grants",
                           tuple(getattr(base_profile, "effective_grants", ()) or ()))

        rc, out, _t = _git_runner(["git", "-C", str(repo_root), "rev-parse", "HEAD"])
        if rc != 0:
            return _err(EXIT_INTERNAL, "resume_provision_failed",
                        "cannot resolve base_sha (git rev-parse HEAD)", retryable=False,
                        correlation_id=ce, audit_path=str(audit.path))
        base_sha = out.strip()
        # #638: herdr_backend is configured here too (unconditionally, cheap) even though
        # resume_dispatch below always refuses a mutating profile before ever launching — a
        # build-seat job is never resumed through this path, so no NEW herdr launch can
        # originate here. Configuring it anyway keeps this supervisor instance consistent
        # with the other two sites and correct if this path is ever extended to touch an
        # existing build-seat record.
        supervisor = pe.TmuxSupervisor(
            snapshot=snap, quota=quota, capture_root=paths["capture_root"],
            registry_root=str(registry_root), worktree_manager=wm,
            pane_env={"PYTHONPATH": _pane_pythonpath()},
            herdr_backend=pe.HerdrBackend(workspace_id=os.environ.get("HERDR_WORKSPACE_ID"),
                                          pane_env={"PYTHONPATH": _pane_pythonpath()}))

        def provision():
            handle = wm.create(str(repo_root), identity, base_sha, root=str(wt_root))
            return identity, handle

        return resume_dispatch(
            seat=args.seat, prompt=prompt, run_id=args.run_id, correlation_id=ce,
            resume_session_id=args.resume_session_id, effort=args.effort, timeout=args.timeout,
            engine=engine, profile=resume_profile, target=target, snapshot=snap,
            capture_root=paths["capture_root"], audit=audit, supervisor=supervisor,
            provision=provision, enforce=pe.enforce, author_provider=args.author_provider)
    except pe.routing.RoutingError as e:
        return _err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False,
                    correlation_id=ce, audit_path=str(audit.path))
    # F8 (#571): resume_dispatch's supervisor.launch + wm.create in provision() raise
    # SupervisorError/WorktreeError; without these a provisioning failure escaped as a bare CLI
    # traceback instead of the documented structured exit.
    except (pe.supervisor.SupervisorError, pe.worktree.WorktreeError, ValueError, OSError) as e:
        return _err(EXIT_INTERNAL, "resume_provision_failed", f"{type(e).__name__}: {e}",
                    retryable=False, correlation_id=ce, audit_path=str(audit.path))


def _do_dispatch(args) -> int:
    # Guarded import: a stale tree / missing dep fails CLOSED to exit 5 (never a silent inherit).
    try:
        pe = _import_phase_executor()
    except ImportError as e:
        return _emit(_err(EXIT_INTERNAL, "phase_executor_import_failed", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    try:
        ws_snapshot = _load_workspace_snapshot(args.workspace)  # ONE read per invocation (8a F1)
        action, _ = resolve_seat_action_from_snapshot(args.seat, ws_snapshot, args.project)
        if action != "executor":
            raise MalformedConfig(f"dispatch called on a {action!r} seat {args.seat!r} — dispatch is only valid for an executor-mode seat")
        repo_root = repo_root_from_snapshot(ws_snapshot, args.workspace, args.project)
    except MalformedConfig as e:
        return _emit(_err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    # Table resolution + path derivation: a missing/malformed table (package default OR a
    # project phaseExecutorTable override) fails CLOSED (like the import guard) rather than
    # crashing to a bare traceback (Step-8a R1/R2; #445 resolve_table).
    try:
        rt = resolve_table(repo_root, pe.routing)
        snap = rt.snapshot
        paths = derive_paths(repo_root, args.project, args.run_id, snap.pool_concurrency())
    except MalformedConfig as e:
        return _emit(_err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    except pe.routing.RoutingError as e:
        return _emit(_err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    except OSError as e:
        return _emit(_err(EXIT_INTERNAL, "routing_table_unreadable", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    except Exception as e:  # noqa: BLE001 — e.g. jsonschema.ValidationError on a schema-invalid table
        return _emit(_err(EXIT_INTERNAL, "routing_table_invalid", f"{type(e).__name__}: {e}", retryable=False,
                          correlation_id=args.correlation_id))
    # #735 F4: resolve the dispatch timeout from THIS seat's declared bound now that the
    # table is loaded. Done here, once, because all three downstream uses of args.timeout
    # read it after this point — the single place every dispatch caller routes through.
    try:
        args.timeout = resolve_dispatch_timeout(snap.seat(args.seat), args.timeout)
    except pe.routing.RoutingError as e:
        return _emit(_err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    # Trust-boundary CLI inputs: a missing/unreadable prompt or context file is bad input (exit 2).
    try:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        context = tuple(Path(c).read_text(encoding="utf-8") for c in (args.context_file or []))
    except OSError as e:
        return _emit(_err(EXIT_MALFORMED, "prompt_or_context_unreadable", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    # #464 §E / #470 §2b: build-gate evidence. A build-role seat REQUIRES an authenticated gate file
    # (--gate-file) AND the live implementation plan (--plan-file). An unreadable/malformed gate file
    # is bad input (exit 2). The plan context is minted INTERNALLY below — no caller-assembled context
    # object crosses this boundary.
    gate_decision = None
    try:
        if args.gate_file:
            gate_decision = _load_gate_decision(args.gate_file)
    except (OSError, ValueError, KeyError, TypeError) as e:
        return _emit(_err(EXIT_MALFORMED, "gate_input_unreadable", f"{type(e).__name__}: {e}",
                          retryable=False, correlation_id=args.correlation_id))
    # The live plan file is a trust-boundary input: a missing/unreadable one is bad input (exit 2).
    plan_content = None
    if args.plan_file:
        try:
            plan_content = Path(args.plan_file).read_text(encoding="utf-8")
        except OSError as e:
            return _emit(_err(EXIT_MALFORMED, "plan_file_unreadable", str(e), retryable=False,
                              correlation_id=args.correlation_id))
    # Role governs whether the plan context is minted. snapshot.seat raises RoutingError on a
    # stale/wrong-project table — map it into the taxonomy rather than leaking a traceback.
    try:
        role = snap.seat(args.seat).get("role")
    except pe.routing.RoutingError as e:
        return _emit(_err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    # #470 §2b: mint the canonical plan context internally + enforce plan-digest freshness. A build
    # seat requires the plan file; with the gate present, the mint recomputes the live plan's digest
    # and REFUSES a stale gate (gate_stale_for_plan) or a pre-#470 gate with no recorded digest
    # (gate_missing_plan_digest) — enforcement class, exit 4 — before any provider launch. A build
    # seat with no gate falls through to dispatch_seat's gate_file_required refusal (exit 2).
    plan_context = plan_freshness = None
    if role == "build":
        if plan_content is None:
            return _emit(_err(EXIT_MALFORMED, "plan_file_required",
                              f"build seat {args.seat!r} requires the live implementation plan (--plan-file)",
                              retryable=False, correlation_id=args.correlation_id))
        if gate_decision is not None:
            try:
                plan_context, plan_freshness = mint_plan_context(
                    gate_decision, plan_content,
                    run_id=args.run_id, correlation_id=args.correlation_id)
            except PlanStale as e:
                return _emit(_err(EXIT_ENFORCEMENT, e.code, str(e), retryable=False,
                                  correlation_id=args.correlation_id))
            except plan_lib.PlanFormatError as e:
                return _emit(_err(EXIT_MALFORMED, "plan_file_malformed", str(e), retryable=False,
                                  correlation_id=args.correlation_id))
    try:
        quota = pe.QuotaCoordinator(paths["permits_dir"], snap.pool_concurrency())
        audit = pe.enforce.RoutingAuditLog(paths["capture_root"], args.run_id)
    except (OSError, ValueError) as e:
        return _emit(_err(EXIT_INTERNAL, "runtime_init_failed", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    # #555 AC2 — ledger-aware choke-point (the ONE choke both the sync and supervised branches pass
    # through). Fail closed: a run whose expected-call ledger is run_closed refuses any NEW dispatch
    # BEFORE any spawn or audit append, and every accepted call is appended to the ledger
    # append-before-dispatch — "zero uninstrumented dispatch" (#472 AC1) is enforced here, not by
    # convention. The initial_digest is seeded lazily from the resolved table's config digest.
    # colocate the ledger with the audit: RoutingAuditLog sanitizes run_id for ITS dir, so the
    # ledger MUST use the identical transform or the two land in different dirs (#555 8a F10).
    run_dir = Path(paths["capture_root"]) / pe.capture.sanitize_component(args.run_id)
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        led = pe.ledger.ExpectedCallLedger(run_dir, args.run_id)
        state = led.read()
        # #474: dispatch CONSUMES the run declaration, never seeds it (the pre-flip lazy seed is
        # gone — begin-run is the one producer). An undeclared run refuses; a pre-3.93 initial
        # (architecture None) proceeds with an advisory (bounded compat — every ≤3.92 ledger is
        # an executor run's by construction); a legacy-pinned ledger is a mixed run — refuse.
        if state.initial_digest is None:
            return _emit(_err(EXIT_ENFORCEMENT, "run_not_declared",
                              f"run {args.run_id!r} has no architecture declaration — run "
                              f"`begin-run --run-id {args.run_id}` first (#474)",
                              retryable=False, correlation_id=args.correlation_id))
        if state.architecture == "legacy":
            return _emit(_err(EXIT_ENFORCEMENT, "mixed_architecture_run_refused",
                              f"run {args.run_id!r} is pinned architecture 'legacy' — executor "
                              f"dispatch into a legacy run is a mixed run (#474)",
                              retryable=False, correlation_id=args.correlation_id))
        if state.architecture is None:
            print(f"advisory: run {args.run_id!r} has a pre-3.93 ledger (no architecture) — "
                  f"treated as executor (bounded compat, #474)", file=sys.stderr)
        # S11 F1: the run pinned its config epoch at declaration — a routing-table change
        # mid-run must refuse (the begin-run idempotence rule enforced at CONSUME time too;
        # otherwise dispatch would resolve targets from a snapshot the run never declared).
        if state.initial_digest != snap.config_digest:
            return _emit(_err(EXIT_ENFORCEMENT, "run_digest_conflict",
                              f"run {args.run_id!r} declared config digest "
                              f"{state.initial_digest!r} but the current snapshot is "
                              f"{snap.config_digest!r} — a routing-table change mid-run is a "
                              f"declared-state conflict (#474)", retryable=False,
                              correlation_id=args.correlation_id))
        if state.closed:
            return _emit(_err(EXIT_ENFORCEMENT, "run_closed_dispatch_refused",
                              f"run {args.run_id!r} ledger is run_closed — new dispatch refused (#555)",
                              retryable=False, correlation_id=args.correlation_id))
        # correlation_id IS the ledger/reconcile join key — a keyless dispatch would be an
        # UNINSTRUMENTED spawn (no ledger record, an orphan at reconcile), the exact thing the
        # choke-point exists to make impossible. Refuse it here, not by convention (#555 AC2).
        if not args.correlation_id:
            return _emit(_err(EXIT_MALFORMED, "correlation_id_required",
                              "dispatch requires --correlation-id (the ledger/reconcile join key)",
                              retryable=False, correlation_id=None))
        # append-before-dispatch (dup → fail closed); the architecture assertion re-checks the
        # pin UNDER the same flock that appends (#474 — no read-then-append window)
        led.append_expected(args.seat, args.correlation_id, expected_architecture="executor")
    except pe.ledger.LedgerError as e:
        return _emit(_err(EXIT_ENFORCEMENT, "ledger_refused", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    except OSError as e:
        return _emit(_err(EXIT_INTERNAL, "ledger_unavailable", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    # #559 AC2a resume dispatch: --resume-session-id routes to the leaner claude-only resume
    # chokepoint (check_pre → launch(session_policy=resume) → await(expect_session_id) →
    # Observation → verify_post). It is ledgered like any dispatch (append_expected above); the
    # claude-only + non-mutating refusals live in resume_dispatch.
    if getattr(args, "resume_session_id", None):
        return _emit(_run_resume(args, pe, snap, quota, audit, paths, repo_root, prompt))
    # #470 §1 internal routing: inspect the resolved target's staged LaunchProfile. A MUTATING
    # profile routes to the supervised branch (gate-auth → stage-and-bind → phase-1 canary → probe
    # session → require_canary → launch, in-process) INSIDE this same CLI call — there is no second
    # entry point, so neither control can be skipped by "calling the other path". A NON-mutating
    # profile runs the existing synchronous path BYTE-IDENTICAL below.
    manifest = snap.seat(args.seat).get("manifest") or {}
    mutating = bool({"edit", "bash"} & set(manifest.get("tool_grants") or ()))
    if mutating:
        result = _run_supervised(args, pe, snap, manifest, quota, audit, paths, repo_root,
                                 prompt, gate_decision, plan_context, resolved_table=rt)
        if plan_freshness is not None and isinstance(result, dict):
            result["plan_freshness"] = plan_freshness
        return _emit(result)
    result = dispatch_seat(
        seat=args.seat, prompt=prompt, run_id=args.run_id,
        correlation_id=args.correlation_id, author_provider=args.author_provider,
        effort=args.effort, timeout=args.timeout, context=context,
        snapshot=snap, quota=quota, audit=audit, capture_root=paths["capture_root"],
        routing=pe.routing, enforce=pe.enforce, run_seat=pe.run_seat, dispatch_real=pe.dispatch_real,
        gate_decision=gate_decision, plan_context=plan_context,
        quota_timeout=pe.QuotaTimeout,
    )
    # #470 §2b audit trail: record the plan-freshness binding (gate policy_digest, live plan digest,
    # run_id, correlation_id) alongside the dispatch result. Attached to the emitted structured
    # output — the dispatch CLI's own audit surface — on every build attempt that got past the mint.
    if plan_freshness is not None and isinstance(result, dict):
        result["plan_freshness"] = plan_freshness
    return _emit(result)


def _do_probe_account(args) -> int:
    """#559 AC2a: emit the active claude account identity observation as JSON (digest + categories
    only — no raw PII). Read-only; never launches or mutates. The digest is run evidence (R8) —
    the orchestrator persists it under the gitignored run dir, never into a committed report."""
    return _emit(probe_account(args.claude_bin))


def _do_recover_run(args) -> int:
    """#559 C1: CLI wrapper for the recovery-dispatch chokepoint. The live await/relaunch is the
    RUN_LIVE seam (CELL-3b); the gate + run_closed refusal + reconcile-provenance logic is
    unit-tested against recover_run with an injected supervisor."""
    try:
        pe = _import_phase_executor()
    except ImportError as e:
        return _emit(_err(EXIT_INTERNAL, "phase_executor_import_failed", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    try:
        ws_snapshot = _load_workspace_snapshot(args.workspace)  # ONE read per invocation (8a F1)
        arch = resolve_architecture_from_snapshot(ws_snapshot)
        repo_root = repo_root_from_snapshot(ws_snapshot, args.workspace, args.project)
        snap = resolve_table(repo_root, pe.routing).snapshot
        paths = derive_paths(repo_root, args.project, args.run_id, snap.pool_concurrency())
    except MalformedConfig as e:
        return _emit(_err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    except pe.routing.RoutingError as e:
        return _emit(_err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    except OSError as e:
        return _emit(_err(EXIT_INTERNAL, "routing_table_unreadable", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    # #474: the architecture gate runs BEFORE supervisor construction — rollback stops recovery
    # relaunches too ("lever takes effect" on every spawn path). Workspace architecture must be
    # executor; the ledger pin must be executor (or a pre-3.93 None on a VALID existing initial —
    # an absent/deleted/corrupt/symlinked ledger refuses pre-launch, never conflated with compat).
    if arch != "executor":
        return _emit(_err(EXIT_ENFORCEMENT, "legacy_architecture_recover_refused",
                          "legacy architecture — executor recovery refused; the joint rollback "
                          "stops recovery relaunches too (#474)", retryable=False,
                          correlation_id=args.correlation_id))
    run_dir_gate = Path(paths["capture_root"]) / pe.capture.sanitize_component(args.run_id)
    try:
        gate_state = pe.ledger.ExpectedCallLedger(run_dir_gate, args.run_id).read()
    except pe.ledger.LedgerError as e:
        return _emit(_err(EXIT_ENFORCEMENT, "ledger_refused",
                          f"recover-run: ledger unreadable/invalid — refusing before any launch "
                          f"({e})", retryable=False, correlation_id=args.correlation_id))
    if gate_state.initial_digest is None:
        return _emit(_err(EXIT_ENFORCEMENT, "run_not_declared",
                          f"run {args.run_id!r} has no valid architecture declaration — recovery "
                          f"refused before launch (#474)", retryable=False,
                          correlation_id=args.correlation_id))
    if gate_state.architecture == "legacy":
        return _emit(_err(EXIT_ENFORCEMENT, "mixed_architecture_run_refused",
                          f"run {args.run_id!r} is pinned architecture 'legacy' — executor "
                          f"recovery into a legacy run is a mixed run (#474)", retryable=False,
                          correlation_id=args.correlation_id))
    if gate_state.architecture is None:
        print(f"advisory: run {args.run_id!r} has a pre-3.93 ledger (no architecture) — "
              f"treated as executor (bounded compat, #474)", file=sys.stderr)
    if gate_state.closed:
        # S11 F5: refuse a run_closed ledger AT the preflight, before supervisor construction —
        # terminal state wins over any epoch comparison; recover_run's own refusal is the backstop.
        return _emit(_err(EXIT_ENFORCEMENT, "run_closed_recover_refused",
                          f"run {args.run_id!r} ledger is run_closed — recovery refused (#559)",
                          retryable=False, correlation_id=args.correlation_id))
    if gate_state.initial_digest != snap.config_digest:
        # S11 R2-2: recovery must relaunch on the EPOCH the run declared — a routing-table
        # change mid-run is a declared-state conflict at every consume site, recovery included.
        return _emit(_err(EXIT_ENFORCEMENT, "run_digest_conflict",
                          f"run {args.run_id!r} declared config digest "
                          f"{gate_state.initial_digest!r} but the current snapshot is "
                          f"{snap.config_digest!r} — recovery refused (#474)", retryable=False,
                          correlation_id=args.correlation_id))
    from phase_executor.registry import JobRegistry, RegistryCorrupt  # noqa: PLC0415
    from phase_executor.worktree import WorktreeManager  # noqa: PLC0415
    base = Path(repo_root)
    registry_root = base / ".rawgentic" / "runtime" / "registry"
    run_dir = Path(paths["capture_root"]) / pe.capture.sanitize_component(args.run_id)
    try:
        registry = JobRegistry(str(registry_root))
        audit = pe.enforce.RoutingAuditLog(paths["capture_root"], args.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        led = pe.ledger.ExpectedCallLedger(run_dir, args.run_id)
        ledger_closed = led.is_closed()
        wm = pe.WorktreeManager(_git_runner, forbid_tmp=True)
        # #638: herdr_backend configured unconditionally — recover()'s per-record loop can
        # resolve an EXISTING herdr-backed record correctly (record.terminal_backend), even
        # though this path never launches a NEW job.
        supervisor = pe.TmuxSupervisor(
            snapshot=snap, quota=pe.QuotaCoordinator(paths["permits_dir"], snap.pool_concurrency()),
            capture_root=paths["capture_root"], registry_root=str(registry_root),
            registry=registry, worktree_manager=wm,
            pane_env={"PYTHONPATH": _pane_pythonpath()},
            herdr_backend=pe.HerdrBackend(workspace_id=os.environ.get("HERDR_WORKSPACE_ID"),
                                          pane_env={"PYTHONPATH": _pane_pythonpath()}))
    except (OSError, ValueError, RegistryCorrupt) as e:
        return _emit(_err(EXIT_INTERNAL, "runtime_init_failed", f"{type(e).__name__}: {e}",
                          retryable=False, correlation_id=args.correlation_id))
    return _emit(recover_run(
        run_id=args.run_id, supervisor=supervisor, snapshot=snap, audit=audit,
        routing=pe.routing, enforce=pe.enforce, ledger_closed=ledger_closed,
        correlation_id=args.correlation_id))


def _do_collect_work_product(args) -> int:
    """#559 AC1: CLI wrapper — resolve the run's registry/audit + a WorktreeManager, then run the
    two-phase collect_work_product. The live git/CAS seam (CELL-1); the two-phase + idempotency
    logic is unit-tested against collect_work_product with injected fakes."""
    try:
        pe = _import_phase_executor()
    except ImportError as e:
        return _emit(_err(EXIT_INTERNAL, "phase_executor_import_failed", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    try:
        repo_root = resolve_repo_root(args.workspace, args.project)
        snap = resolve_table(repo_root, pe.routing).snapshot
        paths = derive_paths(repo_root, args.project, args.run_id, snap.pool_concurrency())
    except MalformedConfig as e:
        return _emit(_err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    except pe.routing.RoutingError as e:
        return _emit(_err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    except OSError as e:
        return _emit(_err(EXIT_INTERNAL, "routing_table_unreadable", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    from phase_executor.registry import JobRegistry, RegistryCorrupt  # noqa: PLC0415
    from phase_executor.worktree import WorktreeManager  # noqa: PLC0415
    base = Path(repo_root)
    registry_root = base / ".rawgentic" / "runtime" / "registry"
    try:
        registry = JobRegistry(str(registry_root))
        manager = WorktreeManager(_git_runner, forbid_tmp=True)
        audit = pe.enforce.RoutingAuditLog(paths["capture_root"], args.run_id)
    except (OSError, ValueError, RegistryCorrupt) as e:
        return _emit(_err(EXIT_INTERNAL, "runtime_init_failed", f"{type(e).__name__}: {e}",
                          retryable=False, correlation_id=args.correlation_id))
    intent_dir = (Path(paths["capture_root"]) / pe.capture.sanitize_component(args.run_id)
                  / "work-product-intents")
    return _emit(collect_work_product(
        run_id=args.run_id, session_name=args.session_name, target_ref=args.target_ref,
        expected_target_sha=args.expected_target_sha, kind=args.kind,
        registry=registry, manager=manager, audit=audit, intent_dir=str(intent_dir),
        correlation_id=args.correlation_id, promote_paths=args.promote_paths,
        expected_feature_ref=args.expected_feature_ref))


def _do_land_work_product(args) -> int:
    """#767 Step-11 R1-F2 + #762 D1: CLI wrapper for the production guarded landing — audited
    by default (identity args REQUIRED; ``--no-audit`` is the explicit, greppable opt-out for
    standalone/ops use). The function resolves the repo and builds the run's RoutingAuditLog
    from workspace+project itself."""
    return _emit(land_work_product(
        repo=args.repo, expected_ref=args.expected_ref, pre_sha=args.pre_sha,
        new_sha=args.new_sha, temp_ref=args.temp_ref, correlation_id=args.correlation_id,
        run_id=args.run_id, workspace=args.workspace, project=args.project,
        no_audit=args.no_audit))


def _status_tail(path: Path, limit: int = 200) -> str:
    """Last non-empty line of ``path`` (≤ ``limit`` chars) — bounded read, never the whole file."""
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 1024))
        text = fh.read().decode("utf-8", errors="replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1][:limit] if lines else ""


# 8a R2#1 (High): the ONLY files the activity probe may select/echo. input.md is the raw
# prompt (written BEFORE the provider call — during the whole running window it is the
# newest file) and the pane spec/.incomplete are runner internals; tailing any of them
# into the status JSON is a prompt/config leak.
_ACTIVITY_ALLOWLIST = frozenset({"transport.stdout.txt", "stderr.txt", "output.md",
                                 "observation.json"})


def _status_activity(record, *, clock=time.time) -> Optional[dict]:
    """AC-J1f: the latest capture write (file, age, tail line) among the OUTPUT artifacts
    (``_ACTIVITY_ALLOWLIST`` — never the prompt/spec). Read-only, best-effort — a
    missing/racing capture dir is ``None``, never an error."""
    try:
        files = [p for p in Path(record.capture_dir).iterdir()
                 if p.is_file() and p.name in _ACTIVITY_ALLOWLIST]
        if not files:
            return None
        newest = max(files, key=lambda p: p.stat().st_mtime)
        return {"file": newest.name,
                "age_s": max(0, int(clock() - newest.stat().st_mtime)),
                "tail": _status_tail(newest)}
    except OSError:
        return None


def status_live_verdict(record, *, tmux, herdr, tmux_present: bool) -> tuple:
    """`(live, probe_error)` for one record on the READ-ONLY status surface (#647).

    Backends are INJECTED rather than constructed here: that is what makes the three AC4
    cases unit-testable (a closure has no substitution seam, and driving the real thing
    would need a herdr binary CI does not have).

    Resolution uses the lifted `supervisor.resolve_backend`, the SAME rule
    `TmuxSupervisor._resolve_backend` applies — the status surface cannot call the method
    itself, because constructing a supervisor builds a `JobRegistry` whose `__init__`
    mkdir/chmods the registry root, a write the AC-J3 read-only invariant forbids.

    The verdict mapping is the whole point of the fix: `probe_error` is returned ONLY when
    liveness could not be DETERMINED, because `run_status` turns a non-None `probe_error`
    into the derived `liveness_unknown` state. A CONFIRMED absence therefore reports
    `(False, None)` — byte-identical to the pre-fix behavior for an ordinary tmux
    "no sessions on this socket" (AC3).
    """
    _ensure_pe_importable()
    # phase_executor resolves at runtime via _ensure_pe_importable, so astroid cannot see
    # these names statically — a scoped false positive, not a blanket disable.
    # pylint: disable=no-name-in-module
    from phase_executor.supervisor import SupervisorError, resolve_backend  # noqa: PLC0415
    from phase_executor.terminal_backend import Liveness  # noqa: PLC0415
    # pylint: enable=no-name-in-module
    try:
        backend = resolve_backend(record.terminal_backend, tmux=tmux, herdr=herdr)
    except SupervisorError as e:
        # A herdr record with no herdr backend is a CONFIGURATION fault, not a dead job —
        # surfacing it as an indeterminate probe keeps it out of the "it exited" bucket.
        return False, f"backend unresolvable: {e}"
    if backend is tmux:
        # Checked AFTER resolution, deliberately: evaluated first (as the pre-#647 code did)
        # a HERDR record on a tmux-less host reported "tmux unavailable on this host" — a
        # true degradation flag carrying a false reason.
        if not tmux_present:
            return False, "tmux unavailable on this host"
    if backend is None:
        return False, "no terminal backend available for this record"
    try:
        verdict = backend.probe_session(record.run_socket, record.session_name, timeout=10)
    except Exception as e:  # noqa: BLE001 — probe_session is contract-bound not to raise;
        # if one ever does, ONE bad record must not empty the whole status view.
        return False, f"liveness probe failed: {type(e).__name__}"
    if verdict is Liveness.CONFIRMED_ALIVE:
        return True, None
    if verdict is Liveness.CONFIRMED_GONE:
        return False, None
    return False, "liveness indeterminate: the probe could not determine liveness"


def _do_status(args) -> int:
    """#471 W8 (AC-J2): the read-only run-status verb — JSON derived from the job registry +
    launch specs + capture dirs. AC-J3: reads only; never constructs a supervisor, never
    upserts, kills, or touches permits. RegistryCorrupt is a structured exit 5 (fail-loud,
    never an empty view — registry.py's own contract)."""
    try:
        pe = _import_phase_executor()
    except ImportError as e:
        return _emit(_err(EXIT_INTERNAL, "phase_executor_import_failed", str(e), retryable=False))
    try:
        repo_root = resolve_repo_root(args.workspace, args.project)
    except MalformedConfig as e:
        return _emit(_err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False))
    if not Path(repo_root).is_dir():
        # declared-but-missing project dir: the dispatch path's exit-2 class
        # (test_dispatch_path_declared_missing_exit2), kept consistent here.
        return _emit(_err(EXIT_MALFORMED, "malformed_config",
                          f"project {args.project!r} path {str(repo_root)!r} does not exist",
                          retryable=False))
    registry_root = Path(repo_root) / ".rawgentic" / "runtime" / "registry"
    out = {"run_id": args.run, "generated_at": int(time.time()), "seats": [], "exit": EXIT_OK}
    try:
        # registry.read_all, never a JobRegistry: its __init__ mkdir/chmods the root —
        # a metadata write the AC-J3 read-only surface must not perform (8a R2#2).
        records = [r for r in pe.registry_read_all(str(registry_root))
                   if r.identity.run_id == args.run]
    except pe.RegistryCorrupt as e:
        return _emit(_err(EXIT_INTERNAL, "registry_corrupt", str(e), retryable=False))
    if not records:
        return _emit(out)

    has_tmux = shutil.which("tmux") is not None
    # #647: both backends, constructed the same way the launch/recover/reap sites do
    # (:2157/:2266/:2574). Construction opens no connection, so building the herdr one
    # unconditionally is cheap and lets a herdr-backed record resolve correctly even when
    # the config gate is currently off.
    _status_tmux_backend = pe.TmuxBackend()
    _status_herdr_backend = pe.HerdrBackend(
        workspace_id=os.environ.get("HERDR_WORKSPACE_ID"))

    def live_fn(record) -> tuple:
        """(live, probe_error) — a failed/unavailable probe is NEVER silently 'dead'
        (gpt-diff A3): the row carries the degradation. #647: a thin binding over the
        module-level `status_live_verdict`, which resolves the record's OWN backend instead
        of assuming tmux."""
        return status_live_verdict(record, tmux=_status_tmux_backend,
                                   herdr=_status_herdr_backend, tmux_present=has_tmux)

    def spec_fn(record) -> tuple:
        """(spec, status) — missing vs corrupt launch specs stay distinguishable per row
        (gpt-diff A5); a corrupt spec never kills the whole run view."""
        p = registry_root / "specs" / f"{pe.registry_session_name(record.identity)}.json"
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh), "ok"
        except FileNotFoundError:
            return None, "missing"
        except (OSError, ValueError):
            return None, "corrupt"

    out["seats"] = pe.supervisor.run_status(
        records, live_fn=live_fn, sentinel_fn=pe.supervisor.read_sentinel,
        spec_fn=spec_fn, activity_fn=_status_activity, clock=time.time)
    return _emit(out)


def _ledger_for(args, pe):
    """Resolve (repo_root, ExpectedCallLedger) for the run — shared by close-run + reconcile.
    Raises MalformedConfig / RoutingError (mapped to exit 2 by the callers)."""
    repo_root = resolve_repo_root(args.workspace, args.project)
    if not Path(repo_root).is_dir():
        raise MalformedConfig(f"project {args.project!r} path {str(repo_root)!r} does not exist")
    snap = resolve_table(repo_root, pe.routing).snapshot
    paths = derive_paths(Path(repo_root), args.project, args.run_id, snap.pool_concurrency())
    # same sanitize as RoutingAuditLog (dir colocation, #555 8a F10)
    run_dir = Path(paths["capture_root"]) / pe.capture.sanitize_component(args.run_id)
    return repo_root, snap, paths, pe.ledger.ExpectedCallLedger(run_dir, args.run_id)


def _do_close_run(args) -> int:
    """#555: append the terminal ``run_closed`` marker so no further dispatch is accepted and a
    ``reconcile --mode final`` can run. Idempotent-guarded: a double close fails closed (exit 2)."""
    try:
        pe = _import_phase_executor()
    except ImportError as e:
        return _emit(_err(EXIT_INTERNAL, "phase_executor_import_failed", str(e), retryable=False))
    try:
        _, snap, _, led = _ledger_for(args, pe)
    except MalformedConfig as e:
        return _emit(_err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False))
    except pe.routing.RoutingError as e:
        return _emit(_err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False))
    try:
        # #474: close-run CONSUMES the run declaration exactly like dispatch — the zero-dispatch
        # close seed is gone (closing a never-declared run is meaningless; begin-run declares).
        state = led.read()
        if state.initial_digest is None:
            return _emit(_err(EXIT_ENFORCEMENT, "run_not_declared",
                              f"run {args.run_id!r} has no architecture declaration — nothing to "
                              f"close; `begin-run` declares a run (#474)", retryable=False))
        if state.architecture == "legacy":
            return _emit(_err(EXIT_ENFORCEMENT, "mixed_architecture_run_refused",
                              f"run {args.run_id!r} ledger is pinned 'legacy' — an unreachable "
                              f"state (legacy runs write no ledger, #474); refusing to close",
                              retryable=False))
        if state.architecture is None:
            print(f"advisory: run {args.run_id!r} has a pre-3.93 ledger (no architecture) — "
                  f"treated as executor (bounded compat, #474)", file=sys.stderr)
        led.append_run_closed()
    except pe.ledger.LedgerError as e:
        return _emit(_err(EXIT_MALFORMED, "ledger_refused", str(e), retryable=False))
    except OSError as e:
        return _emit(_err(EXIT_INTERNAL, "ledger_unavailable", str(e), retryable=False))
    return _emit({"run_id": args.run_id, "run_closed": True, "exit": EXIT_OK})


def _do_begin_run(args) -> int:
    """#474: the run-start architecture declaration — the ONE producer of the ledger ``initial``
    record. Executor architecture only (a legacy run writes no ledger; its declaration is the
    run-record + session notes). Idempotence is keyed on BOTH (architecture, config_digest):
    a matching duplicate is a benign noop; a digest mismatch is a declared-state conflict."""
    try:
        pe = _import_phase_executor()
    except ImportError as e:
        return _emit(_err(EXIT_INTERNAL, "phase_executor_import_failed", str(e), retryable=False))
    try:
        ws_snapshot = _load_workspace_snapshot(args.workspace)  # ONE read per invocation (8a F1)
        arch = resolve_architecture_from_snapshot(ws_snapshot)
        repo_root = repo_root_from_snapshot(ws_snapshot, args.workspace, args.project)
        snap = resolve_table(repo_root, pe.routing).snapshot
        paths = derive_paths(repo_root, args.project, args.run_id, snap.pool_concurrency())
    except MalformedConfig as e:
        return _emit(_err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False))
    except pe.routing.RoutingError as e:
        return _emit(_err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False))
    except OSError as e:
        return _emit(_err(EXIT_INTERNAL, "routing_table_unreadable", str(e), retryable=False))
    if arch != "executor":
        return _emit(_err(EXIT_ENFORCEMENT, "legacy_architecture_begin_refused",
                          "legacy architecture — executor run machinery unused; legacy runs are "
                          "declared in session notes + the run-record architecture field (#474)",
                          retryable=False))
    run_dir = Path(paths["capture_root"]) / pe.capture.sanitize_component(args.run_id)
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        led = pe.ledger.ExpectedCallLedger(run_dir, args.run_id)
        state = led.read()
        if state.initial_digest is not None:
            # 8a F3: idempotence requires BOTH fields to match EXACTLY — a pre-3.93 unpinned
            # (architecture-less) initial is NOT an executor declaration; begin-run must never
            # certify it (consumers keep their None compat; the producer stays strict).
            if state.architecture is None:
                return _emit(_err(EXIT_ENFORCEMENT, "begin_run_unpinned_ledger",
                                  f"run {args.run_id!r} has a pre-3.93 ledger with no architecture "
                                  f"pin — begin-run cannot re-declare it; dispatch/recover/close "
                                  f"keep their bounded compat (#474)", retryable=False))
            if state.architecture == "executor" and state.initial_digest == snap.config_digest:
                return _emit({"run_id": args.run_id, "architecture": "executor",
                              "already_declared": True, "exit": EXIT_OK})
            return _emit(_err(EXIT_ENFORCEMENT, "begin_run_digest_conflict",
                              f"run {args.run_id!r} already declared (architecture="
                              f"{state.architecture!r}, digest {state.initial_digest!r}) — "
                              f"conflicts with the current config digest {snap.config_digest!r}; "
                              f"a routing-table change mid-run is a declared-state conflict (#474)",
                              retryable=False))
        try:
            led.append_initial(snap.config_digest, architecture="executor")
        except pe.ledger.LedgerError:
            # a concurrent begin-run seeded it — benign iff it seeded the SAME declaration
            state = led.read()
            if state.initial_digest != snap.config_digest or state.architecture != "executor":
                raise
            return _emit({"run_id": args.run_id, "architecture": "executor",
                          "already_declared": True, "exit": EXIT_OK})
    except pe.ledger.LedgerError as e:
        return _emit(_err(EXIT_ENFORCEMENT, "ledger_refused", str(e), retryable=False))
    except OSError as e:
        return _emit(_err(EXIT_INTERNAL, "ledger_unavailable", str(e), retryable=False))
    return _emit({"run_id": args.run_id, "architecture": "executor",
                  "config_digest": snap.config_digest, "exit": EXIT_OK})


# reconcile-verb anomaly buckets that a PROVISIONAL (mid-run) check tolerates as in-flight
# (an expected call not yet observed); every OTHER bucket is a hard breach that fails both modes.
_PROVISIONAL_TOLERATED = frozenset({"missing_receipt", "missing_obs"})


def _do_reconcile(args) -> int:
    """#555 AC3: bind the expected-call ledger against the routing audit and report a verdict.
    ``--mode provisional`` tolerates an open ledger and in-flight (not-yet-observed) calls, failing
    only on a hard breach; ``--mode final`` requires ``run_closed`` last AND zero anomalies. Exit
    0 = reconciled, 1 = anomalies, 2 = usage/malformed."""
    try:
        pe = _import_phase_executor()
    except ImportError as e:
        return _emit(_err(EXIT_INTERNAL, "phase_executor_import_failed", str(e), retryable=False))
    try:
        _, _, paths, led = _ledger_for(args, pe)
    except MalformedConfig as e:
        return _emit(_err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False))
    except pe.routing.RoutingError as e:
        return _emit(_err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False))
    try:
        state = led.read()
        audit = pe.enforce.RoutingAuditLog(paths["capture_root"], args.run_id)
        records = audit.records()
    except pe.ledger.LedgerError as e:
        return _emit(_err(EXIT_MALFORMED, "ledger_refused", str(e), retryable=False))
    except (OSError, ValueError) as e:
        return _emit(_err(EXIT_MALFORMED, "audit_unreadable", f"{type(e).__name__}: {e}",
                          retryable=False))
    final = args.mode == "final"
    if final and not state.closed:
        return _emit({"run_id": args.run_id, "mode": args.mode, "reconciled": False,
                      "reason": "ledger not run_closed", "exit": EXIT_ANOMALY})
    if state.initial_digest is None:
        # no dispatch recorded yet: provisional passes vacuously, final fails (nothing closed a run)
        return _emit({"run_id": args.run_id, "mode": args.mode, "reconciled": not final,
                      "reason": "no initial_digest (no dispatch recorded)",
                      "exit": EXIT_OK if not final else EXIT_ANOMALY})
    expected = [e.as_expected_call() for e in state.expected]
    try:
        rec = pe.enforce.reconcile_run(expected, records, initial_digest=state.initial_digest,
                                       require_nonempty=final, run_id=args.run_id)
    except ValueError as e:  # duplicate expected tuples / broken epoch chain — fail-closed anomaly
        return _emit({"run_id": args.run_id, "mode": args.mode, "reconciled": False,
                      "reason": f"reconcile_run: {e}", "exit": EXIT_ANOMALY})
    # #762 R5-C: every landing bucket enumerated explicitly — a new Reconcile field that never
    # reaches this dict would be a silently-invisible anomaly class.
    buckets = {"missing_receipt": rec.missing_receipt, "failed_precheck": rec.failed_precheck,
               "missing_obs": rec.missing_obs, "binding_mismatch": rec.binding_mismatch,
               "duplicate_nonce": rec.duplicate_nonce, "duplicate": rec.duplicate,
               "unverified": rec.unverified, "unaudited_digest": rec.unaudited_digest,
               "orphan": rec.orphan, "orphan_work_product": rec.orphan_work_product,
               "duplicate_work_product": rec.duplicate_work_product,
               "missing_work_product": rec.missing_work_product,
               "unlanded_work_product": rec.unlanded_work_product,
               "orphan_landing": rec.orphan_landing,
               "landing_mismatch": rec.landing_mismatch,
               "landing_conflict": rec.landing_conflict}
    present = {k: list(v) for k, v in buckets.items() if v}
    if final:
        reconciled = rec.ok
    else:
        # provisional: fail only on a HARD breach; tolerate not-yet-observed in-flight calls
        # (#762 R5-C: the landing buckets are hard — they are deliberately NOT tolerated)
        reconciled = not any(k not in _PROVISIONAL_TOLERATED for k in present)
    return _emit({"run_id": args.run_id, "mode": args.mode, "closed": state.closed,
                  "expected_calls": len(expected), "reconciled": reconciled,
                  "anomalies": present,
                  # R5-D: report-only bucket — visible, named, never a verdict input
                  "report": {"pre_cutover_unverifiable": list(rec.pre_cutover_unverifiable)},
                  "exit": EXIT_OK if reconciled else EXIT_ANOMALY})


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="executor_routing_lib")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("resolve-seat", help="decide inherit|executor|driver_only for a seat")
    r.add_argument("--seat", required=True)
    r.add_argument("--workspace", required=True)
    r.add_argument("--project", required=True)
    r.set_defaults(fn=_do_resolve)

    d = sub.add_parser("dispatch", help="run a seat through the executor")
    d.add_argument("--seat", required=True)
    d.add_argument("--prompt-file", required=True, dest="prompt_file")
    d.add_argument("--run-id", required=True, dest="run_id")
    d.add_argument("--context-file", action="append", dest="context_file")
    d.add_argument("--gate-file", dest="gate_file")          # #464 §E: #429 GateDecision JSON (build seat)
    d.add_argument("--plan-file", dest="plan_file")          # #470 §2b: live impl-plan.md; context minted internally
    d.add_argument("--correlation-id", dest="correlation_id")
    d.add_argument("--author-provider", dest="author_provider")
    d.add_argument("--resume-session-id", dest="resume_session_id")  # #559 AC2a: claude-only resume
    d.add_argument("--effort")
    # #735 F4: no flat default. None means "resolve from the seat's bounds.timeout_s"
    # (resolve_dispatch_timeout), so a caller who omits the flag gets the seat's
    # sanctioned budget rather than a sixth of it.
    d.add_argument("--timeout", type=float, default=None)
    d.add_argument("--workspace", required=True)
    d.add_argument("--project", required=True)
    d.set_defaults(fn=_do_dispatch)

    mg = sub.add_parser("mint-gate", help="#470: mint gate.json from the live plan (producer side)")
    mg.add_argument("--plan-file", required=True)
    mg.add_argument("--issue-complexity", required=True, choices=["trivial", "standard", "complex"])
    mg.add_argument("--plan-est-lines", required=True, type=int)
    mg.add_argument("--out", required=True)
    mg.set_defaults(fn=_do_mint_gate)

    pa = sub.add_parser("probe-account",
                        help="#559 AC2a: observe the active claude account identity (digest + categories, no raw PII)")
    pa.add_argument("--claude-bin", dest="claude_bin", default="claude")
    pa.set_defaults(fn=_do_probe_account)

    rr = sub.add_parser("recover-run",
                        help="#559 C1: ledgered/receipted recovery relaunch of quota_paused jobs")
    rr.add_argument("--run-id", required=True, dest="run_id")
    rr.add_argument("--correlation-id", dest="correlation_id")
    rr.add_argument("--workspace", required=True)
    rr.add_argument("--project", required=True)
    rr.set_defaults(fn=_do_recover_run)

    cw = sub.add_parser("collect-work-product",
                        help="#559 AC1 / #767: promote a completed build's work product + record an "
                             "audited binding (default: appendix prefix; --promote-path: exact "
                             "per-task paths, ENFORCED to target "
                             "refs/rawgentic/collect/<receipt-nonce> with the all-zero expected "
                             "SHA)")
    cw.add_argument("--run-id", required=True, dest="run_id")
    cw.add_argument("--session-name", required=True, dest="session_name")
    cw.add_argument("--target-ref", required=True, dest="target_ref")
    cw.add_argument("--expected-target-sha", required=True, dest="expected_target_sha")
    cw.add_argument("--kind", default="docs", choices=["code", "review", "design", "docs"])
    cw.add_argument("--promote-path", action="append", dest="promote_paths", metavar="REL_FILE",
                    help="#767: exact relative file promotable by this collect (repeatable; one "
                         "per declared task file; REQUIRED for --kind code); absent -> the "
                         "appendix-prefix default")
    cw.add_argument("--expected-feature-ref", dest="expected_feature_ref",
                    help="#762 R5-B: the refs/heads/ feature ref this collect authorizes "
                         "landing on (REQUIRED for --kind code); land-work-product requires "
                         "byte-exact equality with its --expected-ref")
    cw.add_argument("--correlation-id", dest="correlation_id")
    cw.add_argument("--workspace", required=True)
    cw.add_argument("--project", required=True)
    cw.set_defaults(fn=_do_collect_work_product)

    lw = sub.add_parser("land-work-product",
                        help="#767 Step-11 / #762 D1: guarded AUDITED tri-state landing — "
                             "fast-forward the checked-out feature branch onto a collected "
                             "temp-ref commit (authorization bound to the collect record, "
                             "scoped dirty check, ff-only, postconditions, landed_work_product "
                             "audit append, CAS temp-ref delete). Identity args required; "
                             "--no-audit is the explicit unaudited opt-out")
    lw.add_argument("--repo", default=None,
                    help="path to the orchestrator checkout (audited mode derives it from "
                         "workspace+project and merely cross-checks this; --no-audit default: .)")
    lw.add_argument("--expected-ref", required=True, dest="expected_ref",
                    help="the feature ref recorded before dispatch (refs/heads/...)")
    lw.add_argument("--pre-sha", required=True, dest="pre_sha",
                    help="the recorded pre-task HEAD SHA")
    lw.add_argument("--new-sha", required=True, dest="new_sha",
                    help="the collected commit SHA (collect-work-product's new_sha)")
    lw.add_argument("--temp-ref", required=True, dest="temp_ref",
                    help="the per-receipt collect ref (refs/rawgentic/collect/<nonce>)")
    lw.add_argument("--run-id", dest="run_id",
                    help="the run whose audit log records the landing (audited mode)")
    lw.add_argument("--workspace", help="workspace file (audited mode)")
    lw.add_argument("--project", help="project name (audited mode)")
    lw.add_argument("--no-audit", action="store_true", dest="no_audit",
                    help="#762: explicit unaudited opt-out — pure-git landing for "
                         "standalone/ops use; mutually exclusive with the identity args")
    lw.add_argument("--correlation-id", dest="correlation_id")
    lw.set_defaults(fn=_do_land_work_product)

    su = sub.add_parser("status", help="#471: read-only per-run seat status (registry + capture) as JSON")
    su.add_argument("--workspace", required=True)
    su.add_argument("--project", required=True)
    su.add_argument("--run", required=True)
    su.set_defaults(fn=_do_status)

    st = sub.add_parser("show-table", help="#446: display the resolved seat table (setup Step 2i)")
    st.add_argument("--workspace", required=True)
    st.add_argument("--project", required=True)
    st.add_argument("--json", action="store_true")
    st.set_defaults(fn=_do_show)

    ap = sub.add_parser("apply-table", help="#446: validate/materialize a sparse seat patch (setup Step 2i)")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--patch-json", required=True, dest="patch_json")
    ap.add_argument("--dest", required=True)
    ap.add_argument("--expected-digest", required=True, dest="expected_digest")
    ap.add_argument("--expected-candidate-digest", dest="expected_candidate_digest")
    ap.add_argument("--validate-only", action="store_true", dest="validate_only")
    ap.add_argument("--reset-to-default", action="store_true", dest="reset_to_default")
    ap.set_defaults(fn=_do_apply)

    br = sub.add_parser("begin-run",
                        help="#474: declare the run's architecture at run start (the one "
                             "producer of the ledger initial record; executor only)")
    br.add_argument("--run-id", required=True, dest="run_id")
    br.add_argument("--workspace", required=True)
    br.add_argument("--project", required=True)
    br.set_defaults(fn=_do_begin_run)

    cr = sub.add_parser("close-run", help="#555: append the terminal run_closed ledger marker")
    cr.add_argument("--run-id", required=True, dest="run_id")
    cr.add_argument("--workspace", required=True)
    cr.add_argument("--project", required=True)
    cr.set_defaults(fn=_do_close_run)

    rc = sub.add_parser("reconcile", help="#555: bind the expected-call ledger against the routing audit")
    rc.add_argument("--run-id", required=True, dest="run_id")
    rc.add_argument("--mode", required=True, choices=["provisional", "final"])
    rc.add_argument("--workspace", required=True)
    rc.add_argument("--project", required=True)
    rc.set_defaults(fn=_do_reconcile)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
