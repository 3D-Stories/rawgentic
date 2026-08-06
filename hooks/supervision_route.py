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

import argparse
import json
import os
import sys
from collections import namedtuple
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import driver_lib
import review_runner
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

#: The strictness poset (design §9) lives in driver_lib (the field's sole writer's own
#: module) — derived here rather than re-hardcoded, so a change to the poset cannot
#: drift the two modules apart: none < no_merge < no_merge_no_consult = attended_only;
#: none < no_consult < no_merge_no_consult = attended_only.
_NO_MERGE_MODES = frozenset(
    mode for mode, restrictions in driver_lib.SUPERVISION_OVERRIDE_RESTRICTIONS.items()
    if "merge" in restrictions)
_NO_CONSULT_MODES = frozenset(
    mode for mode, restrictions in driver_lib.SUPERVISION_OVERRIDE_RESTRICTIONS.items()
    if "consult" in restrictions)


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


def _read_driver_state(project_root: str, campaign_id: str) -> "tuple[dict, bool]":
    """(data, corrupt) for the campaign's OWN driver-state file.

    `corrupt=True` means the file EXISTS but could not be read/parsed as a JSON
    object — Step 8a cross-model review, High finding 5: the earlier version
    returned `{}` on ANY failure, which drops a restrictive `supervision_override`
    (and the merge grant check) exactly like a legitimately-absent file, WIDENING
    permission via file corruption. `corrupt` lets the caller deny instead
    (fail-safe for authority, matching `supervision_lib.installs_forbidden`'s own
    established convention: a broken file must never unlock an outward action).
    A missing file, or an invalid `campaign_id` itself, is genuinely absent — a
    brand-new campaign that never had a policy is normal, not suspicious — and
    reads as `({}, False)`."""
    try:
        path = _driver_state_path(project_root, campaign_id)
    except ValueError:
        return {}, False
    if not os.path.exists(path):
        return {}, False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}, True
    if not isinstance(data, dict):
        return {}, True
    return data, False


