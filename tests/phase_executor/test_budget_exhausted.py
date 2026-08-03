"""#852 — a cost-cap trip must be classified distinctly, must not burn the fallback chain, and
must be diagnosable from the receipt.

The fixtures are REAL envelopes, copied verbatim from the #762 run's preserved receipts
(`.rawgentic/runs/wf2-762-ff40b6d5/analysis/`): `0-a8662635` tripped the then-$2 cap, `0-870117e4`
succeeded. AC4 forbids a stub — "a stub returning success is not evidence — that is precisely how
#829 went unnoticed" — so every assertion here runs against the bytes the provider actually emitted.

Established by reproducing before fixing, and it corrects the issue's AC1: the budget-exhausted
envelope has **no `result` key at all** (the success envelope has 11,303 chars), so there is no
model output to preserve. The CLI discards the text itself; rawgentic never receives it. What can be
preserved is the EVIDENCE, and that is what is asserted below.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from phase_executor import contract
from phase_executor.adapters import base, claude_cli

FIXTURES = Path(__file__).parent / "fixtures"
BUDGET = json.loads((FIXTURES / "claude-budget-exhausted-envelope.json").read_text())
SUCCESS = json.loads((FIXTURES / "claude-success-envelope.json").read_text())
MODEL = "claude-opus-5"


# -- the premise, pinned so it cannot rot ---------------------------------------------------------

def test_the_real_envelopes_differ_exactly_as_the_diagnosis_claims():
    """A guard on the fixtures themselves: the whole fix rests on this asymmetry."""
    assert BUDGET["subtype"] == "error_max_budget_usd"
    assert BUDGET["terminal_reason"] == "budget_exhausted"
    assert BUDGET["stop_reason"] == "end_turn"          # the model FINISHED, it did not crash
    assert "result" not in BUDGET                       # absent, not empty — nothing to salvage
    assert BUDGET["modelUsage"][MODEL]["outputTokens"] == 17434   # generated and billed
    assert SUCCESS["subtype"] == "success"
    assert len(SUCCESS["result"]) > 1000                # a success DOES carry its text


# -- AC1: classified distinctly from a crash ------------------------------------------------------

def test_budget_trip_is_not_classified_as_nonzero_exit():
    parsed = claude_cli.parse_claude(BUDGET, requested_model=MODEL)
    status = base.resolve_parse_status(
        parsed, MODEL, timed_out=False, exit_code=1, launch_error=None)
    assert status == contract.BUDGET_EXHAUSTED, (
        "a cost cap reached after the model finished is a terminal condition, not a crash; "
        f"got {status!r}")


def test_budget_exhausted_is_in_the_status_vocabulary():
    assert contract.BUDGET_EXHAUSTED in contract.PARSE_STATUSES


def test_a_genuine_crash_is_still_nonzero_exit():
    """The new branch must not swallow ordinary failures."""
    parsed = claude_cli.parse_claude(SUCCESS, requested_model=MODEL)
    status = base.resolve_parse_status(
        parsed, MODEL, timed_out=False, exit_code=1, launch_error=None)
    assert status == contract.NONZERO_EXIT


@pytest.mark.parametrize("kwargs,expected", [
    ({"timed_out": True, "exit_code": 1, "launch_error": None}, contract.TIMEOUT),
    ({"timed_out": False, "exit_code": 1, "launch_error": "boom"}, contract.LAUNCH_ERROR),
])
def test_timeout_and_launch_error_still_win_over_the_budget_signal(kwargs, expected):
    """Precedence: a timed-out or un-launchable dispatch is that, whatever the envelope says."""
    parsed = claude_cli.parse_claude(BUDGET, requested_model=MODEL)
    assert base.resolve_parse_status(parsed, MODEL, **kwargs) == expected


# -- AC2: the fallback chain must not advance -----------------------------------------------------

def test_budget_exhausted_does_not_warrant_a_chain_fallback():
    """The cap is a per-attempt DOLLAR bound and model-independent, so the next chain entry hits
    the identical wall. The #762 receipts show exactly that: opus, then fable, then sonnet, all
    exhausted on the same cause, and the fallback budget that exists for real availability
    failures spent on a wall none of them could avoid.

    `engine.run_with_fallback` advances only while `parse_status in AVAILABILITY_FAILURES`
    (engine.py:163), so membership IS the contract.
    """
    assert contract.BUDGET_EXHAUSTED not in contract.AVAILABILITY_FAILURES


def test_budget_exhausted_is_not_a_success():
    assert contract.BUDGET_EXHAUSTED not in contract.PROCESS_SUCCESS_STATUSES
    assert contract.observation_process_failure(
        {"parse_status": contract.BUDGET_EXHAUSTED,
         "process": {"exit_code": 1, "timed_out": False}, "parsed_payload": None}) is not None


# -- AC3: the cause is readable from the receipt --------------------------------------------------

def test_terminal_evidence_is_extracted_from_the_envelope():
    parsed = claude_cli.parse_claude(BUDGET, requested_model=MODEL)
    assert parsed.terminal is not None, "the terminal envelope fields must be extracted"
    assert parsed.terminal["terminal_reason"] == "budget_exhausted"
    assert parsed.terminal["subtype"] == "error_max_budget_usd"
    assert parsed.terminal["errors"] == ["Reached maximum budget ($2)"]


def test_a_successful_envelope_carries_no_terminal_evidence():
    """Only a non-success terminal condition records this, so a clean receipt stays unchanged."""
    parsed = claude_cli.parse_claude(SUCCESS, requested_model=MODEL)
    assert parsed.terminal is None


def test_the_cost_that_was_paid_is_preserved():
    """The dollars are the whole point of the issue — they must survive into the receipt even
    though the text does not."""
    parsed = claude_cli.parse_claude(BUDGET, requested_model=MODEL)
    assert parsed.usage is not None
    assert parsed.usage["cost_proxy"] == pytest.approx(2.274939)
    assert parsed.usage["output"] == 7209        # usage.output_tokens, the final-turn counter


# -- the regression that would have shipped -------------------------------------------------------

def test_a_budget_exhausted_observation_validates_against_the_schema():
    """`build_observation` calls `contract.validate_observation` fail-loud on the write path
    (adapters/base.py:219). So adding the status WITHOUT the schema would have made a real cost
    trip RAISE instead of returning an honest failure observation — turning a recoverable,
    diagnosable outcome into a crash. This is why the v3 bump ships in the same commit.
    """
    obs = contract.Observation(
        schema_version=contract.SCHEMA_VERSION,
        run_id="r1", attempt_id="0-abc", seat="analysis", engine="claude", transport="native",
        requested_model=MODEL, actual_model=MODEL, prompt_hash="sha256:x",
        usage={"input": 34, "output": 7209, "cached": 1521102, "cost_proxy": 2.274939},
        timing_ms=115861, queued_ms=0, process={"exit_code": 1, "timed_out": False},
        parse_status=contract.BUDGET_EXHAUSTED, parsed_payload=None,
        raw_capture_path="/tmp/x", fallback_reason=None, routing_config_digest="sha256:y",
        terminal={"terminal_reason": "budget_exhausted", "subtype": "error_max_budget_usd",
                  "errors": ["Reached maximum budget ($2)"]},
    )
    d = obs.to_dict()
    assert d["parse_status"] == "budget_exhausted"
    assert d["terminal"]["subtype"] == "error_max_budget_usd"
    contract.validate_observation(d)          # must not raise


def test_a_clean_observation_omits_terminal_entirely():
    """Optional-additive: a successful receipt is byte-identical to before this change."""
    obs = contract.Observation(
        schema_version=contract.SCHEMA_VERSION,
        run_id="r1", attempt_id="0-abc", seat="analysis", engine="claude", transport="native",
        requested_model=MODEL, actual_model=MODEL, prompt_hash="sha256:x",
        usage={"input": 1, "output": 2}, timing_ms=1, queued_ms=0,
        process={"exit_code": 0, "timed_out": False},
        parse_status=contract.OK, parsed_payload="text",
        raw_capture_path="/tmp/x", fallback_reason=None, routing_config_digest="sha256:y",
    )
    d = obs.to_dict()
    assert "terminal" not in d
    contract.validate_observation(d)


# -- from the #852 code review --------------------------------------------------------------------

def test_downstream_failure_reason_is_not_nonzero_exit():
    """Review finding 1 (High). `resolve_parse_status` returning BUDGET_EXHAUSTED is not enough:
    `observation_process_failure` tested the positive exit code FIRST, so every downstream consumer
    read the trip back as `nonzero_exit` and the distinct classification was undone one layer down.
    My original test only asserted the reason was non-None, which is exactly the assertion that
    cannot catch this.
    """
    reason = contract.observation_process_failure(
        {"parse_status": contract.BUDGET_EXHAUSTED,
         "process": {"exit_code": 1, "timed_out": False}, "parsed_payload": None})
    assert reason == contract.BUDGET_EXHAUSTED, f"got {reason!r} — the classification was undone"


def test_a_timeout_still_outranks_the_budget_signal_downstream():
    assert contract.observation_process_failure(
        {"parse_status": contract.BUDGET_EXHAUSTED,
         "process": {"exit_code": 1, "timed_out": True}, "parsed_payload": None}) == contract.TIMEOUT


@pytest.mark.parametrize("env,why", [
    ({**BUDGET, "is_error": "true"}, "a wrong-TYPED is_error must not gate classification"),
    ({**BUDGET, "is_error": 1}, "truthy-but-not-True must not gate classification"),
])
def test_wrong_typed_is_error_yields_no_terminal_evidence(env, why):
    """Review finding 3 (Medium): the envelope is untrusted subprocess output."""
    assert claude_cli.parse_claude(env, requested_model=MODEL).terminal is None, why


def test_a_contradictory_envelope_is_not_reclassified():
    """Cost subtype but a different terminal_reason: corroboration fails, so the process outcome
    stands rather than a single attacker-chosen field deciding the status."""
    env = {**BUDGET, "terminal_reason": "something_else"}
    parsed = claude_cli.parse_claude(env, requested_model=MODEL)
    assert base.resolve_parse_status(
        parsed, MODEL, timed_out=False, exit_code=1, launch_error=None) == contract.NONZERO_EXIT
