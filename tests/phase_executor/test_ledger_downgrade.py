"""#855 Task 4 — what a BASE-VERSION reader does with a ledger written after the upgrade.

Task 1 proved the new reader tolerates old data. This is the reverse direction, and it is the one
that decides whether `git revert` is a safe rollback: after the upgrade the ledger carries a new
FIELD (`review_admission`) and, once PR 1b lands, new record KINDS (observations).

The two directions turn out to differ, and the difference is the rollback constraint:

* an unknown FIELD on an expected record is ignored by the base parser — tolerated;
* an unknown KIND is refused by `kind not in _KINDS` — fail-closed, by design.

So a plain revert is safe while only admission fields exist, and once observation records are being
written an upgraded run must be completed or archived before downgrading. Recorded here as an
executable pin rather than a sentence in a plan, because plan revision 1 asserted "no persisted
state shape change, safely revertible" and that was simply wrong.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from phase_executor import ledger as L

BASE_REF = "origin/main"
MODULE_PATH = "phase_executor/src/phase_executor/ledger.py"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _base_ledger_module(tmp_path):
    """Load the ledger module as it exists at BASE_REF — the real base reader, not a stand-in."""
    try:
        src = subprocess.run(
            ["git", "show", f"{BASE_REF}:{MODULE_PATH}"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip(f"{BASE_REF}:{MODULE_PATH} not resolvable here")
    if "review_admission" in src:
        pytest.skip(f"{BASE_REF} already carries #855; this pin is only meaningful pre-merge")

    path = tmp_path / "base_ledger.py"
    path.write_text(src, encoding="utf-8")
    # Name it INSIDE the package so `from .enforce import ...` resolves and __package__ derives
    # correctly — setting __package__ by hand instead leaves __spec__.parent mismatched and emits
    # a DeprecationWarning into everyone else's test run.
    name = "phase_executor.base_ledger_855"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:                                    # pragma: no cover - environment
        pytest.skip(f"base ledger module not importable in isolation ({e})")
    return mod


def _write(path: Path, run_id: str, recs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"run_id": run_id, **r}, sort_keys=True) + "\n"
                            for r in recs), encoding="utf-8")


ADMISSION = {"schema_version": 1, "issue_key": "855", "workflow": "wf2", "gate": "11",
             "generation": 0, "digest": "sha256:abc", "slot": "reviewer-1", "attempt": 0,
             "fencing_token": "tok-1"}


def test_base_reader_tolerates_the_new_admission_field(tmp_path):
    """An extra key on an expected record is ignored by the base parser → revert is safe."""
    base = _base_ledger_module(tmp_path)
    run_dir = tmp_path / "run"
    led = base.ExpectedCallLedger(run_dir, "r1")
    _write(led.path, "r1", [
        {"kind": "initial", "initial_digest": "sha256:d", "architecture": "executor"},
        {"kind": "expected", "seat": "review", "correlation_id": "c1", "recovered_from": None,
         "review_admission": ADMISSION},
    ])
    state = led.read()
    assert len(state.expected) == 1
    assert state.expected[0].correlation_id == "c1"


def test_base_reader_refuses_an_unknown_kind(tmp_path):
    """This is the rollback CONSTRAINT: once observation kinds are written, a base reader fails
    closed, so an upgraded run must be completed or archived before downgrading."""
    base = _base_ledger_module(tmp_path)
    run_dir = tmp_path / "run"
    led = base.ExpectedCallLedger(run_dir, "r1")
    _write(led.path, "r1", [
        {"kind": "initial", "initial_digest": "sha256:d", "architecture": "executor"},
        {"kind": "review_observation", "outcome": "success", "correlation_id": "c1"},
    ])
    with pytest.raises(base.LedgerError):
        led.read()


# -- properties of the CURRENT reader, always exercised -----------------------------------------

def test_current_reader_also_refuses_an_unknown_kind(tmp_path):
    """Symmetry: the constraint above is not an artefact of the old code."""
    led = L.ExpectedCallLedger(tmp_path, "r1")
    _write(led.path, "r1", [
        {"kind": "initial", "initial_digest": "sha256:d", "architecture": "executor"},
        {"kind": "review_observation", "outcome": "success"},
    ])
    with pytest.raises(L.LedgerError):
        led.read()


def test_current_reader_ignores_an_unrelated_unknown_field(tmp_path):
    """Mirrors the tolerance the base reader has: unknown FIELDS on an expected record pass."""
    led = L.ExpectedCallLedger(tmp_path, "r1")
    _write(led.path, "r1", [
        {"kind": "initial", "initial_digest": "sha256:d", "architecture": "executor"},
        {"kind": "expected", "seat": "review", "correlation_id": "c1", "recovered_from": None,
         "some_future_field": 1},
    ])
    assert len(led.read().expected) == 1
