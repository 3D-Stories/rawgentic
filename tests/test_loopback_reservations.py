"""Authorize-without-charging loop-back budget (#1003).

The defect: `review-reopen` debited the budget at MINT time, so asking permission to open a fix
round cost the same as opening one. A review returning ZERO findings — the case the budget exists
to protect — billed identically to one that opened a round.

The fix is reserve-then-commit. A mint creates an OUTSTANDING reservation and moves no counter.
The debit happens only when a fix round actually opens, and the round record is the linearization
point that proves it did.

**Why reserve-then-commit rather than debit-then-refund.** Both fail. Debit-then-refund fails
toward DESTROYING budget the owner paid for, silently. Reserve-then-commit fails toward
under-charging, which `loopback-status` shows and an operator can fix. Given a choice of which way
to be wrong, be wrong in the direction someone can see.

Concurrency is not assumed here — it was probed. 24 real processes against a capacity of 3 admitted
exactly 3 in 5 of 5 trials, and 30 staggered arrivals across repeated inode swaps lost zero
increments in 4 of 4. Both controls, with the lock removed, failed every trial. See the design's
§2.9 and §2.11.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "hooks"))

import plan_lib  # noqa: E402


def _state(tmp_path, **over):
    p = tmp_path / "loopback_counters.json"
    base = {s: 0 for s in plan_lib._LOOPBACK_SOURCES} | {"total": 0}
    base.update(over)
    p.write_text(json.dumps(base), encoding="utf-8")
    return str(p)


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _authorize(path, source="design", **kw):
    kw.setdefault("issue", 1003)
    kw.setdefault("workflow", "WF2")
    kw.setdefault("gate", "step4")
    kw.setdefault("run_id", "run-a")
    kw.setdefault("session_id", "sess-a")
    kw.setdefault("requested_by", "test")
    return plan_lib.authorize_loopback(path, source, **kw)


# ---------------------------------------------------------------------------
# The headline: a zero-finding review must leave the counters byte-identical


def test_a_review_that_opens_no_round_leaves_the_counters_byte_identical(tmp_path) -> None:
    """AC7's headline test, and the entire point of the issue."""
    path = _state(tmp_path)
    before = Path(path).read_bytes()

    ok, nonce, _ = _authorize(path)
    assert ok and nonce
    after_mint = _read(path)
    assert after_mint["design"] == 0, "minting must not debit"
    assert after_mint["total"] == 0

    ok, reason, _ = plan_lib.release_loopback(path, nonce, actor="test", reason="no findings")
    assert ok, reason

    final = _read(path)
    assert final["design"] == 0 and final["total"] == 0
    # Byte-identity is asserted on the COUNTERS, not the whole file: a release deliberately
    # appends an audit entry (owner decision D309 item 2), so the document legitimately grows.
    for key in list(plan_lib._LOOPBACK_SOURCES) + ["total"]:
        assert final[key] == json.loads(before)[key], key


def test_the_debit_happens_only_when_a_round_opens(tmp_path) -> None:
    path = _state(tmp_path)
    ok, nonce, _ = _authorize(path)
    assert ok
    assert _read(path)["design"] == 0

    ok, round_id, _ = plan_lib.open_fix_round(path, nonce, actor="test")
    assert ok and round_id
    assert _read(path)["design"] == 0, "opening a round must not debit either — commit does"

    ok, reason, _ = plan_lib.commit_loopback(path, nonce, actor="test")
    assert ok, reason
    final = _read(path)
    assert final["design"] == 1 and final["total"] == 1


# ---------------------------------------------------------------------------
# Capacity counts committed PLUS outstanding (F4)


def test_capacity_counts_outstanding_reservations_not_just_committed(tmp_path) -> None:
    """Two authorizations must not both see the same remaining slot. `design` caps at 2."""
    path = _state(tmp_path)
    first_ok, first_nonce, _ = _authorize(path)
    second_ok, second_nonce, _ = _authorize(path)
    assert first_ok and second_ok

    third_ok, third_nonce, state = _authorize(path)
    assert not third_ok, "the source cap is 2; a third must be refused while two are outstanding"
    assert third_nonce is None


def test_releasing_restores_availability(tmp_path) -> None:
    path = _state(tmp_path)
    _, n1, _ = _authorize(path)
    _, n2, _ = _authorize(path)
    assert not _authorize(path)[0]
    plan_lib.release_loopback(path, n1, actor="test", reason="clean review")
    assert _authorize(path)[0], "a released slot must become available again"


# ---------------------------------------------------------------------------
# The round record is the linearization point


def test_commit_refuses_without_a_round_record(tmp_path) -> None:
    """Commit VALIDATES that a round opened rather than trusting the caller."""
    path = _state(tmp_path)
    _, nonce, _ = _authorize(path)
    ok, reason, _ = plan_lib.commit_loopback(path, nonce, actor="test")
    assert not ok
    assert "round" in reason, reason
    assert _read(path)["design"] == 0


