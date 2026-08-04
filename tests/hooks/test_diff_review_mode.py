"""Tests for `adversarialReview.diffReviewMode` (issue #879, epic #875 M1).

Why the field exists: `plan_lib.should_run_diff_review` elects the WF2 Step-11
cross-model diff review only when a changed path matches the security-relevant
allowlist or a plan task is `riskLevel: high`. This repo's product is markdown, so a
prose-only diff scores "no security surface" and the author model ends up reviewing
its own work as the only Step-11 pass (#856 run, friction point 1). `diffReviewMode`
lets a prose-product repo force the review on; app repos keep the cheap heuristic.

Design invariants under test — deliberately the same shape as the #403 `backend`
field (`tests/hooks/test_glm_backend.py` is the precedent this mirrors):
- Absent `diffReviewMode` -> "auto" silently, byte-identical behavior for every
  existing project (AC1).
- Present-but-invalid -> the "invalid" sentinel with the rejected raw value preserved
  on `diff_review_mode_error_value` + a stderr warning; the config-RESOLVING CLI verb
  refuses with exit 2 rather than laundering the value into "auto" (AC3). A silent
  fall-through to "auto" would re-create the exact silent-skip class #879 closes.
- The `diff-review-mode` subcommand exits 0 printing the mode for valid/absent/
  disabled config, and exits 2 naming the rejected value for present-but-invalid.
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
# Task 1 — config coercion: the `diffReviewMode` field
# ---------------------------------------------------------------------------

class TestDiffReviewModeCoercion:
    def test_modes_vocabulary_constant(self):
        assert arl.DIFF_REVIEW_MODES == ("auto", "always")

    def test_absent_mode_defaults_auto_silently(self, tmp_path, capsys):
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["implement-feature"]})])
        cfg = arl.load_adversarial_review_config(str(ws), "p")
        assert cfg.diff_review_mode == "auto"
        assert cfg.diff_review_mode_error_value is None
        # silent: the absent case must not warn (every pre-#879 config is this case)
        assert "diffReviewMode" not in capsys.readouterr().err

    @pytest.mark.parametrize("value", ["auto", "always"])
    def test_valid_mode_kept(self, tmp_path, value):
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["implement-feature"],
            "diffReviewMode": value})])
        cfg = arl.load_adversarial_review_config(str(ws), "p")
        assert cfg.diff_review_mode == value
        assert cfg.diff_review_mode_error_value is None

    @pytest.mark.parametrize(
        "bad", ["Always", "ALWAYS", "always ", "", "on", "never", 5, {"x": 1},
                ["always"], True])
    def test_invalid_mode_sentinel_preserves_value(self, tmp_path, bad):
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["implement-feature"],
            "diffReviewMode": bad})])
        cfg = arl.load_adversarial_review_config(str(ws), "p")
        assert cfg.diff_review_mode == "invalid"
        # repr (not the live object) keeps the frozen dataclass hashable when the
        # rejected value is a dict/list — the same reason backend_error_value is a repr.
        assert cfg.diff_review_mode_error_value == repr(bad)
        hash(cfg)

    def test_invalid_mode_warns_stderr_naming_the_value(self, tmp_path, capsys):
        """The loader itself warns, naming the rejected value — a silent coercion
        would leave an operator's typo undiscoverable."""
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["x"], "diffReviewMode": "alwyas"})])
        arl.load_adversarial_review_config(str(ws), "p")
        err = capsys.readouterr().err
        assert "alwyas" in err
        assert "diffReviewMode" in err

    def test_explicit_null_mode_is_invalid(self, tmp_path):
        """JSON `"diffReviewMode": null` is a PRESENT value outside the vocabulary —
        it must refuse like any other invalid value, not alias to absent."""
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["x"], "diffReviewMode": None})])
        cfg = arl.load_adversarial_review_config(str(ws), "p")
        assert cfg.diff_review_mode == "invalid"

    def test_bool_shorthand_mode_auto(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj(adversarialReview=True)])
        cfg = arl.load_adversarial_review_config(str(ws), "p")
        assert cfg.diff_review_mode == "auto"
        assert cfg.diff_review_mode_error_value is None

    def test_disabled_block_mode_still_coerced(self, tmp_path):
        """A disabled block with a valid mode keeps it (harmless, informative)."""
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": False, "workflows": [], "diffReviewMode": "always"})])
        cfg = arl.load_adversarial_review_config(str(ws), "p")
        assert cfg.enabled is False
        assert cfg.diff_review_mode == "always"

    def test_missing_file_disabled_mode_auto(self, tmp_path):
        cfg = arl.load_adversarial_review_config(str(tmp_path / "nope.json"), "p")
        assert cfg.enabled is False
        assert cfg.diff_review_mode == "auto"

    def test_peer_consult_key_mode(self, tmp_path):
        """The field rides the shared coercion, so it parses under any key selector."""
        ws = _write_ws(tmp_path, [{
            "name": "p", "path": "./projects/p",
            "peerConsult": {"enabled": True, "workflows": ["implement-feature"],
                            "diffReviewMode": "always"},
        }])
        cfg = arl.load_adversarial_review_config(str(ws), "p", key="peerConsult")
        assert cfg.diff_review_mode == "always"

    def test_mode_and_backend_are_independent(self, tmp_path):
        """An invalid mode must not poison a valid backend, or vice versa (#403 x #879)."""
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["x"],
            "backend": "glm", "diffReviewMode": "nope"})])
        cfg = arl.load_adversarial_review_config(str(ws), "p")
        assert cfg.backend == "glm"
        assert cfg.backend_error_value is None
        assert cfg.diff_review_mode == "invalid"
        assert cfg.diff_review_mode_error_value == repr("nope")

    def test_ac1_absent_field_leaves_every_other_field_untouched(self, tmp_path):
        """AC1: a config with no `diffReviewMode` resolves exactly as it did pre-#879."""
        block = {"enabled": True, "workflows": ["implement-feature", "fix-bug"],
                 "backend": "both"}
        ws = _write_ws(tmp_path, [_proj(adversarialReview=block)])
        cfg = arl.load_adversarial_review_config(str(ws), "p")
        assert cfg.enabled is True
        assert cfg.workflows == ("implement-feature", "fix-bug")
        assert cfg.backend == "both"
        assert cfg.backend_error_value is None
        assert cfg.diff_review_mode == "auto"


