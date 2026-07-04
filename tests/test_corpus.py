"""Tests for the skill_corpus() drift-guard helper (#157).

The M2 restructure (#158) moves SKILL.md prose into references/ — prose-pinning
guards must assert over the whole corpus (SKILL.md + references/*.md) so a move
never silently un-pins a guard.
"""

from pathlib import Path

import pytest

from tests.corpus import SKILLS_DIR, skill_corpus

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_corpus_includes_skill_md_body():
    corpus = skill_corpus("implement-feature")
    body = (SKILLS_DIR / "implement-feature" / "SKILL.md").read_text()
    assert body in corpus


def test_corpus_includes_every_reference_file():
    refs = sorted((SKILLS_DIR / "implement-feature" / "references").glob("*.md"))
    assert refs, "implement-feature must have references/ for this test to be meaningful"
    corpus = skill_corpus("implement-feature")
    for ref in refs:
        assert ref.read_text() in corpus, f"{ref.name} missing from corpus"


def test_corpus_without_references_dir_is_just_skill_md():
    # switch has no references/ — corpus degrades to SKILL.md alone
    assert not (SKILLS_DIR / "switch" / "references").is_dir()
    assert skill_corpus("switch") == (SKILLS_DIR / "switch" / "SKILL.md").read_text()


def test_corpus_missing_skill_raises():
    with pytest.raises(FileNotFoundError):
        skill_corpus("no-such-skill")
