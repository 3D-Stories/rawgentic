"""Consult-mode library pieces + --key backward compatibility for
adversarial_review_lib. The `consult` CLI verb moved to hooks/review_runner.py
(#866 M0d); its subprocess coverage lives in tests/hooks/test_review_runner.py."""
import json
import os
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


def _run(args):
    return subprocess.run(
        ["python3", str(LIB), *args],
        capture_output=True, text=True, timeout=30, env=dict(os.environ),
    )


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
