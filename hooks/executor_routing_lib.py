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
WIRED_SEATS: Final[frozenset[str]] = frozenset(
    {"intake", "analysis", "design", "plan", "build", "review", "ship"})
COMPETITIVE_ONLY: Final[frozenset[str]] = frozenset({"design"})
DRIVER_ONLY: Final[frozenset[str]] = frozenset({"merge", "ci_triage", "deploy_verify", "step16"})
VALID_MODES: Final[frozenset[str]] = frozenset({"inherit", "executor"})
SUPPORTED_VERSION: Final[int] = 1

# Exit-code taxonomy (structured {ok:false,error:{code,message,retryable}} on every non-zero).
EXIT_OK: Final[int] = 0
EXIT_MALFORMED: Final[int] = 2      # bad input / config / invalid seat or mode (non-retryable)
EXIT_AVAILABILITY: Final[int] = 3   # chain exhaustion / quota / timeout / availability (retryable)
EXIT_ENFORCEMENT: Final[int] = 4    # pre-check denial or requested!=actual identity breach (non-retryable)
EXIT_INTERNAL: Final[int] = 5       # audit/capture/internal/import failure (non-retryable)
EXIT_REFUSED: Final[int] = 6        # #470 §2a: canary refusal (either phase) — ADDITIVE, no renumber

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


def probe_needed(engine: str, canary_mod) -> bool:
    """True iff the engine's mutating policy has checks that need the phase-2 probe stream."""
    policy = canary_mod.POLICIES.get(f"{engine}_mutating", ())
    return any(c not in LOCAL_EVALUABLE_CANARY_CHECKS for c in policy)

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


def resolve_seat_action(seat: str, workspace_path: str, project: str) -> tuple[str, str]:
    """Return ``(action, reason)`` where action is ``inherit`` | ``executor`` | ``driver_only``.

    Raises ``MalformedConfig`` on an unknown seat or a present-but-malformed config (-> exit 2).
    Does NOT compute the primary model (that needs the routing snapshot — the CLI adds it for the
    ``executor`` action) and never restates a legacy model for the ``inherit`` branch."""
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
    # Fail CLOSED on a corrupt/unreadable workspace (strict_read=True): a config this
    # enforcement boundary cannot evaluate must DENY (exit 2), not collapse into the same
    # _ABSENT→inherit as a clean absence — else an executor-intended seat silently reverts to the
    # legacy path on a mid-edit/corrupt workspace (a false cutover; Step-11 D3/A3). A genuinely
    # absent workspace/entry still returns _ABSENT→inherit (that is "not configured", not "cannot
    # evaluate"). model_routing keeps its fail-OPEN read (strict_read default False).
    try:
        raw = _load_block(workspace_path, project, key="executorRouting", missing=_ABSENT, strict_read=True)
    except (OSError, ValueError) as exc:
        raise MalformedConfig(f"workspace unreadable/corrupt — cannot evaluate executorRouting (fail-closed): {exc}") from exc
    modes = parse_executor_routing(raw)
    mode = modes.get(seat, "inherit")
    if mode == "executor":
        return "executor", "seat routed through the executor"
    return "inherit", "seat not opted into the executor (prior behavior)"


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
    entry = _load_project_entry(workspace_path, project)
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

    pc = enforce.verify_post(final_obs)
    if not pc.ok:  # requested!=actual identity breach (non-retryable) — receipt+obs already audited
        return _err(EXIT_ENFORCEMENT, pc.reason, f"identity breach on seat {seat!r}", retryable=pc.retryable,
                    correlation_id=correlation_id, audit_path=str(audit.path))
    if not pc.verified:  # ok but unverified => the chain exhausted on availability failures
        return _err(EXIT_AVAILABILITY, "chain_exhausted_availability",
                    f"seat {seat!r} exhausted its chain on availability failures", retryable=True,
                    correlation_id=correlation_id, audit_path=str(audit.path))
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
        receipt_nonce=receipt.nonce,
        snapshot_dir=snapshot_dir, snapshot_digest=snapshot_digest)
    state, obs = supervisor.await_job(record, timeout_s=await_timeout_s)

    # STEP 7 — one dispatch result, only after identity capture + phase-2 pass.
    if state != "completed":
        retryable = state in ("timed_out", "exited_no_sentinel", "quota_paused")
        code = EXIT_AVAILABILITY if retryable else EXIT_INTERNAL
        return _err(code, f"supervised_{state}",
                    f"supervised seat {seat!r} ended in state {state!r}", retryable=retryable,
                    correlation_id=ce, audit_path=audit_path)
    # verify_post on the final observation (Step-11 C2) — same breach semantics as the sync path:
    # an envelope with a wrong/missing identity is a NON-retryable enforcement failure; an
    # availability-shaped obs is exit 3.
    pc = enforce.verify_post(obs or {})
    if not pc.ok:
        return _err(EXIT_ENFORCEMENT, pc.reason,
                    f"identity breach on supervised seat {seat!r}", retryable=pc.retryable,
                    correlation_id=ce, audit_path=audit_path)
    if not pc.verified:
        return _err(EXIT_AVAILABILITY, "supervised_unverified",
                    f"supervised seat {seat!r} produced no verifiable envelope ({pc.reason})",
                    retryable=True, correlation_id=ce, audit_path=audit_path)
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