def _effective_override_mode(override, *, now: datetime, current_revision: int) -> str:
    """The override's effective mode at READ time — expired or revision-mismatched
    reads as `none`, independent of whether `set_supervision_override` was ever
    involved (Medium finding fix: the evaluator enforces this too, not only the
    setter)."""
    if not isinstance(override, dict):
        return "none"
    mode = override.get("mode")
    if mode not in driver_lib.SUPERVISION_OVERRIDE_MODES:
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

    Step 8a cross-model review fixes, both confirmed real gaps:

    - **Critical finding 2:** the workspace-level consult grant is applied ONLY when
      this campaign is actually GOVERNED by the current declaration
      (`governed_campaign_ids` empty = every campaign; otherwise `campaign_id` must be
      listed). Part A's own `supervision_lib.py` deliberately does NOT narrow by
      `governed_campaign_ids` and says so explicitly ("the campaign-scoped evaluator is
      #947") — this check was always meant to live here. `base.state` itself is left
      untouched for an ungoverned campaign (Part A's workspace-global absence is real
      regardless of which campaign the owner meant to cover); only the CONSULT grant,
      which is not otherwise campaign-scoped, needed gating.
    - **High finding 5:** a `corrupt` (not merely absent) driver-state file denies
      merge AND consult, rather than silently reading as "no override, no grant".
    """
    loaded = sl.read_state(workspace_root)
    base = sl.evaluate_workspace(loaded, now=now, session_id=session_id)
    governed_ids = list(loaded.record.get("governed_campaign_ids") or []) \
        if loaded.load_status == "valid" else []
    campaign_governed = not governed_ids or campaign_id in governed_ids

    state, corrupt = _read_driver_state(project_root, campaign_id)
    # #963 measured a proposed hardening here and REFUTED it: treating "governed campaign
    # with no driver-state file" as removed-and-therefore-denying breaks the legitimate
    # ordering where the owner declares away naming a campaign that has not STARTED yet
    # (its state file is written when the run begins) — it would deny consult for exactly
    # the campaign the declaration meant to authorize. Missing and never-created are
    # indistinguishable without a durable campaign registry, which is why #947 Step 11
    # finding 7 named one. Merge is already safe (no policy = no grant); the consult
    # residual stays deferred WITH that registry, not closed by a guess.
    # `TestGovernedCampaignMissingDriverState` pins the ordering this must keep allowing.
    policy = state.get("policy") or {}
    merge_permitted_by_grant = (not corrupt) and \
        policy.get("merge_policy") == "auto-merge-scoped-to-run"

    mode = _effective_override_mode(state.get("supervision_override"), now=now,
                                    current_revision=base.revision)
    merge_denied = corrupt or mode in _NO_MERGE_MODES
    if corrupt or not campaign_governed or mode in _NO_CONSULT_MODES:
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


def authority_permits(action_kind: str, *, view: CampaignView) -> bool:
    """The bounded-autonomous-authority decision (design §7). Takes ONLY the evaluated
    `CampaignView` — no separately-passable `grant`, which is what closed round 2
    finding 7: nothing else could pair a restrictive view with a foreign campaign's
    permissive grant, because there is no second parameter to supply one through.

    Checked in order: an **invalid** supervision file (Step 8a cross-model review,
    Critical finding 3) NEVER permits anything, even a merge under a grant — Part A's
    own `evaluate_workspace` maps an invalid (corrupt/deleted-mid-session) state file to
    `state="attended"` for AVAILABILITY reasons (never wedge a per-tool-call hook), but
    `installs_forbidden` already establishes that this must NOT ALSO be fail-open for
    AUTHORITY; this function follows the same rule. Then: `attended` (with a genuinely
    valid or absent file) short-circuits True for EVERY `action_kind` (a human is
    present to object, so authority questions don't arise); away/sleeping never differ
    from each other (M4 design: "sleeping adds nothing"); `merge` is the ONLY
    action_kind absence can ever permit, and only when the grant allows it AND no
    override has denied it; every other action_kind is False in every absence state —
    absence never WIDENS what's permitted.
    """
    if view.base.load_status == "invalid":
        return False
    if view.base.state == "attended":
        return True
    if action_kind == "merge":
        return view.merge_permitted_by_grant and not view.merge_denied
    return False


def consult_permitted(view: CampaignView, backend: str) -> "tuple[bool, str]":
    """The ONLY new outward-egress gate in this issue (design §8). Fails closed on any
    of three independent checks, in order — checked BEFORE `review_runner` is invoked
    at all, so a refusal costs zero payload construction:

    1. `view.granted` — consult must be granted for this campaign at all.
    2. `backend` must be in `view.consult_providers` — the granted provider LIST, not
       just a single requested name (closes the gap where the runner's own per-account
       429 backend switch could silently reach an ungranted provider).
    3. `review_runner.backend_available(backend)` — the SAME readiness check the
       runner itself trusts, reused rather than a second, potentially-drifting one.
    """
    if not view.granted:
        return False, "consult not granted"
    if backend not in view.consult_providers:
        return False, f"{backend!r} not in the granted provider list {view.consult_providers}"
    if not review_runner.backend_available(backend):
        return False, f"{backend} backend unavailable (readiness check failed)"
    return True, ""


def validate_supervision_override(value, *, current, now: datetime) -> "tuple[bool, str]":
    """(ok, error) — structural + tighten check for a PROPOSED `supervision_override`
    value. Deliberately kept in sync with `driver_lib.set_supervision_override`'s OWN
    transition table (T2): both read the SAME `driver_lib.SUPERVISION_OVERRIDE_MODES`/
    `SUPERVISION_OVERRIDE_RESTRICTIONS` data, so the poset itself cannot drift between
    the two. This function differs from the setter only in HOW a problem is reported (a
    tuple here, a raised `DriverStateError` there, since this is a pre-flight check, not
    a write) and in taking `now` as this module's own `datetime`, not driver_lib's
    ISO-string convention.
    """
    if not isinstance(value, dict):
        return False, f"supervision_override must be a dict, got {value!r}"
    mode = value.get("mode")
    if mode not in driver_lib.SUPERVISION_OVERRIDE_MODES:
        return False, (f"mode must be one of {sorted(driver_lib.SUPERVISION_OVERRIDE_MODES)}, "
                       f"got {mode!r}")
    now_str = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    current_restrictions = driver_lib.supervision_override_effective_restrictions(
        current, now_str)
    new_restrictions = driver_lib.SUPERVISION_OVERRIDE_RESTRICTIONS.get(mode, frozenset())
    if not (new_restrictions >= current_restrictions):
        return False, (
            f"supervision_override must only TIGHTEN: current restrictions "
            f"{sorted(current_restrictions)} are not a subset of {mode!r}'s "
            f"restrictions {sorted(new_restrictions)}")
    return True, ""


def consult_check(*, workspace_root: str, project_root: str, campaign_id: str,
                  backend: str, session_id=None, now: "datetime | None" = None) -> dict:
    """The ONE integration point the skill prose (implement-feature Step 3, WF2's
    invocation reference, peer-consult WF13) calls before dispatching
    `review_runner.py consult` (Step-6 finding 1 / design §1a's AC6 commitment) —
    evaluates the campaign, then `consult_permitted`, and returns everything the caller
    needs to either skip the dispatch or append `--allowed-backends`.

    `allowed_backends` is a LIST (JSON-friendly for the CLI), derived from the
    evaluated view's OWN `consult_providers` — never a hardcoded default — so a mid-
    flight 429 switch inside the runner can only land on a provider this campaign was
    ALSO granted.
    """
    view = evaluate_campaign(workspace_root=workspace_root, campaign_id=campaign_id,
                             project_root=project_root, now=now or datetime.now(timezone.utc),
                             session_id=session_id)
    permitted, reason = consult_permitted(view, backend)
    return {
        "permitted": permitted, "reason": reason,
        "allowed_backends": sorted(view.consult_providers),
    }


def _cmd_consult_check(args) -> int:
    result = consult_check(workspace_root=args.workspace_root, project_root=args.project_root,
                           campaign_id=args.campaign_id, backend=args.backend,
                           session_id=args.session_id)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["permitted"] else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="supervision_route",
        description="Campaign-scoped supervision decisions (#947 Part B).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cc = sub.add_parser("consult-check",
                          help="evaluate consult_permitted for a campaign+backend")
    p_cc.add_argument("--workspace-root", required=True)
    p_cc.add_argument("--project-root", required=True)
    p_cc.add_argument("--campaign-id", required=True)
    p_cc.add_argument("--backend", required=True)
    p_cc.add_argument("--session-id", default=None)
    p_cc.set_defaults(fn=_cmd_consult_check)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
