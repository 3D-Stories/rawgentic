"""Drift guards for WF2 interactive ERROR protocol + ai-error label
create-if-missing + Step 13 CI-unavailable visible non-gate (issue #232).

All three gaps were hit by a live interactive saystory run where the only exit
from a blocker was an undefined/headless-only ERROR path.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS = REPO_ROOT / "skills" / "implement-feature"


def _section(text: str, header: str, nxt: str | None) -> str:
    start = text.index(header)
    end = text.index(nxt, start) if nxt else len(text)
    return text[start:end]


def test_interactive_error_protocol_defined():
    # AC1: an interactive ERROR protocol must exist so the /goal escape ("blocker
    # posted via the ERROR protocol") is satisfiable WITHOUT the headless-only label.
    skill = (SKILLS / "SKILL.md").read_text()
    assert "<error-protocol>" in skill, "no <error-protocol> block in SKILL.md"
    block = _section(skill, "<error-protocol>", "</error-protocol>")
    # normalize hard-wrapped prose to single spaces so phrase checks aren't
    # defeated by a line break landing mid-phrase.
    low = " ".join(block.lower().split())
    assert "interactive" in low
    assert "blocker" in low and "comment" in low
    # the interactive path must NOT require the headless ai-error label
    assert "no label" in low or "without the" in low or "not a requirement" in low
    assert "#232" in block


def test_headless_error_protocol_creates_ai_error_label():
    # AC2 (confirmed): the ERROR protocol must CREATE rawgentic:ai-error before
    # adding it — the first error in a repo otherwise fails ("label not found").
    headless = (SKILLS / "references" / "headless.md").read_text()
    err = _section(headless, "ERROR protocol", "**Label management:**")
    assert "gh label create" in err, "ERROR protocol never creates the label"
    assert "rawgentic:ai-error" in err


def test_step13_ci_unavailable_is_visible_nongate():
    # AC3: Step 13 must treat "no CI run spawned / Actions unavailable" as a
    # visible non-gate (like the quarantine path), not force the ERROR protocol.
    steps = (SKILLS / "references" / "steps.md").read_text()
    s13 = _section(steps, "## Step 13:", "## Step 14:")
    low = s13.lower()
    assert "#232" in s13, "Step 13 missing the #232 CI-unavailable non-gate"
    assert "no run" in low and ("spawn" in low or "unavailable" in low)
    assert "not gating" in low  # recorded as a visible non-gate, never claimed green
