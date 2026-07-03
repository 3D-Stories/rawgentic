"""Drift guards for the `interview` skill (lightweight planning skill).

`interview` is NOT a config-driven SDLC workflow — it deliberately has no
`<config-loading>` block — so it must NOT be counted among the 12 config-driven
workflow skills, and it must NOT trip the config-loading canary in
`tests/hooks/test_headless.py`.

It registers in the marketplace whitelist (alphabetical placement) and is
accounted for as a separate "planning skill" in the count strings.
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
SKILL = SKILLS_DIR / "interview" / "SKILL.md"


def test_skill_dir_and_frontmatter_exist():
    assert SKILL.exists(), "skills/interview/SKILL.md missing"
    text = SKILL.read_text()
    assert "name: rawgentic:interview" in text
    assert "description:" in text
    assert "argument-hint:" in text


def test_skill_is_lightweight_no_config_loading():
    """interview must stay lightweight — no <config-loading>, so the 12-count canary holds."""
    text = SKILL.read_text()
    assert "<config-loading>" not in text


def test_skill_contains_verbatim_core_prompt():
    text = SKILL.read_text()
    assert "interview me about what we're trying to build" in text
    assert "who it is and isn't for" in text
    assert "work through any key decisions together" in text
    assert "summarize it back to me as an implementation spec" in text


def test_skill_offers_to_write_spec_file():
    """User decision: after summarizing, offer to persist the spec to a file."""
    text = SKILL.read_text().lower()
    assert "offer to save" in text or "offer to write" in text
    assert "docs/" in text


def test_marketplace_registers_skill_alphabetically():
    mp = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    skills = mp["plugins"][0]["skills"]
    assert "./skills/interview" in skills
    # alphabetical placement: incident < interview < new-project
    assert skills.index("./skills/interview") == skills.index("./skills/incident") + 1


def test_descriptions_account_for_interview_as_planning_skill():
    plugin = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
    mp = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    for desc in (plugin["description"], mp["plugins"][0]["description"]):
        # interview is a separate planning skill, NOT counted among the SDLC workflows
        assert "12 SDLC workflow skills" in desc
        assert "planning skill" in desc


def test_no_eval_workspace_skill_md():
    """If an interview-workspace dir ever appears, it must not contain a SKILL.md
    (the marketplace validator rejects duplicate SKILL.md names)."""
    ws = SKILLS_DIR / "interview-workspace"
    assert not (ws / "SKILL.md").exists()
