"""Corpus + contract tests for quota_detect (#558 AC1).

Table-driven over the raw fixture corpus (provenance: fixtures/MANIFEST.md — fixture
bytes are RAW, no in-file headers). The classifier is conjunctive and conservative:
any miss → verdict False → exactly the pre-#558 behavior.
"""
import hashlib
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "phase_executor" / "src"))

from phase_executor.quota_detect import (  # noqa: E402
    CEILING_BYTES,
    RULE_TABLE_DIGEST,
    StderrEvidence,
    classify_quota_exit,
    canonical_rule_table,
    evidence_from_bytes,
)
from phase_executor.quota_detect import EnvelopeMeta  # noqa: E402

FIX = pathlib.Path(__file__).resolve().parent / "fixtures"


def _ev(name: str) -> StderrEvidence:
    return evidence_from_bytes(FIX.joinpath(name).read_bytes())


def _cls(name: str, *, engine: str = "claude", exit_code: int = 1, envelope=None):
    return classify_quota_exit(engine=engine, exit_code=exit_code,
                               stderr=_ev(name), envelope=envelope)


# --- corpus: positives ------------------------------------------------------

@pytest.mark.parametrize("fixture", [
    "claude-stderr-quota-5h.txt",
    "claude-stderr-quota-weekly.txt",
])
def test_positive_corpus_classifies_true(fixture):
    cls = _cls(fixture)
    assert cls.verdict is True
    assert cls.conjuncts == {"provider_claude": True, "exit_1": True,
                             "usage_limit_lang": True, "reset_retry_lang": True}
    assert cls.rule_ids  # at least one matched rule id recorded


# --- corpus: negatives (EVERY one must classify False) ----------------------

NEGATIVES = [
    "claude-stderr-auth-expiry.txt",
    "claude-stderr-account-select.txt",
    "claude-stderr-network-fail.txt",
    "claude-stderr-throttle-429.txt",       # contains "rate limit" — must NOT match
    "claude-stderr-wrong-cwd-resume.txt",   # REAL capture (spike #455), exit 1
    "claude-stderr-upgrade-only.txt",       # usage lang WITHOUT temporal recovery lang
    "claude-stderr-budget-trip.txt",        # REAL capture: empty stderr (envelope on stdout)
]


@pytest.mark.parametrize("fixture", NEGATIVES)
def test_negative_corpus_classifies_false(fixture):
    cls = _cls(fixture)
    assert cls.verdict is False
    assert cls.read_error is None  # ordinary negative, not a read failure


def test_budget_trip_real_fixture_sha_pinned():
    # the empty-stderr budget-trip capture is REAL evidence — pin its identity
    raw = FIX.joinpath("claude-stderr-budget-trip.txt").read_bytes()
    assert raw == b""
    assert hashlib.sha256(raw).hexdigest() == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")


# --- conjunct isolation ------------------------------------------------------

def test_right_text_wrong_exit_is_false():
    for code in (0, 2, 137, None):
        assert _cls("claude-stderr-quota-5h.txt", exit_code=code).verdict is False


def test_right_exit_wrong_engine_is_false():
    for engine in ("codex", "zhipuai"):
        assert _cls("claude-stderr-quota-5h.txt", engine=engine).verdict is False


def test_usage_lang_without_reset_lang_is_false():
    cls = classify_quota_exit(engine="claude", exit_code=1,
                              stderr=evidence_from_bytes(b"usage limit reached\n"))
    assert cls.verdict is False
    assert cls.conjuncts["usage_limit_lang"] is True
    assert cls.conjuncts["reset_retry_lang"] is False


def test_reset_lang_without_usage_lang_is_false():
    cls = classify_quota_exit(engine="claude", exit_code=1,
                              stderr=evidence_from_bytes(b"please try again at 9am\n"))
    assert cls.verdict is False
    assert cls.conjuncts["usage_limit_lang"] is False


