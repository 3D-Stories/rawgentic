"""#419 drift guard: docs/model-routing.md carries the canonical refresh-rule move sentence + the
two-test rule phrases. Anchored to ONE file, whitespace-normalized so prose wrapping doesn't break it
(repo drift-guard convention)."""
import re
from pathlib import Path

DOC = Path(__file__).resolve().parent.parent / "docs" / "model-routing.md"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_doc_exists():
    assert DOC.exists(), "docs/model-routing.md must exist (#419)"


def test_canonical_move_rule_sentence_present():
    norm = _norm(DOC.read_text(encoding="utf-8"))
    # The one canonical, drift-guardable move-rule sentence (markdown decorations excluded from the pin).
    for clause in (
        "changes ONLY when BOTH the gap test and the floor test pass",
        "a tie or a void holds the incumbent",
        "provenance is re-stamped on every decision",
    ):
        assert clause in norm, f"model-routing.md missing canonical move-rule clause: {clause!r}"


def test_two_tests_documented():
    norm = _norm(DOC.read_text(encoding="utf-8"))
    # Gap test: an explicit effect-size heuristic, not a significance test.
    assert "candidate_median" in norm and "pooled_sd" in norm
    assert "effect-size heuristic" in norm.lower() and "significance test" in norm.lower()
    # Floor test: the 70 / 80 floors + the driver 5/6 gate rule.
    assert "70" in norm and "80" in norm and "5/6" in norm


def test_no_auto_tuner_and_role_phase_map():
    norm = _norm(DOC.read_text(encoding="utf-8"))
    assert "no auto-tuner" in norm.lower()
    # role→phase map rows
    for phase in ("intake", "design", "build", "plan", "review"):
        assert phase in norm
