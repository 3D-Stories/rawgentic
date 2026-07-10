"""Drift guards for the WF17 session-mining skill's canonical sentences (#376).

Pattern: test_run_feedback_clarity.py — one canonical sentence per pin,
section isolated by header slicing where needed, whitespace-normalized.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / "skills" / "session-mining" / "SKILL.md"


def _norm(text: str) -> str:
    return " ".join(text.split())


def _skill() -> str:
    return _norm(SKILL.read_text(encoding="utf-8"))


def test_wf17_declared():
    text = SKILL.read_text(encoding="utf-8")
    assert text.splitlines()[2].startswith("description: WF17 —")
    assert "# WF17: Session Mining" in text


def test_write_surface_enumeration_pinned():
    s = _skill()
    assert ("WF17 is STRICTLY report-only — the ONLY file writes are the "
            "candidates queue (`claude_docs/.mining/candidates.jsonl`), the "
            "report `.md`/`.html` pair under `docs/reviews/`, the "
            "session-note DONE marker append, and — when Step 1 refreshes "
            "it — the #375 session-index store" in s)


def test_recurrence_threshold_pinned():
    assert ("A candidate is only PROPOSED for filing at recurrence ≥ 3 "
            "distinct sessions" in _skill())


def test_propose_then_approve_pinned():
    assert ("Accepted candidates are routed to WF1 as a prepared draft; "
            "nothing is ever auto-filed — propose-then-approve, always"
            in _skill())


def test_declined_not_reproposed_pinned():
    assert ("A declined candidate is recorded in the queue and not "
            "re-proposed" in _skill())


def test_coverage_honesty_pinned():
    assert ("v1 does not inspect raw tool_use/tool_result payloads and "
            "cannot conclude that command sequences or tool errors are "
            "absent" in _skill())


def test_no_config_loading_block():
    text = SKILL.read_text(encoding="utf-8")
    assert "<config-loading>" not in text
