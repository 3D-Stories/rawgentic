"""Campaign-scoped supervision decision layer (#947 Part B §4, §7, §8, §9).

READ + PURE — may import `plan_lib`/`review_runner` freely (not on the per-tool-call
hot path Part A's `supervision_lib` guards). `CampaignView` is the ONE input every
decision function in this module accepts; `evaluate_campaign` is its only constructor
(AST-tested — round 3 finding 10), so no combination of calls can pair one campaign's
view with a foreign campaign's grant, override, or workspace state. That single-path
property is what closes round 1 finding 5, round 2 finding 7, and round 3 finding 10
together: there is no authority-checking function signature left, anywhere in this
design, that can be satisfied without reading the workspace state, the grant, and the
override from one single, unambiguous set of real files.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import supervision_lib as sl

#: The strictness poset (design §9): none < no_merge < no_merge_no_consult =
#: attended_only; none < no_consult < no_merge_no_consult = attended_only.
_NO_MERGE_MODES = frozenset({"no_merge", "no_merge_no_consult", "attended_only"})
_NO_CONSULT_MODES = frozenset({"no_consult", "no_merge_no_consult", "attended_only"})
_OVERRIDE_MODES = frozenset(
    {"none", "no_merge", "no_consult", "no_merge_no_consult", "attended_only"})


def _parse_ts(value):
    """Parse an ISO-8601 UTC timestamp, or return None. Never raises. Small and
    private by this project's own established convention (`supervision_lib._parse_ts`,
    `scanner_bootstrap._parse_ts` each keep their own copy rather than share one)."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class CampaignView:
    """The evaluated answer to "what may an unsupervised session do, for THIS
    campaign?" `base` is Part A's own workspace-evaluated view, untouched, never
    re-shaped — every campaign-scoped question still ultimately depends on whether
    anyone is watching at all."""

    base: "sl.SupervisionView"
    merge_denied: bool
    merge_permitted_by_grant: bool
    consult_providers: tuple
    granted: bool


def _driver_state_path(project_root: str, campaign_id: str) -> str:
    if not sl.validate_campaign_id(campaign_id):
        raise ValueError(f"invalid campaign_id: {campaign_id!r}")
    return os.path.join(project_root, "claude_docs", ".driver-state", f"{campaign_id}.json")


def _read_driver_state(project_root: str, campaign_id: str) -> dict:
    """Best-effort read of the campaign's OWN driver-state file. A missing or
    unreadable file is a SAFE pass-through (no grant, no override) — never a crash and
    never a widened permission, matching this module's fail-safe-for-authority
    direction."""
    path = _driver_state_path(project_root, campaign_id)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _effective_override_mode(override, *, now: datetime, current_revision: int) -> str:
    """The override's effective mode at READ time — expired or revision-mismatched
    reads as `none`, independent of whether `set_supervision_override` was ever
    involved (Medium finding fix: the evaluator enforces this too, not only the
    setter)."""
    if not isinstance(override, dict):
        return "none"
    mode = override.get("mode")
    if mode not in _OVERRIDE_MODES:
        return "none"
    expires_at = _parse_ts(override.get("expires_at"))
    if expires_at is not None and now >= expires_at:
        return "none"
    bound_revision = override.get("bound_revision")
    if bound_revision is not None and bound_revision != current_revision:
        return "none"
    return mode


def evaluate_campaign(*, workspace_root: str, campaign_id: str, project_root: str,
                      now: datetime, session_id=None) -> CampaignView:
    """The ONE authority-evaluating function in this design. Derives the workspace view
    itself (`workspace_root`, never a pre-built `SupervisionView` parameter — round 3
    finding 10) and resolves the campaign's OWN driver-state file itself (`project_root`
    + `campaign_id`, via the EXISTING path-safe `validate_campaign_id` — same reuse as
    claims/preflight), reading it ONCE and extracting `policy` (the grant) and
    `supervision_override` from that SAME parsed JSON. There is no parameter through
    which a caller could pair this campaign's view with a foreign grant, override, or
    workspace state, because there is only one read of each.

    `session_id` threads through to Part A's `evaluate_workspace` for the
    `transport_verified` computation (T1 — tightened per owner decision D267); omitting
    it means `base.transport_verified` is correctly False (no current session to match).
    """
    base = sl.evaluate_workspace(sl.read_state(workspace_root), now=now,
                                 session_id=session_id)
    state = _read_driver_state(project_root, campaign_id)
    policy = state.get("policy") or {}
    merge_permitted_by_grant = policy.get("merge_policy") == "auto-merge-scoped-to-run"

    mode = _effective_override_mode(state.get("supervision_override"), now=now,
                                    current_revision=base.revision)
    merge_denied = mode in _NO_MERGE_MODES
    if mode in _NO_CONSULT_MODES:
        consult_providers: tuple = ()
        granted = False
    else:
        consult_providers = base.consult_providers
        granted = base.granted

    return CampaignView(
        base=base, merge_denied=merge_denied,
        merge_permitted_by_grant=merge_permitted_by_grant,
        consult_providers=consult_providers, granted=granted,
    )
