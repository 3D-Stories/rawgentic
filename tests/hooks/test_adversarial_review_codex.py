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
        # Capture full argv (before the shift loop consumes it) so a test can
        # assert the invocation flags. Opt-in via CODEX_STUB_ARGS_FILE.
        '  if [ -n "$CODEX_STUB_ARGS_FILE" ]; then printf \'%s\\n\' "$@" > "$CODEX_STUB_ARGS_FILE"; fi\n'
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

def test_build_prompt_loopback_class_rubric_present():
    # #407: the prompt must define both classes with WF2's own rubric wording,
    # the unsure default, the boundary clarifier, and null-for-Medium/Low.
    p = arl.build_prompt("# My Design", "design", nonce="abc123")
    assert '"spec-tightening"' in p and '"design-flaw"' in p
    assert "for Critical and High findings only" in p
    assert "INTENT is right but its text is wrong" in p
    assert "verbatim in the recommendation" in p
    # Peer-adopted boundary clarifier: doc-shaped edits that change behavior
    # are still design flaws.
    assert ("contracts, executable behavior, data shape, ordering, or "
            "verification strategy") in p
    assert 'unsure, use "design-flaw"' in p
    assert "null for Medium/Low findings" in p


def test_build_prompt_injection_guard_covers_loopback_classifications():
    # #407: steering the loop-back classification is a named attack.
    p = arl.build_prompt("body", "design", nonce="n")
    assert "severity or loop-back classifications" in p


def test_build_prompt_includes_artifact_and_lens():
    p = arl.build_prompt("# My Design", "design", nonce="abc123")
    assert "My Design" in p
    assert "architectural" in p.lower()
    assert "BEGIN UNTRUSTED ARTIFACT" in p and "END UNTRUSTED ARTIFACT" in p


def test_build_prompt_unknown_type_uses_generic_lens():
    p = arl.build_prompt("text", "weird-type", nonce="n")
    assert "broadly" in p.lower()  # generic lens text


def test_build_prompt_nonce_in_both_fence_lines_and_instruction():
    # The same nonce must appear in BOTH fence markers AND the instruction that
    # references it; if they drift apart the unforgeable-delimiter guard weakens.
    p = arl.build_prompt("body", "design", nonce="DEADBEEF")
    assert p.count("[k=DEADBEEF]") == 3  # 2 fence lines + 1 instruction reference


def test_build_prompt_generates_unforgeable_nonce_when_omitted():
    a = arl.build_prompt("x", "design")
    b = arl.build_prompt("x", "design")
    # Each call mints a fresh random nonce (untrusted text can't predict it).
    assert a != b


def test_build_prompt_has_injection_and_tool_guards():
    p = arl.build_prompt("payload", "design", nonce="n")
    assert "STRICTLY FORBIDDEN" in p            # forbid shell/tools (bwrap workaround)
    assert "untrusted DATA" in p                # data-not-instructions framing
    assert "SEVERITY RUBRIC" in p               # de-inflation rubric
    assert "GROUNDING" in p and "verbatim" in p  # evidence grounding rule
    assert "Respond using the provided output schema only." in p


# --- run_codex_review: success + every fail-closed path ---

def _valid_output() -> str:
    return json.dumps({
        "summary": "ok",
        "findings": [
            {"evidence": "q1", "severity": "High", "category": "security",
             "confidence": "high", "description": "Issue here",
             "recommendation": "Fix it", "location": "S2"},
            {"evidence": "q2", "severity": "Low", "category": "scope",
             "confidence": "low", "description": "Minor",
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
    # model/effort recorded for report auditability (effort pinned, model inherited)
    assert res.effort == arl.REASONING_EFFORT
    assert res.model == (arl.REVIEW_MODEL or "")


def test_run_invocation_pins_effort_ephemeral_and_clean_output(tmp_path, monkeypatch):
    args_file = tmp_path / "argv.txt"
    monkeypatch.setenv("CODEX_STUB_ARGS_FILE", str(args_file))
    _make_codex_stub(tmp_path / "bin", exec_body=_valid_output())
    _path_with(tmp_path / "bin", monkeypatch)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "design.md"; art.write_text("# Design\nstuff")
    res = arl.run_codex_review(str(art), "design", str(root))
    assert res.status == "success"
    argv = args_file.read_text().splitlines()
    # the quality/privacy/parse-safety/independence levers must all be present
    assert f"model_reasoning_effort={arl.REASONING_EFFORT}" in argv
    assert "--ephemeral" in argv
    assert "--color" in argv and "never" in argv
    assert "project_doc_max_bytes=0" in argv
    assert "read-only" in argv               # sandbox belt-and-suspenders kept
    assert "--skip-git-repo-check" in argv
    # model NOT pinned by default (would rot as OpenAI retires model ids)
    assert "-m" not in argv


def test_run_invocation_pins_model_when_env_set(tmp_path, monkeypatch):
    args_file = tmp_path / "argv.txt"
    monkeypatch.setenv("CODEX_STUB_ARGS_FILE", str(args_file))
    monkeypatch.setattr(arl, "REVIEW_MODEL", "gpt-some-model")
    _make_codex_stub(tmp_path / "bin", exec_body=_valid_output())
    _path_with(tmp_path / "bin", monkeypatch)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "design.md"; art.write_text("# Design\nstuff")
    res = arl.run_codex_review(str(art), "design", str(root))
    assert res.status == "success"
    argv = args_file.read_text().splitlines()
    assert "-m" in argv and "gpt-some-model" in argv
    assert res.model == "gpt-some-model"


def test_run_invalid_when_codex_drops_evidence(tmp_path, monkeypatch):
    # If Codex returns findings missing the required grounding quote, fail-closed
    # (parse_error) rather than presenting ungrounded findings as a clean review.
    bad = json.dumps({"summary": "s", "findings": [
        {"severity": "High", "category": "security", "confidence": "high",
         "description": "d", "recommendation": "r", "location": "S1",
         "ambiguity_flag": None, "ambiguity_reason": None}]})  # no evidence
    _make_codex_stub(tmp_path / "bin", exec_body=bad)
    _path_with(tmp_path / "bin", monkeypatch)
    root = tmp_path / "proj"; root.mkdir()
    art = root / "d.md"; art.write_text("x")
    res = arl.run_codex_review(str(art), "design", str(root))
    assert res.status == "parse_error"


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