def _err(exit_code: int, code: str, message: str, *, retryable: bool, correlation_id=None, audit_path=None) -> dict:
    err = {"code": code, "message": message, "retryable": retryable}
    if correlation_id is not None:
        err["correlation_id"] = correlation_id
    out = {"ok": False, "exit": exit_code, "error": err}
    if audit_path is not None:
        out["audit_path"] = audit_path
    return out


# --- CLI (guarded phase_executor import lives here) --------------------------------------------
def _ensure_pe_importable() -> None:
    """Put ``phase_executor/src`` (sibling of this hook's repo) on ``sys.path`` so the plain repo
    interpreter can import the package (verified: core modules are stdlib + jsonschema only)."""
    src = str(Path(__file__).resolve().parent.parent / "phase_executor" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


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
    from phase_executor import run_seat  # noqa: PLC0415
    from phase_executor.engine import _dispatch_real, PROVIDER_ENGINE  # noqa: PLC0415
    from phase_executor.quota import QuotaCoordinator, QuotaTimeout  # noqa: PLC0415
    from phase_executor.supervisor import TmuxSupervisor  # noqa: PLC0415
    import phase_executor.supervisor as supervisor_mod  # noqa: PLC0415 — #471 status surface
    from phase_executor.registry import JobRegistry, RegistryCorrupt  # noqa: PLC0415
    from phase_executor.registry import read_all as registry_read_all  # noqa: PLC0415
    from phase_executor.registry import session_name as registry_session_name  # noqa: PLC0415
    from phase_executor.worktree import WorktreeIdentity, WorktreeManager  # noqa: PLC0415
    from phase_executor.adapters import ADAPTERS  # noqa: PLC0415
    return types.SimpleNamespace(
        routing=routing, enforce=enforce, run_seat=run_seat,
        dispatch_real=_dispatch_real, QuotaCoordinator=QuotaCoordinator, QuotaTimeout=QuotaTimeout,
        canary=canary, canary_evidence=canary_evidence, contract=contract,
        PROVIDER_ENGINE=PROVIDER_ENGINE, TmuxSupervisor=TmuxSupervisor,
        supervisor=supervisor_mod, JobRegistry=JobRegistry, RegistryCorrupt=RegistryCorrupt,
        registry_read_all=registry_read_all,
        registry_session_name=registry_session_name,
        WorktreeIdentity=WorktreeIdentity, WorktreeManager=WorktreeManager, ADAPTERS=ADAPTERS,
    )


def _emit(obj: dict) -> int:
    """Print one JSON object to stdout; return its ``exit`` (default 0)."""
    code = obj.pop("exit", EXIT_OK) if isinstance(obj, dict) else EXIT_OK
    print(json.dumps(obj, separators=(",", ":")))
    return code


def _do_resolve(args) -> int:
    try:
        action, reason = resolve_seat_action(args.seat, args.workspace, args.project)
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
            repo_root = resolve_repo_root(args.workspace, args.project)
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
                    prompt, gate_decision, plan_context) -> dict:
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
                        f"ship the FS-sandbox child", retryable=False,
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
        final_argv = pe.ADAPTERS[engine].build_command(
            target["model"], effort=eff.native, profile=profile)

        rc, out, _err_txt = _git_runner(["git", "-C", str(repo_root), "rev-parse", "HEAD"])
        if rc != 0:
            return _err(EXIT_INTERNAL, "supervised_provision_failed",
                        "cannot resolve base_sha (git rev-parse HEAD)", retryable=False,
                        correlation_id=ce, audit_path=str(audit.path))
        base_sha = out.strip()

        supervisor = pe.TmuxSupervisor(
            snapshot=snap, quota=quota, capture_root=paths["capture_root"],
            registry_root=str(registry_root), worktree_manager=wm,
            pane_env={"PYTHONPATH": str(Path(__file__).resolve().parent)})

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

        # snapshot_dir: the plugin registration root (its hooks.json digest is the pinned
        # EXPECTED_REGISTRATION_DIGEST). A frozen read-only STAGING copy is the #472 hardening.
        return supervised_dispatch(
            seat=args.seat, prompt=prompt, run_id=args.run_id, correlation_id=ce,
            effort=args.effort, timeout=args.timeout, engine=engine, profile=profile,
            final_argv=final_argv, snapshot_dir=str(repo_root),
            capture_root=paths["capture_root"], audit=audit,
            canary=pe.canary, canary_evidence=pe.canary_evidence, supervisor=supervisor,
            probe_session=probe_session, provision=provision,
            gate_decision=gate_decision, plan_context=plan_context,
            mk_nonce=lambda: uuid.uuid4().hex,
            mk_probe_cid=lambda cls: f"probe-{uuid.uuid4().hex[:8]}",
            containment_root=str(wt_root),
            target=target, snapshot=snap, enforce=pe.enforce,
            author_provider=args.author_provider)
    except pe.routing.RoutingError as e:
        return _err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False,
                    correlation_id=ce, audit_path=str(audit.path))
    except (ValueError, OSError) as e:
        return _err(EXIT_INTERNAL, "supervised_provision_failed", f"{type(e).__name__}: {e}",
                    retryable=False, correlation_id=ce, audit_path=str(audit.path))


