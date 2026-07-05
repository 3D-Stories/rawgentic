"""Drift guards for data-gated decision records under docs/measurements/.

A data-gated issue's outcome is a *decision record* citing the measured data
(e.g. #162's AC4: switch or abandon-with-data). These pins keep the record —
and the specific numbers the decision rests on — from silently vanishing or
being edited into a different conclusion without a deliberate test change.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_162 = REPO_ROOT / "docs" / "measurements" / "2026-07-05-issue-162-data-gate-decision.md"


def test_issue_162_decision_record_exists():
    assert DOC_162.exists(), "issue #162 data-gate decision record must exist"


def test_issue_162_decision_cites_the_gate_data():
    body = DOC_162.read_text()
    # the two facts the abandon decision rests on
    assert "builtin_code_review" in body
    assert "0 gate-instances" in body          # candidate arm never ran
    assert "≥10" in body or ">=10" in body      # AC4's required sample size
    assert "run_records.jsonl" in body          # data source named
    # the decision and its framing (deferral, not rejection of /code-review)
    assert "abandon" in body.lower()
    assert "not a rejection" in body.lower()
