"""Tests for hooks/usage_capture.py — #189 token/cost capture from Claude Code
session transcripts.

These tests are deliberately STRONGER than #155/#172's TestValidateUsage, which
only validated the usage *schema* and explicitly blessed an all-null usage object
(`test_all_null_ok`) — so nothing caught that the field was never populated, and it
was null in all 24 run-records. The lesson: a schema-accepts-values test is
necessary but NOT sufficient. So the tests below exercise the REAL capture path
end-to-end against a real-shaped session fixture and FAIL if capture yields
null/zero tokens for a real run (AC5).
"""
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

import usage_capture as uc  # noqa: E402

SAMPLE_SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"   # known-value fixture
EMPTY_SID = "ffffffff-0000-1111-2222-333333333333"    # no usage blocks at all
ZERO_SID = "cccccccc-0000-0000-0000-000000000000"     # usage blocks present but zero/null

# Hand-computed from tests/fixtures/<SAMPLE_SID>.jsonl, EXCLUDING <synthetic>:
#   opus  input = (100+50+200)+(10+0+500) = 860 ; output = 30+40 = 70
#   sonnet input = 5 ; output = 20
EXP_INPUT = 865
EXP_OUTPUT = 90
EXP_MIX = {
    "claude-opus-4-8": {"input_tokens": 860, "output_tokens": 70},
    "claude-sonnet-5": {"input_tokens": 5, "output_tokens": 20},
}


# --- parse_session_jsonl: known values (AC5c) ---

def test_parse_known_token_totals():
    u = uc.parse_session_jsonl(FIXTURES / f"{SAMPLE_SID}.jsonl")
    assert u["input_tokens"] == EXP_INPUT
    assert u["output_tokens"] == EXP_OUTPUT


def test_parse_model_mix_excludes_synthetic():
    u = uc.parse_session_jsonl(FIXTURES / f"{SAMPLE_SID}.jsonl")
    assert u["model_mix"] == EXP_MIX
    assert "<synthetic>" not in u["model_mix"]
    # synthetic's 999s must not leak into the totals either
    assert u["input_tokens"] == EXP_INPUT
    assert u["output_tokens"] == EXP_OUTPUT


# --- non-vacuity: a real run must produce NON-null, NON-zero tokens (AC5a) ---

def test_parse_real_fixture_is_nonzero():
    u = uc.parse_session_jsonl(FIXTURES / f"{SAMPLE_SID}.jsonl")
    assert u["input_tokens"] is not None and u["input_tokens"] > 0
    assert u["output_tokens"] is not None and u["output_tokens"] > 0


# --- cost: derived from the module's own RATE_CARD (arithmetic, not price truth) ---

def test_cost_matches_rate_card():
    u = uc.parse_session_jsonl(FIXTURES / f"{SAMPLE_SID}.jsonl")
    rc = uc.RATE_CARD
    # opus per-category totals: fresh=110 cache_create=50 cache_read=700 out=70
    o = rc["claude-opus-4-8"]
    exp_opus = (110 * o["input"] + 50 * o["cache_write"]
                + 700 * o["cache_read"] + 70 * o["output"]) / 1_000_000
    s = rc["claude-sonnet-5"]
    exp_sonnet = (5 * s["input"] + 20 * s["output"]) / 1_000_000
    # module rounds cost to micro-USD (secondary estimate) — assert the rounded value
    assert u["cost_estimate_usd"] == round(exp_opus + exp_sonnet, 6)


def test_cost_unknown_model_contributes_zero(tmp_path):
    p = tmp_path / "unknown.jsonl"
    p.write_text(json.dumps({"type": "assistant", "message": {
        "model": "some-future-model",
        "usage": {"input_tokens": 100, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 0, "output_tokens": 50}}}) + "\n")
    u = uc.parse_session_jsonl(p)
    # tokens still counted, but no rate -> 0 cost contribution (best-effort, honest)
    assert u["input_tokens"] == 100
    assert u["cost_estimate_usd"] == 0


# --- non-vacuity guard: zero usage blocks must NOT masquerade as captured (AC5d) ---

def test_parse_no_usage_blocks_raises():
    with pytest.raises(uc.NoUsageData):
        uc.parse_session_jsonl(FIXTURES / f"{EMPTY_SID}.jsonl")


