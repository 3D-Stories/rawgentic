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


def _load_project_entry(workspace_path: str, project_name: str,
                        *, strict_read: bool = False) -> dict | None:
    """Return the project's entry dict from the workspace, or None on any problem.

    A real read error (unreadable / invalid JSON) is warned then None (fail-open) — UNLESS
    ``strict_read`` is set, in which case the read error is RAISED so an enforcement-boundary
    caller (the #427 executor glue) can fail CLOSED rather than mistake a corrupt/unreadable
    workspace for a clean absence (a false-cutover). A genuinely-absent workspace file
    (FileNotFoundError), a missing projects list, or a missing entry returns None in BOTH modes —
    those are true "not configured", not "cannot evaluate". Shared loader (one home) for
    `_load_block` and the executor glue."""
    try:
        with open(workspace_path, encoding="utf-8") as f:
            ws = json.load(f)
    except FileNotFoundError:
        return None  # genuinely absent workspace — "not configured", not a read error
    except (OSError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError and UnicodeDecodeError (invalid UTF-8). Fail
        # open for modelRouting (default); fail closed (raise) for a strict executor-glue read.
        if strict_read:
            raise
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
                *, missing: object = _ABSENT, strict_read: bool = False) -> object:
    """Return the project's ``<key>`` value from the workspace.

    - Key ABSENT (or workspace/entry unavailable) -> ``missing`` (default: the ``_ABSENT``
      sentinel). A caller wanting the legacy fail-open dict passes ``missing={}``.
    - Key PRESENT -> the raw value, dict or not. A non-dict is returned VERBATIM (not coerced
      to ``{}``) so a caller can distinguish an absent key from a malformed one (#427). Callers
      that need a dict (e.g. ``resolve``) coerce a non-dict themselves.
    """
    entry = _load_project_entry(workspace_path, project_name, strict_read=strict_read)
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
        # Absent (the _ABSENT sentinel) or an explicit null -> silently inherit (parity with the
        # pre-#427 `block is None` path); only a genuinely non-null, non-dict value warns.
        if block is not _ABSENT and block is not None:
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


REVIEW_LENSES: Final[frozenset[str]] = frozenset(
    {"security", "mechanical", "ac_completeness", "test_coverage", "bug_logic"}
)
_LENS_DEFAULT: Final[str] = "sonnet"


def select_review_lens_model(
    review_model: str, lens: str, lens_overrides: dict | None = None
) -> tuple[str, str]:
    """Pick a review model per LENS under the resolved review-role model (#491).

    Never raises; fail-open (warns to stderr on malformed config, like
    ``resolve``). The security lens is PINNED to the resolved
    review model — a ``reviewLenses.security`` override is ignored with a warning,
    so config can never downgrade the security lens. An unknown lens fails safe to
    the review model (strong). Non-security lenses take a valid configured
    override, else default to sonnet. ``haiku`` floors to sonnet everywhere —
    rawgentic never routes review work to Haiku. A review_model of ``inherit``
    passes through for security/unknown (the dispatch-site Haiku guard covers
    inherit); non-security lenses still default to sonnet.
    """
    overrides = lens_overrides if isinstance(lens_overrides, dict) else {}
    if review_model == "haiku":
        # Boundary floor (8a R2, #491): resolve() pre-floors the CLI path, but a
        # direct library caller passing raw config must never get haiku back on
        # the security-pin or unknown-lens paths either.
        _warn("review model 'haiku' passed to select_review_lens_model — "
              "never-Haiku; flooring to 'sonnet'")
        review_model = "sonnet"
    if lens == "security":
        if "security" in overrides:
            _warn("reviewLenses.security override ignored — the security lens is "
                  "pinned to the resolved review model (#491)")
        return review_model, f"security lens pinned to review model {review_model!r}"
    if lens not in REVIEW_LENSES:
        return review_model, (
            f"unknown lens {lens!r} — fail-safe to review model {review_model!r}")
    value = overrides.get(lens)
    if isinstance(value, str) and value in VALID_MODELS:
        if value == "haiku":
            _warn(f"reviewLenses.{lens} configured to 'haiku' — never-Haiku; "
                  f"using 'sonnet' instead")
            return "sonnet", f"lens {lens!r} haiku override floored to sonnet"
        return value, f"lens {lens!r} override → {value}"
    if value is not None:
        _warn(f"invalid reviewLenses.{lens} value {value!r} "
              f"(valid: {sorted(VALID_MODELS)}); using the {_LENS_DEFAULT} default")
    return _LENS_DEFAULT, f"lens {lens!r} default → {_LENS_DEFAULT}"


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
    p_res.add_argument(
        "--lens", default=None,
        help="review lens (#491): apply per-lens selection over the resolved role model "
             "(security pinned strong; mechanical/ac_completeness/test_coverage/bug_logic "
             "default sonnet; overridable via modelRouting.reviewLenses)",
    )
    args = parser.parse_args(argv)
    if args.cmd == "resolve":
        model, effort = resolve(args.workspace, args.project, args.role)
        if args.lens is not None:
            block = _load_block(args.workspace, args.project)
            overrides = block.get("reviewLenses") if isinstance(block, dict) else None
            model, _ = select_review_lens_model(model, args.lens, overrides)
        if args.effort:
            print(effort if effort is not None else "none")
        else:
            print(model)
        return 0  # fail-open: always 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