def test_open_fix_round_is_idempotent_and_returns_the_same_round_id(tmp_path) -> None:
    """A9: idempotency is expressed by returning the SAME id, never by a sentinel that
    destroys the value a caller needs to recover after a lost response."""
    path = _state(tmp_path)
    _, nonce, _ = _authorize(path)
    ok1, id1, _ = plan_lib.open_fix_round(path, nonce, actor="test")
    ok2, id2, _ = plan_lib.open_fix_round(path, nonce, actor="test")
    assert ok1 and ok2
    assert id1 == id2 and id1


def test_commit_is_idempotent_via_settled_commits(tmp_path) -> None:
    path = _state(tmp_path)
    _, nonce, _ = _authorize(path)
    plan_lib.open_fix_round(path, nonce, actor="test")
    assert plan_lib.commit_loopback(path, nonce, actor="test")[0]
    ok, reason, _ = plan_lib.commit_loopback(path, nonce, actor="test")
    assert ok and reason == "already_committed"
    assert _read(path)["design"] == 1, "a retried commit must not double-charge"


def test_an_unknown_nonce_commit_is_refused(tmp_path) -> None:
    """Release forgives an unknown nonce; commit must not. If it did, a bug that lost a nonce
    would open a fix round for free — the accounting hole this issue closes."""
    path = _state(tmp_path)
    ok, reason, _ = plan_lib.commit_loopback(path, "RG-nope", actor="test")
    assert not ok and reason == "unknown_nonce"


def test_release_is_idempotent_for_an_unknown_nonce(tmp_path) -> None:
    path = _state(tmp_path)
    ok, reason, _ = plan_lib.release_loopback(path, "RG-nope", actor="test", reason="x")
    assert ok and reason == "already_released"


# ---------------------------------------------------------------------------
# B2 and A5 — release precedence


def test_release_refuses_once_the_round_has_opened(tmp_path) -> None:
    """B2: releasing a reservation whose round opened recreates the unbilled-round failure this
    issue exists to close — the round happened and the budget is never charged."""
    path = _state(tmp_path)
    _, nonce, _ = _authorize(path)
    plan_lib.open_fix_round(path, nonce, actor="test")
    ok, reason, _ = plan_lib.release_loopback(path, nonce, actor="test", reason="changed my mind")
    assert not ok and reason == "round_already_opened"


def test_release_reports_already_committed_not_already_released(tmp_path) -> None:
    """A5: a committed nonce is absent from `reservations`. Reporting `already_released` would
    tell the caller capacity was restored when it was not."""
    path = _state(tmp_path)
    _, nonce, _ = _authorize(path)
    plan_lib.open_fix_round(path, nonce, actor="test")
    plan_lib.commit_loopback(path, nonce, actor="test")
    ok, reason, _ = plan_lib.release_loopback(path, nonce, actor="test", reason="x")
    assert not ok and reason == "already_committed"


def test_both_release_paths_append_to_the_reconciliation_log(tmp_path) -> None:
    """Owner decision D309 item 2: the audit append is a property of RELEASING, wherever it is
    invoked from. A release with no audit entry is the hole; two entry points are not."""
    path = _state(tmp_path)
    _, nonce, _ = _authorize(path)
    plan_lib.release_loopback(path, nonce, actor="operator", reason="clean review")
    log = _read(path).get("reconciliation_log") or []
    assert len(log) == 1, log
    assert log[0]["nonce"] == nonce
    assert log[0]["actor"] == "operator"
    assert log[0]["reason"] == "clean review"


# ---------------------------------------------------------------------------
# A1 — no writer may drop the new keys


def test_consume_loopback_preserves_the_reservation_keys(tmp_path) -> None:
    """THE most dangerous interaction in the design. `consume_loopback` already writes this file.
    If it reconstructs only the counters, the first legacy write deletes every reservation, round
    record and audit entry — restoring capacity and destroying billing evidence in one move."""
    path = _state(tmp_path)
    _, nonce, _ = _authorize(path)
    plan_lib.open_fix_round(path, nonce, actor="test")
    doc = _read(path)
    doc["an_unknown_future_key"] = {"keep": "me"}
    Path(path).write_text(json.dumps(doc), encoding="utf-8")

    plan_lib.consume_loopback(path, "tdd")

    after = _read(path)
    for key in ("reservations", "rounds", "an_unknown_future_key"):
        assert key in after, f"{key} was dropped by consume_loopback"
    assert after["reservations"], "the reservation itself was erased"
    assert after["tdd"] == 1, "the legacy debit must still work"


# ---------------------------------------------------------------------------
# B3 and A3 — fail closed on corruption, in EVERY mutator


