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
        assert cfg.backend_error_value == bad

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
