"""Tests for hooks/review_runner.py — the M0a review runner (#866, roadmap §4 M0a).

The one small cross-model review entry point (D174/D179) that will serve WF2
Steps 4/8a/11, WF5 and WF13 after the M0b cutover. Lands UNUSED in M0a.

Invariants under test (the roadmap's load-bearing ACs):
- reviewer identity pinned: explicit `-m`, refuse author==reviewer or
  unresolvable, never an empty reviewer_model string on an egressed result;
- reopen-token choke point (#855): tokenless -> diagnostic:true; a valid token
  -> actionable and consumed; a spent/malformed token REFUSES;
- oversize input REFUSES (never truncate-and-continue, #834);
- error classes, not a blanket retry (#857): org-429 terminal, per-account 429
  may switch backend once, transport one retry, else recorded-unclassified;
- dead process / empty output = terminal FAILURE (#766);
- result carries input_sha256 / base_sha / head_sha / timing / diagnostic;
- fixed visible noise-strip list, no generic docs-only skip (#793).

The `codex` binary is PATH-stubbed (house pattern) — no live calls in CI.
GLM is exercised via an injected fake glm_fn — the SDK is never imported.
"""
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import review_runner as rr  # noqa: E402

CLI = str(HOOKS_DIR / "review_runner.py")

VALID_FINDING = {
    "evidence": "the guard returns True on error",
    "severity": "High",
    "category": "correctness",
    "confidence": 0.85,
    "description": "fail-open guard",
    "recommendation": "return False on error",
    "ambiguity_flag": None,
    "ambiguity_reason": None,
    "location": "hooks/x.py:10",
    "loopback_class": "design-flaw",
}
VALID_BODY = json.dumps({"summary": "one real defect", "findings": [VALID_FINDING]})
# #902: legacy word-confidence body — exercises the retry-then-map fallback.
WORD_BODY = json.dumps({"summary": "one real defect",
                        "findings": [dict(VALID_FINDING, confidence="high")]})
GARBAGE_CONF_BODY = json.dumps({"summary": "s",
                                "findings": [dict(VALID_FINDING, confidence="certain")]})
# Hoisted so the keyword and quoted value never share a line: gitleaks'
# generic-api-key rule false-positives on `consumed_at="<iso timestamp>"`.
SPENT_AT = "2026-08-03T20:30:00Z"
VALID_PROPOSAL = json.dumps({
    "approach": "small module", "key_decisions": ["k1"],
    "risks": ["r1"], "sketch": "def f(): ...",
})


# ---------------------------------------------------------------------------
# The parameterized codex stub: behavior driven entirely by CODEX_STUB_* env
# vars so ONE script serves every scenario. Counts invocations.
# ---------------------------------------------------------------------------

STUB_SCRIPT = r"""#!/usr/bin/env bash
if [ "$1" = "login" ] && [ "$2" = "status" ]; then echo "Logged in"; exit 0; fi
if [ "$1" != "exec" ]; then exit 0; fi
n=0
if [ -n "$CODEX_STUB_COUNT_FILE" ]; then
  [ -f "$CODEX_STUB_COUNT_FILE" ] && n=$(cat "$CODEX_STUB_COUNT_FILE")
  n=$((n+1)); printf '%s' "$n" > "$CODEX_STUB_COUNT_FILE"
fi
if [ -n "$CODEX_STUB_ARGS_FILE" ]; then printf '%s\n' "$@" > "$CODEX_STUB_ARGS_FILE"; fi
[ -n "$CODEX_STUB_SLEEP" ] && sleep "$CODEX_STUB_SLEEP"
if [ -n "$CODEX_STUB_STDIN_FILE" ]; then cat - > "$CODEX_STUB_STDIN_FILE"; fi
if [ -n "$CODEX_STUB_FAIL_FIRST" ] && [ "$n" = "1" ]; then
  printf '%s\n' "$CODEX_STUB_FAIL_FIRST_STDERR" >&2
  exit "${CODEX_STUB_FAIL_FIRST_RC:-1}"
fi
if [ -n "$CODEX_STUB_FAIL_SECOND" ] && [ "$n" = "2" ]; then
  printf '%s\n' "$CODEX_STUB_FAIL_SECOND_STDERR" >&2
  exit "${CODEX_STUB_FAIL_SECOND_RC:-1}"
fi
if [ -n "$CODEX_STUB_STDERR" ]; then printf '%s\n' "$CODEX_STUB_STDERR" >&2; fi
if [ -n "$CODEX_STUB_STDOUT" ]; then printf '%s\n' "$CODEX_STUB_STDOUT"; fi
rc="${CODEX_STUB_RC:-0}"
if [ "$rc" != "0" ]; then exit "$rc"; fi
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; fi
  shift
done
if [ -n "$CODEX_STUB_BODY_FIRST" ] && [ "$n" = "1" ]; then
  if [ -n "$out" ]; then printf '%s' "$CODEX_STUB_BODY_FIRST" > "$out"; fi
  exit 0
fi
if [ -n "$out" ] && [ -n "$CODEX_STUB_BODY" ]; then
  printf '%s' "$CODEX_STUB_BODY" > "$out"
fi
exit 0
"""


