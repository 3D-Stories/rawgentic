"""Tests for adversarial_review_lib Codex prereq detection + build_prompt (issue #77).

The `codex` binary is PATH-stubbed via a fake script — NO live calls in CI.
The invocation functions (run_codex_review et al.) were deleted in #866 M0d;
invocation coverage lives in tests/hooks/test_review_runner.py.
"""
import os
import stat
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import adversarial_review_lib as arl  # noqa: E402


def _make_codex_stub(bin_dir: Path, *, login_rc: int = 0) -> None:
    """Write a fake `codex` that handles `login status`."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "codex"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "login" ] && [ "$2" = "status" ]; then\n'
        f"  echo 'Logged in'; exit {login_rc}\n"
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


def test_prereq_unauthenticated(tmp_path, monkeypatch):
    _make_codex_stub(tmp_path / "bin", login_rc=1)
    _path_with(tmp_path / "bin", monkeypatch)
    ok, msg = arl.prereq_status()
    assert ok is False
    assert "codex login" in msg


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


# --- the pre-#393 golden: build_prompt output is pinned byte-for-byte ---

_GOLDEN_PRE393 = Path(__file__).resolve().parent.parent / "fixtures" / \
    "build_prompt_golden_pre393_plan.txt"


def test_build_prompt_no_ledger_byte_identical_to_pre393_golden():
    # The golden was captured from the PRE-#393 build_prompt at RED time — the
    # surviving (ledger-free, #866 M0d) build_prompt must match it byte-for-byte.
    golden = _GOLDEN_PRE393.read_text(encoding="utf-8")
    p = arl.build_prompt(
        "GOLDEN-ARTIFACT-BODY", "plan",
        nonce="cafef00dcafef00dcafef00dcafef00d")
    assert p == golden
