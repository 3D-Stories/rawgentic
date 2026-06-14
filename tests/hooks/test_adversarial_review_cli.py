"""Tests for adversarial_review_lib main() CLI (issue #77, Task 4).

Invokes the module as a subprocess (as the SKILL.md and WF2/WF3 hooks will),
with a PATH-stubbed codex. Asserts the documented exit-code contract:
0 ok, 2 prereq-fail, 3 codex-error, 4 parse-fail.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
LIB = HOOKS_DIR / "adversarial_review_lib.py"


def _make_codex_stub(bin_dir: Path, *, login_rc=0, exec_body="", exec_rc=0):
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "codex"
    body = exec_body.replace("'", "'\\''")
    script.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "login" ] && [ "$2" = "status" ]; then exit %d; fi\n' % login_rc
        + 'if [ "$1" = "exec" ]; then\n'
        "  out=\"\"; while [ $# -gt 0 ]; do if [ \"$1\" = \"-o\" ]; then out=\"$2\"; fi; shift; done\n"
        f"  if [ -n \"$out\" ]; then printf '%s' '{body}' > \"$out\"; fi\n"
        f"  exit {exec_rc}\n"
        "fi\nexit 0\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run(args, *, extra_path: Path | None = None, strip_codex=False):
    env = dict(os.environ)
    if strip_codex:
        env["PATH"] = os.pathsep.join(
            d for d in env.get("PATH", "").split(os.pathsep)
            if d and not os.path.isfile(os.path.join(d, "codex"))
        )
    if extra_path is not None:
        env["PATH"] = str(extra_path) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        ["python3", str(LIB), *args],
        capture_output=True, text=True, timeout=30, env=env,
    )


def _ws(tmp_path, adv):
    ws = tmp_path / ".rawgentic_workspace.json"
    ws.write_text(json.dumps({"version": 1, "projects": [
        {"name": "p", "path": "./projects/p", "adversarialReview": adv}]}))
    return ws


def _valid_output():
    return json.dumps({"summary": "s", "findings": [
        {"severity": "High", "category": "security", "description": "d",
         "recommendation": "r", "location": "S1"}]})


# --- prereq subcommand ---

def test_cli_prereq_ok(tmp_path):
    _make_codex_stub(tmp_path / "bin", login_rc=0)
    r = _run(["prereq"], extra_path=tmp_path / "bin")
    assert r.returncode == 0


def test_cli_prereq_not_installed_exit2(tmp_path):
    r = _run(["prereq"], strip_codex=True)
    assert r.returncode == 2


# --- is-enabled subcommand ---

def test_cli_is_enabled_true(tmp_path):
    ws = _ws(tmp_path, {"enabled": True, "workflows": ["implement-feature"]})
    r = _run(["is-enabled", "--workspace", str(ws), "--project", "p",
              "--skill", "implement-feature"])
    assert r.returncode == 0
    assert "enabled" in r.stdout


def test_cli_is_enabled_false_exit1(tmp_path):
    ws = _ws(tmp_path, {"enabled": False, "workflows": []})
    r = _run(["is-enabled", "--workspace", str(ws), "--project", "p",
              "--skill", "implement-feature"])
    assert r.returncode == 1


# --- review subcommand ---

def test_cli_review_success_writes_report(tmp_path):
    _make_codex_stub(tmp_path / "bin", exec_body=_valid_output())
    root = tmp_path / "proj"; root.mkdir()
    art = root / "design.md"; art.write_text("# Design")
    r = _run(["review", "--artifact", str(art), "--type", "design",
              "--project-root", str(root), "--date", "2026-06-14"],
             extra_path=tmp_path / "bin")
    assert r.returncode == 0
    report = root / "docs" / "reviews" / "design-md-2026-06-14.md"
    assert report.exists()
    assert "[High]" in report.read_text()


def test_cli_review_not_installed_exit2(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    r = _run(["review", "--artifact", str(art), "--project-root", str(root)],
             strip_codex=True)
    assert r.returncode == 2


def test_cli_review_codex_error_exit3(tmp_path):
    _make_codex_stub(tmp_path / "bin", exec_rc=1)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    r = _run(["review", "--artifact", str(art), "--project-root", str(root)],
             extra_path=tmp_path / "bin")
    assert r.returncode == 3


def test_cli_review_parse_error_exit4(tmp_path):
    _make_codex_stub(tmp_path / "bin", exec_body="not json")
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    r = _run(["review", "--artifact", str(art), "--project-root", str(root)],
             extra_path=tmp_path / "bin")
    assert r.returncode == 4


def test_cli_review_does_not_edit_artifact(tmp_path):
    _make_codex_stub(tmp_path / "bin", exec_body=_valid_output())
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("ORIGINAL")
    _run(["review", "--artifact", str(art), "--project-root", str(root),
          "--date", "2026-06-14"], extra_path=tmp_path / "bin")
    assert art.read_text() == "ORIGINAL"


def test_cli_no_subcommand_errors():
    r = _run([])
    assert r.returncode != 0


def test_cli_review_write_failure_is_fail_closed(tmp_path):
    # F3 regression: if the report dir can't be created, exit 3 (not a traceback).
    _make_codex_stub(tmp_path / "bin", exec_body=_valid_output())
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    # Make 'docs' a FILE so os.makedirs(docs/reviews) fails with OSError.
    (root / "docs").write_text("not a dir")
    r = _run(["review", "--artifact", str(art), "--project-root", str(root),
              "--date", "2026-06-14"], extra_path=tmp_path / "bin")
    assert r.returncode == 3
    assert "Traceback" not in r.stderr