@pytest.fixture()
def stub(tmp_path):
    """Install the codex stub on PATH; return an env dict + counter accessor."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    script = bin_dir / "codex"
    script.write_text(STUB_SCRIPT)
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    count_file = tmp_path / "stub-count"
    args_file = tmp_path / "stub-args"
    env = dict(os.environ)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["CODEX_STUB_COUNT_FILE"] = str(count_file)
    env["CODEX_STUB_ARGS_FILE"] = str(args_file)
    env["CODEX_STUB_BODY"] = VALID_BODY
    # Deterministic GLM absence: strip credentials so a backend switch to glm
    # refuses on prereq in CLI tests (the module-level tests inject a fake).
    for k in ("ZHIPUAI_API_KEY", "ZHIPU_API_KEY", "GLM_API_KEY",
              "RAWGENTIC_ADV_REVIEW_MODEL"):
        env.pop(k, None)

    class Stub:
        def __init__(self):
            self.env = env
            self.count_file = count_file
            self.args_file = args_file

        @property
        def calls(self):
            return int(count_file.read_text()) if count_file.exists() else 0

        @property
        def argv(self):
            return args_file.read_text().splitlines() if args_file.exists() else []

    return Stub()


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "artifact.md").write_text("# Design\n\nA small design document.\n")
    return root


def _cli(args, env, cwd=None, timeout=60):
    return subprocess.run(
        [sys.executable, CLI, *args],
        capture_output=True, text=True, env=env, cwd=cwd, timeout=timeout,
    )


def _artifact_args(project, out="result.json", reviewer="gpt-5.5-codex",
                   author="claude-fable-5", extra=()):
    return [
        "review-artifact",
        "--artifact", str(project / "artifact.md"),
        "--type", "design",
        "--author-model", author,
        "--reviewer", reviewer,
        "--out", str(project / out),
        "--project-root", str(project),
        *extra,
    ]


def _result(project, out="result.json"):
    return json.loads((project / out).read_text())


def _write_token(path, **overrides):
    token = {
        "version": 1, "source": "review",
        "minted_at": "2026-08-03T20:00:00Z", "nonce": "a" * 32,
    }
    token.update(overrides)
    Path(path).write_text(json.dumps(token))
    return token


# ===========================================================================
# Pure units
# ===========================================================================

class TestClassifyBackendError:
    def test_org_quota_terminal_phrases(self):
        for text in (
            "You exceeded your current quota, please check your plan and billing details",
            "insufficient_quota: org spend limit reached",
            "Monthly spend limit reached for organization",
        ):
            assert rr.classify_backend_error(text) == "org_quota"

    def test_account_quota_phrases(self):
        for text in (
            "You've hit your usage limit. Try again at 3pm.",
            "Rate limit reached for gpt-5.5 on tokens per min",
            "HTTP 429 Too Many Requests",
        ):
            assert rr.classify_backend_error(text) == "account_quota"

    def test_transport_phrases(self):
        for text in (
            "connection reset by peer",
            "network error: could not resolve host",
            "upstream returned 502 Bad Gateway",
            "request timed out",
            "stream disconnected before completion",
        ):
            assert rr.classify_backend_error(text) == "transport"

    def test_unknown_is_unclassified(self):
        assert rr.classify_backend_error("segfault in provider") == "unclassified"
        assert rr.classify_backend_error("") == "unclassified"

    def test_live_verified_codex_limit_shapes(self):
        # Marker shapes verified against live sources 2026-08-03 (exa): the
        # Codex weekly/session cap sentences carry neither "usage limit" nor
        # "rate limit" and must still classify as account-level quota.
        for text in (
            "You've reached your weekly limit",
            "You've hit your session limit · resets 1:10pm (Europe/Madrid)",
            "quota exceeded",
            "UsageLimitExceeded: turn aborted",
        ):
            assert rr.classify_backend_error(text) == "account_quota", text

    def test_org_beats_account_when_both_present(self):
        text = "429: You exceeded your current quota (billing)"
        assert rr.classify_backend_error(text) == "org_quota"


class TestReviewerIdentity:
    def test_equal_models_refuse_case_and_space_insensitive(self):
        assert rr.check_reviewer_identity("GPT-5.5 ", "gpt-5.5") is not None

    def test_distinct_models_pass(self):
        assert rr.check_reviewer_identity("claude-fable-5", "gpt-5.5") is None

    def test_empty_author_refuses(self):
        assert rr.check_reviewer_identity("", "gpt-5.5") is not None
        assert rr.check_reviewer_identity("   ", "gpt-5.5") is not None


class TestResolveReviewer:
    def test_explicit_flag_wins(self, monkeypatch):
        monkeypatch.setenv("RAWGENTIC_ADV_REVIEW_MODEL", "env-model")
        model, err = rr.resolve_reviewer("gpt", "flag-model")
        assert (model, err) == ("flag-model", "")

    def test_gpt_env_fallback(self, monkeypatch):
        monkeypatch.setenv("RAWGENTIC_ADV_REVIEW_MODEL", "env-model")
        model, err = rr.resolve_reviewer("gpt", None)
        assert (model, err) == ("env-model", "")

    def test_gpt_unresolvable_refuses(self, monkeypatch):
        monkeypatch.delenv("RAWGENTIC_ADV_REVIEW_MODEL", raising=False)
        model, err = rr.resolve_reviewer("gpt", None)
        assert model is None and err

    def test_glm_has_verified_default(self, monkeypatch):
        monkeypatch.delenv("RAWGENTIC_ADV_REVIEW_GLM_MODEL", raising=False)
        model, err = rr.resolve_reviewer("glm", None)
        assert model == "glm-5.2" and err == ""

    def test_whitespace_flag_refuses(self):
        model, err = rr.resolve_reviewer("gpt", "   ")
        assert model is None and err


class TestNoiseStrip:
    DIFF = (
        "diff --git a/hooks/x.py b/hooks/x.py\n"
        "--- a/hooks/x.py\n+++ b/hooks/x.py\n@@ -1 +1 @@\n-old\n+new\n"
        "diff --git a/docs/assets/wf2-light.png b/docs/assets/wf2-light.png\n"
        "Binary files a/docs/assets/wf2-light.png and b/docs/assets/wf2-light.png differ\n"
        "diff --git a/vendored/uv.lock b/vendored/uv.lock\n"
        "--- a/vendored/uv.lock\n+++ b/vendored/uv.lock\n@@ -1 +1 @@\n-a\n+b\n"
    )

    def test_strips_only_the_fixed_list(self):
        stripped, paths = rr.strip_noise_from_diff(self.DIFF)
        assert "hooks/x.py" in stripped
        assert "docs/assets/wf2-light.png" not in stripped
        assert "uv.lock" not in stripped
        assert set(paths) == {"docs/assets/wf2-light.png", "vendored/uv.lock"}

    def test_markdown_is_never_stripped(self):
        # No generic docs-only skip: SKILL.md IS executable behavior here.
        diff = (
            "diff --git a/skills/switch/SKILL.md b/skills/switch/SKILL.md\n"
            "--- a/skills/switch/SKILL.md\n+++ b/skills/switch/SKILL.md\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )
        stripped, paths = rr.strip_noise_from_diff(diff)
        assert "SKILL.md" in stripped
        assert paths == ()

    def test_noise_list_is_fixed_and_visible(self):
        assert isinstance(rr.NOISE_STRIP_PATHS, tuple)
        assert "docs/assets/" in rr.NOISE_STRIP_PATHS


class TestReopenToken:
    def test_missing_flag_means_diagnostic(self):
        token, err = rr.load_reopen_token(None)
        assert token is None and err == ""

    def test_valid_token_loads(self, tmp_path):
        path = tmp_path / "t.json"
        _write_token(path)
        token, err = rr.load_reopen_token(str(path))
        assert err == "" and token["source"] == "review"

    def test_spent_token_errors(self, tmp_path):
        path = tmp_path / "t.json"
        _write_token(path, consumed_at=SPENT_AT)
        token, err = rr.load_reopen_token(str(path))
        assert token is None and "spent" in err.lower()

    def test_malformed_token_errors(self, tmp_path):
        path = tmp_path / "t.json"
        path.write_text("{not json")
        token, err = rr.load_reopen_token(str(path))
        assert token is None and err

    def test_missing_file_errors(self, tmp_path):
        token, err = rr.load_reopen_token(str(tmp_path / "absent.json"))
        assert token is None and err

    def test_consume_stamps_consumed_at_atomically(self, tmp_path):
        path = tmp_path / "t.json"
        _write_token(path)
        rr.consume_reopen_token(str(path))
        data = json.loads(path.read_text())
        assert data["consumed_at"].endswith("Z")
        assert [p for p in os.listdir(tmp_path) if p.endswith(".tmp")] == []


# ===========================================================================
# CLI black-box: review-artifact (codex stubbed)
# ===========================================================================

class TestReviewArtifactCli:
    def test_success_tokenless_is_diagnostic(self, stub, project):
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 0, result.stderr
        r = _result(project)
        assert r["status"] == "success"
        assert r["diagnostic"] is True
        assert r["reviewer_model"] == "gpt-5.5-codex"
        assert r["backend"] == "gpt"
        assert len(r["findings"]) == 1
        assert r["summary"] == "one real defect"
        artifact_bytes = (project / "artifact.md").read_bytes()
        assert r["input_sha256"] == hashlib.sha256(artifact_bytes).hexdigest()
        for key in ("composed_at", "compose_seconds", "invoke_seconds",
                    "total_seconds"):
            assert key in r["timing"]

    def test_codex_argv_pins_model_and_sandbox(self, stub, project):
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 0
        argv = stub.argv
        assert "-m" in argv and argv[argv.index("-m") + 1] == "gpt-5.5-codex"
        assert "--sandbox" in argv
        assert argv[argv.index("--sandbox") + 1] == "read-only"
        assert "--output-schema" in argv
        assert "-o" in argv
        assert "--ephemeral" in argv

    def test_start_and_end_lines_on_stderr(self, stub, project):
        result = _cli(_artifact_args(project), stub.env)
        assert "review_runner: START" in result.stderr
        assert "review_runner: END" in result.stderr

    def test_valid_token_actionable_and_consumed(self, stub, project):
        token_path = project / "token.json"
        _write_token(token_path)
        result = _cli(_artifact_args(
            project, extra=("--reopen-token", str(token_path))), stub.env)
        assert result.returncode == 0, result.stderr
        r = _result(project)
        assert r["diagnostic"] is False
        assert r["reopen"]["nonce"] == "a" * 32
        assert json.loads(token_path.read_text())["consumed_at"]

    def test_spent_token_refuses_without_egress(self, stub, project):
        token_path = project / "token.json"
        _write_token(token_path, consumed_at=SPENT_AT)
        result = _cli(_artifact_args(
            project, extra=("--reopen-token", str(token_path))), stub.env)
        assert result.returncode == 2
        assert stub.calls == 0
        r = _result(project)
        assert r["status"] == "refused"

    def test_author_equals_reviewer_refuses_without_egress(self, stub, project):
        result = _cli(_artifact_args(project, reviewer="same-model",
                                     author="same-model"), stub.env)
        assert result.returncode == 2
        assert stub.calls == 0
        r = _result(project)
        assert r["status"] == "refused"
        assert r["error_class"] == "identity"

    def test_unresolvable_reviewer_refuses(self, stub, project):
        args = [
            "review-artifact", "--artifact", str(project / "artifact.md"),
            "--type", "design", "--author-model", "claude-fable-5",
            "--out", str(project / "result.json"),
            "--project-root", str(project),
        ]
        result = _cli(args, stub.env)
        assert result.returncode == 2
        assert stub.calls == 0

    def test_oversize_refuses_never_truncates(self, stub, project):
        (project / "artifact.md").write_text("x" * 5000)
        result = _cli(_artifact_args(project, extra=("--max-bytes", "1000")),
                      stub.env)
        assert result.returncode == 2
        assert stub.calls == 0
        r = _result(project)
        assert r["status"] == "refused"
        assert r["error_class"] == "oversize"

    def test_failure_never_records_empty_reviewer_model(self, stub, project):
        stub.env["CODEX_STUB_RC"] = "1"
        stub.env["CODEX_STUB_STDERR"] = "segfault"
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 3
        r = _result(project)
        assert r["reviewer_model"] == "gpt-5.5-codex"  # pinned, echoed, non-empty


class TestErrorClassesCli:
    def test_empty_output_terminal_single_attempt(self, stub, project):
        stub.env["CODEX_STUB_BODY"] = ""
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 4
        assert stub.calls == 1
        r = _result(project)
        assert r["status"] == "failure"
        assert r["error_class"] == "empty_output"

    def test_invalid_output_gets_one_retry_then_terminal(self, stub, project):
        stub.env["CODEX_STUB_BODY"] = '{"summary": "trunca'
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 4
        assert stub.calls == 2
        r = _result(project)
        assert r["error_class"] == "invalid_output"

    def test_transport_error_gets_one_retry_then_terminal(self, stub, project):
        stub.env["CODEX_STUB_RC"] = "1"
        stub.env["CODEX_STUB_STDERR"] = "connection reset by peer"
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 3
        assert stub.calls == 2
        r = _result(project)
        assert r["error_class"] == "transport"

    def test_transport_blip_then_success(self, stub, project):
        stub.env["CODEX_STUB_FAIL_FIRST"] = "1"
        stub.env["CODEX_STUB_FAIL_FIRST_STDERR"] = "network error: connection reset"
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 0, result.stderr
        assert stub.calls == 2
        assert _result(project)["status"] == "success"

    def test_org_quota_terminal_no_retry(self, stub, project):
        stub.env["CODEX_STUB_RC"] = "1"
        stub.env["CODEX_STUB_STDERR"] = (
            "You exceeded your current quota, please check your plan and billing")
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 3
        assert stub.calls == 1
        r = _result(project)
        assert r["error_class"] == "org_quota"

    def test_account_quota_terminal_when_switch_unavailable(self, stub, project):
        # GLM creds stripped in the fixture -> the permitted one-time backend
        # switch is unavailable; the run must end terminal, not blind-retry gpt.
        stub.env["CODEX_STUB_RC"] = "1"
        stub.env["CODEX_STUB_STDERR"] = "You've hit your usage limit. Try again later."
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 3
        assert stub.calls == 1
        r = _result(project)
        assert r["error_class"] == "account_quota"
        assert r["backend_switched"] is False

    def test_unclassified_terminal_no_retry(self, stub, project):
        stub.env["CODEX_STUB_RC"] = "1"
        stub.env["CODEX_STUB_STDERR"] = "panicked at 'index out of bounds'"
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 3
        assert stub.calls == 1
        assert _result(project)["error_class"] == "unclassified"

    def test_timeout_is_transport_class(self, stub, project):
        stub.env["CODEX_STUB_SLEEP"] = "5"
        result = _cli(_artifact_args(project, extra=("--timeout", "1")),
                      stub.env, timeout=120)
        assert result.returncode == 3
        assert _result(project)["error_class"] == "transport"


class TestNumericConfidence:
    """#902: native numeric passes through; word forms get one bounded retry
    then map through ADV_CONFIDENCE_TO_FLOAT with a provenance flag; garbage
    refuses (whole-review invalid_output — never a silent pass)."""

    def test_native_confidence_single_call_not_mapped(self, stub, project):
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 0, result.stderr
        assert stub.calls == 1  # native numbers never trigger the word retry
        r = _result(project)
        assert r["confidence_mapped"] is False
        f = r["findings"][0]
        assert f["confidence"] == 0.85
        assert f["confidence_source"] == "native"

    def test_word_confidence_one_retry_then_mapped(self, stub, project):
        stub.env["CODEX_STUB_BODY"] = WORD_BODY
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 0, result.stderr
        assert stub.calls == 2  # one bounded word-confidence retry, then accept
        r = _result(project)
        assert r["status"] == "success"
        assert r["confidence_mapped"] is True
        f = r["findings"][0]
        assert f["confidence"] == 0.9  # ADV_CONFIDENCE_TO_FLOAT — the one map
        assert f["confidence_source"] == "mapped"

    def test_word_then_native_retry_recovers_unmapped(self, stub, project):
        stub.env["CODEX_STUB_BODY_FIRST"] = WORD_BODY
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 0, result.stderr
        assert stub.calls == 2
        r = _result(project)
        assert r["confidence_mapped"] is False
        assert r["findings"][0]["confidence_source"] == "native"

    # --- 8a F1 (#902): the confidence re-roll must never lose the held result.
    # The first valid mapped parse is retained; the retry replaces it ONLY when
    # it is itself valid, fully native, and non-empty. Any other retry outcome
    # (empty, invalid, transport-failed) falls back to the held mapped result —
    # a review already validly in hand never turns into a failure or an
    # empty pass because its polish re-roll went sideways.

    def test_word_retry_returning_empty_findings_keeps_held_result(self, stub, project):
        stub.env["CODEX_STUB_BODY_FIRST"] = WORD_BODY
        stub.env["CODEX_STUB_BODY"] = json.dumps({"summary": "nothing", "findings": []})
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 0, result.stderr
        assert stub.calls == 2
        r = _result(project)
        assert r["status"] == "success"
        assert r["confidence_mapped"] is True
        assert len(r["findings"]) == 1  # the held finding survives the empty re-roll
        assert r["findings"][0]["confidence"] == 0.9
        assert r["findings"][0]["confidence_source"] == "mapped"

    def test_word_retry_returning_garbage_keeps_held_result(self, stub, project):
        stub.env["CODEX_STUB_BODY_FIRST"] = WORD_BODY
        stub.env["CODEX_STUB_BODY"] = '{"summary": "trunca'
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 0, result.stderr
        assert stub.calls == 2  # no truncation retry spent on the re-roll
        r = _result(project)
        assert r["status"] == "success"
        assert r["confidence_mapped"] is True
        assert len(r["findings"]) == 1

    def test_word_retry_transport_failure_keeps_held_result(self, stub, project):
        # Stub counts calls; make the SECOND call fail at transport.
        stub.env["CODEX_STUB_BODY_FIRST"] = WORD_BODY
        stub.env["CODEX_STUB_FAIL_SECOND"] = "1"
        stub.env["CODEX_STUB_FAIL_SECOND_STDERR"] = "connection reset by peer"
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 0, result.stderr
        assert stub.calls == 2  # no transport retry spent on the re-roll
        r = _result(project)
        assert r["status"] == "success"
        assert r["confidence_mapped"] is True
        assert len(r["findings"]) == 1

    def test_native_reroll_merges_instead_of_replacing_held(self, stub, project):
        # Step-11 F1 (#902): a native non-empty re-roll must never silently
        # LOSE held findings — the union is kept (native copy wins only on an
        # exact dedupe-key match).
        stub.env["CODEX_STUB_BODY_FIRST"] = WORD_BODY
        stub.env["CODEX_STUB_BODY"] = json.dumps({"summary": "fresh", "findings": [
            dict(VALID_FINDING, confidence=0.75,
                 description="a different native finding"),
        ]})
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 0, result.stderr
        r = _result(project)
        descs = {f["description"] for f in r["findings"]}
        assert descs == {"fail-open guard", "a different native finding"}
        by_desc = {f["description"]: f for f in r["findings"]}
        assert by_desc["fail-open guard"]["confidence_source"] == "mapped"
        assert by_desc["a different native finding"]["confidence_source"] == "native"
        assert r["confidence_mapped"] is True  # a mapped held finding survived
        # Adversarial A2 (#902): the re-roll's summary alone could omit or
        # contradict retained held findings — the merge disclosure names them.
        assert r["summary"].startswith("fresh")
        assert "[merge note: 1 finding(s) retained" in r["summary"]

    def test_native_reroll_matching_finding_takes_native_copy(self, stub, project):
        # Same finding (identical dedupe key) in both rounds: the native
        # re-roll copy wins the collapse, so nothing is mapped in the result.
        stub.env["CODEX_STUB_BODY_FIRST"] = WORD_BODY
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 0, result.stderr
        r = _result(project)
        assert len(r["findings"]) == 1
        assert r["findings"][0]["confidence"] == 0.85
        assert r["findings"][0]["confidence_source"] == "native"
        assert r["confidence_mapped"] is False

    def test_retry_diagnostic_names_non_native_confidence(self, stub, project):
        # 8a F3: numeric strings route here too — the diagnostic must not
        # claim "word-form".
        stub.env["CODEX_STUB_BODY"] = WORD_BODY
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 0, result.stderr
        assert "non-native confidence" in result.stderr
        assert "word-form confidence" not in result.stderr

    def test_garbage_confidence_refuses_invalid_output(self, stub, project):
        stub.env["CODEX_STUB_BODY"] = GARBAGE_CONF_BODY
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 4
        assert stub.calls == 1  # schema-invalid is terminal, never a blind retry
        r = _result(project)
        assert r["error_class"] == "invalid_output"
        assert "confidence" in r["error_detail"]

    def test_glm_word_confidence_maps_with_flag(self, project):
        calls = []

        def fake_glm(prompt, *, model, effort, timeout):
            calls.append(1)
            return WORD_BODY, ""

        res = rr.run_review(
            verb="review-artifact",
            artifact=str(project / "artifact.md"), artifact_type="design",
            author_model="claude-fable-5", reviewer=None, backend="glm",
            project_root=str(project), out_path=str(project / "r.json"),
            glm_fn=fake_glm,
        )
        assert res["status"] == "success"
        assert len(calls) == 2  # one bounded retry before accepting the map
        assert res["confidence_mapped"] is True
        assert res["findings"][0]["confidence"] == 0.9

    def test_banded_filter_runs_mechanically_on_mapped_result(self, stub, project):
        # The consumer operation AC2 exists for: apply
        # plan_lib.SEVERITY_BANDED_CONFIDENCE to a result's findings with no
        # per-finding human judgement in between (the Step-11 filter).
        import plan_lib
        stub.env["CODEX_STUB_BODY"] = json.dumps({"summary": "s", "findings": [
            dict(VALID_FINDING, confidence="low", description="worded low"),
            dict(VALID_FINDING, confidence=0.95, description="native high"),
        ]})
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 0, result.stderr
        r = _result(project)
        kept = [f["description"] for f in r["findings"]
                if f["confidence"] >= plan_lib.SEVERITY_BANDED_CONFIDENCE[f["severity"]]]
        dropped = [f["description"] for f in r["findings"]
                   if f["confidence"] < plan_lib.SEVERITY_BANDED_CONFIDENCE[f["severity"]]]
        assert kept == ["native high"]      # 0.95 >= High band 0.65
        assert dropped == ["worded low"]    # low -> 0.4 < High band 0.65


# ===========================================================================
# CLI black-box: review-code (real tmp git repo)
# ===========================================================================

def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "f.py").write_text("x = 1\n")
    _git(repo, "add", "f.py")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "feature")
    (repo / "g.py").write_text("y = 2\n")
    (repo / "brief.md").write_text("Review this change for fail-open paths.\n")
    _git(repo, "add", "g.py", "brief.md")
    _git(repo, "commit", "-m", "feature")
    return repo


def _code_args(repo, extra=()):
    return [
        "review-code",
        "--base", "main",
        "--brief", str(repo / "brief.md"),
        "--author-model", "claude-fable-5",
        "--reviewer", "gpt-5.5-codex",
        "--out", str(repo / "result.json"),
        "--project-root", str(repo),
        *extra,
    ]


class TestReviewCodeCli:
    def test_success_binds_shas_and_hashes_the_diff(self, stub, repo):
        result = _cli(_code_args(repo), stub.env, cwd=str(repo))
        assert result.returncode == 0, result.stderr
        r = json.loads((repo / "result.json").read_text())
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        base = subprocess.run(["git", "-C", str(repo), "merge-base", "main", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        assert r["head_sha"] == head
        assert r["base_sha"] == base
        diff = subprocess.run(
            ["git", "-C", str(repo), "diff", base, "HEAD"],
            capture_output=True, text=True).stdout
        assert r["input_sha256"] == hashlib.sha256(diff.encode()).hexdigest()

    def test_empty_diff_refuses(self, stub, repo):
        result = _cli(_code_args(repo, extra=("--base", "HEAD")),
                      stub.env, cwd=str(repo))
        assert result.returncode == 2
        assert stub.calls == 0

    def test_unknown_base_refuses(self, stub, repo):
        result = _cli(_code_args(repo, extra=("--base", "no-such-ref")),
                      stub.env, cwd=str(repo))
        assert result.returncode == 2
        assert stub.calls == 0


# ===========================================================================
# CLI black-box: consult
# ===========================================================================

class TestConsultCli:
    def test_consult_success_is_always_diagnostic(self, stub, project):
        stub.env["CODEX_STUB_BODY"] = VALID_PROPOSAL
        args = [
            "consult", "--artifact", str(project / "artifact.md"),
            "--reviewer", "gpt-5.5-codex",
            "--out", str(project / "result.json"),
            "--project-root", str(project),
        ]
        result = _cli(args, stub.env)
        assert result.returncode == 0, result.stderr
        r = _result(project)
        assert r["status"] == "success"
        assert r["diagnostic"] is True
        assert r["proposal"]["approach"] == "small module"

    def test_consult_author_equals_reviewer_refuses(self, stub, project):
        args = [
            "consult", "--artifact", str(project / "artifact.md"),
            "--author-model", "gpt-5.5-codex",
            "--reviewer", "gpt-5.5-codex",
            "--out", str(project / "result.json"),
            "--project-root", str(project),
        ]
        result = _cli(args, stub.env)
        assert result.returncode == 2
        assert stub.calls == 0


# ===========================================================================
# Module-level: GLM backend + the one-time backend switch (injected fake)
# ===========================================================================

class TestGlmAndBackendSwitch:
    def test_glm_backend_success(self, project, monkeypatch):
        def fake_glm(prompt, *, model, effort, timeout):
            assert model == "glm-5.2"
            return VALID_BODY, ""
        res = rr.run_review(
            verb="review-artifact",
            artifact=str(project / "artifact.md"), artifact_type="design",
            author_model="claude-fable-5", reviewer=None, backend="glm",
            project_root=str(project), out_path=str(project / "r.json"),
            glm_fn=fake_glm,
        )
        assert res["status"] == "success"
        assert res["backend"] == "glm"
        assert res["reviewer_model"] == "glm-5.2"

    def test_account_quota_switches_backend_once(self, stub, project, monkeypatch):
        monkeypatch.setenv("PATH", stub.env["PATH"])
        monkeypatch.setenv("CODEX_STUB_COUNT_FILE", str(stub.count_file))
        monkeypatch.setenv("CODEX_STUB_RC", "1")
        monkeypatch.setenv("CODEX_STUB_STDERR",
                           "429: rate limit reached for account")
        calls = []

        def fake_glm(prompt, *, model, effort, timeout):
            calls.append(model)
            return VALID_BODY, ""
        res = rr.run_review(
            verb="review-artifact",
            artifact=str(project / "artifact.md"), artifact_type="design",
            author_model="claude-fable-5", reviewer="gpt-5.5-codex",
            backend="gpt", project_root=str(project),
            out_path=str(project / "r.json"), glm_fn=fake_glm,
            glm_available=True,
        )
        assert res["status"] == "success"
        assert res["backend_switched"] is True
        assert res["backend"] == "glm"
        assert calls == ["glm-5.2"]
        assert stub.calls == 1  # gpt attempted exactly once, never blind-retried

    def test_switch_refused_when_author_is_the_glm_model(self, stub, project,
                                                         monkeypatch):
        monkeypatch.setenv("PATH", stub.env["PATH"])
        monkeypatch.setenv("CODEX_STUB_COUNT_FILE", str(stub.count_file))
        monkeypatch.setenv("CODEX_STUB_RC", "1")
        monkeypatch.setenv("CODEX_STUB_STDERR", "429 too many requests")

        def fake_glm(prompt, *, model, effort, timeout):  # pragma: no cover
            raise AssertionError("switch must be refused before glm egress")
        res = rr.run_review(
            verb="review-artifact",
            artifact=str(project / "artifact.md"), artifact_type="design",
            author_model="glm-5.2", reviewer="gpt-5.5-codex",
            backend="gpt", project_root=str(project),
            out_path=str(project / "r.json"), glm_fn=fake_glm,
            glm_available=True,
        )
        assert res["status"] == "failure"
        assert res["error_class"] == "account_quota"
        assert res["backend_switched"] is False


# ===========================================================================
# Adversarial review round 1 (2026-08-03, gpt-5.6-sol) — one red test per fix
# ===========================================================================

import adversarial_review_lib as arl  # noqa: E402
from types import SimpleNamespace  # noqa: E402


class TestRenameNoiseStrip:
    """Round-1 H4: renames into a noise path must not hide executable code."""

    def test_rename_into_noise_path_is_not_stripped(self):
        diff = (
            "diff --git a/hooks/security.py b/docs/assets/security.py\n"
            "similarity index 100%\n"
            "rename from hooks/security.py\n"
            "rename to docs/assets/security.py\n"
        )
        stripped, paths = rr.strip_noise_from_diff(diff)
        assert "hooks/security.py" in stripped
        assert paths == ()

    def test_rename_out_of_noise_path_is_not_stripped(self):
        diff = (
            "diff --git a/docs/assets/x.py b/hooks/x.py\n"
            "similarity index 100%\n"
            "rename from docs/assets/x.py\n"
            "rename to hooks/x.py\n"
        )
        stripped, paths = rr.strip_noise_from_diff(diff)
        assert "hooks/x.py" in stripped
        assert paths == ()

    def test_rename_within_noise_paths_is_stripped(self):
        diff = (
            "diff --git a/docs/assets/a.png b/docs/assets/b.png\n"
            "similarity index 100%\n"
            "rename from docs/assets/a.png\n"
            "rename to docs/assets/b.png\n"
        )
        stripped, paths = rr.strip_noise_from_diff(diff)
        assert stripped == ""
        assert "docs/assets/b.png" in paths


class TestStrictOutputValidation:
    """Round-1 M5: schema-invalid / vacuous responses are never a pass."""

    def test_missing_summary_is_invalid_output(self, stub, project):
        stub.env["CODEX_STUB_BODY"] = '{"findings": []}'
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 4
        r = _result(project)
        assert r["error_class"] == "invalid_output"
        assert stub.calls == 1  # schema-invalid is a model problem: terminal

    def test_empty_findings_with_summary_is_a_valid_clean_review(self, stub, project):
        stub.env["CODEX_STUB_BODY"] = '{"summary": "clean", "findings": []}'
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 0, result.stderr
        assert _result(project)["status"] == "success"

    def test_empty_object_consult_is_invalid_output(self, stub, project):
        stub.env["CODEX_STUB_BODY"] = "{}"
        args = [
            "consult", "--artifact", str(project / "artifact.md"),
            "--reviewer", "gpt-5.5-codex",
            "--out", str(project / "result.json"),
            "--project-root", str(project),
        ]
        result = _cli(args, stub.env)
        assert result.returncode == 4
        assert _result(project)["error_class"] == "invalid_output"


class TestBriefIsBoundInput:
    """Round-1 M6: the brief is part of the reviewed input — capped, scanned,
    hashed, and fenced as data."""

    def test_brief_is_secret_scanned(self, stub, repo):
        secret_brief = repo / "brief-secret.md"
        secret_brief.write_text("api_key = check the rotation path\n")
        result = _cli(_code_args(repo, extra=("--brief", str(secret_brief))),
                      stub.env, cwd=str(repo))
        assert result.returncode == 0, result.stderr
        r = json.loads((repo / "result.json").read_text())
        assert "API key" in r["secrets_detected"]

    def test_combined_diff_plus_brief_cap_refuses(self, stub, repo):
        big_brief = repo / "brief-big.md"
        big_brief.write_text("x" * 950)
        result = _cli(_code_args(repo, extra=("--brief", str(big_brief),
                                              "--max-bytes", "1000")),
                      stub.env, cwd=str(repo))
        assert result.returncode == 2
        assert stub.calls == 0
        r = json.loads((repo / "result.json").read_text())
        assert r["error_class"] == "oversize"

    def test_result_carries_brief_sha256(self, stub, repo):
        result = _cli(_code_args(repo), stub.env, cwd=str(repo))
        assert result.returncode == 0
        r = json.loads((repo / "result.json").read_text())
        expected = hashlib.sha256((repo / "brief.md").read_bytes()).hexdigest()
        assert r["brief_sha256"] == expected

    def test_brief_rides_inside_a_data_fence(self, stub, repo):
        fenced_brief = repo / "brief-fence.md"  # uncommitted: absent from diff
        fenced_brief.write_text("Concentrate on the token lifecycle.\n")
        stub.env["CODEX_STUB_STDIN_FILE"] = str(repo / "stdin.txt")
        result = _cli(_code_args(repo, extra=("--brief", str(fenced_brief))),
                      stub.env, cwd=str(repo))
        assert result.returncode == 0, result.stderr
        prompt = (repo / "stdin.txt").read_text()
        brief_text = fenced_brief.read_text()
        assert prompt.count(brief_text) == 1
        before, after = prompt.split(brief_text)
        nonce_line = before.rstrip("\n").splitlines()[-1].strip()
        assert nonce_line and nonce_line in after


class TestOutPathDiscipline:
    """Round-1 M7: --out is validated BEFORE egress; a receipt-write failure
    is never exit 0."""

    def test_out_outside_project_root_refuses_before_egress(self, stub, project,
                                                            tmp_path):
        out = tmp_path / "escaped-result.json"
        args = [
            "review-artifact", "--artifact", str(project / "artifact.md"),
            "--type", "design", "--author-model", "claude-fable-5",
            "--reviewer", "gpt-5.5-codex",
            "--out", str(out), "--project-root", str(project),
        ]
        result = _cli(args, stub.env)
        assert result.returncode == 2
        assert stub.calls == 0
        assert not out.exists()

    def test_receipt_write_failure_is_nonzero(self, stub, project):
        outdir = project / "ro"
        outdir.mkdir()
        args = _artifact_args(project, out="ro/result.json")
        os.chmod(outdir, 0o500)
        try:
            result = _cli(args, stub.env)
        finally:
            os.chmod(outdir, 0o700)
        assert result.returncode == 3
        assert not (outdir / "result.json").exists()

    def test_stale_out_is_invalidated_before_egress(self, stub, project):
        (project / "result.json").write_text('{"status": "success", "stale": true}')
        stub.env["CODEX_STUB_RC"] = "1"
        stub.env["CODEX_STUB_STDERR"] = "segfault"
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 3
        r = _result(project)
        assert r["status"] == "failure"
        assert "stale" not in r


class TestStdoutClassification:
    """Round-1 L8: failure text on stdout classifies like stderr."""

    def test_stdout_only_quota_text_is_classified(self, stub, project):
        stub.env["CODEX_STUB_RC"] = "1"
        stub.env["CODEX_STUB_STDOUT"] = "429 too many requests"
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 3
        r = _result(project)
        assert r["error_class"] == "account_quota"


class TestTokenSingleUse:
    """Round-1 H2: an actionable result requires a successful EXCLUSIVE stamp;
    a token that cannot be stamped (or was spent concurrently) downgrades the
    result to diagnostic — never actionable, never a hard failure."""

    def test_token_spent_concurrently_downgrades_to_diagnostic(self, project):
        token_path = project / "token.json"
        _write_token(token_path)

        def fake_glm(prompt, *, model, effort, timeout):
            rr.consume_reopen_token(str(token_path))  # the concurrent runner
            return VALID_BODY, ""

        res = rr.run_review(
            verb="review-artifact",
            artifact=str(project / "artifact.md"), artifact_type="design",
            author_model="claude-fable-5", reviewer=None, backend="glm",
            reopen_token=str(token_path), project_root=str(project),
            out_path=str(project / "r.json"), glm_fn=fake_glm,
        )
        assert res["status"] == "success"
        assert res["diagnostic"] is True

    def test_token_stamp_failure_downgrades_to_diagnostic(self, stub, project):
        tokdir = project / "tok"
        tokdir.mkdir()
        token_path = tokdir / "t.json"
        _write_token(token_path)
        os.chmod(tokdir, 0o500)
        try:
            result = _cli(_artifact_args(
                project, extra=("--reopen-token", str(token_path))), stub.env)
        finally:
            os.chmod(tokdir, 0o700)
        assert result.returncode == 0, result.stderr
        r = _result(project)
        assert r["status"] == "success"
        assert r["diagnostic"] is True


class TestGlmSingleAttempt:
    """Round-1 H3: the default GLM transport makes ONE provider attempt per
    runner attempt — the runner alone owns retry/switch policy."""

    def _wire(self, monkeypatch, create_fn):
        calls = []

        def fake_create(**kwargs):
            calls.append(kwargs.get("model"))
            return create_fn(**kwargs)

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
        monkeypatch.setattr(arl, "_load_glm_client", lambda timeout: client)
        monkeypatch.setattr(arl, "glm_sdk_available", lambda: True)
        monkeypatch.setattr(arl, "glm_api_key", lambda: "test-key")
        return calls

    def test_org_quota_is_single_provider_attempt(self, project, monkeypatch):
        def raise_quota(**kwargs):
            raise RuntimeError("You exceeded your current quota (billing)")
        calls = self._wire(monkeypatch, raise_quota)
        res = rr.run_review(
            verb="review-artifact",
            artifact=str(project / "artifact.md"), artifact_type="design",
            author_model="claude-fable-5", reviewer=None, backend="glm",
            project_root=str(project), out_path=str(project / "r.json"),
        )
        assert res["status"] == "failure"
        assert res["error_class"] == "org_quota"
        assert len(calls) == 1

    def test_transport_gets_exactly_one_runner_retry(self, project, monkeypatch):
        def raise_conn(**kwargs):
            raise ConnectionError("connection reset by peer")
        calls = self._wire(monkeypatch, raise_conn)
        res = rr.run_review(
            verb="review-artifact",
            artifact=str(project / "artifact.md"), artifact_type="design",
            author_model="claude-fable-5", reviewer=None, backend="glm",
            project_root=str(project), out_path=str(project / "r.json"),
        )
        assert res["status"] == "failure"
        assert res["error_class"] == "transport"
        assert len(calls) == 2


# ===========================================================================
# #761 T4 — the `--task-class` / `--issue` flag pair on all three verbs.
#
# C3/C7 (pass-6 High, terminal ADOPTED disposition d-761-6-7-b750): omitting
# `--issue` conflates a legitimately issue-less review with an accidental
# omission on an issue-scoped one. The latter would silently inject the project
# default instead of the snapshotted class, so the prompt could display the
# WRONG class with no failure and no diagnostic. Hence: `--issue` without
# `--task-class` REFUSES rather than defaulting.
#
# Design AC 10 (docs/planning/2026-08-04-761-proportionality-contract-design.md
# :319): an out-of-enum value is refused (exit 2) BEFORE egress, and absence of
# the flag leaves behaviour unchanged.
# ===========================================================================

class TestTaskClassFlags:
    def test_out_of_enum_task_class_refuses_before_egress(self, stub, project):
        result = _cli(_artifact_args(project, extra=("--task-class", "bogus")),
                      stub.env)
        assert result.returncode == 2
        assert stub.calls == 0, "refusal must precede egress"
        r = _result(project)
        assert r["error_class"] == "invalid_input"
        assert "bogus" in r["error_detail"]

    def test_issue_without_task_class_refuses(self, stub, project):
        """C3/C7: an issue-scoped review may not fall back to the default."""
        result = _cli(_artifact_args(project, extra=("--issue", "761")),
                      stub.env)
        assert result.returncode == 2
        assert stub.calls == 0
        r = _result(project)
        assert r["error_class"] == "invalid_input"
        assert "--task-class" in r["error_detail"]

    def test_issue_with_task_class_succeeds_and_records_both(self, stub, project):
        result = _cli(
            _artifact_args(project,
                           extra=("--task-class", "disposable", "--issue", "761")),
            stub.env)
        assert result.returncode == 0, result.stderr
        r = _result(project)
        assert r["status"] == "success"
        assert r["task_class"] == "disposable"
        assert r["issue"] == 761

    def test_task_class_renders_in_the_artifact_prompt(self, stub, project):
        stub.env["CODEX_STUB_STDIN_FILE"] = str(project / "stdin.txt")
        result = _cli(_artifact_args(project,
                                     extra=("--task-class", "disposable")),
                      stub.env)
        assert result.returncode == 0, result.stderr
        prompt = (project / "stdin.txt").read_text()
        assert "TASK CLASS: disposable" in prompt
        assert "TASK CLASS: production" not in prompt

    def test_absent_flag_leaves_behaviour_unchanged(self, stub, project):
        """Always-render, defaulting to the strictest class — never NO line."""
        stub.env["CODEX_STUB_STDIN_FILE"] = str(project / "stdin.txt")
        result = _cli(_artifact_args(project), stub.env)
        assert result.returncode == 0, result.stderr
        r = _result(project)
        assert r["task_class"] == "production"
        assert r["issue"] is None
        assert "TASK CLASS: production" in (project / "stdin.txt").read_text()

    def test_consult_threads_the_task_class(self, stub, project):
        stub.env["CODEX_STUB_BODY"] = VALID_PROPOSAL
        stub.env["CODEX_STUB_STDIN_FILE"] = str(project / "stdin.txt")
        args = [
            "consult", "--artifact", str(project / "artifact.md"),
            "--reviewer", "gpt-5.5-codex",
            "--out", str(project / "result.json"),
            "--project-root", str(project),
            "--task-class", "internal", "--issue", "761",
        ]
        result = _cli(args, stub.env)
        assert result.returncode == 0, result.stderr
        assert "TASK CLASS: internal" in (project / "stdin.txt").read_text()
        assert _result(project)["task_class"] == "internal"

    def test_consult_issue_without_task_class_refuses(self, stub, project):
        args = [
            "consult", "--artifact", str(project / "artifact.md"),
            "--reviewer", "gpt-5.5-codex",
            "--out", str(project / "result.json"),
            "--project-root", str(project),
            "--issue", "761",
        ]
        result = _cli(args, stub.env)
        assert result.returncode == 2
        assert stub.calls == 0
        assert _result(project)["error_class"] == "invalid_input"

    def test_review_code_threads_the_task_class(self, stub, repo):
        stub.env["CODEX_STUB_STDIN_FILE"] = str(repo / "stdin.txt")
        result = _cli(_code_args(repo, extra=("--task-class", "internal",
                                              "--issue", "761")),
                      stub.env, cwd=str(repo))
        assert result.returncode == 0, result.stderr
        assert "TASK CLASS: internal" in (repo / "stdin.txt").read_text()
        assert json.loads((repo / "result.json").read_text())["task_class"] \
            == "internal"

    def test_review_code_issue_without_task_class_refuses(self, stub, repo):
        result = _cli(_code_args(repo, extra=("--issue", "761")),
                      stub.env, cwd=str(repo))
        assert result.returncode == 2
        assert stub.calls == 0
        r = json.loads((repo / "result.json").read_text())
        assert r["error_class"] == "invalid_input"

    def test_refusal_is_recorded_in_the_receipt_not_just_stderr(self, stub,
                                                               project):
        """The orchestrator gates on the receipt, so the refusal must be IN it."""
        result = _cli(_artifact_args(project, extra=("--task-class", "Production")),
                      stub.env)
        assert result.returncode == 2, "the enum is case-SENSITIVE"
        assert (project / "result.json").exists()
        assert _result(project)["status"] == "refused"
