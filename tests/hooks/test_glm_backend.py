"""Tests for the selectable GLM backend (issue #403).

Covers, task by task per the #403 plan:
- Task 1: backend config vocabulary (`gpt`|`glm`|`both`) on the adversarialReview /
  peerConsult blocks + the `backend` CLI subcommand exit contract.

Design invariants under test (docs/planning/2026-07-14-403-glm-backend-design.md):
- Absent backend -> "gpt" silently (backward compatible).
- Present-but-invalid backend -> the "invalid" sentinel with the rejected raw value
  preserved on `backend_error_value` + a stderr warning; every CONFIG-RESOLVING entry
  point refuses with exit 2 BEFORE any provider call (a typo'd "glm5" must never
  silently reroute the artifact to OpenAI).
- The `backend` subcommand exits 0 printing the backend for valid/absent/disabled
  config, and exits 2 naming the rejected value for present-but-invalid — it never
  launders an invalid value into "gpt".
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import adversarial_review_lib as arl  # noqa: E402

CLI = str(HOOKS_DIR / "adversarial_review_lib.py")


def _write_ws(tmp_path: Path, projects: list) -> Path:
    ws = tmp_path / ".rawgentic_workspace.json"
    ws.write_text(json.dumps({"version": 1, "projects": projects}, indent=2))
    return ws


def _proj(name="p", **block):
    """A workspace project entry with an adversarialReview block."""
    entry = {"name": name, "path": f"./projects/{name}"}
    if block:
        entry["adversarialReview"] = block.pop("adversarialReview", block)
    return entry


# ---------------------------------------------------------------------------
# Task 1 — config coercion: the `backend` field
# ---------------------------------------------------------------------------

class TestBackendCoercion:
    def test_backends_vocabulary_constant(self):
        assert arl.BACKENDS == ("gpt", "glm", "both")

    def test_absent_backend_defaults_gpt_silently(self, tmp_path, capsys):
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["implement-feature"]})])
        cfg = arl.load_adversarial_review_config(str(ws), "p")
        assert cfg.backend == "gpt"
        assert cfg.backend_error_value is None
        # silent: no stderr warning for the absent case
        assert "backend" not in capsys.readouterr().err

    @pytest.mark.parametrize("value", ["gpt", "glm", "both"])
    def test_valid_backend_kept(self, tmp_path, value):
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["implement-feature"], "backend": value})])
        cfg = arl.load_adversarial_review_config(str(ws), "p")
        assert cfg.backend == value
        assert cfg.backend_error_value is None

    @pytest.mark.parametrize("bad", ["glm5", "bot", "GPT ", "", 5, {"x": 1}, ["glm"], True])
    def test_invalid_backend_sentinel_preserves_value(self, tmp_path, bad):
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["implement-feature"], "backend": bad})])
        cfg = arl.load_adversarial_review_config(str(ws), "p")
        assert cfg.backend == "invalid"
        # repr (not the live object): keeps the frozen dataclass hashable when
        # the rejected value is a dict/list (8a T1 finding) — and JSON round-trips
        # a config's `true` to Python True, so repr is computed on the parsed value.
        assert cfg.backend_error_value == repr(bad)
        # the frozen dataclass stays hashable even on the invalid path
        hash(cfg)

    def test_invalid_backend_warns_stderr(self, tmp_path):
        """The warning is emitted by the loader (subprocess-visible via the CLI)."""
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["x"], "backend": "glm5"})])
        result = subprocess.run(
            [sys.executable, CLI, "backend", "--workspace", str(ws), "--project", "p"],
            capture_output=True, text=True, timeout=30)
        assert "glm5" in result.stderr

    def test_bool_shorthand_backend_gpt(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj(adversarialReview=True)])
        cfg = arl.load_adversarial_review_config(str(ws), "p")
        assert cfg.backend == "gpt"
        assert cfg.backend_error_value is None

    def test_disabled_block_backend_still_coerced(self, tmp_path):
        """A disabled block with a valid backend keeps it (harmless, informative)."""
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": False, "workflows": [], "backend": "glm"})])
        cfg = arl.load_adversarial_review_config(str(ws), "p")
        assert cfg.enabled is False
        assert cfg.backend == "glm"

    def test_peer_consult_key_backend(self, tmp_path):
        ws = _write_ws(tmp_path, [{
            "name": "p", "path": "./projects/p",
            "peerConsult": {"enabled": True, "workflows": ["implement-feature"],
                            "backend": "both"},
        }])
        cfg = arl.load_adversarial_review_config(str(ws), "p", key="peerConsult")
        assert cfg.backend == "both"

    def test_missing_file_disabled_backend_gpt(self, tmp_path):
        cfg = arl.load_adversarial_review_config(str(tmp_path / "nope.json"), "p")
        assert cfg.enabled is False
        assert cfg.backend == "gpt"


# ---------------------------------------------------------------------------
# Task 1 — the `backend` CLI subcommand exit contract
# ---------------------------------------------------------------------------

def _run_backend_cmd(ws: Path, project: str, key: str | None = None):
    cmd = [sys.executable, CLI, "backend", "--workspace", str(ws), "--project", project]
    if key:
        cmd += ["--key", key]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


class TestBackendSubcommand:
    def test_valid_backend_exit0_prints_it(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["x"], "backend": "glm"})])
        r = _run_backend_cmd(ws, "p")
        assert r.returncode == 0
        assert r.stdout.strip() == "glm"

    def test_absent_backend_exit0_gpt(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["x"]})])
        r = _run_backend_cmd(ws, "p")
        assert r.returncode == 0
        assert r.stdout.strip() == "gpt"

    def test_missing_config_exit0_gpt(self, tmp_path):
        ws = _write_ws(tmp_path, [{"name": "p", "path": "./projects/p"}])
        r = _run_backend_cmd(ws, "p")
        assert r.returncode == 0
        assert r.stdout.strip() == "gpt"

    def test_disabled_block_exit0_prints_backend(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": False, "workflows": [], "backend": "both"})])
        r = _run_backend_cmd(ws, "p")
        assert r.returncode == 0
        assert r.stdout.strip() == "both"

    def test_invalid_backend_exit2_names_value_never_gpt(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["x"], "backend": "glm5"})])
        r = _run_backend_cmd(ws, "p")
        assert r.returncode == 2
        assert "glm5" in r.stderr
        # NEVER launder the invalid value into a printed "gpt"
        assert r.stdout.strip() != "gpt"

    def test_peer_consult_key_selector(self, tmp_path):
        ws = _write_ws(tmp_path, [{
            "name": "p", "path": "./projects/p",
            "peerConsult": {"enabled": True, "workflows": ["x"], "backend": "both"},
        }])
        r = _run_backend_cmd(ws, "p", key="peerConsult")
        assert r.returncode == 0
        assert r.stdout.strip() == "both"


# ---------------------------------------------------------------------------
# Task 2 — GLM prereq helpers, backend-aware prereq_status + egress_warning
# ---------------------------------------------------------------------------

class TestGlmSdkAvailable:
    def test_not_installed(self, monkeypatch):
        monkeypatch.setattr(arl, "_zhipuai_version", lambda: None)
        ok, detail = arl.glm_sdk_status()
        assert ok is False
        assert "not installed" in detail

    def test_below_floor_rejected_with_guidance(self, monkeypatch):
        monkeypatch.setattr(arl, "_zhipuai_version", lambda: "2.0.1")
        ok, detail = arl.glm_sdk_status()
        assert ok is False
        assert "2.0.1" in detail          # names the detected version
        assert "2.1.5" in detail          # names the floor

    @pytest.mark.parametrize("ver", ["2.1.5", "2.2.0", "3.0.0", "2.10.1"])
    def test_at_or_above_floor_ok(self, monkeypatch, ver):
        monkeypatch.setattr(arl, "_zhipuai_version", lambda: ver)
        ok, _ = arl.glm_sdk_status()
        assert ok is True

    def test_unparseable_version_fails_closed(self, monkeypatch):
        monkeypatch.setattr(arl, "_zhipuai_version", lambda: "weird")
        ok, _ = arl.glm_sdk_status()
        assert ok is False


class TestGlmApiKey:
    def test_precedence_zhipuai_over_zhipu_over_glm(self, monkeypatch):
        monkeypatch.setenv("ZHIPUAI_API_KEY", "a")
        monkeypatch.setenv("ZHIPU_API_KEY", "b")
        monkeypatch.setenv("GLM_API_KEY", "c")
        assert arl.glm_api_key() == "a"
        monkeypatch.delenv("ZHIPUAI_API_KEY")
        assert arl.glm_api_key() == "b"
        monkeypatch.delenv("ZHIPU_API_KEY")
        assert arl.glm_api_key() == "c"
        monkeypatch.delenv("GLM_API_KEY")
        assert arl.glm_api_key() is None

    def test_empty_values_skipped(self, monkeypatch):
        monkeypatch.setenv("ZHIPUAI_API_KEY", "  ")
        monkeypatch.setenv("ZHIPU_API_KEY", "real")
        assert arl.glm_api_key() == "real"


class TestGlmBaseUrl:
    DEFAULT = "https://api.z.ai/api/coding/paas/v4"

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("ZHIPUAI_BASE_URL", raising=False)
        monkeypatch.delenv("GLM_JUDGE_BASE_URL", raising=False)
        assert arl.glm_base_url() == self.DEFAULT

    def test_precedence_zhipuai_over_glm_judge(self, monkeypatch):
        monkeypatch.setenv("ZHIPUAI_BASE_URL", "https://a.example/v1")
        monkeypatch.setenv("GLM_JUDGE_BASE_URL", "https://b.example/v1")
        assert arl.glm_base_url() == "https://a.example/v1"
        monkeypatch.delenv("ZHIPUAI_BASE_URL")
        assert arl.glm_base_url() == "https://b.example/v1"

    def test_validation_https_required(self):
        ok, reason = arl.validate_glm_base_url("http://api.z.ai/api/coding/paas/v4")
        assert ok is False
        assert "https" in reason.lower()

    @pytest.mark.parametrize("bad", [
        "https://user:tok@api.z.ai/v4",       # userinfo
        "https://api.z.ai/v4?key=x",          # query
        "https://api.z.ai/v4#frag",           # fragment
    ])
    def test_validation_rejects_credential_bearing_shapes(self, bad):
        ok, _ = arl.validate_glm_base_url(bad)
        assert ok is False

    def test_validation_accepts_default_and_custom_https(self):
        assert arl.validate_glm_base_url(self.DEFAULT)[0] is True
        assert arl.validate_glm_base_url("https://open.bigmodel.cn/api/paas/v4")[0] is True

    def test_redact_endpoint_scheme_host_only(self):
        red = arl.redact_endpoint("https://user:tok@api.z.ai/api/x?key=secret#f")
        assert red == "https://api.z.ai"
        assert "tok" not in red and "secret" not in red


class TestPrereqStatusBackend:
    def _glm_ready(self, monkeypatch):
        monkeypatch.setattr(arl, "_zhipuai_version", lambda: "2.1.5")
        monkeypatch.setenv("ZHIPUAI_API_KEY", "k")
        monkeypatch.delenv("ZHIPUAI_BASE_URL", raising=False)
        monkeypatch.delenv("GLM_JUDGE_BASE_URL", raising=False)

    def _glm_unready(self, monkeypatch):
        monkeypatch.setattr(arl, "_zhipuai_version", lambda: None)
        monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
        monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
        monkeypatch.delenv("GLM_API_KEY", raising=False)

    def test_gpt_default_byte_identical(self, monkeypatch):
        """backend='gpt' (and the no-arg default) must return the EXACT pre-#403 messages."""
        monkeypatch.setattr(arl, "codex_installed", lambda: True)
        monkeypatch.setattr(arl, "codex_authenticated", lambda: True)
        assert arl.prereq_status() == arl.prereq_status(backend="gpt")
        ok, msg = arl.prereq_status(backend="gpt")
        assert ok is True
        assert msg == "Codex CLI installed and authenticated."

    def test_glm_ready(self, monkeypatch):
        self._glm_ready(monkeypatch)
        ok, msg = arl.prereq_status(backend="glm")
        assert ok is True
        assert "GLM" in msg or "zhipuai" in msg

    def test_glm_sdk_missing(self, monkeypatch):
        self._glm_unready(monkeypatch)
        monkeypatch.setenv("ZHIPUAI_API_KEY", "k")
        ok, msg = arl.prereq_status(backend="glm")
        assert ok is False
        assert "zhipuai>=2.1.5" in msg          # install guidance with pinned floor

    def test_glm_key_missing(self, monkeypatch):
        monkeypatch.setattr(arl, "_zhipuai_version", lambda: "2.1.5")
        for v in ("ZHIPUAI_API_KEY", "ZHIPU_API_KEY", "GLM_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        ok, msg = arl.prereq_status(backend="glm")
        assert ok is False
        assert "ZHIPUAI_API_KEY" in msg

    def test_glm_bad_base_url_not_ready(self, monkeypatch):
        self._glm_ready(monkeypatch)
        monkeypatch.setenv("ZHIPUAI_BASE_URL", "http://plaintext.example/v4")
        ok, msg = arl.prereq_status(backend="glm")
        assert ok is False

    def test_both_degrade_and_warn_one_ready(self, monkeypatch):
        """both = ok iff >=1 ready; message names BOTH results, never collapsed."""
        monkeypatch.setattr(arl, "codex_installed", lambda: True)
        monkeypatch.setattr(arl, "codex_authenticated", lambda: True)
        self._glm_unready(monkeypatch)
        ok, msg = arl.prereq_status(backend="both")
        assert ok is True                      # degrade-and-warn
        assert "gpt" in msg.lower() and "glm" in msg.lower()

    def test_both_zero_ready_fails(self, monkeypatch):
        monkeypatch.setattr(arl, "codex_installed", lambda: False)
        self._glm_unready(monkeypatch)
        ok, msg = arl.prereq_status(backend="both")
        assert ok is False
        assert "gpt" in msg.lower() and "glm" in msg.lower()

    def test_both_all_ready(self, monkeypatch):
        monkeypatch.setattr(arl, "codex_installed", lambda: True)
        monkeypatch.setattr(arl, "codex_authenticated", lambda: True)
        self._glm_ready(monkeypatch)
        ok, msg = arl.prereq_status(backend="both")
        assert ok is True

    def test_glm_headless_message(self, monkeypatch):
        monkeypatch.setattr(arl, "_zhipuai_version", lambda: "2.1.5")
        for v in ("ZHIPUAI_API_KEY", "ZHIPU_API_KEY", "GLM_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        ok, msg = arl.prereq_status(headless=True, backend="glm")
        assert ok is False
        assert "ZHIPUAI_API_KEY" in msg


class TestEgressWarningBackend:
    def test_gpt_text_unchanged(self):
        """The no-arg and backend='gpt' notices are the EXACT pre-#403 text."""
        base = arl.egress_warning()
        assert base == arl.egress_warning(backend="gpt")
        assert "OpenAI" in base

    def test_glm_names_zai_and_effective_endpoint(self, monkeypatch):
        monkeypatch.delenv("ZHIPUAI_BASE_URL", raising=False)
        monkeypatch.delenv("GLM_JUDGE_BASE_URL", raising=False)
        w = arl.egress_warning(backend="glm")
        assert "z.ai" in w or "Zhipu" in w
        assert "https://api.z.ai" in w          # effective sanitized endpoint named
        assert "OpenAI" not in w                 # glm-only notice must not blame OpenAI

    def test_glm_overridden_endpoint_named_sanitized(self, monkeypatch):
        monkeypatch.setenv("ZHIPUAI_BASE_URL", "https://custom.example/api/v4")
        w = arl.egress_warning(backend="glm")
        assert "https://custom.example" in w
        assert "/api/v4" not in w                # scheme+host only

    def test_both_names_both_destinations(self, monkeypatch):
        monkeypatch.delenv("ZHIPUAI_BASE_URL", raising=False)
        monkeypatch.delenv("GLM_JUDGE_BASE_URL", raising=False)
        w = arl.egress_warning(backend="both")
        assert "OpenAI" in w
        assert "z.ai" in w or "Zhipu" in w

    def test_glm_secrets_appended(self):
        w = arl.egress_warning(["API key"], backend="glm")
        assert "API key" in w
        assert "RAWGENTIC_ADV_REVIEW_BLOCK_SECRETS" in w
