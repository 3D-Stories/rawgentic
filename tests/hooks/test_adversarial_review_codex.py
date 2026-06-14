"""Tests for adversarial_review_lib Codex invocation (issue #77, Task 2).

The `codex` binary is PATH-stubbed via a fake script — NO live calls in CI.
Every Codex failure path must be fail-closed (non-success status).
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import adversarial_review_lib as arl  # noqa: E402


def _make_codex_stub(bin_dir: Path, *, login_rc: int = 0, exec_body: str = "",
                     exec_rc: int = 0, sleep: float = 0.0) -> None:
    """Write a fake `codex` that handles `login status` and `exec`.

    For `exec`, writes exec_body to the path following `-o` and exits exec_rc.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "codex"
    body = exec_body.replace("'", "'\\''")
    script.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "login" ] && [ "$2" = "status" ]; then\n'
        f"  echo 'Logged in'; exit {login_rc}\n"
        "fi\n"
        'if [ "$1" = "--version" ]; then echo "codex-cli 0.139.0"; exit 0; fi\n'
        'if [ "$1" = "exec" ]; then\n'
        f"  sleep {sleep}\n"
        "  out=\"\"\n"
        "  while [ $# -gt 0 ]; do\n"
        '    if [ "$1" = "-o" ]; then out="$2"; fi\n'
        "    shift\n"
        "  done\n"
        f"  if [ -n \"$out\" ]; then printf '%s' '{body}' > \"$out\"; fi\n"
        f"  exit {exec_rc}\n"
        "fi\n"
        "exit 0\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _path_with(bin_dir: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))


def _path_without_codex(monkeypatch) -> None:
    original = os.environ.get("PATH", "")
    filtered = [d for d in original.split(os.pathsep)
                if d and not os.path.isfile(os.path.join(d, "codex"))]
    monkeypatch.setenv("PATH", os.pathsep.join(filtered))


# --- prereq detection ---

def test_codex_installed_true_with_stub(tmp_path, monkeypatch):
    _make_codex_stub(tmp_path / "bin")
    _path_with(tmp_path / "bin", monkeypatch)
    assert arl.codex_installed() is True


def test_codex_installed_false_without(monkeypatch):
    _path_without_codex(monkeypatch)
    assert arl.codex_installed() is False


def test_codex_authenticated_true(tmp_path, monkeypatch):
    _make_codex_stub(tmp_path / "bin", login_rc=0)
    _path_with(tmp_path / "bin", monkeypatch)
    assert arl.codex_authenticated() is True


def test_codex_authenticated_false_on_nonzero(tmp_path, monkeypatch):
    _make_codex_stub(tmp_path / "bin", login_rc=1)
    _path_with(tmp_path / "bin", monkeypatch)
    assert arl.codex_authenticated() is False


def test_prereq_not_installed(monkeypatch):
    _path_without_codex(monkeypatch)
    ok, msg = arl.prereq_status()
    assert ok is False
    assert "install" in msg.lower()


def test_prereq_unauthenticated_interactive(tmp_path, monkeypatch):
    _make_codex_stub(tmp_path / "bin", login_rc=1)
    _path_with(tmp_path / "bin", monkeypatch)
    ok, msg = arl.prereq_status(headless=False)
    assert ok is False
    assert "codex login" in msg


def test_prereq_unauthenticated_headless_message(tmp_path, monkeypatch):
    _make_codex_stub(tmp_path / "bin", login_rc=1)
    _path_with(tmp_path / "bin", monkeypatch)
    ok, msg = arl.prereq_status(headless=True)
    assert ok is False
    assert "headless" in msg.lower()
    assert "with-api-key" in msg


def test_prereq_ok(tmp_path, monkeypatch):
    _make_codex_stub(tmp_path / "bin", login_rc=0)
    _path_with(tmp_path / "bin", monkeypatch)
    ok, _ = arl.prereq_status()
    assert ok is True


# --- build_prompt ---

def test_build_prompt_includes_artifact_and_lens():
    p = arl.build_prompt("# My Design", "design")
    assert "My Design" in p
    assert "architectural" in p.lower()
    assert "ARTIFACT START" in p


