"""Consult mode + --key backward compatibility for adversarial_review_lib."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import adversarial_review_lib as arl  # noqa: E402

LIB = HOOKS / "adversarial_review_lib.py"


def _ws(tmp_path, entry):
    p = tmp_path / ".rawgentic_workspace.json"
    p.write_text(json.dumps({"version": 1, "projects": [entry]}))
    return str(p)


# --- CLI runner-level tests (subprocess entry path, PATH-stubbed codex) ---
# Mirrors the stub/run pattern in test_adversarial_review_cli.py exactly.

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


def test_cli_consult_success_writes_proposal_to_out(tmp_path):
    # Stub emits an extra, out-of-schema field so the assertion can only pass
    # if run_codex_consult actually parsed + re-serialized (not just passed
    # the stub's raw bytes through untouched).
    raw = json.dumps({"approach": "A", "key_decisions": ["d1"], "risks": ["r1"],
                       "sketch": "s", "unexpected_field": "must be stripped"})
    _make_codex_stub(tmp_path / "bin", exec_body=raw)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "problem.md"; art.write_text("# Problem")
    out = tmp_path / "out.json"
    r = _run(["consult", "--artifact", str(art), "--project-root", str(root),
              "--out", str(out), "--date", "2026-07-03"],
             extra_path=tmp_path / "bin")
    assert r.returncode == 0
    data = json.loads(out.read_text())
    assert data == {"approach": "A", "key_decisions": ["d1"], "risks": ["r1"], "sketch": "s"}
    assert "unexpected_field" not in data


def test_cli_consult_codex_error_exit3_writes_marker(tmp_path):
    _make_codex_stub(tmp_path / "bin", exec_rc=1)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "problem.md"; art.write_text("x")
    out = tmp_path / "out.json"
    r = _run(["consult", "--artifact", str(art), "--project-root", str(root),
              "--out", str(out)], extra_path=tmp_path / "bin")
    assert r.returncode == 3
    assert json.loads(out.read_text()) == arl._EMPTY_PROPOSAL


def test_cli_consult_parse_error_exit4_writes_marker(tmp_path):
    _make_codex_stub(tmp_path / "bin", exec_body="not json")
    root = tmp_path / "proj"; root.mkdir()
    art = root / "problem.md"; art.write_text("x")
    out = tmp_path / "out.json"
    r = _run(["consult", "--artifact", str(art), "--project-root", str(root),
              "--out", str(out)], extra_path=tmp_path / "bin")
    assert r.returncode == 4
    assert json.loads(out.read_text()) == arl._EMPTY_PROPOSAL


def test_cli_consult_not_installed_exit2_writes_marker(tmp_path):
    root = tmp_path / "proj"; root.mkdir()
    art = root / "problem.md"; art.write_text("x")
    out = tmp_path / "out.json"
    r = _run(["consult", "--artifact", str(art), "--project-root", str(root),
              "--out", str(out)], strip_codex=True)
    assert r.returncode == 2
    assert json.loads(out.read_text()) == arl._EMPTY_PROPOSAL


def test_cli_is_enabled_peerconsult_key_via_subprocess(tmp_path):
    # adversarialReview and peerConsult deliberately differ so a pass proves
    # --key actually selects the block (not just falling through to default).
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                         "adversarialReview": {"enabled": False, "workflows": []},
                         "peerConsult": {"enabled": True, "workflows": ["implement-feature"]}})
    r = _run(["is-enabled", "--workspace", ws, "--project", "app",
              "--skill", "implement-feature", "--key", "peerConsult"])
    assert r.returncode == 0
    assert "enabled" in r.stdout


def test_cli_is_enabled_default_key_still_adversarial_via_subprocess(tmp_path):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                         "adversarialReview": {"enabled": False, "workflows": []},
                         "peerConsult": {"enabled": True, "workflows": ["implement-feature"]}})
    r = _run(["is-enabled", "--workspace", ws, "--project", "app",
              "--skill", "implement-feature"])  # no --key
    assert r.returncode == 1
    assert "disabled" in r.stdout


def test_is_enabled_default_key_is_adversarial(tmp_path):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "adversarialReview": {"enabled": True, "workflows": ["implement-feature"]}})
    assert arl.is_enabled_for(ws, "app", "implement-feature") is True
    # peerConsult absent -> not enabled under that key
    assert arl.is_enabled_for(ws, "app", "implement-feature", key="peerConsult") is False


def test_is_enabled_peerconsult_key(tmp_path):
    ws = _ws(tmp_path, {"name": "app", "path": "./p",
                        "peerConsult": {"enabled": True, "workflows": ["implement-feature"]}})
    assert arl.is_enabled_for(ws, "app", "implement-feature", key="peerConsult") is True
    assert arl.is_enabled_for(ws, "app", "fix-bug", key="peerConsult") is False


def test_proposal_schema_shape():
    props = arl.PROPOSAL_SCHEMA["properties"]
    assert set(props) >= {"approach", "key_decisions", "risks", "sketch"}


def test_build_consult_prompt_has_peer_framing():
    p = arl.build_consult_prompt("Design X.", nonce="NONCE123")
    assert "peer" in p.lower()
    assert "not a reviewer" in p.lower()
    assert "NONCE123" in p  # nonce-fenced


def test_consult_report_path_shape(tmp_path):
    path = arl.consult_report_path(str(tmp_path), "my-problem.md", "2026-07-03")
    assert path.endswith("/docs/reviews/peer-my-problem-2026-07-03.md")


def test_render_consult_md_contains_sections():
    md = arl.render_consult_md(
        {"approach": "A", "key_decisions": ["d1"], "risks": ["r1"], "sketch": "s"},
        {"artifact": "x.md", "date": "2026-07-03"},
    )
    assert "Approach" in md and "d1" in md and "r1" in md
