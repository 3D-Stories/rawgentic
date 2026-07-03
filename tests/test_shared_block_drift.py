"""Drift guard for shared SKILL.md blocks single-sourced under shared/blocks/.

Marketplace plugins cannot share a file across skills at runtime (path traversal is
blocked; ${CLAUDE_PLUGIN_ROOT} does not expand in SKILL.md body), so duplicated prose
is single-sourced by keeping the source in shared/blocks/ and generating each skill's
inline copy via scripts/sync_shared_blocks.py. This guard fails if any copy drifts from
its source — the exact failure that let config-loading silently diverge (an em-dash) before.
"""
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "sync_shared_blocks.py"
SKILLS = REPO_ROOT / "skills"
SHARED = REPO_ROOT / "shared" / "blocks"


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


def _run_in(root: Path, *args):
    """Run a COPY of the sync script rooted at `root` (it resolves paths from its
    own __file__). Isolates any file-writing sync from the real working tree."""
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "sync_shared_blocks.py"), *args],
        capture_output=True, text=True,
    )


def _config_block(skill: str) -> str:
    text = (SKILLS / skill / "SKILL.md").read_text()
    return text.split("<config-loading>", 1)[1].split("</config-loading>", 1)[0]


def test_shared_sources_exist():
    assert (SHARED / "config-loading.md").exists()


def test_no_shared_block_drift():
    """The main guard: every synced copy must equal its source."""
    r = _run("--check")
    assert r.returncode == 0, f"drift detected:\n{r.stderr}"


def test_sync_is_idempotent(tmp_path):
    """Running sync when already in sync changes nothing.

    Runs in a COPY of the repo, not the working tree: a bare `sync` WRITES files,
    so on a tree with in-progress drift it would silently clobber the edit. The
    sandbox keeps this guard from ever mutating a contributor's real checkout.
    """
    for d in ("scripts", "shared", "skills"):
        shutil.copytree(REPO_ROOT / d, tmp_path / d)
    r = _run_in(tmp_path)
    assert r.returncode == 0
    assert "nothing to sync" in r.stdout.lower(), r.stdout
    # And it truly touched nothing: the copied skills equal the originals.
    for skill in ("config-loading.md",):  # source block itself unchanged
        assert (tmp_path / "shared" / "blocks" / skill).read_text() == (SHARED / skill).read_text()


def test_create_issue_intentionally_not_synced():
    """create-issue (WF1) keeps its deliberately-slim bespoke block (PR #104)."""
    block = _config_block("create-issue")
    assert block.rstrip("\n") != (SHARED / "config-loading.md").read_text().rstrip("\n")