def test_build_prompt_unknown_type_uses_generic_lens():
    p = arl.build_prompt("text", "weird-type")
    assert "broadly" in p.lower()  # generic lens text


# --- run_codex_review: success + every fail-closed path ---

def _valid_output() -> str:
    return json.dumps({
        "summary": "ok",
        "findings": [
            {"severity": "High", "category": "security", "description": "Issue here",
             "recommendation": "Fix it", "location": "S2"},
            {"severity": "Low", "category": "scope", "description": "Minor",
             "recommendation": "Consider", "location": "S3"},
        ],
    })


def test_run_success(tmp_path, monkeypatch):
    _make_codex_stub(tmp_path / "bin", exec_body=_valid_output())
    _path_with(tmp_path / "bin", monkeypatch)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "design.md"; art.write_text("# Design\nstuff")
    res = arl.run_codex_review(str(art), "design", str(root))
    assert res.status == "success"
    assert len(res.findings) == 2
    # ranked: High before Low
    assert res.findings[0]["severity"] == "High"


def test_run_not_installed(tmp_path, monkeypatch):
    _path_without_codex(monkeypatch)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    res = arl.run_codex_review(str(art), "design", str(root))
    assert res.status == "not_installed"
    assert res.findings == ()


def test_run_unauthenticated(tmp_path, monkeypatch):
    _make_codex_stub(tmp_path / "bin", login_rc=1)
    _path_with(tmp_path / "bin", monkeypatch)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    res = arl.run_codex_review(str(art), "design", str(root))
    assert res.status == "unauthenticated"
    assert res.findings == ()


def test_run_nonzero_exit_is_error(tmp_path, monkeypatch):
    _make_codex_stub(tmp_path / "bin", exec_rc=1, exec_body="")
    _path_with(tmp_path / "bin", monkeypatch)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    res = arl.run_codex_review(str(art), "design", str(root))
    assert res.status == "error"
    assert res.findings == ()


def test_run_malformed_json_is_parse_error(tmp_path, monkeypatch):
    _make_codex_stub(tmp_path / "bin", exec_body="this is not json")
    _path_with(tmp_path / "bin", monkeypatch)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    res = arl.run_codex_review(str(art), "design", str(root))
    assert res.status == "parse_error"


def test_run_invalid_finding_is_parse_error(tmp_path, monkeypatch):
    bad = json.dumps({"findings": [{"severity": "Nope", "category": "x",
                                    "description": "y", "recommendation": "z"}]})
    _make_codex_stub(tmp_path / "bin", exec_body=bad)
    _path_with(tmp_path / "bin", monkeypatch)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    res = arl.run_codex_review(str(art), "design", str(root))
    assert res.status == "parse_error"


def test_run_timeout_is_timeout(tmp_path, monkeypatch):
    _make_codex_stub(tmp_path / "bin", exec_body=_valid_output(), sleep=2)
    _path_with(tmp_path / "bin", monkeypatch)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    res = arl.run_codex_review(str(art), "design", str(root), timeout=1)
    assert res.status == "timeout"


def test_run_blocks_secrets_when_env_set(tmp_path, monkeypatch):
    monkeypatch.setattr(arl, "BLOCK_SECRETS", True)
    _make_codex_stub(tmp_path / "bin", exec_body=_valid_output())
    _path_with(tmp_path / "bin", monkeypatch)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("API_KEY=sk-secret123")
    res = arl.run_codex_review(str(art), "design", str(root))
    assert res.status == "error"
    assert "secret" in res.raw_error.lower()


def test_run_cleans_temp_files(tmp_path, monkeypatch):
    _make_codex_stub(tmp_path / "bin", exec_body=_valid_output())
    _path_with(tmp_path / "bin", monkeypatch)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    arl.run_codex_review(str(art), "design", str(root))
    assert not (root / ".rawgentic-adv-review-schema.json").exists()
    assert not (root / ".rawgentic-adv-review-out.json").exists()
