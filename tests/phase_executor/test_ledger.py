"""#555 (H2) — the expected-call ledger: an append-only per-run record of every expected seat
call + a terminal run_closed marker, so the dispatch chokepoint can refuse a post-close call and
the reconcile verb can bind expected↔audit. Fail-closed: every malformed/oversized/tampered/
symlinked ledger RAISES (LedgerError), never a silent pass. Tested at the pure-module level.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from phase_executor import ledger as L


def _new(tmp_path, run_id="r1"):
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    return L.ExpectedCallLedger(run_dir, run_id)


# ---- happy path ----

def test_initial_then_expected_then_closed_round_trips(tmp_path):
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="executor")
    lg.append_expected("review", "c1")
    lg.append_expected("build", "c2", recovered_from=None)
    lg.append_run_closed()
    st = lg.read()
    assert st.initial_digest == "sha256:cfg"
    assert st.closed is True
    assert [(e.seat, e.correlation_id) for e in st.expected] == [("review", "c1"), ("build", "c2")]


def test_open_ledger_reads_not_closed(tmp_path):
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="executor")
    lg.append_expected("review", "c1")
    st = lg.read()
    assert st.closed is False and len(st.expected) == 1


def test_provenance_recovered_from_preserved(tmp_path):
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="executor")
    lg.append_expected("review", "c1#resume1", recovered_from="c1")
    st = lg.read()
    assert st.expected[0].correlation_id == "c1#resume1"
    assert st.expected[0].recovered_from == "c1"


# ---- fail-closed hardening (AC1) ----

def test_post_close_append_refused(tmp_path):
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="executor")
    lg.append_run_closed()
    with pytest.raises(L.LedgerError):
        lg.append_expected("review", "c1")


def test_duplicate_expected_call_refused(tmp_path):
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="executor")
    lg.append_expected("review", "c1")
    with pytest.raises(L.LedgerError):
        lg.append_expected("review", "c1")


def test_symlinked_ledger_refused_nofollow(tmp_path):
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="executor")
    # replace the leaf with a symlink to a target the attacker controls
    target = tmp_path / "evil.jsonl"
    target.write_text('{"kind":"initial","run_id":"r1","initial_digest":"sha256:evil"}\n')
    lg.path.unlink()
    os.symlink(target, lg.path)
    with pytest.raises(L.LedgerError):
        lg.read()
    with pytest.raises(L.LedgerError):
        lg.append_expected("review", "cX")


def test_oversized_ledger_refused(tmp_path):
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="executor")
    # blow past the byte cap
    with open(lg.path, "a", encoding="utf-8") as fh:
        fh.write("x" * (L.MAX_LEDGER_BYTES + 1) + "\n")
    with pytest.raises(L.LedgerError):
        lg.read()


def test_too_many_records_refused(tmp_path, monkeypatch):
    # exercise the RECORD cap specifically (a small cap so it trips before the byte cap — with the
    # shipped 100k cap a file that long trips the byte cap first, so this pins the record path).
    monkeypatch.setattr(L, "MAX_LEDGER_RECORDS", 5)
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="executor")
    with open(lg.path, "a", encoding="utf-8") as fh:
        for i in range(6):
            fh.write(json.dumps({"kind": "expected", "run_id": "r1", "seat": "review",
                                 "correlation_id": f"c{i}", "recovered_from": None}) + "\n")
    with pytest.raises(L.LedgerError):
        lg.read()


def test_run_id_mismatch_refused(tmp_path):
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="executor")
    with open(lg.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "expected", "run_id": "OTHER", "seat": "review",
                             "correlation_id": "c9", "recovered_from": None}) + "\n")
    with pytest.raises(L.LedgerError):
        lg.read()


def test_first_record_must_be_initial(tmp_path):
    lg = _new(tmp_path)
    with open(lg.path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "expected", "run_id": "r1", "seat": "review",
                             "correlation_id": "c1", "recovered_from": None}) + "\n")
    with pytest.raises(L.LedgerError):
        lg.read()


def test_run_closed_must_be_last(tmp_path):
    lg = _new(tmp_path)
    with open(lg.path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "initial", "run_id": "r1", "initial_digest": "sha256:c"}) + "\n")
        fh.write(json.dumps({"kind": "run_closed", "run_id": "r1"}) + "\n")
        fh.write(json.dumps({"kind": "expected", "run_id": "r1", "seat": "review",
                             "correlation_id": "c1", "recovered_from": None}) + "\n")
    with pytest.raises(L.LedgerError):
        lg.read()


def test_malformed_json_refused(tmp_path):
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="executor")
    with open(lg.path, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    with pytest.raises(L.LedgerError):
        lg.read()


def test_double_initial_refused(tmp_path):
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="executor")
    with pytest.raises(L.LedgerError):
        lg.append_initial("sha256:other", architecture="executor")


def test_double_close_refused(tmp_path):
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="executor")
    lg.append_run_closed()
    with pytest.raises(L.LedgerError):
        lg.append_run_closed()


def test_read_absent_ledger_is_empty_open(tmp_path):
    lg = _new(tmp_path)
    st = lg.read()
    assert st.initial_digest is None and st.expected == [] and st.closed is False


# ---- #474: run-level architecture pin ----

def test_append_initial_requires_architecture(tmp_path):
    lg = _new(tmp_path)
    with pytest.raises(TypeError):
        lg.append_initial("sha256:cfg")  # pylint: disable=missing-kwoa


def test_append_initial_validates_architecture_vocab(tmp_path):
    lg = _new(tmp_path)
    with pytest.raises(L.LedgerError):
        lg.append_initial("sha256:cfg", architecture="banana")
    with pytest.raises(L.LedgerError):
        lg.append_initial("sha256:cfg", architecture="")


def test_architecture_round_trips(tmp_path):
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="executor")
    st = lg.read()
    assert st.architecture == "executor"
    assert st.initial_digest == "sha256:cfg"


def test_legacy_architecture_round_trips(tmp_path):
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="legacy")
    assert lg.read().architecture == "legacy"


def test_pre_flip_initial_without_architecture_parses_as_none(tmp_path):
    # a ≤3.92 ledger has no architecture field on line 1 — compat, not an error
    lg = _new(tmp_path)
    with open(lg.path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "initial", "run_id": "r1", "initial_digest": "sha256:c"}) + "\n")
    st = lg.read()
    assert st.architecture is None
    assert st.initial_digest == "sha256:c"


def test_invalid_architecture_in_file_refused(tmp_path):
    lg = _new(tmp_path)
    with open(lg.path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "initial", "run_id": "r1", "initial_digest": "sha256:c",
                             "architecture": "hybrid"}) + "\n")
    with pytest.raises(L.LedgerError):
        lg.read()


def test_append_expected_architecture_assertion_mismatch_refused(tmp_path):
    # the flock-held assertion: append_expected(expected_architecture=...) checks the freshly
    # parsed state under the same lock that appends — a mismatch refuses, nothing is written
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="legacy")
    with pytest.raises(L.LedgerError):
        lg.append_expected("review", "c1", expected_architecture="executor")
    assert lg.read().expected == []


def test_append_expected_architecture_assertion_match_proceeds(tmp_path):
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="executor")
    lg.append_expected("review", "c1", expected_architecture="executor")
    st = lg.read()
    assert len(st.expected) == 1 and st.architecture == "executor"


def test_append_expected_architecture_assertion_none_compat(tmp_path):
    # pre-flip ledger (architecture None): the assertion tolerates None (bounded compat window)
    lg = _new(tmp_path)
    with open(lg.path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "initial", "run_id": "r1", "initial_digest": "sha256:c"}) + "\n")
    lg.append_expected("review", "c1", expected_architecture="executor")
    assert len(lg.read().expected) == 1


def test_append_expected_without_assertion_unchanged(tmp_path):
    # the kwarg is optional — existing callers without it keep today's behavior
    lg = _new(tmp_path)
    lg.append_initial("sha256:cfg", architecture="executor")
    lg.append_expected("review", "c1")
    assert len(lg.read().expected) == 1
