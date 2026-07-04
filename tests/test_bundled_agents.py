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
    """Parse the simple `key: value` YAML frontmatter block.

    ponytail: single-line values only — a folded/multi-line YAML value would be
    dropped or mis-keyed; the shipped definitions use none."""
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
    # == "inherit" is strictly stronger than any not-haiku check, so it is the
    # single assertion; the body-level never-Haiku prose is pinned separately.
    assert fm.get("model") == "inherit", f"{path.name} model must be inherit (routing overrides per-invocation)"


def test_implementer_is_worktree_isolated():
    fm = _frontmatter(IMPLEMENTER)
    assert fm.get("isolation") == "worktree", (
        "implementer mutates the tree — parallel dispatch requires worktree isolation"
    )


@pytest.mark.parametrize("path", [IMPLEMENTER, REVIEWER], ids=["implementer", "reviewer"])
def test_body_states_never_haiku_contract(path):
    body = path.read_text(encoding="utf-8")
    assert "never" in body.lower() and "haiku" in body.lower(), (
        f"the never-Haiku guarantee must be stated in {path.name} itself"
    )


def test_reviewer_tools_are_read_heavy():
    """The reviewer reads and reports; it must not carry file-editing tools.

    Bash stays in the list (git log/show/diff, running the suite), so this list
    alone does not prove read-only — the definition's prose therefore claims
    "no file-editing tools", not "read-only", and instructs Bash be used for
    inspection only. This guard pins the tool list; the prose pin is below."""
    fm = _frontmatter(REVIEWER)
    tools = [t.strip() for t in fm.get("tools", "").split(",") if t.strip()]
    assert tools, "reviewer must declare an explicit read-heavy tools list"
    for forbidden in ("Write", "Edit", "NotebookEdit"):
        assert forbidden not in tools, f"reviewer tools must not include {forbidden}"
    # Bash is REQUIRED, not merely tolerated — the definition's contract relies
    # on it (git log/show/diff, running the suite); its write capability is
    # bounded by prose (pinned below), not by the tool layer.
    for required in ("Read", "Grep", "Glob", "Bash"):
        assert required in tools, f"reviewer tools must include {required}"


def test_reviewer_is_not_isolated():
    """Read-only agent — a worktree copy would only add setup cost."""
    fm = _frontmatter(REVIEWER)
    assert "isolation" not in fm


def test_wf2_references_both_agent_types():
    """AC2: WF2 dispatch prose references the shipped types (namespaced form).

    Counts, not mere presence: the <model-routing-resolve> inventory alone must
    not satisfy this — the per-step dispatch sites must reference the types too
    (reviewer: inventory + Step 4 judges + Step 8a + Step 11 = >= 4;
    implementer: inventory + Step 8 delegation + whole-issue build = >= 3)."""
    corpus = skill_corpus("implement-feature")
    assert corpus.count("rawgentic:rawgentic-implementer") >= 3
    assert corpus.count("rawgentic:rawgentic-reviewer") >= 4


def test_reviewer_prose_limits_bash_to_inspection():
    """The Bash escape hatch is real; the definition must bound it explicitly."""
    body = REVIEWER.read_text(encoding="utf-8")
    assert "no file-editing tools" in body.lower()
    assert "read-only inspection" in body.lower()
    assert "never to mutate" in body.lower()


def test_wf2_step8_reconciles_worktree_commit():
    """A worktree commit does not land on the feature branch by itself; Step 8
    must collect it and assert the branch advanced — otherwise the diff-scoped
    gates (8a/11/secret scan) would run over an empty diff and pass vacuously."""
    corpus = skill_corpus("implement-feature")
    # per-task path (Step 8 item 3)
    assert "cherry-pick or fast-forward" in corpus
    assert "branch actually advanced" in corpus
    # whole-issue path (Step 8 item 4b + reference): collect BEFORE validate,
    # else receipt Rule 4 diffs an un-advanced HEAD and rejects every build
    assert "Collect BEFORE validating" in corpus
    assert "Collect before validation" in corpus  # references/whole-issue-delegation.md section
    impl = IMPLEMENTER.read_text(encoding="utf-8")
    assert "does NOT land" in impl and "commit SHA" in impl


def test_wf2_documents_worktree_fallback():
    """AC4: the #136 probe is consulted with graceful fallback when worktrees
    are unavailable — dispatch proceeds non-isolated/serial rather than failing."""
    corpus = skill_corpus("implement-feature")
    assert "probe-parallelism" in corpus
    low = corpus.lower()
    assert "fallback" in low and "serial-only" in low


def test_wf2_notes_85_config_gated_follow_up():
    """AC5: with isolation shipped, #85's concurrent Step 8 is a config-gated
    follow-up — the prose must say so rather than still calling it unconditional."""
    corpus = skill_corpus("implement-feature")
    assert "#85" in corpus
    assert "config-gated" in corpus
