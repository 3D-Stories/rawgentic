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


# ---------------------------------------------------------------------------
# Task 3 — GLM invocation core (fake clients; no network, no zhipuai SDK)
# ---------------------------------------------------------------------------

import time
from types import SimpleNamespace


def _chunk(content):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


class _FakeCompletions:
    """Scripted chat.completions: each create() pops the next behavior.

    A behavior is either an Exception (raised), a list of chunk-contents
    (returned as a streaming iterator), or a callable returning an iterator.
    """

    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.behaviors:
            raise AssertionError("fake client exhausted")
        b = self.behaviors.pop(0)
        if isinstance(b, Exception):
            raise b
        if callable(b):
            return b()
        return iter([_chunk(c) for c in b])


class _FakeClient:
    def __init__(self, behaviors):
        self.completions = _FakeCompletions(behaviors)
        self.chat = SimpleNamespace(completions=self.completions)

    @property
    def calls(self):
        return self.completions.calls


def _valid_finding(desc="a real problem"):
    return {
        "evidence": "quoted artifact text", "severity": "High",
        "category": "security", "confidence": "high",
        "description": desc, "recommendation": "fix it",
        "ambiguity_flag": None, "ambiguity_reason": None, "location": "L1",
        "loopback_class": None,
    }


def _findings_json(n=1):
    return json.dumps({"summary": "risk read",
                       "findings": [_valid_finding(f"d{i}") for i in range(n)]})


def _proposal_json():
    return json.dumps({"approach": "do X", "key_decisions": ["a"],
                       "risks": ["r"], "sketch": "s"})