@pytest.mark.parametrize("mutator", ["authorize", "open", "commit", "release"])
def test_every_mutator_refuses_a_corrupt_counter(tmp_path, mutator) -> None:
    """A3: covering only `authorize` left the others free to read a corrupt counter as 0 and
    persist that repaired zero during their own write — destroying committed-spend history."""
    path = _state(tmp_path)
    _, nonce, _ = _authorize(path)
    doc = _read(path)
    doc["design"] = "not-an-int"
    Path(path).write_text(json.dumps(doc), encoding="utf-8")

    if mutator == "authorize":
        ok, _, state = _authorize(path)
    elif mutator == "open":
        ok, state, _ = plan_lib.open_fix_round(path, nonce, actor="t")
        ok = bool(ok)
    elif mutator == "commit":
        ok, state, _ = plan_lib.commit_loopback(path, nonce, actor="t")
    else:
        ok, state, _ = plan_lib.release_loopback(path, nonce, actor="t", reason="x")
    assert not ok, f"{mutator} accepted a corrupt counter"
    assert _read(path)["design"] == "not-an-int", "the corrupt value must not be rewritten"


@pytest.mark.parametrize("key", ["reservations", "rounds", "settled_commits",
                                 "reconciliation_log"])
def test_a_malformed_collection_refuses_without_rewriting(tmp_path, key) -> None:
    """Owner decision D309 item 3: fail-closed covers all FOUR keys, not just `reservations`.
    Overwriting `settled_commits` destroys settlement evidence and `reconciliation_log` IS the
    audit trail."""
    path = _state(tmp_path)
    doc = _read(path)
    doc[key] = "not-a-collection"
    Path(path).write_text(json.dumps(doc), encoding="utf-8")
    before = Path(path).read_bytes()

    ok, nonce, _ = _authorize(path)
    assert not ok and nonce is None
    assert Path(path).read_bytes() == before, "a malformed file must not be rewritten"


# ---------------------------------------------------------------------------
# Identity, and the compatibility floor


def test_identity_mismatch_is_refused_with_a_named_reason(tmp_path) -> None:
    path = _state(tmp_path)
    _, nonce, _ = _authorize(path, issue=1003)
    plan_lib.open_fix_round(path, nonce, actor="test")
    ok, reason, _ = plan_lib.commit_loopback(path, nonce, actor="test", expect_issue=999)
    assert not ok and "issue" in reason


def test_issue_may_be_null_for_a_legacy_caller(tmp_path) -> None:
    """A10: the compatibility fallback can yield null when the issue cannot be parsed from the
    path. Refusing it would break the hard backward-compatibility requirement."""
    path = _state(tmp_path)
    ok, nonce, _ = _authorize(path, issue=None)
    assert ok and nonce
    res = _read(path)["reservations"][nonce]
    assert res["issue"] is None


def test_status_is_read_only_and_classifies_each_reservation(tmp_path) -> None:
    path = _state(tmp_path)
    _, outstanding, _ = _authorize(path)
    _, opened, _ = _authorize(path)
    plan_lib.open_fix_round(path, opened, actor="test")
    before = Path(path).read_bytes()

    status = plan_lib.loopback_status(path)

    assert Path(path).read_bytes() == before, "status must not mutate"
    assert status["reservations"][outstanding]["state"] == "outstanding"
    assert status["reservations"][opened]["state"] == "opened_uncommitted"


# ---------------------------------------------------------------------------
# The CLI — black-box via subprocess, per docs/testing.md


def _cli(tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "hooks" / "plan_lib.py"), *args,
         "--project-root", str(tmp_path)],
        capture_output=True, text=True)


def test_cli_round_trip(tmp_path) -> None:
    path = _state(tmp_path)
    out = _cli(tmp_path, "review-reopen", "--state-file", path, "--source", "design",
               "--out", str(tmp_path / "tok.json"))
    assert out.returncode == 0, out.stdout + out.stderr
    assert _read(path)["design"] == 0, "the CLI mint must not debit"
    nonce = json.loads((tmp_path / "tok.json").read_text())["nonce"]

    assert _cli(tmp_path, "loopback-open-round", "--state-file", path,
                "--nonce", nonce, "--actor", "t").returncode == 0
    assert _cli(tmp_path, "loopback-commit", "--state-file", path,
                "--nonce", nonce, "--actor", "t").returncode == 0
    assert _read(path)["design"] == 1


def test_cli_refuses_a_state_file_outside_the_project_root(tmp_path) -> None:
    """B10: the containment rule guarded `--out`, an argument these commands do not take. The
    externally supplied path they DO take is `--state-file`, and it is the file they REPLACE."""
    outside = tmp_path.parent / "escape.json"
    outside.write_text("{}", encoding="utf-8")
    out = _cli(tmp_path, "loopback-status", "--state-file", str(outside))
    assert out.returncode == 2, out.stdout + out.stderr
