# tests/hooks/test_model_routing_dispatch.py
"""Drift guard: every known subagent dispatch site carries a model-routing role
annotation, so new dispatch sites cannot silently bypass routing.

Pins assert over the skill CORPUS (SKILL.md + references/*.md, via
tests.corpus.skill_corpus) so the #158 prose restructure can move dispatch
prose into references/ without weakening the guard.
"""
from tests.corpus import skill_corpus

# (skill, role, count of annotations expected in that skill's corpus)
EXPECTED = [
    ("implement-feature", "analysis", 2),
    ("implement-feature", "review", 3),
    ("implement-feature", "implementation", 1),
    ("fix-bug", "review", 1),
    ("refactor", "review", 1),
]


def _count(skill: str, role: str) -> int:
    return skill_corpus(skill).count(f"<!-- model-routing: role={role} -->")


def test_dispatch_sites_annotated():
    for skill, role, want in EXPECTED:
        got = _count(skill, role)
        assert got == want, f"{skill} role={role}: expected {want} annotations, got {got}"


def test_resolve_invoked_in_preambles():
    for skill in ("implement-feature", "fix-bug", "refactor"):
        text = skill_corpus(skill)
        assert "model_routing_lib.py resolve" in text, f"{skill} missing routing resolve call"


def test_step8_delegation_documents_clean_state_boundary():
    text = skill_corpus("implement-feature")
    # the delegation sub-step must document pre-task state capture + restore-before-retry
    assert "clean-state boundary" in text
    assert "git status --porcelain" in text
    assert "restore" in text.lower()
    # #132: a struggling down-routed task escalates — retry at the CEILING model
    # (restore-first), not a flat inline retry.
    assert "retry that task once at the CEILING model" in text


def test_step8_documents_ceiling_downrouting():
    """#132: implementation model is a per-task ceiling, selected via select_impl_model."""
    text = skill_corpus("implement-feature")
    assert "CEILING, not a blanket assignment" in text
    assert "select_impl_model" in text
    # never-Haiku guarantee is stated at the dispatch site (covers inherit→session-model):
    # pin the specific guard sentence, not just the two words appearing somewhere.
    assert "Never dispatch an implementation subagent with `model: haiku`" in text
    # per-task audit log line
    assert "impl task <id>: model" in text


def test_preambles_resolve_effort():
    """#154 Task 2: both skills also resolve the role's effort tier via --effort."""
    for skill in ("implement-feature", "fix-bug"):
        text = skill_corpus(skill)
        assert "--effort" in text, f"{skill} missing --effort resolution"


def test_effort_dual_path_documented():
    """#154 Task 2: the Agent tool has no per-invocation effort parameter, so effort
    is carried dual-path (pass where supported, always log). Pin the literals."""
    for skill in ("implement-feature", "fix-bug"):
        text = skill_corpus(skill)
        assert "dual-path" in text, f"{skill} missing dual-path marker"
        assert "no per-invocation effort parameter" in text, f"{skill} missing effort-parameter marker"


def test_impl_audit_line_carries_effort():
    """#154 Task 2: Step 8's per-task audit line extends with the resolved effort."""
    text = skill_corpus("implement-feature")
    assert "impl task <id>: model" in text
    assert "effort <effort|none>" in text