@pytest.fixture
def artifact(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text("# design\nsome text\n")
    return str(p), str(tmp_path)


class TestGlmReview:
    def test_success_streamed(self, artifact):
        path, root = artifact
        payload = _findings_json(2)
        client = _FakeClient([[payload[:10], payload[10:]]])  # split across chunks
        res = arl.run_glm_review(path, "design", root, client=client)
        assert res.status == "success"
        assert len(res.findings) == 2
        assert res.backend == "glm"
        assert res.model == "glm-5.2"

    def test_create_kwargs_verified_shape(self, artifact):
        path, root = artifact
        client = _FakeClient([[_findings_json()]])
        arl.run_glm_review(path, "design", root, client=client)
        kw = client.calls[0]
        assert kw["model"] == "glm-5.2"
        assert kw["response_format"] == {"type": "json_object"}
        assert kw["thinking"] == {"type": "enabled"}
        assert kw["extra_body"] == {"reasoning_effort": arl.REASONING_EFFORT}
        assert kw["stream"] is True
        assert kw["max_tokens"] == 16384
        assert kw["temperature"] == 0.2

    def test_prompt_nonce_fenced_plus_schema(self, artifact):
        path, root = artifact
        client = _FakeClient([[_findings_json()]])
        arl.run_glm_review(path, "design", root, client=client)
        prompt = client.calls[0]["messages"][0]["content"]
        assert "BEGIN UNTRUSTED ARTIFACT [k=" in prompt          # nonce fence intact
        assert "TOOLS — STRICTLY FORBIDDEN" in prompt
        assert "JSON Schema" in prompt                            # schema-in-prompt suffix
        assert '"findings"' in prompt                             # schema body inlined

    def test_malformed_json_parse_error(self, artifact):
        path, root = artifact
        client = _FakeClient([["this is not json"]])
        res = arl.run_glm_review(path, "design", root, client=client)
        assert res.status == "parse_error"
        assert res.findings == ()

    def test_empty_stream_parse_error(self, artifact):
        path, root = artifact
        client = _FakeClient([[]])
        res = arl.run_glm_review(path, "design", root, client=client)
        assert res.status == "parse_error"

    def test_invalid_findings_fail_closed(self, artifact):
        path, root = artifact
        bad = json.dumps({"summary": "s", "findings": [{"severity": "Nope"}]})
        client = _FakeClient([[bad]])
        res = arl.run_glm_review(path, "design", root, client=client)
        assert res.status == "parse_error"

    def test_fenced_output_stripped(self, artifact):
        path, root = artifact
        fenced = "```json\n" + _findings_json() + "\n```"
        client = _FakeClient([[fenced]])
        res = arl.run_glm_review(path, "design", root, client=client)
        assert res.status == "success"

    def test_trickling_deadline_timeout(self, artifact, monkeypatch):
        path, root = artifact

        def trickle():
            def gen():
                while True:
                    time.sleep(0.02)
                    yield _chunk("x")
            return gen()

        monkeypatch.setattr(arl, "MAX_RETRIES", 0)
        client = _FakeClient([trickle])
        res = arl.run_glm_review(path, "design", root, client=client, timeout=0.05)
        assert res.status == "timeout"

    def test_sdk_exception_retries_then_error(self, artifact):
        path, root = artifact
        client = _FakeClient([RuntimeError("boom"), RuntimeError("boom2")])
        res = arl.run_glm_review(path, "design", root, client=client)
        assert res.status == "error"
        assert "boom" in res.raw_error

    def test_retry_discards_partial_chunks(self, artifact):
        """Attempt 1 streams a valid-JSON PREFIX then dies; attempt 2 streams a
        complete document. If chunks were combined across attempts the parse
        would fail — success proves the discard."""
        path, root = artifact
        prefix = _findings_json()[:15]

        def dying():
            def gen():
                yield _chunk(prefix)
                raise RuntimeError("mid-stream death")
            return gen()

        client = _FakeClient([dying, [_findings_json()]])
        res = arl.run_glm_review(path, "design", root, client=client)
        assert res.status == "success"

    def test_sdk_missing_not_installed(self, artifact, monkeypatch):
        path, root = artifact
        monkeypatch.setattr(arl, "_zhipuai_version", lambda: None)
        res = arl.run_glm_review(path, "design", root)   # no injected client
        assert res.status == "not_installed"
        assert "zhipuai" in res.raw_error

    def test_key_missing_unauthenticated(self, artifact, monkeypatch):
        path, root = artifact
        monkeypatch.setattr(arl, "_zhipuai_version", lambda: "2.1.5")
        for v in ("ZHIPUAI_API_KEY", "ZHIPU_API_KEY", "GLM_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        res = arl.run_glm_review(path, "design", root)
        assert res.status == "unauthenticated"
        assert "ZHIPUAI_API_KEY" in res.raw_error

    def test_bad_base_url_error_no_call(self, artifact, monkeypatch):
        path, root = artifact
        monkeypatch.setattr(arl, "_zhipuai_version", lambda: "2.1.5")
        monkeypatch.setenv("ZHIPUAI_API_KEY", "k")
        monkeypatch.setenv("ZHIPUAI_BASE_URL", "http://plain.example/v4")
        res = arl.run_glm_review(path, "design", root)
        assert res.status == "error"

    def test_block_secrets_no_egress(self, artifact, monkeypatch):
        path, root = artifact
        Path(path).write_text("password: hunter2\n")
        monkeypatch.setattr(arl, "BLOCK_SECRETS", True)
        client = _FakeClient([[_findings_json()]])
        res = arl.run_glm_review(path, "design", root, client=client)
        assert res.status == "error"
        assert client.calls == []                # blocked BEFORE any provider call

    def test_block_secrets_applies_to_supplied_artifact_text(self, artifact, monkeypatch):
        """A3: the scan runs INSIDE the run function even on preloaded text."""
        path, root = artifact
        monkeypatch.setattr(arl, "BLOCK_SECRETS", True)
        client = _FakeClient([[_findings_json()]])
        res = arl.run_glm_review(path, "design", root, client=client,
                                 artifact_text=("api_key = SECRET\n", False))
        assert res.status == "error"
        assert client.calls == []

    def test_artifact_text_skips_file_read(self, artifact):
        path, root = artifact
        client = _FakeClient([[_findings_json()]])
        res = arl.run_glm_review(str(Path(root) / "does-not-exist.md"), "design",
                                 root, client=client, artifact_text=("hello", False))
        assert res.status == "success"


class TestGlmConsult:
    def test_success_writes_out(self, artifact, tmp_path):
        path, root = artifact
        out = tmp_path / "out.json"
        client = _FakeClient([[_proposal_json()]])
        res = arl.run_glm_consult(path, root, str(out), client=client)
        assert res.status == "success"
        assert res.backend == "glm"
        data = json.loads(out.read_text())
        assert data["approach"] == "do X"

    def test_failure_writes_empty_marker(self, artifact, tmp_path):
        path, root = artifact
        out = tmp_path / "out.json"
        client = _FakeClient([["not json"]])
        res = arl.run_glm_consult(path, root, str(out), client=client)
        assert res.status == "parse_error"
        data = json.loads(out.read_text())
        assert data == {"approach": "", "key_decisions": [], "risks": [], "sketch": ""}

    def test_consult_prompt_is_peer_framing(self, artifact, tmp_path):
        path, root = artifact
        client = _FakeClient([[_proposal_json()]])
        arl.run_glm_consult(path, root, str(tmp_path / "o.json"), client=client)
        prompt = client.calls[0]["messages"][0]["content"]
        assert "peer" in prompt.lower()
        assert "JSON Schema" in prompt

    def test_consult_artifact_text_supported(self, artifact, tmp_path):
        path, root = artifact
        client = _FakeClient([[_proposal_json()]])
        res = arl.run_glm_consult(str(Path(root) / "nope.md"), root,
                                  str(tmp_path / "o.json"), client=client,
                                  artifact_text=("problem text", False))
        assert res.status == "success"


class TestStripJsonFences:
    def test_plain_passthrough(self):
        assert arl._strip_json_fences('{"a": 1}') == '{"a": 1}'

    def test_json_fence(self):
        assert arl._strip_json_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_bare_fence(self):
        assert arl._strip_json_fences('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_whitespace_around(self):
        assert arl._strip_json_fences('  ```json\n{"a": 1}\n```  ') == '{"a": 1}'


class TestCodexResultBackend:
    def test_default_backend_gpt(self):
        r = arl.CodexResult(status="success", findings=())
        assert r.backend == "gpt"


class TestUrlEdgeCases:
    """8a T2 findings: lazy urlsplit port parsing + IPv6 + unknown-backend egress."""

    def test_out_of_range_port_rejected_not_crash(self):
        ok, reason = arl.validate_glm_base_url("https://api.z.ai:99999/v4")
        assert ok is False
        assert "port" in reason.lower()

    def test_redact_endpoint_bad_port_degrades(self):
        assert arl.redact_endpoint("https://api.z.ai:99999/v4") == "<unparseable endpoint>"

    def test_redact_endpoint_ipv6_rebracketed(self):
        assert arl.redact_endpoint("https://[::1]:8443/v4") == "https://[::1]:8443"

    def test_egress_warning_bad_port_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("ZHIPUAI_BASE_URL", "https://api.z.ai:99999/v4")
        w = arl.egress_warning(backend="glm")   # must not raise
        assert "unparseable" in w or "endpoint" in w.lower()

    def test_egress_warning_unknown_backend_no_destination(self):
        """Consent surface must not claim OpenAI for an unknown/invalid backend."""
        w = arl.egress_warning(backend="invalid")
        assert "OpenAI" not in w
        assert "unknown" in w.lower() or "invalid" in w.lower()


# ---------------------------------------------------------------------------
# Task 4 — backend-aware report paths + Reviewer line (gpt golden byte-compat)
# ---------------------------------------------------------------------------

# Captured from the PRE-#403 renderer for this exact input (golden — gpt
# single-backend output must stay byte-identical; snapshot 2026-07-14).
_LEGACY_GPT_REPORT = (
    "# Adversarial Review — a.md\n\n- Date: 2026-07-14\n- Artifact type: design\n"
    "- Reviewer: Codex (model config-default, reasoning effort high)\n"
    "- Findings: 1 (Critical 0, High 1, Medium 0, Low 0)\n\n## Summary\n\nsum\n\n"
    "## Findings\n\n### 1. [High] security · high confidence — L1\n\n> quoted\n\n"
    "desc\n\n**Recommendation:** rec\n\n---\n"
    "_Report-only: this review does not edit the artifact. Findings are advisory; "
    "incorporate them at your discretion._"
)


class TestBackendReportPaths:
    def test_review_path_gpt_unchanged(self, tmp_path):
        legacy = arl.review_report_path(str(tmp_path), "doc.md", "2026-07-14")
        explicit = arl.review_report_path(str(tmp_path), "doc.md", "2026-07-14",
                                          backend="gpt")
        assert legacy == explicit
        assert legacy.endswith("doc-md-2026-07-14.md")

    def test_review_path_glm_suffix_after_date(self, tmp_path):
        p = arl.review_report_path(str(tmp_path), "doc.md", "2026-07-14", backend="glm")
        assert p.endswith("doc-md-2026-07-14-glm.md")

    def test_glm_suffix_collision_free(self, tmp_path):
        """gpt review of foo-glm.md vs glm review of foo.md — disjoint by construction."""
        gpt_of_glm_named = arl.review_report_path(str(tmp_path), "foo-glm.md",
                                                  "2026-07-14", backend="gpt")
        glm_of_foo = arl.review_report_path(str(tmp_path), "foo.md",
                                            "2026-07-14", backend="glm")
        assert gpt_of_glm_named != glm_of_foo

    def test_consult_path_glm_suffix_after_date(self, tmp_path):
        gpt = arl.consult_report_path(str(tmp_path), "prob.md", "2026-07-14")
        glm = arl.consult_report_path(str(tmp_path), "prob.md", "2026-07-14",
                                      backend="glm")
        assert gpt.endswith("peer-prob-2026-07-14.md")
        assert glm.endswith("peer-prob-2026-07-14-glm.md")


class TestBackendReviewerLine:
    FINDING = {"evidence": "quoted", "severity": "High", "category": "security",
               "confidence": "high", "description": "desc", "recommendation": "rec",
               "ambiguity_flag": None, "ambiguity_reason": None, "location": "L1"}
    META = {"artifact": "a.md", "date": "2026-07-14", "artifact_type": "design",
            "summary": "sum", "model": "", "effort": "high"}

    def test_gpt_report_byte_identical_golden(self):
        """No backend key in meta (single-backend gpt) -> EXACT legacy bytes."""
        assert arl.render_report_md([self.FINDING], dict(self.META)) == _LEGACY_GPT_REPORT

    def test_gpt_explicit_backend_also_legacy(self):
        meta = dict(self.META); meta["backend"] = "gpt"
        assert arl.render_report_md([self.FINDING], meta) == _LEGACY_GPT_REPORT

    def test_glm_reviewer_line(self):
        meta = dict(self.META); meta.update(backend="glm", model="glm-5.2")
        md = arl.render_report_md([self.FINDING], meta)
        assert "- Reviewer: GLM (model glm-5.2, reasoning effort high)" in md
        assert "Codex" not in md

    def test_consult_gpt_legacy_line(self):
        md = arl.render_consult_md({"approach": "a", "key_decisions": [],
                                    "risks": [], "sketch": "s"},
                                   {"artifact": "p.md", "date": "2026-07-14"})
        assert "- Reviewer: Codex (peer designer)" in md

    def test_consult_glm_line(self):
        md = arl.render_consult_md({"approach": "a", "key_decisions": [],
                                    "risks": [], "sketch": "s"},
                                   {"artifact": "p.md", "date": "2026-07-14",
                                    "backend": "glm", "model": "glm-5.2"})
        assert "GLM" in md
        assert "Codex" not in md


# ---------------------------------------------------------------------------
# Task 5 — CLI dispatch: --backend on review/consult + both-mode orchestration
# (in-process main() with monkeypatched run functions — dispatch layer only)
# ---------------------------------------------------------------------------

def _ok_result(backend, n=1, summary="s"):
    return arl.CodexResult(status="success",
                           findings=tuple(_valid_finding(f"{backend}-d{i}") for i in range(n)),
                           summary=summary, model="m", effort="high", backend=backend)


def _fail_result(backend, status="error", raw="boom"):
    return arl.CodexResult(status=status, findings=(), raw_error=raw, backend=backend)


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Artifact + project root + spies on both run functions."""
    root = tmp_path
    art = root / "doc.md"
    art.write_text("# doc\n")
    calls = {"gpt": 0, "glm": 0}

    def fake_gpt(*a, **kw):
        calls["gpt"] += 1
        return _ok_result("gpt")

    def fake_glm(*a, **kw):
        calls["glm"] += 1
        return _ok_result("glm")

    monkeypatch.setattr(arl, "run_codex_review", fake_gpt)
    monkeypatch.setattr(arl, "run_glm_review", fake_glm)
    return SimpleNamespace(root=str(root), art=str(art), calls=calls,
                           monkeypatch=monkeypatch)


def _review_argv(env, *extra):
    return ["review", "--artifact", env.art, "--project-root", env.root,
            "--date", "2026-07-14", *extra]


class TestCliBackendDispatch:
    def test_legacy_argv_runs_gpt_only(self, cli_env, capsys):
        rc = arl.main(_review_argv(cli_env))
        assert rc == 0
        assert cli_env.calls == {"gpt": 1, "glm": 0}
        out = capsys.readouterr().out
        assert "doc-md-2026-07-14.md" in out
        assert "-glm" not in out

    def test_backend_glm_runs_glm_only(self, cli_env, capsys):
        rc = arl.main(_review_argv(cli_env, "--backend", "glm"))
        assert rc == 0
        assert cli_env.calls == {"gpt": 0, "glm": 1}
        assert "doc-md-2026-07-14-glm.md" in capsys.readouterr().out

    @pytest.mark.parametrize("status,code", [
        ("not_installed", 2), ("unauthenticated", 2), ("timeout", 3),
        ("error", 3), ("parse_error", 4)])
    def test_glm_failure_exit_codes(self, cli_env, status, code):
        cli_env.monkeypatch.setattr(
            arl, "run_glm_review", lambda *a, **kw: _fail_result("glm", status))
        rc = arl.main(_review_argv(cli_env, "--backend", "glm"))
        assert rc == code

    def test_invalid_backend_arg_rejected_by_argparse(self, cli_env):
        with pytest.raises(SystemExit) as exc:
            arl.main(_review_argv(cli_env, "--backend", "glm5"))
        assert exc.value.code == 2

    def test_backend_absent_resolves_from_config(self, cli_env, tmp_path):
        ws = _write_ws(tmp_path / "w", []) if False else None
        wsdir = tmp_path / "wsdir"; wsdir.mkdir()
        ws = wsdir / ".rawgentic_workspace.json"
        ws.write_text(json.dumps({"version": 1, "projects": [
            {"name": "p", "path": "./p",
             "adversarialReview": {"enabled": True, "workflows": ["x"],
                                   "backend": "glm"}}]}))
        rc = arl.main(_review_argv(cli_env, "--workspace", str(ws), "--project", "p"))
        assert rc == 0
        assert cli_env.calls == {"gpt": 0, "glm": 1}

    def test_backend_absent_invalid_config_refuses_no_run(self, cli_env, tmp_path, capsys):
        wsdir = tmp_path / "wsdir"; wsdir.mkdir()
        ws = wsdir / ".rawgentic_workspace.json"
        ws.write_text(json.dumps({"version": 1, "projects": [
            {"name": "p", "path": "./p",
             "adversarialReview": {"enabled": True, "workflows": ["x"],
                                   "backend": "glm5"}}]}))
        rc = arl.main(_review_argv(cli_env, "--workspace", str(ws), "--project", "p"))
        assert rc == 2
        assert cli_env.calls == {"gpt": 0, "glm": 0}   # refused BEFORE any provider call
        assert "glm5" in capsys.readouterr().err

    def test_explicit_arg_skips_invalid_config(self, cli_env, tmp_path):
        """Explicit valid --backend is the source — corrupt config backend ignored."""
        wsdir = tmp_path / "wsdir"; wsdir.mkdir()
        ws = wsdir / ".rawgentic_workspace.json"
        ws.write_text(json.dumps({"version": 1, "projects": [
            {"name": "p", "path": "./p",
             "adversarialReview": {"enabled": True, "workflows": ["x"],
                                   "backend": "glm5"}}]}))
        rc = arl.main(_review_argv(cli_env, "--backend", "gpt",
                                   "--workspace", str(ws), "--project", "p"))
        assert rc == 0
        assert cli_env.calls == {"gpt": 1, "glm": 0}


class TestCliBothMode:
    def test_both_success_exit0_two_reports_manifest(self, cli_env, capsys):
        rc = arl.main(_review_argv(cli_env, "--backend", "both"))
        assert rc == 0
        assert cli_env.calls == {"gpt": 1, "glm": 1}
        out = capsys.readouterr().out
        assert "gpt: " in out and "glm: " in out       # per-backend manifest lines
        gpt_report = Path(cli_env.root) / "docs/reviews/doc-md-2026-07-14.md"
        glm_report = Path(cli_env.root) / "docs/reviews/doc-md-2026-07-14-glm.md"
        assert gpt_report.exists() and glm_report.exists()

    def test_both_reads_artifact_once(self, cli_env):
        reads = []
        real = arl.read_artifact
        cli_env.monkeypatch.setattr(
            arl, "read_artifact",
            lambda *a, **kw: (reads.append(1), real(*a, **kw))[1])
        arl.main(_review_argv(cli_env, "--backend", "both"))
        assert len(reads) == 1

    def test_both_glm_fails_exit5_gpt_report_kept(self, cli_env, capsys):
        cli_env.monkeypatch.setattr(
            arl, "run_glm_review", lambda *a, **kw: _fail_result("glm", "timeout"))
        rc = arl.main(_review_argv(cli_env, "--backend", "both"))
        assert rc == 5
        cap = capsys.readouterr()
        assert "gpt: " in cap.out
        assert "FAILED" in cap.err and "glm" in cap.err
        assert (Path(cli_env.root) / "docs/reviews/doc-md-2026-07-14.md").exists()
        assert not (Path(cli_env.root) / "docs/reviews/doc-md-2026-07-14-glm.md").exists()

    def test_both_gpt_fails_exit5(self, cli_env):
        cli_env.monkeypatch.setattr(
            arl, "run_codex_review", lambda *a, **kw: _fail_result("gpt", "error"))
        rc = arl.main(_review_argv(cli_env, "--backend", "both"))
        assert rc == 5

    def test_both_all_fail_gpt_failure_class(self, cli_env):
        cli_env.monkeypatch.setattr(
            arl, "run_codex_review", lambda *a, **kw: _fail_result("gpt", "parse_error"))
        cli_env.monkeypatch.setattr(
            arl, "run_glm_review", lambda *a, **kw: _fail_result("glm", "timeout"))
        rc = arl.main(_review_argv(cli_env, "--backend", "both"))
        assert rc == 4                                  # gpt's class wins

    def test_both_sidecars_gpt_exact_glm_sibling(self, cli_env, tmp_path):
        sc = Path(cli_env.root) / "sc.json"
        rc = arl.main(_review_argv(cli_env, "--backend", "both",
                                   "--findings-json", str(sc)))
        assert rc == 0
        gpt_side = json.loads(sc.read_text())
        glm_side = json.loads((Path(cli_env.root) / "sc-glm.json").read_text())
        # gpt sidecar byte-shape: NO backend key anywhere (legacy consumers)
        assert "backend" not in json.dumps(gpt_side)
        # glm sibling: per-finding backend tag
        assert all(f.get("backend") == "glm" for f in glm_side["findings"])

    def test_both_stale_sibling_cleared_on_glm_failure(self, cli_env):
        sc = Path(cli_env.root) / "sc.json"
        stale = Path(cli_env.root) / "sc-glm.json"
        stale.write_text('{"stale": true}')
        cli_env.monkeypatch.setattr(
            arl, "run_glm_review", lambda *a, **kw: _fail_result("glm", "error"))
        rc = arl.main(_review_argv(cli_env, "--backend", "both",
                                   "--findings-json", str(sc)))
        assert rc == 5
        assert not stale.exists()                       # prior run's sibling never survives

    def test_glm_only_sidecar_exact_path_with_tag(self, cli_env):
        sc = Path(cli_env.root) / "sc.json"
        rc = arl.main(_review_argv(cli_env, "--backend", "glm",
                                   "--findings-json", str(sc)))
        assert rc == 0
        side = json.loads(sc.read_text())
        assert all(f.get("backend") == "glm" for f in side["findings"])


class TestCliConsultBackend:
    @pytest.fixture
    def consult_env(self, tmp_path, monkeypatch):
        root = tmp_path
        art = root / "prob.md"
        art.write_text("problem\n")
        out = root / "out.json"
        calls = {"gpt": 0, "glm": 0}

        def fake_gpt(artifact, project_root, out_path, **kw):
            calls["gpt"] += 1
            Path(out_path).write_text(_proposal_json())
            return _ok_result("gpt")

        def fake_glm(artifact, project_root, out_path, **kw):
            calls["glm"] += 1
            Path(out_path).write_text(_proposal_json())
            return _ok_result("glm")

        monkeypatch.setattr(arl, "run_codex_consult", fake_gpt)
        monkeypatch.setattr(arl, "run_glm_consult", fake_glm)
        return SimpleNamespace(root=str(root), art=str(art), out=str(out),
                               calls=calls, monkeypatch=monkeypatch)

    def _argv(self, env, *extra):
        return ["consult", "--artifact", env.art, "--project-root", env.root,
                "--out", env.out, "--date", "2026-07-14", *extra]

    def test_legacy_consult_gpt_only(self, consult_env):
        rc = arl.main(self._argv(consult_env))
        assert rc == 0
        assert consult_env.calls == {"gpt": 1, "glm": 0}

    def test_consult_backend_glm(self, consult_env, capsys):
        rc = arl.main(self._argv(consult_env, "--backend", "glm"))
        assert rc == 0
        assert consult_env.calls == {"gpt": 0, "glm": 1}
        assert "peer-prob-2026-07-14-glm.md" in capsys.readouterr().out

    def test_consult_both_dual_out_and_reports(self, consult_env, capsys):
        rc = arl.main(self._argv(consult_env, "--backend", "both"))
        assert rc == 0
        assert consult_env.calls == {"gpt": 1, "glm": 1}
        assert Path(consult_env.out).exists()
        assert (Path(consult_env.root) / "out-glm.json").exists()
        out = capsys.readouterr().out
        assert "gpt: " in out and "glm: " in out

    def test_consult_both_glm_fails_exit5(self, consult_env):
        consult_env.monkeypatch.setattr(
            arl, "run_glm_consult",
            lambda *a, **kw: _fail_result("glm", "timeout"))
        rc = arl.main(self._argv(consult_env, "--backend", "both"))
        assert rc == 5


class TestResolveEdgeCases:
    """8a T5 findings: half-given resolution info + cross-backend collisions."""

    def test_workspace_without_project_refuses(self, cli_env, capsys):
        rc = arl.main(_review_argv(cli_env, "--workspace", "/some/ws.json"))
        assert rc == 2
        assert cli_env.calls == {"gpt": 0, "glm": 0}

    def test_empty_project_refuses_not_gpt(self, cli_env):
        """--project "$NAME" with $NAME unset must fail closed, never egress gpt."""
        rc = arl.main(_review_argv(cli_env, "--workspace", "/some/ws.json",
                                   "--project", ""))
        assert rc == 2
        assert cli_env.calls == {"gpt": 0, "glm": 0}

    def test_both_sidecar_at_other_backends_report_path_refused(self, cli_env):
        glm_report = arl.review_report_path(cli_env.root, cli_env.art,
                                            "2026-07-14", backend="glm")
        Path(glm_report).parent.mkdir(parents=True, exist_ok=True)
        rc = arl.main(_review_argv(cli_env, "--backend", "both",
                                   "--findings-json", glm_report))
        assert rc == 2
        assert cli_env.calls == {"gpt": 0, "glm": 0}


class TestPrereqCliBackend:
    """Step 11 High: the SKILL.md-documented `prereq --backend <b>` must work."""

    def test_prereq_backend_glm_cli(self, monkeypatch):
        monkeypatch.setattr(arl, "_zhipuai_version", lambda: None)
        for v in ("ZHIPUAI_API_KEY", "ZHIPU_API_KEY", "GLM_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        rc = arl.main(["prereq", "--backend", "glm"])
        assert rc == 2                       # a real prereq verdict, not an argparse error

    def test_prereq_backend_gpt_cli(self, monkeypatch):
        monkeypatch.setattr(arl, "codex_installed", lambda: True)
        monkeypatch.setattr(arl, "codex_authenticated", lambda: True)
        assert arl.main(["prereq", "--backend", "gpt"]) == 0

    def test_prereq_backend_both_degrades(self, monkeypatch, capsys):
        monkeypatch.setattr(arl, "codex_installed", lambda: True)
        monkeypatch.setattr(arl, "codex_authenticated", lambda: True)
        monkeypatch.setattr(arl, "_zhipuai_version", lambda: None)
        for v in ("ZHIPUAI_API_KEY", "ZHIPU_API_KEY", "GLM_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        rc = arl.main(["prereq", "--backend", "both"])
        assert rc == 0                       # degrade-and-warn: >=1 ready
        out = capsys.readouterr().out
        assert "gpt" in out.lower() and "glm" in out.lower()

    def test_prereq_bare_legacy_unchanged(self, monkeypatch):
        monkeypatch.setattr(arl, "codex_installed", lambda: True)
        monkeypatch.setattr(arl, "codex_authenticated", lambda: True)
        assert arl.main(["prereq"]) == 0


class TestStep11DiffReviewFindings:
    """Step 11 adversarial diff-review findings (#403): null backend, empty-empty
    resolution, consult --out collision with the artifact."""

    def test_explicit_null_backend_is_invalid(self, tmp_path):
        """JSON `"backend": null` is a PRESENT value outside the vocabulary —
        it must refuse like any other invalid value, not alias to absent."""
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["x"], "backend": None})])
        cfg = arl.load_adversarial_review_config(str(ws), "p")
        assert cfg.backend == "invalid"

    def test_absent_backend_still_gpt(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["x"]})])
        assert arl.load_adversarial_review_config(str(ws), "p").backend == "gpt"

    def test_both_empty_ws_and_project_refuse(self, cli_env):
        """Two unset shell vars interpolated as empty strings must fail closed."""
        rc = arl.main(_review_argv(cli_env, "--workspace", "", "--project", ""))
        assert rc == 2
        assert cli_env.calls == {"gpt": 0, "glm": 0}

    def test_neither_given_still_legacy_gpt(self, cli_env):
        rc = arl.main(_review_argv(cli_env))
        assert rc == 0
        assert cli_env.calls == {"gpt": 1, "glm": 0}

    def test_consult_both_out_sibling_colliding_with_artifact_refused(self, tmp_path, monkeypatch):
        """artifact foo-glm.md + --out foo.md: the derived glm sibling IS the
        artifact — the engine must refuse before any run function executes."""
        root = tmp_path
        art = root / "foo-glm.md"
        art.write_text("problem\n")
        calls = []
        monkeypatch.setattr(arl, "run_codex_consult",
                            lambda *a, **kw: calls.append("gpt") or _ok_result("gpt"))
        monkeypatch.setattr(arl, "run_glm_consult",
                            lambda *a, **kw: calls.append("glm") or _ok_result("glm"))
        rc = arl.main(["consult", "--artifact", str(art), "--project-root", str(root),
                       "--out", str(root / "foo.md"), "--date", "2026-07-14",
                       "--backend", "both"])
        assert rc == 2
        assert calls == []

    def test_consult_single_out_equal_artifact_refused(self, tmp_path, monkeypatch):
        root = tmp_path
        art = root / "prob.md"
        art.write_text("problem\n")
        monkeypatch.setattr(arl, "run_codex_consult", lambda *a, **kw: _ok_result("gpt"))
        rc = arl.main(["consult", "--artifact", str(art), "--project-root", str(root),
                       "--out", str(art), "--date", "2026-07-14"])
        assert rc == 2


class TestGlmDispositionsThreading:
    def test_dispositions_text_threaded_into_prompt(self, artifact):
        # #393: both backends receive the same ledger fence; schema suffix
        # stays after the ledger block.
        path, root = artifact
        client = _FakeClient([[_findings_json()]])
        arl.run_glm_review(path, "design", root, client=client,
                           dispositions_text="d-4-2-1-ab3f | High | security | x | dissolved | d | r")
        prompt = client.calls[0]["messages"][0]["content"]
        assert "=== BEGIN SETTLED DISPOSITIONS [k=" in prompt
        assert prompt.index("=== END SETTLED DISPOSITIONS") < prompt.index("JSON Schema")
