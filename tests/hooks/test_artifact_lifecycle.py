"""Drift guard: the HTML design-artifact lifecycle prose (#174) stays wired into
WF1/WF2/WF3, and the shared render helper stays referenced (pattern mirrors
tests/hooks/test_model_routing_dispatch.py). Corpus-based (SKILL.md + references/)
so the #158/#159 spine splits don't hide the prose.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.corpus import skill_corpus

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "hooks"))


def test_render_helper_referenced_by_all_three_skills():
    """AC1/AC2/AC3: every lifecycle-participating skill names the shared helper."""
    for skill in ("create-issue", "implement-feature", "fix-bug"):
        assert "render_artifact.py" in skill_corpus(skill), f"{skill} must reference the render helper"


def test_wf1_renders_and_comments_artifact():
    """AC2: WF1 renders the spec artifact, publishes it, posts the URL as a comment."""
    c = skill_corpus("create-issue")
    assert "design artifact" in c.lower()
    assert "designArtifact" in c          # the config opt-out key
    assert "Artifact" in c                # publish via the Artifact tool


def test_wf2_wf3_create_or_update_artifact_before_pr():
    """AC3: WF2/WF3 create-or-update the artifact committed inside the PR BEFORE gh pr create."""
    for skill in ("implement-feature", "fix-bug"):
        c = skill_corpus(skill)
        assert "design artifact" in c.lower(), f"{skill} missing design-artifact step"
        assert "render_artifact.py" in c
        assert "before" in c.lower() and "gh pr create" in c


def test_lifecycle_is_config_gated_opt_out():
    """AC5: skippable per project via the designArtifact key; declining changes nothing."""
    for skill in ("create-issue", "implement-feature", "fix-bug"):
        c = skill_corpus(skill)
        assert "designArtifact" in c, f"{skill} must gate the artifact step on the designArtifact config key"
        assert "is_enabled_for" in c or "is-enabled" in c


def test_telemetry_embed_documented_in_wf2_wf3():
    """AC4: the end-of-workflow artifact embeds run-record telemetry (read, not retyped)."""
    for skill in ("implement-feature", "fix-bug"):
        c = skill_corpus(skill)
        assert "telemetry" in c.lower()
        assert "run-record" in c.lower() or "run_record" in c.lower()


# --- designArtifact.sharedDoc reader (#174 shared-doc mode) ---

def test_shared_doc_returns_path_when_set(tmp_path):
    import adversarial_review_lib as arl
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps({"projects": [
        {"name": "p", "designArtifact": {"enabled": True, "workflows": ["implement-feature"],
                                          "sharedDoc": "docs/planning/program.md"}}]}))
    assert arl.design_artifact_shared_doc(str(ws), "p") == "docs/planning/program.md"


def test_shared_doc_none_when_unset(tmp_path):
    import adversarial_review_lib as arl
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps({"projects": [
        {"name": "p", "designArtifact": {"enabled": True, "workflows": ["implement-feature"]}}]}))
    assert arl.design_artifact_shared_doc(str(ws), "p") is None


def test_shared_doc_none_when_no_block(tmp_path):
    import adversarial_review_lib as arl
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps({"projects": [{"name": "p"}]}))
    assert arl.design_artifact_shared_doc(str(ws), "p") is None


def test_shared_doc_rejects_absolute_and_traversal(tmp_path):
    import adversarial_review_lib as arl
    for bad in ("/etc/evil.md", "../../escape.md", "docs/../../x.md"):
        ws = tmp_path / "ws.json"
        ws.write_text(json.dumps({"projects": [
            {"name": "p", "designArtifact": {"sharedDoc": bad}}]}))
        assert arl.design_artifact_shared_doc(str(ws), "p") is None, bad


def test_shared_doc_fail_safe_on_malformed(tmp_path):
    import adversarial_review_lib as arl
    ws = tmp_path / "ws.json"
    ws.write_text("{not json")
    assert arl.design_artifact_shared_doc(str(ws), "p") is None
    assert arl.design_artifact_shared_doc(str(tmp_path / "nope.json"), "p") is None


# --- designArtifact.style reader (#199 opt-in style; #344 full template vocabulary
#     + absent-vs-invalid semantics: absent→design silently, unreadable→design+warn,
#     valid→verbatim, invalid→plain+warn) ---

def test_style_roadmap_when_set(tmp_path):
    import adversarial_review_lib as arl
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps({"projects": [
        {"name": "p", "designArtifact": {"enabled": True, "style": "roadmap"}}]}))
    assert arl.design_artifact_style(str(ws), "p") == "roadmap"


def test_style_absent_key_defaults_design_silently(tmp_path, capsys):
    """#344: project exists, designArtifact block present but no style key
    → 'design' (documented default for design artifacts), NO stderr warning."""
    import adversarial_review_lib as arl
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps({"projects": [
        {"name": "p", "designArtifact": {"enabled": True}}]}))
    assert arl.design_artifact_style(str(ws), "p") == "design"
    assert capsys.readouterr().err == ""


def test_style_no_designartifact_block_defaults_design_silently(tmp_path, capsys):
    """#344: project exists with no designArtifact block at all → 'design', silent."""
    import adversarial_review_lib as arl
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps({"projects": [{"name": "p"}]}))
    assert arl.design_artifact_style(str(ws), "p") == "design"
    assert capsys.readouterr().err == ""


def test_style_missing_file_defaults_design_with_warning(tmp_path, capsys):
    """#344: unreadable config → 'design' PLUS a stderr warning (operational failure visible)."""
    import adversarial_review_lib as arl
    assert arl.design_artifact_style(str(tmp_path / "nope.json"), "p") == "design"
    assert capsys.readouterr().err.strip() != ""


