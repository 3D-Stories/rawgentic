"""Tasks 7-9 (AC1): adapter pure parsers fixture-tested against REAL captured provider outputs
(claude --output-format json, codex --json events, zhipuai non-streaming response). Plus the
parser -> build_observation bridge validating against the schema."""
import json
import pathlib

import pytest

from phase_executor import contract
from phase_executor.adapters import parse_claude, parse_codex, parse_zhipuai
from phase_executor.adapters.base import AdapterRequest, ProcOutcome, build_observation

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"


def _load(name):
    return (FIX / name).read_text()


# ---- claude -----------------------------------------------------------------

def test_parse_claude_single_dated_key_canonical_match():
    raw = _load("claude-envelope.json")
    r = parse_claude(raw, requested_model="claude-haiku-4-5")
    assert r.actual_model == "claude-haiku-4-5-20251001"  # raw key preserved
    assert r.usage["input"] == 10 and r.usage["output"] == 70
    assert r.usage["cached"] == 17562
    assert "cost_proxy" in r.usage
    assert r.parse_error is None


def test_parse_claude_multimodel_picks_requested_not_auxiliary():
    raw = _load("claude-envelope-multimodel.json")
    r = parse_claude(raw, requested_model="claude-opus-4-8")
    assert r.actual_model == "claude-opus-4-8"           # not the haiku auxiliary key
    assert r.usage["input"] == 21 and r.usage["output"] == 9697
    assert r.text == "Design looks sound."


def test_parse_claude_requested_absent_gives_no_identity():
    raw = _load("claude-envelope-multimodel.json")
    r = parse_claude(raw, requested_model="claude-sonnet-5")  # not a key
    assert r.actual_model is None                             # engine -> identity_failure


def test_parse_claude_malformed():
    assert parse_claude("{not json", requested_model="x").parse_error
    assert parse_claude(json.dumps({"result": "hi"}), requested_model="x").parse_error  # no modelUsage


# ---- codex ------------------------------------------------------------------

def test_parse_codex_native_events():
    raw = _load("codex-events.jsonl")
    r = parse_codex(raw, requested_model="gpt-5.6-sol", transport="native")
    assert r.text == "OK"
    assert r.usage == {"input": 16748, "output": 5, "cached": 9984}
    assert r.actual_model == "gpt-5.6-sol"  # native pinned -> requested is the actual


def test_parse_codex_proxied_transport_no_identity():
    raw = _load("codex-events.jsonl")
    r = parse_codex(raw, requested_model="gpt-5.6-sol", transport="ccr")
    assert r.actual_model is None  # cannot audit proxied id from --json -> engine fails closed
    assert r.usage["input"] == 16748


def test_parse_codex_empty():
    assert parse_codex("", requested_model="x").empty_transport          # no bytes -> availability
    assert parse_codex("garbage\nlines", requested_model="x").empty_transport  # no parseable events


# ---- zhipuai ----------------------------------------------------------------

def test_parse_zhipuai_response():
    raw = _load("zhipuai-response.json")
    r = parse_zhipuai(raw, requested_model="glm-4.5-flash")
    assert r.actual_model == "glm-4.5-flash"
    assert r.usage == {"input": 10, "output": 22, "cached": 4}
    assert r.text == "OK"


def test_parse_zhipuai_error_and_malformed():
    assert parse_zhipuai(json.dumps({"error": "quota"}), requested_model="x").parse_error
    assert parse_zhipuai("{bad", requested_model="x").parse_error


# ---- parser -> Observation bridge (AC1 + schema conformance) ----------------

def _proc_ok():
    return ProcOutcome(returncode=0, stdout="x", stderr="", timed_out=False)


@pytest.mark.parametrize("fixture,engine,requested,parse_fn", [
    ("claude-envelope-multimodel.json", "claude", "claude-opus-4-8", parse_claude),
    ("zhipuai-response.json", "zhipuai", "glm-4.5-flash", parse_zhipuai),
])
def test_parsed_result_builds_valid_ok_observation(fixture, engine, requested, parse_fn):
    parsed = parse_fn(_load(fixture), requested_model=requested)
    req = AdapterRequest(seat="review", requested_model=requested, prompt="hi", transport="native")
    obs = build_observation(
        req=req, engine=engine, run_id="r1", attempt_id="a1", parsed=parsed, proc=_proc_ok(),
        timing_ms=100, queued_ms=0, raw_capture_path="/runs/r1/a1", routing_config_digest="sha256:deed",
    )
    d = obs.to_dict()
    assert d["parse_status"] == "ok"
    assert d["actual_model"] and d["usage"]["input"] >= 0
    contract.validate_observation(d)  # conforms to the committed schema


