# Adversarial Review — .rawgentic-diff-review-162.patch

- Date: unknown-date
- Artifact type: diff
- Reviewer: Codex (model config-default, reasoning effort high)
- Findings: 2 (Critical 0, High 0, Medium 2, Low 0)

## Summary

The diff is mostly a documentation/version bump plus a new drift-guard test for the Issue #162 data-gate decision. The main risk is that the added test claims to pin measured decision data but only checks loose strings in the decision record, so the guard can pass after the underlying evidence or key decision facts drift.

## Findings

### 1. [Medium] correctness · high confidence — tests/test_decision_records.py

> +    assert "run_records.jsonl" in body          # data source named

The new drift guard treats naming the data source as sufficient, but it never reads or validates the actual `run_records.jsonl` data that the decision claims to be based on. If the run-record data is absent, corrupt, or later changes to include candidate runs/token telemetry, this test still passes as long as the markdown keeps the same words, so the guard is vacuous for the measured evidence.

**Recommendation:** Change `tests/test_decision_records.py` to parse `docs/measurements/run_records.jsonl` and assert the measured facts used by the decision, including candidate-arm count, total records, and token/cost telemetry state, instead of only checking that the document names the source.

### 2. [Medium] internal-consistency · high confidence — tests/test_decision_records.py

> +    # the two facts the abandon decision rests on
> +    assert "builtin_code_review" in body
> +    assert "0 gate-instances" in body          # candidate arm never ran
> +    assert "≥10" in body or ">=10" in body      # AC4's required sample size
> +    assert "run_records.jsonl" in body          # data source named

The test says it pins “the two facts the abandon decision rests on,” but the decision record states the gate failed twice, including token telemetry being null in all records. The test does not assert the token telemetry fact at all, so the record could drop or alter one of its stated load-bearing reasons while the drift guard remains green.

**Recommendation:** In `test_issue_162_decision_cites_the_gate_data`, add explicit assertions for the second gate-failure fact, such as `token telemetry`, `null in all 23 records`, and the relevant `usage.input_tokens` / `output_tokens` / `cost_estimate_usd` fields, or rename the comment and outcome claim to state that only the candidate-arm sample-size fact is pinned.

---
_Report-only: this review does not edit the artifact. Findings are advisory; incorporate them at your discretion._