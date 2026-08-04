"""#450 gate-preservation invariant, re-pinned by M0b (#866).

"A dispatch mechanism is never a gate bypass." Since M0d retired the executor
(and tests/hooks/test_executor_routing.py with it), the surviving layer is PROSE:
ONE canonical sentence (subagent/runner dispatch never bypasses a gate; an
actionable review round carries a minted reopen token) anchored in BOTH the WF2
and WF3 corpora, plus the WF2 mandatory-step table still naming every gate row.
Single-sentence anchor, whitespace-normalized (mistake #6/#11 idiom).
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT / "hooks"))

from corpus import skill_corpus  # noqa: E402

GATE_SENTENCE = (
    "A subagent or runner dispatch is never a gate bypass — every mandatory review "
    "gate runs with identical semantics whether a pass ran inline or through "
    "`hooks/review_runner.py`, and a review that may open a fix round carries a "
    "reopen token minted first."
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_gate_sentence_in_wf2_corpus():
    assert _norm(GATE_SENTENCE) in _norm(skill_corpus("implement-feature"))


def test_gate_sentence_in_wf3_corpus():
    assert _norm(GATE_SENTENCE) in _norm(skill_corpus("fix-bug"))


def test_wf2_mandatory_step_table_names_every_gate():
    """The SKILL.md mandatory-steps section keeps every gate: table rows for the
    always-run gates, and the conditional block still marks 8a mandatory-when-high."""
    body = (REPO_ROOT / "skills" / "implement-feature" / "SKILL.md").read_text(encoding="utf-8")
    section = body.split("<mandatory-steps>", 1)[1].split("</mandatory-steps>", 1)[0]
    for step in ("| 4 |", "| 9 |", "| 11 |", "| 11.5 |"):
        assert step in section, f"mandatory-step table lost its gate row {step!r}"
    assert "Step 8a (Per-task Review, P15):** mandatory when ANY task has `riskLevel: high`" in section
