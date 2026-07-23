"""#450 gate-preservation invariant, test-pinned by #470 (AC2).

"Fan-out is a dispatch mechanism, never a gate bypass." Two layers:

- PROSE: the two canonical sentences (executor-scoped build clause + fallback-tier
  prose-gate sentence) anchored in BOTH the WF2 and WF3 corpora, plus the WF2
  mandatory-step table still naming every gate row. Single-sentence anchors,
  whitespace-normalized (mistake #6/#11 idiom).
- MECHANICAL: the production mutating-engine allowlist pin, and cross-references to
  the executable black-box cells that live with the dispatch harness in
  tests/hooks/test_executor_routing.py (design §3 rule: cross-reference, never
  duplicate — those cells subprocess/in-process-drive the real CLI):
    * gateless build dispatch refused pre-launch .... test_supervised_missing_gate_refuses_malformed
    * tampered/stale gate ........................... test_cli_build_* (gate_tampered / gate_stale_for_plan)
    * un-sandboxed mutating engine refusal .......... test_supervised_refuses_unsandboxed_mutating_engine
    * canary-refusal-creates-nothing ................ test_supervised_phase2_refusal_exits_six_and_creates_nothing
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT / "hooks"))

from corpus import skill_corpus  # noqa: E402

GATE_SENTENCE = (
    "An executor seat is never a gate bypass — every mandatory gate (Steps 4, 8a, 9, "
    "11, 11.5) runs with identical semantics whichever tier dispatches its model calls, "
    "and every EXECUTOR-tier build-seat dispatch requires the authenticated gate "
    "decision plus the internally minted plan context."
)
FALLBACK_SENTENCE = (
    "WF2/WF3 prose runs the complexity-gate step before any legacy-architecture build dispatch."
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_gate_sentence_in_wf2_corpus():
    assert _norm(GATE_SENTENCE) in _norm(skill_corpus("implement-feature"))


def test_gate_sentence_in_wf3_corpus():
    assert _norm(GATE_SENTENCE) in _norm(skill_corpus("fix-bug"))


def test_fallback_sentence_in_wf2_corpus():
    assert _norm(FALLBACK_SENTENCE) in _norm(skill_corpus("implement-feature"))


def test_fallback_sentence_in_wf3_corpus():
    assert _norm(FALLBACK_SENTENCE) in _norm(skill_corpus("fix-bug"))


def test_wf2_mandatory_step_table_names_every_gate():
    """The SKILL.md mandatory-steps section keeps every gate: table rows for the
    always-run gates, and the conditional block still marks 8a mandatory-when-high."""
    body = (REPO_ROOT / "skills" / "implement-feature" / "SKILL.md").read_text(encoding="utf-8")
    section = body.split("<mandatory-steps>", 1)[1].split("</mandatory-steps>", 1)[0]
    for step in ("| 4 |", "| 9 |", "| 11 |", "| 11.5 |"):
        assert step in section, f"mandatory-step table lost its gate row {step!r}"
    assert "Step 8a (Per-task Review, P15):** mandatory when ANY task has `riskLevel: high`" in section


def test_mutating_engine_allowlist_is_codex_only():
    """Production pin: mutating dispatch allowlist stays codex-only until the
    FS-sandbox child ships (owner decision 2026-07-20; contract.py SECURITY-LAYER
    ASYMMETRY). The behavioral refusal cell lives in
    tests/hooks/test_executor_routing.py::test_supervised_refuses_unsandboxed_mutating_engine."""
    import executor_routing_lib as er  # noqa: PLC0415
    assert er.MUTATING_FS_SANDBOXED == frozenset({"codex"})


def test_black_box_cells_exist_where_cross_referenced():
    """The module docstring's cross-references must not rot: each named executable
    cell still exists in the dispatch-harness test file."""
    harness = (REPO_ROOT / "tests" / "hooks" / "test_executor_routing.py").read_text(encoding="utf-8")
    for cell in (
        "def test_supervised_missing_gate_refuses_malformed",
        "def test_supervised_refuses_unsandboxed_mutating_engine",
        "def test_supervised_phase2_refusal_exits_six_and_creates_nothing",
    ):
        assert cell in harness, f"cross-referenced black-box cell {cell!r} is gone"