def test_upgrade_is_not_reset_retry_language():
    cls = classify_quota_exit(engine="claude", exit_code=1, stderr=evidence_from_bytes(
        b"You've reached your usage limit. Upgrade to Max.\n"))
    assert cls.conjuncts["reset_retry_lang"] is False
    assert cls.verdict is False


def test_conjuncts_may_match_on_separate_lines():
    cls = classify_quota_exit(engine="claude", exit_code=1, stderr=evidence_from_bytes(
        b"You've hit your usage limit.\nTry again at 10:00.\n"))
    assert cls.verdict is True


# --- StderrEvidence I/O contract (bounded read) ------------------------------

def test_evidence_raw_hash_and_count():
    raw = b"usage limit\xff\xfe garbled"
    ev = evidence_from_bytes(raw)
    assert ev.raw_sha256 == hashlib.sha256(raw).hexdigest()
    assert ev.byte_count == len(raw)
    assert ev.read_error is None
    assert "�" in ev.decoded_text  # errors="replace" decoding


def test_ceiling_boundary_exact_and_over():
    at = evidence_from_bytes(b"x" * CEILING_BYTES)
    assert at.read_error is None
    over = evidence_from_bytes(b"x" * (CEILING_BYTES + 1))
    assert over.read_error == "oversized"
    assert over.byte_count == CEILING_BYTES + 1  # true count recorded
    under = evidence_from_bytes(b"x" * (CEILING_BYTES - 1))
    assert under.read_error is None


def test_oversized_forces_false_with_distinct_evidence():
    marker = b"usage limit resets at 3am\n"
    raw = b"y" * CEILING_BYTES + marker  # marker past the ceiling
    cls = classify_quota_exit(engine="claude", exit_code=1,
                              stderr=evidence_from_bytes(raw))
    assert cls.verdict is False
    assert cls.read_error == "oversized"  # distinct from an ordinary negative


def test_read_error_evidence_forces_false_even_with_matching_text():
    ev = StderrEvidence(decoded_text="usage limit resets at 3am", raw_sha256="",
                        byte_count=0, read_error="unreadable: EACCES")
    cls = classify_quota_exit(engine="claude", exit_code=1, stderr=ev)
    assert cls.verdict is False
    assert cls.read_error == "unreadable: EACCES"


def test_adversarial_long_line_bounded_runtime():
    import time
    raw = (b"usage " * 200000)[:CEILING_BYTES - 1]
    t0 = time.monotonic()
    classify_quota_exit(engine="claude", exit_code=1, stderr=evidence_from_bytes(raw))
    assert time.monotonic() - t0 < 5.0


# --- evidence shape -----------------------------------------------------------

def test_evidence_shape_and_no_excerpt_field():
    cls = _cls("claude-stderr-quota-5h.txt")
    assert cls.classifier_version == 1
    assert cls.source == "stderr.txt"
    assert cls.engine == "claude"
    assert cls.exit_code == 1
    assert not hasattr(cls, "stderr_excerpt")  # no raw provider text persisted
    assert cls.stderr_sha256 == _ev("claude-stderr-quota-5h.txt").raw_sha256


def test_envelope_meta_copied_verbatim_observability_only():
    meta = EnvelopeMeta(session_id="s-1", subtype="error_max_budget_usd",
                        subtype_sha256=None, error=None)
    cls = _cls("claude-stderr-budget-trip.txt", envelope=meta)
    assert cls.envelope_subtype == "error_max_budget_usd"
    assert cls.envelope_error is None
    assert cls.verdict is False  # envelope NEVER a verdict input
    pos = _cls("claude-stderr-quota-5h.txt", envelope=EnvelopeMeta(
        session_id=None, subtype=None, subtype_sha256=None, error="malformed"))
    assert pos.verdict is True   # envelope error does not veto either
    assert pos.envelope_error == "malformed"


# --- rule-table digest (version enforcement) ----------------------------------

def test_rule_table_digest_pinned_next_to_version():
    canon = canonical_rule_table()
    assert isinstance(canon, str) and canon
    digest = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    assert digest == RULE_TABLE_DIGEST, (
        "rule table changed — bump classifier_version AND RULE_TABLE_DIGEST together")
