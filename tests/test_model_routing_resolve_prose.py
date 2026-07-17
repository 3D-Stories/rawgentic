"""#417 drift guard: the routing-resolve prose (seat fallback chain + circuit breaker, the ≤3-Claude
concurrency ceiling, the driver-seat guidance) is present in the single-source shared block AND ships
into the WF2 corpus. Anchors the ONE canonical fallback-contract sentence to ONE file (the shared
source), per the repo drift-guard pattern."""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHARED = REPO / "shared" / "blocks" / "model-routing-resolve.md"
sys.path.insert(0, str(REPO / "tests"))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


# The ONE canonical, drift-guardable fallback-contract sentence (shared source = single source).
CANONICAL = "a chain that exhausts its eligible entries is a handled hard failure, never a silent downgrade"


def test_canonical_fallback_sentence_in_shared_source():
    norm = _norm(SHARED.read_text(encoding="utf-8")).lower()
    assert CANONICAL in norm, "shared/blocks/model-routing-resolve.md must carry the canonical fallback sentence"


def test_concurrency_and_driver_prose_in_shared_source():
    norm = _norm(SHARED.read_text(encoding="utf-8"))
    assert "≤ 3 concurrent Claude subagents" in norm
    assert "effective working ceiling of 2" in norm
    assert "strong-model-on-top reliability floor" in norm
    assert "GUIDANCE only" in norm or "guidance, not enforcement" in norm.lower() or "guidance only" in norm.lower()


def test_prose_shipped_into_wf2_corpus():
    # the sync must have propagated the shared block into implement-feature's SKILL.md corpus
    from corpus import skill_corpus
    norm = _norm(skill_corpus("implement-feature")).lower()
    assert CANONICAL in norm, "implement-feature corpus must carry the synced canonical fallback sentence"


def test_wf3_fix_bug_carries_concurrency_and_fallback():
    # fix-bug's bespoke WF3 block (edited directly, not synced) gets the corresponding note
    norm = _norm((REPO / "skills" / "fix-bug" / "SKILL.md").read_text(encoding="utf-8"))
    assert "effective working ceiling of 2" in norm
    assert "handled hard failure, never a silent downgrade" in norm
