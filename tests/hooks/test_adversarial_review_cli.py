"""Tests for adversarial_review_lib main() CLI (issue #77, Task 4).

Invokes the module as a subprocess (as the SKILL.md orchestrators do), with a
PATH-stubbed codex. Asserts the documented exit-code contract for the kept
subcommands: prereq (0 ok / 2 fail) and is-enabled (0 / 1). The review/consult
verbs moved to hooks/review_runner.py and were deleted here in #866 M0d.
"""
import json
import os
import stat
import subprocess
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
LIB = HOOKS_DIR / "adversarial_review_lib.py"


def _make_codex_stub(bin_dir: Path, *, login_rc=0):
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "codex"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "login" ] && [ "$2" = "status" ]; then exit %d; fi\n' % login_rc
        + "exit 0\n"
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


def test_cli_no_subcommand_errors():
    r = _run([])
    assert r.returncode != 0
