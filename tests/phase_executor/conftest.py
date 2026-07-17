"""Test-gate import shim for the phase_executor package.

The repo has no root packaging (hooks are imported via sys.path.insert), so the gate
imports the package the same way: put its src/ dir on sys.path. Production consumers
(E2-E8) import the INSTALLED package (see tests/phase_executor/test_packaging.py); this
shim is the test-gate convenience only.
"""
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "phase_executor" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
