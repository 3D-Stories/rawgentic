"""Drift guard: the HTML design-artifact lifecycle prose (#174) stays wired into
WF1/WF2/WF3, and the shared render helper stays referenced (pattern mirrors
tests/hooks/test_model_routing_dispatch.py). Corpus-based (SKILL.md + references/)
so the #158/#159 spine splits don't hide the prose.
"""
from tests.corpus import skill_corpus


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
