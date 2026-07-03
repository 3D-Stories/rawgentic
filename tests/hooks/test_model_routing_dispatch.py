# tests/hooks/test_model_routing_dispatch.py
"""Drift guard: every known subagent dispatch site carries a model-routing role
annotation, so new dispatch sites cannot silently bypass routing."""
from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent.parent / "skills"

# (file, role, count of annotations expected in that file)
EXPECTED = [
    ("implement-feature/SKILL.md", "analysis", 2),
    ("implement-feature/SKILL.md", "review", 3),
    ("implement-feature/SKILL.md", "implementation", 1),
    ("fix-bug/SKILL.md", "review", 1),
    ("refactor/SKILL.md", "review", 1),
]


def _count(path: Path, role: str) -> int:
    return path.read_text().count(f"<!-- model-routing: role={role} -->")


def test_dispatch_sites_annotated():
    for rel, role, want in EXPECTED:
        got = _count(SKILLS / rel, role)
        assert got == want, f"{rel} role={role}: expected {want} annotations, got {got}"


def test_resolve_invoked_in_preambles():
    for rel in ("implement-feature/SKILL.md", "fix-bug/SKILL.md", "refactor/SKILL.md"):
        text = (SKILLS / rel).read_text()
        assert "model_routing_lib.py resolve" in text, f"{rel} missing routing resolve call"


def test_step8_delegation_documents_clean_state_boundary():
    text = (SKILLS / "implement-feature" / "SKILL.md").read_text()
    # the delegation sub-step must document pre-task state capture + restore-before-retry
    assert "clean-state boundary" in text
    assert "git status --porcelain" in text
    assert "restore" in text.lower()
    assert "retries that task once inline" in text or "retry that task once inline" in text
