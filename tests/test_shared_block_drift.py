"""Drift guard for shared SKILL.md blocks single-sourced under shared/blocks/.

Marketplace plugins cannot share a file across skills at runtime (path traversal is
blocked; ${CLAUDE_PLUGIN_ROOT} does not expand in SKILL.md body), so duplicated prose
is single-sourced by keeping the source in shared/blocks/ and generating each skill's
inline copy via scripts/sync_shared_blocks.py. This guard fails if any copy drifts from
its source — the exact failure that let config-loading silently diverge (an em-dash) before.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "sync_shared_blocks.py"
SKILLS = REPO_ROOT / "skills"
SHARED = REPO_ROOT / "shared" / "blocks"

CLEANUP_MARKER = "[Headless cleanup]"


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def _config_block(skill: str) -> str:
    text = (SKILLS / skill / "SKILL.md").read_text()
    return text.split("<config-loading>", 1)[1].split("</config-loading>", 1)[0]


def test_shared_sources_exist():
    assert (SHARED / "config-loading.standard.md").exists()
    assert (SHARED / "config-loading.headless.md").exists()


def test_no_shared_block_drift():
    """The main guard: every synced copy must equal its source."""
    r = _run("--check")
    assert r.returncode == 0, f"drift detected:\n{r.stderr}"


def test_sync_is_idempotent():
    """Running sync when already in sync changes nothing."""
    r = _run()
    assert r.returncode == 0
    assert "nothing to sync" in r.stdout.lower(), r.stdout


def test_headless_skills_carry_cleanup_paragraph():
    """implement-feature + fix-bug use the headless config-loading variant."""
    for skill in ("implement-feature", "fix-bug"):
        assert CLEANUP_MARKER in _config_block(skill), f"{skill} should use the headless variant"


def test_standard_skills_omit_cleanup_paragraph():
    """The 8 standard skills use the standard variant (proves the split is real, not vacuous)."""
    for skill in ("refactor", "incident", "security-audit", "update-deps"):
        assert CLEANUP_MARKER not in _config_block(skill), f"{skill} should use the standard variant"


def test_create_issue_intentionally_not_synced():
    """create-issue (WF1) keeps its deliberately-slim bespoke block (PR #104)."""
    block = _config_block("create-issue")
    assert block.rstrip("\n") != (SHARED / "config-loading.standard.md").read_text().rstrip("\n")
    assert block.rstrip("\n") != (SHARED / "config-loading.headless.md").read_text().rstrip("\n")
