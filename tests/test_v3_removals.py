"""v3.0.0 removal guard (#161): the six workflows deprecated at #160
(WF4 refactor, WF7 update-docs, WF8 update-deps, WF9 security-audit,
WF10 optimize-perf, WF12 create-tests) are REMOVED — and stay removed.

Guards against accidental resurrection (a bad merge, a stale-cache copy landing
back in the tree) and against stale references: an active skill pointing a user
at a removed skill would 404 at invocation time. docs/ may still name the old
skills (the upgrade guide's replacement table, changelog history) — only the
active skill corpus is held reference-free.
"""
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
CODEX_SKILLS = REPO_ROOT / "plugins" / "rawgentic" / "skills"

REMOVED = ("create-tests", "optimize-perf", "refactor",
           "security-audit", "update-deps", "update-docs")


@pytest.mark.parametrize("skill", REMOVED)
def test_stub_skill_dir_removed(skill):
    assert not (SKILLS_DIR / skill).exists(), f"skills/{skill} must be gone at v3.0.0"


@pytest.mark.parametrize("skill", REMOVED)
def test_codex_mirror_skill_dir_removed(skill):
    assert not (CODEX_SKILLS / skill).exists(), \
        f"plugins/rawgentic/skills/{skill} must be gone at v3.0.0"


@pytest.mark.parametrize("skill", REMOVED)
def test_stub_eval_workspace_removed(skill):
    assert not (SKILLS_DIR / f"{skill}-workspace").exists(), \
        f"skills/{skill}-workspace (eval artifacts of a removed skill) must be gone"


def test_marketplace_whitelist_has_no_removed_skills():
    mp = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    listed = {Path(rel).name for rel in mp["plugins"][0]["skills"]}
    assert not listed & set(REMOVED), f"whitelist still carries: {listed & set(REMOVED)}"
    assert len(listed) == 13


def test_descriptions_no_longer_mention_stubs():
    for rel in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
                "plugins/rawgentic/.codex-plugin/plugin.json"):
        text = (REPO_ROOT / rel).read_text()
        assert "deprecated stub" not in text.lower(), f"{rel} still advertises stubs"


def test_active_skills_do_not_reference_removed_skills():
    """No active skill may direct a user to a removed invocation."""
    pat = re.compile(r"rawgentic:(?:%s)\b" % "|".join(REMOVED))
    offenders = []
    for md in SKILLS_DIR.rglob("*.md"):
        if "-workspace" in md.parts[len(SKILLS_DIR.parts)]:
            continue
        hits = pat.findall(md.read_text(encoding="utf-8", errors="replace"))
        if hits:
            offenders.append((str(md.relative_to(REPO_ROOT)), sorted(set(hits))))
    assert not offenders, f"active skills still reference removed skills: {offenders}"


def test_upgrade_guide_exists_with_replacement_table():
    guide = REPO_ROOT / "docs" / "upgrade-3.0.md"
    assert guide.exists(), "AC1: docs/upgrade-3.0.md must ship with v3.0.0"
    text = guide.read_text()
    for skill in REMOVED:
        assert skill in text, f"upgrade guide must cover removed skill {skill}"
    assert "claude plugin remove" in text and "claude plugin install" in text, \
        "AC1: cache-refresh steps required"
    assert "implement-feature" in text  # the dominant replacement
