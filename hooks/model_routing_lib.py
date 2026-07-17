"""modelRouting resolution — role -> model, fail-open.

Reads the per-project ``modelRouting`` block from ``.rawgentic_workspace.json``
and resolves a dispatch ROLE (review | analysis | implementation) to a MODEL
name for the Agent tool's ``model`` parameter. Fail-open by design: any missing
/ malformed / unknown input degrades to ``inherit`` (use the session model) with
a warning on stderr. Routing is an optimization knob, never a gate — this module
never raises to its callers and its CLI always exits 0.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Final

VALID_MODELS: Final[frozenset[str]] = frozenset(
    {"opus", "sonnet", "haiku", "fable", "inherit"}
)
VALID_EFFORT: Final[frozenset[str]] = frozenset(
    {"low", "medium", "high", "xhigh", "max"}
)
INHERIT: Final[str] = "inherit"
# review-role soft floor: explicit models weaker than opus warn (but still apply)
_BELOW_OPUS: Final[frozenset[str]] = frozenset({"sonnet", "haiku"})
# per-task implementation ceiling clamp, cheap -> capable. haiku deliberately
# absent — never Haiku for coding (standing project rule).
_IMPL_RANK: Final[dict[str, int]] = {"sonnet": 1, "opus": 2, "fable": 3}


def _warn(msg: str) -> None:
    print(f"[model_routing] {msg}", file=sys.stderr)


# Sentinel distinguishing an ABSENT config key from a present-but-empty/malformed one.
# `_load_block(..., missing=_ABSENT)` returns this only when the key is not present at all,
# so a caller (e.g. the executor-routing glue, #427) can tell "not configured" (fail-safe to
# inherit) from "present but not an object" (a malformed config to reject). `resolve` passes the
# default and coerces anything non-dict back to {} so its fail-open contract is unchanged.
_ABSENT: Final[object] = object()


def _load_project_entry(workspace_path: str, project_name: str) -> dict | None:
    """Return the project's entry dict from the workspace, or None on any problem.

    A real read error (unreadable / invalid JSON) is warned; a simply-absent workspace,
    projects list, or project entry returns None quietly. Shared by `_load_block` and the
    #427 executor-routing glue (one loader, one home)."""
    try:
        with open(workspace_path, encoding="utf-8") as f:
            ws = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError and UnicodeDecodeError (invalid
        # UTF-8 bytes), both of which must fail open like any other bad workspace.
        _warn(f"cannot read workspace ({exc}); using inherit")
        return None
    projects = ws.get("projects") if isinstance(ws, dict) else None
    if not isinstance(projects, list):
        return None
    return next(
        (p for p in projects
         if isinstance(p, dict) and p.get("name") == project_name),
        None,
    )


def _load_block(workspace_path: str, project_name: str, key: str = "modelRouting",
                *, missing: object = _ABSENT) -> object:
    """Return the project's ``<key>`` value from the workspace.

    - Key ABSENT (or workspace/entry unavailable) -> ``missing`` (default: the ``_ABSENT``
      sentinel). A caller wanting the legacy fail-open dict passes ``missing={}``.
    - Key PRESENT -> the raw value, dict or not. A non-dict is returned VERBATIM (not coerced
      to ``{}``) so a caller can distinguish an absent key from a malformed one (#427). Callers
      that need a dict (e.g. ``resolve``) coerce a non-dict themselves.
    """
    entry = _load_project_entry(workspace_path, project_name)
    if entry is None:
        return missing
    block = entry.get(key, _ABSENT)
    if block is _ABSENT:
        return missing
    return block


def _resolve_effort(value: object, role: str) -> str | None:
    """Validate a dict's 'effort' member. None passes through; anything else
    invalid warns and degrades to None (fail-open, same spirit as the model path).
    """
    if value is None:
        return None
    if not isinstance(value, str) or value not in VALID_EFFORT:
        _warn(
            f"invalid effort {value!r} for role '{role}' "
            f"(valid: {sorted(VALID_EFFORT)}); ignoring effort"
        )
        return None
    return value


def resolve(workspace_path: str, project_name: str, role: str) -> tuple[str, str | None]:
    """Resolve a dispatch role to (model, effort). Never raises.

    A configured value is either a plain model string (effort is always None —
    equivalent to ``{"model": <str>, "effort": None}``) or a ``{model, effort}``
    dict. The model member is validated identically in both shapes.

    rawgentic NEVER uses Haiku for any routed subagent role: a config value of
    ``haiku`` is accepted (not rejected to inherit) but hard-bumped to ``sonnet``
    with a warning, so a misconfigured entry can never send review/analysis/
    implementation work to Haiku. (A session model of Haiku when a role is
    ``inherit`` is guarded at the dispatch site, not here — see the WF2 Step 8
    delegation block.)
    """
    block = _load_block(workspace_path, project_name)
    if not isinstance(block, dict):
        # Absent (the _ABSENT sentinel) or a malformed non-dict modelRouting -> fail open.
        if block is not _ABSENT:
            _warn(f"modelRouting for '{project_name}' is not an object; using inherit")
        block = {}
    value = block.get(role, INHERIT)
    effort: str | None = None
    if isinstance(value, dict):
        effort = _resolve_effort(value.get("effort"), role)
        value = value.get("model", INHERIT)
    if not isinstance(value, str) or value not in VALID_MODELS:
        _warn(
            f"invalid model {value!r} for role '{role}' "
            f"(valid: {sorted(VALID_MODELS)}); using inherit"
        )
        return INHERIT, effort
    if value == "haiku":
        _warn(
            f"role '{role}' configured to 'haiku' — rawgentic never uses Haiku for "
            f"routed work; using 'sonnet' instead"
        )
        return "sonnet", effort
    if role == "review" and value in _BELOW_OPUS:
        _warn(
            f"review role resolved to '{value}', below recommended opus floor "
            f"— review quality may drop"
        )
    return value, effort


def select_impl_model(ceiling: str, risk_level: str, complexity: str) -> tuple[str, str]:
    """Pick the per-task implementation model under a resolved ceiling.

    Pure, never raises, fail-open. `inherit` ceiling → ('inherit', ...) (routing
    off, use the session model). A `haiku` or otherwise-unknown ceiling floors to
    ('sonnet', ...) — rawgentic never routes coding to Haiku, and never punts to a
    session model that might BE Haiku. Otherwise picks the cheapest sufficient
    model under the ceiling: high-risk/complex → ceiling, else sonnet.
    """
    if ceiling == "inherit":
        return "inherit", "no routing configured — session model"
    if ceiling not in _IMPL_RANK:
        # haiku / unknown ceiling: never route coding to Haiku and never punt to a
        # session model that might BE Haiku — fall back to the sonnet coding floor.
        return "sonnet", f"ceiling {ceiling!r} not a valid implementation model — floor to sonnet"

    # Reason keys off WHY the task was routed (the branch taken), NOT off a
    # desired==ceiling coincidence — a standard task under a sonnet ceiling is a
    # down-route to sonnet, and must not be logged as "high-risk/complex".
    high_or_complex = risk_level == "high" or complexity == "complex_feature"
    desired = ceiling if high_or_complex else "sonnet"

    if _IMPL_RANK[desired] <= _IMPL_RANK[ceiling]:
        actual = desired
    else:
        actual = ceiling  # defensive: unreachable while sonnet is rank 1 (the minimum)

    if high_or_complex:
        reason = f"high-risk/complex → ceiling {actual}"
    else:
        reason = f"standard/simple → down-routed to {actual}"
    return actual, reason


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="model_routing_lib")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_res = sub.add_parser("resolve", help="resolve a role to a model name")
    p_res.add_argument("--workspace", required=True)
    p_res.add_argument("--project", required=True)
    p_res.add_argument("--role", required=True)
    p_res.add_argument(
        "--effort", action="store_true",
        help="print the resolved effort instead of the model (back-compat: default omits it)",
    )
    args = parser.parse_args(argv)
    if args.cmd == "resolve":
        model, effort = resolve(args.workspace, args.project, args.role)
        if args.effort:
            print(effort if effort is not None else "none")
        else:
            print(model)
        return 0  # fail-open: always 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