def test_parse_present_but_zero_tokens_raises():
    # Realistic aborted/degenerate turn: usage dict PRESENT but all fields zero/null.
    # Guarding on block count instead of token sum would bless this as captured 0/0 —
    # the #155 failure mode. This asserts red-before-green for that exact hole.
    with pytest.raises(uc.NoUsageData):
        uc.parse_session_jsonl(FIXTURES / f"{ZERO_SID}.jsonl")


@pytest.mark.parametrize("sid", [EMPTY_SID, ZERO_SID])
def test_capture_never_returns_captured_with_zero(sid):
    got = uc.capture_usage(sid, projects_dir=FIXTURES)
    # MUST be None (orchestrator marks "unavailable") — never captured-with-zero/null.
    assert got is None


def test_capture_invalid_utf8_returns_none_not_crash(tmp_path):
    # The current session log may be read mid-write; a split multibyte char at EOF
    # must degrade to None, never propagate to the Step 16 summary. (UnicodeDecodeError
    # is a ValueError, not OSError — capture must not let it escape.)
    sid = "dddddddd-0000-0000-0000-000000000000"
    p = tmp_path / f"{sid}.jsonl"
    good = json.dumps({"type": "assistant", "message": {
        "model": "claude-opus-4-8",
        "usage": {"input_tokens": 100, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 0, "output_tokens": 50}}}).encode()
    p.write_bytes(good + b"\n" + b"\xff\xfe garbage bytes\n")
    got = uc.capture_usage(sid, projects_dir=tmp_path)
    # valid line still recovered -> captured with the 100/50 from the good line
    assert got is not None and got["capture_status"] == "captured"
    assert got["input_tokens"] == 100 and got["output_tokens"] == 50


def test_float_token_counts_are_counted_not_dropped(tmp_path):
    sid = "eeeeeeee-0000-0000-0000-000000000000"
    p = tmp_path / f"{sid}.jsonl"
    p.write_text(json.dumps({"type": "assistant", "message": {
        "model": "claude-opus-4-8",
        "usage": {"input_tokens": 100.0, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 0, "output_tokens": 50.0}}}) + "\n")
    u = uc.parse_session_jsonl(p)
    assert u["input_tokens"] == 100 and u["output_tokens"] == 50


def test_session_id_rejects_trailing_newline():
    with pytest.raises(ValueError):
        uc.find_session_file("abc\n", FIXTURES)


# --- capture_usage end-to-end: resolve -> parse -> dict (AC5b) ---

def test_capture_usage_end_to_end():
    u = uc.capture_usage(SAMPLE_SID, projects_dir=FIXTURES)
    assert u is not None
    assert u["capture_status"] == "captured"
    assert u["input_tokens"] == EXP_INPUT
    assert u["output_tokens"] == EXP_OUTPUT
    # all five schema keys present (present-is-strict contract)
    for k in ("input_tokens", "output_tokens", "cost_estimate_usd",
              "wall_clock_s", "model_mix"):
        assert k in u


def test_capture_missing_session_returns_none():
    assert uc.capture_usage("00000000-dead-beef-0000-000000000000",
                            projects_dir=FIXTURES) is None


# --- find_session_file: path-traversal guard (high-risk surface) ---

@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "..", "a/b", "foo/../bar", "with space", "semi;colon",
])
def test_find_session_file_rejects_traversal(bad):
    with pytest.raises(ValueError):
        uc.find_session_file(bad, FIXTURES)


def test_find_session_file_resolves_direct():
    p = uc.find_session_file(SAMPLE_SID, FIXTURES)
    assert p is not None and p.name == f"{SAMPLE_SID}.jsonl"


def test_find_session_file_missing_returns_none():
    assert uc.find_session_file("11111111-2222-3333-4444-555555555555",
                                FIXTURES) is None


# --- backfill against known-value fixtures (AC5c) ---

def _rec(n, usage=None, session_id=None):
    r = {"issue": {"number": n}}
    if session_id:
        r["session_id"] = session_id
    if usage is not None:
        r["usage"] = usage
    return r


def _null_usage(wall=None):
    return {"input_tokens": None, "output_tokens": None, "cost_estimate_usd": None,
            "wall_clock_s": wall, "model_mix": None}