def test_codex_native_builds_valid_ok_observation():
    parsed = parse_codex(_load("codex-events.jsonl"), requested_model="gpt-5.6-sol", transport="native")
    req = AdapterRequest(seat="build", requested_model="gpt-5.6-sol", prompt="hi", transport="native")
    obs = build_observation(
        req=req, engine="codex", run_id="r1", attempt_id="a1", parsed=parsed, proc=_proc_ok(),
        timing_ms=50, queued_ms=0, raw_capture_path="/runs/r1/a1", routing_config_digest="sha256:deed",
    )
    d = obs.to_dict()
    assert d["parse_status"] == "ok"
    contract.validate_observation(d)


def test_resolve_status_empty_transport_is_no_response():
    from phase_executor.adapters.base import ParsedResult, resolve_parse_status
    st = resolve_parse_status(ParsedResult(empty_transport=True), "m", timed_out=False, exit_code=0, launch_error=None)
    assert st == "no_response"  # availability failure -> engine falls back


def test_resolve_status_empty_output_is_no_response():
    from phase_executor.adapters.base import ParsedResult, resolve_parse_status
    # valid identity + usage but empty output -> not a usable success (diff-review finding #1)
    p = ParsedResult(text="", payload=None, actual_model="claude-opus-4-8", usage={"input": 1, "output": 2})
    st = resolve_parse_status(p, "claude-opus-4-8", timed_out=False, exit_code=0, launch_error=None)
    assert st == "no_response"


def test_resolve_status_ok_requires_output():
    from phase_executor.adapters.base import ParsedResult, resolve_parse_status
    p = ParsedResult(text="answer", actual_model="claude-opus-4-8", usage={"input": 1, "output": 2})
    st = resolve_parse_status(p, "claude-opus-4-8", timed_out=False, exit_code=0, launch_error=None)
    assert st == "ok"


def test_identity_failure_when_actual_missing():
    parsed = parse_codex(_load("codex-events.jsonl"), requested_model="gpt-5.6-sol", transport="ccr")
    req = AdapterRequest(seat="build", requested_model="gpt-5.6-sol", prompt="hi", transport="ccr")
    obs = build_observation(
        req=req, engine="codex", run_id="r1", attempt_id="a1", parsed=parsed, proc=_proc_ok(),
        timing_ms=50, queued_ms=0, raw_capture_path="/runs/r1/a1", routing_config_digest="sha256:deed",
    )
    d = obs.to_dict()
    assert d["parse_status"] == "identity_failure"
    contract.validate_observation(d)  # non-ok -> null actual allowed


# ---- #558 AC2: zhipu adapter max_tokens boundary + threading -----------------

def test_zhipuai_max_tokens_invalid_refuses_pre_launch(tmp_path):
    from phase_executor import contract as _c
    from phase_executor.adapters import zhipuai_sdk
    from phase_executor.adapters.base import AdapterRequest
    for bad in (True, 0, -1, "1024", 512.5):
        prof = _c.LaunchProfile(max_tokens=bad)
        req = AdapterRequest(seat="s", requested_model="glm-5.2", prompt="p", profile=prof)
        with pytest.raises(_c.CompositionError):
            zhipuai_sdk.run(req, run_id="r", attempt_id="a", capture_root=tmp_path,
                            routing_config_digest="sha256:d")


def test_zhipuai_max_tokens_threads_and_defaults(tmp_path, monkeypatch):
    import json as _json
    from phase_executor import contract as _c
    from phase_executor.adapters import zhipuai_sdk
    from phase_executor.adapters.base import AdapterRequest, ProcOutcome
    captured = []

    def fake_invoke(payload, timeout):
        captured.append(_json.loads(payload))
        return ProcOutcome(returncode=0, stdout="", stderr="", timed_out=False)

    monkeypatch.setattr(zhipuai_sdk, "_invoke_worker", fake_invoke)
    req = AdapterRequest(seat="s", requested_model="glm-5.2", prompt="p",
                         profile=_c.LaunchProfile(max_tokens=2048))
    zhipuai_sdk.run(req, run_id="r", attempt_id="a1", capture_root=tmp_path,
                    routing_config_digest="sha256:d")
    assert captured[-1]["max_tokens"] == 2048
    req2 = AdapterRequest(seat="s", requested_model="glm-5.2", prompt="p")
    zhipuai_sdk.run(req2, run_id="r", attempt_id="a2", capture_root=tmp_path,
                    routing_config_digest="sha256:d")
    assert captured[-1]["max_tokens"] == 1024  # None -> the preserved default, not uncapped
