# tests/hooks/test_model_routing_dispatch.py
"""Drift guard: every known subagent dispatch site carries a model-routing role
annotation, so new dispatch sites cannot silently bypass routing.

Pins assert over the skill CORPUS (SKILL.md + references/*.md, via
tests.corpus.skill_corpus) so the #158 prose restructure can move dispatch
prose into references/ without weakening the guard.
"""
from tests.corpus import skill_corpus

# (skill, role, count of annotations expected in that skill's corpus).
# M0b (#866): analysis routing retired (inline/harness gathers carry no marker);
# the review markers mark the review SITES (tests/hooks/test_wf_review_sites.py
# checks their content); the implementation marker heads the inline TDD loop.
EXPECTED = [
    ("implement-feature", "analysis", 0),
    ("implement-feature", "review", 3),
    ("implement-feature", "implementation", 1),
    ("fix-bug", "review", 1),
]


def _count(skill: str, role: str) -> int:
    return skill_corpus(skill).count(f"<!-- model-routing: role={role} -->")


def test_dispatch_sites_annotated():
    for skill, role, want in EXPECTED:
        got = _count(skill, role)
        assert got == want, f"{skill} role={role}: expected {want} annotations, got {got}"


def test_no_routing_cli_in_active_prose():
    # M0b (#866): per-phase model routing retired — the resolve CLI must not
    # re-enter either workflow corpus.
    for skill in ("implement-feature", "fix-bug"):
        text = skill_corpus(skill)
        assert "model_routing_lib.py resolve" not in text, (
            f"{skill}: the retired routing resolve call re-entered the corpus")
