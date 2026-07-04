"""Codex plugin packaging drift guards.

Phase 1 makes rawgentic installable from a private Codex marketplace while
keeping the existing Claude package as the source of truth for skill coverage.
"""

import json
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_PLUGIN = REPO_ROOT / ".claude-plugin" / "plugin.json"
CLAUDE_MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
CODEX_PLUGIN_ROOT = REPO_ROOT / "plugins" / "rawgentic"
CODEX_PLUGIN = CODEX_PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
CODEX_MARKETPLACE = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"
CODEX_SKILLS = CODEX_PLUGIN_ROOT / "skills"
README = REPO_ROOT / "README.md"


def _load(path: Path):
    return json.loads(path.read_text())


def _claude_skill_paths():
    marketplace = _load(CLAUDE_MARKETPLACE)
    return marketplace["plugins"][0]["skills"]


def test_codex_plugin_manifest_exists_and_is_public_ready():
    manifest = _load(CODEX_PLUGIN)

    assert manifest["name"] == "rawgentic"
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["skills"] == "./skills/"
    assert manifest["author"]["name"] == "3D-Stories"
    assert manifest["repository"] == "https://github.com/3D-Stories/rawgentic"
    assert manifest["license"] == "MIT"

    interface = manifest["interface"]
    assert interface["displayName"] == "Rawgentic"
    assert interface["developerName"] == "3D-Stories"
    assert interface["category"] == "Productivity"
    assert interface["websiteURL"].startswith("https://")
    assert interface["shortDescription"]
    assert interface["longDescription"]


def test_codex_plugin_version_matches_claude_manifest():
    assert _load(CODEX_PLUGIN)["version"] == _load(CLAUDE_PLUGIN)["version"]


def test_codex_private_marketplace_points_at_repo_plugin_root():
    marketplace = _load(CODEX_MARKETPLACE)

    assert marketplace["name"] == "rawgentic-private"
    assert marketplace["interface"]["displayName"] == "Rawgentic Private"

    plugins = marketplace["plugins"]
    assert len([p for p in plugins if p["name"] == "rawgentic"]) == 1
    entry = next(p for p in plugins if p["name"] == "rawgentic")
    assert entry["source"] == {"source": "local", "path": "./plugins/rawgentic"}
    assert entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    assert entry["category"] == "Productivity"


def test_codex_manifest_exposes_same_runtime_skills_as_claude_marketplace():
    manifest = _load(CODEX_PLUGIN)
    assert manifest["skills"] == "./skills/"

    for rel in _claude_skill_paths():
        source = REPO_ROOT / rel
        codex_skill = CODEX_SKILLS / Path(rel).name
        assert source.joinpath("SKILL.md").exists(), f"missing {rel}/SKILL.md"
        assert codex_skill.is_symlink(), f"{codex_skill} should symlink to shared skill source"
        assert codex_skill.resolve() == source.resolve()


def test_codex_package_does_not_claim_hook_parity_yet():
    manifest = _load(CODEX_PLUGIN)
    assert "hooks" not in manifest
    assert "mcpServers" not in manifest
    assert "apps" not in manifest


def test_readme_documents_private_codex_marketplace_install():
    readme = README.read_text()
    assert "Private Codex marketplace" in readme
    assert "codex plugin marketplace add ." in readme
    assert "$rawgentic:create-issue" in readme
    assert "Codex hook support is planned for Phase 2" in readme


def test_codex_skill_package_excludes_eval_workspaces():
    assert CODEX_SKILLS.exists()
    packaged = {p.name for p in CODEX_SKILLS.iterdir()}
    assert packaged == {Path(rel).name for rel in _claude_skill_paths()}
    assert all(not name.endswith("-workspace") for name in packaged)
