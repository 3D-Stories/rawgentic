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
import json
import re
import sys
import types
from pathlib import Path
from typing import Callable, Final, Optional

# Sibling hook import (hooks/*.py import each other via PYTHONPATH=hooks / sys.path.insert).
from model_routing_lib import _ABSENT, _load_block, _load_project_entry

# --- constants ---------------------------------------------------------------------------------
WIRED_SEATS: Final[frozenset[str]] = frozenset({"ship", "intake", "plan"})
DRIVER_ONLY: Final[frozenset[str]] = frozenset({"merge", "ci_triage", "deploy_verify", "step16"})
VALID_MODES: Final[frozenset[str]] = frozenset({"inherit", "executor"})
SUPPORTED_VERSION: Final[int] = 1

# Exit-code taxonomy (structured {ok:false,error:{code,message,retryable}} on every non-zero).
EXIT_OK: Final[int] = 0
EXIT_MALFORMED: Final[int] = 2      # bad input / config / invalid seat or mode (non-retryable)
EXIT_AVAILABILITY: Final[int] = 3   # chain exhaustion / quota / timeout / availability (retryable)
EXIT_ENFORCEMENT: Final[int] = 4    # pre-check denial or requested!=actual identity breach (non-retryable)
EXIT_INTERNAL: Final[int] = 5       # audit/capture/internal/import failure (non-retryable)

_UNSAFE_COMPONENT: Final[re.Pattern] = re.compile(r"[/\\]|\.\.|[\x00-\x1f]")

# Routing table shipped inside the package (the §1 seat table as data).
_ROUTING_TABLE_REL: Final[str] = "phase_executor/src/phase_executor/routing/rawgentic.routing-table.json"


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
    """
    # eligible_targets → snapshot.seat raises RoutingError when the table lacks this seat (a
    # stale/edited/wrong-project table). Catch it into the structured taxonomy — the dispatch path
    # must not leak a bare traceback where _do_resolve's executor branch already maps it (Step-11 A2-F1).
    try:
        targets = routing.eligible_targets(seat, snapshot, author_provider=author_provider)
    except routing.RoutingError as e:
        return _err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False,
                    correlation_id=correlation_id, audit_path=str(audit.path))

    def wrapped_dispatch(engine, req, *, run_id, attempt_id, capture_root, digest, queued_ms, fallback_reason):
        i = int(str(attempt_id).split("-", 1)[0])
        target = targets[i]
        receipt = enforce.check_pre(
            seat, target, snapshot,
            correlation_id=correlation_id, attempt_id=attempt_id, author_provider=author_provider,
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
    from phase_executor import run_seat  # noqa: PLC0415
    from phase_executor.engine import _dispatch_real  # noqa: PLC0415
    from phase_executor.quota import QuotaCoordinator, QuotaTimeout  # noqa: PLC0415
    return types.SimpleNamespace(
        routing=routing, enforce=enforce, run_seat=run_seat,
        dispatch_real=_dispatch_real, QuotaCoordinator=QuotaCoordinator, QuotaTimeout=QuotaTimeout,
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
            snap = pe.routing.snapshot_from_file(repo_root / _ROUTING_TABLE_REL)
            targets = pe.routing.eligible_targets(args.seat, snap)
            out["primary_model"] = targets[0]["model"] if targets else None
        except MalformedConfig as e:
            return _emit(_err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False))
        except pe.routing.RoutingError as e:
            return _emit(_err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False))
        except OSError as e:
            return _emit(_err(EXIT_INTERNAL, "routing_table_unreadable", str(e), retryable=False))
        except Exception as e:  # noqa: BLE001 — never leak a bare traceback from resolve-seat
            return _emit(_err(EXIT_INTERNAL, "internal_error", f"{type(e).__name__}: {e}", retryable=False))
    return _emit(out)


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
    # phase_executor table load + path derivation: a missing/malformed shipped table fails CLOSED
    # (like the import guard) rather than crashing to a bare traceback (Step-8a R1/R2).
    try:
        snap = pe.routing.snapshot_from_file(repo_root / _ROUTING_TABLE_REL)
        paths = derive_paths(repo_root, args.project, args.run_id, snap.pool_concurrency())
    except MalformedConfig as e:
        return _emit(_err(EXIT_MALFORMED, "malformed_config", str(e), retryable=False))
    except pe.routing.RoutingError as e:
        return _emit(_err(EXIT_MALFORMED, "routing_table_invalid", str(e), retryable=False))
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
    try:
        quota = pe.QuotaCoordinator(paths["permits_dir"], snap.pool_concurrency())
        audit = pe.enforce.RoutingAuditLog(paths["capture_root"], args.run_id)
    except (OSError, ValueError) as e:
        return _emit(_err(EXIT_INTERNAL, "runtime_init_failed", str(e), retryable=False,
                          correlation_id=args.correlation_id))
    result = dispatch_seat(
        seat=args.seat, prompt=prompt, run_id=args.run_id,
        correlation_id=args.correlation_id, author_provider=args.author_provider,
        effort=args.effort, timeout=args.timeout, context=context,
        snapshot=snap, quota=quota, audit=audit, capture_root=paths["capture_root"],
        routing=pe.routing, enforce=pe.enforce, run_seat=pe.run_seat, dispatch_real=pe.dispatch_real,
        quota_timeout=pe.QuotaTimeout,
    )
    return _emit(result)


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
    d.add_argument("--correlation-id", dest="correlation_id")
    d.add_argument("--author-provider", dest="author_provider")
    d.add_argument("--effort")
    d.add_argument("--timeout", type=float, default=300.0)
    d.add_argument("--workspace", required=True)
    d.add_argument("--project", required=True)
    d.set_defaults(fn=_do_dispatch)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
