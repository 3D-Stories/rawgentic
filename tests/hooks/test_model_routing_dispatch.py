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
    # #132: a struggling down-routed task escalates — retry at the CEILING model
    # (restore-first), not a flat inline retry.
    assert "retry that task once at the CEILING model" in text


def test_step8_documents_ceiling_downrouting():
    """#132: implementation model is a per-task ceiling, selected via select_impl_model."""
    text = (SKILLS / "implement-feature" / "SKILL.md").read_text()
    assert "CEILING, not a blanket assignment" in text
    assert "select_impl_model" in text
    # never-Haiku guarantee is stated at the dispatch site (covers inherit→session-model):
    # pin the specific guard sentence, not just the two words appearing somewhere.
    assert "Never dispatch an implementation subagent with `model: haiku`" in text
    # per-task audit log line
    assert "impl task <id>: model" in text


def test_preambles_resolve_effort():
    """#154 Task 2: both skills also resolve the role's effort tier via --effort."""
    for rel in ("implement-feature/SKILL.md", "fix-bug/SKILL.md"):
        text = (SKILLS / rel).read_text()
        assert "--effort" in text, f"{rel} missing --effort resolution"


def test_effort_dual_path_documented():
    """#154 Task 2: the Agent tool has no per-invocation effort parameter, so effort
    is carried dual-path (pass where supported, always log). Pin the literals."""
    for rel in ("implement-feature/SKILL.md", "fix-bug/SKILL.md"):
        text = (SKILLS / rel).read_text()
        assert "dual-path" in text, f"{rel} missing dual-path marker"
        assert "no per-invocation effort parameter" in text, f"{rel} missing effort-parameter marker"


def test_impl_audit_line_carries_effort():
    """#154 Task 2: Step 8's per-task audit line extends with the resolved effort."""
    text = (SKILLS / "implement-feature" / "SKILL.md").read_text()
    assert "impl task <id>: model" in text
    assert "effort <effort|none>" in text