def test_style_malformed_json_defaults_design_with_warning(tmp_path, capsys):
    """#344: JSON parse error → 'design' PLUS a stderr warning."""
    import adversarial_review_lib as arl
    ws = tmp_path / "ws.json"
    ws.write_text("{not json")
    assert arl.design_artifact_style(str(ws), "p") == "design"
    assert capsys.readouterr().err.strip() != ""


def test_style_project_not_found_defaults_design_with_warning(tmp_path, capsys):
    """#344: read OK but the project entry is absent → 'design' PLUS a stderr warning."""
    import adversarial_review_lib as arl
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps({"projects": [{"name": "other"}]}))
    assert arl.design_artifact_style(str(ws), "p") == "design"
    assert capsys.readouterr().err.strip() != ""


@pytest.mark.parametrize(
    "style", ["plain", "roadmap", "report", "design", "dashboard", "review", "spec"])
def test_style_valid_values_round_trip(tmp_path, capsys, style):
    """#344: every render_artifact template name is honored verbatim, no warning."""
    import adversarial_review_lib as arl
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps({"projects": [
        {"name": "p", "designArtifact": {"style": style}}]}))
    assert arl.design_artifact_style(str(ws), "p") == style
    assert capsys.readouterr().err == ""


def test_style_invalid_value_falls_back_plain_with_warning(tmp_path, capsys):
    """#344: a present-but-invalid style → 'plain' (conservative fail-safe) PLUS a
    stderr warning naming the rejected value."""
    import adversarial_review_lib as arl
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps({"projects": [
        {"name": "p", "designArtifact": {"style": "sparkle"}}]}))
    assert arl.design_artifact_style(str(ws), "p") == "plain"
    assert "sparkle" in capsys.readouterr().err


def test_style_fallback_tuple_matches_render_artifact_templates():
    """#344 drift guard: the literal fallback vocabulary used when render_artifact
    can't be imported == the live render_artifact._TEMPLATES registry keys."""
    import adversarial_review_lib as arl
    import render_artifact
    assert arl._FALLBACK_TEMPLATE_STYLES == tuple(render_artifact._TEMPLATES)


def test_style_cross_module_import_works_via_subprocess(tmp_path):
    """#344: the real `from render_artifact import _TEMPLATES` resolves in the hooks'
    own execution context (python3 with 'hooks' on sys.path, cwd=repo root)."""
    repo_root = Path(__file__).resolve().parents[2]
    ws = tmp_path / "ws.json"
    ws.write_text(json.dumps({"projects": [
        {"name": "p", "designArtifact": {"style": "report"}}]}))
    code = (
        "import sys; sys.path.insert(0, 'hooks'); "
        "from adversarial_review_lib import design_artifact_style; "
        f"print(design_artifact_style({str(ws)!r}, 'p'))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(repo_root),
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "report", out.stderr


def test_style_wired_into_wf2():
    """WF2 artifact step must read the configured style and pass it to render_artifact."""
    c = skill_corpus("implement-feature")
    assert "design_artifact_style" in c
    assert "--style" in c


# --- shared-doc mode wired into WF1/WF2/WF3 prose (#174) ---

def test_shared_doc_mode_documented_in_all_three_skills():
    """Multi-issue/campaign model: the artifact step must branch on a configured
    shared rolling doc (one doc updated per slot, like this session) vs per-issue."""
    for skill in ("create-issue", "implement-feature", "fix-bug"):
        c = skill_corpus(skill)
        assert "sharedDoc" in c, f"{skill} must document the sharedDoc shared-rolling-doc mode"
        assert "design_artifact_shared_doc" in c, f"{skill} must read the shared-doc config"
        assert "per-issue" in c.lower(), f"{skill} must state the per-issue default"


def test_shared_doc_requires_docs_md_target(tmp_path):
    """Codex Medium: sharedDoc must be a docs/*.md path — an arbitrary tracked
    file (README.md, a source file) must NOT be an accepted render target."""
    import adversarial_review_lib as arl
    for bad in ("README.md", ".github/workflows/ci.yml", "hooks/x.py", "docs/planning/x.html", "planning/x.md"):
        ws = tmp_path / "ws.json"
        ws.write_text(json.dumps({"projects": [{"name": "p", "designArtifact": {"sharedDoc": bad}}]}))
        assert arl.design_artifact_shared_doc(str(ws), "p") is None, bad
    ws = tmp_path / "ok.json"
    ws.write_text(json.dumps({"projects": [{"name": "p", "designArtifact": {"sharedDoc": "docs/planning/prog.md"}}]}))
    assert arl.design_artifact_shared_doc(str(ws), "p") == "docs/planning/prog.md"


def test_setup_asks_about_design_artifact_and_shared_doc():
    """AC5 UX (owner-requested): setup must offer the design-artifact lifecycle AND
    the per-issue-vs-shared-doc choice, mirroring the 2c/2d/2f/2g opt-in steps."""
    c = skill_corpus("setup")
    assert "Step 2h" in c, "setup must have a Step 2h for the design-artifact lifecycle"
    assert "designArtifact" in c
    assert "sharedDoc" in c
    assert "per-issue" in c.lower() and "shared-doc" in c.lower()
    assert "render_artifact.py" in c


def test_style_non_list_projects_never_raises(tmp_path, capsys):
    # Step 11 adversarial diff review: {"projects": 1} must not raise (never-raises
    # contract) — it is an unreadable-shape config: design + stderr warning.
    import adversarial_review_lib as arl
    ws = tmp_path / "ws.json"
    ws.write_text('{"projects": 1}')
    assert arl.design_artifact_style(str(ws), "p") == "design"
    assert "non-list" in capsys.readouterr().err
    assert arl.design_artifact_shared_doc(str(ws), "p") is None
