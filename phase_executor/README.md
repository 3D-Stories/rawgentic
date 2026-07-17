# phase_executor

Deterministic model-seat execution engine — the machinery that replaces prose-subagent dispatch
for routed model seats. Extracted from the rawgentic bench cell-runner (proven across 400+ cells);
designed to be consumed by rawgentic **and** kukakuka (a Rust producer emits the same documents).

This is **E1** of rawgentic epic #422 — the foundation package. It ships the primitives that
later children (routing-enforcement hooks E2, seat cutover E3/E4, competitive rounds + build
bake-offs E5, complexity gate E6, driver-bench E7, multi-account lanes E8) consume.

## What it is

- **Normative contract = versioned JSON Schemas** (`src/phase_executor/schemas/`):
  `observation.schema.json` + `routing-table.schema.json`. `contract.py` is one producer.
  An `Observation`'s `actual_model` + `usage` are mandatory evidence when `parse_status == "ok"`
  (absent identity on a successful call is a failure, not an unknown success), and may be null only
  on a non-success status — so a pre-envelope timeout is still recordable.
- **Adapters** (`adapters/`): `claude_cli`, `codex_cli`, `zhipuai_sdk`. Each is a pure `parse_*`
  (fixture-tested, no I/O) + a live `run`. The adapter owns the model flag; the prompt goes on
  stdin (no argv injection).
- **Engine** (`engine.py`): `run_seat` (chain-aware fallback on availability failures) and
  `run_competitive` (concurrent bake-off across quota pools; caller-supplied judge / failure
  strategy / results sink). Synchronous; no detached mode.
- **Routing** (`routing.py` + `routing/rawgentic.routing-table.json`): declarative seat table as
  data, immutable snapshots, a cross-language-reproducible config digest, audited reload epochs,
  and chain-aware eligibility. Invariants (never-Haiku, cross-model author) are project-supplied
  `forbidden_combinations` rows — the engine is policy-free.
- **Quota** (`quota.py`): an inter-process, pool-keyed permit coordinator (the Claude ceiling holds
  across separate OS processes, not just threads).

## Install / use

```python
from phase_executor import run_seat, run_competitive, snapshot_from_file, QuotaCoordinator

snap = snapshot_from_file("src/phase_executor/routing/rawgentic.routing-table.json")
quota = QuotaCoordinator("/run/phase_executor/permits", snap.pool_concurrency())
obs = run_seat("review", "Review this diff…", snapshot=snap, quota=quota, capture_root="/run/pe")
assert obs.parse_status == "ok" and obs.actual_model  # provider-reported evidence
```

uv-native: `uv sync` (add `--extra glm` for the zhipuai adapter's locked env). The zhipuai worker
runs isolated via uv, so importing this package never imports `zhipuai`.

## Tests

Run from the rawgentic repo root: `pytest tests/phase_executor/`. The pure parsers are
fixture-tested against real captured provider outputs (AC1); live seat/bake-off tests live under
`tests/phase_executor/live/` (marked `live`, skipped in CI — they need real CLIs/SDK auth).