def _do_dispatch(args) -> int:
    # Guarded import: a stale tree / missing dep fails CLOSED to exit 5 (never a silent inherit).
    try:
        pe = _import_phase_executor()
    except ImportError as e:
        return _emit(_err(EXIT_INTERNAL, "phase_executor_import_failed", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    try:
        action, _ = resolve_seat_action(args.seat, args.workspace, args.project)
        if action != "executor":
            raise MalformedConfig(f"dispatch called on a {action!r} seat {args.seat!r} — dispatch is only valid for an executor-mode seat")
        repo_root = resolve_repo_root(args.workspace, args.project)
    except MalformedConfig as e:
        return _emit(_err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    # Table resolution + path derivation: a missing/malformed table (package default OR a
    # project phaseExecutorTable override) fails CLOSED (like the import guard) rather than
    # crashing to a bare traceback (Step-8a R1/R2; #445 resolve_table).
    try:
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
    except Exception as e:  # noqa: BLE001 — e.g. jsonschema.ValidationError on a schema-invalid table
        return _emit(_err(EXIT_INTERNAL, "routing_table_invalid", f"{type(e).__name__}: {e}", retryable=False,
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
    # #470 §1 internal routing: inspect the resolved target's staged LaunchProfile. A MUTATING
    # profile routes to the supervised branch (gate-auth → stage-and-bind → phase-1 canary → probe
    # session → require_canary → launch, in-process) INSIDE this same CLI call — there is no second
    # entry point, so neither control can be skipped by "calling the other path". A NON-mutating
    # profile runs the existing synchronous path BYTE-IDENTICAL below.
    manifest = snap.seat(args.seat).get("manifest") or {}
    mutating = bool({"edit", "bash"} & set(manifest.get("tool_grants") or ()))
    if mutating:
        result = _run_supervised(args, pe, snap, manifest, quota, audit, paths, repo_root,
                                 prompt, gate_decision, plan_context)
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

    def live_fn(record) -> bool:
        if not has_tmux:
            return False
        try:
            res = subprocess.run(
                ["tmux", "-S", record.run_socket, "has-session", "-t", record.session_name],
                capture_output=True, text=True, timeout=10, check=False)
            return res.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def spec_fn(record) -> Optional[dict]:
        p = registry_root / "specs" / f"{pe.registry_session_name(record.identity)}.json"
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    out["seats"] = pe.supervisor.run_status(
        records, live_fn=live_fn, sentinel_fn=pe.supervisor.read_sentinel,
        spec_fn=spec_fn, activity_fn=_status_activity, clock=time.time)
    return _emit(out)


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
    d.add_argument("--effort")
    d.add_argument("--timeout", type=float, default=300.0)
    d.add_argument("--workspace", required=True)
    d.add_argument("--project", required=True)
    d.set_defaults(fn=_do_dispatch)

    mg = sub.add_parser("mint-gate", help="#470: mint gate.json from the live plan (producer side)")
    mg.add_argument("--plan-file", required=True)
    mg.add_argument("--issue-complexity", required=True, choices=["trivial", "standard", "complex"])
    mg.add_argument("--plan-est-lines", required=True, type=int)
    mg.add_argument("--out", required=True)
    mg.set_defaults(fn=_do_mint_gate)

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

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
