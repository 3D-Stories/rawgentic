"""Test-gate import shim for the phase_executor package.

The repo has no root packaging (hooks are imported via sys.path.insert), so the gate
imports the package the same way: put its src/ dir on sys.path. Production consumers
(E2-E8) import the INSTALLED package (see tests/phase_executor/test_packaging.py); this
shim is the test-gate convenience only.
"""
import os
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "phase_executor" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: exercises real provider CLIs/SDKs (claude/codex/zhipuai). Skipped unless "
        "RUN_LIVE=1 — needs auth + tools, never runs in CI.",
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="live test (set RUN_LIVE=1 to run; needs real CLIs/SDK auth)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
