"""#855 Task 1 — the optional ``review_admission`` object on an expected-call record.

The load-bearing property is BACKWARD COMPATIBILITY: when the keyword is omitted the persisted
line must be byte-for-byte what today's code writes, and a ledger written before this change must
parse with the admission reading as ``None``. Everything else here is round-trip and fail-closed
validation, matching the module's existing "malformed input RAISES, nothing is appended" contract.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from phase_executor import ledger as L


def _new(tmp_path, run_id="r1"):
    return L.ExpectedCallLedger(tmp_path, run_id)


def _admission(**over):
    base = {
        "schema_version": 1,
        "issue_key": "855",
        "workflow": "wf2",
        "gate": "11",
        "generation": 0,
        "digest": "sha256:abc",
        "slot": "reviewer-1",
        "attempt": 0,
        "fencing_token": "tok-1",
    }
    base.update(over)
    return base


def _lines(path: Path):
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# -- 1. byte-for-byte back-compat -------------------------------------------------------------

def test_omitted_admission_writes_a_byte_identical_record(tmp_path):
    """The whole serialized line is compared, not a chosen subset of keys.

    Enumerating "the keys I expect" would let this pass while a real field was dropped, and would
    invite deleting `run_id` (which `_locked_append` injects) to make an enumeration match.
    """
    a = _new(tmp_path / "a")
    a.append_initial("sha256:d", architecture="executor")
    a.append_expected("review", "c1", expected_architecture="executor")
    baseline = _lines(a.path)[1]

    b = _new(tmp_path / "b")
    b.append_initial("sha256:d", architecture="executor")
    b.append_expected("review", "c1", expected_architecture="executor", review_admission=None)
    explicit_none = _lines(b.path)[1]

    assert explicit_none == baseline
    # and the key must be ABSENT, not serialized as null — `sort_keys=True` would otherwise
    # change the line and break every existing byte-comparison of this file.
    assert "review_admission" not in json.loads(baseline)


# -- 2. round-trip ----------------------------------------------------------------------------

def test_populated_admission_round_trips(tmp_path):
    led = _new(tmp_path)
    led.append_initial("sha256:d", architecture="executor")
    adm = _admission()
    led.append_expected("review", "c1", expected_architecture="executor", review_admission=adm)

    state = led.read()
    assert len(state.expected) == 1
    assert state.expected[0].review_admission == adm


def test_mixed_records_keep_their_own_admission(tmp_path):
    led = _new(tmp_path)
    led.append_initial("sha256:d", architecture="executor")
    led.append_expected("build", "c1", expected_architecture="executor")
    led.append_expected("review", "c2", expected_architecture="executor",
                        review_admission=_admission(slot="reviewer-2"))

    state = led.read()
    by_cid = {e.correlation_id: e for e in state.expected}
    assert by_cid["c1"].review_admission is None
    assert by_cid["c2"].review_admission["slot"] == "reviewer-2"


# -- 3. a ledger written before this change ----------------------------------------------------

def test_pre_upgrade_ledger_parses_with_admission_none(tmp_path):
    """Hand-written in the OLD record shape — no admission key anywhere."""
    led = _new(tmp_path)
    led.path.parent.mkdir(parents=True, exist_ok=True)
    old = [
        {"run_id": "r1", "kind": "initial", "initial_digest": "sha256:d", "architecture": "executor"},
        {"run_id": "r1", "kind": "expected", "seat": "review", "correlation_id": "c1",
         "recovered_from": None},
    ]
    led.path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in old), encoding="utf-8")

    state = led.read()
    assert [e.review_admission for e in state.expected] == [None]


# -- 4. malformed input fails closed, appending nothing ----------------------------------------

@pytest.mark.parametrize("bad", [
    "not-a-dict",
    123,
    [],
    {k: v for k, v in _admission().items() if k != "slot"},      # missing required key
    _admission(generation="0"),                                   # wrong scalar type
    _admission(schema_version="1"),                               # wrong scalar type
    _admission(workflow="wf9"),                                   # off-vocabulary
    {**_admission(), "surprise": 1},                              # unknown key
])
def test_malformed_admission_raises_and_appends_nothing(tmp_path, bad):
    led = _new(tmp_path)
    led.append_initial("sha256:d", architecture="executor")
    before = led.path.read_bytes()

    with pytest.raises(L.LedgerError):
        led.append_expected("review", "c1", expected_architecture="executor", review_admission=bad)

    assert led.path.read_bytes() == before


def test_corrupt_admission_on_disk_fails_closed_at_read(tmp_path):
    """A tampered record is refused by the parser too, not only by the writer."""
    led = _new(tmp_path)
    led.path.parent.mkdir(parents=True, exist_ok=True)
    recs = [
        {"run_id": "r1", "kind": "initial", "initial_digest": "sha256:d", "architecture": "executor"},
        {"run_id": "r1", "kind": "expected", "seat": "review", "correlation_id": "c1",
         "recovered_from": None, "review_admission": "not-a-dict"},
    ]
    led.path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in recs), encoding="utf-8")

    with pytest.raises(L.LedgerError):
        led.read()
