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
INHERIT: Final[str] = "inherit"
# review-role soft floor: explicit models weaker than opus warn (but still apply)
_BELOW_OPUS: Final[frozenset[str]] = frozenset({"sonnet", "haiku"})


def _warn(msg: str) -> None:
    print(f"[model_routing] {msg}", file=sys.stderr)


def _load_block(workspace_path: str, project_name: str) -> dict:
    """Return the project's modelRouting dict, or {} on any problem (warned)."""
    try:
        with open(workspace_path, encoding="utf-8") as f:
            ws = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError and UnicodeDecodeError (invalid
        # UTF-8 bytes), both of which must fail open like any other bad workspace.
        _warn(f"cannot read workspace ({exc}); using inherit")
        return {}
    projects = ws.get("projects") if isinstance(ws, dict) else None
    if not isinstance(projects, list):
        return {}
    entry = next(
        (p for p in projects
         if isinstance(p, dict) and p.get("name") == project_name),
        None,
    )
    if entry is None:
        return {}
    block = entry.get("modelRouting")
    if block is None:
        return {}
    if not isinstance(block, dict):
        _warn(f"modelRouting for '{project_name}' is not an object; using inherit")
        return {}
    return block


def resolve(workspace_path: str, project_name: str, role: str) -> str:
    """Resolve a dispatch role to a model name (or 'inherit'). Never raises."""
    block = _load_block(workspace_path, project_name)
    value = block.get(role, INHERIT)
    if not isinstance(value, str) or value not in VALID_MODELS:
        _warn(
            f"invalid model {value!r} for role '{role}' "
            f"(valid: {sorted(VALID_MODELS)}); using inherit"
        )
        return INHERIT
    if role == "review" and value in _BELOW_OPUS:
        _warn(
            f"review role resolved to '{value}', below recommended opus floor "
            f"— review quality may drop"
        )
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="model_routing_lib")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_res = sub.add_parser("resolve", help="resolve a role to a model name")
    p_res.add_argument("--workspace", required=True)
    p_res.add_argument("--project", required=True)
    p_res.add_argument("--role", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "resolve":
        print(resolve(args.workspace, args.project, args.role))
        return 0  # fail-open: always 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
