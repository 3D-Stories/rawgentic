"""Drift guards for the plugin-bundled subagent definitions (#164).

The plugin ships agents/rawgentic-implementer.md and agents/rawgentic-reviewer.md
(auto-discovered from the plugin-root agents/ directory; the installed agent type
is namespaced "rawgentic:<name>"). Routing stays per-project config, so the
definitions declare `model: inherit` and WF2 passes the resolved role model
per-invocation — the Agent tool's model parameter overrides frontmatter (documented
resolution order: env var > per-invocation param > frontmatter > session model).

These pins keep the definitions' safety properties from silently eroding:
never-Haiku, worktree isolation on the implementer, read-only tooling on the
reviewer, and WF2 actually referencing the shipped types.
"""
import re
from pathlib import Path

import pytest

from tests.corpus import skill_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / "agents"
IMPLEMENTER = AGENTS_DIR / "rawgentic-implementer.md"
REVIEWER = AGENTS_DIR / "rawgentic-reviewer.md"


def _frontmatter(path: Path) -> dict:
    """Parse the simple `key: value` YAML frontmatter block (no nesting used)."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, f"{path.name} missing YAML frontmatter"
    fields = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip()
    return fields


@pytest.mark.parametrize("path", [IMPLEMENTER, REVIEWER], ids=["implementer", "reviewer"])
def test_definition_exists_with_name_and_description(path):
    assert path.exists(), f"plugin must ship {path.relative_to(REPO_ROOT)}"
    fm = _frontmatter(path)
    assert fm.get("name") == path.stem
    assert fm.get("description"), f"{path.name} needs a description (drives dispatch selection)"


@pytest.mark.parametrize("path", [IMPLEMENTER, REVIEWER], ids=["implementer", "reviewer"])
def test_model_is_inherit_never_haiku(path):
    """Routing is per-project config a static file can't read: the definition
    declares inherit and the per-invocation model param carries the routed value.
    A haiku frontmatter model would silently route coding/review to Haiku."""
    fm = _frontmatter(path)
    assert fm.get("model") == "inherit", f"{path.name} model must be inherit (routing overrides per-invocation)"
    assert "haiku" not in fm.get("model", ""), f"{path.name} must never pin haiku"


def test_implementer_is_worktree_isolated():
    fm = _frontmatter(IMPLEMENTER)
    assert fm.get("isolation") == "worktree", (
        "implementer mutates the tree — parallel dispatch requires worktree isolation"
    )


def test_implementer_body_states_never_haiku_contract():
    body = IMPLEMENTER.read_text(encoding="utf-8")
    assert "never" in body.lower() and "haiku" in body.lower(), (
        "the never-Haiku guarantee must be stated in the definition itself"
    )


def test_reviewer_tools_are_read_heavy():
    """The reviewer reads and reports; it must not carry write tools."""
    fm = _frontmatter(REVIEWER)
    tools = [t.strip() for t in fm.get("tools", "").split(",") if t.strip()]
    assert tools, "reviewer must declare an explicit read-heavy tools list"
    for forbidden in ("Write", "Edit", "NotebookEdit"):
        assert forbidden not in tools, f"reviewer tools must not include {forbidden}"
    for required in ("Read", "Grep", "Glob"):
        assert required in tools, f"reviewer tools must include {required}"


def test_reviewer_is_not_isolated():
    """Read-only agent — a worktree copy would only add setup cost."""
    fm = _frontmatter(REVIEWER)
    assert "isolation" not in fm


def test_wf2_references_both_agent_types():
    """AC2: WF2 dispatch prose references the shipped types (namespaced form)."""
    corpus = skill_corpus("implement-feature")
    assert "rawgentic:rawgentic-implementer" in corpus
    assert "rawgentic:rawgentic-reviewer" in corpus


def test_wf2_documents_worktree_fallback():
    """AC4: the #136 probe is consulted with graceful fallback when worktrees
    are unavailable — dispatch proceeds non-isolated/serial rather than failing."""
    corpus = skill_corpus("implement-feature")
    assert "probe-parallelism" in corpus
    low = corpus.lower()
    assert "fallback" in low and "serial-only" in corpus


def test_wf2_notes_85_config_gated_follow_up():
    """AC5: with isolation shipped, #85's concurrent Step 8 is a config-gated
    follow-up — the prose must say so rather than still calling it unconditional."""
    corpus = skill_corpus("implement-feature")
    assert "#85" in corpus
    assert "config-gated" in corpus
