#!/usr/bin/env python3
"""Hermes unattended fallback policy — rawgentic #568 Phase-2 (strand iii).

A PURE, table-driven decision function. Hooks own transport + durable state; this module owns
the DECISIONS; skill prose only describes them (never a competing source of behavior). Fail-CLOSED
(decision guide, repo manual §3: a policy that can't evaluate → the safe non-action). See
docs/planning/2026-07-22-568-phase2-hermes-offload-design.md §6.

Two safety invariants (property-tested, exhaustive over the enumerated domain):
1. No input with ``remote_reply_allowed == False`` ever yields ``resume`` — a text reply may
   substitute for terminal input ONLY on an explicitly remote-reply-allowed ask.
2. A ``critical`` ask never lands on ``continue_safe_branch`` or ``seat_skip`` — a critical gate
   never silently progresses; criticality overrides the offload-skip.
"""
from __future__ import annotations

ACTIONS = (
    "resume", "pause_resumable", "seat_skip", "fallback_dispatch",
    "error_protocol", "wait", "clarify_once", "continue_safe_branch",
)
NOTIFY = ("none", "run_status", "blocker")

_DOMAINS = {
    "ask_criticality": {"critical", "advisory"},
    "attendance_mode": {"attended", "unattended"},
    "delivery_state": {"unsent", "send_failed", "delivery_unknown", "delivered"},
    "transport_state": {"healthy", "bb_read_down", "gateway_down"},
    "reply_interpretation": {"none", "selected", "free_text", "ambiguous", "unmatched_option"},
    "response_mode": {"free_text", "option_required", "option_or_text"},
    "dispatch_requirement": {"optional", "hermes_required"},
    "remote_reply_allowed": {True, False},
    "deadline_state": {"within", "expired"},
    "has_fallback": {True, False},
}

_FAIL_CLOSED = {"action": "pause_resumable", "notify": "run_status", "disposition": "fail_closed"}


def _valid(inp: dict) -> bool:
    for field, domain in _DOMAINS.items():
        if field not in inp or inp[field] not in domain:
            return False
    return True


def _out(action: str, notify: str, disposition: str) -> dict:
    return {"action": action, "notify": notify, "disposition": disposition}


def decide(inp: dict) -> dict:
    """Map a fully-typed input to {action, notify, disposition}. Fail-closed on any missing or
    off-domain field. Evaluation is priority-ordered; the critical-ask guard binds first so a
    critical gate can never be silently skipped or continued past."""
    if not isinstance(inp, dict) or not _valid(inp):
        return dict(_FAIL_CLOSED)

    critical = inp["ask_criticality"] == "critical"

    # 1) Offload dispatch failure (gateway unreachable).
    if inp["transport_state"] == "gateway_down":
        if inp["dispatch_requirement"] == "hermes_required":
            return _out("error_protocol", "blocker", "hermes_required_unavailable")
        if critical:  # criticality overrides the skip — never silently drop a critical gate
            return _out("pause_resumable", "run_status", "gateway_down_critical")
        if inp["has_fallback"]:
            return _out("fallback_dispatch", "run_status", "availability_fallback")
        return _out("seat_skip", "none", "offload_skipped")

    # 2) Outbound delivery uncertain.
    if inp["delivery_state"] in ("unsent", "send_failed", "delivery_unknown"):
        if critical:
            return _out("pause_resumable", "run_status", "send_uncertain")
        return _out("continue_safe_branch", "none", "send_uncertain_advisory")

    # 3) Delivered, but inbound reads are down — never infer a reply.
    if inp["transport_state"] == "bb_read_down":
        if critical:
            if inp["deadline_state"] == "within":
                return _out("wait", "none", "await_reply")
            return _out("pause_resumable", "run_status", "reply_unavailable")
        return _out("continue_safe_branch", "none", "reply_unavailable_advisory")

    # 4) Delivered, healthy transport — evaluate the reply.
    interp = inp["reply_interpretation"]
    if interp in ("ambiguous", "unmatched_option"):
        return _out("clarify_once", "none", "clarify_" + interp)

    valid_reply = interp == "selected" or (
        interp == "free_text" and inp["response_mode"] in ("free_text", "option_or_text"))
    if valid_reply:
        # Terminal-substitution rule: a text reply substitutes ONLY when explicitly allowed,
        # unattended, and delivered.
        if inp["remote_reply_allowed"] and inp["attendance_mode"] == "unattended":
            return _out("resume", "none", "resumed")
        if critical:
            return _out("pause_resumable", "run_status", "reply_present_substitution_not_allowed")
        return _out("continue_safe_branch", "none", "reply_present_advisory")

    # 5) No usable reply yet (interp == "none" or a free_text under option_required).
    if critical:
        if inp["deadline_state"] == "within":
            return _out("wait", "none", "await_reply")
        return _out("pause_resumable", "run_status", "no_reply_deadline")
    return _out("continue_safe_branch", "none", "no_reply_advisory")


def _demo() -> None:
    assert decide({}) == _FAIL_CLOSED
    base = dict(ask_criticality="advisory", attendance_mode="unattended",
                delivery_state="delivered", transport_state="healthy", reply_interpretation="none",
                response_mode="free_text", dispatch_requirement="optional",
                remote_reply_allowed=False, deadline_state="within", has_fallback=False)
    assert decide({**base, "transport_state": "gateway_down",
                   "dispatch_requirement": "hermes_required"})["action"] == "error_protocol"
    assert decide({**base, "reply_interpretation": "selected",
                   "remote_reply_allowed": True})["action"] == "resume"
    assert decide({**base, "reply_interpretation": "selected"})["action"] != "resume"
    print("hermes_policy self-check OK")


if __name__ == "__main__":
    _demo()
