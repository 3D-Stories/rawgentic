"""Drift guards for data-gated decision records under docs/measurements/.

A data-gated issue's outcome is a *decision record* citing the measured data
(e.g. #162's AC4: switch or abandon-with-data). These pins keep the record —
and the specific numbers the decision rests on — from silently vanishing or
being edited into a different conclusion without a deliberate test change.
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_162 = REPO_ROOT / "docs" / "measurements" / "2026-07-05-issue-162-data-gate-decision.md"
RUN_RECORDS = REPO_ROOT / "docs" / "measurements" / "run_records.jsonl"

# The #162 decision was made over the store's first 23 records (as of
# 2026-07-04). The store is append-only, so the decision's evidence basis is
# exactly records[:23] — later appends (e.g. a builtin_code_review pilot, the
# documented reopen path) must NOT flip these guards.
DECISION_BASIS_COUNT = 23


def _basis_records():
    lines = [l for l in RUN_RECORDS.read_text().splitlines() if l.strip()]
    assert len(lines) >= DECISION_BASIS_COUNT, "run-record store shrank below the decision basis"
    return [json.loads(l) for l in lines[:DECISION_BASIS_COUNT]]


def test_issue_162_decision_record_exists():
    assert DOC_162.exists(), "issue #162 data-gate decision record must exist"


def test_issue_162_decision_cites_the_gate_data():
    body = DOC_162.read_text()
    # both facts the abandon decision rests on:
    # (1) candidate-arm sample size zero vs the AC4 floor
    assert "builtin_code_review" in body
    assert "0 gate-instances" in body
    assert "≥10" in body or ">=10" in body
    # (2) the cost axis was never measured
    assert "null in all 23 records" in body
    assert "cost_estimate_usd" in body
    # data source named + decision and its framing
    assert "run_records.jsonl" in body
    assert "abandon" in body.lower()
    assert "not a rejection" in body.lower()


def test_issue_162_decision_basis_matches_the_store():
    """Recompute the decision's evidence from the store itself (non-vacuous:
    the doc could keep its words while the cited data never existed)."""
    recs = _basis_records()
    gates = [g for r in recs for g in r.get("gates", [])]
    builtin = [g for g in gates if g.get("reviewer_kind") == "builtin_code_review"]
    assert builtin == [], "decision basis claims candidate arm never ran"
    for r in recs:
        usage = r.get("usage") or {}
        for field in ("input_tokens", "output_tokens", "cost_estimate_usd"):
            assert usage.get(field) is None, (
                f"decision basis claims token/cost telemetry null; "
                f"record #{r['issue']['number']} has {field}={usage.get(field)!r}"
            )
