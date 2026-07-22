"""Tests for hooks/hermes_policy.py — #568 Phase-2 unattended fallback policy (pure).

The module is a pure, table-driven decision function: (typed inputs) -> {action, notify,
disposition}. These tests pin every §6 decision-table row, the terminal-substitution rule,
the enumerated outputs, and the two safety properties (no critical-ask default action; no
remote_reply_allowed==false resume-from-text).
"""
import itertools
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))

import hermes_policy as hp  # noqa: E402


def _in(**kw):
    base = dict(
        ask_criticality="advisory", attendance_mode="unattended",
        delivery_state="delivered", transport_state="healthy",
        reply_interpretation="none", response_mode="free_text",
        dispatch_requirement="optional", remote_reply_allowed=False,
        deadline_state="within", has_fallback=False,
    )
    base.update(kw)
    return base


def test_outputs_are_enumerated():
    for a in hp.ACTIONS:
        assert isinstance(a, str)
    d = hp.decide(_in())
    assert d["action"] in hp.ACTIONS
    assert d["notify"] in hp.NOTIFY


# ---- offload dispatch rows ----
def test_gateway_down_required_is_error_protocol():
    d = hp.decide(_in(transport_state="gateway_down", dispatch_requirement="hermes_required"))
    assert d["action"] == "error_protocol" and d["notify"] == "blocker"


def test_gateway_down_optional_with_fallback():
    d = hp.decide(_in(transport_state="gateway_down", dispatch_requirement="optional",
                      has_fallback=True))
    assert d["action"] == "fallback_dispatch"


def test_gateway_down_optional_no_fallback_skips():
    d = hp.decide(_in(transport_state="gateway_down", dispatch_requirement="optional",
                      has_fallback=False))
    assert d["action"] == "seat_skip"


# ---- ask gating rows ----
def test_send_uncertain_critical_pauses():
    d = hp.decide(_in(delivery_state="delivery_unknown", ask_criticality="critical"))
    assert d["action"] == "pause_resumable" and d["notify"] == "run_status"


def test_send_failed_advisory_continues():
    d = hp.decide(_in(delivery_state="send_failed", ask_criticality="advisory"))
    assert d["action"] == "continue_safe_branch"


def test_bb_read_down_critical_within_deadline_waits():
    d = hp.decide(_in(delivery_state="delivered", transport_state="bb_read_down",
                      ask_criticality="critical", deadline_state="within"))
    assert d["action"] == "wait"


def test_bb_read_down_critical_expired_pauses():
    d = hp.decide(_in(delivery_state="delivered", transport_state="bb_read_down",
                      ask_criticality="critical", deadline_state="expired"))
    assert d["action"] == "pause_resumable"


def test_valid_reply_allowed_resumes():
    d = hp.decide(_in(delivery_state="delivered", reply_interpretation="selected",
                      response_mode="option_required", remote_reply_allowed=True,
                      attendance_mode="unattended"))
    assert d["action"] == "resume"


def test_ambiguous_reply_clarifies_once():
    d = hp.decide(_in(delivery_state="delivered", reply_interpretation="ambiguous"))
    assert d["action"] == "clarify_once"


def test_unmatched_option_clarifies_once():
    d = hp.decide(_in(delivery_state="delivered", reply_interpretation="unmatched_option"))
    assert d["action"] == "clarify_once"


# ---- terminal-substitution rule ----
def test_remote_reply_not_allowed_never_resumes_from_text():
    d = hp.decide(_in(delivery_state="delivered", reply_interpretation="selected",
                      response_mode="option_required", remote_reply_allowed=False,
                      ask_criticality="critical"))
    assert d["action"] != "resume"


def test_attended_valid_reply_without_remote_flag_does_not_auto_resume():
    d = hp.decide(_in(delivery_state="delivered", reply_interpretation="selected",
                      remote_reply_allowed=False, attendance_mode="attended"))
    assert d["action"] != "resume"


# ---- fail-closed ----
def test_unknown_input_fails_closed():
    d = hp.decide(_in(transport_state="who_knows"))
    assert d["action"] == "pause_resumable" and d["notify"] == "run_status"


def test_missing_field_fails_closed():
    bad = _in()
    del bad["ask_criticality"]
    d = hp.decide(bad)
    assert d["action"] == "pause_resumable"


# ---- safety properties (exhaustive sweep of the enumerated domain) ----
_DOMAINS = dict(
    ask_criticality=["critical", "advisory"],
    attendance_mode=["attended", "unattended"],
    delivery_state=["unsent", "send_failed", "delivery_unknown", "delivered"],
    transport_state=["healthy", "bb_read_down", "gateway_down"],
    reply_interpretation=["none", "selected", "free_text", "ambiguous", "unmatched_option"],
    response_mode=["free_text", "option_required", "option_or_text"],
    dispatch_requirement=["optional", "hermes_required"],
    remote_reply_allowed=[True, False],
    deadline_state=["within", "expired"],
    has_fallback=[True, False],
)


def _all_inputs():
    keys = list(_DOMAINS)
    for combo in itertools.product(*_DOMAINS.values()):
        yield dict(zip(keys, combo))


def test_property_no_remote_reply_false_ever_resumes():
    for inp in _all_inputs():
        if inp["remote_reply_allowed"] is False:
            assert hp.decide(inp)["action"] != "resume", inp


def test_property_critical_never_silently_continues():
    # A critical ask must never land on continue_safe_branch or seat_skip (silent progress).
    for inp in _all_inputs():
        if inp["ask_criticality"] == "critical":
            act = hp.decide(inp)["action"]
            assert act not in ("continue_safe_branch", "seat_skip"), inp


def test_property_every_output_enumerated():
    for inp in _all_inputs():
        d = hp.decide(inp)
        assert d["action"] in hp.ACTIONS and d["notify"] in hp.NOTIFY
