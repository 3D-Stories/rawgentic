"""Tests for `plan_lib review-reopen` — the M0a reopen-token mint (#866, #855).

The verb is the ONE choke point that authorizes an actionable review round:
it debits the existing atomic loop-back budget (consume_loopback) and mints a
token file the review runner requires for a non-diagnostic result. Tokenless
runner runs are diagnostic-only; transport retries reuse the token and never
debit again.

CLI is exercised black-box via subprocess (the house pattern).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import plan_lib  # noqa: E402

CLI = str(HOOKS_DIR / "plan_lib.py")


def _run(args, cwd=None):
    return subprocess.run(
        [sys.executable, CLI, *args],
        capture_output=True, text=True, cwd=cwd, timeout=30,
    )


def _mint_args(tmp_path, source="review", state=None, out=None):
    state_file = state or str(tmp_path / "loopback-state.json")
    out_file = out or str(tmp_path / "reopen-token.json")
    return [
        "review-reopen",
        "--state-file", state_file,
        "--source", source,
        "--out", out_file,
        "--project-root", str(tmp_path),
    ], state_file, out_file


# --- happy path ---

def test_mint_writes_token_and_debits(tmp_path):
    args, state_file, out_file = _mint_args(tmp_path)
    result = _run(args)
    assert result.returncode == 0, result.stderr
    token = json.loads(Path(out_file).read_text())
    assert token["version"] == 1
    assert token["source"] == "review"
    assert isinstance(token["nonce"], str) and len(token["nonce"]) >= 16
    assert token["minted_at"].endswith("Z")
    assert "consumed_at" not in token
    state = json.loads(Path(state_file).read_text())
    assert state["review"] == 1
    assert state["total"] == 1


def test_mint_reports_token_path_on_stdout(tmp_path):
    args, _, out_file = _mint_args(tmp_path)
    result = _run(args)
    assert result.returncode == 0
    assert out_file in result.stdout


def test_mint_no_stray_tmp_files(tmp_path):
    args, _, _ = _mint_args(tmp_path)
    result = _run(args)
    assert result.returncode == 0
    strays = [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]
    assert strays == []


# --- budget exhaustion (fail-closed, no token) ---

def test_mint_refuses_when_source_cap_reached(tmp_path):
    args, state_file, out_file = _mint_args(tmp_path)
    assert _run(args).returncode == 0
    out2 = str(tmp_path / "token2.json")
    args2, _, _ = _mint_args(tmp_path, state=state_file, out=out2)
    result = _run(args2)
    assert result.returncode == 3
    assert not os.path.exists(out2)
    assert "review" in result.stderr


def test_mint_refuses_when_global_budget_exhausted(tmp_path):
    state_file = str(tmp_path / "loopback-state.json")
    exhausted = {s: 0 for s in plan_lib._LOOPBACK_SOURCES}
    exhausted["design"] = 2
    exhausted["tdd"] = 1
    exhausted["total"] = 3
    Path(state_file).write_text(json.dumps(exhausted))
    args, _, out_file = _mint_args(tmp_path, state=state_file)
    result = _run(args)
    assert result.returncode == 3
    assert not os.path.exists(out_file)


def test_mint_unknown_source_is_a_usage_error(tmp_path):
    args, _, out_file = _mint_args(tmp_path, source="nonsense")
    result = _run(args)
    assert result.returncode == 2
    assert not os.path.exists(out_file)


# --- containment ---

def test_mint_refuses_out_path_outside_project_root(tmp_path):
    escape = tmp_path / "outside"
    escape.mkdir()
    inner = tmp_path / "root"
    inner.mkdir()
    result = _run([
        "review-reopen",
        "--state-file", str(inner / "state.json"),
        "--source", "review",
        "--out", str(escape / "token.json"),
        "--project-root", str(inner),
    ])
    assert result.returncode == 2
    assert not (escape / "token.json").exists()


def test_mint_nonces_are_unique(tmp_path):
    # spec_tighten has a per-source cap of 2 — two mints must not share a nonce.
    args1, state_file, out1 = _mint_args(tmp_path, source="spec_tighten")
    assert _run(args1).returncode == 0
    out2 = str(tmp_path / "token2.json")
    args2, _, _ = _mint_args(tmp_path, source="spec_tighten",
                             state=state_file, out=out2)
    assert _run(args2).returncode == 0
    n1 = json.loads(Path(out1).read_text())["nonce"]
    n2 = json.loads(Path(out2).read_text())["nonce"]
    assert n1 != n2
