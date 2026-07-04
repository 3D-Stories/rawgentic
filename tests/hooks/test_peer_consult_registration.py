"""WF13 peer-consult registration drift guard (mirrors WF5's)."""
import json
import re
from pathlib import Path

from tests.corpus import skill_corpus

REPO = Path(__file__).resolve().parent.parent.parent
SKILLS = REPO / "skills"


def test_skill_dir_and_frontmatter_exist():
    skill = SKILLS / "peer-consult" / "SKILL.md"
    assert skill.exists()
    # LOCATION pin: frontmatter must be in SKILL.md itself (that's how the
    # skill registers); the prose blocks are content pins over the corpus.
    assert "name: rawgentic:peer-consult" in skill.read_text()
    corpus = skill_corpus("peer-consult")
    assert "<config-loading>" in corpus
    assert "<completion-gate>" in corpus
    assert "not a reviewer" in corpus.lower()  # peer framing


def test_marketplace_registers_skill():
    mp = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    skills = mp["plugins"][0]["skills"]
    assert "./skills/peer-consult" in skills


def test_evals_stub_exists():
    assert (SKILLS / "peer-consult" / "evals.json").exists()


def test_wf2_step3_integration_present():
    text = skill_corpus("implement-feature")
    assert "--key peerConsult" in text          # gate check
    assert "blind" in text.lower()
    assert "empty-proposal marker" in text       # timeout handling
    assert "before reading" in text.lower() or "must not read" in text.lower()


def test_setup_has_modelrouting_and_peerconsult_steps():
    text = skill_corpus("setup")
    assert "modelRouting" in text
    assert "peerConsult" in text
    # The Step 8 finalize write must apply all four pending fields in one
    # read-modify-write sentence, so no step's write clobbers another's field.
    match = re.search(
        r"Apply any pending per-project field changes.*?Write the file back once\.",
        text,
        re.DOTALL,
    )
    assert match, "Step 8 finalize read-modify-write sentence not found"
    finalize_sentence = match.group(0)
    for field in ("headlessEnabled", "adversarialReview", "modelRouting", "peerConsult"):
        assert field in finalize_sentence, f"{field!r} missing from finalize sentence"