# ---------------------------------------------------------------------------
# Task 3 — the `diff-review-mode` CLI subcommand exit contract
# ---------------------------------------------------------------------------

def _run_mode_cmd(ws: Path, project: str, key: str | None = None):
    cmd = [sys.executable, CLI, "diff-review-mode",
           "--workspace", str(ws), "--project", project]
    if key:
        cmd += ["--key", key]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


class TestDiffReviewModeSubcommand:
    def test_valid_mode_exit0_prints_it(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["x"], "diffReviewMode": "always"})])
        r = _run_mode_cmd(ws, "p")
        assert r.returncode == 0
        assert r.stdout.strip() == "always"

    def test_absent_mode_exit0_auto(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["x"]})])
        r = _run_mode_cmd(ws, "p")
        assert r.returncode == 0
        assert r.stdout.strip() == "auto"

    def test_missing_config_exit0_auto(self, tmp_path):
        ws = _write_ws(tmp_path, [{"name": "p", "path": "./projects/p"}])
        r = _run_mode_cmd(ws, "p")
        assert r.returncode == 0
        assert r.stdout.strip() == "auto"

    def test_missing_project_exit0_auto(self, tmp_path):
        ws = _write_ws(tmp_path, [{"name": "other", "path": "./projects/other"}])
        r = _run_mode_cmd(ws, "p")
        assert r.returncode == 0
        assert r.stdout.strip() == "auto"

    def test_disabled_block_exit0_prints_mode(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": False, "workflows": [], "diffReviewMode": "always"})])
        r = _run_mode_cmd(ws, "p")
        assert r.returncode == 0
        assert r.stdout.strip() == "always"

    def test_invalid_mode_exit2_names_value_never_auto(self, tmp_path):
        """AC3: the refusal is loud and never launders the value into "auto"."""
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["x"], "diffReviewMode": "alwyas"})])
        r = _run_mode_cmd(ws, "p")
        assert r.returncode == 2
        assert "alwyas" in r.stderr
        assert r.stdout.strip() != "auto"
        assert r.stdout.strip() == ""

    def test_invalid_mode_stderr_names_the_project_and_vocabulary(self, tmp_path):
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["x"], "diffReviewMode": 5})])
        r = _run_mode_cmd(ws, "p")
        assert r.returncode == 2
        assert "'p'" in r.stderr
        assert "always" in r.stderr  # the accepted vocabulary is shown

    def test_peer_consult_key_selector(self, tmp_path):
        ws = _write_ws(tmp_path, [{
            "name": "p", "path": "./projects/p",
            "peerConsult": {"enabled": True, "workflows": ["x"],
                            "diffReviewMode": "always"},
        }])
        r = _run_mode_cmd(ws, "p", key="peerConsult")
        assert r.returncode == 0
        assert r.stdout.strip() == "always"

    def test_invalid_backend_does_not_fail_the_mode_verb(self, tmp_path):
        """The two verbs refuse independently — a bad backend must not make the
        mode unreadable (or the 1a gate could not report which field is broken)."""
        ws = _write_ws(tmp_path, [_proj(adversarialReview={
            "enabled": True, "workflows": ["x"],
            "backend": "glm5", "diffReviewMode": "always"})])
        r = _run_mode_cmd(ws, "p")
        assert r.returncode == 0
        assert r.stdout.strip() == "always"

    def test_help_lists_the_new_verb(self):
        r = subprocess.run([sys.executable, CLI, "--help"],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0
        assert "diff-review-mode" in r.stdout
