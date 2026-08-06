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
from collections import namedtuple
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import supervision_lib as sl

#: A single, self-identifying ask (round 3 finding 9). The CALLER is responsible for
#: ensuring `token` is the exact token it minted for THIS blocker before ever
#: constructing one — `route_for` does not independently verify it against anything
#: else, since there is nothing else in this function's own inputs to check it against.
AskAttempt = namedtuple("AskAttempt", "token confirmed_at disposition send_failed")

#: `action` is a closed vocabulary of exactly 5 values (design §4), tested exhaustively.
Route = namedtuple("Route", "action reason deadline")

_ASK_DEADLINE_MINUTES = 20
_TERMINAL_NON_ANSWER_DISPOSITIONS = frozenset({"ambiguous", "unreachable", "late"})

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


def _compute_deadline(confirmed_at, until):
    """`confirmed_at + 20min` if `until is None`, else `min(until, confirmed_at+20min)`
    (finding 7 fix — `until` remains OPTIONAL on `SupervisionView`, required only for
    `sleeping`, so this branches explicitly rather than assuming it is always present).
    `None` when `confirmed_at` is absent — nothing to compute a deadline from yet."""
    confirmed_dt = _parse_ts(confirmed_at)
    if confirmed_dt is None:
        return None
    default_deadline = confirmed_dt + timedelta(minutes=_ASK_DEADLINE_MINUTES)
    until_dt = _parse_ts(until)
    if until_dt is None:
        return default_deadline
    return min(until_dt, default_deadline)


def route_for(view: CampaignView, *, now: datetime, run_fatal: bool = False,
              owner_only: bool = False, dependency_safe: bool = True,
              ask_attempt: "AskAttempt | None" = None) -> Route:
    """The blocker-routing decision (design §4). Only meaningful when
    `nobody_to_ask(view.base)` is true — a caller routing while attended is a caller
    error, not this function's job to re-derive.

    `now` is REQUIRED (finding 7): defaulting it to `None` would let a caller silently
    skip supplying one until the exact call that raises, which is strictly harder to
    notice than failing at every call site immediately.

    Precedence, checked in this order (owner-only exemption checked FIRST after
    run-fatal, which is checked before everything):
    1. `run_fatal` -> `notify_only`, regardless of anything else.
    2. `owner_only` -> only the owner ever decides; never `decide_locally`.
    3. `state == "sleeping"` (not owner-only) -> `decide_locally` immediately (M4
       design: sleeping decides and logs, no wake-for-wait).
    4. `state == "away"` -> gated on `view.base.transport_verified`, then on the ONE
       `ask_attempt` value (mutually exclusive states, not first-match rows).
    """
    if run_fatal:
        return Route("notify_only", "one-way heads-up regardless of anything else", None)

    confirmed_at = ask_attempt.confirmed_at if ask_attempt is not None else None
    disposition = ask_attempt.disposition if ask_attempt is not None else None
    send_failed = bool(ask_attempt.send_failed) if ask_attempt is not None else False
    deadline = _compute_deadline(confirmed_at, view.base.until)

    if owner_only:
        # Owner-only ALSO never trusts an ask that can't arrive, and never decides
        # locally on a plain deadline pass — dependency_safe alone decides the outcome
        # once waiting has stopped being productive.
        exhausted = (
            send_failed
            or disposition in _TERMINAL_NON_ANSWER_DISPOSITIONS
            or disposition == "timeout"
            or not view.base.transport_verified
            or (deadline is not None and now >= deadline)
        )
        if not exhausted:
            return Route("ask_owner_and_wait",
                         "only the owner clears it, so still worth asking", deadline)
        if dependency_safe:
            return Route("wait_for_owner",
                         "owner-only decisions are never taken locally", None)
        return Route("park_campaign",
                     "owner-only and not safe to defer -- the campaign parks", None)

    if view.base.state == "sleeping":
        return Route("decide_locally",
                     "M4 design: sleeping decides and logs immediately, no "
                     "wake-for-wait", None)

    # view.base.state == "away" from here — route_for's own precondition
    # (nobody_to_ask) rules out every other value reaching this point.
    if not view.base.transport_verified:
        return Route("wait_for_owner", "never ask if the ask can't be trusted to arrive",
                     None)

    if disposition == "timeout" and deadline is not None and now >= deadline:
        return Route("decide_locally", "the ONLY case authorizing a local decision", None)
    if send_failed or disposition in _TERMINAL_NON_ANSWER_DISPOSITIONS:
        return Route("wait_for_owner", "never read as 'chose not to answer'", None)
    if ask_attempt is None:
        return Route("ask_owner_and_wait",
                     "send not yet confirmed; nothing to compute a deadline from", None)
    return Route("ask_owner_and_wait", "deadline computed from confirmed_at", deadline)