def test_backfill_recovers_marks_and_skips(tmp_path):
    store = tmp_path / "recs.jsonl"
    recs = [
        _rec(1, _null_usage(wall=42), session_id=SAMPLE_SID),   # recoverable
        _rec(2, _null_usage(wall=10)),                          # no correlator
        _rec(3, {"input_tokens": 500, "output_tokens": 9, "cost_estimate_usd": 0.1,
                 "wall_clock_s": 5, "model_mix": None}),        # already has data
        _rec(4),                                                # no usage object
    ]
    store.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    stats = uc.backfill_store(store, projects_dir=FIXTURES)
    assert stats == {"recovered": 1, "unrecoverable": 1, "skip-has-data": 1,
                     "skip-no-usage": 1, "malformed": 0}
    out = [json.loads(l) for l in store.read_text().splitlines() if l.strip()]
    # rec1: recovered to KNOWN hand-computed values, wall_clock preserved
    assert out[0]["usage"]["input_tokens"] == EXP_INPUT
    assert out[0]["usage"]["output_tokens"] == EXP_OUTPUT
    assert out[0]["usage"]["capture_status"] == "captured"
    assert out[0]["usage"]["wall_clock_s"] == 42
    # rec2: unrecoverable marker, tokens stay null
    assert out[1]["usage"]["capture_status"] == "unrecoverable"
    assert out[1]["usage"]["input_tokens"] is None
    # rec3 + rec4: untouched
    assert out[2]["usage"]["input_tokens"] == 500 and "capture_status" not in out[2]["usage"]
    assert "usage" not in out[3]


def test_backfill_preserves_malformed_lines(tmp_path):
    store = tmp_path / "r.jsonl"
    store.write_text("not json at all\n"
                     + json.dumps(_rec(1, _null_usage())) + "\n")
    stats = uc.backfill_store(store)
    assert stats["malformed"] == 1 and stats["unrecoverable"] == 1
    assert store.read_text().splitlines()[0] == "not json at all"


# --- AC3 drift-guard: the anti-#155 invariant over the committed store ---

RUN_RECORDS = (Path(__file__).resolve().parent.parent.parent
               / "docs" / "measurements" / "run_records.jsonl")


def _has_positive_input(usage):
    it = usage.get("input_tokens")
    return isinstance(it, int) and not isinstance(it, bool) and it > 0


def test_committed_store_no_meaningless_usage_without_marker():
    """No record may carry a usage object with meaningless tokens (null OR a
    non-positive input) AND no capture_status marker — that is the #155 null-forever
    state and its zero-token variant. A row is fine only if it has positive input
    (a real capture) or an explicit {unrecoverable, unavailable} marker. Backfill
    fixes historical rows; live capture prevents it for new rows."""
    recs = [json.loads(l) for l in RUN_RECORDS.read_text().splitlines() if l.strip()]
    usage_objs = [r for r in recs if isinstance(r.get("usage"), dict)]
    assert usage_objs, "no usage objects in the store — this guard would be vacuous"
    offenders = [
        r.get("issue") for r in usage_objs
        if not _has_positive_input(r["usage"])
        and r["usage"].get("capture_status") not in {"unrecoverable", "unavailable"}
    ]
    assert not offenders, (
        f"{len(offenders)} usage object(s) with null/zero tokens and no capture_status "
        f"marker (the #155 null-forever state or its zero variant): {offenders}")


def test_drift_guard_catches_zero_token_no_marker(tmp_path):
    """Red-before-green proof the drift guard's zero-variant fix is load-bearing:
    a 0/0 usage object with no marker MUST be flagged (the old `is None` predicate
    missed it because 0 is not None)."""
    store = tmp_path / "s.jsonl"
    store.write_text(json.dumps({"issue": {"number": 9}, "usage": {
        "input_tokens": 0, "output_tokens": 0, "cost_estimate_usd": None,
        "wall_clock_s": None, "model_mix": None}}) + "\n")
    recs = [json.loads(l) for l in store.read_text().splitlines() if l.strip()]
    usage_objs = [r for r in recs if isinstance(r.get("usage"), dict)]
    offenders = [r.get("issue") for r in usage_objs
                 if not _has_positive_input(r["usage"])
                 and r["usage"].get("capture_status") not in {"unrecoverable", "unavailable"}]
    assert offenders, "a 0/0 usage object with no marker must be flagged as an offender"
