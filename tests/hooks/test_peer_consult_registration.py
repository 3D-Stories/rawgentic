"""WF13 peer-consult registration drift guard (mirrors WF5's)."""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SKILLS = REPO / "skills"


def test_skill_dir_and_frontmatter_exist():
    skill = SKILLS / "peer-consult" / "SKILL.md"
    assert skill.exists()
    text = skill.read_text()
    assert "name: rawgentic:peer-consult" in text
    assert "<config-loading>" in text
    assert "<completion-gate>" in text
    assert "not a reviewer" in text.lower()  # peer framing


def test_marketplace_registers_skill():
    mp = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    skills = mp["plugins"][0]["skills"]
    assert "./skills/peer-consult" in skills


def test_evals_stub_exists():
    assert (SKILLS / "peer-consult" / "evals.json").exists()


def test_wf2_step3_integration_present():
    text = (SKILLS / "implement-feature" / "SKILL.md").read_text()
    assert "--key peerConsult" in text          # gate check
    assert "blind" in text.lower()
    assert "empty-proposal marker" in text       # timeout handling
    assert "before reading" in text.lower() or "must not read" in text.lower()
