# #420 — routing telemetry in run records (epic #422) — lane design+plan

**Small-standard lane** (work_summary.py + test ≤ 7 impl files). Plan §6.4. No unmet deps
(additive/optional; the fields are POPULATED once #417 wires the executor + a seat is opted in — this
child ships the SCHEMA). Key-independent.

## Brief design (lane)

Extend each existing `dispatches[]` entry (run-record, `hooks/work_summary.py`) with **OPTIONAL**
per-dispatch routing-telemetry fields, validated only-if-present so old records + entries without them
stay `rc=0` (same validated-optional pattern as `usage`/`goal_guard`; additive, no schema-version bump):

- `preferred_model`: str | null — the routed/requested model (executor `requested_model`).
- `actual_model`: str | null — provider-reported id (executor `actual_model`).
- `fallback_reason`: str | null — why the chain fell back (executor `fallback_reason`).
- `queued_ms`: int (non-bool) | null — quota queue wait (executor `queued_ms`).
- `concurrency`: int (non-bool) | null — observed concurrent-permit count (≤3 ceiling visibility).
- `selector`: object | null — `{risk_level, complexity, ceiling}` (each str|null) — the routing
  selector inputs.

Every one is optional: `validate_record` checks a field's TYPE only when the key is present. Exclude
prompt contents (issue directive) — none of these carry prompt text. The aggregate rollup
(`summarize`) is unchanged (it already tolerates arbitrary dispatch entries); a richer rollup over the
new fields is a later concern, not this schema child.

## Platform / external dependencies

platform_apis: none

(Pure run-record validator logic; no external/platform API.)

## Plan (lane checklist — TDD)

### Task 1: optional routing fields on dispatches[]
- riskLevel: standard
- RED: tests in tests/hooks/test_work_summary*.py (or a new test) — a dispatches[] entry carrying the
  6 routing fields (valid types) validates (rc 0); bad types (preferred_model non-str, queued_ms bool,
  selector non-dict) error; an entry WITHOUT them (the pre-#420 6-key shape) still validates; an OLD
  record with no dispatches stays valid. GREEN: add the only-if-present checks after the model/effort
  block in the `dispatches[]` loop.
- files: hooks/work_summary.py, tests/hooks/test_work_summary_dispatches_routing.py

### Task 2: schema doc
- riskLevel: standard
- Document the optional routing fields in the `dispatches` section of
  `skills/implement-feature/references/run-record.md` (the schema source of truth). verify: full suite.

version 3.47.0 → 3.48.0 (minor, feat). No spine change → no diagram REV.
